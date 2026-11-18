// The page view's links, Edit and history (ui/app/pages.ts). Chips come from the page read's links, and a writer is not a
// chip. "+ Link" offers its role and a search: a document role (Spec, QA report, ...) only offers tasks. A chip's ✕ removes
// its link (DELETE with target and role). Edit is only for a page its reader may write: Save posts page, text, name and
// note as a new version, and the editor's own markup is not saved. Version history and the status line name the person
// who wrote a version. Fixtures only, no network.
import type {Browser, LaunchOptions, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

const DESKTOP = {width: 1440, height: 900}, PHONE = {width: 390, height: 844};
const now = Date.now(), at = (hours: number) => new Date(now - hours * 36e5).toISOString();

// The bots, people, goals and tasks the picker searches (their shapes as the boot and the task list give them).
const BOTS = [{id: 'cmo', slug: 'cmo', name: 'cmo', display_name: 'AI CMO', team: 'marketing', org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [], schedules: []},
  {id: 'data', slug: 'data', name: 'data', display_name: 'Data Analyst', team: 'eng', org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [], schedules: []}];
const PEOPLE = [{id: 'ana', name: 'Ana'}, {id: 'steven', name: 'Steven'}];
const GOALS = [{id: 'g-signups', title: 'Grow signups', status: 'active', owner_id: 'cmo', parent_id: null, rank: 1}];
const TASKS = [{id: 't-18463', number: 18463, title: 'Edit property copy', status: 'open'},
  {id: 't-19385', number: 19385, title: 'Weekly review', status: 'open'},
  {id: 't-20002', number: 20002, title: 'Launch plan', status: 'open'}];
const TASK_NAMES: Record<string, string> = {'t-18463': 'D-18463 Edit property copy', 't-19385': 'D-19385 Weekly review', 't-20002': 'D-20002 Launch plan'};

interface LinkRow {target: string; role: string; name: string; by: string; at: string; approval?: unknown}
interface WriterRow {actor: string; name: string; versions: number; last_at: string}
interface VersionRow {version: number; actor: string; created_at: string}
// A page as the server holds it for these fixtures: its read's fields, its versions and each version's HTML.
interface PageFixture {
  file_id: string; title: string; bot_id: string; bot_name: string; version: number;
  updated_by: string; updated_by_name: string; updated_at: string; may_write: boolean;
  links: LinkRow[]; writers: WriterRow[]; versions: VersionRow[]; html: Record<number, string>;
}
interface Call {method: string; path: string; query: string; body: unknown}
interface Fixture {pages: Record<string, PageFixture>; calls: Call[]}
interface Viewport {width: number; height: number}

const pageHtml = (n: number) => `<!doctype html><html><head><meta charset="utf-8"><style>p{color:#333}</style></head>`
  + `<body><h2>Engineering weekly</h2><p>Shipped ${n} changes.</p></body></html>`;
// Five versions, the newest by the Engineering Lead (the bot) before a person, Steven, published the fifth.
const weekly = (): PageFixture => ({file_id: 'file-weekly', title: 'Engineering weekly', bot_id: 'engineering-manager', bot_name: 'Engineering Lead',
  version: 5, updated_by: 'human:steven', updated_by_name: 'Steven', updated_at: at(2), may_write: true,
  links: [
    {target: 'task:t-18463', role: 'Spec', name: TASK_NAMES['t-18463']!, by: 'human:steven', at: at(72), approval: {version: 4}},
    {target: 'task:t-19385', role: 'related', name: TASK_NAMES['t-19385']!, by: 'bot:engineering-manager', at: at(30)},
    {target: 'bot:cmo', role: 'related', name: 'AI CMO', by: 'human:ana', at: at(20)},
    {target: 'human:steven', role: 'writer', name: 'Steven', by: 'human:ana', at: at(90)},
  ],
  writers: [{actor: 'bot:engineering-manager', name: 'Engineering Lead', versions: 4, last_at: at(50)},
    {actor: 'human:steven', name: 'Steven', versions: 1, last_at: at(2)}],
  versions: [5, 4, 3, 2, 1].map(v => ({version: v, actor: v === 5 ? 'human:steven' : 'bot:engineering-manager', created_at: at(v === 5 ? 2 : 50 + v)})),
  html: Object.fromEntries([1, 2, 3, 4, 5].map(v => [v, pageHtml(v === 5 ? 9 : v)])),
});
// A page one person may only read: no ✕, no +Link, no Edit.
const plain = (): PageFixture => ({file_id: 'file-plain', title: 'Launch notes', bot_id: 'cmo', bot_name: 'AI CMO', version: 1,
  updated_by: 'human:ana', updated_by_name: 'Ana', updated_at: at(4), may_write: false,
  links: [{target: 'bot:cmo', role: 'related', name: 'AI CMO', by: 'human:ana', at: at(4)}],
  writers: [{actor: 'human:ana', name: 'Ana', versions: 1, last_at: at(4)}],
  versions: [{version: 1, actor: 'human:ana', created_at: at(4)}], html: {1: '<!doctype html><html><body><p>Launch notes</p></body></html>'}});

const fixture = (): Fixture => ({pages: {'file-weekly': weekly(), 'file-plain': plain()}, calls: []});

// The name a link's target has, as the server gives it on the page read.
function nameFor(target: string): string {
  const [kind = '', ...rest] = target.split(':'), id = rest.join(':');
  if (kind === 'task') return TASK_NAMES[id] ?? id;
  if (kind === 'bot') return BOTS.find(b => b.id === id)?.display_name ?? id;
  if (kind === 'human') return PEOPLE.find(p => p.id === id)?.name ?? id;
  if (kind === 'goal') return GOALS.find(g => g.id === id)?.title ?? id;
  return id;
}
// GET /api/v2/pages/{id}: the page, its links, writers and may_write, with the reader's flags (PageShown).
const pageRead = (p: PageFixture) => ({page: {file_id: p.file_id, title: p.title, bot_id: p.bot_id, bot_name: p.bot_name, version: p.version,
  updated_at: p.updated_at, updated_by: p.updated_by, updated_by_name: p.updated_by_name, cadence: 'none', as_of: null, stale_days: null,
  replaced_by_id: null, replaced_by_title: null, archived: false, task_id: null, src: null,
  links: p.links, writers: p.writers, may_write: p.may_write}, can_manage: false, can_delete: false, pinned: []});

// A request's JSON body, or null when it is not JSON (the route answers rather than throwing).
function jsonBody(request: {postDataJSON(): unknown}): any {
  try { return request.postDataJSON(); } catch { return null; }
}

async function open(browser: Browser, viewport: Viewport, fx: Fixture) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors: string[] = [];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'light'); } catch {} });
  await page.route('**/*', async (route: Route) => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, q = url.searchParams, method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (isUiPath(p)) return serveUi(route, p);
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: PEOPLE, org_groups: []});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals') return json(q.get('all') ? {goals: GOALS} : {goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: q.get('q') ? TASKS.filter(t => `${t.number} ${t.title}`.toLowerCase().includes(q.get('q')!.toLowerCase())) : []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/groups') return json([]);
    if (p === '/api/v2/files/search') {
      const term = (q.get('q') || '').toLowerCase();
      return json({results: Object.values(fx.pages).filter(x => x.title.toLowerCase().includes(term)).map(x => ({id: x.file_id, title: x.title}))});
    }
    const pages = p.match(/^\/api\/v2\/pages\/([^/]+)(\/links)?$/);
    const body = method === 'POST' || method === 'PATCH' ? jsonBody(request) : null;
    if (pages) {
      const pg = fx.pages[decodeURIComponent(pages[1]!)];
      if (!pg) return json({error: 'not_found'}, 404);
      fx.calls.push({method, path: p, query: url.search, body});
      if (!pages[2] && method === 'GET') return json(pageRead(pg));
      if (pages[2] && method === 'POST') {
        pg.links.push({target: body.target, role: body.role, name: nameFor(body.target), by: 'human:ana', at: new Date().toISOString()});
        return json(pageRead(pg));
      }
      if (pages[2] && method === 'DELETE') {
        const target = q.get('target'), role = q.get('role');
        pg.links = pg.links.filter(l => !(l.target === target && l.role === role));
        return json(pageRead(pg));
      }
    }
    if (p === '/api/v2/pages' && method === 'POST') {
      const pg = fx.pages[body.page];
      if (!pg) return json({error: 'not_found'}, 404);
      fx.calls.push({method, path: p, query: url.search, body});
      pg.version += 1;
      pg.html[pg.version] = body.text;
      pg.versions.unshift({version: pg.version, actor: 'human:ana', created_at: new Date().toISOString()});
      if (!pg.writers.some(w => w.actor === 'human:ana')) pg.writers.push({actor: 'human:ana', name: 'Ana', versions: 0, last_at: new Date().toISOString()});
      const mine = pg.writers.find(w => w.actor === 'human:ana');
      if (mine) { mine.versions += 1; mine.last_at = new Date().toISOString(); }
      pg.updated_by = 'human:ana'; pg.updated_by_name = 'Ana'; pg.updated_at = new Date().toISOString();
      return json({page: pageRead(pg).page, unembedded: [], created: false});
    }
    const versions = p.match(/^\/api\/v2\/files\/([^/]+)\/versions$/);
    if (versions) {
      const pg = fx.pages[decodeURIComponent(versions[1]!)];
      return pg ? json({versions: pg.versions}) : json({versions: []});
    }
    const file = p.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (file) {
      const pg = fx.pages[decodeURIComponent(file[1]!)];
      if (!pg) return json({detail: 'gone'}, 404);
      const v = Number(q.get('v')) || pg.version;
      return route.fulfill({contentType: 'text/html', headers: {'content-disposition': `inline; filename="${pg.file_id}.html"`}, body: pg.html[v] || ''});
    }
    return json(unrouted(p));
  });
  return {page, errors};
}

