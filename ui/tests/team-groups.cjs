// Offline regression for the team chart's groups (docs/org-chart.md). Fixtures only.
//  - the chart shows groups as nested, collapsible sections holding their humans and bots; a group's members hang
//    under whoever they report to when that one is in the same group; the Built-in bots stay outside every group;
//  - owners and admins add a group, rename it, drag a human or a bot into a group (or out, onto "No group") and
//    nest a group in another; a member has none of those handles.
//  - the top of the chart (the one person nobody is above) is a plain row, with what reports to them beside it: groups
//    first, then humans and bots; a drop on it still reports there, and a saved fold of it hides nothing;
//  - a drop waits for Move in a strip under the row (ui/tests/sidebar-drag-confirm.cjs covers Cancel and Escape);
//  - a manager's reports keep the order they are dragged into, through a reload;
//    a drop onto a manager the viewer may not put reports under says so and moves nothing; reports nobody placed
//    follow the server's rule on the chart and a person's page; reports shown in a group are reordered there.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, beta} = require('./support/page.cjs');
const LEDGER_ID = '01990000-0000-7000-8000-000000001ed9';
const {unrouted} = require('./support/routes.cjs');

const FULL = {see: true, read: true, write: true};
async function shot(page, name) {
  if (!process.env.TICO_SCREENSHOT_DIR) return;
  await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity));
  await page.screenshot({path: path.join(process.env.TICO_SCREENSHOT_DIR, name + '.png')});
}

// The plus menu's hiring items. Build a team and Cloud agents appear only where their pages are loaded.
const hiring = page => page.evaluate(() => ['Post a job', typeof teamBuildOpen === 'function' && 'Build a team',
  typeof cloudAgentsOpen === 'function' && 'Cloud agents', 'From scratch'].filter(Boolean));

