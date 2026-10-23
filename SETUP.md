# AoE4 Live Analyst — v6

It only reads your game screen. It never clicks or presses anything in the game.

## What it does
- **Teams, civs and ages from the in-game player list** (skirmish and ranked): your civ, allies,
  enemies, everyone's age, with civ win rates at your rating. Alerts when an enemy ages up.
- **Ranked games: opponent scouting from aoe4world.com** (20-40 s after the game starts): for each
  enemy their rating and rank, win rate, record on the civ they picked (main or off-civ), last games
  and rating trend, hot/cold streak, climbing fast since last season, drops games often, controller,
  and your head-to-head record. The biggest threat is listed first, plus team rating vs team rating.
- **Live economy** every second: villagers per resource, idle, population, income. Alerts for idle
  villagers, being housed, floating resources, production stalling.
- **Your army by unit type** every time you press Ctrl+C (select all military), e.g.
  "80 Spearman · 52 Crossbowman · 30 Zhuge Nu", plus change since last time and big losses.
- **Enemy units**: click any enemy unit and it's logged automatically when it's a unit your civ
  doesn't have. For units you share (e.g. spearmen), select them and press **F9**.
  Before you've seen any, it uses the enemy civs' signature units.
- **Counters and fight estimate**: your army vs an equal-size army of what the enemy fields, the best
  counter units for your civ (by resource efficiency), and how many villagers two production
  buildings need to keep them coming. Estimate only: ignores micro, terrain and upgrades.
- **Upgrades for your army**: the blacksmith/university techs that help the units you actually have.
  It can't see what you've already researched, so skip the ones you have.
- **Build order picked automatically** from aoe4guides.com as soon as your civ is known, then re-picked
  in the first 2:30 once it knows the enemies (and the map, in ranked). It prefers current-patch,
  popular builds and weighs: game size (greedier in 3v3/4v4, no all-ins there), map type (open vs
  closed), aggressive enemy civs (no fast castle in 1v1) vs defensive ones (boom is safe), and builds
  written against a specific enemy civ. The card says why it picked it; **Other build** cycles the top 5.
  Then it follows the build: current/next step and villagers short or over vs the build.

## Install (once)
1. Unzip, then double-click **install.bat**. You should see `read 100%`.

## Every game
1. Double-click **start.bat**. The dashboard opens on your second screen. Keep it there: if it covers
   the player list on your game screen, teams fall back to row order.
2. Play in Borderless Windowed. Press Ctrl+C as you normally do.

## config.json (created on first start)
- `"builds"`: optional. A build link per civ always wins over the automatic pick, e.g.
  `"zhu_xis_legacy": "https://aoe4guides.com/builds/XXXX"`.
- `"auto_builds_api"`: where builds are fetched from; set it to `"off"` to turn automatic builds off.
- `"army_key"` / `"hotkey"`: change if you use other keys for "select all military" / pinning enemies.
- `"debug_save_frames": true` saves screens it can't read to `debug/` so they can be fixed.

## Troubleshooting
- Black window shows each alert and event as it happens; copy any error lines from there.
- `python main.py --test screenshot.png` shows what it reads from a screenshot.
- Footer shows `reading NN%` and which field it missed, if any.

## Data sources
Unit stats, techs and icons: github.com/aoe4world/data (May 2026; Vikings and Scots not yet included).
Civ flags: github.com/aoe4world/overlay. Win rates: aoe4world.com API. Builds: aoe4guides.com.
Rebuild unit data after a patch: `python tools/build_units_db.py <path to aoe4world/data clone>`.
