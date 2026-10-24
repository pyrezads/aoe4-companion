"""
Reads the in-game player list (top right of the screen, 2560x1440):

    [⚔] 5 A.I. Hard | Age II  [flag]

- civ   : the flag, matched against the official civ flags (data/flags) and
          against flags cut from calibrated screenshots (data/flag_templates.json)
- enemy : the crossed swords left of the name (when visible); otherwise row order
          (your team is listed first, then you, then the enemies)
- age   : the Roman numeral after "Age"

Works in skirmish and ranked, no internet needed.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

DATA = pathlib.Path(__file__).with_name("data")
BASE_W, BASE_H = 2560, 1440

ROW0_Y = 467.5          # centre of the first row
ROW_STEP = 39.0
MAX_ROWS = 8
FLAG_X0, FLAG_X1 = 2504, 2545     # flag box
FLAG_H = 22
TEXT_X1 = 2494                    # text ends just left of the flag
SWORDS_SEARCH_X0 = 2080           # swords can sit far left on long names
FLAG_SIZE = (20, 11)              # comparison size


@dataclass
class PlayerRow:
    index: int
    y: int
    civ: Optional[str] = None
    civ_score: float = 0.0
    enemy: Optional[bool] = None      # None = swords not visible (covered)
    age: Optional[int] = None


@dataclass
class Scoreboard:
    rows: List[PlayerRow] = field(default_factory=list)
    me: Optional[int] = None          # index into rows

    def team(self) -> List[PlayerRow]:
        return [r for r in self.rows if r.enemy is False]

    def enemies(self) -> List[PlayerRow]:
        return [r for r in self.rows if r.enemy]

    def mode(self) -> Optional[str]:
        n = len(self.rows)
        return {2: "rm_1v1", 4: "rm_2v2", 6: "rm_3v3", 8: "rm_4v4"}.get(n)

    def my_row(self) -> Optional[PlayerRow]:
        return self.rows[self.me] if self.me is not None else None


def _vec(rgb: np.ndarray) -> np.ndarray:
    """Flag image -> small, contrast-normalised colour vector."""
    img = Image.fromarray(rgb.astype(np.uint8)).resize(FLAG_SIZE, Image.BILINEAR)
    v = np.asarray(img, dtype=np.float32).reshape(-1, 3)
    v = v - v.mean(0)
    n = np.linalg.norm(v)
    return (v / max(n, 1e-6)).ravel()


def _official_flags() -> Dict[str, np.ndarray]:
    out = {}
    for p in sorted((DATA / "flags").glob("*.png")):
        im = Image.open(p).convert("RGBA")
        a = np.asarray(im)
        ys, xs = np.where(a[:, :, 3] > 200)
        if len(xs) == 0:
            continue
        crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1, :3]
        out[p.stem] = _vec(crop)
    return out


class ScoreboardReader:
    def __init__(self):
        self.refs: List[Tuple[str, np.ndarray]] = [(k, v) for k, v in _official_flags().items()]
        tpl = DATA / "flag_templates.json"
        if tpl.exists():
            for civ, vecs in json.loads(tpl.read_text()).items():
                for v in vecs:
                    self.refs.append((civ, np.asarray(v, dtype=np.float32)))
        self.names = [k for k, _ in self.refs]
        self.mat = np.stack([v for _, v in self.refs])
        sw = DATA / "swords_mask.npy"
        self.swords = np.load(sw) if sw.exists() else None

    # ── geometry ──────────────────────────────────────────────────────
    @staticmethod
    def _flag_box(rgb, y_center):
        y0 = int(round(y_center - FLAG_H / 2))
        return rgb[y0:y0 + FLAG_H, FLAG_X0:FLAG_X1]

    def _find_row_y(self, rgb, k) -> Optional[int]:
        """Refine row k's vertical position; None if there's no flag there."""
        guess = ROW0_Y + k * ROW_STEP
        best, best_y = 0.0, None
        for dy in range(-6, 7):
            box = self._flag_box(rgb, guess + dy)
            if box.size == 0:
                continue
            # flags are bright/saturated against the dark list background
            cover = (box.max(axis=2) > 95).mean()
            if cover > best:
                best, best_y = cover, int(round(guess + dy))
        return best_y if best >= 0.35 else None

    # ── per-row reads ─────────────────────────────────────────────────
    def classify_flag(self, rgb, y) -> Tuple[str, float]:
        v = _vec(self._flag_box(rgb, y))
        sims = self.mat @ v
        i = int(np.argmax(sims))
        return self.names[i], float(sims[i])

    @staticmethod
    def text_mask(strip: np.ndarray) -> np.ndarray:
        mx = strip.max(axis=2)
        mn = strip.min(axis=2)
        return (mx > 90) & ((mx - mn > 60) | (mn > 150))

    def read_age(self, rgb, y) -> Optional[int]:
        strip = rgb[y - 11:y + 12, TEXT_X1 - 70:TEXT_X1]
        m = self.text_mask(strip)
        cols = m.sum(0) > 0
        # glyph runs from the right
        runs, x = [], len(cols) - 1
        while x >= 0:
            if cols[x]:
                e = x
                while x >= 0 and cols[x]:
                    x -= 1
                runs.append((x + 1, e))
            else:
                x -= 1
        if not runs:
            return None
        group = [runs[0]]
        for r in runs[1:]:
            gap = group[-1][0] - r[1] - 1
            if gap >= 5:
                break
            group.append(r)
        widths = [e - s + 1 for s, e in group]
        narrow = sum(1 for w in widths if w <= 5)
        wide = sum(1 for w in widths if w >= 8)
        if wide == 1 and narrow == 1:
            return 4
        if wide == 0 and 1 <= narrow <= 3:
            return narrow
        return None

    @staticmethod
    def _gold(rgb):
        R, G, B = (rgb[..., i].astype(np.int16) for i in range(3))
        return ((R > 125) & (R >= G - 5) & (R - B > 35) & (G - B > 15)).astype(np.float32)

    def has_swords(self, rgb, y) -> bool:
        """The swords are a gold X: correlate its colour shape along the row."""
        if self.swords is None:
            return False
        t = self.swords
        h, w = t.shape
        y0 = y - h // 2 - 3
        strip = self._gold(rgb[y0:y0 + h + 6, SWORDS_SEARCH_X0:TEXT_X1])
        if strip.shape[0] < h or strip.shape[1] < w:
            return False
        win = np.lib.stride_tricks.sliding_window_view(strip, (h, w))       # (dy, dx, h, w)
        hit = (win * t).sum(axis=(2, 3))                                   # template pixels found
        extra = win.sum(axis=(2, 3)) - hit                                  # gold pixels where the template is empty
        score = (hit - 0.5 * extra) / t.sum()
        return float(score.max()) > 0.55

    # ── full read ─────────────────────────────────────────────────────
    def read(self, rgb: np.ndarray, with_swords: bool = True) -> Scoreboard:
        if rgb.shape[1] != BASE_W or rgb.shape[0] != BASE_H:
            rgb = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize((BASE_W, BASE_H))).astype(np.int16)
        sb = Scoreboard()
        for k in range(MAX_ROWS):
            y = self._find_row_y(rgb, k)
            if y is None:
                break
            civ, score = self.classify_flag(rgb, y)
            row = PlayerRow(index=k, y=y, civ=civ if score >= 0.55 else None, civ_score=score,
                            age=self.read_age(rgb, y))
            if with_swords:
                row.enemy = True if self.has_swords(rgb, y) else None
            sb.rows.append(row)
        if not sb.rows:
            return sb
        sb.me = assign_sides(sb.rows)
        return sb


def assign_sides(rows: List[PlayerRow]) -> Optional[int]:
    """Decides who's an enemy from the crossed swords (row.enemy True = swords seen, None = not seen)
    and returns your row. Normally the list is your allies, you, then the enemies, and a swords icon
    can be hidden behind another icon, so everything from the first swords row down is an enemy. When
    the very first row has swords the list isn't in that order (free-for-all, some custom games), so
    each row counts on its own and you are the last row without swords."""
    n = len(rows)
    flagged = [r.index for r in rows if r.enemy]
    if not flagged:
        if n % 2:
            return None
        for r in rows:                      # swords covered: allies, you, then equal enemies
            r.enemy = r.index >= n // 2
        return n // 2 - 1
    if flagged[0] == 0:
        free = [r.index for r in rows if not r.enemy]
        for r in rows:
            r.enemy = bool(r.enemy)
        return free[-1] if free else None
    for r in rows:
        r.enemy = r.index >= flagged[0]
    return flagged[0] - 1


class TeamTracker:
    """Stabilises player-list reads over time and reports age-ups."""

    AGE_NAMES = {1: "Dark", 2: "Feudal", 3: "Castle", 4: "Imperial"}

    def __init__(self):
        self.reset()

    def reset(self):
        self.votes: Dict[int, Dict[str, int]] = {}
        self.enemy_votes: Dict[int, List[bool]] = {}
        self.ages: Dict[int, int] = {}
        self.pending: Dict[int, Tuple[int, int]] = {}   # row -> (age, times seen)
        self.n_rows = 0
        self.me: Optional[int] = None

    def update(self, sb: Scoreboard, clock: Optional[str]) -> List[str]:
        events = []
        if not sb.rows:
            return events
        if len(sb.rows) != self.n_rows and self.n_rows:
            # rows changed (a player left or a new game): start over unless just one read glitched
            if abs(len(sb.rows) - self.n_rows) > 1:
                self.reset()
        self.n_rows = max(self.n_rows, len(sb.rows))
        if sb.me is not None:
            self.me = sb.me
        for r in sb.rows:
            if r.civ:
                self.votes.setdefault(r.index, {}).setdefault(r.civ, 0)
                self.votes[r.index][r.civ] += 1
            if r.enemy is not None:
                self.enemy_votes.setdefault(r.index, []).append(bool(r.enemy))
                self.enemy_votes[r.index] = self.enemy_votes[r.index][-10:]
            if r.age:
                old = self.ages.get(r.index)
                if old is None:
                    self.ages[r.index] = r.age
                elif r.age > old:
                    # ages only go up; accept a new one after seeing it twice in a row
                    pa, pn = self.pending.get(r.index, (r.age, 0))
                    pn = pn + 1 if pa == r.age else 1
                    self.pending[r.index] = (r.age, pn)
                    if pn >= 2:
                        self.ages[r.index] = r.age
                        self.pending.pop(r.index, None)
                        if r.index != self.me:
                            from aoe4world import civ_name
                            who = "Enemy" if self.is_enemy(r.index) else "Ally"
                            events.append(f"{who} {civ_name(self.civ(r.index) or '?')} reached "
                                          f"{self.AGE_NAMES.get(r.age, r.age)} Age" + (f" ({clock})" if clock else ""))
                else:
                    self.pending.pop(r.index, None)
        return events

    def set_civ(self, i: int, civ: str) -> None:
        """You corrected a row's civ in the app: it wins over what the flag reads."""
        self.votes.setdefault(i, {})
        self.votes[i][civ] = self.votes[i].get(civ, 0) + 100000

    def civ(self, i: int) -> Optional[str]:
        v = self.votes.get(i)
        return max(v, key=v.get) if v else None

    def is_enemy(self, i: int) -> bool:
        v = self.enemy_votes.get(i) or []
        return sum(v) > len(v) / 2 if v else False

    def summary(self) -> dict:
        rows = sorted(self.votes)
        me = self.me
        return {
            "mode": {2: "rm_1v1", 4: "rm_2v2", 6: "rm_3v3", 8: "rm_4v4"}.get(self.n_rows),
            "me": {"civ": self.civ(me), "age": self.ages.get(me), "row": me} if me is not None else None,
            "allies": [{"civ": self.civ(i), "age": self.ages.get(i), "row": i} for i in rows if i != me and not self.is_enemy(i)],
            "enemies": [{"civ": self.civ(i), "age": self.ages.get(i), "row": i} for i in rows if self.is_enemy(i)],
        }
