// BotOps' Confirm cards in its chat (ui/assistant.js), fixtures only.
//  - plain words: the card's title is the server's sentence, never the method and path; a card stored with no readable
//    description gets a plain sentence too. The method, path and fields sit behind a closed Details.
//  - no jump on Confirm: in a chat scrolled partway down with several cards, a click changes only that card. The thread
//    and the sidebar keep their scroll, no card falls back to "Loading the card…", and an open Details stays open.
//    Every other card is the same element after the rebuild, so a "Show more" opened on one stays open and where it
//    was, and focus stays put: on a card being read, and on the card whose Confirm was pressed from the keyboard.
//    A Confirm that fails leaves the card waiting with its buttons usable.
//  - a repository card stored before the plain wording ("access to chosen") reads as the server now sends it: a plain
//    title, and the old line only under Details (backend/assistant.py action_view).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const at = n => new Date(Date.UTC(2026, 9, 17, 9, n)).toISOString();
const say = (id, from, body, n, refs = {}) => ({id, from_actor: from, body, refs, created_at: at(n), kind: 'say'});

function fixture() {
  const card = (id, extra) => ({id, owner: 'human:ana', status: 'pending', result: null, proposed_via: 'botops', proposer: 'botops',
    diff: [], body: {}, ...extra});
  const actions = {
    act1: card('act1', {method: 'PUT', path: '/api/v2/bots/security-engineer-2/repositories',
      summary: 'Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.',
      description: 'Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.',
      body: {mode: 'chosen', chosen: [{full_name: 'ticoteam/tico', access: 'read'}]},
      diff: [{field: 'mode', new: 'chosen'}, {field: 'chosen', new: [{full_name: 'ticoteam/tico', access: 'read'}]}]}),
    // A card stored before its route had a sentence: the description was the method and path.
    act2: card('act2', {method: 'POST', path: '/api/v2/bots/ops/go-live', summary: 'Ops asked BotOps for this for you: Launch Ops',
      description: 'POST /api/v2/bots/ops/go-live', body: {confirm: true}, diff: [{field: 'confirm', new: true}]}),
    // Chris's card from before the plain wording, as action_view sends it now: the title read from the stored body,
    // the old line moved under Details.
    act0: card('act0', {method: 'PUT', path: '/api/v2/bots/security-engineer-2/repositories', summary: '',
      original_summary: "Set Security Engineer's repository access to chosen. This stops: ticoteam/tico (write), ticoteam/tico-marketing-site (write)",
      description: 'Security Engineer: read-only on tico, besides its own repository.',
      body: {mode: 'chosen', chosen: [{full_name: 'ticoteam/tico', access: 'read'}]},
      diff: [{field: 'mode', new: 'chosen'}, {field: 'chosen', new: [{full_name: 'ticoteam/tico', access: 'read'}]}]}),
    // A long send above the card clicked: its "Show more" is opened and must stay open.
    actL: card('actL', {method: 'POST', path: '/api/v2/messages', summary: 'Tell Ben the plan', description: 'Send a message to Ben',
      body: {to: 'human:ben', body: 'Hi Ben, here is the whole plan. ' + 'Each step is written out so nothing is missed. '.repeat(12)}}),
    act3: card('act3', {method: 'POST', path: '/api/v2/bots/ops/archive', summary: 'Archive the Ops bot', description: 'Archive bot Ops',
      body: {expected_revision: 3}, diff: [{field: 'expected_revision', new: 3}]}),
    // A send: the words that go out are what the person approves, so they show with Details closed.
    act4: card('act4', {method: 'POST', path: '/api/v2/messages', summary: 'Tell Ben the launch moved', description: 'Send a message to Ben',
      body: {to: 'human:ben', body: 'Hi Ben, the launch moves to Friday.'},
      diff: [{field: 'to', new: 'human:ben'}, {field: 'body', new: 'Hi Ben, the launch moves to Friday.'}]}),
  };
  const messages = [];
  let n = 0;
  const filler = k => { for (let i = 0; i < k; i++) messages.push(say('h' + n, n % 2 ? 'bot:botops' : 'human:ana', `Line ${n}. ` + 'Some words to fill the line. '.repeat(8), n++)); };
  filler(3);
  messages.push(say('c0', 'bot:botops', 'Needs your OK: ' + actions.act0.description, n++, {assistant: true, action: 'act0'}));
  filler(3);
  messages.push(say('c1', 'bot:botops', 'Needs your OK: ' + actions.act1.summary, n++, {assistant: true, action: 'act1'}));
  filler(2);
  messages.push(say('cL', 'bot:botops', 'Needs your OK: ' + actions.actL.summary, n++, {assistant: true, action: 'actL'}));
  filler(2);
  messages.push(say('c2', 'bot:botops', 'Needs your OK: ' + actions.act2.summary, n++, {assistant: true, action: 'act2'}));
  filler(4);
  messages.push(say('c3', 'bot:botops', 'Needs your OK: ' + actions.act3.summary, n++, {assistant: true, action: 'act3'}));
  messages.push(say('c4', 'bot:botops', 'Needs your OK: ' + actions.act4.summary, n++, {assistant: true, action: 'act4'}));
  filler(8);
  return {actions, messages, n, gets: [], decided: []};
}

