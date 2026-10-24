"""
The game's own log (warnings.log), read live.

Age of Empires IV writes Documents\\My Games\\Age of Empires IV\\warnings.log while it runs and
starts it over at every launch. It names every player the moment a match starts loading, so
civs, teams and the opponents' profile IDs never have to be read off the screen:

    [Match Flow] MatchSetup::StartMatch() - m_matchStatus = ST_STARTING [match type: 23, session id 257199960]
    GAME -- Human Player: 4 Spade ♠ 26229781 1 hre_ha_01        slot, name, profile id, team, civ
    GAME -- AI Player: 1 2 A.I. Intermediate -1 1 french
    MapGen -- Generating with layout black_forest
    GAME -- Starting mission:                                     the game clock starts (0:00)
    Player [NarkyNarc] killed on game tick [405] due to [loss]    someone lost, resigned or left
    MOD -- Game Over at frame 484
    Report sent for profileID[3381880] -> race=[106553], result=[1], XP Gain=[203]
    "GameResultNotificationMessage" ...                           everyone's end-of-game stats
    MatchSetupManager::RequestLeaveGameMatch                      back to the menu

It only logs events: no resources, villagers or armies during the game.

    GameLog(path).poll()   reads what's new and returns the events it found
    python gamelog.py [path]   prints what a log says (the live one if no path)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# the game's race names -> the civ ids the rest of the app (and aoe4world) uses
RACE_TO_CIV = {
    "abbasid": "abbasid_dynasty", "abbasid_ha_01": "ayyubids",
    "byzantine": "byzantines", "byzantine_ha_mac": "macedonian_dynasty",
    "chinese": "chinese", "chinese_ha_01": "zhu_xis_legacy",
    "english": "english", "lancaster": "house_of_lancaster",
    "french": "french", "french_ha_01": "jeanne_darc",
    "hre": "holy_roman_empire", "hre_ha_01": "order_of_the_dragon",
    "japanese": "japanese", "japanese_ha_sen": "sengoku_daimyo",
    "jin_dynasty": "jin_dynasty", "jin": "jin_dynasty",
    "malian": "malians", "mongol": "mongols", "mongol_ha_gol": "golden_horde",
    "ottoman": "ottomans", "rus": "rus",
    "sultanate": "delhi_sultanate", "sultanate_ha_tug": "tughlaq_dynasty",
    "templar": "knights_templar",
}

# online match types seen in the log: 20-23 are the 1v1..4v4 quick match queues
QUICK_MATCH_TYPES = {20: 1, 21: 2, 22: 3, 23: 4}
SKIRMISH_TYPE = 52

_TIME = re.compile(r"^\([A-Z]\) \[(\d\d):(\d\d):(\d\d)\.(\d{3})\]")
_STARTED = re.compile(r"^RelicCardinal started at (\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)")
_PLAYER = re.compile(r"GAME -- (Human|AI) Player: (\d+) (.*) (-?\d+) (-?\d+) ([a-z0-9_]+)\s*$")
_START_MATCH = re.compile(r"MatchSetup::StartMatch\(\).*\[match type: (\d+), session id (-?\d+)\]")
_MAPGEN = re.compile(r"MapGen -- Generating with (biome|layout|size) (\S+)")
_KILLED = re.compile(r"Player \[(.+?)\] killed on game tick \[(\d+)\] due to \[([a-z_ ]+)\]")
_REPORT = re.compile(r"Report sent for profileID\[(\d+)\] -> race=\[(\d+)\], result=\[(\d+)\]")
_STEAM_NAME = re.compile(r"GAME -- Current Steam name is \[(.*)\]")
_MY_ID = re.compile(r"GetPartyStatsByID found \d+ teams for user ID (\d+)")
_AUTOMATCH = re.compile(r'"Automatch2GameMessage",\d+,\["\d+","(\d+)","(scen_[a-z0-9_]+)"')
_PERF_FPS = re.compile(r"Local user framerate is \[([\d.]+)\]")
_PERF_RTT = re.compile(r"Local user MinDelay=.*avgRTT\[(\d+)ms\]")


def civ_from_race(race: str) -> str:
    return RACE_TO_CIV.get(race, race)


def map_title(layout: Optional[str]) -> Optional[str]:
    """black_forest -> Black Forest"""
    if not layout:
        return None
    return " ".join(w.capitalize() for w in layout.split("_"))


LOG_REL = pathlib.Path("My Games") / "Age of Empires IV" / "warnings.log"


def _documents_folders() -> List[pathlib.Path]:
    """Every place Windows may keep this user's Documents: the real one (Known Folder, which
    follows OneDrive and folder moves), the registry's shell folder, then the usual spots."""
    out: List[pathlib.Path] = []
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                out.append(pathlib.Path(buf.value))
                ctypes.windll.ole32.CoTaskMemFree(buf)
        except Exception:
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, "Personal")
                out.append(pathlib.Path(os.path.expandvars(v)))
        except Exception:
            pass
    home = pathlib.Path.home()
    out.append(home / "Documents")
    try:                                   # OneDrive, "OneDrive - Personal", "OneDrive - Company", ...
        for d in sorted(home.glob("OneDrive*")):
            out += [d / "Documents", d / "Dokumente", d / "Documentos", d / "Documenti"]
    except OSError:
        pass
    seen, uniq = set(), []
    for d in out:
        k = str(d).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