async function open(browser, me, branches = false) {
  const page = await browser.newPage({viewport: {width: 1300, height: 900}, serviceWorkers: 'block'});
  // `recruits` holds the applicants calls a seat page makes when it opens (POST .../recruit). They land whenever the
  // page gets them, so they are never mixed into `calls`, which steps compare exactly.
  const errors = [], calls = [], recruits = [], failures = new Set();
  const deletion = {status: 0, hold: null}, seatSave = {status: 0, hire: 0, draftHold: null, draftText: null};
  const people = [
    {id: 'ana', name: 'Ana Rivera', email: 'ana@example.test', org_parent: '', team: '', inbox_bot: 'inbox'},
    {id: 'ben', name: 'Ben Cole', org_parent: 'p:ana', team: 'marketing', reports_to: 'ana'},
    {id: 'cara', name: 'Cara Mendes', org_parent: 'p:ana', team: '', reports_to: 'ana'},
  ];
  const groups = [{id: 'marketing', name: 'Marketing', parent: ''}, {id: 'seo', name: 'SEO', parent: 'marketing'},
                  {id: 'sales', name: 'Sales', parent: ''}];
  const bot = (name, display_name, team, org_parent, extra = {}) => ({name, display_name, team, org_parent, host: 'keeper',
    status: 'active', state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}], owner_id: 'ana', revision: 1, ...extra});
  const bots = [
    bot('cmo', 'CMO', 'marketing', 'p:ben', {reports_to: 'human:ben'}),
    bot('writer', 'Writer', 'seo', 'b:cmo', {reports_to: 'cmo'}),
    bot('scout', 'Scout', '', 'p:ana', {reports_to: 'human:ana'}),
    bot('botops', 'BotOps', '', 'p:ana', {reports_to: 'human:ana', helper: true}),
    bot('inbox', 'Inbox Manager', '', 'p:ana'),
    bot('channel', 'Channel Inbox', 'marketing', 'b:botops', {helper: true}),
  ];
  if (branches) bots.push(
    bot('cmo-ben', 'CMO', 'marketing', 'p:ben', {shared_from: 'cmo', owner_id: 'ben'}),
    bot('cmo-ana', 'CMO', 'marketing', 'p:ana', {shared_from: 'cmo', owner_id: 'ana'}),
    bot('cmo-limited', 'CMO', 'marketing', 'p:ben', {is_branch: true, owner_id: 'ben', my_access: {see: true, read: false, write: false}}),
    bot('cmo-unknown', 'CMO', 'marketing', 'p:ben', {shared_from: 'cmo', owner_id: null}),
  );
  const seats = [];
  const seatView = seat => ({...seat, kind: 'seat', stage: 'recruiting', can_edit: true,
    description_by: seat.description ? 'person' : 'chart'});
  const applicant = (template, name, match, extra = {}) => ({template, name, match, source: 'template', icon: '',
    department: 'Finance', department_id: 'finance', line: name + ' keeps the books.', summary: name + ' keeps the books.',
    tags: ['Ledger', 'Month end'], owns: ['Closes each month'], never: [], onboarding: [], starter: false,
    prerequisites: [{tool: 'hub', label: 'Hub', required: true, state: 'connected'}],
    model: 'fixture-model', model_name: 'Fixture model', effort: '', ...extra});
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
      const seat = {id: 'seat' + (seats.length + 1), team: '', created_at: '2026-10-24T00:00:0' + seats.length + 'Z', ...body};
      seats.push(seat);
      return json(seatView(seat));
    }
    if (p === '/api/v2/seats/draft' && method === 'POST') {
      const body = request.postDataJSON(); calls.push(['POST', 'draft', body]);
      if (seatSave.draftHold) await seatSave.draftHold;
      if (seatSave.draftText) return json({description: seatSave.draftText(body), source: 'catalog'});
      return json({description: body.redraft ? 'Keeps the books, closes each month and files the taxes.' : 'Keeps the books and closes each month.',
        source: 'catalog'});
    }
    if (p === '/api/v2/computers') return json({computers: [{id: 'mac', label: 'Office Mac'}]});
    let s;
    if ((s = p.match(/^\/api\/v2\/seats\/([^/]+)(?:\/(recruit|hire))?$/))) {
      const seat = seats.find(row => row.id === s[1]), body = method === 'GET' ? null : request.postDataJSON();
      if (s[2] === 'recruit') recruits.push(['POST', 'recruit', s[1], body]);
      else if (s[2]) calls.push(['POST', s[2], s[1], body]);
      if (!s[2] && method === 'PATCH') {
        calls.push(['PATCH', s[1], body]);
        if (seatSave.status) return route.fulfill({status: seatSave.status, contentType: 'application/json',
          body: JSON.stringify({error: {detail: 'Saving failed'}})});
        Object.assign(seat, body);
        return json(seatView(seat));
      }
      if (!s[2]) return json(seatView(seat));
      if (s[2] === 'recruit') return json({seat: seatView(seat), models: [{id: 'fixture-model', name: 'Fixture model'}, {id: 'other-model', name: 'Other model'}],
        candidates: [applicant('bookkeeping', 'Bookkeeper', 'strong'), applicant('controller', 'Controller', 'good')]});
      if (seatSave.hire) return route.fulfill({status: seatSave.hire, contentType: 'application/json',
        body: JSON.stringify({error: {detail: 'Hiring failed'}})});
      seat.filled = true;
      // A hired bot's id is not its slug (Ledger's here): the page opens by the id the reply gives.
      const slug = body.name.toLowerCase(), id = slug === 'ledger' ? LEDGER_ID : slug;
      calls.push(['hired with', seat.description]);
      bots.push(bot(slug, body.name, '', seat.reports_to.startsWith('human:') ? 'p:' + seat.reports_to.slice(6) : 'b:' + seat.reports_to,
        {id, slug, reports_to: seat.reports_to, status: 'planned', onboarding_state: 'needs_setup', stage: 'onboarding'}));
      return json({bot: id, slug, name: body.name, seat: seat.id, stage: 'onboarding', reports_to: seat.reports_to, template: body.template, model: body.model});
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
    if ((m = p.match(/^\/api\/v2\/bots\/([^/]+)\/definition$/)) && method === 'POST') {
      const body = request.postDataJSON(), one = bots.find(b => b.name === m[1]); calls.push(['POST', 'definition', m[1], body]);
      one.reports_to = body.reports_to;
      one.org_parent = body.reports_to.startsWith('human:') ? 'p:' + body.reports_to.slice(6) : 'b:' + body.reports_to;
      return json({});
    }
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
    if (p === '/api/v2/assistant') return json({available: true, state: 'active', bot: 'coo', name: 'Assistant', can_turn_on: false, room_id: 'asst', messages: [], earlier: null});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/#/updates');
  await page.locator('#tree a.node').first().waitFor();
  return {page, errors, calls, recruits, people, groups, bots, failures, deletion, seatSave};
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
// Ana is the top of the chart: the humans and bots that report to her sit beside her row, after the groups.
const besideTop = async page => {
  const [top, ...rest] = await shape(page);
  assert.equal(top, 'Ana', 'the top of the chart is a row of its own');
  return rest.filter(n => typeof n === 'string');
};
// How many rows a node is nested in, its own included: 1 at the top.
const level = (page, key) => page.locator(`#tree [data-org="${key}"]`).first().evaluate(el => {
  let n = 0;
  for (let li = el.closest('li'); li; li = li.parentElement.closest('li')) n++;
  return n;
});
const row = (page, name) => page.locator('#tree .noderow[data-org^="g:"]', {has: page.locator('.dept-label', {hasText: new RegExp('^' + name + '$')})});
const node = (page, key) => page.locator(`#tree [data-org="${key}"]`);
const expectValue = (field, value) => field.evaluate((el, want) => new Promise((resolve, reject) => {
  const start = Date.now(), check = () => el.value === want ? resolve() : Date.now() - start > 5000 ? reject(new Error('value: ' + el.value)) : setTimeout(check, 25);
  check();
}), value);
// A real drag, with a pause after it starts: the "No group" row only shows once a drag has begun.
// `edge` 'top' or 'bottom' drops on that edge of a fellow report (before or after it); else on its middle.
async function drag(page, from, to, edge) {
  await from.hover();
  await page.mouse.down();
  await page.mouse.move(40, 40);
  await page.mouse.move(45, 45);
  const box = edge && await to.boundingBox();
  await to.hover(edge ? {position: {x: 20, y: edge === 'top' ? 2 : box.height - 2}} : undefined);
  await page.mouse.up();
}
// A drop waits in a strip under the moved row: its sentence, then Move.
async function confirmMove(page, text) {
  const strip = page.locator('#tree .org-confirm');
  await strip.waitFor();
  assert.equal(await strip.locator('.org-confirm-q').textContent(), text);
  await strip.getByRole('button', {name: 'Move', exact: true}).click();
  await strip.waitFor({state: 'detached'});
}

async function reportOrder(browser) {
  const {page, errors, calls, bots} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
  for (const [name, display_name] of [['analyst', 'Analyst'], ['builder', 'Builder']])
    bots.push({...bots.find(b => b.name === 'scout'), name, display_name});
  await page.evaluate(() => refresh(true));
  const under = () => besideTop(page);
  assert.deepEqual(await under(), ['Cara', 'Analyst', 'Builder', 'Scout'], 'nobody placed them: humans, then names');
  // Dragged onto the top of a fellow report, Scout goes above it, and stays there through a reload.
  await drag(page, node(page, 'b:scout'), node(page, 'b:analyst'), 'top');
  await confirmMove(page, 'Move Scout above Analyst?');
  await page.waitForFunction(() => document.querySelectorAll('#tree [data-org-place]')[1]?.dataset.org === 'b:scout');
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['human:cara', 'bot:scout', 'bot:analyst', 'bot:builder']}]);
  await page.reload();
  await page.locator('#tree a.node').first().waitFor();
  assert.deepEqual(await under(), ['Cara', 'Scout', 'Analyst', 'Builder']);
  // Ordering is by drag only: no Move menu on the rows.
  assert.equal(await page.locator('#tree [data-org-menu], #tree .org-order-menu').count(), 0, 'no Move menu');
  // The bottom of a fellow report puts it below. The strip shows whole above the rows after it and the tree's lines
  // (the list's layout containment clips nothing), with focus on Move; Enter saves.
  await drag(page, node(page, 'b:builder'), node(page, 'b:scout'), 'bottom');
  const strip = page.locator('#tree .org-confirm');
  await strip.waitFor();
  assert.equal(await strip.locator('.org-confirm-q').textContent(), 'Move Builder below Scout?');
  assert.deepEqual(await under(), ['Cara', 'Scout', 'Builder', 'Analyst'], 'the tree shows the new order while it waits');
  assert.equal(await page.evaluate(() => [...document.querySelectorAll('#tree .org-confirm button')].every(b => {
    const r = b.getBoundingClientRect();
    return b.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) && b.contains(document.elementFromPoint(r.left + 4, r.bottom - 3));
  })), true, 'both buttons are on top where they are drawn');
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.orgConfirm), 'go');
  assert.deepEqual(calls, [], 'nothing saved before Move');
  await page.keyboard.press('Enter');
  await strip.waitFor({state: 'detached'});
  assert.deepEqual(calls.shift(), ['PUT', 'human:ana', {reports: ['human:cara', 'bot:scout', 'bot:builder', 'bot:analyst']}]);
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.org), 'b:builder', 'focus back on the moved row');
  assert.deepEqual(await under(), ['Cara', 'Scout', 'Builder', 'Analyst']);
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
  await page.close();

  // Ben may move his CMO, but not under Scout, who is not his: he is told so, and nothing moves.
  const ben = await open(browser, {id: 'ben', role: 'viewer', name: 'Ben', email: 'ben@example.test', cloud: true, company_role: 'member'});
  assert.equal(await ben.page.locator('#tree [data-org-place]').count(), 0, 'nothing of his to order');
  const before = await shape(ben.page);
  await ben.page.locator('#tree [data-toggle="p:ben"]').first().click();
  await drag(ben.page, node(ben.page, 'b:cmo'), node(ben.page, 'b:scout'));
  await ben.page.locator('.toast.err', {hasText: "You can't put CMO under Scout"}).waitFor();
  assert.equal(await ben.page.locator('#tree .org-confirm').count(), 0, 'a refused drop asks nothing');
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
  const under = () => besideTop(page);
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
  // Two of Ana's reports shown in a group of their own: a drop below the other there swaps them in her order.
  await page.goto('https://tico-ui.test/#/updates');
  groups.push({id: 'campaigns', name: 'Campaigns', parent: ''});
  for (const r of [...people, ...bots]) if (['cara', 'builder'].includes(r.id || r.name)) r.team = 'campaigns';
  await page.evaluate(() => refresh(true));
  assert.deepEqual((await shape(page)).find(n => n.Campaigns)?.Campaigns, ['Cara', 'Builder']);
  await drag(page, node(page, 'p:cara'), node(page, 'b:builder'), 'bottom');
  await confirmMove(page, 'Move Cara Mendes below Builder?');
  // Her whole order as the tree shows it: the group (above her other reports at the top) first.
  assert.deepEqual(calls, [['PUT', 'human:ana', {reports: ['bot:builder', 'human:cara', 'bot:scout', 'human:bea', 'human:eli', 'human:zoe', 'bot:analyst']}]]);
  assert.deepEqual((await shape(page)).find(n => n.Campaigns)?.Campaigns, ['Builder', 'Cara']);
  assert.deepEqual(errors, []);
  await page.close();
}

