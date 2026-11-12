// The Calendar (ui/app/calendar.ts, ui/app/calendar-row.ts): the page opens on the viewer's week with her events and a
// due pill and no bot runs, under one header line with no title; Add ticks a bot (its runs show, × takes them away) and
// "Bots that report to me" adds every one; short runs that touch draw as one block that opens their list; no block is an
// outline and each one's text meets AA; Runs under Show hides runs and is kept after a reload; search is an icon that
// "/" opens; a calendar found in the search joins the view as a chip; 3 days moves by three days; a phone header takes
// two lines; and the sidebar row shows the next event with its time, its name linking to the call. Fixtures only, no
// network, at a fixed Wednesday 10:00 (spec calendar-page-cleanup AC-1 to AC-9).
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
const OPS = {ref: 'bot:ops', type: 'bot', id: 'ops', name: 'Ops', work_starts: '09:00', work_ends: '17:00', work_days: 'mon,tue,wed,thu,fri'};
const event = (id: string, title: string, s: string, e: string, extra: Record<string, unknown> = {}) =>
  ({occurrence_id: id, event_id: id, calendar: cal(ANA), kind: 'event', title, start: s, end: e, response: 'accepted', ...extra});
const run = (id: string, title: string, s: string, e: string) =>
  ({occurrence_id: id, calendar: OPS, kind: 'run', title, start: s, end: e, links: {automation: 'au-sweep'}});
const OPS_OCC = [
  // Three short sweeps that touch: one block. The report stands alone.
  run('run-1', 'Inbox sweep', at(11, 14), at(11, 14, 2)),
  run('run-2', 'Inbox sweep', at(11, 14, 3), at(11, 14, 5)),
  run('run-3', 'Inbox sweep', at(11, 14, 6), at(11, 14, 8)),
  run('run-4', 'Daily report', at(10, 15), at(10, 15, 40)),
  {...run('run-5', 'Weekly digest', at(13, 9), at(13, 9, 30)), planned: true},
];
const ANA_OCC = [
  event('ev-standup', 'Standup', at(11, 9, 30), at(11, 9, 45)),
  event('ev-pricing', 'Pricing review', at(11, 11), at(11, 11, 30), {meeting_url: MEET, open_url: 'https://calendar.google.com/event?eid=x',
    attendees: [{type: 'human', id: 'ana', name: 'Ana', response: 'accepted'}, {type: 'human', id: 'ben', name: 'Ben', response: 'accepted'}],
    links: {thread: 'th-1'}}),
  event('ev-plan', 'Planning', at(9, 13), at(9, 14)),
  {occurrence_id: 'due-quote', calendar: cal(ANA), kind: 'due', title: 'Send the quote', start: '2026-11-12', end: '2026-11-13', all_day: true, links: {task: 't-9'}},
];
const BEN_OCC = [{...event('ev-pricing', 'Pricing review', at(11, 11), at(11, 11, 30), {meeting_url: MEET}), calendar: cal(BEN)},
  {...event('ev-ben-1', '1:1 Dana', at(12, 10), at(12, 10, 30)), calendar: cal(BEN)}];

