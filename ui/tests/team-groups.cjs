// Offline regression for the team chart's groups (docs/org-chart.md). Fixtures only.
//  - the chart shows groups as nested, collapsible sections holding their humans and bots; a group's members hang
//    under whoever they report to when that one is in the same group; the Built-in bots stay outside every group;
//  - owners and admins add a group, rename it, drag a human or a bot into a group (or out, onto "No group") and
//    nest a group in another; a member has none of those handles.
//  - a manager's reports keep the order they are dragged (or moved up and down by keyboard) into, through a reload;
//    a drop onto a manager the viewer may not put reports under says so and moves nothing; reports nobody placed
//    follow the server's rule on the chart and a person's page; reports shown in a group move up and down there.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const FULL = {see: true, read: true, write: true};
async function shot(page, name) {
  if (!process.env.TICO_SCREENSHOT_DIR) return;
  await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity));
  await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, name + '.png')});
}

async function open(browser, me, branches = false) {
  const page = await browser.newPage({viewport: {width: 1300, height: 900}, serviceWorkers: 'block'});
  const errors = [], calls = [], failures = new Set();
  const deletion = {status: 0, hold: null}, seatSave = {status: 0, hold: null};
  const people = [
    {id: 'ana', name: 'Ana Rivera', email: 'ana@example.test', org_parent: '', team: '', inbox_bot: 'inbox'},
    {id: 'ben', name: 'Ben Cole', org_parent: 'p:ana', team: 'marketing', reports_to: 'ana'},
    {id: 'cara', name: 'Cara Mendes', org_parent: 'p:ana', team: '', reports_to: 'ana'},
  ];
  const groups = [{id: 'marketing', name: 'Marketing', parent: ''}, {id: 'seo', name: 'SEO', parent: 'marketing'},
                  {id: 'sales', name: 'Sales', parent: ''}];
  const bot = (name, display_name, team, org_parent, extra = {}) => ({name, display_name, team, org_parent, host: 'keeper',
    status: 'active', state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}], operator: 'ana', revision: 1, ...extra});
  const bots = [
    bot('cmo', 'CMO', 'marketing', 'p:ben', {reports_to: 'human:ben'}),
    bot('writer', 'Writer', 'seo', 'b:cmo', {reports_to: 'cmo'}),
    bot('scout', 'Scout', '', 'p:ana', {reports_to: 'human:ana'}),
    bot('botops', 'BotOps', '', 'p:ana', {reports_to: 'human:ana', helper: true}),
    bot('inbox', 'Inbox Manager', '', 'p:ana'),
    bot('channel', 'Channel Inbox', 'marketing', 'b:botops', {helper: true}),
  ];
  if (branches) bots.push(
    bot('cmo-ben', 'CMO', 'marketing', 'p:ben', {shared_from: 'cmo', operator: 'ben'}),
    bot('cmo-ana', 'CMO', 'marketing', 'p:ana', {shared_from: 'cmo', operator: 'ana'}),
    bot('cmo-limited', 'CMO', 'marketing', 'p:ben', {is_branch: true, operator: 'ben', my_access: {see: true, read: false, write: false}}),
    bot('cmo-unknown', 'CMO', 'marketing', 'p:ben', {shared_from: 'cmo', operator: null}),
  );
  const seats = [];
  const seatView = seat => ({...seat, kind: 'seat', stage: 'recruiting', can_edit: true,
    draft: {state: seat.drafted ? 'drafted' : seat.drafting ? 'drafting' : 'none', by: 'CEO Bot', at: ''},
    description_by: seat.drafted ? 'ceo_bot' : seat.description ? 'person' : 'chart'});
  const groupRows = () => groups.map((g, i) => ({...g, org_parent: g.parent ? 'g:' + g.parent : '', order: i}));
  const apply = (id, body) => {
    const group = groups.find(g => g.id === id);
    if (body.name) group.name = body.name;
    if (body.parent !== undefined) group.parent = body.parent;
    for (const [kind, rows, key] of [['people', people, 'id'], ['bots', bots, 'name']]) {
      for (const one of (body.add || {})[kind] || []) rows.find(r => r[key] === one).team = id;
      for (const one of (body.remove || {})[kind] || []) rows.find(r => r[key] === one).team = '';
    }
  };
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/')) {
      const file = path.join(__dirname, '..', p);
      if (fs.existsSync(file)) return route.fulfill({contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript', body: fs.readFileSync(file)});
    }
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (failures.has(p)) return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Temporarily unavailable'})});
    if (p === '/api/me') return json({...me, config: {beta}});
    if (p === '/api/humans') return json({people, org_groups: groupRows()});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/issues') return json([]);
    if (p === '/api/runs') return json([]);
    if (p === '/api/v2/templates') return json({cards: [{template: 'bookkeeping', slug: 'bookkeeping', name: 'Bookkeeper',
      summary: 'Keeps the ledger.', instructions: '# Bookkeeper\n\nKeep the ledger.', model: 'fixture-model'}]});
    if (p === '/api/v2/bots' && method === 'POST') {
      const body = request.postDataJSON(); calls.push(['POST', 'bots', body]);
      bots.push(bot(body.slug, body.display_name, '', '', {status: body.status}));
      return json({bot: {slug: body.slug}});
    }
    if (p === '/api/v2/access/humans' && method === 'POST') {
      const body = request.postDataJSON(); calls.push(['POST', 'humans', body]);
      people.push({id: 'new-human', ...body, org_parent: '', team: ''});
      return json({id: 'new-human'});
    }
    if (p.startsWith('/api/v2/chat/') && method === 'POST') { calls.push(['POST', 'chat', request.postDataJSON()]); return json({}); }
    if (p === '/api/v2/seats' && method === 'GET') return json({seats: seats.filter(seat => !seat.filled).map(seatView)});
    if (p === '/api/v2/seats' && method === 'POST') {
      const body = request.postDataJSON(); calls.push(['POST', 'seats', body]);
      const seat = {id: 'seat' + (seats.length + 1), team: '', created: '2026-10-24T00:00:0' + seats.length + 'Z', ...body};
      seats.push(seat);
      return json(seatView(seat));
    }
    let s;
    if ((s = p.match(/^\/api\/v2\/seats\/([^/]+)(?:\/(open|recruit|hire))?$/))) {
      const seat = seats.find(row => row.id === s[1]), body = method === 'GET' ? null : request.postDataJSON();
      if (s[2]) calls.push(['POST', s[2], s[1], body]);
      if (s[2] === 'open') { if (!seat.description) seat.drafting = true; return json(seatView(seat)); }
      if (!s[2] && method === 'PATCH') {
        calls.push(['PATCH', s[1], body]);
        if (seatSave.hold) await seatSave.hold;
        if (seatSave.status) return route.fulfill({status: seatSave.status, contentType: 'application/json',
          body: JSON.stringify({error: {detail: 'Saving failed'}})});
        Object.assign(seat, {description: body.description, drafting: false, drafted: false});
        return json(seatView(seat));
      }
      if (!s[2]) {                                   // the CEO Bot's draft lands by the first poll
        if (seat.drafting) Object.assign(seat, {drafting: false, drafted: true, description: 'Keeps the books and closes each month.'});
        return json(seatView(seat));
      }
      if (s[2] === 'recruit') return json({seat: seatView(seat), models: [{id: 'fixture-model', name: 'Fixture model'}, {id: 'other-model', name: 'Other model'}],
        candidates: [
          {template: 'bookkeeping', name: 'Bookkeeper', reason: 'Matches “closes each month” in the job description', recommended: true, model: 'fixture-model', model_name: 'Fixture model', summary: 'Keeps the ledger.'},
          {template: 'controller', name: 'Controller', reason: 'Controller fits the role “Ledger” in Finance', recommended: false, model: 'fixture-model', model_name: 'Fixture model', summary: ''}]});
      seat.filled = true;
      const slug = seat.title.toLowerCase();
      calls.push(['hired with', seat.description]);
      bots.push(bot(slug, seat.title, '', seat.reports_to.startsWith('human:') ? 'p:' + seat.reports_to.slice(6) : 'b:' + seat.reports_to,
        {reports_to: seat.reports_to, status: 'planned', onboarding_state: 'needs_setup', stage: 'onboarding'}));
      return json({slug, seat: seat.id, stage: 'onboarding', reports_to: seat.reports_to, template: body.template, model: body.model});
    }
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/messaging/bots') return json({bots: [{bot: 'channel', name: 'Channel Inbox', sources: [{kind: 'slack', id: 'slack:C1', name: '#support'}]}]});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/groups' && method === 'POST') {
      const body = request.postDataJSON(); calls.push(['POST', 'groups', body]);
      const id = body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      groups.push({id, name: body.name, parent: body.parent || ''});
      return json({id});
    }
    let m;
    if ((m = p.match(/^\/api\/v2\/groups\/([^/]+)$/)) && method === 'PATCH') {
      const body = request.postDataJSON(); calls.push(['PATCH', m[1], body]); apply(m[1], body);
      return json({id: m[1]});
    }
    if ((m = p.match(/^\/api\/v2\/groups\/([^/]+)$/)) && method === 'DELETE') {
      calls.push(['DELETE', m[1]]);
      if (deletion.hold) await deletion.hold;
      const group = groups.find(g => g.id === m[1]), status = deletion.status || (group ? 0 : 404);
      if (status) return route.fulfill({status, contentType: 'application/json',
        body: JSON.stringify({error: {detail: status === 403 ? 'Permission denied' : 'Group not found'}})});
      for (const child of groups) if (child.parent === group.id) child.parent = group.parent;
      for (const teammate of [...people, ...bots]) if (teammate.team === group.id) teammate.team = group.parent;
      groups.splice(groups.indexOf(group), 1);
      return json({id: m[1], deleted: true, moved_to: group.parent});
    }
    if ((m = p.match(/^\/api\/v2\/org\/order\/([^/]+)$/)) && method === 'PUT') {
      const body = request.postDataJSON(); calls.push(['PUT', decodeURIComponent(m[1]), body]);
      body.reports.forEach((id, i) => {
        const [kind, key] = id.split(':');
        (kind === 'human' ? people.find(r => r.id === key) : bots.find(r => r.name === key)).report_position = i;
      });
      return json({});
    }
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/#/updates');
  await page.locator('#tree a.node').first().waitFor();
  return {page, errors, calls, people, groups, bots, failures, deletion, seatSave};
}

