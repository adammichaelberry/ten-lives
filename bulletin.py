#!/usr/bin/env python3
"""
Thursday bulletin — sent Thursday afternoon (Sydney), before the weekend's games.
Where you stand, how many picks you've locked for this week, the ten leading picks with reasons,
and a few things worth a look: lines the market and the models disagree on, and lines that moved
since the last data run. Pass --print to preview.
"""
import sys, json, pathlib
from model import load, assess, solve, lives_lost, cap, top_picks, COMFORT
from notify import send, app_url

d = load(); T = d["teams"]; w = d["currentWeek"]
picks_all = d.get("picks") or {"me": "B Spak", "players": []}
ME = picks_all["me"]; players = {p["name"]: p["picks"] for p in picks_all["players"]}
mine = players.get(ME, {})
me_src = next((p.get("source", {}) for p in picks_all["players"] if p["name"] == ME), {})

lines = [f"Thursday bulletin · Week {w} 🏈"]

# --- where we stand
lost = lives_lost(d, mine)
standings = sorted((lives_lost(d, pk), pl) for pl, pk in players.items())
pos = next((i for i, (l, pl) in enumerate(standings) if pl == ME), 0) + 1
leaders = [pl for l, pl in standings if l == standings[0][0]]
lines.append(f"You: {10-lost}/10 lives, {'=' if len(leaders) > 1 and ME in leaders else ''}{pos}{'st' if pos==1 else 'nd' if pos==2 else 'rd' if pos==3 else 'th'} of {len(standings)}."
             + (f" Leading with {', '.join(x for x in leaders if x != ME)}." if ME in leaders and len(leaders) > 1 else " Out in front." if ME in leaders else f" Leaders: {', '.join(leaders)}."))

# --- this week's picks so far
locked = mine.get(str(w), [])
n = cap(d, w)
if locked:
    src = me_src.get(str(w), "app")
    lines.append(f"Week {w} picks locked: {len(locked)}/{n} ({', '.join(T[i]['name'] for i in locked)}) — from the {'spreadsheet' if src == 'sheet' else 'app'}.")
else:
    lines.append(f"Week {w} picks: none locked yet — {n} needed.")

# --- leading picks
lines.append("")
lines += top_picks(d, mine, w)

# --- worth a look: market vs models, and movers
used = {i for ids in mine.values() for i in ids}
disagree, moved = [], []
snap = {}
p = pathlib.Path(__file__).parent / "odds_snapshot.json"
if p.exists():
    try: snap = json.loads(p.read_text())
    except ValueError: pass
for t in d["POOL"]:
    if t["id"] in used: continue
    a = assess(d, t, w)
    g = a.get("game")
    if not a["legal"] or not g or g["spread"] is None: continue
    hfa = 0 if g["neutral"] else (2.4 if a["home"] else -2.4)
    parts = [x for x in [(t.get("fpi") or 0) - (a["opp"].get("fpi") or 0), (t.get("sag") or 0) - (a["opp"].get("sag") or 0)] if x]
    if parts:
        model_m = sum(parts) / len(parts) + hfa
        gap = a["margin"] - model_m
        if abs(gap) >= 5: disagree.append((gap, t, a, model_m))
lines.append("")
if disagree:
    disagree.sort(key=lambda x: -abs(x[0]))
    lines.append("MARKET vs MODELS (≥5 pts apart):")
    for gap, t, a, mm in disagree[:5]:
        lines.append(f"  {t['name']} {'vs' if a['home'] else '@'} {a['opp']['name']}: DK {a['margin']:+.1f}, models {mm:+.1f} — {'bookies like them more' if gap > 0 else 'bookies are cooler than the ratings'}")
comf = [t for t in d["POOL"] if t["id"] not in used and assess(d, t, w)["legal"] and (assess(d, t, w)["margin"] or 0) >= COMFORT]
lines.append(f"\n{len(comf)} of your remaining teams are comfortable ({COMFORT}+) favourites this week.")

msg = "\n".join(lines)
if "--print" in sys.argv: print(msg)
else: send(f"Thursday bulletin · Week {w}", msg, tags=["football", "newspaper"], click=app_url())
