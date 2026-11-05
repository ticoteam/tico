// Overview › Graph (ui/app/overview-graph.js): the team chart top down with each teammate's rules or goals.
//  - the Graph tab beside Campus at #/overview/graph; built-in bots stay off; groups are labelled boxes;
//  - Rules | Goals in the address (?view=goals), kept on reload;
//  - Rules: counts on bots and people, Stop / Ask first marked; a tap opens the panel (Follows, With, Team), a rule
//    picked there lights up its Who and With teammates and the link between them; Edit only where the rule allows;
//    Add rule opens the Rules dialog with Who set to the teammate, and the graph reads its rules again after;
//  - Goals: chips coloured by status, the panel with KPI and target, a link where a goal supports another owner's;
//    Update opens the Goals page's sheet, and closing it reads the goals again;
//  - Reports to / Reports move the selection; a person's panel; an older server without the rules route;
//  - pan and zoom (buttons, keys, wheel, drag, Fit) with 60+ bots; keyboard open and close;
//  - a phone: the same chart in its frame, the panel a bottom sheet, no sideways page scroll.
// GRAPH_SHOTS=<dir> saves desktop and phone screenshots, dark and light, in both modes.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.GRAPH_SHOTS || '';

const policy = {see: {everyone: true}, read: {everyone: true}, write: {everyone: true}};
const bot = (name, display, extra = {}) => ({name, display_name: display, status: 'active', state: 'active', owner_id: 'ana', can_manage: true,
  users: [{id: 'ana', name: 'Ana'}], bot_owners: [], my_access: {see: true, read: true, write: true}, access_policy: policy, ...extra});
function fixtures({many = 0} = {}) {
  const bots = [
    bot('lead', 'Head of Growth', {team: 'growth', reports_to: 'human:ana', org_parent: 'p:ana'}),
    bot('writer', 'Content Writer', {team: 'growth', reports_to: 'lead', org_parent: 'b:lead'}),
    bot('seo', 'SEO Analyst', {team: 'growth', reports_to: 'lead', org_parent: 'b:lead'}),
    bot('builder', 'Shop Builder', {reports_to: 'human:sam', org_parent: 'p:sam'}),
    bot('botops', 'BotOps', {reports_to: 'human:ana', org_parent: 'p:ana'}),
  ];
  for (let i = 1; i <= many; i++) bots.push(bot('helper' + i, 'Helper ' + String(i).padStart(2, '0'), {team: 'growth', reports_to: i % 3 ? 'writer' : 'seo', org_parent: i % 3 ? 'b:writer' : 'b:seo'}));
  return {
    bots,
    people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}, {id: 'sam', name: 'Sam Okafor', role: 'member', reports_to: 'ana', org_parent: 'p:ana'}],
    groups: [{id: 'growth', name: 'Growth'}],
    rules: {
      rules: [
        {id: 'r1', kind: 'internal', text: 'Never share pricing with vendors', if_broken: 'block'},
        {id: 'r2', kind: 'internal', text: 'Ask before refunds over $500', if_broken: 'escalate'},
        {id: 'r3', kind: 'external', text: 'Reply to Acme Supply within 2 days', if_broken: 'warn'},
        {id: 'r4', kind: 'internal', text: 'Sam reviews launch copy', if_broken: null},
      ],
      bots: [{slug: 'lead', who: ['r3'], with: []}, {slug: 'writer', who: ['r1'], with: ['r4']}, {slug: 'seo', who: ['r1'], with: []},
        {slug: 'builder', who: ['r2'], with: ['r1']}, {slug: 'botops', who: ['r2'], with: []}],
      people: [{id: 'sam', who: ['r4'], with: ['r2']}],
    },
    full: {r1: {id: 'r1', kind: 'internal', text: 'Never share pricing with vendors', can_edit: true}, r2: {id: 'r2', can_edit: true},
      r3: {id: 'r3', kind: 'external', can_edit: false}, r4: {id: 'r4', can_edit: false}},
    goals: [
      {id: 'g1', owner_id: 'human:ana', title: 'Grow revenue 30%', status: 'green', rank: 1,
        kpis: [{id: 'k1', name: 'MRR', unit: '$', latest: {value: 42000}, freshness: 'fresh', status: 'green', link: {target: 60000}}]},
      {id: 'g2', owner_id: 'bot:lead', title: 'Double signups', status: 'yellow', parent_id: 'g1', rank: 1, kpis: []},
      {id: 'g3', owner_id: 'bot:writer', title: 'Ship 8 posts a month', status: 'red', parent_id: 'g2', rank: 1, kpis: []},
      {id: 'g4', owner_id: 'bot:seo', title: 'Rank top 3 for bike repair', status: 'green', parent_id: 'g2', rank: 1, kpis: []},
      {id: 'g5', owner_id: 'bot:builder', title: 'Launch the shop', status: 'gray', rank: 1, kpis: []},
      {id: 'g6', owner_id: 'bot:botops', title: 'Keep installs healthy', status: 'green', rank: 1, kpis: []},
    ],
  };
}

