"""
Unit recognition (selection panel cards) and unit data lookup.

A card is 50x50 at x = 390 + 53*i, y = 1210 (2560x1440). The art is the same
as the official unit icons (data/unit_icons.npz). The count in the top-left
corner and the tier dots top-right are masked out when matching the figure;
the dots are then used to pick the tier (age 2/3/4 variant).
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

DATA = pathlib.Path(__file__).with_name("data")
CARD_X0, CARD_STEP, CARD_Y0, CARD = 390, 53, 1210, 50


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _masks():
    body = np.ones((CARD, CARD), bool)
    body[:20, :27] = False          # count number
    body[:12, :] = False            # tier dots strip
    body[:, :2] = body[:, -2:] = False
    body[-2:, :] = False
    dots = np.zeros((CARD, CARD), bool)
    dots[1:12, 24:48] = True
    return body, dots


BODY, DOTS = _masks()


def _norm(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    return v / max(float(np.linalg.norm(v)), 1e-6)


@dataclass
class UnitMatch:
    unit_id: str          # e.g. "zhuge-nu-3"
    name: str
    score: float
    count: int


class UnitDB:
    def __init__(self):
        self.db: Dict[str, List[dict]] = json.loads((DATA / "units_db.json").read_text())
        self.by_icon: Dict[str, List[str]] = {}
        for uid, variants in self.db.items():
            for v in variants:
                if v["icon"]:
                    self.by_icon.setdefault(v["icon"], [])
                    if uid not in self.by_icon[v["icon"]]:
                        self.by_icon[v["icon"]].append(uid)

    def get(self, unit_id: str, civ: Optional[str] = None) -> Optional[dict]:
        variants = self.db.get(unit_id) or []
        if civ:
            for v in variants:
                if civ in v["civs"]:
                    return v
        return variants[0] if variants else None

    def best_variant(self, unit_id: str, civ: Optional[str], age: Optional[int]) -> Optional[dict]:
        """Same unit line, highest tier this civ has by `age` (the card art doesn't show tier reliably)."""
        v0 = self.get(unit_id, civ)
        if not v0:
            return None
        base, best = v0.get("base"), v0
        for variants in self.db.values():
            for v in variants:
                if v.get("base") == base and (not civ or civ in v["civs"]) and (v["age"] or 1) <= (age or 4):
                    if (v["age"] or 0) > (best["age"] or 0) or (best["age"] or 0) > (age or 4):
                        best = v
        return best

    def base_name(self, unit_id: str, civ: Optional[str] = None) -> str:
        v = self.get(unit_id, civ)
        if not v:
            return unit_id
        base = v.get("base") or unit_id
        lowest = min((x for vs in self.db.values() for x in vs if x.get("base") == base),
                     key=lambda x: x["age"] or 0, default=v)
        name = lowest.get("name") or base
        for p in ("Early ", "Hardened ", "Veteran ", "Elite "):
            name = name.replace(p, "")
        return name

    def signature_units(self, civ: str, age: int, top: int = 2) -> List[str]:
        """Units few other civs have (unique units), best tier by `age`, priciest first."""
        found = {}
        for uid, variants in self.db.items():
            for v in variants:
                if civ in v["civs"] and (v["age"] or 1) <= age:
                    owners = self.civs_of(uid)
                    cls = set(v.get("classes") or [])
                    has_attack = any((w.get("damage") or 0) > 0 and w.get("type") in ("melee", "ranged")
                                     for w in v.get("weapons") or [])
                    if (len(owners) <= 3 and "military" in cls and has_attack
                            and not cls & {"hero", "leader", "ship", "worker", "religious", "siege", "naval_unit"}):
                        b = v.get("base") or uid
                        if b not in found or (v["age"] or 0) > (found[b][1]["age"] or 0):
                            found[b] = (uid, v)
        ranked = sorted(found.values(), key=lambda t: -sum(t[1]["cost"].values()))
        out, names = [], []
        for uid, v in ranked:
            n = self.base_name(uid, civ)
            if any(n in m or m in n for m in names):
                continue
            out.append(uid)
            names.append(n)
            if len(out) >= top:
                break
        return out

    def civs_of(self, unit_id: str) -> set:
        return {c for v in self.db.get(unit_id, []) for c in v["civs"]}

    def roster(self, civ: str, max_age: int = 4) -> List[str]:
        """Unit ids a civ can field, best tier available by max_age."""
        best: Dict[str, Tuple[int, str]] = {}
        for uid, variants in self.db.items():
            for v in variants:
                if civ in v["civs"] and (v["age"] or 1) <= max_age:
                    b = v["base"] or uid
                    if b not in best or v["age"] > best[b][0]:
                        best[b] = (v["age"], uid)
        return [uid for _, uid in best.values()]


class UnitRecognizer:
    def __init__(self, db: Optional[UnitDB] = None):
        self.db = db or UnitDB()
        z = np.load(DATA / "unit_icons.npz")
        self.icon_names = [str(n) for n in z["names"]]
        lum = _lum(z["icons"])                                   # (N, 50, 50)
        self.body = np.stack([_norm(l[BODY]) for l in lum])
        self.dots = np.stack([_norm(l[DOTS]) for l in lum])

    def _candidates(self, civs: Optional[Iterable[str]]) -> np.ndarray:
        if not civs:
            return np.ones(len(self.icon_names), bool)
        civs = set(civs)
        ok = []
        for name in self.icon_names:
            ids = self.db.by_icon.get(name, [])
            ok.append(any(self.db.civs_of(u) & civs for u in ids))
        return np.array(ok)

    def classify_card(self, card_rgb: np.ndarray, civs: Optional[Iterable[str]] = None) -> Tuple[str, float]:
        lum = _lum(card_rgb)
        b = _norm(lum[BODY])
        d = _norm(lum[DOTS])
        cand = self._candidates(civs)
        s_body = self.body @ b
        s_body = np.where(cand, s_body, -1.0)
        best = float(s_body.max())
        # figures that tie on the body (same art, different tier) are split by the dots
        near = np.where(s_body >= best - 0.02)[0]
        score = s_body[near] + 0.3 * (self.dots[near] @ d)
        i = int(near[int(np.argmax(score))])
        return self.icon_names[i], best

    def unit_for_icon(self, icon: str, civs: Optional[Iterable[str]] = None) -> str:
        ids = self.db.by_icon.get(icon, [icon.rsplit(".", 1)[0]])
        if civs:
            civs = set(civs)
            for u in ids:
                if self.db.civs_of(u) & civs:
                    return u
        return ids[0]

    def read_cards(self, rgb: np.ndarray, counts: List[int], civs: Optional[Iterable[str]] = None,
                   min_score: float = 0.55) -> List[UnitMatch]:
        out = []
        for i, n in enumerate(counts):
            x = CARD_X0 + i * CARD_STEP
            card = rgb[CARD_Y0:CARD_Y0 + CARD, x:x + CARD]
            if card.shape[:2] != (CARD, CARD):
                break
            icon, score = self.classify_card(card, civs)
            uid = self.unit_for_icon(icon, civs)
            v = self.db.get(uid, next(iter(civs)) if civs else None) or {}
            out.append(UnitMatch(unit_id=uid if score >= min_score else "?",
                                 name=v.get("name", uid) if score >= min_score else "unknown", score=score, count=n))
        return out
