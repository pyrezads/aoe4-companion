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
    gamelog         the game's own log: found, path, my_name, my_id, phase (menu/loading/playing/over),
                    kind, map, players [{name, civ, team, ai, out}], my_team, session
    intel           at game start: me {civ}, enemies [{civ, civ_name, players, note, units [{name, counters}]}]
    result          after a game: result (win/loss), seconds, players [{name, civ, team, result, units, kills, apm...}]
    gamelog_path    reply to find_log / set_log_path: path, ok, my_name, my_id, last {kind, map, players}
    builds          reply to list_builds: civ, builds [{id, title, strategy, season, views, map, why}]
    reading_check   reply to reading_check: confidence, missed, clock, villagers, resources, width, height
    error           cmd, message

In:
    {"cmd": "list_builds", "civ": "zhu_xis_legacy"}
    {"cmd": "use_build", "id": "..."}  or  {"cmd": "use_build", "url": "https://aoe4guides.com/builds/..."}
    {"cmd": "build_detail", "id": "...", "civ": "..."}   everything aoe4guides has on one build
    {"cmd": "list_games"} / {"cmd": "game_detail", "id": "..."}   saved games for review
    {"cmd": "list_matches", "refresh": false} / {"cmd": "match_detail", "id": ...} / {"cmd": "save_replay", "id": ...}
    {"cmd": "find_steam", "text": "steamcommunity.com/profiles/7656..."}
    {"cmd": "auto_build"}                 go back to the automatic pick
    {"cmd": "coach_skip", "n": 1}         n = -1 goes back a step
    {"cmd": "set_monitor", "width": 2560, "height": 1440, "x": 0, "y": 0}
    {"cmd": "reading_check"}
    {"cmd": "pin"}                        same as F9: the selected units are enemy units
    {"cmd": "find_log"}                   look for the game's warnings.log
    {"cmd": "set_log_path", "path": "..."}  read this warnings.log (or the folder it's in)
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


def _ranked(row: dict, ranks: dict) -> dict:
    r = ranks.get(row.get("profile_id")) if row.get("profile_id") else None
    return {**row, "rank": r.get("rank"), "rating": r.get("rating"), "level": r.get("level")} if r else row


def _teams(state):
    """During loading the HUD can't be read yet, but the log already knows the teams. Ranks from aoe4world."""
    t = state.get("teams")
    if state.get("log_game") and state.get("log_me") and not (t and t.get("source") == "log"):
        from gamelog import team_summary
        t = team_summary(state.get("log_game"), state.get("log_me"), t)
    ranks = state.get("player_ranks") or {}
    if t and ranks and t.get("me"):
        t = {**t, "me": _ranked(t["me"], ranks), "allies": [_ranked(r, ranks) for r in t.get("allies", [])],
             "enemies": [_ranked(r, ranks) for r in t.get("enemies", [])]}
    return t


def _intel(state):
    it = state.get("intel")
    ranks = state.get("player_ranks") or {}
    if not it or not ranks:
        return it
    return {**it, "enemies": [{**e, "who": [_ranked(w, ranks) for w in e.get("who", [])]} for e in it["enemies"]]}


def _gamelog(state) -> dict:
    st = dict(state.get("log_status") or {"found": False})
    g = state.get("log_game")
    if g:
        st["clock"] = None                     # the HUD clock is shown; this only says the game is on
        st["players"] = [{"name": p.name, "civ": p.civ, "team": p.team, "ai": p.ai, "out": p.out}
                         for p in sorted(g.players, key=lambda p: (p.team, p.slot))]
        me = state.get("log_me")
        st["my_team"] = me.team if me else None
        st["session"] = g.session_id
    return st


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
            "game_window": state.get("game_window"),
            "capture": state.get("capture"),
        },
        "hud": dataclasses.asdict(snap) if snap else None,
        "coach": state.get("coach"),
        "build": _build_summary(state),
        "teams": _teams(state),
        "gamelog": _gamelog(state),
        "intel": _intel(state),
        "result": state.get("log_result"),
        "match": {"lines": state.get("briefing_lines")},
        "scout": state.get("scout"),
        "army": {"lines": state.get("army_lines")},
        "advice": {"enemy": state.get("enemy_lines"), "advice": state.get("advice_lines"),
                   "upgrades": state.get("upgrade_lines")},
    }