async function open(browser, state) {
  const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
      ? [{id: 'c-botops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']}] : []});
    if (p === '/api/v2/conversations/c-botops/snapshot') return json({messages: state.messages, execution: null, next_before: null});
    const one = p.match(/^\/api\/v2\/assistant\/actions\/([^/]+)$/);
    if (one && method === 'GET') {
      state.gets.push(one[1]);
      const then = JSON.stringify(state.actions[one[1]]);  // as it was when asked
      await new Promise(done => setTimeout(done, state.slowGet || 120));    // a real round trip: long enough to be seen
      return route.fulfill({contentType: 'application/json', body: `{"action":${then}}`});
    }
    const act = p.match(/^\/api\/v2\/assistant\/actions\/([^/]+)\/(confirm|cancel)$/);
    if (act && state.refuse) return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({detail: 'It could not be reached'})});
    if (act) {
      if (state.slowPost) await new Promise(done => setTimeout(done, state.slowPost));
      const action = state.actions[act[1]];
      state.decided.push([act[1], act[2]]);
      action.status = act[2] === 'confirm' ? 'done' : 'cancelled';
      // The server says how it went in the chat (assistant.py action_confirm): one more message.
      state.messages.push(say('r' + act[1], 'bot:botops', 'Done: ' + action.description, state.n++, {assistant: true, action_result: act[1]}));
      return json({action});
    }
    return json(unrouted(p));
  });
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const state = fixture();
    const {page, errors} = await open(browser, state);
    await page.goto('https://tico-ui.test/#/bot/botops/chat');
    await page.waitForFunction(() => V2C?.rendered && document.querySelectorAll('#conv-thread .asst-card').length === 6);

    // Plain words first; the raw request only behind Details, closed.
    const repo = page.locator('.asst-card[data-action="act1"]');
    assert.equal(await repo.locator('[data-what]').innerText(),
      'Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.', 'repo: the sentence is the title');
    const details = repo.locator('details.asst-raw');
    assert.equal(await details.count(), 1, 'repo: one Details');
    assert.equal(await details.evaluate(d => d.open), false, 'repo: Details starts closed');
    const seen = await repo.evaluate(el => el.innerText);
    for (const raw of ['PUT', '/api/v2', 'chosen', 'full_name', '{', 'mode:']) assert.ok(!seen.includes(raw), `repo: "${raw}" is not shown before Details opens`);
    assert.ok(!seen.includes('“'), 'repo: no quote that repeats the title');
    await details.locator('summary').click();
    const opened = await repo.evaluate(el => el.innerText);
    assert.ok(opened.includes('PUT /api/v2/bots/security-engineer-2/repositories') && opened.includes('ticoteam/tico'), 'repo: Details has the method, path and fields');
    await details.locator('summary').click();

    const old = page.locator('.asst-card[data-action="act2"]');
    const title = await old.locator('[data-what]').innerText();
    assert.ok(!/\/api\/|^(POST|PUT|PATCH|DELETE)\b/.test(title), 'old card: no method or path as its title, got ' + title);
    // What it does is on the card before the click: the bot's own summary, not a generic line over a hidden action.
    assert.equal(title, 'Ops asked BotOps for this for you: Launch Ops', 'old card: its summary is the title');
    assert.equal(await old.locator('.asst-who').innerText(), 'BotOps asks', 'old card: who asks, above the title');
    // Two buttons and no helper line: what Confirm does is its tooltip.
    assert.deepEqual(await old.locator('button').allInnerTexts(), ['Confirm', 'Cancel']);
    assert.ok(!(await old.evaluate(el => el.innerText)).includes('Runs as you'), 'old card: no helper line');
    assert.ok(!(await old.evaluate(el => el.innerText)).includes('/api/v2'), 'old card: path only behind Details');

    // The card Chris saw: no mode word, method, path or JSON until Details opens, and the old line only there.
    const legacy = page.locator('.asst-card[data-action="act0"]');
    assert.equal(await legacy.locator('[data-what]').innerText(), 'Security Engineer: read-only on tico, besides its own repository.');
    const legacySeen = await legacy.evaluate(el => el.innerText);
    for (const raw of ['chosen', 'PUT', '/api/', '{', 'full_name', 'mode:', 'Set Security']) assert.ok(!legacySeen.includes(raw), `old repo card: "${raw}" is not shown before Details opens`);
    await legacy.locator('details.asst-raw summary').click();
    assert.ok((await legacy.evaluate(el => el.innerText)).includes("First written as: “Set Security Engineer's repository access to chosen."), 'old repo card: its old line is under Details');
    await legacy.locator('details.asst-raw summary').click();

    const send = page.locator('.asst-card[data-action="act4"]');
    assert.equal(await send.locator('details.asst-raw').evaluate(d => d.open), false, 'send: Details starts closed');
    assert.equal(await send.locator('.asst-said').innerText(), 'Hi Ben, the launch moves to Friday.', 'send: the words sent are on the card');
    assert.ok(!(await send.evaluate(el => el.innerText)).includes('human:ben'), 'send: the raw fields stay under Details');

    // Open Details on the third card and "Show more" on the long send above the second, scroll so the second sits
    // mid-thread, then Confirm the second from the keyboard.
    await page.locator('.asst-card[data-action="act3"] details.asst-raw summary').click();
    await page.locator('.asst-card[data-action="actL"] .asst-said details.asst-long summary').click();
    await page.evaluate(() => document.querySelectorAll('#conv-thread .asst-card').forEach(c => { c.mark_ = c.dataset.action; }));
    await page.evaluate(() => {
      const t = document.querySelector('#conv-thread'), c = document.querySelector('.asst-card[data-action="act2"]');
      t.scrollTop += c.getBoundingClientRect().top - t.getBoundingClientRect().top - 200;
      V2C.stick.release();
    });
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const before = await page.evaluate(() => ({top: document.querySelector('#conv-thread').scrollTop,
      side: document.querySelector('#side')?.scrollTop || 0,
      card: document.querySelector('.asst-card[data-action="act2"]').getBoundingClientRect().top,
      long: document.querySelector('.asst-card[data-action="actL"]').getBoundingClientRect().top}));
    assert.ok(before.top > 0 && before.card > 100 && before.card < 400, 'set up: the card is mid-thread, away from the top');
    assert.equal(await page.evaluate(() => V2C.stick.atBottom), false, 'set up: not at the bottom');

    // Watch every frame from the click until the result message is in: what a person would see.
    await page.evaluate(() => {
      window.frames_ = [];
      const tick = () => {
        const t = document.querySelector('#conv-thread'), c = document.querySelector('.asst-card[data-action="act2"]');
        window.frames_.push({top: t.scrollTop, side: document.querySelector('#side')?.scrollTop || 0, card: c ? c.getBoundingClientRect().top : null,
          loading: [...t.querySelectorAll('[data-action-host]')].filter(h => !h.querySelector('.asst-card')).length,
          open: !!document.querySelector('.asst-card[data-action="act3"] details.asst-raw')?.open,
          more: !!document.querySelector('.asst-card[data-action="actL"] .asst-said details.asst-long')?.open,
          long: document.querySelector('.asst-card[data-action="actL"]')?.getBoundingClientRect().top ?? null});
        if (window.frames_.length < 120) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    state.gets.length = 0;
    await page.evaluate(() => document.querySelector('.asst-card[data-action="act2"] [data-confirm]').focus({preventScroll: true}));
    await page.keyboard.press('Enter');
    await page.locator('.asst-card[data-action="act2"][data-status="done"]').waitFor();
    await page.locator('[data-message="ract2"]').waitFor({state: 'attached'});
    await page.waitForFunction(() => window.frames_.length >= 120);
    const frames = await page.evaluate(() => window.frames_);
    assert.deepEqual(state.decided, [['act2', 'confirm']], 'the click confirmed only that card');
    assert.equal(frames.filter(f => f.loading).length, 0, 'no card fell back to "Loading the card…" after the click');
    assert.deepEqual([...new Set(frames.map(f => f.top))], [before.top], 'the thread kept its scroll on every frame');
    assert.deepEqual([...new Set(frames.map(f => f.side))], [before.side], 'the sidebar kept its scroll');
    assert.ok(frames.every(f => f.card !== null && Math.abs(f.card - before.card) <= 1), 'the card stayed where it was on screen');
    assert.ok(frames.every(f => f.open), 'an open Details on another card stays open');
    assert.ok(frames.every(f => f.more), 'a "Show more" opened on another card stays open');
    assert.ok(frames.every(f => f.long !== null && Math.abs(f.long - before.long) <= 1), 'the card above stayed where it was on screen');
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#conv-thread .asst-card')].filter(c => c.dataset.action !== 'act2')
      .map(c => c.mark_ || 'redrawn:' + c.dataset.action)), ['act0', 'act1', 'actL', 'act3', 'act4'], 'every other card is the same element');
    assert.equal(await page.evaluate(() => document.activeElement?.closest('.asst-card')?.dataset.action), 'act2',
      'focus stays on the card whose Confirm was pressed');
    assert.equal(await page.locator('.asst-card[data-action="act1"] [data-confirm]').count(), 1, 'the other cards are still pending');
    assert.equal(await page.locator('.asst-card[data-action="act3"] [data-confirm]').count(), 1);
    // A message arriving while a person reads a card: the thread rebuilds and their focus and "Show more" stay.
    await page.evaluate(() => document.querySelector('.asst-card[data-action="actL"] .asst-said details.asst-long summary').focus({preventScroll: true}));
    await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, say('read', 'bot:botops', 'While you read.', state.n++));
    assert.deepEqual(await page.evaluate(() => {
      const long = document.querySelector('.asst-card[data-action="actL"]');
      return [!!document.querySelector('[data-message="read"]'), long.mark_, long.querySelector('.asst-said details.asst-long').open,
        document.activeElement === long.querySelector('.asst-said details.asst-long summary')];
    }), [true, 'actL', true, true], 'a rebuild keeps the card being read, open and focused');
    // A refresh asked just before a click and answered while the Confirm is on its way is dropped: a rebuild after it
    // still draws the card as running, with no live button (a second click would be refused).
    Object.assign(state, {slowGet: 300, slowPost: 900});
    await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, say('late', 'bot:botops', 'One more thing.', state.n++));
    await page.locator('.asst-card[data-action="act1"] [data-confirm]').click();
    await page.waitForTimeout(450);
    await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, say('later', 'bot:botops', 'And another.', state.n++));
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('.asst-card[data-action="act1"] button')].some(b => !b.disabled)), false,
      'no live button while the Confirm runs');
    await page.locator('.asst-card[data-action="act1"][data-status="done"]').waitFor();
    Object.assign(state, {slowGet: 0, slowPost: 0});

    // A Confirm that does not go through leaves the card waiting, its buttons usable again.
    state.refuse = true;
    await page.locator('.asst-card[data-action="act3"] [data-confirm]').click();
    await page.locator('.toast.err').first().waitFor();
    await page.waitForFunction(() => document.querySelector('.asst-card[data-action="act3"] [data-confirm]')?.disabled === false);
    assert.equal(await page.locator('.asst-card[data-action="act3"]').getAttribute('data-status'), 'pending', 'a failed Confirm leaves it pending');
    assert.deepEqual(errors, [], 'no page errors');
    console.log('action cards: plain titles, Details, no jump on Confirm: ok');
    await page.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
