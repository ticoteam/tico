// The ask filter on Needs you (backend/ask_ingest.py): a chore it let through sits under Hands needed; what it kept off
// the plate today is one folded "Filtered for you" line at the foot, each with its asker and reason, and Let it through
// posts once with no confirm. A Rehearsal item says what On would have done and has no button. Settings → Tasks holds
// "What bots may put on you" and saves the next item's settings.
// NEEDS_SHOTS=<dir> also saves the screenshots for the PR.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.NEEDS_SHOTS || '';
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const base = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'human:ana', requester: 'bot:botops', status: 'open',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(600), updated_at: ago(60), ...over});
const TASKS = [base('t-access', 'Grant ops read access to the billing repo'), base('t-pick', 'Gusto or Rippling for payroll?')];
const NEEDS = [{...TASKS[1], kind: 'task', need: 'decide'}, {...TASKS[0], kind: 'task', need: 'hands'}];
const FILTERED = [
  {id: 'f-1', kind: 'task', task: 't-verify', title: 'Verify the cleanup ran', asker: 'bot:ops', asker_name: 'Ops',
    outcome: 'sent_back', would: 'sent_back', reason: 'own_work', mode: 'on', created_at: ago(20), let_through_at: null},
  {id: 'f-2', kind: 'loose', task: null, title: 'Release at 17:00Z', asker: 'bot:release', asker_name: 'Release Manager',
    outcome: 'through', would: 'fyi', reason: 'status', mode: 'rehearsal', created_at: ago(40), let_through_at: null},
];

async function shot(target, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await target.screenshot({path: path.join(SHOTS, name + '.png')});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks', byNeed: true, baseTasks: TASKS});
    const let_ = [], saved = [];
    let filtered = FILTERED;
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: NEEDS})}));
    await page.route('**/api/v2/ask-ingest/filtered*', route => route.fulfill({contentType: 'application/json',
      body: JSON.stringify({items: filtered, count: filtered.length})}));
    await page.route('**/api/v2/ask-ingest/items/*/let-through', route => {
      let_.push(route.request().url().split('/items/')[1].split('/')[0]);
      filtered = filtered.map(f => f.id === 'f-1' ? {...f, let_through_at: ago(0)} : f);
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({item: {id: 'f-1'}})});
    });
    await page.evaluate(async () => { await v2Refresh(); await tasksLoad(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.evaluate(async () => { await tasksLoad(TASKS_ST); });

    const hands = page.locator('#task-body [data-group="need:hands"]');
    await hands.waitFor();
    assert.equal((await hands.locator('.tl-gname').textContent()).trim(), 'Hands needed');
    assert.ok((await hands.textContent()).includes('Grant ops read access to the billing repo'));

    // One folded line at the foot, after every group.
    const line = page.locator('#task-body [data-group="filtered"]');
    await line.waitFor();
    const order = await page.locator('#task-body [data-group]').evaluateAll(gs => gs.map(g => g.dataset.group));
    assert.equal(order.at(-1), 'filtered', JSON.stringify(order));
    assert.equal(await line.evaluate(d => d.open), false, 'folded until opened');
    assert.equal((await line.locator('.tl-gcount').textContent()).trim(), '2');
    await line.locator('summary').click();
    const rows = line.locator('.tl-filtered-row');
    assert.match(await rows.nth(0).innerText(), /Ops:\s*Verify the cleanup ran[\s\S]*Sent back · Work a bot can do itself/);
    assert.match(await rows.nth(1).innerText(), /Would have: fyi/);
    assert.equal(await rows.nth(1).locator('[data-let-through]').count(), 0, 'Rehearsal has nothing to let through');
    await shot(page, 'ask-filter-needs-you');

    // Let it through: one post, no confirm, and the row says so after the reload.
    await rows.nth(0).locator('[data-let-through]').click();
    for (let i = 0; i < 50 && !let_.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(let_, ['f-1']);
    await page.waitForFunction(() => !document.querySelector('#task-body [data-let-through="f-1"]'));
    assert.deepEqual(let_, ['f-1'], 'once');

    // Settings → Tasks: the person's own settings save with the next item's values.
    await page.route('**/api/v2/ask-ingest/settings', route => {
      if (route.request().method() === 'PUT') { saved.push(route.request().postDataJSON()); }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({
        settings: {mode: 'rehearsal', budget: 10, chores_budget: 5, criteria: ['Strategy and positioning'], own: false},
        team: {mode: 'rehearsal', budget: 10, chores_budget: 5, criteria: ['Strategy and positioning']},
        modes: ['off', 'rehearsal', 'on'], may_edit_team: false})});
    });
    await page.evaluate(() => { location.hash = '#/settings/tasks'; });
    const card = page.locator('#set-ask-filter form.ask-filter');
    await card.waitFor();
    assert.match(await page.locator('#set-ask-filter').innerText(), /You use the team default/);
    await card.locator('input[name="mode"][value="on"]').check();
    await card.locator('input[name="budget"]').fill('15');
    await card.locator('textarea[name="criteria"]').fill('Strategy and positioning\nPricing');
    await shot(page.locator('#set-ask-filter-card'), 'ask-filter-settings');
    // The save reloads the settings and repaints the card, day field included (today by default): mark this paint's
    // field, so the replay below fills the repainted one (it passed only while today was the day it fills).
    const DAY = '#set-ask-filter .ask-filter-replay input[name="day"]';
    await page.evaluate(sel => { document.querySelector(sel).dataset.beforeSave = '1'; }, DAY);
    await card.locator('button[type="submit"]').click();
    for (let i = 0; i < 50 && !saved.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(saved, [{mode: 'on', budget: 15, chores_budget: 5, criteria: ['Strategy and positioning', 'Pricing'], team: false}]);

    // Replay a day: every page read, what On would keep off the plate first, nothing else posted.
    const replays = [];
    await page.route('**/api/v2/ask-ingest/replay', route => {
      const body = route.request().postDataJSON();
      replays.push(body);
      const pages = [
        {items: [{kind: 'task', ref: 't-1', title: 'Pick the launch date', asker: 'bot:ops', asker_name: 'Ops', created_at: ago(300), load: 4, would: 'through', reason: 'under_budget'}], next_offset: 1, total: 2},
        {items: [{kind: 'loose', ref: 'm-1', title: 'Verify the cleanup ran', asker: 'bot:ops', asker_name: 'Ops', created_at: ago(200), load: 16, would: 'sent_back', reason: 'own_work'}], next_offset: null, total: 2}];
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(pages[body.offset ? 1 : 0])});
    });
    await page.waitForFunction(sel => { const day = document.querySelector(sel); return day && !day.dataset.beforeSave; }, DAY);
    await page.locator(DAY).fill('2026-10-27');
    await page.locator('#set-ask-filter .ask-filter-replay button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#set-ask-filter .ask-filter-replay-rows .tl-filtered-row').length === 2);
    assert.deepEqual(replays.map(r => [r.date, r.offset]), [['2026-10-27', 0], ['2026-10-27', 1]]);
    const replayed = await page.locator('#set-ask-filter .ask-filter-replay-rows .tl-filtered-row').allInnerTexts();
    assert.match(replayed[0], /Verify the cleanup ran[\s\S]*Sent back/);
    assert.match(await page.locator('[data-ask-replay-msg]').innerText(), /2 items; On would have kept 1 off your plate/);
    await shot(page.locator('#set-ask-filter-card'), 'ask-filter-replay');
    assert.deepEqual(errors, []);
    console.log('Ask filter: Hands needed group, Filtered for you at the foot, Let it through once, Rehearsal rows, settings card, replay a day');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