def _build_row(choice) -> dict:
    from buildorder import _date, _english, _season_num, _strategy
    b = choice.build
    return {
        "id": b.get("id") or b.get("title"), "title": b.get("title", "Build"),
        "strategy": _strategy(b), "season": _season_num(b), "views": b.get("views") or 0,
        "map": b.get("map") if b.get("map") not in (None, "-", "") else None,
        "author": b.get("creatorName") or b.get("author") or b.get("authorName"),
        "score": round(choice.score, 2), "why": choice.reasons[:2],
        "likes": b.get("likes"), "upvotes": b.get("upvotes"), "downvotes": b.get("downvotes"),
        "video": bool(b.get("video")), "feudal": _feudal(b), "updated": _date(b.get("timeUpdated")),
        "created": _date(b.get("timeCreated")),
        "english": round(_english(b), 2),
    }


def _feudal(b: dict):
    t = ((b.get("ageTimings") or {}).get("feudal") or (b.get("ageTimings") or {}).get("2") or {})
    t = t.get("t") if isinstance(t, dict) else None
    return f"{int(t) // 60}:{int(t) % 60:02d}" if isinstance(t, (int, float)) else None


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

    def do_build_detail(self, msg):
        """Everything aoe4guides has on one build, for the expandable view in the picker."""
        from buildorder import build_detail
        raw = self.library().find(msg.get("id"))
        if raw is None and msg.get("civ"):
            self.library().builds_for(msg["civ"])
            raw = self.library().find(msg.get("id"))
        if raw is None:
            raise KeyError(f"build {msg.get('id')!r} isn't in the list")
        self.emit.send("build_detail", data=build_detail(raw))

    def _games_dir(self):
        an = self.state.get("analyst")
        return an.games_dir if an else None

    def do_list_games(self, msg):
        from review import list_games
        self.emit.send("games", data={"games": list_games(self._games_dir())})

    def do_game_detail(self, msg):
        from review import load_game
        g = load_game(self._games_dir(), msg.get("id"))
        if g is None:
            raise KeyError(f"no saved game {msg.get('id')!r}")
        self.emit.send("game_detail", data=g)

    # ── match history (official Age of Empires service) ───────────────
    def _history(self):
        h = self.state.get("history")
        if h is None:
            from history import History
            h = History(self.cfg.get("cache_dir"))
            self.state.set(history=h)
        return h

    def _pid(self, msg):
        pid = int(msg.get("profile_id") or self.cfg.get("aoe4world_profile_id") or 0)
        if not pid:
            raise ValueError("no profile yet: add your Steam or aoe4 profile in Settings")
        return pid

    def do_list_matches(self, msg):
        """Recent games, with leaderboards and insights. Runs in the background: it can take a few seconds."""
        def work():
            from history import by_period, slim
            try:
                pid = self._pid(msg)
                d = self._history().load(pid, force=bool(msg.get("refresh")))
                self.emit.send("matches", data={"profile": d["profile"], "leaderboards": d.get("leaderboards", []),
                                                "matches": [slim(m) for m in d["matches"]], "periods": (per := by_period(d["matches"])), "insights": per["all"],
                                                "kept_since": min((m["end"] for m in d["matches"] if m.get("end")), default=None),
                                                "fetched_at": d.get("fetched_at"), "offline": d.get("offline", False)})
            except Exception as e:
                self.emit.send("error", data={"cmd": "list_matches", "message": f"{type(e).__name__}: {e}"})
        threading.Thread(target=work, daemon=True).start()

    def do_match_detail(self, msg):
        m = self._history().match(self._pid(msg), msg.get("id"))
        if m is None:
            raise KeyError(f"no match {msg.get('id')!r}")
        self.emit.send("match_detail", data=m)

    def do_save_replay(self, msg):
        def work():
            from history import save_replay
            try:
                m = self._history().match(self._pid(msg), msg.get("id"))
                if not m or not m.get("replay"):
                    raise ValueError("this match has no replay to download")
                path = save_replay(m["replay"], m["id"])
                self.emit.send("replay_saved", data={"id": m["id"], "path": path,
                                                     "launch": f"-dev -replay playback:AgeIV_Replay_{int(m['id'])}"})
            except Exception as e:
                self.emit.send("error", data={"cmd": "save_replay", "message": f"{type(e).__name__}: {e}"})
        threading.Thread(target=work, daemon=True).start()

    def do_find_steam(self, msg):
        """A Steam profile link -> the player's Age of Empires IV profile."""
        from history import profile_for_steam, steam_id_from
        sid = steam_id_from(msg.get("text", ""))
        if not sid:
            self.emit.send("steam_profile", data={"ok": False, "message": "Paste the link from your Steam profile page "
                                                  "(steamcommunity.com/profiles/7656…). Custom /id/ links don't carry the number."})
            return
        def work():
            try:
                p = profile_for_steam(sid)
                self.emit.send("steam_profile", data={"ok": bool(p), "steam": sid, **(p or {}),
                                                      "message": None if p else "No Age of Empires IV profile on that Steam account."})
            except Exception as e:
                self.emit.send("steam_profile", data={"ok": False, "steam": sid,
                                                      "message": f"Couldn't reach the official service ({type(e).__name__}). Try again in a minute."})
        threading.Thread(target=work, daemon=True).start()

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

    def do_set_log_path(self, msg):
        """Settings > Game log > Choose: the warnings.log to read, if it isn't in the usual place."""
        import pathlib
        p = pathlib.Path(str(msg.get("path") or ""))
        if p.is_dir():
            p = p / "warnings.log"
        if not p.is_file():
            raise ValueError(f"no game log at {p}")
        self.cfg["game_log_path"] = str(p)
        self.state.set(log_path_request=str(p))
        self.emit.send("gamelog_path", data={"path": str(p), "ok": True})

    def do_find_log(self, msg):
        from gamelog import default_log_path, GameLog
        p = default_log_path()
        info = {"path": str(p) if p else None, "ok": bool(p)}
        if p:
            gl = GameLog(p)
            gl.poll()
            g = gl.game or gl.last_game
            info.update(my_name=gl.my_name, my_id=gl.my_id,
                        last=None if not g else {"kind": g.kind, "map": g.map, "players": len(g.players)})
        self.emit.send("gamelog_path", data=info)

    def do_pin(self, msg):
        """Same as F9: treat the units selected in game as enemy units."""
        req = self.state.get("pin_request")
        if req:
            req.set()

    def do_set_civ(self, msg):
        """Correct a player's civ (the row in the player list) when the flag was misread."""
        an = self.state.get("analyst")
        if an and msg.get("civ"):
            an.teams.set_civ(int(msg.get("row", -1)), str(msg["civ"]))
            self.state.set(teams=an.teams.summary())
            print(f"  [teams] row {msg.get('row')} corrected to {msg['civ']}")

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
    state.set(army_request=threading.Event(), pin_request=threading.Event(), emit=emit, log_wake=threading.Event())
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
    if str(cfg.get("game_log_path") or "").lower() != "off":
        from gamelog import watch
        threading.Thread(target=watch, args=(state, cfg, stop), daemon=True).start()
    if network:
        threading.Thread(target=match_loop, args=(state, cfg, stop), daemon=True).start()
    threading.Thread(target=_read_commands, args=(stdin or sys.stdin, commands, stop), daemon=True).start()
    if not replay:
        start_hotkey(cfg.get("hotkey", "f9"), lambda: state.get("pin_request").set())
        start_hotkey(cfg.get("army_key", "ctrl+c"), lambda: state.get("army_request").set())
        start_hotkey(cfg.get("coach_skip_key", "f10"),
                     lambda: state.get("analyst") and state.get("analyst").coach_skip())

    emit.send("ready", data={"version": 1, "replay": bool(replay)})
    try:
        _publish_loop(state, emit, stop, publish_every)
    finally:                                          # quitting mid-game still saves the game
        an = state.get("analyst")
        if an and an.recorder.samples:
            an.recorder.finish()


def _publish_loop(state, emit, stop, publish_every):
    from gamewindow import find_game_window
    sent = {}
    next_window_check = 0.0
    while not stop.is_set() and not emit.closed:
        if time.time() >= next_window_check:           # the game's window, for pinning the strip to it
            next_window_check = time.time() + 5
            gw = find_game_window()
            state.set(game_window=gw)
            if gw:                                     # and the monitor it's really on
                from screen import monitor_for_window
                mon = monitor_for_window(gw)
                if mon and mon != state.get("game_monitor"):
                    print(f"  The game is on the {mon['width']}x{mon['height']} monitor at {mon['left']},{mon['top']}")
                    state.set(game_monitor=mon)
        for k, v in _payloads(state).items():
            text = json.dumps(v, default=_jsonable, sort_keys=True)
            if sent.get(k) != text:
                sent[k] = text
                emit.send(k, data=v)
        stop.wait(publish_every)
