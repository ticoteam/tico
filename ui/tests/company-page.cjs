// The Company page section (ui/app/company-page.js) against a fake hub: Acme (acme.example). Hidden from a member, by
// link and by address; shown to an admin. Transparency warns before Open and says what stops being visible before
// anything is taken away, and sends nothing on Cancel. The queue puts a held edition first with its reason. Approve shows
// only to the person an approval is addressed to and goes through the approvals API. Override needs a note. Text from a
// bot's update is escaped. A held stat says why. Removing a reader says emails can't be recalled. With no sign-in
// provider, Private and adding readers are off. A part that fails shows Retry while the others draw, and a 403 is the
// permission line. Every way an approval can stand reads plainly and offers the way on; approve and publish are two steps.
// A failed draft offers drafting again. A stopped schedule, no email, a failed release and a tightened rule's reach all show.
// A phone has no sideways scroll and shows one audience at a time. No native dialog ever opens.
// TICO_UI_SHOTS=<dir> saves each part at 1280 and 390 wide, light and dark.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1280, height: 900};

const people = [{id: 'ana', name: 'Ana Silva', role: 'owner', email: 'ana@acme.example'}, {id: 'cara', name: 'Cara Lee', role: 'admin', email: 'cara@acme.example'},
  {id: 'erin', name: 'Erin Tester', role: 'member', email: 'erin@acme.example'}];
const emps = [['eng-lead', 'Engineering Lead'], ['mkt-lead', 'Marketing Lead']].map(([name, display_name]) =>
  ({name, display_name, role: display_name, team: '', status: 'active', host: 'keeper', can_chat: true}));
const ME = {ana: {role: 'owner', bot_admin: true, company_role: 'owner'}, cara: {role: 'viewer', bot_admin: true, company_role: 'admin'},
  erin: {role: 'viewer', bot_admin: false, company_role: 'member'}};

const AUDIENCES = [
  {id: 'a-board', name: 'Board', kind: 'board', cadence: 'monthly', due_rule: {kind: 'business_day', n: 5}, approver: 'human:ana', enabled: true, built_in: true, next_due: '2026-12-07'},
  {id: 'a-inv', name: 'Investors', kind: 'investors', cadence: 'monthly', due_rule: {kind: 'business_day', n: 10}, approver: 'human:cara', enabled: true, built_in: true, next_due: '2026-12-14'},
  {id: 'a-pub', name: 'Public', kind: 'public', cadence: 'monthly', due_rule: {kind: 'days_after_period_end', n: 30}, approver: null, enabled: false, built_in: true, next_due: null},
];
const ed = (id, status, audience_id, title, extra = {}) => ({id, status, audience_id, title, audience_name: '', author: {id: 'company', name: 'Company', kind: 'company'}, period_start: '2026-10-01', period_end: '2026-10-31', version: 1,
  sections: [{key: 'highlights', items: [{body: 'Revenue grew **12%** in October.', ask_id: null}]}, {key: 'asks', items: [{body: 'Intros to two design partners.', ask_id: 'ask-1'}]}],
  checks: [{version: 1, kind: 'summary', outcome: 'pass', detail: {reasons: []}}, {version: 1, kind: 'rules', outcome: 'pass', detail: {}}, {version: 1, kind: 'leak', outcome: 'pass', detail: {}}, {version: 1, kind: 'leak', outcome: 'flagged', detail: {names: ['An older run']}}],
  sources: [{id: 'src1', update_id: 'u1', body_sha256: 'x', body: 'Shipped the importer.', author_name: 'Engineering Lead'}], seen_by: [], seen_count: 0, checks_current: true, last_check: {outcome: 'pass', reasons: []}, ...extra});
const EDITIONS = () => [
  ed('e-pub', 'published', 'a-board', 'September update', {period_start: '2026-09-01', period_end: '2026-09-30', published_at: '2026-10-08', seen_by: [{reader_id: 'r1', email: 'rita@acme.example', name: null, viewed_at: '2026-10-09'}], seen_count: 1}),
  ed('e-ready', 'ready', 'a-pub', 'October, public'),
  ed('e-mine', 'awaiting_approval', 'a-board', 'October, board', {approval: {id: 'ap-1', state: 'pending', to: 'human:ana', to_name: 'Ana Silva'}}),
  ed('e-other', 'awaiting_approval', 'a-inv', 'October, investors', {approval: {id: 'ap-2', state: 'pending', to: 'human:cara', to_name: 'Cara Lee'}}),
  ed('e-held', 'held', 'a-inv', 'Held <img src=x onerror="window.__pwned=1">', {last_check: {outcome: 'held', reasons: ['leak']}, checks_current: true,
    sections: [{key: 'highlights', items: [{body: 'Closed <script>window.__pwned=1</script> Northwind.', ask_id: null}]}],
    checks: [{version: 1, kind: 'summary', outcome: 'held', detail: {reasons: ['leak']}}, {version: 1, kind: 'rules', outcome: 'pass', detail: {}}, {version: 1, kind: 'leak', outcome: 'flagged', detail: {names: ['Northwind']}}]}),
  ed('e-gone', 'retracted', 'a-pub', 'August update', {period_start: '2026-08-01', period_end: '2026-08-31', published_at: '2026-09-05', retraction_note: 'Wrong numbers'}),
  ed('e-dropped', 'retracted', 'a-pub', 'Never sent', {period_start: '2026-07-01', period_end: '2026-07-31', retraction_note: 'Not sending this one'}),
  // Where an approval can stand, and a draft that failed: each says so and offers the way on.
  ed('e-approved', 'awaiting_approval', 'a-inv', 'Approved one', {approval: {id: 'ap-3', state: 'approved', to: 'human:cara', to_name: 'Cara Lee'}}),
  ed('e-declined', 'awaiting_approval', 'a-inv', 'Declined one', {approval: {id: 'ap-4', state: 'declined', to: 'human:cara', to_name: 'Cara Lee', note: 'Too much on churn'}}),
  ed('e-lapsed', 'awaiting_approval', 'a-inv', 'Lapsed one', {approval: {id: 'ap-5', state: 'lapsed'}}),
  ed('e-stale', 'awaiting_approval', 'a-inv', 'Stale one', {approval: {id: 'ap-6', state: 'stale', to: 'human:cara', to_name: 'Cara Lee'}}),
  ed('e-failed', 'held', 'a-inv', null, {sections: [], last_check: null, checks_current: false, sources: [],
    checks: [{version: 1, kind: 'draft', outcome: 'failed', detail: {reason: 'no_engine'}}]}),
];
const STATS = [{id: 's1', source_kind: 'finance_metric', ref_id: 'revenue', data_class: 'revenue', label: 'Revenue', chart: 'line', position: 0},
  {id: 's2', source_kind: 'kpi', ref_id: 'k1', data_class: 'customers', label: 'Active homes', chart: 'number', position: 1},
  {id: 's3', source_kind: 'finance_metric', ref_id: 'gross_margin', data_class: 'gross_margin', label: 'Gross margin', chart: 'bars', position: 2}];
