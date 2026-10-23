"""
Offline checks for the live pieces: build-order parsing and comparison,
the economy tracker, and the aoe4world briefing (with recorded API shapes).

Run:  python test_live.py
"""

import numpy as np
from PIL import Image

import buildorder as bo_mod
from hud_reader import HudReader, HudState
from tracker import EcoTracker
import aoe4world as aw

ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""))


# ── build order: same shape as aoe4guides.com/api/builds/<id> ──────────
BUILD = {
    "title": "English | 2TC | Team Games", "civ": "ENG",
    "ageTimings": {"feudal": {"e": False, "t": 280}},
    "steps": [
        {"age": 1, "type": "age", "steps": [
            {"time": "0:00", "food": "6", "gold": "+", "wood": "", "stone": "",
             "description": 'Pick 1 <img src="https://aoe4guides.com/assets/pictures/resource/sheep.webp" class="icon-none" />'},
            {"time": "0:25", "food": "5+", "gold": "1", "wood": "", "stone": "",
             "description": 'Build a <img src="/assets/pictures/building_economy/house.webp" class="icon-default" title="House" />'},
            {"time": "2:05", "food": "10", "gold": "2", "wood": "+", "stone": "",
             "description": 'Force drop <img src="/assets/pictures/resource/resource_food.webp" title="Food" /> to age up earlier'},
            {"description": "", "gameplan": "Scout your opponents<br>Plan your landmark"},
        ]},
        {"age": 2, "type": "age", "steps": [
            {"time": "4:40", "food": "4+", "gold": "2", "wood": "9", "stone": "",
             "description": 'Move 4 villagers to the <img src="/assets/pictures/building_economy/town-center.webp" title="Town Center" /> spot'},
            {"kind": "alternatives", "paths": [[], []]},
        ]},
    ],
}
bo = bo_mod.BuildOrder.from_json(BUILD)
check("build parses steps", len(bo.steps) == 4, f"{len(bo.steps)} steps")
check("icons become words", "House" in bo.steps[1].text and "sheep" in bo.steps[0].text, bo.steps[1].text)
check("gameplan kept", bo.gameplans and "Scout" in bo.gameplans[0], bo.gameplans[0] if bo.gameplans else "")
check("age timing read", bo.age_times.get("feudal") == 280)
c = bo.compare(130, {"food": 8, "wood": 0, "gold": 2, "stone": 0})   # 2:10, step 3 wants 10 food
check("compare finds step at 2:10", c["step"] is bo.steps[2])
check("compare: 2 short on food", c["gaps"] == {"food": 2}, c["summary"])
check("compare: next step at 4:40", c["next"].seconds == 280)
check("build id from link", bo_mod.build_id_from("https://aoe4guides.com/builds/AbC123xyz") == "AbC123xyz")

# ── tracker on the real screenshots, with fake wall-clock times ───────
reader = HudReader()
frames = ["s05", "s06", "s07", "s08", "s09", "s10"]   # 01:00 → 03:50
tr = EcoTracker()
t = 1000.0
snap = None
for n in frames:
    hud = reader.read(np.asarray(Image.open(f"calib/{n}.png").convert("RGB")).astype(np.int16))
    t += (hud.seconds or 0) - (tr.last_good.seconds if tr.last_good and tr.last_good.seconds else 0)
    snap = tr.update(hud, now=t)
check("tracker live on last frame", snap.live and snap.clock == "03:50")
check("tracker gold income estimate", 100 < snap.income_est["gold"] < 130, f"{snap.income_est['gold']:.0f}/min")
check("tracker measured gold change", snap.bank_change.get("gold", 0) > 0, f"{snap.bank_change.get('gold', 0):.0f}/min")
check("idle alert present", any("idle" in a.text for a in snap.alerts), [a.text for a in snap.alerts])

non_game = HudState(confidence=0.05)
check("non-game frame ignored", not tr.update(non_game, now=t + 1).live)

# a misread that jumps the clock backwards is rejected
bad = HudState(clock="00:40", seconds=40 + 200, confidence=1.0)
bad.seconds = 100
check("backwards clock misread rejected", not tr.update(bad, now=t + 2).live)

# housed + floating alerts on a synthetic early-game state
tr2 = EcoTracker()
s = HudState(clock="03:00", seconds=180, pop=20, pop_cap=20, idle=0, confidence=1.0,
             resources={"food": 520, "wood": 120, "gold": 30, "stone": 0},
             villagers={"food": 12, "wood": 6, "gold": 0, "stone": 0})
a = [x.text for x in tr2.update(s, now=1).alerts]
check("housed alert", any("Housed" in x for x in a), a)
check("floating alert", any("Floating food" in x for x in a), a)

