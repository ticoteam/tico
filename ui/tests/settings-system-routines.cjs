// Settings > System (ui/app/settings-recurring.js): one flat list of Tico's own jobs, no group headings, a search box
// that matches name, trigger, action and department; each row reads WHEN ... THEN ... with one department tag, its
// schedule, last run in words (a failure in red with its reason), next run, and for a Decide job the route that answered,
// a fallback in words and this month's spend. Housekeeping says Tico needs it. An owner or admin gets a switch, a
// schedule preset (no every 15 minutes for an AI job) and Run now on the six; Health scan only the owner; a member none.
// Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const soon = new Date(Date.now() + 40 * 60000).toISOString(), before = new Date(Date.now() - 20 * 60000).toISOString();
const ALL = [{key: '15m', label: 'Every 15 minutes'}, {key: 'hourly', label: 'Hourly'}, {key: '4h', label: 'Every 4 hours'}, {key: 'daily', label: 'Daily at a time'}];
const AI = ALL.slice(1);
const job = (key, name, extra = {}) => ({key, name, what: name + ' does a thing.', schedule: 'Hourly', ai: false,
  controllable: false, needed: false, records: true, last: null, next: null, group: 'housekeeping', when: ['every hour'],
  then: ['do ' + name.toLowerCase()], department: {name: 'Engineering', in_chart: true}, switch: false, on: true,
  preset: null, at: null, presets: [], owner_only: false, can_change: false, search: '', ...extra});
function system(role) {
  const admin = ['owner', 'admin'].includes(role), owner = role === 'owner';
  const ctl = (extra) => ({switch: true, controllable: true, can_change: admin, ...extra});
  const jobs = [
    job('stale_review', 'Stale ticket review', ctl({ai: true, route: 'Gateway', fallback: null, group: 'checks', preset: 'hourly', presets: AI,
      then: ['check stale tickets', 'suggest to each owner: mark done, close or check it'], on: true,
      spent_this_month_usd: 0.42, next: soon, last: {started: before, finished: before, ok: true, result: '9 checked, 2 suggestions', runs: 1}})),
    job('still_wanted', 'Still wanted?', ctl({schedule: 'Daily at 09:00', preset: 'daily', at: '09:00', presets: ALL, group: 'checks', next: soon,
      when: ['every day at 09:00'], last: {started: before, finished: before, ok: false, result: 'Failed: the database was busy', runs: 1}})),
    job('health_scan', 'Health scan', ctl({group: 'checks', preset: 'hourly', presets: ALL, owner_only: true, can_change: owner})),
    job('memory_tidy', 'Nightly memory tidy', ctl({on: false, schedule: 'Nightly at 03:30', group: 'learning', preset: 'daily', at: '03:30', presets: [{key: 'daily', label: 'Daily at a time'}], pilots: [], pilot_models: [{model: 'codex:gpt-6.1-sol', label: 'GPT-6.1 Sol', efforts: ['low', 'high']}, {model: 'claude:claude-sonnet-5-5', label: 'Sonnet 5.5', efforts: ['low', 'high']}]})),
    job('learnings', 'Nightly learnings', ctl({ai: true, schedule: 'Nightly at 03:00', route: 'Company key', group: 'learning', preset: 'daily', at: '03:00',
      presets: [{key: 'daily', label: 'Daily at a time'}], when: ['nightly at 03:00'],
      fallback: 'Ran on your own key: Gateway unavailable', spent_this_month_usd: 1.5, next: soon,
      last: {started: before, finished: before, ok: true, result: '40 read, 3 bots sent learnings', runs: 1}})),
    job('due_reminders', 'Due reminders', {schedule: 'Every 10 seconds', group: 'reminders', when: ['every 10 seconds'], then: ['tell a bot when one of its tasks reaches its due time']}),
    job('approval_expiry', 'Approval expiry', {group: 'reminders', then: ['take approvals and asks nobody will act on out of Needs you']}),
    job('stall_checks', 'Stall checks', {needed: true, schedule: 'Every minute', when: ['every minute']}),
    job('stall_triage', 'Stalled task check', ctl({ai: true, group: 'checks', records: false, schedule: 'When it happens',
      when: ['a quiet task nothing has changed on'], then: ['set its wait, close it, or let one wake through']})),
    job('topic_fresh', 'Start fresh when the topic changes', {switch: true, can_change: admin, on: false, records: false, group: 'chat', schedule: 'When it happens',
      when: ['a message in a direct chat changes the topic'], then: ['start a fresh session, like Clear']}),
  ];
  jobs.push(job('task-rule-close-after-done', 'Close 24h after done', {kind: 'task_rule', group: 'task_rules', switch: true, on: true, can_change: role !== 'member', preset: '15m', presets: ALL, department: {id: 'engineering', name: 'Engineering', in_chart: true}, triggers: [{key: 'done_untouched', hours: 24}], actions: ['close'], when: ['a task has been done and untouched for 24 hours'], then: ['close it']}));
  const words = {stale_review: 'stale done close suggest checks that suggest', approval_expiry: 'approval expire close done', due_reminders: 'due remind'};
  for (const j of jobs) j.search = [j.name, j.what, ...j.when, ...j.then, j.department.name, j.group, words[j.key] || ''].join(' ').toLowerCase();
  return {now: new Date().toISOString(), departments: ['Engineering'], task_rule_departments: [{id: 'engineering', name: 'Engineering'}, {id: 'marketing', name: 'Marketing'}], task_rule_department: 'engineering', can_create_task_rule: role !== 'member', jobs};
}

