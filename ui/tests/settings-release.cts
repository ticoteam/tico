import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// Release now on the Release Owner's Settings card (docs/pipelines-v3.md D13). Guards: the button is there with no
// task pending (the count never decides it); the note goes with the press; already_open says so and links the open
// Release task only when the answer carries its id. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [], posts: unknown[] = [];
    const answers = [{sent: true, reason: null, task: 't-new', tasks: 0}, {sent: false, reason: 'already_open', task: 't-rel', tasks: 0},
      {sent: false, reason: 'already_open', task: null, tasks: 0}];
    page.on('pageerror', (e: Error) => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/employees') return json([{name: 'rm', display_name: 'Release Owner', status: 'active', can_chat: true, can_manage: true,
        users: [{id: 'ana', name: 'Ana'}], schedules: [], revision: 1}]);
      if (p === '/api/status') return json({cloud: true, active: [], recent_runs: [], health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: [{bot: 'rm', state: 'idle'}]});
      if (p === '/api/v2/release-schedule') return json({on: true, times: ['10:00'], zone: 'UTC', bot: null, writer: null, release_manager: 'rm',
        technical_writer: null, next: null, last: null, pending: 0, can_change: true, can_release: true});
      if (p === '/api/v2/release/now') { posts.push(req.postDataJSON()); return json(answers.shift()); }
      if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/rm/settings');
    const now = page.locator('#set-release [data-release-now]'), status = page.locator('#set-release [data-release-status]');
    await now.waitFor();
    assert.equal(await page.locator('#set-release [data-release-pending]').innerText(), '0 tasks');
    assert.equal(await now.isEnabled(), true, 'nothing pending still offers Release now');

    // Filed: the note goes with it and clears.
    await page.locator('#set-release [data-release-note]').fill('Ship the fix');
    await now.click();
    await page.waitForFunction(() => document.querySelector('#set-release [data-release-status]')?.textContent === 'Asked for a release');
    assert.deepEqual(posts[0], {note: 'Ship the fix'});
    assert.equal(await page.locator('#set-release [data-release-note]').inputValue(), '');
    assert.equal(await status.locator('a').count(), 0);

    // Already open, readable: the words link the open task.
    await now.click();
    await status.locator('a[data-release-task]').waitFor();
    assert.equal(await status.innerText(), 'A release task is open');
    assert.equal(await status.locator('a').getAttribute('href'), '#/task/t-rel');
    assert.deepEqual(posts[1], {});

    // Already open, not readable: the same words, no link.
    await now.click();
    await page.waitForFunction(() => !document.querySelector('#set-release [data-release-task]'));
    assert.equal(await status.innerText(), 'A release task is open');
    assert.equal(posts.length, 3);
    assert.deepEqual(errors, []);
    await page.close();
  } finally { await browser.close(); }
  console.log('settings-release ok');
})().catch(error => { console.error(error); process.exit(1); });
