// Overview › Dashboard and team pages (ui/app/dashboard.js, backend/cards.py) against a fake server shaped as
// GET/PUT /api/v2/places/{place}, POST /places/{place}/order, /api/v2/pins and /api/v2/cards answer: the Dashboard
// opens with its default cards and the Campus tab beside it; the first change saves the defaults, then the order goes
// by pin ids (keys and a pointer drag); a card is added from its form, sized from its menu and removed; a page is
// pinned from Add page; a team opens from its name in the sidebar while its arrow still folds it; a member sees the
// cards and no way to arrange them; a phone has one column and no sideways scroll.
// TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const OWNER = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const MEMBER = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true};
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const NAMES = {tasks_by_status: 'Tasks by status', done_per_week: 'Done per week', needs_you: 'Needs you', latest_pages: 'Latest pages',
  spend: 'Spend', kpi: 'KPI', automation_runs: 'Automation runs'};
const weeks = n => Array.from({length: n}, (_, i) => ({week: `2026-${String(8 + Math.floor(i / 4)).padStart(2, '0')}-${String(1 + i % 4 * 7).padStart(2, '0')}`, done: [3, 5, 2, 8, 6, 4, 9, 7][i % 8]}));
const DATA = {
  tasks_by_status: () => ({counts: {open: 4, doing: 2, waiting: 1, done: 7}}),
  done_per_week: s => ({weeks: weeks(s.weeks || 8)}),
  needs_you: () => ({count: 2, items: [{id: 't1', kind: 'task', title: 'Approve the Acme launch plan', task_id: 't1'},
    {id: 'q1', kind: 'question', title: 'Pick a pricing page layout', task_id: 'q1'}]}),
  latest_pages: () => ({pages: [{file_id: 'f1', title: 'Weekly numbers', bot: 'eng-lead', updated_at: ago(90), archived: false}]}),
};
const TEAMS = {eng: 'Engineering'};
const card = (id, template, settings, title = '') => ({id, template, title, settings, name: NAMES[template], heading: title || NAMES[template],
  team_name: TEAMS[settings.team] || '', hidden: false, data: DATA[template](settings), made_by: id ? 'human:ana' : null});
const defaults = (templates, team = '') => templates.map((t, i) => ({id: null, item: null, position: i, size: 'medium', made_by: null,
  card: card(null, t, {team, ...(t === 'done_per_week' ? {weeks: 8} : {})})}));
const CHOICES = {templates: Object.entries(NAMES).map(([id, name]) => ({id, name, fields: id === 'done_per_week' ? ['team', 'weeks'] : id === 'kpi' ? ['kpi'] : id === 'latest_pages' ? ['team', 'count'] : ['team']})),
  teams: [{id: 'eng', name: 'Engineering', parent: null}], kpis: [],
  pages: [{file_id: 'f2', title: 'Acme launch plan', bot: 'eng-lead', updated_at: ago(30), archived: false}]};

