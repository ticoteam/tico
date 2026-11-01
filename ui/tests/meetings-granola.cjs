// The viewer's own Granola account on the Meetings page (ui/app/meetings-granola.js): Connect Granola opens a dialog
// with a short code and an Open Granola link, polls the connect status every `interval` seconds until connected,
// expired or denied; a connected account syncs once when the page opens and the list refreshes; Sync now sits on the
// row and Disconnect in a … menu; a signed-out account offers "Sign in again"; the API-key importer is a dialog link.
// Fixtures only, no network. GRANOLA_SHOTS=<dir> also saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const shots = process.env.GRANOLA_SHOTS || '';
if (shots) fs.mkdirSync(shots, {recursive: true});
const now = Date.now();
const ago = minutes => new Date(now - minutes * 6e4).toISOString();
const meeting = (id, title, minutes) => ({id, title, source: 'granola', kind: 'meeting', status: 'done', started: ago(minutes), created: ago(minutes),
  duration_ms: 1800000, participants: [{name: 'Ana', email: 'ana@acme.example'}, {name: 'Ben'}], outbox: {doc: [], task: [], feature: []}});
// Shapes as the server sends them (OpenAPI GranolaStatus / GranolaSync); `syncing` is filled in by the fake below.
const OFF = {mode: 'off', connected: false, plan_hint: null, last_sync: null, last_error: null, imported_count: 0, skipped: 0, needs_signin: false};
const ON = {mode: 'account', connected: true, email: 'ana@acme.example', plan_hint: 'free', last_sync: ago(3), last_error: null, imported_count: 42, skipped: 0, needs_signin: false};
const CODE = {user_code: 'WDJB-MJHT', verification_uri: 'https://mcp-auth.granola.ai/device',
  verification_uri_complete: 'https://mcp-auth.granola.ai/device?user_code=WDJB-MJHT', expires_in: 600, interval: 3};

async function open(browser, viewport, w, scheme = 'dark') {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', colorScheme: scheme, hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript(theme => { try { localStorage.setItem('tico.theme', theme); } catch {} }, scheme);
  const page = await context.newPage();
  // Install before the pause target: a busy machine can take time between the two protocol calls.
  await page.clock.install({time: new Date(now - 3600000)});
  await page.clock.pauseAt(new Date(now));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: w.role, name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/setup/getting-started') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true, cards_dismissed: [],
      can_build: true, owner: true, empty: {docs: true, market: true, tasks: true, updates: true, goals: true, meetings: true}});
    if (p === '/api/meetings/sources') return json({sources: [{id: 'import', name: 'Import', status: 'available'}]});
    if (p === '/api/v2/meetings') { w.listed++; return json({meetings: w.meetings, pending_count: 0}); }
    if (p === '/api/v2/meeting-importers') return json({computers: [{id: 'mac', label: 'Test Mac', platform: 'macos', online: true}],
      importers: [{source: 'granola', name: 'Granola', enabled: false, runner_id: '', status: 'off', imported_total: 0, error: '',
        setup: {file: 'secrets/granola.env', keys: ['GRANOLA_API_KEY'], doc: 'docs/meetings.md'}}]});
    if (p === '/api/v2/meetings/granola') {
      w.calls.push('status');
      if (w.missing) return json({error: {detail: 'Not found'}}, 404);
      // A sync runs for `syncLeft` more status reads, then `finish` lands its result.
      if (w.syncLeft > 0) { w.syncLeft--; return json({...w.granola, syncing: true}); }
      if (w.finish && count(w, 'sync')) { const finish = w.finish; w.finish = null; finish(); }
      return json({...w.granola, syncing: false});
    }
    if (p === '/api/v2/meetings/granola/connect' && method === 'POST') { w.calls.push('connect'); return json(w.code || CODE); }
    if (p === '/api/v2/meetings/granola/connect' && method === 'DELETE') { w.calls.push('disconnect'); w.granola = {...OFF}; return json({ok: true}); }
    if (p === '/api/v2/meetings/granola/connect/status') {
      w.calls.push('poll');
      const next = w.polls.length > 1 ? w.polls.shift() : w.polls[0];
      if (next.state === 'connected') w.granola = {...ON, email: next.email, last_sync: null, imported_count: 0};
      return json(next);
    }
    if (p === '/api/v2/meetings/granola/sync' && method === 'POST') {
      w.calls.push('sync');
      w.syncLeft = w.syncSteps;
      return json({state: 'syncing', last_sync: w.granola.last_sync});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/meetings');
  return {page, errors, context};
}
const world = (extra = {}) => ({role: 'owner', meetings: [meeting('g1', 'Renewal call with Dana', 300)], granola: {...OFF}, syncSteps: 0, syncLeft: 0, finish: null,
  polls: [{state: 'pending'}], calls: [], listed: 0, ...extra});
