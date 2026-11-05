// Automations without a bot beside them (ui/app/automations*.js) against the fake hub in support/automations-fake.cjs.
// A bot's proposal (a pending version) shows "Proposed" on the version chip and waits in Versions: View diff, Discard
// (DELETE), Turn on (restore). A draft (off, its first version pending) shows Pending in the list, and Turn on turns it
// on. + New makes a blank one, off, at once; Ask beside it opens the Ask panel. An old #/automations/bot link opens
// the list. Computer, then phone.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

(async () => {
  const browser = await chromium.launch();
  try {
    // A computer: the proposal in Versions, its diff, Discard.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations', proposal: true});
      await page.waitForSelector('tr.au-row[data-row="au_inbox"] .pill:text-is("Pending")');
      assert.equal(await page.locator('.au-side').isVisible(), false, 'no side panel on the list');
      assert.equal(await page.locator('.au-ph [data-ask-open]').getAttribute('aria-label'), 'Ask');
      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="f1"]');
      assert.equal(await page.locator('.au-ver').innerText(), 'v7 · Proposed');
      await page.locator('.au-ver').click();
      const card = page.locator('.au-prop[data-n="8"]');
      await card.waitFor();
      assert.match(await card.innerText(), /v8[\s\S]*Proposed[\s\S]*Archive receipts before triage/);
      await card.getByRole('button', {name: 'View diff'}).click();
      assert.deepEqual(await card.locator('.au-diff .add').allTextContents(), ['+ filter subject has “receipt” → Archive']);
      assert.equal(await card.locator('.au-diff .del').count(), 0);
      assert.ok((await page.locator('.au-side').boundingBox()).x > 1000, 'Versions sits on the right');
      await shot(page, 'desktop-proposal');
      await card.getByRole('button', {name: 'Discard'}).click();
      await card.waitFor({state: 'detached'});
      assert.deepEqual(writes.at(-1), {method: 'DELETE', path: '/api/v2/automations/au_inbox/versions/8', body: {}});
      assert.equal(await page.locator('.au-ver').innerText(), 'v7');
      await page.locator('.au-ed [data-op="close"]').click();
      assert.equal(await page.locator('.au-side').isVisible(), false);
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A draft (a bot's: off, version 1 pending): Pending and Off in the list; Turn on enables it and clears Pending.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, draft: true});
      const row = page.locator('tr.au-row[data-row="au_draft"]');
      await row.waitFor();
      assert.equal(await row.locator('.pill:text-is("Pending")').count(), 1);
      assert.equal(await row.locator('.pill:text-is("Off")').count(), 1, 'a draft is off');
      await row.locator('a').click();
      await page.waitForSelector('.au-card[data-open="f1"]');
      await page.locator('.au-ver').click();
      const card = page.locator('.au-prop[data-n="1"]');
      await card.waitFor();
      await card.getByRole('button', {name: 'Turn on'}).click();
      await card.waitFor({state: 'detached'});
      assert.deepEqual(writes.at(-1), {method: 'POST', path: '/api/v2/automations/au_draft/versions/1/restore', body: {}});
      assert.equal(await page.locator('.au-hd [data-on-cur]').isChecked(), true);
      await page.locator('.au-crumb .au-up').click();
      await page.waitForSelector('tr.au-row[data-row="au_draft"]');
      assert.equal(await row.locator('.pill').count(), 0, 'no Pending, no Off');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A phone: an old #/automations/bot link opens the list; + New, On a schedule makes an empty one, off, and its header turns it on.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, hash: '#/automations/bot'});
      await page.waitForSelector('tr.au-row[data-row="au_inbox"]');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'list');
      await page.getByRole('button', {name: '+ New'}).click();
      await page.locator('.au-newm [data-new="schedule"]').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'detail' && /New automation/.test(document.querySelector('.au-name')?.textContent));
      const made = writes.filter(w => w.method === 'POST' && w.path === '/api/v2/automations');
      assert.equal(made.length, 1);
      assert.equal(made[0].body.enabled, false);
      const sw = page.locator('.au-hd [data-on-cur]');
      assert.equal(await sw.isVisible(), true, 'the switch shows on a phone');
      assert.equal(await sw.isChecked(), false);
      await sw.click();
      await page.waitForFunction(() => document.querySelector('.au-hd [data-on-cur]')?.checked);
      assert.deepEqual([writes.at(-1).method, writes.at(-1).body], ['PATCH', {enabled: true}]);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-new');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations proposals ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
