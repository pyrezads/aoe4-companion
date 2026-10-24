// Bridge between the pages and the main process. Pages only get these functions.
const { contextBridge, ipcRenderer } = require('electron');

const demo = process.argv.includes('--aoe4-demo');
const onMsg = (cb) => ipcRenderer.on('core:msg', (_e, type, data) => cb(type, data));

// Launcher (home.html)
contextBridge.exposeInMainWorld('app', {
  platform: process.platform,
  displays: () => ipcRenderer.invoke('displays'),
  setGameDisplay: (id) => ipcRenderer.invoke('set-game-display', id),
  onDisplaysChanged: (cb) => ipcRenderer.on('displays:changed', () => cb()),
  toggleCoach: () => ipcRenderer.invoke('coach:toggle'),
  onCoachState: (cb) => ipcRenderer.on('coach:state', (_e, open) => cb(open)),
  // the Python core
  coreState: () => ipcRenderer.invoke('core:state'),
  onCoreState: (cb) => ipcRenderer.on('core:state', (_e, s) => cb(s)),
  send: (cmd) => ipcRenderer.invoke('core:send', cmd),
  onMessage: onMsg,
  restartCore: () => ipcRenderer.invoke('core:restart'),
  // updates
  version: () => ipcRenderer.invoke('app:version'),
  onUpdate: (cb) => ipcRenderer.on('app:update', (_e, u) => cb(u)),
  installUpdate: () => ipcRenderer.invoke('app:install-update'),
  openLogs: () => ipcRenderer.invoke('app:open-logs'),
  openUrl: (url) => ipcRenderer.invoke('app:open-url', url),
  // settings
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (patch) => ipcRenderer.invoke('settings:set', patch),
});

// Startup splash (loading.html): progress from the real startup steps.
if (process.argv.includes('--aoe4-splash')) {
  contextBridge.exposeInMainWorld('splash', {
    onProgress: (cb) => ipcRenderer.on('splash:progress', (_e, p, message, detail) => cb(p, message, detail)),
    onDone: (cb) => ipcRenderer.on('splash:done', () => cb()),
  });
}

// Overlay windows (overlay.html). In demo mode the overlay plays its own simulated game.
contextBridge.exposeInMainWorld('coach', {
  demo,
  onMessage: onMsg,
  onSettings: (cb) => ipcRenderer.on('overlay:settings', (_e, s) => cb(s)),
  // the widget over the game: it takes the mouse only while the pointer is on it
  mouse: (inside) => ipcRenderer.send('overlay:mouse', !!inside),
  saveWidget: (w) => ipcRenderer.send('overlay:widget', w),
});
