#!/usr/bin/env node
// Regenerates the product screenshots in docs/images/ from demo mode, so they show the product as it is
// today and never a stale or half-loaded page. It boots `python -m backend.demo` (or uses --url), visits a
// fixed list of pages at the chosen widths in the chosen themes, and FAILS, saving nothing further to
// trust, if any page logs a console error, makes a failing request, shows an error banner, or renders an
// icon as its ligature text (the icon font not loaded yet).
//
//   node scripts/screenshots                        # all pages, desktop and phone, both themes -> docs/images
//   node scripts/screenshots --only updates,settings-health  # some pages
//   node scripts/screenshots --url http://127.0.0.1:8765 --out /tmp/shots --widths desktop-1440,phone --themes light
//   node scripts/screenshots --pages-for-diff v0.5.204..origin/main   # the pages a change to ui/ touches (JSON)
//   node scripts/screenshots --list-pages           # the page table: each page's ui/ globs and the files they match
//
// --report FILE also writes {written, problems} as JSON; scripts/release_shots.py reads it.
// Needs `npm ci` and a browser: TICO_BROWSER_CHANNEL=chrome (the default, your own Chrome) or empty for
// Playwright's Chromium (`npx playwright install chromium`, what CI does). TICO_PYTHON picks the interpreter.
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const select = require('./select.js');

const ROOT = path.resolve(__dirname, '../..');
const WIDTHS = {
  desktop: {viewport: {width: 1280, height: 800}, deviceScaleFactor: 1},
  'desktop-1440': {viewport: {width: 1440, height: 900}, deviceScaleFactor: 1},
  phone: {viewport: {width: 390, height: 844}, deviceScaleFactor: 2, isMobile: true, hasTouch: true},
};
const THEMES = {light: 'light', dark: 'dark'};
const DEFAULT_WIDTHS = 'desktop,phone';
const DEFAULT_THEMES = 'light,dark';
// The demo's clock and the browser's: one fixed instant, so the seeded data and every "1d ago" come out the same on
// every run. TICO_DEMO_NOW overrides it; the demo reads the same variable (backend/demo.py demo_now).
const DEMO_NOW = process.env.TICO_DEMO_NOW || '2026-11-06T18:00:00Z';

// The files the Tasks page draws: the Needs you page shows the same list (its route is #/tasks).
const TASKS_UI = [
  'ui/app/tasks.js', 'ui/app/tasks-page.js', 'ui/app/task-*.js', 'ui/app/pull-refresh.js', 'ui/app/hub-v2.js',
  'ui/app/issues.js', 'ui/styles/tasks.css', 'ui/styles/task-*.css', 'ui/styles/board.css', 'ui/styles/hub-v2.css',
];
const BOT_CHAT_UI = [
  'ui/app/bot-*.js', 'ui/bot-files.js', 'ui/bot-tools.js', 'ui/app/chat*.js', 'ui/app/feedback.js',
  'ui/app/slash-commands.js', 'ui/app/visual-*.js', 'ui/app/question-card.js', 'ui/styles/bot-*.css',
  'ui/styles/chat*.css', 'ui/styles/feedback.css', 'ui/styles/visual-*.css',
];
const MEETINGS_UI = [
  'ui/app/meetings*.js', 'ui/app/live-meetings.js', 'ui/meeting-importers.js', 'ui/app/task-meetings.js',
  'ui/styles/meetings.css', 'ui/styles/task-meetings.css',
];
const SETTINGS_UI = ['ui/app/settings.js', 'ui/styles/settings.css'];

