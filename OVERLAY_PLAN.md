# Plan: Electron app and build coach overlay

Windows only. The Python core you already have stays. Electron is the app window,
the launcher and the overlays.

## 1. How the pieces fit

```
AoE4 Companion (one installer, Windows)
│
├─ Electron main process (electron/main.js)
│    · starts analyst-core.exe in the background when the app opens, stops it on quit
│    · opens the windows:
│        home.html                       launcher: tools, build picker, settings
│        overlay.html?mode=panel         coach panel on your second monitor
│        overlay.html?mode=strip         small strip on the game screen:
│                                        transparent, always on top, clicks pass through
│    · detects monitors with screen.getAllDisplays() and tells the core which is the game
│
│        ▲ ws://127.0.0.1:47614  (JSON messages, only on this PC)
│        ▼
└─ analyst-core.exe  (your Python, packaged with PyInstaller)
     · screen reader, trackers, aoe4world, build orders: what exists today
     · NEW coach.py   decides "next villager → wood", pace, age-up timing
     · NEW server.py  sends AppState out as messages and takes commands
                      (pick build, swap monitors, run reading check)
```

The Tkinter dashboard keeps working during the move. Retire it once the Electron
windows show the same things.

### Messages between the two halves

| Direction | Message | When |
|---|---|---|
| core → app | `coach` {clock, villagers, have, want, idle, next, step, upcoming, pace_seconds, age} | every second while a build is followed |
| core → app | `hud` {bank, villagers, pop, idle, alerts, read_quality, missed} | every second |
| core → app | `teams`, `scout`, `army`, `advice` | when they change |
| core → app | `builds` [list for a civ] | after `list_builds` |
| app → core | `list_builds` {civ} / `use_build` {id or url} | from the build picker |
| app → core | `display` {game monitor, resolution, ui_scale} | on start and when settings change |
| app → core | `reading_check` | Settings button; replies with what it read and what it missed |

## 2. The build coach (coach.py)

Input: the chosen `BuildOrder` (already parsed by buildorder.py) and the HUD
reading each second.

1. **Follow by villager count, not clock.** Total villagers = sum on each
   resource + idle. The current step is the last one whose villager count you've
   reached. Use the step's `villagers` field from aoe4guides if it has one,
   otherwise add up its resource targets. If someone is 40 s behind, the coach still
   tells them the right next move instead of skipping ahead.
2. **Next villager.** Look at the *next* step's targets. Send the next villager
   to the resource furthest below its target. Ties go to the order the build lists
   them. Also work out the villager after that ("then food") so you can queue up.
3. **New villager detected** when the total goes up by 1. Lock in the
   recommendation then, so the big word doesn't flicker while the player
   moves villagers around.
4. **Pace.** Write down the game time when each villager count is reached and
   compare it with the build's time for that step. Show "6 s behind" or "ahead".
5. **Idle villagers** → "2 idle. Send them to wood" (the current recommendation).
6. **Age-up.** Read the target age time from the build's `ageTimings`. When the
   scoreboard shows your age changed, mark it done.
7. **Manual control.** F10 skips a step. A "back" click on the panel goes back one.

What it can't know (state it in the UI, don't guess): which building a villager is
putting up, what's queued in the Town Center, and individual villager tasks.
Builders don't show up on the HUD counts, so a step that says "1 builds a
house" will look one short on its resource for a few seconds. Let a gap of 1
go for ~20 s before flagging it.

Tests: `test_coach.py` replays a scripted sequence of HUD readings (villagers
going up, a misread frame, a player falling behind) against a saved build JSON.
No game needed, same style as test_builds.py.

## 3. Screen size: detect automatically, let people override

- Electron reports every monitor's real pixel size (after Windows scaling). The
  game monitor is the one AoE4 is on: check the game window's bounds when it's
  running, otherwise the largest monitor. The user can swap in Settings.
- Python already scales regions from 2560×1440. That's good enough for
  same-shape screens (16:9) **only if** the glyphs still match at that size, and
  small text at 1080p probably won't. Plan:
  1. Move every region into one table that depends on resolution and UI scale
     (`layouts.json`) instead of constants spread across modules.
  2. Calibrate 1920×1080 and 3840×2160 the same way you did 1440p: ~10 labelled
     screenshots each in `calib/1080/`, `calib/2160/`, then `build_templates.py --size 1080`.
  3. Ultrawide (3440×1440): the HUD is pinned to the screen edges, so regions
     anchor left or right rather than scale. Do this after 1080p.
  4. **Reading check** button: in a skirmish, it reads one frame and reports
     what it read and what it missed. If it misses, offer "save this screenshot
     for calibration" so friends can send you the files.
- Detect UI scale by trying each layout on a frame and keeping the one with the
  best read quality. Fall back to the Settings choice.

## 4. Packaging and updates

- **During development** you never build an .exe. Run `npm start` (Electron) and
  it starts `python main.py --core` directly. Edits show up on the next restart
  (or instantly for HTML/CSS with a reload).
- **For friends**, a GitHub Actions workflow on a Windows runner does it all on
  every version tag:
  1. `pyinstaller analyst-core.spec` → `analyst-core.exe` + data folder
  2. `electron-builder --win nsis` → `AoE4-Companion-Setup-x.y.z.exe`
  3. uploads the installer to a GitHub Release
- **Updates:** `electron-updater` checks GitHub Releases at startup, downloads the
  new version in the background, and installs it on next launch. Friends install
  once.
- **Unsigned for now:** Windows SmartScreen will say "Windows protected your PC"
  the first time. Click *More info → Run anyway*. A code-signing certificate
  removes that later if the app spreads beyond friends.

## 5. Order of work

| Phase | What | Done when |
|---|---|---|
| 1 | `coach.py` + `test_coach.py` | Scripted games give the right "next villager" every time |
| 2 | Show the coach in the existing Tkinter dashboard | You've played 5 games with it and it felt right |
| 3 | `server.py` (websocket) + `python main.py --core` (no Tkinter) | A test page shows live messages |
| 4 | Electron shell: home.html, overlay panel + strip, build picker | Plays a full game with only the Electron windows open |
| 5 | Layout table, 1080p calibration, reading check | A friend on 1080p reads ≥ 95% |
| 6 | PyInstaller + electron-builder + GitHub Actions + auto-update | A friend installs from a link and gets an update |

Phases 1–2 only use Python you already have, so the coach can be tested in real
games before any Electron work starts.

## 6. Rules to keep

- Read pixels only. Never read game memory or send input to the game. Keep it at
  "an overlay like the ones streamers use".
- aoe4guides: ask the site owner before shipping their API to other people, cache
  build lists, and show each build's author and a link back on the coach panel.
- Everything stays on the PC except the two public APIs.

Mockups: `app/renderer/home.html` (launcher, build picker, settings) and
`app/renderer/overlay.html` (coach panel + game strip, with a simulated game when
opened in a browser).