async function open(browser, role) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], asked = [], writes = [];
  const data = system(role);
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role: role === 'owner' ? 'owner' : 'human', bot_admin: role === 'admin',
      name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/access') return json({rules: {}});
    if (p === '/api/v2/system-routines') { asked.push(req.method()); return json(data); }
    if (p.startsWith('/api/v2/task-rules')) {
      const body = req.postDataJSON();
      writes.push({method: req.method(), key: p.split('/').at(-1), body});
      if (body.name === 'Rejected') return json({error: {detail: 'Only this department can edit the rule', code: 'forbidden'}}, 403);
      let j = data.jobs.find(x => x.key === p.split('/').at(-1));
      if (!j) { j = {...data.jobs.find(x => x.kind === 'task_rule'), key: 'task-rule-new'}; data.jobs.push(j); }
      Object.assign(j, body);
      if (body.department) j.department = {id: body.department, name: body.department === 'engineering' ? 'Engineering' : 'Marketing', in_chart: true};
      if (body.triggers) j.when = [`a task has been done and untouched for ${body.triggers[0].hours} hours`];
      j.search = [j.name, ...j.when, ...j.then, j.department.name].join(' ').toLowerCase();
      return json({job: j});
    }
    const one = p.match(/^\/api\/v2\/system-routines\/([a-z_]+)(\/run)?$/);
    if (one) {
      const body = req.postDataJSON() || {}, j = data.jobs.find(x => x.key === one[1]);
      writes.push({method: req.method(), key: one[1], body});
      if (one[2]) {
        j.last = {started: new Date().toISOString(), finished: new Date().toISOString(), ok: true, result: '4 checked, 1 suggestion', runs: 1};
        return json({job: j, running: false});
      }
      if (body.pilots) j.pilots = body.pilots;
      if (body.on != null) j.on = body.on;
      if (body.preset) { j.preset = body.preset; j.schedule = body.preset === '4h' ? 'Every 4 hours' : j.schedule; j.when = body.preset === '4h' ? ['every 4 hours'] : j.when; }
      return json({job: j});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  await page.locator('#settings-more summary').click();
  await page.locator('[data-settings-tab="system"]').click();
  await page.locator('[data-system-routine="stale_review"]').waitFor();
  return {page, errors, asked, writes};
}
const row = (page, key) => page.locator(`[data-system-routine="${key}"]`);
// A change on a row is saved once its write has gone, the row is no longer busy, and it shows no error ("Saved" is a toast).
const saved = async (page, writes, count, key) => {
  for (let i = 0; i < 200 && writes.length < count; i++) await page.waitForTimeout(25);
  assert.ok(writes.length >= count, `the ${key} change is sent`);
  await page.waitForFunction(k => !SYSTEM_ROUTINE_BUSY.has(k) && !document.querySelector(`[data-system-routine="${k}"] [data-sysr-status].err`), key);
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const role of ['owner', 'admin', 'member']) {
      const {page, errors, asked, writes} = await open(browser, role);
      // One flat list, no wake rules or bot routines: no group headings, a search box on top.
      const headings = await page.locator('#settings-system h2:visible').allInnerTexts();
      assert.equal(headings[0].trim(), 'What Tico runs on its own', role);
      assert.ok(!headings.some(h => /wakes|routines/i.test(h)), role + ': ' + headings);
      assert.equal(await page.locator('#set-system-routines h3').count(), 0, 'no group headings');
      assert.equal(await page.locator('#set-system-routines [data-sysr-search]').count(), 1);
      assert.equal(await page.locator('#set-system-routines [data-system-routine]').count(), 11);
      // Each row: WHEN ... THEN ..., one department tag.
      assert.equal(await row(page, 'stale_review').locator('[data-sysr-rule]').innerText(),
        'WHEN every hour THEN check stale tickets AND suggest to each owner: mark done, close or check it');
      assert.equal(await row(page, 'stale_review').locator('[data-sysr-dept]').count(), 1);
      assert.equal(await row(page, 'stale_review').locator('[data-sysr-dept]').innerText(), 'Engineering');
      const stale = await row(page, 'stale_review').innerText();
      assert.match(stale, /Stale ticket review[\s\S]*AI: Gateway[\s\S]*\$0\.42 this month[\s\S]*9 checked, 2 suggestions[\s\S]*20m ago[\s\S]*in 40m/);
      const failed = row(page, 'still_wanted').locator('[data-sysr-result]');
      assert.equal(await failed.innerText(), 'Failed: the database was busy');
      assert.notEqual(await failed.evaluate(el => getComputedStyle(el).color), await row(page, 'stale_review').locator('[data-sysr-result]').evaluate(el => getComputedStyle(el).color), 'a failure is red');
      assert.equal(await row(page, 'learnings').locator('[data-sysr-fallback]').innerText(), 'Ran on your own key: Gateway unavailable');
      assert.match(await row(page, 'stall_checks').innerText(), /Tico needs this to run/);
      assert.equal(await row(page, 'stall_checks').locator('input, select, button').count(), 0, 'housekeeping has no controls');
      assert.match(await row(page, 'due_reminders').innerText(), /Not run yet/);
      // Search: "done" finds what touches done tasks; an action word matches by what a row does; a department matches all.
      const search = page.locator('[data-sysr-search]');
      const shown = async () => page.locator('#set-system-routines [data-system-routine]').evaluateAll(els => els.map(el => el.dataset.systemRoutine));
      await search.fill('done');
      assert.deepEqual(await shown(), ['stale_review', 'approval_expiry', 'task-rule-close-after-done']);
      await search.fill('remind');
      assert.deepEqual(await shown(), ['due_reminders', 'approval_expiry'], 'its action, or its group word');
      await search.fill('fresh session');
      assert.deepEqual(await shown(), ['topic_fresh']);
      await search.fill('engineering');
      assert.equal((await shown()).length, 11);
      await search.fill('zzz');
      assert.match(await page.locator('[data-sysr-none]').innerText(), /No job matches/);
      await search.fill('');
      if (role === 'member') {
        // A member reads the list: no switch, schedule choice or Run now anywhere.
        assert.equal(await page.locator('#set-system-card input:not([data-sysr-search]), #set-system-card select, #set-system-card button').count(), 0);
        assert.match(await row(page, 'stale_review').locator('[data-sysr-state]').innerText(), /On/);
        assert.equal(await page.locator('[data-sysr-owner]').count(), 0);
      } else {
        // The six (and Topic changed) have a switch; an AI job is never offered every 15 minutes.
        const presets = key => row(page, key).locator('[data-sysr-preset] option').evaluateAll(els => els.map(el => el.value));
        assert.deepEqual(await presets('stale_review'), ['hourly', '4h', 'daily']);
        assert.deepEqual(await presets('still_wanted'), ['15m', 'hourly', '4h', 'daily']);
        assert.equal(await row(page, 'learnings').locator('[data-sysr-preset]').count(), 0, 'one choice: its time');
        assert.equal(await row(page, 'learnings').locator('[data-sysr-at]').inputValue(), '03:00');
        assert.equal(await row(page, 'still_wanted').locator('[data-sysr-at]').inputValue(), '09:00');
        assert.equal(await row(page, 'topic_fresh').locator('[data-sysr-on]').isChecked(), false);
        assert.equal(await row(page, 'topic_fresh').locator('[data-sysr-run]').count(), 0, 'a switch only');
        // Stalled task check runs on a happening: a switch, no schedule choice, no Run now (the server has nothing to run).
        assert.equal(await row(page, 'stall_triage').locator('[data-sysr-on]').count(), 1);
        assert.equal(await row(page, 'stall_triage').locator('[data-sysr-run], [data-sysr-preset]').count(), 0);
        // Health scan: the owner changes it; an admin reads it and is told why.
        if (role === 'owner') assert.equal(await row(page, 'health_scan').locator('[data-sysr-on]').count(), 1);
        else {
          assert.equal(await row(page, 'health_scan').locator('input, select, button').count(), 0);
          assert.equal(await row(page, 'health_scan').locator('[data-sysr-owner]').innerText(), 'Only the owner changes this');
        }
        // Every 4 hours: saved, and the row reads it back.
        await row(page, 'stale_review').locator('[data-sysr-preset]').selectOption('4h');
        await saved(page, writes, 1, 'stale_review');
        assert.match(await row(page, 'stale_review').locator('[data-sysr-rule]').innerText(), /^WHEN every 4 hours THEN/);
        // Switch Topic changed on; Run now on the stale review lands its result on the row.
        await row(page, 'topic_fresh').locator('[data-sysr-on]').check();
        await saved(page, writes, 2, 'topic_fresh');
        await row(page, 'stale_review').locator('[data-sysr-run]').click();
        await page.waitForFunction(() => /4 checked, 1 suggestion/.test(document.querySelector('[data-system-routine="stale_review"] [data-sysr-result]')?.textContent || ''));
        // The daily time saves once, when the box is left, not after the hour alone.
        const at = row(page, 'still_wanted').locator('[data-sysr-at]');
        await at.click();
        await page.keyboard.type('1030');
        await page.keyboard.type('AM');
        assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-sysr-at]')), true, 'focus stays while typing');
        await page.keyboard.press('Enter');
        await saved(page, writes, 4, 'still_wanted');
        assert.deepEqual(writes, [{method: 'PUT', key: 'stale_review', body: {preset: '4h'}},
          {method: 'PUT', key: 'topic_fresh', body: {on: true}}, {method: 'POST', key: 'stale_review', body: {}},
          {method: 'PUT', key: 'still_wanted', body: {at: '10:30'}}]);
      }
      assert.deepEqual(asked, ['GET']);
      assert.deepEqual(errors, []);
      if (role !== 'member') {
        const tidy = row(page, 'memory_tidy');
        assert.equal(await tidy.locator('[data-sysr-on]').isChecked(), false);
        assert.equal(await tidy.locator('[data-sysr-run]').isDisabled(), true);
        assert.match(await tidy.locator('[data-sysr-run]').getAttribute('title'), /Switch it on/);
        await tidy.locator('[data-memory-tidy-edit]').click();
        const dialog = page.locator('dialog:visible');
        assert.equal(await dialog.locator('[data-pilot-row]').count(), 0);
        await dialog.locator('[data-add]').click();
        await dialog.locator('[data-bot]').fill('botops');
        assert.deepEqual(await dialog.locator('[data-model] option').evaluateAll(es => es.map(e => e.value)), ['', 'codex:gpt-6.1-sol', 'claude:claude-sonnet-5-5']);
        await dialog.locator('[data-model]').selectOption('claude:claude-sonnet-5-5');
        assert.equal(await dialog.locator('[data-effort]').inputValue(), 'low');
        await dialog.locator('[type="submit"]').click();
        await dialog.waitFor({state: 'detached'});
        assert.deepEqual(writes.at(-1).body, {pilots: [{bot: 'botops', model: 'claude:claude-sonnet-5-5', effort: 'low'}]});
        await tidy.locator('[data-memory-tidy-edit]').click();
        await page.locator('dialog:visible [data-remove]').click();
        await page.locator('dialog:visible [type="submit"]').click();
        await page.locator('.task-rule-dialog').waitFor({state: 'detached'});
        assert.deepEqual(writes.at(-1).body, {pilots: []});
        const count = writes.length;
        await tidy.locator('[data-sysr-on]').check();
        await saved(page, writes, count + 1, 'memory_tidy');
        assert.equal(await tidy.locator('[data-sysr-run]').isDisabled(), false);
        await tidy.locator('[data-sysr-run]').click();
        await saved(page, writes, count + 2, 'memory_tidy');
        assert.equal(writes.at(-1).method, 'POST');
      }
      await page.close();
    }
    {
      const {page, errors, writes} = await open(browser, 'department');
      const rule = () => row(page, 'task-rule-close-after-done');
      assert.equal(await row(page, 'stale_review').locator('button,select,input').count(), 0);
      await rule().locator('[data-task-rule-edit]').click();
      assert.equal(await page.locator('.task-rule-dialog select[name=department]').inputValue(), 'engineering');
      await page.locator('.task-rule-dialog input[name=ruleName]').fill('Rejected');
      await page.locator('.task-rule-dialog button[type=submit]').click();
      await page.locator('[data-task-rule-error]').filter({hasText: /department/}).waitFor();
      assert.equal(await page.locator('.task-rule-dialog input[name=ruleName]').inputValue(), 'Rejected');
      await page.locator('.task-rule-dialog input[name=ruleName]').fill('Close reviewed exports');
      await page.locator('.task-rule-dialog input[name=hours]').fill('48');
      await page.locator('.task-rule-dialog button[type=submit]').click();
      await page.locator('.task-rule-dialog').waitFor({state: 'detached'});
      assert.match(await rule().innerText(), /48 hours/);
      const before = writes.length;
      await rule().locator('[data-sysr-on]').uncheck();
      await saved(page, writes, before + 1, 'task-rule-close-after-done');
      assert.equal(await rule().locator('[data-sysr-on]').isChecked(), false);
      await page.locator('[data-task-rule-new]').click();
      assert.equal(await page.locator('.task-rule-dialog select[name=department]').inputValue(), 'engineering');
      assert.deepEqual(await page.locator('.task-rule-dialog select[name=department] option').evaluateAll(es => es.map(e => e.value)), ['', 'engineering', 'marketing']);
      await page.locator('.task-rule-dialog input[name=ruleName]').fill('Close exports');
      for (const dark of [false, true]) {
        await page.evaluate(d => document.documentElement.dataset.theme = d ? 'dark' : 'light', dark);
        for (const width of [1440, 390]) {
          await page.setViewportSize({width, height: 900});
          assert.equal(await page.locator('.task-rule-dialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
        }
      }
      await page.locator('.task-rule-dialog button[type=submit]').click();
      await page.locator('.task-rule-dialog').waitFor({state: 'detached'});
      assert.equal(await row(page, 'task-rule-new').count(), 1);
      assert.equal(writes.at(-1).method, 'POST');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A phone: the row stacks and nothing spills sideways.
      const {page} = await open(browser, 'owner');
      await page.setViewportSize({width: 390, height: 844});
      const wide = await page.evaluate(() => document.querySelector('#set-system-routines').scrollWidth - document.querySelector('#set-system-routines').clientWidth);
      assert.ok(wide <= 1, `no sideways scroll on a phone (${wide}px)`);
      await page.close();
    }
    console.log('settings-system-routines: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
