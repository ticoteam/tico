// The GitHub nudge (ui/app/github-nudge.ts): while GitHub is not connected and installed, a bot someone made and a
// live "Build a bot" task say their work is backed up nowhere. The owner gets Connect GitHub, a link to Tools; anyone
// else gets the ask to the owner, with no link. Built-in bots, other tasks, done tasks and a connected, installed team
// get no card. The team's answer is read once per page, and visiting Tools › GitHub asks again. Fixtures only, no
// network. TICO_SCREENSHOT_DIR keeps the bot page and the task at desktop and phone width, light and dark.
import type {Browser, Page, LaunchOptions, Route} from 'playwright';
import type * as PageSupport from './support/page.cjs';
import type * as RouteSupport from './support/routes.cjs';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs') as typeof PageSupport;
const {unrouted} = require('./support/routes.cjs') as typeof RouteSupport;
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
async function open(browser: Browser, {viewport = {width: 1280, height: 900}, colorScheme = 'light', owner = true, connected = false, ownerName = 'Ana'}: {viewport?: {width: number; height: number}; colorScheme?: string; owner?: boolean; connected?: boolean; ownerName?: string}) {
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
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
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
