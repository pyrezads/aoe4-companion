# AoE4 Coach

A companion app for Age of Empires IV. It reads your game screen (never memory,
never input) and coaches you while you play:

- **Build coach**: which resource the next villager goes to, the current and next
  step of an aoe4guides.com build, how far ahead or behind you are, age-up timing.
- **Live analyst**: villagers per resource, idle villagers, housing, floating resources.
- **Opponent scout**: ranked opponents from aoe4world.com.
- **Army and counters**: your army by unit type and what beats the enemy's.

Windows only for playing (the game is Windows only). The app window itself runs
on a Mac too, in demo mode.

## Run it

Python part (on the Windows PC with the game): see [SETUP.md](SETUP.md).

Electron app (any computer, needs [Node.js LTS](https://nodejs.org)):

```
npm install
npm start
```

`npm start` opens the launcher and starts the Python screen reader in the background
(Python 3 with `pip install -r requirements.txt`). Pick a build under **Build coach**,
press **Start coach**, then start a game in Borderless Windowed.
`npm run demo` runs without Python and plays a simulated game in the overlay.

## Layout

```
app/renderer/   the app's pages: home.html (launcher), overlay.html (coach), theme.css, fonts, images
electron/       Electron main process and preload
build/          app icon for the installer
*.py            the Python core: screen reader, coach, trackers, aoe4world, build orders
calib/ data/    calibration screenshots and game data
```

Developer notes: [DEVELOPERS.md](DEVELOPERS.md).
