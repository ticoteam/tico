// Operations (ui/app/operations-*.js) and Settings > General > Sidebar against a fake hub: Acme (acme.example) with an owner
// (Ana, admin) and a member (Cara). Cases from the Operations test list:
// X2  turning Operations off takes it from the sidebar and ⌘K; the data and API answer as before; back on, same data.
// X5  a bookmark to a hidden section still opens it, and a contextual link still works.
// X5b an admin with Operations hidden sees one muted Hidden row naming it, which opens Settings > Sidebar; a member doesn't.
// X8  an archived location is hidden unless Show inactive; it comes back from its panel.
// X15 on a phone rows stack, the panel is a full-screen sheet, and nothing scrolls sideways.
// Also: + Add from photo makes one task with the photo attached; due dates add, complete, reopen; the CSV flow previews and
// commits; a teammate without the records right sees no write controls and a read-only Sidebar; "Your view" on a partial P&L.
// TICO_UI_SHOTS=<dir> saves screenshots (desktop and phone). Localhost so the page is a secure context: uploads hash with crypto.subtle.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const ORIGIN = 'http://localhost:48123', PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const SHOTS = process.env.TICO_UI_SHOTS || '';

const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}, {id: 'cara', name: 'Cara Lee', role: 'member'}];
const ME = {ana: {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true},
  cara: {id: 'cara', name: 'Cara Lee', email: 'cara@acme.example', role: 'viewer', company_role: 'member', cloud: true}};
const emps = [['assistant', 'Assistant', ''], ['dispatcher', 'Dispatcher', 'dispatcher']].map(([name, display_name, template]) =>
  ({name, display_name, role: display_name, team: '', status: 'active', host: 'keeper', can_chat: true, template}));
const iso = days => { const d = new Date(); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); };

function world() {
  return {
    hidden: [], business: 'field_service', actions: [], uploads: [], sidebarPuts: [], imports: [],
    locations: [
      {id: 'loc-1', name: 'Acme North Store', kind: 'store', parent_id: null, address: {street: '1 Main St', city: 'Portland', state: 'OR'}, source: 'quickbooks', archived_at: null},
      {id: 'loc-2', name: 'Acme Old Depot', kind: 'warehouse', parent_id: null, address: {city: 'Salem', state: 'OR'}, source: 'tico', archived_at: '2026-10-01T00:00:00Z'},
      {id: 'loc-3', name: 'Beta Foods Site', kind: 'customer_site', parent_id: null, customer_id: 'c-1', customer_name: 'Beta Foods', address: {city: 'Bend', state: 'OR'}, source: 'csv', archived_at: null}],
    items: [{id: 'it-1', name: 'Filter change', sku: 'SVC-10', kind: 'service', unit: 'each', currency: 'USD', price: 8900, source: 'stripe', archived_at: null},
      {id: 'it-2', name: 'Air filter 20x20', sku: 'PRT-20', kind: 'inventory', quantity_on_hand: 14, source: 'quickbooks', archived_at: null}],
    projects: [{id: 'pr-1', name: 'North Store refit', customer_id: 'c-1', customer_name: 'Beta Foods', status: 'open', source: 'tico', archived_at: null}],
    equipment: [{id: 'eq-1', name: 'Rooftop unit 3', kind: 'customer', customer_id: 'c-1', customer_name: 'Beta Foods', location_id: 'loc-3', location_name: 'Beta Foods Site', make: 'Trane', model: 'XR-16',
      serial: 'SN-4471', installed_on: '2024-05-01', warranty_ends_on: iso(40), next_due_on: iso(-3), source: 'tico', archived_at: null},
      {id: 'eq-2', name: 'Walk-in cooler', kind: 'owned', location_id: 'loc-1', location_name: 'Acme North Store', make: 'Hussmann', serial: 'HU-88', next_due_on: iso(200), source: 'tico', archived_at: null}],
    due_dates: [{id: 'dd-1', name: 'Filter change', subject_type: 'equipment', subject_id: 'eq-1', repeats: 'quarterly', starts_on: '2026-01-01', next_due_on: iso(-3), status: 'overdue', lead_days: 14, archived_at: null,
      history: [{due_on: '2026-07-01', completed_at: '2026-07-02T10:00:00Z', completed_by_id: 'human:ana', task_id: null}]}],
  };
}
const filterRows = (rows, sp) => {
  const q = (sp.get('q') || '').toLowerCase(), archived = sp.get('archived') || 'hide';
  return rows.filter(r => (!q || r.name.toLowerCase().includes(q)) && (archived === 'all' || (archived === 'only' ? r.archived_at : !r.archived_at)));
};
const accessFor = who => ({teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1', can_view_as: false, can_edit_defaults: false,
  teammates: [who === 'ana'
    ? {teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ['finance.admin']}
    : {teammate: 'human:cara', kind: 'person', team: '', reports_to: 'human:ana', default: 'own_expenses', grants: [], rights: []}]});