def default_log_path() -> Optional[pathlib.Path]:
    """The game's warnings.log for this user, wherever Documents lives. None if the game
    has never been started on this PC."""
    for docs in _documents_folders():
        p = docs / LOG_REL
        if p.exists():
            return p
    return search_log_path()


def search_log_path(max_dirs: int = 4000) -> Optional[pathlib.Path]:
    """Last resort: look a few levels under the home folder for My Games/Age of Empires IV
    (a Documents folder moved somewhere odd). Bounded, so it takes a second at most."""
    home = pathlib.Path.home()
    seen = 0
    stack = [(home, 0)]
    skip = {"appdata", "node_modules", ".git", "$recycle.bin", "windows", "program files"}
    while stack and seen < max_dirs:
        d, depth = stack.pop()
        seen += 1
        p = d / LOG_REL
        try:
            if p.exists():
                return p
            if depth < 3:
                for c in d.iterdir():
                    if c.is_dir() and not c.name.startswith(".") and c.name.lower() not in skip:
                        stack.append((c, depth + 1))
        except OSError:
            continue
    return None


@dataclass
class LogPlayer:
    slot: int
    name: str
    profile_id: Optional[int]       # None for AI
    team: int
    race: str
    civ: str
    ai: bool = False
    out: Optional[str] = None       # "loss", "surrender"... once they're out
    result: Optional[str] = None    # "win" / "loss" when the game reports it
    stats: Dict[str, float] = field(default_factory=dict)


@dataclass
class LogGame:
    session_id: Optional[int] = None
    match_type: Optional[int] = None
    players: List[LogPlayer] = field(default_factory=list)
    biome: Optional[str] = None
    layout: Optional[str] = None
    size: Optional[str] = None
    loading_at: Optional[float] = None      # wall-clock time the players were announced
    started_at: Optional[float] = None      # wall-clock time of 0:00
    ended_at: Optional[float] = None        # "Game Over"
    left_at: Optional[float] = None         # back to the menu
    fps: Optional[float] = None
    ping_ms: Optional[int] = None

    @property
    def phase(self) -> str:
        if self.left_at:
            return "left"
        if self.ended_at:
            return "over"
        if self.started_at:
            return "playing"
        return "loading" if self.players else "matched"

    @property
    def online(self) -> bool:
        return self.match_type not in (None, SKIRMISH_TYPE) and (self.session_id or 0) > 0

    @property
    def team_size(self) -> int:
        teams: Dict[int, int] = {}
        for p in self.players:
            teams[p.team] = teams.get(p.team, 0) + 1
        return max(teams.values()) if teams else 0

    @property
    def kind(self) -> str:
        n = QUICK_MATCH_TYPES.get(self.match_type or -1) or self.team_size or 1
        if self.match_type in QUICK_MATCH_TYPES:
            return f"qm_{n}v{n}"
        if not self.online:
            return f"custom_{n}v{n}"
        return f"rm_{n}v{n}"

    @property
    def map(self) -> Optional[str]:
        return map_title(self.layout)

    def clock(self, now: float) -> Optional[int]:
        """Seconds since 0:00 (no pauses counted, so it can drift after a pause)."""
        if not self.started_at:
            return None
        end = self.ended_at or now
        return max(0, int(end - self.started_at))

    def me(self, my_id: Optional[int], my_name: Optional[str]) -> Optional[LogPlayer]:
        for p in self.players:
            if my_id and p.profile_id == my_id:
                return p
        for p in self.players:
            if my_name and not p.ai and p.name == my_name:
                return p
        return None


