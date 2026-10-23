# AoE4 Coach — developer notes

About 3,500 lines of Python 3 (Wally runs 3.14 on Windows 10). Dependencies: numpy, pillow, mss, keyboard; Tkinter for the UI.
It runs locally and only reads the screen. It never sends input to the game. Free APIs only: aoe4world.com and aoe4guides.

## How it flows
```
screen.py   mss grab of the game monitor (DPI-aware) ─┐  1/s, plus on Ctrl+C / F9
                                                       ▼
analyst.py  Analyst.on_frame / on_army_key / on_pin ── publishes to AppState (main.py, a locked dict)
   ├ hud_reader.py   resources, villagers, pop, idle, clock, selection counts (glyph template matching)
   ├ tracker.py      EcoTracker (income, alerts), ArmyTracker (army size over time, losses)
   ├ scoreboard.py   player list: civ flags, crossed swords = enemy, age numerals → TeamTracker
   ├ units.py        unit DB + selection-card icon recognition
   └ combat.py       fight estimate (DPS vs armour, bonus damage, Lanchester), counters, upgrade advice
main.py match_loop (every 8 s)
   ├ aoe4world.py    current ranked match, civ/map win rates, opponent scouting, head-to-head
   └ buildorder.py   aoe4guides builds: automatic pick (civ, map, game size, enemy civs) + live follower
 analyst.py → coach.py  build coach: next villager's resource by villager count, pace, idle, age-up (F10 = skip a step)
dashboard.py  Tkinter window on the second monitor, redraws from AppState
```
Threads: reader_loop (screen), match_loop (network), Tk main thread (UI). They only share AppState.

## Screen reading
- Regions are written for 2560x1440 and scaled to the screen; pixel sizes (line heights, brightness)
  scale too. `python test_sizes.py` checks 1080p, 4K and 900p on resized calibration shots: 1080p and
  4K read everything, 900p ~80%, never a wrong number. Ultrawide and other UI scales still need real shots.
- Digits: text mask → column runs → glyph vectors, matched by cosine against data/glyphs.json.
  `build_templates.py` builds the templates from the labelled screenshots in calib/
  (`--holdout` = leave-one-out test; currently 148/148).
- Unit cards: 50x50 icons matched by luminance correlation against aoe4world/data icons
  (data/unit_icons.npz, made by tools/build_units_db.py).
- Player list: flags vs data/flags + in-game templates; enemy = gold crossed swords (data/swords_mask.npy).

## Tests (no game needed)
- `python test_live.py` replays calibration screenshots and checks HUD, scoreboard, units, counters
  and scouting (aoe4world responses are fixtures).
- `python test_builds.py` checks the build ranking against a saved sample of Zhu Xi builds.
- `python test_production_math.py` checks the production/gather-rate math.
- `python test_coach.py` checks the build coach on scripted games (builders, misreads, idle, pace).
- `python main.py --test calib/s02.png` shows what it reads from one screenshot.

## Electron app
`npm install`, then `npm start` (Node.js LTS). `npm run demo` skips Python and plays a simulated game.
```
electron/main.js   windows, settings (userData/settings.json), monitors, routes core messages
electron/core.js   starts `python main.py --core --config userData/config.json`, JSON lines both ways
electron/preload.js  what the pages may call: window.app (launcher), window.coach (overlays)
app/renderer/      home.html (launcher, build picker, live analyst, scout, army, settings, setup), overlay.html, theme.css
core.py            core mode: no window; messages and commands are documented at the top
```
- The app starts the core itself. If Python is missing or crashes, the launcher says why and the
  overlay plays the demo game.
- Settings live in the app's userData folder (`%APPDATA%/AoE4 Coach` on Windows): profile, game
  monitor, chosen build, follow-by. The app writes `config.json` there for the core; the build list
  cache is in `cache/` next to it (6 h, used offline).
- Test without the game: `AOE4_CORE_ARGS="--offline --replay calib/s07.png" npm start`
  (or `python main.py --core --offline --replay calib/s07.png` to see the raw messages).
- `python test_core.py` drives the core like the app does, replaying calib screenshots.
- The old Tkinter dashboard (`python main.py`, start.bat) still works and shows the same data;
  it goes once the app has been through real games.

