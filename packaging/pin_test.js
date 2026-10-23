// Windows check (GitHub Actions): pins an overlay to a stand-in "Age of Empires IV"
// window, moves that window, and checks the overlay follows.
//   npx electron packaging/pin_test.js
const { app, BrowserWindow } = require('electron');
const { spawn, execFileSync } = require('child_process');
const path = require('path');
const { OverlayController, OVERLAY_WINDOW_OPTS } = require('electron-overlay-window');

const TITLE = 'Age of Empires IV';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
// GitHub shows these as notes on the run (readable through the API)
const note = (msg, level = 'notice') => console.log(`::${level} title=Pin test::${msg}`);
const ps = (script) => spawn('powershell', ['-NoProfile', '-Command', script], { stdio: 'inherit' });

app.whenReady().then(async () => {
  let ok = true;
  const fail = (m) => { ok = false; note('FAIL ' + m, 'warning'); };
  // the stand-in game: a WinForms window that moves after 4 s
  const game = ps(`Add-Type -AssemblyName System.Windows.Forms;
    $f = New-Object Windows.Forms.Form; $f.Text = '${TITLE}'; $f.StartPosition = 'Manual';
    $f.Left = 200; $f.Top = 150; $f.Width = 900; $f.Height = 520;
    $t = New-Object Windows.Forms.Timer; $t.Interval = 4000; $t.Add_Tick({ $f.Left = 420; $f.Top = 260; $f.Width = 1100; $f.Height = 640; $f.Activate(); $t.Stop() });
    $f.Add_Shown({ $f.Activate(); $t.Start() }); [Windows.Forms.Application]::Run($f)`);
  await wait(2500);

  // the Python side finds it too
  try {
    const out = execFileSync('python', ['-c', 'import gamewindow, json; print(json.dumps(gamewindow.find_game_window()))'],
      { cwd: path.join(__dirname, '..') }).toString().trim();
    note('python finds: ' + out);
    if (!out.includes(TITLE)) fail('gamewindow.py did not find the window');
  } catch (e) { fail('gamewindow.py: ' + e.message); }

  const w = new BrowserWindow({ ...OVERLAY_WINDOW_OPTS });
  w.loadURL('data:text/html,<body style="background:transparent"></body>');
  let attached = null;
  OverlayController.events.on('attach', (e) => { attached = e; note('attach ' + JSON.stringify(e)); });
  OverlayController.attachByTitle(w, TITLE);
  await wait(1500);
  const before = w.getBounds();
  note('overlay before move: ' + JSON.stringify(before) + ' visible: ' + w.isVisible());
  if (!attached) fail('never attached');
  await wait(3500);
  const after = w.getBounds();
  note('overlay after move: ' + JSON.stringify(after));
  if (attached && (after.x === before.x && after.width === before.width)) fail('did not follow the window');
  game.kill();
  note(ok ? 'OK: the overlay pins to the game window and follows it' : 'pin test failed', ok ? 'notice' : 'warning');
  app.exit(ok ? 0 : 1);
});