// The chart as nested text: a group is {name: [...]}, a human or a bot is its name.
const shape = page => page.evaluate(() => {
  const walk = ul => [...ul.children].filter(li => li.tagName === 'LI' && !li.classList.contains('org-no-group')).map(li => {
    const kids = li.querySelector(':scope > ul:not(.node-sources)'), label = li.querySelector(':scope > .noderow .dept-label, :scope > .noderow .nm, :scope > .noderow .org-name');
    const name = (label?.value ?? label?.textContent ?? '').trim();
    return kids ? {[name]: walk(kids)} : name;
  });
  return walk(document.querySelector('#tree'));
});
const row = (page, name) => page.locator('#tree .noderow[data-org^="g:"]', {has: page.locator('.dept-label', {hasText: new RegExp('^' + name + '$')})});
const node = (page, key) => page.locator(`#tree [data-org="${key}"]`);
// A real drag, with a pause after it starts: the "No group" row only shows once a drag has begun.
async function drag(page, from, to) {
  await from.hover();
  await page.mouse.down();
  await page.mouse.move(40, 40);
  await page.mouse.move(45, 45);
  await to.hover();
  await page.mouse.up();
}

async function reportOrder(browser) {
  const {page, errors, calls, bots} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
  for (const [name, display_name] of [['analyst', 'Analyst'], ['builder', 'Builder']])
    bots.push({...bots.find(b => b.name === 'scout'), name, display_name});
  await page.evaluate(() => refresh(true));
  const under = async () => (await shape(page))[0].Ana;
  assert.deepEqual(await under(), ['Cara', 'Analyst', 'Builder', 'Scout'], 'nobody placed them: humans, then names');
  // Dragged onto the top of a fellow report, Scout goes above it, and stays there through a reload.
  const analyst = node(page, 'b:analyst');
  await node(page, 'b:scout').hover();
  await page.mouse.down();
  await page.mouse.move(40, 40);
  await analyst.hover({position: {x: 20, y: 2}});
  await page.mouse.up();
  await page.waitForFunction(() => document.querySelectorAll('#tree [data-org-place]')[1]?.dataset.org === 'b:scout');
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['human:cara', 'bot:scout', 'bot:analyst', 'bot:builder']}]);
  await page.reload();
  await page.locator('#tree a.node').first().waitFor();
  assert.deepEqual(await under(), ['Cara', 'Scout', 'Analyst', 'Builder']);
  // By keyboard: Tab from the row to its Move menu, then Move up, Move down.
  await node(page, 'b:builder').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.orgMenu), 'b:builder');
  await page.keyboard.press('Enter');
  assert.equal(await page.getByRole('menuitem', {name: 'Move down'}).isDisabled(), true, 'the last one goes no lower');
  assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Move up');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.activeElement?.dataset.orgMenu === 'b:builder' && !document.querySelector('#tree .org-order-menu'));
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['human:cara', 'bot:scout', 'bot:builder', 'bot:analyst']}]);
  assert.deepEqual(await under(), ['Cara', 'Scout', 'Builder', 'Analyst']);
  await page.keyboard.press('Enter');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelectorAll('#tree [data-org-place]')[3]?.dataset.org === 'b:builder');
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['human:cara', 'bot:scout', 'bot:analyst', 'bot:builder']}]);
  // Escape closes the menu, back on its button, and moves nothing.
  await page.keyboard.press('Enter');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#tree .org-order-menu').count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.orgMenu), 'b:builder');
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
  await page.close();

  // Ben may move his CMO, but not under Scout, who is not his: he is told so, and nothing moves.
  const ben = await open(browser, {id: 'ben', role: 'viewer', name: 'Ben', email: 'ben@example.test', cloud: true, company_role: 'member'});
  assert.equal(await ben.page.locator('#tree [data-org-menu], #tree [data-org-place]').count(), 0, 'nothing of his to order');
  const before = await shape(ben.page);
  await ben.page.locator('#tree [data-toggle="p:ben"]').first().click();
  await drag(ben.page, node(ben.page, 'b:cmo'), node(ben.page, 'b:scout'));
  await ben.page.locator('.toast.err', {hasText: "You can't put CMO under Scout"}).waitFor();
  assert.deepEqual(ben.calls, []);
  assert.deepEqual(await shape(ben.page), before);
  assert.deepEqual(ben.errors, []);
  await ben.page.close();
}

