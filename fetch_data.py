#!/usr/bin/env python3
"""
Survivor-pool data fetcher.

Pulls, from ESPN's public (no-key) endpoints:
  * FPI power index  -> every FBS team, conference, logo, colours, FPI rating, AP rank
  * Scoreboard        -> full regular-season schedule (weeks 1-15), DraftKings spreads,
                         results for completed games
and writes `data.js` (window.CFB_DATA = {...}) next to app.html.

Also pulls Sagarin ratings (sagarin.com) and, if the comp spreadsheet is in the folder
(or given with --picks), everyone's picks from it.

Usage:
    python fetch_data.py                          # writes data.js
    python fetch_data.py --bundle                 # also writes tipping.html = index.html with data inlined
    python fetch_data.py --picks path/to/comp.xlsx
    python fetch_data.py --notify                 # also compare odds with last run and ping ntfy on big moves

Environment (set as GitHub Actions secrets/variables when hosted):
    SHEET_URL   direct-download link to the comp spreadsheet (Google Sheets: .../export?format=xlsx)
    NTFY_TOPIC  ntfy.sh topic to post alerts to (keep it unguessable)
    MOVE_ALERT  points of line movement that triggers an alert (default 3)
Requires: pip install requests openpyxl
"""
import json, os, sys, time, datetime, re, pathlib
import requests

YEAR = 2026
WEEK1_STARTS = "2026-09-01"     # ESPN lumps the Week 0 weekend into "Week 1"; anything earlier becomes week 0 (not pickable)
WEEKS = range(1, 16)
POOL_CONFS = {"Big Ten", "SEC", "Big 12", "ACC"}
POOL_EXTRA = {"Notre Dame"}          # independents that count
PICKS_PER_WEEK = {1: 6, 2: 6, 3: 6}   # everything else: 5
DEFAULT_PICKS = 5
ME = "B Spak"                         # my column in the comp spreadsheet
PICKS_XLSX = "Power_4_Survivor_2026.xlsx"
SHEET_URL = os.environ.get("SHEET_URL", "").strip()
NTFY_TOPIC = (os.environ.get("NTFY_TOPIC", "").strip() or "tenlives-k7q2m9")
MOVE_ALERT = float(os.environ.get("MOVE_ALERT", "3") or 3)
COMFORT = 21

def ntfy(title, body, tags="football", url=None, priority="default"):
    if not NTFY_TOPIC:
        print("  (no NTFY_TOPIC set — would notify:", title, "|", body.replace(chr(10), " / "), ")"); return
    h = {"Title": title, "Tags": tags, "Priority": priority}
    if url: h["Click"] = url
    try: requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode(), headers=h, timeout=20)
    except Exception as e: print("  ntfy failed:", e)

HERE = pathlib.Path(__file__).parent
S = requests.Session()
# (default requests user-agent works; ESPN blocks some custom ones)

def get(url, **params):
    for attempt in range(3):
        r = S.get(url, params=params, timeout=30)
        if r.ok:
            return r.json()
        time.sleep(1.5)
    r.raise_for_status()

# ---------- teams / FPI ----------
print("Fetching FPI + team list…")
fpi = get("https://site.web.api.espn.com/apis/fitt/v3/sports/football/college-football/powerindex",
          region="us", lang="en", limit=200)
names = fpi["categories"][0]["names"]          # fpi, fpirank, ..., numwins, numlosses
teams = {}
for row in fpi["teams"]:
    t = row["team"]
    cat = {c["name"]: c for c in row["categories"]}
    vals = dict(zip(names, cat["fpi"]["values"]))
    ap = None
    if "resume" in cat:
        rn = fpi["categories"][1]["names"]
        rv = dict(zip(rn, cat["resume"]["values"]))
        ap = int(rv.get("APRank/CFPRank") or 0) or None
    conf = (t.get("group") or {}).get("shortName", "")
    # the app has a dark background, so prefer ESPN's "dark" logo variant (some default logos are solid black)
    logo = f"https://a.espncdn.com/i/teamlogos/ncaa/500-dark/{t['id']}.png"
    teams[t["id"]] = {
        "id": t["id"], "name": t["shortDisplayName"], "full": t["displayName"],
        "abbr": t["abbreviation"], "mascot": t["name"],
        "conf": conf, "logo": logo,
        "color": "#" + t.get("color", "333333"), "alt": "#" + t.get("alternateColor", "cccccc"),
        "fpi": round(vals.get("fpi", 0), 2), "fpiRank": int(vals.get("fpirank", 0)),
        "ap": ap, "wins": int(vals.get("numwins", 0)), "losses": int(vals.get("numlosses", 0)),
        "pool": conf in POOL_CONFS or t["shortDisplayName"] in POOL_EXTRA,
    }
