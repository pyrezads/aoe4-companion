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
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

USER_AGENT = "AOE4-Live-Analyst/1.0 (personal coaching tool)"
RESOURCES = ("food", "wood", "gold", "stone")


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
        name = re.sub(r"-\d+$", "", name).replace("-", " ").replace("_", " ")
        return {"1": "Dark Age", "2": "Feudal", "3": "Castle", "4": "Imperial"}.get(name, name)
    s = re.sub(r"<img[^>]*>", icon, s or "")
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
                                     builders=_count(str(st.get("builders") or ""))))
        for k, v in (data.get("ageTimings") or {}).items():
            if isinstance(v, dict) and v.get("t"):
                bo.age_times[k] = v["t"]
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
    """Fetches and caches the builds per civ for this session (nothing written to disk)."""

    def __init__(self, api_base: str, timeout: float = 10):
        self.api = api_base.rstrip("/")
        self.timeout = timeout
        self.cache: Dict[str, List[dict]] = {}

    def _get(self, code: str, order: str) -> List[dict]:
        req = urllib.request.Request(f"{self.api}/builds?civ={code}&orderBy={order}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, list) else (data.get("builds") or data.get("data") or [])

    def builds_for(self, civ_slug: str) -> List[dict]:
        code = GUIDES_CIV.get(civ_slug)
        if not code:
            return []
        if code not in self.cache:
            seen, merged = set(), []
            for order in ("score", "views", "likes", "timeCreated"):
                try:
                    for b in self._get(code, order):
                        key = b.get("id") or b.get("title")
                        if key not in seen:
                            seen.add(key)
                            merged.append(b)
                except Exception as e:
                    print(f"  [builds] {code} {order}: {e}")
            if not merged:
                return []          # don't remember a failure; try again next time
            self.cache[code] = merged
        return self.cache[code]

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
