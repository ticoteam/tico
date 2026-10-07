// Offline regression for a human's own agents on the team chart. Fixtures only.
//  - synced Grok Bots are chips beside their human's name (one, then "+N"), not rows under them; one with bots under it
//    stays a row, and the history list still has every bot;
//  - my row offers Connect while none of my tokens has reached Tico; it opens the connect dialog and goes once one has.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

const FULL = {see: true, read: true, write: true};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1300, height: 900}, serviceWorkers: 'block'});
  const errors = [];
  let used = false;
  const people = [
    {id: 'ana', name: 'Ana Rivera', email: 'ana@example.test', org_parent: '', team: ''},
    {id: 'ben', name: 'Ben Cole', org_parent: 'p:ana', team: '', reports_to: 'ana'},
  ];
  const bot = (name, display_name, org_parent, extra = {}) => ({name, display_name, team: '', org_parent, host: 'keeper',
    status: 'active', state: 'active', can_chat: true, my_access: FULL, users: [{id: 'ana', name: 'Ana'}], operator: 'ana',
    revision: 1, reports_to: org_parent.startsWith('p:') ? 'human:' + org_parent.slice(2) : org_parent.slice(2), ...extra});
  const grok = {harness: 'grokbot'};
  const bots = [
    bot('scout', 'Scout', 'p:ana'),
    bot('grok-designer', 'Grok Designer', 'p:ben', grok),
    bot('grok-sync', 'Grok Sync', 'p:ben', grok),
    bot('grok-writer', 'Grok Writer', 'p:ben', grok),
    bot('grok-lead', 'Grok Lead', 'p:ana', grok),
    bot('helper', 'Helper', 'b:grok-lead'),
  ];
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', email: 'ana@example.test', cloud: true, bot_admin: true});
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/me/tokens') return json({tokens: [
      {id: 'old', label: 'Grok · 2026-06-01', last_used: '2026-06-02T00:00:00Z', expires_at: '2026-08-30T00:00:00Z'},
      {id: 'new', label: 'Muse · 2026-10-06', last_used: used ? new Date().toISOString() : null},
    ]});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json({});
  });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/#/updates');
  await page.locator('#tree a.node').first().waitFor();

  // Ben's three Grok Bots: the first by name as a chip, then "+2", and no rows under him.
  const benRow = page.locator('#tree .noderow', {has: page.locator('[data-org="p:ben"]')});
  assert.deepEqual(await benRow.locator('.org-agent').allInnerTexts(), ['Designer', '+2']);
  assert.equal(await benRow.locator('.org-agent').first().getAttribute('href'), '#/bot/grok-designer');
  assert.equal(await page.locator('#tree a.node[data-org="b:grok-designer"]').count(), 0);
  // Ana's Grok Bot with a bot under it stays a row.
  assert.equal(await page.locator('#tree a.node[data-org="b:grok-lead"]').count(), 1);

  // My row: Connect, which opens the dialog; once a token is used it goes.
  const anaRow = page.locator('#tree .noderow', {has: page.locator('[data-org="p:ana"]')});
  await anaRow.locator('.org-connect').waitFor();
  assert.equal(await benRow.locator('.org-connect').count(), 0);
  await anaRow.locator('.org-connect').click();
  const dialog = page.locator('dialog.connect-agent[open]');
  await dialog.waitFor();
  used = true;
  await dialog.locator('[data-close]').click();
  await anaRow.locator('.org-connect').waitFor({state: 'detached'});

  // The history list still has every bot as a row.
  await page.locator('#org-history').click();
  assert.equal(await page.locator('#tree a.node[data-org="b:grok-writer"]').count(), 1);
  assert.equal(await page.locator('#tree .org-agent').count(), 0);

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('agent-chips: ok');
})().catch(error => { console.error(error); process.exit(1); });
