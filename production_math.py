"""
AoE4 production math — Python port of the AoE4 Production Calculator
(https://www.aoe4-production-calculator.com, source: github.com/SichYuriy/aoe4-production-calculator,
ported from commit f5471fd, 2026-02-16).

The core idea:
    1. Every production building that is constantly working costs resources PER MINUTE:
           cost_per_min = unit_cost * 60 / effective_train_time
           effective_train_time = base_time / product(1 + speed_bonus for each bonus)
    2. Add those up for everything you are producing.
    3. Subtract passive income (traders, Abbasid/Malian/etc. trickles), per minute.
    4. Send food villagers to limited sources first (e.g. sheep, berries, shrines),
       each capped by how many villagers that source can hold.
    5. Divide what is left by the per-villager gathering rate for each resource:
           villagers_needed = resource_per_min / gather_rate_per_villager_per_min
    6. Round the way the calculator does: drop the fraction if it is below 0.3,
       otherwise round up (4.2 -> 4, 4.3 -> 5).

Gathering rates are resources per villager per minute.

The analyst also uses the reverse direction: given the villagers it sees on
screen, how much production can they actually keep running, and where are the gaps?
"""

from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

RESOURCES = ("food", "wood", "gold", "stone")

# ── Base gathering rates (resources per villager per minute) ──────────────
BASE_RATES = {
    "sheep": 37.05,
    "cattle": 40.78,
    "berry": 32.93,
    "deer": 41.70,
    "farm": 36.718,
    "stockyard": 38.67,
    "gold": 37.0,
    "wood": 31.0,
    "stone": 37.0,
}

# ── Common economy upgrades, as gathering-rate multipliers ────────────────
# Wheelbarrow is special: it boosts food differently per source.
WHEELBARROW_FOOD = {"berry": 1.0952, "deer": 1.0952, "sheep": 1.03, "cattle": 1.03, "farm": 1.032}
WHEELBARROW_OTHER = 1.07  # wood, gold, stone

UPGRADES = {
    # wood
    "double_broadax": ("wood", 1.10),
    "lumber_preservation": ("wood", 1.09),
    "crosscut_saw": ("wood", 1.08),
    "forestry": ("wood", 1.04),
    # food (sheep, berries, farms, cattle)
    "horticulture": ("food", 1.08),
    "fertilization": ("food", 1.067),
    "cross_breeding": ("food", 1.067),
    # gold + stone
    "specialized_pick": ("gold_stone", 1.12),
    "acid_distillation": ("gold_stone", 1.11),
    # Mongol improved versions replace the normal tier
    "improved_double_broadax": ("wood", 1.16),
    "improved_horticulture": ("food", 1.16),
    "improved_specialized_pick": ("gold_stone", 1.16),
    # deer only
    "survival_techniques": ("deer", 1.15),
}
FOOD_UPGRADE_SOURCES = {"sheep", "berry", "farm", "cattle"}

_UNITS_PATH = pathlib.Path(__file__).with_name("data") / "units.json"
_UNITS: Optional[Dict[str, dict]] = None


def units() -> Dict[str, dict]:
    """All units keyed by id, e.g. 'VILLAGER', 'ARCHER'. Loaded from data/units.json."""
    global _UNITS
    if _UNITS is None:
        _UNITS = json.loads(_UNITS_PATH.read_text(encoding="utf-8"))
    return _UNITS


def round_villagers(value: float) -> int:
    """The calculator's rounding rule: fraction < 0.3 rounds down, otherwise up."""
    whole = math.floor(value)
    return whole if value - whole < 0.3 else math.ceil(value)


# ── Gathering rates ───────────────────────────────────────────────────────