async function open(browser, who, w, hash, viewport = DESKTOP) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== ORIGIN) return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) return route.fulfill({path: uiFile(p.slice('/tico/ui/'.length))});
    if (p.startsWith('/vendor/')) return route.fulfill({path: uiFile(p.slice(1))});
    if (p === '/api/v2/events') return;
    const config = {app_name: 'Acme', hidden_sections: w.hidden, business_type: w.business};
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...ME[who], config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json(accessFor(who));
    if (p === '/api/v2/customers') return json({customers: [{id: 'c-1', name: 'Beta Foods'}], next_cursor: null});
    if (p === '/api/v2/finance/reports/pnl_by') {
      if (who === 'ana') return json({rows: [{key: 'pr-1', label: 'North Store refit', income: 500000, expense: 320000, net: 180000}, {key: 'loc-1', label: 'Acme North Store', income: 900000, expense: 400000, net: 500000}], income: 0, expense: 0, net: 0});
      return json({error: {message: 'No'}}, 403);
    }
    if (p === '/api/v2/company/sidebar' && req.method() === 'GET') return json({hidden_sections: w.hidden, business_type: w.business, may_change: who === 'ana', who_may_change: 'the owner or an admin'});
    if (p === '/api/v2/company/sidebar' && req.method() === 'PUT') {
      const body = req.postDataJSON(); w.sidebarPuts.push(body);
      if (body.hidden_sections) w.hidden = body.hidden_sections;
      if (body.business_type) w.business = body.business_type;
      return json({hidden_sections: w.hidden, business_type: w.business});
    }
    if (p === '/api/v2/finance/actions' && req.method() === 'POST') {
      const body = req.postDataJSON(); w.actions.push(body);
      const load = body.payload || {};
      if (body.kind === 'location_update' && 'active' in load) w.locations.find(l => l.id === load.location_id).archived_at = load.active ? null : '2026-11-01T00:00:00Z';
      if (body.kind === 'due_date_create') w.due_dates.push({id: 'dd-new', archived_at: null, history: [], next_due_on: load.starts_on, status: 'upcoming', ...load});
      if (body.kind === 'due_date_occurrence_complete') {
        const d = w.due_dates.find(x => x.id === load.due_date_id);
        d.history.push({due_on: load.due_on, completed_at: '2026-11-01T10:00:00Z', completed_by_id: 'human:ana', task_id: null});
        d.next_due_on = iso(87);
      }
      if (body.kind === 'due_date_occurrence_reopen') {
        const d = w.due_dates.find(x => x.id === load.due_date_id);
        d.history = d.history.filter(o => o.due_on !== load.due_on);
      }
      return json({status: 'committed', result: {}});
    }
    if (p === '/api/v2/setup' && req.method() === 'PUT') { w.setupPut = req.postDataJSON(); return json({}); }
    if (p === '/api/v2/imports/preview') {
      return json({rows: 3, summary: {new: 1, possible_duplicate: 1, error: 1}, lines: [{line: 2, name: 'Rooftop unit 9', status: 'new', reason: ''},
        {line: 3, name: 'Rooftop unit 3', status: 'possible_duplicate', reason: 'x', matches: {name: 'Rooftop unit 3'}}, {line: 4, name: '', status: 'error', reason: 'name is blank'}]});
    }
    if (p === '/api/v2/imports/commit') { w.imports.push(req.postDataJSON()); return json({rows: 3, summary: {created: 2, error: 1}, lines: [{line: 4, name: '', status: 'error', reason: 'name is blank'}]}); }
    if (p === '/api/v2/uploads/tasks' && req.method() === 'POST') {
      w.uploads.push({type: req.headers()['content-type'] || ''});
      return json({task: {id: 't-9'}});
    }
    // The list has no history; the detail does.
    if (p === '/api/v2/due-dates') return json({due_dates: w.due_dates.filter(d => d.subject_id === url.searchParams.get('subject_id')).map(({history, ...d}) => d), next_cursor: null});
    const date = p.match(/^\/api\/v2\/due-dates\/([^/]+)$/);
    if (date) return json({due_date: w.due_dates.find(d => d.id === date[1])});
    const m = p.match(/^\/api\/v2\/(locations|items|projects|equipment)(?:\/([^/]+))?$/);
    if (m && req.method() === 'GET') {
      const rows = w[m[1]];
      if (m[2]) { const one = {locations: 'location', items: 'item', projects: 'project', equipment: 'equipment'}[m[1]]; return json({[one]: rows.find(r => r.id === m[2])}); }
      return json({[m[1]]: filterRows(rows, url.searchParams), next_cursor: null});
    }
    return json(unrouted(p));
  });
  await page.goto(ORIGIN + '/' + hash);
  await page.waitForFunction(() => S.me && (S.emps || []).length);
  return {page, errors};
}
const rows = (page, sel = '#ops-table tr[data-ops-row]') => page.locator(sel).count();
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await page.screenshot({path: path.join(SHOTS, name + '.png')}); } };
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const palette = async page => {
  await page.evaluate(() => { const r = searchEntries().filter(e => e.kind === 'page').map(e => e.label); window.__pages = r; });
  return page.evaluate(() => window.__pages);
};
const close = async (o, label) => { assert.deepEqual(o.errors, [], label + ' page errors'); await o.page.close(); };

