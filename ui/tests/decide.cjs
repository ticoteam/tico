// The Needs you card on a phone (390px), a full-screen takeover: a row that takes a yes or no, an option or an
// approval opens its card; any other row opens the whole task. A task card leads with the plain ask, large, and the
// title second, smaller; the bot's status note and the rest of the body fold under Details, as text. A yes/no ask gets
// Yes and No, which answer the bot with a comment. An old #/decide link lands on Needs you with the item's card open,
// or its task when it has none. Back closes the card.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = minutes => new Date(NOW - minutes * 60000).toISOString();
const NOTE = 'Engineering answer db14c907 resolves initial inquiry 8719e1a6: public example is independent static Larkspur. '
  + 'Docs task Doing; no sample edit/deploy or duplicate human question. <img src=x onerror="window.pwned=1">';
const PLAIN = "Approve the video tool trial on Thursday? If yes, I'll send the invite.";
const wait = (id, title, over = {}) => ({id, kind: 'waiting', title, body: '', owner_id: 'bot:writer', requester_id: 'bot:botops',
  status: 'waiting', waiting_on: 'human:ana', note: NOTE, version: 2, created_at: at(400), updated_at: at(360), ask: null, open_asks: 0,
  person_ask: '', ask_missing: false, lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, ...over});
const items = [
  // an open question with "or" is not a yes or a no: it opens the task
  wait('t-ask', 'Coordinate the external agent scenario in the example', {created_at: at(500),
    ask: {id: 'q-1', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Should the public example show the agent demo, or stay static?', created_at: at(300)}}),
  // a stale ask (unanswered past three days): the card carries one-click Clarify
  wait('t-stale', 'Sync the external agent scenario', {created_at: at(5000), updated_at: at(5000),
    ask: {id: 'q-stale', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Should the public example show the agent demo?', created_at: at(5000), stale: true}}),
  wait('t-plain', 'Run the video tool trial', {created_at: at(490), note: PLAIN, person_ask: PLAIN,
    body: 'Run the video tool trial\n\nThe test plan is in the shared folder.'}),
  // set waiting before the rule, with no plain ask: its task, not a card
  wait('t-blank', 'Check the newsletter list', {created_at: at(480), ask_missing: true}),
  {id: 't-person', kind: 'task', title: 'Read the Q4 board notes', body: 'Read the Q4 board notes\n\nBefore Thursday, please.', owner_id: 'human:ana',
    requester_id: 'human:sam', status: 'open', version: 1, created_at: at(460), updated_at: at(460), ask: null, open_asks: 0,
    lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}},
];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', extraTasks: items});
    const sent = [], clarified = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2));
      if (route.request().method() === 'POST') { sent.push({id, ...route.request().postDataJSON()}); return route.fulfill({contentType: 'application/json', body: '{}'}); }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({comments: []})});
    });
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.route('**/api/v2/needs-you/clarify', route => {
      clarified.push(route.request().postDataJSON());
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    const card = page.locator('#need-card .dc-card');
    const read = async id => {
      await page.locator(`#need-card .dc-card[data-key="task:${id}"]`).waitFor();
      await page.waitForFunction(id => DEC?.data.get(id)?.comments, id);
      return card.evaluate(el => {
        const body = el.querySelector('.dc-body'), [first, second] = body.children, details = body.querySelector('.dc-details');
        return {sub: el.querySelector('.dc-who-sub').textContent, lead: first.textContent.trim(), leadClass: first.className,
          title: second?.className === 'dc-title' ? second.textContent : null,
          sizes: [first, second].map(x => parseFloat(getComputedStyle(x).fontSize)),
          details: details ? {open: details.open, text: details.textContent, last: body.lastElementChild === details} : null,
          buttons: [...document.querySelectorAll('#need-card .dc-actions button')].map(b => b.dataset.dc),
          clarify: body.querySelector('[data-dc="clarify"]')?.textContent.trim() || null};
      });
    };
    // DECIDE_SHOTS=<dir> saves each card for the owner.
    const shot = name => process.env.DECIDE_SHOTS && page.screenshot({path: require('node:path').join(process.env.DECIDE_SHOTS, name + '.png')});
    // Act once the last action's lock (350 ms) has passed: a tap inside it does nothing, by design.
    const tap = async what => {
      await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
      await page.locator(`#need-card .dc-actions [data-dc="${what}"]`).click();
    };

    // Newest first: being old or pressing never moves a card up (only the server's tier does, below).
    // The card says whose request it is when that is not the name it shows.
    assert.deepEqual(await page.evaluate(() => ({
      order: decideCards([
        {id: 'bot-made', kind: 'waiting', owner_id: 'bot:writer', requester_id: 'bot:writer', waiting_on: 'human:ana', created_at: '2026-01-01T00:00:00Z'},
        {id: 'for-sam', kind: 'task', owner_id: 'human:ana', requester_id: 'bot:writer', from_person: 'human:sam', created_at: '2026-01-03T00:00:00Z'},
        {id: 'overdue', kind: 'waiting', owner_id: 'bot:writer', requester_id: 'bot:writer', waiting_on: 'human:ana', pressing: true, created_at: '2026-01-02T00:00:00Z'},
      ]).map(c => c.key),
      cue: [decideFromPerson({from_person: 'human:sam'}, 'bot:writer'), decideFromPerson({from_person: 'human:ana'}, 'bot:writer'),
        decideFromPerson({from_person: 'human:sam'}, 'human:sam'), decideFromPerson({}, 'bot:writer')],
    })), {order: ['task:for-sam', 'task:overdue', 'task:bot-made'], cue: ['From Sam', 'You asked', '', '']});
    // A 4-day-old question and a 1-hour-old one that holds up a roadmap item at Building: the Building one is first.
    // Something due, or going ahead on its own, within two working days goes ahead of both (backend/views.py decide_tier).
    assert.deepEqual(await page.evaluate(() => decideCards([
      {id: 'old', kind: 'waiting', owner_id: 'bot:writer', waiting_on: 'human:ana', created_at: new Date(Date.now() - 96 * 3600e3).toISOString(), tier: 2},
      {id: 'building', kind: 'waiting', owner_id: 'bot:writer', waiting_on: 'human:ana', created_at: new Date(Date.now() - 3600e3).toISOString(), tier: 1},
      {id: 'due', kind: 'waiting', owner_id: 'bot:writer', waiting_on: 'human:ana', created_at: new Date(Date.now() - 200 * 3600e3).toISOString(), tier: 0},
    ]).map(c => c.key)), ['task:due', 'task:building', 'task:old']);
    // A yes or no, an option, an approval, a stale check, a question outside a task, or a recommendation: a card.
    // Anything else opens whole.
    assert.deepEqual(await page.evaluate(() => [
      {id: 'a', kind: 'approval'}, {id: 'b', kind: 'ask'}, {id: 'c', kind: 'stale'},
      {id: 'd', kind: 'task', owner_id: 'human:ana', recommend: {ask: 'q', do: 'Gusto'}}, {id: 'e', kind: 'task', owner_id: 'human:ana'},
    ].map(decideCardKey)), ['approval:a', 'ask:b', 'stale:c', 'task:d', '']);

    // An old link to one item: Needs you, with that item's card open over it, full screen.
    await page.evaluate(() => { location.hash = '#/decide?item=t-plain'; });
    let c = await read('t-plain');
    assert.match(await page.evaluate(() => location.hash), /^#\/tasks\?(.*&)?view=foryou/);
    assert.deepEqual(await page.locator('#need-card').evaluate(d => { const r = d.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }),
      [0, 0, 390, 844], 'a takeover on a phone');
    // Only rows a card answers carry one; the rest open their task.
    const rows = await page.locator('#task-body .tl-row[data-task-key^="t"]').evaluateAll(els => Object.fromEntries(els
      .filter(el => /^t-(ask|stale|plain|blank|person)$/.test(el.dataset.taskKey.slice(1))).map(el => [el.dataset.taskKey.slice(1), el.dataset.needCard || ''])));
    assert.deepEqual(rows, {'t-ask': '', 't-stale': 'task:t-stale', 't-plain': 'task:t-plain', 't-blank': '', 't-person': ''});
    assert.equal(await page.locator('#task-body details').count(), 0, 'nothing in the list unfolds');

    // The bot's plain yes/no ask leads, with Yes and No; the rest of the body is a detail. Yes answers the bot.
    assert.match(c.sub, /^Waiting on you · /);
    assert.equal(c.lead, PLAIN);
    assert.equal(c.leadClass, 'dc-msg md');
    assert.equal(c.title, 'Run the video tool trial');
    assert.ok(c.sizes[1] < c.sizes[0], `the title is smaller than the ask: ${c.sizes}`);
    assert.equal(c.details.open, false);
    assert.equal(c.details.last, true);
    assert.ok(c.details.text.includes('The test plan is in the shared folder.') && c.details.text.includes('db14c907') === false);
    assert.deepEqual(c.buttons, ['no', 'yes']);
    assert.equal((await page.locator('#need-card .dc-count').textContent()).replace(/\s+/g, ' ').trim().startsWith('1 of 2'), true);
    await page.locator('#need-card .dc-details summary').click();
    await shot('yes-no');
    await tap('yes');
    // The card moves on to the other one; the answered row leaves the list at once.
    c = await read('t-stale');
    assert.equal(await page.locator('#task-body [data-need-card="task:t-plain"]').count(), 0);
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(sent, [{id: 't-plain', text: 'Yes.'}]);

    // A stale ask carries one-click Clarify: one tap asks the bot whether it still matters, in clearer words.
    assert.equal(c.lead, 'Should the public example show the agent demo?');
    assert.equal(c.clarify, 'Ask Writer to clarify');
    assert.deepEqual(c.buttons, ['no', 'yes']);
    await page.locator('#need-card .dc-body [data-dc="clarify"]').click();
    assert.equal(await page.locator('#need-card .dc-toast span').textContent(), 'Asked Writer to clarify');
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(clarified, [{ask: 'q-stale'}]);
    // the note is only ever text
    assert.equal(await page.locator('#need-card .dc-body img').count(), 0);
    assert.equal(await page.evaluate(() => window.pwned), undefined);

    // Back closes the card and stays on Needs you.
    await page.goBack();
    await page.waitForFunction(() => !document.querySelector('#need-card')?.open && !DEC);
    assert.match(await page.evaluate(() => location.hash), /^#\/tasks\?(.*&)?view=foryou/);

    // A row with no card opens its task.
    await page.locator('#task-body [data-task-key="tt-blank"] .tl-title').click();
    await page.locator('#task-peek[open]').waitFor();
    assert.equal(await page.evaluate(() => !!document.querySelector('#need-card')?.open), false);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#task-peek')?.open);

    // An old link to an item a card does not answer opens its task.
    await page.evaluate(() => { location.hash = '#/decide?item=t-person'; });
    await page.waitForFunction(() => location.hash === '#/task/t-person');
    await page.evaluate(() => { location.hash = '#/tasks?view=foryou'; });

    // Go through, in the phone's header, says how many cards wait and opens the first.
    const go = page.locator('#tasks-pane [data-go-through]:visible');
    assert.equal(await go.count(), 0, 'nothing waits on a card: no Go through');
    await page.evaluate(() => { DEC_HANDLED.delete('task:t-stale'); needsListsRedraw(); });
    assert.equal((await go.textContent()).trim(), 'Go through · 1');
    assert.equal(await go.evaluate(el => el.classList.contains('tl-decide-head')), true);
    await go.click();
    await read('t-stale');
    // Closed and opened again at once: the close's Back lands after the new card opened, and leaves it open with a
    // Back of its own.
    await page.evaluate(() => { decideStop(); decideOpen('task:t-stale'); });
    await page.waitForFunction(() => history.state?.needCard);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => !!document.querySelector('#need-card')?.open && !!DEC), true, 'the card opened again stays open');
    // A bot's face and name open the bot; the face is not a second tab stop. Tapping the name sends nothing from the
    // card. Opening a bot page saves the recently viewed list (preference org.history) after a short debounce: that
    // write is not a card action, and waiting for it gives any card action the click might have sent time to show up.
    assert.deepEqual(await page.locator('#need-card .dc-who a').evaluateAll(as => as.map(a => ({cls: a.className, href: a.getAttribute('href'), tab: a.tabIndex}))), [
      {cls: 'dc-who-face', href: '#/bot/writer', tab: -1}, {cls: 'dc-who-link', href: '#/bot/writer', tab: 0}]);
    assert.deepEqual(errors, []);
    const writes = [];
    page.on('request', r => { if (r.method() !== 'GET' && !new URL(r.url()).pathname.startsWith('/api/v2/preferences/')) writes.push(r.url()); });
    const visited = page.waitForRequest(r => r.method() !== 'GET' && new URL(r.url()).pathname === '/api/v2/preferences/org.history');
    await page.locator('#need-card .dc-who-link').click();
    await page.waitForFunction(() => location.hash === '#/bot/writer');
    await visited;
    assert.deepEqual(writes, [], 'no card action was sent');
    assert.equal(await page.evaluate(() => !!document.querySelector('#need-card')?.open), false, 'the card closes with its page');
    console.log('Needs you card (phone): a takeover; only rows a card answers open one, the rest open the task; a yes/no ask'
      + ' answers the bot and moves on; Back closes it; old #/decide links open the card or the task');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