// Reports nobody placed follow the server's rule (backend/tests/test_report_order.py) on the chart and a person's page:
// humans first, by name with accents and case aside, not as listed; then bots with an `order`, lowest first. A place
// someone saved comes first. In a group, a manager's reports there keep their order and move up and down in it.
async function reportFallback(browser) {
  const {page, errors, calls, people, groups, bots} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
  for (const [id, name] of [['zoe', 'Zoë'], ['eli', 'Éli'], ['bea', 'bea']])
    people.push({id, name, org_parent: 'p:ana', team: '', reports_to: 'ana'});
  for (const [name, display_name, order] of [['analyst', 'Analyst', 2], ['builder', 'Builder', 1]])
    bots.push({...bots.find(b => b.name === 'scout'), name, display_name, order});
  await page.evaluate(() => refresh(true));
  const under = async () => (await shape(page))[0].Ana;
  assert.deepEqual(await under(), ['bea', 'Cara', 'Éli', 'Zoë', 'Builder', 'Analyst', 'Scout']);
  bots.find(b => b.name === 'scout').report_position = 0;
  await page.evaluate(() => refresh(true));
  const chart = ['Scout', 'bea', 'Cara', 'Éli', 'Zoë', 'Builder', 'Analyst'];
  assert.deepEqual(await under(), chart, 'a saved place first, the rest by the same rule');
  await page.goto('https://tico-ui.test/#/person/ana');
  const chips = title => page.locator('#main section.card', {has: page.locator('h2', {hasText: new RegExp('^' + title + '$')})}).locator('a.chip > span:not([class])');
  await chips('Reports').first().waitFor();
  assert.deepEqual(await chips('Reports').allTextContents(), ['bea', 'Ben', 'Cara', 'Éli', 'Zoë'], "Ana's page: the same humans' order (Ben too, in his group on the chart)");
  assert.deepEqual((await chips('Bots').allTextContents()).filter(n => ['Scout', 'Builder', 'Analyst'].includes(n)), ['Scout', 'Builder', 'Analyst'],
    "and the same bots' order");
  // Two of Ana's reports shown in a group of their own: Move down there swaps them in her order.
  await page.goto('https://tico-ui.test/#/updates');
  groups.push({id: 'campaigns', name: 'Campaigns', parent: ''});
  for (const r of [...people, ...bots]) if (['cara', 'builder'].includes(r.id || r.name)) r.team = 'campaigns';
  await page.evaluate(() => refresh(true));
  assert.deepEqual((await shape(page)).find(n => n.Campaigns)?.Campaigns, ['Cara', 'Builder']);
  await node(page, 'p:cara').focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  assert.equal(await page.getByRole('menuitem', {name: 'Move up'}).isDisabled(), true, 'first in the group');
  assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Move down');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.activeElement?.dataset.orgMenu === 'p:cara' && !document.querySelector('#tree .org-order-menu'));
  assert.deepEqual(calls, [['PUT', 'human:ana', {reports: ['bot:scout', 'human:bea', 'human:eli', 'human:zoe', 'bot:analyst', 'bot:builder', 'human:cara']}]]);
  assert.deepEqual((await shape(page)).find(n => n.Campaigns)?.Campaigns, ['Builder', 'Cara']);
  assert.equal(await page.getByRole('button', {name: 'Move Cara Mendes'}).count(), 1);
  assert.deepEqual(errors, []);
  await page.close();
}

