// Offline: the Team list of a new 0.6 company (the first live one's shape). The owner's own seeded group ("Leadership")
// is not drawn: the bots that report straight to the owner sit at the top of the list beside them. The Built-in group
// lists the built-in bots, starts folded and unfolds. The Team header has no org chart button (Strategy › Org has it).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const FULL = {see: true, read: true, write: true};
const bot = (id: string, display_name: string, team: string, org_parent: string, reports_to: string | null = null, helper = false) =>
  ({id, name: id, slug: id, display_name, team, org_parent, reports_to, helper, host: 'cloud', status: 'active', state: 'active', can_chat: true,
    my_access: FULL, users: [{id: 'chris', name: 'Chris'}], owner_id: 'chris', revision: 1});
const BUILT_INS = [['coo', 'Assistant', 'p:chris'], ['botops', 'BotOps', 'b:coo'], ['task-manager', 'Task Manager', 'p:chris'],
  ['goal-manager', 'Strategy Bot', 'p:chris'], ['librarian', 'Librarian', 'p:chris'], ['tico-counsel', 'Rules Owner', 'p:chris'],
  ['brand', 'Brand Owner', 'p:chris'], ['updates-owner', 'Updates Owner', 'p:chris'], ['automations-owner', 'Automations Owner', 'p:chris']] as const;
const BOTS = [
  ...BUILT_INS.map(([id, name, up]) => bot(id, name, 'built-in', up, null, ['task-manager', 'tico-counsel', 'brand', 'updates-owner', 'automations-owner'].includes(id))),
  bot('hop', 'Head of Product', 'leadership', 'b:goal-manager', 'human:chris'),
  bot('cm', 'Content Marketer', 'leadership', 'p:chris', 'human:chris'),
  bot('sa', 'Support Agent', 'leadership', 'p:chris', 'human:chris'),
  bot('builder', 'Builder', 'engineering', 'b:hop', 'hop'),
];
const GROUPS = [{id: 'built-in', name: 'Built-in', parent: '', order: 0}, {id: 'leadership', name: 'Leadership', parent: '', order: 1},
  {id: 'engineering', name: 'Engineering', parent: '', order: 2}];
const PEOPLE = [{id: 'chris', name: 'Chris', email: 'chris@example.test', title: 'Owner', team: 'leadership', org_parent: '', reports_to: ''}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const p = new URL(route.request().url()).pathname;
      const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'chris', owner_id: 'chris', role: 'owner', name: 'Chris', email: 'chris@example.test', registered: true, config: {version: 'dev', company_name: 'Acme'}});
      if (p === '/api/v2/config') return json({version: 'dev', company_name: 'Acme'});
      if (p === '/api/employees') return json(BOTS);
      if (p === '/api/humans' || p === '/api/people') return json({people: PEOPLE, org_groups: GROUPS, teams: {}});
      if (p === '/api/status') return json({keeper_alive: true, active: [], queued: [], recent_runs: [], health_issues: []});
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/updates');
    await page.waitForSelector('#tree a.node[data-org="b:cm"]');
    const tree = page.locator('#tree');
    // No Leadership group: the owner's direct reports are top-level rows of the list.
    assert.equal(await tree.locator('.dept-name', {hasText: 'Leadership'}).count(), 0, 'no Leadership group');
    for (const id of ['cm', 'sa', 'hop'])
      assert.equal(await page.locator(`#tree > li > .noderow > a.node[data-org="b:${id}"]`).count(), 1, `${id} at the top level`);
    assert.ok(await page.locator('#tree > li.org-group .dept-name', {hasText: 'Engineering'}).isVisible());
    // Built-in lists the built-ins, folded at first, and unfolds.
    const builtIn = page.locator('#tree > li.org-group:has([data-toggle="g:built-in"])');
    assert.equal(await builtIn.locator('a.node').count(), BUILT_INS.length, 'every built-in in the group');
    assert.equal(await builtIn.locator(':scope > ul').isHidden(), true, 'folded by default');
    await builtIn.locator('[data-toggle="g:built-in"]').click();
    await page.waitForFunction(() => !document.querySelector('#tree [data-toggle="g:built-in"]')?.closest('li')?.querySelector(':scope > ul')?.hasAttribute('hidden'));
    for (const [id] of BUILT_INS) assert.ok(await builtIn.locator(`a.node[data-org="b:${id}"]`).isVisible(), `${id} listed`);
    assert.equal(await builtIn.locator('[data-group-rename], [data-group-delete], [data-group-plus]').count(), 0, 'Built-in is Tico\'s own');
    await builtIn.locator('[data-toggle="g:built-in"]').click();
    await page.waitForFunction(() => !!document.querySelector('#tree [data-toggle="g:built-in"]')?.closest('li')?.querySelector(':scope > ul')?.hasAttribute('hidden'));
    // Built-in comes after the teammates.
    const order = await page.locator('#tree > li:not(.org-no-group)').evaluateAll(lis => lis.map(li => li.querySelector('.dept-name, .nm')?.textContent?.trim()));
    assert.equal(order.at(-1), 'Built-in');
    // No org chart button in the Team header.
    assert.equal(await page.locator('#org-chart-link, .org-head a[href="#/strategy/org"]').count(), 0, 'no org chart button');
    assert.deepEqual(errors, []);
    console.log('Team list: no Leadership group, top-level reports, Built-in lists its bots folded, no org chart button');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
