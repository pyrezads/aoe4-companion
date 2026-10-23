"""
aoe4world.com client: match detection and pre-game briefing.

Uses only documented public endpoints (aoe4world.com/api). Results are kept
in memory for the session; nothing is written to disk.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

BASE = "https://aoe4world.com/api/v0"
USER_AGENT = "AOE4-Live-Analyst/1.0 (personal coaching tool)"

# Ranked rating buckets accepted by the stats API (team and solo ranked).
RM_BUCKETS = [(0, 499, "<499"), (500, 699, "500-699"), (700, 999, "700-999"),
              (1000, 1199, "1000-1199"), (1200, 1399, "1200-1399")]
# Quick match uses different buckets.
QM_BUCKETS = [(0, 899, "<899"), (900, 999, "900-999"), (1000, 1099, "1000-1099"),
              (1100, 1199, "1100-1199"), (1200, 1299, "1200-1299"), (1300, 1399, "1300-1399")]

CIV_NAMES = {
    "abbasid_dynasty": "Abbasid", "ayyubids": "Ayyubids", "byzantines": "Byzantines", "chinese": "Chinese",
    "delhi_sultanate": "Delhi", "english": "English", "french": "French", "golden_horde": "Golden Horde",
    "holy_roman_empire": "HRE", "house_of_lancaster": "Lancaster", "japanese": "Japanese",
    "jeanne_darc": "Jeanne d'Arc", "jin_dynasty": "Jin", "knights_templar": "Templar",
    "macedonian_dynasty": "Macedonian", "malians": "Malians", "mongols": "Mongols",
    "order_of_the_dragon": "Dragon", "ottomans": "Ottomans", "rus": "Rus", "sengoku_daimyo": "Sengoku",
    "tughlaq_dynasty": "Tughlaq", "zhu_xis_legacy": "Zhu Xi", "vikings": "Vikings", "scots": "Scots",
}


def civ_name(slug: str) -> str:
    return CIV_NAMES.get(slug, (slug or "?").replace("_", " ").title())


def rating_bucket(rating: Optional[int], kind: str) -> Optional[str]:
    if not rating:
        return None
    buckets = QM_BUCKETS if kind.startswith("qm") else RM_BUCKETS
    for lo, hi, label in buckets:
        if lo <= rating <= hi:
            return label
    return ">1400" if not kind.startswith("qm") else ">1400"


@dataclass
class Player:
    name: str
    profile_id: Optional[int]
    civ: str
    rating: Optional[int]
    mmr: Optional[int]
    team: int
    modes: dict = field(default_factory=dict)      # the player's full aoe4world stats per mode
    input_type: Optional[str] = None
    country: Optional[str] = None


@dataclass
class Match:
    game_id: int
    kind: str                 # rm_1v1, rm_2v2, rm_4v4, qm_3v3 ...
    map: str
    map_id: Optional[int]
    ongoing: bool
    started_at: str
    players: List[Player]
    my_team: int

    def allies(self) -> List[Player]:
        return [p for p in self.players if p.team == self.my_team]

    def enemies(self) -> List[Player]:
        return [p for p in self.players if p.team != self.my_team]


@dataclass
class Briefing:
    match: Match
    my_civ: str
    my_rating: Optional[int]
    bucket: Optional[str]
    civ_winrates: Dict[str, float] = field(default_factory=dict)   # civ -> win % in this mode at my bucket
    map_civ_winrates: Dict[str, float] = field(default_factory=dict)
    map_median_minutes: Optional[float] = None
    my_civ_record: Optional[str] = None
    lines: List[str] = field(default_factory=list)


class Aoe4WorldClient:
    def __init__(self, profile_id: int, timeout: float = 8.0):
        self.profile_id = profile_id
        self.timeout = timeout
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self.profile: dict = {}

    # ── http ──────────────────────────────────────────────────────────
    def _get(self, path: str, params: Optional[dict] = None, ttl: float = 600) -> Optional[dict]:
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        now = time.time()
        with self._lock:
            hit = self._cache.get(url)
            if hit and now - hit[0] < ttl:
                return hit[1]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  [aoe4world] {path}: {e}")
            return hit[1] if hit else None
        with self._lock:
            self._cache[url] = (now, data)
        return data

    # ── profile ───────────────────────────────────────────────────────
    def load_profile(self) -> dict:
        self.profile = self._get(f"/players/{self.profile_id}", ttl=1800) or {}
        return self.profile

    def mode_stats(self, mode: str) -> dict:
        return (self.profile.get("modes") or {}).get(mode) or {}

    def my_rating_for(self, kind: str) -> Optional[int]:
        """Rating that matches the stats bucket for this game kind."""
        if kind.startswith("rm_"):
            team_size = kind.split("_")[1]                     # 1v1, 2v2, 3v3, 4v4
            m = self.mode_stats("rm_solo" if team_size == "1v1" else "rm_team")
            if m.get("rating"):
                return m["rating"]
            return self.mode_stats(f"rm_{team_size}_elo").get("rating")
        return self.mode_stats(kind).get("rating")

    def my_civ_record(self, civ: str) -> Optional[str]:
        for mode in ("rm_team", "rm_solo"):
            for c in self.mode_stats(mode).get("civilizations") or []:
                if c.get("civilization") == civ and c.get("games_count"):
                    return f"{c['games_count']} games, {c.get('win_rate', 0):.0f}% wins ({mode.replace('rm_', '')} ranked)"
        return None

    # ── match detection ───────────────────────────────────────────────
    def last_game(self) -> Optional[Match]:
        g = self._get(f"/players/{self.profile_id}/games/last", ttl=15)
        if not g or not g.get("teams"):
            return None
        players, my_team = [], 0
        for ti, team in enumerate(g["teams"]):
            for p in team:
                pl = Player(name=p.get("name", "?"), profile_id=p.get("profile_id"), civ=p.get("civilization", "?"),
                            rating=p.get("rating"), mmr=p.get("mmr"), team=ti, modes=p.get("modes") or {},
                            input_type=p.get("input_type"), country=p.get("country"))
                players.append(pl)
                if pl.profile_id == self.profile_id:
                    my_team = ti
        return Match(game_id=g.get("game_id"), kind=g.get("kind", "?"), map=g.get("map", "?"), map_id=g.get("map_id"),
                     ongoing=bool(g.get("ongoing")), started_at=g.get("started_at", ""), players=players, my_team=my_team)

    def current_match(self) -> Optional[Match]:
        m = self.last_game()
        return m if m and m.ongoing else None

    # ── stats ─────────────────────────────────────────────────────────
    def civ_winrates(self, kind: str, bucket: Optional[str]) -> Dict[str, float]:
        mode = "rm_solo" if kind == "rm_1v1" else kind
        data = self._get(f"/stats/{mode}/civilizations", {"rating": bucket}) or {}
        return {d["civilization"]: d["win_rate"] for d in data.get("data", []) if "civilization" in d}

    def map_civ_winrates(self, kind: str, map_id: Optional[int], bucket: Optional[str]) -> tuple:
        if not map_id:
            return {}, None
        mode = "rm_solo" if kind == "rm_1v1" else kind
        data = self._get(f"/stats/{mode}/maps/{map_id}", {"rating": bucket}) or {}
        rows = data.get("data") or data.get("civilizations") or []
        rates = {d["civilization"]: d["win_rate"] for d in rows if isinstance(d, dict) and "civilization" in d}
        med = data.get("duration_median")
        return rates, (med / 60 if med else None)

    def matchup_1v1(self, civ: str, other: str, bucket: Optional[str]) -> Optional[float]:
        data = self._get("/stats/rm_solo/matchups", {"rating": bucket}, ttl=3600) or {}
        for d in data.get("data", []):
            if d.get("civilization") == civ and d.get("other_civilization") == other:
                return d.get("win_rate")
        return None

    # ── briefing ──────────────────────────────────────────────────────
    def briefing(self, match: Match) -> Briefing:
        me = next((p for p in match.players if p.profile_id == self.profile_id), None)
        my_civ = me.civ if me else "?"
        rating = self.my_rating_for(match.kind) or (me.rating if me else None)
        bucket = rating_bucket(rating, match.kind)
        b = Briefing(match=match, my_civ=my_civ, my_rating=rating, bucket=bucket)
        b.civ_winrates = self.civ_winrates(match.kind, bucket)
        b.map_civ_winrates, b.map_median_minutes = self.map_civ_winrates(match.kind, match.map_id, bucket)
        b.my_civ_record = self.my_civ_record(my_civ)

        lines = [f"{match.kind.replace('_', ' ').upper()} on {match.map}" + (f" · your bracket {bucket}" if bucket else "")]
        if b.civ_winrates:
            ranked = sorted(b.civ_winrates.items(), key=lambda kv: -kv[1])
            pos = next((i + 1 for i, (c, _) in enumerate(ranked) if c == my_civ), None)
            if my_civ in b.civ_winrates:
                lines.append(f"{civ_name(my_civ)}: {b.civ_winrates[my_civ]:.1f}% in this mode ({pos} of {len(ranked)})")
            top = ", ".join(f"{civ_name(c)} {w:.0f}%" for c, w in ranked[:3])
            lines.append(f"Strongest here: {top}")
        if b.my_civ_record:
            lines.append(f"Your {civ_name(my_civ)}: {b.my_civ_record}")
        if b.map_civ_winrates and my_civ in b.map_civ_winrates:
            lines.append(f"{civ_name(my_civ)} on {match.map}: {b.map_civ_winrates[my_civ]:.1f}%")
        if b.map_median_minutes:
            lines.append(f"Games on this map usually last ~{b.map_median_minutes:.0f} min")
        enemies = match.enemies()
        if enemies:
            threat = []
            for p in enemies:
                w = b.civ_winrates.get(p.civ)
                threat.append(f"{civ_name(p.civ)}" + (f" ({w:.0f}%)" if w else "") + (f" {p.rating}" if p.rating else ""))
            lines.append("Enemies: " + ", ".join(threat))
        if match.kind == "rm_1v1" and enemies:
            wr = self.matchup_1v1(my_civ, enemies[0].civ, bucket)
            if wr is not None:
                lines.append(f"Matchup vs {civ_name(enemies[0].civ)}: {wr:.0f}% for you")
        b.lines = lines
        return b


def team_briefing(client: "Aoe4WorldClient", kind: str, my_civ: str, ally_civs: List[str], enemy_civs: List[str],
                  ranked_rating_mode: str = "rm_team") -> List[str]:
    """Briefing from civs read off the in-game player list (skirmish or any game)."""
    rating = client.my_rating_for(kind)
    bucket = rating_bucket(rating, kind)
    rates = client.civ_winrates(kind, bucket) if kind else {}
    size = kind.split("_")[1] if kind and "_" in kind else "?"
    lines = [f"{size.upper()} (from the in-game player list)" + (f" · stats at your bracket {bucket}" if bucket else "")]
    if rates and my_civ in rates:
        ranked = sorted(rates.items(), key=lambda kv: -kv[1])
        pos = next((i + 1 for i, (c, _) in enumerate(ranked) if c == my_civ), None)
        lines.append(f"{civ_name(my_civ)}: {rates[my_civ]:.1f}% in ranked {size} ({pos} of {len(ranked)})")
    rec = client.my_civ_record(my_civ)
    if rec:
        lines.append(f"Your {civ_name(my_civ)}: {rec}")

    def fmt(cs):
        return ", ".join(civ_name(c) + (f" {rates[c]:.0f}%" if c in rates else "") for c in cs)

    if ally_civs:
        lines.append("Allies: " + fmt(ally_civs))
    if enemy_civs:
        lines.append("Enemies: " + fmt(enemy_civs))
    if rates and ally_civs is not None and enemy_civs:
        mine = [rates[c] for c in [my_civ] + list(ally_civs) if c in rates]
        theirs = [rates[c] for c in enemy_civs if c in rates]
        if mine and theirs:
            d = sum(mine) / len(mine) - sum(theirs) / len(theirs)
            lines.append("Civ matchup: " + ("your team's civs are stronger on paper" if d > 1 else
                                            "their civs are stronger on paper" if d < -1 else "civs are even on paper")
                         + f" ({d:+.1f} pts)")
    return lines


# ── opponent scouting ──────────────────────────────────────────────────

RANK_NAMES = {"bronze": "Bronze", "silver": "Silver", "gold": "Gold", "platinum": "Plat", "diamond": "Diamond",
              "conqueror": "Conq"}


def _rank(level: Optional[str]) -> str:
    if not level or level == "unranked":
        return ""
    name, _, n = level.partition("_")
    return f"{RANK_NAMES.get(name, name.title())} {n}".strip()


def _mode_keys(kind: str) -> List[str]:
    """Which of a player's modes describe this game, best first."""
    if kind.startswith("rm_"):
        size = kind.split("_")[1]
        return (["rm_solo", "rm_1v1_elo"] if size == "1v1" else ["rm_team", f"rm_{size}_elo"])
    return [kind]


