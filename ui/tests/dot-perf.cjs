// The Liquid Dot stays cheap (ui/app/liquid-dot.js, styles/liquid-dot.css; budgets in docs/assistant.md). Chrome over
// CDP, fixtures only:
// - Idle 10 s with Ask closed: no layout, at most 4 style recalcs per idle swap (one or two land in 10 s), under 150 ms of main-thread work, no paints from
//   the dot, and every running animation on a dot moves transform or opacity only, on one dot at most.
// - Each idle variant swapped in by hand: no layout and no paint.
// - With the CPU slowed 4x, opening Ask (the dot flies up to the panel's header), a reply (the header's dot works, then
//   rests) and closing (it flies back down): frame gaps p95 <= 20 ms and max <= 50 ms, and no long task over 50 ms. On a loaded runner
//   (TICO_UI_SLOWDOWN > 1) the frame budgets stretch by that factor; the counts never do.
// - Opening and closing Ask, by the row, ⌘J, Escape and a peek, with a peek showing, and from a page with no heading to
//   one with a heading (the page it sees goes and comes back), flights included: no `hidden` attribute changes anywhere
//   and no child of <body> comes or goes (each restyles the whole page on a real hub); the flying copy is made once
//   and reused.
// - A hidden tab: every dot paused and no idle timer left, and none set while hidden.
// DOT_PERF_OUT=<file> writes the numbers (the PR body quotes them).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {SLOWDOWN} = require('./support/load.cjs');

