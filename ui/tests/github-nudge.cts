// The GitHub nudge (ui/app/github-nudge.ts): while GitHub is not connected and installed, a bot someone made and a
// live "Build a bot" task say their work is backed up nowhere. The owner gets Connect GitHub, a link to Tools; anyone
// else gets the ask to the owner, with no link. Built-in bots, other tasks, done tasks and a connected, installed team
// get no card. Tools carries the same card above its list. The team's answer is read once per page, and visiting
// Tools › GitHub asks again. GitHub is a required setup row: while it is open, Finish setup stays in the owner's
// sidebar even after Don't remind me, which hides only the optional subscription row. Fixtures only, no network. TICO_SCREENSHOT_DIR keeps the bot page and the task at desktop and phone width, light and dark.
import type {Browser, Page, LaunchOptions, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const shots = process.env.TICO_SCREENSHOT_DIR;

const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
const task = (id: string, title: string, over: Record<string, unknown> = {}) => ({id, title, body: 'Details.', owner_id: 'bot:botops', requester_id: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(30), updated_at: ago(5), ...over});
const TASKS = [
  task('t-build', 'Build a bot: Ops', {body: 'Set up the Ops bot for the release checks.'}),
  task('t-plan', 'Plan the release', {owner_id: 'bot:lead'}),
  task('t-done', 'Build a bot: Ops', {status: 'done'}),
];
const BOTS = [
  {name: 'ops', display_name: 'Ops', org_parent: '', host: 'keeper', status: 'active', can_chat: true, can_manage: true, runtime: 'codex',
    my_access: {read: true}, users: [{id: 'ana', name: 'Ana'}], schedules: []},
  {name: 'botops', display_name: 'BotOps', org_parent: '', host: 'keeper', status: 'active', can_chat: true, can_manage: true,
    my_access: {read: true}, users: [{id: 'ana', name: 'Ana'}], schedules: []},
];

// ownerName is the owner's name the config carries ('' for none): a non-owner's card names the owner, or says "the owner".
// started is Getting started's answer (backend/getting_started.py view), which decides Finish setup in the sidebar.
type Started = {items: {id: string; done: boolean; optional: boolean; skipped?: boolean}[]; dismissed: boolean};
async function open(browser: Browser, {viewport = {width: 1280, height: 900}, colorScheme = 'light', owner = true, connected = false, ownerName = 'Ana', started}: {viewport?: {width: number; height: number}; colorScheme?: string; owner?: boolean; connected?: boolean; ownerName?: string; started?: Started}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript((theme: string) => { try { localStorage.setItem('tico.theme', theme); } catch {} }, colorScheme);
  const page = await context.newPage();
  const errors: string[] = [];
  const counts = {repositories: 0};
  page.on('pageerror', (error: Error) => errors.push(error.message));
  page.on('request', (request: {url(): string}) => { if (new URL(request.url()).pathname === '/api/v2/repositories') counts.repositories += 1; });
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico', ...(ownerName ? {owner_name: ownerName} : {})};
    if (p === '/api/me') return json({id: owner ? 'ana' : 'cara', role: owner ? 'owner' : 'human', name: owner ? 'Ana' : 'Cara',
      email: 'ana@example.test', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: BOTS.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/repositories') return json({repositories: [], new_bot_default: null, github_connected: connected, github_installed: connected});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/tool-index') return json({rows: [{id: 'api:crm', kind: 'api', name: 'CRM', href: '#/tools/api/crm', status: {tone: 'ok', text: 'Working'}}], counts: null});
    if (p === '/api/v2/setup') return json({names: {company_name: 'Acme'}, answers: {}, selected: {}, completed: '2026-11-01T10:00:00Z',
      needed: false, home: 'human:ana', bots: [], machine: {runners: [{id: 'r1', label: "Ana's Mac", online: true}], enrolled: true}});
    if (p === '/api/v2/templates') return json({cards: []});
    if (p === '/api/v2/setup/getting-started') return json({tour_seen: true, can_build: true, items: [], dismissed: false, ...started});
    if (p === '/api/v2/tasks') return json({tasks: []});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/), found = one && TASKS.find(t => t.id === one[1]);
    if (found) return json({task: found, children: []});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, context, errors, counts};
}

// The repository read the card waits for: a bot's page and a task's page ask GET /v2/repositories once per page, so a
// later visit in the same page waits for nothing (read: false).
async function visit(page: Page, url: string, {read = true}: {read?: boolean} = {}) {
  const seen = read ? page.waitForResponse((r: {url(): string}) => r.url().endsWith('/api/v2/repositories')) : null;
  await page.goto(url);
  if (seen) await seen;
  await page.locator(url.includes('/task/') ? '.tmodal-main' : '#bot-banners').first().waitFor({state: 'attached'});
  await page.waitForTimeout(200);   // the card fills in the same tick as the read; this lets the render finish
}

