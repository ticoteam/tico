// The Files page (Library › Files, ui/app/pages.js). On a computer it is a table across the page (name, kind, made by,
// team, updated), about 40px a row with the name on one line; on a phone each row is two lines (the name, then Kind · Bot
// · time) and the search and the filter button share the top row. The filters sit in a popover (kind and page role,
// updated, bot, team, linked to, written by, pinned, archived) that shows as a count on the button and as chips under the
// row, and every filter is in the address, so a view can be shared. Also: a page's meta line (its links and writers), More
// paging, search, the empty, error and loading states, and opening a file.
// Fixtures only, no network.
import type {Browser, LaunchOptions, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const shots = process.env.TICO_SCREENSHOT_DIR;

const DESKTOP = {width: 1440, height: 900}, PHONE = {width: 390, height: 844};
const GDOC = 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit';
const TEAMS = [{id: 'marketing', name: 'Marketing'}, {id: 'eng', name: 'Engineering'}, {id: 'support', name: 'Support'},
  {id: 'sales', name: 'Sales'}, {id: 'finance', name: 'Finance'}, {id: 'people', name: 'People'}, {id: 'ops', name: 'Operations'}];
// Nine bots, so the popover's bot list has its find box (more than seven).
const BOTS = [['cmo', 'AI CMO', 'marketing'], ['writer', 'Writer', 'marketing'], ['engineer', 'Engineering Lead', 'eng'],
  ['data', 'Data Analyst', 'eng'], ['support', 'Support Bot', 'support'], ['sales', 'Sales Bot', 'sales'],
  ['finance', 'Finance Bot', 'finance'], ['recruiter', 'Recruiter', 'people'], ['ops', 'Ops Lead', 'ops']]
  .map(([name, display_name, team]) => ({name, display_name, team, org_parent: '', host: 'keeper', status: 'active',
    can_chat: true, users: [], schedules: []}));
const teamOf = (bot: string) => BOTS.find(b => b.name === bot)?.team;
// The tasks the Linked to picker finds by its search (search=) or by the ids a link names (ids=), and the goals it names.
const TASKS = [{id: 't-18463', title: 'Q4 launch brief'}, {id: 't-19385', title: 'Signup funnel fix'}];
const GOALS = [{id: 'g-grow', title: 'Grow signups', owner_id: 'cmo', parent_id: null, rank: 1, status: 'active'}];

const now = Date.now(), at = (hours: number) => new Date(now - hours * 36e5).toISOString();
interface FileOpen {type: string; url: string}
interface FileLink {target: string; role: string; name: string}
interface FileWriter {actor: string; name: string}
interface FileRow {id: string; name: string | null; title: string; bot: string; library_kind: string; locator: string; open: FileOpen | null | undefined;
  last_activity_at: string; pins: string[]; archived: boolean; task_id: null; links: FileLink[]; links_more: number; writers: FileWriter[]}
interface RowSpec {title: string; name?: string | null; bot: string; kind: string; locator?: string; open?: FileOpen | null; h: number; pins?: string[]; archived?: boolean;
  links?: FileLink[]; links_more?: number; writers?: FileWriter[]}
interface Viewport {width: number; height: number}
// What the fixture server was asked for and served: the page's list and search calls, and the file it opened.
interface FetchState {files: FileRow[]; mode: string; listed: string[]; searched: (string | null)[]; opened: string[]}
const row = (id: string, o: RowSpec): FileRow => ({id, name: 'name' in o ? o.name ?? null : o.title, title: o.title, bot: o.bot, library_kind: o.kind, locator: o.locator || 'tico_blob',
  open: 'open' in o ? o.open : {type: 'tico', url: '/api/v2/files/' + id}, last_activity_at: at(o.h), pins: o.pins || [],
  archived: !!o.archived, task_id: null, links: o.links || [], links_more: o.links_more || 0, writers: o.writers || []});
// A search hit carries an excerpt of its text; the list does not.
const EXCERPT: Record<string, string> = {'file-funnel': 'Trial to paid, by week and plan'};
const FILES: FileRow[] = [
  row('file-q4', {title: 'Q4 launch plan', bot: 'cmo', kind: 'document', locator: 'remote_link', open: {type: 'external', url: GDOC}, h: 0.2, pins: ['Ana']}),
  row('file-funnel', {title: 'Signup funnel', bot: 'engineer', kind: 'data', h: 2, pins: ['Ana', 'Chris']}),
  // A page with its links (five at most, and a count of the rest) and its writers: its meta line names them.
  row('file-weekly', {title: 'Weekly report', bot: 'cmo', kind: 'page', h: 3, open: null,
    links: [{target: 'task:t-18463', role: 'Spec', name: 'D-18463'}, {target: 'task:t-19385', role: 'Spec', name: 'D-19385'}], links_more: 2,
    writers: [{actor: 'bot:cmo', name: 'AI CMO'}, {actor: 'human:ana', name: 'Ana'}]}),
  row('file-budget', {title: 'Budget 2026', bot: 'finance', kind: 'data', h: 5}),
  // A stored file whose title is its path: the row shows the file's own name, never the folder.
  row('file-guide', {title: 'guides/holiday-setup.md', name: 'holiday-setup.md', bot: 'writer', kind: 'document', h: 5.5}),
  // A title with a slash that is not a path: a stored file shows its own name; without one, words with spaces stay whole.
  row('file-sales', {title: 'Sales 2024/25.xlsx', name: 'Sales 2024-25.xlsx', bot: 'writer', kind: 'document', h: 5.6}),
  row('file-q3q4', {title: 'Q3/Q4 plan.pdf', name: null, bot: 'writer', kind: 'document', h: 5.7}),
  // A path with no name (as a search hit has none): its last part.
  row('file-export', {title: 'exports/traffic-2026.csv', name: null, bot: 'writer', kind: 'document', h: 5.8}),
  row('file-hero', {title: 'Hero image v3', bot: 'writer', kind: 'image', h: 6, pins: ['Chris']}),
  row('file-video', {title: 'Launch video cut', bot: 'writer', kind: 'media', h: 26}),
  row('file-traffic', {title: 'Traffic export', bot: 'data', kind: 'data', h: 30}),
  row('file-pricing', {title: 'Competitor pricing', bot: 'sales', kind: 'document', h: 50}),
  row('file-macros', {title: 'Support macros', bot: 'support', kind: 'document', h: 70}),
  row('file-rubric', {title: 'Recruiting rubric', bot: 'recruiter', kind: 'document', h: 480}),
  row('file-old', {title: 'Old brief', bot: 'ops', kind: 'document', h: 960, archived: true}),
];
// Forty-six more, so the list pages: 50 a page, and More brings the other ten (60 that are not archived).
const GEN_BOTS = ['cmo', 'writer', 'sales', 'support', 'ops', 'recruiter'], GEN_KINDS = ['document', 'image', 'page', 'other', 'media'];
for (let i = 0; i < 46; i++) FILES.push(row(`file-n${String(i).padStart(2, '0')}`, {title: `Note ${String(i + 1).padStart(2, '0')}`,
  bot: GEN_BOTS[i % 6]!, kind: GEN_KINDS[i % 5]!, h: 100 + i * 17}));

// The server's filters, as GET /api/v2/files applies them: newest activity first.
function listing(q: URLSearchParams, rows: FileRow[]): FileRow[] {
  const kind = q.get('kind'), bot = q.get('bot'), team = q.get('team'), since = q.get('since');
  const role = q.get('role'), linked = q.get('linked'), writer = q.get('writer');
  return rows.filter(f => f.archived === (q.get('archived') === 'true') && (!kind || f.library_kind === kind)
    && (!bot || f.bot === bot) && (!team || teamOf(f.bot) === team) && (!since || f.last_activity_at >= since)
    && (!role || f.links.some(l => l.role === role)) && (!linked || f.links.some(l => l.target === linked))
    && (!writer || f.writers.some(w => w.actor === writer))
    && (q.get('pinned') !== 'true' || f.pins.length > 0))
    .sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at));
}

