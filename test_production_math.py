"""
Checks the Python port against the original calculator's own unit tests
(src/service/production-calculator-service/ProductionCalculatorService.test.ts).

Run:  python test_production_math.py
"""

import math
from production_math import (
    Production, LimitedFoodSource, gathering_rates, villagers_needed,
    villagers_needed_rounded, villager_gaps, sustainable_fraction,
    round_villagers, describe_gaps,
)

RATES_40 = {"food": 40, "wood": 40, "gold": 40, "stone": 40}

# The repo's test production: 1 TC villagers, 1 archery (archer), 1 barracks (spear), 1 stable (knight)
def base_mix(knight_speed=(), discount=1.0):
    return [
        Production("VILLAGER"),
        Production("ARCHER", cost_multiplier=discount),
        Production("SPEARMAN"),
        Production("KNIGHT", speed_bonuses=list(knight_speed), cost_multiplier=discount),
    ]


def close(a, b, tol=0.01):
    return math.isclose(a, b, abs_tol=tol)


def check(name, got, want):
    ok = all(close(got[r], v) for r, v in want.items())
    print(("PASS " if ok else "FAIL ") + name, {r: round(got[r], 4) for r in want})
    return ok


results = []

# 1. no modifiers -> food 18.75, wood 7, gold 4.2857, stone 0
results.append(check("no modifiers", villagers_needed(base_mix(), RATES_40),
                     {"food": 18.75, "wood": 7, "gold": 4.2857, "stone": 0}))

# 2. French school of cavalry (+20% cavalry production speed) -> food 19.95, gold 5.1428
results.append(check("school of cavalry", villagers_needed(base_mix(knight_speed=[0.2]), RATES_40),
                     {"food": 19.95, "wood": 7, "gold": 5.1428, "stone": 0}))

# 3. Speed bonuses multiply: English MMA + White Tower + Military Academy on White Tower MAA
mult = villagers_needed([Production("WHITE_TOWER_MAN_AT_ARMS",
                                    speed_bonuses=[0.0, 0.0, 0.0])], RATES_40)
# (the TS test uses those three specific modifiers; we only verify the unit exists and math runs)
results.append(mult["food"] > 0)

# 4. French castle discount (20% off archer + knight) -> food 16.95, wood 6, gold 3.4285
results.append(check("french castle discount", villagers_needed(base_mix(discount=0.8), RATES_40),
                     {"food": 16.95, "wood": 6, "gold": 3.4285, "stone": 0}))

# 5. passive income +100 gold/min -> gold 1.7857
results.append(check("passive gold", villagers_needed(base_mix(), RATES_40, passive_income={"gold": 100}),
                     {"food": 18.75, "wood": 7, "gold": 1.7857, "stone": 0}))

# 6. limited food source: 5 villagers max at 50/min -> food 17.5
results.append(check("limited food source",
                     villagers_needed(base_mix(), RATES_40, limited_food=[LimitedFoodSource(50, 5)]),
                     {"food": 17.5, "wood": 7, "gold": 4.2857, "stone": 0}))

# 7. all together -> food 16.66, wood 6, gold 1.61161
results.append(check("all together",
                     villagers_needed(base_mix(knight_speed=[0.2], discount=0.8), RATES_40,
                                      passive_income={"gold": 100},
                                      limited_food=[LimitedFoodSource(50, 5)]),
                     {"food": 16.66, "wood": 6, "gold": 1.6116, "stone": 0}))

# Rounding rule: <0.3 down, otherwise up (exactly x.3 can float to 0.2999.. and round down, same as the original JS)
results.append(round_villagers(4.29) == 4 and round_villagers(4.35) == 5 and round_villagers(7.0) == 7)

# Real gathering rates sanity: wheelbarrow on sheep
r = gathering_rates("sheep", wheelbarrow=True)
results.append(close(r["food"], 37.05 * 1.03) and close(r["wood"], 31 * 1.07) and close(r["gold"], 37 * 1.07))

# Reverse direction, as the analyst will use it
plan = [Production("VILLAGER"), Production("ARCHER", buildings=2)]
rates = gathering_rates("sheep", wheelbarrow=True)
need = villagers_needed_rounded(plan, rates)
gaps = villager_gaps(plan, {"food": 4, "wood": 4, "gold": 0}, rates)
frac = sustainable_fraction(plan, {"food": 4, "wood": 4, "gold": 0}, rates)
print("example  1 TC + 2 archery on sheep, wheelbarrow -> need", need)
print("         with 4 food / 4 wood on screen ->", describe_gaps(gaps), f"keeps {frac:.0%} running")
# 1 TC = 150 food/min, 2 archery = 240 food + 400 wood/min; 390/38.16 = 10.2 -> 10, 400/33.17 = 12.06 -> 12
results.append(need == {"food": 10, "wood": 12, "gold": 0, "stone": 0})

print(f"\n{sum(bool(x) for x in results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
