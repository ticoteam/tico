// Strategy › Budget (ui/app/strategy-budget.js) against a fake hub: the org chart as one tree of budgets. A small team
// (fixtures/redesign-org.json with redesign-usage-limits.json and redesign-task-caps.json, the Acme example): the rows in
// chart order, each with the window that binds, Paused and a caps chip; a group's pool against what is under it; search
// keeping what a row sits in; Near limit; the editor as a panel (a sheet on a phone) whose save sends only what changed,
// task caps both ways, and read only for whoever may not edit. Then Acme at 5 people and 200 bots in nested groups:
// folded by default, quick to draw, expand and collapse all, search and Near limit. TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const WINDOWS = {per_30m_usd: '30m', hourly_usd: 'hour', daily_usd: 'day', weekly_usd: 'week', monthly_usd: 'month'};

// ----------------------------------------------------------------- the teams
function small() {
  const fx = fixture('redesign-org.json');
  return {people: fx.people, emps: fx.employees, groups: fx.groups, orgGroups: fx.org_groups,
    limits: fixture('redesign-usage-limits.json'), caps: fixture('redesign-task-caps.json').caps};
}

// Acme at the target size: 5 people, 16 groups (5 on top, 11 inside them), a lead bot in each and 184 more under the leads,
// 200 bots, plus the helpers. Every bot has a day limit of $10; a few are near it and a few over it (paused).
function big() {
  const people = [['ana', 'Ana Silva', ''], ['ben', 'Ben Ortiz', 'sales'], ['cara', 'Cara Lee', 'eng'],
    ['dana', 'Dana Kim', 'mkt'], ['eli', 'Eli Park', 'support']].map(([id, name, team]) =>
    ({id, name, role: id === 'ana' ? 'owner' : 'member', org_parent: id === 'ana' ? '' : 'p:ana', team}));
  const tops = [['eng', 'Engineering', 'cara', 'Engineer'], ['sales', 'Sales', 'ben', 'Account Exec'],
    ['mkt', 'Marketing', 'dana', 'Marketer'], ['support', 'Support', 'eli', 'Support Lead'], ['ops', 'Operations', 'ana', 'Ops Analyst']];
  const inner = [['platform', 'Platform', 'eng', 'Platform Engineer'], ['web', 'Web', 'eng', 'Web Developer'],
    ['mobile', 'Mobile', 'eng', 'Mobile Developer'], ['data', 'Data', 'eng', 'Data Analyst'], ['outbound', 'Outbound', 'sales', 'SDR'],
    ['partners', 'Partners', 'sales', 'Partner Manager'], ['content', 'Content', 'mkt', 'Writer'], ['growth', 'Growth', 'mkt', 'Growth Analyst'],
    ['tier1', 'Tier 1', 'support', 'Support Agent'], ['tier2', 'Tier 2', 'support', 'Support Engineer'], ['finance', 'Finance', 'ops', 'Bookkeeper']];
  const all = [...tops.map(([id, name, lead, role], order) => ({id, name, parent: '', person: lead, role, order})),
    ...inner.map(([id, name, parent, role], order) => ({id, name, parent, role, order}))];
  const emps = [], bots = {};
  const spend = (slug, i) => {
    const day = i % 41 === 7 ? 10.5 : i % 23 === 5 ? 9 : ((i * 37) % 60) / 10;
    bots[slug] = {...none(), daily_usd: 10, own_daily_usd: 10, monthly_usd: 200, own_monthly_usd: 200, source: 'bot',
      day_spent: day, month_spent: day * 12, percent: Math.round(day * 10), blocked: day >= 10 ? 'daily' : null,
      blocked_by: day >= 10 ? {scope: 'bot', period: 'daily'} : null, may_edit: true,
      spent: {'30m': Math.round(day * 10) / 100, hour: Math.round(day * 20) / 100, day, week: day * 4, month: day * 12}};
  };
  let n = 0;
  const per = Math.ceil(184 / all.length);
  for (const g of all) {
    const lead = g.id + '-lead', boss = g.parent ? 'b:' + g.parent + '-lead' : 'p:' + g.person;
    emps.push({name: lead, display_name: g.name + ' Lead', role: 'Head of ' + g.name, org_parent: boss, team: g.id, status: 'active', host: 'keeper', can_chat: true});
    spend(lead, n++);
    g.bots = [lead];
    for (let k = 1; k <= per && emps.length < 200; k++) {
      const slug = `${g.id}-${String(k).padStart(2, '0')}`;
      emps.push({name: slug, display_name: `${g.role} ${String(k).padStart(2, '0')}`, role: g.role, org_parent: 'b:' + lead, team: g.id, status: 'active', host: 'keeper', can_chat: true});
      spend(slug, n++);
      g.bots.push(slug);
    }
  }
  for (const [name, display_name] of [['coo', 'Assistant'], ['botops', 'BotOps'], ['librarian', 'Librarian']]) {
    emps.push({name, display_name, org_parent: '', status: 'active', host: 'keeper', can_chat: true});
    spend(name, n++);
  }
  const groups = all.map(g => ({id: g.id, name: g.name, parent: g.parent, lead: g.person ? 'person:' + g.person : 'bot:' + g.id + '-lead',
    people: people.filter(p => p.team === g.id).map(p => p.id), bots: g.bots, order: g.order, bet_id: null}));
  const orgGroups = all.map(g => ({id: g.id, name: g.name, parent: g.parent, org_parent: '', order: g.order}));
  // A group's spend is everyone's in it and the groups inside it.
  const under = gid => [gid, ...all.filter(g => g.parent === gid).flatMap(g => under(g.id))];
  const pool = (cap, slugs) => {
    const spent = {};
    for (const s of slugs) for (const [w, v] of Object.entries(bots[s].spent)) spent[w] = (spent[w] || 0) + v;
    return {per_30m_usd: null, daily_usd: cap, weekly_usd: null, monthly_usd: null, '30m_spent': null, day_spent: cap == null ? null : spent.day,
      week_spent: null, month_spent: null, blocked: null, spent};
  };
  const caps = {eng: 600, sales: 300, mkt: 300, support: 300, ops: 100};
  const pools = {company: pool(1500, Object.keys(bots)), groups: Object.fromEntries(all.map(g => [g.id,
    {name: g.name, parent: g.parent || null, ...pool(caps[g.id] ?? null, emps.filter(e => under(g.id).includes(e.team)).map(e => e.name))}]))};
  const capRows = [...tops.map(([id, , person]) => ({sender: 'bot:' + id + '-lead', receiver: 'person:' + person, per_day: 10, count: 4, closed: 0, rejected: 0})),
    ...inner.map(([id]) => ({sender: 'bot:' + id + '-01', receiver: 'bot:' + id + '-lead', per_day: 3, count: 3, closed: 0, rejected: 1}))];
  return {people, emps, groups, orgGroups, caps: capRows,
    limits: {default: {per_30m_usd: null, daily_usd: null, weekly_usd: null, monthly_usd: null}, may_edit_default: true, leads: [], pools, bots}};
}
function none() {
  return Object.fromEntries(Object.keys(WINDOWS).filter(k => k !== 'hourly_usd').flatMap(k => [[k, null], ['own_' + k, null]]));
}

