// The sidebar's Calendar row (ui/app/calendar-row.ts; spec "Next event on Home's Calendar row" v3): one line, "Calendar"
// then the next event in plain text ("Sales call · in 3 min"), its name opening the event; › steps to the one after and ‹
// back; a new page resets it to the very next; an event under way reads "now", without a reload; no events in the next 7
// days read "Nothing scheduled" with no arrows; and at 360px the line stays one line, the name cut with "…".
// Fixtures only, no network, at a fixed Wednesday 10:00, in a 24-hour locale.
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
const ANA = {ref: 'human:ana', type: 'human', id: 'ana', name: 'Ana', work_starts: '09:00', work_ends: '17:00', work_days: 'mon,tue,wed,thu,fri'};
const event = (id: string, title: string, s: string, e: string, extra: Record<string, unknown> = {}) =>
  ({occurrence_id: id, event_id: id, calendar: {ref: ANA.ref, type: ANA.type, id: ANA.id, name: ANA.name}, kind: 'event', title, start: s, end: e, response: 'accepted', ...extra});
const LONG = 'Quarterly planning with the sales team and the whole of finance';
const WEEK = [
  event('ev-past', 'Standup', at(11, 9), at(11, 9, 30)),                            // over: never the next
  event('ev-sales', 'Sales call', at(11, 10, 3), at(11, 10, 30), {meeting_url: 'https://meet.google.com/abc-defg-hij'}),
  event('ev-no', 'Declined sync', at(11, 12), at(11, 12, 30), {response: 'declined'}),
  event('ev-design', 'Design review', at(11, 15, 30), at(11, 16)),
  event('ev-allday', 'Offsite', '2026-11-12', '2026-11-13', {all_day: true}),
  event('ev-standup', 'Standup', at(12, 9), at(12, 9, 15)),
  event('ev-board', 'Board prep', at(13, 10), at(13, 11)),
  event('ev-far', 'Far away', at(20, 10), at(20, 11)),                               // past the next 7 days
];

