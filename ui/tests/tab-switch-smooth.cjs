// Switching between the main pages on a real hub feels instant: Dana cycles Overview → Strategy → Updates → Tasks →
// Automations → Overview twice on a phone, then a desktop, each API answer 200ms late. The second lap revisits pages the
// app has already drawn, so it paints their last-known content at once: no skeleton is ever on screen in that lap, the
// layout barely moves (cumulative layout shift below 0.02, every shift counted, from a PerformanceObserver installed
// before the first page; and the same score for text that moved between frames, which the browser's metric misses
// when a page redrew its nodes), a page's first frame holds nearly all of its text, and no team list row is redrawn.
// A press while a page change still fades lands on the new page.
// TICO_UI_SHIFTS=1 prints each shift of the second lap with the nodes that moved, and each visit's late text.
const assert = require('node:assert/strict');
const {run, open, go, t} = require('./support/redesign-e2e.cjs');

const LAP = ['#/overview', '#/strategy', '#/updates', '#/tasks', '#/automations'];
const SKELETONS = '.skel, .skel-row, .upd-skel, .ob-skeleton';
const API_DELAY = 200;

// Installed before the app's first script: every layout shift (hadRecentInput too: a page switch is what is measured,
// however it was asked for), fetches in flight, and while __watch is on, each frame's visible skeletons.
function probe(skeletons) {
  window.__shifts = []; window.__skels = []; window.__watch = false; window.__inflight = 0; window.__quietSince = 0;
  const label = n => !n ? '?' : n.nodeType !== 1 ? '#text' : n.tagName.toLowerCase() + (n.id ? '#' + n.id : '')
    + [...n.classList].slice(0, 2).map(c => '.' + c).join('');
  new PerformanceObserver(list => {
    for (const e of list.getEntries()) window.__shifts.push({at: e.startTime, value: e.value, route: location.hash,
      nodes: (e.sources || []).map(s => label(s.node) + ' ' + [s.previousRect, s.currentRect].map(r => [r.x, r.y, r.width, r.height].map(Math.round).join(',')).join('>'))});
  }).observe({type: 'layout-shift', buffered: true});
  const fetch0 = window.fetch;
  window.fetch = (...args) => {
    window.__inflight++;
    return fetch0(...args).finally(() => { if (--window.__inflight === 0) window.__quietSince = performance.now(); });
  };
  // Each frame while __watch is on (the lap number): visible skeletons, and the text on #main's screen. A text that was
  // on screen in the last frame and moved is a shift the browser's metric does not count when the page redrew its
  // nodes (scored like it: the moved area's share of the viewport times the distance's share of the viewport); a text
  // that was not there in a visit's first frame landed late.
  window.__visits = [];
  let visit = null, last = null;
  const leaves = () => {
    const out = new Map(), seen = {}, h = innerHeight;
    for (const el of document.querySelectorAll('#main *')) {
      if (el.firstElementChild || !el.textContent.trim()) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || r.bottom < 0 || r.top > h || !el.checkVisibility({opacityProperty: false})) continue;
      const text = el.textContent.trim().slice(0, 60), n = seen[text] = (seen[text] || 0) + 1;
      out.set(text + '|' + n, r);
    }
    return out;
  };
  const frame = () => {
    if (window.__watch) {
      for (const el of document.querySelectorAll(skeletons))
        if (el.checkVisibility()) window.__skels.push({lap: window.__watch, route: location.hash, cls: el.className, where: label(el.parentElement) + '<' + label(el.parentElement?.parentElement) + ' main=' + document.querySelector('#main').className + ' t=' + Math.round(performance.now())});
      // A page change cross-fades: the old page stays on screen until route() has drawn the new one, so a visit's
      // first frame is the first one route() has drawn.
      const routed = typeof S !== 'undefined' && S.route === location.hash;
      const now = routed ? leaves() : null, vw = innerWidth, vh = innerHeight;
      if (!routed) { /* the old page, still */ }
      else if (!visit || visit.route !== location.hash || visit.lap !== window.__watch) {
        visit = {lap: window.__watch, route: location.hash, first: new Set(now.keys()), shift: 0, moved: [], late: [], keys: 0};
        window.__visits.push(visit);
      } else {
        let area = 0, dist = 0;
        for (const [k, r] of now) {
          const was = last.get(k);
          if (!was) continue;
          const d = Math.max(Math.abs(r.top - was.top), Math.abs(r.left - was.left));
          if (d < 1) continue;
          area += r.width * r.height; dist = Math.max(dist, d);
          if (visit.moved.length < 8) visit.moved.push(k.slice(0, 30));
        }
        visit.shift += Math.min(1, area / (vw * vh)) * Math.min(1, dist / Math.max(vw, vh));
      }
      if (routed) {
        visit.late = [...now.keys()].filter(k => !visit.first.has(k));
        visit.keys = now.size;
      }
      last = now;
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// A page is drawn once no fetch has been in flight for a while and no skeleton shows; a little longer covers what lands late.
async function settle(page) {
  await page.waitForFunction(sel => window.__inflight === 0 && performance.now() - window.__quietSince > 400
    && ![...document.querySelectorAll(sel)].some(el => el.checkVisibility()), SKELETONS, {timeout: t(15000), polling: 100});
  await page.waitForTimeout(250);
}

// Each page's first read has landed and drawn: the first lap waits for it, so the second lap is a true return visit
// (on a slow machine a quiet network alone can come before a page has even asked).
const READ = {
  '#/overview': () => !!DASH?.view && !!document.querySelector('#dash-grid > :not(.skel):not(.skel-row)'),
  '#/strategy': () => !!STRAT && !document.querySelector('#strat-body .skel-row'),
  '#/updates': () => !!document.querySelector('.upd-feed > :not(.upd-skel)'),
  '#/tasks': () => !!TASKS_ST?.read,
  '#/automations': () => Array.isArray(AU?.list),
};

async function lap(page, first = false) {
  for (const hash of [...LAP, LAP[0]]) {
    await go(page, hash);
    await page.waitForFunction(h => location.hash === h && S.route === h && document.querySelector('#main')?.children.length > 0, hash);
    if (first) await page.waitForFunction(READ[hash], null, {timeout: t(15000), polling: 100});
    await settle(page);
  }
}

run('tab-switch-smooth', ['org', 'toured', 'groups', 'automations'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone});
  await page.addInitScript(probe, SKELETONS);
  // A real server is a network away: each API answer arrives a moment late, so a page that waits for its data shows
  // it waiting instead of winning the race against the first frame.
  await page.route('**/api/**', route => setTimeout(() => route.continue().catch(() => {}), API_DELAY));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#main')?.children.length > 0, null, {timeout: t(20000)});
  await settle(page);

  await page.evaluate(() => { window.__watch = 1; });
  await lap(page, true);
  // The team list's rows are marked: a page change toggles which one is current in place and replaces none of them.
  const start = await page.evaluate(() => {
    window.__watch = 2;
    for (const row of document.querySelectorAll('#tree > li')) row.__lap2 = true;
    return performance.now();
  });
  await lap(page);
  const rows = await page.evaluate(() => [...document.querySelectorAll('#tree > li')].map(row => !!row.__lap2));
  const {shifts, skels, visits} = await page.evaluate(at => {
    window.__watch = false;
    return {shifts: window.__shifts.filter(s => s.at >= at), skels: window.__skels.filter(s => s.lap === 2),
      visits: window.__visits.map(v => ({...v, first: undefined}))};
  }, start);
  const cls = shifts.reduce((sum, s) => sum + s.value, 0);
  const sum = lap => visits.filter(v => v.lap === lap).reduce((n, v) => n + v.shift, 0);
  console.log(`${tag}: second lap CLS ${cls.toFixed(4)} over ${shifts.length} shifts; content shift lap 1 ${sum(1).toFixed(4)}, lap 2 ${sum(2).toFixed(4)}`);
  if (process.env.TICO_UI_SHIFTS) {
    for (const s of shifts) console.log(`  ${s.value.toFixed(4)} ${s.route} ${s.nodes.join(' ')}`);
    for (const v of visits) console.log(`  lap ${v.lap} ${v.route}: shift ${v.shift.toFixed(4)} late ${v.late.length}/${v.keys} ${JSON.stringify(v.late.slice(0, 5))} moved ${JSON.stringify(v.moved)}`);
    console.log('  skeletons ' + JSON.stringify(skels.slice(0, 6)));
  }
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  assert.deepEqual([...new Set(skels.map(s => `${s.route} ${s.cls}`))], [], `${tag}: no skeleton on a return visit`);
  assert.ok(cls < 0.02, `${tag}: second lap CLS ${cls.toFixed(4)} < 0.02: ${JSON.stringify(shifts.slice(0, 12))}`);
  assert.ok(sum(2) < 0.02, `${tag}: second lap content shift ${sum(2).toFixed(4)} < 0.02`);
  // A return visit's first frame already holds (nearly) all of the page's text.
  for (const v of visits.filter(v => v.lap === 2))
    assert.ok(v.late.length <= Math.max(2, v.keys / 10), `${tag}: ${v.route} drew ${v.late.length} of ${v.keys} texts after its first frame: ${JSON.stringify(v.late.slice(0, 8))}`);
  assert.ok(rows.length && rows.every(Boolean), `${tag}: no team list row was redrawn by a page change: ${JSON.stringify(rows)}`);

  // Leaving Tasks before its first read answers still keeps that answer: the next visit draws it, no skeleton.
  let release, held = new Promise(r => { release = r; });
  await page.route(/\/api\/v2\/tasks\?lane=company&status=/, async route => { await held; route.continue().catch(() => {}); });
  await page.evaluate(() => { S.lastRead.tasks = null; window.__watch = 3; });
  await go(page, '#/tasks');
  await page.waitForFunction(() => S.route === '#/tasks' && !!document.querySelector('#main .skel, #main .skel-row'));
  await go(page, '#/strategy');
  await page.waitForFunction(() => S.route === '#/strategy');
  release();
  await page.waitForFunction(() => !!S.lastRead.tasks, null, {timeout: t(5000)}).catch(() => {});   // the assert below says why
  await page.unroute(/\/api\/v2\/tasks\?lane=company&status=/);
  await page.evaluate(() => { window.__back = window.__skels.length; });
  await go(page, '#/tasks');
  await page.waitForFunction(() => S.route === '#/tasks');
  await page.waitForTimeout(300);
  const late = await page.evaluate(() => { window.__watch = false; return window.__skels.slice(window.__back).filter(s => s.route === '#/tasks'); });
  assert.deepEqual(late, [], `${tag}: a read that landed after leaving Tasks is drawn on the next visit, not a skeleton`);

  // A press during the cross-fade lands on the new page: 30ms after Updates → Automations, a tap or click on the first
  // automation opens it.
  await go(page, '#/updates');
  await settle(page);
  await page.evaluate(() => { location.hash = '#/automations'; });
  await page.waitForTimeout(30);
  const link = await page.evaluate(() => {
    const fading = !!document.querySelector(':root:active-view-transition');
    const a = [...document.querySelectorAll('#main a[href^="#/automations/"]')].find(a => {
      const r = a.getBoundingClientRect();
      return r.width > 8 && r.height > 8 && r.top > 0 && r.bottom < innerHeight;
    });
    const r = a?.getBoundingClientRect();
    return a && {fading, href: a.getAttribute('href'), x: r.x + Math.min(10, r.width / 2), y: r.y + r.height / 2};
  });
  assert.ok(link?.fading, `${tag}: the press lands while the page change still fades: ${JSON.stringify(link)}`);
  if (phone) await page.touchscreen.tap(link.x, link.y); else await page.mouse.click(link.x, link.y);
  await page.waitForFunction(h => location.hash === h, link.href, {timeout: t(3000)});
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
});
