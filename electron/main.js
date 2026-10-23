// AoE4 Coach: Electron main process.
//
// Windows:
//   home     launcher: tools, build picker, settings, first-time setup  (app/renderer/home.html)
//   panel    coach panel on your second monitor                        (overlay.html?mode=panel)
//   strip    small strip on the game screen: transparent, always on top,
//            clicks pass straight through                              (overlay.html?mode=strip)
//
// The Python core (python main.py --core, see core.py) reads the screen and sends
// messages; core.js runs it. If it can't start (no Python, missing packages), or
// with `npm run demo`, the overlay plays a simulated game instead.

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { Core } = require('./core');

// ── updates: the installed app checks GitHub Releases, downloads in the background,
//    and installs on restart (or when you press "Restart to update").
let update = null;            // {state: 'available'|'ready', version}
function setupUpdates() {
  if (!app.isPackaged) return;
  let autoUpdater;
  try { ({ autoUpdater } = require('electron-updater')); } catch { return; }
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  const tell = () => home && !home.isDestroyed() && home.webContents.send('app:update', update);
  autoUpdater.on('update-available', (i) => { update = { state: 'available', version: i.version }; tell(); });
  autoUpdater.on('update-downloaded', (i) => { update = { state: 'ready', version: i.version }; tell(); });
  autoUpdater.on('error', (e) => console.log('[update]', e?.message || e));
  ipcMain.handle('app:install-update', () => { core?.stop(); autoUpdater.quitAndInstall(); });
  const check = () => autoUpdater.checkForUpdates().catch((e) => console.log('[update]', e?.message || e));
  setTimeout(check, 8000);
  setInterval(check, 4 * 3600 * 1000);
}

const ROOT = path.join(__dirname, '..');
const page = (name) => path.join(ROOT, 'app', 'renderer', name);
const isMac = process.platform === 'darwin';
const FORCE_DEMO = process.argv.includes('--demo');

let home = null, panel = null, strip = null, core = null;
const latest = {};            // last message of each type from the core, for windows that open later

// ── settings (kept in the app's own folder, e.g. %APPDATA%/AoE4 Coach) ──
const userDir = () => app.getPath('userData');
const settingsPath = () => path.join(userDir(), 'settings.json');
let settings = { setupDone: false, profileId: null, gameDisplayId: null, build: null, auto: true, followBy: 'villagers' };

function loadSettings() {
  try { settings = { ...settings, ...JSON.parse(fs.readFileSync(settingsPath(), 'utf8')) }; } catch {}
}
function saveSettings() {
  fs.mkdirSync(userDir(), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(settings, null, 2));
}
// The core reads a config.json; the app writes the parts it controls before starting it.
function writeCoreConfig() {
  const p = path.join(userDir(), 'config.json');
  let cfg = {};
  try { cfg = JSON.parse(fs.readFileSync(p, 'utf8')); } catch {}
  cfg.aoe4world_profile_id = settings.profileId || 0;
  cfg.cache_dir = path.join(userDir(), 'cache');
  cfg.coach_follow_by = settings.followBy || 'villagers';
  fs.mkdirSync(userDir(), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(cfg, null, 2));
  return p;
}

// ── monitors ──────────────────────────────────────────────────────────
const pixels = (d) => d.size.width * d.size.height * d.scaleFactor ** 2;
function gameDisplay() {
  const all = screen.getAllDisplays();
  return all.find((d) => d.id === settings.gameDisplayId) || all.slice().sort((a, b) => pixels(b) - pixels(a))[0];
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
    scale: d.scaleFactor, bounds: d.bounds, game: d.id === g.id,
  }));
}
function tellCoreMonitor() {
  const g = gameDisplay();
  core?.send({ cmd: 'set_monitor', width: Math.round(g.size.width * g.scaleFactor), height: Math.round(g.size.height * g.scaleFactor),
    x: Math.round(g.bounds.x * g.scaleFactor), y: Math.round(g.bounds.y * g.scaleFactor) });
}

// ── messages ──────────────────────────────────────────────────────────
const toOverlay = new Set(['build', 'status', 'coach']);   // build first: it resets the overlay's coach view
function broadcast(type, data) {
  if (home && !home.isDestroyed()) home.webContents.send('core:msg', type, data);
  if (toOverlay.has(type)) for (const w of [panel, strip]) if (w && !w.isDestroyed()) w.webContents.send('core:msg', type, data);
}
function coreState() {
  return { running: !!core?.running, error: FORCE_DEMO ? 'Demo mode' : core?.error || null, demo: FORCE_DEMO || !core?.running };
}
function sendCoreState() {
  if (home && !home.isDestroyed()) home.webContents.send('core:state', coreState());
}

