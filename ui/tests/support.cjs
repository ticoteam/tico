// Offline browser regression: Support on the Help page. The form shows exactly what it will send, Enter files the ticket
// (Shift+Enter is a new line), the list shows each request with its status badge and an unread dot, a reply puts a count
// on the ? and opening the conversation reads it, a failed send says so in place, the trash asks first, and nothing a
// ticket says is rendered as HTML. Help lists the Beta features the server names.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, beta} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const INSTALL = '6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63';
const DIAGNOSTICS_TEXT = '{\n  "format": 1,\n  "versions": {\n    "tico": "0.2.17"\n  }\n}';
const HOSTILE = '<img src=x onerror="window.__pwned=1"> <b>bold</b>';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({enabled = true, seed = [], native = false, feedback = null, viewport = {width: 1440, height: 900}} = {}) => {
      const ctx = await browser.newContext({viewport, serviceWorkers: 'block'});
      // The desktop app's bridge (app/src/main.rs BRIDGE_SCRIPT), recording what the page posts to it.
      if (native) await ctx.addInitScript(() => {
        window.__posted = [];
        window.ticoNative = name => ({postMessage: body => window.__posted.push(name)});
        window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog'];
        window.ticoNativeVersion = '0.3.48';
        window.ticoNativeLog = async () => ({version: '0.3.48', lines: ['2026-10-16T06:47:31Z WARN tico_app: update check failed: 401']});
      });
      const page = await ctx.newPage();
      const errors = [], calls = [];
      const hq = {tickets: seed, reply: false, read: false, fail: false, confirm: true, asked: []};
      const view = () => ({enabled, tickets: hq.tickets.map(t => ({...t, unread: t.messages.filter(m => m.from === 'staff' && !hq.read).length})),
                           unread: hq.tickets.reduce((n, t) => n + t.messages.filter(m => m.from === 'staff' && !hq.read).length, 0)});
      page.on('pageerror', e => errors.push(e.message));
      page.on('dialog', d => { hq.asked.push(d.message()); return hq.confirm ? d.accept() : d.dismiss(); });
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.2.17', update: null, app_name: 'Tico', usage_count_notice: false, beta, feedback_question: feedback};
        if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p.startsWith('/api/v2/support/')) {
          const rest = p.slice('/api/v2/support/'.length), body = req.postData() ? JSON.parse(req.postData()) : null;
          calls.push(req.method() + ' ' + rest + (body ? ' ' + JSON.stringify(body) : ''));
          if (rest === 'setup-snapshot') calls.push('asked from ' + new URL(req.url()).searchParams.get('asked_from'));
          if (rest === 'setup-snapshot') return json({message: "I'd like help setting up Tico.\n\n- Computer online: no", kind: 'setup'});
          if (rest === 'feedback') return json({moment: body.moment, answered: !!body.answer, ticket: null});
          if (rest === 'compose') return json({enabled: true, email: 'ana@acme.example', version: '0.2.17', install_id: INSTALL, to: 'updates.tico.team', max: 4000});
          if (rest === 'diagnostics/capture') return json({id: 'c'.repeat(64), bytes: 2048, text: DIAGNOSTICS_TEXT});
          if (rest === 'diagnostics') return json({id: (req.method() === 'POST' ? 'e' : 'd').repeat(64), bytes: 2048, text: body?.text || DIAGNOSTICS_TEXT});
          if (rest === 'tickets' && req.method() === 'GET') return json(view());
          if (rest === 'tickets/refresh') {
            if (hq.reply && hq.tickets[0] && !hq.tickets[0].messages.length)
              Object.assign(hq.tickets[0], {status: 'answered', messages: [{id: 1, created: new Date().toISOString(), from: 'staff', body: 'Try the latest release. ' + HOSTILE}]});
            return json(view());
          }
          if (rest === 'tickets' && req.method() === 'POST') {
            const ticket = {id: 'sup_1', created: new Date().toISOString(), status: 'open', message: body.message, email: body.email,
                            sent: body.include_ids ? {version: '0.2.17', install_id: INSTALL} : {}, messages: [], unread: 0};
            hq.tickets.unshift(ticket);
            return json(ticket);
          }
          if (rest.endsWith('/read')) { hq.read = true; return json({...hq.tickets[0], unread: 0}); }
          if (rest.endsWith('/messages') && hq.fail) return json({error: {detail: 'Could not reach Tico support. Try again in a moment.'}}, 502);
          if (rest.endsWith('/messages')) { hq.tickets[0].messages.push({id: 2, created: new Date().toISOString(), from: 'person', body: body.message}); return json({...hq.tickets[0], unread: 0}); }
          if (req.method() === 'DELETE') { hq.tickets = []; return json({deleted: true}); }
        }
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json({});
      });
      await page.goto('https://tico-ui.test/#/help');
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      return {page, ctx, errors, calls, hq};
    };

    let v = await visit();
    const {page} = v;
    await page.locator('#support-rail').waitFor();
    // Wide: the overview on the left, Support in the right rail, no docs line above the overview.
    const order = p => p.evaluate(() => {
      const top = sel => document.querySelector(sel).getBoundingClientRect();
      return {rail: top('#support-rail'), docs: top('.help-docs-first'), hero: top('.help-hero'), page: top('#help-page'),
              box: top('[data-compose] [name=message]'), docsShown: getComputedStyle(document.querySelector('.help-docs-first')).display !== 'none',
              jump: document.querySelectorAll('a[href="#support-rail"]').length, height: innerHeight};
    });
    let laid = await order(page);
    assert.ok(laid.rail.left >= laid.page.right - 1, 'Support is the right rail on a wide screen');
    assert.equal(laid.docsShown, false);
    assert.equal(laid.jump, 0);
    // The overview stays short (about a quarter of the 2,180 characters it once was); the rest is in the docs.
    assert.ok((await page.locator('#help-page').innerText()).replace(/\s+/g, ' ').length < 650, 'Help overview stays short');
    // Help lists the server's Beta features, each with its pill, and the sidebar's static spots draw theirs.
    assert.deepEqual(await page.locator('#help-beta li').evaluateAll(rows => rows.map(li => li.firstChild.textContent)), beta.map(f => f.name));
    assert.equal(await page.locator('#help-beta li .pill.beta').count(), beta.length);
    assert.equal(await page.locator('[data-nav=market] .pill.beta').count(), 1);
    assert.equal(await page.locator('#nav-inboxes-section .pill.beta').count(), 1);
    // A bot is Beta by its template or harness; a bot off the list gets no pill.
    assert.deepEqual(await page.evaluate(() => [{template: 'listening'}, {template: 'inbox'}, {harness: 'hermes'},
      {agent: {harness: 'openclaw'}}, {template: 'support'}, {harness: 'claude'}].map(e => !!botBetaPill(e))),
      [true, true, true, true, false, false]);
    const form = page.locator('[data-compose]'), box = form.locator('[name=message]');
    const requests = page.locator('[data-requests] .support-item'), help = page.locator('#help-open');
    // No requests yet: a friendly prompt with the composer, no list.
    assert.match(await page.locator('.support-empty').innerText(), /Ask Tico support/);
    assert.equal(await page.locator('[data-list]').isVisible(), false);
    assert.equal(await form.locator('[name=diag]').isChecked(), true);
    assert.equal(await page.locator('.help-pieces').count(), 0);
    assert.equal(await page.locator('.help-actions .primary').count(), 0);
    assert.match(await page.locator('.help-hero').innerText(), /human and AI teammates/);
    await form.locator('[data-preview]').click();
    const dialog = page.locator('dialog.support-modal');
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    assert.equal(await dialog.locator('textarea').inputValue(), DIAGNOSTICS_TEXT);
    await dialog.locator('textarea').fill('{"format":1}');
    assert.equal(await dialog.locator('[data-use]').isDisabled(), true);
    await dialog.locator('[data-save]').click();
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    await dialog.locator('[data-use]').click();
    // Shift+Enter is a new line; Enter sends.
    await box.fill('The board will not load.');
    await box.press('Shift+Enter');
    await box.pressSequentially(HOSTILE);
    assert.equal(await box.inputValue(), 'The board will not load.\n' + HOSTILE);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'Shift+Enter does not send');
    await box.press('Enter');
    await page.locator('.support-msg.you').waitFor();
    const sent = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.equal(sent.diagnostics, 'e'.repeat(64), 'the edited preview is sent');
    assert.equal(sent.message, 'The board will not load.\n' + HOSTILE);
    assert.equal(await form.locator('[name=diag]').isChecked(), false, 'follow-up attachments are opt-in');
    assert.match(await page.locator('[data-status] .support-badge').innerText(), /Open/);
    assert.match(await page.locator('.support-delivered').innerText(), /Delivered to Tico support/);
    assert.match(await page.locator('[data-thread]').innerText(), /<img src=x/);
    assert.equal(await page.locator('[data-thread] img, [data-thread] b, [data-requests] img').count(), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);

    // Back to the list, a reply arrives: an unread dot, an Answered badge and a count on the ?.
    await box.fill('Draft kept during refresh');
    await page.locator('[data-back]').click();
    assert.equal(await requests.count(), 1);
    assert.equal(await help.getAttribute('data-support-unread'), null);
    v.hq.reply = true;
    await page.evaluate(() => window.supportPoll());
    assert.equal(await requests.locator('.support-dot').count(), 1);
    assert.equal(await requests.locator('.support-badge').innerText(), 'Answered');
    assert.match(await requests.locator('.support-item-title').innerText(), /^The board will not load\. <img/);
    assert.equal(await help.getAttribute('data-support-unread'), '1');
    assert.equal(await help.evaluate(el => getComputedStyle(el, '::after').content), '"1"');
    assert.match(await help.getAttribute('aria-label'), /1 new support reply/);
    // Opening the conversation reads it: bubbles from each side, the count leaves the ?.
    await requests.first().click();
    await page.waitForFunction(() => !document.querySelector('#help-open')?.hasAttribute('data-support-unread'));
    assert.ok(v.calls.includes('POST tickets/sup_1/read'));
    assert.equal(await help.evaluate(el => getComputedStyle(el, '::after').content), 'none');
    assert.match(await page.locator('.support-msg.staff .support-who').innerText(), /^Tico support/);
    assert.match(await page.locator('.support-msg.staff').innerText(), /Try the latest release/);
    assert.equal(await page.locator('.support-msg.staff img').count(), 0);
    assert.equal(await box.inputValue(), 'Draft kept during refresh');
    await form.locator('[name=diag]').check();
    await form.getByRole('button', {name: 'Send'}).click();
    await page.waitForFunction(() => document.querySelectorAll('.support-msg').length === 3);
    assert.ok(v.calls.some(c => c.startsWith('POST tickets/sup_1/messages') && c.includes('"diagnostics":"' + 'd'.repeat(64))));

    // A send that fails says so where the person is looking, and keeps the message.
    v.hq.fail = true;
    await box.fill('Is anyone there?');
    await box.press('Enter');
    await form.locator('[data-error]:not([hidden])').waitFor();
    assert.match(await form.locator('[data-error]').innerText(), /^Not sent\. Could not reach Tico support.*still here/);
    assert.equal(await box.inputValue(), 'Is anyone there?');
    assert.equal(await form.getByRole('button', {name: 'Send'}).isDisabled(), false);
    assert.equal(await page.locator('.support-msg').count(), 3);
    v.hq.fail = false;

    await page.screenshot({path: '/tmp/tico-support-desktop.png', fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'phone has no horizontal overflow');
    // finish the existing navigation drawer's resize transition (every finite animation has ended)
    await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity), null, {timeout: t(5000)});
    await page.locator('#support-rail').scrollIntoViewIfNeeded();
    await page.screenshot({path: '/tmp/tico-support-phone.png', fullPage: false});
    // The trash asks first; saying no keeps the request.
    const trash = page.getByRole('button', {name: 'Delete request'});
    v.hq.confirm = false;
    await trash.click();
    assert.equal(v.hq.asked.length, 1);
    assert.ok(!v.calls.some(c => c.startsWith('DELETE')));
    v.hq.confirm = true;
    await trash.click();
    await page.locator('.support-empty').waitFor();
    assert.ok(v.calls.some(c => c.startsWith('DELETE')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Existing requests: newest activity first, each with its badge, unread dots, and the count of replies on the ?.
    const at = m => new Date(Date.now() - m * 60000).toISOString();
    const reply = (id, m) => ({id, created: at(m), from: 'staff', body: 'Reply ' + id});
    v = await visit({seed: [
      {id: 'sup_a', created: at(300), status: 'closed', message: 'Old question', messages: [reply(1, 200)], sent: {}},
      {id: 'sup_b', created: at(100), status: 'open', message: 'Waiting question', messages: [], sent: {}},
      {id: 'sup_c', created: at(400), status: 'answered', message: 'Answered question', messages: [reply(1, 50), reply(2, 5)], sent: {}},
    ]});
    await v.page.locator('#support-rail [data-requests] .support-item').first().waitFor();
    const rows = await v.page.locator('[data-requests] .support-item').evaluateAll(els => els.map(el =>
      [el.querySelector('.support-item-title').textContent, el.querySelector('.support-badge').textContent, !!el.querySelector('.support-dot')]));
    assert.deepEqual(rows, [['Answered question', 'Answered', true], ['Waiting question', 'Open', false], ['Old question', 'Closed', true]]);
    assert.equal(await v.page.locator('#help-open').getAttribute('data-support-unread'), '3');
    assert.ok(!v.calls.some(c => c.endsWith('/read')), 'nothing is read until a conversation is opened');
    // A long conversation opens at its newest reply. Scrolled up to read, a new reply doesn't move it: the pill offers it.
    const long = v.hq.tickets.find(x => x.id === 'sup_c');
    for (let i = 3; i < 30; i++) long.messages.push({...reply(i, 4), body: `Reply ${i}\n` + 'More detail. '.repeat(20)});
    await v.page.evaluate(() => window.supportPoll());
    await v.page.locator('[data-requests] .support-item').first().click();
    const thread = v.page.locator('[data-thread]'), gap = () => thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
    await v.page.waitForFunction(() => document.querySelectorAll('.support-msg').length === 30);
    assert.ok(await gap() < 2, 'opens at the newest reply');
    await thread.hover();
    await v.page.mouse.wheel(0, -600);
    await v.page.waitForFunction(() => { const el = document.querySelector('[data-thread]'); return el.scrollHeight - el.scrollTop - el.clientHeight > 300; });
    const top = await thread.evaluate(el => el.scrollTop);
    long.messages.push({...reply(30, 0), body: 'A new reply while you read'});
    await v.page.evaluate(() => window.supportPoll());
    await v.page.waitForFunction(() => document.querySelectorAll('.support-msg').length === 31);
    assert.equal(await thread.evaluate(el => el.scrollTop), top, 'a reader scrolled up is not moved');
    const pill = v.page.locator('#support-rail .stick-pill');
    await pill.waitFor();
    await pill.click();
    await v.page.waitForFunction(() => { const el = document.querySelector('[data-thread]'); return el.scrollHeight - el.scrollTop - el.clientHeight < 2; });
    assert.equal(await pill.isVisible(), false);
    // Scrolled up with the pill showing, then the last request is deleted: the empty state starts clean, no stale pill.
    v.hq.tickets = [long];
    await thread.hover();
    await v.page.mouse.wheel(0, -600);
    await v.page.waitForFunction(() => { const el = document.querySelector('[data-thread]'); return el.scrollHeight - el.scrollTop - el.clientHeight > 300; });
    long.messages.push({...reply(31, 0), body: 'Another reply while you read'});
    await v.page.evaluate(() => window.supportPoll());
    await pill.waitFor();
    await v.page.waitForTimeout(800);                         // past the 600 ms reader window, as a real reader would be
    await v.page.getByRole('button', {name: 'Delete request'}).click();
    await v.page.locator('.support-empty').waitFor();
    assert.equal(await pill.isVisible(), false, 'no stale pill after the conversation changes');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Inside the desktop app: Help shows both versions and opens the app's log; a request carries the app's version
    // labelled apart from the server's, and diagnostics carry the end of the app's log.
    v = await visit({native: true});
    await v.page.locator('#support-rail').waitFor();
    assert.equal(await v.page.locator('.help-foot').innerText(), "Version v0.2.17 · App v0.3.48 · Show the app's log");
    await v.page.locator('#help-app-log').click();
    assert.deepEqual(await v.page.evaluate(() => window.__posted), ['showLog']);
    assert.match(await v.page.locator('[data-sent]').innerText(), /version 0\.2\.17, app version 0\.3\.48, install ID/);
    await v.page.locator('[data-compose] [name=message]').fill('The app did not update.');
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.locator('.support-msg.you').waitFor();
    const captured = JSON.parse(v.calls.find(c => c.startsWith('POST diagnostics/capture {')).slice('POST diagnostics/capture '.length));
    assert.deepEqual(captured.app, {version: '0.3.48', lines: ['2026-10-16T06:47:31Z WARN tico_app: update check failed: 401'], mac: false});
    const filed = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.equal(filed.app_version, '0.3.48');
    assert.equal(filed.diagnostics, 'c'.repeat(64));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A phone: Support first, the composer in view without scrolling, the docs link right under it, then the overview.
    // Reviewing and editing the diagnostics still works there.
    v = await visit({viewport: {width: 390, height: 844}});
    await v.page.locator('#support-rail').waitFor();
    laid = await order(v.page);
    assert.equal(await v.page.evaluate(() => document.querySelector('#main').scrollTop), 0);
    assert.ok(laid.rail.top < laid.docs.top && laid.docs.top < laid.hero.top, 'Support, then the docs link, then the overview');
    assert.ok(laid.docsShown && laid.docs.top - laid.rail.bottom < 30, 'the docs link sits directly under Support');
    assert.ok(laid.box.top >= 0 && laid.box.bottom <= laid.height, 'the composer is in view without scrolling');
    assert.equal(laid.jump, 0);
    assert.equal(await v.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'phone has no horizontal overflow');
    await v.page.locator('[data-compose] [data-preview]').click();
    const phoneDialog = v.page.locator('dialog.support-modal');
    await phoneDialog.locator('[data-use]:not([disabled])').waitFor();
    assert.equal(await phoneDialog.locator('textarea').inputValue(), DIAGNOSTICS_TEXT);
    await phoneDialog.locator('textarea').fill('{"format":1,"note":"phone"}');
    await phoneDialog.locator('[data-save]').click();
    await phoneDialog.locator('[data-use]:not([disabled])').waitFor();
    await phoneDialog.locator('[data-use]').click();
    await phoneDialog.waitFor({state: 'detached'});
    assert.equal(await v.page.locator('[data-compose] [name=diag]').isChecked(), true);
    await v.page.screenshot({path: '/tmp/tico-support-phone-first.png'});
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A tablet, between the phone and the rail layout: Support still comes first, full width, nothing sideways.
    v = await visit({viewport: {width: 900, height: 1000}});
    await v.page.locator('#support-rail').waitFor();
    laid = await order(v.page);
    assert.ok(laid.rail.top < laid.docs.top && laid.docs.top < laid.hero.top, 'Support first on a tablet');
    assert.ok(Math.abs(laid.rail.width - laid.page.width) < 2, 'Support spans the overview\'s width');
    assert.equal(await v.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // "Want help setting up?" opens a new request that starts from the snapshot, which the person reads and sends as setup.
    v = await visit({seed: [{id: 'sup_0', created: new Date().toISOString(), status: 'open', message: 'Earlier', sent: {}, messages: []}]});
    await v.page.locator('#support-rail').waitFor();
    await v.page.evaluate(() => {
      document.body.insertAdjacentHTML('beforeend', '<button type="button" data-setup-help="health">Want help setting up?</button>');
      document.querySelector('[data-setup-help]').click();
    });
    await v.page.waitForFunction(() => document.querySelector('#support-message')?.value.startsWith("I'd like help setting up Tico."));
    assert.ok(v.calls.includes('asked from health'));
    assert.match(await v.page.locator('[data-sent]').innerText(), /message marked setup/);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'nothing is sent before the person presses Send');
    // A second press keeps the draft as it is: one snapshot, not two.
    await v.page.evaluate(() => document.querySelector('[data-setup-help]').click());
    await v.page.waitForFunction(() => document.querySelector('[data-setup-help]') && !document.querySelector('[data-setup-help]').disabled);
    await v.page.waitForTimeout(100);
    assert.equal(v.calls.filter(c => c === 'asked from health').length, 2);
    assert.equal((await v.page.locator('#support-message').inputValue()).split("I'd like help setting up Tico.").length, 2);
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.locator('.support-msg.you', {hasText: 'Computer online'}).waitFor();
    assert.equal(JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length)).kind, 'setup');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // "How is Tico working for you?": Send files the answer as feedback, and the question goes.
    v = await visit({feedback: {moment: 'day_7', question: 'How is Tico working for you?'}});
    const ask = v.page.locator('#feedback-ask');
    await ask.waitFor();
    await ask.locator('[name=answer]').fill('Faster than I expected');
    await ask.locator('[type=submit]').click();
    await ask.waitFor({state: 'hidden'});
    assert.ok(v.calls.includes('POST feedback {"moment":"day_7","answer":"Faster than I expected"}'));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    v = await visit({enabled: false});
    await v.page.waitForSelector('#help-page [data-support-off]');
    assert.match(await v.page.locator('[data-support-off]').innerText(), /turned off/);
    assert.equal(await v.page.locator('[data-help-support]').count(), 0, 'no pointer to a Support that is off');
    assert.equal(await v.page.locator('#support-rail').count(), 0);
    assert.ok(!v.calls.some(c => c.startsWith('POST')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