async function open(browser, {viewport = {width: 1280, height: 820}, theme = 'dark', hash = '#/overview/graph', old = false, many = 0, as = 'owner'} = {}) {
  const data = fixtures({many});
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 600, isMobile: viewport.width < 600});
  const errors = [], writes = [], reads = {rules: 0, goals: 0, ruleOne: []};
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const me = {owner: {id: 'ana', role: 'owner', name: 'Ana Ruiz'}, member: {id: 'sam', role: 'member', name: 'Sam Okafor'}}[as];
  Object.assign(me, {email: me.id + '@example.com', cloud: true, registered: true, owner_id: 'ana', config: {company_name: 'Acme Bikes'}});
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const name = p.slice('/tico/ui/'.length), file = uiFile(name);
      if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
    }
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(data.bots);
    if (p === '/api/humans') return json({people: data.people, teams: {}, org_groups: data.groups});
    if (p === '/api/v2/groups') return json(data.groups);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/rules/by-teammate') { reads.rules++; return old ? json({detail: 'Not Found'}, 404) : json(data.rules); }
    const one = p.match(/^\/api\/v2\/rules\/(r\d+)$/);
    if (one && method === 'GET') { reads.ruleOne.push(one[1]); return json(data.full[one[1]] || {}); }
    if (p === '/api/v2/rules' && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, body});
      data.rules.rules.push({id: 'r9', kind: 'internal', text: body.text, if_broken: null});
      data.rules.bots.find(b => b.slug === 'seo').who.push('r9');
      return json({id: 'r9', kind: 'internal', status: 'active'});
    }
    if (p === '/api/v2/rule-sources') return json({sources: []});
    if (p === '/api/v2/threads') return json({threads: [], attention: [], dms: []});
    if (p === '/api/v2/goals/tree') { reads.goals++; return json({goals: data.goals, owners: {}, unaligned: {}, other_kpis: [], proposals: []}); }
    const goal = p.match(/^\/api\/v2\/goals\/(g\d+)$/);
    if (goal && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, body});
      Object.assign(data.goals.find(g => g.id === goal[1]), body);
      return json({goal: data.goals.find(g => g.id === goal[1])});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator(hash.startsWith('#/overview/graph') ? '#og-cv [data-og]' : '.dash-tabs').first().waitFor();
  return {page, errors, writes, reads, data};
}
const ready = async (page, view) => page.waitForFunction(v => OG && (v === 'rules' ? OG.rules !== null : OG.goals !== null), view);
const node = (page, key) => page.locator(`#og-cv [data-og="${key}"]`);
async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(250);
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}
const until = async (check, what) => { for (let i = 0; i < 100; i++) { if (await check()) return; await new Promise(r => setTimeout(r, 30)); } throw new Error('timed out: ' + what); };