// state: {files, mode ('error' or 'slow' or ''), listed, searched, opened} is what the page was served and asked for.
async function open(browser: Browser, viewport: Viewport, state: FetchState, theme: string) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors: string[] = [];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (isUiPath(p)) return serveUi(route, p);
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: TEAMS});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'cmo', state: 'idle'}]});
    if (p === '/api/v2/goals') return json({goals: GOALS, chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') {
      const word = (q.get('search') || '').toLowerCase(), ids = (q.get('ids') || '').split(',').filter(Boolean);
      return json({tasks: TASKS.filter(t => ids.includes(t.id) || (word && t.title.toLowerCase().includes(word)))});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/groups') return json(TEAMS);
    if (p === '/api/v2/files') {
      state.listed.push(q.toString());
      if (state.mode === 'error') return json({detail: 'The file list is down'}, 500);
      if (state.mode === 'slow') await new Promise(r => setTimeout(r, 900));
      const rows = listing(q, state.files), offset = Number(q.get('cursor') || 0), limit = Number(q.get('limit'));
      const more = offset + limit < rows.length;
      return json({files: rows.slice(offset, offset + limit), has_more: more, next_cursor: more ? String(offset + limit) : null});
    }
    if (p === '/api/v2/files/search') {
      state.searched.push(q.get('q'));
      const term = (q.get('q') || '').toLowerCase();
      return json({results: state.files.filter(f => !f.archived && f.title.toLowerCase().includes(term))
        .map(f => ({...f, hit: true, url: null, updated_at: f.last_activity_at, excerpt: EXCERPT[f.id] || ''}))});
    }
    const file = p.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (file) { state.opened.push(file[1]!); return route.fulfill({contentType: 'text/plain', body: 'Trial,Paid\n'}); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}

const fresh = (files: FileRow[] = FILES, mode = ''): FetchState => ({files, mode, listed: [], searched: [], opened: []});
const titles = (page: Page) => page.locator('.lf-row .lf-title').allInnerTexts();
// Kept for parity with the spike; unused today, so only its type is referenced here (no runtime change).
export type TitlesHelper = typeof titles;
// Waits until the rows on the page are exactly these titles, in this order (a filter change reloads the list).
async function settle(page: Page, want: string[]) {
  await page.waitForFunction(w => JSON.stringify([...(document.querySelectorAll('.lf-row .lf-title') as unknown as Iterable<Element>)].map(e => e.textContent)) === JSON.stringify(w),
    want, {timeout: 5000});
}
const popOpen = (page: Page) => page.evaluate(() => document.getElementById('lf-filter-pop')!.matches(':popover-open'));
async function openPopover(page: Page) {
  if (!(await popOpen(page))) await page.locator('.lf-fbtn').click();
  await page.locator('#lf-filter-pop').waitFor({state: 'visible'});
}
// The popover's entrance has finished (a screenshot taken mid-animation shows nothing). Looping animations (the loading
// shimmer) never finish, so only the finite ones are waited for, and no longer than a second.
const settled = (page: Page) => page.evaluate(() => Promise.race([
  Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().endTime !== Infinity)
    .map(a => a.finished.catch(() => {}))),
  new Promise(r => setTimeout(r, 1000))]));