// Desktop: the chips, +Link with a document role, the link posted and removed, Edit, Save, and the history names.
async function desktop(browser: Browser) {
  const fx = fixture();
  const {page, errors} = await open(browser, DESKTOP, fx);
  await page.goto('https://tico-ui.test/#/page/file-weekly');
  await page.locator('#pg-view .pg-chip-link').first().waitFor({timeout: 10000});
  // Chips from the links: role and name, a writer is not one, a chip opens its target.
  const names = await page.locator('#pg-view .pg-chip-link').evaluateAll(els => els.map(el => el.lastElementChild?.textContent?.trim()));
  assert.deepEqual(names, ['Spec of D-18463 Edit property copy', 'D-19385 Weekly review', 'AI CMO'], 'chips from the links, no writer');
  assert.equal(await page.locator('#pg-view .pg-chip-link').first().getAttribute('href'), '#/task/t-18463', 'a task chip opens its task');
  assert.equal(await page.locator('#pg-view .pg-chip-link').nth(2).getAttribute('href'), '#/bot/cmo', 'a bot chip opens its bot');
  assert.equal(await page.locator('#pg-view .pg-chip-ok').textContent(), "OK'd v4", "the version the task OK'd");
  // The status line names the newest writer, a person, and not the bot the page was first made by.
  const status = await page.locator('#pg-view .pg-head .pg-status').innerText();
  assert.match(status, /^Updated .+ by Steven · v5$/, `status line: ${status}`);

  // + Link with a document role: only tasks are offered.
  await page.locator('#pg-view [data-pg-link-open]').click();
  await page.locator('#pg-view .pg-link-pick').waitFor({state: 'visible'});
  await page.locator('#pg-view .pg-link-role').selectOption('Spec');
  await page.locator('#pg-view .pg-link-q').fill('Launch');
  await page.locator('#pg-view .pg-link-opt', {hasText: 'D-20002 Launch plan'}).waitFor({timeout: 5000});
  assert.equal(await page.locator('#pg-view .pg-link-opt').count(), 1, 'a document role offers tasks only');
  await page.locator('#pg-view .pg-link-opt', {hasText: 'D-20002 Launch plan'}).click();
  await page.locator('#pg-view .pg-chip-link', {hasText: 'Spec of D-20002 Launch plan'}).waitFor({timeout: 5000});
  const posted = fx.calls.filter(c => c.method === 'POST' && c.path === '/api/v2/pages/file-weekly/links').pop();
  assert.deepEqual(posted?.body, {target: 'task:t-20002', role: 'Spec'}, 'the link posts its target and role');
  assert.equal(await page.locator('#pg-view .pg-link-pick').isHidden(), true, 'the picker closes after a pick');

  // A related bot: a link already there is not offered again.
  await page.locator('#pg-view [data-pg-link-open]').click();
  await page.locator('#pg-view .pg-link-role').selectOption('related');
  await page.locator('#pg-view .pg-link-q').fill('CMO');
  await page.locator('#pg-view .pg-link-results').getByText('No matches').waitFor({timeout: 5000});
  await page.locator('#pg-view .pg-link-q').fill('data');
  await page.locator('#pg-view .pg-link-opt', {hasText: 'Data Analyst'}).click();
  await page.locator('#pg-view .pg-chip-link', {hasText: 'Data Analyst'}).waitFor({timeout: 5000});
  assert.deepEqual(fx.calls.filter(c => c.method === 'POST' && c.path.endsWith('/links')).pop()?.body, {target: 'bot:data', role: 'related'}, 'a related bot link');

  // Remove: the chip's ✕ sends DELETE with the target and the role.
  const chip = page.locator('#pg-view .pg-chip', {hasText: 'Spec of D-20002 Launch plan'});
  await chip.hover();
  await chip.locator('.pg-chip-x').click();
  await page.waitForFunction(() => !document.querySelector('#pg-view [data-pg-link-del="task:t-20002"]'), null, {timeout: 5000});
  const deleted = fx.calls.filter(c => c.method === 'DELETE').pop();
  assert.equal(new URLSearchParams(deleted?.query).get('target'), 'task:t-20002', 'DELETE names the target');
  assert.equal(new URLSearchParams(deleted?.query).get('role'), 'Spec', 'DELETE names the role');

  // Edit: first in ⋯; Save posts page, text, name and note, and the editor's markup stays out.
  await page.locator('#pg-view .pg-menu summary').click();
  const menu = await page.locator('#pg-view .pg-menu-list [role^=menuitem]').evaluateAll(els => els.map(el => el.textContent?.trim()));
  assert.equal(menu[0], 'Edit', `Edit is the first ⋯ item for a writer (${menu.join(' | ')})`);
  await page.locator('#pg-view [data-pg-act=edit]').click();
  await page.locator('#pg-view .pg-edit-frame').waitFor({timeout: 5000});
  await page.waitForFunction(() => !document.querySelector<HTMLButtonElement>('#pg-view [data-pg-edit-save]')?.disabled, null, {timeout: 5000});
  await page.evaluate(() => {
    const frame = document.querySelector<HTMLIFrameElement>('#pg-view .pg-edit-frame');
    const paragraph = frame?.contentDocument?.querySelector('p');
    if (paragraph) paragraph.textContent = 'Shipped 14 changes.';
  });
  await page.locator('#pg-view .pg-edit-note').fill('Fixed the count');
  await page.locator('#pg-view [data-pg-edit-save]').click();
  await page.waitForFunction(() => /v6/.test(document.querySelector('#pg-view .pg-head .pg-status')?.textContent || ''), null, {timeout: 5000});
  const saved = fx.calls.filter(c => c.method === 'POST' && c.path === '/api/v2/pages').pop();
  const text = (saved?.body as {text?: string} | null)?.text || '';
  assert.deepEqual({page: (saved?.body as {page?: string})?.page, name: (saved?.body as {name?: string})?.name,
    note: (saved?.body as {note?: string})?.note}, {page: 'file-weekly', name: 'file-weekly.html', note: 'Fixed the count'}, 'Save posts page, name and note');
  assert.match(text, /Shipped 14 changes\./, 'Save posts the edited text');
  assert.match(text, /<h2>Engineering weekly<\/h2>/, "the page's own markup is kept");
  assert.doesNotMatch(text, /data-tico-edit|contenteditable/, "the editor's own markup is not saved");

  // History names the writers: a person's version by name, a bot's by its name.
  await page.locator('#pg-view .pg-menu summary').click();
  await page.locator('#pg-view [data-pg-act=history]').click();
  assert.match(await page.locator('#pg-view [data-pg-v="5"] .pg-mi-sub').innerText(), /Steven/, 'a person writer shows by name');
  assert.match(await page.locator('#pg-view [data-pg-v="6"] .pg-mi-sub').innerText(), /Ana/, 'the new version shows its writer');
  assert.match(await page.locator('#pg-view [data-pg-v="4"] .pg-mi-sub').innerText(), /Engineering Lead/, 'a bot writer shows by name');
  assert.deepEqual(errors, [], 'desktop: page errors');
  await page.close();
  console.log('page view links (desktop): chips, + Link with a role, remove, Edit and Save, history names');
}

