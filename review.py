"""
Game review: records each game while you play and saves a summary afterwards.

Every second it keeps what the HUD showed (villagers, idle, population, bank),
where the coach was in the build, and when you and the enemies aged up. When the
game ends (the HUD disappears for a while, or a new game starts) it saves
games/<date>-<civ>.json with a summary:

    villagers at 5, 10 and 15 minutes, vs the build
    when you reached each age, vs the build
    time with idle villagers, time housed, time floating resources
    the longest stretch without a new villager (your Town Center sitting idle)
    the build steps you reached late, and by how much

Nothing leaves your PC.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Dict, List, Optional

AGE_NAMES = {2: "Feudal", 3: "Castle", 4: "Imperial"}
AGE_KEYS = {"feudal": 2, "castle": 3, "imperial": 4}
END_AFTER = 90          # seconds without a readable HUD before a game counts as over
MIN_GAME = 120          # games shorter than this (game seconds) aren't saved


def _mmss(s) -> Optional[str]:
    return None if s is None else f"{int(s) // 60}:{int(s) % 60:02d}"


def build_curve(build) -> List[List[int]]:
    """[[game seconds, villagers]] the build expects, from its timed steps."""
    if build is None:
        return []
    from coach import coach_steps
    pts = []
    for s in coach_steps(build):
        if s.seconds is not None and (not pts or s.seconds >= pts[-1][0]):
            pts.append([s.seconds, s.villagers])
    return pts


def build_ages(build) -> Dict[int, int]:
    out = {}
    for k, v in (getattr(build, "age_times", None) or {}).items():
        from coach import _age_seconds                 # seconds or "m:ss"
        a = int(k) if str(k).isdigit() else AGE_KEYS.get(str(k).lower())
        secs = _age_seconds(v)
        if a and secs is not None:
            out[a] = secs
    return out


def _at(curve: List[List[int]], t: int) -> Optional[int]:
    """Villagers the build expects at game second t (linear between its steps)."""
    if not curve or t < curve[0][0]:
        return None
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        if t0 <= t <= t1:
            return round(v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1))
    return None                                   # past the end of the build: no target


class GameRecorder:
    def __init__(self, out_dir: Optional[str], on_saved=None):
        self.dir = pathlib.Path(out_dir) if out_dir else None
        self.on_saved = on_saved
        self.reset()

    def reset(self):
        self.samples: List[dict] = []
        self.events: List[dict] = []
        self.meta: dict = {}
        self.build = None
        self.last_t: Optional[int] = None
        self.last_live_wall: Optional[float] = None
        self.ages: Dict[str, int] = {}
        self.enemy_ages: Dict[int, int] = {}
        self.step: Optional[int] = None
        self.steps_reached: List[dict] = []

    # ── every frame ───────────────────────────────────────────────────
    def update(self, snap, now: float, coach: Optional[dict] = None, teams: Optional[dict] = None,
               build=None, match=None) -> Optional[str]:
        """Feed one reading. Returns the saved file's path when a game just ended."""
        saved = None
        if not snap or not snap.live or snap.seconds is None:
            if self.samples and self.last_live_wall and now - self.last_live_wall > END_AFTER:
                saved = self.finish()
            return saved
        t = snap.seconds
        if self.samples and self.last_t is not None and t + 30 < self.last_t:
            saved = self.finish()                   # the clock went back: a new game started
        self.last_live_wall = now
        if t == self.last_t:
            return saved
        self.last_t = t
        if build is not None and build is not self.build:
            self.build = build
            self.meta["build"] = {"title": build.title, "civ": build.civ}
            self.meta["build_curve"] = build_curve(build)
            self.meta["build_ages"] = {AGE_NAMES[a]: s for a, s in build_ages(build).items() if a in AGE_NAMES}
        if teams and teams.get("me"):
            self.meta["civ"] = teams["me"].get("civ") or self.meta.get("civ")
            self.meta["enemies"] = [e.get("civ") for e in teams.get("enemies", [])]
            self.meta["allies"] = [a.get("civ") for a in teams.get("allies", [])]
            self.meta["mode"] = teams.get("mode")
            age = teams["me"].get("age")
            if age in AGE_NAMES and AGE_NAMES[age] not in self.ages:
                self.ages[AGE_NAMES[age]] = t
                self.events.append({"t": t, "kind": "age", "text": f"You reached {AGE_NAMES[age]}"})
            for i, e in enumerate(teams.get("enemies", [])):
                a = e.get("age")
                if a in AGE_NAMES and self.enemy_ages.get(i, 1) < a:
                    self.enemy_ages[i] = a
                    self.events.append({"t": t, "kind": "enemy_age", "text": f"Enemy reached {AGE_NAMES[a]}"})
        if match is not None:
            self.meta["map"] = getattr(match, "map", None)
            self.meta["kind"] = getattr(match, "kind", None)
        bank = {k: v for k, v in (snap.bank or {}).items() if v is not None and v < 50000}
        housed = bool(snap.pop is not None and snap.pop_cap is not None and snap.pop_cap < 200 and snap.pop >= snap.pop_cap - 1)
        float_limit = 400 if t < 600 else 800
        self.samples.append({
            "t": t, "vil": snap.villagers_total, "pop": snap.pop, "cap": snap.pop_cap, "idle": snap.idle or 0,
            "on": {k: v for k, v in (snap.villagers or {}).items()}, "bank": bank, "housed": housed,
            "floating": any(v > float_limit for v in bank.values()),
            "pace": coach.get("pace_seconds") if coach else None,
        })
        if coach and coach.get("step"):
            idx = coach["step"].get("index")
            if idx != self.step:
                self.step = idx
                at = coach["step"].get("at")
                due = None
                if at and re.fullmatch(r"\d+:\d\d", at):
                    m, s = at.split(":")
                    due = int(m) * 60 + int(s)
                self.steps_reached.append({"t": t, "index": idx, "text": coach["step"].get("text"),
                                           "villagers": coach["step"].get("villagers"), "due": due,
                                           "late": (t - due) if due is not None else None})
        return saved

    # ── the end of a game ─────────────────────────────────────────────
    def summary(self) -> dict:
        s = self.samples
        if not s:
            return {}
        t0, t1 = s[0]["t"], s[-1]["t"]
        dt_s = [max(1, min(5, b["t"] - a["t"])) for a, b in zip(s, s[1:])] + [1]   # seconds each sample stands for
        def secs(pred):
            return sum(d for x, d in zip(s, dt_s) if pred(x))
        curve = self.meta.get("build_curve") or []

        def vil_at(t):
            got = [x["vil"] for x in s if x["t"] <= t and x["vil"] is not None]
            return max(got) if got else None
        marks = {}
        for minute in (5, 10, 15):
            t = minute * 60
            if t0 <= t <= t1:
                marks[f"{minute}:00"] = {"you": vil_at(t), "build": _at(curve, t)}
        # the longest stretch without a new villager while you weren't maxed out
        longest, start, best = 0, None, None
        prev = None
        for x in s:
            v = x["vil"]
            if v is None:
                continue
            if prev is None or v > prev:
                start = x["t"]
            elif start is not None and v < 100 and x["t"] - start > longest:
                longest, best = x["t"] - start, (start, x["t"])
            prev = v if prev is None else max(prev, v)
        late = sorted([r for r in self.steps_reached if r.get("late") and r["late"] >= 15],
                      key=lambda r: -r["late"])[:5]
        ages = {}
        for name, t in self.ages.items():
            ages[name] = {"you": _mmss(t), "build": _mmss(self.meta.get("build_ages", {}).get(name)),
                          "late": (t - self.meta["build_ages"][name]) if name in self.meta.get("build_ages", {}) else None}
        out = {
            "length": _mmss(t1), "joined_at": _mmss(t0) if t0 > 30 else None,
            "villagers_end": next((x["vil"] for x in reversed(s) if x["vil"] is not None), None),
            "villagers_at": marks, "ages": ages,
            "idle_time": secs(lambda x: x["idle"] > 0), "idle_villager_seconds": sum(x["idle"] * d for x, d in zip(s, dt_s)),
            "housed_time": secs(lambda x: x["housed"]), "floating_time": secs(lambda x: x["floating"]),
            "longest_no_villager": longest, "longest_no_villager_at": [_mmss(best[0]), _mmss(best[1])] if best else None,
            "late_steps": [{"at": _mmss(r["t"]), "due": _mmss(r["due"]), "late": r["late"], "text": r["text"]} for r in late],
            "steps_reached": len(self.steps_reached),
        }
        out["tips"] = tips(out)
        return out

    def finish(self) -> Optional[str]:
        """Save the game if it was long enough; start fresh either way."""
        path = None
        if self.samples and self.dir and self.samples[-1]["t"] - self.samples[0]["t"] >= MIN_GAME:
            summ = self.summary()
            when = dt.datetime.now()
            civ = self.meta.get("civ") or "unknown"
            gid = f"{when:%Y-%m-%d-%H%M}-{civ}"
            data = {"id": gid, "date": when.isoformat(timespec="minutes"), "ended_at": int(when.timestamp()), **self.meta,
                    "summary": summ, "events": self.events, "steps": self.steps_reached,
                    "series": [[x["t"], x["vil"], x["idle"], x["pop"], x["cap"]] for x in self.samples]}
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                path = str(self.dir / f"{gid}.json")
                pathlib.Path(path).write_text(json.dumps(data), encoding="utf-8")
                print(f"  Game saved for review: {path}")
                if self.on_saved:
                    self.on_saved(listing(data))
            except OSError as e:
                print(f"  [review] couldn't save the game: {e}")
                path = None
        self.reset()
        return path


