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
- Fixed regions for **2560x1440, default UI scale** (hud_reader.REGIONS, scoreboard constants, units.CARD_*).
  Other resolutions or UI scales need new regions and recalibration.
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
`npm install` then `npm start` (Node.js LTS). Runs on Mac or Windows, in demo mode for now.
- `electron/main.js` opens the launcher (app/renderer/home.html), and on **Start coach** the coach
  panel on the second monitor plus a transparent, click-through strip at the top of the game screen
  (both app/renderer/overlay.html, `?mode=panel` / `?mode=strip`). Monitors come from Electron in real pixels.
- `app/renderer/theme.css` holds the colours (sampled from the reference design) and the bundled fonts.
- Next: `python main.py --core` + a local websocket so the overlay shows the live coach messages
  from coach.py instead of the simulated game. Plan: OVERLAY_PLAN.md.

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