async function externalChanges(browser) {
  const {page, errors, calls, people, groups, bots, failures} = await open(browser, {id: 'ana', role: 'owner', cloud: true});
  // Another session changes both the group tree and its members while this chart is open.
  await page.locator('#tree .dept-label', {hasText: /^Marketing$/}).click();
  groups.push({id: 'campaigns', name: 'Campaigns', parent: 'marketing'});
  people.find(p => p.id === 'cara').team = 'campaigns';
  bots.find(b => b.name === 'scout').team = 'campaigns';
  bots.find(b => b.name === 'cmo').display_name = 'Marketing Lead';
  await page.evaluate(() => refresh(false));
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['Marketing Lead']}, {SEO: ['Writer']}, {Campaigns: ['Cara', 'Scout']}]},
    {Sales: []}]);
  assert.equal(await node(page, 'b:scout').isVisible(), false, 'normal polling preserves collapsed groups');
  await page.locator('#tree .dept-label', {hasText: /^Marketing$/}).click();
  assert.equal(await node(page, 'b:scout').isVisible(), true);

  // A partial failure must not mix new bots with old humans/groups. Retry replaces them together.
  const before = await shape(page);
  groups.find(g => g.id === 'campaigns').name = 'Launches';
  bots.find(b => b.name === 'scout').display_name = 'Launch Scout';
  failures.add('/api/humans');
  await page.evaluate(() => refresh(false));
  assert.deepEqual(await shape(page), before);
  failures.clear();
  await page.evaluate(() => refresh(false));
  assert.equal(await row(page, 'Launches').count(), 1);
  assert.equal(await node(page, 'b:scout').locator('.nm').textContent(), 'Launch Scout');
  assert.deepEqual(calls, [], 'polling does not write chart state');
  assert.deepEqual(errors, []);
  await page.close();
}

