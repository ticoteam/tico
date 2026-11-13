// Overview › Dashboard and team pages (ui/app/dashboard.ts, backend/cards.py) against a fake server shaped as
// GET/PUT /api/v2/places/{place}, POST /places/{place}/order, /api/v2/pins and /api/v2/cards answer: the Dashboard
// opens with its default cards and the Campus tab beside it; the first change saves the defaults, then the order goes
// by pin ids (keys and a pointer drag); a card is added from its form, sized from its menu and removed; a page is
// pinned from Add page; a team opens from its name in the sidebar while its arrow still folds it; a member sees the
// cards and no way to arrange them; a phone has one column and no sideways scroll. Arranging is in edit mode only: Edit
// shows the grips, the menus and + Card / + Page, a redraw keeps it, and Done, Escape or leaving the page ends it. The app opens on My dashboard
// (person:me, Overview › Mine) with its own defaults.
// TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
interface Person { id: string; name: string; email: string; role: string; company_role: string; cloud: boolean; bot_admin?: boolean }
type Settings = {team: string; weeks?: number};
// App global: the signed-in person, and the employee list the page loads before it draws.
declare const S: {me: Person | null; emps?: unknown[]};
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900}, LAPTOP = {width: 1280, height: 800};
const OWNER: Person = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const MEMBER: Person = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true};
const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
const NAMES: Record<string, string> = {tasks_by_status: 'Tasks by status', done_per_week: 'Done per week', needs_you: 'Needs you', latest_pages: 'Latest pages',
  spend: 'Spend', kpi: 'KPI', automation_runs: 'Automation runs', needs_attention: 'Needs attention', unread_threads: 'Unread threads',
  my_tasks: 'My tasks', updates: 'Updates'};
const weeks = (n: number) => Array.from({length: n}, (_, i) => ({week: `2026-${String(8 + Math.floor(i / 4)).padStart(2, '0')}-${String(1 + i % 4 * 7).padStart(2, '0')}`, done: [3, 5, 2, 8, 6, 4, 9, 7][i % 8]!}));
const DATA: Record<string, (s?: Settings) => unknown> = {
  // The server counts open and done only (backend/cards.py tasks_by_status).
  tasks_by_status: () => ({counts: {open: 4, done: 7}}),
  done_per_week: s => ({weeks: weeks(s!.weeks || 8)}),
  needs_you: () => ({count: 2, items: [{id: 't1', kind: 'task', title: 'Approve the Acme launch plan', line: 'Say when the work is done', act: 'done', task_id: 't1'},
    {id: 'q1', kind: 'ask', title: 'Pick a pricing page layout', line: 'Which layout?', act: 'answer', task_id: 'q1'}]}),
  latest_pages: () => ({pages: [{file_id: 'f1', title: 'Weekly numbers', bot: 'eng-lead', updated_at: ago(90), archived: false}]}),
  needs_attention: () => DATA.needs_you!(),
  unread_threads: () => ({count: 0, threads: []}),
  my_tasks: () => ({count: 2, tasks: [{id: 't7', title: 'Send the Acme renewal quote', status: 'open', due_at: '2026-11-03', updated_at: ago(40)},
    {id: 't8', title: 'Review the onboarding checklist', status: 'doing', due_at: null, updated_at: ago(300)}]}),
  updates: () => ({unread: 1, updates: [{id: 'u1', author: 'eng-lead', kind: 'daily', day: '2026-11-01', headline: 'Shipped the billing fix',
    created_at: ago(60), read: false}]}),
};
const TEAMS: Record<string, string> = {eng: 'Engineering'};
const card = (id: string | null, template: string, settings: Settings, title = '') => ({id, template, title, settings, name: NAMES[template], heading: title || NAMES[template],
  team_name: TEAMS[settings.team] || '', hidden: false, data: DATA[template]!(settings), made_by: id ? 'human:ana' : null});
type CardView = ReturnType<typeof card>;
const defaults = (templates: string[], team = '') => templates.map((t, i) => ({id: null, item: null, position: i, size: 'medium', made_by: null,
  card: card(null, t, {team, ...(t === 'done_per_week' ? {weeks: 8} : {})})}));
const CHOICES = {templates: Object.entries(NAMES).map(([id, name]) => ({id, name, fields: id === 'done_per_week' ? ['team', 'weeks'] : id === 'kpi' ? ['kpi'] : id === 'latest_pages' ? ['team', 'count'] : ['team']})),
  teams: [{id: 'eng', name: 'Engineering', parent: null}], kpis: [],
  pages: [{file_id: 'f2', title: 'Acme launch plan', bot: 'eng-lead', updated_at: ago(30), archived: false}]};

