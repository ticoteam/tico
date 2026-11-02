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
      {id: 'loc-1', name: 'Acme North Store', kind: 'store', in_books: true, parent_id: null, address: {street: '1 Main St', city: 'Portland', state: 'OR'}, source: 'quickbooks', archived_at: null},
      {id: 'loc-2', name: 'Acme Old Depot', kind: 'warehouse', parent_id: null, address: {city: 'Salem', state: 'OR'}, source: 'tico', archived_at: '2026-10-01T00:00:00Z'},
      {id: 'loc-3', name: 'Beta Foods Site', kind: 'customer_site', parent_id: null, customer_id: 'c-1', customer_name: 'Beta Foods', address: {city: 'Bend', state: 'OR'}, source: 'csv', archived_at: null}],
    items: [{id: 'it-1', name: 'Filter change', sku: 'SVC-10', kind: 'service', unit: 'each', currency: 'USD', price: 8900, source: 'stripe', archived_at: null},
      {id: 'it-2', name: 'Air filter 20x20', sku: 'PRT-20', kind: 'inventory', quantity_on_hand: 14, source: 'quickbooks', archived_at: null}],
    projects: [{id: 'pr-1', name: 'North Store refit', customer_id: 'c-1', customer_name: 'Beta Foods', status: 'open', source: 'tico', archived_at: null}],
    equipment: [{id: 'eq-1', name: 'Rooftop unit 3', kind: 'customer', customer_id: 'c-1', customer_name: 'Beta Foods', location_id: 'loc-3', location_name: 'Beta Foods Site', make: 'Trane', model: 'XR-16',
      serial: 'SN-4471', installed_on: '2024-05-01', warranty_ends_on: iso(40), next_due_on: iso(-3), next_due_status: 'overdue', source: 'tico', archived_at: null},
      {id: 'eq-2', name: 'Walk-in cooler', kind: 'owned', location_id: 'loc-1', location_name: 'Acme North Store', make: 'Hussmann', serial: 'HU-88', next_due_on: iso(200), next_due_status: 'upcoming', source: 'tico', archived_at: null}],
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
    if (p === '/api/v2/finance/reports/pnl_by_account') {
      if (who !== 'ana') return json({error: {message: 'No'}}, 403);
      return json({rows: [{account_id: 'a-1', label: 'Service income', amount: 500000}], ...(w.pnlOf?.[url.searchParams.get('id')] || {income: 500000, expense: 320000, net: 180000})});
    }
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
      if (w.approve) return json({status: 'needs_approval'});
      if (body.kind === 'location_update' && !('active' in load)) { const {location_id, ...fields} = load; Object.assign(w.locations.find(l => l.id === location_id), fields); }
      if (body.kind === 'location_update' && 'active' in load) w.locations.find(l => l.id === load.location_id).archived_at = load.active ? null : '2026-11-01T00:00:00Z';
      if (body.kind === 'record_file_add') {
        const one = {location: 'locations', item: 'items', equipment: 'equipment'}[load.subject_type];
        const row = w[one].find(r => r.id === load.subject_id);
        row.files = [...(row.files || []), {id: 'link-' + load.file_id, file_id: load.file_id, added_at: '2026-11-01T10:00:00Z', added_by_id: 'human:ana'}];
      }
      if (body.kind === 'record_file_remove') for (const one of ['locations', 'items', 'equipment']) for (const r of w[one]) r.files = (r.files || []).filter(f => f.id !== load.record_file_id);
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
    if (p === '/api/v2/files') return json({files: [{id: 'f-1', title: 'Manual.pdf'}, {id: 'f-2', title: 'Warranty.pdf'}], next_cursor: null});
    if (p === '/api/v2/files/search') return json({results: [{id: 'f-2', title: 'Warranty.pdf'}]});
    if (/^\/api\/v2\/files\/[^/]+\/meta$/.test(p)) return json({name: {'f-1': 'Manual.pdf', 'f-2': 'Warranty.pdf'}[p.split('/')[4]] || 'File'});
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
      // Pages by limit and cursor as the API does.
      const found = filterRows(rows, url.searchParams), from = Number(url.searchParams.get('cursor') || 0), limit = Number(url.searchParams.get('limit') || 50);
      if (from && w.failMore) return json({error: {message: 'Did not load more'}}, 500);
      return json({[m[1]]: found.slice(from, from + limit), next_cursor: from + limit < found.length ? String(from + limit) : null});
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
    assert.equal(await page.locator('#ops-table th', {hasText: 'In books'}).count(), 1, 'a mirrored book shows In books');
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

  // A long list: pick lists reach past the first page, the panel stays on screen deep in the table, a save keeps the place,
  // a failed More keeps the rows, and a search leaves the open panel alone.
  {
    const w = world();
    w.locations.push(...Array.from({length: 260}, (_, n) => ({id: 'many-' + n, name: `Acme Branch ${String(n).padStart(3, '0')}`, kind: 'store', parent_id: null, address: {}, source: 'tico', archived_at: null})));
    const o = await open(browser, 'ana', w, '#/operations/equipment'), {page} = o;
    await page.locator('#ops-table tr[data-ops-row]').first().waitFor();
    await page.waitForFunction(() => document.querySelectorAll('select[data-ops-f="location"] option, select[name=location] option').length > 200);
    await page.evaluate(() => { location.hash = '#/operations/locations'; });
    await page.locator('tr[data-ops-row="many-0"]').waitFor();
    await page.locator('[data-ops-more]').click();
    await page.locator('tr[data-ops-row="many-90"]').waitFor();
    const loaded = await rows(page);
    assert.ok(loaded >= 100);

    w.failMore = true;
    await page.locator('[data-ops-more]').click();
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.trim());
    assert.equal(await rows(page), loaded, 'a failed More keeps the table');
    w.failMore = false;

    const deep = page.locator('tr[data-ops-row="many-90"]');
    await deep.locator('a.ops-name').click();
    await page.locator('#ops-panel h2', {hasText: 'Acme Branch 090'}).waitFor();
    await page.waitForTimeout(100);
    const box = await page.locator('#ops-panel').boundingBox();
    assert.ok(box.y >= 0 && box.y < DESKTOP.height, `the panel is on screen, at ${box.y}`);
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'ops-panel', 'focus moves into the panel');

    const before = await page.evaluate(() => scrollY + (document.querySelector('.main-scroll, #main')?.scrollTop || 0));
    await page.locator('[data-ops-edit]').click();
    await page.locator('dialog.fin-dlg [name=name]').fill('Acme Branch renamed');
    await page.locator('dialog.fin-dlg [type=submit]').click();
    await page.locator('tr[data-ops-row="many-90"]', {hasText: 'Acme Branch renamed'}).waitFor();
    assert.equal(await rows(page), loaded, 'a save refreshes its row and keeps the rest');
    assert.equal(await page.evaluate(() => scrollY + (document.querySelector('.main-scroll, #main')?.scrollTop || 0)), before, 'the place in the list stays');
    assert.equal(await page.locator('tr[data-ops-row="many-90"].ops-on').count(), 1);

    // A search reads the list again; the panel is left alone.
    await page.evaluate(() => { document.querySelector('#ops-panel').dataset.same = 'yes'; document.querySelector('#ops-panel h2').dataset.same = 'yes'; });
    await page.locator('[data-ops-q]').fill('Branch');
    await page.waitForFunction(() => document.querySelectorAll('#ops-table tr[data-ops-row]').length === 50);
    assert.equal(await page.locator('#ops-panel h2').getAttribute('data-same'), 'yes', 'the panel did not redraw');
    assert.equal(await page.locator('tr[data-ops-row="many-90"]').count(), 0);

    // Esc closes it. Focus goes back to its row when the row is still here.
    await page.locator('[data-ops-q]').fill('');
    await page.locator('tr[data-ops-row="many-0"]').waitFor();
    await page.locator('tr[data-ops-row="many-3"] a.ops-name').click();
    await page.locator('#ops-panel h2', {hasText: 'Acme Branch 003'}).waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#ops-panel').hidden);
    assert.equal(await page.evaluate(() => document.activeElement?.closest('tr')?.dataset.opsRow), 'many-3', 'focus returns to the row');
    await close(o, 'long list');
  }

  // At tablet width the panel covers the table: focus goes into it too.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/locations', {width: 1000, height: 800}), {page} = o;
    await page.locator('tr[data-ops-row="loc-1"] a.ops-name').click();
    await page.locator('#ops-panel h2').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'ops-panel');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#ops-panel').hidden);
    await close(o, 'tablet');
  }

  // A write that waits for a person says so, and only that. The record stays as it was.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/locations?id=loc-1'), {page} = o;
    await page.locator('[data-ops-archive]').waitFor();
    w.approve = true;
    await page.locator('[data-ops-archive]').click();
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Sent for approval'));
    await page.waitForTimeout(300);
    assert.doesNotMatch((await page.locator('.toast').allTextContents()).join(), /Marked inactive/);
    await page.goto(ORIGIN + '/#/operations/equipment?id=eq-1');
    const done = page.locator('[data-due="dd-1"] [data-due-done]');
    await done.waitFor();
    await done.click();
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Sent for approval'));
    await page.waitForTimeout(300);
    assert.doesNotMatch((await page.locator('.toast').allTextContents()).join(), /Marked done|\bDone\b/, 'no success toast');
    await close(o, 'approval');
  }

  // An address: a Save with no change writes nothing, whatever order the API keeps it in; a change keeps keys the form lacks.
  {
    const w = world();
    w.locations[0].address = {state: 'OR', country: 'US', city: 'Portland', street: '1 Main St'};
    const o = await open(browser, 'ana', w, '#/operations/locations?id=loc-1'), {page} = o;
    const form = page.locator('dialog.fin-dlg');
    await page.locator('[data-ops-edit]').click();
    await form.locator('[type=submit]').click();
    await form.waitFor({state: 'detached'});
    assert.equal(w.actions.filter(a => a.kind === 'location_update').length, 0, 'a no-op Save writes nothing');
    await page.locator('[data-ops-edit]').click();
    await form.locator('[name=city]').fill('Salem');
    await form.locator('[type=submit]').click();
    await form.waitFor({state: 'detached'});
    const sent = w.actions.filter(a => a.kind === 'location_update').at(-1).payload.address;
    assert.deepEqual(sent, {state: 'OR', country: 'US', city: 'Salem', street: '1 Main St'});
    await close(o, 'address');
  }

  // Money in its own currency; the row's due state is the server's; a parent names itself; small empty states.
  {
    const w = world();
    w.items[0].currency = 'EUR';
    w.pnlOf = {'pr-2': {income: 12300, expense: 0, net: 12300, cost_of_goods: 4100, gross_margin: 8200}};
    w.projects.push({id: 'pr-2', name: 'Small job', status: 'open', source: 'tico', archived_at: null});
    // Due 60 days out with a 90-day lead: soon by the server's answer, which no fixed window on this side would know.
    w.equipment[1] = {...w.equipment[1], next_due_on: iso(60), next_due_status: 'due'};
    w.locations[2].parent_id = 'gone-from-refs'; w.locations[2].parent_name = 'Acme Region West';
    const o = await open(browser, 'ana', w, '#/operations/items'), {page} = o;
    await page.locator('tr[data-ops-row="it-1"]').waitFor();
    assert.match(await page.locator('tr[data-ops-row="it-1"]').textContent(), /€89\.00/);
    assert.doesNotMatch(await page.locator('tr[data-ops-row="it-1"]').textContent(), /\$/);
    await page.evaluate(() => { location.hash = '#/operations/projects?id=pr-2'; });
    await page.locator('#ops-m-pnl', {hasText: '$123'}).waitFor();
    const totals = (await page.locator('#ops-m-pnl .fin-kv').allInnerTexts()).map(t => t.replace(/\s+/g, ' '));
    assert.deepEqual(totals.slice(3), ['Cost of goods $41', 'Gross margin $82'], 'after the totals, as Finance orders them');
    await page.evaluate(() => { location.hash = '#/operations/projects?id=pr-1'; });
    await page.locator('#ops-panel h2', {hasText: 'North Store refit'}).waitFor();
    await page.locator('#ops-m-pnl', {hasText: '$5,000'}).waitFor();
    assert.doesNotMatch(await page.locator('#ops-m-pnl').textContent(), /Cost of goods|Gross margin/, 'a read without them shows neither');
    await page.evaluate(() => { location.hash = '#/operations/projects?id=pr-2'; });
    await page.locator('#ops-m-pnl', {hasText: '$123'}).waitFor();
    assert.doesNotMatch(await page.locator('tr[data-ops-row="pr-2"]').textContent(), /\$/, 'a row the list read left out stays blank');
    await page.evaluate(() => { location.hash = '#/operations/equipment'; });
    await page.locator('tr[data-ops-row="eq-2"] .ops-soon').waitFor();
    await page.locator('tr[data-ops-row="eq-1"] .ops-overdue').waitFor();
    await page.locator('[data-ops-q]').fill('nothing like this');
    await page.locator('#ops-table', {hasText: 'No equipment matches'}).waitFor();
    await page.evaluate(() => { location.hash = '#/operations/locations?id=loc-3'; });
    await page.locator('#ops-panel a', {hasText: 'Acme Region West'}).waitFor();
    await page.locator('[data-ops-edit]').click();
    assert.equal(await page.locator('dialog.fin-dlg [name=parent_id] option:checked').textContent(), 'Acme Region West', 'Edit names the parent');
    await page.locator('dialog.fin-dlg [data-close], dialog.fin-dlg button.ghost').first().click();

    // A file with no rows says so.
    await page.route('**/api/v2/imports/preview', r => r.fulfill({contentType: 'application/json', body: JSON.stringify({rows: 0, summary: {}, lines: []})}));
    await page.locator('[data-ops-import]').click();
    await page.locator('dialog.ops-import [name=file]').setInputFiles({name: 'acme.csv', mimeType: 'text/csv', buffer: Buffer.from('name\n')});
    await page.locator('[data-import-preview]').click();
    await page.locator('dialog.ops-import', {hasText: 'No rows in this file'}).waitFor();
    await page.locator('dialog.ops-import [data-close]').first().click();
    await close(o, 'small');

    // No due dates and no way to add one: no heading.
    const m = await open(browser, 'cara', w, '#/operations/locations?id=loc-1');
    await m.page.locator('#ops-panel h2').waitFor();
    await m.page.waitForTimeout(400);
    assert.deepEqual((await m.page.locator('#ops-panel .ops-sec-h h3').allTextContents()).filter(h => h === 'Due dates'), []);
    await close(m, 'member due');
  }

  // Leaving Operations with the phone sheet open gives the page its scroll back.
  {
    const w = world(), o = await open(browser, 'ana', w, '#/operations/locations?id=loc-1', PHONE), {page} = o;
    await page.locator('#ops-panel h2').waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('ops-sheet-open')), true);
    await page.evaluate(() => { location.hash = '#/tasks'; });
    await page.waitForFunction(() => !document.body.classList.contains('ops-sheet-open'));
    await close(o, 'sheet');
  }

  // Business type: blank only until it's set; the server takes no blank.
  {
    const w = world(); w.business = null;
    const o = await open(browser, 'ana', w, '#/settings/general/settings-sidebar'), {page} = o;
    const type = page.locator('[data-sbset-type]');
    await type.waitFor();
    assert.equal(await type.locator('option[value=""]').count(), 1);
    await type.selectOption('restaurant');
    await page.waitForFunction(() => !document.querySelector('[data-sbset-type] option[value=""]'));
    assert.equal(w.sidebarPuts.at(-1).business_type, 'restaurant');
    await close(o, 'business blank');
    const set = await open(browser, 'ana', world(), '#/settings/general/settings-sidebar');
    await set.page.locator('[data-sbset-type]').waitFor();
    assert.equal(await set.page.locator('[data-sbset-type] option[value=""]').count(), 0);
    await close(set, 'business set');
  }

  // A provider's copy says when it synced and flags when stale, on the row and the panel; Tico's own record says nothing.
  {
    const w = world();
    const hours = h => new Date(Date.now() - h * 3600e3).toISOString();
    Object.assign(w.locations[0], {source: 'quickbooks', as_of: hours(2), stale: false});
    Object.assign(w.equipment[0], {source: 'xero', as_of: null, stale: true});
    Object.assign(w.items[0], {source: 'stripe', as_of: hours(72), stale: true});
    Object.assign(w.equipment[1], {source: 'tico', as_of: hours(0), stale: false});
    const o = await open(browser, 'ana', w, '#/operations/locations?id=loc-1'), {page} = o;
    const copy = page.locator('[data-ops-copy]');
    await copy.waitFor();
    assert.equal(await copy.textContent(), 'QuickBooks copy · synced 2h ago');
    assert.equal(await page.locator('[data-ops-stale]').count(), 0, 'a fresh copy has no flag');
    await page.evaluate(() => { location.hash = '#/operations/items?id=it-1'; });
    await copy.filter({hasText: 'Stripe copy · synced 3d ago'}).waitFor();
    assert.match(await copy.textContent(), /May be out of date/);
    assert.match(await page.locator('tr[data-ops-row="it-1"]').textContent(), /May be out of date/, 'the row flags it too');
    await page.evaluate(() => { location.hash = '#/operations/equipment?id=eq-1'; });
    await copy.filter({hasText: 'Xero copy · not synced yet'}).waitFor();
    assert.match(await copy.textContent(), /May be out of date/);
    await page.evaluate(() => { location.hash = '#/operations/equipment?id=eq-2'; });
    await page.locator('#ops-panel h2', {hasText: 'Walk-in cooler'}).waitFor();
    assert.equal(await copy.count(), 0, "Tico's own record shows nothing extra");
    assert.doesNotMatch(await page.locator('tr[data-ops-row="eq-2"]').textContent(), /out of date/);
    await close(o, 'freshness');
  }

  // Files: add from the Library, remove (the file stays there), no controls without the right, and a request that waits
  // for a person shows no success toast and changes nothing.
  for (const [tab, id] of [['locations', 'loc-1'], ['items', 'it-1'], ['equipment', 'eq-1']]) {
    const w = world(), one = {locations: 'location', items: 'item', equipment: 'equipment'}[tab];
    const o = await open(browser, 'ana', w, `#/operations/${tab}?id=${id}`), {page} = o;
    const files = page.locator('#ops-m-files');
    await files.locator('[data-file-add]').click();
    const dlg = page.locator('dialog.fin-dlg');
    await dlg.locator('[type=submit]').click();
    await dlg.locator('[data-error]', {hasText: 'Pick a file'}).waitFor();
    await dlg.locator('[name=find]').fill('warr');
    await dlg.getByRole('option', {name: 'Manual.pdf'}).waitFor({state: 'detached'});
    await dlg.getByRole('option', {name: 'Warranty.pdf'}).click();
    await dlg.locator('[type=submit]').click();
    await files.getByRole('link', {name: 'Warranty.pdf'}).waitFor();
    assert.deepEqual(w.actions.at(-1), {kind: 'record_file_add', payload: {subject_type: one, subject_id: id, file_id: 'f-2'}});
    assert.match(await files.getByRole('link', {name: 'Warranty.pdf'}).getAttribute('href'), /\/v2\/files\/f-2$/);
    // A linked file isn't offered again.
    await files.locator('[data-file-add]').click();
    await dlg.getByRole('option', {name: 'Manual.pdf'}).waitFor();
    assert.equal(await dlg.getByRole('option', {name: 'Warranty.pdf'}).count(), 0);
    await dlg.locator('[data-close]').first().click();
    if (tab === 'equipment') await shot(page, 'desktop-equipment-files');
    // Sent for approval: no success toast, the link stays.
    w.approve = true;
    await files.locator('[data-file-remove="link-f-2"]').click();
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Sent for approval'));
    await page.waitForTimeout(200);
    assert.doesNotMatch((await page.locator('.toast').allTextContents()).join(), /Removed/);
    await files.getByRole('link', {name: 'Warranty.pdf'}).waitFor();
    w.approve = false;
    await files.locator('[data-file-remove="link-f-2"]').click();
    await files.getByRole('link', {name: 'Warranty.pdf'}).waitFor({state: 'detached'});
    assert.deepEqual(w.actions.at(-1), {kind: 'record_file_remove', payload: {record_file_id: 'link-f-2'}});
    await close(o, 'files ' + tab);
  }
  {
    const w = world();
    w.locations[0].files = [{id: 'link-1', file_id: 'f-1', added_at: '2026-11-01T10:00:00Z', added_by_id: 'human:ana'}];
    const m = await open(browser, 'cara', w, '#/operations/locations?id=loc-1'), files = m.page.locator('#ops-m-files');
    await files.getByRole('link', {name: 'Manual.pdf'}).waitFor();
    assert.equal(await files.locator('[data-file-add],[data-file-remove]').count(), 0, 'no write controls without the right');
    await m.page.evaluate(() => { location.hash = '#/operations/locations?id=loc-3'; });
    await m.page.locator('#ops-panel h2', {hasText: 'Beta Foods Site'}).waitFor();
    await m.page.waitForTimeout(200);
    assert.equal(await m.page.locator('#ops-m-files .ops-sec').count(), 0, 'no empty section for a viewer');
    await close(m, 'files viewer');
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