async function owner(browser) {
  const {page, errors, calls} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
  // Groups nest and hold humans and bots; a member hangs under its manager inside the group; Built-in is outside.
  assert.deepEqual(await shape(page), [
    {Ana: ['Cara', 'Scout']}, {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}, {Sales: []}]);
  // A group collapses.
  await page.locator('#tree .dept-label', {hasText: /^Marketing$/}).click();
  assert.equal(await page.locator('#tree a.node[href="#/bot/writer"]').isVisible(), false);
  await page.locator('#tree .dept-label', {hasText: /^Marketing$/}).click();
  assert.equal(await page.locator('#tree a.node[href="#/bot/writer"]').isVisible(), true);
  assert.equal(await node(page, 'b:botops').count(), 0, 'built-in bots stay out of the chart');
  assert.equal(await page.locator('#nav-botops').getAttribute('href'), '#/bot/botops');
  assert.equal(await page.locator('#tree [data-org="b:inbox"], #tree [data-org="b:channel"]').count(), 0, 'message bots stay off the chart');
  assert.equal(await page.locator('#nav-inboxes-section').isVisible(), false, 'nor are their mailboxes in the sidebar');

  // Add a group even when Team is collapsed and showing history.
  await page.locator('#org-history').click();
  await page.locator('[data-section-toggle=organisation]').click();
  await page.locator('#org-add-group').click();
  await page.getByRole('menuitem', {name: 'Add section', exact: true}).click();
  const field = page.locator('#tree .org-name');
  await field.fill('Legal');
  await field.press('Enter');
  await page.locator('#tree .dept-label', {hasText: /^Legal$/}).waitFor();
  assert.deepEqual(calls.shift(), ['POST', 'groups', {name: 'Legal'}]);
  // Escape adds nothing.
  await page.locator('#org-add-group').click();
  await page.getByRole('menuitem', {name: 'Add section', exact: true}).click();
  await page.locator('#tree .org-name').press('Escape');
  assert.equal(await page.locator('#tree .org-name').count(), 0);
  assert.deepEqual(calls, []);
  // Add one inside a group.
  await row(page, 'Sales').hover();
  await page.locator('[data-group-add=sales]').click();
  await page.locator('#tree .org-name').fill('EMEA');
  await page.locator('#tree .org-name').press('Enter');
  await page.locator('#tree .dept-label', {hasText: /^EMEA$/}).waitFor();
  assert.deepEqual(calls.shift(), ['POST', 'groups', {name: 'EMEA', parent: 'sales'}]);

  // Rename.
  await row(page, 'Sales').hover();
  await page.locator('[data-group-rename=sales]').click();
  assert.equal(await page.locator('#tree .org-name').inputValue(), 'Sales');
  await page.locator('#tree .org-name').fill('Revenue');
  await page.locator('#tree .org-name').press('Enter');
  await page.locator('#tree .dept-label', {hasText: /^Revenue$/}).waitFor();
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {name: 'Revenue'}]);

  // Drag a human, and a bot, into a group.
  await drag(page, node(page, 'p:cara'), row(page, 'Revenue'));
  await page.waitForFunction(() => document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/person/cara"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {add: {people: ['cara']}}]);
  await drag(page, node(page, 'b:scout'), row(page, 'Revenue'));
  await page.waitForFunction(() => document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/bot/scout"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {add: {bots: ['scout']}}]);

  // Nest a group in another.
  await drag(page, row(page, 'Legal'), row(page, 'Revenue'));
  await page.waitForFunction(() => [...document.querySelectorAll('#tree li.org-group')].some(li => li.querySelector(':scope > .noderow .dept-label')?.textContent === 'Revenue' && [...li.querySelectorAll(':scope > ul > li.org-group > .noderow .dept-label')].some(l => l.textContent === 'Legal')));
  assert.deepEqual(calls.shift(), ['PATCH', 'legal', {parent: 'sales'}]);
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]},
    {Revenue: ['Cara', 'Scout', {Legal: []}, {EMEA: []}]}]);

  // Out of a group: onto "No group", which shows while dragging.
  assert.equal(await page.locator('#tree .org-no-group').isVisible(), false);
  await drag(page, node(page, 'p:cara'), page.locator('#tree .org-no-group'));
  await page.waitForFunction(() => !document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/person/cara"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {remove: {people: ['cara']}}]);
  await drag(page, row(page, 'SEO'), page.locator('#tree .org-no-group'));
  await page.waitForFunction(() => [...document.querySelectorAll('#tree > li.org-group > .noderow .dept-label')].some(l => l.textContent === 'SEO'));
  assert.deepEqual(calls.shift(), ['PATCH', 'seo', {parent: ''}]);
  assert.deepEqual(await shape(page), [
    {Ana: ['Cara']}, {Marketing: [{Ben: ['CMO']}]}, {SEO: ['Writer']}, {Revenue: ['Scout', {Legal: []}, {EMEA: []}]}]);
  assert.deepEqual(errors, []);
  await page.close();
}

async function member(browser) {
  const {page, errors} = await open(browser, {id: 'ben', role: 'viewer', name: 'Ben', email: 'ben@example.test', cloud: true, company_role: 'member'});
  // A member may create bots, but has no group-edit handles; an empty group is not shown.
  assert.deepEqual(await shape(page), [
    {Ana: ['Cara', 'Scout']}, {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}]);
  await page.locator('#org-add-group').click();
  assert.deepEqual(await page.getByRole('menuitem').allTextContents(), ['Add bot']);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('[data-group-rename], [data-group-add], [data-group-delete], #tree .org-no-group').count(), 0);
  await page.evaluate(() => orgGroupDelete('marketing'));
  assert.equal(await page.locator('#tree .noderow[data-org^="g:"][draggable="true"]').count(), 0);
  assert.equal(await node(page, 'b:scout').getAttribute('draggable'), null, 'a member moves only what reports up to them');
  assert.deepEqual(errors, []);
  await page.close();
}

