// Automations (redesign, ui/app/automations*.js) against the fake hub in support/automations-fake.cts: the overview is one
// dense row per automation grouped by trigger (a table on a computer; on a phone a row each, its name over its facts),
// no switches, filters on the left (behind a Filters button on a phone), a 5m/1h/24h/7d range kept across visits (in
// the header; in ⋯ on a phone), Group by and Sort by in ⋯, and a Runs tab of every run newest first, its status a
// filter in the rail. Automations | Runs are the header's tabs. A row opens the flow with a "‹ Automations" link back
// that keeps the filters, tab and range. Inside, the header switch turns it off, Runs lists dense rows, a tap opens a
// run's steps, More reads the next page; ⋯ deletes, but not a built-in.
import type {Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
// An open automation's ⋯: the page header's on a computer (its items join it there), the automation's own on a phone.
const auMoreOpen = async (page: Page) => { const own = page.locator('.au-hd-more'); if (await own.isVisible()) await own.click(); else await page.locator('.au-ph > .ph-more').click(); };
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;

type Box = {x: number; y: number; width: number; height: number};
// The page's own helpers, read inside page.evaluate (ui/app/format.ts, ui/app/automations.ts).
declare function cronWords(cron: string): string;
declare function auCap(s: unknown): string;

const rows = (page: Page) => page.locator('.au-tbl .au-row').evaluateAll(r => r.map(x => (x as HTMLElement).dataset.row));
const groups = (page: Page) => page.locator('.au-tbl .au-grp').evaluateAll(r => r.map(x => (x as HTMLElement).dataset.agrp));
// ⋯ in the header: open it and pick an item (it closes on a pick).
const more = async (page: Page, sel: string) => { await page.locator('.au-ph > .ph-more').click(); await page.locator(`.au-ph > .ph-menu ${sel}`).click(); };
const screen = (page: Page) => page.locator('#au-root').getAttribute('data-screen');

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the overview, its filters, then an automation's flow and runs, and back to the same filters.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE});
      await page.waitForSelector('.au-row');
      assert.equal(await screen(page), 'list');
      assert.deepEqual(await groups(page), ['message', 'task_received', 'webhook', 'event']);
      assert.equal(await page.locator('.au-row .au-sw').count(), 0, 'no switches in rows');
      assert.match(await page.locator('.au-row[data-row="au_price"]').innerText(), /Off/);
      // A table does not fit a phone: each automation is a row, its name over its facts, and nothing scrolls sideways.
      assert.equal(await page.locator('.au-tbl-t').count(), 0, 'no table on a phone');
      for (const r of await page.locator('.au-tbl .au-r').evaluateAll(r => r.map(x => {
        const one = x.querySelector('.au-r1')!.getBoundingClientRect(), two = x.querySelector('.au-r2')?.getBoundingClientRect();
        return {h: x.getBoundingClientRect().height, under: !two || two.top >= one.bottom - 1};
      }))) { assert.ok(r.h >= 44 && r.h < 90, 'a row is a 44px target of two lines at most: ' + r.h); assert.ok(r.under, 'the facts sit under the name'); }
      assert.match(await page.locator('.au-row[data-row="au_inbox"] .au-r2').innerText(), /dana@acme\.example/, 'the trigger is a fact');
      assert.equal(await page.locator('.au-tbl').evaluate(b => b.scrollWidth > b.clientWidth), false);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-list');

      // The header: the title and New automation, then search and filters; no Ask (the tab bar has the Assistant, and
      // the list is below). Automations | Runs and the range are the bar's. The search is on the page and narrows the list.
      assert.equal(await page.locator('.au-ph h1').isVisible(), true);
      assert.equal(await page.locator('.au-ph [data-ask-open]').count(), 0, 'no Ask on Automations');
      assert.equal(await page.locator('.au-ph .au-tabs [data-tab="list"]').isVisible(), true);
      assert.equal(await page.locator('.au-ph .au-range').isVisible(), false, 'a phone picks the range in ⋯');
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

      await page.locator('.au-row[data-row="au_inbox"]').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
      assert.equal(await screen(page), 'detail');
      assert.equal(await page.locator('.au-tbl').isVisible(), false);
      assert.equal(await page.locator('.au-crumb .au-up').isVisible(), true);
      assert.match(await page.locator('.au-trig').innerText(), /Message received[\s\S]*dana@acme\.example/);
      assert.equal(await page.locator('.au-ver').innerText(), 'v7');
      assert.equal(await sideways(page), false);
      // The "?" keeps its 44px hit area (.tap-mark) off the spend chip beside it: the chip's own right edge is the chip's.
      // (while the route's fade runs the pointer goes to the root element: wait until it hits the page)
      await page.waitForFunction(() => { const r = document.querySelector('.au-hd [data-spend]')!.getBoundingClientRect(); return document.elementFromPoint(r.left + 2, r.top + r.height / 2) !== document.documentElement; });
      const edge = await page.locator('.au-hd [data-spend]').evaluate(chip => {
        const r = chip.getBoundingClientRect(), hit = document.elementFromPoint(r.right - 1, r.top + r.height / 2);
        return !!hit && (hit === chip || chip.contains(hit)) ? true : (hit?.outerHTML || '').slice(0, 120);
      });
      assert.equal(edge, true, 'the chip, not the help, at its right edge: ' + edge);
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

    // A small phone: the header's rows fit (title, New automation and ⋯; search and filters; the tabs), on the list and
    // on Runs; the range is picked in ⋯ and kept; New automation opens its menu, and nothing scrolls sideways.
    {
      const W = 320, {page, errors} = await openAutomations(browser, {viewport: {width: W, height: 640}});
      await page.waitForSelector('.au-row');
      const box = (sel: string) => page.locator(sel).boundingBox();
      for (const tab of ['list', 'runs']) {
        await page.locator(`.au-ph [data-tab="${tab}"]`).click();
        const b: Record<string, Box | null> = {};
        for (const sel of ['.au-ph h1', '.au-ph .au-tabs', '.au-q', '[data-filters]', '.au-new', '.au-ph > .ph-more']) {
          b[sel] = await box(sel);
          assert.ok(b[sel] && b[sel]!.x >= 0 && b[sel]!.x + b[sel]!.width <= W, `${tab}: ${sel} fits`);
        }
        const mid = (x: Box) => x.y + x.height / 2;
        assert.ok(Math.abs(mid(b['.au-ph h1']!) - mid(b['.au-new']!)) < 2, `${tab}: the title and New automation share the header's first row`);
        assert.ok(Math.abs(mid(b['.au-q']!) - mid(b['[data-filters]']!)) < 2 && b['.au-q']!.y > b['.au-new']!.y && b['.au-q']!.y < b['.au-ph .au-tabs']!.y,
          `${tab}: the search row under it, above the tabs`);
        assert.ok(b['.au-new']!.height >= 44 && b['[data-filters]']!.width >= 44 && b['.au-ph > .ph-more']!.height >= 44, `${tab}: 44px targets`);
        // Runs too are rows, the automation over when, its outcome and the rest; no table.
        if (tab === 'runs') {
          await page.waitForSelector('.au-tbl .au-arow');
          assert.equal(await page.locator('.au-tbl table').count(), 0, 'no runs table on a phone');
          assert.ok(await page.locator('.au-tbl .au-arow .au-r2').count() > 0);
          assert.equal((await page.locator('.au-tbl .au-arow .pill').allInnerTexts()).includes('Done'), false, 'no Done pill on a phone');
          const facts = (await page.locator('.au-tbl .au-arow .au-r2 > span').allInnerTexts()).join('\n');   // one fact a line
          assert.match(facts, /^\d+ steps?$/m, 'a run says its steps: ' + facts.slice(0, 300));
          assert.match(facts, /^\d+(s|m|h)$/m, 'and its time');
        }
        assert.equal(await sideways(page), false);
      }
      // The range in ⋯: picked there, marked there and kept.
      await more(page, '.au-mrange [data-range="24h"]');
      await page.locator('.au-ph > .ph-more').click();
      assert.equal(await page.locator('.au-mrange [data-range="24h"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.au-mrange [data-range="24h"]').evaluate(b => b.getBoundingClientRect().height >= 44), true, 'a 44px item');
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => localStorage.getItem('tico.automations.range')), '24h');
      await more(page, '.au-mrange [data-range="1h"]');
      await page.locator('.au-ph [data-tab="list"]').click();
      await page.getByRole('button', {name: 'New automation'}).click();
      assert.equal(await page.locator('.au-newm').isVisible(), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.au-newm').isVisible(), false);
      // Widened to a computer, a typed search moves back to the filters, and returns to the header on a phone.
      await page.fill('.au-q', 'deploy');
      await page.setViewportSize(DESKTOP);
      await page.waitForSelector('.au-rail .au-q', {state: 'visible'});
      assert.equal(await page.locator('.au-ph .au-tabs').isVisible(), true);
      assert.equal(await page.locator('.au-ph .au-range').isVisible(), true, 'a computer has the range in the header');
      assert.equal(await page.locator('.au-q').inputValue(), 'deploy');
      assert.deepEqual(await rows(page), ['au_deploy']);
      await page.setViewportSize({width: W, height: 640});
      await page.waitForSelector('.au-ph .au-q', {state: 'visible'});
      assert.equal(await page.locator('.au-ph .au-tabs').isVisible(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone whose counts cannot be read says so under the rows (never a list that only lacks its numbers).
    {
      const {page, errors} = await openAutomations(browser, {viewport: PHONE,
        routes: async p => { await p.route('**/api/v2/automations/stats*', r => r.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: 'Counts are down'})})); }});
      await page.waitForSelector('.au-row');
      await page.waitForSelector('.au-tbl .err');
      assert.match(await page.locator('.au-tbl .err').innerText(), /Counts are down/);
      assert.ok(await page.locator('.au-tbl .au-r').count() > 0, 'the rows still show');
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
      // One header line (style guide, "Top bars: one line"): the title, the Automations | Runs tabs, the range, the quiet +
      // (named for a screen reader, 32px high) and ⋯ last; no second bar under it.
      assert.equal(await page.locator('.au-ph > .ph-act .au-new').getAttribute('aria-label'), 'New automation');
      assert.equal(await page.locator('.au-ctl').count(), 0, 'no bar under the header');
      const place = await page.evaluate(() => {
        const r = (s: string) => document.querySelector(s)!.getBoundingClientRect();
        const add = r('.au-new'), range = r('.au-ph .au-range'), tabs = r('.au-ph .au-tabs'), dots = r('.au-ph > .ph-more'), title = r('.au-ph h1');
        const mid = (x: DOMRect) => x.top + x.height / 2;
        return {h: add.height, line: [range, tabs, dots, add].every(x => Math.abs(mid(x) - mid(title)) < 2), order: title.right < tabs.left && tabs.right < range.left && range.right < add.left && add.right < dots.left};
      });
      assert.equal(place.h, 32, 'New automation is 32px high: ' + JSON.stringify(place));
      assert.ok(place.line && place.order, 'title, tabs, range, + and ⋯ on one line, in that order: ' + JSON.stringify(place));
      assert.equal(await page.locator('.au-ph > :last-child').evaluate(e => e.matches('.ph-menu') ? e.previousElementSibling!.matches('.ph-more') : e.matches('.ph-more')), true, '⋯ is last');
      assert.equal(await page.locator('[data-filters]').isVisible(), false);
      assert.equal(await page.locator('.au-rail .au-q').isVisible(), true);
      assert.equal(await page.locator('.au-fh').count(), 0);
      assert.equal(await page.locator('[data-ftrig="message"]').isVisible(), true);
      assert.equal(await page.locator('.au-side').isVisible(), false, 'no side panel until an editor opens');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '1h');
      const runsOf = (id: string) => page.locator(`tr.au-row[data-row="${id}"] td.au-n`).first();
      // A schedule's words fit their column whole (cronWords, 12-hour), and the last column keeps a gutter.
      const fit = await page.locator('tr.au-row td.au-tt').first().evaluate(td => {
        const probe = document.createElement('span');
        probe.textContent = auCap(cronWords('30 10 * * 1-5'));   // "Weekdays at 10:30 AM", the longest usual schedule
        probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
        td.append(probe);
        const cs = getComputedStyle(td), room = td.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), need = probe.offsetWidth;
        probe.remove();
        return {words: probe.textContent, room, need};
      });
      assert.ok(/^weekdays at 10:30 AM$/i.test(fit.words ?? '') && fit.need <= fit.room, 'a schedule is not cut: ' + JSON.stringify(fit));
      assert.equal(await page.locator('.au-tbl-t thead th:last-child').evaluate(t => parseFloat(getComputedStyle(t).paddingRight) >= 16), true);
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement).innerText === '4');
      assert.match(await page.locator('tr.au-row[data-row="au_inbox"]').innerText(), /now/, 'its last run');

      // The exception shows, not the default: no cell holds a 0 or a dash, an automation with no runs has no sparkline,
      // and a column no automation has a value in (Failed, in the last hour) is left out, the Name column taking its room.
      const heads = () => page.locator('.au-tbl-t thead th').evaluateAll(t => t.map(x => (x as HTMLElement).innerText.trim()));
      const cells = await page.locator('tr.au-row td').evaluateAll(t => t.map(x => (x as HTMLElement).innerText.trim()));
      assert.deepEqual(cells.filter(c => c === '0' || c === '—'), [], 'no 0 or — in the list');
      assert.equal(await page.locator('tr.au-row[data-row="au_deploy"] .au-spark').count(), 0, 'no empty sparkline');
      assert.equal(await page.locator('tr.au-row[data-row="au_deploy"] td.au-cn').innerText(), '', 'no runs is blank');
      const hourHeads = await heads();
      assert.ok(!hourHeads.includes('FAILED') && hourHeads.includes('COST'), 'Failed leaves; Cost stays: ' + hourHeads);
      const nameAt1h = (await page.locator('th.au-tn').boundingBox())!.width;
      // Filters with nothing in them are left out; a search does not move a column.
      assert.deepEqual(await page.locator('.au-fb .au-fn').allInnerTexts().then(n => n.filter(x => x === '0')), [], 'no filter at 0');
      assert.equal(await page.locator('[data-fstatus="paused"]').count(), 0, 'nothing paused, no Paused filter');
      await page.fill('.au-rail .au-q', 'deploy');
      assert.deepEqual(await heads(), hourHeads, 'typing keeps the columns');
      await page.fill('.au-rail .au-q', '');

      // A range with a failure brings Failed back, and while it loads no column leaves and none comes and goes.
      const atClick = await page.evaluate(() => {
        (document.querySelector('.au-range [data-range="24h"]') as HTMLElement).click();
        return [...document.querySelectorAll('.au-tbl-t thead th')].map(x => (x as HTMLElement).innerText.trim());
      });
      assert.deepEqual(atClick.filter(h => !h.startsWith('LAST ')), hourHeads.filter(h => !h.startsWith('LAST ')), 'no column leaves while the range loads');
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement).innerText === '30');
      await page.waitForSelector('th.au-cf');
      assert.equal(await page.locator('tr.au-row[data-row="au_inbox"] td.au-cf').innerText(), '3');
      assert.match(await page.locator('tr.au-row[data-row="au_inbox"] td.au-cf').getAttribute('class') || '', /au-bad/, 'a failure count is red');
      assert.ok((await page.locator('th.au-tn').boundingBox())!.width < nameAt1h, 'Name is wider without Failed');
      await page.reload();
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('.au-range [aria-pressed="true"]').innerText(), '24h', 'the range is kept');
      await page.waitForFunction(() => (document.querySelector('tr.au-row[data-row="au_inbox"] td.au-n') as HTMLElement | null)?.innerText === '30');
      assert.equal(await runsOf('au_deploy').innerText(), '', 'no runs is blank');

      // ⋯ holds Group by and Sort by (used less than daily); the picked one is checked. Sort by most failures puts the
      // one with failures first.
      await more(page, '[data-group-v="none"]');
      await more(page, '[data-sort-v="fail"]');
      assert.equal((await rows(page))[0], 'au_inbox');
      assert.equal(await page.locator('tr.au-grp').count(), 0);
      await page.locator('.au-ph > .ph-more').click();
      assert.deepEqual(await page.locator('.au-ph > .ph-menu [aria-pressed="true"]').evaluateAll(b => b.filter(x => (x as HTMLElement).checkVisibility()).map(x => (x.lastChild?.textContent || '').trim())), ['None', 'Most failures']);
      // The range's copy in ⋯ (a phone's) is hidden on a computer but follows the header's pick.
      assert.equal(await page.locator('.au-ph > .ph-menu .au-mrange [aria-pressed="true"]').getAttribute('data-range'), '24h');
      assert.equal(await page.locator('.au-ph .au-range [aria-pressed="true"]').innerText(), '24h');
      assert.equal(await page.locator('.au-ph > .ph-menu .au-mrange').isVisible(), false, 'a computer picks the range in the header');
      await page.keyboard.press('Escape');
      await shot(page, 'desktop-list');

      await page.locator('.au-ph [data-tab="runs"]').click();
      await page.waitForSelector('tr.au-arow');
      assert.equal(await page.locator('tr.au-arow').count(), 30);
      assert.match(await page.locator('.au-chart').innerText(), /3 failed/);
      // A run that went through has no pill; a failed one does (style guide: show the exception).
      const pills = await page.locator('tr.au-arow .pill').allInnerTexts();
      assert.ok(pills.length < 30 && !pills.includes('Done'), 'no pill on a done run: ' + pills);
      // The run's status is a filter in the rail, first on the Runs tab.
      assert.equal(await page.locator('.au-facets .au-fg').first().getAttribute('aria-label'), 'Run');
      await page.locator('[data-frun="failed"]').click();
      await page.waitForFunction(() => document.querySelectorAll('tr.au-arow').length === 3);
      assert.equal(await page.locator('[data-frun="failed"]').getAttribute('aria-pressed'), 'true');
      await shot(page, 'desktop-all-runs');
      const run = await page.locator('tr.au-arow').first().getAttribute('data-arun');
      await page.locator('tr.au-arow').first().click();
      await page.waitForURL(/#\/automations\/au_inbox\/runs$/);
      await page.waitForSelector(`.au-rr[aria-expanded="true"]`);
      assert.match(await page.locator('.au-rx').innerText(), /model timed out/, `run ${run} opens`);
      // A Run filter picked with an automation open shows the list again, filtered, as every filter does; the picked
      // choice keeps the focus.
      await page.locator('[data-frun="waiting"]').click();
      await page.waitForFunction(() => (document.querySelector('#au-root') as HTMLElement).dataset.screen === 'list');
      assert.match(page.url(), /#\/automations$/);
      assert.equal(await page.locator('[data-frun="waiting"]').getAttribute('aria-pressed'), 'true');
      await page.locator('[data-frun="failed"]').focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('tr.au-arow').length === 3);
      assert.equal(await page.evaluate(() => !!document.activeElement?.matches('[data-frun="failed"]')), true, 'focus stays on the pick');
      assert.equal(await page.locator('.au-ph [data-tab="runs"]').getAttribute('aria-selected'), 'true', 'the tab is kept');
      assert.equal(await page.locator('[data-frun="failed"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('tr.au-arow').count(), 3);
      await page.locator('.au-ph [data-tab="list"]').click();

      // The header switch.
      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
      await shot(page, 'desktop-flow');
      // One ⋯ on screen (style guide: ⋯ is the single last control): the page header's, now carrying the automation's
      // own items and Copy link, Delete last, red, after a separator.
      const dots = await page.locator('.au-ph > .ph-more, .au-hd-more').evaluateAll(b => b.filter(x => (x as HTMLElement).checkVisibility()).map(x => x.className));
      assert.deepEqual(dots, ['ph-more'], 'one ⋯: ' + dots);
      await page.locator('.au-ph > .ph-more').click();
      const items = await page.locator('.au-ph > .ph-menu > *').evaluateAll(e => e.map(x => (x as HTMLElement).dataset.mi || (x.matches('.ph-copy') ? 'copy' : x.matches('.au-msep') ? 'sep' : x.className)));
      assert.deepEqual(items, ['run', 'apply', 'versions', 'copy', 'sep', 'delete'].filter(k => k !== 'apply' || items.includes('apply')), 'the automation\'s items in ⋯: ' + items);
      assert.equal(await page.locator('.au-ph > .ph-menu [data-mi="delete"]').evaluate(b => getComputedStyle(b).color === getComputedStyle(document.querySelector('.au-ph .ph-copy')!).color), false, 'Delete is red');
      assert.equal(await page.locator('.au-ph > .ph-menu [data-group-v]').count(), 0, 'no Group by while one is open');
      await page.keyboard.press('Escape');
      // Back on the list, ⋯ has Group by and Sort by again.
      await page.locator('.au-crumb .au-up').click();
      await page.waitForFunction(() => (document.querySelector('#au-root') as HTMLElement).dataset.screen === 'list');
      assert.ok(await page.locator('.au-ph > .ph-menu [data-group-v]').count() === 3 && await page.locator('.au-ph > .ph-menu [data-sort-v]').count() === 5, 'the list\'s ⋯ again');
      assert.equal(await page.locator('.au-ph > .ph-menu [data-mi]').count(), 0);
      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-card[data-open="trigger"]');
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
      await auMoreOpen(page);
      assert.equal(await page.locator(':is(.au-menu, .au-ph > .ph-menu) [data-mi="delete"]:visible').isDisabled(), true);
      await page.keyboard.press('Escape');
      page.once('dialog', d => d.dismiss());
      await page.locator('.au-hd [data-on-cur]').click();
      assert.equal(await page.locator('.au-hd [data-on-cur]').isChecked(), true);
      assert.notEqual(writes.at(-1)!.path, '/api/v2/automations/au_tasks');

      // Any other: ⋯ Delete, once confirmed, goes and leaves the list.
      await page.evaluate(() => { location.hash = '#/automations/au_deploy'; });
      await page.waitForSelector('.au-hd .au-name:text("Deploy watch")');
      await auMoreOpen(page);
      page.once('dialog', d => d.accept());
      await page.locator(':is(.au-menu, .au-ph > .ph-menu) [data-mi="delete"]:visible').click();
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