async function externalChanges(browser) {
  const {page, errors, calls, people, groups, bots, failures} = await open(browser, {id: 'ana', role: 'owner', cloud: true});
  // Another session changes both the group tree and its members while this chart is open.
  await row(page, 'Marketing').locator('.chev').click();
  groups.push({id: 'campaigns', name: 'Campaigns', parent: 'marketing'});
  people.find(p => p.id === 'cara').team = 'campaigns';
  bots.find(b => b.name === 'scout').team = 'campaigns';
  bots.find(b => b.name === 'cmo').display_name = 'Marketing Lead';
  await page.evaluate(() => refresh(false));
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['Marketing Lead']}, {SEO: ['Writer']}, {Campaigns: ['Cara', 'Scout']}]},
    {Sales: []}]);
  assert.equal(await node(page, 'b:scout').isVisible(), false, 'normal polling preserves collapsed groups');
  await row(page, 'Marketing').locator('.chev').click();
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
    'Ana', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}, {Sales: []}, 'Cara', 'Scout']);
  // A group collapses.
  await row(page, 'Marketing').locator('.chev').click();
  assert.equal(await page.locator('#tree a.node[href="#/bot/writer"]').isVisible(), false);
  await row(page, 'Marketing').locator('.chev').click();
  assert.equal(await page.locator('#tree a.node[href="#/bot/writer"]').isVisible(), true);
  assert.equal(await node(page, 'b:botops').count(), 0, 'built-in bots stay out of the chart');
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
  await page.locator('[data-group-plus=sales]').click();
  assert.deepEqual(await page.getByRole('menuitem').allTextContents(), [...await hiring(page), 'Add existing bot', 'Add group'], 'a group adds no humans');
  await shot(page, 'group-add-menu-desktop');
  await page.getByRole('menuitem', {name: 'Add group', exact: true}).click();
  await page.locator('#tree .org-name').fill('EMEA');
  await page.locator('#tree .org-name').press('Enter');
  await page.locator('#tree .dept-label', {hasText: /^EMEA$/}).waitFor();
  assert.deepEqual(calls.shift(), ['POST', 'groups', {name: 'EMEA', parent: 'sales'}]);

  // On a narrow rail and on a phone the tools and the fold never push off the row: a long name shortens instead, and
  // the fold still folds. Sales takes a long name for these checks and gets its own back after.
  const LONG = 'Sales and partner operations for enterprise accounts';
  await row(page, 'Sales').hover();
  await page.locator('[data-group-rename=sales]').click();
  await page.locator('#tree .org-name').fill(LONG);
  await page.locator('#tree .org-name').press('Enter');
  await page.locator('#tree .dept-label', {hasText: LONG}).waitFor();
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {name: LONG}]);
  const narrowFold = async margin => {
    await node(page, 'g:sales').hover();
    const fold = await node(page, 'g:sales').evaluate(el => {
      const b = x => x.getBoundingClientRect(), chev = el.querySelector('.chev'), c = b(chev);
      const hit = document.elementFromPoint(c.left + c.width / 2, c.top + c.height / 2);
      return {chev: c.right, side: b(document.querySelector('#side')).right, tools: b(el.querySelector('.org-group-tools')).width,
        truncated: el.querySelector('.dept-name').scrollWidth > el.querySelector('.dept-name').clientWidth, hit: chev.contains(hit)}; });
    assert.ok(fold.tools > 0 && fold.chev <= fold.side - margin, `the fold stays on the row: ${JSON.stringify(fold)}`);
    assert.equal(fold.truncated, true, 'the long name is cut short with an ellipsis');
    assert.equal(fold.hit, true, 'the fold is what a click hits');
    await node(page, 'g:sales').locator('.chev').click();
    assert.equal(await page.locator('#tree .dept-label', {hasText: /^EMEA$/}).isVisible(), false, 'the fold folds the group');
    await node(page, 'g:sales').locator('.chev').click();
    assert.equal(await page.locator('#tree .dept-label', {hasText: /^EMEA$/}).isVisible(), true, 'and unfolds it');
  };
  await page.evaluate(() => document.documentElement.style.setProperty('--rail-left', '190px'));
  await narrowFold(4);
  await page.evaluate(() => document.documentElement.style.removeProperty('--rail-left'));
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#mobile-more').click();
  await node(page, 'g:sales').waitFor({state: 'visible'});
  await narrowFold(0);
  await page.setViewportSize({width: 1300, height: 900});
  // Sales gets its name back.
  await node(page, 'g:sales').hover();
  await page.locator('[data-group-rename=sales]').click();
  await page.locator('#tree .org-name').fill('Sales');
  await page.locator('#tree .org-name').press('Enter');
  await page.locator('#tree .dept-label', {hasText: /^Sales$/}).waitFor();
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {name: 'Sales'}]);
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
  await confirmMove(page, 'Move Cara Mendes into Revenue?');
  await page.waitForFunction(() => document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/person/cara"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {add: {people: ['cara']}}]);
  await drag(page, node(page, 'b:scout'), row(page, 'Revenue'));
  await confirmMove(page, 'Move Scout into Revenue?');
  await page.waitForFunction(() => document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/bot/scout"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {add: {bots: ['scout']}}]);

  // Nest a group in another.
  await drag(page, row(page, 'Legal'), row(page, 'Revenue'));
  await confirmMove(page, 'Move Legal into Revenue?');
  await page.waitForFunction(() => [...document.querySelectorAll('#tree li.org-group')].some(li => li.querySelector(':scope > .noderow .dept-label')?.textContent === 'Revenue' && [...li.querySelectorAll(':scope > ul > li.org-group > .noderow .dept-label')].some(l => l.textContent === 'Legal')));
  assert.deepEqual(calls.shift(), ['PATCH', 'legal', {parent: 'sales'}]);
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]},
    {Revenue: ['Cara', 'Scout', {Legal: []}, {EMEA: []}]}]);

  // Out of a group: onto "No group", which shows while dragging.
  assert.equal(await page.locator('#tree .org-no-group').isVisible(), false);
  await drag(page, node(page, 'p:cara'), page.locator('#tree .org-no-group'));
  await confirmMove(page, 'Move Cara Mendes out of Revenue?');
  await page.waitForFunction(() => !document.querySelector('#tree [data-org="g:sales"]')?.closest('li')?.querySelector('a[href="#/person/cara"]'));
  assert.deepEqual(calls.shift(), ['PATCH', 'sales', {remove: {people: ['cara']}}]);
  await drag(page, row(page, 'SEO'), page.locator('#tree .org-no-group'));
  await confirmMove(page, 'Move SEO out of Marketing?');
  await page.waitForFunction(() => [...document.querySelectorAll('#tree > li.org-group > .noderow .dept-label')].some(l => l.textContent === 'SEO'));
  assert.deepEqual(calls.shift(), ['PATCH', 'seo', {parent: ''}]);
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['CMO']}]}, {SEO: ['Writer']}, {Revenue: ['Scout', {Legal: []}, {EMEA: []}]}, 'Cara']);
  assert.deepEqual(errors, []);
  await page.close();
}