// The row's facts as read on screen: CSS puts the ' · ' between them.
const lineText = page => page.locator('#mg-row .mg-line').evaluate(el => [...el.children].map(c => c.textContent.trim()).join(' · '));
const count = (w, name) => w.calls.filter(c => c === name).length;
const until = async (fn, what, ms = 10000) => {
  for (const end = Date.now() + t(ms); !await fn();) { if (Date.now() > end) throw new Error('timed out: ' + what); await new Promise(r => setTimeout(r, 10)); }
};
// HTTP responses finish in real time; wait for each handler to arm its next fake timer before advancing again.
const poll = async (page, w, name, ms) => {
  const before = count(w, name);
  const timer = await page.evaluate(name => name === 'poll' ? MEET.gTimer : MEET.gSyncTimer, name);
  await page.clock.fastForward(ms);
  await until(() => count(w, name) === before + 1, name + ' request');
  await until(() => page.evaluate(([name, timer]) => name === 'poll' ? !MEET.gflow || (!!MEET.gTimer && MEET.gTimer !== timer)
    : !MEET.gSyncing || (!!MEET.gSyncTimer && MEET.gSyncTimer !== timer), [name, timer]), name + ' response');
};
const shot = async (page, name) => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const desk = {width: 1280, height: 800};
  try {
    // Functional contracts run once; both themes and viewports keep the layout checks below.
    {
      const scheme = 'dark';
      // ---- connect: Connect Granola -> code + Open Granola -> pending, pending -> connected -> one sync
      let w = world({polls: [{state: 'pending'}, {state: 'pending'}, {state: 'connected', email: 'ana@acme.example'}]});
      let {page, errors, context} = await open(browser, desk, w, scheme);
      const connect = page.locator('#mg-row [data-g=connect]');
      await connect.waitFor();
      assert.equal(await connect.innerText(), 'Connect Granola');
      assert.equal(await lineText(page), 'Granola');
      assert.equal(await page.locator('#mg-row .mg-key').count(), 0, 'the key link lives in the dialog');
      assert.equal(count(w, 'sync'), 0, 'no sync when not connected');
      await page.locator('.meet-row').first().waitFor();
      await connect.click();
      await page.locator('#mg-dialog #mg-code').waitFor();
      await page.clock.fastForward(30);
      assert.equal(await page.locator('#mg-code').innerText(), 'WDJB-MJHT');
      assert.equal(await page.locator('#mg-dialog [data-g=open]').getAttribute('href'), CODE.verification_uri_complete);
      assert.equal(await page.locator('#mg-dialog [data-g=open]').getAttribute('target'), '_blank');
      assert.equal(await page.locator('#mg-dialog .mg-key').innerText(), 'Use an API key');
      assert.equal(await page.evaluate(() => document.activeElement?.dataset.g), 'open', 'focus moves to Open Granola');
      assert.equal(await page.locator('#mg-live').getAttribute('aria-live'), 'polite');
      await page.waitForFunction(() => /Enter it in Granola/.test(document.querySelector('#mg-live')?.textContent || ''));
      await shot(page, `granola-code-${scheme}`);
      await page.clock.fastForward(CODE.interval * 1000 - 31);
      assert.equal(count(w, 'poll'), 0, 'the server interval is respected');
      await poll(page, w, 'poll', 1);
      await poll(page, w, 'poll', CODE.interval * 1000);
      await poll(page, w, 'poll', CODE.interval * 1000);
      await page.locator('#mg-row [data-g=more]').waitFor();
      assert.equal(count(w, 'poll'), 3, 'polled until connected');
      await page.clock.fastForward(30);
      await page.waitForFunction(() => /Granola connected/.test(document.querySelector('#mg-live')?.textContent || ''));
      await until(() => count(w, 'sync') === 1, 'a new connection syncs once');
      assert.equal(count(w, 'connect'), 1);
      assert.match(await lineText(page), /^Granola · ana@acme\.example · (Syncing…|Not synced yet|Synced .+) · \d+ notes?$/);
      assert.equal(await page.locator('#mg-code').count(), 0);
      assert.equal(await page.locator('#mg-dialog[open]').count(), 0, 'the dialog closes once connected');
      const polls = count(w, 'poll');
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'poll'), polls, 'polling stops once connected');
      assert.deepEqual(errors, []);
      await context.close();

      // ---- expired and denied: one short line, Connect Granola again, polling stops
      for (const [state, words] of [['expired', 'The code expired. Try again.'], ['denied', 'Granola access was denied.'], ['needs_signin', 'Granola sign-in failed. Try again.']]) {
        w = world({polls: [{state: 'pending'}, {state}]});
        ({page, errors, context} = await open(browser, desk, w, scheme));
        await page.locator('#mg-row [data-g=connect]').click();
        await page.locator('#mg-code').waitFor();
        await poll(page, w, 'poll', CODE.interval * 1000);
        await poll(page, w, 'poll', CODE.interval * 1000);
        await page.locator('#mg-row .mg-err').waitFor();
        assert.equal(await page.locator('#mg-row .mg-err').innerText(), words);
        assert.equal(await page.locator('#mg-row .mg-err').getAttribute('role'), 'alert');
        assert.equal(await page.locator('#mg-code').count(), 0);
        assert.equal(await page.evaluate(() => document.activeElement?.dataset.g), 'connect', state + ': focus on Connect Granola');
        const n = count(w, 'poll');
        await page.clock.fastForward(15000);
        assert.equal(count(w, 'poll'), n, state + ': polling stops');
        assert.equal(count(w, 'sync'), 0);
        assert.deepEqual(errors, []);
        await context.close();
      }

      // ---- connected: sync once on open, list refreshes when last_sync moves; free plan line; Sync and Disconnect in …
      const before = ago(30);
      w = world({granola: {...ON, last_sync: before, imported_count: 41}, syncSteps: 1});
      w.finish = () => { w.granola = {...ON, last_sync: ago(3), imported_count: 42, skipped: 2}; w.meetings = [meeting('g2', 'Pricing review', 5), ...w.meetings]; };
      ({page, errors, context} = await open(browser, desk, w, scheme));
      await page.locator('#mg-row [data-g=more]').waitFor();
      await page.locator('#mg-row .mg-syncing .mg-spin').waitFor();
      assert.match(await lineText(page), /· Syncing… · 41 notes$/);
      await until(() => page.evaluate(() => !!MEET.gSyncTimer), 'sync timer armed');
      await page.clock.fastForward(2999);
      assert.equal(count(w, 'status'), 1, 'no status poll before three seconds');
      await poll(page, w, 'status', 1);
      assert.equal(await page.locator('.meet-row').count(), 1, 'the list waits for the sync to finish');
      await page.clock.fastForward(4499);
      assert.equal(count(w, 'status'), 2, 'the next sync poll backs off to 4.5 seconds');
      await poll(page, w, 'status', 1);
      await page.waitForFunction(() => document.querySelectorAll('.meet-row').length === 2, null, {timeout: t(12000)});
      assert.equal(count(w, 'sync'), 1, 'one sync when the page opens');
      assert.deepEqual(await page.locator('.meet-row .note-title').allInnerTexts(), ['Pricing review', 'Renewal call with Dana']);
      await page.waitForFunction(() => /42 notes/.test(document.querySelector('#mg-row .mg-line')?.textContent || ''));
      assert.equal(await lineText(page), 'Granola · ana@acme.example · Synced 3m ago · 42 notes · 2 skipped');
      assert.equal(await page.locator('#mg-row .mg-skip').getAttribute('title'), '2 notes from Granola could not be imported');
      const polled = count(w, 'status');
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'status'), polled, 'polling stops once the sync is done');
      assert.equal(await page.locator('#mg-row .mg-line > span[title^="Free plan"]').innerText(), '42 notes');
      assert.equal(await page.locator('#mg-row [data-g=connect]').count(), 0);
      assert.equal(await page.locator('#meet-sources [data-msrc=granola]').count(), 1, 'Add source still lists Granola');
      assert.equal(await page.locator('[data-meet-tiles] [data-msrc=granola]').count(), 0, 'the big tiles leave Granola to its row');
      await shot(page, `granola-connected-${scheme}`);
      // The … menu: keyboard opens it on its first item; Escape closes it back on the button.
      const more = page.locator('#mg-row [data-g=more]');
      assert.equal(await more.getAttribute('aria-expanded'), 'false');
      await more.focus(); await page.keyboard.press('Enter');
      assert.equal(await more.getAttribute('aria-expanded'), 'true');
      assert.equal(await page.evaluate(() => document.activeElement?.dataset.g), 'disconnect');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#mg-menu').isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement?.dataset.g), 'more');
      await page.locator('#mg-row [data-g=sync]').click();
      await until(() => count(w, 'sync') === 2, 'Sync asks again');
      await page.waitForFunction(() => /Syncing…/.test(document.querySelector('#mg-row .mg-line')?.textContent || ''));
      await more.click();
      await page.locator('#mg-menu [data-g=disconnect]').click();
      await page.locator('#mg-row [data-g=connect]').waitFor();
      await page.clock.fastForward(30);
      assert.equal(count(w, 'disconnect'), 1);
      assert.equal(await lineText(page), 'Granola');
      await page.waitForFunction(() => /Granola disconnected/.test(document.querySelector('#mg-live')?.textContent || ''));
      assert.deepEqual(errors, []);
      await context.close();

      // ---- needs sign-in: no sync on open; "Sign in to Granola again" restarts the flow
      w = world({granola: {...ON, connected: false, plan_hint: 'paid', needs_signin: true, last_error: 'Granola needs sign-in again'}});
      ({page, errors, context} = await open(browser, desk, w, scheme));
      const again = page.locator('#mg-row [data-g=connect]');
      await again.waitFor();
      assert.equal(await again.innerText(), 'Sign in again');
      assert.equal(await lineText(page), 'Granola · ana@acme.example · Signed out');
      assert.equal(await page.locator('#mg-row .mg-sub').count(), 0, 'no free plan line on a paid plan');
      assert.equal(await page.locator('#mg-row .mg-err').innerText(), 'Granola needs sign-in again');
      assert.equal(await page.locator('#mg-row [data-g=more]').count(), 1, 'Disconnect stays reachable');
      await shot(page, `granola-needs-signin-${scheme}`);
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'sync'), 0, 'no sync while signed out');
      await again.click();
      await page.locator('#mg-code').waitFor();
      assert.equal(count(w, 'connect'), 1);
      await page.locator('#mg-dialog footer [data-g=cancel]').click();
      assert.equal(await page.locator('#mg-code').count(), 0);
      await page.clock.fastForward(15000);
      assert.equal(count(w, 'poll'), 0, 'cancel stops the device-code timer');
      assert.deepEqual(errors, []);
      await context.close();
    }

    // ---- the API-key importer stays reachable for the owner
    let w = world();
    let {page, errors, context} = await open(browser, desk, w);
    await page.locator('#mg-row [data-g=connect]').click();
    await page.locator('#mg-dialog .mg-key').click();
    await page.locator('dialog.import-modal[aria-label="Connect Granola"] form[data-importer=granola]').waitFor();
    await page.keyboard.press('Escape');
    // Add source > Granola opens the same sign-in as the row
    await page.locator('#meet-sources .meet-src-btn').click();
    await page.locator('#meet-sources [data-msrc=granola]').click();
    await page.locator('#mg-code').waitFor();
    assert.deepEqual(errors, []);
    await context.close();

    // ---- a member: no key link, but Connect works for their own account
    w = world({role: 'member'});
    ({page, errors, context} = await open(browser, desk, w));
    await page.locator('#mg-row [data-g=connect]').waitFor();
    assert.equal(await page.locator('#mg-row [data-g=connect]').isDisabled(), false);
    await page.locator('#mg-row [data-g=connect]').click();
    await page.locator('#mg-code').waitFor();
    assert.equal(await page.locator('#mg-dialog .mg-key').count(), 0);
    await page.locator('#mg-dialog footer [data-g=cancel]').click();
    assert.equal(await page.locator('#meet-sources [data-msrc=zoom]').isDisabled(), true);
    await context.close();

    // ---- an older server without the route: no row, the tile opens the API-key importer as before
    w = world({missing: true});
    ({page, errors, context} = await open(browser, desk, w));
    await page.locator('.meet-row').first().waitFor();
    await page.waitForFunction(() => document.querySelector('#meet-granola')?.hidden === true);
    await page.locator('#meet-sources .meet-src-btn').click();
    await page.locator('#meet-sources [data-msrc=granola]').click();
    await page.locator('dialog[aria-label="Connect Granola"]').waitFor();
    await context.close();

    // ---- a sign-in link off Granola's own https host is not a link: the code and the host as text
    w = world({code: {...CODE, verification_uri: 'https://granola.example.net/device', verification_uri_complete: 'javascript:alert(1)'}});
    ({page, errors, context} = await open(browser, desk, w));
    await page.locator('#mg-row [data-g=connect]').click();
    await page.locator('#mg-code').waitFor();
    assert.equal(await page.locator('#mg-dialog a').count(), 0, 'no link');
    assert.equal(await page.locator('#mg-dialog .mg-host').innerText(), 'granola.example.net');
    assert.deepEqual(errors, []);
    await context.close();

    // ---- a sync already running when the page opens (the schedule): follow it, no second sync
    w = world({granola: {...ON}, syncLeft: 1});
    ({page, errors, context} = await open(browser, desk, w));
    await page.locator('#mg-row .mg-syncing').waitFor();
    await until(() => page.evaluate(() => !!MEET.gSyncTimer), 'existing sync timer armed');
    await poll(page, w, 'status', 3000);
    await page.locator('#mg-row .mg-syncing').waitFor({state: 'detached', timeout: t(8000)});
    assert.equal(count(w, 'sync'), 0, 'no sync asked while one runs');
    assert.deepEqual(errors, []);
    await context.close();

    // ---- desktop and phone, both themes: code, account row and menu fit without sideways scroll
    for (const [device, viewport] of [['desktop', desk], ['phone', {width: 390, height: 844}]]) {
      for (const scheme of ['dark', 'light']) {
        w = world();
        ({page, errors, context} = await open(browser, viewport, w, scheme));
        await page.locator('#mg-row [data-g=connect]').click();
        await page.locator('#mg-code').waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${device}-${scheme}: code fits`);
        const box = await page.locator('#mg-dialog [data-g=open]').boundingBox();
        assert(box && box.x + box.width <= viewport.width, 'Open Granola is on screen');
        await shot(page, `granola-${device}-code-${scheme}`);
        w.granola = {...ON, email: 'ana.longname@acme-example-company.example'};
        await page.reload();
        await page.locator('#mg-row [data-g=more]').waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${device}-${scheme}: connected row fits`);
        await page.locator('#mg-row [data-g=more]').click();
        const menu = await page.locator('#mg-menu').boundingBox();
        assert(menu.x >= 0 && menu.x + menu.width <= viewport.width, 'menu on screen');
        await shot(page, `granola-${device}-connected-${scheme}`);
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    console.log('meetings-granola ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
