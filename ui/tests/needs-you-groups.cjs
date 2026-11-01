// Needs you on a busy company: grouped by what each item takes (Decide, Pay, Sign in or connect, Review and send, Do)
// with a count per group, in the server's order (due first); an approval reads what it approves; a deadline shows as a
// due chip; a card with folded duplicates says "+N"; an ask with a recommendation offers "Do what you recommend", which
// posts once and closes the card. Its card (a tap on the row) leads with the same button (and R). Overview shows the count and how many
// went away on their own this week.
// NEEDS_SHOTS=<dir> also saves the screenshots for the PR.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.NEEDS_SHOTS || '';
const inDays = days => new Date(Date.now() + days * 86400000).toISOString();
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const base = (id, title, over = {}) => ({id, title, body: 'Details.', owner_id: 'human:ana', requester_id: 'bot:writer', status: 'open',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(600), updated_at: ago(60), ...over});
const TASKS = [
  base('t-keys', 'Add the new OpenAI keys', {requester_id: 'bot:botops', due_at: inDays(2)}),
  base('t-pick', 'Gusto or Rippling for payroll?', {requester_id: 'bot:analyst', due_at: inDays(9),
    ask: {id: 'q-pick', body: 'Should payroll move to Gusto or Rippling?', sender_id: 'bot:analyst', recipient_id: 'human:ana'}}),
  base('t-invoice', 'Pay the October hosting invoice', {requester_id: 'bot:botops'}),
  base('t-deck', 'Review the launch deck', {requester_id: 'human:sam'}),
  base('t-call', 'Call Murphy back about the lease', {requester_id: 'bot:support'}),
  base('t-vendor', 'Switch the payroll vendor', {owner_id: 'bot:analyst', requester_id: 'human:ana', status: 'waiting', note: 'Waiting on Ana to pick the vendor.'}),
  // A bot's question the Strategy Bot merged into an item of its own: the server leaves it out of Needs you, so the
  // page does.
  base('t-merged', 'Which pricing page first?', {requester_id: 'bot:ops',
    ask: {id: 'q-merged', body: 'Which pricing page launches first?', sender_id: 'bot:ops', recipient_id: 'human:ana'}}),
  // Bots' own tasks with a question to Ana over three days old: one merged, which stays out of Older questions too.
  base('t-old-merged', 'Which launch day?', {owner_id: 'bot:coo', requester_id: 'bot:coo',
    ask: {id: 'q-old-merged', body: 'Which day do we launch?', sender_id: 'bot:coo', recipient_id: 'human:ana', older: true}}),
  base('t-old', 'Which logo on the invoice?', {owner_id: 'bot:finance', requester_id: 'bot:finance',
    ask: {id: 'q-old', body: 'Which logo goes on the invoice?', sender_id: 'bot:finance', recipient_id: 'human:ana', older: true}}),
];
const SEND = {id: 'ap-send', kind: 'approval', what: 'send', requester_id: 'bot:writer', requested_by: 'bot:writer', created_at: ago(30),
  title: "Send: '$200 for each rental owner you introduce' to Jennie Ooten", need: 'review',
  payload: {to: ['Jennie Ooten <jennie@acme.example>'], cc: [], subject: '$200 for each rental owner you introduce', body_sha256: 'a'.repeat(64)}};
const SPEND = {id: 'ap-spend', kind: 'approval', what: 'spend', requester_id: 'bot:analyst', requested_by: 'bot:analyst', created_at: ago(90),
  title: 'Pay $1,200: LinkedIn ads for October', need: 'pay', payload: {amount: '$1,200', what: 'LinkedIn ads for October', account: 'Ads'}};
const REC = {do: 'Gusto', ask: 'q-pick', option: 'Gusto', kind: 'reversible'};
const card = (id, over) => ({...TASKS.find(x => x.id === id), kind: 'task', ...over});
const NEEDS = [
  card('t-keys', {need: 'connect'}),
  // It holds up a roadmap item at Building, so Decide shows it first (backend/views.py decide_tier).
  card('t-pick', {need: 'decide', recommend: REC, tier: 1, holds_up: {id: 'r-payroll', title: 'Payroll move', kind: 'roadmap', step: 'Building'},
    folded: [{id: 't-vendor', title: 'Switch the payroll vendor', owner_id: 'bot:analyst', kind: 'waiting', note: 'Waiting on Ana to pick the vendor.'}]}),
  SEND,
  SPEND,
  card('t-invoice', {need: 'pay'}),
  card('t-deck', {need: 'review'}),
  card('t-call', {need: 'do', merged: ['t-merged', 't-old-merged']}),
];
const WENT_AWAY = {expired: 3, withdrawn: 1, assistant: 2, total: 6, days: 7};

async function shot(target, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await target.screenshot({path: path.join(SHOTS, name + '.png')});
}

