"""Checks the build coach on scripted games (no game or internet needed).

Run:  python test_coach.py
"""
from buildorder import BuildOrder
from coach import Coach, coach_steps

# A build in aoe4guides' JSON shape: steps grouped by age, numbers as strings, "+" = no fixed number.
BUILD_JSON = {
    "title": "Test timing attack", "civ": "ZXL",
    "ageTimings": {"2": {"t": "4:20"}, "3": {"t": "11:00"}},
    "steps": [
        {"age": 1, "steps": [
            {"time": "0:00", "villagers": "6",  "food": "6", "description": "All six on sheep."},
            {"time": "0:45", "villagers": "9",  "food": "6", "wood": "3", "description": "Three to wood, build a house."},
            {"time": "1:30", "villagers": "12", "food": "6", "wood": "3", "gold": "3", "description": "Three to gold."},
            {"time": "2:20", "villagers": "15", "food": "9", "wood": "3", "gold": "3", "description": "Three to berries."},
            {"time": "3:15", "villagers": "18", "food": "9", "wood": "6", "gold": "3", "description": "Age up."},
        ]},
        {"age": 2, "steps": [
            {"time": "4:20", "villagers": "22", "food": "9", "wood": "7", "gold": "6", "description": "Two archery ranges."},
        ]},
    ],
}
# The same build without villager counts, like many older builds.
NO_COUNTS = {**BUILD_JSON, "steps": [{**sec, "steps": [{k: v for k, v in st.items() if k != "villagers"}
                                                        for st in sec["steps"]]} for sec in BUILD_JSON["steps"]]}

ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


def V(f=0, w=0, g=0, s=0):
    return {"food": f, "wood": w, "gold": g, "stone": s}


def feed(c, seconds, vil, idle=0, times=2):
    """Same reading twice, like two seconds of HUD reads."""
    m = None
    for k in range(times):
        m = c.update(seconds + k, vil, idle)
    return m


build = BuildOrder.from_json(BUILD_JSON)

print("Steps")
st = coach_steps(build)
check([s.villagers for s in st] == [6, 9, 12, 15, 18, 22], "villager totals read from the build")
check(coach_steps(BuildOrder.from_json(NO_COUNTS))[2].villagers == 12, "totals added up when the build has no counts")

print("Game start")
c = Coach(build)
m = feed(c, 0, V(6))
check(m["next"]["resource"] == "wood", "6 on sheep: villager 7 goes to wood")
check(m["next"]["then"] == "wood", "and the one after that too")
check(m["age"]["target"] == "Feudal" and m["age"]["at"] == "4:20", "age-up target from ageTimings")

print("Following the build")
m = feed(c, 40, V(6, 3))
check(m["next"]["resource"] == "gold", "3 on wood: next goes to gold")
m = feed(c, 90, V(6, 3, 3))
check(m["next"]["resource"] == "food", "12 villagers: next goes to food (berries)")
check(m["step"]["text"] == "Three to gold.", "current step is the 12-villager step")
m = feed(c, 140, V(9, 3, 3))
check(m["next"]["resource"] == "wood", "15 villagers: next goes to wood")

print("Misreads")
before = c.last
m = c.update(141, {"food": None, "wood": 3, "gold": 3, "stone": 0}, 0)
check(m is before, "a field it couldn't read keeps the last answer")
m = c.update(142, V(9, 3, 30), 0)
check(m["villagers"] == 15, "a one-frame jump in the total is ignored")

print("Idle villagers")
m = feed(c, 150, V(9, 3, 3), idle=2)
check(m["idle_to"] == ["wood", "wood"], "2 idle: both sent to wood")
check(m["next"]["resource"] == "wood", "and the next one to wood as well (18 wants 6 wood)")

print("Builders don't cause flicker")
c2 = Coach(build)
feed(c2, 0, V(6))
m = feed(c2, 60, V(6, 3))
check(m["next"]["resource"] == "gold" and m["villagers"] == 9, "9 villagers: next to gold")
m = feed(c2, 62, V(6, 2))           # one wood villager went to build a house: not on the HUD now
check(m["villagers"] == 9, "a builder leaving the counts doesn't lower the villager total")
check(m["step"]["villagers"] == 9, "...or send the coach back a step")
check(m["next"]["resource"] == "gold", "answer stays gold")
check(not m["short"]["wood"], "a one-villager gap isn't flagged straight away")
m = feed(c2, 90, V(6, 2))
check(m["short"]["wood"], "...but is after 20 s")
m = feed(c2, 120, V(6, 2))
check(m["villagers"] == 8, "a drop that lasts 45 s is believed (a villager died)")

print("Pace")
c3 = Coach(build)
feed(c3, 0, V(6))
m = feed(c3, 60, V(6, 3))           # 9 villagers at 1:00, build has 0:45
check(m["pace_seconds"] == 15, f"9 villagers at 1:00 vs 0:45 in the build: 15 s behind (got {m['pace_seconds']})")
m = feed(c3, 120, V(6, 3))          # still 9 at 2:00; the 12-villager step was due at 1:30
check(m["pace_seconds"] == 31, f"stuck on 9 villagers at 2:00: 31 s behind (got {m['pace_seconds']})")

print("Joining mid-game")
c4 = Coach(build)
m = feed(c4, 200, V(9, 3, 3))
check(m["pace_seconds"] in (0, 6), "no made-up pace for villagers made before it started")
check(m["next"]["resource"] == "wood", "picks up at the right step")

print("Clock mode and manual skip")
c5 = Coach(build, by="clock")
m = feed(c5, 100, V(6))
check(m["step"]["text"] == "Three to gold.", "by clock: 1:40 is the gold step even with 6 villagers")
c.skip()
m = feed(c, 160, V(9, 3, 3))
check(m["step"]["villagers"] == 18, "F10 skips ahead one step")

print("End of the build")
c6 = Coach(build)
m = feed(c6, 300, V(9, 7, 6))
check(m["done"] and m["upcoming"] is None, "past the last step: done")
check(m["next"]["resource"] in ("food", "wood", "gold"), "still suggests where to keep adding")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
