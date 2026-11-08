// The Hire page of a seat under an open manager seat (ticket e9c84fc5, bot-lifecycle-spec.md section 3): one line says who
// the bot reports to until the manager seat is hired, and it hides once the Reports-to choice moves off the seat's own
// line. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const writer = {id: 's-writer', kind: 'seat', stage: 'recruiting', title: 'Content Marketer', description: 'Writes the blog.',
  team: '', reports_to: 'seat:s-head', created_at: '2026-10-24T00:00:00Z', org_parent: 's:s-head', can_edit: true,
  reports_now: {name: 'Ana', until: 'Head of Marketing'}};
const head = {id: 's-head', kind: 'seat', stage: 'recruiting', title: 'Head of Marketing', description: 'Leads marketing.',
  team: '', reports_to: 'human:ana', created_at: '2026-10-24T00:00:00Z', org_parent: 'p:ana'};
const seats = [writer, head];
const candidate = {template: 'content', slug: 'content', name: 'Content Marketer', line: 'Writes the blog.',
  summary: 'Writes the blog.', owns: ['The drafts'], never: [], onboarding: [], prerequisites: [], tags: [],
  model_name: '', model: '', instructions: ''};
const found = {seat: writer, candidates: [candidate], agents: [], models: [], agents_at: 0};

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('hub.collapsed.v3.ana', '[]'));
    await page.route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}], seats, org_groups: []});
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p === '/api/v2/seats/s-writer/recruit') return json(found);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/seat/s-writer/content');
    const line = page.locator('[data-reports-now]');
    await line.waitFor();
    assert.equal((await line.innerText()).trim(), 'Reports to Ana until Head of Marketing is hired.');
    const picked = page.locator('select[name=reports_to]');
    assert.equal(await picked.inputValue(), 'seat:s-head');
    assert.match(await picked.locator('option:checked').innerText(), /^Head of Marketing, not hired yet$/);
    // Move the bot to the owner by hand: the line says nothing, since the choice is no longer the seat's own line.
    await picked.selectOption('human:ana');
    await line.waitFor({state: 'hidden'});
    // Back on the seat's own line, the line returns.
    await picked.selectOption('seat:s-head');
    await line.waitFor({state: 'visible'});
    assert.deepEqual(errors, [], 'no page errors');
    console.log('recruit-hire-line: ok');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