async function until(test: () => boolean, ms = 5000) {
  for (const end = Date.now() + ms; !test(); ) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 50)); }
}

async function desktop(browser: Browser) {
  const state = fresh();
  const {page, errors} = await open(browser, DESKTOP, state, 'dark');
  await page.goto('https://tico-ui.test/#/library/files');
  await page.locator('.lf-row').first().waitFor();
  if (shots) await page.screenshot({path: path.join(shots, 'files-desktop-dark.png')});

  // A table: the column heads, one line a row, Updated at the right edge, the columns left to right.
  assert.deepEqual((await page.locator('.lf-tbl thead th').evaluateAll(els => els.map(e => e.textContent || ''))).map(s => s.trim()).filter(Boolean),
    ['Name', 'Kind', 'Made by', 'Team', 'Updated'], 'desktop: column heads');
  assert(await page.locator('.lf-tbl thead').isVisible(), 'desktop: the heads show');
  // The shared table look (.tt-list, as Contacts and Skills): small uppercase heads, flat rows, no frame around it.
  const look = await page.locator('.lf-tbl').evaluate(t => {
    const th = getComputedStyle(t.querySelector('th')!), frame = t.closest('.lf-list')!;
    return {shared: t.classList.contains('tt-list'), upper: th.textTransform, radius: getComputedStyle(t).borderRadius + getComputedStyle(frame).borderRadius,
      border: getComputedStyle(frame).borderTopWidth};
  });
  assert.deepEqual(look, {shared: true, upper: 'uppercase', radius: '0px0px', border: '0px'}, 'desktop: the shared table, no frame');
  assert.equal(await page.locator('.lf-meta').first().isVisible(), false, 'desktop: no Kind · Bot line under the name');
  const rows = await page.locator('.lf-row').evaluateAll(els => els.slice(0, 12).map(el => {
    const box = (c: string) => el.querySelector(c)!.getBoundingClientRect(), r = el.getBoundingClientRect();
    return {h: r.height, titleH: box('.lf-title').height, right: r.right, whenRight: box('.lf-when').right,
      x: ['.lf-name', '.lf-kind', '.lf-by', '.lf-team', '.lf-when'].map(c => Math.round(box(c).left))};
  }));
  for (const r of rows) {
    assert(r.h >= 36 && r.h <= 44, `desktop: a row is about 40px, got ${r.h}`);
    assert(r.titleH <= 20, `desktop: the name is one line, got ${r.titleH}`);
    assert(Math.abs(r.whenRight - r.right) <= 2, 'desktop: Updated sits at the right edge');
    assert.deepEqual(r.x, [...r.x].sort((a, b) => a - b), 'desktop: the columns run left to right');
  }
  assert.equal(await page.locator('.lf-row').count(), 50, 'desktop: a page of 50');
  const signup = page.locator('.lf-row').filter({hasText: 'Signup funnel'});
  assert.equal((await signup.locator('.lf-kind').innerText()).trim(), 'Data', 'desktop: the kind column');
  assert.equal((await signup.locator('.lf-by-n').innerText()).trim(), 'Engineering Lead', 'desktop: who made it');
  assert.equal((await signup.locator('.lf-team').innerText()).trim(), 'Engineering', 'desktop: the team of the bot');
  // Pinned is a mark, not a pill: no words in the row, its name for a screen reader and on hover.
  assert.equal(await page.locator('.lf-row .pill, .lf-pins').count(), 0, 'desktop: no pills in the rows');
  assert.equal(await signup.locator('.lf-pin').getAttribute('aria-label'), 'Pinned in 2 places', 'desktop: pinned twice');
  assert.equal((await signup.locator('.lf-text .lf-line').innerText()).trim(), 'Signup funnel', 'desktop: the mark adds no words');
  assert.equal(await page.locator('.lf-row').filter({hasText: 'Hero image v3'}).locator('.lf-pin').getAttribute('aria-label'), 'Pinned', 'desktop: pinned once');
  // A file's name carries its ending and never its folder; the full title is the name's tooltip.
  const guide = page.locator('.lf-row:has([data-id="file-guide"])');
  assert.equal((await guide.locator('.lf-title').innerText()).trim(), 'holiday-setup.md', 'desktop: the name without its folder');
  assert.equal(await guide.locator('.lf-title').getAttribute('title'), 'guides/holiday-setup.md', 'desktop: the folder is in the tooltip');
  const shown = async (id: string) => (await page.locator(`.lf-row:has([data-id="${id}"]) .lf-title`).innerText()).trim();
  assert.equal(await shown('file-sales'), 'Sales 2024-25.xlsx', 'desktop: a slash in a title is not a folder when the file has its name');
  assert.equal(await shown('file-q3q4'), 'Q3/Q4 plan.pdf', 'desktop: words with spaces are not a path');
  assert.equal(await shown('file-export'), 'traffic-2026.csv', 'desktop: a path without a name shows its last part');
  assert.equal(await signup.locator('.lf-pin').getAttribute('title'), 'Pinned: Ana, Chris', 'desktop: the pin says where on hover');
  // The one page header: Files, the search and the filter icon, then ⋯ last.
  const head = page.locator('#lf-root > header.ph');
  assert.equal((await head.locator('.ph-t').innerText()).trim(), 'Files', 'desktop: the header title');
  assert.equal(await head.locator('.ph-tools .lf-q, .ph-tools .lf-fbtn').count(), 2, 'desktop: search and filter are the header tools');
  assert.equal(await head.evaluate(h => [...h.children].filter(c => !c.matches('[popover]')).at(-1)?.classList.contains('ph-more')), true, 'desktop: ⋯ is last');
  // Links: a Google file opens in a new tab, a page opens its page, a stored file opens in the viewer.
  const q4 = page.locator('.lf-row').filter({hasText: 'Q4 launch plan'});
  assert.equal(await q4.locator('a.lf-open').getAttribute('href'), GDOC, 'desktop: a linked file goes to its provider');
  assert.equal(await q4.locator('a.lf-open').getAttribute('target'), '_blank', 'desktop: and opens in a new tab');
  assert.equal(await page.locator('.lf-row').filter({hasText: 'Weekly report'}).locator('a.lf-open').getAttribute('href'), '#/page/file-weekly', 'desktop: a page opens the page');
  assert.equal((await page.locator('.lf-row').filter({hasText: 'Weekly report'}).locator('.lf-by-n').innerText()).trim(), 'AI CMO, Ana', 'desktop: a page is made by its writers');
  // A file whose bot has no team shows nothing there, not a dash.
  assert.ok((await page.locator('td.lf-team').allInnerTexts()).every(x => x.trim() !== '—'), 'desktop: no dash for no team');

  // More: the next page is asked for with its cursor, and the list grows.
  await page.locator('[data-lf-more]').click();
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 60);
  assert(state.listed.some(s => /cursor=50/.test(s)), 'desktop: More asks for the next page');
  assert.equal(await page.locator('[data-lf-more]').count(), 0, 'desktop: no More at the end');

  // The filter popover: its sections, each pick is a chip, the count on the button, the address, the ticks.
  await page.locator('.lf-fbtn').click();
  await page.locator('#lf-filter-pop').waitFor({state: 'visible'});
  assert.deepEqual(await page.locator('#lf-filter-pop .lf-sec').evaluateAll(els => els.map((e: Element) => e.getAttribute('aria-label'))),
    ['Kind', 'Updated', 'Bot', 'Team', 'Linked to', 'Written by', 'Show'], 'popover: its sections');
  await page.waitForFunction(() => document.querySelector('.lf-fbtn')!.getAttribute('aria-expanded') === 'true', null, {timeout: 5000});
  assert.equal(await page.locator('#lf-filter-pop .lf-sec[aria-label="Bot"] .tl-menu-q').count(), 1, 'popover: a find box for nine bots');
  assert.equal(await page.locator('#lf-filter-pop').evaluate(el => el.scrollTop), 0, 'popover: opens with Kind at the top');
  assert(await page.locator('#lf-filter-pop').evaluate(el => el.getBoundingClientRect().bottom <= innerHeight), 'popover: it sits inside a 900px window');
  assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.lfSet), 'kind', 'popover: focus goes to the first pick, not the bot find box');
  await page.locator('[data-lf-set="kind"][data-lf-value="data"]').click();
  await settle(page, ['Signup funnel', 'Budget 2026', 'Traffic export']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '1', 'popover: the count on the button');
  assert.equal(await page.locator('.lf-fbtn').getAttribute('aria-label'), 'Filters, 1 active');
  assert.equal(await page.locator('.lf-bar .tl-chip-main').first().getAttribute('aria-label'), 'Kind: Data. Change', 'popover: a chip for the pick');
  assert.match(page.url(), /kind=data/, 'popover: the kind is in the address');
  assert.equal(await page.locator('[data-lf-set="kind"][data-lf-value="data"]').getAttribute('aria-checked'), 'true', 'popover: the tick follows');
  assert.equal(await page.locator('[data-lf-set="kind"][data-lf-value=""]').getAttribute('aria-checked'), 'false', 'popover: All is off');

  // The find box narrows the bot list, and a bot pick narrows the rows.
  await page.locator('#lf-filter-pop .lf-sec[aria-label="Bot"] .tl-menu-q').fill('engineer');
  const bots = await page.locator('#lf-filter-pop .lf-sec[aria-label="Bot"] .tl-mi').evaluateAll(els => els.filter(e => !(e as HTMLElement).hidden).map(e => (e as HTMLElement).dataset.lfValue));
  assert.deepEqual(bots, ['engineer'], 'popover: the find box narrows the bots');
  await page.locator('[data-lf-set="bot"][data-lf-value="engineer"]').click();
  await settle(page, ['Signup funnel']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '2', 'popover: two filters');
  assert.equal(await page.locator('.lf-bar .tl-chip-main').nth(1).getAttribute('aria-label'), 'Bot: Engineering Lead. Change');
  // A chip's ✕ takes that filter off (the popover closes on the click outside it).
  await page.locator('.lf-bar [data-lf-clear="bot"]').click();
  await settle(page, ['Signup funnel', 'Budget 2026', 'Traffic export']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '1', 'chip ✕: one filter left');

  // Updated and Team, then the popover again with the chips under the row.
  await openPopover(page);
  await page.locator('[data-lf-set="days"][data-lf-value="7"]').click();
  await page.locator('[data-lf-set="team"][data-lf-value="eng"]').click();
  await settle(page, ['Signup funnel', 'Traffic export']);
  assert.equal(await page.locator('.lf-bar .tl-chip').count(), 3, 'three chips for three filters');
  await settled(page);
  if (shots) await page.screenshot({path: path.join(shots, 'files-desktop-popover-dark.png')});
  await page.locator('[data-lf-toggle="pinned"]').click();
  await settle(page, ['Signup funnel']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '4', 'four filters');
  // The filters come back from the address on a reload.
  await page.reload();
  await settle(page, ['Signup funnel']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '4', 'reload: the filters come back from the address');
  assert.equal(await page.locator('.lf-bar .tl-chip').count(), 4, 'reload: a chip for each');
  assert.match(page.url(), /team=eng/, 'the team is in the address');
  assert.match(page.url(), /days=7/, 'the time is in the address');
  await page.locator('.lf-bar [data-lf-clear="kind"]').click();
  await settle(page, ['Signup funnel']);
  assert.equal(await page.locator('.lf-fbn').textContent(), '3', 'the chip ✕ takes off one');
  await page.locator('.lf-bar [data-lf-clear-all]').click();
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 50);
  assert.equal(await page.locator('.lf-bar').isVisible(), false, 'clear: no chips');
  assert.equal(await page.locator('.lf-fbn').isVisible(), false, 'clear: no count');
  assert.equal(new URL(page.url()).hash, '#/library/files', 'clear: a plain address');

  // Archived shows what is archived, with its label in the row.
  await openPopover(page);
  await page.locator('[data-lf-toggle="archived"]').click();
  await settle(page, ['Old brief']);
  assert.equal(await page.locator('.lf-row .lf-arch').getAttribute('aria-label'), 'Archived', 'archived: marked in the row');
  await page.locator('.lf-bar [data-lf-clear-all]').click();
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 50);

  // Role, Linked to and Written by send role, linked and writer, and narrow the rows the way the server does. The Linked to
  // find box lists the tasks its search finds; a page with links and writers is the one that matches all three.
  await openPopover(page);
  await page.locator('[data-lf-set="role"][data-lf-value="Spec"]').click();
  await settle(page, ['Weekly report']);
  assert.match(state.listed.at(-1)!, /role=Spec/, 'role: the list asks for the role');
  assert.equal(await page.locator('.lf-bar .tl-chip-main').first().getAttribute('aria-label'), 'Role: Spec. Change', 'role: a chip for it');
  await page.locator('#lf-filter-pop .lf-sec[aria-label="Linked to"] .lf-find').fill('signup');
  await page.locator('[data-lf-set="linked"][data-lf-value="task:t-19385"]').click();
  await settle(page, ['Weekly report']);
  assert.match(state.listed.at(-1)!, /linked=task%3At-19385/, 'linked: the list asks for the target');
  assert.equal(await page.locator('.lf-bar .tl-chip-main').nth(1).getAttribute('aria-label'), 'Linked to: Signup funnel fix. Change', 'linked: a chip named by the task');
  await page.locator('#lf-filter-pop .lf-sec[aria-label="Written by"] .tl-menu-q').fill('writer');
  await page.locator('[data-lf-set="writer"][data-lf-value="bot:writer"]').click();
  await page.waitForFunction(() => document.querySelector('.lf-list')!.textContent!.includes('Nothing matches.'));
  assert.match(state.listed.at(-1)!, /writer=bot%3Awriter/, 'writer: the list asks for the writer');
  assert.equal(await page.locator('.lf-bar .tl-chip-main').nth(1).getAttribute('aria-label'), 'Written by: Writer. Change', 'writer: a chip for the writer');
  await page.locator('.lf-bar [data-lf-clear-all]').click();
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 50);

  // Search: the hit with its excerpt stays one line; nothing matching says so.
  await page.locator('.lf-q').fill('signup');
  await settle(page, ['Signup funnel']);
  assert.equal((await page.locator('.lf-excerpt').innerText()).trim(), 'Trial to paid, by week and plan', 'search: the excerpt');
  assert(await page.locator('.lf-row').first().evaluate(el => el.getBoundingClientRect().height) <= 44, 'search: a hit is still one line');
  assert.match(page.url(), /q=signup/, 'search: in the address');
  await page.locator('.lf-q').fill('zzz');
  await page.waitForFunction(() => document.querySelector('.lf-list')!.textContent!.includes('Nothing matches.'));
  await page.locator('.lf-q').fill('');
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 50);

  // A stored file opens in the viewer.
  // A click anywhere on the row (here its Kind cell, not the name) opens the file: the row is one target.
  await page.locator('.lf-row').filter({hasText: 'Budget 2026'}).locator('td.lf-kind').click();
  await until(() => state.opened.includes('file-budget'));
  assert.deepEqual(errors, [], 'desktop: page errors');
  await page.close();
  console.log('files page (desktop 1440): columns and one line a row, More, the filter popover and its chips, the address, archived, search, opening a file');
}