// The top of the chart is not one more level: Ana, whom nobody is above, is a plain row (no chevron), and the groups and
// the humans and bots that report to her sit at her level, at desktop and phone widths. Nothing about whom anyone
// reports to changes: a drop on her row still makes a bot report to her, and her reports keep their drop zones. A fold of
// her row saved before (hub.collapsed.v3.*) hides nothing. Another person at the top does not undo it; with the owner
// not at the top and two others there, the chart nests as before.
async function topOfChart(browser) {
  const {page, errors, calls, people} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true, owner_id: 'ana'});
  for (const width of [1300, 390]) {
    if (width === 390) {
      await page.setViewportSize({width: 390, height: 844});
      await page.locator('#mobile-more').click();
      await node(page, 'p:ana').waitFor({state: 'visible'});
    }
    assert.equal(await page.locator('#tree [data-toggle="p:ana"]').count(), 0, `${width}: no chevron on the top row`);
    assert.equal(await node(page, 'p:ana').evaluate(el => el.closest('li').className), 'org-top');
    for (const key of ['p:ana', 'g:marketing', 'g:sales', 'p:cara', 'b:scout'])
      assert.equal(await level(page, key), 1, `${width}: ${key} is at the top level`);
    assert.equal(await level(page, 'b:cmo'), 3, `${width}: a group's rows still nest`);
    for (const key of ['p:cara', 'b:scout']) assert.equal(await node(page, key).isVisible(), true);
    await shot(page, width === 390 ? 'team-top-mobile' : 'team-top-desktop');
  }
  await page.setViewportSize({width: 1300, height: 900});
  assert.deepEqual(await page.locator('#tree [data-org-place]').evaluateAll(els => els.map(b => [b.dataset.org, b.dataset.orgIn])),
    [['p:cara', 'p:ana'], ['b:scout', 'p:ana']], 'her reports keep their places to drop before and after');

  // A drop on the top row makes the bot report to her.
  await drag(page, node(page, 'b:writer'), node(page, 'p:ana'));
  await confirmMove(page, 'Move Writer under Ana Rivera?');
  await page.waitForFunction(() => S.emps.find(e => e.name === 'writer')?.reports_to === 'human:ana');
  assert.deepEqual(calls.shift(), ['POST', 'definition', 'writer', {reports_to: 'human:ana', expected_revision: 1}]);

  // A fold of her row saved earlier hides nothing.
  await page.evaluate(() => localStorage.setItem('hub.collapsed.v3.ana', JSON.stringify(['p:ana'])));
  await page.reload();
  await node(page, 'p:ana').waitFor();
  for (const key of ['p:cara', 'b:scout', 'g:marketing']) assert.equal(await node(page, key).isVisible(), true, `${key} shows`);

  // A second person nobody is above (a leftover test account, say) does not undo it: the owner stays the top, and the
  // other one is an ordinary row at the top after the owner's reports, with their own report under them.
  people.push({id: 'dana', name: 'Dana Ruiz', org_parent: '', team: ''},
              {id: 'eve', name: 'Eve Park', org_parent: 'p:dana', team: '', reports_to: 'dana'});
  await page.evaluate(() => refresh(true));
  await page.locator('#tree [data-toggle="p:dana"]').waitFor();
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}, {Sales: []}, 'Cara', 'Scout', {Dana: ['Eve']}]);
  assert.equal(await page.locator('#tree [data-toggle="p:ana"]').count(), 0);
  assert.equal(await level(page, 'p:dana'), 1);
  assert.equal(await level(page, 'p:eve'), 2);
  assert.deepEqual(calls, []);
  assert.deepEqual(errors, []);
  await page.close();

  // The owner under someone, with two other people nobody is above: no one is picked as the top, and the chart nests
  // under each as before.
  const ben = await open(browser, {id: 'ben', role: 'viewer', name: 'Ben', email: 'ben@example.test', cloud: true, company_role: 'member', owner_id: 'ana'});
  ben.people.find(p => p.id === 'ana').org_parent = 'p:dana';
  ben.people.find(p => p.id === 'ana').reports_to = 'dana';
  ben.people.push({id: 'dana', name: 'Dana Ruiz', org_parent: '', team: ''}, {id: 'zed', name: 'Zed Ito', org_parent: '', team: ''});
  await ben.page.evaluate(() => refresh(true));
  await ben.page.locator('#tree [data-toggle="p:dana"]').waitFor();
  assert.deepEqual(await shape(ben.page), [
    {Dana: [{Ana: ['Cara', 'Scout']}]}, 'Zed', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}]);
  assert.equal(await ben.page.locator('#tree li.org-top').count(), 0, 'no top row');
  assert.deepEqual(ben.errors, []);
  await ben.page.close();
}