// What GET /api/v2/usage/limits answers this viewer (backend/usage.py limits): the owner everything; a person who leads
// groups the bots and pools of those groups and the groups inside them, each editable, listed in `leads`; anyone else
// the bots it runs (none here).
function limitsFor(team, viewer) {
  if (viewer === 'ana') return team.limits;
  const led = new Set();
  const add = gid => { led.add(gid); team.groups.filter(g => g.parent === gid).forEach(g => add(g.id)); };
  team.groups.filter(g => g.lead === 'person:' + viewer).forEach(g => add(g.id));
  const bots = Object.fromEntries(Object.entries(team.limits.bots)
    .filter(([slug]) => led.has(team.emps.find(e => e.name === slug)?.team)).map(([slug, row]) => [slug, {...row, may_edit: true}]));
  const groups = Object.fromEntries(Object.entries(team.limits.pools?.groups || {}).filter(([gid]) => led.has(gid)).map(([gid, row]) => [gid, {...row, may_edit: true}]));
  return {default: team.limits.default, may_edit_default: false, leads: [...led].sort(), bots,
    pools: led.size ? {company: null, groups} : null};
}

// ----------------------------------------------------------------- the fake hub
async function open(browser, {team = small(), viewport = DESKTOP, viewer = 'ana', hash = '#/strategy/budget', capsStatus = 200} = {}) {
  const person = team.people.find(p => p.id === viewer);
  const me = {id: viewer, name: person.name, email: viewer + '@acme.example', role: viewer === 'ana' ? 'owner' : 'member',
    company_role: viewer === 'ana' ? 'owner' : 'member', cloud: true};
  const config = {company_name: 'Acme'};
  const writes = [];
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') writes.push([method, p + url.search, body]);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(team.emps);
    if (p === '/api/humans') return json({people: team.people, org_groups: team.orgGroups});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/groups') return json(team.groups);
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    // Spend (CONTRACT §1): a write lands, so the reload after a save reads it back.
    if (p === '/api/v2/usage/limits' && method === 'GET') return json(limitsFor(team, viewer));
    if (p === '/api/v2/usage/limits') { Object.assign(team.limits.default, body); return json(team.limits.default); }
    let m = p.match(/^\/api\/v2\/usage\/limits\/([^/]+)$/);
    if (m) {
      const row = team.limits.bots[decodeURIComponent(m[1])];
      for (const [k, v] of Object.entries(body)) { row[k] = v; row['own_' + k] = v; }
      return json(row);
    }
    if (p === '/api/v2/usage/pools/company') { Object.assign(team.limits.pools.company, body); return json(team.limits.pools.company); }
    m = p.match(/^\/api\/v2\/usage\/pools\/groups\/([^/]+)$/);
    if (m) { const row = team.limits.pools.groups[decodeURIComponent(m[1])]; Object.assign(row, body); return json(row); }
    // Task caps (CONTRACT §1).
    if (p === '/api/v2/task-caps') {
      if (method === 'GET') return capsStatus === 200 ? json({caps: team.caps}) : json({error: {code: 'not_found', detail: 'Not found'}}, capsStatus);
      const sender = body?.sender || url.searchParams.get('sender'), receiver = body?.receiver || url.searchParams.get('receiver');
      const at = team.caps.findIndex(c => c.sender === sender && c.receiver === receiver);
      if (method === 'DELETE') { if (at >= 0) team.caps.splice(at, 1); return json({ok: true}); }
      if (at >= 0) team.caps[at].per_day = body.per_day; else team.caps.push({...body, count: 0, closed: 0, rejected: 0});
      return json(body);
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('[data-bt-open="company"]').waitFor();
  return {page, errors, writes, team};
}

// ----------------------------------------------------------------- helpers
const row = key => `.bt-r:has([data-bt-open="${key}"])`;
const amount = (page, key) => page.locator(row(key) + ' .bt-amt').innerText();
const keys = page => page.locator('[data-bt-open]').evaluateAll(els => els.map(el => el.dataset.btOpen));
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}
async function edit(page, key) {
  await page.locator(`[data-bt-open="${key}"]`).click();
  await page.locator('dialog.bt-panel[open]').waitFor();
}
async function save(page) {
  await page.click('dialog.bt-panel[open] [data-save]');
  await page.locator('dialog.bt-panel').waitFor({state: 'detached'});
}
// Every row is one line, and nothing in the open editor runs past its edge.
const oneLine = page => page.locator('.bt-main').evaluateAll(els => els.every(el => el.getBoundingClientRect().height <= 48));
const editorFits = page => page.locator('dialog.bt-panel[open]').evaluate(d => [...d.querySelectorAll('.bt-win,.bt-cap,.bt-foot,.tmodal-head')]
  .every(el => el.scrollWidth <= el.clientWidth + 1 && el.getBoundingClientRect().right <= innerWidth + 1));

