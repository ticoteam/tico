// Automations > System jobs (ui/app/settings-recurring.ts, #/automations/system-jobs): one flat list of Tico's own jobs, no group headings, a search box
// that matches name, trigger, action and department; each row reads WHEN ... THEN ... with one department tag, its
// schedule, last run in words (a failure in red with its reason), next run, and for a Decide job the route that answered,
// a fallback in words and this month's spend. Housekeeping says Tico needs it. An owner or admin gets a switch, a
// schedule preset (no every 15 minutes for an AI job) and Run now on controlled jobs; a member none.
// Fixtures only. TICO_UI_SHOTS=<dir> saves the screen at 1440 and 390, dark.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
// Page globals this spec reads (file-local; see ui/tests/tsconfig.json moduleDetection).
declare const window: Window & typeof globalThis & {
  ticoTheme: {set(theme: string): void};
};

type Page = import('playwright').Page;
type Job = {key: string; name: string; what: string; schedule: string; ai: boolean; controllable: boolean; needed: boolean; records: boolean;
  last: {started_at: string; finished_at: string; ok: boolean; result: string; runs: number} | null; next: string | null; group: string;
  when: string[]; then: string[]; department: {id?: string; name: string; in_chart: boolean}; switch: boolean; on: boolean;
  preset: string | null; at: string | null; presets: {key: string; label: string}[]; owner_only: boolean; can_change: boolean; search: string;
  route?: string; fallback?: string; spent_this_month_usd?: number};
// One recorded write to the system-routines API: the request method, the job key and the body the page sent.
type Write = {method: string; key: string; body: unknown};

declare var ticoTheme: {set(theme: string): unknown};
declare var SYSTEM_ROUTINE_BUSY: Set<string>;

const soon = new Date(Date.now() + 40 * 60000).toISOString(), before = new Date(Date.now() - 20 * 60000).toISOString();
const ALL: {key: string; label: string}[] = [{key: '15m', label: 'Every 15 minutes'}, {key: 'hourly', label: 'Hourly'}, {key: '4h', label: 'Every 4 hours'}, {key: 'daily', label: 'Daily at a time'}];
const AI = ALL.slice(1);
const job = (key: string, name: string, extra: Partial<Job> = {}): Job => ({key, name, what: name + ' does a thing.', schedule: 'Hourly', ai: false,
  controllable: false, needed: false, records: true, last: null, next: null, group: 'housekeeping', when: ['every hour'],
  then: ['do ' + name.toLowerCase()], department: {name: 'Engineering', in_chart: true}, switch: false, on: true,
  preset: null, at: null, presets: [], owner_only: false, can_change: false, search: '', ...extra});
function system(role: string) {
  const admin = ['owner', 'admin'].includes(role);
  const ctl = (extra: Partial<Job>) => ({switch: true, controllable: true, can_change: admin, ...extra});
  const jobs = [
    job('stale_review', 'Stale task review', ctl({ai: true, route: 'Company key', fallback: 'Ran on your own key: Gateway unavailable', group: 'checks', preset: 'hourly', presets: AI,
      then: ['check stale tasks', 'suggest to each owner: mark done, close or check it'], on: true,
      spent_this_month_usd: 0.42, next: soon, last: {started_at: before, finished_at: before, ok: true, result: '9 checked, 2 suggestions', runs: 1}})),
    job('kpi_asks', 'KPI stale asks', ctl({schedule: 'Daily at 09:00', preset: 'daily', at: '09:00', presets: ALL, group: 'checks', next: soon,
      when: ['every day at 09:00'], last: {started_at: before, finished_at: before, ok: false, result: 'Failed: the database was busy', runs: 1}})),
    job('daily_check', 'Daily check', ctl({on: false, schedule: 'Daily at 03:30', group: 'checks', preset: 'daily', at: '03:30', presets: [{key: 'daily', label: 'Daily at a time'}]})),
    job('due_reminders', 'Due reminders', {schedule: 'Every 10 seconds', group: 'reminders', when: ['every 10 seconds'], then: ['tell a bot when one of its tasks reaches its due time']}),
    job('approval_expiry', 'Approval expiry', {group: 'reminders', then: ['take approvals and asks nobody will act on out of Needs you']}),
    job('stall_checks', 'Stall checks', {needed: true, schedule: 'Every minute', when: ['every minute']}),
    job('topic_fresh', 'Start fresh when the topic changes', {switch: true, can_change: admin, on: false, group: 'chat', schedule: 'When it happens',
      when: ['a message in a direct chat changes the topic'], then: ['start a fresh session, like Clear']}),
  ];
  const words: Record<string, string> = {stale_review: 'stale done close suggest checks that suggest', approval_expiry: 'approval expire close done', due_reminders: 'due remind'};
  for (const j of jobs) j.search = [j.name, j.what, ...j.when, ...j.then, j.department.name, j.group, words[j.key] || ''].join(' ').toLowerCase();
  return {now: new Date().toISOString(), departments: ['Engineering', 'Marketing'], task_rule_departments: [{id: 'engineering', name: 'Engineering'}, {id: 'marketing', name: 'Marketing'}], jobs};
}

