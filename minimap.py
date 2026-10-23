"""
Map watch: reads the minimap every second and tells you when enemies show up near your base.

Each player's colour comes from their name in the player list (scoreboard.read_color), so it
works with the game's normal player colours; no setting to change. Every vivid pixel on the
minimap is given to the nearest player colour: you, an ally or an enemy. Things that are coloured
from the start and never move (gold mines, sacred sites, the minimap's own frame) are learned in
the first seconds of the game and ignored.

    reader = MinimapReader(rect)                 rect in 2560x1440 pixels: x, y, w, h
    masks = reader.read(frame, teams.palette())  {"me", "ally", "enemy"} -> boolean pixel masks
    watch = MapWatch(); alerts, events = watch.update(masks, game_seconds, now)

What it reports:
  - enemy units near your base (a raid), as an alert and a game review event
  - an enemy building near your base early on (a forward tower / barracks)
  - many more enemy units visible than a minute ago
The minimap only shows what you can see, so it complements clicking units; it can't tell unit types.
"""

from __future__ import annotations

import collections
from typing import Dict, List, Optional, Tuple

import numpy as np

BASE_W, BASE_H = 2560, 1440
# Where the minimap is at 2560x1440 and 100% UI scale. Set from a real screenshot in calibration;
# config "minimap_rect" overrides it.
DEFAULT_RECT = (2148, 1028, 404, 404)
GRID = 32


def scaled_rect(rect: Tuple[int, int, int, int], width: int, height: int) -> Tuple[int, int, int, int]:
    """The 1440p rect on another screen size. The minimap hugs the bottom-right corner, so wider
    screens (21:9) keep it at the right edge."""
    x, y, w, h = rect
    k = height / BASE_H
    right = BASE_W - x
    return int(round(width - right * k)), int(round(y * k)), int(round(w * k)), int(round(h * k))


class MinimapReader:
    def __init__(self, rect: Optional[Tuple[int, int, int, int]] = None, max_dist: float = 62.0):
        self.rect = tuple(rect or DEFAULT_RECT)
        self.max_dist = max_dist

    def crop(self, frame: np.ndarray) -> np.ndarray:
        x, y, w, h = scaled_rect(self.rect, frame.shape[1], frame.shape[0])
        return frame[max(0, y):y + h, max(0, x):x + w, :3]

    def read(self, frame: np.ndarray, palette: Dict[str, List[Tuple[int, int, int]]]) -> Dict[str, np.ndarray]:
        c = self.crop(frame).astype(np.float32)
        out = {k: np.zeros(c.shape[:2], bool) for k in ("me", "ally", "enemy")}
        cols, sides = [], []
        for side in out:
            for col in palette.get(side) or []:
                cols.append(col)
                sides.append(side)
        if not cols or c.size == 0:
            return out
        mx, mn = c.max(2), c.min(2)
        vivid = (mx > 90) & (mx - mn > 70)
        if not vivid.any():
            return out
        px = c[vivid]
        # compare colour, not brightness: minimap dots are darker or lighter than the name text
        pn = px * (255.0 / np.maximum(px.max(1, keepdims=True), 1))
        P = np.array(cols, np.float32)
        Pn = P * (255.0 / np.maximum(P.max(1, keepdims=True), 1))
        d = ((pn[:, None, :] - Pn[None]) ** 2).sum(2)
        j = d.argmin(1)
        ok = d[np.arange(len(j)), j] < self.max_dist ** 2
        ys, xs = np.nonzero(vivid)
        for side in out:
            sel = ok & (np.array(sides)[j] == side)
            out[side][ys[sel], xs[sel]] = True
        return out