def _recent_form(mode: dict, n: int = 10):
    """(wins, losses, rating change) over the player's last n games in this mode."""
    hist = mode.get("rating_history") or {}
    if not isinstance(hist, dict) or len(hist) < 2:
        return None
    pts = [hist[k] for k in sorted(hist, key=lambda k: int(k))]
    pts = [p for p in pts if isinstance(p, dict) and p.get("games_count") is not None]
    if len(pts) < 2:
        return None
    end = pts[-1]
    start = next((p for p in pts if end["games_count"] - p["games_count"] <= n), pts[0])
    games = end["games_count"] - start["games_count"]
    if games <= 0:
        return None
    wins = (end.get("wins_count") or 0) - (start.get("wins_count") or 0)
    return wins, games - wins, (end.get("rating") or 0) - (start.get("rating") or 0)


@dataclass
class Scout:
    player: Player
    rating: Optional[int]
    threat: float
    line: str
    flags: List[str]


def scout_player(p: Player, kind: str, my_rating: Optional[int], h2h: Optional[tuple] = None) -> Scout:
    modes = p.modes or {}
    keys = [k for k in _mode_keys(kind) if modes.get(k)]
    m = modes.get(keys[0]) if keys else {}
    rating = m.get("rating") or p.rating
    parts = [f"{p.name} ({civ_name(p.civ)})"]
    head = []
    if rating:
        head.append(str(rating))
    rk = _rank(m.get("rank_level"))
    if rk:
        head.append(rk)
    if m.get("win_rate") is not None and m.get("games_count"):
        head.append(f"{m['win_rate']:.0f}% of {m['games_count']}")
    if head:
        parts.append(" · ".join(head))
    flags: List[str] = []

    # their record on the civ they picked
    civ_rows = []
    for k in keys:
        civ_rows += modes[k].get("civilizations") or []
    civ_row = next((c for c in civ_rows if c.get("civilization") == p.civ), None)
    civ_wr = None
    if civ_row and civ_row.get("games_count"):
        civ_wr = civ_row.get("win_rate")
        main = (civ_row.get("pick_rate") or 0) >= 40
        parts.append(f"{civ_name(p.civ)} {civ_row['games_count']} games, {civ_wr:.0f}%" + (" (main)" if main else ""))
        if main and civ_wr and civ_wr >= 55:
            flags.append("strong on this civ")
    elif civ_rows:
        flags.append("off-civ")

    form = _recent_form(m)
    if form:
        w, l, d = form
        parts.append(f"last {w + l}: {w}-{l} ({d:+d})")
        if d >= 60 or w - l >= 4:
            flags.append("hot streak")
        elif d <= -60 or l - w >= 4:
            flags.append("cold streak")
    prev = (m.get("previous_seasons") or [{}])[0] if m.get("previous_seasons") else {}
    if prev.get("rating") and rating and rating - prev["rating"] >= 150:
        flags.append(f"climbing fast (was {_rank(prev.get('rank_level')) or prev['rating']})")
    games = m.get("games_count") or 0
    if games >= 20 and (m.get("drops_count") or 0) / games >= 0.06:
        flags.append("drops games often")
    if p.input_type and p.input_type != "keyboard":
        flags.append("on controller")
    if h2h and h2h[0] + h2h[1] > 0:
        parts.append(f"vs you {h2h[0]}-{h2h[1]}")

    threat = (rating or 0) + (8 * ((civ_wr or 50) - 50)) + (30 if "hot streak" in flags else 0)
    line = " · ".join(parts) + (f"  [{', '.join(flags)}]" if flags else "")
    return Scout(p, rating, threat, line, flags)


