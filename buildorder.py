"""
Follow an aoe4guides.com build order against what the HUD shows.

A build step looks like: time "2:05", food "10", wood "+", gold "2", and an
HTML description with icons. We turn icons into words, find the step for the
current game time, and compare its villager targets with the live split.
"""

from __future__ import annotations

import html
import json
import math
import pathlib
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

USER_AGENT = "AOE4-Live-Analyst/1.0 (personal coaching tool)"
RESOURCES = ("food", "wood", "gold", "stone")


AGE_KEYS = {"feudal": 2, "castle": 3, "imperial": 4}


def build_id_from(url_or_id: str) -> str:
    m = re.search(r"/builds/([A-Za-z0-9]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def _clock(t: str) -> Optional[int]:
    m = re.match(r"\s*~?(\d+):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _count(v: str) -> Optional[int]:
    """'6' -> 6, '5+' -> 5, '+' / '' -> None (no target)."""
    m = re.match(r"\s*(\d+)", v or "")
    return int(m.group(1)) if m else None


# icon file names that read badly aloud
ICON_WORDS = {"berrybush": "berries", "rally": "rally point", "food": "food", "gold": "gold", "wood": "wood",
              "stone": "stone", "olive oil": "olive oil", "time": "time", "pop": "population"}

CIV_WORDS = {"abbasid", "ayyubid", "ayyubids", "byzantine", "byzantines", "chinese", "delhi", "english", "french",
             "hre", "japanese", "jin", "jeanne", "lancaster", "macedonian", "malian", "malians", "mongol", "mongols",
             "dragon", "ottoman", "ottomans", "rus", "sengoku", "templar", "tughlaq", "zhuxi", "zxl", "vikings", "scots"}


def icons_in(s: str) -> List[dict]:
    """The in-game icons a step shows, as full image links with a name."""
    out = []
    for tag in re.findall(r"<img[^>]*>", s or ""):
        src = re.search(r'src="([^"]+)"', tag)
        if not src:
            continue
        url = src.group(1)
        if url.startswith("/"):
            url = "https://aoe4guides.com" + url
        if not url.startswith("https://"):
            continue
        out.append({"src": url, "title": html_to_text(tag)})
    return out


def rich_text(s: str) -> List[dict]:
    """A step's text as pieces: {"t": "text"} or {"icon": url, "title": name}, for showing icons inline."""
    parts = []
    for piece in re.split(r"(<img[^>]*>|<br\s*/?>)", s or ""):
        if not piece:
            continue
        if piece.startswith("<img"):
            ic = icons_in(piece)
            if ic:
                parts.append({"icon": ic[0]["src"], "title": ic[0]["title"]})
        elif re.match(r"<br", piece):
            parts.append({"br": True})
        else:
            t = html.unescape(re.sub(r"<[^>]+>", "", piece))
            if t.strip():
                parts.append({"t": re.sub(r"\s+", " ", t)})
    return parts


def html_to_text(s: str) -> str:
    """Replace icons with their name (from title or file name), drop the rest of the HTML."""
    def icon(m):
        tag = m.group(0)
        t = re.search(r'title="([^"]+)"', tag)
        if t:
            return t.group(1)
        src = re.search(r'src="[^"]*/([a-z0-9_\-]+)\.\w+"', tag)
        if not src:
            return ""
        name = re.sub(r"^(resource_|age_)", "", src.group(1))
        name = re.sub(r"-\d+$", "", name)
        # "villager-abbasid" -> "villager": drop a civ name tacked on the end
        parts = re.split(r"[-_]", name)
        if len(parts) > 1 and parts[-1] in CIV_WORDS:
            parts = parts[:-1]
        name = " ".join(parts)
        name = ICON_WORDS.get(name, name)
        return {"1": "Dark Age", "2": "Feudal", "3": "Castle", "4": "Imperial"}.get(name, name)
    s = re.sub(r"<img[^>]*>", lambda m: " " + icon(m) + " ", s or "")   # "5<img>" -> "5 villager"
    s = re.sub(r"<br\s*/?>", " · ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip(" ·")


@dataclass
class Step:
    seconds: Optional[int]
    age: int
    targets: Dict[str, Optional[int]]
    text: str
    villagers: Optional[int] = None    # total villagers at this step, when the build gives it
    builders: Optional[int] = None     # villagers building (not counted on any resource)
    blank: Dict[str, bool] = field(default_factory=dict)   # resource left empty (usually 0), not "+"


@dataclass
class BuildOrder:
    title: str
    civ: str
    steps: List[Step] = field(default_factory=list)
    gameplans: List[str] = field(default_factory=list)
    age_times: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, url_or_id: str, timeout: float = 10) -> "BuildOrder":
        bid = build_id_from(url_or_id)
        req = urllib.request.Request(f"https://aoe4guides.com/api/builds/{bid}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return cls.from_json(data)

    @classmethod
    def from_json(cls, data: dict) -> "BuildOrder":
        bo = cls(title=data.get("title", "Build"), civ=data.get("civ", "?"))
        for section in data.get("steps", []):
            age = section.get("age", 1)
            for st in section.get("steps", []):
                if st.get("kind") == "alternatives":
                    continue       # branches: we follow the main line
                if st.get("gameplan"):
                    bo.gameplans.append(html_to_text(st["gameplan"]))
                if not st.get("description") and not st.get("time"):
                    continue
                bo.steps.append(Step(seconds=_clock(st.get("time", "")), age=age,
                                     targets={r: _count(st.get(r, "")) for r in RESOURCES},
                                     text=html_to_text(st.get("description", "")),
                                     villagers=_count(str(st.get("villagers") or "")),
                                     builders=_count(str(st.get("builders") or "")),
                                     blank={r: not str(st.get(r) or "").strip() for r in RESOURCES}))
        for k, v in (data.get("ageTimings") or {}).items():
            if isinstance(v, dict) and v.get("t"):
                bo.age_times[k] = v["t"]
        # No age-up times given: use the step that shows an age-up icon (the click),
        # else the time of each age's first step.
        if not bo.age_times:
            for sec in data.get("steps", []):
                for st in sec.get("steps", []):
                    m = re.search(r'/age/age_([234])\.\w+"', st.get("description") or "")
                    t = _clock(st.get("time", ""))
                    if m and t is not None and m.group(1) not in bo.age_times:
                        bo.age_times[m.group(1)] = t
        have = {AGE_KEYS.get(str(k).lower(), int(k) if str(k).isdigit() else None) for k in bo.age_times}
        for s in bo.steps:
            if s.age > 1 and s.age not in have and s.seconds is not None:
                bo.age_times[str(s.age)] = s.seconds
                have.add(s.age)
        return bo

    # ── live comparison ───────────────────────────────────────────────
    def current_index(self, seconds: int) -> Optional[int]:
        idx = None
        for i, s in enumerate(self.steps):
            if s.seconds is not None and s.seconds <= seconds:
                idx = i
        return idx

    def compare(self, seconds: int, villagers: Dict[str, Optional[int]]) -> dict:
        """Where you are in the build and how your villager split differs from it."""
        i = self.current_index(seconds)
        if i is None:
            nxt = self.steps[0] if self.steps else None
            return {"step": None, "next": nxt, "gaps": {}, "summary": "Build starts soon."}
        step = self.steps[i]
        nxt = self.steps[i + 1] if i + 1 < len(self.steps) else None
        gaps = {}
        for r, want in step.targets.items():
            have = villagers.get(r)
            if want is not None and have is not None and want != have:
                gaps[r] = want - have      # positive = you need more on r
        parts = [f"+{g} {r}" for r, g in gaps.items() if g > 0] + [f"{-g} extra on {r}" for r, g in gaps.items() if g < 0]
        summary = ("On build." if not gaps else "Vs build: " + ", ".join(parts))
        return {"step": step, "next": nxt, "gaps": gaps, "summary": summary}


# ── everything aoe4guides has on a build, for the details view ─────────

def _clock_str(sec) -> Optional[str]:
    if sec is None:
        return None
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return str(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _date(v) -> Optional[str]:
    """aoe4guides dates: Firestore {"seconds": ...}, epoch numbers or ISO strings -> YYYY-MM-DD."""
    import datetime as _dt
    if isinstance(v, dict):
        v = v.get("seconds") or v.get("_seconds")
    try:
        if isinstance(v, (int, float)):
            return _dt.datetime.utcfromtimestamp(v / 1000 if v > 1e11 else v).strftime("%Y-%m-%d")
        if isinstance(v, str) and v:
            return v[:10]
    except (ValueError, OSError, OverflowError):
        return None
    return None


def build_detail(b: dict) -> dict:
    """The build in full: who made it, their notes, video, votes, age-up times and every step."""
    sections, gameplans, alternatives = [], [], 0
    for sec in b.get("steps") or []:
        rows = []
        for st in sec.get("steps") or []:
            if st.get("kind") == "alternatives":
                alternatives += 1
                continue
            if st.get("gameplan"):
                gameplans.append({"age": sec.get("age"), "text": html_to_text(st["gameplan"]), "parts": rich_text(st["gameplan"])})
            if not st.get("description") and not st.get("time"):
                continue
            rows.append({"time": st.get("time") or "", "villagers": _count(str(st.get("villagers") or "")),
                         "builders": _count(str(st.get("builders") or "")),
                         "split": {r: (str(st.get(r)).strip() if st.get(r) not in (None, "") else "") for r in RESOURCES},
                         "text": html_to_text(st.get("description") or ""), "parts": rich_text(st.get("description") or "")})
        sections.append({"age": sec.get("age"), "steps": rows})
    ages = {}
    for k, v in (b.get("ageTimings") or {}).items():
        if isinstance(v, dict) and v.get("t"):
            ages[str(k)] = _clock_str(v["t"])
    desc = b.get("description") or ""
    bid = b.get("id")
    video = b.get("video") or None
    if video:
        m = re.search(r"(?:embed/|watch\?v=|youtu\.be/)([\w-]{6,})", str(video))
        video = f"https://www.youtube.com/watch?v={m.group(1)}" if m else (
            f"https://www.youtube.com/watch?v={video}" if not str(video).startswith("http") else str(video))
    comments = b.get("comments")
    return {
        "id": bid, "title": b.get("title"), "civ": b.get("civ"),
        "author": b.get("creatorName") or b.get("author"),
        "url": f"https://aoe4guides.com/builds/{bid}" if bid else None,
        "video": video,
        "description": html_to_text(desc) if "<" in desc else desc.strip(),
        "season": b.get("season"), "strategy": b.get("strategy") if b.get("strategy") not in (None, "-") else None,
        "map": b.get("map") if b.get("map") not in (None, "-") else None,
        "views": b.get("views"), "likes": b.get("likes"), "upvotes": b.get("upvotes"), "downvotes": b.get("downvotes"),
        "score": b.get("score"), "comments": len(comments) if isinstance(comments, list) else comments,
        "created": _date(b.get("timeCreated")), "updated": _date(b.get("timeUpdated")),
        "remix_of": b.get("remixOf") or None,
        "ages": ages, "gameplans": gameplans, "alternatives": alternatives, "sections": sections,
    }


# ── automatic build choice ─────────────────────────────────────────────

GUIDES_CIV = {
    "abbasid_dynasty": "ABB", "ayyubids": "AYY", "byzantines": "BYZ", "chinese": "CHI", "delhi_sultanate": "DEL",
    "english": "ENG", "french": "FRE", "golden_horde": "GOH", "holy_roman_empire": "HRE", "house_of_lancaster": "HOL",
    "japanese": "JAP", "jin_dynasty": "JIN", "jeanne_darc": "JDA", "knights_templar": "KTE",
    "macedonian_dynasty": "MAC", "malians": "MAL", "mongols": "MON", "order_of_the_dragon": "DRA",
    "ottomans": "OTT", "rus": "RUS", "scots": "SCO", "sengoku_daimyo": "SEN", "tughlaq_dynasty": "TUG",
    "vikings": "VIK", "zhu_xis_legacy": "ZXL",
}


AGGRESSIVE = {"mongols", "french", "english", "rus", "jeanne_darc", "malians", "ayyubids", "golden_horde",
              "order_of_the_dragon", "knights_templar"}
DEFENSIVE = {"holy_roman_empire", "chinese", "byzantines", "delhi_sultanate", "zhu_xis_legacy", "abbasid_dynasty",
             "house_of_lancaster", "macedonian_dynasty", "tughlaq_dynasty"}
# Rough map types for common ranked maps; unknown maps count as neutral.
MAP_TYPES = {
    "dry arabia": "Open", "hill and dale": "Open", "king of the hill": "Open", "altai": "Open", "lipany": "Open",
    "mongolian heights": "Open", "carmel": "Open", "high view": "Open", "craters": "Open", "four lakes": "Open",
    "gorge": "Closed", "highwoods": "Closed", "hideout": "Closed", "black forest": "Closed", "forest ponds": "Closed",
    "mountain pass": "Closed", "the pit": "Closed",
    "archipelago": "Water", "warring islands": "Water", "mediterranean": "Water", "boulder bay": "Water",
    "rocky river": "Hybrid", "himeyama": "Hybrid", "flankwoods": "Hybrid", "nagari": "Hybrid", "danube river": "Hybrid",
}
STRAT_WORDS = [("Fast Castle", r"\bfc\b|fast castle|castle rush"), ("Boom", r"boom|2 ?tc|3 ?tc|eco\b|economy"),
               ("Rush", r"rush|all[- ]?in|aggress|feudal (push|pressure|fight)|tower rush|proscout|pro scout"),
               ("Timing Attack", r"timing"), ("Fast Imperial", r"fast imp|\bfi\b")]


def _strategy(b: dict) -> str:
    s = b.get("strategy") or ""
    if s and s != "-":
        return s
    t = (b.get("title") or "").lower()
    for name, pat in STRAT_WORDS:
        if re.search(pat, t):
            return name
    return ""


ENGLISH = {"villager", "villagers", "vills", "vils", "build", "builds", "to", "on", "the", "and", "send", "sheep",
           "wood", "food", "gold", "stone", "house", "age", "up", "berries", "farm", "farms", "with", "next", "first",
           "scout", "mill", "camp", "get", "make", "go", "then", "into", "start", "keep", "for", "at", "of"}


def _english(b: dict) -> float:
    """Share of a build's step texts that read as English (icons left out)."""
    texts = [st.get("description") or "" for sec in b.get("steps") or [] for st in sec.get("steps") or []]
    texts = [re.sub(r"<[^>]+>", " ", html.unescape(t)) for t in texts]
    texts = [t for t in texts if re.search(r"[^\W\d_]{3,}", t)]
    if not texts:
        return 1.0
    hits = sum(1 for t in texts if set(re.findall(r"[a-z]+", t.lower())) & ENGLISH)
    return hits / len(texts)


def _season_num(b: dict) -> int:
    m = re.search(r"(\d+)", b.get("season") or "")
    return int(m.group(1)) if m else 0


@dataclass
class BuildChoice:
    build: dict
    score: float
    reasons: List[str]


def rank_builds(builds: List[dict], kind: Optional[str], map_name: Optional[str],
                enemy_civs: List[str]) -> List[BuildChoice]:
    """Order builds for this game: current patch, popular, and suited to the game size, map and enemies."""
    team_game = bool(kind) and not kind.endswith("1v1")
    map_type = MAP_TYPES.get((map_name or "").lower())
    aggro = [c for c in enemy_civs if c in AGGRESSIVE]
    defensive = [c for c in enemy_civs if c in DEFENSIVE]
    newest = max((_season_num(b) for b in builds), default=0)
    out = []
    for b in builds:
        if b.get("isDraft") or not b.get("steps"):
            continue
        strat = _strategy(b)
        title = (b.get("title") or "").lower()
        sc, why = 0.0, []
        if _english(b) < 0.4:                    # the coach reads steps aloud: prefer ones in English
            sc -= 6
        age = newest - _season_num(b)
        sc += 3.0 if age == 0 else 2.0 if age == 1 else 0.5 if age <= 3 else -1.5     # balance changes every season
        sc += min(float(b.get("score") or 0), 8) * 0.3 + math.log10(float(b.get("views") or 0) + 1) * 0.6
        if b.get("ageTimings"):
            sc += 0.5
        greedy = strat in ("Boom", "Fast Castle", "Fast Imperial")
        if team_game:
            if re.search(r"team|\btg\b|2v2|3v3|4v4", title):
                sc += 2; why.append("made for team games")
            if greedy:
                big = kind.endswith(("3v3", "4v4"))
                sc += 2.5 if big else 1.0
                why.append(("big team game" if big else "team game") + ": allies cover you, so greed pays")
            if kind.endswith(("3v3", "4v4")) and re.search(r"all[- ]?in|cheese", title):
                sc -= 2          # one player's all-in rarely ends a 4v4
        else:
            if re.search(r"1v1|ladder|ranked", title):
                sc += 0.5
        if map_type:
            if b.get("map") == map_type:
                sc += 1; why.append(f"tagged for {map_type.lower()} maps")
            elif b.get("map") in ("Open", "Closed") and b.get("map") != map_type:
                sc -= 2
            if map_type == "Closed" and greedy:
                sc += 2; why.append("closed map: safe to boom")
            if map_type == "Open" and strat in ("Rush", "Timing Attack") and not team_game:
                sc += 0.5; why.append("open map suits pressure")
        if aggro and not team_game:
            if strat in ("Fast Castle", "Fast Imperial"):
                sc -= 1.5
            if strat in ("Rush", "Timing Attack"):
                sc += 0.5; why.append("aggressive enemy: match their pressure")
        if defensive and not aggro and greedy:
            sc += 1; why.append("defensive enemies: greedy start is safe")
        if defensive and not aggro and re.search(r"against defensive", title):
            sc += 2; why.append("made against defensive civs")
        for c in enemy_civs:
            word = c.split("_")[0]
            if re.search(rf"\b(vs|against)\b.*{word}", title):
                sc += 2.5; why.append(f"written for {word.title()}")
        out.append(BuildChoice(b, sc, why or ([f"{strat.lower()} build, current patch"] if strat and age == 0 else [])))
    out.sort(key=lambda c: -c.score)
    return out


class BuildLibrary:
    """Fetches the builds per civ. Kept in memory, and on disk when cache_dir is set,
    so the picker still works offline and aoe4guides gets at most one request per civ
    every few hours."""

    CACHE_HOURS = 6

    def __init__(self, api_base: str, timeout: float = 10, cache_dir: Optional[str] = None):
        self.api = api_base.rstrip("/")
        self.timeout = timeout
        self.cache: Dict[str, List[dict]] = {}
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        self.last_source = None          # "memory", "network", "cache" (offline) or None

    def _get(self, code: str, order: str) -> List[dict]:
        req = urllib.request.Request(f"{self.api}/builds?civ={code}&orderBy={order}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, list) else (data.get("builds") or data.get("data") or [])

    def _disk(self, code: str) -> Optional[pathlib.Path]:
        return self.cache_dir / f"builds_{code}.json" if self.cache_dir else None

    def builds_for(self, civ_slug: str) -> List[dict]:
        code = GUIDES_CIV.get(civ_slug)
        if not code:
            return []
        if code in self.cache:
            self.last_source = "memory"
            return self.cache[code]
        disk = self._disk(code)
        if disk and disk.exists() and time.time() - disk.stat().st_mtime < self.CACHE_HOURS * 3600:
            try:
                self.cache[code] = json.loads(disk.read_text(encoding="utf-8"))
                self.last_source = "cache"
                return self.cache[code]
            except ValueError:
                pass
        seen, merged = set(), []
        if self.api:
            for order in ("score", "views", "likes", "timeCreated"):
                try:
                    for b in self._get(code, order):
                        key = b.get("id") or b.get("title")
                        if key not in seen:
                            seen.add(key)
                            merged.append(b)
                except Exception as e:
                    print(f"  [builds] {code} {order}: {e}")
        if merged:
            self.cache[code] = merged
            self.last_source = "network"
            if disk:
                try:
                    disk.parent.mkdir(parents=True, exist_ok=True)
                    disk.write_text(json.dumps(merged), encoding="utf-8")
                except OSError as e:
                    print(f"  [builds] couldn't save the cache: {e}")
            return merged
        if disk and disk.exists():               # offline: an old list beats nothing
            try:
                self.cache[code] = json.loads(disk.read_text(encoding="utf-8"))
                self.last_source = "cache"
                return self.cache[code]
            except ValueError:
                pass
        self.last_source = None
        return []                                # don't remember a failure; try again next time

    def find(self, build_id) -> Optional[dict]:
        """A build from any list fetched so far, by id (or title for builds without one)."""
        for builds in self.cache.values():
            for b in builds:
                if (b.get("id") or b.get("title")) == build_id:
                    return b
        return None

    def choose(self, civ_slug: str, kind: Optional[str], map_name: Optional[str],
               enemy_civs: List[str]) -> List[BuildChoice]:
        return rank_builds(self.builds_for(civ_slug), kind, map_name, enemy_civs)


def choice_state(choices: List[BuildChoice], i: int) -> dict:
    """State fields for showing build choice i (wraps around)."""
    i %= len(choices)
    c = choices[i]
    bo = BuildOrder.from_json(c.build)
    why = "; ".join(c.reasons[:2]) or "most popular current build"
    return {"build": bo, "build_note": None, "build_why": why, "build_pick": i, "build_compare": None,
            "build_count": min(len(choices), 5)}