async function teamAdd(browser) {
  const {page, errors, calls, failures, bots, seatSave} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
  const choose = async name => {
    await page.locator('#org-add-group').click();
    await page.getByRole('menuitem', {name, exact: true}).click();
  };
  // The plus is reachable by keyboard and cancelling writes nothing.
  await page.locator('#org-add-group').focus();
  await page.keyboard.press('ArrowDown');
  await shot(page, 'team-add-menu-desktop');
  await page.keyboard.press('End');
  assert.equal(await page.getByRole('menuitem', {name: 'Add human'}).evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#org-add-group').evaluate(el => el === document.activeElement), true);
  assert.deepEqual(calls, []);

  // Add bot makes a Recruiting seat first (acceptance 9): it is on the chart before any bot exists, the CEO Bot's draft
  // of its job description arrives while it is open, Recruit offers one recommended candidate with its model, and the
  // pick creates the bot in Onboarding where the seat was.
  await choose('Add bot');
  await shot(page, 'team-add-bot-desktop');
  await page.locator('[data-recruit-new] [name=title]').fill('Ledger');
  await page.locator('[data-recruit-new] [name=reports_to]').selectOption('scout');
  await page.locator('[data-recruit-new] [type=submit]').click();
  await page.locator('[data-recruit-draft]', {hasText: 'is drafting'}).waitFor();
  assert.deepEqual(calls.shift(), ['POST', 'seats', {title: 'Ledger', reports_to: 'scout', description: ''}]);
  assert.deepEqual(calls.shift(), ['POST', 'open', 'seat1', {}]);
  assert.equal(bots.some(b => b.name === 'ledger'), false, 'no bot exists until a candidate is picked');
  await page.locator('#tree a.node.seat[data-seat-open="seat1"]').waitFor({state: 'attached'});
  assert.match(await page.locator('#tree a.node.seat[data-seat-open="seat1"]').textContent(), /Ledger\s*Recruiting/);
  assert.deepEqual(await page.evaluate(() => orgTreeByParent()['b:scout'].map(n => n.kind + ':' + n.id)), ['seat:seat1']);
  await page.locator('[data-recruit-draft]', {hasText: 'Drafted by Strategy Bot'}).waitFor({timeout: 8000});
  assert.equal(await page.locator('#recruit-picker textarea').inputValue(), 'Keeps the books and closes each month.');
  await page.getByRole('button', {name: 'Recruit', exact: true}).click();
  const cards = page.locator('[data-recruit-candidate]');
  await cards.first().waitFor();
  assert.equal(await cards.count(), 2);
  assert.equal(await page.locator('[data-recruit-candidate] .pill', {hasText: 'Recommended'}).count(), 1);
  assert.match(await cards.first().innerText(), /Matches “closes each month”/);
  await shot(page, 'team-add-bot-candidates');
  await cards.first().locator('[data-recruit-model]').selectOption('other-model');
  await cards.first().getByRole('button', {name: 'Pick Bookkeeper'}).click();
  await node(page, 'b:ledger').waitFor({state: 'attached'});
  assert.deepEqual(calls.filter(c => c[1] === 'recruit' || c[1] === 'hire'),
    [['POST', 'recruit', 'seat1', {}], ['POST', 'hire', 'seat1', {template: 'bookkeeping', model: 'other-model'}]]);
  assert.deepEqual(calls.find(c => c[0] === 'hired with'), ['hired with', 'Keeps the books and closes each month.']);
  calls.length = 0;
  await page.waitForFunction(() => location.hash === '#/bot/ledger');
  assert.equal(await page.locator('#tree a.node.seat').count(), 0, 'the filled seat leaves the chart');
  await page.goBack();
  await page.waitForFunction(() => location.hash === '#/updates');
  // A permission lost while the form is open creates nothing, and submitting never navigates the page.
  await choose('Add bot');
  await page.locator('[data-recruit-new] [name=title]').fill('Analyst');
  await page.evaluate(() => { S.me.role = 'viewer'; S.me.can_create_bots = false; });
  await page.locator('[data-recruit-new] [type=submit]').click();
  await page.locator('[data-recruit-error]:not([hidden])').waitFor();
  assert.deepEqual(calls, []);
  assert.equal(new URL(page.url()).hash, '#/updates');
  await page.evaluate(() => { S.me.role = 'owner'; delete S.me.can_create_bots; });
  await page.keyboard.press('Escape');

  // The bot is hired from the words on screen. A reply to an earlier save never marks newer typing saved: one save runs
  // at a time, a second click joins it, and it carries on until the stored text matches.
  await choose('Add bot');
  await page.locator('[data-recruit-new] [name=title]').fill('Payroll');
  await page.locator('[data-recruit-new] [name=description]').fill('Runs payroll.');
  await page.locator('[data-recruit-new] [type=submit]').click();
  await page.locator('[data-recruit-go]').waitFor({state: 'visible'});
  const text = page.locator('#recruit-picker textarea'), saveButton = page.locator('[data-recruit-save]');
  await text.waitFor();
  calls.length = 0;
  let release;
  seatSave.hold = new Promise(resolve => { release = resolve; });
  await text.fill('Runs payroll every two weeks.');
  await saveButton.waitFor({state: 'visible'});
  await saveButton.click();
  await text.fill('Runs payroll every two weeks and files the quarterly taxes.');
  await saveButton.dispatchEvent('click');
  assert.deepEqual(calls.filter(c => c[0] === 'PATCH').length, 1, 'a second Save waits for the first');
  seatSave.hold = null; release();
  await page.locator('[data-recruit-save][hidden]').waitFor({state: 'attached'});
  assert.deepEqual(calls.filter(c => c[0] === 'PATCH').map(c => c[2].description),
    ['Runs payroll every two weeks.', 'Runs payroll every two weeks and files the quarterly taxes.']);
  // Editing after Recruit: Pick saves the edit first; a failed save hires nobody and keeps the text.
  await page.getByRole('button', {name: 'Recruit', exact: true}).click();
  await page.locator('[data-recruit-candidate]').first().waitFor();
  await text.fill('Runs payroll and answers pay questions.');
  seatSave.status = 403;
  await page.getByRole('button', {name: 'Pick Bookkeeper'}).click();
  await page.locator('[data-recruit-status]', {hasText: 'Saving failed'}).waitFor();
  assert.equal(calls.some(c => c[1] === 'hire'), false);
  assert.equal(await text.inputValue(), 'Runs payroll and answers pay questions.');
  assert.equal(await text.evaluate(el => el.readOnly), false);
  seatSave.status = 0;
  await page.getByRole('button', {name: 'Pick Bookkeeper'}).click();
  await node(page, 'b:payroll').waitFor({state: 'attached'});
  const hire = calls.findIndex(c => c[1] === 'hire');
  assert.deepEqual(calls[hire - 1], ['PATCH', 'seat2', {description: 'Runs payroll and answers pay questions.'}]);
  assert.deepEqual(calls[hire + 1], ['hired with', 'Runs payroll and answers pay questions.']);
  calls.length = 0;
  await page.goBack();
  await page.waitForFunction(() => location.hash === '#/updates');

  // A failed add keeps the entered human available for correction and retry.
  await choose('Add human');
  await page.locator('#team-add-human [name=email]').fill('dana@acme.example');
  await page.locator('#team-add-human [name=name]').fill('Dana');
  failures.add('/api/v2/access/humans');
  await page.locator('#team-add-human [type=submit]').click();
  await page.locator('[data-team-error]:not([hidden])').waitFor();
  assert.equal(await page.locator('#team-add-human [name=email]').inputValue(), 'dana@acme.example');
  failures.clear();
  await page.locator('#team-add-human [type=submit]').click();
  await node(page, 'p:new-human').waitFor();
  assert.deepEqual(calls.shift(), ['POST', 'humans', {email: 'dana@acme.example', name: 'Dana'}]);

  // BotOps (for an agent you already run) starts an editable draft, never sends it, and keeps one being written.
  await choose('Add bot');
  await page.locator('[data-recruit-botops]').click();
  const draft = page.locator('#chat-composer .p-text');
  await draft.waitFor();
  assert.match(await draft.inputValue(), /folder path and computer, or GitHub URL/);
  assert.equal(await draft.evaluate(el => el === document.activeElement), true);
  await draft.fill('Keep this existing request.');
  await choose('Add bot');
  await page.locator('[data-recruit-botops]').click();
  assert.equal(await draft.inputValue(), 'Keep this existing request.');
  assert.deepEqual(calls, [], 'the handoff does not send a chat message');

  // The same actions work from the phone drawer and the menu stays within the screen.
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#bot-back-arrow').click();
  await page.waitForFunction(() => location.hash === '#/updates');
  await page.locator('#mobile-more').click();
  await page.locator('#org-add-group').click();
  const bounds = await page.locator('#team-add-menu').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 390 && bounds.y + bounds.height <= 844);
  await shot(page, 'team-add-menu-mobile');
  await page.getByRole('menuitem', {name: 'Add human', exact: true}).click();
  assert.equal(await page.locator('body').evaluate(el => el.classList.contains('drawer')), false);
  assert.equal(await page.locator('#team-add-human [name=email]').evaluate(el => el === document.activeElement), true);
  await shot(page, 'team-add-human-mobile');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('#team-add-dialog'));
  assert.equal(await page.locator('#mobile-more').evaluate(el => el === document.activeElement), true);
  assert.deepEqual(errors, []);
  await page.close();

  // Members see only capabilities granted by the server; revocation blocks even an open form.
  const limited = await open(browser, {id: 'ben', role: 'viewer', can_create_bots: false, can_add_people: true, cloud: true});
  await limited.page.locator('#org-add-group').click();
  assert.deepEqual(await limited.page.getByRole('menuitem').allTextContents(), ['Add human']);
  await limited.page.getByRole('menuitem', {name: 'Add human'}).click();
  await limited.page.locator('#team-add-human [name=email]').fill('coworker@acme.example');
  await limited.page.evaluate(() => { S.me.can_add_people = false; teamAddRefresh(); });
  await limited.page.locator('#team-add-human [type=submit]').click();
  await limited.page.locator('[data-team-error]:not([hidden])').waitFor();
  assert.deepEqual(limited.calls, []);
  assert.equal(await limited.page.locator('#org-add-group').isVisible(), false);
  assert.deepEqual(limited.errors, []);
  await limited.page.close();
}

