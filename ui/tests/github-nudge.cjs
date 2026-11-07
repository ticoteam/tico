// The GitHub nudge (ui/app/github-nudge.ts): while GitHub is not connected and installed, a bot someone made and a
// "Build a bot" task say their work is backed up nowhere. The owner gets Connect GitHub, a link to Tools; anyone else
// gets the card with no link. Built-in bots, other tasks and a connected, installed team get no card. Fixtures only,
// no network. TICO_SCREENSHOT_DIR keeps the bot page and the task at desktop and phone width, light and dark.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const task = (id, title, over = {}) => ({id, title, body: 'Details.', owner_id: 'bot:botops', requester_id: 'human:ana', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 1, created_at: ago(30), updated_at: ago(5), ...over});
const TASKS = [
  task('t-build', 'Build a bot: Ops', {body: 'Set up the Ops bot for the release checks.'}),
  task('t-plan', 'Plan the release', {owner_id: 'bot:lead'}),
];
const BOTS = [
  {name: 'ops', display_name: 'Ops', org_parent: '', host: 'keeper', status: 'active', can_chat: true, can_manage: true, runtime: 'codex',
    my_access: {read: true}, users: [{id: 'ana', name: 'Ana'}], schedules: []},
  {name: 'botops', display_name: 'BotOps', org_parent: '', host: 'keeper', status: 'active', can_chat: true, can_manage: true,
    my_access: {read: true}, users: [{id: 'ana', name: 'Ana'}], schedules: []},
];

async function open(browser, {viewport = {width: 1280, height: 900}, colorScheme = 'light', owner = true, connected = false}) {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript(theme => { try { localStorage.setItem('tico.theme', theme); } catch {} }, colorScheme);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
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
  return {page, context, errors};
}

// The repository read the card waits for: a bot's page and a task's page both ask GET /v2/repositories once they draw.
async function visit(page, url, {read = true} = {}) {
  const seen = read ? page.waitForResponse(r => r.url().endsWith('/api/v2/repositories')) : null;
  await page.goto(url);
  if (seen) await seen;
  await page.locator(url.includes('/task/') ? '.tmodal-main' : '#bot-banners').first().waitFor();
  await page.waitForTimeout(200);   // the card fills in the same tick as the read; this lets the render finish
}

const bot = page => page.locator('.gh-nudge');
const connect = page => page.locator('.gh-nudge a[data-gh-connect]');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // The owner of a bot someone made gets the card with the one link, to Tools.
      const {page, context, errors} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await bot(page).count(), 1);
      assert.equal(await connect(page).getAttribute('href'), '#/tools/github');
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Anyone else who can read the bot gets the card with no link to act on.
      const {page, context, errors} = await open(browser, {owner: false});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await bot(page).count(), 1);
      assert.equal(await connect(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // Connected and installed: nothing to nudge.
      const {page, context, errors} = await open(browser, {connected: true});
      await visit(page, 'https://tico-ui.test/#/bot/ops');
      assert.equal(await bot(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // The company's own bots are not someone's to back up here.
      const {page, context, errors} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/bot/botops', {read: false});
      assert.equal(await page.locator('#bot-github-host').count(), 0);
      assert.equal(await bot(page).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      // A Build a bot task gets the card; the owner's link is there, anyone else's is not.
      const {page, context, errors} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await page.locator('[data-gh-nudge-task] .gh-nudge').count(), 1);
      assert.equal(await connect(page).count(), 1);
      assert.deepEqual(errors, []);
      await context.close();
    }
    {
      const {page, context} = await open(browser, {owner: false});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await bot(page).count(), 1);
      assert.equal(await connect(page).count(), 0);
      await context.close();
    }
    {
      // Other tasks have no host at all, and a connected team sees no card on a Build a bot task.
      const {page, context} = await open(browser, {});
      await visit(page, 'https://tico-ui.test/#/task/t-plan', {read: false});
      assert.equal(await page.locator('[data-gh-nudge-task]').count(), 0);
      await context.close();
    }
    {
      const {page, context} = await open(browser, {connected: true});
      await visit(page, 'https://tico-ui.test/#/task/t-build');
      assert.equal(await bot(page).count(), 0);
      await context.close();
    }
    if (shots) {
      // The card at both widths, in both themes; the phone pass also checks the page does not scroll sideways.
      for (const colorScheme of ['light', 'dark']) {
        for (const [label, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]]) {
          const {page, context, errors} = await open(browser, {colorScheme, viewport});
          await visit(page, 'https://tico-ui.test/#/bot/ops');
          if (label === 'phone') {
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
            assert.ok(overflow <= 0, `bot page scrolls sideways by ${overflow}px at phone width`);
          }
          await page.screenshot({path: path.join(shots, `github-nudge-bot-${label}-${colorScheme}.png`)});
          await visit(page, 'https://tico-ui.test/#/task/t-build');
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
