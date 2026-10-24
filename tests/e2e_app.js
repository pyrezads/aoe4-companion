// Clicks through the whole app with the real Python core reading a calibration screenshot:
// first-time setup, build picker and details, coach windows, overlay settings, voice,
// reading check, live analyst, game review, match history. Runs on GitHub Actions (Linux, virtual screen).
//
//   AOE4_PYTHON=python3 xvfb-run -a npx electron tests/e2e_app.js
//
// Writes e2e_result.txt (OK / FAIL) because Electron's exit code isn't reliable here.
const path = require('path'), fs = require('fs'), os = require('os');
const { app, BrowserWindow } = require('electron');

const ROOT = path.join(__dirname, '..');
const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'aoe4-e2e-'));
app.setPath('userData', ud);
// a cached build list (no internet needed) and one saved game
fs.mkdirSync(path.join(ud, 'cache'));
const build = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'test_build.json')));
fs.writeFileSync(path.join(ud, 'cache', 'builds_ZXL.json'), JSON.stringify([
  { ...build, id: 'test-1', title: 'Test timing attack', season: 'Season 13', views: 900, strategy: 'Timing Attack', creatorName: 'Tester', video: 'https://www.youtube.com/embed/abcdefgh' },
  { ...build, id: 'test-2', title: 'Old fast castle', season: 'Season 6', views: 18000, strategy: 'Fast Castle' }]));
fs.mkdirSync(path.join(ud, 'games'));
fs.writeFileSync(path.join(ud, 'games', '2026-01-01-1200-zhu_xis_legacy.json'), JSON.stringify({
  id: '2026-01-01-1200-zhu_xis_legacy', date: '2026-01-01T12:00', civ: 'zhu_xis_legacy', enemies: ['french'],
  build: { title: 'Test timing attack' }, build_curve: [[0, 6], [260, 22]], series: [[0, 6, 0, 10, 20], [300, 20, 0, 30, 40]],
  events: [{ t: 290, kind: 'age', text: 'You reached Feudal' }],
  summary: { length: '5:00', ages: { Feudal: { you: '4:50', build: '4:20', late: 30 } }, villagers_at: {}, idle_time: 0, housed_time: 0,
    floating_time: 0, longest_no_villager: 20, tips: ['Feudal at 4:50, 30 s after the build\'s 4:20.'], late_steps: [] } }));
// a saved copy of the official match history (fetched "in the future", so the core doesn't refresh it)
const pl = (id, name, civ, team, result, rating, diff) => ({ profile_id: id, name, country: 'us', civ, team, result, rating, rating_diff: diff,
  stats: { unitprod: 150, sqkill: 90, sqlost: 70, bprod: 40, upg: 12 }, apm: 80 });
const hm = (id, end, mode, result, map, foe) => ({ id, start: end - 1500, end, duration: 1500, map, mode, matchtype_id: 1, automatch: true, result,
  civ: 'zhu_xis_legacy', team: 0, rating: 1100 + id % 50, rating_diff: result === 'win' ? 9 : -9, allies: [], enemies: [foe],
  players: [pl(123456, 'Tester', 'zhu_xis_legacy', 0, result, 1100 + id % 50, result === 'win' ? 9 : -9), pl(99, 'Rival', foe, 1, result === 'win' ? 'loss' : 'win', 1150, null)],
  replay: 'https://rl0aoelivemk2blob.blob.core.windows.net/cardinal/x.gz' });
const now = Date.now() / 1000;
fs.writeFileSync(path.join(ud, 'cache', 'history_123456.json'), JSON.stringify({
  profile: { profile_id: 123456, name: 'Tester', country: 'us', level: 42, steam: '76561199110245053' },
  matches: [hm(1001, now - 3600, 'rm_1v1', 'win', 'Dry Arabia', 'french'), hm(1002, now - 9000, 'rm_1v1', 'loss', 'Lipany', 'mongols'), hm(1003, now - 20000, 'qm_2v2', 'win', 'Altai', 'rus')],
  leaderboards: [{ leaderboard_id: 17, mode: 'rm_1v1', rating: 1101, highest: 1150, wins: 30, losses: 25, rank: 9000 }],
  fetched_at: now + 3600 }));
process.env.AOE4_CORE_ARGS = `--offline --replay ${path.join(ROOT, 'calib', 's07.png')}`;
require(path.join(ROOT, 'electron', 'main.js'));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const find = (s) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().includes(s));
let ok = true;
const GH = process.env.GITHUB_ACTIONS === 'true';
const passed = [];
const check = (cond, msg) => {
  console.log((cond ? 'PASS ' : 'FAIL ') + msg);
  if (cond) passed.push(msg);
  else if (GH) process.stdout.write(`::warning title=App test (${process.platform})::FAIL ${msg}\n`);
  if (!cond) ok = false;
};
// GitHub shows only a few notes per step: the passes go in one
const summary = () => GH && process.stdout.write(`::notice title=App test (${process.platform})::${passed.length} passed: ${passed.join('; ')}\n`);
async function until(fn, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch {} await wait(250); }
  return null;
}

