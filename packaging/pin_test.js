// Windows check (GitHub Actions): pins an overlay to a stand-in "Age of Empires IV"
// window, moves that window, and checks the overlay follows. Results show as notes on the run.
//   npx electron packaging/pin_test.js
const { app, BrowserWindow } = require('electron');
const { spawn, execFileSync } = require('child_process');
const path = require('path');
const { OverlayController, OVERLAY_WINDOW_OPTS } = require('electron-overlay-window');

const TITLE = 'Age of Empires IV';
const ROOT = path.join(__dirname, '..');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const note = (msg, level = 'notice') => console.log(`::${level} title=Pin test::${msg}`);
const py = (code) => execFileSync('python', ['-c', code], { cwd: ROOT }).toString().trim();

app.whenReady().then(async () => {
  let ok = true;
  const fail = (m) => { ok = false; note('FAIL ' + m, 'warning'); };
  const game = spawn('powershell', ['-NoProfile', '-Command', `Add-Type -AssemblyName System.Windows.Forms;
    $f = New-Object Windows.Forms.Form; $f.Text = '${TITLE}'; $f.StartPosition = 'Manual';
    $f.Left = 200; $f.Top = 150; $f.Width = 900; $f.Height = 520; $f.Add_Shown({ $f.Activate() });
    [Windows.Forms.Application]::Run($f)`], { stdio: 'inherit' });

  // 1. the Python side finds the window (wait for PowerShell to open it)
  let found = 'null';
  for (let i = 0; i < 30 && !found.includes(TITLE); i++) {
    await wait(500);
    try { found = py('import gamewindow, json; print(json.dumps(gamewindow.find_game_window()))'); } catch (e) { found = 'error ' + e.message; }
  }
  note('python finds: ' + found);
  if (!found.includes(TITLE)) fail('gamewindow.py did not find the window');

  // 2. the overlay attaches to it
  const w = new BrowserWindow({ ...OVERLAY_WINDOW_OPTS });
  w.loadURL('data:text/html,<body style="background:transparent"></body>');
  let attached = null;
  OverlayController.events.on('attach', (e) => { attached = e; note('attach ' + JSON.stringify(e)); });
  OverlayController.events.on('moveresize', (e) => note('moveresize ' + JSON.stringify(e)));
  OverlayController.attachByTitle(w, TITLE);
  for (let i = 0; i < 20 && !attached; i++) await wait(250);
  const before = w.getBounds();
  note('overlay before move: ' + JSON.stringify(before) + ' visible: ' + w.isVisible());
  if (!attached) fail('never attached');

  // 3. move and resize the game window; the overlay should follow
  try {
    note('moved window: ' + py(`import ctypes
from ctypes import wintypes
u = ctypes.windll.user32
u.FindWindowW.restype = wintypes.HWND
h = u.FindWindowW(None, '${TITLE}')
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u.SetWindowPos(h, None, 420, 260, 1100, 640, 0x0004)
r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r)); print(r.left, r.top, r.right - r.left, r.bottom - r.top)`));
  } catch (e) { fail('could not move the window: ' + e.message); }
  await wait(2000);
  const after = w.getBounds();
  note('overlay after move: ' + JSON.stringify(after));
  if (attached && after.x === before.x && after.width === before.width) fail('did not follow the window');
  game.kill();
  note(ok ? 'OK: found by Python, pinned, and follows the window' : 'pin test failed', ok ? 'notice' : 'warning');
  app.exit(ok ? 0 : 1);
});
