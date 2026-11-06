// What bots may put on you: chores use ordinary Needs you rows, refused asks have no kept-items section,
// and Settings saves mode, the main budget and criteria. NEEDS_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.NEEDS_SHOTS || '';
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const base = (id, title, over = {}) => ({id, title, body: 'Details.', owner_id: 'human:ana', requester_id: 'bot:botops', status: 'open',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(600), updated_at: ago(60), ...over});
const TASKS = [base('t-access', 'Grant ops read access to the billing repo'), base('t-pick', 'Gusto or Rippling for payroll?')];
const NEEDS = [{...TASKS[1], kind: 'task', need: 'decide'}, {...TASKS[0], kind: 'task', need: 'do'}];
async function shot(target, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await target.screenshot({path: path.join(SHOTS, name + '.png')});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks', byNeed: true, baseTasks: TASKS});
    const retiredRequests = [], saved = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: NEEDS})}));
    await page.route('**/api/v2/ask-ingest/filtered*', route => {
      retiredRequests.push(route.request().url());
      return route.fulfill({status: 404, body: '{}'});
    });
    await page.evaluate(async () => { await v2Refresh(); await tasksLoad(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.evaluate(async () => { await tasksLoad(TASKS_ST); });

    const ordinary = page.locator('#task-body [data-group="need:do"]');
    await ordinary.waitFor();
    assert.ok((await ordinary.textContent()).includes('Grant ops read access to the billing repo'));
    assert.equal(await page.locator('#task-body [data-group="need:hands"], #task-body [data-group="filtered"], [data-let-through]').count(), 0);
    assert.deepEqual(retiredRequests, [], 'Needs you never reads the retired endpoint');
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => document.documentElement.dataset.theme = t, theme);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Needs you fits the viewport');
        await shot(page, `ask-needs-${width}-${theme}`);
      }
    }
    await page.setViewportSize({width: 1440, height: 900});

    // Settings → Tasks: the person's own settings save with the next item's values.
    await page.route('**/api/v2/ask-ingest/settings', route => {
      if (route.request().method() === 'PUT') { saved.push(route.request().postDataJSON()); }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({
        settings: {mode: 'off', budget: 10, criteria: ['Strategy and positioning'], own: false},
        team: {mode: 'off', budget: 10, criteria: ['Strategy and positioning']},
        modes: ['off', 'on'], may_edit_team: false})});
    });
    await page.evaluate(() => { location.hash = '#/settings/tasks'; });
    const card = page.locator('#set-ask-filter form.ask-filter');
    await card.waitFor();
    assert.equal(await card.locator('input[name="mode"]').count(), 2, 'Off and On only');
    assert.equal(await card.locator('input[name="chores_budget"]').count(), 0);
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
    assert.deepEqual(saved, [{mode: 'on', budget: 15, criteria: ['Strategy and positioning', 'Pricing'], team: false}]);

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
    await page.waitForFunction(() => document.querySelectorAll('#set-ask-filter .ask-filter-replay-rows .ask-replay-row').length === 2);
    assert.deepEqual(replays.map(r => [r.date, r.offset]), [['2026-10-27', 0], ['2026-10-27', 1]]);
    const replayed = await page.locator('#set-ask-filter .ask-filter-replay-rows .ask-replay-row').allInnerTexts();
    assert.match(replayed[0], /Verify the cleanup ran[\s\S]*Refused/);
    assert.match(await page.locator('[data-ask-replay-msg]').innerText(), /2 items; On would have kept 1 off your plate/);
    await shot(page.locator('#set-ask-filter-card'), 'ask-filter-replay');
    assert.deepEqual(errors, []);
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => document.documentElement.dataset.theme = t, theme);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Settings fits the viewport');
        await shot(page, `ask-settings-${width}-${theme}`);
      }
    }
    console.log('Ask settings: ordinary chore rows, no kept-items section, main budget, criteria and replay');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
