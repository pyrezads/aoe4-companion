"""
Decodes the official API's numbers (civilization_id, matchtype_id, resulttype, map names)
by lining up a player's recent matches with the same matches' known details. The result
is a small table the app ships with (data/official_ids.json); the app itself only talks
to the official service. Run on GitHub Actions.

    python tools/probe_official2.py [profile_id ...]
"""
import collections
import json
import os
import sys
import urllib.parse
import urllib.request

PIDS = [int(a) for a in sys.argv[1:]] or [6979980]
GH = os.environ.get("GITHUB_ACTIONS") == "true"
UA = {"User-Agent": "AoE4-Coach/0.1 (personal coaching tool)", "Accept": "application/json"}
WE = "https://aoe-api.worldsedgelink.com/community/leaderboard"


def note(msg, level="notice"):
    print(f"::{level} title=official ids::{msg}" if GH else f"[{level}] {msg}", flush=True)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


civ = collections.defaultdict(collections.Counter)
kind = collections.defaultdict(collections.Counter)
result = collections.defaultdict(collections.Counter)
maps = collections.defaultdict(collections.Counter)
matched = 0
for pid in PIDS:
    off = get(f"{WE}/getRecentMatchHistory?title=age4&profile_ids={urllib.parse.quote(json.dumps([pid]))}")
    known = {}
    for page in range(1, 4):
        g = get(f"https://aoe4world.com/api/v0/players/{pid}/games?limit=50&page={page}")
        for x in g.get("games", []):
            known[x["game_id"]] = x
    for m in off.get("matchHistoryStats", []):
        k = known.get(m["id"])
        if not k:
            continue
        matched += 1
        kind[m["matchtype_id"]][k["kind"]] += 1
        maps[m["mapname"]][k["map"]] += 1
        players = {p["player"]["profile_id"]: p["player"] for t in k["teams"] for p in t}
        for r in m.get("matchhistoryreportresults", []):
            p = players.get(r["profile_id"])
            if p:
                civ[r["civilization_id"]][p["civilization"]] += 1
                result[r["resulttype"]][p["result"]] += 1

best = lambda d: {str(k): v.most_common(1)[0][0] for k, v in d.items()}
table = {"civilization_id": best(civ), "matchtype_id": best(kind), "resulttype": best(result), "mapname": best(maps)}
note(f"{matched} matches lined up")
note("ids: " + json.dumps(table)[:3800])
os.makedirs("data", exist_ok=True)
json.dump(table, open("data/official_ids.json", "w"), indent=1)

# other official endpoints, briefly
pid = PIDS[0]
ids = urllib.parse.quote(json.dumps([pid]))
try:
    ps = get(f"{WE}/GetPersonalStat?title=age4&profile_ids={ids}")
    sg = (ps.get("statGroups") or [{}])[0]
    note("GetPersonalStat: keys " + ", ".join(ps.keys()) + " | leaderboardStats[0]: " + json.dumps((ps.get("leaderboardStats") or [{}])[0])[:600]
         + " | members[0]: " + json.dumps((sg.get("members") or [{}])[0])[:300])
except Exception as e:
    note(f"GetPersonalStat failed: {e}", "warning")
try:
    steam = "/steam/76561199110245053"
    ps = get(f"{WE}/GetPersonalStat?title=age4&profile_names={urllib.parse.quote(json.dumps([steam]))}")
    sg = (ps.get("statGroups") or [{}])[0]
    note("Steam lookup → profile " + json.dumps([(m.get('profile_id'), m.get('alias')) for m in sg.get('members', [])])[:300])
except Exception as e:
    note(f"Steam lookup failed: {e}", "warning")