@dataclass
class Event:
    kind: str           # game_start, players, map, clock_start, player_out, game_over, results, left, perf
    t: float
    data: dict = field(default_factory=dict)
    game: Optional["LogGame"] = None    # the match it belongs to (a poll can cover several)


class GameLog:
    """Reads warnings.log as it grows. Call poll() every half second or so."""

    def __init__(self, path=None):
        self.path = pathlib.Path(path) if path else default_log_path()
        self.offset = 0
        self.ident = None               # (size at first read, first line) to notice a new launch
        self.day: Optional[_dt.date] = None
        self.last_t: Optional[float] = None
        self.my_name: Optional[str] = None
        self.my_id: Optional[int] = None
        self.game: Optional[LogGame] = None
        self.last_game: Optional[LogGame] = None
        self._pending_map: Optional[str] = None
        self._partial = b""

    # ── reading ───────────────────────────────────────────────────────
    def poll(self) -> List[Event]:
        if not self.path or not self.path.exists():
            return []
        try:
            size = self.path.stat().st_size
            with open(self.path, "rb") as f:
                head = f.readline(200)
                if self.ident is None or head != self.ident or size < self.offset:
                    self._reset(head)          # the game restarted: a brand new log
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        if not chunk:
            return []
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()            # a line still being written
        self.offset += len(chunk)
        events: List[Event] = []
        for raw in lines:
            events += self.feed(raw.decode("utf-8", "replace").rstrip("\r"))
        return events

    def _reset(self, head: bytes):
        self.ident = head
        self.offset = 0
        self._partial = b""
        self.game = None
        self.day = None
        self.last_t = None

    def _time(self, line: str) -> Optional[float]:
        m = _TIME.match(line)
        if not m or not self.day:
            return self.last_t
        h, mi, s, ms = (int(x) for x in m.groups())
        t = _dt.datetime.combine(self.day, _dt.time(h, mi, s, ms * 1000)).timestamp()
        if self.last_t and t < self.last_t - 3600:        # past midnight
            self.day += _dt.timedelta(days=1)
            t += 86400
        self.last_t = t
        return t

    # ── parsing one line ──────────────────────────────────────────────
    def feed(self, line: str) -> List[Event]:
        m = _STARTED.match(line)
        if m:
            y, mo, d, h, mi = (int(x) for x in m.groups())
            self.day = _dt.date(y, mo, d)
            self.last_t = _dt.datetime(y, mo, d, h, mi).timestamp()
            return []
        if "GAME --" not in line and "MapGen" not in line and "Match" not in line and "Player" not in line \
                and "Report sent" not in line and "Automatch2" not in line and "Local user" not in line \
                and "user ID" not in line and "Game Over" not in line and "GameObj::Shutdown" not in line \
                and "GameResultNotification" not in line:
            return []
        t = self._time(line) or 0.0
        out: List[Event] = []

        if (m := _STEAM_NAME.search(line)):
            self.my_name = m.group(1)
        elif (m := _MY_ID.search(line)):
            self.my_id = int(m.group(1))
        elif (m := _AUTOMATCH.search(line)):
            self._pending_map = m.group(2)
        elif (m := _START_MATCH.search(line)):
            if self.game and self.game.phase not in ("left",):
                self._finish(t, out)
            self.game = LogGame(match_type=int(m.group(1)), session_id=int(m.group(2)))
            out.append(Event("game_start", t, {"match_type": self.game.match_type, "session_id": self.game.session_id}))
        elif (m := _PLAYER.search(line)):
            if self.game is None or self.game.started_at:
                self.game = LogGame()
            kind, slot, name, pid, team, race = m.groups()
            ai = kind == "AI"
            if ai:
                name = re.sub(r"^\d+ ", "", name)       # "2 A.I. Intermediate" -> "A.I. Intermediate"
            pid_i = int(pid)
            p = LogPlayer(slot=int(slot), name=name, profile_id=pid_i if pid_i > 0 else None, team=int(team),
                          race=race, civ=civ_from_race(race), ai=ai)
            self.game.players = [q for q in self.game.players if q.slot != p.slot] + [p]
            if self.game.loading_at is None:
                self.game.loading_at = t
            out.append(Event("players", t, {"player": p.name, "civ": p.civ, "team": p.team}))
        elif (m := _MAPGEN.search(line)) and self.game:
            setattr(self.game, m.group(1), m.group(2))
            if m.group(1) == "layout":
                out.append(Event("map", t, {"map": self.game.map}))
        elif "GAME -- Starting mission" in line and self.game and not self.game.started_at:
            self.game.started_at = t
            out.append(Event("clock_start", t, {}))
        elif (m := _KILLED.search(line)) and self.game:
            name, tick, why = m.groups()
            if why != "unused player" and name:
                p = next((q for q in self.game.players if q.name == name), None)
                if p and not p.out:
                    p.out = why
                    out.append(Event("player_out", t, {"player": name, "why": why, "team": p.team,
                                                        "civ": p.civ, "tick": int(tick)}))
        elif "MOD -- Game Over" in line and self.game and not self.game.ended_at:
            self.game.ended_at = t
            out.append(Event("game_over", t, {}))
        elif (m := _REPORT.search(line)) and self.game:
            pid, _race, res = m.groups()
            p = next((q for q in self.game.players if q.profile_id == int(pid)), None)
            if p:                                   # 1 win, 0 loss; 3 = you left before it was decided
                p.result = {"1": "win", "0": "loss"}.get(res)
        elif '"GameResultNotificationMessage"' in line and self.game:
            self._read_results(line)
            out.append(Event("results", t, {"players": [(p.name, p.result, p.stats) for p in self.game.players]}))
        elif ("MatchSetupManager::RequestLeaveGameMatch" in line or "GameObj::ShutdownGameObj" in line) and self.game:
            if not self.game.left_at:
                self._finish(t, out)
        elif (m := _PERF_FPS.search(line)) and self.game:
            self.game.fps = float(m.group(1))
        elif (m := _PERF_RTT.search(line)) and self.game:
            self.game.ping_ms = int(m.group(1))
            if self.game.started_at:
                out.append(Event("perf", t, {"fps": self.game.fps, "ping": self.game.ping_ms}))
        for ev in out:
            ev.game = ev.game or self.game
        if self.game and self._pending_map and not self.game.layout:
            # "scen_black_forest_small" from the matchmaker, until the map generator says it
            self.game.layout = re.sub(r"^scen_|_(tiny|small|medium|large|huge)$", "", self._pending_map)
        return out

    def _finish(self, t: float, out: List[Event]):
        g = self.game
        g.left_at = t
        out.append(Event("left", t, {}, game=g))
        self.last_game = g
        self._pending_map = None

    def _read_results(self, line: str):
        """Per-player end-of-game stats from the server's result message."""
        # the message is JSON: [0,"GameResultNotificationMessage",me,[[players...], ...]]
        try:
            start = line.index("[0,\"GameResultNotificationMessage\"")
            msg = json.loads(line[start:].strip())
            rows = msg[3][0]
        except (ValueError, IndexError, TypeError):
            return
        for row in rows:
            try:
                pid, _mt, _race, team, _x, _sg, stats = row[:7]
            except (ValueError, TypeError):
                continue
            p = next((q for q in self.game.players if q.profile_id == pid), None)
            if p:
                p.stats = {k: v for k, v in stats if isinstance(v, (int, float))}

    # ── handy views ───────────────────────────────────────────────────
    def current(self) -> Optional[LogGame]:
        """The match being loaded or played right now (None in the menus)."""
        g = self.game
        return g if g and g.players and g.phase in ("loading", "playing", "over") else None

    def me(self, profile_id: Optional[int] = None) -> Optional[LogPlayer]:
        g = self.game
        return g.me(profile_id or self.my_id, self.my_name) if g else None


