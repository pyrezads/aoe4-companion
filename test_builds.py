"""Checks the automatic build choice on a saved sample of Zhu Xi builds (no internet needed)."""
from buildorder import rank_builds

STEPS = [{"age": 1, "steps": [{"time": "0:20", "food": "6", "description": "Build a house"}]}]


def sample():
    out = []
    for line in open("data/builds_fixture_zxl.txt", encoding="utf-8"):
        t, season, strat, mp, score, views, draft = line.rstrip("\n").split("~")
        out.append({"title": t, "season": f"Season {season}", "strategy": strat or "-", "map": mp or "-",
                    "score": float(score), "views": int(views), "isDraft": draft == "1", "ageTimings": {"2": 1},
                    "steps": STEPS})
    return out


def top(kind, mp, enemies, n=3):
    ch = rank_builds(sample(), kind, mp, enemies)
    print(f"{kind} {mp} vs {', '.join(enemies)}")
    for c in ch[:n]:
        print(f"   {c.score:5.1f}  {c.build['title']}  ({'; '.join(c.reasons)})")
    return ch


ok = True
def check(cond, msg):
    global ok
    print(("   PASS " if cond else "   FAIL ") + msg)
    ok &= cond

c = top("rm_2v2", "Dry Arabia", ["french", "english"])
check(all(not x.build["isDraft"] for x in c), "drafts skipped")
check(c[0].build["season"] in ("Season 12", "Season 13"), "team game picks a current-patch build")
c = top("rm_1v1", "Dry Arabia", ["french"])
check("french" in c[0].build["title"].lower() or c[0].build["strategy"] in ("Rush", "Timing Attack"),
      "1v1 vs French: pressure or a build written for French")
check(c[0].build["strategy"] != "Fast Castle", "no fast castle vs French in 1v1")
c = top("rm_1v1", "Black Forest", ["holy_roman_empire"])
check(c[0].build["strategy"] in ("Fast Castle", "Boom") or "defensive" in c[0].build["title"].lower(),
      "closed map vs HRE: greedy or anti-defensive build")
c = top("rm_4v4", None, ["ottomans", "chinese", "rus", "malians"])
check(c[0].build["season"] in ("Season 12", "Season 13"), "4v4 picks a current-patch build")
check(rank_builds([], "rm_1v1", None, []) == [], "no builds → empty list")
print("ALL PASS" if ok else "SOME FAILED")
