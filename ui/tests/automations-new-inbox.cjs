// New inbox (ui/app/automations-new.js) against the fake hub in support/automations-fake.cjs: + New → New inbox on a
// person's email inbox offers Person and Bot only (a channel adds Group and Other); a refused template fill shows in
// the dialog and saves nothing; Create POSTs the filled template's steps and destinations with Turn on now as
// switched, then opens the new automation as a board; the dialog fits a phone with no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, filledTemplate, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const kinds = page => page.locator('.au-nd [data-kind]').evaluateAll(els => els.map(e => e.dataset.kind));
async function openDialog(page) {
  await page.locator('.au-new').click();
  await page.locator('.au-newm [data-new="inbox"]').click();
  await page.waitForSelector('dialog.au-nd-new[open] [data-tid="founder"]');
}

(async () => {
  const browser = await chromium.launch();
  try {
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP});
      await page.waitForSelector('tr.au-row');
      await openDialog(page);
      // The viewer's own address, as their own: an email inbox is a person's or a bot's.
      assert.equal(await page.locator('.au-nd [data-n="inbox"]').inputValue(), 'dana@acme.example');
      assert.deepEqual(await kinds(page), ['person', 'bot']);
      assert.equal(await page.locator('.au-nd [data-kind="person"]').getAttribute('aria-checked'), 'true');
      assert.equal(await page.locator('.au-nd [data-n="person"]').inputValue(), 'person:dana');
      assert.equal(await page.locator('.au-nd [data-tid]').count(), 4);
      assert.equal(await page.locator('.au-nd [data-tid="founder"]').getAttribute('aria-checked'), 'true');
      assert.equal(await page.locator('.au-nd [data-n="on"]').isChecked(), false, 'off unless turned on');
      await shot(page, 'new-inbox-desktop');

      // A channel adds Group and Other; back on an address they go, and a Group pick falls back to Person.
      await page.fill('.au-nd [data-n="inbox"]', 'slack:#support');
      assert.deepEqual(await kinds(page), ['person', 'bot', 'group', 'other']);
      await page.locator('.au-nd [data-kind="group"]').click();
      assert.equal(await page.locator('.au-nd [data-n="group"]').inputValue(), 'group:engineering');
      await page.locator('.au-nd [data-kind="other"]').click();
      assert.equal(await page.locator('.au-nd select').count(), 0, 'Other is the creator’s: nothing to pick');
      await page.fill('.au-nd [data-n="inbox"]', 'dana@acme.example');
      assert.deepEqual(await kinds(page), ['person', 'bot']);
      assert.equal(await page.locator('.au-nd [data-kind="person"]').getAttribute('aria-checked'), 'true');

      // Someone else's inbox: the hub refuses the fill, the dialog says so and nothing is saved.
      await page.selectOption('.au-nd [data-n="person"]', 'person:omar');
      await page.locator('.au-nd [data-go]').click();
      await page.waitForSelector('.au-nd [data-error]:not([hidden])');
      assert.match(await page.locator('.au-nd [data-error]').innerText(), /may not make an automation for person:omar/);
      assert.equal(writes.length, 0, 'a refused fill saves nothing');
      assert.equal(await page.locator('dialog.au-nd-new[open]').count(), 1, 'the dialog stays open');

      // Dana's own, Founder, on now: the POST is the filled template, switched on; the new one opens as a board.
      await page.selectOption('.au-nd [data-n="person"]', 'person:dana');
      await page.locator('.au-nd [data-n="on"]').check();
      await page.locator('.au-nd [data-go]').click();
      await page.waitForSelector('.au-board');
      assert.equal(await page.locator('dialog.au-nd').count(), 0);
      const posts = writes.filter(w => w.method === 'POST' && w.path === '/api/v2/automations');
      assert.equal(posts.length, 1);
      const filled = filledTemplate('founder', 'person:dana', 'dana@acme.example');
      assert.equal(posts[0].body.enabled, true);
      assert.equal(posts[0].body.owner, 'person:dana');
      assert.deepEqual(posts[0].body.trigger, {type: 'message', inbox: 'dana@acme.example'});
      assert.deepEqual(posts[0].body.steps, filled.steps);
      assert.deepEqual(posts[0].body.destinations, filled.destinations);
      assert.match(await page.evaluate(() => location.hash), /^#\/automations\/au_\d+$/);
      assert.equal(await page.locator('.au-dest[data-board^="dest:"]:not(.au-dest-add)').count(), 4);
      assert.equal(await page.locator('[data-bmode="draft"]').getAttribute('aria-pressed'), 'true');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Turn on now left off saves it off.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP});
      await page.waitForSelector('tr.au-row');
      await openDialog(page);
      await page.locator('.au-nd [data-tid="blank"]').click();
      await page.locator('.au-nd [data-go]').click();
      await page.waitForSelector('.au-board');
      const post = writes.find(w => w.method === 'POST');
      assert.equal(post.body.enabled, false);
      assert.equal(post.body.name, 'Blank');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: the dialog is a sheet that fits, nothing scrolls sideways.
    {
      const {page, errors} = await openAutomations(browser, {viewport: PHONE});
      await page.waitForSelector('tr.au-row');
      await openDialog(page);
      const box = await page.locator('dialog.au-nd').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1, 'the dialog fits the phone');
      assert.ok(await page.locator('.au-nd [data-go]').isVisible());
      assert.equal(await sideways(page), false);
      const tp = await page.locator('.au-nd [data-tid="founder"]').boundingBox();
      assert.ok(tp.height >= 44, 'a template is a full-size tap target');
      await shot(page, 'new-inbox-phone');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations-new-inbox: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