STALE_AFTER = 150      # seconds without a log write mid-game: the game crashed or was killed


def is_stale(gl: GameLog, now: float) -> bool:
    try:
        return now - gl.path.stat().st_mtime > STALE_AFTER
    except (OSError, AttributeError):
        return True


def to_match(g: LogGame, me: Optional[LogPlayer]):
    """The log's game as the aoe4world Match the rest of the app already understands."""
    from aoe4world import Match, Player
    players = [Player(name=p.name, profile_id=p.profile_id, civ=p.civ, rating=None, mmr=None, team=p.team)
               for p in sorted(g.players, key=lambda p: p.slot)]
    gid = g.session_id if g.online else f"local-{int(g.loading_at or 0)}"
    started = _dt.datetime.fromtimestamp(g.started_at or g.loading_at or 0).isoformat(timespec="seconds")
    return Match(game_id=gid, kind=g.kind, map=g.map or "?", map_id=None, ongoing=g.phase in ("loading", "playing"),
                 started_at=started, players=players, my_team=me.team if me else 0)


def team_summary(g: Optional[LogGame], me: Optional[LogPlayer], screen: Optional[dict] = None) -> Optional[dict]:
    """The Teams view from the log (exact civs, names, teams), with ages from the screen when it has them."""
    if not g or not me:
        return screen
    scr = screen or {}

    def row(p: LogPlayer, screen_rows) -> dict:
        age = next((r.get("age") for r in screen_rows or [] if r.get("civ") == p.civ and r.get("age")), None)
        return {"civ": p.civ, "age": age, "name": p.name, "ai": p.ai, "out": p.out, "profile_id": p.profile_id}
    allies = [p for p in g.players if p.team == me.team and p is not me]
    enemies = [p for p in g.players if p.team != me.team]
    n = max(len(allies) + 1, len(enemies))
    return {
        "mode": f"rm_{n}v{n}" if not g.kind.startswith("qm") else g.kind,
        "me": {**row(me, [scr.get("me")] if scr.get("me") else []), "row": None},
        "allies": [row(p, scr.get("allies")) for p in sorted(allies, key=lambda p: p.slot)],
        "enemies": [row(p, scr.get("enemies")) for p in sorted(enemies, key=lambda p: p.slot)],
        "source": "log", "map": g.map, "kind": g.kind,
    }