(async () => {
  const browser = await chromium.launch();
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks', byNeed: true, baseTasks: TASKS});
    let needs = NEEDS;
    const taken = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json',
      body: JSON.stringify({items: needs, went_away: WENT_AWAY})}));
    await page.route('**/api/v2/needs-you/recommended', route => {
      taken.push(route.request().postDataJSON());
      needs = needs.filter(x => x.id !== 't-pick');
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({did: 'Gusto', task: 't-pick'})});
    });
    await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    assert.equal((await page.locator('#task-group .v').textContent()).trim(), 'What it takes');

    // Groups in order, each with its count; approvals sit in the group their kind takes.
    const groups = await page.locator('#task-body [data-group]').evaluateAll(gs => gs.map(g => ({
      key: g.dataset.group, label: g.querySelector('.tl-gname, .tl-group-name, h3')?.textContent.trim(),
      count: g.querySelector('.tl-count, .tl-gcount, .count')?.textContent.trim()})));
    assert.deepEqual(groups.map(g => [g.key, g.count]),
      [['need:decide', '1'], ['need:pay', '2'], ['need:connect', '1'], ['need:review', '2'], ['need:do', '1'], ['older', '1']], JSON.stringify(groups));
    assert.deepEqual(groups.map(g => g.label), ['Answer', 'Pay', 'Sign in or connect', 'Review and send', 'Do', 'Older questions']);
    const review = page.locator('#task-body [data-group="need:review"]');
    assert.ok((await review.textContent()).includes("Send: '$200 for each rental owner you introduce' to Jennie Ooten"));
    assert.ok((await page.locator('#task-body [data-group="need:pay"]').textContent()).includes('Pay $1,200: LinkedIn ads for October'));

    // The due chip, the folded duplicate and the recommendation on the decide card; the folded task is not its own row.
    const pick = page.locator('#task-body [data-group="need:decide"] .tl-row').first();
    assert.match(await pick.textContent(), /Due (Wed|Thu|Fri|Sat|Sun|Mon|Tue|[A-Z][a-z]{2} \d+)/);
    assert.match(await page.locator('#task-body [data-group="need:connect"] .tl-row').first().textContent(), /Due (\w{3}|tomorrow|today)/);
    assert.equal((await pick.locator('.tl-folded').textContent()).trim(), '+1');
    assert.equal(await page.locator('#task-body .tl-row', {hasText: 'Switch the payroll vendor'}).count(), 0);
    // Older questions, opened, holds the unmerged old question and not the merged one.
    const older = page.locator('#task-body [data-group="older"]');
    if ((await older.locator('[data-group-toggle]').getAttribute('aria-expanded')) !== 'true') await older.locator('[data-group-toggle]').click();
    assert.deepEqual(await older.locator('.tl-row').evaluateAll(rs => rs.map(r => r.textContent.includes('Which logo on the invoice?')
      ? 'control' : r.textContent.includes('Which launch day?') ? 'merged' : 'other')), ['control']);
    const rec = pick.locator('[data-recommend]');
    assert.equal((await rec.textContent()).trim(), 'Do what you recommend');
    await shot(page, 'needs-you-groups');

    // Its card leads with the same one-tap answer.
    await pick.locator('.tl-title').click();
    const dc = page.locator('#need-card .dc-card[data-key="task:t-pick"]');
    await dc.waitFor();
    assert.equal((await dc.locator('.dc-who .pill.needs').textContent()).trim(), 'Question');
    const dcRec = page.locator('.decide .dc-actions [data-dc="recommend"]');
    assert.ok((await dcRec.textContent()).includes('Do what you recommend'));
    assert.match(await dc.locator('.need-folded').innerText(), /Also waiting on this[\s\S]*Switch the payroll vendor/i);
    if (SHOTS) await page.waitForTimeout(500);   // the card's entrance animation
    await shot(page, 'decide-recommend');

    // One tap answers and the card goes.
    await page.locator('#need-card [data-dc="close"]').click();
    await page.locator('#task-body [data-group="need:decide"] [data-recommend]').click();
    for (let i = 0; i < 50 && !taken.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(taken, [{ask: 'q-pick', version: 1}], 'with the version the card showed');
    await page.waitForFunction(() => !document.querySelector('#task-body [data-group="need:decide"] [data-recommend]'));

    // Overview: the count and what went away on its own.
    await page.evaluate(() => { location.hash = '#/overview/campus'; });
    const strip = page.locator('#overview-needs');
    await strip.waitFor();
    assert.equal(await strip.locator('strong').textContent(), 'Needs you 6');
    assert.equal(await strip.locator('.muted').textContent(), '6 went away on their own this week (3 expired, 1 withdrawn, 2 answered by your Assistant)');
    await shot(strip, 'overview-needs');
    assert.deepEqual(errors, []);
    console.log('Needs you groups: what-it-takes groups with counts, titled approvals, due chips, folded duplicates, one-tap recommendation, overview strip');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
