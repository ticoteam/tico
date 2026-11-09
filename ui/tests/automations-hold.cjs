// The run guard's hold (backend/automations.py `_guard`) on the fake hub in support/automations-fake.cjs: a held
// automation shows "On hold" on its row, and inside "On hold · N held" with Release and Drop for whoever may change it;
// its held runs read "Held" in Runs. Release sends {action: release} and the line goes; a viewer without rights sees the
// line but no buttons.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, DESKTOP, PHONE, sideways, shot} = require('./support/automations-fake.cjs');

const HOLD = {rule: 'sustained', since: '2026-10-29T09:15:00Z', held: 3, dropped: 0, task: 'task_hold'};

function held(fx) {
  const a = fx.automations.find(x => x.id === 'au_deploy');
  a.hold = {...HOLD};
}
const heldRuns = db => {
  const now = Date.now();
  db.runs.au_deploy = Array.from({length: 3}, (_, i) => ({id: 'run_h' + i, automation_id: 'au_deploy', version: 1,
    trigger_ref: 'webhook:trg_1:d' + i, started_at: new Date(now - i * 60000).toISOString(), finished_at: null, status: 'held',
    cost: 0, steps: []}));
};

(async () => {
  const browser = await chromium.launch();
  try {
    for (const viewport of [DESKTOP, PHONE]) {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport, tweak: held});
      heldRuns(db);
      await page.waitForSelector('tr.au-row[data-row="au_deploy"]');
      assert.match(await page.locator('tr.au-row[data-row="au_deploy"]').innerText(), /On hold/);
      await page.evaluate(() => { location.hash = '#/automations/au_deploy/runs'; });
      await page.waitForSelector('.au-hd .au-hold');
      assert.match(await page.locator('.au-hd .au-hold').innerText(), /On hold\s*3 held/);
      await page.waitForSelector('tr.au-rr');
      assert.deepEqual(await page.locator('tr.au-rr .au-ro').allInnerTexts(), ['Held', 'Held', 'Held']);
      assert.equal(await sideways(page), false);
      await shot(page, viewport === DESKTOP ? 'hold-desktop' : 'hold-phone');
      if (viewport === DESKTOP) {
        await page.locator('.au-hd [data-hold="release"]').click();
        await page.waitForFunction(() => !document.querySelector('.au-hd .au-hold'));
        assert.deepEqual(writes.at(-1), {method: 'POST', path: '/api/v2/automations/au_deploy/held', body: {action: 'release'}});
        await page.waitForFunction(() => !document.querySelector('tr.au-rr .au-ro')?.textContent.includes('Held'));
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Someone who may not change it sees the hold, not the buttons.
    {
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, viewer: 'sam', tweak: held, hash: '#/automations/au_deploy'});
      await page.waitForSelector('.au-hd .au-hold');
      assert.equal(await page.locator('.au-hd [data-hold]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations hold ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
