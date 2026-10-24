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
// The app's own log (userData/logs/main.log), next to the screen reader's core.log.
const log = require('electron-log/main');
log.initialize();
Object.assign(console, log.functions);
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
let settings = { v: 3, setupDone: false, profileId: null, gameDisplayId: null, build: null, auto: true, followBy: 'villagers',
  overlay: { strip: 'widget', panel: 'off', opacity: 92, widget: { x: 0.012, y: 0.13, collapsed: false } }, voice: { on: false, volume: 80, readSteps: false } };

function loadSettings() {
  try {
    const saved = JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
    settings = { ...settings, ...saved, overlay: { ...settings.overlay, ...saved.overlay }, voice: { ...settings.voice, ...saved.voice } };
    // 0.1.2: the live coach moved onto the Home tab, so the floating panel starts off (it's still in Settings)
    if (!saved.v || saved.v < 2) { settings.overlay.panel = 'off'; settings.v = 2; saveSettings(); }
    // 0.1.3: the full coach widget goes over the game instead of the small bar
    if (settings.v < 3) { if (settings.overlay.strip === 'top') settings.overlay.strip = 'widget'; settings.v = 3; saveSettings(); }
  } catch {}
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
  cfg.army_key = /^[a-z0-9+ ,.;'/\[\]-]{1,40}$/.test(settings.armyKey || '') ? settings.armyKey : 'ctrl+c';
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
// every coach window: the panel, the floating strip, and the strip pinned to the game
const coachWindows = () => [panel, strip, pinned].filter((w) => w && !w.isDestroyed());
function broadcast(type, data) {
  if (home && !home.isDestroyed()) home.webContents.send('core:msg', type, data);
  if (toOverlay.has(type)) for (const w of coachWindows()) w.webContents.send('core:msg', type, data);
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
      if (!['builds', 'build_detail', 'reading_check', 'error', 'games', 'game_detail', 'game_saved', 'matches', 'match_detail', 'replay_saved', 'steam_profile'].includes(type)) latest[type] = data;
      if (type === 'status' && data?.game_window) ensurePinned(data.game_window);
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
  backgroundThrottling: false,      // the coach and its voice keep time while the game has focus
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

// ── the strip pinned to the game window (Windows) ─────────────────────
// electron-overlay-window keeps a transparent, click-through window exactly over
// Age of Empires IV: it follows the game when it moves or resizes and hides when
// you Alt-Tab away. The core reports the game window's exact title. The pinned
// window lives for the whole session (the add-on can only attach once).
let OverlayController = null, OVERLAY_OPTS = null, pinned = null, pinnedAttached = false;
if (process.platform === 'win32') {
  try { ({ OverlayController, OVERLAY_WINDOW_OPTS: OVERLAY_OPTS } = require('electron-overlay-window')); }
  catch (e) { console.log('[overlay] pinning not available:', e.message); }
}
function ensurePinned(gameWindow) {
  if (!OverlayController || pinned || !gameWindow?.title) return;
  pinned = new BrowserWindow({ ...OVERLAY_OPTS, title: 'AoE4 Coach strip', webPreferences: webPreferences(coreState().demo) });
  pinned.setFocusable(false);                      // clicking the widget never takes the keyboard from the game
  pinned.setIgnoreMouseEvents(true, { forward: true });
  // the add-on turns mouse pass-through back on when the game gets focus; keep the pointer moves coming
  const passThrough = () => setTimeout(() => pinned && !pinned.isDestroyed() && pinned.setIgnoreMouseEvents(true, { forward: true }), 0);
  OverlayController.events.on('focus', passThrough);
  OverlayController.events.on('attach', passThrough);
  pinned.loadFile(page('overlay.html'), { query: { mode: 'strip', pinned: '1' } });
  pinned.webContents.on('did-finish-load', () => {
    for (const t of toOverlay) if (latest[t] !== undefined) pinned.webContents.send('core:msg', t, latest[t]);
    sendOverlaySettings();
  });
  // Only switch over once it has really attached; until then the plain strip stays.
  OverlayController.events.on('attach', () => {
    pinnedAttached = true;
    console.log('[overlay] attached to the game window');
    if (strip && !strip.isDestroyed()) strip.close();
    sendOverlaySettings();
  });
  OverlayController.events.on('detach', () => {           // game closed: back to the plain strip
    pinnedAttached = false;
    if (coachOpen) openStrip();
  });
  OverlayController.attachByTitle(pinned, gameWindow.title);
  console.log('[overlay] looking for the game window', JSON.stringify(gameWindow.title));
}

let coachOpen = false;
function overlaySettingsFor() {
  return { active: coachOpen && settings.overlay.strip !== 'off', position: settings.overlay.strip, opacity: settings.overlay.opacity,
    widget: settings.overlay.widget };
}
function sendOverlaySettings() {
  const o = overlaySettingsFor();
  for (const w of coachWindows()) w.webContents.send('overlay:settings', o);
}

function openStrip() {
  if (pinnedAttached || settings.overlay.strip === 'off' || (strip && !strip.isDestroyed())) return;
  // Until the game window is found: a small strip at the top of the game's monitor.
  const gb = gameDisplay().bounds;
  const sw = 600, sh = 64;
  const x = settings.overlay.strip === 'left' ? gb.x + 16 : Math.round(gb.x + (gb.width - sw) / 2);
  strip = overlayWindow({ x, y: gb.y + 6, width: sw, height: sh, movable: false, focusable: false, skipTaskbar: true }, 'strip');
  strip.setAlwaysOnTop(true, 'screen-saver');
  strip.setIgnoreMouseEvents(true);
  if (isMac) strip.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  strip.webContents.on('did-finish-load', sendOverlaySettings);
  strip.on('closed', () => { strip = null; });
}

function openCoach() {
  coachOpen = true;
  // Coach panel on the second monitor (or the right edge of the only one).
  if (settings.overlay.panel !== 'off') {
    const cd = coachDisplay();
    const wa = cd.workArea;
    const w = 460, h = Math.min(760, wa.height - 48);
    panel = overlayWindow({ x: wa.x + wa.width - w - 24, y: wa.y + 24, width: w, height: h,
      alwaysOnTop: cd.id === gameDisplay().id }, 'panel');
    // On the game's monitor (opened or dragged there) it stays above the game; elsewhere it's a normal window.
    const keepOnTop = () => {
      if (!panel || panel.isDestroyed()) return;
      const onGame = screen.getDisplayMatching(panel.getBounds()).id === gameDisplay().id;
      panel.setAlwaysOnTop(onGame, 'screen-saver');
    };
    keepOnTop();
    panel.on('moved', keepOnTop);
    panel.on('closed', () => { panel = null; if (coachOpen) closeCoach(); });
  }
  openStrip();
  sendOverlaySettings();
  home?.webContents.send('coach:state', true);
}

function closeCoach() {
  coachOpen = false;
  for (const w of [panel, strip]) if (w && !w.isDestroyed()) w.close();
  panel = strip = null;
  sendOverlaySettings();                                     // the pinned strip just goes blank
  if (home && !home.isDestroyed()) home.webContents.send('coach:state', false);
}

// ── requests from the pages ───────────────────────────────────────────
ipcMain.handle('displays', () => displaysInfo());
ipcMain.handle('set-game-display', (_e, id) => {
  settings.gameDisplayId = id;
  saveSettings();
  tellCoreMonitor();
  if (coachOpen) { closeCoach(); openCoach(); }
  return displaysInfo();
});
ipcMain.handle('coach:toggle', () => {
  if (coachOpen) closeCoach(); else openCoach();
  return coachOpen;
});
ipcMain.handle('core:state', () => coreState());
// the widget over the game: take the mouse while the pointer is on it, otherwise let clicks through to the game
ipcMain.on('overlay:mouse', (e, inside) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w && w === pinned && !w.isDestroyed()) w.setIgnoreMouseEvents(!inside, { forward: true });
});
ipcMain.on('overlay:widget', (_e, w) => {
  if (!w || typeof w !== 'object') return;
  const n = (v, lo, hi) => Math.min(hi, Math.max(lo, Number(v) || 0));
  settings.overlay.widget = { x: n(w.x, 0, 0.95), y: n(w.y, 0, 0.95), collapsed: !!w.collapsed };
  saveSettings();
});
ipcMain.handle('app:version', () => app.getVersion());
// Links from build details open in the normal browser (only these sites).
ipcMain.handle('app:open-url', (_e, url) => {
  try {
    const u = new URL(url);
    if (u.protocol === 'https:' && /(^|\.)(aoe4guides\.com|youtube\.com|youtu\.be|aoe4world\.com|twitch\.tv)$/.test(u.hostname))
      require('electron').shell.openExternal(u.toString());
  } catch {}
});
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
  const before = [settings.profileId, settings.followBy, settings.armyKey];
  settings = { ...settings, ...patch,
    overlay: { ...settings.overlay, ...(patch.overlay || {}) }, voice: { ...settings.voice, ...(patch.voice || {}) } };
  saveSettings();
  if (patch.gameDisplayId !== undefined) tellCoreMonitor();
  if (patch.overlay && coachOpen) {                          // re-open the coach windows with the new layout
    settings.overlay = { ...settings.overlay };
    if (patch.overlay.strip !== undefined || patch.overlay.panel !== undefined) { closeCoach(); openCoach(); }
    else sendOverlaySettings();
  }
  if (patch.openAtLogin !== undefined && app.isPackaged) app.setLoginItemSettings({ openAtLogin: !!patch.openAtLogin });
  if (core && (settings.profileId !== before[0] || settings.followBy !== before[1] || settings.armyKey !== before[2])) {   // restart the core with them
    writeCoreConfig();
    for (const k of Object.keys(latest)) delete latest[k];
    core.restart();
  }
  return { ...settings };
});
ipcMain.handle('core:restart', () => { if (core) { writeCoreConfig(); core.restart(); } else startCore(); });

// Build videos play inside the app. YouTube's player refuses to start without a Referer, and a page
// loaded from a file has none, so give the player's requests one. Links from inside a video
// (YouTube logo, "watch on YouTube") open in the normal browser instead of a new app window.
function setupVideo() {
  const { session, shell } = require('electron');
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['https://www.youtube-nocookie.com/*', 'https://www.youtube.com/*', 'https://*.googlevideo.com/*', 'https://*.ytimg.com/*'] },
    (d, cb) => { d.requestHeaders.Referer = d.requestHeaders.Referer || 'https://github.com/pyrezads/aoe4-companion'; cb({ requestHeaders: d.requestHeaders }); });
  app.on('web-contents-created', (_e, wc) => {
    wc.setWindowOpenHandler(({ url }) => {
      try { if (new URL(url).protocol === 'https:') shell.openExternal(url); } catch {}
      return { action: 'deny' };
    });
  });
}

app.whenReady().then(() => {
  setupVideo();
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