interface Audience { everyone: boolean; people: string[]; teams: string[]; bots: string[] }
interface Body { ids?: string[]; place?: string; template?: string; settings?: Settings; title?: string; size?: string; item?: string; read?: Audience; audience?: Audience; write?: {teams: string[]}; revision?: number; see?: Audience }
interface Pin { id: string | null; item?: string | null | undefined; position?: number; size?: string | undefined; made_by: string | null; card?: CardView; page?: unknown }
interface Place { place: string; kind: string; saved: boolean; group?: string; name?: string; audience?: Audience; revision?: number; pins: Pin[] }
function server(me: Person) {
  let n = 0;
  const places: Record<string, Place> = {
    dashboard: {place: 'dashboard', kind: 'dashboard', saved: false, pins: defaults(['tasks_by_status', 'done_per_week', 'needs_you', 'latest_pages'])},
    'team:eng': {place: 'team:eng', kind: 'team', saved: false, group: 'eng', name: 'Engineering', audience: {everyone: true, people: [], teams: [], bots: []},
      pins: defaults(['tasks_by_status', 'done_per_week', 'needs_you'], 'eng')},
    'person:me': {place: 'person:' + me.id, kind: 'person', saved: false,
      pins: defaults(['needs_attention', 'unread_threads', 'my_tasks', 'updates', 'latest_pages'])},
  };
  const writes: [string, string, Body][] = [];
  const view = (p: string) => ({...places[p], can_edit: places[p]!.kind === 'person' || me.company_role !== 'member'});
  const save = (p: string) => {
    const at = places[p]!;
    if (!at.saved) {
      at.saved = true;
      at.pins = at.pins.map(pin => { const cid = 'c' + (++n); return {...pin, id: 'p' + n, item: 'card:' + cid, card: {...pin.card!, id: cid}}; });
    }
  };
  const pinPlace = (pid: string) => Object.keys(places).find(p => places[p]!.pins.some(x => x.id === pid));
  return {writes, places, handle(method: string, p: string, body: Body): [number, unknown] | null {
    if (method !== 'GET' && /^\/api\/v2\/(places|pins|cards|groups)\b/.test(p)) writes.push([method, p, body]);
    let m: RegExpMatchArray | null;
    // A team page's "Seen by" is its group's See and Read (backend/teammate_access.py).
    if ((m = p.match(/^\/api\/v2\/groups\/([^/]+)\/access$/))) {
      const at = places['team:' + decodeURIComponent(m[1]!)];
      if (!at || me.company_role === 'member') return [403, {error: {code: 'forbidden', detail: 'Only its leads'}}];
      if (method === 'PUT') { at.audience = body.read!; at.revision = (at.revision || 0) + 1; }
      const everyone = {everyone: true, people: [], teams: [], bots: []};
      return [200, {target: 'group:' + m[1], see: at.audience, read: at.audience, write: {everyone: false, people: [], teams: [m[1]!], bots: []},
        default: {see: everyone, read: everyone, write: {everyone: false, people: [], teams: [m[1]!], bots: []}}, revision: at.revision || 0,
        you: {see: true, read: true, write: true, manage: true}, teams: [{id: 'eng', name: 'Engineering'}]}];
    }
    if ((m = p.match(/^\/api\/v2\/places\/([^/]+)(\/order)?$/))) {
      const place = decodeURIComponent(m[1]!);
      if (!places[place]) return [404, {error: {code: 'not_found', detail: 'No such place'}}];
      if (method === 'PUT') { save(place); if (body.audience) places[place]!.audience = body.audience; }
      if (m[2]) { const pins = places[place]!.pins; places[place]!.pins = body.ids!.map((id: string) => pins.find(x => x.id === id)!); }
      return [200, view(place)];
    }
    if (p === '/api/v2/cards/choices') return [200, CHOICES];
    if (p === '/api/v2/cards' && method === 'POST') {
      save(body.place!);
      const cid = 'c' + (++n), made = card(cid, body.template!, {team: '', ...body.settings}, body.title);
      places[body.place!]!.pins.push({id: 'p' + (++n), item: 'card:' + cid, position: places[body.place!]!.pins.length, size: body.size, made_by: 'human:ana', card: made});
      return [200, {card: made, pin: 'p' + n}];
    }
    if (p === '/api/v2/pins' && method === 'POST') {
      const page = CHOICES.pages.find(x => 'page:' + x.file_id === body.item);
      places[body.place!]!.pins.push({id: 'p' + (++n), item: body.item, position: places[body.place!]!.pins.length, size: body.size, made_by: 'human:ana', page});
      return [200, {id: 'p' + n, ...view(body.place!)}];
    }
    if ((m = p.match(/^\/api\/v2\/pins\/([^/]+)$/))) {
      const place = pinPlace(m[1]!)!, pins = places[place]!.pins;
      if (method === 'DELETE') { places[place]!.pins = pins.filter(x => x.id !== m![1]); return [200, {removed: m[1]}]; }
      Object.assign(pins.find(x => x.id === m![1])!, body);
      return [200, view(place)];
    }
    if ((m = p.match(/^\/api\/v2\/cards\/([^/]+)$/)) && method === 'PATCH') {
      const pin = Object.values(places).flatMap(x => x.pins).find(x => x.card?.id === m![1])!;
      pin.card = card(m![1]!, pin.card!.template, {...pin.card!.settings, ...body.settings}, body.title ?? pin.card!.title);
      return [200, {card: pin.card}];
    }
    return null;
  }};
}

