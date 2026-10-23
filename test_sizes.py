"""Checks the screen reader at other screen sizes by resizing the 2560x1440 calibration shots.

Resized shots aren't the same as the game drawn at that size, but they catch any
pixel size that's still fixed to 1440p. Run:  python test_sizes.py
"""
import numpy as np
from PIL import Image

from hud_reader import HudReader
from scoreboard import ScoreboardReader
from units import UnitDB, UnitRecognizer

ok = True


def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= bool(cond)


r = HudReader()


def fields(s):
    d = {"clock": s.clock, "pop": s.pop, "cap": s.pop_cap, "idle": s.idle}
    d.update({f"bank_{k}": v for k, v in s.resources.items()})
    d.update({f"vil_{k}": v for k, v in s.villagers.items()})
    return d


shots = [Image.open(f"calib/s{i:02d}.png").convert("RGB") for i in range(13)]
truth = [fields(r.read(np.asarray(im).astype(np.int16))) for im in shots]
db = UnitDB()
rec = UnitRecognizer(db)
sb = ScoreboardReader()
board = Image.open("calib/sb_gameC.png").convert("RGB")
board_truth = [(x.civ, x.enemy, x.age) for x in sb.read(np.asarray(board).astype(np.int16)).rows]

for size, need in (((1920, 1080), 1.0), ((3840, 2160), 1.0), ((1600, 900), 0.75)):
    print(f"{size[0]} x {size[1]}")
    right = wrong = total = 0
    for im, t in zip(shots, truth):
        got = fields(r.read(np.asarray(im.resize(size, Image.LANCZOS)).astype(np.int16)))
        for k, v in t.items():
            if v is None:
                continue
            total += 1
            right += got[k] == v
            wrong += got[k] is not None and got[k] != v
    check(wrong == 0, f"HUD: never a wrong number ({wrong} wrong)")
    check(right / total >= need, f"HUD: {right} of {total} read ({right / total:.0%}, need {need:.0%})")
    a = np.asarray(board.resize(size, Image.LANCZOS)).astype(np.int16)
    rows = [(x.civ, x.enemy, x.age) for x in sb.read(a).rows]
    check(rows == board_truth, "player list: civs, enemies and ages")
    s11 = np.asarray(shots[11].resize(size, Image.LANCZOS)).astype(np.int16)
    names = [db.base_name(m.unit_id, "zhu_xis_legacy") for m in rec.read_cards(s11, [7, 80, 30, 52, 14], ["zhu_xis_legacy"])]
    check(names == ["Horseman", "Spearman", "Zhuge Nu", "Crossbowman", "Grenadier"], f"army cards: {names}")

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
