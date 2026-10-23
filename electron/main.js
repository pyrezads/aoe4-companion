// AoE4 Coach: Electron main process.
//
// Windows it opens:
//   home     launcher: tools, build picker, settings          (app/renderer/home.html)
//   panel    coach panel on your second monitor               (overlay.html?mode=panel)
//   strip    small strip on the game screen: transparent,
//            always on top, clicks pass straight through      (overlay.html?mode=strip)
//
// For now it runs in demo mode: the overlay plays a simulated game. The next step
// connects it to the Python core (python main.py --core) over a local websocket,
// which forwards the coach's messages to the overlay with send('coach:update', msg).

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const page = (name) => path.join(ROOT, 'app', 'renderer', name);
const isMac = process.platform === 'darwin';
const DEMO = true;

let home = null, panel = null, strip = null;
let gameDisplayId = null;   // chosen in Settings; otherwise the largest monitor

const webPreferences = () => ({
  preload: path.join(__dirname, 'preload.js'),
  contextIsolation: true,
  sandbox: true,
  additionalArguments: DEMO ? ['--aoe4-demo'] : [],
});

// ── monitors ──────────────────────────────────────────────────────────
const pixels = (d) => d.size.width * d.size.height * d.scaleFactor ** 2;

function gameDisplay() {
  const all = screen.getAllDisplays();
  return all.find((d) => d.id === gameDisplayId) || all.slice().sort((a, b) => pixels(b) - pixels(a))[0];
}
function coachDisplay() {
  const g = gameDisplay();
  return screen.getAllDisplays().find((d) => d.id !== g.id) || g;
}
function displaysInfo() {
  const g = gameDisplay();
  return screen.getAllDisplays().map((d) => ({
    id: d.id,
    width: Math.round(d.size.width * d.scaleFactor),    // real pixels, what the game renders at
    height: Math.round(d.size.height * d.scaleFactor),
    scale: d.scaleFactor,
    bounds: d.bounds,
    game: d.id === g.id,
  }));
}

// ── windows ───────────────────────────────────────────────────────────
function createHome() {
  home = new BrowserWindow({
    width: 1340, height: 880, minWidth: 980, minHeight: 640,
    title: 'AoE4 Coach',
    backgroundColor: '#050909',
    icon: path.join(ROOT, 'build', 'icon.png'),
    titleBarStyle: isMac ? 'hiddenInset' : 'hidden',
    titleBarOverlay: isMac ? false : { color: '#050909', symbolColor: '#8b9293', height: 36 },
    show: false,
    webPreferences: webPreferences(),
  });
  home.loadFile(page('home.html'));
  home.once('ready-to-show', () => home.show());
  home.on('closed', () => { home = null; closeCoach(); });
}

function openCoach() {
  // Coach panel on the second monitor (or the right edge of the only one).
  const cd = coachDisplay();
  const wa = cd.workArea;
  const w = 460, h = Math.min(760, wa.height - 48);
  const sameScreen = cd.id === gameDisplay().id;
  panel = new BrowserWindow({
    x: wa.x + wa.width - w - 24, y: wa.y + 24, width: w, height: h,
    frame: false, transparent: true, backgroundColor: '#00000000', hasShadow: false,
    resizable: false, maximizable: false, fullscreenable: false,
    alwaysOnTop: sameScreen,
    title: 'AoE4 Coach',
    webPreferences: webPreferences(),
  });
  panel.loadFile(page('overlay.html'), { query: { mode: 'panel' } });
  panel.on('closed', () => { panel = null; closeCoach(); });

  // Strip at the top centre of the game screen. Clicks go through it to the game.
  const gb = gameDisplay().bounds;
  const sw = 600, sh = 64;
  strip = new BrowserWindow({
    x: Math.round(gb.x + (gb.width - sw) / 2), y: gb.y + 6, width: sw, height: sh,
    frame: false, transparent: true, backgroundColor: '#00000000', hasShadow: false,
    resizable: false, movable: false, focusable: false, skipTaskbar: true,
    webPreferences: webPreferences(),
  });
  strip.setAlwaysOnTop(true, 'screen-saver');
  strip.setIgnoreMouseEvents(true);
  if (isMac) strip.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  strip.loadFile(page('overlay.html'), { query: { mode: 'strip' } });
  strip.on('closed', () => { strip = null; });

  home?.webContents.send('coach:state', true);
}

function closeCoach() {
  for (const w of [panel, strip]) if (w && !w.isDestroyed()) w.close();
  panel = strip = null;
  if (home && !home.isDestroyed()) home.webContents.send('coach:state', false);
}

// ── messages from the pages ───────────────────────────────────────────
ipcMain.handle('displays', () => displaysInfo());
ipcMain.handle('set-game-display', (_e, id) => {
  gameDisplayId = id;
  if (panel) { closeCoach(); openCoach(); }
  return displaysInfo();
});
ipcMain.handle('coach:toggle', () => {
  if (panel) closeCoach(); else openCoach();
  return !!panel;
});

app.whenReady().then(() => {
  createHome();
  const refresh = () => home?.webContents.send('displays:changed');
  screen.on('display-added', refresh);
  screen.on('display-removed', refresh);
  app.on('activate', () => { if (!home) createHome(); });
});
app.on('window-all-closed', () => { if (!isMac) app.quit(); });