async function member(browser) {
  const {page, errors} = await open(browser, {id: 'ben', role: 'viewer', name: 'Ben', email: 'ben@example.test', cloud: true, company_role: 'member'});
  // A member may create bots, but has no group-edit handles; an empty group is not shown.
  assert.deepEqual(await shape(page), [
    'Ana', {Marketing: [{Ben: ['CMO']}, {SEO: ['Writer']}]}, 'Cara', 'Scout']);
  await page.locator('#org-add-group').click();
  assert.deepEqual(await page.getByRole('menuitem').allTextContents(), await hiring(page));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('[data-group-rename], [data-group-bet], [data-group-delete], #tree .org-no-group').count(), 0);
  // A group's plus still hires into it.
  await row(page, 'Marketing').hover();
  await page.locator('[data-group-plus=marketing]').click();
  assert.deepEqual(await page.getByRole('menuitem').allTextContents(), await hiring(page));
  await page.keyboard.press('Escape');
  await page.evaluate(() => orgGroupDelete('marketing'));
  assert.equal(await page.locator('#tree .noderow[data-org^="g:"][draggable="true"]').count(), 0);
  assert.equal(await node(page, 'b:scout').getAttribute('draggable'), null, 'a member moves only what reports up to them');
  assert.deepEqual(errors, []);
  await page.close();
}

