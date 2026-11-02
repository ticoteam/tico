// Automations (redesign, ui/app/automations*.js) against the fake hub in support/automations-fake.cjs. The page opens on
// the overview: one dense row per automation grouped by trigger, no switches, filters on the left (behind a Filters
// button on a phone), a 5m/1h/24h/7d range kept across visits, and a Runs tab of every run newest first. A row opens
// the flow with a "‹ Automations" link back that keeps the filters, tab and range. Inside, the header switch turns it
// off, Runs lists dense rows, a tap opens a run's steps, More reads the next page; ⋯ deletes, but not a built-in.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const rows = page => page.locator('tr.au-row').evaluateAll(r => r.map(x => x.dataset.row));
const groups = page => page.locator('tr.au-grp').evaluateAll(r => r.map(x => x.dataset.agrp));
const screen = page => page.locator('#au-root').getAttribute('data-screen');

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the overview, its filters, then an automation's flow and runs, and back to the same filters.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE});
      await page.waitForSelector('tr.au-row');
      assert.equal(await screen(page), 'list');
      assert.deepEqual(await groups(page), ['message', 'task_received', 'webhook', 'listening']);
      assert.equal(await page.locator('tr.au-row .au-sw').count(), 0, 'no switches in rows');
      assert.match(await page.locator('tr.au-row[data-row="au_listen"]').innerText(), /Off/);
      for (const h of await page.locator('tr.au-row').evaluateAll(r => r.map(x => x.getBoundingClientRect().height))) assert.ok(h < 50, 'a row is one line');
      // The table scrolls sideways in its own box; the page does not.
      assert.ok(await page.locator('.au-tbl').evaluate(b => b.scrollWidth > b.clientWidth));
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-list');

      // Filters sit behind a button; a trigger narrows the list, and so does the search.
      assert.equal(await page.locator('.au-rail').isVisible(), false);
      await page.locator('[data-filters]').click();
      await page.waitForSelector('.au-rail .au-q', {state: 'visible'});
      await page.fill('.au-q', 'deploy');
      assert.deepEqual(await rows(page), ['au_deploy']);
      await page.fill('.au-q', '');
      await page.locator('[data-ftrig="message"]').click();
      assert.deepEqual(await rows(page), ['au_inbox']);
      assert.match(await page.locator('[data-filters]').innerText(), /Filters · 1/);
      await page.locator('[data-filters]').click();

      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
      assert.equal(await screen(page), 'detail');
      assert.equal(await page.locator('.au-tbl').isVisible(), false);
      assert.equal(await page.locator('.au-crumb .au-up').isVisible(), true);
      assert.match(await page.locator('.au-trig').innerText(), /Message received[\s\S]*dana@acme\.example/);
      assert.equal(await page.locator('.au-ver').innerText(), 'v7');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-flow');

      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('.au-rr');
      assert.equal(await page.locator('.au-rr').count(), 25);
      assert.equal(await page.locator('.au-rr .au-dot.fail').count() > 0, true);
      assert.equal(await page.locator('.au-rr .au-dot.wait').count() > 0, true);
      for (const h of await page.locator('.au-rr').evaluateAll(r => r.map(x => x.getBoundingClientRect().height))) assert.ok(h < 50, 'a run is one line');
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
      assert.deepEqual(writes, [], 'reads only');
      assert.equal(await sideways(page), false);

      // Back goes to the list, still filtered.
      await page.locator('.au-crumb .au-up').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'list');
      assert.deepEqual(await rows(page), ['au_inbox']);
      assert.equal(await page.locator('[data-ftrig="message"]').getAttribute('aria-pressed'), 'true');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer: the overview stays put, the range is kept, Runs lists every run, and a run opens its automation.
    {
      const {page, errors, writes, hold} = await openAutomations(browser, {viewport: DESKTOP});
      await page.waitForSelector('tr.au-row');
      await page.waitForTimeout(200);
      assert.match(page.url(), /#\/automations$/, 'no automation opens by itself');
      assert.equal(await page.locator('.au-rail').isVisible(), true);
      assert.equal(await page.locator('.au-side').isVisible(), false, 'no side panel until an editor opens');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '1h');
      const runsOf = id => page.locator(`tr.au-row[data-row="${id}"] td.au-n`).first();
      await page.waitForFunction(() => document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n').innerText === '4');
      assert.match(await page.locator('tr.au-row[data-row="au_inbox"]').innerText(), /now/, 'its last run');

      await page.locator('.au-range [data-range="24h"]').click();
      await page.waitForFunction(() => document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n').innerText === '30');
      await page.reload();
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '24h', 'the range is kept');
      await page.waitForFunction(() => document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n')?.innerText === '30');
      assert.equal(await runsOf('au_deploy').innerText(), '0');

      // Sort by most failures puts the one with failures first.
      await page.locator('[data-group]').selectOption('none');
      await page.locator('[data-sort]').selectOption('fail');
      assert.equal((await rows(page))[0], 'au_inbox');
      assert.equal(await page.locator('tr.au-grp').count(), 0);
      await shot(page, 'desktop-list');

      await page.locator('[data-tab="runs"]').click();
      await page.waitForSelector('tr.au-arow');
      assert.equal(await page.locator('tr.au-arow').count(), 30);
      assert.match(await page.locator('.au-chart').innerText(), /3 failed/);
      await page.locator('[data-rstatus]').selectOption('failed');
      await page.waitForFunction(() => document.querySelectorAll('tr.au-arow').length === 3);
      await shot(page, 'desktop-all-runs');
      const run = await page.locator('tr.au-arow').first().getAttribute('data-arun');
      await page.locator('tr.au-arow').first().click();
      await page.waitForURL(/#\/automations\/au_inbox\/runs$/);
      await page.waitForSelector(`.au-rr[aria-expanded="true"]`);
      assert.match(await page.locator('.au-rx').innerText(), /model timed out/, `run ${run} opens`);
      await page.locator('.au-crumb .au-up').click();
      await page.waitForSelector('tr.au-arow');
      assert.equal(await page.locator('[data-tab="runs"]').getAttribute('aria-selected'), 'true', 'the tab is kept');
      assert.equal(await page.locator('[data-rstatus]').inputValue(), 'failed');
      assert.equal(await page.locator('tr.au-arow').count(), 3);
      await page.locator('[data-tab="list"]').click();

      // The header switch.
      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
      await shot(page, 'desktop-flow');
      await page.locator('.au-hd [data-on-cur]').click();
      await page.waitForFunction(() => !document.querySelector('.au-hd [data-on-cur]').checked);
      assert.deepEqual(writes.at(-1), {method: 'PATCH', path: '/api/v2/automations/au_inbox', body: {enabled: false}});

      // A slow answer for an automation already left (an answer or a failure) never lands on the one now open.
      for (const fail of [false, true]) {
        const release = hold('au_deploy', fail);
        await page.evaluate(() => { location.hash = '#/automations/au_deploy'; });
        await page.evaluate(() => { location.hash = '#/automations/au_listen'; });
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
      await page.evaluate(() => { location.hash = '#/automations/au_tasks'; });
      await page.waitForSelector('.au-hd .au-name:text("Task triage")');
      await page.locator('[data-more]').click();
      assert.equal(await page.locator('.au-menu [data-mi="delete"]').isDisabled(), true);
      await page.keyboard.press('Escape');
      page.once('dialog', d => d.dismiss());
      await page.locator('.au-hd [data-on-cur]').click();
      assert.equal(await page.locator('.au-hd [data-on-cur]').isChecked(), true);
      assert.notEqual(writes.at(-1).path, '/api/v2/automations/au_tasks');

      // Any other: ⋯ Delete, once confirmed, goes and leaves the list.
      await page.evaluate(() => { location.hash = '#/automations/au_deploy'; });
      await page.waitForSelector('.au-hd .au-name:text("Deploy watch")');
      await page.locator('[data-more]').click();
      page.once('dialog', d => d.accept());
      await page.locator('.au-menu [data-mi="delete"]').click();
      await page.waitForFunction(() => document.querySelector('#au-root').dataset.screen === 'list');
      await page.waitForSelector('tr.au-row[data-row="au_inbox"]');
      assert.equal(await page.locator('tr.au-row[data-row="au_deploy"]').count(), 0);
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
