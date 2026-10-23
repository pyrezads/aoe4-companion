"""
What the official Age of Empires services give for a player's matches (run on GitHub Actions).

World's Edge "community" API (aoe-api.worldsedgelink.com, title=age4): the game's own
service, used by the official stats page. Also tries the ageofempires.com stats API.

    python tools/probe_official.py [profile_id]
"""
import json
import os
import sys
import urllib.parse
import urllib.request

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 6979980
GH = os.environ.get("GITHUB_ACTIONS") == "true"
UA = {"User-Agent": "AoE4-Coach/0.1 (personal coaching tool)", "Accept": "application/json"}
WE = "https://aoe-api.worldsedgelink.com/community/leaderboard"


def note(msg, level="notice"):
    print(f"::{level} title=official probe::{msg}" if GH else f"[{level}] {msg}", flush=True)


def get(url, data=None):
    try:
        req = urllib.request.Request(url, headers={**UA, **({"Content-Type": "application/json"} if data else {})},
                                     data=json.dumps(data).encode() if data else None)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return None, str(e)


def shape(v, depth=0):
    if isinstance(v, dict):
        if depth >= 3:
            return "{" + ", ".join(list(v)[:15]) + "}"
        return {k: shape(x, depth + 1) for k, x in list(v.items())[:30]}
    if isinstance(v, list):
        return [shape(v[0], depth + 1), f"x{len(v)}"] if v else []
    if isinstance(v, str) and len(v) > 60:
        return f"str({len(v)})"
    return v if isinstance(v, (int, float, bool)) or v is None else "str"


ids = urllib.parse.quote(json.dumps([PID]))
st, body = get(f"{WE}/getRecentMatchHistory?title=age4&profile_ids={ids}")
note(f"getRecentMatchHistory: HTTP {st}, {len(body)} bytes")
steam = None
if st == 200:
    d = json.loads(body)
    note("top keys: " + ", ".join(d.keys()))
    stats = d.get("matchHistoryStats") or []
    note(f"{len(stats)} matches; match shape: " + json.dumps(shape(stats[0] if stats else {}))[:3000])
    for p in d.get("profiles", []):
        if p.get("profile_id") == PID:
            note("my profile: " + json.dumps(p)[:600])
            steam = p.get("name")                       # "/steam/7656..."
    if stats:
        m = stats[0]
        rr = (m.get("matchhistoryreportresults") or [{}])[0]
        note("report result keys: " + ", ".join(rr.keys()))
        c = rr.get("counters")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except ValueError:
                pass
        note("counters: " + json.dumps(c)[:3500])
        for k in ("options", "slotinfo"):
            v = m.get(k)
            note(f"{k}: {str(v)[:300]}")
        mm = (m.get("matchhistorymember") or [{}])[0]
        note("member: " + json.dumps(mm)[:800])
        items = m.get("matchhistoryitems")
        note(f"items: {str(items)[:400]}")
        urls = m.get("matchurls")
        note(f"matchurls: {str(urls)[:600]}")
else:
    note("says: " + body[:300], "warning")

st, body = get(f"{WE}/GetPersonalStat?title=age4&profile_ids={ids}")
note(f"GetPersonalStat: HTTP {st}, {len(body)} bytes: " + (json.dumps(shape(json.loads(body)))[:2500] if st == 200 else body[:300]))

if steam:
    names = urllib.parse.quote(json.dumps([steam]))
    st, body = get(f"{WE}/GetPersonalStat?title=age4&profile_names={names}")
    note(f"lookup by steam id {steam}: HTTP {st}, {len(body)} bytes")

st, body = get(f"{WE}/getLeaderBoard2?title=age4&leaderboard_id=17&start=1&count=2&sortBy=1")
note(f"getLeaderBoard2: HTTP {st}: {body[:300]}")

# ageofempires.com stats site API
for url, payload in (
    ("https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchList",
     {"gamertag": "", "playerNumber": 0, "game": "ageiv", "profileId": PID, "sortColumn": "dateTime", "sortDirection": "DESC", "page": 1, "recordCount": 10, "matchType": "3"}),
    ("https://api.ageofempires.com/api/ageiv/Leaderboard",
     {"region": 7, "matchType": "unranked", "versus": "players", "teamSize": "1v1", "searchPlayer": "", "page": 1, "count": 2}),
):
    st, body = get(url, payload)
    note(f"{url}: HTTP {st}: {body[:500]}")
