// Unit tests for the page's own helpers (outlier-radar/static/index.html), run on the real code: each helper is cut out of the
// page by name and evaluated in a sandbox with small stand-ins for translation, formatting and the clock. No dependencies.
// Run: node web/scanner/page_tests.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const page = fs.readFileSync(path.join(ROOT, 'outlier-radar', 'static', 'index.html'), 'utf8');

// Cut one top-level `const NAME = ...;` or `function NAME(...) {...}` out of the page, skipping strings, templates, comments and regexes.
function cut(name) {
  const m = new RegExp(`\\n((?:async )?function ${name}\\(|const ${name} = )`).exec(page);
  if (!m) throw new Error(`${name} not found in the page`);
  const start = m.index + 1, isFn = m[1].includes('function');
  let i = start, depth = 0, opened = false;
  const tmpl = [];   // brace depth at which each open template's ${ began
  const regexOk = () => { let j = i - 1; while (j > start && /\s/.test(page[j])) j--; return /[(,=:[!&|?{};+\-*%<>~^]/.test(page[j]) || /\breturn$/.test(page.slice(Math.max(start, j - 6), j + 1)); };
  while (i < page.length) {
    const ch = page[i], nx = page[i + 1];
    if (ch === '/' && nx === '/') { i = page.indexOf('\n', i); continue; }
    if (ch === '/' && nx === '*') { i = page.indexOf('*/', i + 2) + 2; continue; }
    if (ch === "'" || ch === '"') { i++; while (page[i] !== ch) i += page[i] === '\\' ? 2 : 1; i++; continue; }
    if (ch === '`' || (ch === '}' && tmpl.length && tmpl[tmpl.length - 1] === depth)) {   // template text runs until ` or ${
      if (ch === "}") tmpl.pop();
      i++;
      while (page[i] !== '`' && !(page[i] === '$' && page[i + 1] === '{')) i += page[i] === '\\' ? 2 : 1;
      if (page[i] === '$') { tmpl.push(depth); depth++; i += 2; continue; }
      i++; continue;
    }
    if (ch === '/' && regexOk()) {   // a regex literal
      i++; let cls = false;
      while (cls || page[i] !== '/') { if (page[i] === '\\') i++; else if (page[i] === '[') cls = true; else if (page[i] === ']') cls = false; i++; }
      i++; continue;
    }
    if ('([{'.includes(ch)) { depth++; if (ch === '{') opened = true; }
    else if (')]}'.includes(ch)) {
      depth--;
      if (isFn && opened && depth === 0 && ch === '}') return page.slice(start, i + 1);
    } else if (!isFn && ch === ';' && depth === 0) return page.slice(start, i + 1);
    i++;
  }
  throw new Error(`could not find the end of ${name}`);
}

let NOW = Date.UTC(2026, 9, 29, 12) / 1000;
const ctx = {
  NOW_: () => NOW, DATA: null, STATIC: true, S: {detail: false},
  t: (k, v) => k + (v ? JSON.stringify(v) : ''), esc: s => String(s), fmt: n => String(n), pct: x => Math.round(x * 100) + '%', ago: ts => `ago(${Math.round((NOW - ts) / 3600)}h)`,
  whyLines: () => ['why one', 'why two'], Intl, Date, Math, JSON, WeakMap, Set, Map,
};
vm.createContext(ctx);
vm.runInContext('const nowTs = () => NOW_();', ctx);
for (const name of ['speed', 'grow', 'young', 'when', 'accelerating', 'subStep', 'factCache', 'chAgeDays', 'chFacts', 'trendStage', 'weakSignal',
  'topShare', 'freshShare', 'statusInfo', 'takeaway', 'chRisks', 'nextPacificMidnight', 'coolingOf', 'median', 'SERIES_RX', 'FMT_HABITS', 'formatFacts', 'vidOk', 'BD_STAGES', 'bdStageIdx', 'vidFromLink', 'bdFunnel', 'bdResult']) vm.runInContext(cut(name), ctx);
const run = src => vm.runInContext(src, ctx);
const H = 3600, D = 86400;
let n = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); n++; };