FULL_NAMES = {"order_of_the_dragon": "Order of the Dragon", "abbasid_dynasty": "Abbasid Dynasty",
              "delhi_sultanate": "Delhi Sultanate", "jin_dynasty": "Jin Dynasty", "knights_templar": "Knights Templar",
              "macedonian_dynasty": "Macedonian Dynasty", "sengoku_daimyo": "Sengoku Daimyo",
              "tughlaq_dynasty": "Tughlaq Dynasty", "zhu_xis_legacy": "Zhu Xi's Legacy",
              "house_of_lancaster": "House of Lancaster", "holy_roman_empire": "Holy Roman Empire"}


def enemy_intel(g: LogGame, me: LogPlayer, db=None, age: int = 3) -> dict:
    """For the coach's "who you're facing" bar: each enemy civ, its key units and your answers."""
    from aoe4world import civ_name
    from combat import counters_for_unit
    from units import UnitDB
    try:
        from buildorder import AGGRESSIVE
    except Exception:
        AGGRESSIVE = set()
    db = db or UnitDB()
    by_civ: Dict[str, List[str]] = {}
    for p in sorted((p for p in g.players if p.team != me.team), key=lambda p: p.slot):
        by_civ.setdefault(p.civ, []).append(p.name)
    enemies = []
    for civ, names in by_civ.items():
        units = []
        for uid in db.signature_units(civ, age, top=2):
            v = db.best_variant(uid, civ, age)
            if v:
                units.append({"name": db.base_name(uid, civ), "counters": counters_for_unit(db, me.civ, age, v)})
        who = [{"name": p.name, "profile_id": p.profile_id, "ai": p.ai}
               for p in sorted(g.players, key=lambda p: p.slot) if p.team != me.team and p.civ == civ]
        enemies.append({"civ": civ, "civ_name": FULL_NAMES.get(civ, civ_name(civ)), "players": names, "who": who, "units": units,
                        "note": "Often goes for early pressure" if civ in AGGRESSIVE else None})
    return {"session": g.session_id if g.online else f"local-{int(g.loading_at or 0)}",
            "me": {"civ": me.civ, "civ_name": FULL_NAMES.get(me.civ, civ_name(me.civ))}, "enemies": enemies,
            "map": g.map, "kind": g.kind, "allies": len([p for p in g.players if p.team == me.team]) - 1}


