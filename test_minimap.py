"""Map watch on made-up minimaps: your base, gold mines in an enemy's colour, a raid, a forward tower."""
import numpy as np
from PIL import Image

from minimap import DEFAULT_RECT, MapWatch, MinimapReader, scaled_rect
from scoreboard import ScoreboardReader, TeamTracker

ok = True


def check(c, msg):
    global ok
    print(("   PASS " if c else "   FAIL ") + msg)
    ok &= bool(c)


print("Player colours from the player list (real screenshot)")
a = np.asarray(Image.open("calib/sb_gameA.png").convert("RGB")).astype(np.int16)
tt = TeamTracker()
tt.update(ScoreboardReader().read(a), "0:10")
pal = tt.palette()
check(len(pal["me"]) == 1 and len(pal["ally"]) == 3 and len(pal["enemy"]) == 4, f"8 players by side {[len(v) for v in pal.values()]}")
check(pal["me"][0][1] > 180 and pal["me"][0][2] > 180 and pal["me"][0][0] < 60, f"you are cyan {pal['me']}")
RED = next(c for c in pal["enemy"] if c[0] > 200 and c[1] < 90)
YEL = next(c for c in pal["enemy"] if c[0] > 200 and c[1] > 200)
ORA = next(c for c in pal["enemy"] if c[0] > 200 and 110 < c[1] < 160)
ME = pal["me"][0]

x0, y0, w, h = DEFAULT_RECT


def frame(dots, W=2560, H=1440):
    """dots: (x, y, size, colour, brightness) in minimap pixels at 1440p."""
    f = np.zeros((H, W, 3), np.uint8)
    rx, ry, rw, rh = scaled_rect(DEFAULT_RECT, W, H)
    k = H / 1440
    f[ry:ry + rh, rx:rx + rw] = (38, 52, 30)                       # terrain
    for x, y, s, col, b in dots:
        cx, cy, ss = int(rx + x * k), int(ry + y * k), max(1, int(s * k))
        f[cy:cy + ss, cx:cx + ss] = (np.array(col) * b).clip(0, 255)
    return f.astype(np.int16)


BASE = [(60, 330, 8, ME, 1.0), (75, 318, 5, ME, .8), (52, 345, 4, ME, .9)]          # your Town Center and houses
GOLD = [(200, 120, 5, YEL, .85), (300, 260, 5, YEL, .85), (90, 200, 5, YEL, .85)]      # gold mines: yellow, like player 6
reader, watch = MinimapReader(), MapWatch()
said, events = [], []


def run(t, dots):
    al, ev = watch.update(reader.read(frame(dots), pal), t, float(t))
    said.extend(x[1] for x in al)
    events.extend(x[1] for x in ev)
    return al


print("Start of the game")
for t in range(0, 30):
    run(t, BASE + GOLD)
check(watch.home is not None and abs(watch.home[1] - 60 * 32 // w) <= 2 and abs(watch.home[0] - 330 * 32 // h) <= 2, f"your base found {watch.home}")
for t in range(30, 120):
    run(t, BASE + GOLD)
check(not said, f"gold mines in an enemy's colour aren't enemies ({said[:2]})")

print("A raid")
for i, t in enumerate(range(120, 160)):
    x = 360 - i * 8                                         # an army walks from the far corner to your base
    run(t, BASE + GOLD + [(x, 60 + i * 7, 6, RED, .7), (x + 7, 66 + i * 7, 5, RED, .9)])
check("Enemy units near your base" in said, "raid alert")
check(events.count("Enemy units at your base") == 1, f"one raid event {events}")
check(watch.state["raid"] and watch.state["enemy"], "map state shows the raid")
said.clear()
for t in range(160, 200):
    run(t, BASE + GOLD)
check(not watch.raid_on, "raid ends when they leave")

print("A forward tower")
for t in range(200, 270):
    run(t, BASE + GOLD + [(120, 290, 4, ORA, .8)])
check("Enemy building near your base" in events, f"forward building event {events}")

print("Other screen sizes")
r2 = MinimapReader()
m = r2.read(frame(BASE + [(300, 60, 6, RED, .8)], 1920, 1080), pal)
check(m["me"].sum() > 0 and m["enemy"].sum() > 0, "1080p")
m = r2.read(frame(BASE + [(300, 60, 6, RED, .8)], 3440, 1440), pal)
check(m["me"].sum() > 0 and m["enemy"].sum() > 0, "ultrawide 3440x1440")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
