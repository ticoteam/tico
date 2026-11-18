// Scheduled calls (docs/meet.md, Scheduled calls): the Meetings page's Call now books a call with Schedule (the time
// picked, 30 minutes by default; no window opens, the dialog closes), and the call shows in Upcoming at the top of the
// page with when, who and Join, and in its thread's header as "Call · <when>". Desktop and a 390px phone; every name is synthetic (Acme, acme.example).
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots: scheduled-calls-<name>-<desktop|phone>-<dark|light>.png.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare const window: Window & typeof globalThis & {__opened: number};

type Page = import('playwright').Page;
const shots = process.env.TICO_SCREENSHOT_DIR;
const me = {member: true, following: true, notify: 'live', last_read_seq: 0, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false};
const dm = {id: 't-dm', title: 'Ops', audience: 'personal', audience_group_id: null, dm_with: [{actor: 'bot:ops'}], dm_with_count: 1, last_seq: 0,
  last_message_at: new Date().toISOString(), me, members: [], live_call: null};

async function themed(page: Page, name: string, phone: boolean) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.screenshot({path: path.join(shots, `scheduled-calls-${name}-${phone ? 'phone' : 'desktop'}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch();
  for (const phone of [false, true]) {
    const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block',
      reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles', ...(phone ? {isMobile: true, hasTouch: true} : {})});
    const page = await context.newPage();
    // 9:10 in the morning in Los Angeles: the next half hour is 9:30.
    await page.clock.setFixedTime(new Date('2026-11-18T17:10:00Z'));
    const errors: string[] = [], booked: Record<string, unknown>[] = [];
    let upcoming: Record<string, unknown>[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.addInitScript(() => {
      window.__opened = 0;
      const open = window.open.bind(window);
      window.open = (...a: Parameters<typeof window.open>) => { window.__opened++; return open(...a); };
    });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname !== 'tico-ui.test') return route.fallback();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true, config: {features: {tico_meet: true}}});
      if (p === '/api/v2/config') return json({features: {tico_meet: true}});
      if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}], org_groups: []});
      if (p === '/api/v2/meetings') return json({meetings: [], pending_count: 0});
      if (p === '/api/meetings/sources') return json({sources: []});
      if (p === '/api/v2/threads/dm') return json(dm);
      if (p === '/api/v2/calls/upcoming') return json({calls: upcoming});
      if (p === '/api/v2/threads/t-dm/calls' && req.method() === 'POST') {
        const body = JSON.parse(req.postData() || '{}') as Record<string, unknown>;
        booked.push(body);
        const call = {id: 'cs', thread_id: 't-dm', title: body.title || 'Call', state: 'scheduled', provider: 'tico', connector: 'human:ana',
          scheduled_start: body.starts_at, scheduled_end: body.ends_at, participants: [], bots: body.bots || [], ring: false};
        upcoming = [{...call, people: ['human:ana']}];
        return json({call});
      }
      if (p === '/api/v2/threads/t-dm/calls') return json({calls: upcoming, next_before: null, available: true, providers: ['tico']});
      if (p === '/api/v2/threads/t-dm/timeline') return json({thread: dm, breadcrumb: [], next_cursor: null, messages: [], bot_status: []});
      if (p === '/api/v2/threads/t-dm') return json(dm);
      if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [dm], catch_up: 0, next_cursor: null, dms: {}});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/meetings');
    await page.locator('.ph #meet-call-now').click();
    const dialog = page.locator('dialog.meet-call-dialog');
    const schedule = dialog.locator('.meet-call-schedule');
    assert.ok(await schedule.isDisabled(), 'Schedule waits for someone to call');
    // The next half hour, 30 minutes long.
    assert.equal(await dialog.locator('input[name="starts"]').inputValue(), '2026-11-18T09:30');
    assert.equal(await dialog.locator('select[name="minutes"]').inputValue(), '30');
    await dialog.locator('.who-pick-q').click();
    await page.locator('.who-pick-list [role="option"][data-who="bot:ops"]').click();
    await dialog.locator('.nm-chip[data-actor="bot:ops"]').waitFor();
    await dialog.locator('input[name="title"]').fill('Acme planning');
    await dialog.locator('.who-pick-q').blur();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), phone ? 390 : 1280, 'nothing sideways');
    await themed(page, 'dialog', phone);
    await schedule.click();
    await dialog.waitFor({state: 'detached'});
    assert.equal(booked.length, 1);
    const [one] = booked as [{starts_at: string; ends_at: string; bots: string[]; title: string; provider: string}];
    assert.equal(one.starts_at, '2026-11-18T17:30:00.000Z', 'the time picked');
    assert.equal(Date.parse(one.ends_at) - Date.parse(one.starts_at), 30 * 60000, '30 minutes');
    assert.deepEqual([one.bots, one.title, one.provider], [['ops'], 'Acme planning', 'tico']);
    assert.equal(await page.evaluate(() => window.__opened), 0, 'no window opens');
    assert.ok(!page.url().includes('#/call/'), 'this tab stays on Meetings');
    // Upcoming: the call, with Join.
    const row = page.locator('#meet-upcoming .meet-upcoming-row[data-upcoming="cs"]');
    await row.waitFor();
    assert.equal(await row.locator('.meet-upcoming-t').innerText(), 'Acme planning');
    assert.ok(await row.locator('[data-call-join="cs"]').isVisible(), 'Join');
    assert.ok((await row.locator('.meet-upcoming-when').innerText()).length > 0, 'when');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), phone ? 390 : 1280, 'nothing sideways');
    await themed(page, 'upcoming', phone);
    // The thread's header: when it is, beside the phone.
    if (!phone) {
      await page.goto('https://tico-ui.test/#/thread/t-dm');
      const next = page.locator('.thv-head .thv-call-next');
      await next.waitFor();
      assert.ok((await next.innerText()).length > 0);
      await themed(page, 'header', phone);
    }
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log('scheduled calls: ok');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