def watch(state, cfg: dict, stop, every: float = 0.5) -> None:
    """Background thread: keeps the app's view of the current match in step with the log.

    Sets in state: log_status, log_game, log_me, log_match, intel, log_alerts, log_result,
    log_new_game (session id at 0:00), log_left (time you left the match). Sets log_wake after
    every change so the match loop reacts at once instead of on its next 8 second poll."""
    import time
    gl = GameLog(cfg.get("game_log_path") or None)
    db = None
    intel_for = None
    next_look = 0.0
    alerts: List[tuple] = []
    state.set(log_status=_status(gl, None))
    while not stop.is_set():
        now = time.time()
        if (not gl.path or not gl.path.exists()) and now >= next_look:
            next_look = now + 30                          # the game may not have been launched yet
            p = pathlib.Path(cfg["game_log_path"]) if cfg.get("game_log_path") else default_log_path()
            if p and p.exists():
                gl = GameLog(p)
        try:
            events = gl.poll()
        except Exception as e:
            print(f"  [gamelog] {type(e).__name__}: {e}")
            events = []
        want = state.get("log_path_request")
        if want:
            state.set(log_path_request=None)
            gl = GameLog(want)
            events = gl.poll()
        g = gl.current()
        if g and g.phase in ("loading", "playing") and is_stale(gl, now):
            g = None
        pid = int(cfg.get("aoe4world_profile_id") or 0) or gl.my_id
        me = g.me(pid, gl.my_name) if g else None
        changed = bool(events) or state.get("log_game") is not g
        for ev in events:
            if ev.kind == "player_out" and ev.game is g and me and ev.data["player"] != me.name:
                side = "Ally" if ev.data["team"] == me.team else "Enemy"
                from aoe4world import civ_name
                text = f"{side} {ev.data['player']} ({civ_name(ev.data['civ'])}) is out"
                if g and not g.ended_at:
                    mine = len([p for p in g.players if p.team == me.team and not p.out])
                    theirs = len([p for p in g.players if p.team != me.team and not p.out])
                    text += f": {mine}v{theirs} now"
                alerts.append((now, text))
                print(f"  [gamelog] {text}")
            elif ev.kind == "clock_start" and g and ev.game is g:
                state.set(log_new_game=g.session_id or g.loading_at)
            elif ev.kind == "results" and ev.game:
                res = _result(ev.game, ev.game.me(pid, gl.my_name))
                state.set(log_result=res)
                print(f"  [gamelog] result: {res.get('result')}")
            elif ev.kind == "left":
                state.set(log_left=ev.t)
            elif ev.kind in ("game_start", "players") and g:
                print(f"  [gamelog] {ev.kind}: {ev.data}")
        alerts = [(t, a) for t, a in alerts if now - t < 45]
        if changed:
            state.set(log_game=g, log_me=me, log_match=to_match(g, me) if g and me else None,
                      log_status=_status(gl, g))
            sig = (g.session_id if g else None, me.civ if me else None, tuple(p.civ for p in g.players) if g else ())
            if g and me and sig != intel_for:
                intel_for = sig
                try:
                    from units import UnitDB
                    db = db or UnitDB()
                    state.set(intel=enemy_intel(g, me, db))
                except Exception as e:
                    print(f"  [gamelog] intel: {type(e).__name__}: {e}")
            elif not g:
                intel_for = None
                state.set(intel=None)
            wake = state.get("log_wake")
            if wake:
                wake.set()
        state.set(log_alerts=[a for _, a in alerts])
        stop.wait(every)


