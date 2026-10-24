"""The core reading the game's log while it's written: a 4v4 loads, the teams, the enemy civ bar
and the scout list come from the log; then a 1v1 ends with a result.  python test_gamelog_core.py

No game and no internet (offline core, a screenshot replayed for the screen reader)."""
import json
import pathlib
import shutil
import tempfile
import threading
import time

import main
from core import run_core

LOGS = pathlib.Path(__file__).parent / "tests" / "logs"
ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


class Capture:
    def __init__(self):
        self.msgs, self.lock = [], threading.Lock()

    def write(self, s):
        with self.lock:
            for line in s.splitlines():
                if line.strip():
                    self.msgs.append(json.loads(line))

    def flush(self):
        pass

    def wait_for(self, t, cond=lambda d: True, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                hit = [m["data"] for m in self.msgs if m["type"] == t and m.get("data") is not None and cond(m["data"])]
            if hit:
                return hit[-1]
            time.sleep(0.1)
        return None


lines = (LOGS / "quickmatch_1v1_and_4v4.log").read_text(encoding="utf-8").splitlines(keepends=True)
cut = next(i for i, l in enumerate(lines) if "match type: 23" in l)          # where the 4v4 starts
head = next(i for i, l in enumerate(lines) if "match type: 20" in l)         # launch lines before the 1v1
tmp = pathlib.Path(tempfile.mkdtemp())
log = tmp / "warnings.log"
# the 1v1 that already happened, then the 4v4 loading
log.write_text("".join(lines[:head] + lines[cut:]), encoding="utf-8")

cfg = dict(main.DEFAULT_CONFIG, cache_dir=str(tmp / "cache"), read_every_seconds=0.3, auto_builds_api="",
           game_log_path=str(log), aoe4world_profile_id=0)
shot = sorted(str(p) for p in pathlib.Path("calib").glob("s*.png"))[:1]
cap, stop = Capture(), threading.Event()
import os
r_fd, w_fd = os.pipe()                       # a stdin that stays open, like the app's
th = threading.Thread(target=run_core, kwargs=dict(cfg=cfg, replay=shot, stdin=os.fdopen(r_fd), stdout=cap,
                                                    stop=stop, network=False), daemon=True)
th.start()
try:
    print("4v4 loading, read from the log")
    gl = cap.wait_for("gamelog", lambda d: d.get("phase") == "loading")
    check(gl is not None, "gamelog says a match is loading")
    check(gl and gl.get("my_name") == "NarkyNarc", "knows who I am from the log")
    check(gl and gl.get("kind") == "qm_4v4", "4v4 quick match")
    teams = cap.wait_for("teams", lambda d: d.get("source") == "log")
    check(teams and teams["me"]["civ"] == "ottomans", "my civ from the log")
    check(teams and sorted(e["civ"] for e in teams["enemies"]) == ["chinese", "english", "french", "order_of_the_dragon"],
          "the 4 enemy civs")
    check(teams and len(teams["allies"]) == 3 and all(a["name"] for a in teams["allies"]), "3 allies with names")
    intel = cap.wait_for("intel")
    check(intel and len(intel["enemies"]) == 4, "enemy civ bar has 4 civs")
    check(intel and all(e["units"] and all(u["counters"] for u in e["units"]) for e in intel["enemies"]),
          "every enemy civ has key units with counters")
    if intel:
        for e in intel["enemies"]:
            print("      ", e["civ_name"], "|", "; ".join(f"{u['name']} → {', '.join(u['counters'])}" for u in e["units"]))
    # the 4v4 starts, someone on the other team quits
    with open(log, "a", encoding="utf-8") as f:
        f.write("(I) [09:34:30.000] [000004576]: GAME -- Starting mission: \n")
        f.write("(I) [09:40:00.000] [000004576]: Player [Spade ♠] killed on game tick [2700] due to [surrender]\n")
    gl = cap.wait_for("gamelog", lambda d: d.get("phase") == "playing" and any(p.get("out") for p in d.get("players", [])))
    check(gl is not None, "sees the game start and an enemy surrender")
    # ...and the game ends: the 1v1's ending lines stand in for it
    with open(log, "a", encoding="utf-8") as f:
        f.write("(I) [09:55:00.000] [000004576]: MOD -- Game Over at frame 12000\n")
        f.write("(I) [09:55:01.000] [000004576]: MatchSetupManager::RequestLeaveGameMatch - RequestGameEnd()\n")
    gl = cap.wait_for("gamelog", lambda d: d.get("phase") in ("menu", "left"))
    check(gl is not None, "back to the menu after leaving")
    intel_after = cap.wait_for("intel", timeout=1)
    print("1v1 with a result")
    with open(log, "a", encoding="utf-8") as f:
        f.write("".join(lines[head:cut]))
    res = cap.wait_for("result", lambda d: d.get("session") == 257198481)
    check(res and res["result"] == "loss", "result: loss")
    check(res and any(p["name"] == "Fioul" and p["result"] == "win" for p in res["players"]), "Fioul won")
    check(res and next(p for p in res["players"] if p["me"])["apm"] == 21, "my APM from the result stats")
finally:
    stop.set()
    time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)

print("\nall passed" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