function server(me) {
  let n = 0;
  const places = {
    dashboard: {place: 'dashboard', kind: 'dashboard', saved: false, pins: defaults(['tasks_by_status', 'done_per_week', 'needs_you', 'latest_pages'])},
    'team:eng': {place: 'team:eng', kind: 'team', saved: false, group: 'eng', name: 'Engineering', audience: {everyone: true, people: [], teams: [], bots: []},
      pins: defaults(['tasks_by_status', 'done_per_week', 'needs_you'], 'eng')},
  };
  const writes = [];
  const view = p => ({...places[p], can_edit: me.company_role !== 'member'});
  const save = p => {
    const at = places[p];
    if (!at.saved) {
      at.saved = true;
      at.pins = at.pins.map(pin => { const cid = 'c' + (++n); return {...pin, id: 'p' + n, item: 'card:' + cid, card: {...pin.card, id: cid}}; });
    }
  };
  const pinPlace = pid => Object.keys(places).find(p => places[p].pins.some(x => x.id === pid));
  return {writes, places, handle(method, p, body) {
    if (method !== 'GET' && /^\/api\/v2\/(places|pins|cards)\b/.test(p)) writes.push([method, p, body]);
    let m;
    if ((m = p.match(/^\/api\/v2\/places\/([^/]+)(\/order)?$/))) {
      const place = decodeURIComponent(m[1]);
      if (!places[place]) return [404, {error: {code: 'not_found', detail: 'No such place'}}];
      if (method === 'PUT') { save(place); if (body.audience) places[place].audience = body.audience; }
      if (m[2]) { const pins = places[place].pins; places[place].pins = body.ids.map(id => pins.find(x => x.id === id)); }
      return [200, view(place)];
    }
    if (p === '/api/v2/cards/choices') return [200, CHOICES];
    if (p === '/api/v2/cards' && method === 'POST') {
      save(body.place);
      const cid = 'c' + (++n), made = card(cid, body.template, {team: '', ...body.settings}, body.title);
      places[body.place].pins.push({id: 'p' + (++n), item: 'card:' + cid, position: places[body.place].pins.length, size: body.size, made_by: 'human:ana', card: made});
      return [200, {card: made, pin: 'p' + n}];
    }
    if (p === '/api/v2/pins' && method === 'POST') {
      const page = CHOICES.pages.find(x => 'page:' + x.file_id === body.item);
      places[body.place].pins.push({id: 'p' + (++n), item: body.item, position: places[body.place].pins.length, size: body.size, made_by: 'human:ana', page});
      return [200, {id: 'p' + n, ...view(body.place)}];
    }
    if ((m = p.match(/^\/api\/v2\/pins\/([^/]+)$/))) {
      const place = pinPlace(m[1]), pins = places[place].pins;
      if (method === 'DELETE') { places[place].pins = pins.filter(x => x.id !== m[1]); return [200, {removed: m[1]}]; }
      Object.assign(pins.find(x => x.id === m[1]), body);
      return [200, view(place)];
    }
    if ((m = p.match(/^\/api\/v2\/cards\/([^/]+)$/)) && method === 'PATCH') {
      const pin = Object.values(places).flatMap(x => x.pins).find(x => x.card?.id === m[1]);
      pin.card = card(m[1], pin.card.template, {...pin.card.settings, ...body.settings}, body.title ?? pin.card.title);
      return [200, {card: pin.card}];
    }
    return null;
  }};
}