// views per hour since upload: an average, only for Shorts under 3 days old
ctx.v = {v: 50000, p: NOW - 10 * H};
ok(run('speed(v)') === 5000, 'speed: 50,000 views in 10 h = 5,000 an hour');
ctx.v = {v: 50000, p: NOW - 80 * H};
ok(run('speed(v)') === 0, 'speed: nothing for Shorts over 3 days old');
ctx.v = {v: 50000};
ok(run('speed(v)') === 0, 'speed: nothing without an upload time');
ctx.v = {v: 500, p: NOW - 600};
ok(run('speed(v)') === 500, 'speed: the first hour counts as one hour, not a fraction');

// views per hour between two checks: shown only while both checks are recent
ctx.v = {g: 6000, h: 3, gAt: NOW - H};
ok(run('grow(v)') === 2000, 'grow: 6,000 views over 3 h = 2,000 an hour');
ctx.v = {g: 6000, h: 13, gAt: NOW - H};
ok(run('grow(v)') === 0, 'grow: checks more than 12 h apart are not shown');
ctx.v = {g: 6000, h: 3, gAt: NOW - 7 * H};
ok(run('grow(v)') === 0, 'grow: a measurement older than 6 h is not shown');
ctx.v = {g: 0, h: 3, gAt: NOW - H};
ok(run('grow(v)') === 0, 'grow: no growth, no badge');

// too new to judge
ok(run(`young({p: ${NOW - 3 * D}})`) === true && run(`young({p: ${NOW - 8 * D}})`) === false && run('young({})') === false, 'young: under a week, with an upload time');

// speeding up: the latest interval is 1.5x the one before, at least 200 an hour, and recent
const vh = (shift, c) => [[NOW - 8 * H - shift, 10000], [NOW - 5 * H - shift, 13000], [NOW - H - shift, c]];
ctx.v = {vh: vh(0, 20000)};
ok(run('accelerating(v)') === true, 'accelerating: 1,000/h then 1,750/h');
ctx.v = {vh: vh(2 * D, 20000)};
ok(run('accelerating(v)') === false, 'accelerating: the same curve two days ago is not shown as current');
ctx.v = {vh: vh(0, 17000)};
ok(run('accelerating(v)') === false, 'accelerating: 1,000/h then 1,000/h is not speeding up');
ctx.v = {vh: [[NOW - 5 * H, 0], [NOW - H, 100]]};
ok(run('accelerating(v)') === false, 'accelerating: needs three checks');

// YouTube's rounding of public subscriber counts
ok(run('subStep(1234000)') === 10000 && run('subStep(56700)') === 100 && run('subStep(999)') === 1, 'subStep: one rounding step at each size');

// channel age and "new to Shorts"
const series = days => days.map((d, i) => ({id: 'v' + i, v: 1000, p: NOW - d * D}));
ctx.c = {created: NOW - 30 * D, series: series([2, 5, 9])};
let f = run('chFacts(c)');
ok(f.isNew && Math.round(f.days) === 30 && !f.newShorts, 'chFacts: created 30 days ago is a new channel');
ctx.c = {created: NOW - 400 * D, series: series([2, 9, 20, 39.5])};
f = run('chFacts(c)');
ok(!f.isNew && Math.round(f.days) === 400, 'chFacts: a 400-day-old channel is not new');
ctx.c = {series: series([2, 9, 20, 39.5])};
f = run('chFacts(c)');
ok(f.newShorts && f.shortDays === 40 && !f.isNew, 'chFacts: no creation date, all Shorts recent = new to Shorts (40 days)');
ctx.c = {series: series([2, 30, 70, 100])};
ok(run('chFacts(c)').newShorts === false, 'chFacts: Shorts going back 100 days are not new');

// trend stages and weak signals
ok(run("trendStage({chs: new Set([1, 2, 3]), r: 30})") === 'early', 'stage: 3 channels is early, however many Shorts');
ok(run("trendStage({chs: new Set([1, 2, 3, 4, 5, 6]), r: 20})") === 'crowded', 'stage: 6 channels and 20 Shorts is crowded');
ok(run("trendStage({chs: new Set([1, 2, 3, 4, 5, 6]), r: 3, cool: {peak: 12, at: 6, now: 3}})") === 'cooling', 'stage: a trend marked cooling says so');

