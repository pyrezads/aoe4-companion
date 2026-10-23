"""Checks core mode (what the Electron app talks to) by replaying calibration screenshots.

No game and no internet: the build list comes from a cache file written here.
Run:  python test_core.py
"""
import json
import os
import pathlib
import tempfile
import threading
import time

import main
from core import run_core
BUILD_JSON = json.load(open("data/test_build.json", encoding="utf-8"))

ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


class Capture:
    """Collects what the core writes to stdout, one message per line."""
    def __init__(self):
        self.msgs, self.lock = [], threading.Lock()

    def write(self, s):
        with self.lock:
            for line in s.splitlines():
                if line.strip():
                    self.msgs.append(json.loads(line))

    def flush(self):
        pass

    def of(self, t):
        with self.lock:
            return [m for m in self.msgs if m["type"] == t]

    def wait_for(self, t, cond=lambda m: True, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            hit = [m for m in self.of(t) if cond(m)]
            if hit:
                return hit[-1]
            time.sleep(0.1)
        return None


tmp = pathlib.Path(tempfile.mkdtemp())
builds = [dict(BUILD_JSON, id="test-1", title="Test timing attack", season="Season 13", views=900, strategy="Timing Attack"),
          dict(BUILD_JSON, id="test-2", title="Old fast castle", season="Season 6", views=18000, strategy="Fast Castle")]
(tmp / "builds_ZXL.json").write_text(json.dumps(builds))

cfg = dict(main.DEFAULT_CONFIG, cache_dir=str(tmp), read_every_seconds=0.2, auto_builds_api="")
shots = sorted(str(p) for p in pathlib.Path("calib").glob("s*.png"))

r, w = os.pipe()
stdin, cmd_pipe = os.fdopen(r, "r"), os.fdopen(w, "w")
out, stop = Capture(), threading.Event()
t = threading.Thread(target=run_core, kwargs=dict(cfg=cfg, replay=shots, stdin=stdin, stdout=out,
                                                   publish_every=0.1, stop=stop, network=False), daemon=True)
t.start()


def send(**cmd):
    cmd_pipe.write(json.dumps(cmd) + "\n")
    cmd_pipe.flush()


print("Start")
check(out.wait_for("ready"), "sends ready")
st = out.wait_for("status", lambda m: m["data"]["live"])
check(st, "reads the replayed game screen (status live)")
check(st and st["data"]["read_quality"] >= 0.6, f"read quality {st and st['data']['read_quality']}")
hud = out.wait_for("hud", lambda m: m["data"] and m["data"]["villagers_total"])
check(hud, f"hud with villagers ({hud and hud['data']['villagers_total']})")

print("Build picker")
send(cmd="list_builds", civ="zhu_xis_legacy")
b = out.wait_for("builds")
check(b and len(b["data"]["builds"]) == 2, "lists the cached builds")
check(b and b["data"]["builds"][0]["title"] == "Test timing attack", "current-patch build ranked first")
check(b and b["data"]["offline"], "says the list came from the cache")
send(cmd="build_detail", id="test-1")
bd = out.wait_for("build_detail")
check(bd and bd["data"]["title"] == "Test timing attack" and len(bd["data"]["sections"]) == 2, "build details: everything on one build")
check(bd and bd["data"]["ages"].get("2") == "4:20", "build details: age-up times")
send(cmd="use_build", id="test-1")
bm = out.wait_for("build", lambda m: m["data"] and m["data"]["locked"])
check(bm and bm["data"]["title"] == "Test timing attack", "chosen build is followed")
check(bm and len(bm["data"]["steps"]) == 6, "build steps sent to the app")
co = out.wait_for("coach", lambda m: m["data"])
check(co and co["data"]["next"]["resource"] in ("food", "wood", "gold", "stone"),
      f"coach running: next villager to {co and co['data']['next']['resource']}")

print("Commands")
send(cmd="reading_check")
rc = out.wait_for("reading_check")
check(rc and rc["data"]["width"] == 2560, "reading check answers with what it read")
send(cmd="nope")
check(out.wait_for("error", lambda m: "unknown" in m["data"]["message"]), "unknown command is an error, not a crash")
send(cmd="use_build", id="missing")
check(out.wait_for("error", lambda m: "isn't in the list" in m["data"]["message"]), "missing build is reported")
send(cmd="use_build", id="test-2", civ="zhu_xis_legacy")
check(out.wait_for("build", lambda m: m["data"] and m["data"]["title"] == "Old fast castle"), "use_build with a civ works without listing first")
send(cmd="coach_skip", n=1)
send(cmd="auto_build")
time.sleep(0.5)
check(out.wait_for("build", lambda m: m["data"] and not m["data"]["locked"]), "auto_build unlocks the pick")

print("Quit")
send(cmd="quit")
t.join(5)
check(not t.is_alive(), "quits on request")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
