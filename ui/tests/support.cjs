// Offline browser regression: the Tico team chat (ui/support.js). The ? beside Search (in the phone's More drawer, beside
// the Search row) opens one ongoing conversation with the Tico team: every ticket's messages merged in time order as
// bubbles (theirs with the writer's name when it changes, a date line where the day changes), one box and a paperclip
// for diagnostics. No list, no form, no statuses. Sending writes to the open ticket, or starts a new one with the
// message (email and install details filled in). A reply on the person's own request opens the chat once and puts a dot on the ?; a bot's report's reply waits in the chat. A #/support
// link, Report this, Want help setting up?, old #/help links and Let the Tico team in all work through it; with
// support off the ? is gone and the chat says so. Nothing a message says is rendered as HTML.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const fs = require('node:fs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const INSTALL = '6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63';
const DIAGNOSTICS_TEXT = '{\n  "format": 1,\n  "versions": {\n    "tico": "0.2.17"\n  }\n}';
const HOSTILE = '<img src=x onerror="window.__pwned=1"> <b>bold</b>';
const UNREACHABLE = 'Not sent. Could not reach the Tico team. Try again in a moment.';
// No help-desk words anywhere in the chat.
const DESK = /request|ticket|submitted|get back to you|Delivered|business day|Open|Answered|Closed|sup_/;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({enabled = true, seed = [], native = false, feedback = null, viewport = {width: 1440, height: 900}, access = null, hash = '#/tasks', theme = 'light', keyboard = false, userAgent} = {}) => {
      // The fixtures are minutes and days before now; a browser whose local time is near midnight would put 'an hour
      // ago' on yesterday. A fixed-offset zone where it is about noon keeps every day line where the test expects it.
      const noon = `Etc/GMT${(h => (h > 0 ? '+' : '-') + Math.abs(h))(new Date().getUTCHours() - 12)}`.replace('-0', '+0');
      const ctx = await browser.newContext({viewport, serviceWorkers: 'block', userAgent, timezoneId: noon});
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
      const hq = {full: [], tickets: seed, seen: Object.fromEntries(seed.map(t => [t.id, t.seen || 0])), reply: false, fail: false, abort: false, pollFail: false, confirm: true, asked: [], access, accessGate: null, gate: null, refreshGate: null, refreshStarted: null};
      const unread = t => t.messages.filter(m => m.from === 'staff').length - (hq.seen[t.id] || 0);
      // `source`: who filed a ticket (a person's own has no sent.source). `unread_own`: the replies on the person's own.
      const source = t => t.sent?.source || 'person';
      const out = t => ({...t, source: source(t), unread: unread(t)});
      const view = () => ({enabled, tickets: hq.tickets.slice(0, 50).map(out),   // the server lists at most 50
      unread: hq.tickets.reduce((n, t) => n + unread(t), 0),
      unread_own: hq.tickets.reduce((n, t) => n + (source(t) === 'person' ? unread(t) : 0), 0)});
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
            // An Idempotency-Key seen before gets the same ticket back, as HQ does; `loseCreate` drops one reply after saving.
            const key = req.headers()['idempotency-key'];
            (hq.createKeys ||= []).push(key);
            const again = hq.tickets.find(x => x.key === key);
            if (again) return json(out(again));
            const ticket = {key, id: 'sup_' + (hq.tickets.length + 1), created_at: new Date().toISOString(), status: 'open', message: body.message, email: body.email,
                            sent: body.include_ids ? {version: '0.2.17', install_id: INSTALL} : {}, messages: []};
            hq.tickets.unshift(ticket);
            if (hq.loseCreate) { hq.loseCreate = false; return json({error: {detail: 'Could not reach Tico support. Try again in a moment.'}}, 502); }
            return json(out(ticket));
          }
          const id = decodeURIComponent(rest.split('/')[1] || ''), ticket = hq.tickets.find(x => x.id === id);
          if (rest.endsWith('/read')) { hq.seen[id] = ticket.messages.filter(m => m.from === 'staff').length; return json(out(ticket)); }
          if (rest.endsWith('/messages') && hq.gate) await hq.gate;
          // `full`: tickets HQ refuses follow-ups to (60 messages), as backend/support.py passes HQ's 409 on.
          if (rest.endsWith('/messages') && hq.full?.includes(id)) return json({error: {code: 'support_closed', detail: 'This request is closed.'}}, 409);
          if (rest.endsWith('/messages') && hq.abort) return route.abort('connectionrefused');
          if (rest.endsWith('/messages') && hq.fail) return json({error: {detail: 'Could not reach Tico support. Try again in a moment.'}}, 502);
          if (rest.endsWith('/messages')) { ticket.messages.push({id: ticket.messages.length + 1, created_at: new Date().toISOString(), from: 'person', body: body.message}); return json(out(ticket)); }
          if (req.method() === 'DELETE' && hq.deleteGate) await hq.deleteGate;
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
    // A link to #/support, as an Assistant reply carries ("Talk to the Tico team"), opens the chat over the page (the
    // app's click handler for any such link, ui/app/ask-panel.js); it is ready once the box (or the "turned off" line)
    // shows. Focus goes back to the link on closing.
    const supportLink = page => page.evaluate(() => {
      if (document.getElementById('t-support')) return;
      const a = Object.assign(document.createElement('a'), {id: 't-support', href: '#/support', textContent: 'Talk to the Tico team'});
      a.style.cssText = 'position:fixed;top:4px;left:50%;z-index:30';
      document.body.append(a);
    });
    const openSupport = async page => {
      await supportLink(page);
      await page.locator('#t-support').click();
      await page.locator('#support-pop[open] [data-view]:not([hidden]) [name=message], #support-pop[open] [data-off]:not([hidden])').first().waitFor();
      await page.waitForFunction(() => !document.querySelector('#support-pop [data-connection]')?.textContent);
    };
    const active = page => page.evaluate(() => ({id: document.activeElement?.id, name: document.activeElement?.getAttribute('name'), inPop: !!document.activeElement?.closest('#support-pop')}));

    const at = m => new Date(Date.now() - m * 60000).toISOString();
    const noSideways = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth
      && [...document.querySelectorAll('#support-pop *')].every(x => x.getBoundingClientRect().right <= innerWidth + 1));

    // ---- A computer, no conversation yet.
    let v = await visit({viewport: {width: 1280, height: 800}});
    let {page} = v;
    // The ? sits right after Search's icon at the end of the Ask row, the same size.
    const q = page.locator('#support-open');
    await q.waitFor();
    const row = await page.evaluate(() => {
      const b = id => document.getElementById(id).getBoundingClientRect(), q = b('support-open'), s = b('search-open');
      return {prev: document.getElementById('support-open').previousElementSibling?.id, gap: q.left - s.right, dy: Math.abs(q.top - s.top), w: q.width, h: q.height, sw: s.width, sh: s.height};
    });
    assert.equal(row.prev, 'search-open');
    assert.ok(row.gap >= 0 && row.gap < 8 && row.dy < 2 && row.w === row.sw && Math.abs(row.h - row.sh) < 1, `next to Search, the same size: ${JSON.stringify(row)}`);
    assert.deepEqual([await q.getAttribute('aria-label'), await q.getAttribute('title'), await q.innerText()], ['Tico team', 'Tico team', 'help']);
    await q.click();
    const pop = page.getByRole('dialog', {name: 'Tico team'});
    await pop.locator('[name=message]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tasks', 'the page behind stays');
    const box0 = await pop.boundingBox(), side = await page.locator('#side').boundingBox();
    assert.ok(box0.x >= side.x + side.width && box0.x - (side.x + side.width) < 30 && Math.abs(box0.width - 400) <= 2 && box0.height <= 640, `a panel beside the sidebar: ${JSON.stringify(box0)}`);
    assert.deepEqual(await active(page), {id: 'support-message', name: 'message', inPop: true}, 'focus in the box');
    // The header: the headset and "Tico team", a close button. No list, form, subject, status or ticket words.
    assert.equal(await pop.locator('.support-head h2').innerText(), 'Tico team');
    assert.equal(await pop.locator('.support-head .support-av').innerText(), 'headset_mic');
    assert.equal(await pop.locator('[data-requests], [data-list], [data-mine], [data-new], [name=email], [name=subject], .support-badge').count(), 0, 'no list, form or statuses');
    assert.equal(await pop.locator('[data-delete]').isHidden(), true, 'nothing to delete yet');
    const box = pop.locator('[name=message]');
    assert.equal(await box.getAttribute('placeholder'), 'Message the Tico team');
    assert.equal(await box.getAttribute('maxlength'), '4000');
    assert.equal(await pop.locator('[data-thread]').innerText(), '', 'an empty chat: just the box');
    assert.doesNotMatch(await pop.innerText(), DESK);
    // Shift+Enter is a new line; Enter sends. No conversation open: a new ticket, its details filled in for the person.
    await box.fill('The board will not load.');
    await box.press('Shift+Enter');
    await box.pressSequentially(HOSTILE);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'Shift+Enter does not send');
    await box.press('Enter');
    await pop.locator('.support-msg.you').waitFor();
    const filed = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.deepEqual(filed, {message: 'The board will not load.\n' + HOSTILE, diagnostics: '', email: 'ana@acme.example', include_ids: true});
    assert.equal(await box.inputValue(), '', 'the box empties');
    assert.equal(await pop.locator('.support-msg.you .support-body').innerText(), 'The board will not load.\n' + HOSTILE);
    assert.equal(await page.evaluate(() => window.__pwned), undefined, 'text, never HTML');
    assert.doesNotMatch(await pop.innerText(), DESK);
    assert.deepEqual(await pop.locator('.support-day').allInnerTexts(), ['Today']);
    // The next message continues that conversation.
    await box.fill('Still blank.');
    await box.press('Enter');
    await pop.locator('.support-msg.you', {hasText: 'Still blank.'}).waitFor();
    assert.ok(v.calls.includes('POST tickets/sup_1/messages {"message":"Still blank.","diagnostics":""}'), 'a reply, not a new ticket');
    assert.equal(v.calls.filter(c => c.startsWith('POST tickets {')).length, 1);
    // Tab stays inside the chat, both ways.
    await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab');
    assert.equal((await active(page)).inPop, true);
    // Esc closes it and gives focus back to the ?; words typed but not sent are there again on reopening.
    await box.fill('half a thought');
    // The close event (which hands focus back to the ?) is dispatched after Escape: wait for it, so it cannot take focus
    // from a field the test focuses later.
    await page.evaluate(() => { window.__closed = new Promise(done => document.getElementById('support-pop').addEventListener('close', done, {once: true})); });
    await page.keyboard.press('Escape');
    await page.evaluate(() => window.__closed);
    await page.locator('#support-pop').waitFor({state: 'hidden'});
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'support-open');
    // A reply while the person is typing in another field: the ? gets its dot and the chat waits. It opens by itself once
    // the field is left (the unsent words are still in its box), and opening reads the reply. No notice, no toast.
    const focused = await page.evaluate(() => {
      window.__focus = [];
      for (const type of ['focusin', 'focusout']) document.addEventListener(type, e => window.__focus.push(type + ' ' + (e.target.id || e.target.tagName)), true);
      const i = Object.assign(document.createElement('input'), {id: 't-elsewhere'}); document.body.append(i); i.focus();
      return document.activeElement?.id;
    });
    assert.equal(focused, 't-elsewhere', 'the field being typed in has focus');
    v.hq.reply = true;
    await page.evaluate(() => window.supportPoll());
    await page.locator('#support-open.has-unread').waitFor();
    const {log, ...waiting} = await page.evaluate(() => ({unread: window.supportState().unread, active: document.activeElement?.id,
      open: document.getElementById('support-pop').open, label: document.getElementById('support-open').getAttribute('aria-label'), log: window.__focus.slice()}));
    assert.deepEqual(waiting, {unread: 1, active: 't-elsewhere', open: false, label: 'Tico team, 1 new'},
      `the reply waits while the field has focus; focus: ${JSON.stringify(log)}; calls: ${v.calls.slice(-6).join(' | ')}`);
    assert.equal(await page.locator('#ask-row').evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no count on Ask');
    await page.evaluate(() => document.getElementById('t-elsewhere').blur());
    await pop.locator('.support-msg.them').waitFor();
    assert.equal(await box.inputValue(), 'half a thought', 'unsent words survive');
    await page.locator('#support-open:not(.has-unread)').waitFor();
    assert.ok(v.calls.includes('POST tickets/sup_1/read'));
    assert.equal(await page.locator('.toast', {hasText: 'replied'}).count(), 0, 'no notice for the reply');
    // Theirs: the name above, in a bubble on the left; text, never HTML.
    assert.equal(await pop.locator('.support-msg.them .support-who').innerText(), 'Tico team');
    assert.match(await pop.locator('.support-msg.them .support-body').innerText(), /^Try the latest release\. <img src=x/);
    const sides = await pop.evaluate(el => { const b = s => el.querySelector(s).getBoundingClientRect(), t = el.querySelector('[data-thread]').getBoundingClientRect();
      return {them: b('.support-msg.them').left - t.left, you: t.right - b('.support-msg.you').right}; });
    assert.ok(sides.them < 20 && sides.you < 30, `theirs left, mine right: ${JSON.stringify(sides)}`);
    // A send that fails says so in place and keeps the words: HQ down, or no connection at all.
    v.hq.fail = true;
    await box.fill('Are you there?');
    await box.press('Enter');
    await pop.locator('[data-error]', {hasText: UNREACHABLE}).waitFor();
    assert.equal(await box.inputValue(), 'Are you there?');
    v.hq.fail = false; v.hq.abort = true;
    await box.press('Enter');
    await pop.locator('[data-error]', {hasText: UNREACHABLE}).waitFor();
    v.hq.abort = false;
    await box.press('Enter');
    await pop.locator('.support-msg.you', {hasText: 'Are you there?'}).waitFor();
    assert.equal(await pop.locator('[data-error]').isVisible(), false);
    // A click outside closes it, and so does ✕.
    await page.mouse.click(1200, 100);
    await page.locator('#support-pop').waitFor({state: 'hidden'});
    await page.evaluate(() => window.supportPoll());
    assert.equal(await page.locator('#support-pop').evaluate(el => el.open), false, 'opened once, not again');
    await q.click();
    await pop.getByRole('button', {name: 'Close'}).click();
    await page.locator('#support-pop').waitFor({state: 'hidden'});
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Two earlier conversations, one closed: one chat, oldest at the top, newest at the bottom, a date line per day
    // and the writer's name when it changes. A closed one never stops the person writing: the next message goes to the
    // one still open.
    // Within an hour after midnight the newest replies would straddle two days: then the whole seed sits in yesterday.
    const sinceMidnight = (Date.now() - new Date().setHours(0, 0, 0, 0)) / 60000, shift = sinceMidnight < 61 ? sinceMidnight + 120 : 0;
    const atDay = m => at(m + shift);
    v = await visit({viewport: {width: 1280, height: 800}, seed: [
      {id: 'sup_2', created_at: atDay(60), status: 'answered', message: 'Bots stopped', sent: {},
        messages: [{id: 1, created_at: atDay(50), from: 'staff', author_name: 'Mia', body: 'Restarted the runner.'},
                   {id: 2, created_at: atDay(49), from: 'staff', author_name: 'Mia', body: 'Try now.'},
                   {id: 3, created_at: atDay(40), from: 'staff', body: 'Anything else?'}]},
      {id: 'sup_1', created_at: atDay(3 * 24 * 60), status: 'closed', message: 'Can bots share a computer?', sent: {diagnostics: 'x'},
        messages: [{id: 1, created_at: atDay(3 * 24 * 60 - 4), from: 'staff', body: 'Yes.'}, {id: 2, created_at: atDay(3 * 24 * 60 - 5), from: 'person', body: 'Thanks'}]},
    ]});
    ({page} = v);
    // Replies that were waiting when the page opened: the chat opens by itself, once, with them. It reads them as it
    // opens, so the count is not waited on (it passes through 4 and back to 0 before a wait could see it).
    const chat = page.locator('#support-pop');
    await chat.locator('.support-msg.them', {hasText: 'Anything else?'}).waitFor();
    const order = await chat.locator('[data-thread] > *').evaluateAll(els => els.map(el => el.matches('.support-day') ? 'day'
      : (el.matches('.you') ? 'me: ' : 'them: ') + [...el.children].map(c => c.textContent).join(' | ')));
    assert.deepEqual(order.filter(x => x !== 'day'), ['me: Can bots share a computer? | Diagnostics attached', 'them: Tico team | Yes.', 'me: Thanks',
      'me: Bots stopped', 'them: Mia | Restarted the runner.', 'them: Try now.', 'them: Tico team | Anything else?'], `in time order: ${order}`);
    // Days are the browser's own (its time zone may not be this script's): count them there.
    const seen = await page.evaluate(times => ({days: new Set(times.map(t => new Date(t).toDateString())).size,
      today: new Date(times[0]).toDateString() === new Date().toDateString()}),
      [atDay(40), atDay(49), atDay(50), atDay(60), atDay(3 * 24 * 60), atDay(3 * 24 * 60 - 4), atDay(3 * 24 * 60 - 5)]);
    assert.equal(order.filter(x => x === 'day').length, seen.days, 'a date line for each day');
    assert.equal(await chat.locator('.support-day').last().innerText(), seen.today ? 'Today' : 'Yesterday');
    assert.ok(await chat.locator('[data-thread]').evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 4), 'opens at the newest');
    assert.doesNotMatch(await chat.innerText(), DESK);
    await page.locator('#support-open:not(.has-unread)').waitFor();
    assert.deepEqual(v.calls.filter(c => c.endsWith('/read')).sort(), ['POST tickets/sup_1/read', 'POST tickets/sup_2/read'], 'every reply on screen is read');
    assert.ok(v.calls.includes('POST tickets/refresh {"ticket":"sup_2"}'), 'the open conversation is asked about');
    await chat.locator('[name=message]').fill('All good now');
    await chat.locator('[name=message]').press('Enter');
    await chat.locator('.support-msg.you', {hasText: 'All good now'}).waitFor();
    assert.ok(v.calls.includes('POST tickets/sup_2/messages {"message":"All good now","diagnostics":""}'));
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')));
    // Diagnostics, from the paperclip: reviewed and edited first, attached as one small line, sent with the next message.
    await chat.getByRole('button', {name: 'Attach diagnostics'}).click();
    const dialog = page.locator('dialog.support-modal');
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    assert.equal(await dialog.locator('textarea').inputValue(), DIAGNOSTICS_TEXT);
    await dialog.locator('[name=perf]').selectOption('168');
    await page.waitForFunction(() => document.querySelector('dialog.support-modal [data-use]') && !document.querySelector('dialog.support-modal [data-use]').disabled);
    assert.equal(v.calls.filter(c => c === 'GET diagnostics').length, 2, 'a new window captures again');
    await dialog.locator('textarea').fill('{"format":1}');
    assert.equal(await dialog.locator('[data-use]').isDisabled(), true);
    await dialog.locator('[data-save]').click();
    await dialog.locator('[data-use]:not([disabled])').waitFor();
    await dialog.locator('[data-use]').click();
    await dialog.waitFor({state: 'detached'});
    assert.match(await chat.locator('[data-report]').innerText(), /^description\s+Diagnostics\s+Review\s+close$/, 'one small line over the box');
    await chat.locator('[name=message]').fill('Logs attached');
    await chat.locator('[name=message]').press('Enter');
    await chat.locator('.support-msg.you', {hasText: 'Logs attached'}).waitFor();
    assert.ok(v.calls.includes(`POST tickets/sup_2/messages {"message":"Logs attached","diagnostics":"${'e'.repeat(64)}"}`), 'the edited capture goes with it');
    assert.equal(await chat.locator('[data-report]').count(), 0, 'the next message starts with none');
    // Access isn't allowed on this server: nothing about it shows.
    assert.equal(await chat.locator('[data-access]').isHidden(), true, 'no standing access-off line');
    assert.doesNotMatch(await chat.innerText(), /Access for/);
    // Delete chat history: asked inside the chat (Cancel keeps everything), then every ticket deleted and the chat empty.
    const trash = chat.getByRole('button', {name: 'Delete chat history'});
    const boxes = await page.evaluate(() => { const b = s => document.querySelector(s).getBoundingClientRect(); return [b('#support-pop [data-delete]').right, b('#support-pop [data-close]').left]; });
    assert.ok(boxes[0] <= boxes[1], 'left of Close');
    await trash.click();
    const confirmBox = chat.getByRole('alertdialog', {name: 'Delete chat history?'});
    await confirmBox.waitFor();
    assert.equal(await chat.locator('[data-view]').isHidden(), true);
    await confirmBox.getByRole('button', {name: 'Cancel'}).click();
    await chat.locator('.support-msg.you', {hasText: 'Logs attached'}).waitFor();
    assert.ok(!v.calls.some(c => c.startsWith('DELETE')), 'Cancel deletes nothing');
    page.on('dialog', d => { throw new Error('window dialog: ' + d.message()); });
    await trash.click();
    await confirmBox.getByRole('button', {name: 'Delete'}).click();
    await confirmBox.waitFor({state: 'hidden'});
    assert.deepEqual(v.calls.filter(c => c.startsWith('DELETE')).sort(), ['DELETE tickets/sup_1', 'DELETE tickets/sup_2'], "each ticket's DELETE");
    assert.equal(await chat.locator('.support-msg, .support-day').count(), 0, 'the chat is empty');
    assert.equal(await chat.locator('[name=message]').getAttribute('placeholder'), 'Message the Tico team');
    assert.equal(await trash.isHidden(), true, 'nothing left to delete');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Only closed conversations: the next message starts a new one.
    v = await visit({seed: [{id: 'sup_1', created_at: at(90), status: 'closed', message: 'Old', sent: {}, messages: []}]});
    await openSupport(v.page);
    await v.page.locator('#support-pop .support-msg.you', {hasText: 'Old'}).waitFor();
    await v.page.locator('#support-message').fill('New thing');
    await v.page.locator('#support-message').press('Enter');
    await v.page.locator('#support-pop .support-msg.you', {hasText: 'New thing'}).waitFor();
    assert.equal(JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length)).message, 'New thing');
    assert.equal((await active(v.page)).id, 'support-message');
    await v.page.keyboard.press('Escape');
    assert.equal((await active(v.page)).id, 't-support', 'focus back on the link');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Letting the Tico team in, over the box: only what this person may give; the banner and the chat say the tier
    // and the time left in the same words; the banner opens the chat; Stop ends it at once.
    v = await visit({seed: [{id: 'sup_9', created_at: at(1), status: 'open', message: 'Bots stopped', messages: [], sent: {}}],
                     access: {available: true, off_reason: '', active: null, last: null, sessions: [], can_start: true, can_full: false}});
    await openSupport(v.page);
    const strip = v.page.locator('#support-pop [data-access]');
    await strip.getByRole('button', {name: 'Let the Tico team in'}).click();
    const letIn = v.page.locator('dialog[aria-label="Let the Tico team in"]');
    assert.equal(await letIn.locator('[name=tier][value=full]').isDisabled(), true, "full access is the owner's to give");
    await letIn.locator('[name=hours]').selectOption('4');
    await letIn.locator('[type=submit]').click();
    await v.page.locator('#support-access-banner:not([hidden])').waitFor();
    assert.ok(v.calls.includes('POST access {"ticket_id":"sup_9","tier":"app","hours":4}'));
    assert.equal(await v.page.locator('#support-access-banner [data-access-left]').innerText(), 'Tico team has access (App only), 3 h 12 min left');
    await strip.locator('.support-access-log li').waitFor();
    assert.equal(await strip.locator('.support-access-on').innerText(), 'Tico team has access (App only), 3 h 12 min left · Stop');
    assert.match(await strip.locator('.support-access-log li').innerText(), /^just now Support Agent · GET \/api\/v2\/health<img src=x/);
    await v.page.keyboard.press('Escape');
    await v.page.locator('#support-access-banner [data-access-left]').click();
    await v.page.locator('#support-pop[open]').waitFor();
    let releaseAccess;
    v.hq.accessGate = new Promise(resolve => { releaseAccess = resolve; });
    await strip.getByRole('button', {name: 'Stop'}).click();
    await v.page.locator('#support-access-banner').waitFor({state: 'hidden'});
    await strip.getByRole('button', {name: 'Let the Tico team in'}).waitFor();
    releaseAccess(); v.hq.accessGate = null;
    assert.equal(await v.page.evaluate(() => window.__pwned), undefined);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- A phone: the ? in the More drawer, a 44 px icon at the Search row's end; the chat a full page above the tab
    // bar, square, with a close control; nothing sideways.
    v = await visit({viewport: {width: 390, height: 844}, theme: 'dark', seed: [{id: 'sup_3', created_at: at(20), status: 'answered', message: 'Phone question', sent: {}, seen: 1,
      messages: [{id: 1, created_at: at(10), from: 'staff', body: 'A longer answer that wraps over a few lines on a phone screen, with a link-like-word-that-is-very-long-indeed'}]}]});
    ({page} = v);
    await page.locator('#mobile-more').click();
    await page.locator('#search-open').waitFor();
    const drawer = await page.evaluate(() => { const b = id => document.getElementById(id).getBoundingClientRect(), q = b('support-open'), s = b('search-open');
      return {gap: q.left - s.right, dy: Math.abs(q.top + q.height / 2 - s.top - s.height / 2), w: q.width, h: q.height, right: q.right, sameParent: document.getElementById('support-open').parentElement === document.getElementById('search-open').parentElement}; });
    assert.ok(drawer.sameParent && drawer.gap >= 0 && drawer.gap < 8 && drawer.dy < 2 && drawer.w >= 44 && drawer.h >= 44 && drawer.right <= 390, `beside Search in the drawer: ${JSON.stringify(drawer)}`);
    await page.locator('#support-open').click();
    const phone = page.locator('#support-pop');
    await phone.locator('.support-msg.them').waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), false, 'the drawer gets out of the way');
    const sheet = await phone.boundingBox(), bar = await page.locator('#mobile-nav').boundingBox();
    assert.ok(sheet.x === 0 && sheet.y === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - bar.y) < 2, `a full page above the tab bar: ${JSON.stringify([sheet, bar])}`);
    assert.equal(await phone.evaluate(el => getComputedStyle(el).borderTopLeftRadius), '0px', 'square, like a page');
    const close = await phone.getByRole('button', {name: 'Close'}).boundingBox();
    assert.ok(close.width >= 44 && close.height >= 44 && close.y < 60, `a close control in the header: ${JSON.stringify(close)}`);
    assert.ok(await noSideways(page), 'no horizontal scroll at 390 px');
    assert.equal(await phone.locator('[name=message]').evaluate(el => getComputedStyle(el).fontSize), '16px', 'Safari does not zoom in on the box');
    assert.notEqual(await phone.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)', 'the dark theme reaches it');
    await phone.getByRole('button', {name: 'Close'}).click();
    await phone.waitFor({state: 'hidden'});
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
    // With the keyboard up the chat ends at the keyboard: the box and Send stay in view.
    v = await visit({viewport: {width: 390, height: 844}, keyboard: true});
    await v.page.evaluate(() => window.__keys(844));
    await openSupport(v.page);
    await v.page.evaluate(() => window.__keys(458, 120));
    await v.page.waitForFunction(() => document.querySelector('#support-pop').getBoundingClientRect().height <= 458 + 1);
    const send = await v.page.locator('#support-pop .support-send').boundingBox();
    assert.ok(send.y + send.height <= 458 + 1, `Send above the keyboard: ${JSON.stringify(send)}`);
    await v.page.evaluate(() => window.__keys(844));
    await v.page.waitForFunction(() => Math.abs(document.querySelector('#support-pop').getBoundingClientRect().bottom - document.querySelector('#mobile-nav').getBoundingClientRect().top) < 2);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- An old #/help link: Overview with the chat open over it.
    v = await visit({hash: '#/help'});
    await v.page.locator('#support-pop[open] [name=message]').waitFor();
    assert.equal(await v.page.evaluate(() => location.hash), '#/overview');
    await v.page.waitForFunction(() => document.activeElement?.id === 'support-message');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- A #/support/manage link: the chat opens with the words in the box, sent only by the person.
    v = await visit();
    await v.page.evaluate(() => { const a = Object.assign(document.createElement('a'), {id: 't-manage', href: '#/support/manage', textContent: 'Fully managed'});
      a.style.cssText = 'position:fixed;top:4px;left:50%;z-index:30'; document.body.append(a); });
    await v.page.locator('#t-manage').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open] #support-message')?.value === "We'd like Tico to fully manage our company.");
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Report this on an error toast: the chat opens with the error named and attached; sent only on Send.
    v = await visit();
    await v.page.waitForFunction(() => window.supportOn(true));      // offered once Tico knows support is on
    await v.page.evaluate(() => toast('Could not save the task "Ship it" (409)', true));
    await v.page.locator('.toast.err button', {hasText: 'Report this'}).click();
    await v.page.locator('#support-pop[open] [data-report]').waitFor();
    assert.match(await v.page.locator('[data-report]').innerText(), /Diagnostics: Error/);
    // The message names only the kind: the words on screen go in the redacted attachment, never in the text.
    assert.equal(await v.page.locator('#support-message').inputValue(), 'Error: details attached. ');
    await v.page.waitForFunction(() => document.activeElement?.name === 'message' && document.activeElement.selectionStart === document.activeElement.value.length);
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'nothing is sent yet');
    await v.page.locator('#support-message').pressSequentially('It happens every time.');
    await v.page.locator('#support-message').press('Enter');
    await v.page.locator('.support-msg.you').waitFor();
    const reported = JSON.parse(v.calls.find(c => c.startsWith('POST diagnostics/capture')).slice('POST diagnostics/capture '.length)).report;
    assert.deepEqual([reported.kind, reported.title], ['toast', 'Could not save the task "Ship it" (409)']);
    assert.equal(JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length)).diagnostics, 'c'.repeat(64));
    // A Setup check makes a new ticket a setup one; another report in its place, or ×, gives back the kind before.
    await v.page.keyboard.press('Escape');
    v.hq.tickets[0].status = 'closed';
    const setup = {kind: 'setup_check', title: '1 of 2 checks fail', checks: [{name: 'HTTPS certificate', status: 'bad', detail: 'no', fix: 'Open port 443.', source: 'doctor'}]};
    await v.page.evaluate(r => talkToUs(r), setup);
    await v.page.locator('#support-pop[open] [data-report]', {hasText: 'Setup check'}).waitFor();
    await v.page.locator('[data-unreport]').click();
    assert.equal(await v.page.locator('[data-report]').count(), 0);
    assert.equal(await v.page.locator('#support-message').inputValue(), '', 'the untouched prefill goes with it');
    await v.page.evaluate(r => talkToUs(r), setup);
    await v.page.locator('[data-report]', {hasText: 'Setup check'}).waitFor();
    await v.page.locator('[data-compose] [data-preview]').click();
    await v.page.locator('dialog.support-modal [data-use]:not([disabled])').waitFor();
    const previewed = JSON.parse(v.calls.filter(c => c.startsWith('POST diagnostics/capture')).at(-1).slice('POST diagnostics/capture '.length)).report;
    assert.deepEqual(previewed.checks.map(c => [c.name, c.status]), [['HTTPS certificate', 'bad']]);
    await v.page.locator('dialog.support-modal [data-use]').click();
    await v.page.locator('#support-message').press('Enter');
    await v.page.waitForFunction(() => document.querySelectorAll('#support-pop .support-msg.you').length === 2);
    assert.equal(JSON.parse(v.calls.filter(c => c.startsWith('POST tickets {')).at(-1).slice('POST tickets '.length)).kind, 'setup');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- "Want help setting up?": the snapshot in the box, whole, for the person to read and send; twice is still once.
    v = await visit();
    await v.page.evaluate(() => document.querySelector('#main').insertAdjacentHTML('beforeend', '<button type="button" data-setup-help="health">Want help setting up?</button>'));
    await v.page.locator('#main [data-setup-help]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open] #support-message')?.value.startsWith("I'd like help setting up Tico."));
    assert.ok(v.calls.includes('asked from health'));
    assert.ok(await v.page.locator('#support-message').evaluate(el => el.scrollHeight <= el.clientHeight + 1), 'the snapshot shows whole');
    await v.page.keyboard.press('Escape');
    await v.page.locator('#main [data-setup-help]').click();
    await v.page.waitForFunction(() => document.querySelector('#support-pop[open]') && !document.querySelector('#main [data-setup-help]').disabled);
    await v.page.waitForTimeout(100);
    assert.equal((await v.page.locator('#support-message').inputValue()).split("I'd like help setting up Tico.").length, 2, 'one snapshot, not two');
    assert.ok(!v.calls.some(c => c.startsWith('POST tickets {')), 'nothing sent before Send');
    await v.page.locator('#support-message').press('Enter');
    await v.page.locator('.support-msg.you', {hasText: 'Computer online'}).waitFor();
    assert.equal(JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length)).kind, 'setup');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Inside the desktop app: a new conversation carries the app's version; diagnostics the end of its log.
    v = await visit({native: true, userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15'});
    await openSupport(v.page);
    await v.page.getByRole('button', {name: 'Attach diagnostics'}).click();
    await v.page.locator('dialog.support-modal [data-use]:not([disabled])').waitFor();
    const app = JSON.parse(v.calls.find(c => c.startsWith('POST diagnostics/capture')).slice('POST diagnostics/capture '.length)).app;
    assert.deepEqual(app, {version: '0.3.48', lines: ['2026-10-16T06:47:31Z WARN tico_app: update check failed: 401'], mac: true});
    await v.page.locator('dialog.support-modal [data-use]').click();
    await v.page.locator('#support-message').fill('From the app');
    await v.page.locator('#support-message').press('Enter');
    await v.page.locator('.support-msg.you').waitFor();
    const fromApp = JSON.parse(v.calls.find(c => c.startsWith('POST tickets {')).slice('POST tickets '.length));
    assert.deepEqual([fromApp.app_version, fromApp.diagnostics], ['0.3.48', 'c'.repeat(64)]);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- "How is Tico working for you?": Send files the answer as feedback, and the question goes.
    v = await visit({feedback: {moment: 'day_7', question: 'How is Tico working for you?'}});
    const ask = v.page.locator('#feedback-ask');
    await ask.waitFor();
    await ask.locator('[name=answer]').fill('Faster than I expected');
    await ask.locator('[type=submit]').click();
    await ask.waitFor({state: 'hidden'});
    assert.ok(v.calls.includes('POST feedback {"moment":"day_7","answer":"Faster than I expected"}'));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- A full conversation: HQ refuses the follow-up (409), so the same message starts a new one, shown as sent.
    v = await visit({seed: [{id: 'sup_1', created_at: at(30), status: 'answered', message: 'Long thread', sent: {}, messages: []}]});
    v.hq.full = ['sup_1'];
    await openSupport(v.page);
    {
      const chat = v.page.locator('#support-pop');
      await chat.locator('[name=message]').fill('One more thing');
      await chat.locator('[name=message]').press('Enter');
      await chat.locator('.support-msg.you', {hasText: 'One more thing'}).waitFor();
      assert.ok(v.calls.includes('POST tickets/sup_1/messages {"message":"One more thing","diagnostics":""}'));
      assert.ok(v.calls.includes('POST tickets {"message":"One more thing","diagnostics":"","email":"ana@acme.example","include_ids":true}'), v.calls.join('\n'));
      assert.equal(await chat.locator('[data-error]').isHidden(), true, 'no error shown');
      assert.equal(await chat.locator('[name=message]').inputValue(), '');
      // The next message goes to the new conversation.
      await chat.locator('[name=message]').fill('And another');
      await chat.locator('[name=message]').press('Enter');
      await chat.locator('.support-msg.you', {hasText: 'And another'}).waitFor();
      assert.ok(v.calls.includes('POST tickets/sup_2/messages {"message":"And another","diagnostics":""}'));
      assert.deepEqual(v.errors, []);
    }
    await v.ctx.close();

    // ---- The new conversation is saved but its reply is lost: Send again repeats that creation (same key), no second one.
    v = await visit({seed: [{id: 'sup_1', created_at: at(30), status: 'answered', message: 'Long thread', sent: {}, messages: []}]});
    Object.assign(v.hq, {full: ['sup_1'], loseCreate: true});
    await openSupport(v.page);
    {
      const chat = v.page.locator('#support-pop');
      await chat.locator('[name=message]').fill('One more thing');
      await chat.locator('[name=message]').press('Enter');
      await chat.locator('[data-error]').waitFor();
      await chat.locator('[name=message]').press('Enter');
      await chat.locator('.support-msg.you', {hasText: 'One more thing'}).waitFor();
      assert.equal(v.hq.createKeys.length, 2);
      assert.equal(v.hq.createKeys[0], v.hq.createKeys[1], 'the retry carries the first creation\'s key');
      assert.equal(v.hq.tickets.filter(t => t.message === 'One more thing').length, 1, 'one new conversation');
      assert.equal(await chat.locator('[name=message]').inputValue(), '');
    }
    await v.ctx.close();

    // ---- Same, but the chat is closed and opened again first: it now shows the saved conversation, and Send still
    // repeats the creation rather than adding the message to it.
    v = await visit({seed: [{id: 'sup_1', created_at: at(30), status: 'answered', message: 'Long thread', sent: {}, messages: []}]});
    Object.assign(v.hq, {full: ['sup_1'], loseCreate: true});
    await openSupport(v.page);
    {
      let chat = v.page.locator('#support-pop');
      await chat.locator('[name=message]').fill('One more thing');
      await chat.locator('[name=message]').press('Enter');
      await chat.locator('[data-error]').waitFor();
      await v.page.keyboard.press('Escape');
      await chat.waitFor({state: 'hidden'});
      await openSupport(v.page);
      chat = v.page.locator('#support-pop');
      await chat.locator('.support-msg.you', {hasText: 'One more thing'}).first().waitFor();
      assert.equal(await chat.locator('[name=message]').inputValue(), 'One more thing', 'the unsent words are still in the box');
      await chat.locator('[name=message]').press('Enter');
      await v.page.waitForFunction(() => document.querySelector('#support-pop [name=message]')?.value === '');
      assert.equal(v.calls.filter(c => c.startsWith('POST tickets/sup_2/messages')).length, 0, 'not added to the new conversation');
      assert.equal(v.hq.createKeys.length, 2);
      assert.equal(v.hq.createKeys[0], v.hq.createKeys[1]);
      const made = v.hq.tickets.find(t => t.message === 'One more thing');
      assert.ok(made && made.messages.length === 0, 'the message is there once');
    }
    await v.ctx.close();

    // ---- Delete chat history with 51 conversations: the list holds 50, so it lists and deletes again until empty.
    v = await visit({seed: Array.from({length: 51}, (_, i) => ({id: 'old_' + i, created_at: at(100 + i), status: 'closed', message: 'Old ' + i, sent: {}, messages: []}))});
    await openSupport(v.page);
    {
      const chat = v.page.locator('#support-pop');
      await chat.locator('.support-msg.you', {hasText: 'Old 0'}).waitFor();
      await chat.getByRole('button', {name: 'Delete chat history'}).click();
      let release; v.hq.deleteGate = new Promise(r => { release = r; });
      await chat.getByRole('alertdialog').getByRole('button', {name: 'Delete'}).click();
      await v.page.waitForFunction(() => [...document.querySelectorAll('#support-pop [data-compose] :is(textarea, [type=submit])')].every(el => el.disabled));
      assert.equal(await chat.getByRole('alertdialog').getByRole('button', {name: 'Delete'}).isDisabled(), true, 'once');
      release(); v.hq.deleteGate = null;
      await chat.getByRole('alertdialog').waitFor({state: 'hidden'});
      assert.equal(v.calls.filter(c => c.startsWith('DELETE')).length, 51, 'all 51 deleted');
      assert.ok(v.calls.includes('DELETE tickets/old_50'), 'the 51st too');
      assert.ok(v.calls.filter(c => c === 'GET tickets').length >= 3, 'listed again until empty');
      assert.equal(v.hq.tickets.length, 0);
      assert.equal(await chat.locator('.support-msg').count(), 0, 'the chat is empty');
      assert.deepEqual(v.errors, []);
    }
    await v.ctx.close();

    // ---- Delete chat history while a message is on its way: Delete waits for it, then that conversation goes too.
    v = await visit({seed: [{id: 'sup_1', created_at: at(30), status: 'answered', message: 'First', sent: {}, messages: []}]});
    await openSupport(v.page);
    {
      const chat = v.page.locator('#support-pop');
      let release; v.hq.gate = new Promise(r => { release = r; });
      await chat.locator('[name=message]').fill('Slow one');
      await chat.locator('[name=message]').press('Enter');
      await v.page.waitForFunction(() => document.querySelector('#support-pop [data-compose] [name=message]').disabled);
      await chat.getByRole('button', {name: 'Delete chat history'}).click();
      const yes = chat.getByRole('alertdialog').getByRole('button', {name: 'Delete'});
      assert.equal(await yes.isDisabled(), true, 'Delete waits while a message is on its way');
      release(); v.hq.gate = null;
      await v.page.waitForFunction(() => !document.querySelector('#support-pop [data-confirm-yes]').disabled);
      await yes.click();
      await chat.getByRole('alertdialog').waitFor({state: 'hidden'});
      assert.ok(v.calls.includes('DELETE tickets/sup_1'));
      assert.equal(v.hq.tickets.length, 0);
      assert.equal(await chat.locator('.support-msg').count(), 0, 'the sent message does not come back');
      await v.page.waitForTimeout(300);
      assert.equal(await chat.locator('.support-msg').count(), 0);
      assert.equal(await chat.locator('[name=message]').isDisabled(), false, 'the box works again');
      assert.deepEqual(v.errors, []);
    }
    await v.ctx.close();

    // ---- A bot's report the team answered: its reply is in the chat, but it neither opens the chat nor puts a dot on the ?.
    v = await visit({seed: [{id: 'sup_1', created_at: at(30), status: 'answered', message: 'Bot report', sent: {source: 'bot_report'},
      messages: [{id: 1, created_at: at(5), from: 'staff', body: 'The report is with the team.'}]}]});
    await v.page.waitForFunction(() => window.supportState().enabled === true);
    await v.page.evaluate(() => window.supportPoll());
    assert.equal(await v.page.locator('#support-pop').evaluate(el => el.open), false, 'a bot report reply does not open the chat');
    assert.equal(await v.page.locator('#support-open.has-unread').count(), 0, 'nor a dot on the ?');
    assert.equal(await v.page.evaluate(() => window.supportState().unread), 0);
    const read = v.page.waitForResponse(r => r.url().endsWith('/api/v2/support/tickets/sup_1/read'));
    await openSupport(v.page);
    await v.page.locator('#support-pop .support-msg.them', {hasText: 'The report is with the team.'}).waitFor();
    await read;
    assert.ok(v.calls.includes('POST tickets/sup_1/read'), 'opening the chat reads it');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // ---- Support turned off: no ?; a #/support link still opens the chat, which says so, with no box.
    v = await visit({enabled: false});
    await v.page.waitForFunction(() => window.supportOn(true) === false);
    await v.page.locator('#support-open').waitFor({state: 'hidden'});
    await openSupport(v.page);
    const off = v.page.getByRole('dialog', {name: 'Tico team'});
    assert.equal(await off.locator('[data-off]').innerText(), 'Messages to the Tico team are off on this Tico server.');
    assert.equal(await off.locator('[name=message]').isVisible(), false);
    await v.page.keyboard.press('Escape');
    assert.equal((await active(v.page)).id, 't-support');
    await v.page.evaluate(() => toast('Could not save', true));
    assert.equal(await v.page.locator('.toast.err button').count(), 0, 'Report this is not offered');
    assert.ok(!v.calls.some(c => c.startsWith('POST')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
