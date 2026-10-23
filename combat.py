"""
Combat maths from the official unit data: damage with bonus damage and armour,
army-vs-army estimate (Lanchester square law), best counters for your civ,
and the upgrades that matter for your army.

This is an estimate: it ignores micro, formations, range kiting, terrain and
upgrades already researched. Use it to decide what to build, not to call a fight
to the last unit.
"""

from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from units import UnitDB

DATA = pathlib.Path(__file__).with_name("data")
RES = ("food", "wood", "gold", "stone")


def tokens(v: dict) -> set:
    """The unit's class names (exact, not split)."""
    out = set(v.get("classes") or [])
    if v.get("base"):
        out.add(v["base"])
    return out


def _class_match(combos: List[List[str]], toks: set) -> bool:
    """A combo like ["light", "melee", "infantry"] matches the class light_melee_infantry,
    or a unit that has every one of those classes."""
    return any("_".join(combo) in toks or all(t in toks for t in combo) for combo in combos)


def primary_weapon(v: dict) -> Optional[dict]:
    ws = v.get("weapons") or []
    for kind in ("ranged", "melee", "siege"):
        for w in ws:
            if w.get("type") == kind and w.get("damage"):
                return w
    return ws[0] if ws else None


def damage_per_hit(att: dict, dfn: dict) -> float:
    w = primary_weapon(att)
    if not w:
        return 0.0
    toks = tokens(dfn)
    dmg = w["damage"] + sum(m["value"] for m in w.get("mods", []) if _class_match(m["classes"], toks))
    armor = (dfn.get("armor") or {}).get(w["type"], 0) if w["type"] in ("melee", "ranged") else 0
    return max(1.0, dmg - armor)


def dps(att: dict, dfn: dict) -> float:
    w = primary_weapon(att)
    if not w:
        return 0.0
    out = damage_per_hit(att, dfn) / max(w.get("speed") or 1.0, 0.1)
    # Melee units lose time closing in on ranged units (they shoot first and kite).
    dw = primary_weapon(dfn)
    if w["type"] == "melee" and dw and dw["type"] == "ranged" and (dw.get("range") or 0) >= 3:
        out *= 0.7 if "cavalry" in tokens(att) else 0.5
    return out


def unit_cost(v: dict) -> float:
    c = v.get("cost") or {}
    return sum(c.get(r, 0) for r in RES) or 1.0


Army = List[Tuple[dict, int]]     # [(unit variant, count)]


def _army_dps_vs(att: Army, dfn: Army) -> float:
    """Total damage per second of `att` spread over `dfn` in proportion to its HP."""
    total_hp = sum(v["hp"] * n for v, n in dfn) or 1
    return sum(n * sum(dps(a, d) * (d["hp"] * m / total_hp) for d, m in dfn) for a, n in att)


@dataclass
class FightEstimate:
    ratio: float          # >1 = you're stronger
    you_win: bool
    survivors_pct: int    # of the winner's army, roughly
    seconds: float        # rough length of the fight
    text: str


def fight(mine: Army, theirs: Army) -> Optional[FightEstimate]:
    if not mine or not theirs:
        return None
    hp_m = sum(v["hp"] * n for v, n in mine)
    hp_t = sum(v["hp"] * n for v, n in theirs)
    d_m = _army_dps_vs(mine, theirs)
    d_t = _army_dps_vs(theirs, mine)
    if not (hp_m and hp_t and d_m and d_t):
        return None
    ratio = (d_m * hp_m) / (d_t * hp_t)        # Lanchester square law strength ratio
    you_win = ratio >= 1
    r = ratio if you_win else 1 / ratio
    surv = int(round(100 * math.sqrt(max(0.0, 1 - 1 / r))))
    secs = (hp_t / d_m) if you_win else (hp_m / d_t)
    if 0.85 <= ratio <= 1.18:
        txt = f"Even fight (strength {ratio:.2f}x): don't take it without an edge (towers, a flank, upgrades)"
    elif you_win:
        txt = f"You should win (strength {ratio:.1f}x), keeping ~{surv}% of your army"
    else:
        txt = f"You'd lose (they're {1 / ratio:.1f}x stronger); they'd keep ~{surv}%. Don't take this fight yet"
    return FightEstimate(ratio, you_win, surv, secs, txt)