// Cooling: a busy 3 days, then its share of each day's new Shorts halves; shares, so thinner old history can't fake it
const flat = (n, v) => new Array(n).fill(v);
const cool = (days, tot) => run(`coolingOf(${JSON.stringify(days)}, ${JSON.stringify(tot)})`);
ok(JSON.stringify(cool([1, 1, 1, 1, 1, 1, 1, 6, 6, 6, 1, 1, 1, 1], flat(14, 100))) === '{"peak":18,"at":7,"now":3}', 'cooling: 18 Shorts in 3 days, now 3, is cooling');
ok(cool(flat(14, 5), flat(14, 100)) === null, 'cooling: a steady topic is not cooling');
ok(cool([0, 0, 0, 0, 0, 0, 0, 6, 6, 6, 5, 5, 5, 5], flat(14, 100)) === null, 'cooling: still nearly as busy as its peak is not cooling');
ok(cool([0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 0, 0, 0, 0], flat(14, 100)) === null, 'cooling: a peak under 8 Shorts is too small to call');
ok(cool([2, 2, 2, 2, 2, 2, 2, 4, 4, 4, 4, 4, 4, 4], flat(7, 50).concat(flat(7, 100))) === null, 'cooling: the same share on thinner old days is not a spike');
ok(cool([0, 0, 0, 0, 0, 0, 0, 0, 0, 8, 8, 8, 8, 8], flat(14, 100)) === null, 'cooling: a trend still at its peak is not cooling');
ok(run("trendStage({chs: new Set([1, 2, 3, 4, 5, 6]), r: 10})") === 'spread', 'stage: 6 channels and 10 Shorts is spreading');
ok(run("weakSignal({chs: new Set([1, 2, 3, 4, 5]), r: 5})") && !run("weakSignal({chs: new Set([1, 2, 3, 4, 5]), r: 10})"), 'weak signal: under 8 Shorts');

// Format DNA: lengths, rhythm and title habits, from metadata only
const words2 = x => { const w = x.toLowerCase().split(/\s+/).filter(Boolean), out = new Set(w); w.forEach((a, i) => { if (w[i + 1]) out.add(a + ' ' + w[i + 1]); }); return out; };
ctx.words2 = words2;
ctx.vids = [12, 15, 20, 25, 30, 35, 40, 58].map((d, i) => ({d, v: [9e5, 8e5, 7e5, 1e4, 1e4, 1e4, 1e4, 1e4][i], p: NOW - i * 2 * D,
  t: ['Tom and Jerry vfx magic part 1', 'Tom and Jerry vfx magic part 2', 'Can you spot it?', 'Tom and Jerry vfx magic #shorts', 'Tom and Jerry vfx magic 10kg', 'Why cats sit like this?', 'Day 3 of my build', 'Wait for it 😂'][i]}));
let fx = run('formatFacts(vids, words2)');
ok(fx.lenMed === 27.5 && fx.lenLo === 20 && fx.lenHi === 35, 'format: length median and middle half (20–35 s)');
ok(fx.topLen === 15, 'format: the 3 most-viewed run 15 s typically');
ok(Math.abs(fx.perWeek - 3.5) < 0.01, 'format: 8 uploads over 14 days is 3.5 a week');
ok(fx.days.reduce((a, b) => a + b, 0) === 8, 'format: every upload lands on a weekday');
const hb = Object.fromEntries(fx.habits);
ok(hb.fmtQ === 0.25 && hb.fmtSeries === 0.375 && !('fmtEmoji' in hb), 'format: 2 of 8 titles are questions, 3 are numbered parts, 1 emoji is under the 20% cut');
ok(fx.repeat && fx.repeat.w === 'tom and jerry vfx magic', 'format: overlapping phrases join into the series name');
ok(fx.repeat.k === 4 && fx.repeat.n === 8, 'format: the series name is in 4 of 8 titles');
ok(run('formatFacts([{d: 20, t: "a", v: 1}], words2)') === null, 'format: one Short is too few to say anything');

