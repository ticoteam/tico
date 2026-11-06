// Tico addresses and Texts in (ui/app/inboxes-texts.js) under Tools: external text cannot execute, an inbox's
// recent messages ask for that inbox only, Reveal code, a released address listed as Not in use with its messages,
// Tools › Messages (every inbox or the ones picked, kept through a redraw, by channel, sender, words, code and spam,
// a page at a time, with each message's channel and inbox), joining a Tico company account with a code (explicit consent,
// a changed code drops the shown company, a lost confirmation retries the same submission), asking for a number and
// accepting or cancelling a quote through Tico's own confirm (never a page dialog), who reads an inbox, a member's
// read-only view, and desktop and phone fit.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {googleFake} = require('./support/google-fake.cjs');
const {mailboxesFake, shot} = require('./support/mailboxes-fake.cjs');
const settled = page => page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
const fits = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
const go = (page, hash) => page.evaluate(h => { location.hash = h; }, hash);
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
    const errors = [], dialogs = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', dialog => { dialogs.push(dialog.message()); void dialog.dismiss(); });
    await page.addInitScript(() => { try { localStorage.setItem('tico.inboxes.skip', '1'); } catch { /* storage off */ } });
    const calls = [], mailboxes = mailboxesFake(), google = googleFake();
    const inbox = {inbox_id: 'ibx_a', kind: 'email', name: 'Account logins', address: 'agent-acme-login@tico.team', state: 'active', created_by: 'human:ana',
      used_for: '', period_end: null, cancel_pending: 0, audience: 'company', group_id: null};
    const released = {...inbox, inbox_id: 'ibx_old', name: 'Old logins', address: 'agent-acme-old@tico.team', state: 'released'};
    const evil = '<img src=x onerror="window.INBOUND_PWNED=1">';
    let preview = evil, connectionFailures = 1, releaseConnection, connectionStarted;
    const connectionGate = new Promise(resolve => { releaseConnection = resolve; });
    const connectionInFlight = new Promise(resolve => { connectionStarted = resolve; });
    const connectionCode = 'synthetic_' + 'x'.repeat(33), companyId = '7d05caa5-e578-4c2a-8887-3d5b557f6a0a';
    const phoneRequests = [{request_id: 'prq_quoted', name: 'Monthly logins', state: 'quoted', price_micros: 14000000, quoted_until: Date.now() / 1000 + 3600}];
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const file = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (file && fs.existsSync(uiFile(file[1]))) return route.fulfill({contentType: file[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(file[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1)))});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const body = request.postData() ? request.postDataJSON() : null;
      if (p.startsWith('/api/v2/inbound/')) {
        calls.push({p, query: url.search, method, body, operation: request.headers()['idempotency-key']});
        if (p.endsWith('/company-connection')) {
          const result = {connected: body.action === 'connect', scope: 'inbox', company: {company_id: companyId, name: evil}};
          if (body.action === 'preview') result.expires_at = Date.now() / 1000 + 300;
          if (body.action === 'connect' && connectionFailures-- > 0) {
            connectionStarted();
            return connectionGate.then(() => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: {code: 'inbox_connection_unavailable', detail: 'Retry the same code.'}})}));
          }
          return json(result);
        }
        if (p.endsWith('/audience')) { Object.assign(inbox, {audience: body.audience, group_id: body.group_id || null}); return json(inbox); }
        if (p.endsWith('/inboxes')) return json({inboxes: [inbox, released], configured: true, last_synced_at: 1800000000, error: '', gaps: []});
        if (p.endsWith('/messages')) {
          // Every inbox's read has an older page; one naming inboxes has only theirs.
          const ids = (url.searchParams.get('inbox_ids') || '').split(',').filter(Boolean);
          if (url.searchParams.get('before')) return json({messages: [{seq: 0, message_id: 'ses:test:older', inbox_id: 'ibx_old', kind: 'email',
            received_at: 1600000000, from: 'Older service', subject: 'Older page', preview: '', has_code: false}], next: null, cursor: 0});
          const all = [{seq: 2, message_id: 'ses:test:ibx_a', inbox_id: 'ibx_a', kind: 'email', received_at: 1800000000, from: 'Login service', subject: 'Sign in', preview, has_code: true},
            {seq: 1, message_id: 'ses:test:ibx_old', inbox_id: 'ibx_old', kind: 'email', received_at: 1700000000, from: 'Old service', subject: 'Old sign in',
              preview: 'Kept after release', has_code: false}];
          return json({messages: all.filter(m => !ids.length || ids.includes(m.inbox_id)), next: ids.length ? null : 1, cursor: 2});
        }
        if (p.endsWith('/code')) return json({code: '123456', untrusted: true});
        if (p.includes('/messages/')) return json({message_id: 'ses:test:ibx_a', text: 'Your code is 123456. ' + evil, next_offset: null, untrusted: true});
        if (p.endsWith('/phone-requests')) {
          if (method === 'GET') return json({phone_requests: phoneRequests, next: null});
          const created = {request_id: 'prq_new', name: body.name, state: 'requested', price_micros: null, quoted_until: null};
          phoneRequests.push(created); return json({phone_request: created});
        }
        if (p.includes('/phone-requests/')) {
          const selected = phoneRequests.find(r => p.includes('/' + r.request_id + '/'));
          selected.state = p.endsWith('/accept') ? 'accepted' : 'canceled';
          return json({phone_request: selected});
        }
      }
      if (p.startsWith('/api/v2/mailboxes')) { const answer = mailboxes.answer(p, method, body); if (answer !== undefined) return json(answer); }
      if (p.startsWith('/api/v2/google')) { const answer = google.answer(p, request.url(), method, body); if (answer !== undefined) return json(answer); }
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}, org_groups: [{id: 'sales', name: 'Sales'}]});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });

    // The old Email & Phone address opens Tools' mail; an old inbox address opens its page under Tools, which shows
    // external text as text and reads its own messages only.
    await page.goto('https://tico-ui.test/#/settings/company-inbox');
    await page.waitForFunction(() => location.hash === '#/tools?show=mail');
    const listed = calls.length;
    await go(page, '#/settings/inboxes/tico/ibx_a');
    await page.locator('[data-inbound-read]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tools/number/ibx_a');
    assert.match(await page.locator('#inbound-messages').innerText(), /onerror/);
    assert.equal(await page.locator('#inbound-messages img').count(), 0);
    const reads = () => calls.slice(listed).filter(c => c.p.endsWith('/messages') && c.method === 'GET').map(c => new URLSearchParams(c.query));
    assert.ok(reads().length && reads().every(q => q.get('inbox_ids') === 'ibx_a'), 'an inbox page reads its own messages only');
    await page.locator('#inbound-filter input[name=q]').fill('Sign');
    await page.locator('#inbound-filter input[name=has_code]').check();
    await page.locator('#inbound-filter button[type=submit]').click();
    await page.waitForFunction(() => !GET_INFLIGHT.size);
    assert.ok(reads().some(q => q.get('inbox_ids') === 'ibx_a' && q.get('q') === 'Sign' && q.get('has_code') === 'true'));
    await page.locator('[data-inbound-read]').click();
    await page.locator('.inbound-dialog pre').filter({hasText: '123456'}).waitFor();
    assert.equal(await page.locator('.inbound-dialog img').count(), 0);
    assert.equal(await page.evaluate(() => window.INBOUND_PWNED), undefined);
    await page.locator('.inbound-dialog [data-close]').click();
    await page.locator('[data-inbound-code]').click();
    await page.locator('.inbound-dialog pre').filter({hasText: '123456'}).waitFor();
    await page.locator('.inbound-dialog [data-close]').click();

    // A released address is Not in use, and its page keeps the messages it received.
    await go(page, '#/tools/number/ibx_old');
    await page.locator('#inbound-messages', {hasText: 'Old sign in'}).waitFor();
    assert.equal(await page.locator('#inbox-head [data-ib-status]').innerText(), 'Not in use');
    assert.equal(await page.locator('[data-inbound-release]').count(), 0, 'a released address is not released again');
    assert.ok(calls.some(c => c.p.endsWith('/messages') && new URLSearchParams(c.query).get('inbox_ids') === 'ibx_old'));

    // Messages: every inbox, or the ones picked, by channel, sender, words, code and spam, a page at a time.
    const mark = calls.length;
    const latest = () => calls.slice(mark).filter(c => c.p.endsWith('/messages') && c.method === 'GET').map(c => new URLSearchParams(c.query)).at(-1);
    await go(page, '#/tools/messages');
    const messages = page.locator('#inbox-messages'), all = page.locator('#inbound-filter[data-in-all]');
    await messages.locator('[data-in-message="ses:test:ibx_old"]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tools/messages');
    assert.equal(latest().get('inbox_ids'), null, 'no inbox picked reads every inbox');
    assert.match(await messages.locator('[data-in-message="ses:test:ibx_a"]').innerText(), /EMAIL\s+agent-acme-login@tico\.team\s+Account logins/);
    assert.deepEqual(await all.locator('select[name=inboxes] option').evaluateAll(os => os.map(o => o.value)), ['ibx_a', 'ibx_old'], 'released inboxes can be picked');
    await all.locator('select[name=inboxes]').selectOption(['ibx_a', 'ibx_old']);
    await all.locator('select[name=kind]').selectOption('sms');
    await all.locator('input[name=sender]').fill('Login service');
    await all.locator('input[name=q]').fill('Sign');
    await all.locator('input[name=has_code]').check();
    await all.locator('input[name=spam]').check();
    await all.locator('button[type=submit]').click();
    await page.waitForFunction(() => !GET_INFLIGHT.size);
    assert.deepEqual(Object.fromEntries(latest()), {limit: '50', inbox_ids: 'ibx_a,ibx_old', kind: 'sms', q: 'Sign', from: 'Login service', has_code: 'true', spam: 'true'});
    await page.evaluate(() => inboxesRender());
    await page.waitForFunction(() => !GET_INFLIGHT.size);
    assert.deepEqual(await all.locator('select[name=inboxes]').evaluate(s => [...s.selectedOptions].map(o => o.value)), ['ibx_a', 'ibx_old'], 'a redraw keeps the inboxes picked');
    assert.equal(latest().get('inbox_ids'), 'ibx_a,ibx_old');
    await all.locator('select[name=inboxes]').selectOption([]);
    await all.locator('select[name=kind]').selectOption('');
    await all.locator('input[name=sender]').fill('');
    await all.locator('input[name=q]').fill('');
    await all.locator('input[name=has_code]').uncheck();
    await all.locator('input[name=spam]').uncheck();
    await all.locator('button[type=submit]').click();
    await page.locator('#inbound-older:not([hidden])').click();
    await messages.locator('[data-in-message="ses:test:older"]').waitFor();
    assert.equal(latest().get('before'), '1', 'Older asks for the page after the one shown');
    assert.equal(latest().get('inbox_ids'), null);
    await page.locator('#inbound-newest').click();
    await messages.locator('[data-in-message="ses:test:ibx_a"] [data-inbound-code]').click();
    await page.locator('.inbound-dialog pre').filter({hasText: '123456'}).waitFor();
    await page.locator('.inbound-dialog [data-close]').click();
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 1000});
      await settled(page);
      assert.ok(await fits(page), 'Messages fits ' + width);
      await shot(page, `inbound-messages-${width}.png`);
    }
    await page.setViewportSize({width: 1440, height: 1000});

    // Joining a Tico company account: consent first, a changed code drops the company, a lost answer retries the same operation.
    await go(page, '#/tools/settings/email');
    await page.locator('#inbound-connection > summary').click();
    const codeInput = page.locator('#inbound-connection-form input[name=code]');
    const connectionTarget = page.locator('#inbound-connection-company');
    const connect = page.locator('#inbound-connection-connect');
    await codeInput.fill(connectionCode);
    await page.locator('#inbound-connection-form button[type=submit]').click();
    await connectionTarget.filter({hasText: companyId}).waitFor();
    assert.match(await connectionTarget.innerText(), /onerror/);
    assert.equal(await connectionTarget.locator('img').count(), 0);
    assert.ok(await connect.isDisabled(), 'sharing requires explicit consent');
    await page.locator('#inbound-connection-form input[name=consent]').check();
    assert.ok(await connect.isEnabled());
    await codeInput.fill('othercode_' + 'y'.repeat(33));
    assert.ok(await page.locator('#inbound-connection-preview').isHidden(), 'changing code invalidates the shown company');
    assert.ok(await connect.isDisabled());
    assert.ok(!calls.some(c => c.p.endsWith('/company-connection') && c.body.action === 'connect'));
    await codeInput.fill(connectionCode);
    await page.locator('#inbound-connection-form button[type=submit]').click();
    await connectionTarget.filter({hasText: companyId}).waitFor();
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 1000});
      await settled(page);
      assert.ok(await fits(page), 'connection preview fits ' + width);
      assert.ok(await connectionTarget.evaluate(target => target.scrollWidth <= target.clientWidth), 'company name and ID are readable at ' + width);
      await shot(page, `inbound-connection-${width}.png`);
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#inbound-connection-form input[name=consent]').check();
    await connect.click();
    await connectionInFlight;
    assert.ok(await codeInput.isDisabled(), 'committing adoption cannot discard its result after an edit');
    assert.ok(await page.locator('#inbound-connection-form input[name=consent]').isDisabled());
    releaseConnection();
    await page.locator('#inbound-connection-status').filter({hasText: 'Inbox connected to'}).waitFor();
    assert.equal(await codeInput.inputValue(), '');
    const connections = calls.filter(c => c.p.endsWith('/company-connection'));
    assert.ok(connections.every(c => JSON.stringify(Object.keys(c.body).sort()) === '["action","code"]'));
    const completed = connections.filter(c => c.body.action === 'connect');
    assert.equal(completed.length, 2, 'lost confirmation retries the same submission');
    assert.equal(completed[0].operation, completed[1].operation);
    assert.ok(!page.url().includes(connectionCode));
    assert.ok(!await page.evaluate(code => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]).includes(code), connectionCode));

    // Asking for a number opens its request's page; cancelling it there and accepting a quote in Settings use Tico's confirm.
    await go(page, '#/tools/settings/texts');
    await page.locator('[data-in-phone-open]:not([disabled])').click();
    await page.locator('.wh-dialog input[name=name]').fill('Login number');
    await page.locator('#inbound-phone').click();
    await page.locator('#inbox-head h1', {hasText: 'Login number'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tools/number/prq_new');
    assert.equal(await page.locator('#inbox-head [data-ib-status]').textContent(), 'Awaiting quote');
    assert.ok(calls.some(c => c.p.endsWith('/phone-requests') && c.method === 'POST' && c.body.name === 'Login number'));
    await page.locator('[data-inbound-phone-cancel=prq_new]').click();
    await page.locator('dialog.settings-confirm [data-confirm-ok]').click();
    await page.locator('#inbox-head [data-ib-status]', {hasText: 'Canceled'}).waitFor();
    assert.equal(await page.locator('[data-inbound-phone-cancel=prq_new]').count(), 0);
    await go(page, '#/tools/settings/texts');
    await page.locator('[data-inbound-phone-accept=prq_quoted]').click();
    await page.locator('dialog.settings-confirm [data-confirm-ok]').click();
    await page.waitForFunction(() => document.querySelector('[data-in-request=prq_quoted]') && !document.querySelector('[data-inbound-phone-accept=prq_quoted]'));
    assert.ok(calls.some(c => c.p.endsWith('/prq_quoted/accept') && c.body.price_micros === 14000000));
    assert.ok(calls.some(c => c.p.endsWith('/prq_new/cancel')));
    assert.ok(!calls.some(c => c.p.endsWith('/inboxes') && c.method === 'POST'));
    assert.ok(!calls.some(c => c.p.endsWith('/phone-quote')));

    // Newest reads again; the page fits at desktop and phone width.
    await go(page, '#/tools/number/ibx_a');
    await page.locator('[data-inbound-read]').waitFor();
    preview = 'Your verification code is ••••••.';
    await page.locator('#inbound-filter input[name=q]').fill('');
    await page.locator('#inbound-filter input[name=has_code]').uncheck();
    await page.locator('#inbound-newest').click();
    await page.locator('#inbound-messages').filter({hasText: 'Your verification code'}).waitFor();
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 1000});
      await settled(page);
      assert.ok(await fits(page), 'page fits ' + width);
      await shot(page, `inbound-${width}.png`);
    }
    await page.setViewportSize({width: 1440, height: 1000});

    // Who reads it: an admin narrows it to a group; a member reads who reads it and has no admin controls.
    await page.waitForFunction(() => S.orgGroups?.length);
    await page.evaluate(() => inboxesRender());
    await page.locator('[data-inbound-audience=ibx_a] option[value=sales]').waitFor({state: 'attached'});
    await page.locator('[data-inbound-audience=ibx_a]').selectOption('sales');
    await page.waitForFunction(() => document.querySelector('[data-inbound-audience=ibx_a]')?.value === 'sales' && !document.querySelector('[data-inbound-audience=ibx_a]').disabled);
    assert.deepEqual(calls.filter(c => c.p.endsWith('/audience')).map(c => [c.method, c.p, c.body]),
      [['PUT', '/api/v2/inbound/inboxes/ibx_a/audience', {audience: 'team', group_id: 'sales'}]]);
    await page.evaluate(() => { S.me.role = 'human'; S.me.bot_admin = false; return inboxesRender(); });
    await page.locator('[data-in-readers]', {hasText: 'Sales'}).waitFor();
    assert.equal(await page.locator('[data-inbound-audience]').count(), 0, 'a member reads who reads it');
    assert.equal(await page.locator('[data-inbound-release]').count(), 0);
    await go(page, '#/tools/settings');
    await page.locator('#set-inbound #inbound-sync').waitFor();
    assert.equal(await page.locator('#inbound-phone-card').count(), 0);
    assert.equal(await page.locator('#inbound-connection-form').count(), 0);
    assert.deepEqual(dialogs, [], 'no page dialogs');
    assert.deepEqual(errors, []);
    console.log('Inbound passed: safe text, scope, code reveal, company connection, phone requests with Tico confirms, readers, member view, desktop and phone.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