// The bot's card sits in its host; a task's card sits in the task's host. The task modal can stay open over the bot
// page, so each card is found in its own host, never by class alone.
const host = (inTask = false) => (inTask ? '[data-gh-nudge-task]' : '#bot-github-host');
const card = (page: Page, inTask = false) => page.locator(`${host(inTask)} .gh-nudge`);
const sentence = (page: Page, inTask = false) => card(page, inTask).locator('span').first().textContent();
const link = (page: Page, inTask = false) => page.locator(`${host(inTask)} .gh-nudge a[data-gh-connect]`);
// Tools: the card in its host above the filters and the list.
const toolsCard = (page: Page) => page.locator('#tools-root [data-tl-github] .gh-nudge');
async function tools(page: Page) {
  const seen = page.waitForResponse((r: {url(): string}) => r.url().endsWith('/api/v2/repositories'));
  await page.goto('https://tico-ui.test/#/tools');
  await seen;
  await page.locator('#tools-root [data-tl-row]').first().waitFor();
  await page.waitForTimeout(200);
}
const finishSetup = (page: Page) => page.locator('#nav-welcome').evaluate((el: HTMLElement) => !el.hidden);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    {
      // The owner of a bot someone made gets the card with the one link, to Tools. The task on the same page draws from
      // the same read, and a visit to Tools › GitHub makes the next draw ask again.
      const {page, context, errors, counts} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await card(page).count(), 1);
      assert.equal(await sentence(page), "Connect GitHub to back up Ops's work.");
      assert.equal(await link(page).count(), 1);
      assert.equal(await link(page).getAttribute('href'), '#/tools/github');
      assert.equal(await link(page).textContent(), 'Connect GitHub');
      assert.deepEqual(errors, []);
      assert.equal(counts.repositories, 1);
      await page.evaluate(() => { location.hash = '#/task/t-build'; });
      await card(page, true).waitFor({state: 'attached'});
      assert.equal(counts.repositories, 1);
      await page.evaluate(() => { location.hash = '#/tools/github'; });
      await page.waitForTimeout(300);
      const beforeReturn = counts.repositories;
      const asked = page.waitForResponse((r: {url(): string}) => r.url().endsWith('/api/v2/repositories'));
      await page.evaluate(() => { location.hash = '#/bot/ops'; });
      await asked;
      await card(page).waitFor({state: 'attached'});
      assert.equal(counts.repositories, beforeReturn + 1);
      await context.close();
    }
    {
      // Anyone else who can read the bot gets the card, asking the owner by name, with no link to act on.
      const {page, context, errors} = await open(browser, {owner: false});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await card(page).count(), 1);
      assert.equal(await sentence(page), 'Ask Ana to connect GitHub to back up Ops\'s work.');
      assert.equal(await link(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // With no owner name to give, the ask says "the owner".
      const {page, context, errors} = await open(browser, {owner: false, ownerName: ''});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await sentence(page), "Ask the owner to connect GitHub to back up Ops's work.");
      assert.equal(await link(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Connected and installed: nothing to nudge.
      const {page, context, errors} = await open(browser, {connected: true});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await card(page).count(), 0);
      assert.equal(await link(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // The company's own bots are not someone's to back up here.
      const {page, context, errors} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/bot/botops', {read: false});
      assert.equal(await page.locator('#bot-github-host').count(), 0);
      assert.equal(await card(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A Build a bot task gets the card; the owner's link is there, anyone else's is not.
      const {page, context, errors} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await card(page, true).count(), 1);
      assert.equal(await sentence(page, true), "Connect GitHub to back up Ops's work.");
      assert.equal(await link(page, true).getAttribute('href'), '#/tools/github');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      const {page, context, errors} = await open(browser, {owner: false});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await sentence(page, true), 'Ask Ana to connect GitHub to back up Ops\'s work.');
      assert.equal(await link(page, true).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Other tasks have no host at all, a done Build a bot task gets none, and a connected team sees no card.
      const {page, context} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/task/t-plan', {read: false});
      assert.equal(await page.locator('[data-gh-nudge-task]').count(), 0);
      await visit(page, 'https://tico-ui.test/#/task/t-done', {read: false});
      assert.equal(await page.locator('[data-gh-nudge-task]').count(), 0);
      await context.close();
    }
    {
      const {page, context} = await open(browser, {connected: true});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await card(page, true).count(), 0);
      await context.close();
    }
    {
      // Tools: the owner's card sits above the filters with the one button to Tools › GitHub; the list is still there.
      const {page, context, errors} = await open(browser, {});
      await tools(page);
      assert.equal(await toolsCard(page).count(), 1);
      assert.equal(await toolsCard(page).locator('span').first().textContent(), 'Bots need GitHub.');
      const button = toolsCard(page).locator('a.primary[data-gh-connect]');
      assert.equal(await button.getAttribute('href'), '#/tools/github');
      assert.equal(await button.textContent(), 'Connect GitHub');
      assert.equal(await page.locator('#tools-root [data-tl-github] + .tt-bar').count(), 1, 'the card is above the filters');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Anyone else is asked to ask the owner, with nothing to press; a connected team sees no card.
      const other = await open(browser, {owner: false});
      await tools(other.page);
      assert.equal(await toolsCard(other.page).locator('span').first().textContent(), 'Ask Ana to connect GitHub.');
      assert.equal(await toolsCard(other.page).locator('a').count(), 0);
      assert.deepEqual(other.errors, []);
      await other.context.close();
      const done = await open(browser, {connected: true});
      await tools(done.page);
      assert.equal(await toolsCard(done.page).count(), 0);
      assert.equal(await done.page.locator('#tools-root [data-tl-github]').count(), 1);
      assert.deepEqual(done.errors, []);
      await done.context.close();
    }
    {
      // Finish setup: hidden for good, it still comes back while GitHub (required) is open; once GitHub is installed the
      // hide holds over the subscription row, which is optional. Not hidden, the subscription row keeps it.
      const row = (id: string, done: boolean, optional: boolean) => ({id, done, optional});
      const cases: [Started, boolean][] = [
        [{items: [row('github', false, false), row('subscription', false, true)], dismissed: true}, true],
        [{items: [row('github', true, false), row('subscription', false, true)], dismissed: true}, false],
        [{items: [row('github', true, false), row('subscription', false, true)], dismissed: false}, true],
      ];
      for (const [started, shown] of cases) {
        const {page, context, errors} = await open(browser, {started, connected: started.items[0]!.done});
        const read = page.waitForResponse((r: {url(): string}) => r.url().endsWith('/api/v2/setup/getting-started'));
        await page.goto('https://tico-ui.test/#/tasks');
        await read;
        await page.waitForTimeout(200);
        assert.equal(await finishSetup(page), shown, JSON.stringify(started));
        // Opened again, the step it lands on has the GitHub row while GitHub is open, and its link goes to Tools › GitHub.
        if (shown) {
          await page.locator('#nav-welcome').click();
          await page.locator('dialog#onb[open] #onb-run-lines').waitFor();
          const github = page.locator('#onb-github a');
          if (started.items[0]!.done) assert.equal(await github.count(), 0);
          else {
            await github.waitFor();
            assert.equal(await github.getAttribute('href'), '#/tools/github');
            await github.click();
            await page.locator('dialog#onb').waitFor({state: 'detached'});
            assert.equal(await page.evaluate(() => location.hash), '#/tools/github');
          }
        }
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    if (shots) {
      // The card at both widths, in both themes; the phone pass also checks the page does not scroll sideways.
      for (const colorScheme of ['light', 'dark']) {
        for (const [label, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
          const {page, context, errors} = await open(browser, {colorScheme, viewport});
          await visit(page, 'https://tico-ui.test/#/bot/ops');
          if (label === 'phone') {
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
            assert.ok(overflow <= 0, `bot page scrolls sideways by ${overflow}px at phone width`);
          }
          await page.screenshot({path: path.join(shots, `github-nudge-bot-${label}-${colorScheme}.png`)});
          await page.evaluate(() => { location.hash = '#/task/t-build'; });
          await card(page, true).waitFor({state: 'attached'});
          await page.waitForTimeout(200);
          await page.screenshot({path: path.join(shots, `github-nudge-task-${label}-${colorScheme}.png`)});
          await page.evaluate(() => { location.hash = '#/tools'; });
          await toolsCard(page).waitFor({state: 'attached'});
          await page.waitForTimeout(200);
          if (label === 'phone') {
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
            assert.ok(overflow <= 0, `Tools scrolls sideways by ${overflow}px at phone width`);
          }
          await page.screenshot({path: path.join(shots, `github-nudge-tools-${label}-${colorScheme}.png`)});
          assert.deepEqual(errors, []);
          await context.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
  console.log('github-nudge: ok');
})().catch(error => {
  console.error(error);
  process.exit(1);
});