def head_to_head(client: "Aoe4WorldClient", opponent_id: int) -> Optional[tuple]:
    """(your wins, your losses) in games you played against this player."""
    data = client._get(f"/players/{client.profile_id}/games", {"opponent_profile_id": opponent_id, "limit": 50}, ttl=3600)
    if not data:
        return None
    w = l = 0
    for g in data.get("games") or []:
        me = next((p for t in g.get("teams") or [] for p in t if p.get("profile_id") == client.profile_id), None)
        them = next((p for t in g.get("teams") or [] for p in t if p.get("profile_id") == opponent_id), None)
        if not me or not them or me.get("result") not in ("win", "loss"):
            continue
        same_team = any(me in t and them in t for t in g.get("teams") or [])
        if same_team:
            continue
        w += me["result"] == "win"
        l += me["result"] == "loss"
    return (w, l)


def scouting_report(client: "Aoe4WorldClient", match: Match) -> Dict[str, List[str]]:
    my_rating = client.my_rating_for(match.kind)
    enemies = []
    for p in match.enemies():
        h2h = head_to_head(client, p.profile_id) if p.profile_id else None
        enemies.append(scout_player(p, match.kind, my_rating, h2h))
    enemies.sort(key=lambda s: -s.threat)
    lines = []
    for i, s in enumerate(enemies):
        lines.append(("Biggest threat: " if i == 0 and len(enemies) > 1 else "") + s.line)
    allies = [scout_player(p, match.kind, my_rating) for p in match.allies() if p.profile_id != client.profile_id]
    ally_lines = [s.line for s in allies]
    team_avg = lambda ss: sum(s.rating or 0 for s in ss) / max(len([s for s in ss if s.rating]), 1)
    summary = None
    if enemies and (allies or my_rating):
        mine = [s.rating for s in allies if s.rating] + ([my_rating] if my_rating else [])
        if mine:
            d = sum(mine) / len(mine) - team_avg(enemies)
            summary = (f"Team rating: yours {sum(mine) / len(mine):.0f} vs theirs {team_avg(enemies):.0f} ({d:+.0f}) — "
                       + ("you're favoured" if d > 40 else "they're favoured" if d < -40 else "even match"))
    return {"enemies": lines, "allies": ally_lines, "summary": [summary] if summary else []}
