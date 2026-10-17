// BotOps' Confirm cards in its chat (ui/assistant.js), fixtures only.
//  - plain words: the card's title is the server's sentence, never the method and path; a card stored with no readable
//    description gets a plain sentence too. The method, path and fields sit behind a closed Details.
//  - no jump on Confirm: in a chat scrolled partway down with several cards, a click changes only that card. The thread
//    and the sidebar keep their scroll, no card falls back to "Loading the card…", and an open Details stays open.
//    A Confirm that fails leaves the card waiting with its buttons usable.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');

const at = n => new Date(Date.UTC(2026, 9, 17, 9, n)).toISOString();
const say = (id, from, body, n, refs = {}) => ({id, from_actor: from, body, refs, created: at(n), kind: 'say'});

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
    act3: card('act3', {method: 'POST', path: '/api/v2/bots/ops/archive', summary: 'Archive the Ops bot', description: 'Archive bot Ops',
      body: {expected_revision: 3}, diff: [{field: 'expected_revision', new: 3}]}),
  };
  const messages = [];
  let n = 0;
  const filler = k => { for (let i = 0; i < k; i++) messages.push(say('h' + n, n % 2 ? 'bot:botops' : 'human:ana', `Line ${n}. ` + 'Some words to fill the line. '.repeat(8), n++)); };
  filler(6);
  messages.push(say('c1', 'bot:botops', 'Needs your OK: ' + actions.act1.summary, n++, {assistant: true, action: 'act1'}));
  filler(4);
  messages.push(say('c2', 'bot:botops', 'Needs your OK: ' + actions.act2.summary, n++, {assistant: true, action: 'act2'}));
  filler(4);
  messages.push(say('c3', 'bot:botops', 'Needs your OK: ' + actions.act3.summary, n++, {assistant: true, action: 'act3'}));
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
      await new Promise(done => setTimeout(done, 120));    // a real round trip: long enough to be seen
      return json({action: state.actions[one[1]]});
    }
    const act = p.match(/^\/api\/v2\/assistant\/actions\/([^/]+)\/(confirm|cancel)$/);
    if (act && state.refuse) return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({detail: 'It could not be reached'})});
    if (act) {
      const action = state.actions[act[1]];
      state.decided.push([act[1], act[2]]);
      action.status = act[2] === 'confirm' ? 'done' : 'cancelled';
      // The server says how it went in the chat (assistant.py action_confirm): one more message.
      state.messages.push(say('r' + act[1], 'bot:botops', 'Done: ' + action.description, state.n++, {assistant: true, action_result: act[1]}));
      return json({action});
    }
    return json({});
  });
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const state = fixture();
    const {page, errors} = await open(browser, state);
    await page.goto('https://tico-ui.test/#/bot/botops/chat');
    await page.waitForFunction(() => V2C?.rendered && document.querySelectorAll('#conv-thread .asst-card').length === 3);

    // Plain words first; the raw request only behind Details, closed.
    const repo = page.locator('.asst-card[data-action="act1"]');
    assert.equal(await repo.locator('[data-what]').innerText(),
      'Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.', 'repo: the sentence is the title');
    const details = repo.locator('details.asst-raw');
    assert.equal(await details.count(), 1, 'repo: one Details');
    assert.equal(await details.evaluate(d => d.open), false, 'repo: Details starts closed');
    const seen = await repo.evaluate(el => el.innerText);
    for (const raw of ['PUT', '/api/v2', 'chosen', 'full_name', '{', 'mode:']) assert.ok(!seen.includes(raw), `repo: "${raw}" is not shown before Details opens`);
    assert.equal((await repo.locator('.asst-state').first().innerText()).includes('“'), false, 'repo: no quote that repeats the title');
    await details.locator('summary').click();
    const opened = await repo.evaluate(el => el.innerText);
    assert.ok(opened.includes('PUT /api/v2/bots/security-engineer-2/repositories') && opened.includes('ticoteam/tico'), 'repo: Details has the method, path and fields');
    await details.locator('summary').click();

    const old = page.locator('.asst-card[data-action="act2"]');
    const title = await old.locator('[data-what]').innerText();
    assert.ok(!/\/api\/|^(POST|PUT|PATCH|DELETE)\b/.test(title), 'old card: no method or path as its title, got ' + title);
    assert.match(title, /^BotOps /, 'old card: a plain sentence naming who asks');
    assert.ok(!(await old.evaluate(el => el.innerText)).includes('/api/v2'), 'old card: path only behind Details');

    // Open Details on the third card, scroll so the second sits mid-thread, then Confirm the second.
    await page.locator('.asst-card[data-action="act3"] details.asst-raw summary').click();
    await page.evaluate(() => {
      const t = document.querySelector('#conv-thread'), c = document.querySelector('.asst-card[data-action="act2"]');
      t.scrollTop += c.getBoundingClientRect().top - t.getBoundingClientRect().top - 200;
      V2C.stick.release();
    });
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const before = await page.evaluate(() => ({top: document.querySelector('#conv-thread').scrollTop,
      side: document.querySelector('#side')?.scrollTop || 0,
      card: document.querySelector('.asst-card[data-action="act2"]').getBoundingClientRect().top}));
    assert.ok(before.top > 0 && before.card > 100 && before.card < 400, 'set up: the card is mid-thread, away from the top');
    assert.equal(await page.evaluate(() => V2C.stick.atBottom), false, 'set up: not at the bottom');

    // Watch every frame from the click until the result message is in: what a person would see.
    await page.evaluate(() => {
      window.frames_ = [];
      const tick = () => {
        const t = document.querySelector('#conv-thread'), c = document.querySelector('.asst-card[data-action="act2"]');
        window.frames_.push({top: t.scrollTop, side: document.querySelector('#side')?.scrollTop || 0, card: c ? c.getBoundingClientRect().top : null,
          loading: [...t.querySelectorAll('[data-action-host]')].filter(h => !h.querySelector('.asst-card')).length,
          open: !!document.querySelector('.asst-card[data-action="act3"] details.asst-raw')?.open});
        if (window.frames_.length < 120) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    state.gets.length = 0;
    await page.locator('.asst-card[data-action="act2"] [data-confirm]').click();
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
    assert.equal(await page.locator('.asst-card[data-action="act1"] [data-confirm]').count(), 1, 'the other cards are still pending');
    assert.equal(await page.locator('.asst-card[data-action="act3"] [data-confirm]').count(), 1);
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