async function deleteGroups(browser) {
  const clickDelete = async (page, id) => {
    await node(page, 'g:' + id).hover();
    await page.locator(`[data-group-delete="${id}"]`).click();
  };
  for (const me of [{id: 'ana', role: 'owner', cloud: true}, {id: 'ben', role: 'viewer', bot_admin: true, cloud: true}]) {
    const {page, errors, calls, people, groups, bots, deletion} = await open(browser, me);
    const reporting = [...people, ...bots].map(p => [p.id || p.name, p.reports_to]);
    page.once('dialog', async dialog => { assert.match(dialog.message(), /^Delete Sales\? Its teammates and groups move to [^;]+\.$/); await dialog.dismiss(); });
    await clickDelete(page, 'sales');
    assert.deepEqual(calls, [], 'cancellation never sends a delete');

    deletion.status = 403;
    page.once('dialog', dialog => dialog.accept());
    await clickDelete(page, 'sales');
    await page.locator('.toast.err', {hasText: 'Permission denied'}).waitFor();
    assert.equal(await row(page, 'Sales').count(), 1, 'permission loss preserves the displayed group');
    assert.deepEqual(calls.splice(0), [['DELETE', 'sales']]);
    deletion.status = 0;

    groups.push({id: 'links', name: 'Links', parent: 'seo'});
    await page.evaluate(() => refresh(true));
    let release;
    deletion.hold = new Promise(resolve => { release = resolve; });
    page.once('dialog', async dialog => {
      assert.equal(dialog.message(), 'Delete SEO? Its teammates and groups move to Marketing.');
      await dialog.accept();
    });
    await clickDelete(page, 'seo');
    await page.waitForFunction(() => document.querySelector('[data-group-delete=seo]')?.disabled);
    await page.evaluate(() => refresh(false));
    assert.equal(await page.locator('[data-group-delete=seo]').isDisabled(), true);
    await page.evaluate(() => orgGroupDelete('seo'));
    assert.deepEqual(calls, [['DELETE', 'seo']], 'refresh and a repeated invocation cannot duplicate deletion');
    release(); deletion.hold = null;
    await page.locator('[data-group-delete=seo]').waitFor({state: 'detached'});
    assert.equal(groups.find(g => g.id === 'links').parent, 'marketing');
    assert.equal(bots.find(b => b.name === 'writer').team, 'marketing');
    assert.equal(await row(page, 'Marketing').locator('..').locator('[data-org="b:writer"]').count(), 1);

    page.once('dialog', async dialog => { assert.match(dialog.message(), /move to No group/); await dialog.accept(); });
    await clickDelete(page, 'marketing');
    await page.locator('[data-group-delete=marketing]').waitFor({state: 'detached'});
    assert.equal(groups.find(g => g.id === 'links').parent, '');
    assert.equal(people.find(p => p.id === 'ben').team, '');
    assert.equal(bots.find(b => b.name === 'cmo').team, '');
    assert.equal(people.length, 3); assert.equal(bots.length, 6);
    assert.deepEqual([...people, ...bots].map(p => [p.id || p.name, p.reports_to]), reporting);

    // Another session removed Sales; a stale click refreshes the chart and reports the refusal.
    groups.splice(groups.findIndex(g => g.id === 'sales'), 1);
    page.once('dialog', dialog => dialog.accept());
    await clickDelete(page, 'sales');
    await page.locator('.toast.err', {hasText: 'Group not found'}).waitFor();
    assert.equal(await row(page, 'Sales').count(), 0);
    assert.deepEqual(errors, []);
    await page.close();
  }
}

