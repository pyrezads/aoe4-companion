"""
Build coach: where the next villager should go, following an aoe4guides build.

Fed once a second with what the HUD shows (villagers on each resource, idle,
game clock). Follows the build by villager count rather than by clock, so a
player who is behind still gets the right next move instead of skipping ahead.

    coach = Coach(build_order)
    msg = coach.update(seconds, {"food": 6, "wood": 2, "gold": 0, "stone": 0}, idle=0)
    msg["next"]["resource"]   -> "wood"

The message is what the overlay draws (see app/renderer/overlay.html, render()).

What it can't know: which building a villager is putting up, the Town Center
queue, or what each villager is doing. Builders vanish from the resource counts
for a few seconds, so short gaps of one villager are only flagged after a while.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from buildorder import BuildOrder, Step

RES = ("food", "wood", "gold", "stone")
AGE_NAME = {2: "Feudal", 3: "Castle", 4: "Imperial"}


def _clock(s: Optional[int]) -> str:
    return "--:--" if s is None else f"{s // 60}:{s % 60:02d}"


def _age_seconds(v) -> Optional[int]:
    """ageTimings values are seconds or "m:ss"."""
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and ":" in v:
        m, s = v.strip().lstrip("~").split(":")[:2]
        if m.isdigit() and s[:2].isdigit():
            return int(m) * 60 + int(s[:2])
    return None


@dataclass
class CoachStep:
    index: int                 # position in the build
    villagers: int             # total villagers this step is for
    targets: Dict[str, int]    # villagers wanted on each resource (missing = 0)
    seconds: Optional[int]
    age: int
    text: str


def coach_steps(build: BuildOrder) -> List[CoachStep]:
    """The build's steps with a villager total each.

    Uses the step's own villager count when aoe4guides has one, otherwise
    adds up its resource targets and builders. Each step keeps the targets of
    the step before for any resource it leaves blank ("+" means "keep adding",
    no fixed number). Totals never go down, so a "move 3 from sheep to wood"
    step stays on the same villager count.
    """
    out: List[CoachStep] = []
    prev = {r: 0 for r in RES}
    last_total = 0
    for i, s in enumerate(build.steps):
        t = {r: (s.targets.get(r) if s.targets.get(r) is not None else prev[r]) for r in RES}
        total = s.villagers if s.villagers else sum(t.values()) + (s.builders or 0)
        total = max(total, last_total)
        if total == 0:                       # a note with no numbers at all
            continue
        out.append(CoachStep(i, total, t, s.seconds, s.age, s.text))
        prev, last_total = t, total
    return out


class Coach:
    def __init__(self, build: BuildOrder, by: str = "villagers", short_after: float = 20.0,
                 drop_after: float = 45.0):
        self.drop_after = drop_after          # seconds a lower villager total must last to be believed
        self.build = build
        self.by = by                          # "villagers" (default) or "clock"
        self.steps = coach_steps(build)
        self.short_after = short_after        # seconds a one-villager gap may last before it's flagged
        self.reset()

    def reset(self):
        self.total: Optional[int] = None      # trusted villager total
        self._pending: Optional[int] = None   # a new total seen once, waiting for a second reading
        self._pending_at = 0
        self._drop_since: Optional[int] = None
        self.reached: Dict[int, int] = {}     # villager total -> game second it was first reached
        self.start_total = 0
        self.offset = 0                       # manual skip (+) / back (-)
        self.locked: Optional[str] = None     # current "next villager" answer, kept until it's done
        self.gap_since: Dict[str, Optional[int]] = {r: None for r in RES}
        self.last: Optional[dict] = None

    # ── manual control (F10 / back button) ────────────────────────────
    def skip(self, n: int = 1):
        self.offset += n
        self.locked = None

    # ── one reading ───────────────────────────────────────────────────
    def update(self, seconds: Optional[int], villagers: Dict[str, Optional[int]],
               idle: Optional[int] = 0, my_age: Optional[int] = None) -> Optional[dict]:
        """Returns the overlay message, or the last good one if this reading can't be trusted."""
        if not self.steps:
            return None
        have = {r: villagers.get(r) for r in RES}
        if any(v is None for v in have.values()) or seconds is None:
            return self.last                  # a field was misread: keep showing the last answer
        idle = idle or 0
        total = sum(have.values()) + idle

        # Going up: the new total must show up twice in a row (one-frame misreads happen).
        # Going down: builders aren't on the HUD, so the total dips whenever someone builds.
        # Only believe a drop that lasts (villagers really died).
        if self.total is None:
            self.start_total = total          # joined mid-game: no pace for what came before
            self.total = total
        elif total > self.total:
            self._drop_since = None
            if self._pending == total:
                for n in range(max(self.total, self.start_total) + 1, total + 1):
                    self.reached.setdefault(n, self._pending_at)
                self.total, self._pending = total, None
                self.locked = None            # a villager arrived: work out the next one fresh
            else:
                self._pending, self._pending_at = total, seconds
        elif total < self.total:
            self._pending = None
            if self._drop_since is None:
                self._drop_since = seconds
            elif seconds - self._drop_since >= self.drop_after:
                self.total, self._drop_since = total, None
        else:
            self._pending, self._drop_since = None, None
        total = self.total

        i = self._step_index(seconds, total)
        step = self.steps[i]
        goal = self.steps[i + 1] if i + 1 < len(self.steps) else step
        upcoming = self.steps[i + 1] if i + 1 < len(self.steps) else None

        # Idle villagers first: each goes where the gap is biggest.
        virt = dict(have)
        idle_to = []
        for _ in range(idle):
            r = self._pick(goal, step, virt)
            idle_to.append(r)
            virt[r] += 1

        # The villager about to come out. Keep the earlier answer while it's still short,
        # so builders popping in and out of the counts don't make the big word flicker.
        if self.locked and goal.targets[self.locked] - virt[self.locked] > 0:
            nxt = self.locked
        else:
            nxt = self._pick(goal, step, virt)
            self.locked = nxt
        after = dict(virt)
        after[nxt] += 1
        then = self._pick(goal, step, after, strict=True)

        short = self._short(goal, have, seconds)
        msg = {
            "clock": _clock(seconds),
            "villagers": total,
            "have": have,
            "want": dict(goal.targets),
            "idle": idle,
            "idle_to": idle_to,
            "next": {"resource": nxt, "then": then},
            "short": short,
            "step": {"text": step.text, "at": _clock(step.seconds), "villagers": step.villagers, "index": step.index},
            "upcoming": ({"text": upcoming.text, "at": _clock(upcoming.seconds), "villagers": upcoming.villagers}
                         if upcoming else None),
            "pace_seconds": self._pace(seconds, step, upcoming),
            "age": self._age(seconds, step, my_age),
            "build": {"title": self.build.title, "civ": self.build.civ},
            "done": upcoming is None and total >= step.villagers,
        }
        self.last = msg
        return msg

    # ── helpers ───────────────────────────────────────────────────────
    def _step_index(self, seconds: int, total: int) -> int:
        if self.by == "clock":
            idx = 0
            for k, s in enumerate(self.steps):
                if s.seconds is not None and s.seconds <= seconds:
                    idx = k
        else:
            idx = 0
            for k, s in enumerate(self.steps):
                if s.villagers <= total:
                    idx = k
            # Several steps can share a villager count (e.g. "build a house" notes).
            # Stay on the first of them until the next one's time has come.
            first = next(k for k, s in enumerate(self.steps) if s.villagers == self.steps[idx].villagers)
            for k in range(first, idx + 1):
                s = self.steps[k]
                if s.seconds is not None and s.seconds > seconds + 10 and k > first:
                    idx = k - 1
                    break
        return max(0, min(len(self.steps) - 1, idx + self.offset))

    @staticmethod
    def _pick(goal: CoachStep, step: CoachStep, have: Dict[str, int], strict: bool = False) -> Optional[str]:
        """Resource furthest below the goal. Ties go to the one the build is adding to."""
        best, key = None, None
        for r in RES:
            gap = goal.targets[r] - have[r]
            k = (gap, goal.targets[r] - step.targets[r], -RES.index(r))
            if gap > 0 and (key is None or k > key):
                best, key = r, k
        if best or strict:
            return best
        # Everything is filled for the next step: keep adding where the build last added.
        grow = max(RES, key=lambda r: (goal.targets[r] - step.targets[r], goal.targets[r], -RES.index(r)))
        return grow if goal.targets[grow] > 0 else "food"

    def _short(self, goal: CoachStep, have: Dict[str, int], seconds: int) -> Dict[str, bool]:
        out = {}
        for r in RES:
            gap = goal.targets[r] - have[r]
            if gap <= 0:
                self.gap_since[r] = None
                out[r] = False
                continue
            if self.gap_since[r] is None:
                self.gap_since[r] = seconds
            out[r] = gap >= 2 or seconds - self.gap_since[r] >= self.short_after
        return out

    def _pace(self, seconds: int, step: CoachStep, upcoming: Optional[CoachStep]) -> int:
        """Positive = behind the build, negative = ahead."""
        pace = 0
        if step.seconds is not None and step.villagers in self.reached and step.villagers > self.start_total:
            pace = self.reached[step.villagers] - step.seconds
        if upcoming and upcoming.seconds is not None and seconds > upcoming.seconds:
            pace = max(pace, seconds - upcoming.seconds)       # its time passed and you're not there yet
        return pace

    def _age(self, seconds: int, step: CoachStep, my_age: Optional[int]) -> Optional[dict]:
        age_now = max(my_age or 1, step.age)
        times = {int(k): _age_seconds(v) for k, v in self.build.age_times.items() if str(k).isdigit()}
        nxt = min((a for a in times if a > age_now and times[a]), default=None)
        if not nxt:
            return None
        at = times[nxt]
        return {"target": AGE_NAME.get(nxt, f"Age {nxt}"), "at": _clock(at), "progress": round(min(1.0, seconds / at), 3)}
