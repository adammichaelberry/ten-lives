"""Shared ntfy helpers (JSON publish = full Unicode support, unlike headers)."""
import os, json, datetime, requests
from zoneinfo import ZoneInfo

TOPIC = os.environ.get("NTFY_TOPIC", "").strip() or "tenlives-k7q2m9"
SYD, PDX = ZoneInfo("Australia/Sydney"), ZoneInfo("America/Los_Angeles")

def app_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")           # owner/name
    return f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if "/" in repo else None

def send(title, message, tags=("football",), click=None, priority=3, actions=None):
    """priority: 1 min … 3 default … 5 max. Returns True if ntfy accepted it."""
    payload = {"topic": TOPIC, "title": title, "message": message, "tags": list(tags), "priority": priority}
    if click: payload["click"] = click
    if actions: payload["actions"] = actions
    try:
        r = requests.post("https://ntfy.sh", json=payload, timeout=20)
        ok = r.status_code == 200
        print(("  sent: " if ok else f"  ntfy {r.status_code}: ") + title)
        return ok
    except Exception as e:
        print("  ntfy failed:", e); return False

def sent_titles(hours=24):
    """Titles already published to our topic (ntfy caches ~12h) — used for de-duplication."""
    try:
        r = requests.get(f"https://ntfy.sh/{TOPIC}/json", params={"poll": 1, "since": f"{hours}h"}, timeout=20)
        return {json.loads(l).get("title", "") for l in r.text.splitlines() if l.strip() and '"event":"message"' in l}
    except Exception as e:
        print("  couldn't read sent titles:", e); return set()

def quiet_now():
    q = os.environ.get("QUIET_HOURS", "").strip()
    if not q: return False
    a, b = (int(x) for x in q.split("-")); h = datetime.datetime.now(SYD).hour
    return (a <= h or h < b) if a > b else (a <= h < b)

def both_times(iso):
    d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    f = lambda tz: d.astimezone(tz).strftime("%a %I:%M %p").replace(" 0", " ")
    return f"{f(SYD)} Newcastle · {f(PDX)} Portland"
