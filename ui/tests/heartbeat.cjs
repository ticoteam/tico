// The Heartbeat on the Automations page (ui/app/automations-heartbeat.js) against the fake hub's heartbeat routes
// (support/automations-fake.cjs, `heartbeat: true`). The Heartbeat is a built-in system row right after Maintenance,
// with its runs and counts; its screen shows the interval, the last beat and the day's count, and each step grouped by
// kind (Due times, Recovery, Outside polls) with its cadence, last result, day counts, time and last error, and Ask
// go-ahead's switch. The interval saves one PUT; the switch saves its job. Log lists the beats that did something, newest
// first, a page and More, a folded failure with its count, and a tap opens each step's line. The minute poll skips a
// hidden page and reads once on coming back; a poll leaves a focused interval select alone. A member reads it with every
// control off; a phone fits.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {openAutomations, PHONE, DESKTOP, sideways} = require('./support/automations-fake.cjs');

const until = async (fn, what, ms = 6000) => {
  for (const end = Date.now() + ms; !(await fn());) {
    if (Date.now() > end) throw new Error('timed out waiting for ' + what);
    await new Promise(r => setTimeout(r, 50));
  }
};
const idle = page => page.waitForFunction(() => AU && !AU.hbBusy);
// Screenshots in both themes, with animations stopped.
async function themed(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  require('node:fs').mkdirSync(dir, {recursive: true});
  for (const theme of ['dark', 'light']) {
    await page.evaluate(v => window.ticoTheme.set(v), theme);
    await page.screenshot({path: path.join(dir, `${name}-${theme}.png`), animations: 'disabled'});
  }
  await page.evaluate(() => window.ticoTheme.set('dark'));
}