// `many`: the team has 25 more people, so Add lists more calendars than the server reads at once.
interface World { reads: URLSearchParams[]; settings: unknown[]; many?: boolean; }
const MANY = Array.from({length: 25}, (_, i) => {
  const id = `p${String(i + 1).padStart(2, '0')}`;
  return {ref: 'human:' + id, type: 'human', id, name: `Person ${i + 1}`, work_starts: '09:00', work_ends: '17:00', work_days: 'mon,tue,wed,thu,fri'};
});
const WHO_MAX = 20;        // backend/calendar_api.py WHO_MAX
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
      // As the server answers: more than WHO_MAX calendars is refused.
      if (who.length > WHO_MAX) return route.fulfill({status: 422, contentType: 'application/json', body: JSON.stringify({error: {code: 'validation', detail: `At most ${WHO_MAX} calendars at a time`}})});
      const views = who.map(r => r === 'me' || r === 'human:ana' ? ANA : r === 'human:ben' ? BEN : r === 'bot:ops' ? OPS : MANY.find(m => m.ref === r) || null).filter(Boolean);
      const kinds = q.get('kinds')?.split(',');
      // As the server answers: a bot's runs only when that bot is in `who`, never for the person it reports to.
      const occ = [...ANA_OCC, ...(who.includes('human:ben') ? BEN_OCC : []), ...(who.includes('bot:ops') ? OPS_OCC : [])]
        .filter(o => (!kinds || kinds.includes(o.kind)) && new Date(o.start).getTime() < to && new Date(o.end).getTime() > from);
      return json({from: q.get('from'), to: q.get('to'), calendars: views, occurrences: occ});
    }
    if (p === '/api/v2/calendar/calendars') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return json({calendars: [{...ANA, kind: 'person'}, {...BEN, kind: 'person'}, {...OPS, kind: 'bot'}, ...(w.many ? MANY.map(m => ({...m, kind: 'person'})) : [])].filter(c => c.name.toLowerCase().startsWith(q) && (q || c.ref !== 'human:ana'))});
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
// The header's visible controls grouped into lines by their vertical centre.
async function headerLines(page: Page): Promise<number> {
  return page.locator('#cal-root .cal-h').evaluate(h => {
    const mids: number[] = [];
    for (const el of h.querySelectorAll<HTMLElement>('[data-cal-today], .cal-nav, [data-cal-range], .cal-chip, [data-cal-add], .cal-sr, [data-cal-views], [data-cal-more]')) {
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
      const m = r.top + r.height / 2; if (!mids.some(x => Math.abs(x - m) < 8)) mids.push(m);
    }
    return mids.length;
  });
}
// Every block, pill, month mark and list key: a solid fill (no outline-only shape), and the text of each block and pill
// at least 4.5:1 against its fill (WCAG AA). Returns what fails.
async function shapesFail(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const rgb = (c: string): number[] | null => {
      let m = /^rgba?\(([^)]+)\)/.exec(c);
      if (m) { const v = m[1]!.split(/[ ,/]+/).filter(Boolean).map(Number); return v[3] === 0 ? null : v.slice(0, 3); }
      m = /^color\(srgb ([^)]+)\)/.exec(c);
      if (m) { const v = m[1]!.split(/[ /]+/).filter(Boolean).map(Number); return v[3] === 0 ? null : v.slice(0, 3).map(x => x * 255); }
      return null;
    };
    const lum = ([r, g, b]: number[]) => { const f = (x: number) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r!) + 0.7152 * f(g!) + 0.0722 * f(b!); };
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>('#cal-root .cal-ev:not(.busy), #cal-root .cal-pill:not(.busy), #cal-root .cal-mev i, #cal-root .cal-mruns, #cal-root .cal-kd:not(.busy), #cal-root .cal-gl')) {
      const st = getComputedStyle(el), name = `${el.className} "${(el.textContent || '').trim().slice(0, 30)}"`;
      const bg = rgb(st.backgroundColor);
      if (!bg) { out.push(`${name}: no fill (${st.backgroundColor})`); continue; }
      for (const side of ['Top', 'Right', 'Bottom'] as const)
        if (parseFloat(st.getPropertyValue(`border-${side.toLowerCase()}-width`)) > 0) out.push(`${name}: a ${side} outline`);
      if (!el.matches('.cal-ev, .cal-pill')) continue;
      const fg = rgb(st.color); if (!fg) { out.push(`${name}: no text colour`); continue; }
      const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x), ratio = (a! + 0.05) / (b! + 0.05);
      if (ratio < 4.5) out.push(`${name}: contrast ${ratio.toFixed(2)}`);
    }
    return out;
  });
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
      const {page, errors, context} = await open(browser, {width: 1280, height: 900}, w, theme);
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

      // The page: the viewer's week, Monday to Friday, her events and a due pill, and no bot's runs (AC-1).
      await page.locator('#cal-root .cal-ev[data-cal-item]').first().waitFor();
      assert.equal(await page.locator('[data-cal-view="week"]').getAttribute('aria-checked'), 'true', `${where}: Week`);
      assert.deepEqual(await page.locator('[data-cal-view]').allInnerTexts(), ['Day', '3 days', 'Week', 'Month', 'List'], `${where}: the view switch`);
      assert.equal(await page.locator('[data-cal-range]').innerText(), 'Nov 9 – 15', `${where}: this week`);
      assert.equal(await page.locator('.cal-chd').count(), 5, `${where}: the working days`);
      assert.deepEqual(await page.locator('[data-cal-chip]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.calChip)), ['human:ana'], `${where}: her calendar`);
      for (const title of ['Standup', 'Pricing review', 'Planning'])
        assert.equal(await page.locator('.cal-ev.event', {hasText: title}).count(), 1, `${where}: ${title}`);
      assert.equal(await page.locator('.cal-pill.due', {hasText: 'Send the quote'}).count(), 1, `${where}: due in the top row`);
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 0, `${where}: only her: no run of the bot that reports to her`);
      assert.equal(w.reads.at(-1)?.get('who'), 'me', `${where}: read as just her`);
      // One header line, no title (AC-7); the tab still says Calendar; search is an icon.
      assert.equal(await page.locator('#cal-root h1').count(), 0, `${where}: no "Calendar" heading`);
      assert.match(await page.title(), /Calendar/, `${where}: the tab says Calendar`);
      assert.equal(await page.locator('#cal-q').isVisible(), false, `${where}: search folded to an icon`);
      await shot(page, `page-week-${theme}`);

      // Add: two quick choices with how many, people and bots with a tick, and what to show. Ticking Ops adds its chip
      // and its runs (AC-2).
      await page.locator('[data-cal-add]').click();
      await page.locator('[data-cal-addm] [data-cal-pick="bot:ops"]').waitFor();
      assert.equal(await page.locator('[data-cal-addm] [data-cal-addq]').evaluate(el => el === document.activeElement), true, `${where}: Add's box has the cursor`);
      assert.equal(await page.locator('[data-cal-quick="mine"] [data-cal-quick-n]').innerText(), '1', `${where}: how many bots report to her`);
      assert.equal(await page.locator('[data-cal-quick="all"]').count(), 1, `${where}: Everyone`);
      assert.deepEqual(await page.locator('[data-cal-addm] [data-cal-pick]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.calPick)), ['human:ana', 'human:ben', 'bot:ops'], `${where}: people and bots, her first`);
      assert.equal(await page.locator('[data-cal-addm] [data-cal-lay]').count(), 3, `${where}: Show is in Add`);
      await shot(page, `page-add-${theme}`);
      await page.locator('[data-cal-addm] [data-cal-pick="bot:ops"]').check();
      await page.locator('[data-cal-chip="bot:ops"]').waitFor();
      await page.locator('#cal-root .cal-ev.run').first().waitFor();
      assert.equal(await page.locator('[data-cal-addm] [data-cal-pick="bot:ops"]').isChecked(), true, `${where}: the menu stays open, Ops ticked`);
      assert.equal(await page.locator('.cal-ev.run', {hasText: 'Daily report'}).count(), 1, `${where}: Ops's runs show`);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-cal-addm]').isHidden(), true, `${where}: Escape closes Add`);

      // No block is an outline, and each block's text meets AA (AC-5): an event, a run, a planned run, a group, a due pill.
      assert.equal(await page.locator('.cal-ev.run.planned', {hasText: 'Weekly digest'}).count(), 1, `${where}: a planned run`);
      assert.deepEqual(await shapesFail(page), [], `${where}: solid shapes, AA text`);

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

      // × on the chip takes Ops and its runs away (AC-2); "Bots that report to me" brings every one back (AC-3).
      await page.locator('[data-cal-rm="bot:ops"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-cal-chip="bot:ops"]'));
      await page.waitForFunction(() => !document.querySelector('#cal-root .cal-ev.run'));
      await page.locator('[data-cal-add]').click();
      await page.locator('[data-cal-quick="mine"]').click();
      await page.locator('[data-cal-chip="bot:ops"]').waitFor();
      await page.locator('#cal-root .cal-ev.run').first().waitFor();
      assert.equal(w.reads.at(-1)?.get('who'), 'me,bot:ops', `${where}: her bots are read`);

      // An event's card: Join first, then its links.
      await page.keyboard.press('Escape');
      await page.locator('.cal-ev.event', {hasText: 'Pricing review'}).click();
      await page.locator('#cal-card').waitFor();
      assert.deepEqual(await page.locator('#cal-card .lk a').allInnerTexts(), ['Join', 'Thread', 'Open in Google'], `${where}: the event's links`);
      assert.equal(await page.locator('#cal-card .lk a', {hasText: 'Join'}).getAttribute('href'), MEET);
      await page.keyboard.press('Escape');

      // Runs off under Show: no run is drawn, and the mark says so; kept after a reload (AC-4); on again brings them back.
      await page.locator('[data-cal-add]').click();
      const runs = page.locator('[data-cal-addm] [data-cal-lay="run"]');
      await runs.click();
      assert.equal(await runs.getAttribute('aria-pressed'), 'false', `${where}: Runs off`);
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 0, `${where}: no runs`);
      assert.equal(await page.locator('.cal-ev.event', {hasText: 'Pricing review'}).count(), 1, `${where}: events stay`);
      await page.reload();
      await page.locator('#cal-root .cal-ev.event').first().waitFor();
      await page.locator('[data-cal-chip="bot:ops"]').waitFor();
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 0, `${where}: still no runs after a reload`);
      await page.locator('[data-cal-add]').click();
      await page.locator('[data-cal-addm] [data-cal-lay="run"]').click();
      assert.equal(await page.locator('#cal-root .cal-ev.run').count(), 3, `${where}: runs back`);
      await page.keyboard.press('Escape');

      // Search: an icon that "/" and a click open with the cursor in it (AC-9); a calendar first, then events, Ask Tico
      // last; Enter adds the calendar as a chip.
      const q = page.locator('#cal-q');
      await page.locator('[data-cal-range]').click();
      await page.keyboard.press('/');
      assert.equal(await q.evaluate(el => el === document.activeElement), true, `${where}: "/" opens the search`);
      await page.locator('[data-cal-range]').click();
      await page.waitForFunction(() => !document.querySelector('#cal-root .cal-sr.open'));
      await page.locator('[data-cal-find]').click();
      assert.equal(await q.evaluate(el => el === document.activeElement), true, `${where}: the icon opens the search`);
      await q.fill('be');
      await page.locator('#cal-dd [data-dd-cal="human:ben"]').waitFor();
      const rows = await page.locator('#cal-dd button').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.ddCal ? 'cal' : (e as HTMLElement).dataset.ddEv ? 'event' : 'ddAsk' in (e as HTMLElement).dataset ? 'ask' : 'word'));
      assert.equal(rows[0], 'cal', `${where}: a calendar first: ${rows}`);
      assert.equal(rows[rows.length - 1], 'ask', `${where}: Ask Tico last`);
      assert.equal(await page.locator('.cal-ev.dim', {hasText: 'Standup'}).count(), 1, `${where}: the rest fades while words are in the box`);
      await shot(page, `page-search-${theme}`);
      await q.press('Enter');
      await page.locator('[data-cal-chip="human:ben"]').waitFor({state: 'attached'});      // folded into +1 while search is open
      assert.equal(await q.inputValue(), '', `${where}: the box clears`);
      await page.waitForFunction(() => document.querySelectorAll('#cal-root .cal-ev.event').length >= 4);
      assert.ok(w.reads.some(r => r.get('who') === 'me,bot:ops,human:ben'), `${where}: Ben's calendar is read`);
      assert.match(await page.evaluate(() => location.hash), /who=me%2Cbot%3Aops%2Chuman%3Aben|who=me,bot:ops,human:ben/, `${where}: the address keeps the view`);
      // The shared event draws once, with Ben's dot.
      const pricing = page.locator('.cal-ev.event', {hasText: 'Pricing review'});
      assert.equal(await pricing.count(), 1, `${where}: a shared event once`);
      assert.equal(await pricing.locator('.cal-dots i').count(), 1, `${where}: with a dot for Ben`);
      // Three chips at 1280px: still one header line (AC-7).
      await page.locator('[data-cal-range]').click();
      await page.waitForFunction(() => !document.querySelector('#cal-root .cal-sr.open'));
      assert.equal(await headerLines(page), 1, `${where}: Today, arrows, range, chips, Add, search, views and ⋯ on one line`);
      assert.equal(await page.locator('[data-cal-chip="human:ben"]').isVisible(), true, `${where}: three chips fit once search folds`);
      await shot(page, `page-header-${theme}`, '#cal-root .cal-h');

      // 3 days: today and the next two on one hour grid; › moves by three days (AC-6).
      await page.locator('[data-cal-view="3day"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.cal-chd').length === 3);
      assert.deepEqual(await page.locator('.cal-chd').evaluateAll(els => els.map(e => (e.textContent || '').replace(/\s+/g, ' ').trim())), ['Wed 11', 'Thu 12', 'Fri 13'], `${where}: three days`);
      assert.equal(await page.locator('[data-cal-range]').innerText(), 'Nov 11 – 13', `${where}: their range`);
      await shot(page, `page-3day-${theme}`);
      await page.locator('[data-cal-step="1"]').click();
      await page.waitForFunction(() => document.querySelector('[data-cal-range]')?.textContent === 'Nov 14 – 16' && /14/.test(document.querySelector('.cal-chd')?.textContent || ''));
      assert.deepEqual(await page.locator('.cal-chd').evaluateAll(els => els.map(e => (e.textContent || '').replace(/\s+/g, ' ').trim())), ['Sat 14', 'Sun 15', 'Mon 16'], `${where}: the next three, weekend too`);
      await page.locator('[data-cal-today]').click();

      // Day with several calendars: side by side, with the time the people have free.
      await page.locator('[data-cal-view="day"]').click();
      await page.locator('.cal-chd', {hasText: 'Ben'}).waitFor();
      assert.equal(await page.locator('.cal-chd').count(), 3, `${where}: Ana, Ops and Ben side by side`);
      assert.ok(await page.locator('.cal-free').count() > 0, `${where}: their free time shaded`);
      await shot(page, `page-day-${theme}`);

      // Month and List draw.
      await page.locator('[data-cal-view="month"]').click();
      await page.locator('.cal-month .cal-mc').first().waitFor();
      assert.equal(await page.locator('.cal-month .cal-mc').count(), 42, `${where}: six weeks`);
      assert.deepEqual(await shapesFail(page), [], `${where}: Month's marks are solid`);
      await page.locator('[data-cal-view="list"]').click();
      await page.locator('.cal-list .cal-lr').first().waitFor();
      assert.ok(await page.locator('.cal-list .cal-lr', {hasText: 'Inbox sweep'}).count() === 3, `${where}: List shows every run`);
      assert.deepEqual(await shapesFail(page), [], `${where}: List's keys are solid`);
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

    // Phone: List first, with the week strip, under a header of at most two lines (AC-8).
    for (const theme of ['light', 'dark'] as const) {
      const w: World = {reads: [], settings: []};
      const {page, errors, context} = await open(browser, {width: 375, height: 812}, w, theme);
      await page.goto('https://tico-ui.test/#/calendar');
      await page.locator('.cal-list .cal-lr').first().waitFor();
      assert.equal(await page.locator('[data-cal-view="list"]').getAttribute('aria-checked'), 'true', `phone-${theme}: List first`);
      assert.equal(await page.locator('.cal-strip button').count(), 7, `phone-${theme}: the week strip`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `phone-${theme}: no sideways scroll`);
      assert.ok(await headerLines(page) <= 2, `phone-${theme}: the header takes at most two lines`);
      await page.goto('https://tico-ui.test/#/calendar?who=me,bot:ops,human:ben&view=3day');
      await page.locator('[data-cal-chip="human:ben"]').waitFor();
      await page.locator('.cal-chd').first().waitFor();
      assert.ok(await headerLines(page) <= 2, `phone-${theme}: two lines with three chips`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `phone-${theme}: no sideways scroll with three chips`);
      await shot(page, `phone-${theme}`);
      assert.deepEqual(errors, [], `phone-${theme}`);
      await context.close();
    }

    // More people and bots than the server reads at once (20): "Everyone" is off and says why, "Bots that report to me"
    // still adds; ticking stops at 20; an address naming more opens on the first 20; chips past the line fold into "+N".
    {
      const w: World = {reads: [], settings: [], many: true};
      const {page, errors, context} = await open(browser, {width: 1280, height: 900}, w, 'light');
      const tooMany = ['me', 'bot:ops', 'human:ben', ...MANY.map(m => m.ref)].join(',');
      await page.goto('https://tico-ui.test/#/calendar?who=' + tooMany);
      await page.locator('#cal-root .cal-ev.event').first().waitFor();
      assert.equal(await page.locator('#cal-root .err').count(), 0, 'many: an address with 28 calendars still answers');
      assert.equal(w.reads.at(-1)?.get('who')?.split(',').length, WHO_MAX, 'many: it reads the first 20');
      assert.equal(await headerLines(page), 1, 'many: still one header line');
      const more = page.locator('[data-cal-chips-more]');
      assert.equal(await more.isVisible(), true, 'many: the chips past the line fold into +N');
      const shown = await page.locator('[data-cal-chip]:visible').count();
      assert.equal(await more.innerText(), `+${WHO_MAX - shown}`, 'many: +N counts the hidden chips');
      await more.click();
      await page.locator('[data-cal-addm] [data-cal-pick]').first().waitFor();
      assert.equal(await page.locator('[data-cal-quick="all"]').isDisabled(), true, 'many: Everyone is off over 20');
      assert.match(await page.locator('[data-cal-addm]').innerText(), /at most 20/i, 'many: and says why');
      assert.equal(await page.locator('[data-cal-addm] [data-cal-pick]:not(:checked)').first().isDisabled(), true, 'many: no 21st tick');
      // Take two away: two rows free up, and ticking one more stays within 20.
      await page.locator('[data-cal-addm] [data-cal-pick="human:p01"]').uncheck();
      await page.locator('[data-cal-addm] [data-cal-pick="human:p02"]').uncheck();
      await page.waitForFunction(() => !!document.querySelector('[data-cal-addm] [data-cal-pick]:not(:checked):not(:disabled)'));
      await page.locator('[data-cal-addm] [data-cal-pick="human:p01"]').check();
      await page.waitForFunction(() => document.querySelectorAll('[data-cal-chip]').length === 19);
      assert.equal(await page.locator('#cal-root .err').count(), 0, 'many: no error after ticking');
      assert.deepEqual(errors, [], 'many');
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
    console.log('Calendar: row (bar, next, bubble, join link), week with only her events/due and no bot runs, one header line with no title, Add ticks a bot and × removes it, Bots that report to me, solid AA shapes, grouped runs, card links, Runs under Show kept after reload, search icon and "/", search adds a calendar, shared event once, 3 days, Day side by side with free time, Month, List, record toggle, phone two-line header, no calendars');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
