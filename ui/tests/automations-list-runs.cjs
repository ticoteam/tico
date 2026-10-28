// Automations (redesign, ui/app/automations*.js) against the fake hub in support/automations-fake.cjs: the list groups
// Mine / a group / Company with one line per automation and a search; a row opens the flow; a switch turns an
// automation off (PATCH enabled); Runs lists dense rows, a tap opens a run's steps, More reads the next page; ⋯ deletes,
// but not a built-in. A phone first (one screen at a time), then a computer (list, flow and side together).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const groups = page => page.locator('.au-gh > span:first-child').allTextContents();

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the list, a search, a switch, then an automation's flow and its runs.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE});
      await page.waitForSelector('.au-row');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'list');
      assert.deepEqual(await groups(page), ['Mine', 'Engineering', 'Company']);
      for (const h of await page.locator('.au-row').evaluateAll(rows => rows.map(r => r.getBoundingClientRect().height))) assert.ok(h < 50, 'a row is one line');
      // A viewer may not change "Pricing mentions": it says Off, with no switch.
      const listen = page.locator('.au-row[data-row="au_listen"]');
      assert.equal(await listen.locator('.au-sw').count(), 0);
      assert.match(await listen.innerText(), /Off/);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-list');

      await page.fill('.au-q', 'deploy');
      assert.deepEqual(await page.locator('.au-row').evaluateAll(rows => rows.map(r => r.dataset.row)), ['au_deploy']);
      await page.fill('.au-q', '');

      // Off from the list: a PATCH of enabled only, and the row dims.
      await page.locator('.au-row[data-row="au_deploy"] .au-sw').click();
      await page.waitForFunction(() => document.querySelector('.au-row[data-row="au_deploy"]').classList.contains('off'));
      assert.deepEqual(writes.at(-1), {method: 'PATCH', path: '/api/v2/automations/au_deploy', body: {enabled: false}});

      await page.locator('.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'detail');
      assert.equal(await page.locator('.au-list').isVisible(), false);
      assert.match(await page.locator('.au-trig').innerText(), /Message received[\s\S]*dana@acme\.example/);
      assert.equal(await page.locator('.au-ver').innerText(), 'v7');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-flow');

      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('.au-rr');
      assert.equal(await page.locator('.au-rr').count(), 25);
      assert.equal(await page.locator('.au-rr .au-dot.fail').count() > 0, true);
      assert.equal(await page.locator('.au-rr .au-dot.wait').count() > 0, true);
      for (const h of await page.locator('.au-rr').evaluateAll(rows => rows.map(r => r.getBoundingClientRect().height))) assert.ok(h < 50, 'a run is one line');
      const outcomes = await page.locator('.au-rr .au-ro').allTextContents();
      assert.ok(outcomes.includes('Spend cap') && outcomes.includes('LLM failed'));
      assert.ok(outcomes.every(o => o.split(' ').length <= 3), 'outcomes are one to three words');
      const first = page.locator('.au-rr').first();
      assert.match(await first.innerText(), /Draft reply[\s\S]*\$0\.03/);
      await first.click();
      await page.waitForSelector('.au-rx .au-sl');
      assert.equal(await first.getAttribute('aria-expanded'), 'true');
      const lines = await page.locator('.au-rx .au-sl').allInnerTexts();
      assert.equal(lines.length, 5);
      assert.match(lines[1], /reply 82%/);
      assert.match(lines[2], /Reply/);
      await shot(page, 'phone-runs');
      await first.click();
      assert.equal(await page.locator('.au-rx').count(), 0);
      await page.locator('[data-runs-more]').click();
      await page.waitForFunction(() => document.querySelectorAll('.au-rr').length === 30);
      assert.equal(await page.locator('[data-runs-more]').count(), 0);
      assert.ok(writes.every(w => w.method === 'PATCH'), 'paging reads only');
      assert.equal(await sideways(page), false);

      // Back goes to the list.
      await page.locator('.au-hd .au-back').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'list');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer: the first automation opens beside the list; the header switch; ⋯ delete, refused for a built-in.
    {
      const {page, errors, writes, hold} = await openAutomations(browser, {viewport: DESKTOP});
      await page.waitForURL(/#\/automations\/au_inbox$/);
      await page.waitForSelector('.au-card[data-open="trigger"]');
      assert.equal(await page.locator('.au-list').isVisible(), true);
      assert.equal(await page.locator('.au-botp').isVisible(), true);
      assert.equal(await page.locator('.au-row.sel').getAttribute('data-row'), 'au_inbox');
      await shot(page, 'desktop-flow');

      await page.locator('.au-hd [data-on-cur]').click();
      await page.waitForFunction(() => document.querySelector('.au-row[data-row="au_inbox"]').classList.contains('off'));
      assert.deepEqual(writes.at(-1).body, {enabled: false});

      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('.au-rr');
      await page.locator('.au-rr').nth(4).click();
      assert.match((await page.locator('.au-rx .au-sl').allInnerTexts()).join('\n'), /failed · model timed out/);
      await shot(page, 'desktop-runs');

      // A slow answer for an automation already left (an answer or a failure) never lands on the one now open.
      for (const fail of [false, true]) {
        const release = hold('au_deploy', fail);
        await page.locator('.au-row[data-row="au_deploy"] a').click();
        await page.locator('.au-row[data-row="au_listen"] a').click();
        await page.waitForSelector('.au-hd .au-name:text("Pricing mentions")');
        await page.waitForSelector('.au-card[data-open="n1"]');
        release();
        await page.waitForTimeout(300);
        assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Pricing mentions');
        assert.match(await page.locator('.au-trig').innerText(), /Listening match/);
        assert.equal(await page.locator('.au-pane .err').count(), 0);
        assert.match(page.url(), /au_listen$/);
      }

      // A built-in: Delete is shown off; turning it off asks first, and a No leaves it on.
      await page.locator('.au-row[data-row="au_tasks"] a').click();
      await page.waitForSelector('.au-hd .au-name:text("Task triage")');
      await page.locator('[data-more]').click();
      assert.equal(await page.locator('.au-menu [data-mi="delete"]').isDisabled(), true);
      await page.keyboard.press('Escape');
      page.once('dialog', d => d.dismiss());
      await page.locator('.au-hd [data-on-cur]').click();
      assert.equal(await page.locator('.au-hd [data-on-cur]').isChecked(), true);
      assert.notEqual(writes.at(-1).path, '/api/v2/automations/au_tasks');

      // Any other: ⋯ Delete, once confirmed, goes and leaves the list.
      await page.locator('.au-row[data-row="au_deploy"] a').click();
      await page.waitForSelector('.au-hd .au-name:text("Deploy watch")');
      await page.locator('[data-more]').click();
      page.once('dialog', d => d.accept());
      await page.locator('.au-menu [data-mi="delete"]').click();
      await page.waitForFunction(() => !document.querySelector('.au-row[data-row="au_deploy"]'));
      assert.deepEqual(writes.at(-1), {method: 'DELETE', path: '/api/v2/automations/au_deploy', body: {}});
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations list and runs ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
