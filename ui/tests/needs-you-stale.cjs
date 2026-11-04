// The Stale tickets card in Needs you (backend/stale_review.py): one line in the list; a tap opens its card, a row per
// task with its reason and suggestion; a row with nothing to accept has only Skip. Accept sends the task version its
// row showed, and Accept all sends the rows on screen with theirs, so a suggestion that arrived after the card was
// drawn is never applied unseen.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const row = (id, task, title, action, version) => ({id, task, title, status: 'open', reason: 'Nothing happened on it for 9 days',
  suggest: action === 'done' ? 'Mark done' : 'Check it is still needed', action, version});
const card = {kind: 'stale', id: 'stale:human:ana', title: 'Stale tickets: 3 suggestions', first_line: '',
  created_at: new Date().toISOString(), need: 'decide',
  rows: [row('s-1', 't-1', 'Old chore one', 'done', 4), row('s-2', 't-2', 'Old chore two', null, 2),
    row('s-3', 't-3', 'Old chore three', 'done', 9)]};
const KEY = 'stale:stale:human:ana';
// The open card follows the server (QA on v0.3.60: answered rows stayed, busy, until a reload). The server here keeps
// the suggestions: Skip and Accept take a row out, Accept all takes the rows it names at the version they showed and
// refuses one whose task moved on, and the list it hands back is what the card must draw.
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
  await page.locator(`[data-need-card="${KEY}"]`).click();
  const shown = page.locator('#need-card [data-stale-card]');
  await shown.first().waitFor();
  await shown.evaluate(el => { el.__drawn = true; });
  const look = () => page.evaluate(key => {
    const el = document.querySelector('#need-card [data-stale-card]');
    const all = [...document.querySelectorAll('#need-card [data-stale-all]')];
    return el && {same: !!el.__drawn, title: document.querySelector('#need-card .dc-q').textContent.trim(),
      count: document.querySelector(`[data-need-card="${key}"] .tl-folded`)?.textContent.trim() ?? null,
      rows: [...el.querySelectorAll('[data-stale-row]')].map(li => `${li.dataset.staleRow}@${li.dataset.staleVersion}`),
      busy: [...el.querySelectorAll('button'), ...all].filter(b => b.disabled || b.textContent === '…').length,
      all: all.length, focus: document.activeElement?.closest?.('[data-stale-row]')?.dataset.staleRow || ''};
  }, KEY);
  const settle = async (want, what) => {
    let now;
    for (let i = 0; i < 250; i++) { now = await look(); if (want(now)) return now; await page.waitForTimeout(20); }
    assert.fail(`${what} (${viewport.width}px): ${JSON.stringify(now)}`);
  };
  const button = (id, decision) => shown.locator(`[data-stale-row="${id}"] [data-stale-decision="${decision}"]`);

  await button('s-2', 'skip').click();
  let now = await settle(v => v.rows.length === 2 && v.count === '2' && v.busy === 0 && v.focus === 's-3', 'Skip takes the row out of the card on screen');
  assert.deepEqual(sent.shift(), ['s-2', {decision: 'skip'}]);
  assert.deepEqual(now, {same: true, title: 'Stale tickets: 2 suggestions', count: '2', rows: ['s-1@4', 's-3@9'],
    busy: 0, all: 1, focus: 's-3'}, 'the same card, title and count follow, nothing left busy, focus on the next row');

  // A suggestion arrives while the card is open: it joins the card, and the focus stays on the row it was on.
  await button('s-3', 'skip').focus();
  server.rows.push(row('s-5', 't-5', 'Old chore five', null, 3));
  await poll();
  now = await look();
  assert.deepEqual([now.same, now.title, now.rows, now.focus], [true, 'Stale tickets: 3 suggestions', ['s-1@4', 's-3@9', 's-5@3'], 's-3'],
    'a live suggestion is drawn in the open card; focus stays');

  // One row goes and another comes in the same redraw, so the count stays: the row with the focus is the same
  // element, and the same button still has the focus.
  await button('s-3', 'accept').focus();
  await shown.evaluate(el => { const li = el.querySelector('[data-stale-row="s-3"]'); li.__kept = true; li.querySelector('[data-stale-decision="accept"]').__kept = true; });
  take('s-1');
  server.rows.push(row('s-7', 't-7', 'Old chore seven', 'done', 1));
  await poll();
  now = await look();
  assert.deepEqual([now.same, now.title, now.count, now.rows], [true, 'Stale tickets: 3 suggestions', '3', ['s-3@9', 's-5@3', 's-7@1']]);
  assert.deepEqual(await page.evaluate(() => {
    const li = document.querySelector('#need-card [data-stale-row="s-3"]'), f = document.activeElement;
    return {row: !!li.__kept, button: !!f.__kept, focused: [f.closest('[data-stale-row]')?.dataset.staleRow, f.dataset.staleDecision]};
  }), {row: true, button: true, focused: ['s-3', 'accept']}, 'an equal-count update keeps the focused row and button');

  await button('s-7', 'accept').click();
  now = await settle(v => !v.rows.includes('s-7@1') && v.count === '2' && v.busy === 0, 'Accept takes only its own row out');
  assert.deepEqual(sent.shift(), ['s-7', {decision: 'accept', version: 1}]);
  assert.deepEqual([now.same, now.title, now.count, now.rows, now.busy], [true, 'Stale tickets: 2 suggestions', '2', ['s-3@9', 's-5@3'], 0]);

  // Accept all: s-3's task moved on before the tap (refused, the row stays at its new version), and s-6 arrived
  // after the card was drawn (not applied, drawn after the answer).
  server.rows.find(r => r.id === 's-3').version = 10;
  server.rows.push(row('s-6', 't-6', 'Old chore six', 'done', 1));
  await page.locator('#need-card [data-stale-all]').click();
  now = await settle(v => v.rows.includes('s-6@1') && v.busy === 0, 'Accept all draws the list the server hands back');
  assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-3', version: 9}]}]);
  assert.deepEqual([now.same, now.title, now.rows, now.busy, now.all],
    [true, 'Stale tickets: 3 suggestions', ['s-3@10', 's-5@3', 's-6@1'], 0, 1],
    'refused row at its new version, the late row drawn, Accept all ready again');

  // Accept all refused for every row: the list comes back the same, and Accept all is ready again as itself.
  server.refuseAll = true;
  await page.locator('#need-card [data-stale-all]').click();
  for (let i = 0; i < 250 && !sent.length; i++) await page.waitForTimeout(20);
  assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-3', version: 10}, {id: 's-6', version: 1}]}]);
  now = await settle(v => v.busy === 0, 'Accept all refused for every row is ready again');
  assert.deepEqual([now.same, now.rows, now.all], [true, ['s-3@10', 's-5@3', 's-6@1'], 1]);
  assert.equal((await page.locator('#need-card [data-stale-all]').textContent()).trim(), 'Accept all');
  server.refuseAll = false;

  for (const id of ['s-3', 's-5', 's-6']) {
    await button(id, 'skip').click();
    await settle(v => !v || !v.rows.some(r => r.startsWith(id + '@')), `Skip ${id}`);
  }
  // The last answer takes the card away: nothing else waits, so the card says so, and the line leaves the list.
  await page.locator('#need-card .dc-done').waitFor();
  // The card follows v2Refresh; the list redraws after the hub's own refresh (needs-you.js staleDecide), a beat later.
  await page.waitForFunction(key => !document.querySelector(`[data-need-card="${key}"]`), KEY, {timeout: 10000});
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
    // One line, Tico's, with its count; nothing unfolds in the list.
    const line = page.locator(`#task-body [data-need-card="${KEY}"]`);
    assert.equal(await line.count(), 1, 'one line for every suggestion');
    assert.equal((await line.locator('.tl-title').textContent()).trim(), 'Tico: Stale tickets: 3 suggestions');
    assert.deepEqual(await line.locator('.tl-chips > *').allTextContents(), ['Stale check', '3']);
    assert.equal(await page.locator('#task-body details').count(), 0);
    await line.click();
    const shown = page.locator('#need-card [data-stale-card]');
    await shown.waitFor();
    const rows = shown.locator('[data-stale-row]');
    assert.equal(await rows.count(), 3);
    assert.equal(await rows.nth(1).locator('[data-stale-decision="accept"]').count(), 0, 'nothing to accept: Skip only');
    assert.equal(await rows.nth(1).locator('[data-stale-decision="skip"]').count(), 1);

    await rows.nth(0).locator('[data-stale-decision="accept"]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['s-1', {decision: 'accept', version: 4}], 'Accept names the version the row showed');

    // A suggestion reaches the page's list but is not drawn before the tap: Accept all sends what is on screen.
    // Keep the push and tap in one page turn so a live refresh cannot redraw it between those two actions.
    await page.evaluate(() => {
      const it = S.v2.needs.find(x => x.kind === 'stale');
      it.rows.push({id: 's-4', task: 't-4', title: 'Unseen', status: 'open', reason: 'x', suggest: 'Mark done', action: 'done', version: 1});
      document.querySelector('#need-card [data-stale-all]').click();
    });
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-1', version: 4}, {id: 's-3', version: 9}]}],
      'Accept all sends the rows shown that have an action, never one added since');
    assert.deepEqual(errors, []);
    await page.close();
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) await answered(browser, viewport);
    console.log('Needs you stale card: one line, its card has Skip-only rows, Accept and Accept all send what the card showed;'
      + ' an answered row leaves the card drawn, a live suggestion joins it, focus kept (desktop + phone)');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
