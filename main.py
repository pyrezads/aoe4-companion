"""
AoE4 Live Analyst — stage 1 + 2
  · detects your ranked match and briefs you (aoe4world.com)
  · reads your HUD every second and tracks your economy (local, no AI)
  · follows a build order from aoe4guides.com
  · F9: snapshot of what you have selected

Run:   python main.py
Test:  python main.py --test screenshot.png [more.png ...]
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import sys
import threading
import time

HERE = pathlib.Path(__file__).parent
CONFIG_PATH = HERE / "config.json"

DEFAULT_CONFIG = {
    "aoe4world_profile_id": 6979980,
    "hotkey": "f9",
    "army_key": "ctrl+c",
    "read_every_seconds": 1.0,
    "game_monitor": "auto",
    "builds": {
        "_how": "Paste an aoe4guides.com build link per civ, e.g. \"zhu_xis_legacy\": \"https://aoe4guides.com/builds/XXXX\"",
        "zhu_xis_legacy": ""
    },
    "skirmish_build": "",
    "auto_builds_api": "https://aoe4-guides-api-7h2vti5ckq-ey.a.run.app",
    "debug_save_frames": False,
    "coach_skip_key": "f10",
    "coach_follow_by": "villagers"
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        return copy.deepcopy(DEFAULT_CONFIG)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


class AppState:
    """Shared between the worker threads and the dashboard."""
    def __init__(self):
        self._lock = threading.Lock()
        self._d = {}

    def set(self, **kw):
        with self._lock:
            self._d.update(kw)

    def get(self, k, default=None):
        with self._lock:
            return self._d.get(k, default)

    def copy(self):
        with self._lock:
            return dict(self._d)


# ── workers ───────────────────────────────────────────────────────────

def reader_loop(state: AppState, cfg: dict, monitor: dict, stop: threading.Event):
    from screen import GameScreen
    from analyst import Analyst

    screen = GameScreen(monitor)      # mss must be created in the thread that uses it
    an = Analyst(state, cfg)
    state.set(analyst=an)
    interval = float(cfg.get("read_every_seconds", 1.0))
    debug_dir = HERE / "debug"
    army_request = state.get("army_request")
    pin_request = state.get("pin_request")
    last_alert_keys = set()
    while not stop.is_set():
        t0 = time.time()
        try:
            frame = screen.grab()
            an.on_frame(frame, t0)
            an.refresh_advice_if_needed(t0)
            alerts = state.get("alerts") or []
            keys = {re.sub(r"\d+", "#", t.split(":")[0]) for _, t in alerts}
            snap = state.get("snapshot")
            for _, t in alerts:
                if re.sub(r"\d+", "#", t.split(":")[0]) not in last_alert_keys:
                    print(f"  [{snap.clock if snap else '--:--'}] ALERT: {t}")
            last_alert_keys = keys
            q = state.get("read_quality") or 0
            if cfg.get("debug_save_frames") and 0.2 < q < 0.6:
                debug_dir.mkdir(exist_ok=True)
                screen.save(str(debug_dir / f"lowread_{int(t0)}.png"))
        except Exception as e:
            import traceback
            print(f"  [reader] {type(e).__name__}: {e}")
            traceback.print_exc()
        # Sleep until the next read, but react at once to Ctrl+C (army) or F9 (enemy pin).
        deadline = t0 + interval
        while not stop.is_set() and time.time() < deadline:
            if army_request.is_set():
                army_request.clear()
                time.sleep(0.35)                     # let the game draw the new selection
                try:
                    an.on_army_key(screen.grab())
                except Exception as e:
                    print(f"  [army] {type(e).__name__}: {e}")
            if pin_request.is_set():
                pin_request.clear()
                try:
                    an.on_pin(screen.grab())
                except Exception as e:
                    print(f"  [pin] {type(e).__name__}: {e}")
            time.sleep(0.03)


def match_loop(state: AppState, cfg: dict, stop: threading.Event):
    from aoe4world import Aoe4WorldClient, civ_name
    from buildorder import BuildLibrary, BuildOrder, choice_state

    client = Aoe4WorldClient(int(cfg["aoe4world_profile_id"]))
    prof = client.load_profile()
    if prof:
        team = client.mode_stats("rm_team")
        state.set(match_note=f"{prof.get('name')} · team ranked {team.get('rating', '?')} "
                             f"({(team.get('rank_level') or '').replace('_', ' ')}). Waiting for a ranked match…")
    else:
        state.set(match_note="Couldn't reach aoe4world.com — live HUD tracking still works.")

    def load_build(url, label):
        if not url:
            return
        try:
            bo = BuildOrder.load(url)
            state.set(build=bo, build_note=None)
            print(f"  Build loaded for {label}: {bo.title} ({len(bo.steps)} steps)")
        except Exception as e:
            state.set(build_note=f"Couldn't load build for {label}: {e}")

    load_build(cfg.get("skirmish_build"), "skirmish")
    # older config.json files have "auto_builds_api": "" — treat empty as on; "off" turns it off
    api = cfg.get("auto_builds_api") or DEFAULT_CONFIG["auto_builds_api"]
    api = None if api.strip().lower() == "off" else api
    current_id = None
    team_sig = None
    loaded_for = None
    build_ctx = None
    library = None

    def show_choice(choices, i):
        fields = choice_state(choices, i)
        state.set(**fields)
        bo = fields["build"]
        print(f"  Build picked: {bo.title} — {fields['build_why']}")
    while not stop.is_set():
        try:
            m = client.current_match()
            if m and m.game_id != current_id:
                current_id = m.game_id
                b = client.briefing(m)
                state.set(briefing_lines=b.lines, match=m)
                print("  Match detected:", " | ".join(b.lines))
                from aoe4world import scouting_report
                rep = scouting_report(client, m)
                state.set(scout=rep)
                for line in rep["summary"] + rep["enemies"]:
                    print("   ", line)
            elif not m and current_id is not None:
                current_id = None
                state.set(briefing_lines=None, match=None, scout=None,
                          match_note="Match over. Waiting for the next one…")

            teams = state.get("teams")
            an = state.get("analyst")
            my_civ = an.my_civ() if an else None
            if not m and teams and teams.get("me") and teams["enemies"]:
                sig = (teams["mode"], teams["me"]["civ"], tuple(a["civ"] for a in teams["allies"]),
                       tuple(e["civ"] for e in teams["enemies"]))
                if sig != team_sig and all(sig[2]) and all(sig[3]) and sig[0]:
                    team_sig = sig
                    from aoe4world import team_briefing
                    lines = team_briefing(client, sig[0], sig[1], list(sig[2]), list(sig[3]))
                    state.set(briefing_lines=lines)
                    print("  Teams read from the player list:", " | ".join(lines))
            # build order: your link from config.json, else picked for this civ, game size, map and enemies
            enemies = [p.civ for p in m.enemies()] if m else [e["civ"] for e in (teams or {}).get("enemies", []) if e["civ"]]
            kind = m.kind if m else (teams or {}).get("mode")
            map_name = m.map if m else None
            snap = state.get("snapshot")
            early = not snap or snap.seconds is None or snap.seconds < 150
            ctx = (my_civ, kind, map_name, tuple(sorted(enemies)))
            url = (cfg.get("builds") or {}).get(my_civ) if my_civ else None
            if my_civ and url and my_civ != loaded_for:
                loaded_for = my_civ
                load_build(url, civ_name(my_civ))
            elif my_civ and not url and api and ctx != build_ctx and (early or my_civ != loaded_for):
                build_ctx, loaded_for = ctx, my_civ
                try:
                    if library is None:
                        library = BuildLibrary(api)
                    choices = library.choose(my_civ, kind, map_name, enemies)
                    if choices:
                        state.set(build_choices=choices)
                        show_choice(choices, 0)
                    else:
                        state.set(build=None, build_note=f"No builds found for {civ_name(my_civ)} on aoe4guides")
                except Exception as e:
                    state.set(build_note=f"Couldn't fetch a build for {civ_name(my_civ)}: {e}")
            elif my_civ and not url and not api and my_civ != loaded_for and not cfg.get("skirmish_build"):
                loaded_for = my_civ
                state.set(build=None, build_compare=None,
                          build_note=f"No build set for {civ_name(my_civ)} yet — add a link in config.json")
        except Exception as e:
            print(f"  [match] {type(e).__name__}: {e}")
        stop.wait(8)


def start_hotkey(key: str, callback):
    try:
        import keyboard
        keyboard.add_hotkey(key, callback, suppress=False)   # listen only: the game still gets the key
        print(f"  Hotkey {key.upper()} active.")
    except ImportError:
        print("  Hotkey off: run  pip install keyboard")
    except Exception as e:
        print(f"  Hotkey off ({e}). On Windows, try running the terminal as administrator.")


# ── test mode ─────────────────────────────────────────────────────────

def test_files(paths):
    import numpy as np
    from PIL import Image
    from hud_reader import HudReader
    reader = HudReader()
    for p in paths:
        img = Image.open(p).convert("RGB")
        if img.size != (2560, 1440):
            print(f"  note: {p} is {img.size}, regions are scaled from 2560x1440")
        s = reader.read(np.asarray(img).astype(np.int16))
        print(f"\n{p}  (read {s.confidence:.0%})")
        print(f"  clock {s.clock}  pop {s.pop}/{s.pop_cap}  idle {s.idle}")
        print(f"  bank      {s.resources}")
        print(f"  villagers {s.villagers}")
        if s.selection_total is not None:
            print(f"  selected  {s.selection_total}  groups {s.selection_counts}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", nargs="+", help="read screenshots instead of the live screen")
    args = ap.parse_args()
    if args.test:
        test_files(args.test)
        return

    from screen import make_dpi_aware, pick_monitors
    make_dpi_aware()
    cfg = load_config()

    gm = cfg.get("game_monitor", "auto")
    game, dash = pick_monitors(game_index=None if gm == "auto" else int(gm))
    print(f"  Game screen: {game['width']}x{game['height']} at ({game['left']},{game['top']})")
    print(f"  Dashboard:   {'second screen' if dash else 'same screen (only one monitor found)'}")

    state = AppState()
    stop = threading.Event()
    state.set(army_request=threading.Event(), pin_request=threading.Event())
    threading.Thread(target=reader_loop, args=(state, cfg, game, stop), daemon=True).start()
    threading.Thread(target=match_loop, args=(state, cfg, stop), daemon=True).start()

    from dashboard import Dashboard
    dashboard = Dashboard(state, dash, on_hotkey=lambda: state.get("pin_request").set())
    start_hotkey(cfg.get("hotkey", "f9"), lambda: state.get("pin_request").set())
    # Listen (never block) for your "select all military" key; the game still receives it.
    start_hotkey(cfg.get("army_key", "ctrl+c"), lambda: state.get("army_request").set())
    # F10: move the build coach one step on, if you've done a step it can't see.
    start_hotkey(cfg.get("coach_skip_key", "f10"),
                 lambda: state.get("analyst") and state.get("analyst").coach_skip())
    try:
        dashboard.run()
    finally:
        stop.set()
    print("  Closed. GG!")


if __name__ == "__main__":
    main()