def best_counters(db: UnitDB, civ: str, age: int, enemy: Army, top: int = 3,
                  military_only: bool = True) -> List[Tuple[dict, str, float]]:
    """Your civ's units ranked by how efficiently they trade against the enemy mix, per resource."""
    out = []
    total_hp = sum(v["hp"] * n for v, n in enemy) or 1
    for uid in db.roster(civ, age):
        v = db.get(uid, civ)
        if not v or not v.get("hp") or not primary_weapon(v):
            continue
        toks = tokens(v)
        if military_only and ("military" not in toks or toks & {"worker", "ship", "siege", "hero", "leader",
                                                                   "religious", "naval_unit"}):
            continue
        dealt = sum(dps(v, d) * (d["hp"] * n / total_hp) for d, n in enemy)            # my dps into their mix
        taken = sum(dps(d, v) * n for d, n in enemy) / max(sum(n for _, n in enemy), 1)  # their avg dps into me
        value = (dealt * v["hp"]) / max(taken, 0.1) / unit_cost(v)
        out.append((v, uid, value))
    out.sort(key=lambda t: -t[2])
    return out[:top]


def needed_to_win(db: UnitDB, mine: Army, theirs: Army, add: dict, target_ratio: float = 1.3,
                  max_n: int = 200) -> Optional[int]:
    """How many of unit `add` to add so the fight estimate reaches target_ratio."""
    for n in range(0, max_n + 1, 1 if max_n <= 60 else 2):
        f = fight(mine + [(add, n)] if n else mine, theirs)
        if f and f.ratio >= target_ratio:
            return n
    return None


def villagers_to_produce(v: dict, buildings: int, rates: Dict[str, float]) -> Dict[str, float]:
    """Villagers per resource to keep `buildings` producing unit v non-stop."""
    t = v.get("time") or 30
    return {r: (v["cost"].get(r, 0) * 60 / t * buildings) / rates[r] for r in RES if v["cost"].get(r, 0)}


class TechAdvisor:
    def __init__(self, db: UnitDB):
        self.db = db
        self.techs = json.loads((DATA / "techs_db.json").read_text())

    def for_army(self, civ: str, age: int, army: Army, top: int = 5) -> List[Tuple[str, str, int, int]]:
        """Upgrades available to `civ` by `age`, ranked by how much they help this army.
        Returns [(name, building, age, units affected)]."""
        weight = {"meleeAttack": 1.0, "rangedAttack": 1.0, "meleeArmor": 0.7, "rangedArmor": 0.7,
                  "hitpoints": 0.8, "attackSpeed": 0.9, "moveSpeed": 0.3, "maxRange": 0.5, "healingRate": 0.3}
        scored = []
        for t in self.techs:
            if civ not in t["civs"] or (t["age"] or 1) > age:
                continue
            score, affected = 0.0, 0
            for v, n in army:
                toks = tokens(v)
                w = primary_weapon(v)
                hit = False
                for e in t["effects"]:
                    if not (v.get("base") in e["ids"] or _class_match(e["classes"], toks)):
                        continue
                    prop = e["property"]
                    if prop == "meleeAttack" and (not w or w["type"] != "melee"):
                        continue
                    if prop == "rangedAttack" and (not w or w["type"] != "ranged"):
                        continue
                    score += weight.get(prop, 0.3) * n
                    hit = True
                affected += n if hit else 0
            if score:
                props = tuple(sorted({e["property"] for e in t["effects"]}))
                scored.append((score, affected, t, props))
        seen, per_prop, out = set(), {}, []
        for score, affected, t, props in sorted(scored, key=lambda x: (-x[0], x[2]["age"] or 0)):
            if t["name"] in seen or per_prop.get(props, 0) >= 1:
                continue
            seen.add(t["name"])
            per_prop[props] = per_prop.get(props, 0) + 1
            out.append((t["name"], (t["producedBy"] or ["?"])[0].replace("-", " "), t["age"] or 1, affected))
            if len(out) >= top:
                break
        return out
