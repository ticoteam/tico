// Maintenance on the Automations page (ui/app/automations-maintenance.js) against the fake hub's maintenance routes
// (support/automations-fake.cjs, `maintenance: true`). Maintenance leads the list as a built-in system row, and the
// mirrors of the jobs it replaced are gone from it. Its screen lists the eight steps in run order with their switches
// (Collect is always on), beta and consent pills; a row opens How often and, for Find stuck and Dream, the
// number of bots; Learn shows its beta numbers. Each control saves one PATCH; Run now posts and follows the run; Runs
// lists the run history and a tap opens each step's line. A member reads it with every control off; a phone fits.
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
const idle = page => page.waitForFunction(() => AU && !AU.mtBusy);
const steps = page => page.locator('.mt-step').evaluateAll(r => r.map(x => x.dataset.mtStep));
const finish = mt => {
  const at = new Date().toISOString();
  Object.assign(mt.live, {finished_at: at, heartbeat_at: at, ok: true, result: mt.live.steps.length ? '1 ran' : 'Nothing ran'});
  mt.live = null;
};
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
    // An owner on a computer: the list row, the screen, a save of every control kind, Run now and the run history.
    {
      const {page, errors, writes, db, hold} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true});
      await page.waitForSelector('tr.au-row[data-row="maintenance"]');
      const row = page.locator('tr.au-row[data-row="maintenance"]');
      assert.match(await row.innerText(), /Maintenance[\s\S]*Every hour/);
      assert.equal(await page.locator('tr.au-grp[data-agrp="system"]').count(), 1);
      await page.locator('[data-range="24h"]').click();
      await until(async () => (await row.locator('td.au-cf').innerText()).trim() === '1', 'the failed count');
      assert.ok(Number((await row.locator('td.au-cn').innerText()).replace(/\D/g, '')) >= 20, 'runs over 24h');
      // The overview's Runs tab lists Maintenance's runs beside the automations' runs; a tap opens its run history.
      await page.locator('[data-tab="runs"]').click();
      await page.waitForSelector('tr.au-arow[data-aid="maintenance"]');
      const first = page.locator('tr.au-arow[data-aid="maintenance"]').first();
      assert.match(await first.innerText(), /Maintenance/);
      const tapped = (await first.getAttribute('data-arun')).slice(4);
      await first.click();
      await page.waitForSelector(`tr.au-rr.open[data-mt-run="${tapped}"]`);
      await page.goBack();
      await page.locator('[data-tab="list"]').click();
      await page.waitForSelector('tr.au-row[data-row="maintenance"]');

      await row.locator('a').click();
      await page.waitForSelector('.mt-step');
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'detail');
      assert.equal(await page.locator('.au-hd .au-name').innerText(), 'Maintenance');
      assert.match(await page.locator('[data-mt-meta]').innerText(), /^Last run .+ · next .+/);
      assert.deepEqual(await steps(page), ['collect', 'clean_up', 'check_health', 'find_stuck', 'learn', 'suggest', 'update', 'dream']);
      assert.deepEqual(await page.locator('.mt-step .mt-n').allInnerTexts(), ['1', '2', '3', '4', '5', '6', '7', '8']);
      const step = k => page.locator(`.mt-step[data-mt-step="${k}"]`);
      assert.equal(await step('collect').locator('input[role="switch"]').count(), 0, 'Collect has no switch');
      assert.match(await step('collect').innerText(), /Always/);
      assert.equal(await page.locator('.mt-step [data-mt-on]').count(), 7);
      assert.match(await step('learn').locator('.pill.beta').innerText(), /Beta/);
      assert.equal(await step('learn').locator('[data-mt-on]').isChecked(), false, 'Learn is off by default');
      const suggest = db.maint.steps.find(x => x.key === 'suggest');
      assert.equal(await step('suggest').locator('[data-mt-consent]').getAttribute('href'), '#/settings/general/settings-privacy');
      suggest.consent = true;
      await page.evaluate(() => auMaintLoad(AU));
      assert.equal(await step('suggest').locator('[data-mt-consent]').count(), 0, 'consent granted');
      suggest.consent = false;
      await page.evaluate(() => auMaintLoad(AU));
      assert.match(await step('find_stuck').locator('.mt-when').innerText(), /Every 6 hours/);
      assert.match(await step('dream').locator('.mt-when').innerText(), /Once a day/);

      // The run's schedule: no "Once a week".
      const sched = await page.locator('[data-mt-schedule] option').evaluateAll(o => o.map(x => x.textContent));
      assert.deepEqual(sched, ['Every hour', 'Every 2 hours', 'Every 3 hours', 'Every 6 hours', 'Every 12 hours', 'Once a day']);
      await step('find_stuck').locator('[data-mt-open]').click();
      await page.waitForSelector('.mt-step[data-mt-step="find_stuck"] [data-mt-bots]');
      const release = hold('maintenance');
      await page.selectOption('[data-mt-schedule]', '3h');
      await until(() => writes.length === 1, 'the schedule save');
      assert.deepEqual(writes.at(-1), {method: 'PATCH', path: '/api/v2/maintenance', body: {schedule: '3h'}});
      assert.equal(await page.locator('[data-mt-pause]').isDisabled(), true);
      assert.equal(await page.locator('.mt select:not([disabled]),.mt input:not([disabled])').count(), 0, 'busy disables settings');
      // A queued change redraws the saved value and sends nothing.
      await page.locator('[data-mt-schedule]').evaluate(t => { t.value = '6h'; t.dispatchEvent(new Event('change', {bubbles: true})); });
      assert.equal(await page.locator('[data-mt-schedule]').inputValue(), 'hourly');
      assert.equal(await page.locator('[data-mt-schedule]').isDisabled(), true);
      assert.equal(writes.length, 1);
      release();
      await idle(page);
      assert.equal(await page.locator('[data-mt-schedule]').inputValue(), '3h');
      await step('find_stuck').locator('[data-mt-open]').click();

      // A switch.
      await step('check_health').locator('[data-mt-on]').click();
      await until(() => writes.length === 2, 'the switch save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'check_health', enabled: false}]});
      await page.waitForSelector('.mt-step.off[data-mt-step="check_health"]');
      await idle(page);

      // Find stuck: How often is custom; its number of bots saves on change; a number past 20 saves nothing.
      await step('find_stuck').locator('[data-mt-open]').click();
      await page.waitForSelector('.mt-step.open[data-mt-step="find_stuck"] .mt-detail');
      assert.equal(await step('find_stuck').locator('[data-mt-often]').inputValue(), 'custom');
      assert.equal(await step('find_stuck').locator('[data-mt-every]').inputValue(), '6');
      assert.match(await step('find_stuck').locator('.mt-detail').innerText(), /Most bots woken per run/i);
      const bots = step('find_stuck').locator('[data-mt-bots]');
      assert.equal(await bots.inputValue(), '10');
      await bots.fill('5'); await bots.press('Tab');
      await until(() => writes.length === 3, 'the bots save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'find_stuck', max_bots: 5}]});
      await idle(page);
      assert.equal(await step('find_stuck').locator('[data-mt-bots]').inputValue(), '5');
      await step('find_stuck').locator('[data-mt-bots]').fill('25');
      await step('find_stuck').locator('[data-mt-bots]').press('Tab');
      await page.waitForTimeout(300);
      assert.equal(writes.length, 3, 'past 20 bots saves nothing');
      const every = step('find_stuck').locator('[data-mt-every]');
      await every.fill('12'); await every.press('Tab');
      await until(() => writes.length === 4, 'the custom save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'find_stuck', frequency: 'custom', custom_every: 12, custom_unit: 'hours'}]});
      await idle(page);
      assert.match(await step('find_stuck').locator('.mt-when').innerText(), /Every 12 hours/);

      // Update: How often to once a week, then custom (two hours to start).
      await step('update').locator('[data-mt-open]').click();
      await page.waitForSelector('.mt-step.open[data-mt-step="update"] [data-mt-often]');
      assert.equal(await step('update').locator('[data-mt-bots]').count(), 0, 'Update takes no number of bots');
      await step('update').locator('[data-mt-often]').selectOption('week');
      await until(() => writes.length === 5, 'the How often save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'update', frequency: 'week'}]});
      await idle(page);
      assert.match(await step('update').locator('.mt-when').innerText(), /Once a week/);
      await step('update').locator('[data-mt-often]').selectOption('custom');
      await until(() => writes.length === 6, 'the custom How often save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'update', frequency: 'custom', custom_every: 2, custom_unit: 'hours'}]});
      await idle(page);
      await step('update').locator('[data-mt-unit]').selectOption('days');
      await until(() => writes.length === 7, 'the unit save');
      assert.deepEqual(writes.at(-1).body, {steps: [{key: 'update', frequency: 'custom', custom_every: 2, custom_unit: 'days'}]});
      await idle(page);
      assert.match(await step('update').locator('.mt-when').innerText(), /Every 2 days/);
      // Every-run steps have no How often to pick.
      await step('clean_up').locator('[data-mt-open]').click();
      await page.waitForSelector('.mt-step.open[data-mt-step="clean_up"] .mt-detail');
      assert.equal(await step('clean_up').locator('[data-mt-often]').count(), 0);
      assert.match(await step('clean_up').locator('.mt-detail').innerText(), /Every run/);
      await step('clean_up').locator('[data-mt-open]').click();

      // Learn's beta numbers.
      await step('learn').locator('[data-mt-open]').click();
      await page.waitForSelector('.mt-step[data-mt-step="learn"] [data-mt-stats]');
      const stats = await step('learn').locator('[data-mt-stats]').innerText();
      assert.match(stats, /Last 7 days/i);
      for (const re of [/6\s*Runs/, /42\s*Read/, /5\s*Sent/, /4\s*Saved/, /1\s*Dropped/, /\$0\.18\s*Cost/]) assert.match(stats, re);

      // The monthly cap: the env's $50 in effect, then $100.
      assert.equal(await page.locator('[data-mt-cap]').inputValue(), '50');
      await page.selectOption('[data-mt-cap]', '100');
      await until(() => writes.length === 8, 'the cap save');
      assert.deepEqual(writes.at(-1).body, {monthly_cap_usd: 100});
      await idle(page);
      assert.equal(await page.locator('[data-mt-cap]').inputValue(), '100');
      await themed(page, 'maintenance-settings-desktop');

      // Pause asks first, then saves; the list row says Paused.
      page.once('dialog', d => d.accept());
      await page.locator('[data-mt-pause]').click();
      await until(() => writes.length === 9, 'the pause save');
      assert.deepEqual(writes.at(-1).body, {paused: true});
      await idle(page);
      assert.match(await page.locator('[data-mt-meta]').innerText(), /paused/);
      await page.locator('[data-mt-pause]').click();
      await until(() => writes.length === 10, 'the unpause save');
      assert.deepEqual(writes.at(-1).body, {paused: false});
      await idle(page);

      // Run now: one POST, Running… until the follow reads it finished.
      await page.locator('[data-mt-now]').click();
      await until(() => writes.length === 11, 'Run now');
      assert.deepEqual(writes.at(-1), {method: 'POST', path: '/api/v2/maintenance/run', body: {}});
      await page.waitForSelector('[data-mt-now][disabled]');
      assert.match(await page.locator('[data-mt-now]').innerText(), /Running/);
      finish(db.maint);
      await page.waitForSelector('[data-mt-now]:not([disabled])', {timeout: 8000});
      assert.equal(await page.locator('[data-mt-now]').innerText(), 'Run now');

      // Runs: newest first, a page of 25 and More; a tap opens each step's line.
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('tr.au-rr[data-mt-run]');
      assert.equal(await page.locator('tr.au-rr').count(), 25);
      assert.equal(await page.locator('tr.au-rr').first().getAttribute('data-mt-run'), 'mrun_now');
      assert.match(await page.locator('tr.au-rr').first().innerText(), /Run now/);
      assert.equal(await page.locator('tr.au-rr .au-dot.fail').count(), 1);
      const failed = page.locator('tr.au-rr[data-mt-run="mrun_98"]');
      assert.match(await failed.innerText(), /1 failed, 2 ran, 4 skipped[\s\S]*Schedule/);
      await failed.click();
      await page.waitForSelector('tr.au-rx .au-sl');
      assert.equal(await page.locator('tr.au-rx .au-sl').count(), 8);
      assert.match(await page.locator('tr.au-rx [data-mt-line="dream"]').innerText(), /failed[\s\S]*Dream · model timed out/i);
      assert.equal(await page.locator('tr.au-rx [data-mt-line="dream"] .au-tag.mt-bad').count(), 1);
      assert.match(await page.locator('tr.au-rx [data-mt-line="collect"]').innerText(), /120 checked · 14 found/);
      assert.match(await page.locator('tr.au-rx [data-mt-line="suggest"]').innerText(), /Privacy consent is off/);
      assert.match(await page.locator('tr.au-rr[data-mt-run="mrun_96"]').innerText(), /Run now · Dream/);
      await themed(page, 'maintenance-runs-desktop');
      await page.locator('[data-mt-more]').click();
      await until(async () => await page.locator('tr.au-rr').count() === 31, 'the next page');
      assert.equal(await page.locator('[data-mt-more]').count(), 0);

      // Back on the list, the row has the saved schedule.
      await page.locator('.au-crumb .au-up').click();
      await page.waitForSelector('tr.au-row[data-row="maintenance"]');
      assert.match(await page.locator('tr.au-row[data-row="maintenance"]').innerText(), /Every 3 hours/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Opening a scheduled run follows it to the end, keeping older rows, expanded steps and the scroll position.
    {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true});
      await page.waitForSelector('tr.au-row[data-row="maintenance"]');
      const at = new Date().toISOString();
      db.maint.live = {id: 'mrun_live', occurrence_key: 'schedule:' + at, trigger: 'schedule', step: null,
        started_at: at, finished_at: null, heartbeat_at: at, ok: null, result: '', cost_usd: 0, steps: []};
      db.maint.runs.unshift(db.maint.live);
      await page.locator('tr.au-row[data-row="maintenance"] a').click();
      await page.waitForSelector('[data-mt-now][disabled]');
      assert.match(await page.locator('[data-mt-meta]').innerText(), /Running/);
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('tr.au-rr[data-mt-run="mrun_live"] .au-dot.wait');
      await page.locator('tr.au-rr[data-mt-run="mrun_98"]').click();
      await page.locator('[data-mt-more]').click();
      await until(async () => await page.locator('tr.au-rr').count() === 31, 'older runs');
      await page.locator('.au-pane').evaluate(p => { p.scrollTop = 80; });
      finish(db.maint);
      await page.waitForSelector('[data-mt-now]:not([disabled])', {timeout: 8000});
      await page.waitForSelector('tr.au-rr[data-mt-run="mrun_live"] .au-dot.ok');
      assert.equal(await page.locator('tr.au-rr').count(), 31);
      assert.equal(await page.locator('tr.au-rr.open[data-mt-run="mrun_98"]').count(), 1);
      assert.equal(await page.locator('tr.au-rx .au-sl').count(), 8);
      assert.equal(await page.locator('[data-mt-more]').count(), 0);
      assert.equal(await page.locator('.au-pane').evaluate(p => p.scrollTop), 80);
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // While a run is live, the follow's refresh keeps a number being typed; Runs read before a Run now shows the new run
    // on the way back.
    {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true,
        hash: '#/automations/maintenance'});
      await page.waitForSelector('.mt-step');
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('tr.au-rr[data-mt-run]');
      await page.getByRole('tab', {name: 'Settings'}).click();
      await page.waitForSelector('.mt-step');
      await page.locator('[data-mt-now]').click();
      await page.waitForSelector('[data-mt-now][disabled]');
      const step = k => page.locator(`.mt-step[data-mt-step="${k}"]`);
      await step('find_stuck').locator('[data-mt-open]').click();
      const bots = step('find_stuck').locator('[data-mt-bots]');
      await bots.fill('7');
      await page.waitForTimeout(3500);   // one follow refresh
      assert.equal(await bots.inputValue(), '7');
      assert.equal(await bots.evaluate(el => el === document.activeElement), true, 'the field keeps focus');
      await bots.press('Tab');
      await until(() => writes.some(w => w.body?.steps?.[0]?.max_bots === 7), 'the bots save');
      finish(db.maint);
      await page.waitForSelector('[data-mt-now]:not([disabled])', {timeout: 8000});
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('tr.au-rr[data-mt-run="mrun_now"]');
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A member reads it all with every control off and no Run now.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, maintenance: true, viewer: 'omar',
        hash: '#/automations/maintenance'});
      await page.waitForSelector('.mt-step');
      assert.equal(await page.locator('[data-mt-now]').count(), 0);
      assert.equal(await page.locator('[data-mt-pause]').count(), 0);
      assert.equal(await page.locator('[data-mt-on]:not([disabled])').count(), 0);
      assert.equal(await page.locator('[data-mt-schedule]').isDisabled(), true);
      assert.equal(await page.locator('[data-mt-cap]').isDisabled(), true);
      await page.locator('.mt-step[data-mt-step="dream"] [data-mt-open]').click();
      await page.waitForSelector('.mt-step[data-mt-step="dream"] [data-mt-bots]');
      assert.equal(await page.locator('.mt-step[data-mt-step="dream"] [data-mt-bots]').isDisabled(), true);
      assert.match(await page.locator('.mt-step[data-mt-step="dream"] .mt-detail').innerText(), /Most bots per run/i);
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: the screen opened straight from its link fits, with no sideways scroll, and so do its runs.
    {
      const {page, errors} = await openAutomations(browser, {viewport: PHONE, maintenance: true, hash: '#/automations/maintenance'});
      await page.waitForSelector('.mt-step');
      await page.locator('.mt-step[data-mt-step="find_stuck"] [data-mt-open]').click();
      await page.waitForSelector('.mt-step[data-mt-step="find_stuck"] [data-mt-bots]');
      assert.equal(await sideways(page), false);
      assert.match(await page.locator('.mt-step[data-mt-step="find_stuck"] .mt-what').innerText(), /Every 6 hours/);
      await themed(page, 'maintenance-settings-phone');
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('tr.au-rr[data-mt-run]');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A hub without Maintenance: the list is as it was.
    {
      const {page, errors} = await openAutomations(browser, {viewport: DESKTOP});
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('tr.au-row[data-row="maintenance"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('maintenance settings ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