// A page its reader may not write: no +Link, no ✕, no Edit; its chips still open their targets.
async function readOnly(browser: Browser) {
  const fx = fixture();
  const {page, errors} = await open(browser, DESKTOP, fx);
  await page.goto('https://tico-ui.test/#/page/file-plain');
  await page.locator('#pg-view .pg-chip-link').first().waitFor({timeout: 10000});
  assert.equal(await page.locator('#pg-view [data-pg-link-open], #pg-view .pg-chip-x').count(), 0, 'no +Link and no ✕ without write');
  assert.equal(await page.locator('#pg-view .pg-chip-link').first().getAttribute('href'), '#/bot/cmo', 'a chip still opens its bot');
  await page.locator('#pg-view .pg-menu summary').click();
  assert.equal(await page.locator('#pg-view [data-pg-act=edit]').count(), 0, 'no Edit without may_write');
  assert.deepEqual(errors, [], 'read only: page errors');
  await page.close();
  console.log('page view links (read only): no +Link, ✕ or Edit without may_write');
}

// A phone: the chips wrap, and the page does not scroll sideways.
async function phone(browser: Browser) {
  const fx = fixture();
  const {page, errors} = await open(browser, PHONE, fx);
  await page.goto('https://tico-ui.test/#/page/file-weekly');
  await page.locator('#pg-view .pg-chip-link').first().waitFor({timeout: 10000});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'phone: no sideways scroll');
  assert.deepEqual(errors, [], 'phone: page errors');
  await page.close();
  console.log('page view links (phone 390): chips wrap, no sideways scroll');
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    await desktop(browser);
    await readOnly(browser);
    await phone(browser);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
