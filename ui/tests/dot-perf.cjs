// The Liquid Dot stays cheap (ui/app/liquid-dot.js, styles/liquid-dot.css; budgets in docs/assistant.md). Chrome over
// CDP, fixtures only:
// - Idle 10 s with Ask closed: no layout, at most 4 style recalcs, under 150 ms of main-thread work, no paints from
//   the dot, and every running animation on a dot moves transform or opacity only, on one dot at most.
// - With the CPU slowed 4x, opening Ask (the dot flies in), a reply (it flies to the new turn) and closing (it flies
//   back): frame gaps p95 <= 20 ms and max <= 50 ms, and no long task over 50 ms. On a loaded runner
//   (TICO_UI_SLOWDOWN > 1) the frame budgets stretch by that factor; the counts never do.
// - A hidden tab: every dot paused and no idle timer left, and none set while hidden.
// DOT_PERF_OUT=<file> writes the numbers (the PR body quotes them).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {SLOWDOWN} = require('./support/load.cjs');

const now = new Date().toISOString();
const msg = (id, from, body) => ({id, from_actor: from, body, refs: {}, created: now, kind: 'say'});
const IDLE_MS = 10000;
const DOT_ANIMATED = new Set(['transform', 'opacity', 'offset', 'easing', 'composite', 'computedOffset']);

