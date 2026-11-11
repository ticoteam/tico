// The screen after Create (ui/first-run.ts) for the owner: while GitHub is not connected and installed, one row says
// "GitHub: not backed up." with a Connect GitHub link to Tools › GitHub, above the owners and the admin rows. It is gone once GitHub is connected, a failed read shows nothing, nobody else gets it, and
// the line is filled from the nudge's once-per-page read (ui/app/github-nudge.ts): no screen polls for it. Fixtures only,
// no network. TICO_SCREENSHOT_DIR keeps the screen at desktop and phone width, light and dark.
import type {Browser, Page, LaunchOptions, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const shots = process.env.TICO_SCREENSHOT_DIR;

// A team that was created: the built-in Assistant and BotOps, and a bot someone made that still needs its setup.
const BOTS = [
  {id: 'coo', slug: 'coo', display_name: 'Ace', status: 'active', template: 'assistant', onboarding_state: '', setup_task_id: null, repository_present: false},
  {id: 'botops', slug: 'botops', display_name: 'BotOps', status: 'active', template: 'botops', onboarding_state: '', setup_task_id: null, repository_present: true},
  {id: 'support', slug: 'support', display_name: 'Support desk', status: 'active', template: 'support', onboarding_state: 'needs_setup', setup_task_id: null, repository_present: true},
];
const RECORD = {names: {company_name: 'Acme', app_name: 'Acme Hub', assistant_name: 'Ace'},
  answers: {what_we_do: 'Makes lights.', customers: '', team_size: '', work_arrives: [], software_product: '', repetitive_work: '', pains: [], pains_text: '', tools: []},
  selected: {}, completed: '2026-11-07T10:00:00Z', needed: false, home: 'human:ana', bots: BOTS,
  machine: {runners: [{id: 'runner-1', label: "Ana's Mac", online: true}], enrolled: true}};
const CONFIG = {app_name: 'Acme Hub', company_name: 'Acme', owner_name: 'Ana', version: '0.1.0', local: false, onboarding_needed: false,
  changelog: {unread_count: 0}};

// owner: the team's owner, or a teammate who may not see the screen. github: GET /v2/repositories' answer. failRead: that read fails.
async function open(browser: Browser, {viewport = {width: 1440, height: 900}, colorScheme = 'light', owner = true,
  github = {github_connected: false, github_installed: false}, failRead = false}: {viewport?: {width: number; height: number};
  colorScheme?: string; owner?: boolean; github?: {github_connected: boolean; github_installed: boolean}; failRead?: boolean}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript((theme: string) => { try { localStorage.setItem('tico.theme', theme); } catch {} }, colorScheme);
  const page = await context.newPage();
  const errors: string[] = [];
  const counts = {repositories: 0};
  page.on('pageerror', (error: Error) => errors.push(error.message));
  page.on('request', (request: {url(): string}) => { if (new URL(request.url()).pathname === '/api/v2/repositories') counts.repositories += 1; });
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: owner ? 'ana' : 'cara', role: owner ? 'owner' : 'human', name: owner ? 'Ana' : 'Cara',
      email: 'ana@acme.example', cloud: true, registered: true, credential_access: false, config: CONFIG});
    if (p === '/api/v2/config') return json(CONFIG);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana Rivera', email: 'ana@acme.example'},
      {id: 'ben', name: 'Ben Cole', email: 'ben@acme.example'}], org_groups: []});
    if (p === '/api/v2/setup') return json({...RECORD});
    if (p === '/api/v2/bots') return json(BOTS);
    if (p === '/api/employees') return json(BOTS.map(bot => ({...bot, name: bot.slug, reports_to: '', description: '', revision: 5,
      can_chat: true, can_manage: true, host: 'keeper', thread_mode: 'personal', users: [], team: ''})));
    if (p === '/api/v2/providers') return json({configured: true, preset: 'subscriptions-gateway', enabled: ['anthropic', 'openai'], providers: []});
    if (p === '/api/v2/models') return json({models: []});
    if (p === '/api/v2/templates') return json({cards: []});
    if (p === '/api/v2/setup/getting-started') return json({items: [], tour_seen: true});
    if (p === '/api/v2/repositories') return failRead ? json({error: {code: 'internal', retryable: true, detail: 'fixture'}}, 500)
      : json({repositories: [], new_bot_default: null, ...github});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: BOTS.map(bot => ({bot: bot.slug, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, context, errors, counts};
}

// The owner's screen after Create: the read the line waits for, then the render settles.
async function visit(page: Page) {
  const read = page.waitForResponse((r: {url(): string}) => r.url().endsWith('/api/v2/repositories'));
  await page.goto('https://tico-ui.test/#/welcome');
  await read;
  await page.locator('#onb-tasks').waitFor({state: 'attached'});
  await page.waitForTimeout(200);   // the line is filled in the same tick as the read; this lets the render finish
}

declare const ONB: unknown;
declare function onbPoll(state: unknown): Promise<void>;
declare function frGithubHTML(): string;

const line = (page: Page) => page.locator('#fr-github');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    {
      // The owner, GitHub not connected: one line with a secondary link, above the owners row, and still one primary button.
      const {page, context, errors, counts} = await open(browser, {});
      await visit(page);
      await line(page).locator('a').waitFor({state: 'attached'});
      assert.equal(await line(page).isVisible(), true);
      assert.equal(await line(page).textContent(), 'GitHub: not backed up. Connect GitHub');
      assert.equal(await line(page).locator('a').getAttribute('href'), '#/tools/github');
      assert.equal(await line(page).locator('.primary').count(), 0);
      assert.deepEqual(await page.locator('#onb .primary').evaluateAll((els: Element[]) => els.map(el => el.id)), ['onb-tasks']);
      assert.equal(await line(page).evaluate((el: Element) => el.nextElementSibling?.id), 'fr-owners');
      assert.equal(await page.locator('#onb-after > :first-child').first().getAttribute('id'), 'fr-providers');
      // The progress poll redraws the rows; two polls in a row must not ask GitHub again: the answer is read once per page.
      await page.evaluate(async () => { await onbPoll(ONB); await onbPoll(ONB); });
      assert.equal(counts.repositories, 1);
      assert.deepEqual(errors, []);
      await line(page).locator('a').click();
      await page.waitForFunction(() => location.hash === '#/tools/github');
      await context.close();
    }
    {
      // Connected and installed: no line, and no button to connect it.
      const {page, context, errors} = await open(browser, {github: {github_connected: true, github_installed: true}});
      await visit(page);
      assert.equal(await line(page).isHidden(), true);
      assert.equal(await line(page).locator('a').count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Connected but not installed is still missing: the line stays.
      const {page, context, errors} = await open(browser, {github: {github_connected: true, github_installed: false}});
      await visit(page);
      await line(page).locator('a').waitFor({state: 'attached'});
      assert.equal(await line(page).isVisible(), true);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A failed read shows nothing rather than a guess.
      const {page, context, errors} = await open(browser, {failRead: true});
      await visit(page);
      assert.equal(await line(page).isHidden(), true);
      assert.equal(await line(page).locator('a').count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Anyone else is sent away from the screen, and the line is never built for them.
      const {page, context, errors} = await open(browser, {owner: false});
      await page.goto('https://tico-ui.test/#/welcome');
      await page.waitForFunction(() => location.hash === '#/tasks');
      assert.equal(await line(page).count(), 0);
      assert.equal(await page.evaluate(() => frGithubHTML()), '');
      assert.deepEqual(errors, []);
      await context.close();
    }
    if (shots) {
      // The line at both widths, in both themes; the phone pass also checks the page does not scroll sideways.
      for (const colorScheme of ['light', 'dark']) {
        for (const [label, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
          const {page, context, errors} = await open(browser, {colorScheme, viewport});
          await visit(page);
          await line(page).locator('a').waitFor({state: 'attached'});
          if (label === 'phone') {
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
            assert.ok(overflow <= 0, `the screen after Create scrolls sideways by ${overflow}px at phone width`);
          }
          await line(page).scrollIntoViewIfNeeded();
          await page.waitForTimeout(200);
          await page.screenshot({path: path.join(shots, `github-after-create-${label}-${colorScheme}.png`)});
          assert.deepEqual(errors, []);
          await context.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
  console.log('github-after-create: ok');
})().catch(error => {
  console.error(error);
  process.exit(1);
});
