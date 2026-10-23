"""
What aoe4world.com has on finished games (needs internet; run on GitHub Actions).

Looks at a player's recent games and tries the per-game detail pages, then reports
which fields exist: build order timings, resources over time, scores... Used to
decide what a match history in the app can show.

    python tools/probe_aoe4world.py [profile_id]
"""
import json
import os
import sys
import urllib.request

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 6979980
GH = os.environ.get("GITHUB_ACTIONS") == "true"
UA = {"User-Agent": "AOE4-Live-Analyst/1.0 (personal coaching tool)", "Accept": "application/json"}


def note(msg, level="notice"):
    print(f"::{level} title=aoe4world probe::{msg}" if GH else f"[{level}] {msg}", flush=True)


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)


def shape(v, depth=0):
    """A compact description of a JSON value: keys and types, a few levels down."""
    if isinstance(v, dict):
        if depth >= 3:
            return "{" + ", ".join(list(v)[:12]) + "}"
        return {k: shape(x, depth + 1) for k, x in list(v.items())[:25]}
    if isinstance(v, list):
        return [shape(v[0], depth + 1), f"x{len(v)}"] if v else []
    return type(v).__name__ if not isinstance(v, (int, float)) else v


st, body = get(f"https://aoe4world.com/api/v0/players/{PID}/games?limit=5")
note(f"games list: HTTP {st}")
games = json.loads(body).get("games", []) if st == 200 else []
if not games:
    note("no games: " + body[:300], "warning")
    sys.exit(0)
g = games[0]
note("game list entry keys: " + ", ".join(g.keys()))
note("player entry: " + json.dumps(shape((g.get("teams") or [[{}]])[0][0]))[:1500])
gid = g.get("game_id")
for path in (f"/api/v0/players/{PID}/games/{gid}", f"/api/v0/games/{gid}",
             f"/players/{PID}/games/{gid}/summary?camelize=true",
             f"/players/{PID}/games/{gid}/summary"):
    st, body = get("https://aoe4world.com" + path)
    note(f"{path}: HTTP {st}, {len(body)} bytes")
    if st == 200 and body.lstrip().startswith("{"):
        try:
            data = json.loads(body)
            note(f"{path} shape: " + json.dumps(shape(data))[:3500])
        except ValueError:
            pass
    elif st and st != 404:
        note(f"{path} says: {body[:300]}")
