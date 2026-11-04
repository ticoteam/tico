// A thread's flat timeline as the server sends it with `flat=1` (docs/dev/threads-contract.md): one author's messages
// within five minutes under one header, a reply's one-line quote (and a hidden one), the hover bar's React · Reply ·
// Copy and the reaction picker, replying from a message (the quote above the composer, `in_reply_to` on the post, the
// quote on the new message, a click on it scrolling back), the routing line under a person's message, the bots' status
// rows at the end in their three states (Stop, Fix, a ticking clock) and live from the `bot_status` event, the composer's
// placeholder, the Ask's question as its own row above the composer, the bot page's thread actions in its top line,
// and a phone at 390px (no sideways scroll, finger-sized targets, the quote cut short, the long press's sheet).
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots: threads-halfway-<name>-<desktop|phone>-<dark|light>.png.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), at = min => new Date(now - min * 60000).toISOString();
const me = (extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 99, unread: 0, activity: false, can_post: true, can_manage: false, ...extra});
const team = {id: 't-ship', title: 'Ship day', audience: 'team', audience_group_id: 'ops', last_seq: 9, last_message_at: at(1), me: me(), members: []};
const msg = (id, seq, from, body, min, extra = {}) => ({id, seq, thread_id: 't-ship', sender_id: from, body, created_at: at(min), reply_to: null, routed: null, ...extra});
const quote = (id, author, author_name, excerpt) => ({id, author, author_name, excerpt});
const LONG = 'The Acme invoice run needs the new tax table before it can go out, and the reviewers want the totals checked twice against last month';
const messages = () => [
  msg('m1', 1, 'human:bea', LONG, 20),
  msg('m2', 2, 'human:bea', 'It needs the new tax table.', 19),
  msg('m3', 3, 'bot:ops', 'Yes. The run is queued for 4pm.', 18, {reply_to: quote('m1', 'human:bea', 'Bea', LONG), refs: {turn_id: 'turn1'}, run: {steps: 2, tool_calls: 1, took_s: 3}}),
  msg('m4', 4, 'human:ana', 'Thanks, Ops.', 6, {routed: {to: [{actor: 'bot:ops', name: 'Ops'}]}}),
  msg('m5', 5, 'human:cy', 'No action needed here.', 5, {routed: {to: []}}),
  msg('m6', 6, 'human:bea', 'Agreed.', 4, {reply_to: {id: 'mx', hidden: true}}),
];
const working = (min = 1.1) => ({actor: 'bot:ops', name: 'Ops', state: 'working', detail: 'Reading the tax table', since: at(min), run_id: 'run1', fix: null});
const statuses = () => [working(), {actor: 'bot:fin', name: 'Fin', state: 'up_next', detail: 'After Ops', since: at(1), run_id: null, fix: null},
  {actor: 'bot:qa', name: 'QA', state: 'cant_reply', detail: 'Out of credits', since: at(2), run_id: null, fix: {label: 'Fix', href: '#/settings/billing'}}];
const dmThread = {id: 't-dm-ops', title: 'Chat with Ops', audience: 'personal', last_seq: 2, me: me(), members: [{actor: 'human:ana'}, {actor: 'bot:ops'}],
  dm_with: [{actor: 'bot:ops', name: 'Ops'}], dm_with_count: 1};
const dmMessages = () => [{id: 'd1', seq: 1, thread_id: 't-dm-ops', sender_id: 'human:ana', body: 'Can you check the Acme invoices?', created_at: at(2), reply_to: null,
  routed: {to: [{actor: 'bot:ops', name: 'Ops'}]}}];