const now = new Date().toISOString();
const msg = (id, from, body) => ({id, from_actor: from, body, refs: {}, created_at: now, kind: 'say'});
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
  await page.locator('#ask-row').waitFor();
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
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(4500);                       // the page settles: first draws, fonts, support's read, the peek's first look

    // ---- Idle, Ask closed.
    // Count the scheduler's swaps in the window: each may cost a few style recalcs (4 at most), nothing else may.
    await page.evaluate(() => { window.__swaps = 0; new MutationObserver(l => { window.__swaps += l.length; console.timeStamp('idle-swap'); })
      .observe(document.body, {subtree: true, attributes: true, attributeFilter: ['data-idle']}); });
    const before = await metrics(cdp);
    await browser.startTracing(page, {categories: ['devtools.timeline', 'disabled-by-default-devtools.timeline.stack']});
    await page.waitForTimeout(IDLE_MS);
    const after = await metrics(cdp);
    const swaps = await page.evaluate(() => window.__swaps);
    const trace = JSON.parse((await browser.stopTracing()).toString());
    const sampled = [await dotAnimations(page)];       // after: reading animations recalcs style itself
    const events = (trace.traceEvents || trace).filter(e => e.ph === 'X' || e.ph === 'B');
    // What laid out, if anything: the script frames (or the style change) behind each layout, for the message.
    const why = events.filter(e => e.name === 'Layout').map(e => (e.args?.beginData?.stackTrace || []).slice(0, 4)
      .map(f => f.functionName || '(anonymous)').join(' < ') || events.filter(o => o !== e && o.tid === e.tid && o.ts <= e.ts
      && o.ts + (o.dur || 0) >= e.ts + (e.dur || 0)).map(o => o.args?.data?.functionName || o.args?.data?.type || o.name).join(' > ') || 'no script');
    const idle = {layouts: after.LayoutCount - before.LayoutCount, recalcs: after.RecalcStyleCount - before.RecalcStyleCount,
      taskMs: round((after.TaskDuration - before.TaskDuration) * 1000), paints: events.filter(e => e.name === 'Paint').length,
      props: [...new Set(sampled.flatMap(s => s.props))].sort(), liveDots: Math.max(...sampled.map(s => s.dots)), swaps};
    out.idle = idle;
    assert.equal(idle.layouts, 0, `idle: ${idle.layouts} layouts: ${JSON.stringify(why)}`);
    assert.ok(idle.recalcs <= 4 * Math.max(1, swaps), `idle: ${idle.recalcs} style recalcs over ${swaps} idle swaps`);
    assert.ok(idle.taskMs < 150, `idle: ${idle.taskMs} ms of main-thread work`);
    // Each paint: what it painted and how long after the nearest idle swap, for the message.
    const marks = (trace.traceEvents || trace).filter(e => e.name === 'TimeStamp' && e.args?.data?.message === 'idle-swap').map(e => e.ts);
    const painted = events.filter(e => e.name === 'Paint').map(e => `${e.args?.data?.nodeName || '?'} ${JSON.stringify(e.args?.data?.clip || []).slice(0, 40)}`
      + (marks.length ? ` ${Math.round(Math.min(...marks.map(m => Math.abs(e.ts - m))) / 1000)} ms from a swap` : ''));
    assert.ok(idle.paints <= 1, `idle: ${idle.paints} paints: ${JSON.stringify(painted)}`);
    assert.ok(idle.props.every(p => DOT_ANIMATED.has(p)), `the dot animates ${idle.props}`);
    assert.equal(idle.liveDots, 1, 'one moving dot on the screen');

    // ---- Every idle variant, swapped in by hand: a swap never lays out or paints, whichever variant comes up.
    const swapsBefore = await metrics(cdp);
    await browser.startTracing(page, {categories: ['devtools.timeline']});
    for (const v of await page.evaluate(() => LDOT_IDLES)) {
      await page.evaluate(v => { document.querySelector('#ask-row .ldot').dataset.idle = v; }, v);
      await page.waitForTimeout(400);
    }
    const swapsAfter = await metrics(cdp);
    const swapPaints = JSON.parse((await browser.stopTracing()).toString()).traceEvents.filter(e => e.name === 'Paint')
      .map(e => e.args?.data?.nodeName || '?');
    out.variants = {layouts: swapsAfter.LayoutCount - swapsBefore.LayoutCount, paints: swapPaints.length};
    assert.equal(out.variants.layouts, 0, `idle variants: ${out.variants.layouts} layouts`);
    assert.ok(swapPaints.length <= 1, `idle variants: paints ${JSON.stringify(swapPaints)}`);

    // ---- Open, a reply, close, with the CPU slowed 4x. A first open and close loads the chat; the second is timed.
    await page.locator('#ask-row').click();
    await page.locator('#ask-panel .ask-form .p-text').waitFor();
    await page.waitForFunction(() => !document.querySelector('.ldot-fly'));
    await page.keyboard.press('Escape');
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    await page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
    await page.waitForTimeout(400);
    await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
    const settle = () => page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away') && !document.querySelector('#ask-row').getAnimations().length);
    const budget = {p95: 20 * SLOWDOWN, max: 50 * SLOWDOWN, long: 50 * SLOWDOWN};
    const phases = {
      open: async () => { await page.locator('#ask-row').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle(); },
      reply: async () => {
        const box = page.locator('#ask-panel .ask-form .p-text');
        await box.fill('Thanks'); await box.press('Enter');
        await page.locator('#ask-panel .conv-run[data-message="b1"]').waitFor(); await settle();
        // open: one dot, flying or still; the header's is the live one
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

    // ---- Opening and closing (the row, ⌘J, Escape), flights included, change no `hidden` attribute anywhere and add
    // nothing to <body> or take nothing from it: on a real hub either restyles the whole page (ui/app/ask-panel.js askSync).
    await page.evaluate(() => {
      const seen = window.__askMut = [], name = n => n.id || String(n.className || n.nodeName);
      new MutationObserver(list => list.forEach(r => {
        if (r.type === 'attributes') seen.push('hidden on ' + name(r.target));
        else if (r.target === document.body) seen.push('<body> child ' + [...r.addedNodes].map(n => '+' + name(n)).concat([...r.removedNodes].map(n => '-' + name(n))).join(' '));
      })).observe(document, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden']});
    });
    const shut = async () => { await page.locator('#ask-panel').waitFor({state: 'hidden'}); await settle(); };
    for (let i = 0; i < 2; i++) {
      await page.locator('#ask-row').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle();
      await page.keyboard.press('Escape'); await shut();
      await page.keyboard.press('Control+j'); await page.locator('#ask-panel .p-text:focus').waitFor(); await settle();
      await page.keyboard.press('Control+j'); await shut();
    }
    // With a peek showing (a question just came in): tapping the peek opens Ask, as does the row; the peek goes by a
    // class. And the page it sees: a page with no heading, then one with a heading, so its label goes and comes back.
    const peekShows = id => page.evaluate(id => {
      document.activeElement?.blur();
      (S.v2 ||= {}).needs = [{kind: 'ask', id, created_at: new Date().toISOString(),
        ask: {id, from_actor: 'bot:coo', to_actor: 'human:ben', body: 'Ship it?', created_at: new Date().toISOString(), refs: {}}}];
      askPeek();
    }, id).then(() => page.locator('#ask-peek .ask-peek-go').waitFor());
    const heading = text => page.evaluate(text => { const h = document.querySelector('#main h1, #main h2'); h.dataset.was ??= h.textContent; h.textContent = text ?? h.dataset.was; }, text);
    await peekShows('q1');
    await page.locator('#ask-peek .ask-peek-go').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle();
    await page.keyboard.press('Escape'); await shut();
    await peekShows('q2');
    await heading('');
    await page.locator('#ask-row').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle();
    assert.equal(await page.locator('#ask-panel [data-ask-sees]').isVisible(), false, 'no heading, no page named');
    await page.keyboard.press('Escape'); await shut();
    await heading(null);
    await page.locator('#ask-row').click(); await page.waitForFunction(() => document.querySelector('.ldot-fly')); await settle();
    assert.equal(await page.locator('#ask-panel [data-ask-sees]').isVisible(), true, 'the page it sees, named');
    await page.keyboard.press('Escape'); await shut();
    out.toggleMutations = await page.evaluate(() => window.__askMut);
    assert.deepEqual(out.toggleMutations, [], 'Ask opening and closing changed `hidden` or <body>');
    assert.equal(await page.evaluate(() => document.querySelectorAll('.ldot-layer .ldot').length), 1, 'one flying copy, reused');

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
