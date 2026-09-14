#!/usr/bin/env python3
"""
Weekly wrap — run Monday morning (Sydney) after fetch_data.py has refreshed data.js.
Sends one ntfy message: our results, rivals' week, ladder position, and the top-10 picks for next
week with a one-line reason each. Pass --print to preview without sending.
"""
import sys
from model import load, assess, solve, lives_lost, cap, outcome, top_picks
from notify import send, app_url

d = load(); T = d["teams"]; WEEKS = d["WEEKS"]
picks_all = d.get("picks") or {"me": "B Spak", "players": []}
ME = picks_all["me"]
players = {p["name"]: p["picks"] for p in picks_all["players"]}
mine = players.get(ME, {})

# --- which week just finished
done = [w for w in WEEKS if all(g["completed"] for g in d["games"] if g["week"] == w and (T[g["home"]]["pool"] or T[g["away"]]["pool"]))]
last = max(done) if done else None
nxt = next((w for w in WEEKS if last is None or w > last), None)
if last is None:
    print("no completed week yet"); sys.exit()

def name(i): return T[i]["name"]
def scoreline(i, w):
    a = assess(d, T[i], w); g = a["game"]
    if not g: return f"{name(i)} — bye (❌ illegal)"
    if not a["legal"]: return f"{name(i)} vs {a['opp']['name']} — ❌ illegal (non-FBS)"
    mine_s, theirs = (g["homeScore"], g["awayScore"]) if a["home"] else (g["awayScore"], g["homeScore"])
    return f"{'✅' if a.get('result') == 'W' else '❌'} {name(i)} {mine_s}–{theirs} {a['opp']['name']}"

lines = [f"Week {last} wrap 🏈"]
# --- us
my_week = mine.get(str(last), [])
lost_now = lives_lost(d, mine)
if my_week:
    wins = sum(1 for i in my_week if outcome(d, i, last) == "W")
    lines.append(f"\nYOU: {wins}/{len(my_week)}" + (" — clean sweep!" if wins == len(my_week) else f" — {len(my_week)-wins} life lost" if len(my_week)-wins == 1 else f" — {len(my_week)-wins} lives lost"))
    lines += ["  " + scoreline(i, last) for i in my_week]
else:
    lines.append("\nYOU: no picks recorded for this week 😬")
lines.append(f"Lives: {10-lost_now} of 10 left")

# --- ladder (lives lost, then optimiser outlook)
table = []
for pl, pk in players.items():
    lost = lives_lost(d, pk)
    exp = None
    if lost < 10:
        assign, tot = solve(d, pk, from_week=nxt) if nxt else ({}, 0)
        exp = sum(1 - assess(d, T[i], w)["p"] for i, w in assign.items())
    table.append((lost, exp if exp is not None else 99, pl))
table.sort()
pos = next(i for i, r in enumerate(table) if r[2] == ME) + 1
tied = sum(1 for r in table if r[0] == lost_now)
lines.append(f"\nLADDER: {pos}{'st' if pos==1 else 'nd' if pos==2 else 'rd' if pos==3 else 'th'} of {len(table)}" + (f" (tied with {tied-1} on {lost_now} lost)" if tied > 1 else ""))
lines += [f"  {i+1}. {'👉 ' if pl == ME else ''}{pl} — {lost} lost" + (f", outlook {exp:.1f}" if exp < 99 else " — OUT") for i, (lost, exp, pl) in enumerate(table[:6])]
if pos > 6: lines.append("  …")

# --- rivals this week
hurt = []
for pl, pk in players.items():
    if pl == ME: continue
    bad = [name(i) for i in pk.get(str(last), []) if outcome(d, i, last) in ("L", "X")]
    if bad: hurt.append(f"{pl} ({', '.join(bad)})")
outs = [pl for lost, _, pl in table if lost >= 10]
lines.append(f"\nRIVALS: {len(hurt)} of {len(players)-1} lost a life" + (":" if hurt else "."))
lines += ["  " + h for h in hurt[:8]]
if len(hurt) > 8: lines.append(f"  …and {len(hurt)-8} more")
if outs: lines.append("  Eliminated: " + ", ".join(outs))

# --- next week recommendations
if nxt:
    lines.append("")
    lines += top_picks(d, mine, nxt)


msg = "\n".join(lines)
if "--print" in sys.argv:
    print(msg)
else:
    send(f"Week {last} wrap · you're {pos}{'st' if pos==1 else 'nd' if pos==2 else 'rd' if pos==3 else 'th'} of {len(table)}", msg,
         tags=["football", "newspaper"], click=app_url())
