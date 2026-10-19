// Needs you: opening an approval, a task for the person, a question, a done task or a loose question asks for no issue.
// Those cards are details.req like the issues inbox's but carry no issue number, and each asked for /api/issues/NaN
// (bot-needs, needs-you-approvals, needs-you-done). An inbox card whose number is not a positive whole number asks
// for nothing either; a valid inbox card still fetches its full request by number.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const task = (id, title, over = {}) => ({id, kind: 'task', title, body: '', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], created: at(60), updated: at(60), conversation_id: 'c-' + id, ask: null, open_asks: 0, ...over});
const approval = {id: 'ap-1', kind: 'approval', what: 'send', title: 'Approve this send', requester: 'bot:support',
  requested_by: 'bot:support', created: at(30), conversation_id: 'c-ap', payload: {to: ['ava@creator.example'], subject: 'Contract'}};
const cards = [
  task('t-pick', 'Pick the launch date'),
  task('t-asked', 'Support access fixes', {kind: 'question', owner: 'bot:writer', requester: 'bot:lead', status: 'done',
    ask: {id: 'q-1', body: 'Should full access refuse credential changes?', from_actor: 'bot:writer', to_actor: 'human:ana', created: at(20)}}),
  task('t-done', 'Approve the launch post', {status: 'done'}),
];
const loose = {kind: 'ask', id: 'm-loose', message_id: 'm-loose', title: 'Is Friday fine?', first_line: 'Is Friday fine?', body: 'Is Friday fine?',
  requester: 'bot:lead', origin_actor: 'bot:lead', created: at(10), ask: {id: 'm-loose', from_actor: 'bot:lead', to_actor: 'human:ana', body: 'Is Friday fine?'}};
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const issueGets = [];
    page.on('request', r => { const m = new URL(r.url()).pathname.match(/^\/api\/issues\/([^/]+)$/); if (m) issueGets.push(m[1]); });
    // Every card opened, in the order its toggle event ran.
    await page.evaluate(() => {
      window.TOGGLED = [];
      document.addEventListener('toggle', ev => { const d = ev.target.dataset || {};
        if (ev.target.matches?.('details.req') && ev.target.open) TOGGLED.push(d.v2Id || d.ask || 'n:' + d.n); }, true);
    });
    // The approval as the person opens it, in Needs you.
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [approval]})}));
    await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.locator('[data-group="approvals"] details.req[data-v2-id="ap-1"] > summary').click();
    // Task, question, done and loose-question cards, drawn as Needs you draws them (as needs-you-done.cjs does).
    await page.evaluate(({cards, loose}) => {
      const host = document.createElement('div');
      host.id = 'needs-you-cards';
      host.innerHTML = cards.map(it => needsV2Item(it, false)).join('') + looseAskRow(loose);
      document.querySelector('#main').prepend(host);
    }, {cards, loose});
    const drawn = page.locator('#needs-you-cards > details.req');
    assert.equal(await drawn.count(), cards.length + 1, 'every card is drawn');
    for (const card of await drawn.all()) await card.locator('> summary').click();
    // Inbox cards whose number is not a positive whole number, then a valid one. Toggle events run in order, so once
    // the valid card's fetch is seen every earlier card's toggle has run.
    const BAD = ['abc', '1.5', '0', '-3'];
    const inbox = page.waitForRequest(r => new URL(r.url()).pathname === '/api/issues/7');
    await page.evaluate(numbers => {
      const host = document.createElement('div');
      host.innerHTML = numbers.map(number => needsYouItem({number, title: 'Pick a plan', owner: 'human:ana', body: 'Monthly or annual?'}, false)).join('');
      document.querySelector('#main').append(host);
      host.querySelectorAll('details').forEach(d => { d.open = true; });
    }, [...BAD, 7]);
    await inbox;
    assert.deepEqual(await page.evaluate(() => TOGGLED),
      ['ap-1', ...cards.map(c => c.id), loose.id, ...BAD.map(n => 'n:' + n), 'n:7'], 'every card opened, before the valid inbox card');
    assert.deepEqual(issueGets, ['7'], 'only the valid inbox card asks for its issue, by number');
    assert.deepEqual(errors, []);
    console.log('Needs you: opening approval, task, question, done and loose-question cards asks for no issue, nor does a malformed inbox number; a valid inbox card still does');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