async function open(browser, {viewport = DESKTOP, hash = '#/overview', me = OWNER} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [], fake = server(me);
  page.on('pageerror', e => errors.push(e.message));
  const config = {company_name: 'Acme'};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json([{id: 'eng', name: 'Engineering', parent: '', people: [], bots: ['eng-lead', 'builder']}]);
    const answer = fake.handle(method, p, req.postData() ? JSON.parse(req.postData()) : {});
    if (answer) return json(answer[1], answer[0]);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, fake};
}
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const headings = page => page.locator('#dash-grid .dash-pin-head h2').allInnerTexts();
async function shot(page, name) {
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
      assert.deepEqual(await headings(page), ['Tasks by status', 'Done per week', 'Needs you', 'Latest pages'], 'the default cards');
      assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Dashboard');
      assert.equal(await page.locator('#side a[data-nav="overview"].cur').count(), 1, 'Overview is current');
      assert.equal(await page.locator('.dash-stat strong').first().innerText(), '4');
      assert.equal(await page.locator('#dash-grid a[href="#/task/t1"]').count(), 1, 'Needs you links its top items');
      assert.equal(await page.locator('#dash-grid a[href="#/page/f1"]').count(), 1, 'Latest pages links each page');
      await shot(page, 'desktop-dashboard');

      // The first change saves the defaults, then sends the order by pin ids.
      await page.locator('#dash-grid .dash-pin').nth(0).locator('[data-dash-grip]').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('#dash-grid .dash-pin h2')?.textContent.startsWith('Done'));
      assert.deepEqual(fake.writes.map(([m, p]) => m + ' ' + p), ['PUT /api/v2/places/dashboard', 'POST /api/v2/places/dashboard/order']);
      assert.deepEqual(fake.writes[1][2].ids, ['p2', 'p1', 'p3', 'p4']);
      assert.equal(await page.evaluate(() => document.activeElement?.closest('.dash-pin')?.dataset.at), '1', 'focus follows the card');

      // A pointer drag: Latest pages onto the first card.
      const from = await page.locator('#dash-grid .dash-pin').nth(3).locator('[data-dash-grip]').boundingBox();
      const to = await page.locator('#dash-grid .dash-pin').nth(0).boundingBox();
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(to.x + 20, to.y + 20, {steps: 8});
      await page.mouse.up();
      await page.waitForFunction(() => document.querySelector('#dash-grid .dash-pin h2')?.textContent.startsWith('Latest'));
      assert.deepEqual(fake.writes.at(-1)[2].ids, ['p4', 'p2', 'p1', 'p3']);

      // Add a card from its form: Done per week over 12 weeks, large.
      await page.locator('[data-dash-add="card"]').click();
      const dlg = page.locator('#dash-dialog[open]');
      await dlg.locator('[name="template"]').selectOption('done_per_week');
      await dlg.locator('[name="weeks"]').fill('12');
      await dlg.locator('[name="size"]').selectOption('large');
      await dlg.locator('[type="submit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).waitFor();
      assert.deepEqual(fake.writes.at(-1)[2], {title: '', settings: {team: '', weeks: 12}, template: 'done_per_week', place: 'dashboard', size: 'large'});
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
      await page.waitForFunction(() => document.querySelectorAll('#dash-grid .dash-pin')[4]?.dataset.size === 'small');
      await page.locator('#dash-grid .dash-pin').nth(4).locator('[data-dash-menu]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).locator('.dash-menu [data-dash-remove]').click();
      await page.waitForFunction(() => document.querySelectorAll('#dash-grid .dash-pin').length === 4);

      // Add page: the pin goes in, and the page shows (pageCard, or a link to it).
      await page.locator('[data-dash-add="page"]').click();
      await page.locator('#dash-dialog[open] [type="submit"]').click();
      await page.locator('#dash-grid .dash-pin').nth(4).waitFor();
      assert.deepEqual(fake.writes.at(-1).slice(1), ['/api/v2/pins', {place: 'dashboard', item: 'page:f2', size: 'medium'}]);
      assert.match(await page.locator('#dash-grid .dash-pin').nth(4).innerText(), /Acme launch plan/);
      await shot(page, 'desktop-dashboard-edited');

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
      assert.equal(await page.locator('#tree a.dept-label.cur').count(), 1);
      const kids = page.locator('#tree li.org-group:has(> .noderow a.dept-label.cur) > ul');
      const wasOpen = await kids.isVisible();
      await page.locator('#tree li.org-group:has(> .noderow a.dept-label.cur) > .noderow .chev').click();
      assert.equal(await kids.isVisible(), !wasOpen, 'the arrow folds the group');
      assert.equal(await page.evaluate(() => location.hash), '#/team/eng', 'and does not open the page');
      // Its name unfolds a folded group.
      if (!(await kids.isVisible())) { await label.click(); await kids.waitFor(); }
      // Its audience: narrowed to chosen people.
      await page.locator('[data-dash-audience]').click();
      await page.locator('#dash-dialog[open] [name="everyone"]').uncheck();
      await page.locator('#dash-dialog[open] [name="people"][value="ana"]').check();
      await page.locator('#dash-dialog[open] [type="submit"]').click();
      await page.waitForFunction(() => document.querySelector('[data-dash-audience]')?.textContent.includes('Seen by Ana Silva'));
      assert.deepEqual(fake.writes.at(-1).slice(1), ['/api/v2/places/team%3Aeng', {audience: {everyone: false, teams: [], people: ['ana'], bots: []}}]);
      await shot(page, 'desktop-team');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member reads the cards and has nothing to arrange.
      const {page, errors, fake} = await open(browser, {me: MEMBER});
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.equal(await page.locator('.dash-actions').isVisible(), false);
      assert.equal(await page.locator('[data-dash-grip], [data-dash-menu]').count(), 0);
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
      assert.ok(widths[0] >= 340, 'the full width');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      await shot(page, 'phone-dashboard');
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
    console.log('Dashboard: defaults, Campus tab, order by keys and drag, add/edit/size/remove a card, pin a page, team page from the sidebar, audience, member, phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