async function open(browser: Browser, viewport: {width: number; height: number}, occurrences: unknown[]) {
  const phone = viewport.width < 760;
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: phone, isMobile: phone, locale: 'en-GB'});
  const page = await context.newPage();
  await page.clock.install({time: NOW});
  const errors: string[] = [];
  const reads: URLSearchParams[] = [];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/calendar/events') {
      const q = url.searchParams; reads.push(q);
      const from = new Date(q.get('from') || 0).getTime(), to = new Date(q.get('to') || 0).getTime();
      const occ = (occurrences as {start: string; end: string}[]).filter(o => new Date(o.start).getTime() < to && new Date(o.end).getTime() > from);
      return json({from: q.get('from'), to: q.get('to'), calendars: [ANA], occurrences: occ});
    }
    if (p === '/api/v2/calendar/settings') return json({record_on_join: true});
    return json(unrouted(p));
  });
  return {page, errors, reads, context};
}
const SHOTS = process.env.TICO_CALENDAR_SCREENSHOTS;
async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.locator('#nav-calendar').screenshot({path: path.join(SHOTS, name + '.png'), animations: 'disabled'});
}
// What the row says after "Calendar", and which arrows it shows.
async function says(page: Page) {
  return page.locator('#nav-calendar').evaluate(row => {
    const shown = (sel: string) => { const el = row.querySelector<HTMLElement>(sel); return !!el && el.offsetParent !== null; };
    const nx = row.querySelector<HTMLElement>('.cal-nx');
    const parts = nx?.querySelector('.cal-nm') ? [...nx.children].map(el => el.textContent) : [nx?.textContent || ''];
    return {text: parts.join(' ').replace(/\s+/g, ' ').trim(), back: shown('[data-cal-step="-1"]'), fwd: shown('[data-cal-step="1"]')};
  });
}
// The row is one line: everything in it shares the link's line.
async function oneLine(page: Page) {
  return page.locator('#nav-calendar').evaluate(row => {
    const link = row.querySelector('a.nav-link')!.getBoundingClientRect();
    return [...row.querySelectorAll<HTMLElement>('.cal-nx, [data-cal-step]')].filter(el => el.offsetParent !== null)
      .every(el => { const r = el.getBoundingClientRect(); return r.top >= link.top - 1 && r.bottom <= link.bottom + 1 && r.right <= row.getBoundingClientRect().right + 1; });
  });
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    {
      const {page, errors, reads, context} = await open(browser, {width: 1360, height: 900}, WEEK);
      await page.goto('https://tico-ui.test/#/home');
      const row = page.locator('#nav-calendar');
      await row.locator('a.cal-nx').waitFor();
      // AC-1: the very next event, soon, on one line, with › only. No day bar any more.
      assert.deepEqual(await says(page), {text: 'Sales call · in 3 min', back: false, fwd: true}, 'AC-1: the next event');
      assert.equal(await oneLine(page), true, 'AC-1: one line');
      assert.equal(await row.locator('[data-cal-bar], .cal-blk').count(), 0, 'no timeline bar');
      const read = reads.find(q => q.get('kinds') === 'event');
      assert.ok(read && read.get('who') === 'me', 'the row reads only the viewer\'s own events');
      assert.ok(new Date(read!.get('to')!).getTime() - NOW.getTime() >= 7 * 86400e3, 'the read covers the next 7 days');
      await shot(page, 'row-soon');
      // AC-4: the name opens that event on the Calendar page.
      assert.equal(await row.locator('a.cal-nx').getAttribute('href'), '#/calendar?date=2026-11-11&open=ev-sales', 'AC-4: the name opens the event');
      assert.equal(await row.locator('a.cal-nx').getAttribute('target'), null, 'AC-4: in this tab, not the call');
      // AC-5, AC-2, AC-9: › steps forward; ‹ appears; the last event in range has no ›. Declined, all-day and far events are skipped.
      await row.locator('[data-cal-step="1"]').click();
      assert.deepEqual(await says(page), {text: 'Design review · 15:30', back: true, fwd: true}, 'AC-5: the event after, and ‹');
      await shot(page, 'row-stepped');
      await row.locator('[data-cal-step="1"]').click();
      assert.deepEqual(await says(page), {text: 'Standup · tomorrow 09:00', back: true, fwd: true}, 'AC-2: tomorrow');
      await row.locator('[data-cal-step="1"]').click();
      assert.deepEqual(await says(page), {text: 'Board prep · Fri 10:00', back: true, fwd: false}, 'AC-9: further out, the last one has no ›');
      // AC-6: ‹ back to the very next, where ‹ goes.
      for (let i = 0; i < 3; i++) await row.locator('[data-cal-step="-1"]').click();
      assert.deepEqual(await says(page), {text: 'Sales call · in 3 min', back: false, fwd: true}, 'AC-6: back at the very next, no ‹');
      // AC-7: stepped forward, another page and back to Home: the very next again.
      await row.locator('[data-cal-step="1"]').click();
      assert.equal((await says(page)).text, 'Design review · 15:30');
      await page.goto('https://tico-ui.test/#/tasks');
      await page.goto('https://tico-ui.test/#/home');
      await page.waitForFunction(() => document.querySelector('#nav-calendar .cal-nx')?.textContent?.includes('Sales call'));
      assert.deepEqual(await says(page), {text: 'Sales call · in 3 min', back: false, fwd: true}, 'AC-7: reset on return');
      // AC-11 and AC-8: Home stays open; once the call starts it reads "now", with no reload.
      await page.clock.runFor(4 * 60 * 1000);
      await page.waitForFunction(() => document.querySelector('#nav-calendar .cal-nx')?.textContent?.includes('now'));
      assert.deepEqual(await says(page), {text: 'Sales call · now', back: false, fwd: true}, 'AC-8/AC-11: under way reads "now"');
      // The name is plain ink, not greyed out.
      const ink = await row.locator('.cal-nm').evaluate(el => [getComputedStyle(el).color, getComputedStyle(document.querySelector('#nav-calendar a.nav-link')!).color]);
      assert.equal(ink[0], ink[1], 'the name is the same ink as "Calendar"');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // AC-3: nothing in the next 7 days: "Nothing scheduled", no arrows, and "Clear" nowhere.
      const {page, errors, context} = await open(browser, {width: 1360, height: 900}, [event('ev-far', 'Far away', at(20, 10), at(20, 11))]);
      await page.goto('https://tico-ui.test/#/home');
      const row = page.locator('#nav-calendar');
      await row.locator('.cal-nx').waitFor();
      assert.deepEqual(await says(page), {text: 'Nothing scheduled', back: false, fwd: false}, 'AC-3: Nothing scheduled');
      assert.equal(await row.locator('[data-cal-step]').count(), 0, 'AC-3: no arrows');
      assert.doesNotMatch(await row.innerText(), /Clear/, 'AC-3: no "Clear"');
      assert.equal(await oneLine(page), true, 'AC-3: one line');
      assert.equal(await row.locator('.cal-none').evaluate(el => el.scrollWidth <= el.clientWidth), true, 'AC-3: in full at the sidebar\'s default width');
      await shot(page, 'row-nothing');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // AC-10: a 360px phone, in the side menu: one line, the long name cut with "…", › still in the row.
      const {page, errors, context} = await open(browser, {width: 360, height: 760},
        [event('ev-long', LONG, at(11, 10, 3), at(11, 10, 30)), event('ev-design', 'Design review', at(11, 15, 30), at(11, 16))]);
      await page.goto('https://tico-ui.test/#/home');
      await page.evaluate(() => document.body.classList.add('drawer'));
      const row = page.locator('#nav-calendar');
      await row.locator('a.cal-nx').waitFor({state: 'visible'});
      assert.deepEqual(await says(page), {text: `${LONG} · in 3 min`, back: false, fwd: true}, 'AC-10: the long name');
      assert.equal(await oneLine(page), true, 'AC-10: one line, arrows in the row');
      const cut = await row.locator('.cal-nm').evaluate(el => ({over: el.scrollWidth > el.clientWidth, ellipsis: getComputedStyle(el).textOverflow}));
      assert.deepEqual(cut, {over: true, ellipsis: 'ellipsis'}, 'AC-10: the name is cut with "…"');
      await shot(page, 'row-phone');
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('Calendar row: next event (soon, today, tomorrow, weekday), name opens it, › and ‹ stepping, reset on return, "now" without reload, Nothing scheduled, one line at 360px');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
