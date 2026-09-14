#!/usr/bin/env python3
"""
Game-day pinger — run every ~5 min on game days (GitHub Actions cron). Stateless: de-duplicates
against what's already been posted to the ntfy topic, so nothing needs committing.

For each of OUR current-week picks (from data.js, plus anything published from the app since):
  * ~45 min before kick-off:  "Kick-off soon" with listen links
  * end of each quarter:      score update (halftime = end of Q2)
  * under 2:00 in the 4th:    "2-minute warning" with score
  * final:                    result and whether a life was lost

Env: NTFY_TOPIC (optional), QUIET_HOURS e.g. "23-7" Sydney (optional), LOOKAHEAD_MIN (default 45)
"""
import json, os, re, datetime, pathlib, urllib.parse
import requests
from notify import send, sent_titles, quiet_now, both_times, TOPIC, app_url

HERE = pathlib.Path(__file__).parent
LOOKAHEAD = int(os.environ.get("LOOKAHEAD_MIN", "45") or 45)

data = json.loads(re.sub(r"^window\.CFB_DATA = |;\s*$", "", (HERE / "data.js").read_text()))
T, week, ME = data["teams"], data["currentWeek"], (data.get("picks") or {}).get("me", "B Spak")

# --- our picks: data.js (sheet/app merged) plus any app message newer than that
me = next((p for p in (data.get("picks") or {}).get("players", []) if p["name"] == ME), None)
my = list(me["picks"].get(str(week), [])) if me else []
try:
    r = requests.get(f"https://ntfy.sh/{TOPIC}-picks/json", params={"poll": 1, "since": "12h"}, timeout=20)
    latest = None
    for line in r.text.splitlines():
        try: m = json.loads(line); body = json.loads(m.get("message", "{}"))
        except ValueError: continue
        if m.get("event") == "message" and body.get("player") == ME and str(body.get("week")) == str(week):
            if not latest or body.get("at", "") > latest.get("at", ""): latest = body
    if latest and (not me or (me.get("source", {}).get(str(week)) != "sheet")):
        my = [str(x) for x in latest["picks"]]
except Exception as e:
    print("  couldn't poll app picks:", e)
if not my:
    print(f"no picks for week {week} yet"); raise SystemExit
print(f"week {week} picks: {[T[i]['name'] for i in my if i in T]}")

if quiet_now():
    print("quiet hours, skipping"); raise SystemExit

# --- live scoreboard
sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
                  params={"groups": 80, "limit": 400, "week": week, "seasontype": 2, "dates": data["season"]}, timeout=30).json()
already = sent_titles(24)
now = datetime.datetime.now(datetime.timezone.utc)
site = app_url()

def post(title, msg, **kw):
    if title in already: return
    if send(title, msg, click=kw.pop("click", site), **kw): already.add(title)

for ev in sb.get("events", []):
    c = ev["competitions"][0]
    home = next(x for x in c["competitors"] if x["homeAway"] == "home")
    away = next(x for x in c["competitors"] if x["homeAway"] == "away")
    mine = [x for x in (home, away) if x["team"]["id"] in my]
    if not mine: continue
    us, them = mine[0], (away if mine[0] is home else home)
    t, opp = T.get(us["team"]["id"], {"name": us["team"]["shortDisplayName"]}), them["team"]["shortDisplayName"]
    is_home = us is home
    tag = f"{t['name']} {'vs' if is_home else '@'} {opp}"
    st = ev["status"]; state = st["type"]["state"]; period = st.get("period", 0); clock = st.get("displayClock", "")
    us_s, them_s = int(us.get("score") or 0), int(them.get("score") or 0)
    score = f"{t['name']} {us_s}, {opp} {them_s}"
    gamecast = f"https://www.espn.com/college-football/game/_/gameId/{ev['id']}"
    acts = [{"action": "view", "label": "Gamecast", "url": gamecast}]

    if state == "pre":
        start = datetime.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        mins = (start - now).total_seconds() / 60
        if -15 <= mins <= LOOKAHEAD:
            sp = c.get("odds", [{}])[0].get("spread")
            line = "" if sp is None else f" · line {(-sp if is_home else sp):+.1f}"
            radio = "https://tunein.com/search/?query=" + urllib.parse.quote(f"{t['name']} football radio")
            post(f"Kick-off soon: {tag}",
                 f"{both_times(ev['date'])}{line}\nListen: Varsity Network app (free school call) or TuneIn.",
                 tags=["football", "loudspeaker"], click=radio,
                 actions=acts + [{"action": "view", "label": "TuneIn", "url": radio}])

    elif state == "in":
        halftime = "HALFTIME" in st["type"].get("name", "")
        done_q = period - 1 if not halftime else 2
        if done_q >= 1:
            lead = "up" if us_s > them_s else ("down" if us_s < them_s else "level")
            label = "Halftime" if done_q == 2 else f"End of Q{done_q}" if done_q < 4 else f"End of OT{done_q-4 or ''}"
            post(f"{label}: {tag}", f"{score} — {t['name']} {lead}" + ("" if halftime else f" ({clock} left in Q{period})"),
                 tags=["football", "hourglass_flowing_sand"], actions=acts)
        m = re.match(r"(\d+):(\d\d)", clock or "")
        if period == 4 and m and (int(m.group(1)) * 60 + int(m.group(2))) <= 120:
            urgency = 4 if abs(us_s - them_s) <= 8 else 3
            post(f"2-minute warning: {tag}", f"{score} · {clock} left" + (" — this one's close!" if urgency == 4 else ""),
                 tags=["football", "alarm_clock"], priority=urgency, actions=acts)

    elif state == "post" and st["type"].get("completed"):
        won = us_s > them_s
        post(f"FINAL: {tag}", f"{score}\n" + ("✅ Life safe." if won else "❌ Life lost."),
             tags=["football", "white_check_mark" if won else "x"], priority=3 if won else 4, actions=acts)
print("done")
