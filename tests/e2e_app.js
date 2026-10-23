// Clicks through the whole app with the real Python core reading a calibration screenshot:
// first-time setup, build picker and details, coach windows, overlay settings, voice,
// reading check, live analyst, game review. Runs on GitHub Actions (Linux, virtual screen).
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
process.env.AOE4_CORE_ARGS = `--offline --replay ${path.join(ROOT, 'calib', 's07.png')}`;
require(path.join(ROOT, 'electron', 'main.js'));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const find = (s) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().includes(s));
let ok = true;
const check = (cond, msg) => { console.log((cond ? 'PASS ' : 'FAIL ') + msg); if (!cond) ok = false; };
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
    await js("document.querySelector('[data-use=\"test-1\"]').click()");
    check(await until(() => js("document.getElementById('pickedTitle').textContent === 'Test timing attack'")), 'build chosen from details');

    await js("document.getElementById('startCoach').click()");
    const panel = await until(() => find('mode=panel'));
    check(panel, 'coach panel opens');
    check(await until(() => panel.webContents.executeJavaScript("!document.body.classList.contains('waiting') && document.getElementById('wordTxt').textContent.length > 2")), 'coach panel shows a live call');
    check(find('mode=strip'), 'game strip opens');

    await js("stripPos.value='off'; saveOverlay()");
    check(await until(() => !find('mode=strip')), 'strip turns off from settings');

    check(await js("new Promise(r => { const a = new Audio('voice/vil_wood.ogg'); a.oncanplaythrough = () => r(true); a.onerror = () => r(false); })"), 'voice clips load');

    await js("document.querySelector('.nav[data-go=settings]').click(); document.getElementById('readCheck').click()");
    check(await until(() => js("document.getElementById('readResult').textContent.includes('All good')")), 'reading check reads the HUD');

    await js("document.querySelector('.nav[data-go=analyst]').click()");
    check(await until(() => js("document.getElementById('anVils').textContent === '10'")), 'live analyst: villagers');

    await js("document.querySelector('.nav[data-go=review]').click()");
    check(await until(() => js("document.getElementById('rvDetail').innerText.includes('Fix next game')")), 'game review: saved game opens');
    check(await js("document.querySelectorAll('#rvChart path').length === 2"), 'game review: chart drawn');
  } catch (e) {
    check(false, 'crashed: ' + (e && e.stack || e));
  }
  fs.writeFileSync(path.join(ROOT, 'e2e_result.txt'), ok ? 'OK' : 'FAIL');
  console.log(ok ? 'ALL PASS' : 'SOME FAILED');
  app.exit(ok ? 0 : 1);
});