(async () => {
  const browser = await chromium.launch();

  // X2: off takes it out of the rail and ⌘K, the routes still answer, on brings the same rows back.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/settings/general/settings-sidebar'), {page} = o;
    const link = page.locator('#side a[data-nav="operations"]');
    await link.waitFor();
    assert.ok((await palette(page)).includes('Operations'));
    await page.locator('[data-sb-key="operations"]').waitFor();
    await page.locator('[data-sb-key="operations"]').uncheck();
    await page.waitForFunction(() => document.querySelector('#side a[data-nav="operations"]').offsetParent === null);
    assert.deepEqual(w.sidebarPuts.at(-1), {hidden_sections: ['operations']});
    assert.ok(!(await palette(page)).includes('Operations'), 'X2: gone from ⌘K');
    const api = await page.evaluate(async () => (await get('/v2/locations', {fresh: true})).locations.length);
    assert.equal(api, 2, 'X2: the data still answers');
    await page.locator('[data-sb-key="operations"]').check();
    await page.waitForFunction(() => document.querySelector('#side a[data-nav="operations"]').offsetParent !== null);
    await page.evaluate(() => { location.hash = '#/operations/locations'; });
    await page.locator('#ops-table tr[data-ops-row]').first().waitFor();
    assert.equal(await rows(page), 2, 'X2: same data back');
    // Hiding a section this way takes each optional link: Meetings under Library, and Strategy leaves Growth its own link.
    w.hidden = ['strategy', 'meetings'];
    await page.evaluate(() => { S.config.hidden_sections = ['strategy', 'meetings']; redesignSections(); });
    assert.equal(await page.locator('#side a[data-nav="meetings"]').evaluate(a => a.offsetParent), null, 'Meetings sub-link hides');
    assert.equal(await page.locator('#side a[data-nav="strategy"]').evaluate(a => a.offsetParent), null);
    assert.notEqual(await page.locator('#side a[data-nav="growth"]').evaluate(a => a.offsetParent), null, 'Growth keeps its own link');
    assert.deepEqual((await palette(page)).filter(l => ['Meetings', 'Strategy'].includes(l)), []);
    // Every tab hidden hides the section; a tab's key is its API name, so Products & services is operations.products.
    await page.evaluate(() => { S.config.hidden_sections = ['operations.locations', 'operations.products', 'operations.projects', 'operations.equipment']; redesignSections(); });
    assert.equal(await link.evaluate(a => a.offsetParent), null, 'all tabs hidden hides Operations');
    w.hidden = ['operations.products'];
    await page.evaluate(() => { S.config.hidden_sections = ['operations.products']; redesignSections(); OPS = null; location.hash = '#/operations/projects'; });
    await page.locator('.ops-head a.cur[data-ops-tab="projects"]').waitFor();
    assert.equal(await page.locator('.ops-head a[data-ops-tab="items"]').count(), 0);
    await close(o, 'X2');
  }

  // X5: a bookmark opens a hidden section and a hidden tab; a contextual link works too.
  {
    const w = world(); w.hidden = ['operations', 'operations.equipment'];
    const o = await open(browser, 'cara', w, '#/operations/equipment?id=eq-1'), {page} = o;
    await page.locator('#ops-panel h2').waitFor();
    assert.equal(await page.locator('#ops-panel h2').textContent(), 'Rooftop unit 3', 'X5: the bookmark opens');
    assert.equal(await page.locator('.ops-head a.cur').textContent(), 'Equipment');
    await page.locator('#ops-panel a[href*="locations"]').click();
    await page.waitForFunction(() => (S.route || '').includes('#/operations/locations'));
    await page.locator('#ops-panel h2').waitFor();
    // A tab hidden and not asked for stays out of the tabs.
    assert.equal(await page.locator('.ops-head a[data-ops-tab="equipment"]').count(), 0);
    await close(o, 'X5');
  }

  // X5b: one muted row for an admin, opening Settings > Sidebar; none for a member.
  {
    const w = world(); w.hidden = ['operations'];
    const admin = await open(browser, 'ana', w, '#/tasks');
    const row = admin.page.locator('#side .rd-hidden-row');
    await row.waitFor();
    assert.equal(await row.count(), 1);
    assert.match(await row.textContent(), /Operations/);
    await shot(admin.page, 'sidebar-hidden-row-admin');
    await row.click();
    await admin.page.locator('#settings-sidebar [data-sb-key="operations"]').waitFor();
    assert.equal(await admin.page.locator('#settings-sidebar [data-sb-key="operations"]').isChecked(), false);
    await shot(admin.page, 'settings-sidebar-admin');
    await close(admin, 'X5b admin');
    const member = await open(browser, 'cara', w, '#/settings/general/settings-sidebar');
    await member.page.locator('#settings-sidebar [data-sb-key="operations"]').waitFor();
    assert.equal(await member.page.locator('#side .rd-hidden-row').count(), 0, 'X5b: no row for a member');
    assert.equal(await member.page.locator('#settings-sidebar [data-sb-key="operations"]').isDisabled(), true, 'read only');
    assert.match(await member.page.locator('[data-sb-who]').textContent(), /The owner or an admin can change/);
    await shot(member.page, 'settings-sidebar-member');
    await close(member, 'X5b member');
  }

  // X8: inactive stays out until asked for; its panel brings it back.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/locations'), {page} = o;
    await page.locator('#ops-table tr[data-ops-row]').first().waitFor();
    assert.equal(await page.locator('#ops-table').getByText('Acme Old Depot').count(), 0, 'X8: hidden by default');
    await page.locator('[data-ops-inactive]').check();
    await page.locator('#ops-table').getByText('Acme Old Depot').waitFor();
    await page.locator('tr[data-ops-row="loc-2"] a').click();
    await page.locator('[data-ops-archive]').waitFor();
    await page.locator('[data-ops-archive]').click();
    await page.waitForFunction(() => document.querySelector('tr[data-ops-row="loc-2"]:not(.ops-off)'));
    assert.deepEqual(w.actions.at(-1), {kind: 'location_update', payload: {location_id: 'loc-2', active: true}}, 'X8: unarchive');
    await page.locator('[data-ops-inactive]').uncheck();
    await page.locator('#ops-table').getByText('Acme Old Depot').waitFor();
    await close(o, 'X8');
  }

  // X15, every tab: rows stack, the panel fills the screen, nothing scrolls sideways.
  {
    const w = world();
    for (const [tab, id] of [['locations', 'loc-1'], ['items', 'it-1'], ['projects', 'pr-1'], ['equipment', 'eq-1']]) {
      const o = await open(browser, 'ana', w, '#/operations/' + tab, PHONE), {page} = o;
      const first = page.locator('#ops-table tr[data-ops-row]').first();
      await first.waitFor();
      assert.equal(await sideways(page), 0, `X15: ${tab} list`);
      assert.equal(await first.evaluate(tr => getComputedStyle(tr).display), 'block', `X15: ${tab} rows stack`);
      await shot(page, `phone-${tab}`);
      await page.evaluate(([t, i]) => { location.hash = `#/operations/${t}?id=${i}`; }, [tab, id]);
      const panel = page.locator('#ops-panel');
      await panel.locator('h2').waitFor();
      const box = await panel.boundingBox();
      assert.deepEqual([box.x, box.y, box.width, box.height], [0, 0, PHONE.width, PHONE.height], `X15: ${tab} sheet`);
      assert.equal(await sideways(page), 0, `X15: ${tab} sheet`);
      await page.waitForTimeout(200);
      await shot(page, `phone-${tab}-panel`);
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('#ops-panel')?.hidden);
      await close(o, 'X15 ' + tab);
    }
  }

  // Desktop tabs: columns, a figure only when the read answers, and a teammate without the right gets no write controls.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/projects'), {page} = o;
    await page.locator('tr[data-ops-row="pr-1"]').waitFor();
    assert.match(await page.locator('tr[data-ops-row="pr-1"]').textContent(), /\$5,000/);
    for (const tab of ['locations', 'items', 'projects', 'equipment']) {
      await page.evaluate(t => { location.hash = '#/operations/' + t; }, tab);
      await page.locator(`.ops-t-${tab} tr[data-ops-row]`).first().waitFor();
      await shot(page, `desktop-${tab}`);
    }
    await close(o, 'desktop');
    const m = await open(browser, 'cara', w, '#/operations/projects');
    await m.page.locator('tr[data-ops-row="pr-1"]').waitFor();
    assert.doesNotMatch(await m.page.locator('.ops-t').textContent(), /Income|Profit/, 'no money without a read');
    assert.equal(await m.page.locator('[data-ops-add],[data-ops-import]').count(), 0, 'no write controls');
    await close(m, 'member');
  }

  // Your view: a partial P&L is labeled, on the tab and the panel.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/projects?id=pr-1'), {page} = o;
    await page.route('**/api/v2/finance/reports/pnl_by**', r => r.fulfill({contentType: 'application/json', body: JSON.stringify({rows: [{key: 'pr-1', label: 'x', income: 100, expense: 50, net: 50}], excluded: {accounts: 2}})}));
    await page.evaluate(() => { OPS = null; route(); });
    await page.locator('#ops-m-pnl [data-ops-your-view]').waitFor();
    assert.equal(await page.locator('.ops-t [data-ops-your-view]').count(), 1);
    await close(o, 'your view');
  }

  // + Add from photo: one task, for the dispatcher, with the file in the same request.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/equipment'), {page} = o;
    await page.locator('[data-ops-photo]').waitFor();
    await page.locator('[data-ops-photo-file]').setInputFiles({name: 'plate.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('fake')});
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Dispatcher'));
    assert.equal(w.uploads.length, 1);
    assert.match(w.uploads[0].type, /multipart/);
    await close(o, 'photo');
  }

  // Due dates: add, mark done, reopen, each its own action.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/equipment?id=eq-1'), {page} = o;
    const row = page.locator('[data-due="dd-1"]');
    await row.waitFor();
    assert.match(await row.textContent(), /Overdue/);
    await row.locator('[data-due-done]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-due-reopen]').length === 2);
    assert.equal(w.actions.at(-1).kind, 'due_date_occurrence_complete');
    await row.locator('summary').click();
    await shot(page, 'desktop-equipment-panel');
    await row.locator('[data-due-reopen]').first().click();
    await page.waitForFunction(() => document.querySelectorAll('[data-due-reopen]').length === 1);
    assert.equal(w.actions.at(-1).kind, 'due_date_occurrence_reopen');
    await page.locator('[data-due-add]').click();
    await page.locator('dialog.fin-dlg [name=name]').fill('Coil cleaning');
    await page.locator('dialog.fin-dlg [type=submit]').click();
    await page.locator('[data-due="dd-new"]').waitFor();
    assert.equal(w.actions.at(-1).payload.subject_id, 'eq-1');
    assert.doesNotMatch(await page.locator('#ops-panel').textContent(), /\brule/i, 'never the word');
    await close(o, 'due dates');
  }

  // CSV: per-row errors and duplicates; a kept duplicate and the bulk location go to commit.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/equipment'), {page} = o;
    await page.locator('[data-ops-import]').click();
    await page.locator('dialog.ops-import [name=file]').setInputFiles({name: 'acme.csv', mimeType: 'text/csv', buffer: Buffer.from('name\nRooftop unit 9\n')});
    await page.locator('[data-import-preview]').click();
    await page.locator('[data-import-commit]').waitFor();
    assert.match(await page.locator('.ops-import-rows').textContent(), /name is blank/);
    await shot(page, 'desktop-import-preview');
    assert.equal(await page.locator('[data-import-commit]').textContent(), 'Import 1');
    await page.locator('[data-import-keep="3"]').check();
    await page.locator('dialog.ops-import [name=location_id]').selectOption('loc-1');
    await page.locator('[data-import-commit]').click();
    await page.locator('dialog.ops-import .fin-kv').first().waitFor();
    const sent = w.imports[0];
    assert.deepEqual(sent.assign, {location_id: 'loc-1'});
    assert.deepEqual(sent.include_lines, [3]);
    assert.equal(sent.record_type, 'equipment');
    await close(o, 'csv');
  }

  // Setup: the question asks once the server's record carries the answer, and the answer goes top level on the save.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/tasks'), {page} = o;
    const asked = await page.evaluate(async () => {
      const older = {catalog: catalogState(catalogCards([]))}, newer = {catalog: catalogState(catalogCards([]))};
      onbAdopt(older, {}); onbAdopt(newer, {business_type: null});
      const out = {older: onbStepHTML({...older, step: 0}, 'about').includes('onb-business-type'), newer: onbStepHTML({...newer, step: 0}, 'about').includes('onb-business-type')};
      await onbSave(older);
      newer.record.business_type = 'restaurant';
      await onbSave(newer);
      return out;
    });
    assert.deepEqual(asked, {older: false, newer: true});
    assert.equal(w.setupPut.business_type, 'restaurant');
    await close(o, 'setup');
  }

  await browser.close();
  console.log('operations: ok');
})().catch(e => { console.error(e); process.exit(1); });