// name: the file is <name>-<width>-<theme>.png. route: the hash to open. ready: text that means the page has
// its data. tab: a Settings tab to select first (Settings remembers the last tab in sessionStorage, so a
// capture never assumes the one it wants is the one showing). steps: what to do before the picture (click,
// open a dialog). ui: the files under ui/ that draw the page, as globs (scripts/screenshots/select.js): a change
// to one of them shoots this page, and a change to a file no page claims is listed, never silently dropped.
const PAGES = [
  {name: 'updates', route: '#/updates', ready: 'The rivals sit $20 to $40 above Home Pro, so we still win on price.', ui: ['ui/app/updates.js', 'ui/styles/updates.css']},
  {name: 'tasks', route: '#/tasks', ready: 'Raise Home Pro from $449 to $479 for December?', ui: TASKS_UI, steps: async page => {
    await page.click('#task-scope');
    await page.click('#task-filter-pop [data-task-type="general"]');
    await page.getByText('Draft replies for the open support tickets').first().waitFor();
  }},
  {name: 'needs-you', route: '#/tasks', ready: 'Raise Home Pro from $449 to $479 for December?',
    ui: [...TASKS_UI, 'ui/app/needs-you.js', 'ui/app/decide.js', 'ui/app/question-card.js', 'ui/styles/needs-you.css', 'ui/styles/decide.css']},
  {name: 'org-chart', route: '#/goals', ready: 'Ship 6,000 units and 40 business installs this season',
    ui: ['ui/app/goals.js', 'ui/app/goals-kpi-views.js', 'ui/goals-kpis.js', 'ui/app/redesign.js', 'ui/app/redesign-org.js',
      'ui/app/org-fan.js', 'ui/styles/goals.css', 'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css']},
  // The "Have a draft ready" line shows on desktop only; the empty thread shows at both widths.
  {name: 'bot-chat', route: '#/bot/support', ready: 'No messages yet', ui: BOT_CHAT_UI},
  {name: 'meetings', route: '#/meetings', ready: 'Larkford town bid call', ui: MEETINGS_UI},
  {name: 'meeting', route: async page => '#/meetings?meeting=' + encodeURIComponent(await page.evaluate(async () => {
    const list = await (await fetch('/api/meetings')).json();
    return list.find(m => m.title === 'Weekly ops sync').id;
  })), ready: 'Calibration works first time for 84 percent of owners', ui: MEETINGS_UI, steps: async page => {
    await page.getByText('Let\'s start with the port').first().waitFor();
  }},
  {name: 'usage', route: '#/usage', ready: 'API-equivalent', ui: ['ui/app/usage.js', 'ui/styles/usage.css'], steps: async page => {
    await page.locator('.use-line').first().click();
    await page.locator('.use-col').first().waitFor();
  }},
  {name: 'settings-health', route: '#/settings/health', ready: 'Setup check',
    ui: [...SETTINGS_UI, 'ui/app/settings-storage.js', 'ui/app/settings-release.js', 'ui/app/settings-repositories.js',
      'ui/app/settings-hosted.js', 'ui/health.js', 'ui/runner-updates.js']},
  {name: 'devices', route: '#/settings', tab: 'devices', ready: "Ana's MacBook",
    ui: [...SETTINGS_UI, 'ui/app/settings-devices.js', 'ui/app/computer-detail.js', 'ui/app/cloud-machines.js',
      'ui/app/subscriptions.js', 'ui/runner-updates.js']},
  {name: 'support', route: '#/updates', ready: 'The rivals sit $20 to $40 above Home Pro, so we still win on price.', ui: ['ui/support.js', 'ui/styles/support.css', 'ui/app/updates.js', 'ui/styles/updates.css'],
    steps: async page => {
      await page.evaluate(() => window.supportOpen());
      await page.locator('#support-pop[open] [data-compose] [name=message], #support-pop[open] [data-off]:not([hidden])').first().waitFor();
      await page.waitForTimeout(300);
    }},
  {name: 'tour', route: '#/settings', tab: 'devices', ready: "Ana's MacBook",
    ui: ['ui/app/welcome.js', 'ui/getting-started.js', 'ui/styles/getting-started.css', 'ui/app/settings-devices.js', ...SETTINGS_UI],
    steps: async page => {
      // On a phone the account menu is in the drawer, which stays closed (and inert) until More opens it.
      if (await page.locator('#mobile-more').isVisible()) await page.click('#mobile-more');
      await page.click('#account');
      await page.click('#account-tour');
      await page.locator('.gs-tour-card').waitFor();
      await page.click('[data-tour-next]');
      await page.waitForTimeout(400);
    }},
  {name: 'docs', route: async page => '#/docs/' + encodeURIComponent(await page.evaluate(async () => {
    const {docs} = await (await fetch('/api/v2/docs?limit=500')).json();
    return docs.find(doc => doc.title === 'Returns and replacements').id;
  })), ready: 'Support may send a replacement lens set without asking.', ui: ['ui/docs-*.js', 'ui/styles/hub-v2.css'], steps: async page => {
    if (page.viewportSize().width < 600) {
      await page.locator('#docs-reader .docs-back').click();
      await page.locator('#docs-h-linked').waitFor();
      const folders = page.locator('details[data-docs-folder][open] > summary');
      while (await folders.count()) await folders.first().click();
    }
    await page.locator('#docs-h-internal').waitFor();
    await page.locator('#docs-h-linked').waitFor();
  }},
  {name: 'market', route: '#/strategy/market?note=company%2Fbrightyard', ready: 'It launched a free scene tier for home projectors last month.',
    // The graph lays itself out a frame at a time until it settles. The shot waits until its canvas stops being drawn:
    // two reads 500 ms apart with the same count (capped at 6 s).
    steps: async page => {
      await page.locator('#market-canvas').waitFor();
      let last = -1;
      for (const until = Date.now() + 6000; Date.now() < until;) {
        await page.waitForTimeout(500);
        const draws = await page.evaluate(() => window.__marketDraws || 0);
        if (draws === last) break;
        last = draws;
      }
    },
    ui: ['ui/market-*.js', 'ui/app/redesign.js', 'ui/app/redesign-market.js', 'ui/styles/market-spend.css', 'ui/styles/strategy.css',
      'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css']},
  {name: 'strategy', route: '#/strategy', ready: 'Who we serve',
    ui: ['ui/strategy-page.js', 'ui/app/redesign.js', 'ui/app/redesign-org.js', 'ui/app/redesign-market.js', 'ui/app/strategy-budget.js',
      'ui/styles/strategy.css', 'ui/styles/strategy-budget.css', 'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css']},
  {name: 'growth', route: '#/growth', ready: 'Growth', ui: ['ui/app/growth-*.js', 'ui/app/growth-*.ts', 'ui/app/brand-page.js', 'ui/styles/growth.css']},
  {name: 'finance', route: '#/finance', ready: 'P&L', ui: ['ui/app/finance*.js', 'ui/styles/finance.css']},
  {name: 'rules', route: '#/rules', ready: 'Rules', ui: ['ui/app/org-rules.js', 'ui/app/org-rule-holds.js', 'ui/styles/org-rules.css', 'ui/styles/org-rule-holds.css']},
  {name: 'automations', route: '#/automations', ready: 'Automations', ui: ['ui/app/automations*.js', 'ui/styles/automations.css']},
  {name: 'learnings', route: '#/learnings', ready: 'set the phone on a flat surface before calibrating',
    ui: ['ui/app/learnings.js', 'ui/app/learned.js', 'ui/app/bot-learnings.js', 'ui/styles/learnings.css', 'ui/styles/bot-learnings.css']},
  {name: 'threads', route: '#/threads', ready: 'Threads',
    ui: ['ui/app/threads*.js', 'ui/app/thread-*.js', 'ui/app/catch-up.js', 'ui/app/chat*.js', 'ui/app/feedback.js', 'ui/app/visual-*.js',
      'ui/app/slash-commands.js', 'ui/styles/threads.css', 'ui/styles/thread-*.css', 'ui/styles/chat*.css', 'ui/styles/feedback.css',
      'ui/styles/visual-*.css']},
  {name: 'recruit', route: async page => '#/seat/' + encodeURIComponent(await page.evaluate(async () =>
    (await (await fetch('/api/v2/seats')).json()).seats[0].id)), ready: 'Job post',
    ui: ['ui/app/recruit.js', 'ui/app/team-build.js', 'ui/styles/recruit.css', 'ui/styles/team-build.css']},
];

