// The one question card (ui/app/question-card.js), in Decide and in the task page's Needs you box: the real asker,
// "asked N days ago" and what it holds up; the question and its why line; four picks as checkboxes, the recommended
// ones ticked and labelled; "Yes to all" and "Send ticked (n)", each with its "what happens" line; the fallback small
// at the foot; ids, paths and error text only under Details. Ticking 1, 2 and 4 sends those three, and the bot hears
// pick 3 as "not this one" (the server's answer text); Yes to all sends all four. A check from Tico says "Tico".
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = hours => new Date(NOW - hours * 3600e3).toISOString();
const BODY = 'Website sales chat: go with these picks?\n\nRun 8719e1a6 failed: ValueError: bad date in reports/chat-plan.md';
const PICKS = [
  {label: 'Calls', then: 'Booked on your calendar; you take calls for now', recommended: true},
  {label: 'Visitor notes', then: 'Each becomes a private task for you', recommended: true},
  {label: 'Pages', then: 'Home, pricing and docs get the chat'},
  {label: 'Spend cap', then: 'Chat stops for the day at $10', recommended: true},
];
const question = (id, over = {}) => ({id, kind: 'question', title: 'Website sales chat picks', body: BODY, owner: 'bot:writer',
  requester: 'bot:writer', status: 'waiting', waiting_on: 'human:ana', version: 2, created: at(80), updated: at(72), open_asks: 1,
  labels: [], tags: [], links: [], lane: 'company', rank: null, parts: {total: 0, done: 0}, why: "Engineering can't start until these are set.",
  holds_up: {id: 'r-chat', title: 'Website sales chat build', kind: 'roadmap', step: 'Building'}, tier: 1,
  ask: {id: 'q-' + id, from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Website sales chat: go with these picks?', created: at(72),
    goes_ahead_at: '2026-10-23T16:00:00.000000Z',
    refs: {task: id, questions: [{id: 'decision', header: '', question: 'Website sales chat: go with these picks?', multi: true, other: true,
      why: "Engineering can't start until these are set.", options: PICKS}], if_no_answer: {do: 'It stays a draft', kind: 'reversible'}}},
  ...over});
const stuck = {id: 't-stuck', kind: 'question', title: 'Is this still moving?', body: '', owner: 'bot:writer', requester: 'bot:writer',
  status: 'waiting', version: 1, created: at(2), updated: at(2), open_asks: 1, from_person: 'human:ana', tier: 2,
  ask: {id: 'q-stuck', from_actor: 'keeper', to_actor: 'human:ana', body: 'No progress for 60 min after a status check.', created: at(2),
    refs: {questions: [{id: 'stuck', header: 'Stuck', question: 'Keep going or stop?', multi: false, other: true,
      options: [{label: 'Keep going', then: 'The bot is woken again'}, {label: 'Stop', then: 'The task is closed'}]}]}}};

