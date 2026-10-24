// Game icons for every window. Icons: Age of Empires IV © Microsoft Corporation, extracted from the
// game by aoe4world/data and bundled by tools/make_icons.py, used under Microsoft's Game Content
// Usage Rules. Not endorsed by or affiliated with Microsoft.
const GameIcons = (() => {
  let idx = {items: {}, names: {}, landmarks: {}}, list = [];
  const key = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const CIV_WORDS = /[-_](abbasid|ayyubids?|byzantines?|chinese|delhi|english|french|hre|japanese|jin|jeanne|lancaster|macedonian|malians?|mongols?|dragon|ottomans?|rus|sengoku|templar|tughlaq|zhuxi|zxl|vikings|scots|goldenhorde)$/;
  const ready = fetch('img/game/index.json').then(r => r.json()).then(d => {
    idx = d;
    // names to look for in plain text, longest first so "Improved Wheelbarrow" beats "Wheelbarrow"
    list = Object.entries(d.items).map(([id, it]) => [it.n.toLowerCase(), id]).filter(([n]) => n.length >= 4)
      .sort((a, b) => b[0].length - a[0].length);
  }).catch(() => {});

  const item = id => id && idx.items[id] ? {id, ...idx.items[id], src: 'img/game/' + idx.items[id].f} : null;
  function byName(n){
    const k = key(n);
    return item(idx.names[k] || (k.endsWith('s') ? idx.names[k.slice(0, -1)] : null) || (k.endsWith('es') ? idx.names[k.slice(0, -2)] : null));
  }
  // an aoe4guides icon ("/assets/pictures/technology_economy/wheelbarrow.webp") -> our icon
  function fromGuides(ic){
    const m = String(ic.src || ic.icon || '').match(/\/([a-z0-9_\-]+)\.\w+(\?.*)?$/i);
    let n = m ? m[1].replace(/^(resource_|age_|icon_)/, '').replace(/-\d+$/, '').replace(CIV_WORDS, '') : '';
    return byName(n) || byName(ic.title || '');
  }
  // every known unit/tech/building named in some text, in the order they appear
  function find(text, max = 4){
    const t = ' ' + String(text || '').toLowerCase() + ' ', hits = [];
    for (const [n, id] of list) {
      const at = t.search(new RegExp('[^a-z]' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + 's?[^a-z]'));
      if (at >= 0 && !hits.some(h => at < h.end && at + n.length > h.at)) hits.push({at, end: at + n.length, id});
    }
    return hits.sort((a, b) => a.at - b.at).slice(0, max).map(h => item(h.id));
  }
  // <span class="gi sm k-t"><img></span>; kinds tint the tile: u unit, t tech, b building, g upgrade
  function html(it, size = 'sm', extra = ''){
    if (!it) return '';
    return `<span class="gi ${size} k-${it.k || 'x'} ${extra}" title="${esc(it.n || it.title || '')}"><img src="${esc(it.src)}" alt="${esc(it.n || it.title || '')}" loading="lazy" onerror="this.parentNode.remove()"></span>`;
  }
  // an icon tag from a build step: ours when we have it, else the build site's own picture
  function step(ic, size = 'xs'){
    if (/resource_(food|wood|gold|stone)/.test(ic.src || '')) return '';    // resources have their own colours
    const it = fromGuides(ic);
    return it ? html(it, size) : (ic.src ? html({src: ic.src, n: ic.title, k: 'x'}, size) : '');
  }
  // the most telling icon of a step: what to build or research, not the villagers
  function key_of(icons){
    for (const ic of icons || []) {
      if (/resource_|villager|sheep|berry|deer|boar|scout|rally/i.test((ic.src || '') + ' ' + (ic.title || ''))) continue;
      const it = fromGuides(ic);
      if (it) return it;
    }
    return null;
  }
  const AGE_NUM = {1: 'I', 2: 'II', 3: 'III', 4: 'IV'};
  // an age shield, drawn (the game's own age icons aren't in the extracted set)
  function age(n, size = 28, cls = ''){
    n = +n || 1;
    return `<span class="age-shield a${n} ${cls}" style="width:${size}px;height:${size * 1.15}px" title="${['', 'Dark', 'Feudal', 'Castle', 'Imperial'][n]} Age">
      <svg viewBox="0 0 40 46" aria-hidden="true"><defs><linearGradient id="ag${n}${size}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="s1"/><stop offset="1" class="s2"/></linearGradient></defs>
      <path d="M20 2 37 8v14c0 11-7.5 18.5-17 22C10.5 40.5 3 33 3 22V8z" fill="url(#ag${n}${size})" stroke="rgba(255,255,255,.55)" stroke-width="1.5"/>
      <path d="M20 5.5 33.5 10.3V22c0 9-6 15.3-13.5 18.5" fill="none" stroke="rgba(255,255,255,.22)" stroke-width="1.2"/>
      <text x="20" y="${n === 3 ? 28.5 : 29}" text-anchor="middle">${AGE_NUM[n]}</text></svg></span>`;
  }
  const landmarks = (civ, a) => ((idx.landmarks[civ] || {})[String(a)] || []).map(item).filter(Boolean);
  // A build step as clear actions: [{it, verb, text, builders}] ("Build a Mining Camp", 1 villager).
  // From the build's own icons, else from the units/buildings/techs its text names. Villagers,
  // scouts, sheep and resources aren't actions (the villager call covers them).
  const SKIP = /^(villager|scout|sheep|berry bush|berries|deer|boar|wolf|fish)$/i;
  const VERB = {b: 'Build', t: 'Research', g: 'Research', u: 'Train'};
  const article = n => /s$/.test(n) && !/(ss|us)$/.test(n) ? '' : /^[aeiou]/i.test(n) ? 'an ' : 'a ';
  function actions(st, max = 2){
    if (!st) return [];
    const seen = new Set(), out = [];
    const add = it => {
      if (!it || !it.k || it.k === 'x' || SKIP.test(it.n) || seen.has(it.id)) return;
      seen.add(it.id);
      const verb = VERB[it.k] || 'Make';
      out.push({it, verb, text: `${verb} ${verb === 'Research' ? '' : article(it.n)}${it.n}`});
    };
    for (const ic of st.icons || []) if (!/resource_/.test(ic.src || '')) add(fromGuides(ic));
    if (!out.length) for (const it of find(st.text, 6)) add(it);
    const b = out.find(a => a.it.k === 'b');
    if (b && st.builders) b.builders = st.builders;
    return out.slice(0, max);
  }
  return {ready, byName, fromGuides, find, html, step, key: key_of, age, landmarks, item, actions};
})();
