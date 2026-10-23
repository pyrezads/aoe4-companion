"""Unpacks a match's 'options' and 'slotinfo' (base64 + zlib) to find the map name. GitHub Actions."""
import base64, json, os, re, sys, urllib.parse, urllib.request, zlib
GH = os.environ.get("GITHUB_ACTIONS") == "true"
UA = {"User-Agent": "AoE4-Coach/0.1 (personal coaching tool)", "Accept": "application/json"}
def note(m, level="notice"):
    print(f"::{level} title=official options::{m}" if GH else m, flush=True)
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
        return json.loads(r.read())
pid = 6979980
off = get("https://aoe-api.worldsedgelink.com/community/leaderboard/getRecentMatchHistory?title=age4&profile_ids=" + urllib.parse.quote(json.dumps([pid])))
known = {x["game_id"]: x for x in get(f"https://aoe4world.com/api/v0/players/{pid}/games?limit=50").get("games", [])}
def unpack(s):
    raw = base64.b64decode(s)
    for wbits in (15, -15, 31):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            pass
    return raw
shown = 0
for m in off["matchHistoryStats"]:
    if m["id"] not in known or shown >= 3:
        continue
    shown += 1
    o = unpack(m["options"])
    text = re.sub(rb"[^\x20-\x7e]+", b" ", o).decode()
    note(f"{known[m['id']]['map']} | matchtype {m['matchtype_id']} | desc {m.get('description')} | options: {text[:1400]}")
    if shown == 1:
        s = unpack(m["slotinfo"])
        note("slotinfo: " + re.sub(rb"[^\x20-\x7e]+", b" ", s).decode()[:1500])
types = sorted({m["matchtype_id"] for m in off["matchHistoryStats"]})
note(f"matchtypes seen: {types}; descriptions: {sorted({m.get('description') for m in off['matchHistoryStats']})[:10]}")
u = (off["matchHistoryStats"][0].get("matchurls") or [{}])[0].get("url", "")
note("replay url host/shape: " + re.sub(r"[?].*", "?…", u))