def gathering_rates(food_source: str = "farm",
                    upgrades: Iterable[str] = (),
                    wheelbarrow: bool = False,
                    drop_off_bonus_pct: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Per-villager rates per minute after upgrades.

    food_source: sheep, cattle, berry, deer, farm or stockyard.
    drop_off_bonus_pct: extra % on drop-off per resource, e.g. {"food": 10}.
    """
    if food_source not in BASE_RATES or food_source in ("gold", "wood", "stone"):
        raise ValueError(f"unknown food source: {food_source}")
    rates = {"food": BASE_RATES[food_source], "wood": BASE_RATES["wood"],
             "gold": BASE_RATES["gold"], "stone": BASE_RATES["stone"]}

    if wheelbarrow:
        rates["food"] *= WHEELBARROW_FOOD.get(food_source, 1.0)
        for r in ("wood", "gold", "stone"):
            rates[r] *= WHEELBARROW_OTHER

    for name in upgrades:
        if name not in UPGRADES:
            raise ValueError(f"unknown upgrade: {name}")
        target, mult = UPGRADES[name]
        if target == "wood":
            rates["wood"] *= mult
        elif target == "gold_stone":
            rates["gold"] *= mult
            rates["stone"] *= mult
        elif target == "food" and food_source in FOOD_UPGRADE_SOURCES:
            rates["food"] *= mult
        elif target == "deer" and food_source == "deer":
            rates["food"] *= mult

    for r, pct in (drop_off_bonus_pct or {}).items():
        rates[r] *= 1 + pct / 100
    return rates


# ── Production cost per minute ────────────────────────────────────────────

@dataclass
class Production:
    """One unit type being produced non-stop.

    buildings: how many buildings are producing it at once (2 archery ranges = 2).
    speed_bonuses: production-speed bonuses as fractions, multiplied together
                   (e.g. French school of cavalry 0.2, English network of castles...).
    cost_multiplier: e.g. 0.85 for a 15% discount on this unit.
    """
    unit: str
    buildings: int = 1
    speed_bonuses: List[float] = field(default_factory=list)
    cost_multiplier: float = 1.0


def cost_per_minute(p: Production) -> Dict[str, float]:
    u = units()[p.unit]
    speed = 1.0
    for bonus in p.speed_bonuses:
        speed *= 1 + bonus
    effective_time = u["time"] / speed
    return {r: u["cost"][r] * p.cost_multiplier * p.buildings * 60 / effective_time for r in RESOURCES}


def total_cost_per_minute(production: Iterable[Production]) -> Dict[str, float]:
    total = {r: 0.0 for r in RESOURCES}
    for p in production:
        for r, v in cost_per_minute(p).items():
            total[r] += v
    return total


# ── Villagers needed ──────────────────────────────────────────────────────

@dataclass
class LimitedFoodSource:
    """A food source that only holds a few villagers, e.g. a sheep pen or berry bush."""
    rate: float          # food per villager per minute on this source
    max_villagers: int


def villagers_needed(production: Iterable[Production],
                     rates: Dict[str, float],
                     passive_income: Optional[Dict[str, float]] = None,
                     limited_food: Iterable[LimitedFoodSource] = (),
                     min_food_villagers: float = 0) -> Dict[str, float]:
    """Villagers per resource needed to keep all production running. Unrounded."""
    need = total_cost_per_minute(production)
    for r, v in (passive_income or {}).items():
        need[r] = max(need[r] - v, 0.0)

    # Fill limited food sources first, best rate first, each capped.
    limited_vills = 0
    for src in sorted(limited_food, key=lambda s: s.rate, reverse=True):
        if need["food"] <= 0:
            break
        taken = min(math.ceil(need["food"] / src.rate), src.max_villagers)
        limited_vills += taken
        need["food"] = max(need["food"] - taken * src.rate, 0.0)

    vills = {r: (need[r] / rates[r] if rates[r] else 0.0) for r in RESOURCES}
    vills["food"] = max(vills["food"], min_food_villagers) + limited_vills
    return vills


def villagers_needed_rounded(*args, **kwargs) -> Dict[str, int]:
    return {r: round_villagers(v) for r, v in villagers_needed(*args, **kwargs).items()}


# ── Reverse direction: what can my current villagers support? ─────────────

def income_per_minute(villagers: Dict[str, int], rates: Dict[str, float],
                      passive_income: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    passive = passive_income or {}
    return {r: villagers.get(r, 0) * rates[r] + passive.get(r, 0.0) for r in RESOURCES}


def villager_gaps(production: Iterable[Production], current: Dict[str, int],
                  rates: Dict[str, float], **kwargs) -> Dict[str, int]:
    """Positive = you need that many more villagers on this resource; negative = surplus."""
    need = villagers_needed_rounded(production, rates, **kwargs)
    return {r: need[r] - current.get(r, 0) for r in RESOURCES}


def sustainable_fraction(production: Iterable[Production], current: Dict[str, int],
                         rates: Dict[str, float],
                         passive_income: Optional[Dict[str, float]] = None) -> float:
    """Share of the planned production your economy can keep running (1.0 = all of it).

    The scarcest resource decides it: if gold only covers 60% of the gold you
    spend, every gold unit idles 40% of the time.
    """
    production = list(production)
    cost = total_cost_per_minute(production)
    income = income_per_minute(current, rates, passive_income)
    shares = [income[r] / cost[r] for r in RESOURCES if cost[r] > 0]
    return min(1.0, min(shares)) if shares else 1.0


def describe_gaps(gaps: Dict[str, int]) -> str:
    """One readable line for the dashboard."""
    short = [f"+{g} on {r}" for r, g in gaps.items() if g > 0]
    extra = [f"{-g} spare on {r}" for r, g in gaps.items() if g < 0]
    if not short and not extra:
        return "Villager split matches your production."
    parts = []
    if short:
        parts.append("Need " + ", ".join(short))
    if extra:
        parts.append(", ".join(extra))
    return "; ".join(parts) + "."