function startCore() {
  if (FORCE_DEMO) return;
  core = new Core({
    root: ROOT,
    configPath: writeCoreConfig(),
    logPath: path.join(userDir(), 'core.log'),
    onMessage: (type, data) => {
      if (type === 'ready') {
        if (!startup.core) { startup.core = true; splashProgress(85, 'Screen reader running', 'Loading your builds'); startupStep(); }
        tellCoreMonitor();
        const b = settings.build;                 // re-apply the build you chose last time
        if (b?.url) core.send({ cmd: 'use_build', url: b.url });
        else if (b?.id) core.send({ cmd: 'use_build', id: b.id, civ: b.civ });
      }
      if (!['builds', 'reading_check', 'error'].includes(type)) latest[type] = data;
      broadcast(type, data);
    },
    onState: (s) => { sendCoreState(); if (s?.error) startupStep(); },
  });
  core.start();
}

// ── startup splash ────────────────────────────────────────────────────
// Shows app/renderer/loading.html while the launcher loads and the screen reader
// starts. The bar follows those real steps, then the splash fades into the launcher.
let splash = null, splashShownAt = 0, splashDone = false;
const startup = { home: false, core: false };

function openSplash() {
  const wa = screen.getPrimaryDisplay().workArea;
  const w = Math.min(1120, Math.round(wa.width * 0.62)), h = Math.round(w * 941 / 1672);
  splash = new BrowserWindow({
    width: w, height: h, x: Math.round(wa.x + (wa.width - w) / 2), y: Math.round(wa.y + (wa.height - h) / 2),
    frame: false, resizable: false, movable: true, show: false, skipTaskbar: false,
    backgroundColor: '#0b1420', title: 'AoE4 Coach', icon: path.join(ROOT, 'build', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true,
      additionalArguments: ['--aoe4-splash'] },
  });
  splash.loadFile(page('loading.html'));
  splash.webContents.on('did-finish-load', () => {    // steps that happened while the page was loading
    splashLoaded = true;
    const last = splashLast;
    splashProgress(18, 'Opening AoE4 Coach', 'Loading your settings');
    if (last && last[0] > 18) setTimeout(() => splashProgress(...last), 350);
  });
  splash.once('ready-to-show', () => { splash.show(); splashShownAt = Date.now(); });
  splash.on('closed', () => { splash = null; });
  setTimeout(() => finishStartup('Ready'), 15000);          // never keep anyone waiting longer
}

let splashLast = null, splashLoaded = false;
function splashProgress(p, message, detail) {
  splashLast = [p, message, detail];
  if (splash && !splash.isDestroyed() && splashLoaded) splash.webContents.send('splash:progress', p, message, detail);
}

function startupStep() {
  if (startup.home && (startup.core || FORCE_DEMO || core?.error)) finishStartup('Ready');
}

function finishStartup(message) {
  if (splashDone) return;
  splashDone = true;
  splashProgress(100, message, core?.running ? 'Screen reader running' : FORCE_DEMO ? 'Demo mode' : 'Opening without the screen reader');
  const shown = splashShownAt ? Date.now() - splashShownAt : 0;
  const wait = Math.max(500, 2600 - shown);                    // long enough to actually see it
  setTimeout(() => {
    if (home && !home.isDestroyed()) home.show();
    if (splash && !splash.isDestroyed()) {
      splash.webContents.send('splash:done');
      setTimeout(() => splash && !splash.isDestroyed() && splash.close(), 850);
    }
  }, wait);
}

// ── windows ───────────────────────────────────────────────────────────
const webPreferences = (demo) => ({
  preload: path.join(__dirname, 'preload.js'),
  contextIsolation: true,
  sandbox: true,
  additionalArguments: demo ? ['--aoe4-demo'] : [],
});

// Where the launcher opens: where you left it, if that monitor is still there;
// otherwise a comfortable size centred on the main monitor (not full screen).
function homeBounds() {
  const saved = settings.window;
  if (saved && screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return saved.x < a.x + a.width - 100 && saved.x + saved.width > a.x + 100 && saved.y >= a.y - 10 && saved.y < a.y + a.height - 100;
  })) return saved;
  const a = screen.getPrimaryDisplay().workArea;
  const width = Math.max(980, Math.min(1380, Math.round(a.width * 0.72)));
  const height = Math.max(640, Math.min(900, Math.round(a.height * 0.82)));
  return { width, height, x: Math.round(a.x + (a.width - width) / 2), y: Math.round(a.y + (a.height - height) / 2) };
}
function rememberHome() {
  if (!home || home.isDestroyed() || home.isMinimized()) return;
  settings.window = { ...(home.isMaximized() ? (settings.window || home.getNormalBounds()) : home.getBounds()), maximized: home.isMaximized() };
  saveSettings();
}