(async () => {
  const browser = await chromium.launch();
  try {
    // A small team, the owner, on a computer.
    {
      const tag = 'small desktop';
      const {page, errors, writes} = await open(browser);
      assert.deepEqual(await keys(page), ['company', 'person:ana', 'bot:goal-manager', 'bot:release', 'group:eng', 'bot:eng-lead',
        'bot:builder', 'bot:reviewer', 'person:cara', 'group:web', 'bot:web-dev', 'group:sales', 'person:ben', 'bot:sales-lead', 'bot:sdr',
        'group:outbound', 'bot:coo', 'bot:botops', 'bot:librarian', 'bot:mail-sorter'], tag + ': the chart top down, helpers last');
      assert.equal(await page.locator('.bt-r:has([data-bt-fold="helpers"]) .bt-name').innerText(), 'Helpers', tag + ': helpers in a branch of their own');
      assert.equal(await amount(page, 'bot:builder'), '$5.70 / $6 · 30m', tag + ': a bot by the window that binds');
      assert.equal(await amount(page, 'bot:goal-manager'), '$14 / $20 · day', tag + ': a day window binding says day');
      assert.equal(await amount(page, 'company'), '$27.38 / $80 · 30m', tag + ': cents only when there are any');
      assert.equal(await amount(page, 'bot:eng-lead'), '$2.50 / $3 · hour', tag + ': an hour limit not yet moved to 30 minutes still binds');
      assert.equal(await amount(page, 'group:eng'), '$16.79 / $40 · 30m', tag + ': a group pool against everyone under it');
      assert.equal(await amount(page, 'group:sales'), '$18 · day', tag + ': a group with no pool, what it spent today');
      assert.equal(await amount(page, 'bot:sdr'), '$11 · day', tag + ': a bot with no limit, what it spent today');
      assert.equal(await amount(page, 'person:cara'), '', tag + ': a person with no reports has no spend of its own');
      assert.equal(await amount(page, 'person:ana'), '$26 · day', tag + ': a person with reports adds them up (the Strategy Bot and Release Manager)');
      assert.ok(await page.locator(row('bot:release') + ' .bt-paused').isVisible(), tag + ': a held bot says Paused');
      assert.ok(await page.locator(row('bot:release')).evaluate(el => el.classList.contains('bad')), tag + ': in the fail tone');
      assert.ok(await page.locator(row('bot:builder')).evaluate(el => el.classList.contains('warn')), tag + ': 95% warns');
      assert.equal(await page.locator(row('bot:builder') + ' .bt-bar i').evaluate(el => el.style.width), '95%', tag + ': the bar');
      assert.equal(await page.locator(row('person:ana') + ' .bt-chip').innerText(), '3 caps', tag + ': a caps chip');
      assert.ok(await page.locator(row('bot:eng-lead') + ' .bt-chip.waiting').isVisible(), tag + ': a full cap waits');
      assert.equal(await page.locator('.bt-amt').first().evaluate(el => getComputedStyle(el).fontVariantNumeric), 'tabular-nums', tag + ': tabular numerals');
      for (const gone of ['Tasks', 'Spend', 'Both']) assert.equal(await page.locator(`#main button:text-is("${gone}")`).count(), 0, `${tag}: no ${gone} toggle`);
      assert.equal(await page.locator('#main .help-bubble, #main [data-help]').count() <= 1, true, tag + ': one help bubble at most');
      assert.ok(await oneLine(page), tag + ': one-line rows');
      await shot(page, 'small-desktop');

      // Fold a group, and it stays folded on the next draw.
      await page.click('[data-bt-fold="group:eng"]');
      assert.equal(await page.locator('[data-bt-open="bot:builder"]').count(), 0, tag + ': a folded group hides its rows');
      assert.equal(await page.locator('[data-bt-all]').innerText(), 'Expand all', tag + ': Expand all once anything is folded');
      await page.click('[data-bt-all]');
      assert.equal(await page.locator('[data-bt-open="bot:builder"]').count(), 1, tag + ': Expand all');
      assert.equal(await page.locator('[data-bt-all]').innerText(), 'Collapse all', tag + ': then Collapse all');

      // Search keeps what a match sits in; Near limit keeps what is near or over, paused, or with a full cap.
      await page.fill('#bt-q', 'sdr');
      await page.waitForFunction(() => document.querySelectorAll('[data-bt-open]').length < 10);
      assert.deepEqual(await keys(page), ['company', 'group:sales', 'bot:sales-lead', 'bot:sdr'], tag + ': search keeps the ancestors');
      await page.fill('#bt-q', 'zz');
      await page.locator('#bt-body .bt-note').waitFor();
      assert.equal(await page.locator('#bt-body .bt-note').innerText(), 'No matches', tag + ': no matches');
      await page.fill('#bt-q', '');
      await page.locator('[data-bt-open="bot:sdr"]').waitFor();
      assert.equal((await page.locator('#bt-hot-n').innerText()).trim(), '3', tag + ': how many are near a limit');
      await page.click('[data-bt-hot]');
      assert.deepEqual(await keys(page), ['company', 'person:ana', 'bot:release', 'group:eng', 'bot:eng-lead', 'bot:builder'], tag + ': Near limit');
      await shot(page, 'small-desktop-near-limit');
      await page.click('[data-bt-hot]');

      // A bot: its four windows with what it spent; only what changed is sent; a bad amount says which.
      await edit(page, 'bot:builder');
      const panel = await page.locator('dialog.bt-panel[open]').boundingBox();
      assert.ok(Math.abs(panel.x + panel.width - DESKTOP.width) < 2 && panel.width <= 440 && panel.height >= DESKTOP.height - 2, tag + ': a panel on the right');
      assert.deepEqual(await page.locator('dialog[open] [data-scope="bot"] .bt-win > span:first-child').allInnerTexts(), ['30 min', 'Day', 'Week', 'Month'], tag + ': four windows');
      assert.deepEqual(await page.locator('dialog[open] [data-scope="bot"] .bt-spent').allInnerTexts(), ['$5.70', '$20', '$60', '$150'], tag + ': spent in each');
      assert.equal(await page.inputValue('dialog[open] input[name="bot.per_30m_usd"]'), '6', tag + ': its own limit');
      assert.deepEqual(await page.locator('dialog[open] [data-cap-add="out"] option').evaluateAll(o => o.map(x => x.value)), ['', 'bot:eng-lead'], tag + ': caps to the managers above, not yet capped');
      await shot(page, 'small-desktop-bot');
      await page.fill('dialog[open] input[name="bot.per_30m_usd"]', 'lots');
      await page.click('dialog[open] [data-save]');
      assert.match(await page.locator('dialog[open] [data-error]').innerText(), /^30 min: /, tag + ': a bad amount says which');
      await page.fill('dialog[open] input[name="bot.per_30m_usd"]', '7.5');
      await page.fill('dialog[open] input[name="bot.monthly_usd"]', '');
      await page.selectOption('dialog[open] [data-cap-add="out"]', 'bot:eng-lead');
      assert.equal(await page.inputValue('dialog[open] [data-cap="bot:builder>bot:eng-lead"] input'), '5', tag + ': a new cap starts at 5');
      await page.fill('dialog[open] [data-cap="bot:builder>bot:eng-lead"] input', '4');
      await save(page);
      await page.waitForFunction(() => document.querySelector('.bt-r:has([data-bt-open="bot:builder"]) .bt-amt')?.textContent === '$5.70 / $7.50 · 30m');

      // An hour limit not yet moved shows as its own row and is not sent back unless changed.
      await edit(page, 'bot:eng-lead');
      assert.deepEqual(await page.locator('dialog[open] [data-scope="bot"] .bt-win > span:first-child').allInnerTexts(), ['30 min', 'Hour', 'Day', 'Week', 'Month'], tag + ': the hour row');
      assert.equal(await page.inputValue('dialog[open] input[name="bot.hourly_usd"]'), '3', tag + ': with its amount');
      await page.fill('dialog[open] input[name="bot.daily_usd"]', '45');
      await save(page);
      // A group's pool, and the company's pool and the default for each bot.
      await edit(page, 'group:eng');
      assert.equal(await page.locator('dialog[open] [data-scope="pool"] h3').innerText(), 'POOL', tag + ': a group edits its pool');
      await page.fill('dialog[open] input[name="pool.daily_usd"]', '250');
      await save(page);
      await edit(page, 'company');
      assert.equal(await page.inputValue('dialog[open] input[name="pool.per_30m_usd"]'), '80', tag + ': the company pool');
      await page.fill('dialog[open] input[name="default.daily_usd"]', '5');
      await save(page);
      // A person: the caps it takes; change one, remove one.
      await edit(page, 'person:ana');
      assert.equal(await page.locator('dialog[open] [data-scope]').count(), 0, tag + ': a person has no spend limit');
      assert.deepEqual(await page.locator('dialog[open] [data-caps="in"] .bt-who > span:last-child').allInnerTexts(), ['Engineering Lead', 'Release Manager', 'Builder'], tag + ': caps it takes');
      assert.deepEqual(await page.locator('dialog[open] [data-caps="in"] .bt-today').allInnerTexts(), ['10 today', '2 today', 'blocked'], tag + ': each with today\'s count');
      await shot(page, 'small-desktop-person');
      await page.fill('dialog[open] [data-cap="bot:release>person:ana"] input', '7');
      await page.click('dialog[open] [data-cap="bot:builder>person:ana"] [data-cap-remove]');
      await save(page);
      // Nothing changed, nothing sent.
      await edit(page, 'bot:sdr');
      await save(page);
      assert.deepEqual(writes, [
        ['PUT', '/api/v2/usage/limits/builder', {per_30m_usd: 7.5, monthly_usd: null}],
        ['PUT', '/api/v2/task-caps', {sender: 'bot:builder', receiver: 'bot:eng-lead', per_day: 4}],
        ['PUT', '/api/v2/usage/limits/eng-lead', {daily_usd: 45}],
        ['PUT', '/api/v2/usage/pools/groups/eng', {daily_usd: 250}],
        ['PUT', '/api/v2/usage/limits', {daily_usd: 5}],
        ['PUT', '/api/v2/task-caps', {sender: 'bot:release', receiver: 'person:ana', per_day: 7}],
        ['DELETE', '/api/v2/task-caps?sender=bot%3Abuilder&receiver=person%3Aana', {}],
      ], tag + ': only what changed is sent');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // A small team on a phone: the tree fits, the editor is a sheet whose rows fit.
    {
      const tag = 'small phone';
      const {page, errors} = await open(browser, {viewport: PHONE});
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      assert.ok(await oneLine(page), tag + ': one-line rows');
      assert.ok(await page.locator('.bt-amt').evaluateAll(els => els.every(el => el.getBoundingClientRect().right <= innerWidth)), tag + ': amounts not clipped');
      await shot(page, 'small-phone');
      await edit(page, 'bot:builder');
      const sheet = await page.locator('dialog.bt-panel[open]').boundingBox();
      assert.ok(Math.abs(sheet.y + sheet.height - PHONE.height) < 2 && Math.abs(sheet.width - PHONE.width) < 2, tag + ': a sheet from the bottom');
      assert.ok(await editorFits(page), tag + ': the sheet\'s rows fit');
      await shot(page, 'small-phone-bot');
      await page.click('dialog[open] .tmodal-x');
      await edit(page, 'person:ana');
      assert.ok(await editorFits(page), tag + ': the caps fit');
      await shot(page, 'small-phone-person');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Who may edit: a person who leads Sales edits its bots and nothing else; a member reads.
    {
      const tag = 'lead';
      const {page, errors} = await open(browser, {viewer: 'ben'});
      await edit(page, 'bot:sdr');
      assert.equal(await page.locator('dialog[open] input[name="bot.daily_usd"]').count(), 1, tag + ': edits a bot in Sales');
      assert.equal(await page.locator('dialog[open] [data-cap-add="out"]').count(), 1, tag + ': and its caps');
      await page.click('dialog[open] .tmodal-x');
      await edit(page, 'bot:builder');
      assert.equal(await page.locator('dialog[open] input').count(), 0, tag + ': not one in Engineering');
      assert.equal(await page.locator('dialog[open] [data-save]').count(), 0, tag + ': no Save');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'member';
      const {page, errors} = await open(browser, {viewer: 'cara'});
      await edit(page, 'person:ana');
      assert.equal(await page.locator('dialog[open] input, dialog[open] select').count(), 0, tag + ': caps read only');
      assert.deepEqual(await page.locator('dialog[open] [data-caps="in"] .bt-val').allInnerTexts(), ['10/day', '5/day', '0/day'], tag + ': as their numbers');
      await page.click('dialog[open] .tmodal-x');
      await edit(page, 'company');
      assert.equal(await page.locator('dialog[open] input').count(), 0, tag + ': the default read only');
      await shot(page, 'small-desktop-read-only');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Caps not answered (404): spend still draws, no chips. A link to a row opens its editor.
    {
      const {page, errors} = await open(browser, {capsStatus: 404});
      assert.equal(await page.locator('.bt-chip').count(), 0, '404: no caps chips');
      assert.equal(await amount(page, 'bot:builder'), '$5.70 / $6 · 30m', '404: spend still draws');
      assert.deepEqual(errors, [], '404: no page errors');
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {hash: '#/strategy/budget/bot:sdr'});
      await page.locator('dialog.bt-panel[open][aria-label="SDR"]').waitFor();
      assert.deepEqual(errors, [], 'link: no page errors');
      await page.close();
    }

    // Acme at 200 bots, on a computer: folded by default, quick, every control at that size.
    {
      const tag = 'big desktop';
      const {page, errors, team} = await open(browser, {team: big()});
      const bots = team.emps.length;
      assert.equal(bots, 203, tag + ': 200 bots and 3 helpers');
      const folded = await keys(page);
      assert.ok(folded.length <= 16, tag + ': the groups start folded: ' + folded.length);
      assert.deepEqual(folded.filter(k => k.startsWith('group:')), ['group:eng', 'group:sales', 'group:mkt', 'group:support', 'group:ops'], tag + ': the groups on top');
      assert.equal(await page.locator(row('company') + ' .bt-n').innerText(), String(5 + bots), tag + ': the company counts everyone');
      assert.match(await amount(page, 'group:eng'), /^\$[\d,.]+ \/ \$600 · day$/, tag + ': a group pool');
      await shot(page, 'big-desktop');
      // Drawing the whole tree, open, stays quick.
      const ms = await page.evaluate(() => {
        const t = performance.now();
        BUDGET.tree = budgetTree(BUDGET.limits);
        document.querySelector('[data-bt-all]').click();
        void document.body.offsetHeight;
        return performance.now() - t;
      });
      const all = await keys(page);
      assert.equal(all.length, 1 + 5 + 16 + bots, tag + ': Expand all shows every row: the company, 5 people, 16 groups, the bots');
      assert.ok(ms < 800, tag + ': the tree builds and draws in ' + Math.round(ms) + 'ms');
      assert.ok(await oneLine(page), tag + ': one-line rows');
      await shot(page, 'big-desktop-expanded');
      await page.click('[data-bt-all]');
      assert.deepEqual(await keys(page), folded, tag + ': Collapse all');
      // Search opens what a match sits in.
      await page.fill('#bt-q', 'Mobile Developer 03');
      await page.locator('[data-bt-open="bot:mobile-03"]').waitFor();
      assert.deepEqual(await keys(page), ['company', 'group:eng', 'group:mobile', 'bot:mobile-lead', 'bot:mobile-03'], tag + ': search keeps the ancestors');
      await shot(page, 'big-desktop-search');
      await page.fill('#bt-q', '');
      await page.waitForFunction(n => document.querySelectorAll('[data-bt-open]').length === n, folded.length);
      // Near limit: the paused and near bots, wherever they sit.
      const hot = Object.entries(team.limits.bots).filter(([, r]) => r.spent.day >= 8).map(([slug]) => 'bot:' + slug);
      const full = team.caps.filter(c => c.count >= c.per_day).map(c => c.sender);
      await page.click('[data-bt-hot]');
      const shown = await keys(page);
      for (const k of [...hot, ...full]) assert.ok(shown.includes(k), `${tag}: ${k} is near a limit`);
      assert.equal(Number(await page.locator('#bt-hot-n').innerText()), new Set([...hot, ...full]).size, tag + ': the count');
      await shot(page, 'big-desktop-near-limit');
      await page.click('[data-bt-hot]');
      await edit(page, 'group:eng');
      assert.equal(await page.inputValue('dialog[open] input[name="pool.daily_usd"]'), '600', tag + ': a group pool to edit');
      await shot(page, 'big-desktop-group');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    // Acme at 200 bots on a phone.
    {
      const tag = 'big phone';
      const {page, errors} = await open(browser, {team: big(), viewport: PHONE});
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await shot(page, 'big-phone');
      await page.click('[data-bt-all]');
      assert.equal(await sideways(page), false, tag + ': none with everything open');
      assert.ok(await oneLine(page), tag + ': one-line rows, the deepest too');
      assert.ok(await page.locator('.bt-amt').evaluateAll(els => els.every(el => el.getBoundingClientRect().right <= innerWidth)), tag + ': amounts not clipped');
      await page.evaluate(() => document.querySelector('[data-bt-open="bot:tier1-lead"]').scrollIntoView({block: 'start'}));
      await shot(page, 'big-phone-expanded');
      await page.click('[data-bt-all]');
      await page.fill('#bt-q', 'sdr');
      await page.locator('[data-bt-open="bot:outbound-01"]').waitFor();
      await shot(page, 'big-phone-search');
      await edit(page, 'bot:outbound-01');
      assert.ok(await editorFits(page), tag + ': the sheet fits');
      await shot(page, 'big-phone-bot');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
  } finally {
    await browser.close();
  }
  console.log('strategy-budget: ok');
})().catch(e => { console.error(e); process.exit(1); });
