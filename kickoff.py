#!/usr/bin/env python3
"""
Game-day pinger. Run every ~30 min on game days (GitHub Actions cron).
Reads data.js (my picks for the current week), checks kick-off times, and posts an ntfy alert
shortly before each of my picks, with links to listen live.

Environment:
    NTFY_TOPIC    ntfy.sh topic
    LOOKAHEAD_MIN minutes ahead to announce (default 45 — one cron gap plus slack)
    QUIET_HOURS   e.g. "23-7" (Australia/Sydney local) to suppress pings overnight; empty = never quiet
"""
import json, os, re, datetime, pathlib, urllib.parse
import requests
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).parent
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
LOOKAHEAD = int(os.environ.get("LOOKAHEAD_MIN", "45") or 45)
QUIET = os.environ.get("QUIET_HOURS", "").strip()
SYD = ZoneInfo("Australia/Sydney"); PDX = ZoneInfo("America/Los_Angeles")

data = json.loads(re.sub(r"^window\.CFB_DATA = |;\s*$", "", (HERE / "data.js").read_text()))
T, week = data["teams"], data["currentWeek"]
me = next((p for p in (data.get("picks") or {}).get("players", []) if p["name"] == data["picks"]["me"]), None)
my = set(me["picks"].get(str(week), [])) if me else set()
if not my:
    print("no picks for the current week yet"); raise SystemExit

now = datetime.datetime.now(datetime.timezone.utc)
if QUIET:
    a, b = (int(x) for x in QUIET.split("-")); h = now.astimezone(SYD).hour
    if (a <= h or h < b) if a > b else (a <= h < b):
        print("quiet hours, skipping"); raise SystemExit

state_path = HERE / "kickoff_state.json"
done = set(json.loads(state_path.read_text())) if state_path.exists() else set()

for g in data["games"]:
    if g["week"] != week or g["completed"] or g["id"] in done: continue
    picked = [tid for tid in (g["home"], g["away"]) if tid in my]
    if not picked: continue
    start = datetime.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
    mins = (start - now).total_seconds() / 60
    if not (-15 <= mins <= LOOKAHEAD): continue
    t = T[picked[0]]; home = t["id"] == g["home"]; opp = T[g["away"] if home else g["home"]]
    fmt = lambda tz: start.astimezone(tz).strftime("%a %I:%M %p").replace(" 0", " ").lstrip("0")
    local = f"{fmt(SYD)} Newcastle · {fmt(PDX)} Portland"
    line = "" if g["spread"] is None else f" (line {(-g['spread'] if home else g['spread']):+.1f})"
    radio = "https://tunein.com/search/?query=" + urllib.parse.quote(f"{t['name']} football radio")
    gamecast = f"https://www.espn.com/college-football/game/_/gameId/{g['id']}"
    body = (f"{t['name']} {'vs' if home else '@'} {opp['name']}{line}\n"
            f"Kick-off {local}\n"
            f"Listen: Varsity Network app (free school broadcast) · TuneIn: {radio}\n"
            f"Gamecast: {gamecast}")
    title = f"Kick-off: {t['name']} {'in ' + str(int(mins)) + ' min' if mins > 0 else 'now'}"
    if TOPIC:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=body.encode(), timeout=20,
                      headers={"Title": title, "Tags": "football,loudspeaker", "Click": radio,
                               "Actions": f"view, Gamecast, {gamecast}"})
    print("pinged:", title)
    done.add(g["id"])

state_path.write_text(json.dumps(sorted(done)))