async function tabAndToggle(browser) {
  const {page, errors} = await open(browser, {hash: '#/overview'});
  await page.locator('.dash-tabs a', {hasText: 'Graph'}).click();
  await page.waitForFunction(() => location.hash === '#/overview/graph');
  await ready(page, 'rules');
  assert.deepEqual(await page.locator('.dash-tabs a').allInnerTexts(), ['Mine', 'Company', 'Campus', 'Graph', 'Share']);
  assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Graph');
  // The chart: people at the top, the group as a box, bots under their managers; built-ins stay off.
  for (const k of ['p:ana', 'p:sam', 'g:growth', 'b:lead', 'b:writer', 'b:seo', 'b:builder']) assert.equal(await node(page, k).count(), 1, k);
  assert.equal(await node(page, 'b:botops').count(), 0, 'BotOps stays off the chart');
  const y = async k => (await node(page, k).boundingBox()).y;
  assert.ok(await y('p:ana') < await y('g:growth') && await y('g:growth') < await y('b:lead') && await y('b:lead') < await y('b:writer'));
  assert.equal(await page.locator('[data-og-view=rules]').getAttribute('aria-pressed'), 'true');
  // Goals: the address says so, and a reload keeps it.
  await page.locator('[data-og-view=goals]').click();
  assert.equal(await page.evaluate(() => location.hash), '#/overview/graph?view=goals');
  await ready(page, 'goals');
  assert.ok(await node(page, 'b:writer').locator('.og-chip').count() >= 1);
  await page.reload();
  await page.locator('#og-cv [data-og]').first().waitFor();
  assert.equal(await page.locator('[data-og-view=goals]').getAttribute('aria-pressed'), 'true');
  await page.locator('[data-og-view=rules]').click();
  assert.equal(await page.evaluate(() => location.hash), '#/overview/graph');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('tab, route and toggle: ok');
}

