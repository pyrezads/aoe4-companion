"""
Builds data/glyphs.json from labelled calibration screenshots in calib/.

Each label is the true text of one HUD field on one screenshot. The reader
cuts that field into glyphs; if the glyph count matches the label, each
glyph becomes a template for its character.

Run:  python build_templates.py            (build + self-test)
      python build_templates.py --holdout  (leave-one-screenshot-out test)
"""

import json
import pathlib
import sys

import numpy as np
from PIL import Image

import hud_reader as hr

CALIB = pathlib.Path(__file__).with_name("calib")

# Ground truth per screenshot: field -> text. None = skip (covered / coloured).
LABELS = {
    "s00": {"clock": "00:13", "pop": "7/10", "idle": "5", "food": "99950", "wood": "100000", "gold": "99950", "stone": "100000",
            "food_vil": "0", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0"},
    "s01": {"clock": "01:24", "pop": "7/90", "idle": "4", "food": "92250", "wood": "98200", "gold": "96100", "stone": "100000",
            "food_vil": "0", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0", "sel": (6, [6])},
    "s02": {"clock": "03:23", "pop": "200/200", "idle": "5", "food": "69882", "wood": "91890", "gold": "67790", "stone": "99700",
            "food_vil": "0", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0", "sel": (193, [60, 8, 6, 38, 39, 34, 8])},
    "s03": {"clock": "04:19", "pop": "200/200", "idle": "5", "food": "69922", "wood": "91890", "gold": "67990", "stone": "99700",
            "food_vil": "0", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0", "sel": (2, [2])},
    "s04": {"clock": "04:50", "pop": "200/200", "idle": "5", "food": "69938", "wood": "91890", "gold": "68070", "stone": "99700",
            "food_vil": "0", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0"},
    "s05": {"clock": "01:00", "pop": None, "idle": None, "food": "99858", "wood": "99849", "gold": "99950", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "0", "stone_vil": "0"},
    "s06": {"clock": "01:32", "pop": "11/50", "idle": "0", "food": "99638", "wood": "99849", "gold": "99780", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "2", "stone_vil": "0"},
    "s07": {"clock": "02:42", "pop": "12/50", "idle": "1", "food": "97137", "wood": "98349", "gold": "98500", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "3", "stone_vil": "0", "sel": (7, [1, 2, 2, 2])},
    "s08": {"clock": "02:57", "pop": "12/50", "idle": "7", "food": "97137", "wood": "98349", "gold": "98680", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "3", "stone_vil": "0"},
    "s09": {"clock": "03:19", "pop": "12/50", "idle": "7", "food": "97137", "wood": "98349", "gold": "98940", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "3", "stone_vil": "0"},
    "s10": {"clock": "03:50", "pop": "12/50", "idle": "1", "food": "97137", "wood": "98349", "gold": "99310", "stone": "100000",
            "food_vil": "6", "wood_vil": "0", "gold_vil": "3", "stone_vil": "0", "sel": (3, [3])},
    "s11": {"clock": "04:23", "pop": "200/200", "idle": "7", "food": "78295", "wood": "94231", "gold": "85260", "stone": "100000",
            "food_vil": "4", "wood_vil": "4", "gold_vil": "0", "stone_vil": "0", "sel": (183, [7, 80, 30, 52, 14])},
    "s12": {"clock": "08:06", "pop": "199/200", "idle": "4", "food": "80756", "wood": "96122", "gold": "88470", "stone": "100000",
            "food_vil": "4", "wood_vil": "4", "gold_vil": "3", "stone_vil": "0", "sel": (26, [25, 1])},
}

FIELD_BRIGHT = {"pop": 120}


def load(name):
    return np.asarray(Image.open(CALIB / f"{name}.png").convert("RGB")).astype(np.int16)


def field_glyphs(rgb, box, bright):
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = hr._scale_box(box, w, h)
    res = hr.segment(hr.text_mask(rgb[y0:y1, x0:x1], bright))
    if not res or not res[0]:
        return []
    glyphs, line_h = res
    return [hr.normalise(g, line_h) for g in glyphs]


def clock_glyphs(rgb):
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = hr._scale_box(hr.REGIONS["clock_band"], w, h)
    mask = hr.text_mask(rgb[y0:y1, x0:x1], 200)
    out = []
    for a, b in hr._runs(mask.sum(1), gap=2):
        if 12 <= b - a + 1 <= 20:
            box = (x0, y0 + a - 2, x1, y0 + b + 3)
            out.append(field_glyphs(rgb, box, 200))
    return out


def collect(names):
    templates, report = {}, []
    for n in names:
        rgb = load(n)
        for field, text in LABELS[n].items():
            if text is None or field == "sel":
                continue
            if field == "clock":
                cands = [g for g in clock_glyphs(rgb) if len(g) == len(text)]
                glyphs = cands[0] if cands else []
            else:
                glyphs = field_glyphs(rgb, hr.REGIONS[field], FIELD_BRIGHT.get(field, 165))
            if field.endswith("_vil") or field == "idle":
                if text == "0":
                    continue
            if len(glyphs) != len(text):
                report.append(f"{n}.{field}: expected {len(text)} glyphs, got {len(glyphs)}")
                continue
            for ch, g in zip(text, glyphs):
                key = f"clock:{ch}" if field == "clock" else ch
                templates.setdefault(key, []).append(g.ravel().round(3).tolist())
        # small selection-panel digits
        sel = LABELS[n].get("sel")
        if sel:
            total, counts = sel
            txt = f"ALL({total})"
            g_all = field_glyphs(rgb, hr.REGIONS["sel_all"], 200)
            # keep only the digits inside the parentheses (last len(str(total))+1 glyphs before ')')
            digits = str(total)
            if len(g_all) >= len(digits) + 2:
                for ch, g in zip(digits, g_all[-(len(digits) + 1):-1]):
                    templates.setdefault(f"sel:{ch}", []).append(g.ravel().round(3).tolist())
                templates.setdefault("sel:(", []).append(g_all[-(len(digits) + 2)].ravel().round(3).tolist())
                templates.setdefault("sel:)", []).append(g_all[-1].ravel().round(3).tolist())
            else:
                report.append(f"{n}.sel_all: got {len(g_all)} glyphs")
            for i, c in enumerate(counts or []):
                x = hr.CARD_X0 + i * hr.CARD_STEP
                gs = field_glyphs(rgb, (x, 1214, x + 34, 1231), 190)
                if len(gs) == len(str(c)):
                    for ch, g in zip(str(c), gs):
                        templates.setdefault(f"sel:{ch}", []).append(g.ravel().round(3).tolist())
                else:
                    report.append(f"{n}.card{i}: expected {c}, got {len(gs)} glyphs")
    return templates, report


def strip_prefix(templates):
    """The reader classifies against everything; labels keep only the character."""
    out = {}
    for k, v in templates.items():
        ch = k.split(":", 1)[1] if ":" in k else k
        out.setdefault(ch, []).extend(v)
    return out


def evaluate(names, templates):
    reader = hr.HudReader(hr.GlyphMatcher(strip_prefix(templates)))
    wrong, right = [], 0
    for n in names:
        rgb = load(n)
        s = reader.read(rgb)
        lab = LABELS[n]
        got = {"clock": s.clock, "pop": f"{s.pop}/{s.pop_cap}" if s.pop is not None else None, "idle": s.idle}
        for r in ("food", "wood", "gold", "stone"):
            got[r] = s.resources.get(r)
            got[f"{r}_vil"] = s.villagers.get(r)
        for field, text in lab.items():
            if text is None:
                continue
            if field == "sel":
                total, counts = text
                ok = s.selection_total == total and (counts is None or s.selection_counts[:len(counts)] == counts)
                (right := right + 1) if ok else wrong.append(f"{n}.sel: want {text}, got {(s.selection_total, s.selection_counts)}")
                continue
            want = text if field in ("clock", "pop") else int(text)
            if got[field] == want:
                right += 1
            else:
                wrong.append(f"{n}.{field}: want {want}, got {got[field]}")
    return right, wrong


if __name__ == "__main__":
    names = sorted(LABELS)
    if "--holdout" in sys.argv:
        total_r, total_w = 0, []
        for n in names:
            t, _ = collect([m for m in names if m != n])
            r, w = evaluate([n], t)
            total_r += r
            total_w += w
        print(f"holdout: {total_r} right, {len(total_w)} wrong")
        for w in total_w:
            print("  ", w)
        sys.exit(0)

    templates, report = collect(names)
    for line in report:
        print("calib:", line)
    hr.TEMPLATE_FILE.parent.mkdir(exist_ok=True)
    hr.TEMPLATE_FILE.write_text(json.dumps(strip_prefix(templates)))
    print("templates:", {k: len(v) for k, v in sorted(strip_prefix(templates).items())})
    r, w = evaluate(names, templates)
    print(f"self-test: {r} right, {len(w)} wrong")
    for x in w:
        print("  ", x)