async function desktopLight(browser: Browser) {
  const {page, errors} = await open(browser, DESKTOP, fresh(), 'light');
  await page.goto('https://tico-ui.test/#/library/files');
  await page.locator('.lf-row').first().waitFor();
  if (shots) await page.screenshot({path: path.join(shots, 'files-desktop-light.png')});
  assert.deepEqual(errors, [], 'desktop light: page errors');
  await page.close();
  console.log('files page (desktop 1440, light): the default list');
}

async function phone(browser: Browser) {
  const state = fresh();
  const {page, errors} = await open(browser, PHONE, state, 'dark');
  await page.goto('https://tico-ui.test/#/library/files');
  await page.locator('.lf-row').first().waitFor();
  if (shots) await page.screenshot({path: path.join(shots, 'files-phone-dark.png')});

  // Two lines a row: the name, then Kind · Bot · time; no columns.
  assert.equal(await page.locator('.lf-tbl thead').isVisible(), false, 'phone: no column heads');
  for (const hidden of ['.lf-kind', '.lf-by', '.lf-team', '.lf-when']) assert.equal(await page.locator(hidden).first().isVisible(), false, `phone: ${hidden} is a column, not shown`);
  const signup = page.locator('.lf-row').filter({hasText: 'Signup funnel'});
  assert.match((await signup.locator('.lf-meta').innerText()).trim(), /^Data · Engineering Lead · \S/, 'phone: Kind · Bot · time under the name');
  const heights = await page.locator('.lf-row').evaluateAll(els => els.slice(0, 10).map(el => el.getBoundingClientRect().height));
  for (const h of heights) assert(h >= 48 && h <= 76, `phone: two lines a row, got ${h}`);
  const title = (await signup.locator('.lf-title').boundingBox())!, meta = (await signup.locator('.lf-meta').boundingBox())!;
  assert(title.y + title.height <= meta.y + 1, 'phone: the name is above its line');
  // The search and the filter button share the top row, and the page does not scroll sideways.
  const qBox = (await page.locator('.lf-q').boundingBox())!, fBox = (await page.locator('.lf-fbtn').boundingBox())!;
  assert(Math.abs((qBox.y + qBox.height / 2) - (fBox.y + fBox.height / 2)) < 4, 'phone: the search and the filter share a row');
  assert(qBox.x < fBox.x, 'phone: the filter sits beside the search');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone: no sideways scroll');

  // A page's meta line: its links (each a link to its route, the count of the rest after them), then who wrote it, then when.
  // A page with no links names its kind and its bot.
  const weekly = page.locator('.lf-row').filter({hasText: 'Weekly report'});
  assert.match((await weekly.locator('.lf-meta').innerText()).trim(), /^Spec of D-18463, D-19385 \+2 · AI CMO, Ana · \S/, 'phone: a page names its links and writers');
  assert.deepEqual(await weekly.locator('.lf-meta a').evaluateAll(els => els.map(e => e.getAttribute('href'))), ['#/task/t-18463', '#/task/t-19385'],
    'phone: each link goes to its task');
  assert.match((await page.locator('.lf-row').filter({hasText: 'Note 03'}).locator('.lf-meta').innerText()).trim(), /^Page · Sales Bot · \S/, 'phone: a page with no links names its kind and bot');

  // The popover on a phone: a sheet within the screen; a pick works.
  await openPopover(page);
  const sheet = (await page.locator('#lf-filter-pop').boundingBox())!;
  assert(sheet.width <= PHONE.width, 'phone: the filters fit the screen');
  await settled(page);
  if (shots) await page.screenshot({path: path.join(shots, 'files-phone-popover-dark.png')});
  await page.locator('[data-lf-set="kind"][data-lf-value="data"]').click();
  await settle(page, ['Signup funnel', 'Budget 2026', 'Traffic export']);
  assert.equal(await page.locator('.lf-bar .tl-chip').count(), 1, 'phone: the pick is a chip');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.getElementById('lf-filter-pop')!.matches(':popover-open'));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone with chips: no sideways scroll');
  assert.deepEqual(errors, [], 'phone: page errors');
  await page.close();
  console.log('files page (phone 390): two lines a row, search and filter on one row, the filter popover as a sheet');
}

async function states(browser: Browser) {
  const {page: empty, errors: emptyErrors} = await open(browser, DESKTOP, fresh([]), 'dark');
  await empty.goto('https://tico-ui.test/#/library/files');
  await empty.locator('.lf-list').getByText('No files yet.').waitFor();
  assert.equal(await empty.locator('.lf-tbl').count(), 0, 'empty: no table without rows');
  assert.deepEqual(emptyErrors, [], 'empty: page errors');
  await empty.close();

  const {page: failed, errors: failedErrors} = await open(browser, DESKTOP, fresh(FILES, 'error'), 'dark');
  await failed.goto('https://tico-ui.test/#/library/files');
  await failed.locator('.lf-list .err').waitFor();
  assert.match(await failed.locator('.lf-list .err').innerText(), /\S/, 'error: a message in place of the rows');
  assert.equal(await failed.locator('.lf-tbl').count(), 0, 'error: no table');
  assert.deepEqual(failedErrors, [], 'error: page errors');
  await failed.close();

}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    await desktop(browser);
    await desktopLight(browser);
    await phone(browser);
    await states(browser);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
