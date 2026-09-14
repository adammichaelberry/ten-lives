"""Python port of the app's win-probability model and season optimiser (used by report.py)."""
import json, re, math, pathlib
import numpy as np
from scipy.optimize import linear_sum_assignment

SIGMA, DRIFT, HFA, COMFORT, HORIZON = 13.5, 2.5, 2.4, 21, 13

def load(here=pathlib.Path(__file__).parent):
    d = json.loads(re.sub(r"^window\.CFB_DATA = |;\s*$", "", (here / "data.js").read_text()))
    d["POOL"] = sorted([t for t in d["teams"].values() if t["pool"]], key=lambda t: t["name"])
    d["WEEKS"] = sorted({g["week"] for g in d["games"] if g["week"] >= 1 and (d["teams"][g["home"]]["pool"] or d["teams"][g["away"]]["pool"])})
    d["by_tw"] = {}
    for g in d["games"]:
        d["by_tw"].setdefault((g["home"], g["week"]), g); d["by_tw"].setdefault((g["away"], g["week"]), g)
    return d

def cap(d, w): return d["picksPerWeek"].get(str(w), d["defaultPicks"])
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))

def assess(d, t, w):
    """-> dict(legal, reason, game, opp, home, neutral, margin, src, p, result)"""
    g = d["by_tw"].get((t["id"], w))
    if not g: return {"legal": False, "reason": "Bye week", "p": 0, "game": None}
    home = g["home"] == t["id"]; opp = d["teams"][g["away"] if home else g["home"]]
    r = {"game": g, "opp": opp, "home": home, "neutral": g["neutral"], "legal": True, "p": 0, "margin": None, "src": ""}
    if opp.get("fbs") is False: r["legal"] = False; r["reason"] = f"Non-FBS opponent · {opp['name']}"
    hfa = 0 if g["neutral"] else (HFA if home else -HFA)
    parts = []
    if opp.get("fpi") is not None and t.get("fpi") is not None: parts.append(t["fpi"] - opp["fpi"] + hfa)
    if opp.get("sag") is not None and t.get("sag") is not None: parts.append(t["sag"] - opp["sag"] + hfa)
    ahead = max(0, w - d["currentWeek"])
    if g["spread"] is not None and ahead == 0: r["margin"], r["src"] = (-g["spread"] if home else g["spread"]), "DK"
    elif parts: r["margin"], r["src"] = sum(parts) / len(parts), "FPI+SAG"
    if r["margin"] is not None:
        r["p"] = Phi(r["margin"] / math.sqrt(SIGMA ** 2 + (DRIFT * ahead) ** 2))
    if not r["legal"]: r["p"] = 0
    if g["completed"]:
        mine, theirs = (g["homeScore"], g["awayScore"]) if home else (g["awayScore"], g["homeScore"])
        r["result"] = "W" if mine > theirs else ("L" if mine < theirs else "T")
    return r

def outcome(d, tid, w):
    a = assess(d, d["teams"][tid], w)
    return "X" if not a["legal"] else a.get("result")

def lives_lost(d, picks):
    return sum(1 for w, ids in picks.items() for i in ids if outcome(d, i, int(w)) in ("L", "X"))

def solve(d, picks, from_week=None, exclude=(), cap_override=None):
    """Optimal assignment of unused pool teams to remaining slots. -> (assign {tid: week}, total expected wins)"""
    from_week = from_week or d["currentWeek"]
    used = {i for ids in picks.values() for i in ids} | set(exclude)
    teams = [t for t in d["POOL"] if t["id"] not in used]
    slots = []
    for w in d["WEEKS"]:
        if from_week <= w <= HORIZON:
            n = (cap_override or {}).get(w, max(0, cap(d, w) - len(picks.get(str(w), []))))
            slots += [w] * n
    if not teams or not slots: return {}, 0.0
    P = np.array([[assess(d, t, w)["p"] for w in slots] for t in teams])
    rows, cols = linear_sum_assignment(1 - P)
    assign = {teams[i]["id"]: slots[j] for i, j in zip(rows, cols) if P[i, j] > 0}
    return assign, float(sum(P[i, j] for i, j in zip(rows, cols) if P[i, j] > 0))


def top_picks(d, mine, nxt):
    """Lines: the optimiser's picks for week `nxt` (starred) then the best alternatives, up to 10, with reasons."""
    T = d["teams"]; WEEKS = d["WEEKS"]; lines = []
    assign, total = solve(d, mine, from_week=nxt)
    used = {i for ids in mine.values() for i in ids}
    n = cap(d, nxt)
    cands = []
    for t in d["POOL"]:
        if t["id"] in used: continue
        a = assess(d, t, nxt)
        if not a["legal"]: continue
        cands.append((a["p"], t, a))
    cands.sort(key=lambda x: -x[0])
    stars = [c for c in cands if assign.get(c[1]['id']) == nxt]; others = [c for c in cands if assign.get(c[1]['id']) != nxt]
    cands = stars + others[:max(0, 10 - len(stars))]
    lines.append(f"WEEK {nxt} ({n} picks) — ★ optimiser's picks, then best alternatives:")
    comfortP = Phi(COMFORT / SIGMA)
    for p, t, a in cands:
        star = assign.get(t["id"]) == nxt
        # reasons
        future = [(w, assess(d, t, w)) for w in WEEKS if w > nxt]
        comfy_later = sum(1 for w, x in future if x["legal"] and x["p"] >= comfortP)
        best_later = max([x["p"] for w, x in future if x["legal"]] or [0])
        if star and p >= best_later: why = "their best game of the season"
        elif star and comfy_later == 0: why = "no comfortable game after this"
        elif star: why = f"good now; {comfy_later} comfy week{'s' if comfy_later != 1 else ''} later but the slots are needed"
        else:
            alt, tot_alt = solve(d, mine, from_week=nxt, exclude=[t["id"]], cap_override={nxt: n - 1})
            cost = total - (tot_alt + p)
            planned = assign.get(t["id"])
            why = (f"optimiser holds for W{planned}" if planned else "no planned week") + (f" — using now costs {cost:.2f} exp. wins" if cost > 0.02 else " — nearly free to use now")
        venue = "vs" if a["home"] else ("n" if a["neutral"] else "@")
        lines.append(f"  {'★' if star else '·'} {t['name']} {venue} {a['opp']['name']} {a['margin']:+.1f} ({a['src']}) {round(p*100)}% — {why}")
    exp_rest = sum(1 - assess(d, T[i], w)["p"] for i, w in assign.items())
    lines.append(f"\nExpected lives lost rest of season: {exp_rest:.1f}")
    return lines
