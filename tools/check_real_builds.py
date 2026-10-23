"""
Plays real aoe4guides builds through the coach (needs internet; runs on GitHub Actions).

For the top builds of every civ: parse it, then simulate a player who sends every
new villager where the coach says, one villager every 20 s. Checks the coach always
has an answer, follows the build to its last step, and says sensible things.
Prints a summary; on GitHub the findings also appear as notes on the run.

    python tools/check_real_builds.py [--per-civ 4] [--json report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from buildorder import GUIDES_CIV, BuildLibrary, BuildOrder, rank_builds  # noqa: E402
from coach import RES, Coach, coach_steps  # noqa: E402

API = "https://aoe4-guides-api-7h2vti5ckq-ey.a.run.app"
GH = os.environ.get("GITHUB_ACTIONS") == "true"


def note(msg, level="notice"):
    print(f"::{level} title=Real builds::{msg}" if GH else f"[{level}] {msg}")


def simulate(bo: BuildOrder) -> dict:
    """A perfect player: every new villager goes where the coach says."""
    steps = coach_steps(bo)
    out = {"title": bo.title, "civ": bo.civ, "steps": len(bo.steps), "coach_steps": len(steps),
           "with_villagers": sum(1 for s in bo.steps if s.villagers), "problems": []}
    if not steps:
        out["problems"].append("no steps with villager numbers")
        return out
    first = steps[0]
    have = {r: first.targets[r] for r in RES}
    if sum(have.values()) < first.villagers:          # start: whatever the first step says
        have["food"] += first.villagers - sum(have.values())
    c = Coach(bo)
    t, msg, seen_steps, last_total = 0, None, set(), 0
    final = steps[-1].villagers
    calls = Counter()
    for _ in range(200):
        msg = c.update(t, dict(have), 0)
        msg = c.update(t + 1, dict(have), 0)            # two reads: the coach confirms new totals
        if msg is None:
            out["problems"].append(f"no answer at {t}s")
            break
        seen_steps.add(msg["step"]["index"])
        nxt = msg["next"]["resource"]
        if nxt not in RES:
            out["problems"].append(f"bad resource {nxt!r} at {msg['villagers']} villagers")
            break
        calls[nxt] += 1
        if msg["villagers"] >= final and msg["upcoming"] is None:
            break
        have[nxt] += 1
        t += 20
        last_total = msg["villagers"]
    out["reached_villagers"] = msg["villagers"] if msg else 0
    out["final_villagers"] = final
    out["steps_seen"] = len(seen_steps)
    out["calls"] = dict(calls)
    out["age"] = msg.get("age") if msg else None
    if msg and msg["upcoming"] is not None:
        out["problems"].append(f"stuck at {msg['villagers']} villagers, step {msg['step']['index']} of {len(bo.steps)}")
    skipped = len(steps) - len(seen_steps)
    if skipped > max(2, len(steps) // 3):
        out["problems"].append(f"{skipped} of {len(steps)} steps never shown")
    if not bo.age_times:
        out["problems"].append("no age-up times")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-civ", type=int, default=4)
    ap.add_argument("--json")
    args = ap.parse_args()
    lib = BuildLibrary(API)
    results, raw_sample, fetch_fail = [], None, []
    for slug in GUIDES_CIV:
        builds = lib.builds_for(slug)
        if not builds:
            fetch_fail.append(slug)
            continue
        for choice in rank_builds(builds, None, None, [])[: args.per_civ]:
            b = choice.build
            if raw_sample is None and b.get("steps"):
                raw_sample = {"keys": sorted(b.keys()), "ageTimings": b.get("ageTimings"),
                              "step_keys": sorted({k for sec in b["steps"] for st in sec.get("steps", []) for k in st}),
                              "first_steps": b["steps"][0].get("steps", [])[:3]}
            try:
                r = simulate(BuildOrder.from_json(b))
            except Exception as e:
                r = {"title": b.get("title"), "civ": slug, "problems": [f"crash: {type(e).__name__}: {e}"]}
            r["civ"] = slug
            results.append(r)
        time.sleep(0.3)                                   # be gentle with aoe4guides

    bad = [r for r in results if r["problems"]]
    probs = Counter(p.split(" at ")[0].split(",")[0] for r in bad for p in r["problems"])
    with_v = sum(1 for r in results if r.get("with_villagers"))
    note(f"{len(results)} builds from {len(GUIDES_CIV) - len(fetch_fail)} civs; "
         f"{len(results) - len(bad)} followed to the end with no problems; "
         f"{with_v} have villager counts per step. Problems: {dict(probs) or 'none'}")
    if fetch_fail:
        note("no builds for: " + ", ".join(fetch_fail), "warning")
    if raw_sample:
        note("data shape: " + json.dumps(raw_sample)[:3500])
    for r in bad[:8]:
        note(f"{r['civ']} | {r['title']} | {'; '.join(r['problems'])} | steps {r.get('steps')} "
             f"coach {r.get('coach_steps')} seen {r.get('steps_seen')} vil {r.get('reached_villagers')}/{r.get('final_villagers')}",
             "warning")
    if args.json:
        json.dump({"results": results, "sample": raw_sample}, open(args.json, "w"), indent=1)
    for r in results[:200]:
        if not GH:
            print(f"{r['civ']:22s} {'OK ' if not r['problems'] else 'BAD'} {r['title'][:50]:50s} {'; '.join(r['problems'])}")


if __name__ == "__main__":
    main()