print(f"  {len(teams)} FBS teams, {sum(t['pool'] for t in teams.values())} in the pool")

# ---------- schedule + lines ----------
games, week_dates = [], {}
for w in WEEKS:
    d = get("https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
            groups=80, limit=400, week=w, seasontype=2, dates=YEAR)
    if not week_dates:
        for e in d["leagues"][0]["calendar"][0]["entries"]:
            week_dates[int(e["value"])] = e["startDate"][:10]
    for ev in d.get("events", []):
        c = ev["competitions"][0]
        home = next(x for x in c["competitors"] if x["homeAway"] == "home")
        away = next(x for x in c["competitors"] if x["homeAway"] == "away")
        spread = None      # from the HOME team's perspective (negative = home favoured)
        if c.get("odds"):
            o = c["odds"][0]
            spread = o.get("spread")
            if spread is None and o.get("details"):
                m = re.match(r"([A-Z&]+)\s*(-?[\d.]+)", o["details"])
                if m:
                    v = -abs(float(m.group(2)))
                    spread = v if m.group(1) == home["team"]["abbreviation"] else -v
        st = ev["status"]["type"]
        for side in (home, away):                     # register non-FBS opponents too
            tid = side["team"]["id"]
            if tid not in teams:
                teams[tid] = {"id": tid, "name": side["team"]["shortDisplayName"],
                              "full": side["team"]["displayName"], "abbr": side["team"]["abbreviation"],
                              "mascot": side["team"].get("name", ""), "conf": "FCS",
                              "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500-dark/{tid}.png", "color": "#777777", "alt": "#cccccc",
                              "fpi": None, "fpiRank": None, "ap": None, "wins": 0, "losses": 0,
                              "pool": False, "fbs": False}
        games.append({
            "id": ev["id"], "week": 0 if (w == 1 and ev["date"][:10] < WEEK1_STARTS) else w, "date": ev["date"],
            "home": home["team"]["id"], "away": away["team"]["id"],
            "neutral": bool(c.get("neutralSite")),
            "spread": spread,
            "completed": bool(st.get("completed")),
            "homeScore": int(home["score"]) if st.get("completed") else None,
            "awayScore": int(away["score"]) if st.get("completed") else None,
        })
    print(f"  week {w:2d}: {len(d.get('events', []))} games")

for t in teams.values():
    t.setdefault("fbs", True)


# ---------- Sagarin ----------
def norm(s):
    return re.sub(r"[^a-z]", "", s.lower().replace("state", "st").replace("&", "and"))
SAG_ALIASES = {"Miami-Florida": "Miami", "Mississippi": "Ole Miss", "Southern California": "USC",
               "Central Florida(UCF)": "UCF", "Army West Point": "Army", "Appalachian State": "App State",
               "Louisiana-Lafayette": "Louisiana", "Connecticut": "UConn", "San Jose State": "San José St",
               "Miami-Ohio": "Miami (OH)", "Fla. International": "Florida Intl", "Sam Houston State": "Sam Houston",
               "LouisianaMonroe(ULM)": "UL Monroe"}
sag_hfa = None
try:
    print("Fetching Sagarin…")
    txt = re.sub(r"<[^>]+>", "", S.get("http://sagarin.com/sports/cfsend.htm", timeout=30).text)
    m = re.search(r"HOME ADVANTAGE=\[\s*([\d.]+)\]", txt)
    sag_hfa = float(m.group(1)) if m else None
    by_name = {}
    for t in teams.values():
        for k in (t["name"], t["full"], t["full"].replace(" " + t["mascot"], "")):
            by_name.setdefault(norm(k), t)
    seen = set(); n = 0
    for rk, name, div, rating in re.findall(r"^\s*(\d+)\s+(.+?)\s+([A-Z]{1,2})\s+=\s+([\d.]+)\s+", txt, re.M):
        name = name.strip()
        if name in seen or div != "A": continue
        seen.add(name)
        t = by_name.get(norm(SAG_ALIASES.get(name, name)))
        if t: t["sag"] = float(rating); t["sagRank"] = int(rk); n += 1
        else: print("   unmatched Sagarin team:", name)
    print(f"  matched {n} Sagarin ratings, HFA {sag_hfa}")
except Exception as e:
    print("  Sagarin unavailable:", e)

# ---------- comp picks (spreadsheet) ----------
picks = None
if SHEET_URL:
    try:
        r = S.get(SHEET_URL, timeout=60); r.raise_for_status()
        (HERE / PICKS_XLSX).write_bytes(r.content); print(f"  downloaded comp sheet ({len(r.content)//1024} KB)")
    except Exception as e:
        print("  couldn't download SHEET_URL, using local copy if any:", e)