async function rulesMode(browser) {
  const {page, errors, writes, reads} = await open(browser);
  await ready(page, 'rules');
  const count = async k => (await node(page, k).locator('.og-count b').innerText());
  assert.equal(await count('b:writer'), '2');
  assert.equal(await count('b:builder'), '2');
  assert.equal(await count('b:lead'), '1');
  assert.equal(await count('p:sam'), '2', 'people carry counts too');
  assert.equal(await count('p:ana'), '0');
  assert.equal(await node(page, 'b:writer').locator('.og-flag.block').count(), 1);
  assert.equal(await node(page, 'b:builder').locator('.og-flag.block').count(), 1, 'block outranks escalate');
  assert.equal(await node(page, 'p:sam').locator('.og-flag.escalate').count(), 1);
  // A tap opens the panel: Follows and With, then Team.
  await node(page, 'b:builder').click();
  const panel = page.locator('#og-panel');
  await panel.waitFor();
  assert.equal(await node(page, 'b:builder').evaluate(el => el.classList.contains('sel')), true);
  assert.deepEqual(await panel.locator('.og-sh h3').allInnerTexts(), ['RULES', 'GOALS', 'TEAM']);
  assert.deepEqual(await panel.locator('h4').allInnerTexts(), ['Follows', 'With']);
  assert.equal(await panel.locator('.og-rule-a').first().getAttribute('href'), '#/rules?rule=r2');
  // Pick the With rule: every bot it reaches lights up, and the Who bots link to the With bot.
  await panel.locator('[data-og-rule=r1]').click();
  await page.waitForFunction(() => document.querySelectorAll('#og-cv .og-who').length === 2);
  assert.deepEqual(await page.locator('#og-cv .og-who').evaluateAll(l => l.map(e => e.dataset.og).sort()), ['b:seo', 'b:writer']);
  assert.deepEqual(await page.locator('#og-cv .og-with').evaluateAll(l => l.map(e => e.dataset.og)), ['b:builder']);
  assert.equal(await page.locator('#og-cv .og-rl').count(), 2);
  assert.equal(await node(page, 'b:lead').evaluate(el => el.classList.contains('dim')), true);
  // Edit shows once the rule says it may be changed; the Who teammates are listed under it.
  await until(async () => (await panel.locator('.og-rule.on .og-rule-a').innerText()) === 'Edit', 'Edit on r1');
  assert.deepEqual(await panel.locator('.og-rule.on .og-go').allInnerTexts(), ['Content Writer', 'SEO Analyst']);
  // Reports to moves the selection.
  await panel.locator('.og-gl-r', {hasText: 'Reports to'}).locator('[data-og-go]').click();
  await until(async () => (await panel.locator('.og-pn').innerText()) === 'Sam Okafor', 'Sam selected');
  assert.equal(await node(page, 'p:sam').evaluate(el => el.classList.contains('sel')), true);
  assert.equal(await page.locator('#og-cv .og-rl').count(), 0, 'a new selection drops the picked rule');
  // A person's panel: their rules, and their reports.
  assert.deepEqual(await panel.locator('h4').allInnerTexts(), ['Follows', 'With']);
  assert.deepEqual(await panel.locator('.og-gl-r', {hasText: 'Reports'}).last().locator('.og-go').allInnerTexts(), ['Shop Builder']);
  await panel.locator('.og-gl-r', {hasText: 'Reports to'}).locator('[data-og-go]').click();
  await until(async () => (await panel.locator('.og-pn').innerText()) === 'Ana Ruiz', 'Ana selected');
  await panel.locator('.og-gl-r').filter({hasText: /^Reports(?! to)/}).locator('[data-og-go]', {hasText: 'Head of Growth'}).click();
  await until(async () => (await panel.locator('.og-pn').innerText()) === 'Head of Growth', 'report link');
  // Edit stays hidden where the rule does not allow it.
  await panel.locator('[data-og-rule=r3]').click();
  await until(() => reads.ruleOne.includes('r3'), 'r3 read');
  await page.waitForTimeout(100);
  assert.equal(await panel.locator('.og-rule.on .og-rule-a').innerText(), 'Open');
  assert.equal(await panel.locator('.og-rule.on .og-rule-a').getAttribute('href'), '#/rules/external?rule=r3');
  // Add rule: the Rules dialog with Who set to this teammate; after saving, the graph reads its rules again.
  await node(page, 'b:seo').click();
  await until(async () => (await panel.locator('.og-pn').innerText()) === 'SEO Analyst', 'seo');
  const before = reads.rules;
  await panel.locator('[data-og-add-rule]').click();
  const dlg = page.locator('#ru-add[open]');
  await dlg.waitFor();
  assert.deepEqual(await dlg.locator('[data-scope=who] .ru-chip').allInnerTexts().then(l => l.map(s => s.replace('✕', '').trim())), ['SEO Analyst']);
  await dlg.locator('textarea[name=text]').fill('Cite every source');
  await dlg.locator('[type=submit]').click();
  await until(() => writes.some(w => w.p === '/api/v2/rules'), 'rule saved');
  assert.deepEqual(writes.find(w => w.p === '/api/v2/rules').body.who.bots, ['seo']);
  await until(() => reads.rules > before, 'rules read again');
  await until(async () => (await node(page, 'b:seo').locator('.og-count b').innerText()) === '2', 'count updated');
  assert.equal(await panel.locator('[data-og-rule=r9]').count(), 1);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('rules mode: ok');
}