function createHome() {
  const b = homeBounds();
  home = new BrowserWindow({
    ...b, minWidth: 980, minHeight: 640,
    title: 'AoE4 Coach',
    backgroundColor: '#050909',
    icon: path.join(ROOT, 'build', 'icon.png'),
    titleBarStyle: isMac ? 'hiddenInset' : 'hidden',
    titleBarOverlay: isMac ? false : { color: '#050909', symbolColor: '#8b9293', height: 36 },
    show: false,
    webPreferences: webPreferences(false),
  });
  home.loadFile(page('home.html'));
  if (b.maximized) home.maximize();
  let t = null;
  const later = () => { clearTimeout(t); t = setTimeout(rememberHome, 600); };
  home.on('resize', later);
  home.on('move', later);
  home.on('close', rememberHome);
  home.once('ready-to-show', () => {
    startup.home = true;
    splashProgress(55, 'Starting the screen reader', 'Looking for Age of Empires IV');
    if (splashDone || !splash) home.show();
    startupStep();
  });
  home.webContents.on('did-finish-load', () => {
    sendCoreState();
    if (update) home.webContents.send('app:update', update);
    for (const [t, d] of Object.entries(latest)) home.webContents.send('core:msg', t, d);
  });
  home.on('closed', () => { home = null; closeCoach(); });
}

function overlayWindow(opts, mode) {
  const demo = coreState().demo;
  const w = new BrowserWindow({
    frame: false, transparent: true, backgroundColor: '#00000000', hasShadow: false,
    resizable: false, maximizable: false, fullscreenable: false, title: 'AoE4 Coach',
    ...opts, webPreferences: webPreferences(demo),
  });
  w.loadFile(page('overlay.html'), { query: { mode } });
  w.webContents.on('did-finish-load', () => {
    for (const t of toOverlay) if (latest[t] !== undefined) w.webContents.send('core:msg', t, latest[t]);
  });
  return w;
}

function openCoach() {
  // Coach panel on the second monitor (or the right edge of the only one).
  const cd = coachDisplay();
  const wa = cd.workArea;
  const w = 460, h = Math.min(760, wa.height - 48);
  panel = overlayWindow({ x: wa.x + wa.width - w - 24, y: wa.y + 24, width: w, height: h,
    alwaysOnTop: cd.id === gameDisplay().id }, 'panel');
  panel.on('closed', () => { panel = null; closeCoach(); });

  // Strip at the top centre of the game screen. Clicks go through it to the game.
  const gb = gameDisplay().bounds;
  const sw = 600, sh = 64;
  strip = overlayWindow({ x: Math.round(gb.x + (gb.width - sw) / 2), y: gb.y + 6, width: sw, height: sh,
    movable: false, focusable: false, skipTaskbar: true }, 'strip');
  strip.setAlwaysOnTop(true, 'screen-saver');
  strip.setIgnoreMouseEvents(true);
  if (isMac) strip.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  strip.on('closed', () => { strip = null; });

  home?.webContents.send('coach:state', true);
}

function closeCoach() {
  for (const w of [panel, strip]) if (w && !w.isDestroyed()) w.close();
  panel = strip = null;
  if (home && !home.isDestroyed()) home.webContents.send('coach:state', false);
}

// ── requests from the pages ───────────────────────────────────────────
ipcMain.handle('displays', () => displaysInfo());
ipcMain.handle('set-game-display', (_e, id) => {
  settings.gameDisplayId = id;
  saveSettings();
  tellCoreMonitor();
  if (panel) { closeCoach(); openCoach(); }
  return displaysInfo();
});
ipcMain.handle('coach:toggle', () => {
  if (panel) closeCoach(); else openCoach();
  return !!panel;
});
ipcMain.handle('core:state', () => coreState());
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:open-logs', () => require('electron').shell.openPath(userDir()));
ipcMain.handle('core:send', (_e, cmd) => {
  if (cmd?.cmd === 'use_build') {             // remember it for next time
    settings.build = { id: cmd.id || null, url: cmd.url || null, civ: cmd.civ || null };
    settings.auto = false;
    saveSettings();
  } else if (cmd?.cmd === 'auto_build') {
    settings.build = null;
    settings.auto = true;
    saveSettings();
  }
  return core ? core.send(cmd) : false;
});
ipcMain.handle('settings:get', () => ({ ...settings }));
ipcMain.handle('settings:set', (_e, patch) => {
  const before = [settings.profileId, settings.followBy];
  settings = { ...settings, ...patch };
  saveSettings();
  if (patch.gameDisplayId !== undefined) tellCoreMonitor();
  if (patch.openAtLogin !== undefined && app.isPackaged) app.setLoginItemSettings({ openAtLogin: !!patch.openAtLogin });
  if (core && (settings.profileId !== before[0] || settings.followBy !== before[1])) {   // restart the core with them
    writeCoreConfig();
    for (const k of Object.keys(latest)) delete latest[k];
    core.restart();
  }
  return { ...settings };
});
ipcMain.handle('core:restart', () => { if (core) { writeCoreConfig(); core.restart(); } else startCore(); });

app.whenReady().then(() => {
  loadSettings();
  openSplash();
  startCore();
  createHome();
  setupUpdates();
  const refresh = () => home?.webContents.send('displays:changed');
  screen.on('display-added', refresh);
  screen.on('display-removed', refresh);
  app.on('activate', () => { if (!home) createHome(); });
});
app.on('window-all-closed', () => { if (!isMac) app.quit(); });
app.on('before-quit', () => core?.stop());