app.whenReady().then(async () => {
  try {
    const home = await until(() => { const h = find('home.html'); return h && h.isVisible() ? h : null; }, 20000);
    check(home, 'launcher opens after the splash');
    const js = (s) => home.webContents.executeJavaScript(s);
    check(await js("!document.getElementById('setup').hidden"), 'first-time setup shows');
    await js("document.getElementById('setupPid').value='https://aoe4world.com/players/123456-x'; document.getElementById('setupNext').click()");
    await wait(300);
    await js("document.getElementById('setupDone').click()");
    check(await until(() => js("document.getElementById('gameStatus').textContent.includes('Reading your game')")), 'screen reader running and reading');
    const settings = JSON.parse(fs.readFileSync(path.join(ud, 'settings.json'), 'utf8'));
    check(settings.setupDone && settings.profileId === 123456, 'setup saved');

    await js("document.querySelector('.nav[data-go=coach]').click()");
    check(await until(() => js("document.querySelectorAll('#builds .t').length === 2")), 'build list from the cache');
    await js("document.querySelector('[data-more=\"test-1\"]').click()");
    check(await until(() => js("document.getElementById('bd-test-1').innerText.includes('Every step')")), 'build details load');
    check(await js("document.getElementById('bd-test-1').innerText.includes('Watch the video')"), 'build details: video button');
    await js("document.querySelector('[data-video]').click()");
    check(await until(() => js("!document.getElementById('vmodal').hidden && /youtube-nocookie\\.com\\/embed\\/abcdefgh/.test(document.querySelector('#vFrame iframe').src)")), 'build video plays in the app');
    await js("document.getElementById('vClose').click()");
    await js("document.getElementById('sort').value = 'uploaded'; document.getElementById('sort').dispatchEvent(new Event('change'))");
    check(await js("document.querySelectorAll('#builds .t').length === 2"), 'sort by upload date');
    await js("document.querySelector('[data-use=\"test-1\"]').click()");
    check(await until(() => js("document.getElementById('pickedTitle').textContent === 'Test timing attack'")), 'build chosen from details');

    check(await js("document.getElementById('panelMode').value === 'off'"), 'floating panel off by default');
    await js("document.getElementById('startCoach').click()");
    check(await until(() => js("document.getElementById('live').classList.contains('on') && document.getElementById('lvTag').textContent === 'Live' && /^(Food|Wood|Gold|Stone)$/.test(document.getElementById('lvWord').textContent)")), 'home tab shows the live coach');
    check(!find('mode=panel'), 'no floating panel unless turned on');
    await js("document.getElementById('startCoach').click()");          // stop, turn the panel on, start again
    await until(() => js("!document.getElementById('startCoach').textContent.includes('Stop')"));
    await js("panelMode.value = 'on'; saveOverlay()");
    await wait(500);
    await js("document.getElementById('startCoach').click()");
    const panel = await until(() => find('mode=panel'));
    check(panel, 'coach panel opens when turned on');
    check(await until(() => panel.webContents.executeJavaScript("!document.body.classList.contains('waiting') && document.getElementById('wordTxt').textContent.length > 2")), 'coach panel shows a live call');
    check(find('mode=strip'), 'game strip opens');

    if (process.platform === 'win32') {          // the strip pinned to the game window shows live calls
      const { spawn } = require('child_process');
      const game = spawn('powershell', ['-NoProfile', '-Command', `Add-Type -AssemblyName System.Windows.Forms;
        $f = New-Object Windows.Forms.Form; $f.Text = 'Age of Empires IV'; $f.StartPosition = 'Manual';
        $f.Left = 100; $f.Top = 80; $f.Width = 1000; $f.Height = 600; $f.Add_Shown({ $f.Activate() });
        [Windows.Forms.Application]::Run($f)`], { stdio: 'ignore' });
      try {
        const pinned = await until(() => find('pinned=1'), 30000);
        check(pinned, 'strip pinned to the game window');
        check(await until(() => !BrowserWindow.getAllWindows().some((w) => /mode=strip$/.test(w.webContents.getURL())), 15000), 'floating strip replaced by the pinned one');
        const shown = pinned && await until(() => pinned.webContents.executeJavaScript(
          "document.body.classList.contains('widget') && !document.body.classList.contains('waiting') && getComputedStyle(document.getElementById('pvPanel')).display !== 'none' && /^\\d+$/.test(document.getElementById('sv').textContent) && document.getElementById('sv').textContent !== '7'"), 15000);
        check(shown, 'coach widget over the game shows the live villager call');
        await pinned.webContents.executeJavaScript("document.getElementById('wMin').click()");
        check(await until(() => pinned.webContents.executeJavaScript("document.body.classList.contains('collapsed') && getComputedStyle(document.getElementById('cbar')).display === 'flex'")), 'widget collapses to one line');
        await wait(400);
        const saved = JSON.parse(fs.readFileSync(path.join(ud, 'settings.json'), 'utf8'));
        check(saved.overlay.widget && saved.overlay.widget.collapsed === true, 'collapsed state remembered');
        await pinned.webContents.executeJavaScript("document.getElementById('wMin').click()");
      } finally { game.kill(); }
    }

    await js("stripPos.value='off'; saveOverlay()");
    // the floating strip closes; the pinned one stays for the session and hides
    check(await until(async () => {
      const floating = BrowserWindow.getAllWindows().some((w) => /mode=strip$/.test(w.webContents.getURL()));
      const p = find('pinned=1');
      return !floating && (!p || await p.webContents.executeJavaScript("document.body.classList.contains('inactive')"));
    }), 'strip turns off from settings');

    for (const clip of ['next_wood', 'start_sheep', 'do/house'])                  // recorded calls, Opus, one voice
      check(await js(`new Promise(r => { const a = new Audio('voice/${clip}.ogg'); a.oncanplaythrough = () => r(true); a.onerror = () => r(false); })`), 'voice clip plays: ' + clip);

    await js("document.querySelector('.nav[data-go=settings]').click(); document.getElementById('readCheck').click()");
    check(await until(() => js("document.getElementById('readResult').textContent.includes('All good')")), 'reading check reads the HUD');

    await js("document.querySelector('.nav[data-go=analyst]').click()");
    check(await until(() => js("document.getElementById('anVils').textContent === '10'")), 'live analyst: villagers');

    await js("document.querySelector('.nav[data-go=review]').click()");
    check(await until(() => js("document.getElementById('rvDetail').innerText.includes('Fix next game')")), 'game review: saved game opens');
    check(await js("document.querySelectorAll('#rvChart path').length === 2"), 'game review: chart drawn');

    check(await js("GameIcons.ready.then(() => !!GameIcons.byName('Wheelbarrow') && GameIcons.find('Build a house and a mill').length === 2)"), 'game icons load and match names');
    await js("document.querySelector('.nav[data-go=army]').click()");
    await js("document.querySelector('.nav[data-go=history]').click()");
    check(await until(() => js("document.querySelectorAll('#hxList .hx-row').length === 3")), 'match history: games from the official history');
    check(await js("document.querySelector('.hx-id h2').textContent === 'Tester' && document.querySelectorAll('.hx-lb').length === 1"), 'match history: profile and leaderboard');
    check(await until(() => js("document.querySelectorAll('#hxChart .ln').length === 1")), 'match history: rating chart');
    await js("document.querySelector('[data-hxo=\"1001\"]').click()");
    check(await until(() => js("document.getElementById('hxd-1001').innerText.includes('Rival')")), 'match history: game details open');
    await js("document.querySelector('[data-hxf=qm]').click()");
    check(await js("document.querySelectorAll('#hxList .hx-row').length === 1"), 'match history: filters');
    await js("document.querySelector('.nav[data-go=settings]').click(); document.getElementById('steam').value = 'https://steamcommunity.com/id/someone'; document.getElementById('steamFind').click()");
    check(await until(() => js("document.getElementById('steamHint').textContent.includes('custom Steam link')")), 'settings: custom Steam links explained');
    // your game's "select all military" key: click, press it, saved and passed to the screen reader
    await js("document.getElementById('armyKey').click(); document.getElementById('armyKey').dispatchEvent(new KeyboardEvent('keydown', {key: 'm', ctrlKey: true, bubbles: true}))");
    await wait(500);
    const s2 = JSON.parse(fs.readFileSync(path.join(ud, 'settings.json'), 'utf8'));
    const cfg2 = JSON.parse(fs.readFileSync(path.join(ud, 'config.json'), 'utf8'));
    check(s2.armyKey === 'ctrl+m' && cfg2.army_key === 'ctrl+m' && await js("document.getElementById('armyKey').textContent === 'Ctrl + M'"), 'army key set from Settings');
  } catch (e) {
    check(false, 'crashed: ' + (e && e.stack || e));
  }
  summary();
  fs.writeFileSync(path.join(ROOT, 'e2e_result.txt'), ok ? 'OK' : 'FAIL');
  console.log(ok ? 'ALL PASS' : 'SOME FAILED');
  app.exit(ok ? 0 : 1);
});