async function goalsMode(browser) {
  const {page, errors, writes, reads} = await open(browser, {hash: '#/overview/graph?view=goals'});
  await ready(page, 'goals');
  const chip = page.locator('#og-cv [data-og="b:writer"] .og-chip');
  assert.equal(await chip.innerText(), 'Ship 8 posts a month');
  assert.equal(await chip.evaluate(el => el.classList.contains('red')), true);
  assert.equal(await chip.getAttribute('title'), 'Off track: Ship 8 posts a month');
  // Goals that support another owner's goal: writer→lead, seo→lead, lead→Ana.
  const links = await page.locator('#og-cv .og-gl').evaluateAll(l => l.map(e => e.dataset.a + '>' + e.dataset.b).sort());
  assert.deepEqual(links, ['b:lead>p:ana', 'b:seo>b:lead', 'b:writer>b:lead']);
  // The panel: goals first, with KPI against its target.
  await node(page, 'p:ana').click();
  const panel = page.locator('#og-panel');
  await panel.waitFor();
  assert.deepEqual(await panel.locator('.og-sh h3').allInnerTexts(), ['GOALS', 'RULES', 'TEAM']);
  assert.match(await panel.locator('.og-kpi').innerText(), /MRR\s+\$42,000 \/ \$60,000/);
  assert.equal(await panel.locator('.og-gl-h a').getAttribute('href'), '#/strategy/goals/g1');
  assert.deepEqual(await panel.locator('.og-gl-r', {hasText: 'Supported by'}).locator('.og-go').allInnerTexts(), ['Head of Growth']);
  assert.equal(await page.locator('#og-cv .og-gl.on').count(), 1, "the selection's links stand out");
  // Supports moves to the parent goal's owner.
  await node(page, 'b:writer').click();
  await until(async () => (await panel.locator('.og-pn').innerText()) === 'Content Writer', 'writer');
  assert.match(await panel.locator('.og-gl-r', {hasText: 'Supports'}).innerText(), /Double signups/);
  // Update: the Goals page's sheet; a save there reads the goals again.
  const before = reads.goals;
  await panel.locator('[data-og-goal-edit=g3]').click();
  const sheet = page.locator('#goal-sheet[open]');
  await sheet.waitFor();
  await sheet.locator('input[name=title]').fill('Ship 10 posts a month');
  await sheet.locator('[type=submit]').click();
  await until(() => writes.some(w => w.p === '/api/v2/goals/g3'), 'goal saved');
  await until(() => reads.goals > before, 'goals read again');
  await until(async () => (await chip.innerText()) === 'Ship 10 posts a month', 'chip updated');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('goals mode: ok');
}

async function olderServer(browser) {
  const {page, errors} = await open(browser, {old: true});
  await ready(page, 'rules');
  assert.equal(await page.locator('#og-status').innerText(), 'Rules need a newer Tico.');
  assert.equal(await page.locator('#og-cv .og-count').count(), 0);
  await node(page, 'b:writer').click();
  assert.equal(await page.locator('#og-panel [data-og-add-rule]').count(), 0);
  await page.locator('[data-og-view=goals]').click();
  await ready(page, 'goals');
  assert.equal(await page.locator('#og-status').isHidden(), true);
  assert.ok(await page.locator('#og-cv .og-chip').count() >= 4, 'goals still work');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('older server: ok');
}