const series = vals => vals.map((value, i) => ({period: `2026-0${i + 5}`, value, display: String(value)}));
const PROJECTION = {stats: [{id: 's1', label: 'Revenue', definition: 'Recognised revenue', chart: 'line', period: 'Oct 2026', as_of: '2026-11-01', display: '$50K', stale: false, series: series([31, 34, 38, 41, 46, 50])},
  {id: 's2', label: 'Active homes', chart: 'number', period: 'Oct 2026', as_of: '2026-09-30', display: '320', stale: true, series: []}],
  held: [{id: 's3', label: 'Gross margin', reason: 'Gross margin next to revenue would show cost of sales, which is Hidden.'}], absent: [{id: 's4', label: 'Burn', reason: 'Hidden'}], approved: false, next_offset: null};
const AVAILABLE = [{source_kind: 'kpi', ref_id: 'k1', label: 'Active homes'}, {source_kind: 'kpi', ref_id: 'k2', label: 'Pipeline'}, {source_kind: 'finance_metric', ref_id: 'revenue', label: 'Revenue'},
  {source_kind: 'finance_metric', ref_id: 'gross_margin', label: 'Gross margin'}, {source_kind: 'finance_line', ref_id: 'line-hosting', label: 'Hosting'}];

function fakeHub(opts) {
  const domain = opts.noDomain ? '' : 'pages.acme.example', origin = opts.noSlug || opts.noDomain ? '' : 'https://open.acme.example';
  const hub = {settings: {transparency: 'closed', slug: opts.noSlug || opts.noDomain ? null : 'acme', accent: null, logo_file_id: null, company_name: 'Acme', reader_origin: origin, page_domain: domain, address: origin, public_live: false},
    emailAvailable: opts.noEmail !== true, scheduleProblems: opts.scheduleProblems || [], releaseFails: !!opts.releaseFails, tightenedCount: opts.tightened || 0,
    signIn: opts.privateOn === false ? {available: false, reason: 'No sign-in provider is set up (OIDC), so private audiences are off'} : {available: true, reason: ''},
    editions: opts.noEditions ? [] : EDITIONS(), audiences: structuredClone(AUDIENCES), stats: opts.noEditions ? [] : structuredClone(STATS),
    readers: opts.noEditions ? [] : [{id: 'r1', email: 'rita@acme.example', name: 'Rita Vale', notify: true, audiences: [{id: 'a-board', name: 'Board', kind: 'board'}, {id: 'a-inv', name: 'Investors', kind: 'investors'}]}, {id: 'r2', email: 'sam@acme.example', name: null, notify: false, audiences: [{id: 'a-inv', name: 'Investors', kind: 'investors'}]}],
    invites: [{id: 'i1', state: 'open', created_at: '2026-10-20', expires_at: '2026-12-20'}, {id: 'i2', state: 'used', created_at: '2026-10-01', expires_at: '2026-11-01', used_at: '2026-10-02'}, {id: 'i3', state: 'revoked', created_at: '2026-09-01', expires_at: '2026-10-01', revoked_at: '2026-09-02'}],
    rules: {'a-board': [{data_class: 'revenue', ref_id: '', mode: 'show', lag_days: 0, rounding: null}, {data_class: 'cash_runway', ref_id: '', mode: 'delayed', lag_days: 45, rounding: null}],
      'a-inv': [{data_class: 'revenue', ref_id: '', mode: 'rounded', lag_days: 0, rounding: 'sig:2 quarterly'}, {data_class: 'customers', ref_id: '', mode: 'show', lag_days: 0, rounding: null}], 'a-pub': []},
    fail: new Set(opts.fail || []), writes: [], reads: []};
  return hub;
}
function answer(hub, method, p, url, payload) {
  if (hub.fail.has(p)) return [500, {error: {code: 'down', detail: 'Did not load'}}];
  if (p === '/api/v2/company-page' && method === 'GET') return hub.denied ? [403, {error: {code: 'denied', detail: 'no'}}] : [200, hub.settings];
  if (p === '/api/v2/company-page' && method === 'PATCH') {
    Object.assign(hub.settings, payload);
    if (payload.slug) hub.settings.address = `https://${payload.slug}.${hub.settings.page_domain}`;
    return [200, hub.settings];
  }
  if (p === '/api/v2/audiences') return [200, {audiences: hub.audiences, next_offset: null}];
  let m;
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/sources$/.exec(p))) return [200, {sources: m[1] === 'a-pub' ? [] : [{id: 'src-' + m[1], author: 'company'}, {id: 'src2-' + m[1], author: 'eng-lead'}]}];
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/disclosure$/.exec(p))) {
    if (method === 'PUT') {
      const held = hub.rules[m[1]] ||= [];
      for (const r of payload.rules) { const at = held.findIndex(x => x.data_class === r.data_class && x.ref_id === r.ref_id); if (at >= 0) held.splice(at, 1); held.push({rounding: null, ...r}); }
    }
    return [200, {...(method === 'PUT' ? {published_editions: hub.tightenedCount} : {}), rules: hub.rules[m[1]] || [], revision: 'rev', data_classes: ['revenue', 'growth', 'gross_margin', 'burn', 'cash_runway', 'fundraising', 'ai_spend', 'pnl_line', 'headcount', 'customers', 'pipeline', 'kpi', 'customer_names', 'product', 'people', 'legal', 'security', 'mission'], modes: [], presets: []}];
  }
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/stats\/approval$/.exec(p))) return [200, {approval: null}];
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/stats$/.exec(p))) return [200, PROJECTION];
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/invites$/.exec(p)) && method === 'GET') return [200, {invites: hub.invites}];
  if ((m = /^\/api\/v2\/audiences\/([^/]+)\/invites$/.exec(p))) return [200, {id: 'i9', state: 'open', url: 'https://acme.tico.page/invite/one-time', expires_at: '2026-12-30'}];
  if (p === '/api/v2/editions' && method === 'GET') {
    const aud = url.searchParams.get('audience');
    return [200, {editions: aud ? hub.editions.filter(e => e.audience_id === aud && e.status === url.searchParams.get('status')) : hub.editions, next_before: null, schedule_problems: hub.scheduleProblems}];
  }
  if (p === '/api/v2/editions') return [200, {id: payload.audience_id === 'a-inv' && payload.author === 'company' ? 'e-failed' : 'e-ready'}];
  if ((m = /^\/api\/v2\/editions\/([^/]+)\/notices$/.exec(p))) return [200, {readers: {pending: 1, sent: 2, failed: 1, unavailable: 0, cancelled: 0}, issuer: {delivered: 2, failed: 1, limited: 0}, email_available: hub.emailAvailable}];
  if ((m = /^\/api\/v2\/editions\/([^/]+)\/publish$/.exec(p))) { const e = hub.editions.find(x => x.id === m[1]); Object.assign(e, {status: 'published', published_at: '2026-11-01'}); return [200, {...e, release_failed: hub.releaseFails}]; }
  if ((m = /^\/api\/v2\/editions\/([^/]+)$/.exec(p)) && method === 'GET') {
    const e = hub.editions.find(x => x.id === m[1]);
    return e ? [200, e] : [404, {error: {code: 'not_found', detail: 'no'}}];
  }
  if ((m = /^\/api\/v2\/editions\/([^/]+)$/.exec(p)) && method === 'PATCH') { Object.assign(hub.editions.find(x => x.id === m[1]), payload); return [200, {}]; }
  if ((m = /^\/api\/v2\/editions\/([^/]+)\/retract$/.exec(p))) { Object.assign(hub.editions.find(x => x.id === m[1]), {status: 'retracted', retraction_note: payload.note}); return [200, {}]; }
  if ((m = /^\/api\/v2\/editions\/([^/]+)\/override$/.exec(p))) { Object.assign(hub.editions.find(x => x.id === m[1]), {status: 'ready', override_note: payload.note}); return [200, {}]; }
  if ((m = /^\/api\/v2\/approvals\/([^/]+)$/.exec(p))) { const e = hub.editions.find(x => x.approval?.id === m[1]); if (e) { e.approval.state = payload.decision; e.approval.note = payload.note || ''; } return [200, {}]; }
  if (p === '/api/v2/company-page/stats' && method === 'GET') return [200, {stats: hub.stats, next_offset: null, available: AVAILABLE}];
  if (p === '/api/v2/company-page/stats') { const row = {id: 's9', label: 'Pipeline', data_class: 'kpi', position: 3, ...payload}; hub.stats.push(row); return [200, row]; }
  if (p === '/api/v2/readers' && method === 'GET') return [200, {readers: hub.readers, next: null, sign_in: hub.signIn, email_available: hub.emailAvailable}];
  if ((m = /^\/api\/v2\/readers\/([^/]+)$/.exec(p)) && method === 'PATCH') {
    const r = hub.readers.find(x => x.id === m[1]), {audience_ids, ...rest} = payload;
    Object.assign(r, rest, {audiences: AUDIENCES.filter(a => audience_ids.includes(a.id)).map(({id, name, kind}) => ({id, name, kind}))});
    return [200, r];
  }
  if ((m = /^\/api\/v2\/readers\/([^/]+)$/.exec(p)) && method === 'DELETE') { hub.readers = hub.readers.filter(r => r.id !== m[1]); return [200, {}]; }
  return null;
}
async function open(browser, {viewer = 'ana', viewport = DESKTOP, hash = '#/company-page', theme, hubOpts = {}, ready = true} = {}) {
  const hub = fakeHub(hubOpts);
  if (hubOpts.denied) hub.denied = true;
  const person = people.find(x => x.id === viewer);
  const me = {id: viewer, name: person.name, email: person.email, cloud: true, ...ME[viewer]};
  const config = {company_name: 'Acme'};
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE, colorScheme: 'dark'});
  if (theme) await context.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const page = await context.newPage();
  const errors = [], natives = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { natives.push(d.message()); void d.dismiss(); });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const payload = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') hub.writes.push([method, p, payload]);
    else if (/company-page|audiences|editions|readers/.test(p)) hub.reads.push(p);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json({error: {code: 'off', detail: 'off'}}, 404);
    const a = answer(hub, method, p, url, payload);
    if (a) return json(a[1], a[0]);
    return json(method === 'GET' ? unrouted(p) : {});
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  if (ready) await page.locator('#cp-readers .cp-sec-ready, #cp-readers header').first().waitFor();
  return {page, hub, errors, natives, close: () => context.close()};
}
const loaded = page => page.waitForFunction(() => !document.querySelector('#cp-page .skel'));
const flat = async (page, what) => {
  const info = await page.evaluate(() => {
    const w = document.documentElement.clientWidth, main = document.querySelector('#main');
    const wide = [...document.querySelectorAll('#main *, dialog[open] *')].filter(el => !el.closest('.scroll') && el.getBoundingClientRect().right > w + 1).slice(0, 6).map(el => `${el.tagName}.${el.className}`);
    return {sw: document.documentElement.scrollWidth, w, main: main.scrollWidth - main.clientWidth, wide};
  });
  assert.ok(info.sw <= info.w + 1 && info.main <= 1 && !info.wide.length, `${what}: no sideways scroll ${JSON.stringify(info)}`);
};
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  // Finite animations only (a running skeleton shimmer never finishes): a dialog is shot once it has come in.
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.waitForTimeout(200);
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: false});
}
// One part filling the shot: #main scrolls inside the window, so a full-page picture would cut it off.
async function shotPart(page, id, name) {
  if (!process.env.TICO_UI_SHOTS) return;
  await page.locator('#cp-' + id).scrollIntoViewIfNeeded();
  await page.evaluate(i => { const el = document.getElementById('cp-' + i); document.querySelector('#main').scrollTop += el.getBoundingClientRect().top - 12; }, id);
  await shot(page, name);
}

