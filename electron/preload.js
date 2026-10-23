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
  // settings
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (patch) => ipcRenderer.invoke('settings:set', patch),
});

// Overlay windows (overlay.html). In demo mode the overlay plays its own simulated game.
contextBridge.exposeInMainWorld('coach', {
  demo,
  onMessage: onMsg,
});
