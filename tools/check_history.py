"""Loads a real player's history from the official service and summarises it (GitHub Actions)."""
import collections, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from history import History, insights, profile_for_steam
GH = os.environ.get("GITHUB_ACTIONS") == "true"
def note(m, level="notice"):
    print(f"::{level} title=history check::{m}" if GH else m, flush=True)
pid = int(sys.argv[1]) if len(sys.argv) > 1 else 6979980
d = History(None).load(pid)
ms = d["matches"]
note(f"{d['profile']} | {len(ms)} matches | modes {dict(collections.Counter(m['mode'] for m in ms))}")
note(f"maps: {dict(collections.Counter(m['map'] for m in ms).most_common(12))}")
bad = collections.Counter(p['civ'] for m in ms for p in m['players'] if p['civ'].startswith('civ_'))
note(f"unknown civ ids: {dict(bad) or 'none'} | results: {dict(collections.Counter(m['result'] for m in ms))}")
ins = insights(ms)
note(f"insights: {ins['games']} games, {ins['win_rate']}% won, streak {ins['streak']}, {ins['avg_minutes']} min, {ins['avg_apm']} apm; "
     f"civs {[(r['key'], r['games'], r['win_rate']) for r in ins['by_civ'][:4]]}")
note("leaderboards: " + json.dumps(d["leaderboards"])[:1500])
m = ms[0]
note("newest: " + json.dumps({k: m[k] for k in ('map', 'mode', 'duration', 'result', 'civ', 'rating', 'rating_diff', 'enemies')})
     + " | me: " + json.dumps(next((p for p in m['players'] if p['profile_id'] == pid), {}))[:600])
st = d["profile"].get("steam")
if st:
    note(f"steam {st} -> {profile_for_steam(st)}")