def tips(s: dict) -> List[str]:
    """The two or three things most worth fixing next game, in plain words."""
    out = []
    if s.get("longest_no_villager", 0) >= 45:
        a, b = s.get("longest_no_villager_at") or ["?", "?"]
        out.append(f"No new villager for {s['longest_no_villager']} s ({a} to {b}). Keep the Town Center producing.")
    if s.get("housed_time", 0) >= 20:
        out.append(f"Housed for {s['housed_time']} s. Build a house when you're 3 or 4 population short.")
    if s.get("idle_time", 0) >= 30:
        out.append(f"Villagers stood idle for {s['idle_time']} s in total. Set rally points and check the idle button.")
    for name, a in (s.get("ages") or {}).items():
        if a.get("late") and a["late"] >= 30:
            out.append(f"{name} at {a['you']}, {a['late']} s after the build's {a['build']}.")
    if s.get("floating_time", 0) >= 60:
        out.append(f"Floated resources for {s['floating_time']} s. Add production or spend on upgrades.")
    m10 = (s.get("villagers_at") or {}).get("10:00") or {}
    if m10.get("you") is not None and m10.get("build") and m10["build"] - m10["you"] >= 4:
        out.append(f"{m10['you']} villagers at 10:00 against the build's {m10['build']}.")
    if not out:
        out.append("Clean game: on the build and no long gaps. Try a faster build next.")
    return out[:3]


def listing(data: dict) -> dict:
    """The short version of a saved game, for the list."""
    s = data.get("summary") or {}
    return {"id": data.get("id"), "date": data.get("date"), "ended_at": data.get("ended_at"), "civ": data.get("civ"), "enemies": data.get("enemies") or [],
            "mode": data.get("mode") or data.get("kind"), "map": data.get("map"),
            "build": (data.get("build") or {}).get("title"), "length": s.get("length"),
            "villagers_10": ((s.get("villagers_at") or {}).get("10:00") or {}).get("you"),
            "feudal": ((s.get("ages") or {}).get("Feudal") or {}).get("you"),
            "tip": (s.get("tips") or [None])[0]}


def list_games(out_dir: Optional[str], limit: int = 50) -> List[dict]:
    if not out_dir or not pathlib.Path(out_dir).exists():
        return []
    rows = []
    for p in sorted(pathlib.Path(out_dir).glob("*.json"), reverse=True)[:limit]:
        try:
            rows.append(listing(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return rows


def load_game(out_dir: Optional[str], gid: str) -> Optional[dict]:
    if not out_dir or not re.fullmatch(r"[\w\-]+", gid or ""):
        return None
    p = pathlib.Path(out_dir) / f"{gid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