function argument(name, fallback = '') {
  const at = process.argv.indexOf('--' + name);
  return at > 0 ? process.argv[at + 1] : fallback;
}

// A comma list of names from a fixed set: a typo fails here, not as an empty run.
function choices(flag, fallback, known) {
  const values = argument(flag, fallback).split(',').map(value => value.trim()).filter(Boolean);
  const unknown = values.filter(value => !known.includes(value));
  if (!values.length || unknown.length) {
    throw new Error(`--${flag}: ${unknown.join(', ') || 'no value'} (choose from ${known.join(', ')})`);
  }
  return values;
}

const git = args => execFileSync('git', args, {cwd: process.cwd(), encoding: 'utf8', maxBuffer: 256 << 20});

// Which ui/ files have a changed line that mentions dark (a dark-theme rule or its test), from the diff's own lines.
function darkPaths(base, head) {
  const found = new Set();
  let file = '';
  for (const line of git(['diff', '-U0', '--no-renames', base, head, '--', 'ui']).split('\n')) {
    const header = /^diff --git a\/(\S+) b\/(\S+)/.exec(line);
    if (header) { file = header[2]; continue; }
    const changed = (line.startsWith('+') && !line.startsWith('+++')) || (line.startsWith('-') && !line.startsWith('---'));
    if (changed && /dark/i.test(line)) found.add(file);
  }
  return [...found];
}