(async () => {
  const browser = await chromium.launch();

  // A member: no link, and the address says so without asking the server.
  {
    const {page, hub, errors, close} = await open(browser, {viewer: 'erin', ready: false});
    await page.locator('.cp-head').waitFor();
    await page.waitForSelector('a[data-nav="updates"]');
    assert.equal(await page.locator('a[data-nav="company-page"]').count(), 0, 'no link for a member');
    assert.match(await page.locator('#main').innerText(), /Owners and admins only/);
    assert.deepEqual(hub.reads, [], 'a member asks the server for nothing here');
    await shot(page, 'denied-1280');
    assert.deepEqual(errors, []);
    await close();
  }
  // An admin: the link shows, and a server that says no is the same line. The server is the one that decides.
  {
    const {page, close} = await open(browser, {viewer: 'cara', hubOpts: {denied: true}, ready: false});
    await page.locator('a[data-nav="company-page"]').waitFor();
    assert.match(await page.locator('a[data-nav="company-page"]').innerText(), /Company page/);
    await page.locator('#cp-page .empty-state').waitFor();
    assert.match(await page.locator('#cp-page').innerText(), /Owners and admins only/);
    await close();
  }

  // The owner, on a computer.
  {
    const {page, hub, errors, natives, close} = await open(browser);
    await loaded(page);
    assert.ok(await page.locator('a[data-nav="company-page"]').evaluate(a => a.classList.contains('cur')), 'the link is current');
    const parts = await page.locator('#cp-page > section').evaluateAll(s => s.map(x => x.id));
    assert.deepEqual(parts, ['cp-transparency', 'cp-editions', 'cp-audiences', 'cp-disclosure', 'cp-stats', 'cp-readers', 'cp-preview'], 'the parts, in order');

    // Transparency: Closed has no address; Open warns once and Cancel sends nothing.
    const level = v => page.locator(`[data-cp-level] [data-v="${v}"]`);
    assert.equal(await page.locator('.cp-address').count(), 0);
    await level('open').click();
    assert.match(await page.locator('dialog.cp-dlg').innerText(), /public internet/);
    await shot(page, 'transparency-open-warning-1280');
    await page.locator('dialog.cp-dlg [data-close]').last().click();
    assert.equal(hub.writes.length, 0, 'Cancel sends nothing');
    await level('open').click();
    await page.locator('dialog.cp-dlg button[type=submit]').click();
    await page.locator('.cp-address').waitFor();
    assert.deepEqual(hub.writes.at(-1), ['PATCH', '/api/v2/company-page', {transparency: 'open'}]);
    assert.equal(await level('open').getAttribute('aria-checked'), 'true');
    assert.match(await page.locator('.cp-address').innerText(), /open\.acme\.example/);
    // Open with nothing approved answers not found, so the address isn't a link until the server says the page is up.
    assert.equal(await page.locator('[data-cp-not-live]').count(), 1);
    assert.equal(await page.locator('a.cp-address').count(), 0);
    await shot(page, 'transparency-open-not-live-1280');
    hub.settings.public_live = true;
    await page.reload();
    await page.locator('a.cp-address').waitFor();
    assert.equal(await page.locator('[data-cp-not-live]').count(), 0);
    hub.settings.public_live = false;
    await page.reload();
    await page.locator('[data-cp-not-live]').waitFor();
    // Taking something away says what stops being visible, first.
    await level('private').click();
    assert.match(await page.locator('dialog.cp-dlg').innerText(), /public page comes down/);
    await page.locator('dialog.cp-dlg button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-cp-level] [data-v="private"]')?.getAttribute('aria-checked') === 'true');
    await level('closed').click();
    assert.match(await page.locator('dialog.cp-dlg').innerText(), /Readers lose access/);
    await page.locator('dialog.cp-dlg button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('[data-cp-level] [data-v="closed"]')?.getAttribute('aria-checked') === 'true');
    assert.equal(hub.settings.transparency, 'closed');
    // Closed to Private takes nothing away: no dialog.
    await level('private').click();
    await page.waitForFunction(() => document.querySelector('[data-cp-level] [data-v="private"]')?.getAttribute('aria-checked') === 'true');
    assert.equal(await page.locator('dialog.cp-dlg').count(), 0);

    // The queue: held first, its reason in the row; a text from a bot's update never runs.
    const rows = await page.locator('.cp-ed-row').evaluateAll(r => r.map(x => x.dataset.cpEdition));
    assert.deepEqual(rows, ['e-held', 'e-failed', 'e-approved', 'e-mine', 'e-other', 'e-declined', 'e-lapsed', 'e-stale', 'e-ready', 'e-pub', 'e-gone', 'e-dropped']);
    const held = page.locator('.cp-ed-row[data-cp-edition="e-held"]');
    assert.match(await held.innerText(), /can't see/);
    assert.match(await held.innerText(), /Held/);

    await held.click();
    const dlg = page.locator('dialog.cp-ed');
    await dlg.locator('.cp-read').waitFor();
    assert.match(await dlg.locator('.cp-checks').innerText(), /Names: Northwind/);
    assert.equal(await dlg.locator('.cp-read img, .cp-read script').count(), 0, 'no markup from the text');
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    assert.equal(await dlg.locator('[data-act="approve"]').count(), 0);
    // Override needs its note, and says what it does.
    await dlg.locator('[data-act="override"]').click();
    const over = page.locator('dialog.cp-dlg:not(.cp-ed)');
    assert.match(await over.innerText(), /note is kept/);
    await over.locator('button[type=submit]').click();
    assert.match(await over.locator('[data-error]').innerText(), /note is required/);
    assert.equal(hub.writes.filter(w => w[1].endsWith('/override')).length, 0, 'no note, no override');
    await over.locator('textarea').fill('Northwind agreed to be named');
    await over.locator('button[type=submit]').click();
    await over.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes.at(-1), ['POST', '/api/v2/editions/e-held/override', {note: 'Northwind agreed to be named'}]);
    await dlg.locator('[data-act="request"]').waitFor();
    assert.match(await dlg.locator('[data-ed-body]').innerText(), /Northwind agreed to be named/);
    await dlg.locator('[data-close]').click();

    // A draft that failed says so, offers drafting again and has no Override: there is nothing to override.
    assert.match(await page.locator('.cp-ed-row[data-cp-edition="e-failed"]').innerText(), /Draft failed/);
    await page.locator('.cp-ed-row[data-cp-edition="e-failed"]').click();
    await dlg.locator('[data-act="redraft"]').waitFor();
    assert.equal(await dlg.locator('[data-act="override"]').count(), 0);
    assert.match(await dlg.locator('.cp-checks').innerText(), /No model to run this/);
    await shot(page, 'edition-failed-draft-1280');
    await dlg.locator('[data-act="redraft"]').click();
    await page.waitForFunction(() => document.querySelector('dialog.cp-ed [data-act]')?.disabled === false);
    assert.deepEqual(hub.writes.at(-1), ['POST', '/api/v2/editions', {audience_id: 'a-inv', author: 'company', period_start: '2026-10-01', period_end: '2026-10-31'}]);
    await dlg.locator('[data-close]').click();

    // Every way an approval can stand reads in the row and on the edition, and the way on is the primary button.
    assert.match(await page.locator('.cp-ed-row[data-cp-edition="e-approved"]').innerText(), /Publish next/);
    assert.match(await page.locator('.cp-ed-row[data-cp-edition="e-declined"]').innerText(), /Declined by Cara Lee/);
    assert.match(await page.locator('.cp-ed-row[data-cp-edition="e-lapsed"]').innerText(), /Approval lapsed/);
    assert.match(await page.locator('.cp-ed-row[data-cp-edition="e-stale"]').innerText(), /Changed since asked/);
    for (const [id, words, act] of [['e-declined', /Too much on churn/, 'request'], ['e-lapsed', /lapsed/, 'request'], ['e-stale', /changed after/, 'request'], ['e-approved', /Approved by Cara Lee/, 'publish']]) {
      await page.locator(`.cp-ed-row[data-cp-edition="${id}"]`).click();
      await dlg.locator(`.cp-ed-acts .primary[data-act="${act}"]`).waitFor();
      assert.match(await dlg.locator('[data-ed-body]').innerText(), words);
      await shot(page, `edition-${id}-1280`);
      await dlg.locator('[data-close]').click();
    }
    // A retracted edition that went out has a way back; one discarded before it went out doesn't.
    await page.locator('.cp-ed-row[data-cp-edition="e-gone"]').click();
    await dlg.locator('[data-act="correct"]').waitFor();
    await dlg.locator('[data-close]').click();
    await page.locator('.cp-ed-row[data-cp-edition="e-dropped"]').click();
    await dlg.locator('.cp-read').waitFor();
    assert.equal(await dlg.locator('[data-act]').count(), 0);
    await dlg.locator('[data-close]').click();

    // Approve: for the addressed person only, through the approvals API.
    await page.locator('.cp-ed-row[data-cp-edition="e-other"]').click();
    await dlg.locator('.cp-read').waitFor();
    assert.equal(await dlg.locator('[data-act="approve"]').count(), 0, 'addressed to someone else');
    assert.match(await dlg.locator('[data-ed-body]').innerText(), /Waiting on Cara Lee/);
    await dlg.locator('[data-close]').click();
    await page.locator('.cp-ed-row[data-cp-edition="e-mine"]').click();
    await dlg.locator('.cp-read').waitFor();
    assert.match(await dlg.locator('.cp-read').innerText(), /Revenue grew 12% in October/);
    await shot(page, 'edition-approve-1280');
    await dlg.locator('[data-act="approve"]').click();
    await dlg.locator('[data-act="publish"]').waitFor();
    assert.deepEqual(hub.writes.at(-1), ['POST', '/api/v2/approvals/ap-1', {decision: 'approved'}]);
    assert.equal(await dlg.locator('[data-act="approve"]').count(), 0);
    assert.match(await dlg.locator('[data-ed-body]').innerText(), /Approved by Ana Silva\. Publish is next/);
    // Publishing is its own step, and says what it does at this page's level.
    await dlg.locator('[data-act="publish"]').click();
    const publish = page.locator('dialog.cp-dlg:not(.cp-ed)');
    assert.match(await publish.innerText(), /get an email with a link/);
    await shot(page, 'edition-publish-1280');
    await publish.locator('button[type=submit]').click();
    await publish.waitFor({state: 'detached'});
    await dlg.locator('[data-cp-notices]').waitFor();
    assert.match(await dlg.locator('[data-cp-notices]').innerText(), /2 sent · 1 waiting · 1 failed/);
    assert.equal(await page.locator('[data-cp-release-failed]').count(), 0);
    await dlg.locator('[data-close]').click();

    // Published: Retract says what it does first; Seen by for a private edition.
    await page.locator('.cp-ed-row[data-cp-edition="e-pub"]').click();
    await dlg.locator('.cp-read').waitFor();
    assert.match(await dlg.locator('.cp-seen').innerText(), /rita@acme\.example/);
    await dlg.locator('[data-act="retract"]').click();
    assert.match(await page.locator('dialog.cp-dlg:not(.cp-ed)').innerText(), /can't be recalled/);
    await page.locator('dialog.cp-dlg:not(.cp-ed) button[type=submit]').click();
    assert.match(await page.locator('dialog.cp-dlg:not(.cp-ed) [data-error]').innerText(), /note is required/);
    await page.locator('dialog.cp-dlg:not(.cp-ed) [data-close]').last().click();
    assert.equal(hub.writes.filter(w => w[1].endsWith('/retract')).length, 0);
    await shot(page, 'edition-published-1280');
    await dlg.locator('[data-close]').click();
    await page.locator('.cp-ed-row[data-cp-edition="e-ready"]').click();
    await dlg.locator('.cp-read').waitFor();
    await dlg.locator('[data-act="request"]').waitFor();
    assert.equal(await dlg.locator('.cp-seen').count(), 0, 'nobody to name on a public edition');
    assert.doesNotMatch(await dlg.locator('.cp-checks').innerText(), /older run/, 'the last run of a check, not every run');
    // An edit sends one item a line, and an item whose words stay keeps its ask.
    await dlg.locator('[data-act="edit"]').click();
    const editor = page.locator('dialog.cp-dlg:not(.cp-ed)');
    await editor.locator('[name="s-highlights"]').fill('One thing.\nAnother thing.');
    await editor.locator('button[type=submit]').click();
    await editor.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes.at(-1), ['PATCH', '/api/v2/editions/e-ready', {title: 'October, public', sections: [
      {key: 'highlights', items: [{body: 'One thing.', ask_id: null}, {body: 'Another thing.', ask_id: null}]}, {key: 'asks', items: [{body: 'Intros to two design partners.', ask_id: 'ask-1'}]}]}]);
    // An edition nobody has seen has a way out too.
    await dlg.locator('[data-act="discard"]').click();
    const discard = page.locator('dialog.cp-dlg:not(.cp-ed)');
    await discard.locator('[name=note]').fill('Not sending this one');
    await discard.locator('button[type=submit]').click();
    await discard.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes.at(-1), ['POST', '/api/v2/editions/e-ready/retract', {note: 'Not sending this one'}]);
    await dlg.locator('[data-close]').click();

    // Disclosure on a computer: every audience side by side. A rule change sends that cell.
    assert.equal(await page.locator('.cp-table thead th[scope=col]').count(), 3);
    const cell = page.locator('select[data-cp-mode][data-aud="a-inv"][data-class="headcount"]');
    assert.equal(await cell.inputValue(), 'hidden', 'no rule is Hidden');
    await cell.selectOption('delayed');
    await page.waitForFunction(() => document.querySelector('[data-cp-lag][data-aud="a-inv"][data-class="headcount"]')?.closest('.cp-lag') && !document.querySelector('[data-cp-lag][data-aud="a-inv"][data-class="headcount"]').closest('.cp-lag').hidden);
    assert.deepEqual(hub.writes.find(w => w[1] === '/api/v2/audiences/a-inv/disclosure')[2], {rules: [{data_class: 'headcount', ref_id: '', mode: 'delayed', lag_days: 30}]});
    // A KPI on the board has a row of its own, filed under kpi, and reads its class's rule until it has one.
    await page.locator('[data-cp-group="kpi"]').click();
    const kpiCell = page.locator('select[data-cp-mode][data-aud="a-inv"][data-class="kpi"][data-ref="k1"]');
    await kpiCell.waitFor();
    assert.equal(await kpiCell.inputValue(), 'show', 'its class says Show');
    // "Whole quarters only" stays when the mode changes.
    await page.locator('select[data-cp-mode][data-aud="a-inv"][data-class="revenue"]').selectOption('range');
    await page.waitForFunction(() => document.querySelector('select[data-cp-mode][data-aud="a-inv"][data-class="revenue"]')?.value === 'range');
    assert.deepEqual(hub.writes.at(-1)[2], {rules: [{data_class: 'revenue', ref_id: '', mode: 'range', lag_days: 0, rounding: 'quarterly'}]});

    // A stat comes from the KPIs and Finance's numbers; the server fills in its class and words.
    await page.locator('[data-cp-add-stat]').click();
    const addStat = page.locator('dialog.cp-dlg');
    assert.equal(await addStat.locator('optgroup[label="P&L lines"] option').count(), 1, 'P&L lines can be picked');
    await addStat.locator('select[name=stat]').selectOption('kpi|k2');
    assert.equal(await addStat.locator('option[value="kpi|k1"]').count(), 0, 'what is on the board is not offered');
    await addStat.locator('button[type=submit]').click();
    await addStat.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes.at(-1), ['POST', '/api/v2/company-page/stats', {source_kind: 'kpi', ref_id: 'k2', chart: 'number', position: 3}]);

    // A held stat says why, in words.
    assert.match(await page.locator('[data-cp-held="s3"]').innerText(), /would show cost of sales/);
    assert.match(await page.locator('[data-cp-tile="s2"]').innerText(), /Out of date/);
    assert.equal(await page.locator('[data-cp-stats-request]').count(), 1);
    assert.match(await page.locator('[data-cp-held="s4"]').innerText(), /Hidden/);

    // Editing a reader replaces their audiences, and can turn their emails off.
    await page.locator('[data-cp-reader-edit="r1"]').click();
    const readerEdit = page.locator('dialog.cp-dlg');
    await readerEdit.locator('input[name=audience][value="a-board"]').uncheck();
    await readerEdit.locator('input[name=notify]').uncheck();
    await shot(page, 'reader-edit-1280');
    await readerEdit.locator('button[type=submit]').click();
    await readerEdit.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes.at(-1), ['PATCH', '/api/v2/readers/r1', {name: 'Rita Vale', notify: false, audience_ids: ['a-inv']}]);
    await page.waitForFunction(() => /Emails off/.test(document.querySelector('[data-cp-reader="r1"]')?.innerText || ''));

    // Removing a reader states the rule, and only the second click removes.
    await page.locator('[data-cp-reader-remove="r1"]').click();
    assert.match(await page.locator('dialog.cp-dlg').innerText(), /Emails already sent can't be recalled/);
    assert.equal(hub.readers.length, 2);
    await shot(page, 'reader-remove-1280');
    await page.locator('dialog.cp-dlg button[type=submit]').click();
    await page.locator('[data-cp-reader="r1"]').waitFor({state: 'detached'});
    assert.equal(hub.readers.length, 1);

    // Preview opens the server's page.
    assert.match(await page.locator('[data-cp-preview="a-inv"]').getAttribute('href'), /\/api\/v2\/company-page\/preview\?audience=a-inv$/);

    assert.deepEqual(natives, [], 'no native dialogs');
    assert.deepEqual(errors, []);
    await close();
  }

  // A part that fails shows Retry, and the others still draw.
  {
    const {page, hub, errors, close} = await open(browser, {hubOpts: {fail: ['/api/v2/editions']}});
    await page.locator('#cp-editions [data-cp-retry]').waitFor();
    await page.locator('#cp-readers .cp-reader').first().waitFor();
    await shot(page, 'error-editions-1280');
    hub.fail.clear();
    await page.locator('#cp-editions [data-cp-retry]').click();
    await page.locator('.cp-ed-row').first().waitFor();
    assert.deepEqual(errors, []);
    await close();
  }
  // Nothing yet: one short line and the action. No sign-in provider: Private is off and readers can't be added.
  {
    const {page, close} = await open(browser, {hubOpts: {noEditions: true, privateOn: false}});
    await loaded(page);
    assert.match(await page.locator('#cp-editions .empty-state').innerText(), /No editions yet/);
    await page.locator('#cp-editions .empty-state [data-cp-draft]').waitFor();
    await page.locator('#cp-stats .empty-state [data-cp-add-stat]').waitFor();
    assert.ok(await page.locator('[data-cp-level] [data-v="private"]').isDisabled(), 'Private is off');
    assert.match(await page.locator('#cp-transparency').innerText(), /No sign-in provider/);
    assert.match(await page.locator('#cp-readers').innerText(), /No sign-in provider/);
    assert.equal(await page.locator('[data-cp-add-reader]').count(), 0);
    await shot(page, 'empty-no-sign-in-1280');
    await close();
  }
  // No reader address on this install: the address is set here, under the page domain the server names, and choosing a
  // level with none asks for one first.
  {
    const {page, hub, close} = await open(browser, {hubOpts: {noSlug: true}});
    await loaded(page);
    await page.locator('[data-cp-level] [data-v="private"]').click();
    const dlg = page.locator('dialog.cp-dlg');
    assert.match(await dlg.innerText(), /\.pages\.acme\.example/);
    await shot(page, 'address-1280');
    await dlg.locator('[name=slug]').fill('acme');
    await dlg.locator('button[type=submit]').click();
    await dlg.waitFor({state: 'detached'});
    assert.deepEqual(hub.writes[0], ['PATCH', '/api/v2/company-page', {slug: 'acme'}]);
    await page.waitForFunction(() => document.querySelector('[data-cp-level] [data-v="private"]')?.getAttribute('aria-checked') === 'true');
    assert.deepEqual(hub.writes[1], ['PATCH', '/api/v2/company-page', {transparency: 'private'}]);
    assert.match(await page.locator('[data-cp-address]').innerText(), /Change address/);
    await close();
  }
  // No reader origin and no page domain: nowhere to publish to, so only Closed can be chosen.
  {
    const {page, close} = await open(browser, {hubOpts: {noDomain: true}});
    await loaded(page);
    assert.ok(await page.locator('[data-cp-level] [data-v="open"]').isDisabled());
    assert.ok(await page.locator('[data-cp-level] [data-v="private"]').isDisabled());
    await page.locator('[data-cp-no-address]').waitFor();
    assert.equal(await page.locator('[data-cp-address]').count(), 0);
    await shot(page, 'no-address-1280');
    await close();
  }
  // What the owner has to know: a stopped schedule, no email on this install, a publish the page didn't take, and how many
  // updates a tightened rule can't reach, with the way to them.
  {
    const {page, hub, errors, close} = await open(browser, {hubOpts: {noEmail: true, releaseFails: true, tightened: 3, scheduleProblems: [{audience_id: 'a-inv', name: 'Investors', problem: 'due_rule'}]}});
    await loaded(page);
    assert.match(await page.locator('[data-cp-stopped="a-inv"]').innerText(), /Investors: schedule stopped/);
    await page.locator('[data-cp-email-off]').waitFor();
    await page.locator('.cp-ed-row[data-cp-edition="e-approved"]').click();
    const dlg = page.locator('dialog.cp-ed');
    await dlg.locator('[data-act="publish"]').click();
    const publish = page.locator('dialog.cp-dlg:not(.cp-ed)');
    assert.match(await publish.innerText(), /Nobody sees it until the page is Private or Open/);
    await publish.locator('button[type=submit]').click();
    await publish.waitFor({state: 'detached'});
    await page.locator('[data-cp-release-failed]').waitFor();
    assert.match(await dlg.locator('[data-ed-body]').innerText(), /failed to rebuild/);
    await shot(page, 'release-failed-1280');
    await dlg.locator('[data-close]').click();
    await page.locator('select[data-cp-mode][data-aud="a-board"][data-class="revenue"]').selectOption('hidden');
    await page.locator('[data-cp-tightened] .cp-alert').waitFor();
    assert.match(await page.locator('[data-cp-tightened]').innerText(), /3 published updates may mention this/);
    await page.locator('#cp-disclosure').scrollIntoViewIfNeeded();
    await shot(page, 'disclosure-tightened-1280');
    await page.locator('[data-cp-tightened] [data-cp-editions-of]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.cp-ed-row')].every(r => r.dataset.cpEdition === 'e-pub'));
    assert.match(await page.locator('#cp-editions').innerText(), /Published to Board/);
    await page.locator('#cp-editions [data-cp-editions-of=""]').click();
    await page.waitForFunction(() => document.querySelectorAll('.cp-ed-row').length > 1);
    assert.deepEqual(errors, []);
    assert.ok(hub.writes.length);
    await close();
  }

  // A phone: nothing scrolls sideways, and Disclosure shows one audience at a time.
  {
    const {page, hub, errors, natives, close} = await open(browser, {viewport: PHONE});
    await loaded(page);
    await flat(page, 'the page');
    assert.equal(await page.locator('.cp-table thead').count(), 0);
    assert.deepEqual(await page.locator('select[data-cp-mode][data-class="revenue"]').evaluateAll(s => s.map(x => x.dataset.aud)), ['a-board']);
    await page.locator('[data-cp-pick="disclosure"]').selectOption('a-inv');
    await page.waitForFunction(() => document.querySelector('select[data-cp-mode][data-class="revenue"]')?.dataset.aud === 'a-inv');
    assert.equal(await page.locator('select[data-cp-mode][data-class="revenue"]').inputValue(), 'rounded');
    await flat(page, 'another audience');
    await page.locator('[data-cp-group="kpi"]').click();
    await page.locator('select[data-cp-mode][data-class="kpi"][data-ref="k1"]').waitFor();
    await page.locator('select[data-cp-mode][data-class="headcount"]').selectOption('delayed');
    await flat(page, 'a delayed cell');
    for (const id of ['e-held', 'e-mine']) {
      await page.locator(`.cp-ed-row[data-cp-edition="${id}"]`).click();
      await page.locator('dialog.cp-ed .cp-read').waitFor();
      await flat(page, 'an edition ' + id);
      await page.locator('dialog.cp-ed [data-close]').click();
    }
    assert.ok(hub.reads.length);
    assert.deepEqual(natives, []);
    assert.deepEqual(errors, []);
    await close();
  }

  // Screenshots of every part, wide and narrow, light and dark.
  if (process.env.TICO_UI_SHOTS) {
    for (const [viewport, w] of [[DESKTOP, 1280], [PHONE, 390]]) for (const theme of ['dark', 'light']) {
      const {page, close} = await open(browser, {viewport, theme});
      await loaded(page);
      await page.evaluate(() => { document.querySelector('[data-cp-level] [data-v="private"]').click(); });
      await page.waitForFunction(() => document.querySelector('[data-cp-level] [data-v="private"]')?.getAttribute('aria-checked') === 'true');
      for (const [id] of [['transparency'], ['editions'], ['audiences'], ['disclosure'], ['stats'], ['readers'], ['preview']]) await shotPart(page, id, `${id}-${w}-${theme}`);
      for (const id of ['e-held', 'e-mine']) {
        await page.locator(`.cp-ed-row[data-cp-edition="${id}"]`).click();
        await page.locator('dialog.cp-ed .cp-read').waitFor();
        await shot(page, `edition-${id}-${w}-${theme}`);
        await page.locator('dialog.cp-ed [data-close]').click();
      }
      await page.locator('[data-cp-audience="a-inv"]').click();
      await shot(page, `audience-edit-${w}-${theme}`);
      await page.locator('dialog.cp-dlg [data-close]').first().click();
      const open1 = async (selector, name) => { await page.locator(selector).click(); await page.locator('dialog.cp-dlg').first().waitFor(); await shot(page, `${name}-${w}-${theme}`); await page.locator('dialog.cp-dlg [data-close]').first().click(); await page.locator('dialog.cp-dlg').first().waitFor({state: 'detached'}); };
      await open1('[data-cp-draft]', 'draft-now');
      await open1('[data-cp-add-stat]', 'add-stat');
      await open1('[data-cp-add-reader]', 'add-reader');
      await open1('[data-cp-reader-edit="r1"]', 'reader-edit');
      await open1('[data-cp-reader-remove="r1"]', 'reader-remove');
      await open1('[data-cp-import]', 'import');
      await open1('[data-cp-level] [data-v="open"]', 'go-public');
      for (const id of ['e-approved', 'e-declined', 'e-failed', 'e-pub', 'e-gone']) {
        await page.locator(`.cp-ed-row[data-cp-edition="${id}"]`).click();
        await page.locator('dialog.cp-ed .cp-read').waitFor();
        await shot(page, `edition-${id}-${w}-${theme}`);
        await page.locator('dialog.cp-ed [data-close]').click();
      }
      await close();
      const failed = await open(browser, {viewport, theme, hubOpts: {fail: ['/api/v2/editions'], noEmail: true}});
      await failed.page.locator('#cp-editions [data-cp-retry]').waitFor();
      await shotPart(failed.page, 'editions', `error-editions-${w}-${theme}`);
      await shotPart(failed.page, 'readers', `readers-email-off-${w}-${theme}`);
      await failed.close();
    }
  }
  await browser.close();
  console.log('company-page: ok');
})().catch(e => { console.error(e); process.exit(1); });