// the idea pipeline: links, how far ideas got, and a published Short against the creator's usual
ok(run("vidFromLink('https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share')") === 'dQw4w9WgXcQ', 'pipeline: a Shorts link gives its video id');
ok(run("vidFromLink('https://youtu.be/dQw4w9WgXcQ')") === 'dQw4w9WgXcQ' && run("vidFromLink('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3')") === 'dQw4w9WgXcQ', 'pipeline: youtu.be and watch links work too');
ok(run("vidFromLink('dQw4w9WgXcQ')") === 'dQw4w9WgXcQ' && run("vidFromLink('not a link')") === '', 'pipeline: a bare id works, anything else gives nothing');
ok(JSON.stringify(run("bdFunnel([{stage: 'idea'}, {}, {stage: 'film'}, {stage: 'done'}])")) === '[4,2,2,1,1]', 'pipeline: the funnel counts ideas that reached each step');
let rs = run('bdResult({v: 12000, p: NOW_() - 10 * 86400, at: NOW_()}, 4000)');
ok(rs.x === 3 && Math.round(rs.days) === 10 && !rs.young, 'pipeline: 12K views at 10 days is 3x a usual of 4K');
rs = run('bdResult({v: 900, p: NOW_() - 2 * 86400, at: NOW_()}, 4000)');
ok(rs.young && rs.x < 1, 'pipeline: a 2-day-old Short is flagged as too young to judge');
ok(run('bdResult({v: 5000, at: NOW_()}, null)').x === null && run('bdResult(null, 4000)') === null, 'pipeline: no usual means no comparison; no count means no result');

// the status badge: late once well past the usual gap between refreshes
const status = (ageH, gapH) => { ctx.DATA = {generated: NOW - ageH * H, status: {run_gap_h: gapH}, channels: [{last_probe: NOW - H}]}; return run('statusInfo()'); };
ok(!status(2, 3).late, 'status: 2 h old with a 3 h rhythm is fine');
ok(status(10, 3).late && status(10, 3).cls === 'dot snap' && status(10, 3).txt.includes('stLate'), 'status: 10 h old with a 3 h rhythm is late');
ok(!status(10, 5).late, 'status: 10 h old with a 5 h rhythm is still within 2.5 gaps');
ok(status(7, 0).late, 'status: without a known rhythm, late after 6 h');

// the channel takeaway and risks
const ch = (o, views) => Object.assign({subs: 50000, ratio: 30, trend: 1.2, tags: [], settled_n: 12, last_probe: NOW - H, series: views.map((v, i) => ({id: 'v' + i, v, p: NOW - (8 + i) * D}))}, o);
ctx.c = ch({tags: ['One-hit']}, [9e6, 20000, 15000, 12000, 30000, 25000, 10000, 18000]);
ok(run('takeaway(c)').head === 'tkOneHit', 'takeaway: one big hit and nothing else is called that');
ctx.c = ch({tags: ['One-hit']}, [9e7, 2e6, 3e6, 4e6, 2e6, 5e6, 3e6, 2e6]);
ok(run('takeaway(c)').head === 'tkSmallBig', 'takeaway: a giant hit on top of repeated hits is not "one big hit"');
ctx.c = ch({subs: 0, ratio: null, trend: null, settled_n: 3}, [1e5, 2e5, 3e5]);
const risks = run('chRisks(c, {isNew: false, newShorts: true})').join(' | ');
ok(risks.includes('rkFew') && risks.includes('rkHidden') && risks.includes('rkNew') && risks.includes('rkSample'), 'risks: few settled Shorts, hidden subscribers, short history, sample size');
ctx.c = ch({last_probe: NOW - 3 * D}, [1e5, 2e5, 3e5, 4e5, 5e5, 6e5, 7e5, 8e5]);
ok(run('chRisks(c, {})').join(' ').includes('rkStale'), 'risks: a channel last checked 3 days ago says so');

// YouTube's daily quota resets at midnight Pacific time, shown in the visitor's own time zone
for (const iso of ['2026-10-29T12:00:00Z', '2026-11-01T09:30:00Z', '2026-03-08T11:00:00Z', '2026-12-31T23:59:00Z']) {   // incl. both daylight-saving changes
  const real = Date;
  ctx.Date = class extends real { constructor(...a) { super(...(a.length ? a : [iso])); } static now() { return new real(iso).getTime(); } };
  vm.runInContext(cut('nextPacificMidnight'), ctx);
  const ms = run('nextPacificMidnight()'), clock = new Intl.DateTimeFormat('en-US', {timeZone: 'America/Los_Angeles', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(new real(ms));
  ok(clock === '00:00' && ms > new real(iso).getTime() && ms - new real(iso).getTime() <= 25 * H * 1000, `Pacific midnight after ${iso} (got ${clock})`);
  ctx.Date = real;
}

console.log(`page helpers OK: ${n} checks (views per hour, growth, speeding up, rounding, channel age, stages, late refresh, takeaway, risks, Pacific midnight)`);
