// Automations (redesign, ui/app/automations*.js) against the fake hub in support/automations-fake.cts: the overview is one
// dense row per automation grouped by trigger, no switches, filters on the left (behind a Filters button on a phone), a
// 5m/1h/24h/7d range kept across visits, and a Runs tab of every run newest first. A row opens the flow with a "‹
// Automations" link back that keeps the filters, tab and range. Inside, the header switch turns it off, Runs lists dense
// rows, a tap opens a run's steps, More reads the next page; ⋯ deletes, but not a built-in.
import type {Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;

type Box = {x: number; y: number; width: number; height: number};

const rows = (page: Page) => page.locator('tr.au-row').evaluateAll(r => r.map(x => (x as HTMLElement).dataset.row));
const groups = (page: Page) => page.locator('tr.au-grp').evaluateAll(r => r.map(x => (x as HTMLElement).dataset.agrp));
const screen = (page: Page) => page.locator('#au-root').getAttribute('data-screen');

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the overview, its filters, then an automation's flow and runs, and back to the same filters.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE});
      await page.waitForSelector('tr.au-row');
      assert.equal(await screen(page), 'list');
      assert.deepEqual(await groups(page), ['message', 'task_received', 'webhook', 'event']);
      assert.equal(await page.locator('tr.au-row .au-sw').count(), 0, 'no switches in rows');
      assert.match(await page.locator('tr.au-row[data-row="au_price"]').innerText(), /Off/);
      for (const h of await page.locator('tr.au-row').evaluateAll(r => r.map(x => x.getBoundingClientRect().height))) assert.ok(h < 50, 'a row is one line');
      // The table scrolls sideways in its own box; the page does not.
      assert.ok(await page.locator('.au-tbl').evaluate(b => b.scrollWidth > b.clientWidth));
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-list');

      // The header: the title and New automation, then search and filters; no Ask (the tab bar has the Assistant, and
      // the list is below). Automations | Runs and the range are the bar's. The search is on the page and narrows the list.
      assert.equal(await page.locator('.au-ph h1').isVisible(), true);
      assert.equal(await page.locator('.au-ph [data-ask-open]').count(), 0, 'no Ask on Automations');
      assert.equal(await page.locator('.au-ctl .au-tabs [data-tab="list"]').isVisible(), true);
      await page.fill('.au-ph .au-q', 'deploy');
      assert.deepEqual(await rows(page), ['au_deploy']);
      await page.fill('.au-q', '');
      // Filters sit behind a button, in sections that start closed; the search is not repeated there.
      assert.equal(await page.locator('.au-rail').isVisible(), false);
      await page.getByRole('button', {name: 'Filters'}).click();
      await page.waitForSelector('.au-rail [data-fsec="trig"]', {state: 'visible'});
      assert.equal(await page.locator('.au-q').count(), 1, 'one search');
      assert.ok((await page.locator('.au-fh').evaluateAll(b => b.map(x => x.getAttribute('aria-expanded')))).every(x => x === 'false'));
      assert.equal(await page.locator('[data-ftrig]').count(), 0);
      await page.locator('[data-fsec="trig"]').click();
      await page.locator('[data-ftrig="message"]').click();
      assert.deepEqual(await rows(page), ['au_inbox']);
      assert.equal(await page.locator('[data-filters] .au-fbn').innerText(), '1');
      assert.equal(await page.locator('[data-fsec="trig"] .au-fhn').innerText(), '1');
      // Opened again, the section with a filter on starts open and the others closed.
      await page.locator('[data-filters]').click();
      await page.locator('[data-filters]').click();
      assert.equal(await page.locator('[data-fsec="trig"]').getAttribute('aria-expanded'), 'true');
      assert.equal(await page.locator('[data-fsec="status"]').getAttribute('aria-expanded'), 'false');
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
      assert.match(lines[1]!, /reply 82%/);
      assert.match(lines[2]!, /Reply/);
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
      await page.waitForFunction(() => (document.querySelector('#au-root') as HTMLElement).dataset.screen === 'list');
      assert.deepEqual(await rows(page), ['au_inbox']);
      assert.equal(await page.locator('[data-ftrig="message"]').getAttribute('aria-pressed'), 'true');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A small phone: the header's rows fit (title and New automation; search and filters), then the bar's tabs and range,
    // on the list and on Runs; New automation opens its menu, and nothing scrolls sideways.
    {
      const W = 320, {page, errors} = await openAutomations(browser, {viewport: {width: W, height: 640}});
      await page.waitForSelector('tr.au-row');
      const box = (sel: string) => page.locator(sel).boundingBox();
      for (const tab of ['list', 'runs']) {
        await page.locator(`[data-tab="${tab}"]`).click();
        const b: Record<string, Box | null> = {};
        for (const sel of ['.au-ph h1', '.au-tabs', '.au-range', '.au-q', '[data-filters]', '.au-new']) {
          b[sel] = await box(sel);
          assert.ok(b[sel] && b[sel]!.x >= 0 && b[sel]!.x + b[sel]!.width <= W, `${tab}: ${sel} fits`);
        }
        const mid = (x: Box) => x.y + x.height / 2;
        assert.ok(Math.abs(mid(b['.au-tabs']!) - mid(b['.au-range']!)) < 2, `${tab}: tabs and range share a row`);
        assert.ok(Math.abs(mid(b['.au-ph h1']!) - mid(b['.au-new']!)) < 2, `${tab}: the title and New automation share the header's first row`);
        assert.ok(Math.abs(mid(b['.au-q']!) - mid(b['[data-filters]']!)) < 2 && b['.au-q']!.y > b['.au-new']!.y && b['.au-q']!.y < b['.au-tabs']!.y,
          `${tab}: the search row under it, above the tabs`);
        assert.ok(b['.au-new']!.height >= 32 && b['[data-filters]']!.width >= 40, `${tab}: New automation and filters are full-size targets`);
        assert.equal(await sideways(page), false);
      }
      await page.locator('[data-tab="list"]').click();
      await page.getByRole('button', {name: 'New automation'}).click();
      assert.equal(await page.locator('.au-newm').isVisible(), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.au-newm').isVisible(), false);
      // Widened to a computer, a typed search moves back to the filters, and returns to the header on a phone.
      await page.fill('.au-q', 'deploy');
      await page.setViewportSize(DESKTOP);
      await page.waitForSelector('.au-rail .au-q', {state: 'visible'});
      assert.equal(await page.locator('.au-ctl .au-tabs').isVisible(), true);
      assert.equal(await page.locator('.au-q').inputValue(), 'deploy');
      assert.deepEqual(await rows(page), ['au_deploy']);
      await page.setViewportSize({width: W, height: 640});
      await page.waitForSelector('.au-ph .au-q', {state: 'visible'});
      assert.equal(await page.locator('.au-ctl .au-tabs').isVisible(), true);
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
      // The header is one row (title, New automation); search, the tabs, the range and every filter section show in place.
      assert.equal(await page.locator('.au-ph h1').isVisible(), true);
      assert.equal(await page.locator('.au-ph [data-ask-open]').count(), 0, 'no Ask on Automations');
      // New automation is in words, the header's filled button, 32px high; the range is on the Automations | Runs row.
      assert.equal((await page.locator('.au-ph > .ph-act .au-new').innerText()).trim(), 'New automation');
      assert.equal(await page.locator('.au-ph .au-range').count(), 0, 'the range left the header');
      const place = await page.evaluate(() => {
        const r = (s: string) => document.querySelector(s)!.getBoundingClientRect();
        const add = r('.au-new'), range = r('.au-ctl .au-range'), tabs = r('.au-ctl .au-tabs');
        return {h: add.height, mid: range.top + range.height / 2, tabsMid: tabs.top + tabs.height / 2, right: range.left > tabs.right};
      });
      assert.equal(place.h, 32, 'New automation is 32px high: ' + JSON.stringify(place));
      assert.ok(Math.abs(place.mid - place.tabsMid) < 2 && place.right, 'the range sits right of the tabs, on their row: ' + JSON.stringify(place));
      assert.equal(await page.locator('[data-filters]').isVisible(), false);
      assert.equal(await page.locator('.au-rail .au-q').isVisible(), true);
      assert.equal(await page.locator('.au-ctl .au-tabs').isVisible(), true);
      assert.equal(await page.locator('.au-fh').count(), 0);
      assert.equal(await page.locator('[data-ftrig="message"]').isVisible(), true);
      assert.equal(await page.locator('.au-side').isVisible(), false, 'no side panel until an editor opens');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '1h');
      const runsOf = (id: string) => page.locator(`tr.au-row[data-row="${id}"] td.au-n`).first();
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement).innerText === '4');
      assert.match(await page.locator('tr.au-row[data-row="au_inbox"]').innerText(), /now/, 'its last run');

      await page.locator('.au-range [data-range="24h"]').click();
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement).innerText === '30');
      await page.reload();
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '24h', 'the range is kept');
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement | null)?.innerText === '30');
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
      await page.waitForFunction(() => !(document.querySelector('.au-hd [data-on-cur]') as HTMLInputElement).checked);
      assert.deepEqual(writes.at(-1)!, {method: 'PATCH', path: '/api/v2/automations/au_inbox', body: {enabled: false}});

      // A slow answer for an automation already left (an answer or a failure) never lands on the one now open.
      for (const fail of [false, true]) {
        const release = hold('au_deploy', fail);
        await page.evaluate(() => { location.hash = '#/automations/au_deploy'; });
        await page.evaluate(() => { location.hash = '#/automations/au_price'; });
        await page.waitForSelector('.au-hd .au-name:text("Pricing mentions")');
        await page.waitForSelector('.au-card[data-open="n1"]');
        release();
        await page.waitForTimeout(300);
        assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Pricing mentions');
        assert.match(await page.locator('.au-trig').innerText(), /Urgent insight/);
        assert.equal(await page.locator('.au-pane .err').count(), 0);
        assert.match(page.url(), /au_price$/);
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
      assert.notEqual(writes.at(-1)!.path, '/api/v2/automations/au_tasks');

      // Any other: ⋯ Delete, once confirmed, goes and leaves the list.
      await page.evaluate(() => { location.hash = '#/automations/au_deploy'; });
      await page.waitForSelector('.au-hd .au-name:text("Deploy watch")');
      await page.locator('[data-more]').click();
      page.once('dialog', d => d.accept());
      await page.locator('.au-menu [data-mi="delete"]').click();
      await page.waitForFunction(() => (document.querySelector('#au-root') as HTMLElement).dataset.screen === 'list');
      await page.waitForSelector('tr.au-row[data-row="au_inbox"]');
      assert.equal(await page.locator('tr.au-row[data-row="au_deploy"]').count(), 0);
      assert.deepEqual(writes.at(-1)!, {method: 'DELETE', path: '/api/v2/automations/au_deploy', body: {}});
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations list and runs ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
