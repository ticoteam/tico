// Scheduled calls (docs/meet.md, Scheduled calls): the Meetings page's Call dialog books a call with Schedule (once a
// time is picked: then Schedule is the filled button; at Now, Start is; no window opens, the dialog closes and focus goes
// back to Call now), the call shows in Upcoming at the top of the page with when, who, Join and, for who may change it,
// its ⋯, and in its thread's header as the phone and when (Join, or a menu with Join, Change time and Cancel call). Change
// time reopens the dialog filled in and sends the new time; Cancel call, once confirmed, ends the booking. Desktop and a 390px phone; every name is synthetic (Acme, acme.example).
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots, in both themes: scheduled-calls-<name>-<desktop|phone>-<dark|light>.png.
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

async function shot(page: Page, name: string, phone: boolean, theme: string) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.evaluate(() => document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))));
  await page.screenshot({path: path.join(shots, `scheduled-calls-${name}-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled'});
}
const wide = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth);

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch();
  for (const phone of [false, true]) for (const theme of shots ? ['dark', 'light'] : ['dark']) {
    const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block',
      reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles', locale: 'en-US', colorScheme: theme as 'dark' | 'light',
      ...(phone ? {isMobile: true, hasTouch: true} : {})});
    await context.addInitScript((t: string) => {
      localStorage.setItem('tico.theme', t);
      window.__opened = 0;
      const open = window.open.bind(window);
      window.open = (...a: Parameters<typeof window.open>) => { window.__opened++; return open(...a); };
    }, theme);
    const page = await context.newPage();
    // 9:10 in the morning in Los Angeles: the next half hour is 9:30.
    await page.clock.setFixedTime(new Date('2026-11-18T17:10:00Z'));
    const errors: string[] = [], booked: Record<string, unknown>[] = [], changed: Record<string, unknown>[] = [], ended: string[] = [], asked: string[] = [];
    let upcoming: Record<string, unknown>[] = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => { asked.push(d.message()); return d.accept(); });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname !== 'tico-ui.test') return route.fallback();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const font = p.match(/\/vendor\/fonts\/([^/]+\.woff2)$/);
      if (font && fs.existsSync(uiFile('vendor/fonts/' + font[1]!))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/' + font[1]!))});
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
        upcoming = [{id: 'cs', thread_id: 't-dm', title: call.title, scheduled_start: call.scheduled_start, scheduled_end: call.scheduled_end, people: ['human:ana', 'bot:ops'], can_edit: true}];
        return json({call});
      }
      if (p === '/api/v2/calls/cs' && req.method() === 'PATCH') {
        const body = JSON.parse(req.postData() || '{}') as {starts_at: string; ends_at: string};
        changed.push(body);
        upcoming = upcoming.map(v => ({...v, scheduled_start: body.starts_at, scheduled_end: body.ends_at}));
        return json({call: {...upcoming[0], state: 'scheduled', provider: 'tico'}});
      }
      if (p === '/api/v2/calls/cs/end' && req.method() === 'POST') {
        ended.push('cs');
        const was = upcoming[0];
        upcoming = [];
        return json({call: {...was, state: 'cancelled', provider: 'tico'}});
      }
      if (p === '/api/v2/threads/t-dm/calls') return json({calls: [], next_before: null, available: true, providers: ['tico'], next_scheduled: upcoming[0] ? {...upcoming[0], state: 'scheduled', provider: 'tico'} : null});
      if (p === '/api/v2/threads/t-dm/timeline') return json({thread: dm, breadcrumb: [], next_cursor: null, messages: [], bot_status: []});
      if (p === '/api/v2/threads/t-dm') return json(dm);
      if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [dm], catch_up: 0, next_cursor: null, dms: {}});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/meetings');
    await page.locator('.ph #meet-call-now').click();
    const dialog = page.locator('dialog.meet-call-dialog');
    const schedule = dialog.locator('.meet-call-schedule'), start = dialog.locator('.meet-call-start'), when = dialog.locator('[data-call-at-label]');
    assert.equal(await dialog.locator('h2').innerText(), 'Call');
    assert.ok(await schedule.isDisabled() && await start.isDisabled(), 'Start and Schedule wait for someone to call');
    // At rest: Now, so Start is the filled button; no length for a call now.
    assert.equal(await when.innerText(), 'Now');
    assert.ok(await start.evaluate(b => b.classList.contains('primary')) && await schedule.evaluate(b => b.classList.contains('ghost')));
    assert.ok(!await dialog.locator('select[name="minutes"]').isVisible());
    await dialog.locator('.who-pick-q').click();
    await page.locator('.who-pick-list [role="option"][data-who="bot:ops"]').click();
    await dialog.locator('.nm-chip[data-actor="bot:ops"]').waitFor();
    await dialog.locator('input[name="title"]').fill('Acme planning');
    await dialog.locator('.who-pick-q').blur();
    // A time picked (the picker opens at the next half hour): Schedule is the filled button, 30 minutes long by default.
    const starts = dialog.locator('input[name="starts"]');
    assert.equal(await starts.inputValue(), '2026-11-18T09:30');
    await starts.fill('2026-11-18T09:30');
    assert.equal(await when.innerText(), 'Today 9:30 AM');
    assert.ok(await schedule.evaluate(b => b.classList.contains('primary')) && await start.evaluate(b => b.classList.contains('ghost')));
    assert.equal(await dialog.locator('select[name="minutes"]').inputValue(), '30');
    assert.equal(await wide(page), phone ? 390 : 1280, 'nothing sideways');
    await shot(page, 'dialog', phone, theme);
    await schedule.click();
    await dialog.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'meet-call-now', 'focus goes back to Call now');
    assert.equal(booked.length, 1);
    const [one] = booked as [{starts_at: string; ends_at: string; bots: string[]; title: string; provider: string}];
    assert.equal(one.starts_at, '2026-11-18T17:30:00.000Z', 'the time picked');
    assert.equal(Date.parse(one.ends_at) - Date.parse(one.starts_at), 30 * 60000, '30 minutes');
    assert.deepEqual([one.bots, one.title, one.provider], [['ops'], 'Acme planning', 'tico']);
    assert.equal(await page.evaluate(() => window.__opened), 0, 'no window opens');
    assert.ok(!page.url().includes('#/call/'), 'this tab stays on Meetings');
    // Upcoming: the call, who, Join and its ⋯; a phone says only the time for today.
    const row = page.locator('#meet-upcoming .meet-upcoming-row[data-upcoming="cs"]');
    await row.waitFor();
    assert.equal(await row.locator('.meet-upcoming-t').innerText(), 'Acme planning');
    assert.ok(await row.locator('[data-call-join="cs"]').isVisible(), 'Join');
    assert.equal(await row.locator('.meet-upcoming-when').innerText(), phone ? '9:30 AM' : 'Today 9:30 AM');
    assert.equal(await row.locator('.meet-upcoming-ppl > span').count(), 2, 'who');
    const more = row.locator('[data-upcoming-more="cs"]');
    const box = await more.boundingBox();
    assert.deepEqual([box?.width, box?.height], phone ? [44, 44] : [32, 32]);
    assert.equal(await wide(page), phone ? 390 : 1280, 'nothing sideways');
    await shot(page, 'upcoming', phone, theme);
    // Change time: the dialog again, filled in; Save sends the new time.
    await more.click();
    const menu = page.locator('.call-sched-menu');
    await menu.waitFor();
    await shot(page, 'menu', phone, theme);
    await menu.locator('[data-k="change"]').click();
    await dialog.waitFor();
    assert.equal(await dialog.locator('input[name="title"]').inputValue(), 'Acme planning');
    assert.equal(await when.innerText(), 'Today 9:30 AM');
    assert.equal(await dialog.locator('.meet-call-start').count(), 0, 'no Start while changing a time');
    await starts.fill('2026-11-18T10:00');
    await dialog.locator('select[name="minutes"]').selectOption('45');
    await dialog.locator('.meet-call-save').click();
    await dialog.waitFor({state: 'detached'});
    assert.deepEqual(changed, [{starts_at: '2026-11-18T18:00:00.000Z', ends_at: '2026-11-18T18:45:00.000Z'}]);
    await row.locator('.meet-upcoming-when', {hasText: '10:00 AM'}).waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-upcoming-more')), 'cs', 'focus goes back to the ⋯');
    assert.deepEqual(await page.locator('.toast').allInnerTexts(), ['Time changed'], 'one toast, the newest');
    // Without can_edit, no ⋯.
    upcoming = upcoming.map(v => ({...v, can_edit: false}));
    await page.evaluate('meetUpcoming(MEET)');   // the page's own minute refresh, now
    await more.waitFor({state: 'detached'});
    upcoming = upcoming.map(v => ({...v, can_edit: true}));
    await page.evaluate('meetUpcoming(MEET)');
    await more.waitFor();
    // The thread's header: the phone and when, which joins, with its own ⋯.
    if (!phone) {
      await page.goto('https://tico-ui.test/#/thread/t-dm');
      const next = page.locator('.thv-head .thv-call-next');
      await next.waitFor();
      assert.equal(await next.innerText(), 'Today 10:00 AM');
      assert.equal(await page.locator('.thv-head [data-call="start"]').count(), 0, 'one button for the call');
      assert.equal(await page.locator('.thv-head .call-sched-more').count(), 0, 'no second ⋯ in the header');
      await shot(page, 'header', phone, theme);
      // For who may change it, the time opens Join, Change time and Cancel call.
      await next.click();
      const headMenu = page.locator('.call-sched-menu');
      assert.deepEqual(await headMenu.locator('[data-k]').allInnerTexts(), ['Join', 'Change time', 'Cancel call']);
      await shot(page, 'header-menu', phone, theme);
      await page.keyboard.press('Escape');
      await headMenu.waitFor({state: 'detached'});
      await page.goto('https://tico-ui.test/#/meetings');
      await row.waitFor();
    }
    // Cancel call, once asked: the booking ends and leaves Upcoming.
    await more.click();
    await menu.locator('[data-k="cancel"]').click();
    await row.waitFor({state: 'detached'});
    assert.deepEqual([asked.length, ended], [1, ['cs']]);
    assert.deepEqual(await page.locator('.toast').allInnerTexts(), ['Call cancelled']);
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log('scheduled calls: ok');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
