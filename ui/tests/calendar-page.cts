// The Calendar (ui/app/calendar.ts, ui/app/calendar-row.ts): the page opens on the viewer's week with her events, a
// due pill and a bot's runs; short runs that touch draw as one block that opens their list; the Runs toggle hides runs;
// a calendar found in the search joins the view as a chip; and the sidebar row shows the next event with its time, its
// name linking to the call. Fixtures only, no network, at a fixed Wednesday 10:00.
import type {Browser, LaunchOptions, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

const NOW = new Date(2026, 10, 11, 10, 0);        // Wednesday 11 November 2026, 10:00 local
const at = (day: number, h: number, m = 0) => new Date(2026, 10, day, h, m).toISOString();
const MEET = 'https://meet.google.com/abc-defg-hij';
const PEOPLE = [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben', email: 'ben@acme.example'}];
const BOTS = [{id: 'ops', name: 'ops', display_name: 'Ops', status: 'active', schedules: [], reports_to: 'human:ana'}];
const ANA = {ref: 'human:ana', type: 'human', id: 'ana', name: 'Ana', work_starts: '09:00', work_ends: '17:00', work_days: 'mon,tue,wed,thu,fri'};
const BEN = {ref: 'human:ben', type: 'human', id: 'ben', name: 'Ben', work_starts: '09:00', work_ends: '17:00', work_days: 'mon,tue,wed,thu,fri'};
const cal = (c: typeof ANA) => ({ref: c.ref, type: c.type, id: c.id, name: c.name});
const OPS = {ref: 'bot:ops', type: 'bot', id: 'ops', name: 'Ops'};
const event = (id: string, title: string, s: string, e: string, extra: Record<string, unknown> = {}) =>
  ({occurrence_id: id, event_id: id, calendar: cal(ANA), kind: 'event', title, start: s, end: e, response: 'accepted', ...extra});
const run = (id: string, title: string, s: string, e: string) =>
  ({occurrence_id: id, calendar: OPS, kind: 'run', title, start: s, end: e, links: {automation: 'au-sweep'}});
const ANA_OCC = [
  event('ev-standup', 'Standup', at(11, 9, 30), at(11, 9, 45)),
  event('ev-pricing', 'Pricing review', at(11, 11), at(11, 11, 30), {meeting_url: MEET, open_url: 'https://calendar.google.com/event?eid=x',
    attendees: [{type: 'human', id: 'ana', name: 'Ana', response: 'accepted'}, {type: 'human', id: 'ben', name: 'Ben', response: 'accepted'}],
    links: {thread: 'th-1'}}),
  event('ev-plan', 'Planning', at(9, 13), at(9, 14)),
  {occurrence_id: 'due-quote', calendar: cal(ANA), kind: 'due', title: 'Send the quote', start: '2026-11-12', end: '2026-11-13', all_day: true, links: {task: 't-9'}},
  // Three short sweeps that touch: one block. The report stands alone.
  run('run-1', 'Inbox sweep', at(11, 14), at(11, 14, 2)),
  run('run-2', 'Inbox sweep', at(11, 14, 3), at(11, 14, 5)),
  run('run-3', 'Inbox sweep', at(11, 14, 6), at(11, 14, 8)),
  run('run-4', 'Daily report', at(10, 15), at(10, 15, 40)),
];
const BEN_OCC = [{...event('ev-pricing', 'Pricing review', at(11, 11), at(11, 11, 30), {meeting_url: MEET}), calendar: cal(BEN)},
  {...event('ev-ben-1', '1:1 Dana', at(12, 10), at(12, 10, 30)), calendar: cal(BEN)}];

interface World { reads: URLSearchParams[]; settings: unknown[]; }
async function open(browser: Browser, viewport: {width: number; height: number}, w: World, theme: 'light' | 'dark') {
  const phone = viewport.width < 760;
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: phone, isMobile: phone, colorScheme: theme, locale: 'en-US'});
  await context.addInitScript((value: string) => { localStorage.setItem('tico.theme', value); }, theme);
  const page = await context.newPage();
  await page.clock.setFixedTime(NOW);
  const errors: string[] = [];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: PEOPLE});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/calendar/events') {
      const q = url.searchParams; w.reads.push(q);
      const who = (q.get('who') || 'me').split(','), from = new Date(q.get('from') || 0).getTime(), to = new Date(q.get('to') || 0).getTime();
      const views = who.map(r => r === 'me' || r === 'human:ana' ? ANA : r === 'human:ben' ? BEN : null).filter(Boolean);
      const kinds = q.get('kinds')?.split(',');
      const occ = [...ANA_OCC, ...(who.includes('human:ben') ? BEN_OCC : [])]
        .filter(o => (!kinds || kinds.includes(o.kind)) && new Date(o.start).getTime() < to && new Date(o.end).getTime() > from);
      return json({from: q.get('from'), to: q.get('to'), calendars: views, occurrences: occ});
    }
    if (p === '/api/v2/calendar/calendars') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return json({calendars: [{...BEN, kind: 'person'}, {...OPS, kind: 'bot'}].filter(c => c.name.toLowerCase().startsWith(q))});
    }
    if (p === '/api/v2/calendar/search') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return json({occurrences: ANA_OCC.filter(o => o.kind === 'event' && o.title.toLowerCase().includes(q))});
    }
    if (p === '/api/v2/calendar/settings') {
      if (method === 'PUT') w.settings.push(JSON.parse(route.request().postData() || '{}'));
      return json({record_on_join: true});
    }
    return json(unrouted(p));
  });
  return {page, errors, context};
}
const SHOTS = process.env.TICO_CALENDAR_SCREENSHOTS;
async function shot(page: Page, name: string, clip?: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  const file = path.join(SHOTS, name + '.png');
  if (clip) await page.locator(clip).screenshot({path: file, animations: 'disabled'});
  else await page.screenshot({path: file, animations: 'disabled'});
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    for (const theme of ['light', 'dark'] as const) {
      const where = `desktop-${theme}`;
      const w: World = {reads: [], settings: []};
      const {page, errors, context} = await open(browser, {width: 1360, height: 900}, w, theme);
      await page.goto('https://tico-ui.test/#/calendar');

      // The row: today's events on the bar (the standup behind the now line) and the next one, linking to its call.
      const row = page.locator('#nav-calendar');
      await row.locator('.cal-nx .cal-nm').waitFor();
      assert.equal(await row.locator('.cal-nm').innerText(), 'Pricing review', `${where}: the next event`);
      assert.equal(await row.locator('.cal-tm').innerText(), '11:00', `${where}: at its time`);
      assert.equal(await row.locator('a.cal-nx').getAttribute('href'), MEET, `${where}: the name joins the call`);
      assert.equal(await row.locator('a.cal-nx').getAttribute('target'), '_blank');
      assert.equal(await row.locator('.cal-blk').count(), 2, `${where}: events only on the bar`);
      assert.equal(await row.locator('.cal-blk.past').count(), 1, `${where}: the standup is past`);
      await row.locator('.cal-blk:not(.past)').hover();
      await row.locator('[data-cal-bub] a').waitFor();
      assert.match(await row.locator('[data-cal-bub]').innerText(), /Pricing review[\s\S]*11:00–11:30/, `${where}: the bubble`);
      const rowRead = w.reads.find(q => q.get('kinds') === 'event');
      assert.ok(rowRead && rowRead.get('who') === 'me', `${where}: the row reads the viewer's events`);
      await shot(page, `row-${theme}`, '#nav-calendar');

      // The page: the viewer's week, Monday to Friday, her events, a due pill and the runs.
      await page.locator('#cal-root .cal-ev[data-cal-item]').first().waitFor();
      assert.equal(await page.locator('[data-cal-view="week"]').getAttribute('aria-checked'), 'true', `${where}: Week`);
      assert.equal(await page.locator('[data-cal-range]').innerText(), 'Nov 9 – 15', `${where}: this week`);
      assert.equal(await page.locator('.cal-chd').count(), 5, `${where}: the working days`);
      assert.deepEqual(await page.locator('[data-cal-chip]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.calChip)), ['human:ana'], `${where}: her calendar`);
      for (const title of ['Standup', 'Pricing review', 'Planning'])
        assert.equal(await page.locator('.cal-ev.event', {hasText: title}).count(), 1, `${where}: ${title}`);
      assert.equal(await page.locator('.cal-pill.due', {hasText: 'Send the quote'}).count(), 1, `${where}: due in the top row`);
      assert.equal(await page.locator('.cal-ev.run', {hasText: 'Ops: Daily report'}).count(), 1, `${where}: a run, named by its bot`);
      await shot(page, `page-week-${theme}`);

      // Short runs that touch are one block, which opens their list.
      const grp = page.locator('.cal-ev.run.grp');
      assert.equal(await grp.count(), 1, `${where}: one block for the sweeps`);
      assert.match(await grp.innerText(), /3 runs/);
      await grp.click();
      await page.locator('#cal-card .cal-card-runs button').first().waitFor();
      assert.equal(await page.locator('#cal-card .cal-card-runs button').count(), 3, `${where}: the block lists its runs`);
      await page.locator('#cal-card .cal-card-runs button').first().click();
      await page.locator('#cal-card .lk a', {hasText: 'Automation'}).waitFor();
      assert.equal(await page.locator('#cal-card .lk a', {hasText: 'Automation'}).getAttribute('href'), '#/automations/au-sweep', `${where}: a run links its automation`);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#cal-card').count(), 0, `${where}: Escape closes the card`);

      // An event's card: Join first, then its links.
      await page.locator('.cal-ev.event', {hasText: 'Pricing review'}).click();
      await page.locator('#cal-card').waitFor();
      assert.deepEqual(await page.locator('#cal-card .lk a').allInnerTexts(), ['Join', 'Thread', 'Open in Google'], `${where}: the event's links`);
      assert.equal(await page.locator('#cal-card .lk a', {hasText: 'Join'}).getAttribute('href'), MEET);
      await page.keyboard.press('Escape');

      // Runs off: no run is drawn, and the mark says so; on again brings them back.
      const runs = page.locator('[data-cal-lay="run"]');
      await runs.click();
      assert.equal(await runs.getAttribute('aria-pressed'), 'false', `${where}: Runs off`);
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 0, `${where}: no runs`);
      assert.equal(await page.locator('.cal-ev.event', {hasText: 'Pricing review'}).count(), 1, `${where}: events stay`);
      await runs.click();
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 2, `${where}: runs back`);

      // Search: a calendar first, then events, Ask Tico last; Enter adds the calendar as a chip.
      const q = page.locator('#cal-q');
      await page.locator('#cal-root h1').click();
      await page.keyboard.press('/');
      assert.equal(await q.evaluate(el => el === document.activeElement), true, `${where}: "/" focuses the search`);
      await q.fill('be');
      await page.locator('#cal-dd [data-dd-cal="human:ben"]').waitFor();
      const rows = await page.locator('#cal-dd button').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.ddCal ? 'cal' : (e as HTMLElement).dataset.ddEv ? 'event' : 'ddAsk' in (e as HTMLElement).dataset ? 'ask' : 'word'));
      assert.equal(rows[0], 'cal', `${where}: a calendar first: ${rows}`);
      assert.equal(rows[rows.length - 1], 'ask', `${where}: Ask Tico last`);
      assert.equal(await page.locator('.cal-ev.dim', {hasText: 'Standup'}).count(), 1, `${where}: the rest fades while words are in the box`);
      await shot(page, `page-search-${theme}`);
      await q.press('Enter');
      await page.locator('[data-cal-chip="human:ben"]').waitFor();
      assert.equal(await q.inputValue(), '', `${where}: the box clears`);
      await page.waitForFunction(() => document.querySelectorAll('#cal-root .cal-ev.event').length >= 4);
      assert.ok(w.reads.some(r => r.get('who') === 'me,human:ben'), `${where}: Ben's calendar is read`);
      assert.match(await page.evaluate(() => location.hash), /who=me%2Chuman%3Aben|who=me,human:ben/, `${where}: the address keeps the view`);
      // The shared event draws once, with Ben's dot.
      const pricing = page.locator('.cal-ev.event', {hasText: 'Pricing review'});
      assert.equal(await pricing.count(), 1, `${where}: a shared event once`);
      assert.equal(await pricing.locator('.cal-dots i').count(), 1, `${where}: with a dot for Ben`);

      // Day with two calendars: side by side, with the time both have free.
      await page.locator('[data-cal-view="day"]').click();
      await page.locator('.cal-chd', {hasText: 'Ben'}).waitFor();
      assert.equal(await page.locator('.cal-chd').count(), 2, `${where}: Ana and Ben side by side`);
      assert.ok(await page.locator('.cal-free').count() > 0, `${where}: their free time shaded`);
      await shot(page, `page-day-${theme}`);

      // Month and List draw.
      await page.locator('[data-cal-view="month"]').click();
      await page.locator('.cal-month .cal-mc').first().waitFor();
      assert.equal(await page.locator('.cal-month .cal-mc').count(), 42, `${where}: six weeks`);
      await page.locator('[data-cal-view="list"]').click();
      await page.locator('.cal-list .cal-lr').first().waitFor();
      assert.ok(await page.locator('.cal-list .cal-lr', {hasText: 'Inbox sweep'}).count() === 3, `${where}: List shows every run`);
      await shot(page, `page-list-${theme}`);

      // ⋯: Record calls I join, kept for the person.
      await page.locator('[data-cal-more]').click();
      await page.locator('[data-cal-record]').click();
      assert.equal(await page.locator('[data-cal-record]').getAttribute('aria-checked'), 'false', `${where}: recording off`);
      await page.waitForFunction(() => localStorage.getItem('tico.calendar.record') === 'off');
      assert.deepEqual(w.settings.at(-1), {record_on_join: false}, `${where}: and saved`);
      assert.deepEqual(errors, [], where);
      await context.close();
    }

    // Phone: List first, with the week strip.
    for (const theme of ['light', 'dark'] as const) {
      const w: World = {reads: [], settings: []};
      const {page, errors, context} = await open(browser, {width: 390, height: 844}, w, theme);
      await page.goto('https://tico-ui.test/#/calendar');
      await page.locator('.cal-list .cal-lr').first().waitFor();
      assert.equal(await page.locator('[data-cal-view="list"]').getAttribute('aria-checked'), 'true', `phone-${theme}: List first`);
      assert.equal(await page.locator('.cal-strip button').count(), 7, `phone-${theme}: the week strip`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `phone-${theme}: no sideways scroll`);
      await shot(page, `phone-${theme}`);
      assert.deepEqual(errors, [], `phone-${theme}`);
      await context.close();
    }

    // A server without calendars: the row is just "Calendar".
    {
      const context = await browser.newContext({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', (route: Route) => {
        const url = new URL(route.request().url()), p = url.pathname;
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'https://tico-ui.test') return route.abort();
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
        if (p === '/api/humans') return json({people: PEOPLE});
        if (p === '/api/employees') return json(BOTS);
        if (p.startsWith('/api/v2/calendar/')) return json({detail: 'Not Found'}, 404);
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/updates');
      await page.waitForFunction(() => !!document.querySelector('#nav-calendar a.nav-link'));
      await page.waitForTimeout(800);
      assert.equal(await page.locator('#nav-calendar').evaluate(el => el.classList.contains('cal-on')), false, 'no calendars: no bar');
      assert.equal(await page.locator('#nav-calendar .cal-nx').count(), 0, 'and no next event');
      assert.equal(await page.locator('#nav-calendar a.nav-link').isVisible(), true, 'just "Calendar"');
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('Calendar: row (bar, next, bubble, join link), week with events/due/runs, grouped runs, card links, Runs toggle, search adds a calendar, shared event once, Day side by side with free time, Month, List, record toggle, phone list, no calendars');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
