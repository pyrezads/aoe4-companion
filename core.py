"""
Core mode: the screen reader and match watcher with no window, for the Electron app.

    python main.py --core [--config path/to/config.json] [--replay a.png,b.png,...]

Talks over stdin/stdout, one JSON object per line. Logs go to stderr, so stdout
only ever carries messages.

Out, as {"type": ..., "data": {...}}. The live ones are only sent when they change.
    ready           sent once at start
    status          live, game_seen, read_quality, missed, match_note, build_note, screen {width, height}
    hud             the economy snapshot: clock, villagers, bank, pop, idle, alerts...
    coach           coach.py's message: next villager, step, pace, age-up (null before a build/game)
    build           title, civ, why, locked, pick, count, steps [{at, text, villagers, age}]
    teams, match, scout, army, advice
    builds          reply to list_builds: civ, builds [{id, title, strategy, season, views, map, why}]
    reading_check   reply to reading_check: confidence, missed, clock, villagers, resources, width, height
    error           cmd, message

In:
    {"cmd": "list_builds", "civ": "zhu_xis_legacy"}
    {"cmd": "use_build", "id": "..."}  or  {"cmd": "use_build", "url": "https://aoe4guides.com/builds/..."}
    {"cmd": "auto_build"}                 go back to the automatic pick
    {"cmd": "coach_skip", "n": 1}         n = -1 goes back a step
    {"cmd": "set_monitor", "width": 2560, "height": 1440, "x": 0, "y": 0}
    {"cmd": "reading_check"}
    {"cmd": "quit"}
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
from typing import Optional

import numpy as np


# ── output ────────────────────────────────────────────────────────────

def _jsonable(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


class Emitter:
    """Writes one JSON message per line. Safe to call from any thread."""

    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()
        self.closed = False

    def send(self, type_: str, **data) -> None:
        if self.closed:
            return
        line = json.dumps({"type": type_, **data}, default=_jsonable, separators=(",", ":"))
        with self.lock:
            try:
                self.stream.write(line + "\n")
                self.stream.flush()
            except (BrokenPipeError, ValueError, OSError):
                self.closed = True      # the app went away


# ── replay screen (tests and demos without the game) ─────────────────

class ReplayScreen:
    """Stands in for GameScreen: each grab returns the next screenshot, looping."""

    def __init__(self, paths):
        from PIL import Image
        self.frames = [np.asarray(Image.open(p).convert("RGB")).astype(np.int16) for p in paths]
        self.i = -1

    def grab(self) -> np.ndarray:
        self.i = (self.i + 1) % len(self.frames)
        return self.frames[self.i]

    def save(self, path):
        from PIL import Image
        Image.fromarray(self.frames[max(self.i, 0)].astype(np.uint8)).save(path)


# ── what the app gets ─────────────────────────────────────────────────

def _build_summary(state) -> Optional[dict]:
    bo = state.get("build")
    if not bo:
        return None
    clock = lambda s: None if s is None else f"{s // 60}:{s % 60:02d}"
    return {
        "title": bo.title, "civ": bo.civ,
        "why": state.get("build_why"), "locked": bool(state.get("build_locked")),
        "pick": state.get("build_pick"), "count": state.get("build_count"),
        "steps": [{"at": clock(s.seconds), "text": s.text, "villagers": s.villagers, "age": s.age} for s in bo.steps],
    }


def _payloads(state) -> dict:
    snap = state.get("snapshot")
    live = bool(snap and snap.live)
    mon = state.get("game_monitor") or {}
    return {
        "status": {
            "live": live, "game_seen": bool(state.get("game_seen")),
            "read_quality": state.get("read_quality"), "missed": state.get("missed") or [],
            "match_note": state.get("match_note"), "build_note": state.get("build_note"),
            "screen": {"width": mon.get("width"), "height": mon.get("height")},
        },
        "hud": dataclasses.asdict(snap) if snap else None,
        "coach": state.get("coach"),
        "build": _build_summary(state),
        "teams": state.get("teams"),
        "match": {"lines": state.get("briefing_lines")},
        "scout": state.get("scout"),
        "army": {"lines": state.get("army_lines")},
        "advice": {"enemy": state.get("enemy_lines"), "advice": state.get("advice_lines"),
                   "upgrades": state.get("upgrade_lines")},
    }


def _build_row(choice) -> dict:
    from buildorder import _season_num, _strategy
    b = choice.build
    return {
        "id": b.get("id") or b.get("title"), "title": b.get("title", "Build"),
        "strategy": _strategy(b), "season": _season_num(b), "views": b.get("views") or 0,
        "map": b.get("map") if b.get("map") not in (None, "-", "") else None,
        "author": b.get("author") or b.get("authorName"),
        "score": round(choice.score, 2), "why": choice.reasons[:2],
    }


# ── commands from the app ─────────────────────────────────────────────

class Commands:
    def __init__(self, state, cfg, emit: Emitter, stop: threading.Event):
        self.state, self.cfg, self.emit, self.stop = state, cfg, emit, stop

    def library(self):
        lib = self.state.get("build_library")
        if lib is None:
            from buildorder import BuildLibrary
            api = self.cfg.get("auto_builds_api") or ""
            lib = BuildLibrary(api, cache_dir=self.cfg.get("cache_dir"))
            self.state.set(build_library=lib)
        return lib

    def handle(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        fn = getattr(self, "do_" + str(cmd), None)
        if fn is None:
            self.emit.send("error", data={"cmd": cmd, "message": f"unknown command {cmd!r}"})
            return
        try:
            fn(msg)
        except Exception as e:                       # one bad command must not stop the core
            self.emit.send("error", data={"cmd": cmd, "message": f"{type(e).__name__}: {e}"})

    def do_list_builds(self, msg):
        from buildorder import rank_builds
        civ = msg["civ"]
        m = self.state.get("match")
        teams = self.state.get("teams") or {}
        enemies = [p.civ for p in m.enemies()] if m else [e["civ"] for e in teams.get("enemies", []) if e.get("civ")]
        kind = m.kind if m else teams.get("mode")
        builds = self.library().builds_for(civ)
        ranked = rank_builds(builds, kind, m.map if m else None, enemies)
        self.emit.send("builds", data={"civ": civ, "builds": [_build_row(c) for c in ranked[:40]],
                                       "offline": self.library().last_source == "cache"})

    def do_use_build(self, msg):
        from buildorder import BuildOrder
        if msg.get("build"):
            bo = BuildOrder.from_json(msg["build"])
        elif msg.get("url"):
            bo = BuildOrder.load(msg["url"])
        else:
            raw = self.library().find(msg.get("id"))
            if raw is None and msg.get("civ"):          # e.g. right after a restart: load that civ's list
                self.library().builds_for(msg["civ"])
                raw = self.library().find(msg.get("id"))
            if raw is None:
                raise KeyError(f"build {msg.get('id')!r} isn't in the list; list the builds first")
            bo = BuildOrder.from_json(raw)
        self.state.set(build=bo, build_why="your pick", build_note=None, build_locked=True,
                       build_choices=None, build_count=None, build_pick=None, build_compare=None)
        print(f"  Build chosen in the app: {bo.title} ({len(bo.steps)} steps)")

    def do_auto_build(self, msg):
        self.state.set(build_locked=False, build_repick=True)

    def do_coach_skip(self, msg):
        an = self.state.get("analyst")
        if an:
            an.coach_skip(int(msg.get("n", 1)))

    def do_set_monitor(self, msg):
        from screen import list_monitors
        mons = list_monitors()
        same = [m for m in mons if (m["width"], m["height"]) == (msg.get("width"), msg.get("height"))] or mons
        x, y = msg.get("x", 0), msg.get("y", 0)
        best = min(same, key=lambda m: abs(m["left"] - x) + abs(m["top"] - y))
        self.state.set(game_monitor=best)

    def do_reading_check(self, msg):
        self.state.set(check_request=True)          # the reader thread answers on its next frame

    def do_quit(self, msg):
        self.stop.set()


def _read_commands(stream, commands: Commands, stop: threading.Event):
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            commands.emit.send("error", data={"cmd": None, "message": f"not JSON: {line[:80]}"})
            continue
        commands.handle(msg)
        if stop.is_set():
            break
    stop.set()                                         # stdin closed: the app quit


# ── run ───────────────────────────────────────────────────────────────

def run_core(cfg: dict, replay=None, stdin=None, stdout=None, publish_every: float = 0.5,
             stop: Optional[threading.Event] = None, network: bool = True) -> None:
    from main import AppState, match_loop, reader_loop, start_hotkey
    out = stdout or sys.stdout
    if stdout is None:
        sys.stdout = sys.stderr                        # prints become logs; stdout is for messages
    emit = Emitter(out)
    stop = stop or threading.Event()

    state = AppState()
    state.set(army_request=threading.Event(), pin_request=threading.Event(), emit=emit)
    commands = Commands(state, cfg, emit, stop)
    commands.library()

    if replay:
        frames = ReplayScreen(replay)
        monitor = {"left": 0, "top": 0, "width": frames.frames[0].shape[1], "height": frames.frames[0].shape[0]}
        make_screen = lambda m: frames
    else:
        from screen import make_dpi_aware, pick_monitors
        make_dpi_aware()
        monitor, _ = pick_monitors()
        make_screen = None
    state.set(game_monitor=monitor)

    threading.Thread(target=reader_loop, args=(state, cfg, monitor, stop, make_screen), daemon=True).start()
    if network:
        threading.Thread(target=match_loop, args=(state, cfg, stop), daemon=True).start()
    threading.Thread(target=_read_commands, args=(stdin or sys.stdin, commands, stop), daemon=True).start()
    if not replay:
        start_hotkey(cfg.get("hotkey", "f9"), lambda: state.get("pin_request").set())
        start_hotkey(cfg.get("army_key", "ctrl+c"), lambda: state.get("army_request").set())
        start_hotkey(cfg.get("coach_skip_key", "f10"),
                     lambda: state.get("analyst") and state.get("analyst").coach_skip())

    emit.send("ready", data={"version": 1, "replay": bool(replay)})
    sent = {}
    while not stop.is_set() and not emit.closed:
        for k, v in _payloads(state).items():
            text = json.dumps(v, default=_jsonable, sort_keys=True)
            if sent.get(k) != text:
                sent[k] = text
                emit.send(k, data=v)
        stop.wait(publish_every)