async function teamAdd(browser) {
  const {page, errors, calls, recruits, failures, bots, seatSave} = await open(browser, {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true});
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

  // Post a job makes a Recruiting seat (acceptance 9): leaving the title drafts its description at once, posting puts
  // the seat on the chart before any bot exists and opens its Applicants page, and Hire on a row creates the bot in
  // Onboarding where the seat was, named as the job.
  await choose('Post a job');
  await shot(page, 'team-add-bot-desktop');
  const post = page.locator('#recruit-post');
  await post.locator('[name=title]').fill('Ledger');
  await post.locator('[name=title]').blur();
  await expectValue(post.locator('[name=description]'), 'Keeps the books and closes each month.');
  assert.deepEqual(calls.shift(), ['POST', 'draft', {title: 'Ledger', team: '', reports_to: 'human:ana', redraft: false}]);
  await post.locator('[name=reports_to]').selectOption('scout');
  await post.locator('[data-post-redraft]').click();
  await expectValue(post.locator('[name=description]'), 'Keeps the books, closes each month and files the taxes.');
  assert.deepEqual(calls.shift(), ['POST', 'draft', {title: 'Ledger', team: '', reports_to: 'scout', redraft: true}]);
  await post.locator('[name=description]').fill('Keeps the books and closes each month.');
  await post.locator('[type=submit]').click();
  await page.waitForFunction(() => location.hash === '#/seat/seat1');
  assert.deepEqual(calls.shift(), ['POST', 'seats', {title: 'Ledger', reports_to: 'scout', description: 'Keeps the books and closes each month.'}]);
  assert.equal(bots.some(b => b.name === 'ledger'), false, 'no bot exists until one is hired');
  const rows = page.locator('[data-recruit-row]');
  await page.waitForFunction(() => document.querySelectorAll('[data-recruit-row]').length === 2);
  assert.deepEqual(await rows.evaluateAll(els => els.map(el => el.dataset.recruitRow)), ['bookkeeping', 'controller']);
  await page.locator('#tree a.node.seat[data-seat-open="seat1"] .cnt', {hasText: '2'}).waitFor({state: 'attached'});
  assert.equal(await page.locator('#tree a.node.seat[data-seat-open="seat1"]').getAttribute('href'), '#/seat/seat1');
  assert.deepEqual(await page.evaluate(() => orgTreeByParent()['b:scout'].map(n => n.kind + ':' + n.id)), ['seat:seat1']);
  await shot(page, 'team-add-bot-applicants');
  // Close job asks on the same button and closes nothing on the first click; moving away puts it back.
  const closeJob = page.locator('[data-recruit-close]');
  await closeJob.click();
  assert.equal(await closeJob.textContent(), 'Close job?');
  await closeJob.evaluate(el => el.blur());
  assert.equal(await closeJob.textContent(), 'Close job');
  assert.equal(calls.some(c => c[1] === 'close'), false);
  await page.locator('[data-recruit-hire="bookkeeping"]').click();
  await node(page, 'b:' + LEDGER_ID).waitFor({state: 'attached'});
  await page.waitForFunction(id => location.hash === '#/bot/' + id, LEDGER_ID);
  assert.deepEqual(recruits, [['POST', 'recruit', 'seat1', {}]]);
  assert.deepEqual(calls.filter(c => c[1] === 'hire'),
    [['POST', 'hire', 'seat1', {template: 'bookkeeping', model: '', name: 'Ledger'}]]);
  assert.deepEqual(calls.find(c => c[0] === 'hired with'), ['hired with', 'Keeps the books and closes each month.']);
  calls.length = 0;
  assert.equal(await page.locator('#tree a.node.seat').count(), 0, 'the filled seat leaves the chart');
  await page.evaluate(() => { location.hash = '#/updates'; });
  await page.waitForFunction(() => location.hash === '#/updates');
  // A permission lost while the form is open creates nothing, and submitting never navigates the page.
  await choose('Post a job');
  await post.locator('[name=title]').fill('Analyst');
  await page.evaluate(() => { S.me.role = 'viewer'; S.me.can_create_bots = false; });
  await post.locator('[type=submit]').click();
  await post.locator('[data-post-error]:not([hidden])').waitFor();
  assert.deepEqual(calls.filter(c => c[1] !== 'draft'), []);
  assert.equal(new URL(page.url()).hash, '#/updates');
  await page.evaluate(() => { S.me.role = 'owner'; delete S.me.can_create_bots; });
  await page.keyboard.press('Escape');
  calls.length = 0;

  // A typed description is never replaced by the draft. On one applicant's page the Hire box names the bot, picks its
  // manager and model; a failed hire keeps the choices and says why, and a retry hires with them.
  await choose('Post a job');
  await post.locator('[name=description]').fill('Runs payroll.');
  await post.locator('[name=title]').fill('Payroll');
  await post.locator('[name=title]').blur();
  await page.waitForFunction(() => !document.querySelector('#recruit-post [data-post-redraft]').disabled);
  assert.equal(await post.locator('[name=description]').inputValue(), 'Runs payroll.');
  await post.locator('[type=submit]').click();
  await page.waitForFunction(() => location.hash === '#/seat/seat2');
  await page.locator('[data-recruit-row="controller"] .rc-view').click();
  await page.waitForFunction(() => location.hash === '#/seat/seat2/controller');
  const hireForm = page.locator('[data-recruit-hire-form]');
  await hireForm.locator('[name=name]').fill('Paymaster');
  await hireForm.locator('[name=reports_to]').selectOption('scout');
  await hireForm.locator('.rc-settings summary').click();
  await hireForm.locator('[name=model]').selectOption('other-model');
  await shot(page, 'team-add-bot-applicant');
  calls.length = 0;
  seatSave.hire = 403;
  await hireForm.locator('.rc-hire-go').click();
  await page.locator('[data-hire-error]:not([hidden])', {hasText: 'Hiring failed'}).waitFor();
  assert.equal(await hireForm.locator('[name=name]').inputValue(), 'Paymaster');
  seatSave.hire = 0;
  await hireForm.locator('.rc-hire-go').click();
  await node(page, 'b:paymaster').waitFor({state: 'attached'});
  await page.waitForFunction(() => location.hash === '#/bot/paymaster');
  assert.deepEqual(calls.filter(c => c[0] === 'PATCH'), [['PATCH', 'seat2', {reports_to: 'scout'}]], 'the new manager is saved once');
  assert.deepEqual(calls.filter(c => c[1] === 'hire').map(c => c[3]),
    [{template: 'controller', model: 'other-model', name: 'Paymaster'}, {template: 'controller', model: 'other-model', name: 'Paymaster'}]);
  assert.deepEqual(calls.find(c => c[0] === 'hired with'), ['hired with', 'Runs payroll.']);
  calls.length = 0;
  await page.evaluate(() => { location.hash = '#/updates'; });
  await page.waitForFunction(() => location.hash === '#/updates');

  // Posting while the draft is on its way waits for it, and so does Enter straight from the title: the job is posted
  // with the drafted words, never an empty description.
  const held = () => { let release; seatSave.draftHold = new Promise(resolve => { release = resolve; }); return () => { seatSave.draftHold = null; release(); }; };
  await choose('Post a job');
  await post.locator('[name=title]').fill('Auditor');
  let release = held();
  await post.locator('[name=title]').blur();
  await post.locator('[type=submit]').click();
  await page.waitForTimeout(150);
  assert.equal(calls.some(c => c[1] === 'seats'), false, 'nothing is posted before the draft lands');
  release();
  await page.waitForFunction(() => location.hash === '#/seat/seat3');
  assert.deepEqual(calls.find(c => c[1] === 'seats')[2].description, 'Keeps the books and closes each month.');
  calls.length = 0;
  await page.evaluate(() => { location.hash = '#/updates'; });
  await page.waitForFunction(() => location.hash === '#/updates');
  await choose('Post a job');
  await post.locator('[name=title]').fill('Treasurer');
  await post.locator('[name=title]').press('Enter');
  await page.waitForFunction(() => location.hash === '#/seat/seat4');
  assert.deepEqual(calls.map(c => c[1]), ['draft', 'seats']);
  assert.deepEqual(calls[1][2].description, 'Keeps the books and closes each month.');
  calls.length = 0;
  await page.evaluate(() => { location.hash = '#/updates'; });
  await page.waitForFunction(() => location.hash === '#/updates');
  // Words typed while a Redraft is on its way stay; the late draft does not replace them.
  await choose('Post a job');
  await post.locator('[name=title]').fill('Clerk');
  await post.locator('[name=title]').blur();
  await expectValue(post.locator('[name=description]'), 'Keeps the books and closes each month.');
  release = held();
  await post.locator('[data-post-redraft]').click();
  await post.locator('[name=description]').fill('Files the receipts.');
  release();
  await page.waitForFunction(() => !document.querySelector('#recruit-post textarea').hasAttribute('aria-busy'));
  assert.equal(await post.locator('[name=description]').inputValue(), 'Files the receipts.');
  await page.keyboard.press('Escape');
  calls.length = 0;
  // Drafts that overlap: words drafted for one title are never posted under another, and a draft overtaken by a newer
  // one is never what posting waits for. The title cannot change while the job is being posted.
  seatSave.draftText = body => 'Drafted for ' + body.title + '.';
  const postedAs = async seat => {
    await page.waitForFunction(id => location.hash === '#/seat/' + id, seat);
    const posted = calls.find(c => c[1] === 'seats')[2];
    calls.length = 0;
    await page.evaluate(() => { location.hash = '#/updates'; });
    await page.waitForFunction(() => location.hash === '#/updates');
    return [posted.title, posted.description];
  };
  await choose('Post a job');                                  // A drafted, then the title changed to B and posted
  await post.locator('[name=title]').fill('Cashier');
  await post.locator('[name=title]').blur();
  await expectValue(post.locator('[name=description]'), 'Drafted for Cashier.');
  await post.locator('[name=title]').fill('Payroll clerk');
  release = held();
  await post.locator('[name=title]').press('Enter');
  await page.waitForFunction(() => document.querySelector('#recruit-post [name=title]').readOnly);
  await page.waitForTimeout(150);
  assert.equal(calls.some(c => c[1] === 'seats'), false, 'the earlier title\'s words are not posted');
  release();
  assert.deepEqual(await postedAs('seat5'), ['Payroll clerk', 'Drafted for Payroll clerk.']);
  await choose('Post a job');                                  // A still on its way, overtaken by B, then posted
  release = held();
  await post.locator('[name=title]').fill('Cashier');
  await post.locator('[name=title]').blur();
  await post.locator('[name=title]').fill('Tax preparer');
  await post.locator('[name=title]').press('Enter');
  await page.waitForTimeout(150);
  assert.equal(calls.some(c => c[1] === 'seats'), false);
  release();
  assert.deepEqual(await postedAs('seat6'), ['Tax preparer', 'Drafted for Tax preparer.']);
  seatSave.draftText = null;

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

  // Add existing bot opens the external agent instructions.
  await choose('Add existing bot');
  await page.locator('dialog.connect-agent[open]').waitFor();
  await page.locator('dialog.connect-agent [data-close]').click();
  await page.waitForFunction(() => !document.querySelector('dialog.connect-agent'));
  assert.deepEqual(calls, []);

  // The same actions work from the phone drawer and the menu stays within the screen.
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => { askToggle(false); location.hash = '#/updates'; });
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
    await topOfChart(browser);
    await member(browser);
    await teamAdd(browser);
    await deleteGroups(browser);
    await personalBranches(browser);
    await reportOrder(browser);
    await reportFallback(browser);
    console.log('PASS: groups nest in the team chart; owners add, rename, drag into and nest groups; reports keep their order; members read.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
