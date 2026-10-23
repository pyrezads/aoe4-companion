"""Checks match history parsing on official-API-shaped data (no internet needed).

Run:  python test_history.py
"""
import base64
import json
import tempfile
import zlib

import history
from history import History, insights, parse_match, slim, steam_id_from

ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


def packed(d):
    # like the real service: a few bytes after the JSON
    return base64.b64encode(zlib.compress(json.dumps(d).encode() + b"\x00\x01junk")).decode()


ME = 6979980


def match(mid, start, mapkey, loc, mtype, my_civ, my_res, my_old, my_new, enemy_civ, counters=None):
    other_res = 1 - my_res
    return {
        "id": mid, "mapname": "generated_map", "matchtype_id": mtype, "description": "AUTOMATCH",
        "startgametime": start, "completiontime": start + 1500,
        "options": packed({"mapName": mapkey, "localizedMapName": loc}),
        "matchhistoryreportresults": [
            {"profile_id": ME, "resulttype": my_res, "teamid": 0, "civilization_id": my_civ,
             "counters": json.dumps(counters or {"gt": 1500, "totalcmds": 3000, "unitprod": 120, "sqkill": 80, "bprod": 40, "upg": 12})},
            {"profile_id": 111, "resulttype": other_res, "teamid": 1, "civilization_id": enemy_civ,
             "counters": json.dumps({"gt": 1500, "totalcmds": 1500, "unitprod": 90})}],
        "matchhistorymember": [
            {"profile_id": ME, "oldrating": my_old, "newrating": my_new},
            {"profile_id": 111, "oldrating": 1200, "newrating": 1200 + (my_old - my_new)}],
        "matchurls": [{"profile_id": ME, "url": f"https://rl0aoelivemk2blob.blob.core.windows.net/cloudfiles/x/M_{mid}.gz", "datatype": 0}],
    }


RAW = {"result": {"code": 0}, "profiles": [
    {"profile_id": ME, "name": "/steam/76561199110245053", "alias": "Walasckazzin", "country": "us", "level": 577},
    {"profile_id": 111, "name": "/steam/76561190000000001", "alias": "Opponent", "country": "de", "level": 10}],
    "matchHistoryStats": [
        match(3, 3000, "lipany", "Lipany", 1, 2121950, 1, 1200, 1215, 131384),
        match(2, 2000, "rolling_rivers", " ", 1, 2121950, 0, 1214, 1200, 129267),
        match(1, 1000, "king_of_hill", "King of The Hill", 22, 106553, 1, 0, 0, 131384)]}

profiles = {p["profile_id"]: p for p in RAW["profiles"]}
print("One match")
m = parse_match(RAW["matchHistoryStats"][0], ME, profiles)
check(m["map"] == "Lipany" and m["mode"] == "rm_1v1" and m["duration"] == 1500, "map from the match settings, mode, length")
check(m["civ"] == "zhu_xis_legacy" and m["enemies"] == ["french"], "civs decoded")
check(m["result"] == "win" and m["rating"] == 1215 and m["rating_diff"] == 15, "result and rating change")
me = next(p for p in m["players"] if p["profile_id"] == ME)
check(me["apm"] == 120 and me["stats"]["units made" if False else "unitprod"] == 120, "actions per minute and stats")
check(m["replay"].endswith("M_3.gz"), "official replay link")
m2 = parse_match(RAW["matchHistoryStats"][1], ME, profiles)
check(m2["map"] == "Danube River", "empty map name falls back to a known name")
cn = parse_match(dict(RAW["matchHistoryStats"][0], options=packed({"mapName": "dry_arabia", "localizedMapName": "\u5e72\u71e5\u963f\u62c9\u4f2f"})), ME, profiles)
check(cn["map"] == "Dry Arabia", f"map named in another language uses the map key ({cn['map']})")
m3 = parse_match(RAW["matchHistoryStats"][2], ME, profiles)
check(m3["mode"] == "qm_3v3" and m3["rating_diff"] is None, "quick match without rating change")
check("players" in slim(m) and "stats" not in slim(m)["players"][0], "list version leaves out per-player stats")

print("Insights")
ms = [parse_match(x, ME, profiles) for x in RAW["matchHistoryStats"]]
ins = insights(ms)
check(ins["games"] == 3 and ins["wins"] == 2 and ins["win_rate"] == 67, f"2 of 3 won ({ins['win_rate']}%)")
check(ins["streak"] == {"result": "win", "count": 1}, f"streak {ins['streak']}")
zx = next(r for r in ins["by_civ"] if r["key"] == "zhu_xis_legacy")
check(zx["games"] == 2 and zx["wins"] == 1, "win rate by civ")
fr = next(r for r in ins["vs_civ"] if r["key"] == "french")
check(fr["games"] == 2 and fr["wins"] == 2, "win rate against French")
check(ins["rating"]["rm_1v1"] == [[3500, 1200], [4500, 1215]], f"rating over time {ins['rating']}")

print("Fetching and the cache")
calls = []
def fake_get(url, timeout=25):
    calls.append(url)
    if "getRecentMatchHistory" in url:
        return RAW
    return {"leaderboardStats": [{"leaderboard_id": 17, "rating": 1215, "highestrating": 1250, "wins": 56, "losses": 35,
                                  "streak": 1, "rank": 11848, "ranktotal": 67907, "regionrank": 1817, "lastmatchdate": 4500}]}
history._get = fake_get
tmp = tempfile.mkdtemp()
h = History(tmp)
d = h.load(ME)
check(d["profile"]["name"] == "Walasckazzin" and d["profile"]["steam"] == "76561199110245053", "profile name and Steam id")
check(len(d["matches"]) == 3 and d["matches"][0]["id"] == 3, "newest first")
check(d["leaderboards"][0]["mode"] == "rm_1v1" and d["leaderboards"][0]["rank"] == 11848, "leaderboard rating and rank")
n = len(calls)
h2 = History(tmp)
h2.load(ME)
check(len(calls) == n, "a fresh cache isn't fetched again")
def down(url, timeout=25):
    raise OSError("offline")
history._get = down
d = History(tmp, refresh_after=0).load(ME)
check(d["offline"] and len(d["matches"]) == 3, "offline: last saved history")
check(h.match(ME, 2)["map"] == "Danube River", "one match by id")

print("Steam")
check(steam_id_from("https://steamcommunity.com/profiles/76561199110245053/") == "76561199110245053", "Steam profile link")
check(steam_id_from("76561199110245053") == "76561199110245053", "bare Steam id")
check(steam_id_from("https://steamcommunity.com/id/somename") is None, "custom Steam links need the number")
try:
    history.save_replay("https://evil.example.com/x.gz", 1, tmp)
    check(False, "only official replay links are downloaded")
except ValueError:
    check(True, "only official replay links are downloaded")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