def cells(mask: np.ndarray, grid: int = GRID) -> np.ndarray:
    """Pixel mask -> grid x grid counts."""
    h, w = mask.shape
    if h < grid or w < grid:
        return np.zeros((grid, grid), int)
    hh, ww = h - h % grid, w - w % grid
    m = mask[:hh, :ww].reshape(grid, hh // grid, grid, ww // grid)
    return m.sum((1, 3))


class MapWatch:
    def __init__(self, grid: int = GRID, home_radius: float = 5.5, near_px: int = 8, learn_until: int = 25):
        self.grid = grid
        self.home_radius = home_radius
        self.near_px = near_px
        self.learn_until = learn_until
        yy, xx = np.mgrid[0:grid, 0:grid]
        self._yy, self._xx = yy, xx
        self.reset()

    def reset(self):
        self.neutral: Optional[np.ndarray] = None     # pixels coloured from the start (map features, frame)
        self.home_acc = np.zeros((self.grid, self.grid), float)
        self.home: Optional[Tuple[int, int]] = None
        self.raid_reads = 0
        self.raid_on = False
        self.raid_clear_since: Optional[float] = None
        self.persist = np.zeros((self.grid, self.grid), float)   # seconds an enemy has stayed in each cell
        self.forward_said = False
        self.forward_until = 0.0
        self.totals = collections.deque(maxlen=240)               # (now, enemy pixels)
        self.growth_said = 0.0
        self.last_t: Optional[float] = None
        self.state: dict = {}

    def _near(self, radius: float) -> np.ndarray:
        if self.home is None:
            return np.zeros((self.grid, self.grid), bool)
        return (self._yy - self.home[0]) ** 2 + (self._xx - self.home[1]) ** 2 <= radius ** 2

    def update(self, masks: Dict[str, np.ndarray], seconds: Optional[int], now: float
               ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """Returns (alerts to show now, events for the game review) as (kind/level, text)."""
        alerts: List[Tuple[str, str]] = []
        events: List[Tuple[str, str]] = []
        dt = 1.0 if self.last_t is None else max(0.0, min(5.0, now - self.last_t))
        self.last_t = now
        me, ally, enemy = masks["me"], masks["ally"], masks["enemy"]
        if seconds is None:
            return alerts, events

        # 1. learn what's coloured and still at the start: it isn't anyone's army
        others = ally | enemy
        if seconds < self.learn_until:
            self.neutral = others.copy() if self.neutral is None or self.neutral.shape != others.shape else (self.neutral | others)
        if self.neutral is not None and self.neutral.shape == enemy.shape:
            enemy = enemy & ~self.neutral
            ally = ally & ~self.neutral

        # 2. your base: where your colour is in the first two minutes (Town Center and first buildings)
        me_c, en_c = cells(me, self.grid), cells(enemy, self.grid)
        if seconds < 120 or self.home is None:
            self.home_acc += me_c
            if self.home_acc.sum() > 0:
                sm = self.home_acc.copy()
                sm[1:-1, 1:-1] = sum(self.home_acc[1 + dy:self.grid - 1 + dy, 1 + dx:self.grid - 1 + dx]
                                     for dy in (-1, 0, 1) for dx in (-1, 0, 1))
                self.home = tuple(int(v) for v in np.unravel_index(np.argmax(sm), sm.shape))

        near = int(en_c[self._near(self.home_radius)].sum()) if self.home else 0
        total = int(en_c.sum())
        self.totals.append((now, total))

        if self.home is not None and seconds >= 30:
            # 3. a raid: enemy dots by your base for two reads in a row
            self.raid_reads = self.raid_reads + 1 if near >= self.near_px else 0
            if self.raid_reads >= 2:
                if not self.raid_on:
                    events.append(("raid", "Enemy units at your base"))
                self.raid_on, self.raid_clear_since = True, None
            elif self.raid_on and near < self.near_px // 2:
                self.raid_clear_since = self.raid_clear_since or now
                if now - self.raid_clear_since > 20:
                    self.raid_on = False
            if self.raid_on:
                alerts.append(("bad", "Enemy units near your base"))

            # 4. something of theirs that doesn't move, close to your base, early: a forward building
            fwd_zone = self._near(self.home_radius + 4)
            self.persist = np.where(en_c > 0, self.persist + dt, 0.0)
            if seconds < 600 and not self.forward_said and (self.persist[fwd_zone] >= 45).any():
                self.forward_said = True
                self.forward_until = now + 40
                events.append(("forward", "Enemy building near your base"))
            if now < self.forward_until:
                alerts.append(("warn", "Enemy building near your base: tower or forward production?"))

            # 5. many more of them on the map than a minute ago
            recent = [v for t, v in self.totals if now - t <= 30]
            before = [v for t, v in self.totals if 60 <= now - t <= 90]
            if recent and before and now - self.growth_said > 120:
                cur, old = max(recent), max(before)
                if cur >= 60 and cur >= 2 * max(old, 1):
                    self.growth_said = now
                    alerts.append(("warn", "Much more enemy army on the map than a minute ago"))

        self.state = {
            "grid": self.grid, "home": list(self.home) if self.home else None, "radius": self.home_radius,
            "me": [[int(y), int(x), int(me_c[y, x])] for y, x in zip(*np.nonzero(me_c))],
            "ally": [[int(y), int(x), int(v)] for (y, x), v in np.ndenumerate(cells(ally, self.grid)) if v],
            "enemy": [[int(y), int(x), int(en_c[y, x])] for y, x in zip(*np.nonzero(en_c))],
            "near": near, "raid": self.raid_on,
        }
        return alerts, events
