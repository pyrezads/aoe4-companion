"""
Live economy tracker: turns raw HUD readings into numbers and alerts.

- Ignores frames that aren't the game (low confidence) and single-frame misreads.
- Estimates gather income per minute from villagers on each resource.
- Measures what actually happened to your bank over the last ~30 s.
- Raises alerts: idle villagers, floating resources, housed, villager production stalled.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import production_math as pm
from hud_reader import HudState

RES = ("food", "wood", "gold", "stone")


@dataclass
class Alert:
    level: str      # "warn" or "info"
    text: str


@dataclass
class EcoSnapshot:
    clock: Optional[str] = None
    seconds: Optional[int] = None
    pop: Optional[int] = None
    pop_cap: Optional[int] = None
    idle: Optional[int] = None
    villagers: Dict[str, Optional[int]] = field(default_factory=dict)
    villagers_total: Optional[int] = None
    bank: Dict[str, Optional[int]] = field(default_factory=dict)
    income_est: Dict[str, float] = field(default_factory=dict)     # per minute, from villagers x rates
    bank_change: Dict[str, float] = field(default_factory=dict)    # per minute, measured
    alerts: List[Alert] = field(default_factory=list)
    selection_total: Optional[int] = None
    selection_counts: List[int] = field(default_factory=list)
    live: bool = False


class EcoTracker:
    def __init__(self, min_confidence: float = 0.6, window: float = 30.0):
        self.min_conf = min_confidence
        self.window = window
        self.history: Deque[tuple] = deque(maxlen=120)   # (wall_time, HudState)
        self.last_good: Optional[HudState] = None
        self.last_pop_growth: Optional[int] = None       # game seconds when population last went up
        self.max_pop = 0
        self.snapshot = EcoSnapshot()

    # A reading must agree with the previous good one on the clock moving forward
    # (or staying, when paused) to be trusted.
    def _plausible(self, s: HudState) -> bool:
        if s.confidence < self.min_conf:
            return False
        if self.last_good and self.last_good.seconds is not None and s.seconds is not None:
            back = self.last_good.seconds - s.seconds
            if back > 5:            # clock went backwards: misread or a new game
                if s.seconds < 60:  # a new game started
                    self.reset()
                    return True
                return False
        return True

    def reset(self):
        self.history.clear()
        self.last_good = None
        self.last_pop_growth = None
        self.max_pop = 0

    def update(self, s: HudState, now: Optional[float] = None) -> EcoSnapshot:
        now = now or time.time()
        if not self._plausible(s):
            self.snapshot.live = False
            return self.snapshot
        self.history.append((now, s))
        self.last_good = s
        snap = EcoSnapshot(clock=s.clock, seconds=s.seconds, pop=s.pop, pop_cap=s.pop_cap, idle=s.idle,
                           villagers=dict(s.villagers), bank=dict(s.resources), live=True,
                           selection_total=s.selection_total, selection_counts=list(s.selection_counts))

        vals = [v for v in s.villagers.values() if v is not None]
        if vals:
            snap.villagers_total = sum(vals) + (s.idle or 0)   # gatherers + idle (builders not shown on the HUD)
        if s.pop is not None and s.pop > self.max_pop:
            self.max_pop = s.pop
            self.last_pop_growth = s.seconds

        snap.income_est = self._income_estimate(s)
        snap.bank_change = self._bank_change(now)
        snap.alerts = self._alerts(snap)
        self.snapshot = snap
        return snap

    def _income_estimate(self, s: HudState) -> Dict[str, float]:
        secs = s.seconds or 0
        food_source = "sheep" if secs < 240 else ("berry" if secs < 420 else "farm")
        rates = pm.gathering_rates(food_source, wheelbarrow=secs > 360)
        return {r: (s.villagers.get(r) or 0) * rates[r] for r in RES}

    def _bank_change(self, now: float) -> Dict[str, float]:
        old = [(t, h) for t, h in self.history if now - t >= self.window * 0.8]
        if not old:
            return {}
        t0, h0 = old[-1]
        _, h1 = self.history[-1]
        if h0.seconds is None or h1.seconds is None or h1.seconds <= h0.seconds:
            return {}
        minutes = (h1.seconds - h0.seconds) / 60
        out = {}
        for r in RES:
            a, b = h0.resources.get(r), h1.resources.get(r)
            if a is not None and b is not None:
                out[r] = (b - a) / minutes
        return out

    def _alerts(self, snap: EcoSnapshot) -> List[Alert]:
        al: List[Alert] = []
        secs = snap.seconds or 0
        if snap.idle and snap.idle > 0:
            al.append(Alert("warn" if snap.idle >= 2 else "info", f"{snap.idle} idle villager{'s' if snap.idle > 1 else ''}"))
        if snap.pop is not None and snap.pop_cap is not None and snap.pop_cap < 200 and snap.pop >= snap.pop_cap - 1:
            al.append(Alert("warn", f"Housed ({snap.pop}/{snap.pop_cap}): build a house"))
        float_limit = 400 if secs < 600 else 800
        floating = [f"{r} {v}" for r, v in snap.bank.items() if v is not None and v > float_limit and v < 50000]
        if floating:
            al.append(Alert("warn", "Floating " + ", ".join(floating) + ": spend it"))
        if (self.last_pop_growth is not None and snap.pop is not None and snap.pop_cap is not None
                and snap.pop < snap.pop_cap - 1 and snap.pop < 150 and secs - self.last_pop_growth > 45 and secs > 60):
            al.append(Alert("warn", f"Population flat for {secs - self.last_pop_growth}s: keep the TC and buildings producing"))
        return al


# ── army, read whenever you press Ctrl+C (select all military) ─────────

@dataclass
class ArmyReading:
    seconds: Optional[int]
    clock: Optional[str]
    total: int
    groups: List[int]


class ArmyTracker:
    """Keeps your Ctrl+C army readings for this game and describes the trend."""

    def __init__(self):
        self.readings: List[ArmyReading] = []
        self.peak = 0

    def reset(self):
        self.readings.clear()
        self.peak = 0

    def record(self, seconds, clock, total, groups) -> ArmyReading:
        if self.readings and seconds is not None and self.readings[-1].seconds is not None \
                and seconds + 5 < self.readings[-1].seconds:
            self.reset()                      # clock went back: new game
        r = ArmyReading(seconds, clock, total, list(groups))
        self.readings.append(r)
        self.peak = max(self.peak, total)
        return r

    def summary(self) -> List[str]:
        if not self.readings:
            return []
        cur = self.readings[-1]
        lines = [f"{cur.clock or '--:--'}  ·  {cur.total} military  ·  groups: "
                 + (", ".join(str(g) for g in cur.groups) or "?")]
        if len(self.readings) >= 2:
            prev = self.readings[-2]
            d = cur.total - prev.total
            span = (cur.seconds - prev.seconds) if cur.seconds is not None and prev.seconds is not None else None
            when = f" in {span // 60}:{span % 60:02d}" if span is not None and span >= 0 else ""
            if d < 0 and prev.total and -d >= max(3, 0.15 * prev.total):
                lines.append(f"Lost {-d} units{when} (was {prev.total}): rebuild or pull back")
            elif d:
                lines.append(f"{'+' if d > 0 else ''}{d} since {prev.clock}{when}")
        if self.peak > cur.total:
            lines.append(f"Peak this game: {self.peak}")
        return lines
