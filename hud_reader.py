"""
HUD reader for Age of Empires IV at 2560x1440 (UI scale as calibrated).

Reads the numbers on the HUD by matching each character's shape against
glyph templates cut from real screenshots (see build_templates.py).
No OCR library, no internet, works on any Python 3 with Pillow + numpy.

All regions are in pixels of the 2560x1440 game screen. If the game runs at
another resolution the regions are scaled proportionally.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

BASE_W, BASE_H = 2560, 1440
TEMPLATE_FILE = pathlib.Path(__file__).with_name("data") / "glyphs.json"
GLYPH_SIZE = (12, 18)  # width, height every glyph is normalised to

# x0, y0, x1, y1 at 2560x1440
REGIONS = {
    "pop":        (50, 1132, 160, 1175),
    "idle":       (182, 1132, 245, 1175),
    "food":       (50, 1213, 175, 1252),
    "wood":       (50, 1266, 175, 1305),
    "gold":       (50, 1318, 175, 1357),
    "stone":      (50, 1371, 175, 1410),
    "food_vil":   (182, 1213, 245, 1252),
    "wood_vil":   (182, 1266, 245, 1305),
    "gold_vil":   (182, 1318, 245, 1357),
    "stone_vil":  (182, 1371, 245, 1410),
    "clock_band": (1235, 125, 1325, 245),   # the clock moves down during an age-up
    "sel_all":    (288, 1224, 383, 1247),   # text inside the "ALL (n)" box
    "sel_cards":  (388, 1208, 1200, 1236),  # unit/building cards, count at top-left
}

CARD_X0, CARD_STEP, CARD_W = 392, 53, 50   # unit/building card grid in the selection panel


@dataclass
class HudState:
    clock: Optional[str] = None             # "MM:SS"
    seconds: Optional[int] = None
    pop: Optional[int] = None
    pop_cap: Optional[int] = None
    idle: Optional[int] = None
    resources: Dict[str, Optional[int]] = field(default_factory=dict)
    villagers: Dict[str, Optional[int]] = field(default_factory=dict)
    selection_total: Optional[int] = None
    selection_counts: List[int] = field(default_factory=list)
    confidence: float = 0.0                 # share of fields read successfully
    missed: List[str] = field(default_factory=list)   # fields that couldn't be read

    def to_dict(self):
        return self.__dict__.copy()


# ── low level: masks, segmentation, matching ─────────────────────────────

def _scale_box(box, w, h):
    sx, sy = w / BASE_W, h / BASE_H
    x0, y0, x1, y1 = box
    return int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)


def text_mask(rgb: np.ndarray, bright: int = 165) -> np.ndarray:
    """White/grey HUD text on the dark panel."""
    mn = rgb.min(axis=2)
    mx = rgb.max(axis=2)
    return (mn > bright) & (mx - mn < 70)


def _runs(v: np.ndarray, gap: int = 1) -> List[Tuple[int, int]]:
    idx = np.where(v > 0)[0]
    if len(idx) == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > gap + 1:   # more than `gap` empty positions between them
            out.append((int(s), int(p)))
            s = i
        p = i
    out.append((int(s), int(p)))
    return out


def segment(mask: np.ndarray, min_h: int = 8, max_h: int = 30) -> List[np.ndarray]:
    """Split a text mask into glyph masks, left to right.

    Uses the tallest text line in the box and cuts it at empty columns.
    """
    lines = [(a, b) for a, b in _runs(mask.sum(1), gap=2) if min_h <= b - a + 1 <= max_h]
    if not lines:
        return []
    y0, y1 = max(lines, key=lambda r: r[1] - r[0])
    line = mask[y0:y1 + 1]
    glyphs = []
    for x0, x1 in _runs(line.sum(0), gap=0):
        g = line[:, x0:x1 + 1]
        rows = np.where(g.sum(1) > 0)[0]
        if len(rows) == 0:
            continue
        g = g[rows[0]:rows[-1] + 1]
        glyphs.append(g)
    # Drop specks and flat bars from icons or the scene: real characters are
    # at least ~30% of the line height (":" and "/" included).
    tallest = max((g.shape[0] for g in glyphs), default=0)
    glyphs = [g for g in glyphs if g.shape[0] >= 0.3 * tallest and g.sum() >= 4]
    return glyphs, tallest


def _split_wide(g: np.ndarray, line_h: int) -> List[np.ndarray]:
    """On small screens neighbouring characters can touch ("/5" in "12/50"). A shape
    wider than any single digit is cut at its thinnest column, then trimmed."""
    h, w = g.shape
    if w <= 0.9 * line_h or w < 6:
        return [g]
    ink = g.sum(0)
    lo, hi = int(w * 0.3), int(w * 0.7) + 1
    cut = lo + int(np.argmin(ink[lo:hi]))
    out = []
    for part in (g[:, :cut], g[:, cut + 1:] if ink[cut] == 0 else g[:, cut:]):
        rows = np.where(part.sum(1) > 0)[0]
        cols = np.where(part.sum(0) > 0)[0]
        if len(rows) and len(cols):
            out.extend(_split_wide(part[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1], line_h))
    return out or [g]


def normalise(g: np.ndarray, line_h: int) -> np.ndarray:
    """Resize a glyph into a fixed box, keeping its height relative to the line."""
    from PIL import Image
    W, H = GLYPH_SIZE
    h, w = g.shape
    scale = (H - 2) / max(line_h, 1)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    nw = min(nw, W)
    img = Image.fromarray((g * 255).astype(np.uint8)).resize((nw, nh), Image.BILINEAR)
    out = np.zeros((H, W), dtype=np.float32)
    top = (H - nh) // 2 if nh < (H - 2) * 0.6 else (H - 2) - nh + 1   # small glyphs (":") sit centred
    left = (W - nw) // 2
    out[max(top, 0):max(top, 0) + nh, left:left + nw] = np.asarray(img, dtype=np.float32)[:H, :W] / 255
    return out


def _blur(m: np.ndarray, passes: int = 2) -> np.ndarray:
    """Small box blur so thin and bold strokes of the same character still match."""
    m = m.reshape(GLYPH_SIZE[1], GLYPH_SIZE[0]).astype(np.float32)
    for _ in range(passes):
        p = np.pad(m, 1, mode="edge")
        m = sum(p[dy:dy + m.shape[0], dx:dx + m.shape[1]] for dy in range(3) for dx in range(3)) / 9
    return m.ravel()


class GlyphMatcher:
    def __init__(self, templates: Dict[str, List[List[float]]]):
        self.labels, mats = [], []
        for label, vecs in templates.items():
            for v in vecs:
                self.labels.append(label)
                mats.append(_blur(np.asarray(v, dtype=np.float32)))
        self.mat = np.stack(mats) if mats else np.zeros((0, GLYPH_SIZE[0] * GLYPH_SIZE[1]))
        norms = np.linalg.norm(self.mat, axis=1, keepdims=True)
        self.mat = self.mat / np.maximum(norms, 1e-6)

    @classmethod
    def load(cls, path=TEMPLATE_FILE):
        return cls(json.loads(pathlib.Path(path).read_text()))

    def classify(self, vec: np.ndarray, allowed: Optional[str] = None) -> Tuple[str, float]:
        v = _blur(vec.ravel())
        v = v / max(np.linalg.norm(v), 1e-6)
        sims = self.mat @ v
        if allowed is not None:
            mask = np.array([l in allowed for l in self.labels])
            sims = np.where(mask, sims, -1.0)
        i = int(np.argmax(sims))
        return self.labels[i], float(sims[i])


# ── reading fields ───────────────────────────────────────────────────────

class HudReader:
    def __init__(self, matcher: Optional[GlyphMatcher] = None, min_similarity: float = 0.80):
        self.matcher = matcher or GlyphMatcher.load()
        self.min_sim = min_similarity
        self.k = 1.0          # screen height / 1440: pixel sizes below were tuned at 2560x1440

    # Smaller screens draw thinner, softer text: scale sizes and ease the brightness cut.
    BRIGHT_EASE = 30.0
    def _b(self, bright: int) -> int:
        return int(bright - max(0.0, 1 - self.k) * self.BRIGHT_EASE)

    def _seg(self, mask):
        return segment(mask, min_h=max(4, int(8 * self.k)), max_h=int(30 * self.k) + 2)

    def read_text(self, rgb: np.ndarray, box, bright: int = 165, allowed: Optional[str] = None,
                  max_aspect: Optional[float] = None) -> Tuple[str, float]:
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = _scale_box(box, w, h)
        crop = rgb[y0:y1, x0:x1]
        res = self._seg(text_mask(crop, self._b(bright)))
        if not res or not res[0]:
            return "", 0.0
        glyphs, line_h = res
        if max_aspect is not None:   # stop at the first shape too wide to be a digit (icon art)
            kept = []
            for g in glyphs:
                if g.shape[1] > max_aspect * line_h:
                    break
                kept.append(g)
            glyphs = kept
            if not glyphs:
                return "", 0.0
        if max_aspect is None:
            glyphs = [part for g in glyphs for part in _split_wide(g, line_h)]
        chars, sims = [], []
        for g in glyphs:
            label, sim = self.matcher.classify(normalise(g, line_h), allowed)
            chars.append(label if sim >= self.min_sim else "?")
            sims.append(sim)
        return "".join(chars), min(sims)

    def read_int(self, rgb, box, bright=165, max_aspect=None) -> Optional[int]:
        text, sim = self.read_text(rgb, box, bright, allowed="0123456789", max_aspect=max_aspect)
        digits = re.sub(r"[^0-9]", "", text)
        if not digits or "?" in text:
            return None
        return int(digits)

    def read_count(self, rgb, box) -> Optional[int]:
        """Villager / idle counts: white digits when > 0, a grey outline when 0."""
        n = self.read_int(rgb, box, 165)
        if n is not None:
            return n
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = _scale_box(box, w, h)
        crop = rgb[y0:y1, x0:x1]
        bright = text_mask(crop, self._b(165)).sum()
        dim = text_mask(crop, 85).sum()
        return 0 if bright == 0 and dim >= 15 * self.k * self.k else None

    def read_clock(self, rgb) -> Optional[str]:
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = _scale_box(REGIONS["clock_band"], w, h)
        band = rgb[y0:y1, x0:x1]
        mask = text_mask(band, self._b(200))
        best = None
        for a, b in _runs(mask.sum(1), gap=2):
            if not (int(12 * self.k) - 1 <= b - a + 1 <= int(20 * self.k + 0.999) + 1):
                continue
            sub = (x0 * BASE_W / w, (y0 + a - 2) * BASE_H / h, x1 * BASE_W / w, (y0 + b + 3) * BASE_H / h)
            text, sim = self.read_text(rgb, sub, 200, allowed="0123456789:")
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
            if m and (best is None or sim > best[1]):
                best = (text, sim)
        return best[0] if best else None

    def read_selection(self, rgb) -> Tuple[Optional[int], List[int]]:
        # "ALL (193)": skip the three letters and the brackets, read only the digits.
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = _scale_box(REGIONS["sel_all"], w, h)
        res = self._seg(text_mask(rgb[y0:y1, x0:x1], self._b(200)))
        total = None
        if res and len(res[0]) >= 6:
            glyphs, line_h = res
            digits = []
            for g in glyphs[4:-1]:
                label, sim = self.matcher.classify(normalise(g, line_h), allowed="0123456789")
                digits.append(label if sim >= self.min_sim else "?")
            if digits and "?" not in digits:
                total = int("".join(digits))
        counts = []
        if total is not None:
            sx, sy = w / BASE_W, h / BASE_H
            for i in range(14):
                x = CARD_X0 + i * CARD_STEP
                card = rgb[int(1212 * sy):int(1262 * sy), int(x * sx):int((x + 48) * sx)]
                if card.size == 0 or card.mean() < 55:   # empty slot: dark panel
                    break
                n = self.read_int(rgb, (x, 1214, x + 34, 1231), 190, max_aspect=0.95)
                counts.append(n if n is not None else 1)  # a single unit/building shows no number
            # The group cards use a larger font than "ALL (n)"; when they disagree, trust the cards.
            if counts and sum(counts) != total and len(counts) < 14:
                total = sum(counts)
        return total, counts

    def read(self, rgb: np.ndarray) -> HudState:
        self.k = rgb.shape[0] / BASE_H
        s = HudState()
        ok = 0
        total = 0

        s.clock = self.read_clock(rgb)
        total += 1
        if s.clock:
            ok += 1
            mm, ss = s.clock.split(":")
            s.seconds = int(mm) * 60 + int(ss)
        else:
            s.missed.append("clock")

        pop_text, _ = self.read_text(rgb, REGIONS["pop"], 120, allowed="0123456789/")
        total += 1
        m = re.fullmatch(r"(\d+)/(\d+)", pop_text)
        if m:
            s.pop, s.pop_cap = int(m.group(1)), int(m.group(2))
            ok += 1
        else:
            s.missed.append("pop")

        total += 1
        s.idle = self.read_count(rgb, REGIONS["idle"])
        ok += s.idle is not None
        if s.idle is None:
            s.missed.append("idle")

        for r in ("food", "wood", "gold", "stone"):
            total += 2
            s.resources[r] = self.read_int(rgb, REGIONS[r])
            ok += s.resources[r] is not None
            if s.resources[r] is None:
                s.missed.append(f"{r} bank")
            s.villagers[r] = self.read_count(rgb, REGIONS[f"{r}_vil"])
            ok += s.villagers[r] is not None
            if s.villagers[r] is None:
                s.missed.append(f"{r} villagers")

        s.selection_total, s.selection_counts = self.read_selection(rgb)
        s.confidence = ok / total
        return s
