"""Windows check (GitHub Actions): the game-window capture against a stand-in game.

Shows a calibration screenshot in a borderless window titled "Age of Empires IV", then:
  1. captures it with Windows Graphics Capture and reads the HUD
  2. covers part of it with another window: the reading must not change
  3. times window capture + read against the old monitor grab
  4. runs the packed screen reader (if given) and checks it switches to the window capture

    python packaging/capture_test.py [dist-core/aoe4-core/aoe4-core.exe]
Results appear as notes on the run; capture_result.txt says OK or FAIL.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from gamewindow import find_game_window  # noqa: E402
from hud_reader import HudReader  # noqa: E402
from screen import GameScreen, WindowScreen, list_monitors, make_dpi_aware  # noqa: E402

ok = True


def note(msg, level="notice"):
    print(f"::{level} title=Capture test::{msg}", flush=True)


def fail(msg):
    global ok
    ok = False
    note("FAIL " + msg, "warning")


def ps(script):
    return subprocess.Popen(["powershell", "-NoProfile", "-Command", script])


make_dpi_aware()
W, H = 1920, 1080
img = os.path.join(tempfile.mkdtemp(), "game.png")
Image.open("calib/s07.png").convert("RGB").resize((W, H), Image.LANCZOS).save(img)
mon = list_monitors()[0]
note(f"runner screen {mon['width']}x{mon['height']}")

# The stand-in game: a Tk window, which (unlike WinForms) may be bigger than the runner's
# small screen, so its picture can be a real 1920x1080. It has a title bar and borders,
# like the game in windowed mode, so cutting those off is tested too.
game = subprocess.Popen([sys.executable, "-c", f"""
import tkinter as tk
r = tk.Tk(); r.title('Age of Empires IV'); r.wm_maxsize(4000, 4000)   # normal window: title bar + borders, like windowed mode
r.geometry('{W}x{H}+0+0')
img = tk.PhotoImage(file=r'{img}')
tk.Label(r, image=img, borderwidth=0).pack()
r.mainloop()
"""])
cover = None
try:
    gw = None
    for _ in range(40):
        time.sleep(0.5)
        gw = find_game_window()
        if gw:
            break
    if not gw:
        raise RuntimeError("stand-in window not found")
    note("window: " + json.dumps({k: gw[k] for k in ("title", "hwnd", "client")}))

    r = HudReader()
    truth = r.read(np.asarray(Image.open(img)).astype(np.int16))
    want = (truth.clock, truth.pop, truth.idle, dict(truth.villagers))

    # 1. window capture reads the game
    scr = WindowScreen(gw)
    frame = scr.grab()
    s = r.read(frame)
    got = (s.clock, s.pop, s.idle, dict(s.villagers))
    note(f"window capture: frame {frame.shape[1]}x{frame.shape[0]}, read {s.confidence:.0%}, {got}")
    if frame.shape[:2] != (H, W):
        fail(f"frame is {frame.shape[1]}x{frame.shape[0]}, expected {W}x{H}")
    if got != want:
        fail(f"read {got}, expected {want}")

    # 2. a window on top of the HUD doesn't get into the reading
    cover = ps("""Add-Type -AssemblyName System.Windows.Forms;
    $c = New-Object Windows.Forms.Form; $c.Text = 'Cover'; $c.FormBorderStyle = 'None'; $c.TopMost = $true;
    $c.StartPosition = 'Manual'; $c.Left = 0; $c.Top = 800; $c.Width = 600; $c.Height = 280; $c.BackColor = 'Magenta';
    [Windows.Forms.Application]::Run($c)""")
    time.sleep(3)
    s2 = r.read(scr.grab())
    got2 = (s2.clock, s2.pop, s2.idle, dict(s2.villagers))
    old = GameScreen({"left": 0, "top": 0, "width": W, "height": H})
    s_old = r.read(old.grab())
    note(f"covered: window capture read {s2.confidence:.0%}; old monitor grab read {s_old.confidence:.0%}")
    if got2 != want:
        fail(f"a covering window changed the reading: {got2}")

    # 3. speed
    def timed(f, n=20):
        t = time.perf_counter()
        for _ in range(n):
            r.read(f())
        return (time.perf_counter() - t) / n * 1000
    note(f"grab + read: window capture {timed(scr.grab):.0f} ms, old monitor grab {timed(old.grab):.0f} ms")
    scr.stop()

    # 4. the packed screen reader picks the window capture by itself
    if len(sys.argv) > 1:
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        p = subprocess.Popen([sys.argv[1], "--core", "--offline", "--config", cfg],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        status = {}

        def read():
            for line in p.stdout:
                m = json.loads(line)
                if m["type"] == "status":
                    status.update(m["data"])
        threading.Thread(target=read, daemon=True).start()
        end = time.time() + 25
        while time.time() < end and not (status.get("capture") == "window" and status.get("live")):
            time.sleep(0.5)
        p.stdin.write('{"cmd": "quit"}\n')
        p.stdin.flush()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        note(f"packed core: capture={status.get('capture')} live={status.get('live')} read={status.get('read_quality')}")
        if status.get("capture") != "window" or not status.get("live"):
            fail("the packed core didn't switch to the window capture: " + p.stderr.read()[-1500:].replace("\n", " | "))
except Exception as e:
    fail(f"{type(e).__name__}: {e}")
finally:
    for proc in (game, cover):
        if proc:
            proc.kill()

note("OK: window capture reads the game, ignores windows on top, and the packed core uses it" if ok else "capture test failed",
     "notice" if ok else "warning")
open("capture_result.txt", "w").write("OK" if ok else "FAIL")