# ── aoe4world briefing with the real API response shapes ──────────────
client = aw.Aoe4WorldClient(6979980)
LAST = {"game_id": 1, "kind": "rm_4v4", "map": "Dry Arabia", "map_id": 163361, "ongoing": True, "started_at": "x",
        "teams": [[{"name": "A", "profile_id": 1, "civilization": "french", "rating": 1100},
                   {"name": "B", "profile_id": 2, "civilization": "mongols", "rating": 1150}],
                  [{"name": "Walasckazzin", "profile_id": 6979980, "civilization": "zhu_xis_legacy", "rating": 1100},
                   {"name": "C", "profile_id": 3, "civilization": "english", "rating": 1090}]]}
CIVS = {"data": [{"civilization": "french", "win_rate": 54.7}, {"civilization": "mongols", "win_rate": 53.4},
                 {"civilization": "zhu_xis_legacy", "win_rate": 47.9}, {"civilization": "english", "win_rate": 47.6}]}
PROFILE = {"name": "Walasckazzin", "modes": {"rm_team": {"rating": 1100, "rank_level": "platinum_2",
           "civilizations": [{"civilization": "zhu_xis_legacy", "games_count": 135, "win_rate": 56.3}]}}}


def fake_get(path, params=None, ttl=0):
    if path.endswith("/games/last"):
        return LAST
    if "/civilizations" in path:
        return CIVS
    if path.startswith("/players/"):
        return PROFILE
    return {}


client._get = fake_get
client.load_profile()
m = client.current_match()
check("ongoing match detected", m is not None and m.kind == "rm_4v4")
check("my team found", m and {p.civ for p in m.enemies()} == {"french", "mongols"})
check("bucket from team rating", aw.rating_bucket(client.my_rating_for("rm_4v4"), "rm_4v4") == "1000-1199")
b = client.briefing(m)
print("     briefing:", " | ".join(b.lines))
check("briefing mentions my civ rank", any("Zhu Xi: 47.9%" in l for l in b.lines))
check("briefing has my record", any("135 games" in l for l in b.lines))
check("briefing lists enemies", any(l.startswith("Enemies: French") for l in b.lines))

# ── army tracker (Ctrl+C readings) ─────────────────────────────────────
from tracker import ArmyTracker
at = ArmyTracker()
at.record(200, "03:20", 150, [60, 50, 40])
check("army first reading", at.summary()[0].startswith("03:20  ·  150 military"), at.summary())
at.record(260, "04:20", 193, [60, 8, 6, 38, 39, 34, 8])
check("army growth shown", any(l.startswith("+43 since 03:20") for l in at.summary()), at.summary())
at.record(290, "04:50", 120, [40, 30, 50])
check("army loss flagged", any(l.startswith("Lost 73 units in 0:30") for l in at.summary()), at.summary())
check("army peak kept", any("Peak this game: 193" in l for l in at.summary()))
at.record(20, "00:20", 2, [2])
check("new game resets army", at.peak == 2)

# ── player list, units, counters (real screenshots of three games) ──────
import json as _json
from scoreboard import ScoreboardReader
from units import UnitDB, UnitRecognizer
from combat import fight, best_counters
sbr = ScoreboardReader()
labels = _json.load(open("calib/scoreboard_labels.json"))
for fname, civs in labels.items():
    a = np.asarray(Image.open(f"calib/{fname}").convert("RGB")).astype(np.int16)
    sb = sbr.read(a)
    check(f"player list {fname}: civs", [r.civ for r in sb.rows] == civs, [r.civ for r in sb.rows])
    check(f"player list {fname}: 4 allies then 4 enemies", [bool(r.enemy) for r in sb.rows] == [False] * 4 + [True] * 4)
    check(f"player list {fname}: you are row 4", sb.me == 3)
db = UnitDB()
rec = UnitRecognizer(db)
s11 = np.asarray(Image.open("calib/s11.png").convert("RGB")).astype(np.int16)
m = rec.read_cards(s11, [7, 80, 30, 52, 14], ["zhu_xis_legacy"])
names = [db.base_name(x.unit_id, "zhu_xis_legacy") for x in m]
check("army cards recognised", names == ["Horseman", "Spearman", "Zhuge Nu", "Crossbowman", "Grenadier"], names)
g = lambda u, c: db.get(u, c)
check("spearmen beat knights", fight([(g("spearman-2", "french"), 20)], [(g("knight-2", "english"), 10)]).you_win)
check("archers lose to knights", not fight([(g("archer-2", "french"), 20)], [(g("knight-2", "english"), 10)]).you_win)
top = [db.base_name(u, "english") for _, u, _ in best_counters(db, "english", 2, [(g("knight-2", "french"), 10)])]
check("English answer to knights is spearmen", top and top[0] == "Spearman", top)