async function keyboardAndZoom(browser) {
  const {page, errors} = await open(browser, {many: 64});
  await ready(page, 'rules');
  assert.ok(await page.locator('#og-cv [data-og]').count() >= 70);
  // Fit on open: the whole chart inside the frame.
  const fits = () => page.evaluate(() => { const f = $('#og-frame').getBoundingClientRect(), c = $('#og-cv').getBoundingClientRect();
    return c.left >= f.left - 1 && c.right <= f.right + 1 && c.top >= f.top - 1 && c.bottom <= f.bottom + 1; });
  assert.equal(await fits(), true, 'the chart opens fitted');
  const scale = () => page.evaluate(() => OG.tf.s);
  const s0 = await scale();
  await page.locator('[data-og-zoom=in]').click();
  assert.ok(await scale() > s0);
  await page.locator('#og-frame').focus();
  await page.keyboard.press('-'); await page.keyboard.press('-');
  assert.ok(await scale() < s0 * 1.25);
  await page.keyboard.press('+');
  await page.keyboard.press('0');
  await until(async () => Math.abs(await scale() - s0) < 1e-6, 'keyboard 0 fits');
  // Wheel zooms around the pointer; a drag pans.
  const box = await page.locator('#og-frame').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -300);
  assert.ok(await scale() > s0);
  const x0 = await page.evaluate(() => OG.tf.x);
  await page.mouse.move(box.x + box.width - 200, box.y + 12);
  await page.mouse.down(); await page.mouse.move(box.x + box.width - 100, box.y + 12, {steps: 5}); await page.mouse.up();
  assert.ok(Math.abs(await page.evaluate(() => OG.tf.x) - x0 - 100) < 2, 'a drag pans');
  assert.equal(await page.locator('#og-panel').isHidden(), true, 'a drag is not a tap');
  await page.locator('[data-og-zoom=fit]').click();
  // Keyboard: Tab reaches a teammate, Enter opens the panel, Escape closes it and gives focus back.
  await page.locator('#og-frame').focus();
  await page.keyboard.press('Tab');
  const first = await page.evaluate(() => document.activeElement.dataset.og);
  assert.ok(first);
  await page.keyboard.press('Enter');
  await page.locator('#og-panel').waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#og-panel').isHidden(), true);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.og), first);
  // A selected teammate far down the chart is brought into view.
  await page.evaluate(() => { OG.tf = {x: 0, y: 0, s: 1}; ogApply(); });
  await page.evaluate(() => ogSelect('b:helper64'));
  await page.waitForTimeout(400);
  const seen = await page.evaluate(() => { const f = $('#og-frame').getBoundingClientRect(), n = document.querySelector('[data-og="b:helper64"]').getBoundingClientRect(), p = $('#og-panel').getBoundingClientRect();
    return n.left >= f.left && n.right <= p.left && n.top >= f.top && n.bottom <= f.bottom; });
  assert.equal(seen, true, 'the selection stays in view beside the panel');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('keyboard, pan and zoom with 60+ bots: ok');
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await ready(page, 'rules');
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(await overflow() <= 0, 'no sideways page scroll');
  assert.equal(await page.locator('#og-frame').evaluate(el => getComputedStyle(el).touchAction), 'none', 'a pinch zooms the chart, not the page');
  const s0 = await page.evaluate(() => OG.tf.s);
  await page.locator('[data-og-zoom=in]').tap();
  assert.ok(await page.evaluate(() => OG.tf.s) > s0, 'zoom in');
  await page.locator('[data-og-zoom=fit]').tap();
  await node(page, 'b:writer').tap();
  const panel = page.locator('#og-panel');
  await panel.waitFor();
  const pos = await panel.evaluate(el => { const r = el.getBoundingClientRect(); return {pos: getComputedStyle(el).position, left: r.left, width: r.width, top: r.top}; });
  assert.equal(pos.pos, 'fixed'); assert.equal(pos.left, 0); assert.equal(pos.width, 390);
  assert.ok(pos.top > 844 * 0.3, 'a bottom sheet');
  assert.ok(await overflow() <= 0, 'no sideways page scroll with the sheet open');
  await panel.locator('[data-og-close]').tap();
  assert.equal(await panel.isHidden(), true);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('phone: ok');
}

async function screens(browser) {
  if (!SHOTS) return;
  for (const theme of ['dark', 'light']) {
    for (const [kind, viewport] of [['desktop', {width: 1280, height: 820}], ['phone', {width: 390, height: 844}]]) {
      for (const view of ['rules', 'goals']) {
        const {page, errors} = await open(browser, {viewport, theme, hash: '#/overview/graph' + (view === 'goals' ? '?view=goals' : '')});
        await ready(page, view);
        await shot(page, `graph-${view}-${kind}-${theme}`);
        await page.evaluate(() => ogSelect(OG.view === 'rules' ? 'b:builder' : 'b:lead'));
        if (view === 'rules') await page.locator('#og-panel [data-og-rule=r1]').click();
        await page.waitForTimeout(300);
        await shot(page, `graph-${view}-${kind}-${theme}-panel`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
  }
  const {page} = await open(browser, {many: 64});
  await ready(page, 'rules');
  await shot(page, 'graph-rules-desktop-dark-64-bots');
  await page.close();
  console.log('screenshots: ok');
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    await tabAndToggle(browser); await rulesMode(browser); await goalsMode(browser); await olderServer(browser);
    await keyboardAndZoom(browser); await phone(browser); await screens(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