xlsx = next((a for a in sys.argv[1:] if a.endswith(".xlsx")), None) or (str(HERE / PICKS_XLSX) if (HERE / PICKS_XLSX).exists() else None)
if xlsx:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx, data_only=True); ws = wb["Table"]
        cols = {c.column: str(c.value).strip() for c in ws[1] if c.value and 3 <= c.column <= 17}
        pool_by_name = {}
        for t in teams.values():
            if t["pool"]:
                for k in (t["name"], t["full"], t["full"].replace(" " + t["mascot"], ""), t["abbr"]):
                    pool_by_name.setdefault(norm(k), t["id"])
        SHEET_ALIASES = {"Pitt": "Pittsburgh"}
        players = {name: {} for name in cols.values()}
        for row in ws.iter_rows(min_row=4, max_row=ws.max_row, min_col=2, max_col=17):
            tname = row[0].value
            if not tname or not isinstance(tname, str): continue
            tid = pool_by_name.get(norm(SHEET_ALIASES.get(tname, tname)))
            if not tid: print("   unmatched sheet team:", tname); continue
            for c in row[1:]:
                if c.value is not None and c.column in cols:
                    try: w = int(c.value)
                    except (TypeError, ValueError): continue
                    players[cols[c.column]].setdefault(str(w), []).append(tid)
        picks = {"me": ME, "players": [{"name": n, "picks": p} for n, p in players.items()]}
        print(f"  read picks for {len(players)} players from {pathlib.Path(xlsx).name}")
    except Exception as e:
        print("  couldn't read picks spreadsheet:", e)

# current week = first week with any uncompleted game
now = datetime.datetime.now(datetime.timezone.utc)
current = next((w for w in WEEKS if any(g["week"] == w and not g["completed"] for g in games)), 15)
print(f"  moved {sum(g['week']==0 for g in games)} Week 0 games out of the pickable weeks")

data = {
    "season": YEAR, "fetched": now.isoformat(timespec="minutes"),
    "currentWeek": current, "weekDates": week_dates,
    "picksPerWeek": PICKS_PER_WEEK, "defaultPicks": DEFAULT_PICKS, "sagHfa": sag_hfa, "picks": picks,
    "teams": teams, "games": games,
}
# ---------- odds movement alerts ----------
snap_path = HERE / "odds_snapshot.json"
prev = json.loads(snap_path.read_text()) if snap_path.exists() else {}
my_picks = set()
if picks:
    me = next((p for p in picks["players"] if p["name"] == ME), None)
    if me: my_picks = set(me["picks"].get(str(current), []))
snap = {}
for g in games:
    if g["week"] != current or g["spread"] is None or g["completed"]: continue
    h, a = teams[g["home"]], teams[g["away"]]
    if not (h["pool"] or a["pool"]): continue
    snap[g["id"]] = g["spread"]
    if "--notify" in sys.argv and g["id"] in prev:
        for t, is_home in ((h, True), (a, False)):
            if not t["pool"]: continue
            old_m = -prev[g["id"]] if is_home else prev[g["id"]]      # expected margin for this team
            new_m = -g["spread"] if is_home else g["spread"]
            mine = t["id"] in my_picks
            crossed = mine and old_m >= COMFORT > new_m
            worse = new_m < old_m
            if (abs(new_m - old_m) >= MOVE_ALERT and (worse or mine)) or crossed:
                opp = a if is_home else h
                ntfy(f"{'YOUR PICK ' if mine else ''}{t['name']} line moved {'↓' if worse else '↑'}{abs(new_m-old_m):.1f}",
                     f"{t['name']} {'vs' if is_home else '@'} {opp['name']}: was {old_m:+.1f}, now {new_m:+.1f}"
                     + (" — no longer a comfortable favourite" if crossed else ""),
                     tags="rotating_light" if mine else "chart_with_downwards_trend",
                     url=f"https://www.espn.com/college-football/game/_/gameId/{g['id']}",
                     priority="high" if mine else "default")
snap_path.write_text(json.dumps(snap))

out = HERE / "data.js"
out.write_text("window.CFB_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
print(f"Wrote {out} ({out.stat().st_size//1024} KB). Current week: {current}")

if "--bundle" in sys.argv:
    html = (HERE / "index.html").read_text()
    bundled = html.replace('<script src="data.js"></script>',
                           "<script>" + out.read_text() + "</script>")
    (HERE / "tipping.html").write_text(bundled)
    print("Wrote tipping.html (single file, share this one)")
