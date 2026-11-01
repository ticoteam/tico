// Personal review controls against local fixtures; no provider or server calls.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const evidence = process.env.TICO_REVIEW_EVIDENCE;
const row = (id, title, owner, review_state, privateMeeting = false) => ({id, title, owner, review_state, private: privateMeeting,
  kind: 'meeting', status: 'done', client: privateMeeting ? 'tico-recorder' : null, source: privateMeeting ? 'granola' : 'zoom', can_edit: true, started_at: '2026-10-01T14:00:00Z',
  notes: 'Review the plan and next steps.', turns: [{speaker: 'Sam', start_ms: 0, text: 'The plan is ready.'}],
  participants: [{name: 'Sam'}], outbox: {doc: [], task: [], feature: []}});
async function open(browser, viewport, scheme, role = 'owner') {
  const context = await browser.newContext({viewport, colorScheme: scheme, serviceWorkers: 'block', isMobile: viewport.width < 760, hasTouch: viewport.width < 760});
  await context.addInitScript(theme => localStorage.setItem('tico.theme', theme), scheme);
  const page = await context.newPage(), errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const rows = [row('live', 'Shared planning', 'ana@example.com', 'live'), row('one', 'Planning review', 'ana@example.com', 'pending'),
    row('two', 'Private source notes', 'ana@example.com', 'pending', true), row('three', 'Follow-up review', 'ana@example.com', 'pending'),
    row('other', 'Other person confidential', 'sam@example.com', 'pending')];
  let settings = {auto_share: null, review_default: 'review', effective_auto_share: false};
  const pending = () => rows.filter(r => r.owner === 'ana@example.com' && r.review_state === 'pending');
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@example.com', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@example.com'}, {id: 'sam', name: 'Sam', email: 'sam@example.com'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/updates/unread') return json({unread: 0, meetings_pending: pending().length});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/setup/getting-started') return json({items: [], complete: true, dismissed: true, tour_seen: true, cards_dismissed: [], owner: role === 'owner', empty: {}});
    if (p === '/api/meetings/sources') return json({sources: []});
    if (p === '/api/v2/meetings/granola') return json({state: 'off', connected: false});
    if (p === '/api/v2/meetings/settings') {
      if (method === 'POST') {
        const body = request.postDataJSON(); writes.push({settings: body}); settings = {...settings, ...body};
        settings.effective_auto_share = settings.auto_share ?? settings.review_default === 'auto';
      }
      return json(settings);
    }
    if (p === '/api/v2/meetings') {
      const state = url.searchParams.get('review'), q = url.searchParams.get('q') || '';
      const list = rows.filter(r => r.review_state === state && (state === 'live' || r.owner === 'ana@example.com') && r.title.toLowerCase().includes(q.toLowerCase()));
      return json({meetings: list, count: list.length, pending_count: pending().length});
    }
    if (p.endsWith('/review') && method === 'POST') {
      const body = request.postDataJSON(); writes.push({path: p, ...body});
      const bulk = p === '/api/v2/meetings/review';
      const selected = bulk ? pending().filter(r => !body.ids || body.ids.includes(r.id)) : rows.filter(r => r.id === p.split('/').at(-2));
      for (const r of selected) {
        assert.equal(r.owner, 'ana@example.com', 'writes only target own meetings');
        r.review_state = body.action.startsWith('approve') ? 'live' : body.action === 'restore' ? 'pending' : 'dismissed';
        if ('private' in body) r.private = body.private;
      }
      return json(bulk ? {meetings: selected, count: selected.length, pending_count: pending().length} : selected[0]);
    }
    if (/^\/api\/meetings\/[^/]+$/.test(p)) return json(rows.find(r => r.id === p.split('/').pop()));
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/meetings');
  // New imports are the first thing a person sees: the page opens on Pending while it has any.
  await page.locator('[data-note-row=one]').waitFor();
  assert.equal(await page.locator('[data-review=pending]').getAttribute('aria-selected'), 'true');
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => { window.open = () => null; });
  return {context, page, errors, writes, rows};
}
async function shot(page, name) {
  if (!evidence) return;
  fs.mkdirSync(evidence, {recursive: true});
  await page.screenshot({path: path.join(evidence, name + '.png'), fullPage: page.viewportSize().width >= 760});
}
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [size, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 844}]]) {
      for (const scheme of ['light', 'dark']) {
        const {context, page, errors, writes, rows} = await open(browser, viewport, scheme);
        assert.equal(await page.locator('[data-meet-pending]').textContent(), '3');
        assert.equal(await page.locator('[data-upd-badge]:visible').count(), 0, 'Pending never adds to Updates');
        await page.locator('[data-review=pending]').click();
        await page.locator('[data-note-row=one]').waitFor();
        assert.equal(await page.locator('.meet-row').count(), 3);
        assert.equal(await page.locator('html').getAttribute('data-theme'), scheme);
        if (size === 'desktop') {
          const rects = await page.locator('[data-note-row=one] > *').evaluateAll(els => els.map(el => ({x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y})));
          assert(rects[0].x < rects[1].x && rects[1].x < rects[2].x && rects[2].x < rects[3].x, 'checkbox, source, title and actions align in a row');
          assert(Math.abs(rects[2].y - rects[3].y) < 20);
        }
        assert(!await page.locator('#main').innerText().then(t => t.includes('Other person confidential')));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await shot(page, `${size}-pending-${scheme}`);
        await page.locator('[data-note-row=two] .note-title').click();
        await page.locator('#meet-review-private').waitFor();
        assert.equal(await page.locator('#meet-review-private').inputValue(), 'private');
        assert.match(await page.locator('#notes-detail').innerText(), /From Tico Recorder/);
        assert.equal(await page.locator('#meet-send, #meet-sections').count(), 0, 'Pending has no Send or Push controls');
        await page.locator('[data-note-section=transcript]').click();
        assert.match(await page.locator('#notes-detail').innerText(), /The plan is ready/);
        await shot(page, `${size}-review-${scheme}`);
        await page.locator('#meet-review-private').selectOption('team');
        await page.locator('#notes-detail [data-review-action=approve]').click();
        await page.locator('#notes-modal').waitFor({state: 'hidden'});
        await page.waitForFunction(() => document.querySelectorAll('.meet-row').length === 2);
        assert.equal(rows.find(r => r.id === 'two').private, false);
        assert.equal(writes.find(w => w.path === '/api/v2/meetings/two/review').send_to, 'auto', 'Share & send hands it to a bot');
        await page.locator('[data-note-row=one] [data-review-action=dismiss]').click();
        await page.locator('[data-note-row=one]').waitFor({state: 'detached'});
        await page.locator('[data-review=dismissed]').click();
        await page.locator('[data-note-row=one] [data-review-action=restore]').click();
        await page.locator('[data-note-row=one]').waitFor({state: 'detached'});
        await page.locator('[data-review=pending]').click();
        await page.locator('[data-review-check=one]').check();
        assert.equal(await page.locator('#meet-review-bulk').isHidden(), true, 'one ticked row: the row actions are enough');
        await page.locator('[data-note-row=one] [data-review-action=approve]').click();
        await page.locator('[data-note-row=one]').waitFor({state: 'detached'});
        await page.locator('#meet-settings').click();
        const settings = page.getByRole('dialog', {name: 'Meeting settings'});
        await settings.locator('[name=personal]').selectOption('auto');
        await settings.locator('[name=team]').selectOption('auto');
        await shot(page, `${size}-settings-${scheme}`);
        await settings.getByRole('button', {name: 'Save', exact: true}).click();
        await settings.waitFor({state: 'detached'});
        assert.equal(await page.locator('.meet-row').count(), 1, 'Auto-share leaves the existing queue pending');
        assert.deepEqual(writes.at(-1).settings, {auto_share: true, review_default: 'auto'});
        assert.equal(await page.locator('#meet-select-all').isHidden(), true, 'no Select all for one row');
        await page.locator('[data-note-row=three] [data-review-action=dismiss]').click();
        await page.locator('.notes-empty').waitFor();
        assert.equal(rows.find(r => r.id === 'other').review_state, 'pending');
        assert.equal(await page.locator('[data-meet-pending]:visible').count(), 0);
        await page.locator('[data-review=live]').click();
        await page.locator('[data-note-row=live]').waitFor();
        assert.equal(await page.locator('.meet-row').count(), 3, 'existing and newly approved meetings stay live');
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    const {context, page, writes, errors} = await open(browser, {width: 390, height: 844}, 'light', 'human');
    await page.locator('[data-review=pending]').click();
    await page.locator('[data-note-row=one]').waitFor();
    await page.locator('#meet-select-all input').check();
    await page.locator('[data-review-bulk=approve_all]').click();
    await page.locator('.notes-empty').waitFor();
    assert.equal(writes.at(-1).action, 'approve_all'); assert.deepEqual(writes.at(-1).ids, ['one', 'two', 'three']);
    await page.locator('#meet-settings').click();
    const settings = page.getByRole('dialog', {name: 'Meeting settings'});
    await settings.locator('[name=personal]').waitFor();
    assert.equal(await settings.locator('[name=team]').count(), 0, 'Team default is owner-only');
    await settings.locator('[name=personal]').selectOption('review');
    await settings.getByRole('button', {name: 'Save', exact: true}).click();
    await settings.waitFor({state: 'detached'}); assert.deepEqual(writes.at(-1).settings, {auto_share: false});
    await page.locator('#meet-settings').click();
    await settings.locator('[name=personal]').selectOption('default');
    await settings.getByRole('button', {name: 'Save', exact: true}).click();
    await settings.waitFor({state: 'detached'}); assert.deepEqual(writes.at(-1).settings, {auto_share: null});
    assert.deepEqual(errors, []); await context.close();
    for (const action of ['approve_all', 'dismiss_all']) {
      for (const filter of ['search', 'source']) {
        const {context, page, writes, rows, errors} = await open(browser, {width: 1280, height: 900}, 'light');
        await page.locator('[data-review=pending]').click();
        await page.locator('[data-note-row=two]').waitFor();
        // A selected row becoming invisible must never be sent by a visible-list button.
        await page.locator('[data-review-check=two]').check();
        if (filter === 'search') {
          await page.locator('#notes-search').fill('review');
        } else {
          await page.locator('#notes-source').selectOption('zoom');
        }
        await page.locator('[data-note-row=two]').waitFor({state: 'detached'});
        const visible = ['one', 'three'];
        await page.locator('#meet-select-all input').check();
        const button = page.locator(`[data-review-bulk=${action}]`);
        assert.equal(await button.textContent(), action === 'approve_all' ? 'Share 2' : 'Dismiss 2');
        if (action === 'approve_all' && filter === 'search') await shot(page, 'desktop-share-visible-light');
        await button.click();
        await page.locator('[data-note-row=one]').waitFor({state: 'detached'});
        assert.deepEqual(writes.at(-1).ids, visible);
        assert.equal(rows.find(r => r.id === 'two').review_state, 'pending', 'filtered-out private meeting stays pending');
        assert.equal(rows.find(r => r.id === 'other').review_state, 'pending', 'another person stays private');
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    console.log('meetings-review ok: 9 scenarios, desktop/phone light/dark, review/settings and filtered visible-only sharing/dismissal');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