async function open(browser: import('playwright').Browser, role: string) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors: string[] = [], asked: string[] = [], writes: Write[] = [];
  const data = system(role);
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
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
    const one = p.match(/^\/api\/v2\/system-routines\/([a-z_]+)(\/run)?$/);
    if (one) {
      const body = req.postDataJSON() || {}, j = data.jobs.find(x => x.key === one[1])!;
      writes.push({method: req.method(), key: one[1]!, body});
      if (one[2]) {
        j.last = {started_at: new Date().toISOString(), finished_at: new Date().toISOString(), ok: true, result: '4 checked, 1 suggestion', runs: 1};
        return json({job: j, running: false});
      }
      if (body.on != null) j.on = body.on;
      if (body.preset) { j.preset = body.preset; j.schedule = body.preset === '4h' ? 'Every 4 hours' : j.schedule; j.when = body.preset === '4h' ? ['every 4 hours'] : j.when; }
      return json({job: j});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/automations/system-jobs');
  await page.locator('[data-system-routine="stale_review"]').waitFor();
  return {page, errors, asked, writes};
}
const row = (page: Page, key: string) => page.locator(`[data-system-routine="${key}"]`);
// A change on a row is saved once its write has gone, the row is no longer busy, and it shows no error ("Saved" is a toast).
const saved = async (page: Page, writes: Write[], count: number, key: string) => {
  for (let i = 0; i < 200 && writes.length < count; i++) await page.waitForTimeout(25);
  if (writes.length < count) {
    const box = await page.evaluate(k => { const i = document.querySelector<HTMLInputElement>(`[data-system-routine="${k}"] [data-sysr-at]`); return {value: i?.value, focus: document.activeElement?.outerHTML?.slice(0, 120)}; }, key);
    assert.fail(`the ${key} change is sent: ${JSON.stringify({writes, box})}`);
  }
  await page.waitForFunction(k => !SYSTEM_ROUTINE_BUSY.has(k) && !document.querySelector(`[data-system-routine="${k}"] [data-sysr-status].err`), key);
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    for (const role of ['owner', 'admin', 'member']) {
      const {page, errors, asked, writes} = await open(browser, role);
      // One flat list, no wake rules or bot routines: no group headings, a search box on top.
      assert.equal((await page.locator('.au-hd .au-name').innerText()).trim(), 'System jobs', role);
      assert.equal((await page.locator('.au-hd .pill').innerText()).trim(), 'Built-in', role);
      assert.equal(await page.locator('#au-root').getAttribute('data-screen'), 'detail', 'it opens in place');
      const headings = await page.locator('.au-pane h2:visible').allInnerTexts();
      assert.ok(!headings.some(h => /wakes|routines/i.test(h)), role + ': ' + headings);
      assert.equal(await page.locator('#set-system-routines h3').count(), 0, 'no group headings');
      assert.equal(await page.locator('#set-system-routines [data-sysr-search]').count(), 1);
      assert.equal(await page.locator('#set-system-routines [data-system-routine]').count(), 7);
      assert.equal(await row(page, 'task_roundup').count(), 0, 'Task roundup controls moved to Maintenance Find stuck');
      assert.equal(await row(page, 'learnings').count(), 0, 'Learn runs in Maintenance');
      // Search: "done" finds what touches done tasks; an action word matches by what a row does; a department matches all.
      const search = page.locator('[data-sysr-search]');
      const shown = async () => page.locator('#set-system-routines [data-system-routine]').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.systemRoutine));
      await search.fill('done');
      assert.deepEqual(await shown(), ['stale_review', 'approval_expiry']);
      await search.fill('remind');
      assert.deepEqual(await shown(), ['due_reminders', 'approval_expiry'], 'its action, or its group word');
      await search.fill('fresh session');
      assert.deepEqual(await shown(), ['topic_fresh']);
      await search.fill('engineering');
      assert.equal((await shown()).length, 7);
      await search.fill('zzz');
      assert.match(await page.locator('[data-sysr-none]').innerText(), /No job matches/);
      await search.fill('');
      assert.equal(await row(page, 'health_scan').count(), 0, 'Check health runs through Maintenance');
      if (role === 'member') {
        // A member reads the list: no switch, schedule choice or Run now anywhere.
        assert.equal(await page.locator('#set-system-routines input:not([data-sysr-search]), #set-system-routines select, #set-system-routines button').count(), 0);
        assert.match(await row(page, 'stale_review').locator('[data-sysr-state]').innerText(), /On/);
        assert.equal(await page.locator('[data-sysr-owner]').count(), 0);
      } else {
        // Controlled jobs and Topic changed have a switch; an AI job is never offered every 15 minutes.
        const presets = (key: string) => row(page, key).locator('[data-sysr-preset] option').evaluateAll(els => els.map(el => (el as HTMLOptionElement).value));
        assert.deepEqual(await presets('stale_review'), ['hourly', '4h', 'daily']);
        assert.deepEqual(await presets('kpi_asks'), ['15m', 'hourly', '4h', 'daily']);
        assert.equal(await row(page, 'daily_check').locator('[data-sysr-preset]').count(), 0, 'one choice: its time');
        assert.equal(await row(page, 'daily_check').locator('[data-sysr-at]').inputValue(), '03:30');
        assert.equal(await row(page, 'kpi_asks').locator('[data-sysr-at]').inputValue(), '09:00');
        assert.equal(await row(page, 'topic_fresh').locator('[data-sysr-on]').isChecked(), false);
        assert.equal(await row(page, 'topic_fresh').locator('[data-sysr-run]').count(), 0, 'a switch only');
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
        const at = row(page, 'kpi_asks').locator('[data-sysr-at]');
        await at.click({position: {x: 16, y: 12}});   // the hour
        await page.keyboard.type('1030');
        await page.keyboard.type('AM');
        assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-sysr-at]')), true, 'focus stays while typing');
        await page.keyboard.press('Enter');
        await saved(page, writes, 4, 'kpi_asks');
        assert.deepEqual(writes, [{method: 'PUT', key: 'stale_review', body: {preset: '4h'}},
          {method: 'PUT', key: 'topic_fresh', body: {on: true}}, {method: 'POST', key: 'stale_review', body: {}},
          {method: 'PUT', key: 'kpi_asks', body: {at: '10:30'}}]);
      }
      assert.deepEqual(asked, ['GET']);
      assert.deepEqual(errors, []);
      if (role !== 'member') {
        // A job switched off cannot run until it is switched on.
        const daily = row(page, 'daily_check');
        assert.equal(await daily.locator('[data-sysr-on]').isChecked(), false);
        assert.equal(await daily.locator('[data-sysr-run]').isDisabled(), true);
        assert.match((await daily.locator('[data-sysr-run]').getAttribute('title'))!, /Switch it on/);
        const count = writes.length;
        await daily.locator('[data-sysr-on]').check();
        await saved(page, writes, count + 1, 'daily_check');
        assert.equal(await daily.locator('[data-sysr-run]').isDisabled(), false);
        await daily.locator('[data-sysr-run]').click();
        await saved(page, writes, count + 2, 'daily_check');
        assert.equal(writes.at(-1)!.method, 'POST');
      }
      await page.close();
    }
    {
      // A phone: the row stacks and nothing spills sideways.
      const {page, errors} = await open(browser, 'owner');
      const dir = process.env.TICO_UI_SHOTS;
      const shot = async (name: string) => { if (dir) { fs.mkdirSync(dir, {recursive: true}); await page.screenshot({path: `${dir}/${name}.png`, animations: 'disabled'}); } };
      await page.evaluate(() => window.ticoTheme?.set('dark'));
      await shot('automations-system-jobs-desktop-dark');
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(100);
      const wide = await page.evaluate(() => document.querySelector('#set-system-routines')!.scrollWidth - document.querySelector('#set-system-routines')!.clientWidth);
      assert.ok(wide <= 1, `no sideways scroll on a phone (${wide}px)`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'the page does not scroll sideways');
      await shot('automations-system-jobs-phone-dark');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('settings-system-routines: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
