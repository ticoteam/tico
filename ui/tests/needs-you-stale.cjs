// The Stale tickets card in Needs you (backend/stale_review.py): one card, a row per task with its reason and
// suggestion; a row with nothing to accept has only Skip. Accept sends the task version its row showed, and Accept all
// sends the rows on screen with theirs, so a suggestion that arrived after the card was drawn is never applied unseen.
// The fake server keeps the pending rows as the hub does, so each step checks what the open card shows afterwards: an
// answered row leaves and the title counts again, a live addition appears, a folded card stays folded, and a batch the
// server refuses whole leaves the rows with Accept all usable again.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const row = (id, title, action, version) => ({id, task: 't-' + id.slice(2), title, status: 'open',
  reason: 'Nothing happened on it for 9 days', suggest: action === 'done' ? 'Mark done' : 'Check it is still needed', action, version});
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const server = {rows: [row('s-1', 'Old chore one', 'done', 4), row('s-2', 'Old chore two', null, 2),
      row('s-3', 'Old chore three', 'done', 9)], refuseAll: false};
    const card = () => ({kind: 'stale', id: 'stale:human:ana', need: 'decide', first_line: '', created: new Date().toISOString(),
      title: `Stale tickets: ${server.rows.length} suggestion${server.rows.length === 1 ? '' : 's'}`, rows: server.rows});
    const sent = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json',
      body: JSON.stringify({items: server.rows.length ? [card()] : []})}));
    await page.route('**/api/v2/stale-review/*', route => {
      const sid = new URL(route.request().url()).pathname.split('/').pop(), body = route.request().postDataJSON();
      sent.push([sid, body]);
      let out;
      if (sid === 'accept-all') {
        const ids = body.rows.map(r => r.id);
        out = server.refuseAll ? {accepted: [], refused: ids.map(id => ({id, reason: 'Not yours to change'}))}
          : {accepted: ids.map(id => ({id, did: 'done'})), refused: []};
        if (!server.refuseAll) server.rows = server.rows.filter(r => !ids.includes(r.id));
      } else {
        server.rows = server.rows.filter(r => r.id !== sid);
        out = {id: sid, outcome: body.decision === 'skip' ? 'skipped' : 'accepted'};
      }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(out)});
    });
    const redraw = () => page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });   // as a live `needs` change does
    await redraw();
    await page.locator('#task-view [data-view="foryou"]').click();
    const shown = page.locator('[data-group="stale"] details[data-stale-card]');
    await shown.first().waitFor();
    const rows = shown.locator('[data-stale-row]');
    const onScreen = async () => ({title: (await shown.locator('.req-text').textContent()).trim(),
      rows: await rows.evaluateAll(els => els.map(e => e.dataset.staleRow)),
      busy: await shown.locator('button:disabled').count()});
    const settles = async (want, why) => {
      for (let i = 0; i < 100; i++) { const now = await onScreen(); if (JSON.stringify(now) === JSON.stringify(want)) return; await page.waitForTimeout(20); }
      assert.deepEqual(await onScreen(), want, why);
    };
    assert.equal(await shown.count(), 1, 'one card for every suggestion');
    await settles({title: 'Tico: Stale tickets: 3 suggestions', rows: ['s-1', 's-2', 's-3'], busy: 0}, 'first draw');
    assert.equal(await rows.nth(1).locator('[data-stale-decision="accept"]').count(), 0, 'nothing to accept: Skip only');
    assert.equal(await rows.nth(1).locator('[data-stale-decision="skip"]').count(), 1);

    await rows.nth(0).locator('[data-stale-decision="accept"]').click();
    await settles({title: 'Tico: Stale tickets: 2 suggestions', rows: ['s-2', 's-3'], busy: 0}, 'an accepted row leaves the open card');
    assert.deepEqual(sent.shift(), ['s-1', {decision: 'accept', version: 4}], 'Accept names the version the row showed');

    await rows.nth(0).locator('[data-stale-decision="skip"]').click();
    await settles({title: 'Tico: Stale tickets: 1 suggestion', rows: ['s-3'], busy: 0}, 'a skipped row leaves the open card');
    assert.deepEqual(sent.shift(), ['s-2', {decision: 'skip'}]);

    server.rows.push(row('s-4', 'Old chore four', 'done', 1));
    await redraw();
    await settles({title: 'Tico: Stale tickets: 2 suggestions', rows: ['s-3', 's-4'], busy: 0}, 'a live addition shows on the open card');

    await shown.locator('summary').click();                 // the person folds the card; a change does not open it again
    assert.equal(await shown.evaluate(d => d.open), false);
    server.rows.push(row('s-6', 'Old chore six', 'done', 3));
    await redraw();
    await settles({title: 'Tico: Stale tickets: 3 suggestions', rows: ['s-3', 's-4', 's-6'], busy: 0}, 'drawn again while folded');
    assert.equal(await shown.evaluate(d => d.open), false, 'a folded card stays folded');
    await shown.locator('summary').click();
    assert.equal(await shown.evaluate(d => d.open), true);

    // A suggestion reaches the page's list but the card is not drawn again yet: Accept all sends what is on screen.
    const late = row('s-5', 'Unseen', 'done', 1);
    server.rows.push(late);
    await page.evaluate(r => S.v2.needs.find(x => x.kind === 'stale').rows.push(r), late);
    await shown.locator('[data-stale-all]').click();
    await settles({title: 'Tico: Stale tickets: 1 suggestion', rows: ['s-5'], busy: 0}, 'accepted rows leave; the late row now shows');
    assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-3', version: 9}, {id: 's-4', version: 1}, {id: 's-6', version: 3}]}],
      'Accept all sends the rows shown that have an action, never one added since');

    server.refuseAll = true;                                 // nothing changes, so the card is the same: its button comes back
    await shown.locator('[data-stale-all]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-5', version: 1}]}]);
    await settles({title: 'Tico: Stale tickets: 1 suggestion', rows: ['s-5'], busy: 0}, 'a refused batch leaves the row and a usable button');
    assert.equal((await shown.locator('[data-stale-all]').textContent()).trim(), 'Accept all');
    assert.deepEqual(errors, []);
    console.log('Needs you stale card: Accept, Skip, Accept all and live additions redraw the open card; folded stays folded');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