# ── opponent scouting (shapes recorded from aoe4world on 2026-10-23) ───
def _hist(start_games, start_wins, start_rating, results):
    h, g, w, r = {}, start_games, start_wins, start_rating
    for i, res in enumerate(results):
        g += 1; w += res; r += 15 if res else -15
        h[str(1792700000 + i * 900)] = {"games_count": g, "wins_count": w, "rating": r, "streak": 0, "drops_count": 0}
    return h

def _p(name, pid, civ, team_rating, rank, wr, games, civrows, hist, prev=None, drops=0, inp="keyboard"):
    return {"name": name, "profile_id": pid, "civilization": civ, "rating": team_rating, "input_type": inp,
            "modes": {"rm_team": {"rating": team_rating, "rank_level": rank, "win_rate": wr, "games_count": games,
                                  "drops_count": drops, "civilizations": civrows, "rating_history": hist,
                                  "previous_seasons": [prev] if prev else []}}}

ME = _p("Walasckazzin", 6979980, "zhu_xis_legacy", 1100, "platinum_2", 53.4, 103,
        [{"civilization": "zhu_xis_legacy", "games_count": 135, "pick_rate": 80, "win_rate": 56.3}], {})
HOT = _p("Hericium", 10969574, "knights_templar", 1329, "diamond_2", 55.6, 135,
         [{"civilization": "knights_templar", "games_count": 182, "pick_rate": 51.3, "win_rate": 58.2}],
         _hist(125, 68, 1250, [1, 1, 1, 1, 1, 1, 0, 1, 1, 1]),
         prev={"rating": 1075, "rank_level": "platinum_1", "season": 13})
OFF = _p("slimybeaver", 8839703, "delhi_sultanate", 1061, "gold_3", 47.0, 300,
         [{"civilization": "english", "games_count": 280, "pick_rate": 93, "win_rate": 48.0}],
         _hist(290, 140, 1120, [0, 0, 1, 0, 0, 0, 1, 0, 0, 0]), drops=30, inp="controller")
ALLY = _p("Mitsuha", 25001465, "sengoku_daimyo", 1154, "platinum_3", 51.0, 90,
          [{"civilization": "sengoku_daimyo", "games_count": 60, "pick_rate": 66, "win_rate": 52.0}], {})
GAME = {"game_id": 9, "kind": "rm_4v4", "map": "Dry Arabia", "map_id": 1, "ongoing": True, "started_at": "x",
        "teams": [[HOT, OFF], [ME, ALLY]]}
H2H = {"games": [{"teams": [[{"profile_id": 6979980, "result": "win"}], [{"profile_id": 10969574, "result": "loss"}]]},
                 {"teams": [[{"profile_id": 6979980, "result": "loss"}], [{"profile_id": 10969574, "result": "win"}]]},
                 {"teams": [[{"profile_id": 6979980, "result": "loss"}], [{"profile_id": 10969574, "result": "win"}]]}]}

def fake_get2(path, params=None, ttl=0):
    if path.endswith("/games/last"):
        return GAME
    if path.endswith("/games") and params and params.get("opponent_profile_id") == 10969574:
        return H2H
    if path.endswith("/games"):
        return {"games": []}
    if path.startswith("/players/"):
        return {"name": "Walasckazzin", "modes": ME["modes"]}
    return {}

c2 = aw.Aoe4WorldClient(6979980)
c2._get = fake_get2
c2.load_profile()
rep = aw.scouting_report(c2, c2.current_match())
for l in rep["summary"] + rep["enemies"] + rep["allies"]:
    print("    ", l)
check("scout: biggest threat first", rep["enemies"][0].startswith("Biggest threat: Hericium"))
check("scout: hot + civ main + climbing flagged", all(k in rep["enemies"][0] for k in ("hot streak", "strong on this civ", "climbing fast")))
check("scout: head-to-head", "vs you 1-2" in rep["enemies"][0])
check("scout: off-civ, cold, drops, controller", all(k in rep["enemies"][1] for k in ("off-civ", "cold streak", "drops games often", "on controller")))
check("scout: recent form", "last 9: 8-1" in rep["enemies"][0], rep["enemies"][0])
check("scout: team rating summary", rep["summary"] and "1127 vs theirs 1195" in rep["summary"][0], rep["summary"])

print("\nALL PASS" if ok_all else "\nSOME CHECKS FAILED")
raise SystemExit(0 if ok_all else 1)
