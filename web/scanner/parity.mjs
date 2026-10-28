// Checks that the page and the scanner agree: the scoring (computeMetrics in index.html vs compute_metrics in app.py) and the trend rules
// (index.html vs trends.py: how titles are split into words, which words count, how a channel's history is weighted, the lift, "hot").
// Run: node web/scanner/parity.mjs   (needs Python 3 on the PATH as python or python3)
import {readFileSync, writeFileSync, mkdtempSync} from 'node:fs';
import {join, dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..', '..');
const html = readFileSync(join(root, 'outlier-radar', 'static', 'index.html'), 'utf8');
const block = name => {
  const a = html.indexOf(`/* @${name}-start */`), b = html.indexOf(`/* @${name}-end */`);
  assert.ok(a > 0 && b > a, `marker ${name} not found in index.html`);
  return html.slice(a, b);
};
const words = /<script type="application\/json" id="trendWords">(.*?)<\/script>/s.exec(html)[1];
const ctx = {document: {getElementById: () => ({textContent: words})}, nowTs: () => Date.now() / 1000};
vm.createContext(ctx);
vm.runInContext(block('metrics') + block('trend-rules') +
  ';globalThis.api = {computeMetrics, splitTitle, termsOf, capsIn, coverageWeight, liftOf, isHot, trendRef, GAMES: TW.games, CARTOONS: TW.cartoons};', ctx);
const js = ctx.api;

// ---------- fixtures (a fixed pseudo-random sequence, so every run checks the same cases) ----------
let seed = 20261028;
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const now = 1793100000;
const metricCases = [];
for (let i = 0; i < 400; i++) {
  const n = 4 + Math.floor(rnd() * 21), dated = rnd() > 0.15, vids = [];
  let age = rnd() * 3 * 86400;
  for (let j = 0; j < n; j++) {
    age += rnd() * 4 * 86400;
    const v = Math.floor(10 ** (3 + rnd() * 4.5));
    vids.push(dated || rnd() > 0.9 ? {id: `v${j}`, v, p: Math.round(now - age)} : {id: `v${j}`, v});
  }
  metricCases.push({vids, subs: rnd() < 0.08 ? 0 : Math.floor(10 ** (3 + rnd() * 4))});
}
const titles = [
  'I’m so done with mom yelling 😂', 'POV: Mario finds a #Labubu at 3AM', 'बच्चे का ऐसा जुगाड़ देखा नहीं होगा #जुगाड़', 'Steal a Brainrot but every hit = +1 speed #roblox #shorts',
  'ทดสอบ ภาษาไทย #ไวรัล #ไทย', 'ROBLOX BUT IT IS ALL CAPS #FYP', 'What if you dropped a T-Rex into Tokyo? 🦖🏙️', '1️⃣ 2️⃣ 3️⃣ ranking every Sprunki',
  'Mbappé vs Haaland: who’s faster? ⚽', '¿Qué pasa si comes esto? #comida', 'Почему кошки боятся огурцов? #кот', 'لماذا يحب القطط الصناديق؟',
  'カービィの新しいゲーム #ゲーム', '"Don\'t" do this at home — it\'s dangerous', "Grandma's secret recipe ''quoted''", 'snake_case_title #with_under_score',
  'Labubu Labubu LABUBU labubu', 'The Amazing Digital Circus but Pomni is the boss', 'Grow a Garden 99 Nights in the Forest crossover', 'Ich habe das getestet, damit du es nicht musst',
  'Tôi đã thử món ăn này và ...', 'Hindi: मैं हूँ ना, ये क्या है?', 'emoji‍zwj 👨‍👩‍👧 family', 'NBA 2K26 vs EA FC 26 🎮', 'Skibidi Toilet episode 99 #skibidi'
];
const vocab = ['mario', 'roblox', 'brainrot', 'labubu', 'sprunki', 'cat', 'dog', 'secret', 'giant', 'crab', 'beach', 'kitchen', 'hack', 'prank', 'doctor', 'zombie', 'race', 'mom', 'yelling', 'skibidi'];
const channels = [];
for (let c = 0; c < 40; c++) {
  const many = rnd() < 0.3, n = many ? 24 : 6 + Math.floor(rnd() * 15), step = many ? 0.15 + rnd() * 1.2 : 1 + rnd() * 3, series = [];
  for (let j = 0; j < n; j++) {
    const words3 = Array.from({length: 3}, () => vocab[Math.floor(rnd() * vocab.length)]);
    const title = (rnd() < 0.5 ? 'POV ' : '') + words3.join(' ') + (rnd() < 0.4 ? ' #' + vocab[Math.floor(rnd() * vocab.length)] : '') + (rnd() < 0.2 ? ' Mario' : '');
    series.push({id: `c${c}v${j}`, t: title, v: Math.floor(10 ** (3 + rnd() * 4)), p: Math.round(now - (j * step + rnd()) * 86400)});
  }
  channels.push({ch_id: 'UC' + String(c).padStart(22, '0'), series, last_probe: Math.round(now - (rnd() < 0.15 ? 3 * 86400 : rnd() * 30 * 3600))});
}
const trendData = {generated: now, channels};

// ---------- the page's side ----------
const jsMetrics = metricCases.map(m => js.computeMetrics(JSON.parse(JSON.stringify(m.vids)), m.subs, now));
const jsTerms = titles.map(t => [...js.termsOf(t)].sort());
const jsCaps = titles.map(t => { const c = js.capsIn(t); return [c.plain, [...c.caps].sort()]; });
const jsRefs = channels.map(c => js.trendRef(c, now));
const jsWeights = channels.map((c, i) => (jsRefs[i] == null ? null : js.coverageWeight(c.series, jsRefs[i])));
const stats = new Map(), boards = js.GAMES.map(([n, p]) => ['g:' + n, new RegExp(p, 'i')]).concat(js.CARTOONS.map(([n, p]) => ['c:' + n, new RegExp(p, 'i')]));
const add = (key, age, ch, w) => { const s = stats.get(key) || [0, 0, new Set()]; if (age <= 3 * 86400) { s[0]++; s[2].add(ch); } else s[1] += w; stats.set(key, s); };
channels.forEach((c, i) => {
  const w = jsWeights[i];
  if (w == null) return;
  for (const v of c.series) {
    const age = jsRefs[i] - v.p;
    if (age < 0 || age > 21 * 86400) continue;
    for (const term of js.termsOf(v.t)) add('t:' + term, age, c.ch_id, w);
    for (const [key, rx] of boards) if (rx.test(v.t)) add(key, age, c.ch_id, w);
  }
});
const jsHot = {};
for (const [key, [r, b, chs]] of stats) { const lift = js.liftOf(r, b); if (js.isHot(r, chs.size, lift)) jsHot[key] = [r, Math.round(lift * 10) / 10, key.slice(2)]; }

// ---------- the scanner's side ----------
const dir = mkdtempSync(join(tmpdir(), 'parity-'));
writeFileSync(join(dir, 'fixtures.json'), JSON.stringify({now, metricCases, titles, channels}));
writeFileSync(join(dir, 'data.json'), JSON.stringify(trendData));
writeFileSync(join(dir, 'run.py'), `
import json, sys, importlib.util
sys.path.insert(0, ${JSON.stringify(here)})
import trends
spec = importlib.util.spec_from_file_location("radar", ${JSON.stringify(join(root, 'outlier-radar', 'app.py'))})
radar = importlib.util.module_from_spec(spec); spec.loader.exec_module(radar)
fx = json.load(open(sys.argv[1], encoding="utf-8"))
out = {"metrics": [radar.compute_metrics(m["vids"], m["subs"], fx["now"]) for m in fx["metricCases"]],
       "terms": [sorted(trends.title_terms(t)) for t in fx["titles"]],
       "caps": [[c[0], sorted(c[1])] for c in (trends.caps_in(t) for t in fx["titles"])],
       "weights": [None if trends.trend_ref(c, fx["now"]) is None else trends.coverage_weight(c["series"], trends.trend_ref(c, fx["now"])) for c in fx["channels"]],
       "hot": trends.hot_trends(sys.argv[2], fx["now"])}
sys.stdout.write(json.dumps(out, ensure_ascii=False))
`);
let py = null;
for (const exe of ['python', 'python3']) {
  const r = spawnSync(exe, [join(dir, 'run.py'), join(dir, 'fixtures.json'), join(dir, 'data.json')], {encoding: 'utf8', env: {...process.env, PYTHONIOENCODING: 'utf-8'}, maxBuffer: 64e6});
  if (r.status === 0) { py = JSON.parse(r.stdout); break; }
  if (r.error && r.error.code === 'ENOENT') continue;
  throw new Error(r.stderr || 'python failed');
}
assert.ok(py, 'Python 3 is needed on the PATH');

// ---------- compare ----------
const near = (a, b, tol, what) => {
  if (a == null || b == null) return assert.equal(a, b, what);
  assert.ok(Math.abs(a - b) <= tol, `${what}: page ${a} vs scanner ${b}`);
};
let checked = 0;
jsMetrics.forEach((a, i) => {
  const b = py.metrics[i], what = `metrics case ${i}`;
  if (a == null || b == null) { assert.equal(a, b, what); return; }
  for (const k of ['n', 'base_n', 'settled_n', 'mom_n']) assert.equal(a[k], b[k], `${what} ${k}`);
  for (const k of ['med_recent', 'med_all', 'max_views']) near(a[k], b[k], 1e-6 * Math.max(1, b[k]), `${what} ${k}`);
  for (const [k, tol] of [['share_100k', 0.011], ['share_1m', 0.011], ['trend', 0.011], ['peak_vs_med', 0.11], ['ratio', 0.11], ['score', 0.11], ['top_share', 0.0011]]) near(a[k], b[k], tol, `${what} ${k}`);
  assert.deepEqual([...a.tags], b.tags, `${what} tags`);
  checked++;
});
titles.forEach((t, i) => {
  assert.deepEqual(jsTerms[i], py.terms[i], `terms of "${t}"`);
  assert.deepEqual(jsCaps[i], py.caps[i], `capitals in "${t}"`);
});
jsWeights.forEach((w, i) => near(w, py.weights[i], 1e-9, `coverage weight of channel ${i}`));
assert.deepEqual(Object.keys(jsHot).sort(), Object.keys(py.hot).sort(), 'the same trends are hot');
for (const [key, [r, lift, label]] of Object.entries(jsHot)) {
  assert.equal(r, py.hot[key][0], `${key} recent count`);
  near(lift, py.hot[key][1], 0.051, `${key} lift`);
  assert.equal(label, py.hot[key][2]);
}
console.log(`parity OK: ${checked} scoring cases, ${titles.length} titles, ${channels.length} channel weights and ${Object.keys(jsHot).length} hot trends agree between the page and the scanner`);