function pagesForDiff(range) {
  const [base, head, ...extra] = range.split('..');
  if (!base || !head || extra.length) throw new Error('--pages-for-diff takes <base>..<head>');
  const paths = git(['diff', '--name-only', '--no-renames', base, head, '--']).split('\n').filter(Boolean);
  return {base, head, ...select.selectPages(paths, PAGES, darkPaths(base, head))};
}

// The page table and, for every glob in it, the tracked files it matches: a glob that matches nothing is a stale entry.
function listPages() {
  const tracked = git(['ls-files']).split('\n').filter(Boolean);
  const matching = globs => Object.fromEntries(globs.map(glob => [glob, tracked.filter(file => select.globToRegExp(glob).test(file))]));
  return {
    pages: PAGES.map(page => ({name: page.name, ui: page.ui, matched: matching(page.ui)})),
    shared: matching(select.SHARED),
    unshown: matching(select.UNSHOWN),
    nonRendering: matching(select.NON_RENDERING),
    darkFiles: select.DARK_FILES,
    // A ui/ file no table names: a change to it shoots every page. The test suite fails on any, so it gets a line here.
    unmapped: tracked.filter(file => file.startsWith('ui/') && select.classify(file, PAGES).kind === 'unmapped'),
  };
}

const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer().once('error', reject).listen(0, '127.0.0.1', () => {
    const {port} = server.address();
    server.close(() => resolve(port));
  });
});