const card = () => ({
  who: document.querySelector('.decide .dc-who-name')?.textContent.trim(),
  sub: document.querySelector('.decide .dc-who-sub')?.textContent.trim(),
  q: document.querySelector('.decide .dc-q')?.textContent.trim(),
  why: document.querySelector('.decide .qc-why')?.textContent.trim(),
  picks: [...document.querySelectorAll('.decide .qc-pick')].map(p => ({text: p.querySelector('.qc-l').textContent.trim(),
    then: p.querySelector('.qc-then').textContent.trim(), ticked: p.querySelector('input').checked})),
  buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => ({label: b.querySelector('.qc-bl')?.textContent.replace(/[A-Z]$/, '').trim(),
    then: b.querySelector('.qc-then')?.textContent.trim() || ''})),
});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    let items = [question('t-four'), stuck];
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 900}, hash: '#/tasks', extraTasks: [...items, question('t-page')].map(x => ({...x}))});
    const answers = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.route('**/api/v2/tasks/*/answers', route => {
      answers.push({task: decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2)), ...route.request().postDataJSON()});
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    await page.evaluate(async () => { await v2Refresh(); location.hash = DECIDE; });
    await page.locator('.decide .dc-card[data-key="task:t-four"]').waitFor();

    // 10, 14, 15: all four picks, the recommended ones ticked and labelled; every pick and button says what happens;
    // the header names the bot, the age and what it holds up.
    const shown = await page.evaluate(card);
    assert.deepEqual(shown, {
      who: 'Writer', sub: 'asked 3 days ago · holds up: Website sales chat build',
      q: 'Website sales chat: go with these picks?', why: "Why it matters: Engineering can't start until these are set.",
      picks: [
        {text: '1. Calls (recommended)', then: PICKS[0].then, ticked: true},
        {text: '2. Visitor notes (recommended)', then: PICKS[1].then, ticked: true},
        {text: '3. Pages', then: PICKS[2].then, ticked: false},
        {text: '4. Spend cap (recommended)', then: PICKS[3].then, ticked: true}],
      buttons: [{label: 'Yes to all', then: 'All 4 go to Writer as yes'},
        {label: 'Send ticked (3)', then: 'These 3 as yes; Writer hears "not this one" for the rest'}]});
    // 16, 17: the fallback is a small line at the foot, not a button and not styled as a pick; the run id, the error
    // and the path are only under Details, closed.
    const foot = await page.evaluate(() => {
      const body = document.querySelector('.decide .dc-body'), line = body.querySelector('.qc-fallback'), details = body.querySelector('.qc-details');
      const above = [...body.children].filter(x => x !== details).map(x => x.textContent).join(' ');
      return {line: line.textContent, last: [...body.children].slice(-2)[0] === line && body.lastElementChild === details, open: details.open,
        small: parseFloat(getComputedStyle(line).fontSize) < parseFloat(getComputedStyle(body.querySelector('.qc-then')).fontSize),
        weight: getComputedStyle(line).fontWeight, inButton: !!line.closest('button'),
        leaks: ['8719e1a6', 'ValueError', 'reports/chat-plan.md'].filter(x => above.includes(x)),
        detail: ['8719e1a6', 'ValueError', 'reports/chat-plan.md'].every(x => details.textContent.includes(x))};
    });
    const day = new Date('2026-10-23T16:00:00Z').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
    assert.deepEqual(foot, {line: `If you don't answer by ${day}: It stays a draft`, last: true, open: false, small: true, weight: '400',
      inButton: false, leaks: [], detail: true});

    // 12: untick pick 3 stays unticked; tick changes count. Untick 4, tick it back: Send ticked (3) sends 1, 2 and 4.
    await page.locator('.decide [data-qc-tick="3"]').uncheck();
    assert.equal((await page.evaluate(card)).buttons[1].label, 'Send ticked (2)');
    await page.keyboard.press('4');                       // a number ticks or unticks, as on the keyboard row
    assert.equal((await page.evaluate(card)).buttons[1].label, 'Send ticked (3)');
    await page.locator('.decide .dc-note textarea').fill('Pages: only pricing for now.');
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    await page.locator('.decide .dc-actions [data-dc="ticked"]').click();
    assert.equal(await page.locator('.decide .dc-toast span').textContent(), 'Sent 3 ticked · sent to Writer');
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-four', target: {comment: 'q-t-four'},
      answers: {decision: ['Calls', 'Visitor notes', 'Spend cap']}, other: 'Pages: only pricing for now.'});

    // 15: Tico's own check says Tico, never the assistant's name or "You asked"; each of its buttons says what happens.
    await page.locator('.decide .dc-card[data-key="task:t-stuck"]').waitFor();
    const check = await page.evaluate(() => ({who: document.querySelector('.decide .dc-who-name').textContent.trim(),
      header: document.querySelector('.decide .dc-who').textContent,
      buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => b.querySelector('.qc-then')?.textContent)}));
    assert.equal(check.who, 'Tico');
    assert.ok(!/Assistant|You asked/.test(check.header), check.header);
    assert.deepEqual(check.buttons, ['The bot is woken again', 'The task is closed']);

    // 13: Yes to all sends every pick.
    items = [question('t-all')];
    await page.evaluate(async () => { location.hash = '#/tasks'; await v2Refresh(); location.hash = DECIDE; });
    await page.locator('.decide .dc-card[data-key="task:t-all"]').waitFor();
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    await page.locator('.decide .dc-actions [data-dc="all"]').click();
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-all', target: {comment: 'q-t-all'},
      answers: {decision: ['Calls', 'Visitor notes', 'Pages', 'Spend cap']}});

    // 11: the task page's Needs you box is the same card: same header, question, why line, picks and buttons.
    items = [question('t-page')];
    await page.evaluate(async () => { location.hash = '#/tasks'; await v2Refresh(); });
    const decided = await page.evaluate(() => {
      const it = S.v2.needs.find(x => x.id === 't-page'), q = qcardQuestion(it);
      return {body: qcardBodyHTML(it, q, {attr: 'qc'}), actions: qcardActionsHTML(it, q, {attr: 'qc'}), sub: qcardSub(it)};
    });
    await page.evaluate(() => { location.hash = '#/task/t-page'; });
    const box = page.locator('.qcard[data-qcard="t-page"]');
    await box.waitFor();
    const onPage = await box.evaluate(el => ({who: el.querySelector('.qc-who').textContent, sub: el.querySelector('.qc-sub').textContent,
      picks: [...el.querySelectorAll('.qc-pick')].map(p => p.textContent.replace(/\s+/g, ' ').trim()),
      buttons: [...el.querySelectorAll('.qc-actions button')].map(b => b.textContent.replace(/\s+/g, ' ').trim()),
      fallback: el.querySelector('.qc-fallback').textContent, details: el.querySelector('.qc-details').open}));
    assert.equal(onPage.who, 'Writer');
    assert.equal(onPage.sub, ' · ' + decided.sub);
    assert.deepEqual(onPage.picks, [
      `1. Calls (recommended)${PICKS[0].then}`, `2. Visitor notes (recommended)${PICKS[1].then}`,
      `3. Pages${PICKS[2].then}`, `4. Spend cap (recommended)${PICKS[3].then}`].map(s => s.replace(/\s+/g, ' ')));
    assert.deepEqual(onPage.buttons, ['Yes to allAll 4 go to Writer as yes', 'Send ticked (3)These 3 as yes; Writer hears "not this one" for the rest']);
    assert.equal(onPage.details, false);
    // Unticking on the task page sends the rest, as in Decide.
    await box.locator('[data-qc-tick="0"]').uncheck();
    await box.locator('[data-qc="ticked"]').click();
    for (let i = 0; i < 100 && !answers.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(answers.shift(), {task: 't-page', target: {comment: 'q-t-page'}, answers: {decision: ['Visitor notes', 'Spend cap']}});
    assert.deepEqual(errors, []);
    console.log('Question card: four picks ticked by recommendation, Send ticked and Yes to all, what-happens lines, '
      + 'asker/age/holds up, Tico for checks, fallback at the foot, ids under Details, same card on the task page');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