## Installer and updates
- GitHub Actions (`.github/workflows/build-windows.yml`) packs the core with PyInstaller
  (`packaging/aoe4-core.spec`), checks the packed copy reads a screenshot (`packaging/smoke_core.py`),
  then builds `AoE4-Coach-Setup-x.y.z.exe` with the core in `resources/core`.
- Release: bump `version` in package.json, push, then `git tag vX.Y.Z && git push --tags`.
  Installed apps find it on GitHub Releases, download it and install on restart.

## Known limits / ideas
- mss uses GDI BitBlt: fine in Borderless Windowed, can be black in exclusive fullscreen.
  Windows Graphics Capture or DXGI Desktop Duplication (e.g. the `dxcam` or `windows-capture`
  packages) would be faster and work in more modes. They can drop into screen.GameScreen.grab().
- Can't see researched techs, so upgrade advice may list techs you already have.
- Enemy units sharing an icon with yours need F9 to be pinned as enemy.
- The map is only known in ranked (from aoe4world). Skirmish build picks ignore the map.
- buildorder.MAP_TYPES is a hand-made open/closed/water table; extend it as maps rotate.
- Unit data is from aoe4world/data (May 2026); Vikings and Scots units are missing.
- Packaging: PyInstaller would give a single .exe with no rewrite. A web UI (Electron/Tauri)
  could replace dashboard.py by serving AppState over a local websocket while keeping the Python core.

## Overlay pinning and voice
- `electron-overlay-window` (Windows) keeps the strip over the game window; `gamewindow.py` finds the
  game's window (RelicCardinal.exe) and the core reports its exact title in `status.game_window`.
  The plain strip stays until the add-on reports "attach", so nothing disappears if pinning fails.
  `packaging/pin_test.js` checks it on GitHub's Windows machine with a stand-in window.
- Spoken calls: fixed phrases are recorded ahead of time in `app/renderer/voice/` with
  `tools/make_voice.py` (Kokoro-82M, Apache 2.0). Build steps are read by Windows' own voice.
- Logs: `core.log` (screen reader) and `logs/main.log` (the app) in the app's data folder.
  The reading check saves screens it can't read to `calibration/` there.

## Game review
- `review.py` records each game (one sample per game second: villagers, idle, pop, bank, coach step,
  ages) and saves `games/<date>-<civ>.json` with a summary and tips when the game ends (HUD gone 90 s,
  clock reset, or the app quits). Games under 2 minutes aren't kept.
- Core commands `list_games` / `game_detail`; `game_saved` is sent when one is saved.
- `python test_review.py` plays a scripted 12-minute game through the recorder.

## Match history (official service)
- `history.py` talks only to the game's own backend (`aoe-api.worldsedgelink.com/community/leaderboard`,
  `title=age4`): `getRecentMatchHistory` (about the last 10 games per mode: map, mode, length, result,
  every player's civ/team/rating change, in-game counters, official replay link), `GetPersonalStat`
  (rating, rank, W/L per leaderboard; also finds a profile from `/steam/<SteamID64>`).
- Civ, mode and result numbers are decoded with `data/official_ids.json` (made by `tools/probe_official2.py`).
  Map names come from the packed `options` blob (base64 + zlib JSON with trailing bytes; use `raw_decode`).
- Cached at `cache/history_<profile>.json`, refreshed at most every 2 minutes; shown offline from the cache.
- Core commands `list_matches`, `match_detail`, `save_replay` (official blob links only; unzips into
  Documents/My Games/Age of Empires IV/playback), `find_steam`. `tools/check_history.py` checks the
  real service on GitHub (workflow "probe").
- The page (home.html, "Match history") uses the `.glass`, `.ambient` and `.skel` styles from theme.css.
  Steam custom `/id/` links can't be resolved without a Steam API key, so the app asks for the numeric link.

## Tests
`test_coach.py`, `test_core.py`, `test_review.py`, `test_history.py`, `test_sizes.py`, `test_live.py`, `test_builds.py`,
`test_production_math.py` (Python), `tests/e2e_app.js` (the whole app, GitHub "Test the app"),
`packaging/smoke_core.py` and `packaging/pin_test.js` (Windows build), `tools/check_real_builds.py`
(the coach on real aoe4guides builds, weekly).
