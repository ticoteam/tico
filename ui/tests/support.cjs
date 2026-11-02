// Offline browser regression: the Support pop-up. The ? opens it over the current screen (a bottom sheet on a phone)
// on the latest request that isn't Closed or Removed (Your requests when that one is a day old and read); Esc, ✕ and
// a click outside close it and give focus back; unsent
// words survive. The box shows exactly what it will send under Attach details, Enter files the request (Shift+Enter is
// a new line) or continues the latest one, Your requests lists each request with its status badge and an unread dot,
// a reply is counted (never on Ask's button) and opening the conversation reads it, a failed send says so in place, the trash asks
// first, and nothing a request says is rendered as HTML. A #/support link (an Assistant reply's "Loop in Tico
// support"), Report this, Want help setting up? and old #/help links open it; with support off it says so and offers nothing to send.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const fs = require('node:fs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const INSTALL = '6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63';
const DIAGNOSTICS_TEXT = '{\n  "format": 1,\n  "versions": {\n    "tico": "0.2.17"\n  }\n}';
const HOSTILE = '<img src=x onerror="window.__pwned=1"> <b>bold</b>';
const UNREACHABLE = 'Not sent. Could not reach Tico support. Try again in a moment.';
// The reply promise (reply-promise.md): before sending and after it.
const PROMISE = 'We usually reply within 1 business day (Monday to Friday).';
const EMPTY = ['Ask us anything. ' + PROMISE, "Tico's support assistant answers first and brings in a person when it needs one."];
const DELIVERED = `Delivered to Tico support. ${PROMISE} The reply will appear here.`;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({enabled = true, seed = [], native = false, feedback = null, viewport = {width: 1440, height: 900}, access = null, hash = '#/tasks', theme = 'light', keyboard = false, userAgent} = {}) => {
      const ctx = await browser.newContext({viewport, serviceWorkers: 'block', userAgent});
      await ctx.addInitScript(v => localStorage.setItem('tico.theme', v), theme);   // ui/theme.js
      // A phone keyboard, as iOS Safari reports it: the layout viewport keeps its size while window.visualViewport
      // shrinks to what is left above the keyboard and pans (offsetTop); pinch zoom divides its height by the scale.
      // __keys(height, top, scale) sets it.
      if (keyboard) await ctx.addInitScript(() => {
        const vv = Object.assign(new EventTarget(), {height: 0, width: 0, offsetTop: 0, offsetLeft: 0, scale: 1});
        Object.defineProperty(window, 'visualViewport', {value: vv, configurable: true});
        window.__keys = (height, top = 0, scale = 1) => { Object.assign(vv, {height, offsetTop: top, scale}); vv.dispatchEvent(new Event('resize')); };
      });
      // The desktop app's bridge (app/src/main.rs BRIDGE_SCRIPT), recording what the page posts to it.
      if (native) await ctx.addInitScript(() => {
        window.__posted = [];
        window.ticoNative = name => ({postMessage: body => window.__posted.push(name)});
        window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog'];
        window.ticoNativeVersion = '0.3.48';
        window.ticoNativeLog = async () => ({version: '0.3.48', lines: ['2026-10-16T06:47:31Z WARN tico_app: update check failed: 401']});
      });
      const page = await ctx.newPage();
      // An iPhone's home bar: a 34 px safe area at the bottom.
      if (keyboard) await (await ctx.newCDPSession(page)).send('Emulation.setSafeAreaInsetsOverride', {insets: {bottom: 34}});
      const errors = [], calls = [];
      // `seen`: how many of a request's replies the person has read (POST tickets/<id>/read).
      // `gate`: a promise that holds a follow-up's POST until the test resolves it (a slow HQ).
      const hq = {tickets: seed, seen: {}, reply: false, fail: false, abort: false, pollFail: false, confirm: true, asked: [], access, accessGate: null, gate: null, refreshGate: null, refreshStarted: null};
      const unread = t => t.messages.filter(m => m.from === 'staff').length - (hq.seen[t.id] || 0);
      const out = t => ({...t, unread: unread(t)});
      const view = () => ({enabled, tickets: hq.tickets.map(out), unread: hq.tickets.reduce((n, t) => n + unread(t), 0)});
      page.on('pageerror', e => errors.push(e.message));
      page.on('dialog', d => { hq.asked.push(d.message()); return hq.confirm ? d.accept() : d.dismiss(); });
      await page.route('**/*', async route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.2.17', update: null, app_name: 'Tico', usage_count_notice: false, beta, feedback_question: feedback, changelog: {unread_count: 2}};
        if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p.startsWith('/api/v2/support/')) {
          const rest = p.slice('/api/v2/support/'.length), body = req.postData() ? JSON.parse(req.postData()) : null;
          calls.push(req.method() + ' ' + rest + (body ? ' ' + JSON.stringify(body) : ''));
          if (rest === 'access' && req.method() === 'GET' && hq.accessGate) await hq.accessGate;
          if (rest === 'access' && req.method() === 'GET') return json(hq.access || {available: false, off_reason: 'TICO_SUPPORT_ACCESS', active: null, last: null, sessions: []});
          if (rest === 'access') {
            hq.access.active = hq.access.last = {id: 'sa_1', ticket_id: body.ticket_id, tier: body.tier, started_by: 'human:ana', seconds_left: body.hours * 3600 - 48 * 60};
            hq.access.sessions = [hq.access.active, ...(hq.access.sessions || [])];
            return json(hq.access.active);
          }
          if (rest === 'access/sa_1/stop') {
            const stopped = {...hq.access.active, seconds_left: 0, ended_at: new Date().toISOString(), end_reason: 'stopped'};
            hq.access.active = null; hq.access.sessions = hq.access.sessions.map(x => x.id === stopped.id ? stopped : x);
            return json(stopped);
          }
          if (rest === 'access/sa_1/log') return json({log: [{created_at: new Date().toISOString(), by: 'support-agent', method: 'GET', path: '/api/v2/health' + HOSTILE, status: 200}]});
          if (rest === 'setup-snapshot') calls.push('asked from ' + new URL(req.url()).searchParams.get('asked_from'));
          if (rest === 'setup-snapshot') return json({message: "I'd like help setting up Tico.\n\nSetup snapshot (asked from the setup wizard):\n- Steps done: 2 of 5; left: Add a computer, Sign in a model, First bot ready\n- Computer online: no\n- Model signed in: no\n- First bot ready: no\n- Bots in Needs setup: 3", kind: 'setup'});
          if (rest === 'feedback') return json({moment: body.moment, answered: !!body.answer, ticket: null});
          if (rest === 'compose') return json({enabled: true, email: 'ana@acme.example', version: '0.2.17', install_id: INSTALL, to: 'updates.tico.team', max: 4000});
          if (rest === 'diagnostics/capture' && hq.captureGate) await hq.captureGate;
          if (rest === 'diagnostics/capture') return json({id: hq.captureId || 'c'.repeat(64), bytes: 2048, text: DIAGNOSTICS_TEXT});
          if (rest === 'diagnostics') return json({id: (req.method() === 'POST' ? 'e' : 'd').repeat(64), bytes: 2048, text: body?.text || DIAGNOSTICS_TEXT});
          if (rest === 'tickets' && req.method() === 'GET') return json(view());
          if (rest === 'tickets/refresh') {
            if (hq.pollFail) return json({error: {detail: 'Could not reach Tico support. Try again in a moment.'}}, 502);
            if (hq.reply && hq.tickets[0] && !hq.tickets[0].messages.some(m => m.from === 'staff'))
              Object.assign(hq.tickets[0], {status: 'answered', messages: [...hq.tickets[0].messages, {id: 1, created_at: new Date().toISOString(), from: 'staff', body: 'Try the latest release. ' + HOSTILE}]});
            const snapshot = view();
            if (hq.refreshGate) { hq.refreshStarted?.(); await hq.refreshGate; return json(snapshot); }
            return json(view());
          }
          if (rest === 'tickets' && req.method() === 'POST') {
            const ticket = {id: 'sup_' + (hq.tickets.length + 1), created_at: new Date().toISOString(), status: 'open', message: body.message, email: body.email,
                            sent: body.include_ids ? {version: '0.2.17', install_id: INSTALL} : {}, messages: []};
            hq.tickets.unshift(ticket);
            return json(out(ticket));
          }
          const id = decodeURIComponent(rest.split('/')[1] || ''), ticket = hq.tickets.find(x => x.id === id);
          if (rest.endsWith('/read')) { hq.seen[id] = ticket.messages.filter(m => m.from === 'staff').length; return json(out(ticket)); }
          if (rest.endsWith('/messages') && hq.gate) await hq.gate;
          if (rest.endsWith('/messages') && hq.abort) return route.abort('connectionrefused');
          if (rest.endsWith('/messages') && hq.fail) return json({error: {detail: 'Could not reach Tico support. Try again in a moment.'}}, 502);
          if (rest.endsWith('/messages')) { ticket.messages.push({id: ticket.messages.length + 1, created_at: new Date().toISOString(), from: 'person', body: body.message}); return json(out(ticket)); }
          if (req.method() === 'DELETE') { hq.tickets = hq.tickets.filter(x => x.id !== id); return json({deleted: true}); }
        }
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/' + hash);
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      return {page, ctx, errors, calls, hq};
    };
    // A link to #/support, as an Assistant reply carries ("Loop in Tico support"), opens Support over the page (the
    // app's click handler for any such link, ui/app/ask-panel.js); it is ready once the box (or the "turned off" line)
    // shows. Focus goes back to the link on closing.
    const supportLink = page => page.evaluate(() => {
      if (document.getElementById('t-support')) return;
      const a = Object.assign(document.createElement('a'), {id: 't-support', href: '#/support', textContent: 'Loop in Tico support'});
      a.style.cssText = 'position:fixed;top:4px;left:50%;z-index:30';
      document.body.append(a);
    });
    const openSupport = async page => {
      await supportLink(page);
      await page.locator('#t-support').click();
      await page.locator('#support-pop[open] [data-compose]:not([hidden]) [name=message], #support-pop[open] [data-off]:not([hidden])').first().waitFor();
      await page.waitForFunction(() => !document.querySelector('#support-pop [data-connection]')?.textContent);
    };
    const active = page => page.evaluate(() => ({id: document.activeElement?.id, name: document.activeElement?.getAttribute('name'), inPop: !!document.activeElement?.closest('#support-pop')}));

    let v = await visit();
    const {page} = v;
    // The ? opens the pop-up over Tasks: bottom-left beside the sidebar, about 400 px wide, the address unchanged.
    await openSupport(page);
    const pop = page.getByRole('dialog', {name: 'Support'});
    assert.equal(await pop.count(), 1, 'a dialog named Support');
    assert.equal(await page.evaluate(() => location.hash), '#/tasks');
    assert.equal(await page.locator('#help-page, #support-rail').count(), 0, 'no Help page');
    const box0 = await pop.boundingBox(), side = await page.locator('#side').boundingBox();
    assert.ok(box0.x >= side.x + side.width && box0.x - (side.x + side.width) < 30, 'beside the sidebar');
    assert.ok(900 - (box0.y + box0.height) < 30, 'at the bottom');
    assert.ok(Math.abs(box0.width - 400) <= 2 && box0.height <= 640, `about 400 px wide and at most 640 px tall (${box0.width}x${box0.height})`);
    assert.deepEqual(await active(page), {id: 'support-message', name: 'message', inPop: true}, 'focus in the box');
    // The link row: docs in a new tab, What's new with its count, Your requests.
    const docs = pop.getByRole('link', {name: 'View docs'});
    assert.equal(await docs.getAttribute('href'), 'https://github.com/ticoteam/tico/blob/main/docs/README.md');
    assert.equal(await docs.getAttribute('target'), '_blank');
    assert.equal(await pop.locator('[data-whats-new]').innerText(), "What's new (2)");
    assert.equal(await pop.locator('[data-mine]').innerText(), 'Your requests');
    // No requests yet: an empty chat and the box; Attach details closed, with the diagnostics on for a new request.
    // The promise and who answers, as two lines of text read in order (a screen reader gets the same paragraphs).
    assert.deepEqual(await pop.locator('.support-empty p').allInnerTexts(), EMPTY);
    assert.equal(await pop.locator('.support-empty').innerText(), EMPTY.join('\n\n'));
    assert.doesNotMatch(await pop.innerText(), /A person at Tico reads every message/);
    assert.equal(await pop.locator('.support-empty [aria-hidden], .support-empty [hidden]').count(), 0);
    assert.equal(await pop.locator('[data-head]').isVisible(), false);
    const form = pop.locator('[data-compose]'), box = form.locator('[name=message]'), attach = form.locator('[data-attach]');
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico support…');
    assert.equal(await box.getAttribute('maxlength'), '4000');
    assert.equal(await attach.evaluate(el => el.open), false, 'Attach details is closed by default');
    assert.equal(await attach.locator('[data-attach-label]').innerText(), 'Details attached');
    assert.equal(await form.locator('[name=diag]').isChecked(), true);
    await attach.locator('[data-attach-label]').click();
    assert.match(await form.locator('[data-sent]').innerText(), /^Sends to updates\.tico\.team: message, email ana@acme\.example, version 0\.2\.17, install ID .*redacted diagnostics\.$/);
    await form.locator('[data-preview]').click();
    const dialog = page.locator('dialog.support-modal');
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    assert.equal(await dialog.locator('textarea').inputValue(), DIAGNOSTICS_TEXT);
    await dialog.locator('textarea').fill('{"format":1}');
    assert.equal(await dialog.locator('[data-use]').isDisabled(), true);
    await dialog.locator('[data-save]').click();
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    await dialog.locator('[data-use]').click();
    assert.equal(await pop.isVisible(), true, 'closing Review and edit leaves Support open');
    // Shift+Enter is a new line; Enter sends.
    await box.fill('The board will not load.');
    await box.press('Shift+Enter');
    await box.pressSequentially(HOSTILE);
    assert.equal(await box.inputValue(), 'The board will not load.\n' + HOSTILE);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'Shift+Enter does not send');
    await box.press('Enter');
    await pop.locator('.support-msg.you').waitFor();
    const sent = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.equal(sent.diagnostics, 'e'.repeat(64), 'the edited preview is sent');
    assert.equal(sent.message, 'The board will not load.\n' + HOSTILE);
    // The chat now shows the new request: its title and status above, a You bubble, the delivered line.
    assert.equal(await form.locator('[name=diag]').isChecked(), false, 'follow-up attachments are opt-in');
    assert.equal(await attach.locator('[data-attach-label]').innerText(), 'Attach details');
    assert.match(await pop.locator('[data-title]').innerText(), /^The board will not load\.$/, 'the first line is the title');
    assert.match(await pop.locator('[data-status] .support-badge').innerText(), /Open/);
    assert.equal(await pop.locator('.support-msg.you .support-who').innerText().then(s => s.split(' · ')[0]), 'You');
    assert.equal(await pop.locator('.support-delivered').innerText(), DELIVERED);
    assert.match(await pop.locator('[data-thread]').innerText(), /<img src=x/);
    assert.equal(await page.locator('[data-thread] img, [data-thread] b, [data-requests] img').count(), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);

    // Tab stays inside the pop-up, both ways.
    for (const key of ['Tab', 'Shift+Tab']) for (let i = 0; i < 20; i++) {
      await page.keyboard.press(key);
      assert.equal((await active(page)).inPop, true, `${key} stays in the pop-up`);
    }
    // Esc closes it and gives focus back to the link; words typed but not sent are there again on reopening.
    await box.fill('Draft kept while closed');
    await page.keyboard.press('Escape');
    await pop.waitFor({state: 'hidden'});
    assert.equal((await active(page)).id, 't-support', 'focus back on the link');
    await openSupport(page);
    assert.equal(await box.inputValue(), 'Draft kept while closed');
    // A click outside closes it, and so does ✕.
    await page.mouse.click(1300, 300);
    await pop.waitFor({state: 'hidden'});
    assert.equal((await active(page)).id, 't-support');
    await openSupport(page);
    await pop.getByRole('button', {name: 'Close'}).click();
    await pop.waitFor({state: 'hidden'});
    assert.equal((await active(page)).id, 't-support');
    assert.equal(await page.evaluate(() => location.hash), '#/tasks', 'nothing behind it changed');

    // A reply arrives while it is closed: counted, none on Ask's button, and the toast, which opens the pop-up on that
    // request and reads it.
    const help = page.locator('#ask-row'), unread = () => page.evaluate(() => window.supportState().unread);
    assert.equal(await unread(), 0);
    v.hq.reply = true;
    await page.evaluate(() => window.supportPoll());
    assert.equal(await unread(), 1);
    assert.equal(await help.evaluate(el => el.hasAttribute('data-support-count') || getComputedStyle(el, '::after').content), 'none', 'no count on Ask');
    const replied = page.locator('.toast', {hasText: 'Tico support replied.'});
    await replied.getByRole('button', {name: 'Open'}).click();
    await pop.locator('.support-msg.staff').waitFor();
    await page.waitForFunction(() => window.supportState().unread === 0);
    assert.ok(v.calls.includes('POST tickets/sup_1/read'));
    assert.match(await pop.locator('.support-msg.staff .support-who').innerText(), /^Tico support/);
    assert.match(await pop.locator('.support-msg.staff').innerText(), /Try the latest release/);
    assert.equal(await pop.locator('.support-msg.staff img').count(), 0);
    assert.equal(await box.inputValue(), 'Draft kept while closed');
    // Enter on the latest open request continues it: no new request.
    await attach.locator('[data-attach-label]').click();
    await form.locator('[name=diag]').check();
    await form.getByRole('button', {name: 'Send'}).click();
    await page.waitForFunction(() => document.querySelectorAll('#support-pop .support-msg').length === 3);
    assert.ok(v.calls.some(c => c.startsWith('POST tickets/sup_1/messages') && c.includes('"diagnostics":"' + 'd'.repeat(64))));
    assert.equal(v.calls.filter(c => c.startsWith('POST tickets {')).length, 1, 'still one request');
    // Your requests: the list, newest first, with status and unread dot; the back arrow returns to the chat.
    await pop.locator('[data-mine]').click();
    const requests = pop.locator('[data-requests] .support-item');
    assert.equal(await requests.count(), 1);
    assert.equal(await requests.locator('.support-badge').innerText(), 'Answered');
    assert.equal(await pop.locator('[data-compose]').isVisible(), false);
    await pop.getByRole('button', {name: 'Back to the chat'}).click();
    assert.equal(await box.isVisible(), true);

    // A send that fails says so where the person is looking, and keeps the message: HQ down, or no connection at all.
    for (const how of ['fail', 'abort']) {
      v.hq[how] = true;
      await box.fill('Is anyone there?');
      await box.press('Enter');
      await form.locator('[data-error]:not([hidden])').waitFor();
      assert.equal(await form.locator('[data-error]').innerText(), UNREACHABLE, how);
      assert.equal(await box.inputValue(), 'Is anyone there?');
      assert.equal(await form.getByRole('button', {name: 'Send'}).isDisabled(), false);
      assert.equal(await pop.locator('.support-msg').count(), 3);
      assert.doesNotMatch(await pop.locator('[data-thread]').innerText(), /business day/, 'no promise on an answered request or a failed send');
      v.hq[how] = false;
    }
    // A check for replies that fails says so; what was loaded stays.
    v.hq.pollFail = true;
    await page.evaluate(() => window.supportPoll());
    assert.equal(await pop.locator('[data-connection]').innerText(), 'Could not check for replies just now. Tico will try again.');
    assert.equal(await pop.locator('.support-msg').count(), 3);
    v.hq.pollFail = false;
    await page.evaluate(() => window.supportPoll());
    assert.equal(await pop.locator('[data-connection]').isVisible(), false);

    await page.screenshot({path: '/tmp/tico-support-desktop.png'});
    // What's new opens its page and closes the pop-up.
    await pop.locator('[data-whats-new]').click();
    await pop.waitFor({state: 'hidden'});
    assert.equal(await page.evaluate(() => location.hash), '#/changelog');
    // A phone: a bottom sheet, nothing sideways.
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity), null, {timeout: t(5000)});
    await openSupport(page);
    const sheet = await pop.boundingBox();
    assert.ok(sheet.x === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - 844) < 2, `a bottom sheet (${JSON.stringify(sheet)})`);
    assert.ok(sheet.height > 844 * 0.8, 'fills most of the screen');
    assert.equal(await pop.evaluate(el => el.scrollWidth <= el.clientWidth && [...el.querySelectorAll('*')].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll');
    for (const sel of ['[data-compose] [name=message]', '[data-docs]', '[data-mine]', '.support-msg.staff']) assert.equal(await pop.locator(sel).first().isVisible(), true, sel);
    await page.waitForTimeout(300);
    await page.screenshot({path: '/tmp/tico-support-phone.png'});
    // Swiping the sheet down closes it, back to the link.
    await pop.locator('.support-head').evaluate(el => {
      const at = y => [new Touch({identifier: 1, target: el, clientX: 200, clientY: y})];
      el.dispatchEvent(new TouchEvent('touchstart', {bubbles: true, touches: at(120), changedTouches: at(120)}));
      el.dispatchEvent(new TouchEvent('touchmove', {bubbles: true, touches: at(320), changedTouches: at(320)}));
      el.dispatchEvent(new TouchEvent('touchend', {bubbles: true, touches: [], changedTouches: at(320)}));
    });
    await pop.waitFor({state: 'hidden'});
    assert.equal((await active(page)).id, 't-support');
    // The trash asks first; saying no keeps the request.
    await openSupport(page);
    const trash = pop.getByRole('button', {name: 'Delete request'});
    v.hq.confirm = false;
    await trash.click();
    assert.equal(v.hq.asked.length, 1);
    assert.ok(!v.calls.some(c => c.startsWith('DELETE')));
    v.hq.confirm = true;
    await trash.click();
    await pop.locator('.support-empty').waitFor();
    assert.ok(v.calls.includes('DELETE tickets/sup_1'));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Existing requests: it opens on the one with the most recent message that isn't Closed or Removed, at its newest
    // message; Your requests lists them newest first with their badges and dots, and the ? counts every unread reply.
    const at = m => new Date(Date.now() - m * 60000).toISOString();
    const reply = (id, m) => ({id, created_at: at(m), from: 'staff', body: 'Reply ' + id});
    v = await visit({seed: [
      {id: 'sup_a', created_at: at(300), status: 'closed', message: 'Old question', messages: [reply(1, 1)], sent: {}},
      {id: 'sup_b', created_at: at(100), status: 'open', message: 'Waiting question\nIt still fails after a restart.', messages: [], sent: {}},
      {id: 'sup_c', created_at: at(400), status: 'answered', message: 'Answered question', messages: [reply(1, 50), reply(2, 5)], sent: {}},
      {id: 'sup_d', created_at: at(2), status: 'gone', message: 'Removed question', messages: [], sent: {}},
    ]});
    await v.page.waitForFunction(() => window.supportState().unread === 3);
    assert.ok(!v.calls.some(c => c.endsWith('/read')), 'nothing is read until a conversation is shown');
    await openSupport(v.page);
    const vpop = v.page.locator('#support-pop');
    assert.equal(await vpop.locator('[data-title]').innerText(), 'Answered question');
    assert.equal(await vpop.locator('[data-status]').innerText(), 'Answered');
    await v.page.waitForFunction(() => window.supportState().unread === 1);
    assert.equal(await vpop.locator('[data-mine]').innerText(), 'Your requests (4)', 'every request in the list');
    // Access off on the server says so in words, under the open request.
    assert.equal(await vpop.locator('[data-view] [data-access]').innerText(), 'Support access is turned off on this server.');
    assert.deepEqual(v.calls.filter(c => c.endsWith('/read')), ['POST tickets/sup_c/read'], 'the request shown is read');
    await vpop.locator('[data-mine]').click();
    const rows = await vpop.locator('[data-requests] .support-item').evaluateAll(els => els.map(el =>
      [el.querySelector('.support-item-title').textContent, el.querySelector('.support-badge').textContent, !!el.querySelector('.support-dot')]));
    assert.deepEqual(rows, [['Old question', 'Closed', true], ['Removed question', 'Removed', false], ['Answered question', 'Answered', false], ['Waiting question', 'Open', false]]);
    // The first line is the title; the lines after it are a muted preview. Access off on the server says so in words.
    assert.equal(await vpop.locator('.support-item', {hasText: 'Waiting question'}).locator('.support-item-body').innerText(), 'It still fails after a restart.');
    assert.equal(await vpop.locator('.support-item-body').count(), 1);
    // A closed request opens to read, with no box (and nothing about support access).
    await vpop.locator('[data-requests] .support-item').first().click();
    assert.equal(await vpop.locator('[data-title]').innerText(), 'Old question');
    assert.equal(await vpop.locator('[data-compose]').isVisible(), false);
    assert.equal(await vpop.locator('[data-access]').isVisible(), false);
    assert.match(await vpop.locator('[data-closed]').innerText(), /This request is closed/);
    assert.doesNotMatch(await vpop.locator('[data-thread]').innerText(), /business day/, 'no promise on a closed request');
    // New request starts an empty one, even with open requests about.
    await vpop.getByRole('button', {name: 'New request'}).click();
    assert.equal(await vpop.locator('[data-title]').innerText(), 'New request');
    assert.equal(await vpop.locator('.support-empty').innerText(), EMPTY.join('\n\n'));
    assert.equal((await active(v.page)).name, 'message');
    // A long conversation opens at its newest reply. Scrolled up to read, a new reply doesn't move it: the pill offers it.
    const long = v.hq.tickets.find(x => x.id === 'sup_c');
    for (let i = 3; i < 30; i++) long.messages.push({...reply(i, 4), body: `Reply ${i}\n` + 'More detail. '.repeat(20)});
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => window.supportPoll());
    await openSupport(v.page);
    const thread = vpop.locator('[data-thread]'), gap = () => thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
    await v.page.waitForFunction(() => document.querySelectorAll('#support-pop .support-msg').length === 30);
    assert.ok(await gap() < 2, 'opens at the newest reply');
    await thread.hover();
    await v.page.mouse.wheel(0, -600);
    await v.page.waitForFunction(() => { const el = document.querySelector('[data-thread]'); return el.scrollHeight - el.scrollTop - el.clientHeight > 300; });
    const top = await thread.evaluate(el => el.scrollTop);
    long.messages.push({...reply(30, 0), body: 'A new reply while you read'});
    await v.page.evaluate(() => window.supportPoll());
    await v.page.waitForFunction(() => document.querySelectorAll('#support-pop .support-msg').length === 31);
    assert.equal(await thread.evaluate(el => el.scrollTop), top, 'a reader scrolled up is not moved');
    const pill = vpop.locator('.stick-pill');
    await pill.waitFor();
    await pill.click();
    await v.page.waitForFunction(() => { const el = document.querySelector('[data-thread]'); return el.scrollHeight - el.scrollTop - el.clientHeight < 2; });
    assert.equal(await pill.isVisible(), false);
    // Deleting the shown request moves the chat to the next latest open one.
    await vpop.getByRole('button', {name: 'Delete request'}).click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop [data-title]')?.textContent === 'Waiting question');
    assert.equal(await pill.isVisible(), false, 'no stale pill after the conversation changes');
    // Only closed requests left: the empty chat names the last one, and View it opens it.
    v.hq.tickets = v.hq.tickets.filter(x => x.id === 'sup_a');
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => window.supportPoll());
    await openSupport(v.page);
    assert.equal(await vpop.locator('.support-empty').innerText(), EMPTY.join('\n\n') + '\n\nYour last request, Old question, is closed. View it');
    assert.equal(await vpop.locator('[data-compose]').isVisible(), true, 'a new request can be started');
    await vpop.getByRole('button', {name: 'View it'}).click();
    assert.equal(await vpop.locator('[data-title]').innerText(), 'Old question');
    assert.match(await vpop.locator('[data-thread]').innerText(), /Reply 1/);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A send still on its way when the pop-up is closed and reopened: nothing can send it twice or change it, and the
    // outcome shows in the composer on screen, whether it lands while open or while closed.
    v = await visit({seed: [{id: 'sup_s', created_at: at(30), status: 'open', message: 'Slow line', messages: [], sent: {}}]});
    const slow = v.page.locator('#support-pop');
    const hold = () => { let go; v.hq.gate = new Promise(r => { go = r; }); return () => { v.hq.gate = null; go(); }; };
    const posted = () => v.calls.filter(c => c.startsWith('POST tickets/sup_s/messages')).length;
    await openSupport(v.page);
    let release = hold();
    await slow.locator('[name=message]').fill('First words');
    await slow.locator('[name=message]').press('Enter');
    await v.page.waitForFunction(() => document.querySelector('#support-pop .support-send')?.getAttribute('aria-busy') === 'true');
    await v.page.keyboard.press('Escape');
    let releaseRefresh;
    v.hq.refreshGate = new Promise(resolve => { releaseRefresh = resolve; });
    const staleRefreshStarted = new Promise(resolve => { v.hq.refreshStarted = resolve; });
    await openSupport(v.page);
    await staleRefreshStarted;
    assert.equal(await slow.locator('[name=message]').isDisabled(), true, 'the box stays locked while the send is on its way');
    assert.equal(await slow.locator('.support-send').isDisabled(), true);
    assert.equal(await slow.locator('.support-send').getAttribute('aria-busy'), 'true');
    await slow.locator('[data-compose]').evaluate(f => f.requestSubmit());
    release();
    await slow.locator('.support-msg.you', {hasText: 'First words'}).waitFor();
    const staleResponse = v.page.waitForResponse(response => response.url().includes('/support/tickets/refresh'));
    releaseRefresh();
    await staleResponse;
    await v.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await slow.locator('.support-msg.you', {hasText: 'First words'}).count(), 1, 'an older reopen refresh cannot erase a completed send');
    v.hq.refreshGate = null;
    v.hq.refreshStarted = null;
    assert.equal(posted(), 1, 'sent once');
    assert.equal(await slow.locator('[name=message]').isDisabled(), false);
    assert.equal(await slow.locator('[name=message]').inputValue(), '');
    // Fails while open after a reopen: the Not sent line is in the composer on screen, with the words kept.
    v.hq.fail = true;
    release = hold();
    await slow.locator('[name=message]').fill('Second words');
    await slow.locator('[name=message]').press('Enter');
    await v.page.waitForFunction(() => document.querySelector('#support-pop .support-send')?.getAttribute('aria-busy') === 'true');
    await v.page.keyboard.press('Escape');
    await openSupport(v.page);
    release();
    await slow.locator('[data-compose] [data-error]:not([hidden])').waitFor();
    assert.equal(await slow.locator('[data-compose] [data-error]').innerText(), UNREACHABLE);
    assert.equal(await slow.locator('[name=message]').inputValue(), 'Second words');
    assert.equal(await slow.locator('[name=message]').isDisabled(), false);
    // Fails while closed: reopening shows it.
    release = hold();
    await slow.locator('[name=message]').press('Enter');
    await v.page.waitForFunction(() => document.querySelector('#support-pop .support-send')?.getAttribute('aria-busy') === 'true');
    await v.page.keyboard.press('Escape');
    release();
    await v.page.waitForFunction(() => !document.querySelector('#support-pop .support-send')?.hasAttribute('aria-busy'));
    await openSupport(v.page);
    assert.equal(await slow.locator('[data-compose] [data-error]').innerText(), UNREACHABLE);
    assert.equal(await slow.locator('[name=message]').inputValue(), 'Second words');
    v.hq.fail = false;
    assert.equal(posted(), 3);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A Report this (or a setup snapshot) while a new request's diagnostics are still being captured: the request goes
    // with the words and attachment it had when Send was pressed, and the newer one waits, unsent, with its own.
    v = await visit();
    const holdCapture = () => { let go; v.hq.captureGate = new Promise(r => { go = r; }); return () => { v.hq.captureGate = null; go(); }; };
    const newTickets = () => v.calls.filter(c => c.startsWith('POST tickets {')).map(c => JSON.parse(c.slice('POST tickets '.length)));
    const reportsCaptured = () => v.calls.filter(c => c.startsWith('POST diagnostics/capture {')).map(c => JSON.parse(c.slice('POST diagnostics/capture '.length)).report?.kind);
    await v.page.evaluate(() => talkToUs({kind: 'toast', title: 'Could not save'}));
    await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === 'Error: details attached. ');
    let releaseCapture = holdCapture();
    v.hq.captureId = 'f'.repeat(64);
    await v.page.locator('[data-compose] [name=message]').pressSequentially('Every time.');
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.waitForFunction(() => document.querySelector('#support-pop .support-send')?.getAttribute('aria-busy') === 'true');
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => talkToUs({kind: 'health', title: 'Computers: None online'}));
    await v.page.locator('#support-pop[open] [data-compose] [name=message]').waitFor();
    await v.page.waitForTimeout(200);
    assert.equal(await v.page.locator('[data-compose] [name=message]').inputValue(), 'Error: details attached. Every time.', 'the send on its way is not changed');
    assert.equal(await v.page.locator('[data-compose] [name=message]').isDisabled(), true);
    releaseCapture();
    await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === 'Health: details attached. ');
    assert.deepEqual(newTickets().map(t => [t.message, t.diagnostics]), [['Error: details attached. Every time.', 'f'.repeat(64)]], 'sent once, as pressed');
    assert.deepEqual(reportsCaptured(), ['toast']);
    assert.match(await v.page.locator('[data-report]').innerText(), /Attached: Health Computers: None online/);
    assert.equal(await v.page.locator('[data-compose] [name=message]').isDisabled(), false);
    assert.equal(await v.page.locator('#support-pop [data-title]').innerText(), 'New request');
    // Sent in turn, it captures its own attachment; a setup snapshot meanwhile waits the same way.
    releaseCapture = holdCapture();
    v.hq.captureId = 'a'.repeat(64);
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.waitForFunction(() => document.querySelector('#support-pop .support-send')?.getAttribute('aria-busy') === 'true');
    await v.page.evaluate(() => supportStart('Help me set up.', 'setup'));
    await v.page.waitForTimeout(200);
    releaseCapture();
    await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === 'Help me set up.');
    assert.deepEqual(newTickets().map(t => [t.message, t.diagnostics, t.kind]),
      [['Error: details attached. Every time.', 'f'.repeat(64), undefined], ['Health: details attached. ', 'a'.repeat(64), undefined]]);
    assert.deepEqual(reportsCaptured(), ['toast', 'health']);
    v.hq.captureId = null;
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Opening on Your requests or on another request's reply reads only what it shows: the chat left open last time is
    // not read on the way.
    v = await visit({seed: [
      {id: 'sup_a', created_at: at(60), status: 'answered', message: 'Question A', messages: [reply(1, 30)], sent: {}},
      {id: 'sup_b', created_at: at(90), status: 'open', message: 'Question B', messages: [], sent: {}}]});
    await openSupport(v.page);
    assert.equal(await v.page.locator('#support-pop [data-title]').innerText(), 'Question A');
    await v.page.waitForFunction(() => window.supportState().unread === 0);
    await v.page.keyboard.press('Escape');
    v.hq.tickets.find(x => x.id === 'sup_a').messages.push(reply(2, 2));
    v.hq.tickets.find(x => x.id === 'sup_b').messages.push(reply(1, 1));
    await v.page.evaluate(() => window.supportPoll());
    await v.page.waitForFunction(() => window.supportState().unread === 2);
    const reads = () => v.calls.filter(c => c.endsWith('/read'));
    const before = reads().length;
    await v.page.evaluate(() => window.supportOpen({list: true}));
    await v.page.locator('#support-pop[open] [data-list]:not([hidden])').waitFor();
    await v.page.waitForTimeout(300);
    assert.deepEqual(reads().slice(before), [], 'Your requests reads nothing');
    assert.equal(await v.page.evaluate(() => window.supportState().unread), 2);
    await v.page.keyboard.press('Escape');
    await v.page.locator('.toast', {hasText: 'Tico support replied.'}).last().getByRole('button', {name: 'Open'}).click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop [data-title]')?.textContent === 'Question B');
    await v.page.waitForFunction(() => window.supportState().unread === 1);
    await v.page.waitForTimeout(300);
    assert.deepEqual(reads().slice(before), ['POST tickets/sup_b/read'], "B's reply reads only B");
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Inside the desktop app: a request carries the app's version labelled apart from the server's, and diagnostics
    // carry the end of the app's log.
    // The page reports whether the app runs on a Mac from its user agent, so the test pins one rather than the host's.
    v = await visit({native: true, userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'});
    await openSupport(v.page);
    await v.page.locator('[data-attach-label]').click();
    assert.match(await v.page.locator('[data-sent]').textContent(), /version 0\.2\.17, app version 0\.3\.48, install ID/);
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

    // Only old requests still open (Oct 29: it opened on a week-old alert): Tico support shows Your requests, reading nothing.
    v = await visit({seed: [{id: 'sup_7', created_at: at(3 * 24 * 60), status: 'open', message: 'Health Monitor alert', messages: [], sent: {}},
                            {id: 'sup_6', created_at: at(5), status: 'closed', message: 'Just closed', messages: [], sent: {}}]});
    await supportLink(v.page);
    await v.page.locator('#t-support').click();
    await v.page.locator('#support-pop[open] [data-requests] .support-item').first().waitFor();
    assert.equal(await v.page.locator('#support-pop [data-mine]').getAttribute('aria-pressed'), 'true');
    assert.equal(await v.page.locator('#support-pop [data-view]').isVisible(), false);
    assert.deepEqual(await v.page.locator('#support-pop [data-requests] .support-item-title').allInnerTexts(), ['Just closed', 'Health Monitor alert']);
    assert.ok(!v.calls.some(c => c.endsWith('/read')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Letting Tico support in, under the open request: the dialog offers only what this person may give, the banner
    // and the request show the tier and the time left in the same words, the banner opens the request it started from,
    // the log under that request lists what support did (as text, its time as the app's event times read), another
    // request shows none of it, and Stop ends it.
    v = await visit({seed: [{id: 'sup_9', created_at: new Date().toISOString(), status: 'open', message: 'Bots stopped', messages: [], sent: {}},
                            {id: 'sup_8', created_at: at(600), status: 'closed', message: 'Old question', messages: [], sent: {}}],
                     access: {available: true, off_reason: '', active: null, last: null, sessions: [], can_start: true, can_full: false}});
    await openSupport(v.page);
    const strip = v.page.locator('#support-pop [data-view] [data-access]');
    await v.page.waitForFunction(() => document.querySelector('#support-pop [data-title]')?.textContent === 'Bots stopped');
    assert.ok(v.calls.includes('POST tickets/refresh {"ticket":"sup_9"}'), 'the conversation in view is asked about first');
    await strip.getByRole('button', {name: 'Let Tico support in'}).waitFor();
    assert.equal(await strip.innerText(), 'Let Tico support in');
    assert.ok(await strip.evaluate(el => el.getBoundingClientRect().top >= document.querySelector('#support-pop [data-thread]').getBoundingClientRect().bottom
      && el.getBoundingClientRect().bottom <= document.querySelector('#support-pop [data-compose]').getBoundingClientRect().top), 'under the conversation, over the box');
    await v.page.locator('#support-pop [data-mine]').click();
    await v.page.locator('#support-pop [data-list]:not([hidden])').waitFor();
    assert.equal(await v.page.locator('#support-pop [data-list] [data-access]').count(), 0, 'not in Your requests');
    await v.page.locator('#support-pop [data-back]').click();
    await strip.getByRole('button', {name: 'Let Tico support in'}).click();
    assert.equal(await v.page.locator('dialog[aria-label="Let Tico support in"] [name=tier][value=full]').isDisabled(), true, 'full access is the owner\'s to give');
    await v.page.locator('dialog[aria-label="Let Tico support in"] [name=hours]').selectOption('4');
    await v.page.locator('dialog[aria-label="Let Tico support in"] [type=submit]').click();
    await v.page.locator('#support-access-banner:not([hidden])').waitFor();
    assert.ok(v.calls.includes('POST access {"ticket_id":"sup_9","tier":"app","hours":4}'));
    assert.equal(await v.page.locator('#support-access-banner [data-access-left]').innerText(), 'Tico support has access (App only), 3 h 12 min left');
    await strip.locator('.support-access-log li').waitFor();
    assert.equal(await strip.locator('.support-access-on').innerText(), 'Tico support has access (App only), 3 h 12 min left · Stop');
    assert.match(await strip.locator('.support-access-log li').innerText(), /^just now Support Agent · GET \/api\/v2\/health<img src=x/);
    assert.ok(await strip.locator('.support-access-log li time').getAttribute('title'), 'the full time on hover');
    await v.page.keyboard.press('Escape');
    await v.page.locator('#support-pop').waitFor({state: 'hidden'});
    // From a closed request, the banner opens the one the session started from.
    await v.page.evaluate(() => window.supportOpen({ticket: 'sup_8'}));
    await v.page.waitForFunction(() => document.querySelector('#support-pop [data-title]')?.textContent === 'Old question');
    assert.equal(await strip.isVisible(), false, 'another request shows none of it');
    await v.page.keyboard.press('Escape');
    await v.page.locator('#support-access-banner [data-access-left]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open] [data-title]')?.textContent === 'Bots stopped');
    assert.equal(await strip.locator('.support-access-log li').count(), 1);
    // Stop under the request shows it ended there and on the banner at once, before the next status read answers.
    let releaseAccess;
    v.hq.accessGate = new Promise(resolve => { releaseAccess = resolve; });
    await strip.getByRole('button', {name: 'Stop'}).click();
    await v.page.locator('#support-access-banner').waitFor({state: 'hidden'});
    await strip.getByRole('button', {name: 'Let Tico support in'}).waitFor();
    assert.equal(await strip.locator('.support-access-on').count(), 0);
    assert.ok(v.calls.includes('POST access/sa_1/stop {}'));
    releaseAccess(); v.hq.accessGate = null;
    // The banner's Stop does the same.
    await strip.getByRole('button', {name: 'Let Tico support in'}).click();
    await v.page.locator('dialog[aria-label="Let Tico support in"] [type=submit]').click();
    await v.page.locator('#support-access-banner:not([hidden])').waitFor();
    await v.page.keyboard.press('Escape');
    v.hq.accessGate = new Promise(resolve => { releaseAccess = resolve; });
    await v.page.locator('#support-access-banner [data-access-stop]').click();
    await v.page.locator('#support-access-banner').waitFor({state: 'hidden'});
    assert.equal(v.calls.filter(c => c === 'POST access/sa_1/stop {}').length, 2);
    releaseAccess(); v.hq.accessGate = null;
    assert.equal(await v.page.evaluate(() => window.__pwned), undefined);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A phone in the dark theme: no Talk to us anywhere, the ? tab in its place; reviewing and editing the
    // diagnostics still works in the sheet.
    v = await visit({viewport: {width: 390, height: 844}, theme: 'dark'});
    assert.deepEqual(await v.page.locator('#mobile-nav .mobile-nav-label').allInnerTexts(), ['Team', 'Search', 'Ask Tico', 'Tasks', 'Updates', 'More']);
    assert.equal(await v.page.getByText('Talk to us').count(), 0);
    assert.equal(await v.page.locator('#mobile-ask .ldot').isVisible(), true);
    await openSupport(v.page);
    assert.deepEqual(await active(v.page), {id: 'support-message', name: 'message', inPop: true});
    // The empty chat at phone width: both lines on screen, nothing sideways.
    const phonePop = v.page.locator('#support-pop');
    assert.deepEqual(await phonePop.locator('.support-empty p').allInnerTexts(), EMPTY);
    for (const p of await phonePop.locator('.support-empty p').all()) assert.equal(await p.isVisible(), true);
    assert.equal(await phonePop.evaluate(el => el.scrollWidth <= el.clientWidth && [...el.querySelectorAll('.support-empty, .support-empty *')].every(x => x.getBoundingClientRect().right <= innerWidth + 1 && x.scrollWidth <= x.clientWidth + 1)), true, 'the promise fits a phone');
    const dark = await v.page.locator('#support-pop').evaluate(el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]);
    assert.notDeepEqual(dark[0], 'rgb(255, 255, 255)', 'the dark theme reaches the pop-up');
    await v.page.locator('[data-attach-label]').click();
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
    await v.page.waitForTimeout(300);
    await v.page.screenshot({path: '/tmp/tico-support-phone-dark.png'});
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // An iPhone (390x844) with the keyboard up: the box is 16 px so Safari doesn't zoom in on it, and the sheet fills
    // exactly what is visible above the keyboard: the box, Send and the newest reply on screen, nothing of the page
    // behind it showing and the page not scrolling under it. With the keyboard down it is the usual bottom sheet again.
    v = await visit({viewport: {width: 390, height: 844}, keyboard: true, seed: [{id: 'sup_9', created_at: at(30), status: 'answered', message: 'Roadmap suggestions go to an archived bot.', sent: {},
      messages: [reply(1, 25), {id: 2, created_at: at(20), from: 'person', body: 'Still there on 0.3.54.'},
        {id: 3, created_at: at(10), from: 'staff', body: 'The setting now decides who gets suggestions.\n\nAfter that, it is safe to turn public roadmap suggestions back on. Reply here if anything still goes to the archived bot.'}]}]});
    await openSupport(v.page);
    const kpop = v.page.locator('#support-pop'), kbox = kpop.locator('[data-compose] [name=message]');
    await kbox.focus();
    assert.ok(parseFloat(await kbox.evaluate(el => getComputedStyle(el).fontSize)) >= 16, 'the box is at least 16 px: no zoom on focus');
    assert.equal(await v.page.evaluate(() => document.querySelector('meta[name=viewport]').content.includes('user-scalable=no') || /maximum-scale=1(\D|$)/.test(document.querySelector('meta[name=viewport]').content)), false, 'zoom itself stays allowed');
    const sheetAt = () => kpop.evaluate(el => {
      const r = el.getBoundingClientRect(), inside = (sel, all) => { const b = (all ? [...el.querySelectorAll(sel)].at(-1) : el.querySelector(sel)).getBoundingClientRect(); return {top: b.top, bottom: b.bottom}; };
      const thread = inside('[data-thread]'), newest = el.querySelector('.support-msg') ? inside('.support-msg', true) : null;   // a new request has none
      // Every point along the top and bottom edges and down both sides is the sheet, not the page behind it.
      const probes = [];
      for (let x = 2; x < innerWidth; x += 48) probes.push([x, r.top + 1], [x, r.bottom - 1]);
      for (let y = r.top + 1; y < r.bottom; y += 40) probes.push([2, y], [innerWidth - 2, y]);
      return {top: r.top, bottom: r.bottom, height: r.height, pad: parseFloat(getComputedStyle(el).paddingBottom), box: inside('[name=message]'), send: inside('[type=submit]'), thread, newest,
        covered: probes.every(([x, y]) => el.contains(document.elementFromPoint(x, y))), bodyScroll: getComputedStyle(document.body).overflowY};
    });
    const sheetIs = async (top, bottom, why) => {
      await v.page.waitForFunction(([a, b]) => { const r = document.getElementById('support-pop').getBoundingClientRect(); return Math.abs(r.top - a) < 1 && Math.abs(r.bottom - b) < 1; },
        [top, bottom], {timeout: t(3000)}).catch(() => {});   // asserted below
      const k = await sheetAt();
      assert.ok(Math.abs(k.top - top) < 1 && Math.abs(k.bottom - bottom) < 1, `${why}: ${top}-${bottom} (${JSON.stringify(k)})`);
      return k;
    };
    assert.equal((await sheetAt()).pad, 34, 'keyboard down: the sheet keeps clear of the home bar');
    // Safari leaves 844 - 386 = 458 px above the keyboard and its bar, panned down 120 px to keep the box in view.
    await v.page.evaluate(() => window.__keys(458, 120));
    await sheetIs(120, 578, 'the sheet fills what is visible above the keyboard');
    await v.page.waitForTimeout(100);   // the thread re-pins to its newest reply on the resize (ui/app/stick-scroll.js)
    let k = await sheetAt();
    assert.equal(k.pad, 0, 'keyboard up: no home-bar gap above the keyboard');
    for (const part of ['box', 'send']) assert.ok(k[part].top >= 120 && k[part].bottom <= 578, `${part} visible above the keyboard (${JSON.stringify(k[part])})`);
    assert.ok(k.newest.top >= k.thread.top - 1 && k.newest.bottom <= k.thread.bottom + 1, `the newest reply is whole on screen (${JSON.stringify(k)})`);
    assert.ok(k.newest.bottom <= k.box.top, 'above the box');
    assert.equal(k.covered, true, 'nothing of the page behind shows around the sheet');
    assert.equal(k.bodyScroll, 'hidden', 'the page behind does not scroll while the sheet is open');
    await v.page.screenshot({path: '/tmp/tico-support-phone-keyboard.png'});
    // Pinch zoom (2x) with the keyboard up: the sheet keeps the 458 px above the keyboard, on the bottom of the page
    // where panning reaches it, and zoom itself still works.
    const SHEET = 844 * 0.92;   // the usual bottom sheet: 92dvh
    await v.page.evaluate(() => window.__keys(229, 300, 2));
    await sheetIs(844 - 458, 844, 'zoomed with the keyboard up');
    // The keyboard goes down while still zoomed: the usual bottom sheet, not the keyboard-sized one from before the zoom.
    await v.page.evaluate(() => window.__keys(422, 200, 2));
    k = await sheetIs(844 - SHEET, 844, 'keyboard down while zoomed');
    assert.equal(k.pad, 34, 'and the home-bar gap is back');
    // Closed and opened again while zoomed: the same.
    await v.page.keyboard.press('Escape');
    await kpop.waitFor({state: 'hidden'});
    await openSupport(v.page);
    await sheetIs(844 - SHEET, 844, 'reopened while zoomed');
    // The keyboard comes up while already zoomed: the space above it, on the bottom of the page.
    await v.page.evaluate(() => window.__keys(229, 100, 2));
    await sheetIs(844 - 458, 844, 'keyboard up while already zoomed');
    // Zoomed back out with the keyboard up, Safari panned 300 px: the sheet follows the visible area again.
    await v.page.evaluate(() => window.__keys(458, 300));
    await sheetIs(300, 758, 'zoomed out with the keyboard up');
    // The keyboard goes down: the usual bottom sheet, most of the screen.
    await v.page.evaluate(() => window.__keys(844, 0));
    k = await sheetIs(844 - SHEET, 844, 'back to the bottom sheet');
    assert.equal(k.pad, 34, 'with the home-bar gap');
    // A long reply, then the "Want help setting up?" snapshot, with the keyboard up: the box grows only into the room
    // the short sheet has left, so it and Send stay above the keyboard, and the rest scrolls inside the box.
    const fitsAbove = async (why) => {
      await v.page.waitForTimeout(50);
      const s = await sheetAt();
      for (const part of ['box', 'send']) assert.ok(s[part].top >= 120 && s[part].bottom <= 578, `${why}: ${part} above the keyboard (${JSON.stringify(s)})`);
      assert.ok(s.thread.bottom - s.thread.top >= 119, `${why}: the thread keeps its room (${JSON.stringify(s.thread)})`);
    };
    await v.page.evaluate(() => window.__keys(458, 120));
    await sheetIs(120, 578, 'keyboard up again');
    await kbox.fill(Array.from({length: 30}, (_, i) => `Line ${i + 1} of a long reply about the archived bot.`).join('\n'));
    await fitsAbove('long reply');
    const snapshot = "I'd like help setting up Tico.\n\nSetup snapshot (asked from the setup wizard):\n- Steps done: 2 of 5; left: Add a computer, Sign in a model, First bot ready\n- Computer online: no\n- Model signed in: no\n- First bot ready: no\n- Bots in Needs setup: 3";
    await v.page.evaluate(m => window.supportStart(m, 'setup'), snapshot);
    await v.page.waitForFunction(m => document.querySelector('#support-pop[open] #support-message')?.value === m, snapshot);
    await fitsAbove('setup snapshot');
    if (process.env.TICO_SCREENSHOT_DIR) await v.page.screenshot({path: require('node:path').join(process.env.TICO_SCREENSHOT_DIR, 'phone-help-box-keyboard-light.png')});
    // Keyboard down: the whole snapshot shows, nothing left to scroll.
    await v.page.evaluate(() => window.__keys(844, 0));
    await sheetIs(844 - SHEET, 844, 'keyboard down after the snapshot');
    await v.page.waitForTimeout(50);
    assert.ok(await kpop.locator('[data-compose] [name=message]').evaluate(box => box.scrollHeight <= box.clientHeight + 1), 'keyboard down: the snapshot shows whole');
    await v.page.keyboard.press('Escape');
    await kpop.waitFor({state: 'hidden'});
    assert.notEqual(await v.page.evaluate(() => getComputedStyle(document.body).overflowY), 'hidden', 'closing the sheet lets the page scroll again');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A computer in the dark theme, and keyboard only: focus on a #/support link, Enter, focus in the box.
    v = await visit({theme: 'dark', seed: [{id: 'sup_5', created_at: at(30), status: 'answered', message: 'Can bots share a computer?', sent: {},
      messages: [reply(1, 20), {id: 2, created_at: at(10), from: 'person', body: 'Thanks, and two?'}]}]});
    assert.equal(await v.page.getByText('Talk to us').count(), 0, 'no Talk to us in the sidebar');
    await supportLink(v.page);
    await v.page.locator('#t-support').focus();
    await v.page.keyboard.press('Enter');
    await v.page.locator('#support-pop[open]').waitFor();
    await v.page.waitForFunction(() => document.activeElement?.id === 'support-message');
    await v.page.waitForTimeout(300);
    await v.page.screenshot({path: '/tmp/tico-support-desktop-dark.png'});
    await v.page.keyboard.press('Escape');
    assert.equal((await active(v.page)).id, 't-support');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // An old #/help link: Overview with the pop-up open over it, no Help page.
    v = await visit({hash: '#/help'});
    await v.page.locator('#support-pop[open] [data-compose] [name=message]').waitFor();
    assert.equal(await v.page.evaluate(() => location.hash), '#/overview');
    assert.equal(await v.page.locator('#help-page').count(), 0);
    await v.page.waitForFunction(() => document.activeElement?.id === 'support-message');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();


    // Report this on an error toast: a new request with the error named, attached as diagnostics, shown before sending.
    v = await visit();
    await v.page.waitForFunction(() => window.supportOn(true));      // offered once Tico knows support is on
    await v.page.evaluate(() => toast('Could not save the task "Ship it" (409)', true));
    await v.page.locator('.toast.err button', {hasText: 'Report this'}).click();
    await v.page.locator('#support-pop[open] [data-report]').waitFor();
    assert.equal(await v.page.locator('[data-attach]').evaluate(el => el.open), true, 'Attach details is on');
    assert.equal(await v.page.locator('[data-compose] [name=diag]').isChecked(), true, 'diagnostics on');
    assert.match(await v.page.locator('[data-report]').innerText(), /Attached: Error Could not save the task/);
    // The message names only the kind: the words on screen go in the redacted attachment, never unredacted in the text.
    assert.equal(await v.page.locator('[data-compose] [name=message]').inputValue(), 'Error: details attached. ');
    await v.page.waitForFunction(() => document.activeElement?.name === 'message' && document.activeElement.selectionStart === document.activeElement.value.length);
    assert.match(await v.page.locator('[data-sent]').textContent(), /redacted diagnostics with what you reported/);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'nothing is sent yet');
    await v.page.locator('[data-compose] [name=message]').pressSequentially('It happens every time.');
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.locator('.support-msg.you').waitFor();
    const reported = JSON.parse(v.calls.find(c => c.startsWith('POST diagnostics/capture')).slice('POST diagnostics/capture '.length)).report;
    assert.equal(reported.kind, 'toast');
    assert.equal(reported.title, 'Could not save the task "Ship it" (409)');
    assert.equal(reported.group, 'toast:Could not save the task "…" (#)', 'numbers and quoted names do not split a group');
    const sentTicket = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.equal(sentTicket.diagnostics, 'c'.repeat(64));
    assert.equal(sentTicket.message, 'Error: details attached. It happens every time.', 'only what the person saw and typed');
    // Report this again, with the pop-up closed or open: a new request each time, not the one just sent.
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => talkToUs({kind: 'run', title: 'Run failed: exit 1'}));
    await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === 'Failed run: details attached. ');
    assert.equal(await v.page.locator('#support-pop [data-title]').innerText(), 'New request');
    // A second report onto an untouched prefill takes its own kind; words the person typed stay.
    await v.page.locator('[data-compose] [name=message]').fill('Failed run: details attached. Since Monday.');
    await v.page.evaluate(() => talkToUs({kind: 'stuck', title: 'finance: no reply'}));
    await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === 'Stuck bot: details attached. Since Monday.');
    await v.page.locator('[data-compose] [name=message]').fill('');
    await v.page.evaluate(() => talkToUs({kind: 'run', title: 'Run failed: exit 1'}));
    await v.page.locator('[data-report]', {hasText: 'Failed run'}).waitFor();
    await v.page.locator('[data-unreport]').click();
    assert.equal(await v.page.locator('[data-report]').count(), 0);
    assert.equal(await v.page.locator('[data-compose] [name=message]').inputValue(), '', 'no "details attached" with nothing attached');
    // A red Health line, a stuck bot and a failed run offer Report this; a fine one does not.
    const offered = await v.page.evaluate(() => ({
      bad: hlCheckHtml({id: 'computers', label: 'Computers', status: 'bad', summary: 'None online', fixes: []}).includes('data-report-this'),
      ok: hlCheckHtml({id: 'version', label: 'Version', status: 'ok', summary: 'Current', fixes: []}).includes('data-report-this'),
      stuck: hlStuckHtml([{bot: 'ops', name: 'Ops', why: 'No computer', reason: 'offline'}]).includes('data-report-this'),
      failed: runAttemptHTML({id: 'r1', state: 'failed', reason: 'exit 2', try: 1, tries: 1}, true).includes('data-report-this'),
      done: runAttemptHTML({id: 'r2', state: 'completed', try: 1, tries: 1}, true).includes('data-report-this')}));
    assert.deepEqual(offered, {bad: true, ok: false, stuck: true, failed: true, done: false});
    // A manager sees refused words in Health, but those private words never enter support diagnostics.
    const refused = 'Private launch plan: move the acquisition budget';
    for (const reportTarget of ['held-fixture', 'held-check-fixture']) {
      const quarantineVisit = await visit();
      await quarantineVisit.page.waitForFunction(() => window.supportOn(true));
      await quarantineVisit.page.evaluate(words => {
        document.querySelector('#main').insertAdjacentHTML('beforeend', '<ul id="held-fixture">' + hlStuckHtml([
          {bot: 'ops', name: 'Ops', reason: 'quarantined', why: 'Quarantined for repeated refused writes (rule 8): '
            + 'resumes by itself at 2030-10-27 00:55 UTC. Refused write (reach): ' + words}
        ]) + '</ul><ul id="held-check-fixture">' + hlCheckHtml({id: 'queue', label: 'Stuck bots', status: 'bad',
          summary: '1 bot with work that is not starting. Ops (ops): 1 job queued, oldest 20 min; '
            + 'Quarantined: queued work waits for the quarantine to clear.', fixes: []}) + '</ul>');
      }, refused);
      assert.match(await quarantineVisit.page.locator('#held-fixture').innerText(), new RegExp(refused));
      await quarantineVisit.page.locator('#' + reportTarget + ' [data-report-this]').click();
      await quarantineVisit.page.locator('#support-pop[open] [data-report]').waitFor();
      await quarantineVisit.page.locator('[data-compose] [name=message]').press('Enter');
      await quarantineVisit.page.locator('.support-msg.you').waitFor();
      const quarantineReport = JSON.parse(quarantineVisit.calls.find(c => c.startsWith('POST diagnostics/capture')).slice('POST diagnostics/capture '.length)).report;
      const rowReport = reportTarget === 'held-fixture';
      assert.equal(quarantineReport.kind, rowReport ? 'stuck' : 'health');
      assert.equal(quarantineReport.ref, rowReport ? 'ops' : 'queue');
      assert.equal(quarantineReport.group, rowReport ? 'stuck:quarantined' : 'health:queue');
      assert.match(quarantineReport.detail, /Quarantined/);
      assert.doesNotMatch(JSON.stringify(quarantineReport), /Private launch plan|acquisition budget|Refused write/);
      assert.deepEqual(quarantineVisit.errors, []);
      await quarantineVisit.ctx.close();
    }
    // Report this on a red Health line, pressed on the page behind: the pop-up opens with it attached.
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => document.querySelector('#main').insertAdjacentHTML('beforeend', reportThisHTML({kind: 'health', title: 'Computers: None online'})));
    await v.page.locator('#main .report-this').click();
    await v.page.locator('#support-pop[open] [data-report]', {hasText: 'Health'}).waitFor();
    assert.equal(await v.page.locator('[data-compose] [name=message]').inputValue(), 'Health: details attached. ');
    // Setup check's Send to support: the pop-up opens with the result attached, as a setup request.
    await v.page.keyboard.press('Escape');
    const setup = {kind: 'setup_check', title: '1 of 2 checks fail', checks: [
      {name: 'HTTPS certificate', status: 'bad', detail: 'could not connect to tico.acme.example:443', fix: 'Open port 443 to the server.', source: 'doctor'},
      {name: '/healthz', status: 'ok', detail: 'ok', fix: '', source: 'doctor'}], group: 'setup_check:HTTPS certificate'};
    await v.page.evaluate(r => talkToUs(r), setup);
    await v.page.locator('#support-pop[open] [data-report]', {hasText: 'Setup check'}).waitFor();
    assert.match(await v.page.locator('[data-sent]').textContent(), /message marked setup/, 'a Setup check goes as a setup request');
    // Another report in its place, or Remove, gives back the kind the request had before.
    await v.page.evaluate(() => talkToUs({kind: 'toast', title: 'Could not save'}));
    await v.page.locator('[data-report]', {hasText: 'Error'}).waitFor();
    assert.doesNotMatch(await v.page.locator('[data-sent]').textContent(), /marked setup/, 'an error report is not a setup request');
    await v.page.evaluate(r => talkToUs(r), setup);
    await v.page.locator('[data-report]', {hasText: 'Setup check'}).waitFor();
    await v.page.locator('[data-unreport]').click();
    assert.doesNotMatch(await v.page.locator('[data-sent]').textContent(), /marked setup/);
    await v.page.evaluate(r => talkToUs(r), setup);
    await v.page.locator('[data-report]', {hasText: 'Setup check'}).waitFor();
    await v.page.locator('[data-compose] [data-preview]').click();
    await v.page.locator('dialog.support-modal [data-use]:not([disabled])').waitFor();
    const previewed = JSON.parse(v.calls.filter(c => c.startsWith('POST diagnostics/capture')).at(-1).slice('POST diagnostics/capture '.length)).report;
    assert.deepEqual(previewed.checks.map(c => [c.name, c.status]), [['HTTPS certificate', 'bad'], ['/healthz', 'ok']]);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // "Want help setting up?" opens a new request that starts from the snapshot, which the person reads and sends as setup.
    v = await visit({seed: [{id: 'sup_0', created_at: new Date().toISOString(), status: 'open', message: 'Earlier', sent: {}, messages: []}]});
    await v.page.evaluate(() => document.querySelector('#main').insertAdjacentHTML('beforeend', '<button type="button" data-setup-help="health">Want help setting up?</button>'));
    await v.page.locator('#main [data-setup-help]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open] #support-message')?.value.startsWith("I'd like help setting up Tico."));
    assert.ok(v.calls.includes('asked from health'));
    // The box grows to the whole snapshot: nothing is cut off or left to scroll.
    assert.ok(await v.page.locator('#support-message').evaluate(box => box.scrollHeight <= box.clientHeight + 1), 'the snapshot shows whole');
    assert.equal(await v.page.locator('#support-pop [data-title]').innerText(), 'New request');
    assert.match(await v.page.locator('[data-sent]').textContent(), /message marked setup/);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'nothing is sent before the person presses Send');
    // A Setup check attached and removed again leaves "Want help setting up?" a setup request.
    await v.page.evaluate(() => talkToUs({kind: 'setup_check', title: '1 of 2 checks fail', checks: []}));
    await v.page.locator('[data-unreport]').click();
    assert.match(await v.page.locator('[data-sent]').textContent(), /message marked setup/);
    // A second press keeps the draft as it is: one snapshot, not two.
    await v.page.keyboard.press('Escape');
    await v.page.locator('#main [data-setup-help]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open]') && !document.querySelector('#main [data-setup-help]').disabled);
    await v.page.waitForTimeout(100);
    assert.equal(v.calls.filter(c => c === 'asked from health').length, 2);
    assert.equal((await v.page.locator('#support-message').inputValue()).split("I'd like help setting up Tico.").length, 2);
    // TICO_SCREENSHOT_DIR=<dir> saves the opened snapshot at desktop and phone width, in both themes.
    if (process.env.TICO_SCREENSHOT_DIR) for (const [size, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]]) {
      for (const theme of ['light', 'dark']) {
        const s = await visit({viewport, theme});
        await s.page.evaluate(() => document.querySelector('#main').insertAdjacentHTML('beforeend', '<button type="button" data-setup-help="wizard">Want help setting up?</button>'));
        await s.page.locator('#main [data-setup-help]').click();
        await s.page.waitForFunction(() => document.querySelector('#support-pop[open] #support-message')?.value.startsWith("I'd like help setting up Tico."));
        await s.page.waitForTimeout(300);
        await s.page.screenshot({path: require('node:path').join(process.env.TICO_SCREENSHOT_DIR, `${size}-help-box-${theme}.png`)});
        await s.ctx.close();
      }
    }
    await v.page.locator('[data-compose] [name=message]').press('Enter');
    await v.page.locator('.support-msg.you', {hasText: 'Computer online'}).waitFor();
    assert.equal(JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length)).kind, 'setup');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // A Setup check first, then "Want help setting up?", then Remove: still a setup request.
    v = await visit();
    await v.page.evaluate(() => talkToUs({kind: 'setup_check', title: '1 of 2 checks fail', checks: []}));
    await v.page.locator('#support-pop[open] [data-report]').waitFor();
    await v.page.keyboard.press('Escape');
    await v.page.evaluate(() => document.querySelector('#main').insertAdjacentHTML('beforeend', '<button type="button" data-setup-help="health">Want help setting up?</button>'));
    await v.page.locator('#main [data-setup-help]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-message')?.value.includes("I'd like help setting up Tico."));
    await v.page.locator('[data-unreport]').click();
    assert.match(await v.page.locator('[data-sent]').textContent(), /message marked setup/);
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

    // Support turned off: a #/support link still opens the pop-up, with the link row and the "turned off" line and no box.
    v = await visit({enabled: false});
    await openSupport(v.page);
    const offPop = v.page.getByRole('dialog', {name: 'Support'});
    assert.equal(await offPop.locator('[data-off]').innerText(), 'Contact support is turned off on this Tico server, so requests cannot be sent from here.');
    assert.equal(await offPop.locator('[name=message]').isVisible(), false);
    assert.equal(await offPop.getByRole('button', {name: 'New request'}).isVisible(), false);
    assert.equal(await offPop.getByRole('link', {name: 'View docs'}).isVisible(), true);
    assert.equal(await offPop.locator('[data-whats-new]').isVisible(), true);
    assert.equal((await active(v.page)).inPop, true, 'focus is in the pop-up');
    await v.page.keyboard.press('Escape');
    assert.equal((await active(v.page)).id, 't-support');
    // Report this is not offered: an error toast has nothing to report to.
    await v.page.waitForFunction(() => !window.supportOn());
    await v.page.evaluate(() => toast('Could not save', true));
    assert.equal(await v.page.locator('.toast.err button').count(), 0);
    assert.ok(!v.calls.some(c => c.startsWith('POST')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
