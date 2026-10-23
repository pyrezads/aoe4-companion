"""
Match history from the official Age of Empires service (World's Edge, the game's own
backend; the same data the official stats page shows). No third-party sites.

    matches(profile_id)        recent games: map, mode, length, result, every player's civ,
                               team, rating change and in-game stats (units, kills, buildings,
                               upgrades, actions per minute), plus the official replay link
    personal_stats(profile_id) current rating, rank, wins and losses per leaderboard
    profile_for_steam(steam)   find a player's profile from their Steam account

Numbers the service uses for civs, modes and results are decoded with
data/official_ids.json (see tools/probe_official2.py).
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request
import zlib
from collections import defaultdict
from typing import Dict, List, Optional

API = "https://aoe-api.worldsedgelink.com/community/leaderboard"
UA = {"User-Agent": "AoE4-Coach/0.1 (personal coaching tool)", "Accept": "application/json"}
IDS = json.loads((pathlib.Path(__file__).with_name("data") / "official_ids.json").read_text(encoding="utf-8"))
MAP_NAMES = {"rolling_rivers": "Danube River", "king_of_hill": "King of the Hill"}   # when localizedMapName is empty
STAT_KEYS = {"unitprod": "units made", "sqkill": "kills", "sqlost": "units lost", "bprod": "buildings",
             "blost": "buildings lost", "upg": "upgrades", "abil": "abilities used", "pcap": "sites captured",
             "inactperiod": "idle periods", "totalcmds": "commands"}


def _get(url: str, timeout: float = 25) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _unpack(s: Optional[str]) -> dict:
    """'options' are base64 + zlib JSON."""
    if not s:
        return {}
    try:
        raw = base64.b64decode(s)
        for wbits in (15, -15, 31):
            try:
                raw = zlib.decompress(raw, wbits)
                break
            except zlib.error:
                continue
        text = raw.decode("utf-8", "replace")
        start = text.find("{")
        if start < 0:
            return {}
        obj, _ = json.JSONDecoder().raw_decode(text[start:])   # bytes may follow the JSON
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def civ_name(cid) -> str:
    return IDS["civilization_id"].get(str(cid), f"civ_{cid}")


def mode_name(mid) -> str:
    return IDS["matchtype_id"].get(str(mid), "other")


def map_name(m: dict, options: dict) -> str:
    loc = (options.get("localizedMapName") or "").strip()
    if loc:
        return loc
    key = options.get("mapName") or m.get("mapname") or ""
    if key in MAP_NAMES:
        return MAP_NAMES[key]
    return key.replace("_", " ").title() if key and key != "generated_map" else "Unknown map"


def parse_match(m: dict, me: int, profiles: Dict[int, dict]) -> dict:
    """One official match in the shape the app shows."""
    opts = _unpack(m.get("options"))
    members = {x["profile_id"]: x for x in m.get("matchhistorymember") or []}
    urls = {u.get("profile_id"): u.get("url") for u in m.get("matchurls") or [] if u.get("datatype", 0) == 0}
    dur = max(0, (m.get("completiontime") or 0) - (m.get("startgametime") or 0))
    players = []
    for r in m.get("matchhistoryreportresults") or []:
        pid = r["profile_id"]
        mem = members.get(pid, {})
        c = r.get("counters")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except ValueError:
                c = {}
        c = c or {}
        gt = c.get("gt") or dur or 1
        old, new = mem.get("oldrating"), mem.get("newrating")
        prof = profiles.get(pid, {})
        players.append({
            "profile_id": pid, "name": prof.get("alias") or f"Player {pid}", "country": prof.get("country"),
            "civ": civ_name(r.get("civilization_id")), "team": r.get("teamid"),
            "result": IDS["resulttype"].get(str(r.get("resulttype")), "unknown"),
            "rating": new, "rating_diff": (new - old) if isinstance(new, int) and isinstance(old, int) and old > 0 else None,
            "stats": {k: c.get(k) for k in STAT_KEYS if c.get(k) is not None},
            "apm": round(c.get("totalcmds", 0) / (gt / 60)) if c.get("totalcmds") else None,
        })
    mine = next((p for p in players if p["profile_id"] == me), None)
    return {
        "id": m["id"], "start": m.get("startgametime"), "end": m.get("completiontime"), "duration": dur,
        "map": map_name(m, opts), "mode": mode_name(m.get("matchtype_id")), "matchtype_id": m.get("matchtype_id"),
        "automatch": (m.get("description") == "AUTOMATCH"),
        "result": mine["result"] if mine else "unknown",
        "civ": mine["civ"] if mine else None, "team": mine["team"] if mine else None,
        "rating": mine["rating"] if mine else None, "rating_diff": mine["rating_diff"] if mine else None,
        "allies": [p["civ"] for p in players if mine and p["team"] == mine["team"] and p is not mine],
        "enemies": [p["civ"] for p in players if mine and p["team"] != mine["team"]],
        "players": players,
        "replay": urls.get(me) or next(iter(urls.values()), None),
    }


def matches(profile_id: int) -> dict:
    data = _get(f"{API}/getRecentMatchHistory?title=age4&profile_ids={urllib.parse.quote(json.dumps([profile_id]))}")
    profiles = {p["profile_id"]: p for p in data.get("profiles") or []}
    out = [parse_match(m, profile_id, profiles) for m in data.get("matchHistoryStats") or []]
    out.sort(key=lambda x: -(x["start"] or 0))
    me = profiles.get(profile_id, {})
    return {"profile": {"profile_id": profile_id, "name": me.get("alias"), "country": me.get("country"),
                        "level": me.get("level"), "steam": (me.get("name") or "").replace("/steam/", "") or None},
            "matches": out}


def personal_stats(profile_id: int, recent: Optional[List[dict]] = None) -> List[dict]:
    """Rating, rank, wins and losses per leaderboard. Leaderboards are named by matching
    their rating with your latest game in each mode."""
    data = _get(f"{API}/GetPersonalStat?title=age4&profile_ids={urllib.parse.quote(json.dumps([profile_id]))}")
    latest = {}
    for m in recent or []:
        if m.get("rating") and m["mode"] not in latest:
            latest[m["mode"]] = m["rating"]
    rows = []
    for s in data.get("leaderboardStats") or []:
        lb = str(s.get("leaderboard_id"))
        mode = next((k for k, v in latest.items() if v == s.get("rating")), None) or IDS.get("leaderboard_id", {}).get(lb)
        rows.append({"leaderboard_id": s.get("leaderboard_id"), "mode": mode or f"leaderboard {lb}",
                     "rating": s.get("rating"), "highest": s.get("highestrating"), "wins": s.get("wins"),
                     "losses": s.get("losses"), "streak": s.get("streak"), "rank": s.get("rank"),
                     "rank_total": s.get("ranktotal"), "region_rank": s.get("regionrank"),
                     "last_played": s.get("lastmatchdate")})
    rows.sort(key=lambda r: -(r["last_played"] or 0))
    return rows


def steam_id_from(text: str) -> Optional[str]:
    """A Steam profile link or SteamID64 -> SteamID64. Custom /id/ links can't be resolved without
    a Steam key, so those ask for the numeric link instead."""
    m = re.search(r"(7656119\d{10})", text or "")
    return m.group(1) if m else None


def profile_for_steam(steam_id: str) -> Optional[dict]:
    data = _get(f"{API}/GetPersonalStat?title=age4&profile_names={urllib.parse.quote(json.dumps(['/steam/' + steam_id]))}")
    for g in data.get("statGroups") or []:
        for mem in g.get("members") or []:
            if mem.get("name") == "/steam/" + steam_id:
                return {"profile_id": mem.get("profile_id"), "name": mem.get("alias"), "country": mem.get("country"),
                        "level": mem.get("level")}
    return None


# ── summaries for the page ────────────────────────────────────────────

def insights(ms: List[dict]) -> dict:
    """Win rates by civ, map, mode and opponent civ; rating over time per mode."""
    def rate(groups):
        rows = []
        for k, (w, n) in groups.items():
            rows.append({"key": k, "wins": w, "games": n, "win_rate": round(100 * w / n) if n else 0})
        return sorted(rows, key=lambda r: (-r["games"], -r["win_rate"]))
    by_civ, by_map, by_mode, vs = (defaultdict(lambda: [0, 0]) for _ in range(4))
    durations, apms = [], []
    for m in ms:
        if m["result"] not in ("win", "loss"):
            continue
        won = m["result"] == "win"
        for d, k in ((by_civ, m["civ"]), (by_map, m["map"]), (by_mode, m["mode"])):
            d[k][0] += won
            d[k][1] += 1
        for e in set(m["enemies"]):
            vs[e][0] += won
            vs[e][1] += 1
        durations.append(m["duration"])
        mine = next((p for p in m["players"] if p["civ"] == m["civ"] and p["team"] == m["team"]), None)
        if mine and mine.get("apm"):
            apms.append(mine["apm"])
    rating = defaultdict(list)
    for m in sorted(ms, key=lambda x: x["end"] or 0):
        if m.get("rating") and m["mode"] != "other":
            rating[m["mode"]].append([m["end"], m["rating"]])
    played = [m for m in ms if m["result"] in ("win", "loss")]
    wins = sum(m["result"] == "win" for m in played)
    streak, kind = 0, None
    for m in played:                               # newest first
        if kind is None:
            kind = m["result"]
        if m["result"] != kind:
            break
        streak += 1
    return {"games": len(played), "wins": wins, "win_rate": round(100 * wins / len(played)) if played else None,
            "streak": {"result": kind, "count": streak} if kind else None,
            "avg_minutes": round(sum(durations) / len(durations) / 60, 1) if durations else None,
            "avg_apm": round(sum(apms) / len(apms)) if apms else None,
            "by_civ": rate(by_civ), "by_map": rate(by_map), "by_mode": rate(by_mode), "vs_civ": rate(vs),
            "rating": dict(rating)}


def slim(m: dict) -> dict:
    """A match without per-player stats, for the list."""
    return {k: v for k, v in m.items() if k not in ("players",)} | {
        "players": [{k: p[k] for k in ("name", "civ", "team", "result", "rating", "rating_diff", "profile_id")} for p in m["players"]]}


class History:
    """Fetches and caches a player's history (cache_dir/history_<id>.json), refreshing
    at most every couple of minutes; the cache keeps it working offline."""

    def __init__(self, cache_dir: Optional[str], refresh_after: float = 120):
        self.dir = pathlib.Path(cache_dir) if cache_dir else None
        self.refresh_after = refresh_after
        self.mem: Dict[int, dict] = {}

    def _file(self, pid):
        return self.dir / f"history_{pid}.json" if self.dir else None

    def load(self, profile_id: int, force: bool = False) -> dict:
        now = time.time()
        cached = self.mem.get(profile_id)
        f = self._file(profile_id)
        if cached is None and f and f.exists():
            try:
                cached = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                cached = None
        if cached and not force and now - cached.get("fetched_at", 0) < self.refresh_after:
            self.mem[profile_id] = cached
            return {**cached, "offline": False}
        try:
            got = matches(profile_id)
            try:
                got["leaderboards"] = personal_stats(profile_id, got["matches"])
            except Exception as e:                     # history is still useful without it
                print(f"  [history] leaderboards: {e}")
                got["leaderboards"] = (cached or {}).get("leaderboards", [])
            got["fetched_at"] = now
            self.mem[profile_id] = got
            if f:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(got), encoding="utf-8")
            return {**got, "offline": False}
        except Exception as e:
            print(f"  [history] couldn't reach the official service: {e}")
            if cached:
                self.mem[profile_id] = cached
                return {**cached, "offline": True}
            raise

    def match(self, profile_id: int, match_id) -> Optional[dict]:
        data = self.mem.get(profile_id) or self.load(profile_id)
        return next((m for m in data.get("matches", []) if str(m["id"]) == str(match_id)), None)


def save_replay(url: str, match_id, folder: Optional[str] = None) -> str:
    """Downloads the official replay into the game's playback folder (as the official guide
    describes), named AgeIV_Replay_<match id>, and returns the path."""
    import gzip
    import os
    if not re.match(r"https://[\w.-]+\.blob\.core\.windows\.net/", url or ""):
        raise ValueError("not an official replay link")
    folder = folder or os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Age of Empires IV", "playback")
    os.makedirs(folder, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        raw = r.read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass                                           # already unpacked
    path = os.path.join(folder, f"AgeIV_Replay_{int(match_id)}")
    with open(path, "wb") as fh:
        fh.write(raw)
    return path