def _status(gl: GameLog, g: Optional[LogGame]) -> dict:
    found = bool(gl.path and gl.path.exists())
    return {"found": found, "path": str(gl.path) if gl.path else None, "my_name": gl.my_name, "my_id": gl.my_id,
            "phase": g.phase if g else "menu", "kind": g.kind if g else None, "map": g.map if g else None}


def _result(g: LogGame, me: Optional[LogPlayer]) -> dict:
    gt = None
    rows = []
    for p in sorted(g.players, key=lambda p: (p.team, p.slot)):
        s = p.stats
        gt = gt or s.get("gt")
        mins = max((s.get("gt") or 0) / 60, 1)
        rows.append({"name": p.name, "civ": p.civ, "team": p.team, "result": p.result, "me": p is me,
                     "profile_id": p.profile_id, "units": s.get("sqprod"), "buildings": s.get("bprod"),
                     "kills": s.get("sqkill"), "lost": s.get("sqlost"), "upgrades": s.get("upg"),
                     "apm": round((s.get("totalcmds") or 0) / mins) if s else None})
    return {"session": g.session_id, "result": me.result if me else None, "kind": g.kind, "map": g.map,
            "seconds": gt or (int(g.ended_at - g.started_at) if g.ended_at and g.started_at else None), "players": rows}


def read_all(path) -> GameLog:
    gl = GameLog(path)
    gl.poll()
    return gl


def describe(g: LogGame, my_id=None, my_name=None) -> List[str]:
    me = g.me(my_id, my_name)
    my_team = me.team if me else None
    lines = [f"{g.kind} on {g.map or '?'} · phase {g.phase} · session {g.session_id}"]
    for p in sorted(g.players, key=lambda p: (p.team, p.slot)):
        side = "you" if p is me else ("ally" if my_team is not None and p.team == my_team else "enemy")
        extra = f" OUT ({p.out})" if p.out else ""
        extra += f" → {p.result}" if p.result else ""
        lines.append(f"  team {p.team} {side:5} {p.name} [{p.profile_id or 'AI'}] {p.civ}{extra}")
    if g.started_at and g.ended_at:
        lines.append(f"  lasted {int(g.ended_at - g.started_at) // 60}:{int(g.ended_at - g.started_at) % 60:02d}")
    return lines


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else default_log_path()
    gl = GameLog(path)
    games = []
    orig = gl._finish

    def keep(t, out):
        games.append(gl.game)
        orig(t, out)
    gl._finish = keep
    for ev in gl.poll():
        if ev.kind != "perf":
            print(_dt.datetime.fromtimestamp(ev.t).strftime("%H:%M:%S"), ev.kind, ev.data if ev.kind != "results" else "")
    if gl.game and gl.game not in games:
        games.append(gl.game)
    print(f"\nme: {gl.my_name} ({gl.my_id})")
    for g in games:
        print("\n".join(describe(g, gl.my_id, gl.my_name)))