async function open(browser: Browser, {viewport = DESKTOP, hash = '#/overview', me = OWNER, dashboard = undefined as Pin[] | undefined} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [], fake = server(me);
  if (dashboard) fake.places.dashboard!.pins = dashboard;
  page.on('pageerror', (e: Error) => errors.push(e.message));
  const config = {company_name: 'Acme'};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json([{id: 'eng', name: 'Engineering', parent: '', people: [], bots: ['eng-lead', 'builder']}]);
    const answer = fake.handle(method, p, req.postData() ? JSON.parse(req.postData()!) : {});
    if (answer) return json(answer[1], answer[0]);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, fake};
}
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const headings = (page: Page) => page.locator('#dash-grid .dash-pin-head h2').allInnerTexts();
// How many of `sel` are on screen; the edit controls are in the page while viewing, just not shown.
const shown = (page: Page, sel: string) => page.locator(sel).evaluateAll(els => els.filter(el => el.checkVisibility()).length);
const EDIT_CONTROLS = '[data-dash-grip], [data-dash-menu], [data-dash-add], [data-dash-mode="done"]';
// Every control on the dashboard shown now that is under 40 px tall: on a phone each is the only way to what it does.
const smallTaps = (page: Page) => page.locator('#dash-root :is(a[href], button, [role="button"], [role="menuitem"], [role="menuitemradio"])')
  .evaluateAll(els => els.filter(el => el.checkVisibility() && el.getBoundingClientRect().height < 40)
    .map(el => `${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)}: ${Math.round(el.getBoundingClientRect().height)} px`));
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors, fake} = await open(browser);
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.deepEqual(await headings(page), ['Tasks by status', 'Done per week', 'Needs you', 'Latest pages'], 'the default cards; each heading its own text, the count beside it');
      assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Dashboard');
      assert.equal(await page.locator('#main h1').first().innerText(), 'Company', 'the page is Company');
      assert.equal(await page.locator('#side a[data-nav="overview"].cur').count(), 1, 'Company is current');
      assert.equal(await page.locator('.dash-stat strong').first().innerText(), '4');
      assert.equal(await page.locator('#dash-grid a[href="#/task/t1"]').count(), 1, 'Needs you links its top items');
      assert.equal(await page.locator('#dash-grid a[href="#/page/f1"]').count(), 1, 'Latest pages links each page');
      await shot(page, 'desktop-dashboard');

      // Viewing shows only Edit; Edit shows the grips, the menus, + Card, + Page and Done.
      assert.equal(await shown(page, EDIT_CONTROLS), 0, 'nothing to arrange until Edit');
      assert.equal(await shown(page, '[data-dash-mode="edit"]'), 1);
      await page.locator('[data-dash-mode="edit"]').click();
      assert.equal(await shown(page, '[data-dash-grip]'), 4);
      assert.equal(await shown(page, '[data-dash-menu]'), 4);
      assert.equal(await shown(page, '[data-dash-add]'), 2, '+ Card and + Page');
      assert.equal(await shown(page, '[data-dash-mode="edit"]'), 0);
      assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.dashMode), 'done', 'focus goes to Done');
      await shot(page, 'desktop-dashboard-editing');

      // The first change saves the defaults, then sends the order by pin ids.
      await page.locator('#dash-grid .dash-pin').nth(0).locator('[data-dash-grip]').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('#dash-grid .dash-pin h2')?.textContent.startsWith('Done'));
      assert.deepEqual(fake.writes.map(([m, p]) => m + ' ' + p), ['PUT /api/v2/places/dashboard', 'POST /api/v2/places/dashboard/order']);
      assert.deepEqual(fake.writes[1]![2].ids, ['p2', 'p1', 'p3', 'p4']);
      assert.equal(await page.evaluate(() => document.activeElement?.closest<HTMLElement>('.dash-pin')?.dataset.at), '1', 'focus follows the card');

      // A pointer drag: Latest pages onto the first card.
      const from = (await page.locator('#dash-grid .dash-pin').nth(3).locator('[data-dash-grip]').boundingBox())!;
      const to = (await page.locator('#dash-grid .dash-pin').nth(0).boundingBox())!;
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(to.x + 20, to.y + 20, {steps: 8});
      await page.mouse.up();
      await page.waitForFunction(() => document.querySelector('#dash-grid .dash-pin h2')?.textContent.startsWith('Latest'));
      assert.deepEqual(fake.writes.at(-1)![2].ids, ['p4', 'p2', 'p1', 'p3']);

      // Add a card from its form: Done per week over 12 weeks, large.
      await page.locator('[data-dash-add="card"]').click();
      const dlg = page.locator('#dash-dialog[open]');
      await dlg.locator('[name="template"]').selectOption('done_per_week');
      await dlg.locator('[name="weeks"]').fill('12');
      await dlg.locator('[name="size"]').selectOption('large');
      await dlg.locator('[type="submit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).waitFor();
      assert.deepEqual(fake.writes.at(-1)![2], {title: '', settings: {team: '', weeks: 12}, template: 'done_per_week', place: 'dashboard', size: 'large'});
      assert.equal(await page.locator('#dash-grid .dash-pin').nth(4).getAttribute('data-size'), 'large');
      assert.equal(await page.locator('#dash-grid .dash-pin').nth(4).locator('.dash-bars rect').count(), 12);
      assert.equal(await page.locator('#dash-dialog[open]').count(), 0, 'the form closes');

      // Its menu: Edit names it, a size, Remove.
      const added = page.locator('#dash-grid .dash-pin').nth(4);
      await added.locator('[data-dash-menu]').click();
      await added.locator('.dash-menu [data-dash-edit]').click();
      await page.locator('#dash-dialog[open] [name="title"]').fill('Quarter');
      await page.locator('#dash-dialog[open] [type="submit"]').click();
      await page.waitForFunction(() => document.querySelectorAll('#dash-grid .dash-pin h2')[4]?.textContent === 'Quarter');
      await added.locator('[data-dash-menu]').click();
      await added.locator('.dash-menu [data-dash-size="small"]').click();
      await page.waitForFunction(() => document.querySelectorAll<HTMLElement>('#dash-grid .dash-pin')[4]?.dataset.size === 'small');
      await page.locator('#dash-grid .dash-pin').nth(4).locator('[data-dash-menu]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).locator('.dash-menu [data-dash-remove]').click();
      await page.waitForFunction(() => document.querySelectorAll('#dash-grid .dash-pin').length === 4);

      // Add page: the pin goes in, and the page shows (pageCard, or a link to it).
      await page.locator('[data-dash-add="page"]').click();
      await page.locator('#dash-dialog[open] [type="submit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).waitFor();
      assert.deepEqual(fake.writes.at(-1)!.slice(1), ['/api/v2/pins', {place: 'dashboard', item: 'page:f2', size: 'medium'}]);
      assert.match(await page.locator('#dash-grid .dash-pin').nth(4).innerText(), /Acme launch plan/);
      await shot(page, 'desktop-dashboard-edited');
      // Every write drew the grid again and kept edit mode; Done ends it.
      assert.equal(await shown(page, '[data-dash-grip]'), 5);
      await page.locator('[data-dash-mode="done"]').click();
      assert.equal(await shown(page, EDIT_CONTROLS), 0, 'Done hides them again');
      assert.equal(await shown(page, '[data-dash-mode="edit"]'), 1);
      // Escape leaves edit mode, but first closes an open menu.
      await page.locator('[data-dash-mode="edit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(0).locator('[data-dash-menu]').click();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.dash-menu').count(), 0, 'the menu closes');
      assert.equal(await shown(page, '[data-dash-grip]'), 5, 'still editing');
      await page.keyboard.press('Escape');
      assert.equal(await shown(page, EDIT_CONTROLS), 0, 'Escape leaves edit mode');
      // Leaving the page ends it too.
      await page.locator('[data-dash-mode="edit"]').click();

      // Campus: the 3D view as it was, Overview still current.
      await page.locator('.dash-tabs a', {hasText: 'Campus'}).click();
      await page.locator('#overview-frame').waitFor({state: 'attached'});
      assert.equal(await page.evaluate(() => location.hash), '#/overview/campus');
      assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Campus');
      assert.equal(await page.locator('#side a[data-nav="overview"].cur').count(), 1);

      // A team opens from its name in the sidebar; its arrow still folds it.
      const label = page.locator('#tree a.dept-label', {hasText: /^Engineering$/});
      await label.click();
      await page.waitForFunction(() => location.hash === '#/team/eng');
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.equal(await page.locator('.dash-head h1').innerText(), 'Engineering');
      assert.deepEqual(await headings(page), ['Tasks by status', 'Done per week', 'Needs you'], 'its own team goes unsaid');
      assert.match(await page.locator('[data-dash-audience]').innerText(), /Seen by Everyone$/);
      assert.equal(await shown(page, EDIT_CONTROLS), 0, 'a new page opens viewing');
      assert.equal(await shown(page, '[data-dash-mode="edit"]'), 1, 'its leads arrange it with Edit');
      assert.equal(await page.locator('#tree a.dept-label.cur').count(), 1);
      const kids = page.locator('#tree li.org-group:has(> .noderow a.dept-label.cur) > ul');
      const wasOpen = await kids.isVisible();
      await page.locator('#tree li.org-group:has(> .noderow a.dept-label.cur) > .noderow .chev').click();
      assert.equal(await kids.isVisible(), !wasOpen, 'the arrow folds the group');
      assert.equal(await page.evaluate(() => location.hash), '#/team/eng', 'and does not open the page');
      // Its name unfolds a folded group.
      if (!(await kids.isVisible())) { await label.click(); await kids.waitFor(); }
      // Its audience is the group's See and Read: "Seen by" opens the group's Access editor, narrowed to chosen people.
      await page.locator('[data-dash-audience]').click();
      await page.locator('#access-editor[open] [name="preset"][value="custom"]').check();
      for (const level of ['see', 'read']) {
        const box = page.locator(`#access-editor[open] [data-access-levels="${level}"]`);
        await box.locator('[data-access-everyone]').uncheck();
        await box.locator('[data-access-pick="people"][value="ana"]').check();
      }
      await page.locator('#access-editor[open] [type="submit"]').click();
      await page.waitForFunction(() => document.querySelector('[data-dash-audience]')?.textContent.includes('Seen by Ana Silva'));
      const chosen = {everyone: false, people: ['ana'], teams: [], bots: []};
      const [method, url, body] = fake.writes.at(-1)!;
      assert.deepEqual([method, url, body.see, body.read, body.write!.teams, body.revision],
        ['PUT', '/api/v2/groups/eng/access', chosen, chosen, ['eng'], 0]);
      await shot(page, 'desktop-team');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const viewport of [DESKTOP, PHONE]) {
      // The app opens on My dashboard: the reader's own cards, Mine current, Company one tab away.
      const {page, errors} = await open(browser, {viewport, hash: '', me: MEMBER});
      await page.waitForFunction(() => location.hash === '#/home');
      await page.locator('#dash-grid .dash-pin').nth(4).waitFor();
      assert.deepEqual(await headings(page), ['Needs attention', 'Unread threads', 'My tasks', 'Updates', 'Latest pages']);
      assert.equal(await page.locator('.dash-tabs a.cur').getAttribute('href'), '#/home');
      assert.equal(await page.locator('.dash-actions').isVisible(), true, 'a member arranges their own');
      assert.equal(await shown(page, EDIT_CONTROLS), 0);
      if (viewport === PHONE) assert.deepEqual(await smallTaps(page), [], 'every Home control is at least 40 px tall on a phone');
      await page.locator('[data-dash-mode="edit"]').click();
      assert.equal(await shown(page, '[data-dash-grip]'), 5);
      assert.equal(await sideways(page), false, 'edit mode fits');
      if (viewport === PHONE) {
        assert.deepEqual(await smallTaps(page), [], 'every Home edit control is at least 40 px tall on a phone');
        await page.locator('#dash-grid .dash-pin').first().locator('[data-dash-menu]').click();
        await page.locator('.dash-menu [data-dash-remove]').waitFor();
        assert.deepEqual(await smallTaps(page), [], 'every card menu item is at least 40 px tall on a phone');
        assert.equal(await sideways(page), false, 'the card menu fits');
        await page.keyboard.press('Escape');
        await page.locator('.dash-menu').waitFor({state: 'detached'});
      }
      await shot(page, (viewport === PHONE ? 'phone' : 'desktop') + '-my-dashboard-editing');
      await page.locator('[data-dash-mode="done"]').click();
      assert.equal(await page.locator('#dash-grid a[href="#/task/t7"]').count(), 1, 'My tasks links each task');
      assert.equal(await page.locator('#dash-grid a[href="#/task/t1"]').count(), 1, 'Needs attention links its top items');
      assert.equal(await sideways(page), false);
      await shot(page, (viewport === PHONE ? 'phone' : 'desktop') + '-my-dashboard');
      await page.locator('.dash-tabs a[href="#/overview"]').click();
      await page.waitForFunction(() => location.hash === '#/overview');
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member reads the cards and has nothing to arrange.
      const {page, errors, fake} = await open(browser, {me: MEMBER});
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.equal(await page.locator('.dash-actions').isVisible(), false);
      assert.equal(await page.locator('[data-dash-grip], [data-dash-menu]').count(), 0);
      assert.equal(await shown(page, '[data-dash-mode]'), 0, 'no Edit');
      assert.deepEqual(fake.writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A phone: one column, no sideways scroll, the menu in reach.
      const {page, errors} = await open(browser, {viewport: PHONE});
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      const widths = await page.locator('#dash-grid .dash-pin').evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().width)));
      assert.equal(new Set(widths).size, 1, 'one column');
      assert.ok(widths[0]! >= 340, 'the full width');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      const actionHeights = await page.locator('.dash-list a').evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().height)));
      assert.ok(actionHeights.length > 0 && actionHeights.every(height => height >= 40), 'dashboard task and question links are at least 40 px tall');
      await shot(page, 'phone-dashboard');
      await page.locator('[data-dash-mode="edit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(1).locator('[data-dash-menu]').click();
      assert.equal(await sideways(page), false, 'the menu stays on screen');
      await shot(page, 'phone-dashboard-menu');
      await page.evaluate(() => { location.hash = '#/team/eng'; });
      await page.waitForFunction(() => document.querySelector('.dash-head h1')?.textContent === 'Engineering');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-team');
      await page.evaluate(() => { location.hash = '#/overview/campus'; });
      await page.locator('.overview-tabs .dash-tabs').waitFor();
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-campus');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Tasks by status shows only what the server counts: Open and Done this week, never a dash, on Dashboard and a team page.
    for (const viewport of [LAPTOP, PHONE]) {
      const {page, errors} = await open(browser, {viewport});
      for (const [hash, h1] of [['#/overview', 'Company'], ['#/team/eng', 'Engineering']] as const) {
        await page.evaluate(to => { location.hash = to; }, hash);
        await page.waitForFunction(want => document.querySelector('#main h1')?.textContent === want, h1);
        const statsGrid = page.locator('#dash-grid .dash-pin').filter({has: page.locator('h2', {hasText: 'Tasks by status'})}).locator('.dash-stats');
        const stats = statsGrid.locator('.dash-stat');
        await stats.first().waitFor();
        const where = `${hash} at ${viewport.width}`;
        assert.deepEqual(await stats.locator('span').allInnerTexts(), ['Open', 'Done this week'], 'no Doing or Waiting column, ' + where);
        assert.deepEqual(await stats.locator('strong').allInnerTexts(), ['4', '7'], 'no dash, ' + where);
        const [grid, ...cells] = await statsGrid.evaluate(el => [el, ...el.children].map(c => c.getBoundingClientRect().width));
        assert.ok(cells.every(w => w > grid! * 0.4), `the two numbers share the card's width, not half of it, ${where}: ${cells} of ${grid}`);
        assert.equal(await sideways(page), false, 'no sideways scroll, ' + where);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Needs attention: each row by its task's title with the word for what it needs at the right, the ask as its tooltip;
    // a Paused cause and a mention outside a task by their own line. The count sits on the header line (backend/cards.py needs_brief).
    {
      const done = 'Say when the work is done', was = DATA.needs_attention!;
      const rows = [
        {id: 'n1', kind: 'task', title: 'Fix the invoice export', line: done, act: 'done', task_id: 'n1'},
        {id: 'n2', kind: 'task', title: 'Ship the pricing page', line: done, act: 'done', task_id: 'n2'},
        {id: 'n3', kind: 'ask', title: 'Send an email', line: 'Send this email?', act: 'ok', task_id: 'n3'},
        {id: 'n4', kind: 'ask', title: 'Pick a launch date', line: 'Which date works?', act: 'answer', task_id: 'n4'},
        {id: 'n5', kind: 'mention', title: 'Ship the pricing page', line: '@ana can you look?', act: 'reply', task_id: 'n2'},
      ];
      DATA.needs_attention = () => ({count: 13, items: rows});
      for (const viewport of [DESKTOP, PHONE]) {
        const {page, errors} = await open(browser, {viewport, hash: '#/home', me: MEMBER});
        const pin = page.locator('#dash-grid .dash-pin').filter({has: page.locator('h2', {hasText: 'Needs attention'})});
        await pin.locator('.dash-needs li').nth(4).waitFor();
        const where = 'at ' + viewport.width;
        assert.deepEqual(await pin.locator('.dash-needs-t').allInnerTexts(), rows.map(r => r.title), 'titles, not the ask, ' + where);
        assert.deepEqual(await pin.locator('.dash-needs li > a > .muted').allInnerTexts(), ['Done', 'Done', 'OK', 'Answer', 'Reply'], 'the words, ' + where);
        assert.deepEqual(await pin.locator('.dash-needs li > a').evaluateAll(els => els.map(a => a.getAttribute('title'))), rows.map(r => r.line), 'the ask is the tooltip');
        assert.equal(await pin.locator('.dash-needs li > a').first().getAttribute('href'), '#/task/n1', 'the word is inside the link to the task');
        // A screen reader hears the title and the word apart, not run together.
        assert.equal(await pin.getByRole('link', {name: 'Fix the invoice export Done', exact: true}).count(), 1, 'the link reads its title, then its word');
        // The spec's measure: no row's first line repeats another's while their tasks' titles differ.
        const firsts = await pin.locator('.dash-needs-t').allInnerTexts(), byTitle = rows.map(r => r.title);
        assert.equal(firsts.filter((t, i) => firsts.indexOf(t) !== i && byTitle.indexOf(byTitle[i]!) === i).length, 0, 'no repeated first line unless the titles repeat');
        assert.equal(await pin.locator('.dash-big').count(), 0, 'no big figure');
        assert.equal((await pin.locator('h2').innerText()).trim(), 'Needs attention', 'the heading keeps its own text');
        const n = pin.locator('.dash-pin-head > .dash-head-n');
        assert.equal(await n.innerText(), '13', 'the count on the header line');
        assert.equal(await n.getAttribute('href'), '#/tasks?view=foryou', 'the count opens Tasks > You');
        assert.equal(await n.evaluate(el => getComputedStyle(el).fontSize), '13px', 'the count at 13px');
        assert.equal(await n.evaluate(el => getComputedStyle(el).color), await pin.locator('.dash-needs li > a > .muted').first().evaluate(el => getComputedStyle(el).color), 'the count muted');
        const words = await pin.locator('.dash-needs li > a > .muted').evaluateAll(els => els.map(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.right <= window.innerWidth; }));
        assert.ok(words.every(Boolean), 'every word stays on screen, ' + where);
        assert.equal(await sideways(page), false, 'nothing scrolls sideways, ' + where);
        await shot(page, (viewport === PHONE ? 'phone' : 'desktop') + '-needs-attention');
        assert.deepEqual(errors, []);
        await page.close();
      }
      // At 0 the header shows no count and the body says so.
      DATA.needs_attention = () => ({count: 0, items: []});
      const {page, errors, fake} = await open(browser, {hash: '#/home', me: MEMBER});
      const pin = page.locator('#dash-grid .dash-pin').filter({has: page.locator('h2', {hasText: 'Needs attention'})});
      await pin.locator('.dash-none').waitFor();
      assert.equal(await pin.locator('.dash-none').innerText(), 'Nothing waiting on you');
      assert.equal(await pin.locator('.dash-none a').getAttribute('href'), '#/tasks?view=foryou', 'the empty card still leads to Tasks > You');
      assert.equal(await pin.locator('.dash-pin-head > .dash-head-n').isVisible(), false, 'no count at 0');
      assert.equal((await pin.locator('h2').innerText()).trim(), 'Needs attention');
      // A team's Needs you card on another page: the team in the heading, the count beside it.
      fake.places.dashboard!.pins = defaults(['needs_you'], 'eng');
      await page.evaluate(() => { location.hash = '#/overview'; });
      await page.waitForFunction(() => document.querySelector('#dash-grid .dash-pin-head h2')?.textContent.includes('Engineering'));
      assert.equal((await page.locator('#dash-grid .dash-pin-head h2').innerText()).replace(/\s+/g, ' ').trim(), 'Needs you · Engineering');
      assert.equal(await page.locator('#dash-grid .dash-pin-head > .dash-head-n').innerText(), '2');
      assert.deepEqual(errors, []);
      await page.close();
      DATA.needs_attention = was;
    }
    // Count cards carry their count on the header line, 13px and muted, beside (not in) the heading; no card shows a
    // figure above 16px (#355). Hidden at 0, with the empty card saying so.
    {
      const LONG = 'Tasks for the Acme renewal and the onboarding checklist review';
      const pin = (template: string, data: unknown, title = '', size = 'medium', team = ''): Pin => ({id: null, item: null, position: 0, size, made_by: null,
        card: {...card(null, 'tasks_by_status', {team}, title), template, name: NAMES[template]!, heading: title || NAMES[template]!, data: data as CardView['data']}});
      const tasks = (n: number) => Array.from({length: n}, (_, i) => ({id: 'm' + i, title: 'Task ' + i, status: 'open', due_at: null, updated_at: ago(i)}));
      const dashboard = [
        pin('my_tasks', {count: 8, tasks: tasks(3)}),
        pin('updates', {unread: 56, updates: (DATA.updates!() as {updates: unknown[]}).updates}),
        pin('automation_runs', {total: 42, failed: 3, running: 1, runs: [{name: 'Nightly digest', status: 'failed', started_at: ago(30)}]}),
        pin('automation_runs', {total: 9, failed: 0, running: 0, runs: [{name: 'Weekly report', status: 'succeeded', started_at: ago(90)}]}, 'Quiet runs'),
        pin('updates', {unread: 0, updates: []}, 'Team updates'),
        pin('needs_attention', {count: 0, items: []}),
        pin('spend', {spent: 1240, budget: 2000, month: 'Nov'}),
        pin('kpi', {value: 210, unit: 'users', name: 'Active users', at: '2026-11-12', spark: [1, 3, 2, 5]}),
        pin('tasks_by_status', {counts: {open: 4, done: 7}}),
        pin('done_per_week', {weeks: weeks(8)}),
        pin('my_tasks', {count: 8, tasks: tasks(1)}, LONG, 'small', 'eng'),
      ];
      for (const viewport of [DESKTOP, PHONE]) {
        const {page, errors} = await open(browser, {viewport, dashboard});
        const where = `at ${viewport.width}`;
        await page.locator('#dash-grid .dash-pin').nth(dashboard.length - 1).waitFor();
        const card = (name: string) => page.locator('#dash-grid .dash-pin').filter({has: page.locator('h2', {hasText: new RegExp('^' + name)})}).first();
        const count = (name: string) => card(name).locator('.dash-pin-head > .dash-head-n');
        for (const [name, n, href, label] of [['My tasks', '8', '#/tasks?view=list', '8 open'], ['Updates', '56', '#/updates', '56 unread'],
          ['Automation runs', '42', '#/automations', '42 runs in 24 hours']] as const) {
          assert.equal(await card(name).locator('h2').innerText(), name, `the heading keeps its own text, ${name} ${where}`);
          assert.equal(await count(name).innerText(), n, `${name}'s count is on its header line ${where}`);
          assert.equal(await count(name).getAttribute('href'), href, `${name}'s count opens where its figure did ${where}`);
          assert.equal(await count(name).getAttribute('aria-label'), label);
          assert.equal(await count(name).getAttribute('title'), label);
          assert.equal(await count(name).evaluate(el => getComputedStyle(el).fontSize), '13px');
          assert.equal(await card(name).locator('.dash-big').count(), 0, `${name} has no big figure ${where}`);
        }
        assert.equal(await card('Automation runs').locator('.dash-sub').innerText(), '3 failed · 1 running');
        assert.equal(await card('Automation runs').locator('.dash-sub .dash-bad').innerText(), '3 failed');
        assert.equal(await card('Quiet runs').locator('.dash-sub').count(), 0, 'no line with nothing failed or running');
        assert.equal(await count('Team updates').isVisible(), false, 'a count of 0 is hidden');
        assert.equal(await card('Team updates').locator('.dash-pin-body').innerText(), 'No updates');
        assert.equal(await count('Needs attention').isVisible(), false);
        assert.equal(await card('Needs attention').locator('.dash-pin-body').innerText(), 'Nothing waiting on you');
        // Totals are 16px/600 beside their label; Tasks by status has no filled boxes.
        for (const name of ['Spend', 'KPI', 'Done per week']) {
          assert.deepEqual(await card(name).locator('.dash-big strong').evaluate(el => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight]), ['16px', '600'], `${name} ${where}`);
        }
        const stat = card('Tasks by status').locator('.dash-stat').first();
        assert.deepEqual(await stat.locator('strong').evaluate(el => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight]), ['16px', '600']);
        assert.equal(await stat.evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)', 'no grey box');
        // A long name in a small pin, with its team, never cuts the count.
        const longCount = count(LONG);
        const [head, n] = await Promise.all([card(LONG).locator('.dash-pin-head').boundingBox(), longCount.boundingBox()]);
        assert.ok(n && head && n.x + n.width <= head.x + head.width + 0.5, `the count is inside the header ${where}`);
        assert.equal(await longCount.evaluate(el => el.scrollWidth <= el.clientWidth), true, `the count is shown whole ${where}`);
        if (viewport === PHONE) assert.ok(n!.height >= 40, `the count is a finger-sized target on a phone: ${n!.height}px`);
        // Nothing in the main area above 16px, icon glyphs aside.
        assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#main *')].filter(el => el instanceof HTMLElement && el.checkVisibility()
          && [...el.childNodes].some(c => c.nodeType === 3 && c.textContent!.trim()) && parseFloat(getComputedStyle(el).fontSize) > 16
          && !/Material Symbols/i.test(getComputedStyle(el).fontFamily)).map(el => el.className + ': ' + el.textContent!.trim().slice(0, 30))), [], where);
        await shot(page, `counts-${viewport.width}`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    console.log('Dashboard: My dashboard landing, defaults, Campus tab, edit mode (Edit, Done, Escape), order by keys and drag, add/edit/size/remove a card, pin a page, team page from the sidebar, audience, member, phone, Tasks by status columns, Needs attention rows by title with the count on the header line, header counts and no billboard figures');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