async function personalBranches(browser) {
  for (const [id, role] of [['ana', 'owner'], ['ben', 'viewer'], ['cara', 'viewer']]) {
    const {page, errors} = await open(browser, {id, role, cloud: true}, true);
    for (const history of [false, true]) {
      if (history) await page.locator('#org-history').click();
      assert.equal(await node(page, 'b:cmo').count(), 1, 'the canonical bot stays on the chart');
      for (const person of ['ana', 'ben', 'unknown', 'limited']) {
        const branch = node(page, 'b:cmo-' + person);
        assert.equal(await branch.count(), (person === id || (person === 'limited' && id === 'ben')) ? 1 : 0, 'only the operator sees a branch, including for admins');
        if (person === id) assert.equal(await branch.locator('.org-branch').textContent(), 'Your branch');
      }
    }
    await page.locator('#org-mine').click();
    assert.equal(await node(page, 'b:cmo-unknown').count(), 0);
    const recent = await page.evaluate(() => {
      orgHistoryStore(['b:cmo-ben', 'b:cmo-ana', 'b:cmo-unknown', 'b:cmo'], Date.now());
      return {fan: orgFanBots().map(b => b.slug), chat: orgFanChatBots('scout').map(b => b.slug), total: S.emps.length};
    });
    const expected = ['ana', 'ben'].includes(id) ? ['cmo-' + id, 'cmo'] : ['cmo'];
    assert.deepEqual(recent.fan, expected);
    assert.deepEqual(recent.chat, expected);
    assert.equal(recent.total, 10, 'branch management retains the full readable roster');
    assert.deepEqual(errors, []);
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    await externalChanges(browser);
    await owner(browser);
    await member(browser);
    await teamAdd(browser);
    await deleteGroups(browser);
    await personalBranches(browser);
    await reportOrder(browser);
    await reportFallback(browser);
    console.log('PASS: groups nest in the team chart; owners add, rename, drag into and nest groups; reports keep their order; members read.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