(async () => {
  const browser = await chromium.launch();
  try {
    // An owner on a computer: the list row and its runs, the screen, the interval and the switch, and the log.
    {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true, heartbeat: true});
      await page.waitForSelector('tr.au-row[data-row="heartbeat"]');
      const row = page.locator('tr.au-row[data-row="heartbeat"]');
      assert.match(await row.innerText(), /Heartbeat[\s\S]*Every 10 s/);
      assert.equal(await row.locator('.pill', {hasText: 'Built-in'}).count(), 1);
      // Row order follows the list sort (last run by default), so where the row sits is not checked here.
      await until(async () => (await row.locator('td.au-cf').innerText()).trim() === '1', 'the failed count');
      assert.equal(Number((await row.locator('td.au-cn').innerText()).replace(/\D/g, '')), 6, 'runs in the hour');
      // The overview's Runs tab lists the Heartbeat's runs; a tap opens its log.
      await page.locator('[data-tab="runs"]').click();
      await page.waitForSelector('tr.au-arow[data-aid="heartbeat"]');
      await page.locator('tr.au-arow[data-aid="heartbeat"]').first().click();
      await page.waitForSelector('tr.au-rr[data-hb-beat]');
      assert.equal(await page.locator('.au-hd [role="tab"][aria-selected="true"]').innerText(), 'Log');
      await page.goBack();
      await page.locator('[data-tab="list"]').click();
      await page.waitForSelector('tr.au-row[data-row="heartbeat"]');

      await row.locator('a').click();
      await page.waitForSelector('.mt-step[data-hb-step]');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'detail');
      assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Heartbeat');
      assert.match(await page.locator('[data-hb-meta]').innerText(), /^Last beat .+ · 8,640 in 24 h$/);
      assert.equal(await page.locator('[data-hb-interval]').inputValue(), '10');
      assert.deepEqual(await page.locator('[data-hb-interval] option').allInnerTexts(), ['5 s', '10 s', '15 s', '20 s', '30 s', '45 s', '60 s']);
      // Grouped by kind, in step order inside each.
      assert.deepEqual(await page.locator('.hb-group').evaluateAll(g => g.map(x => x.dataset.hbKind)), ['due', 'recovery', 'poll']);
      assert.deepEqual(await page.locator('.hb-kind').evaluateAll(h => h.map(x => x.textContent)), ['Due times', 'Recovery', 'Outside polls']);
      assert.deepEqual(await page.locator('.mt-step').evaluateAll(r => r.map(x => x.dataset.hbStep)),
        ['due_reminders', 'ask_deadlines', 'stall_checks', 'queue_check', 'github_sync']);
      const step = k => page.locator(`.mt-step[data-hb-step="${k}"]`);
      assert.match(await step('due_reminders').innerText(), /Due reminders[\s\S]*1 reminder sent[\s\S]*Every beat[\s\S]*24 did[\s\S]*0 failed[\s\S]*3 ms/);
      assert.match(await step('github_sync').locator('[data-hb-ms]').innerText(), /^1\.2 s$/);
      assert.match(await step('stall_checks').locator('[data-hb-every]').innerText(), /^Every minute$/);
      assert.equal(await step('due_reminders').locator('[data-hb-error]').count(), 0);
      assert.match(await step('queue_check').locator('[data-hb-error]').innerText(), /Failed: unexpected error \(RuntimeError\)/);
      // Red only while the latest run failed; an old error stays, muted, with its time.
      assert.equal(await step('queue_check').locator('[data-hb-error]').getAttribute('class'), 'hb-err');
      assert.match(await step('github_sync').locator('[data-hb-error]').getAttribute('class'), /\bhb-err-old\b.*\bmuted\b/);
      assert.match(await step('github_sync').locator('[data-hb-error]').innerText(), /Failed: GitHub did not answer · \S/);
      assert.match(await step('queue_check').locator('[data-hb-failed]').innerText(), /^6 failed$/);
      // Only Ask go-ahead has a switch.
      assert.equal(await page.locator('.mt-step [data-hb-on]').count(), 1);
      assert.equal(await step('ask_deadlines').locator('[data-hb-on]').isChecked(), true);
      assert.equal(await sideways(page), false);
      await themed(page, 'heartbeat-steps-desktop');

      // The interval: one PUT, the head and the list row follow.
      await page.locator('[data-hb-interval]').selectOption('20');
      await until(() => writes.length === 1, 'the interval save');
      assert.deepEqual(writes.at(-1), {method: 'PUT', path: '/api/v2/heartbeat', body: {interval_s: 20}});
      await idle(page);
      assert.equal(db.heart.interval_s, 20);
      assert.equal(await page.locator('[data-hb-interval]').inputValue(), '20');

      // The switch saves its job; the answer is the step's row.
      await step('ask_deadlines').locator('[data-hb-on]').click();
      await until(() => writes.length === 2, 'the switch save');
      assert.deepEqual(writes.at(-1), {method: 'PUT', path: '/api/v2/system-routines/ask_deadlines', body: {on: false}});
      await idle(page);
      assert.equal(await step('ask_deadlines').locator('[data-hb-on]').isChecked(), false);
      assert.equal(await step('ask_deadlines').locator('[data-hb-every]').innerText(), 'Off');
      assert.match(await step('ask_deadlines').getAttribute('class'), /\boff\b/);

      // Log: the beats that did something, newest first, a page of 20 and More; a tap opens each step's line.
      await page.getByRole('tab', {name: 'Log'}).click();
      await page.waitForSelector('tr.au-rr[data-hb-beat]');
      assert.equal(await page.locator('tr.au-rr').count(), 20);
      assert.equal(await page.locator('tr.au-rr').first().getAttribute('data-hb-beat'), 'hbt_100');
      assert.equal(await page.locator('tr.au-rr .au-dot.fail').count(), 4);
      const failed = page.locator('tr.au-rr[data-hb-beat="hbt_099"]');
      assert.match(await failed.innerText(), /Due reminders: 1 reminder sent · Queue check failed/);
      await failed.click();
      await page.waitForSelector('tr.au-rx .au-sl');
      assert.equal(await page.locator('tr.au-rx .au-sl').count(), 2);
      assert.match(await page.locator('tr.au-rx [data-hb-line="queue_check"]').innerText(), /failed[\s\S]*Queue check · Failed: unexpected error/i);
      assert.equal(await page.locator('tr.au-rx [data-hb-line="queue_check"] .au-tag.mt-bad').count(), 1);
      assert.match(await page.locator('tr.au-rx [data-hb-line="due_reminders"]').innerText(), /did[\s\S]*Due reminders · 1 reminder sent[\s\S]*3 ms/i);
      // The same failure beat after beat is one row with its count and last time.
      assert.match(await page.locator('tr.au-rr[data-hb-beat="hbt_094"]').innerText(), /Queue check failed ×3, last \S/);
      assert.equal(await page.locator('tr.au-rr[data-hb-beat="hbt_093"] [data-hb-repeats]').count(), 0);
      await themed(page, 'heartbeat-log-desktop');
      await page.locator('[data-hb-more]').click();
      await until(async () => await page.locator('tr.au-rr').count() === 30, 'the next page');
      assert.equal(await page.locator('[data-hb-more]').count(), 0);
      assert.ok(writes.every(w => w.method === 'PUT'), 'paging writes nothing');

      // The safety poll: a new beat lands on top, the open one stays open and the older page stays.
      db.heart.beats.unshift({id: 'hbt_101', started_at: new Date().toISOString(), total_ms: 5, did: 1, failed: 0,
        steps: [{key: 'due_reminders', name: 'Due reminders', text: '2 reminders sent', ms: 4, error: null}]});
      await page.evaluate(() => auHeartLoad(AU));
      assert.equal(await page.locator('tr.au-rr').first().getAttribute('data-hb-beat'), 'hbt_101');
      assert.equal(await page.locator('tr.au-rr').count(), 31);
      assert.equal(await page.locator('tr.au-rr.open[data-hb-beat="hbt_099"]').count(), 1);
      assert.equal(await page.evaluate(() => !!AU.hbPoll), true, 'polls while open');

      // A hidden page skips the minute's read; coming back reads once.
      const addBeat = id => db.heart.beats.unshift({id, started_at: new Date().toISOString(), total_ms: 5, did: 1, failed: 0,
        steps: [{key: 'due_reminders', name: 'Due reminders', text: '3 reminders sent', ms: 4, error: null}]});
      addBeat('hbt_102');
      await page.evaluate(async () => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        const real = window.setTimeout;
        let tick = null;
        window.setTimeout = fn => { tick = fn; return 0; };
        auHeartPoll(AU);
        window.setTimeout = real;
        await tick();
      });
      assert.equal(await page.locator('tr.au-rr').first().getAttribute('data-hb-beat'), 'hbt_101', 'no read while hidden');
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await until(async () => await page.locator('tr.au-rr').first().getAttribute('data-hb-beat') === 'hbt_102', 'the read on coming back');

      // A poll while the interval select is focused leaves it alone and moves only the meta line.
      await page.getByRole('tab', {name: 'Steps'}).click();
      await page.waitForSelector('.mt-step[data-hb-step]');
      db.heart.beats_24h = 9000;
      const kept = await page.evaluate(async () => {
        const sel = document.querySelector('[data-hb-interval]');
        sel.dataset.mark = '1';
        sel.focus();
        await auHeartLoad(AU);
        return document.querySelector('[data-hb-interval]').dataset.mark;
      });
      assert.equal(kept, '1');
      assert.match(await page.locator('[data-hb-meta]').innerText(), /9,000 in 24 h$/);
      await page.evaluate(async () => { document.activeElement.blur(); await auHeartLoad(AU); });
      assert.equal(await page.locator('[data-hb-interval]').getAttribute('data-mark'), null, 'drawn again once it is left');

      // Back on the list, the row has the saved interval and the poll stops.
      await page.locator('.au-crumb .au-up').click();
      await page.waitForSelector('tr.au-row[data-row="heartbeat"]');
      assert.match(await page.locator('tr.au-row[data-row="heartbeat"]').innerText(), /Every 20 s/);
      assert.equal(await page.evaluate(() => !!AU.hbPoll), false, 'no poll off the screen');
      assert.equal(writes.length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A member reads it all with every control off.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, heartbeat: true, viewer: 'omar',
        hash: '#/automations/heartbeat'});
      await page.waitForSelector('.mt-step[data-hb-step]');
      assert.equal(await page.locator('[data-hb-interval]').isDisabled(), true);
      assert.equal(await page.locator('[data-hb-on]:not([disabled])').count(), 0);
      assert.equal(await page.locator('[data-hb-on]').count(), 1);
      await page.getByRole('tab', {name: 'Log'}).click();
      await page.waitForSelector('tr.au-rr[data-hb-beat]');
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: the screen opened straight from its link fits, with no sideways scroll, and so does its log.
    {
      const {page, errors} = await openAutomations(browser, {viewport: PHONE, heartbeat: true, hash: '#/automations/heartbeat'});
      await page.waitForSelector('.mt-step[data-hb-step]');
      assert.equal(await sideways(page), false);
      assert.match(await page.locator('.mt-step[data-hb-step="stall_checks"] .mt-name').innerText(), /Every minute/);
      await themed(page, 'heartbeat-steps-phone');
      await page.getByRole('tab', {name: 'Log'}).click();
      await page.waitForSelector('tr.au-rr[data-hb-beat]');
      await page.locator('tr.au-rr[data-hb-beat="hbt_099"]').click();
      await page.waitForSelector('tr.au-rx .au-sl');
      assert.equal(await sideways(page), false);
      await themed(page, 'heartbeat-log-phone');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A hub without the Heartbeat: no row, and Maintenance and System jobs sit together as before.
    {
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true});
      await page.waitForSelector('tr.au-row[data-row="system-jobs"]');
      assert.equal(await page.locator('tr.au-row[data-row="heartbeat"]').count(), 0);
      assert.equal(await page.locator('tr.au-row[data-row="system-jobs"]').evaluate(r => r.previousElementSibling?.dataset.row), 'maintenance');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('heartbeat ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
