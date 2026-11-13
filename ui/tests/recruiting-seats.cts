import type {Route} from 'playwright';
// Recruiting seats on the sidebar's team chart (bot-lifecycle-spec.md acceptance test 1): each role with no bot yet is a
// row marked "Recruiting" where its reporting line puts it, under its bot, its person or the seat it reports to, with the
// job description on hover. It is never dragged, never a drop target and never in the "recently opened" list. A seat
// under a bot this view leaves out hangs from the next thing up. Fixtures only, no network.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const bot = (name: string, display_name: string, org_parent: string, over: Record<string, unknown> = {}) => ({name, display_name, org_parent, reports_to: org_parent.startsWith('p:') ? 'human:' + org_parent.slice(2) : org_parent.slice(2),
  host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: [], ...over});
const bots = [bot('assistant', 'Assistant', 'p:ana'), bot('botops', 'BotOps', 'p:ana'), bot('sales', 'Head of Sales', 'p:ana')];
const seat = (id: string, title: string, org_parent: string, description = '') => ({id, kind: 'seat', stage: 'recruiting', title, description, team: '',
  reports_to: org_parent.startsWith('p:') ? 'human:' + org_parent.slice(2) : org_parent.startsWith('s:') ? 'seat:' + org_parent.slice(2) : org_parent.slice(2),
  created_at: '2026-10-24T00:00:00Z', org_parent});
const seats = [
  seat('s-eng', 'Head of Engineering', 'p:ana', 'Runs the software factory.\nOwns:\n- the roadmap'),
  seat('s-dev', 'Developer', 's:s-eng', 'Builds features.'),
  seat('s-rep', 'Sales Rep', 'b:sales'),
];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [];
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('hub.collapsed.v3.ana', '[]'));
    await page.route('**/*', (route: Route) => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana', org_parent: ''}], seats, org_groups: []});
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
      if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/tasks');
    const head = page.locator('#tree [data-seat="s-eng"]');
    await head.waitFor();
    assert.equal((await head.locator('.seat-word').innerText()).trim(), '· Recruiting', 'the name ends in Recruiting, no pill');
    assert.match(await head.getAttribute('title'), /^Head of Engineering \(Recruiting\)\n\nRuns the software factory\./);
    assert.equal(await head.getAttribute('draggable'), null, 'a seat is never dragged');
    assert.equal(await head.getAttribute('data-org'), null, 'nor a drop target');
    // Exactly the reporting lines: the Developer seat under the Head of Engineering seat, the rep under the Head of
    // Sales bot, the head reporting to Ana, the top of the chart, so beside her row rather than under it.
    const parentOf = (id: string) => page.locator(`#tree [data-seat="${id}"]`).evaluate((el: Element) => {
      const up = el.closest('li')!.parentElement!.closest('li');
      const node = up && up.querySelector(':scope > .noderow [data-seat], :scope > .noderow [data-org]');
      return node ? (node as HTMLElement).dataset.seat ? 's:' + (node as HTMLElement).dataset.seat : (node as HTMLElement).dataset.org : '';
    });
    assert.equal(await parentOf('s-dev'), 's:s-eng');
    assert.equal(await parentOf('s-rep'), 'b:sales');
    assert.equal(await parentOf('s-eng'), '');
    // Collapsing the head seat folds the seat under it.
    await page.locator('#tree [data-toggle="s:s-eng"]').first().click();
    await page.locator('#tree [data-seat="s-dev"]').waitFor({state: 'hidden'});
    // The "recently opened" list holds bots and people only.
    await page.locator('#org-more').click();
    await page.locator('#team-more-menu [data-org-more="history"]').click();
    await page.locator('#tree.history').waitFor();
    assert.equal(await page.locator('#tree [data-seat]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('recruiting-seats: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
