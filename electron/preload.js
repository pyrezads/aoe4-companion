// Bridge between the pages and the main process. Pages only get these functions.
const { contextBridge, ipcRenderer } = require('electron');

const demo = process.argv.includes('--aoe4-demo');

// Launcher (home.html)
contextBridge.exposeInMainWorld('app', {
  platform: process.platform,
  displays: () => ipcRenderer.invoke('displays'),
  setGameDisplay: (id) => ipcRenderer.invoke('set-game-display', id),
  toggleCoach: () => ipcRenderer.invoke('coach:toggle'),
  onCoachState: (cb) => ipcRenderer.on('coach:state', (_e, open) => cb(open)),
});

// Overlay windows (overlay.html). In demo mode the overlay plays its own simulated game.
contextBridge.exposeInMainWorld('coach', {
  demo,
  onUpdate: (cb) => ipcRenderer.on('coach:update', (_e, msg) => cb(msg)),
});
