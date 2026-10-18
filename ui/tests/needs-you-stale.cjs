// The Stale tickets card in Needs you (backend/stale_review.py): one card, a row per task with its reason and
// suggestion; a row with nothing to accept has only Skip. Accept sends the task version its row showed, and Accept all
// sends the rows on screen with theirs, so a suggestion that arrived after the card was drawn is never applied unseen.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const row = (id, task, title, action, version) => ({id, task, title, status: 'open', reason: 'Nothing happened on it for 9 days',
  suggest: action === 'done' ? 'Mark done' : 'Check it is still needed', action, version});
const card = {kind: 'stale', id: 'stale:human:ana', title: 'Stale tickets: 3 suggestions', first_line: '',
  created: new Date().toISOString(), need: 'decide',
  rows: [row('s-1', 't-1', 'Old chore one', 'done', 4), row('s-2', 't-2', 'Old chore two', null, 2),
    row('s-3', 't-3', 'Old chore three', 'done', 9)]};
// The card already on screen follows the server (QA on v0.3.60: answered rows stayed, busy, until a reload). The
// server here keeps the suggestions: Skip and Accept take a row out, Accept all takes the rows it names at the version
// they showed and refuses one whose task moved on, and the list it hands back is what the card must draw.
async function answered(browser, viewport) {
  const {page, errors} = await open(browser, {hash: '#/tasks', viewport});
  const server = {rows: [row('s-1', 't-1', 'Old chore one', 'done', 4), row('s-2', 't-2', 'Old chore two', null, 2),
    row('s-3', 't-3', 'Old chore three', 'done', 9)]};
  const sent = [];
  const list = () => ({items: server.rows.length ? [{...card, title: `Stale tickets: ${server.rows.length} suggestion${server.rows.length === 1 ? '' : 's'}`,
    rows: server.rows.map(r => ({...r}))}] : []});
  const take = id => { server.rows = server.rows.filter(r => r.id !== id); };
  await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(list())}));
  await page.route('**/api/v2/stale-review/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').pop(), body = route.request().postDataJSON();
    sent.push([id, body]);
    if (id !== 'accept-all') { take(id); return route.fulfill({contentType: 'application/json', body: JSON.stringify({id, outcome: body.decision === 'skip' ? 'skipped' : 'accepted'})}); }
    const accepted = [], refused = [];
    // refuseAll: every row refused and nothing changes (not yours to change), so the card's list comes back the same.
    for (const {id: rid, version} of body.rows) {
      const r = server.rows.find(x => x.id === rid);
      if (r && r.version === version && !server.refuseAll) { accepted.push({id: rid, task: r.task, did: r.action}); take(rid); }
      else refused.push({id: rid, reason: 'The task changed since'});
    }
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({accepted, refused})});
  });
  const poll = () => page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });   // what the live poll does
  await poll();
  await page.locator('#task-view [data-view="foryou"]').click();
  const shown = page.locator('[data-group="stale"] details[data-stale-card]');
  await shown.first().waitFor();
  await shown.evaluate(el => { el.__drawn = true; });
  const look = () => page.evaluate(() => {
    const el = document.querySelector('[data-group="stale"] details[data-stale-card]');
    return el && {same: !!el.__drawn, open: el.open, title: el.querySelector('.req-text').textContent.trim(),
      count: document.querySelector('[data-group="stale"] .tl-gcount').textContent.trim(),
      rows: [...el.querySelectorAll('[data-stale-row]')].map(li => `${li.dataset.staleRow}@${li.dataset.staleVersion}`),
      busy: [...el.querySelectorAll('button')].filter(b => b.disabled || b.textContent === '…').length,
      all: el.querySelectorAll('[data-stale-all]').length,
      focus: document.activeElement?.closest?.('[data-stale-row]')?.dataset.staleRow || (el.contains(document.activeElement) ? 'card' : '')};
  });
  const settle = async (want, what) => {
    let now;
    for (let i = 0; i < 250; i++) { now = await look(); if (want(now)) return now; await page.waitForTimeout(20); }
    assert.fail(`${what} (${viewport.width}px): ${JSON.stringify(now)}`);
  };
  const button = (id, decision) => shown.locator(`[data-stale-row="${id}"] [data-stale-decision="${decision}"]`);

  await button('s-2', 'skip').click();
  let now = await settle(v => v.rows.length === 2, 'Skip takes the row out of the card on screen');
  assert.deepEqual(sent.shift(), ['s-2', {decision: 'skip'}]);
  assert.deepEqual(now, {same: true, open: true, title: 'Tico: Stale tickets: 2 suggestions', count: '2', rows: ['s-1@4', 's-3@9'],
    busy: 0, all: 1, focus: 's-3'}, 'the same card, still open, title and count follow, nothing left busy, focus on the next row');

  // A suggestion arrives while the person has the card shut: it joins the card, which stays shut.
  await shown.locator('summary').click();
  assert.equal((await look()).open, false);
  server.rows.push(row('s-4', 't-4', 'Old chore four', 'done', 1));
  await poll();
  now = await look();
  assert.deepEqual([now.same, now.open, now.title, now.count, now.rows], [true, false, 'Tico: Stale tickets: 3 suggestions', '3', ['s-1@4', 's-3@9', 's-4@1']],
    'a live suggestion is drawn in the open card, and a card the person shut stays shut');
  await shown.locator('summary').click();
  // And one that arrives while a row's button has the focus leaves the focus where it was.
  await button('s-3', 'skip').focus();
  server.rows.push(row('s-5', 't-5', 'Old chore five', null, 3));
  await poll();
  now = await look();
  assert.deepEqual([now.rows, now.focus], [['s-1@4', 's-3@9', 's-4@1', 's-5@3'], 's-3'], 'focus stays on the row it was on');

  await button('s-1', 'accept').click();
  now = await settle(v => !v.rows.includes('s-1@4'), 'Accept takes only its own row out');
  assert.deepEqual(sent.shift(), ['s-1', {decision: 'accept', version: 4}]);
  assert.deepEqual([now.same, now.title, now.count, now.rows, now.busy], [true, 'Tico: Stale tickets: 3 suggestions', '3', ['s-3@9', 's-4@1', 's-5@3'], 0]);

  // Accept all: s-4's task moved on before the click (refused, the row stays at its new version), and s-6 arrived
  // after the card was drawn (not applied, drawn after the answer).
  server.rows.find(r => r.id === 's-4').version = 2;
  server.rows.push(row('s-6', 't-6', 'Old chore six', 'done', 1));
  await shown.locator('[data-stale-all]').click();
  now = await settle(v => !v.rows.includes('s-3@9'), 'Accept all takes the accepted rows out');
  assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-3', version: 9}, {id: 's-4', version: 1}]}]);
  assert.deepEqual([now.same, now.title, now.count, now.rows, now.busy, now.all],
    [true, 'Tico: Stale tickets: 3 suggestions', '3', ['s-4@2', 's-5@3', 's-6@1'], 0, 1],
    'refused row at its new version, the late row drawn, Accept all ready again');

  // Accept all refused for every row: the list comes back the same, so the card is not redrawn, and Accept all is
  // ready again as itself, not left busy.
  server.refuseAll = true;
  await shown.locator('[data-stale-all]').click();
  for (let i = 0; i < 250 && !sent.length; i++) await page.waitForTimeout(20);
  assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-4', version: 2}, {id: 's-6', version: 1}]}]);
  now = await settle(v => v.busy === 0, 'Accept all refused for every row is ready again');
  assert.deepEqual([now.same, now.title, now.rows, now.all], [true, 'Tico: Stale tickets: 3 suggestions', ['s-4@2', 's-5@3', 's-6@1'], 1]);
  assert.equal((await shown.locator('[data-stale-all]').textContent()).trim(), 'Accept all');
  assert.equal(await shown.locator('[data-stale-all]').isEnabled(), true);
  server.refuseAll = false;

  for (const id of ['s-4', 's-5', 's-6']) {
    await button(id, 'skip').click();
    await settle(v => !v || !v.rows.some(r => r.startsWith(id + '@')), `Skip ${id}`);
  }
  assert.equal(await shown.count(), 0, 'the last answer takes the card away');
  assert.equal(await page.locator('[data-group="stale"]').count(), 0);
  assert.deepEqual(errors, []);
  await page.close();
}
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const sent = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [card]})}));
    await page.route('**/api/v2/stale-review/*', route => {
      const p = new URL(route.request().url()).pathname;
      sent.push([p.split('/').pop(), route.request().postDataJSON()]);
      const body = p.endsWith('accept-all') ? {accepted: [{id: 's-1', task: 't-1', did: 'done'}], refused: []} : {id: 's-1', outcome: 'accepted'};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    });
    await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    const shown = page.locator('[data-group="stale"] details[data-stale-card]');
    await shown.first().waitFor();
    assert.equal(await shown.count(), 1, 'one card for every suggestion');
    assert.equal((await shown.locator('.req-text').textContent()).trim(), 'Tico: Stale tickets: 3 suggestions');
    const rows = shown.locator('[data-stale-row]');
    assert.equal(await rows.count(), 3);
    assert.equal(await rows.nth(1).locator('[data-stale-decision="accept"]').count(), 0, 'nothing to accept: Skip only');
    assert.equal(await rows.nth(1).locator('[data-stale-decision="skip"]').count(), 1);

    await rows.nth(0).locator('[data-stale-decision="accept"]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['s-1', {decision: 'accept', version: 4}], 'Accept names the version the row showed');

    // A suggestion reaches the page's list but the card is not drawn again yet: Accept all sends what is on screen.
    await page.evaluate(() => {
      const it = S.v2.needs.find(x => x.kind === 'stale');
      it.rows.push({id: 's-4', task: 't-4', title: 'Unseen', status: 'open', reason: 'x', suggest: 'Mark done', action: 'done', version: 1});
    });
    await shown.locator('[data-stale-all]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-1', version: 4}, {id: 's-3', version: 9}]}],
      'Accept all sends the rows shown that have an action, never one added since');
    assert.deepEqual(errors, []);
    await page.close();
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) await answered(browser, viewport);
    console.log('Needs you stale card: one card, Skip-only rows, Accept and Accept all send what the card showed;'
      + ' an answered row leaves the card drawn, a live suggestion joins it, open or shut and focus kept (desktop + phone)');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
