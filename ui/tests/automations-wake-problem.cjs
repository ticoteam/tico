// A "What wakes" automation whose wake rule names a step its type does not have (GET payload `problems`) says so in one
// warning line: on its row in the list and in its detail header. A clean automation shows no line. Against the fake hub in
// support/automations-fake.cjs; a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, DESKTOP, sideways} = require('./support/automations-fake.cjs');

(async () => {
  const browser = await chromium.launch();
  try {
    const problems = [{rule: 'rule_qa', step: 'QA Fixes', why: 'The wake rule names step "QA Fixes", which its task type does not have.'}];
    const tweak = fx => Object.assign(fx.automations.find(a => a.id === 'au_inbox'), {problems});
    const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, viewer: 'dana', tweak});
    await page.waitForSelector('tr.au-row[data-row="au_inbox"]');
    const line = '.au-warn';
    assert.equal(await page.locator(`tr.au-row[data-row="au_inbox"] ${line}`).count(), 1, 'the row warns');
    assert.match(await page.locator(`tr.au-row[data-row="au_inbox"] ${line}`).innerText(), /Names a step its type does not have: QA Fixes/);
    assert.equal(await page.locator('tr.au-row[data-row="au_tasks"] .au-warn').count(), 0, 'a clean automation does not');
    await page.evaluate(() => { location.hash = '#/automations/au_inbox'; });
    await page.waitForSelector('.au-hd .au-warn');
    assert.match(await page.locator('.au-hd .au-warn').innerText(), /QA Fixes/);
    assert.equal(await sideways(page), false);
    assert.deepEqual(errors, []);
    await page.close();
    console.log('automations wake problem ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