async function bootDemo() {
  const port = await freePort();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'tico-shots-'));
  const child = spawn(process.env.TICO_PYTHON || 'python3', ['-W', 'ignore', '-m', 'backend.demo', '--port', String(port), '--data-dir', data],
    {cwd: ROOT, env: {...process.env, TICO_DEMO_NOW: DEMO_NOW}, stdio: ['ignore', 'inherit', 'inherit']});
  const url = `http://127.0.0.1:${port}`;
  for (let tries = 0; tries < 120; tries++) {
    if (child.exitCode !== null) throw new Error('the demo exited with ' + child.exitCode);
    try { if ((await fetch(url + '/healthz')).ok) return {url, stop: () => child.kill('SIGTERM')}; } catch { /* not up yet */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  child.kill('SIGTERM');
  throw new Error('the demo did not start in a minute');
}

// Everything that must be true of a page before it is worth a screenshot.
async function verify(page, problems, label) {
  await page.evaluate(() => document.fonts.ready);
  const found = await page.evaluate(() => {
    const seen = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
    const banners = [...document.querySelectorAll('.err, [role="alert"], .toast, .banner-error')]
      .filter(seen).map(el => el.textContent.trim()).filter(Boolean);
    // An icon whose font has not loaded is its ligature name ("dynamic_feed") in body type: far wider than an icon.
    const icons = [...document.querySelectorAll('.nav-icon, .mobile-nav-icon')].filter(seen)
      .filter(el => el.getBoundingClientRect().width > 48).map(el => el.textContent.trim());
    return {banners, icons, font: document.fonts.check('24px "Material Symbols Outlined"'), text: document.fonts.check('13px Inter'),
            loading: [...document.querySelectorAll('.loading, .skeleton, .market-loading')].filter(seen).length};
  });
  if (!found.font) problems.push(`${label}: the icon font is not loaded`);
  if (!found.text) problems.push(`${label}: the text font is not loaded`);
  for (const text of found.icons) problems.push(`${label}: icon shown as text "${text}"`);
  for (const text of found.banners) problems.push(`${label}: error banner "${text.slice(0, 120)}"`);
  if (found.loading) problems.push(`${label}: still loading`);
}

async function capture() {
  const {chromium} = require('playwright');
  const out = path.resolve(ROOT, argument('out', 'docs/images'));
  const widths = choices('widths', DEFAULT_WIDTHS, Object.keys(WIDTHS));
  const themes = choices('themes', DEFAULT_THEMES, Object.keys(THEMES));
  const only = argument('only') ? choices('only', '', PAGES.map(page => page.name)) : null;
  const pages = PAGES.filter(page => !only || only.includes(page.name));
  fs.mkdirSync(out, {recursive: true});
  const demo = argument('url') ? {url: argument('url').replace(/\/$/, ''), stop() {}} : await bootDemo();
  const channel = process.env.TICO_BROWSER_CHANNEL ?? 'chrome';
  const browser = await chromium.launch({channel: channel || undefined, headless: true});
  const problems = [];
  const written = [];
  try {
    for (const theme of themes) {
      for (const width of widths) {
        const context = await browser.newContext({...WIDTHS[width], colorScheme: THEMES[theme], locale: 'en-US', timezoneId: 'America/Los_Angeles'});
        // ui/theme.js is dark unless this browser saved a choice, so colorScheme alone shot dark for both themes: save the
        // theme before the page's scripts read it. colorScheme stays for the CSS that follows the system.
        await context.addInitScript(saved => { try { localStorage.setItem('tico.theme', saved); } catch { /* no storage */ } }, theme);
        // The ask peek ("Got a quick question for you") shows a few seconds after load: it is not part of any page. And a
        // panel still sliding or fading when a page measures itself (the rail's "More" fold) would be measured at a
        // different size each run, so transitions and animations end at once.
        await context.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
          const style = document.createElement('style');
          style.textContent = '#ask-peek { display: none !important; }'
            + ' *, *::before, *::after { transition: none !important; animation-duration: 0s !important; animation-delay: 0s !important; }';
          document.head.append(style);
        }));
        // The market graph clears its canvas once per frame while its layout runs, and asks for no frame once it settles.
        // Counting those clears is how the shot knows the layout has stopped (its node positions are not visible from here).
        await context.addInitScript(() => {
          const clear = CanvasRenderingContext2D.prototype.clearRect;
          CanvasRenderingContext2D.prototype.clearRect = function (...args) {
            if (this.canvas?.id === 'market-canvas') window.__marketDraws = (window.__marketDraws || 0) + 1;
            return clear.apply(this, args);
          };
        });
        const page = await context.newPage();
        await page.clock.setFixedTime(new Date(DEMO_NOW));
        let where = 'load';
        page.on('console', message => { if (message.type() === 'error') problems.push(`${where}: console error: ${message.text()}`); });
        page.on('pageerror', error => problems.push(`${where}: page error: ${error.message}`));
        // Leaving a page cancels its open event streams; that is not a failure.
        page.on('requestfailed', request => {
          if (request.failure()?.errorText !== 'net::ERR_ABORTED') problems.push(`${where}: request failed: ${request.url()}`);
        });
        page.on('response', response => { if (response.status() >= 400) problems.push(`${where}: HTTP ${response.status()} ${response.url()}`); });
        await page.goto(demo.url + '/');
        await page.locator('#tree a.node').first().waitFor({state: 'attached'});
        // Inter is font-display: optional, so a cold first load keeps the fallback face; every later load has it.
        await page.evaluate(() => document.fonts.ready);
        await page.reload();
        await page.locator('#tree a.node').first().waitFor({state: 'attached'});
        for (const spec of pages) {
          const label = `${spec.name}-${width}-${theme}`;
          where = label;
          // Each page starts from a fresh load: the scroll position, hover, focus, open popovers and transitions the page
          // before left would otherwise differ from run to run, and a same-ref run must shoot the same pictures.
          await page.goto(demo.url + '/');
          await page.locator('#tree a.node').first().waitFor({state: 'attached'});
          // The mouse stays where the page before left it, and a hover state there would differ between runs.
          await page.mouse.move(0, 0);
          // The route renders the page's layout (a "More" fold measures its text), so the font must be in before it does.
          await page.evaluate(() => document.fonts.ready);
          const route = typeof spec.route === 'function' ? await spec.route(page) : spec.route;
          await page.evaluate(hash => { location.hash = hash; }, route);
          // Settings renders after the route, and keeps System, Webhooks and Billing in a More group that starts closed.
          if (spec.tab) {
            const tab = page.locator(`[data-settings-tab="${spec.tab}"]`);
            await page.locator('#settings-more').waitFor({state: 'attached'}).catch(() => {});
            if (!await tab.isVisible()) await page.locator('#settings-more > summary').click().catch(() => {});
            await tab.click().catch(error => problems.push(`${label}: no ${spec.tab} tab: ${error.message.split('\n')[0]}`));
          }
          await page.getByText(spec.ready).first().waitFor({timeout: 20000}).catch(() => problems.push(`${label}: never showed "${spec.ready}"`));
          if (spec.steps) await spec.steps(page).catch(error => problems.push(`${label}: ${error.message.split('\n')[0]}`));
          // Items mark themselves read 700 to 900 ms after they show (ui/app/updates.js), and side panels fill in as their
          // lists arrive, so a shot waits 3 s after the page is ready.
          await page.waitForTimeout(3000);
          // A click scrolls its target into view and a list that loads late moves the page again: every shot starts at the
          // top of the page and of each scroller (the tour keeps the scroll its steps asked for).
          if (spec.name !== 'tour') {
            await page.evaluate(() => {
              for (const el of document.querySelectorAll('*')) { el.scrollTop = 0; el.scrollLeft = 0; }
              window.scrollTo(0, 0);
            });
          }
          await verify(page, problems, label);
          // A focused cell or field draws its focus ring, and animations are finished: neither is part of the page's content.
          await page.evaluate(() => document.activeElement?.blur());
          await page.screenshot({path: path.join(out, label + '.png'), animations: 'disabled', caret: 'hide'});
          written.push(label + '.png');
        }
        await context.close();
      }
    }
  } finally {
    await browser.close();
    demo.stop();
  }
  const unique = [...new Set(problems)];
  if (argument('report')) {
    fs.writeFileSync(path.resolve(argument('report')), JSON.stringify({written, problems: unique}, null, 2) + '\n');
  }
  console.log(`${written.length} screenshots in ${path.relative(ROOT, out) || '.'}`);
  if (unique.length) {
    console.error('\nScreenshots are not trustworthy:\n  ' + unique.join('\n  '));
    process.exit(1);
  }
}

function main() {
  if (process.argv.includes('--list-pages')) {
    console.log(JSON.stringify(listPages(), null, 2));
    return;
  }
  if (argument('pages-for-diff')) {
    console.log(JSON.stringify(pagesForDiff(argument('pages-for-diff')), null, 2));
    return;
  }
  return capture();
}

Promise.resolve().then(main).catch(error => { console.error(error); process.exit(1); });
