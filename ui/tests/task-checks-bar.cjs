// The Tasks page's right bar (ui/app/task-checks-bar.js): collapsed it shows today's catch count; expanded, a rule the
// person may not change has no switch and says who can, a catch takes a right / wrong mark, and a Task Manager card
// changes nothing until the person confirms it. At desktop and phone widths.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');

const BAR = {type: {id: 'general', name: 'General'}, today: 2, chat: true,
  report: {week: '2026-10-12', catches: 4, overrides: 1, wrong: 2, marked: 3, quiet_waits: 0,
    suggestions: ["'Duplicate' was marked wrong on 2 of 3 catches: narrow its task types or switch it to warn."]},
  rules: [
    {key: 'task-check-duplicate', name: 'Duplicate', on: true, when: ['a task is about to be created'], then: ['block it'],
      for: 'all types', check_words: 'it may already be asked for in an open task (Decide)', stats: 'Last 30 days: 2 catches',
      checkable: true, can_change: true, read_only: null, department: {id: 'engineering', name: 'Engineering'},
      catches: [{id: 'ev-1', at: new Date().toISOString(), task: {id: 't-1', title: 'Prepare the export', status: 'open'}, mark: null}]},
    {key: 'task-check-waiting', name: 'Waiting must say on whom or what', on: true, when: ["a task's status is about to change"],
      then: ['block it'], for: 'all types', check_words: 'a move to waiting says on whom or what it waits', stats: '',
      checkable: true, can_change: false, read_only: 'Only Engineering can change this rule.',
      department: {id: 'engineering', name: 'Engineering'}, catches: []}]};

async function withBar(browser, viewport) {
  const v = await open(browser, {viewport, hash: '#/tasks'});
  const calls = [];
  const card = {id: 'm-3', role: 'bot', body: "Change 'Duplicate': switch it off", card: {kind: 'change_rule', summary: "Change 'Duplicate': switch it off", state: 'pending', result: null}};
  await v.page.route(/\/api\/v2\/(task-checks|task-manager)/, async route => {
    const req = route.request(), p = new URL(req.url()).pathname, body = req.postDataJSON?.() ?? null;
    calls.push({method: req.method(), p, body});
    const json = data => route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    if (p === '/api/v2/task-checks/bar') return json(BAR);
    if (p.endsWith('/mark')) return json({id: 'ev-1', mark: body.verdict});
    if (p === '/api/v2/task-manager' && req.method() === 'GET') return json({messages: []});
    if (p === '/api/v2/task-manager') return json({messages: [{id: 'm-1', role: 'person', body: body.text}, {id: 'm-2', role: 'bot', body: 'Here it is.'}, card]});
    if (p.endsWith('/confirm')) return json({ok: true, messages: [{...card, card: {...card.card, state: 'done', result: 'Done.'}}]});
    return json({});
  });
  await v.page.reload();
  await v.page.locator('#tchk [data-tchk-toggle]').waitFor();
  return {...v, calls};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, calls} = await withBar(browser, {width: 1440, height: 900});
    const bar = page.locator('#tchk');
    await page.waitForFunction(() => document.querySelector('#tchk [data-tchk-today]')?.textContent.trim() === '2');
    await bar.locator('[data-tchk-toggle]').click();
    const waiting = bar.locator('[data-tchk-rule="task-check-waiting"]');
    await waiting.waitFor();
    assert.equal(await waiting.locator('[data-tchk-on]').count(), 0, 'no switch on a rule the person may not change');
    assert.equal(await waiting.locator('.tchk-ro').count(), 1);
    assert.equal(await bar.locator('[data-tchk-on="task-check-duplicate"]').count(), 1);
    await bar.locator('[data-tchk-catch="ev-1"] [data-tchk-mark="wrong"]').click();
    await page.waitForFunction(() => document.querySelector('#tchk [data-tchk-mark="wrong"]')?.getAttribute('aria-pressed') === 'true');
    assert.deepEqual(calls.find(c => c.p.endsWith('/mark')).body, {verdict: 'wrong'});
    await bar.locator('[data-tchk-input]').fill('Duplicates block too much');
    await bar.locator('[data-tchk-input]').press('Enter');
    await bar.locator('[data-tchk-confirm="m-3"]').waitFor();
    assert.equal(calls.filter(c => c.p.endsWith('/confirm')).length, 0, 'nothing runs before the confirm');
    assert.equal(calls.find(c => c.p === '/api/v2/task-manager' && c.method === 'POST').body.type, 'general');
    await bar.locator('[data-tchk-confirm="m-3"]').click();
    await page.waitForFunction(() => /Done\./.test(document.querySelector('#tchk [data-tchk-msg="m-3"]')?.textContent || ''));
    assert.equal(await bar.locator('[data-tchk-confirm]').count(), 0);
    await bar.locator('[data-tchk-toggle]').first().click();
    await bar.locator('.tchk-strip').waitFor();
    assert.deepEqual(errors, []);
    await page.close();

    const phone = await withBar(browser, {width: 390, height: 844});
    const strip = await phone.page.locator('#tchk .tchk-strip').boundingBox();
    assert.ok(strip && strip.x + strip.width <= 390, 'the strip is on screen on a phone');
    await phone.page.locator('#tchk [data-tchk-toggle]').click();
    await phone.page.locator('#tchk [data-tchk-input]').waitFor();
    const sheet = await phone.page.locator('#tchk').boundingBox();
    assert.ok(sheet.x >= 0 && sheet.x + sheet.width <= 390, 'the expanded bar fits a phone');
    assert.deepEqual(phone.errors, []);
    await phone.page.close();
    console.log('Task checks bar: count, read-only rule, marks, confirm-only card, phone passed');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
