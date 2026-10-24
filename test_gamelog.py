"""Tests for gamelog.py on real (trimmed) game logs.  python test_gamelog.py"""

import pathlib
import shutil
import tempfile

from gamelog import GameLog, civ_from_race, safe_console

safe_console()

LOGS = pathlib.Path(__file__).parent / "tests" / "logs"
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def test_quickmatches():
    print("quick match 1v1 (quit) then a 4v4 loading")
    gl = GameLog(LOGS / "quickmatch_1v1_and_4v4.log")
    finished = []
    orig = gl._finish
    gl._finish = lambda t, out: (finished.append(gl.game), orig(t, out))
    events = gl.poll()
    kinds = [e.kind for e in events if e.kind != "perf"]
    check("me from the log", (gl.my_name, gl.my_id), ("NarkyNarc", 19013757))
    g1 = finished[0]
    check("1v1 kind", g1.kind, "qm_1v1")
    check("1v1 map from the matchmaker", g1.map, "Black Forest")
    check("1v1 players", [(p.name, p.profile_id, p.team, p.civ) for p in sorted(g1.players, key=lambda p: p.slot)],
          [("Fioul", 3381880, 0, "english"), ("NarkyNarc", 19013757, 1, "ottomans")])
    check("1v1 enemy is Fioul", [p.name for p in g1.players if p.team != g1.me(19013757, None).team], ["Fioul"])
    check("1v1 I quit", g1.me(19013757, None).out, "loss")
    check("1v1 results", {p.name: p.result for p in g1.players}, {"Fioul": "win", "NarkyNarc": "loss"})
    check("1v1 stats", g1.me(19013757, None).stats.get("totalcmds"), 21)
    check("1v1 lasted ~1 min", int(g1.ended_at - g1.started_at), 61)
    check("event order", kinds[:8], ["game_start", "players", "players", "map", "clock_start", "player_out", "game_over", "results"])
    g2 = gl.current()
    check("4v4 is current", g2 is not None and g2.session_id, 257199960)
    check("4v4 kind", g2.kind, "qm_4v4")
    check("4v4 phase", g2.phase, "loading")
    me = gl.me()
    check("4v4 me", me and me.name, "NarkyNarc")
    allies = sorted(p.civ for p in g2.players if p.team == me.team and p is not me)
    enemies = sorted(p.civ for p in g2.players if p.team != me.team)
    check("4v4 allies", allies, ["golden_horde", "macedonian_dynasty", "ottomans"])
    check("4v4 enemies", enemies, ["chinese", "english", "french", "order_of_the_dragon"])
    check("unicode names", any(p.name == "Spade ♠" for p in g2.players), True)


def test_skirmish():
    print("skirmish vs AI")
    gl = GameLog(LOGS / "skirmish_vs_ai.log")
    gl.poll()
    g = gl.current()
    check("kind", g.kind, "custom_1v1")
    check("not online", g.online, False)
    check("map", g.map, "Ancient Spires")
    check("phase", g.phase, "playing")
    ai = [p for p in g.players if p.ai]
    check("AI player", [(p.name, p.profile_id, p.team, p.civ) for p in ai], [("A.I. Intermediate", None, 1, "french")])
    check("clock runs from Starting mission", g.clock(g.started_at + 95), 95)


def test_live_growth():
    """The log grows while the game runs, sometimes mid-line; a new launch starts it over."""
    print("reading a log as it's written")
    src = (LOGS / "quickmatch_1v1_and_4v4.log").read_bytes()
    tmp = pathlib.Path(tempfile.mkdtemp()) / "warnings.log"
    try:
        gl = GameLog(tmp)
        seen = []
        step = 97                                   # odd chunks: lines get cut in half
        for i in range(0, len(src), step):
            with open(tmp, "ab") as f:
                f.write(src[i:i + step])
            seen += [e.kind for e in gl.poll()]
        check("all players seen in chunks", seen.count("players"), 10)
        check("4v4 current at the end", gl.current() and gl.current().kind, "qm_4v4")
        # the game restarts: the log is replaced by a shorter one
        tmp.write_bytes((LOGS / "skirmish_vs_ai.log").read_bytes())
        gl.poll()
        check("new launch picked up", gl.current() and gl.current().kind, "custom_1v1")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_4v4_left_early():
    """A full 4v4: an ally drops, I'm defeated, another ally falls, I leave before the end."""
    print("4v4, left before the end")
    gl = GameLog(LOGS / "quickmatch_4v4_left_early.log")
    events = gl.poll()
    g = gl.last_game
    check("map from the generator", g.map, "Ponds")
    outs = [e.data["player"] for e in events if e.kind == "player_out"]
    check("three of my team out, in order", outs, ["不愿意透露姓名的网友", "NarkyNarc", "abdasdemir"])
    check("no game over: I left first", g.ended_at, None)
    check("left", g.phase, "left")
    check("results undecided when you leave early", {p.result for p in g.players}, {None})
    check("lasted ~27 min until I left", int((g.left_at - g.started_at) // 60), 27)
    check("not current after leaving", gl.current(), None)


def test_civs():
    print("civ names")
    check("sultanate", civ_from_race("sultanate_ha_tug"), "tughlaq_dynasty")
    check("unknown passes through", civ_from_race("vikings"), "vikings")


if __name__ == "__main__":
    test_quickmatches()
    test_skirmish()
    test_live_growth()
    test_4v4_left_early()
    test_civs()
    print("\nall passed" if not fails else f"\n{fails} FAILED")
    raise SystemExit(1 if fails else 0)