async function open(browser) {
  const state = {messages: [msg('a1', 'human:ben', 'Where is the launch plan?'), msg('a2', 'bot:coo', 'In Docs.')], sent: 0};
  const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ben', role: 'human', name: 'Ben', email: 'ben@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ben', count: 0, version: 'v0', items: []});
    if (p === '/api/v2/assistant' && method === 'GET') return json({available: true, state: 'active', bot: 'coo', name: 'Assistant',
      can_turn_on: false, room_id: 'room1', messages: state.messages, has_more: false, next_before: null, execution: null, actions: {}, pending: []});
    if (p === '/api/v2/assistant/messages') {
      const n = ++state.sent, user = msg('u' + n, 'human:ben', JSON.parse(route.request().postData()).text), bot = msg('b' + n, 'bot:coo', 'On it.');
      state.messages.push(user, bot);
      return json({message: user, reply: bot, fast: true});
    }
    if (p === '/api/v2/support/tickets' || p === '/api/v2/support/tickets/refresh') return json({enabled: true, tickets: [], unread: 0});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks');
  await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
  await page.locator('#ask-fab').waitFor();
  return {page, errors};
}
const metrics = async cdp => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
// The dots' running animations: which dots move, and every property any of them animates.
const dotAnimations = page => page.evaluate(() => {
  const running = document.getAnimations().filter(a => a.playState === 'running' && a.effect?.target?.closest?.('.ldot, .ask-fly'));
  const props = new Set(running.flatMap(a => a.effect.getKeyframes().flatMap(k => Object.keys(k))));
  const dots = new Set(running.map(a => a.effect.target.closest('.ldot')).filter(d => d && !d.closest('[data-paused]') && !d.matches('[data-paused]')));
  return {props: [...props], dots: dots.size};
});
// Frame gaps and long tasks while `act` runs.
async function frames(page, act) {
  await page.evaluate(() => {
    const f = window.__frames = {gaps: [], long: []};
    let last = performance.now();
    const tick = t => { f.gaps.push(t - last); last = t; if (f.on) requestAnimationFrame(tick); };
    f.on = true; requestAnimationFrame(t => { last = t; requestAnimationFrame(tick); });
    f.obs = new PerformanceObserver(list => list.getEntries().forEach(e => f.long.push(e.duration)));
    f.obs.observe({type: 'longtask'});
  });
  await act();
  await page.waitForTimeout(300);
  return page.evaluate(() => {
    const f = window.__frames; f.on = false; f.obs.disconnect();
    const gaps = f.gaps.slice(1).sort((a, b) => a - b);
    return {frames: gaps.length, p95: gaps[Math.floor(gaps.length * .95)] || 0, max: gaps.at(-1) || 0, long: Math.max(0, ...f.long)};
  });
}
const round = v => Math.round(v * 10) / 10;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const out = {};
  try {
    const {page, errors} = await open(browser);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    await page.waitForTimeout(1500);                       // the page settles: first draws, fonts, support's read

    // ---- Idle, Ask closed.
    const before = await metrics(cdp);
    await browser.startTracing(page, {categories: ['devtools.timeline']});
    await page.waitForTimeout(IDLE_MS);
    const after = await metrics(cdp);
    const trace = JSON.parse((await browser.stopTracing()).toString());
    const sampled = [await dotAnimations(page)];       // after: reading animations recalcs style itself
    const events = (trace.traceEvents || trace).filter(e => e.ph === 'X' || e.ph === 'B');
    const idle = {layouts: after.LayoutCount - before.LayoutCount, recalcs: after.RecalcStyleCount - before.RecalcStyleCount,
      taskMs: round((after.TaskDuration - before.TaskDuration) * 1000), paints: events.filter(e => e.name === 'Paint').length,
      props: [...new Set(sampled.flatMap(s => s.props))].sort(), liveDots: Math.max(...sampled.map(s => s.dots))};
    out.idle = idle;
    assert.equal(idle.layouts, 0, `idle: ${idle.layouts} layouts`);
    assert.ok(idle.recalcs <= 4, `idle: ${idle.recalcs} style recalcs`);
    assert.ok(idle.taskMs < 150, `idle: ${idle.taskMs} ms of main-thread work`);
    assert.ok(idle.paints <= 1, `idle: ${idle.paints} paints`);
    assert.ok(idle.props.every(p => DOT_ANIMATED.has(p)), `the dot animates ${idle.props}`);
    assert.equal(idle.liveDots, 1, 'one moving dot on the screen');

    // ---- Open, a reply, close, with the CPU slowed 4x. A first open and close loads the chat; the second is timed.
    await page.locator('#ask-fab').click();
    await page.locator('#ask-panel .ask-form .p-text').waitFor();
    await page.waitForFunction(() => !document.querySelector('.ldot-fly'));
    await page.keyboard.press('Escape');
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    await page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
    await page.waitForTimeout(400);
    await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
    const settle = () => page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away') && !document.querySelector('#ask-fab').getAnimations().length);
    const budget = {p95: 20 * SLOWDOWN, max: 50 * SLOWDOWN, long: 50 * SLOWDOWN};
    const phases = {
      open: async () => { await page.locator('#ask-fab').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle(); },
      reply: async () => {
        const box = page.locator('#ask-panel .ask-form .p-text');
        await box.fill('Thanks'); await box.press('Enter');
        await page.locator('#ask-panel .conv-run[data-message="b1"] .ask-turn-dot').waitFor(); await settle();
        // open: one dot, flying or still; the newest turn holds the live one
        const live = await dotAnimations(page);
        assert.ok(live.dots <= 1, `open: ${live.dots} moving dots`);
        assert.ok(live.props.every(p => DOT_ANIMATED.has(p)), `open: the dot animates ${live.props}`);
      },
      close: async () => { await page.keyboard.press('Escape'); await page.locator('#ask-panel').waitFor({state: 'hidden'}); await settle(); },
    };
    out.throttled = {};
    for (const [name, act] of Object.entries(phases)) {
      const r = await frames(page, act);
      out.throttled[name] = {frames: r.frames, p95: round(r.p95), max: round(r.max), longTask: round(r.long)};
    }
    await cdp.send('Emulation.setCPUThrottlingRate', {rate: 1});
    for (const [name, r] of Object.entries(out.throttled)) {
      assert.ok(r.frames > 5, `${name}: ${r.frames} frames`);
      assert.ok(r.p95 <= budget.p95, `${name}: frame p95 ${r.p95} ms (budget ${budget.p95})`);
      assert.ok(r.max <= budget.max, `${name}: frame max ${r.max} ms (budget ${budget.max})`);
      assert.ok(r.longTask <= budget.long, `${name}: a ${r.longTask} ms long task (budget ${budget.long})`);
    }

    // ---- A hidden tab: everything pauses and no idle timer runs, or is set, until it shows again.
    const hidden = await page.evaluate(async () => {
      let set = 0;
      const real = window.setTimeout;
      window.setTimeout = (fn, ...rest) => { if (fn === ldotIdle) set++; return real(fn, ...rest); };
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
      Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
      await new Promise(r => real(r, 1500));
      const res = {timer: ldotScheduled(), set, paused: [...document.querySelectorAll('.ldot')].every(d => d.hasAttribute('data-paused'))};
      delete document.hidden; delete document.visibilityState;
      document.dispatchEvent(new Event('visibilitychange'));
      res.back = ldotScheduled();
      window.setTimeout = real;
      return res;
    });
    out.hidden = hidden;
    assert.deepEqual(hidden, {timer: false, set: 0, paused: true, back: true});
    assert.deepEqual(errors, []);
  } finally {
    if (process.env.DOT_PERF_OUT) fs.writeFileSync(process.env.DOT_PERF_OUT, JSON.stringify(out, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