async function open(browser, {phone = false} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const seen = {errors: [], posts: [], stops: [], timelines: [], reacts: []};
  const sse = {queue: [], seq: 0, latest: null, done: false};
  page.on('pageerror', e => seen.errors.push(e.message));
  await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async t => { window.copied = t; }}}); });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json(['ops', 'fin', 'qa'].map(name => ({name, display_name: name === 'qa' ? 'QA' : name[0].toUpperCase() + name.slice(1),
      host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [], my_access: {see: true, read: true, write: true}})));
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'},
      {id: 'cy', name: 'Cy', email: 'cy@acme.example'}], org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/v2/events') {
      // A stand-in stream: the newest connection with the threads topic waits for the events the test sends, then ends.
      if (!(url.searchParams.get('topics') || '').split(',').includes('threads')) return route.abort();
      sse.latest = req;
      while (!sse.queue.length || sse.latest !== req) {
        if (sse.latest !== req || sse.done) return route.abort().catch(() => {});
        await new Promise(r => setTimeout(r, 50));
      }
      const body = 'event: ready\ndata: {"seq":1}\n\n' + sse.queue.splice(0).map(d => `event: threads\nid: ${++sse.seq}\ndata: ${JSON.stringify(d)}\n\n`).join('');
      return route.fulfill({status: 200, contentType: 'text/event-stream', body}).catch(() => {});
    }
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [team], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [team], dms: [dmThread], next_cursor: null});
    if (p === '/api/v2/threads/dm') return json(dmThread);
    if (p === '/api/v2/threads/home/' + encodeURIComponent('bot:ops') || p === '/api/v2/threads/home/bot:ops') return json({error: 'not_found'}, 404);
    const tl = p.match(/^\/api\/v2\/threads\/([^/]+)\/timeline$/);
    if (tl) {
      seen.timelines.push(url.searchParams.get('flat'));
      if (tl[1] === 't-ship') return json({thread: team, breadcrumb: [{id: 't-ship', title: 'Ship day'}], next_cursor: null, messages: messages(), bot_status: statuses()});
      if (tl[1] === 't-dm-ops') return json({thread: dmThread, breadcrumb: [], next_cursor: null, messages: dmMessages(), bot_status: [working(0.4)]});
    }
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false} : p.endsWith('members') ? {members: [], next_cursor: null} : {ok: true});
    if (/^\/api\/v2\/threads\/[^/]+\/bot-status$/.test(p)) return json({bots: statuses()});
    const send = p.match(/^\/api\/v2\/threads\/([^/]+)\/messages$/);
    if (send && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}'); seen.posts.push(body);
      const target = messages().find(m => m.id === body.in_reply_to);
      return json({message: msg('n' + seen.posts.length, 20 + seen.posts.length, 'human:ana', body.text, 0, {thread_id: send[1], in_reply_to: body.in_reply_to || null,
        reply_to: target ? quote(target.id, target.sender_id, 'Bea', target.body) : null})});
    }
    const stop = p.match(/^\/api\/v2\/bots\/([^/]+)\/stop$/);
    if (stop) { seen.stops.push({bot: stop[1], ...JSON.parse(req.postData() || '{}')}); return json({ok: true}); }
    const react = p.match(/^\/api\/v2\/messages\/([^/]+)\/reactions\/(.+)$/);
    if (react) { seen.reacts.push(decodeURIComponent(react[2])); return json(method === 'PUT' ? [{emoji: decodeURIComponent(react[2]), count: 1, actors: ['human:ana']}] : []); }
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    return json(unrouted(p));
  });
  return {page, seen, sse};
}
async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `threads-halfway-${name}.png`)});
}
async function themed(page, name, phone = false) {
  if (!shots) return;
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.waitForTimeout(80);
    await shot(page, `${name}-${phone ? 'phone' : 'desktop'}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
const row = (page, id) => page.locator(`.th-msg[data-mid="${id}"]`);
const box = loc => loc.boundingBox();

let browser = null;
(async () => {
  browser = await chromium.launch();

  // ---- a shared thread, desktop
  {
    const {page, seen, sse} = await open(browser);
    await page.goto('https://tico-ui.test/#/thread/t-ship');
    await row(page, 'm6').waitFor();
    assert.ok(seen.timelines.includes('1'), 'the timeline is read flat');

    // Grouping: Bea's second message within five minutes sits under her first header; the bot's reply starts its own.
    assert.equal(await row(page, 'm1').locator('.th-head').count(), 1);
    assert.equal(await row(page, 'm2').evaluate(e => e.classList.contains('th-cont')), true);
    assert.equal(await row(page, 'm2').locator('.th-head').count(), 0);
    assert.equal(await row(page, 'm2').locator('.th-cont-time').count(), 1, 'its time in the avatar column');
    assert.equal(await row(page, 'm3').locator('.th-head .th-bot').count(), 1, 'a bot is tagged');
    assert.equal(await row(page, 'm1').locator('.th-bot').count(), 0);
    // A reply starts a new group even from the same author.
    assert.equal(await row(page, 'm6').locator('.th-head').count(), 1);

    // Quotes: author and the start of the words; a hidden one says only that it is not shown.
    assert.equal(await row(page, 'm3').locator('.th-quote').getAttribute('data-th-quote'), 'm1');
    assert.equal(await row(page, 'm3').locator('.th-quote-who').textContent(), 'Bea');
    assert.equal(await row(page, 'm6').locator('.th-quote-hidden').count(), 1);
    assert.equal(await row(page, 'm6').locator('[data-th-quote]').count(), 0, 'a hidden quote leads nowhere');
    // A bot's reply: its steps line first, then its words, then Copy · 👍 · 👎.
    const parts = await row(page, 'm3').evaluate(e => [...e.querySelector('.th-main').children].map(c => c.matches('.th-quote') ? 'quote'
      : c.matches('.run-steps') || c.querySelector('.run-steps') ? 'steps' : c.matches('.th-body') ? 'body' : c.matches('.fb-row') ? 'fb' : 'other'));
    assert.deepEqual(parts.filter(x => x !== 'other'), ['quote', 'steps', 'body', 'fb'], 'quote, steps, words, then feedback: ' + parts);

    // Routing: under a person's message, the bots it asked; none, it says so. Never under a bot's.
    assert.match(await row(page, 'm4').locator('.th-routed').textContent(), /Ops/);
    assert.equal(await row(page, 'm5').locator('.th-routed').count(), 1);
    assert.doesNotMatch(await row(page, 'm5').locator('.th-routed').textContent(), /Ops/);
    assert.equal(await row(page, 'm3').locator('.th-routed').count(), 0);

    // The hover bar is React, Reply and Copy, nothing else.
    await row(page, 'm2').hover();
    assert.deepEqual(await row(page, 'm2').locator('.th-acts button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label'))), ['React', 'Reply', 'Copy']);
    await themed(page, 'hover-bar');
    await row(page, 'm2').locator('[data-th-copy]').click();
    await page.waitForFunction(() => window.copied === 'It needs the new tax table.');
    // React: six common emoji in the picker; a pick reacts.
    await row(page, 'm2').locator('[data-th-pick]').click();
    assert.ok(await page.locator('.thv-emoji button').count() >= 6);
    await page.locator('.thv-emoji button[data-k="🎉"]').click();
    await row(page, 'm2').locator('.th-react.me').waitFor();
    assert.deepEqual(seen.reacts, ['🎉']);

    // The bots' status at the end: working (Stop, a ticking clock), up next, can't reply (Fix).
    const status = page.locator('.th-status');
    assert.deepEqual(await status.evaluateAll(els => els.map(e => e.dataset.thStatus)), ['bot:ops', 'bot:fin', 'bot:qa']);
    assert.equal(await page.locator('.thv-list > :last-child').getAttribute('data-th-status'), 'bot:qa', 'at the end of the timeline');
    assert.equal(await page.locator('.th-status-cant_reply a[href="#/settings/billing"]').count(), 1);
    assert.equal(await page.locator('.th-status-up_next [data-th-stop], .th-status-up_next a').count(), 0);
    const clock = page.locator('.th-status-working [data-th-since]');
    const t0 = await clock.textContent();
    assert.match(t0, /^\d+:\d\d$/);
    await page.waitForFunction(t => document.querySelector('.th-status-working [data-th-since]')?.textContent !== t, t0, {timeout: 3000});
    await page.locator('.th-status-working [data-th-stop]').click();
    for (let i = 0; i < 40 && !seen.stops.length; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.stops, [{bot: 'ops', run_id: 'run1'}]);
    await themed(page, 'shared-thread');

    // The composer names the thread.
    const text = page.locator('.thv-composer .p-text');
    assert.match(await text.getAttribute('placeholder'), /#Ship day/);

    // Reply: from the hover bar, a quote above the composer with ×; the post names the message; the new one quotes it.
    await row(page, 'm1').hover();
    await row(page, 'm1').locator('[data-th-reply]').click();
    const replying = page.locator('.thv-replying');
    await replying.waitFor();
    assert.equal(await replying.locator('b').textContent(), 'Bea');
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('p-text')), true, 'the composer has the focus');
    await replying.locator('[data-reply-cancel]').click();
    assert.equal(await replying.isHidden(), true);
    await row(page, 'm1').hover();
    await row(page, 'm1').locator('[data-th-reply]').click();
    await text.fill('On it.');
    await page.locator('.thv-composer .p-send').click();
    await row(page, 'n1').waitFor();
    assert.equal(seen.posts.at(-1).in_reply_to, 'm1');
    assert.equal(await replying.isHidden(), true, 'the quote leaves the composer once sent');
    assert.equal(await row(page, 'n1').locator('.th-quote').getAttribute('data-th-quote'), 'm1');
    // A click on the quote scrolls to the message and lights it for a moment.
    await page.evaluate(() => { const b = document.querySelector('.thv-scroll'); b.scrollTop = b.scrollHeight; });
    await row(page, 'n1').locator('.th-quote').click();
    await page.locator('.th-msg.th-flash[data-mid="m1"]').waitFor();
    await page.waitForFunction(() => { const b = document.querySelector('.thv-scroll').getBoundingClientRect(), r = document.querySelector('[data-mid="m1"]').getBoundingClientRect(); return r.top >= b.top - 1 && r.top < b.bottom; });
    await page.waitForFunction(() => !document.querySelector('.th-flash'), null, {timeout: 4000});

    // Live: a `bot_status` event patches the rows; the bot's reply landing takes its row away.
    sse.queue.push({thread_id: 't-ship', kind: 'bot_status', bots: [working(0.2)]});
    await page.waitForFunction(() => document.querySelectorAll('.th-status').length === 1);
    assert.equal(await page.locator('.th-status').getAttribute('data-th-status'), 'bot:ops');
    await page.evaluate(() => threadViewsLive('messages', {id: 'live1', thread_id: 't-ship', root_id: 't-ship',
      message: {id: 'live1', seq: 40, revision: 1, thread_id: 't-ship', sender_id: 'bot:ops', body: 'The tax table is loaded.', in_reply_to: 'm2', created_at: new Date().toISOString()}}));
    await row(page, 'live1').waitFor();
    assert.equal(await page.locator('.th-status').count(), 0, 'the reply takes the row away');
    assert.equal(await row(page, 'live1').locator('.th-quote').getAttribute('data-th-quote'), 'm2', 'a live reply quotes what it answers');

    // The Ask's question, with a thread's composer on screen, is its own row above the composer, never over it.
    await page.evaluate(() => {
      const el = document.getElementById('ask-peek');
      el.querySelector('[data-peek-text]').textContent = 'Got a quick question for you';
      el.dataset.item = 'q1'; el.classList.add('ask-on'); askPeekDock();
    });
    assert.equal(await page.locator('.thv-composer .thv-peek-slot #ask-peek').count(), 1);
    const [peek, pill] = [await box(page.locator('#ask-peek')), await box(page.locator('.thv-composer .thv-ask'))];
    assert.ok(peek.y + peek.height <= pill.y + 0.5, `the peek sits above the composer: ${JSON.stringify([peek, pill])}`);
    // Away from a thread it floats by the dot again.
    await page.goto('https://tico-ui.test/#/tasks');
    await page.waitForFunction(() => document.getElementById('ask-peek')?.parentElement === document.body);
    assert.equal(await page.locator('#ask-peek.ask-peek-row').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // ---- a bot's DM with the bot working, desktop: its placeholder, its status, and the thread's actions in the top line
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/bot/ops/dm');
    await page.locator('#bot-thread .th-status-working').waitFor();
    assert.equal(await page.locator('#bot-thread .thv-composer .p-text').getAttribute('placeholder'), 'Message Ops');
    await page.locator('#bot-top #bot-top-acts [data-thv-menu]').waitFor();
    const [top, menu] = [await box(page.locator('#bot-top')), await box(page.locator('#bot-top-acts [data-thv-menu]'))];
    assert.ok(menu.y >= top.y && menu.y + menu.height <= top.y + top.height + 0.5, 'the menu sits in the top line');
    assert.equal(await page.locator('#pane-chat .bot-talk-bar .thv-menu-btn').count(), 0, 'no box of its own under the tabs');
    await themed(page, 'bot-dm-working');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // ---- a phone at 390px
  {
    const {page, seen} = await open(browser, {phone: true});
    await page.goto('https://tico-ui.test/#/thread/t-ship');
    await row(page, 'm6').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll');
    // The quote is cut short on one line, and a finger's size.
    const q = row(page, 'm3').locator('.th-quote');
    const qb = await box(q);
    assert.ok(qb.x + qb.width <= 390 && qb.height >= 44, 'quote fits and is tappable: ' + JSON.stringify(qb));
    assert.equal(await q.locator('.th-quote-text').evaluate(e => e.scrollWidth > e.clientWidth && getComputedStyle(e).textOverflow === 'ellipsis'), true);
    for (const sel of ['.th-status-working [data-th-stop]', '.th-status-cant_reply a']) assert.ok((await box(page.locator(sel))).height >= 44, sel + ' is finger-sized');
    // No hover bar; a long press opens the sheet with the emoji, Reply and Copy.
    assert.equal(await row(page, 'm2').locator('[data-th-reply]').isVisible(), false);
    await row(page, 'm2').scrollIntoViewIfNeeded();
    const b = await box(row(page, 'm2').locator('.th-body'));
    await page.evaluate(() => { window.__ev = []; for (const t of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'contextmenu', 'scroll'])
      addEventListener(t, ev => window.__ev.push(t + ':' + (ev.pointerType || '') + ':' + ev.isPrimary + ':' + (ev.target?.closest?.('.th-msg')?.dataset.mid || '-') + ':' + Math.round(performance.now())), true);
      addEventListener('error', ev => window.__ev.push('error:' + ev.message));
      const sheet = window.thvSheet; window.thvSheet = (...a) => { window.__ev.push('sheet'); try { return sheet(...a); } catch (e) { window.__ev.push('sheet-error:' + e.message); throw e; } }; });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: b.x + 20, y: b.y + b.height / 2}]});
    await page.waitForTimeout(650);
    await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
    await cdp.detach();
    const sheet = page.locator('dialog.thv-sheet[open]');
    await sheet.waitFor({timeout: 5000}).catch(async e => {
      throw new Error(`no sheet; the press saw ${JSON.stringify(await page.evaluate(() => window.__ev))}: ${e.message.split('\n')[0]}`);
    });
    assert.deepEqual(await sheet.locator('.fb-menu-row button').evaluateAll(bs => bs.map(x => x.getAttribute('aria-label'))), ['Reply', 'Copy']);
    assert.ok(await sheet.locator('[data-pick-emoji]').count() >= 6);
    await themed(page, 'long-press', true);
    await sheet.locator('[data-pick="reply"]').click();
    await page.locator('.thv-replying:not([hidden])').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll with the quote above the composer');
    assert.ok((await box(page.locator('.thv-replying-x'))).height >= 44);
    await page.locator('.thv-replying-x').click();
    await page.evaluate(() => { const b = document.querySelector('.thv-scroll'); b.scrollTop = b.scrollHeight; });
    await themed(page, 'shared-thread', true);
    await page.goto('https://tico-ui.test/#/bot/ops/dm');
    await page.locator('#bot-thread .th-status-working').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'the bot page does not scroll sideways');
    await themed(page, 'bot-dm-working', true);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }
  console.log('threads halfway: ok');
})().then(() => browser?.close(), async e => { console.error(e); await browser?.close(); process.exit(1); });
