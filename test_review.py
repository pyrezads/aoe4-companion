"""Checks game review on a scripted 12-minute game (no game needed).

Run:  python test_review.py
"""
import json
import pathlib
import tempfile

from buildorder import BuildOrder
from coach import Coach
from review import GameRecorder, list_games, load_game
from tracker import EcoSnapshot

ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


build = BuildOrder.from_json(json.load(open("data/test_build.json", encoding="utf-8")))
tmp = pathlib.Path(tempfile.mkdtemp())
saved_rows = []
rec = GameRecorder(str(tmp), on_saved=saved_rows.append)
coach = Coach(build)

# A game: a villager every 20 s, except none from 6:00 to 7:20 (TC idle);
# housed 3:00-3:30; 2 idle villagers 4:00-4:40; Feudal at 5:30 (build says 4:20).
vil, wall = 6, 1000.0
for t in range(0, 12 * 60 + 1):
    if t and t % 20 == 0 and not (360 <= t < 440):
        vil += 1
    idle = 2 if 240 <= t < 280 else 0
    working = vil - idle
    food = min(working, 9)
    wood = min(max(working - food, 0), 7)
    gold = working - food - wood
    pop = vil + 4
    cap = pop + (0 if 180 <= t < 210 else 6)
    snap = EcoSnapshot(clock=f"{t // 60:02d}:{t % 60:02d}", seconds=t, pop=pop, pop_cap=cap, idle=idle,
                       villagers={"food": food, "wood": wood, "gold": gold, "stone": 0}, villagers_total=vil,
                       bank={"food": 100, "wood": 900 if 500 <= t < 600 else 100, "gold": 50, "stone": 0}, live=True)
    msg = coach.update(t, snap.villagers, idle, 2 if t >= 330 else 1)
    teams = {"me": {"civ": "zhu_xis_legacy", "age": 2 if t >= 330 else 1}, "allies": [],
             "enemies": [{"civ": "french", "age": 2 if t >= 300 else 1}], "mode": "rm_1v1"}
    assert rec.update(snap, wall + t, coach=msg, teams=teams, build=build) is None
print("While playing")
check(len(rec.samples) == 721, "one sample per game second")

print("A new game starts (clock back to 0:05): the last one is saved")
path = rec.update(EcoSnapshot(clock="00:05", seconds=5, villagers={"food": 6, "wood": 0, "gold": 0, "stone": 0},
                              villagers_total=6, idle=0, pop=6, pop_cap=10, bank={}, live=True), wall + 800)
check(path and pathlib.Path(path).exists(), "saved to games/")
g = json.loads(pathlib.Path(path).read_text())
s = g["summary"]
check(s["length"] == "12:00", f"length 12:00 ({s['length']})")
check(s["ages"]["Feudal"]["you"] == "5:30" and s["ages"]["Feudal"]["build"] == "4:20" and s["ages"]["Feudal"]["late"] == 70,
      f"Feudal 5:30 vs the build's 4:20, 70 s late ({s['ages']['Feudal']})")
check(s["villagers_at"]["10:00"]["you"] == 32, f"villagers at 10:00 ({s['villagers_at']['10:00']})")
check(s["housed_time"] == 30, f"housed 30 s ({s['housed_time']})")
check(s["idle_time"] == 40 and s["idle_villager_seconds"] == 80, f"idle 40 s, 80 villager-seconds ({s['idle_time']}, {s['idle_villager_seconds']})")
check(s["floating_time"] == 100, f"floating 100 s ({s['floating_time']})")
check(s["longest_no_villager"] >= 90, f"longest without a new villager ≥ 90 s ({s['longest_no_villager']} at {s['longest_no_villager_at']})")
check(any("No new villager" in t for t in s["tips"]) and len(s["tips"]) <= 3, f"tips: {s['tips']}")
check(g["civ"] == "zhu_xis_legacy" and g["enemies"] == ["french"] and g["build"]["title"] == "Test timing attack", "civ, enemies and build saved")
check(any(e["text"] == "Enemy reached Feudal" for e in g["events"]), "enemy age-up recorded")
check(g["build_curve"][0] == [0, 6] and len(g["series"]) == 721, "build curve and the villager series saved for the chart")
check(saved_rows and saved_rows[0]["feudal"] == "5:30", "the app is told a game was saved")

print("Listing and loading")
rows = list_games(str(tmp))
check(len(rows) == 1 and rows[0]["id"] == g["id"], "list of saved games")
check(load_game(str(tmp), g["id"])["summary"]["length"] == "12:00", "load one game")
check(load_game(str(tmp), "../etc/passwd") is None, "game ids can't reach outside the folder")

print("Short games and quitting")
rec2 = GameRecorder(str(tmp))
for t in range(0, 60):
    rec2.update(EcoSnapshot(seconds=t, villagers_total=6, villagers={}, bank={}, live=True), 5000 + t)
check(rec2.finish() is None, "games under 2 minutes aren't saved")
rec3 = GameRecorder(str(tmp))
for t in range(0, 200):
    rec3.update(EcoSnapshot(seconds=t, villagers_total=6 + t // 20, villagers={}, bank={}, live=True, idle=0), 9000 + t)
check(rec3.update(EcoSnapshot(live=False), 9000 + 200 + 91) is not None, "game over after 90 s without a HUD")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
