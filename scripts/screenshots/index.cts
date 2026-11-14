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
import type {AddressInfo} from 'node:net';
import type {Page as BrowserPage, ViewportSize} from 'playwright';
import type {Classification, Page as SelectPage, Selection} from './select.cts';

// The page globals the capture reads and the demo's pages provide. File-local, not a global augmentation: every script
// under scripts/ shares one program (scripts/tsconfig.json).
declare const window: Window & typeof globalThis & {
  supportOpen: () => void;
  __marketDraws?: number;
};

const {spawn, execFileSync}: typeof import('node:child_process') = require('node:child_process');
const fs: typeof import('node:fs') = require('node:fs');
const net: typeof import('node:net') = require('node:net');
const os: typeof import('node:os') = require('node:os');
const path: typeof import('node:path') = require('node:path');
// The module is CommonJS (module.exports), so its shape is written out here rather than read from its exports.
interface SelectModule {
  SHARED: string[];
  UNSHOWN: string[];
  NON_RENDERING: string[];
  DARK_FILES: string[];
  globToRegExp: (glob: string) => RegExp;
  classify: (file: string, pages: SelectPage[]) => Classification;
  selectPages: (paths: string[], pages: SelectPage[], darkPaths?: string[]) => Selection;
}
const select: SelectModule = require('./select.cts');

// One width the pages are shot at: the browser viewport and pixel density, and touch for the phone.
interface WidthSpec {
  viewport: {width: number; height: number};
  deviceScaleFactor: number;
  isMobile?: boolean;
  hasTouch?: boolean;
}

// One entry of PAGES. route is the hash to open (or a function that finds it in the demo data). steps run before the
// picture and may click or wait.
interface PageSpec {
  name: string;
  route: string | ((page: BrowserPage) => Promise<string>);
  ready: string;
  tab?: string;
  steps?: (page: BrowserPage) => Promise<void>;
  ui: string[];
}

// The demo's meetings and docs, as the APIs return them: only the fields the pages look up.
interface Meeting {
  id: string;
  title: string;
}

interface Doc {
  id: string;
  title: string;
}

interface Seat {
  id: string;
}

// The page table as --list-pages prints it.
interface PageListing {
  pages: {name: string; ui: string[]; matched: Record<string, string[]>}[];
  shared: Record<string, string[]>;
  unshown: Record<string, string[]>;
  nonRendering: Record<string, string[]>;
  darkFiles: string[];
  unmapped: string[];
}

// A running demo server and how to stop it.
interface Demo {
  url: string;
  stop: () => void;
}

const ROOT = path.resolve(__dirname, '../..');
const WIDTHS: Record<string, WidthSpec> = {
  desktop: {viewport: {width: 1280, height: 800}, deviceScaleFactor: 1},
  'desktop-1440': {viewport: {width: 1440, height: 900}, deviceScaleFactor: 1},
  phone: {viewport: {width: 390, height: 844}, deviceScaleFactor: 2, isMobile: true, hasTouch: true},
};
const THEMES: Record<string, 'light' | 'dark'> = {light: 'light', dark: 'dark'};
const DEFAULT_WIDTHS = 'desktop,phone';
const DEFAULT_THEMES = 'light,dark';
// The demo's clock and the browser's: one fixed instant, so the seeded data and every "1d ago" come out the same on
// every run. TICO_DEMO_NOW overrides it; the demo reads the same variable (backend/demo.py demo_now).
const DEMO_NOW = process.env.TICO_DEMO_NOW || '2026-11-06T18:00:00Z';

// The files the Tasks page draws: the Needs you page shows the same list (its route is #/tasks).
const TASKS_UI = [
  'ui/app/stage-rules.ts', 'ui/styles/stage-rules.css',  // each stage header's Rules button and panel
  'ui/app/tasks.ts', 'ui/app/tasks-page.ts', 'ui/app/task-*.ts', 'ui/app/pull-refresh.ts', 'ui/app/hub-v2.ts',
  'ui/app/issues.ts', 'ui/styles/tasks.css', 'ui/styles/task-*.css', 'ui/styles/board.css', 'ui/styles/hub-v2.css',
  'ui/app/pipeline-*.ts', 'ui/styles/pipeline.css',
  'ui/app/give-to.ts', 'ui/styles/give-to.css',  // Give to…: a task's Owner, New task, Ask someone, Subtask (and Needs you)
];
const BOT_CHAT_UI = [
  'ui/app/bot-*.js', 'ui/app/bot-*.ts', 'ui/bot-files.ts', 'ui/bot-tools.ts', 'ui/app/chat*.ts', 'ui/app/feedback.ts',
  'ui/app/slash-commands.ts', 'ui/app/visual-*.ts', 'ui/app/github-nudge.ts', 'ui/styles/bot-*.css',
  'ui/styles/chat*.css', 'ui/styles/feedback.css', 'ui/styles/visual-*.css',
];
const MEETINGS_UI = [
  'ui/app/meetings*.ts', 'ui/app/live-meetings.ts', 'ui/meeting-importers.ts', 'ui/app/task-meetings.ts',
  'ui/styles/meetings.css', 'ui/styles/task-meetings.css',
];
const SETTINGS_UI = ['ui/app/settings.ts', 'ui/styles/settings.css'];

// name: the file is <name>-<width>-<theme>.png. route: the hash to open. ready: text that means the page has
// its data. tab: a Settings tab to select first (Settings remembers the last tab in sessionStorage, so a
// capture never assumes the one it wants is the one showing). steps: what to do before the picture (click,
// open a dialog). ui: the files under ui/ that draw the page, as globs (scripts/screenshots/select.cts): a change
// to one of them shoots this page, and a change to a file no page claims is listed, never silently dropped.
const PAGES: PageSpec[] = [
  {name: 'updates', route: '#/updates', ready: 'The rivals sit $20 to $40 above Home Pro, so we still win on price.', ui: ['ui/app/updates.ts', 'ui/styles/updates.css']},
  {name: 'tasks', route: '#/tasks', ready: 'Raise Home Pro from $449 to $479 for December?', ui: TASKS_UI, steps: async page => {
    await page.click('#task-scope');
    await page.click('#task-filter-pop [data-task-type="general"]');
    await page.getByText('Draft replies for the open support tickets').first().waitFor();
  }},
  {name: 'needs-you', route: '#/tasks', ready: 'Raise Home Pro from $449 to $479 for December?',
    ui: [...TASKS_UI, 'ui/app/needs-you.ts', 'ui/app/decide.ts', 'ui/app/decide-content.ts', 'ui/app/decide-about.ts', 'ui/styles/needs-you.css', 'ui/styles/decide.css']},
  {name: 'org-chart', route: '#/goals', ready: 'Ship 6,000 units and 40 business installs this season',
    ui: ['ui/app/goals.ts', 'ui/app/goals-kpi-views.ts', 'ui/goals-kpis.ts', 'ui/app/redesign.ts', 'ui/app/redesign-org.ts',
      'ui/app/drag-pan.ts', 'ui/app/org-fan.ts', 'ui/app/org-factories.ts', 'ui/styles/goals.css', 'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css',
      'ui/styles/org-factories.css']},
  // The "Have a draft ready" line shows on desktop only; the empty thread shows at both widths.
  {name: 'bot-chat', route: '#/bot/support-owner', ready: 'No messages yet', ui: BOT_CHAT_UI},
  {name: 'meetings', route: '#/meetings', ready: 'Larkford town bid call', ui: MEETINGS_UI},
  {name: 'meeting', route: async page => '#/meetings?meeting=' + encodeURIComponent(await page.evaluate(async () => {
    const list = (await (await fetch('/api/meetings')).json()) as Meeting[];
    return (list.find(m => m.title === 'Weekly ops sync')!).id;
  })), ready: 'Calibration works first time for 84 percent of owners', ui: MEETINGS_UI, steps: async page => {
    await page.getByText('Let\'s start with the port').first().waitFor();
  }},
  {name: 'usage', route: '#/usage', ready: 'API-equivalent', ui: ['ui/app/usage.ts', 'ui/styles/usage.css'], steps: async page => {
    await page.locator('.use-line').first().click();
    await page.locator('.use-col').first().waitFor();
  }},
  {name: 'settings-health', route: '#/settings/health', ready: 'Setup check',
    ui: [...SETTINGS_UI, 'ui/app/settings-storage.ts', 'ui/app/settings-release.ts', 'ui/app/settings-repositories.ts',
      'ui/app/settings-hosted.ts', 'ui/health.ts', 'ui/runner-updates.ts']},
  {name: 'devices', route: '#/settings', tab: 'devices', ready: "Ana's MacBook",
    ui: [...SETTINGS_UI, 'ui/app/settings-devices.ts', 'ui/app/computer-detail.ts', 'ui/app/cloud-machines.ts',
      'ui/app/subscriptions.ts', 'ui/app/plan-signin.ts', 'ui/runner-updates.ts']},
  {name: 'support', route: '#/updates', ready: 'The rivals sit $20 to $40 above Home Pro, so we still win on price.', ui: ['ui/support.ts', 'ui/styles/support.css', 'ui/app/updates.ts', 'ui/styles/updates.css'],
    steps: async page => {
      await page.evaluate(() => window.supportOpen());
      await page.locator('#support-pop[open] [data-compose] [name=message], #support-pop[open] [data-off]:not([hidden])').first().waitFor();
      await page.waitForTimeout(300);
    }},
  {name: 'tour', route: '#/settings', tab: 'devices', ready: "Ana's MacBook",
    ui: ['ui/app/welcome.ts', 'ui/getting-started.ts', 'ui/styles/getting-started.css', 'ui/app/settings-devices.ts', ...SETTINGS_UI],
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
    const {docs} = (await (await fetch('/api/v2/docs?limit=500')).json()) as {docs: Doc[]};
    return (docs.find(doc => doc.title === 'Returns and replacements') as Doc).id;
  })), ready: 'Support may send a replacement lens set without asking.', ui: ['ui/docs-*.ts', 'ui/styles/hub-v2.css'], steps: async page => {
    if ((page.viewportSize() as ViewportSize).width < 600) {
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
    ui: ['ui/market-*.ts', 'ui/app/redesign.ts', 'ui/app/redesign-market.ts', 'ui/styles/market-spend.css', 'ui/styles/strategy.css',
      'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css']},
  {name: 'strategy', route: '#/strategy', ready: 'Who we serve',
    ui: ['ui/strategy-page.ts', 'ui/app/redesign.ts', 'ui/app/redesign-org.ts', 'ui/app/redesign-market.ts', 'ui/app/strategy-budget.ts',
      'ui/styles/strategy.css', 'ui/styles/strategy-budget.css', 'ui/styles/redesign.css', 'ui/styles/redesign-strategy.css']},
  {name: 'growth', route: '#/growth', ready: 'Growth', ui: ['ui/app/growth-*.ts', 'ui/app/brand-page.ts', 'ui/styles/growth.css']},
  {name: 'finance', route: '#/finance', ready: 'P&L', ui: ['ui/app/finance*.js', 'ui/app/finance*.ts', 'ui/styles/finance.css']},
  {name: 'rules', route: '#/rules', ready: 'Rules', ui: ['ui/app/org-rules.ts', 'ui/app/drag-pan.ts', 'ui/app/org-rule-holds.ts', 'ui/styles/org-rules.css', 'ui/styles/org-rule-holds.css',
    // The stage rules panel opens from Rules › Pipelines and stages and from each stage header on the board.
    'ui/app/stage-rules.ts', 'ui/styles/stage-rules.css']},
  {name: 'contacts', route: '#/library/contacts', ready: 'Bright Homes Retail', ui: ['ui/app/directory*.ts', 'ui/styles/directory.css']},
  {name: 'automations', route: '#/automations', ready: 'Automations', ui: ['ui/app/automations*.js', 'ui/app/automations*.ts', 'ui/styles/automations.css']},
  {name: 'listening', route: '#/listening', ready: 'Listening', ui: ['ui/app/listening.ts', 'ui/styles/listening.css', 'ui/app/page-chat.ts', 'ui/styles/page-chat.css', 'ui/app/empty-art.ts']},
  {name: 'learnings', route: '#/learnings', ready: 'set the phone on a flat surface before calibrating',
    ui: ['ui/app/learnings.ts', 'ui/app/learned.ts', 'ui/app/assistant-viewers.ts', 'ui/app/bot-learnings.ts', 'ui/styles/learnings.css', 'ui/styles/bot-learnings.css', 'ui/app/empty-art.ts']},
  {name: 'threads', route: '#/threads', ready: 'Threads',
    ui: ['ui/app/threads*.ts', 'ui/app/thread-*.ts', 'ui/app/catch-up.ts', 'ui/app/chat*.ts', 'ui/app/feedback.ts', 'ui/app/visual-*.ts',
      'ui/app/slash-commands.ts', 'ui/styles/threads.css', 'ui/styles/thread-*.css', 'ui/styles/chat*.css', 'ui/styles/feedback.css',
      'ui/styles/visual-*.css', 'ui/app/call-ring.ts', 'ui/app/new-message.ts', 'ui/styles/calls.css', 'ui/app/empty-art.ts']},
  {name: 'calendar', route: '#/calendar', ready: 'Calendar', ui: ['ui/app/calendar*.ts', 'ui/styles/calendar.css']},
  {name: 'recruit', route: async page => '#/seat/' + encodeURIComponent(await page.evaluate(async () =>
    ((await (await fetch('/api/v2/seats')).json()) as {seats: Seat[]}).seats[0]!.id)), ready: 'Job post',
    ui: ['ui/app/recruit.ts', 'ui/app/team-build.ts', 'ui/styles/recruit.css', 'ui/styles/team-build.css']},
];

function argument(name: string, fallback = ''): string {
  const at = process.argv.indexOf('--' + name);
  if (at <= 0) return fallback;
  // A flag with no value after it is a usage error, not the empty string (--only at the end would shoot every page).
  const value = process.argv[at + 1];
  if (value === undefined) throw new Error(`--${name} needs a value`);
  return value;
}

// The message of a thrown value, for a problem line.
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// A comma list of names from a fixed set: a typo fails here, not as an empty run.
function choices(flag: string, fallback: string, known: string[]): string[] {
  const values = argument(flag, fallback).split(',').map(value => value.trim()).filter(Boolean);
  const unknown = values.filter(value => !known.includes(value));
  if (!values.length || unknown.length) {
    throw new Error(`--${flag}: ${unknown.join(', ') || 'no value'} (choose from ${known.join(', ')})`);
  }
  return values;
}

const git = (args: string[]): string => execFileSync('git', args, {cwd: process.cwd(), encoding: 'utf8', maxBuffer: 256 << 20});

// Which ui/ files have a changed line that mentions dark (a dark-theme rule or its test), from the diff's own lines.
function darkPaths(base: string, head: string): string[] {
  const found = new Set<string>();
  let file = '';
  for (const line of git(['diff', '-U0', '--no-renames', base, head, '--', 'ui']).split('\n')) {
    const header = /^diff --git a\/(\S+) b\/(\S+)/.exec(line);
    if (header) { file = header[2]!; continue; }
    const changed = (line.startsWith('+') && !line.startsWith('+++')) || (line.startsWith('-') && !line.startsWith('---'));
    if (changed && /dark/i.test(line)) found.add(file);
  }
  return [...found];
}

function pagesForDiff(range: string): {base: string; head: string} & Selection {
  const [base, head, ...extra] = range.split('..');
  if (!base || !head || extra.length) throw new Error('--pages-for-diff takes <base>..<head>');
  const paths = git(['diff', '--name-only', '--no-renames', base, head, '--']).split('\n').filter(Boolean);
  return {base, head, ...select.selectPages(paths, PAGES, darkPaths(base, head))};
}

// The page table and, for every glob in it, the tracked files it matches: a glob that matches nothing is a stale entry.
function listPages(): PageListing {
  const tracked = git(['ls-files']).split('\n').filter(Boolean);
  const matching = (globs: string[]): Record<string, string[]> => Object.fromEntries(globs.map(glob => [glob, tracked.filter(file => select.globToRegExp(glob).test(file))]));
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

const freePort = (): Promise<number> => new Promise((resolve, reject) => {
  const server = net.createServer().once('error', reject).listen(0, '127.0.0.1', () => {
    const {port} = server.address() as AddressInfo;
    server.close(() => resolve(port));
  });
});

async function bootDemo(): Promise<Demo> {
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
async function verify(page: BrowserPage, problems: string[], label: string): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  const found = await page.evaluate(() => {
    const seen = (el: HTMLElement): boolean => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
    const textOf = (el: HTMLElement): string => (el.textContent as string).trim();
    const banners = [...document.querySelectorAll<HTMLElement>('.err, [role="alert"], .toast, .banner-error')]
      .filter(seen).map(textOf).filter(Boolean);
    // An icon whose font has not loaded is its ligature name ("dynamic_feed") in body type: far wider than an icon.
    const icons = [...document.querySelectorAll<HTMLElement>('.nav-icon, .mobile-nav-icon')].filter(seen)
      .filter(el => el.getBoundingClientRect().width > 48).map(textOf);
    return {banners, icons, font: document.fonts.check('24px "Material Symbols Outlined"'), text: document.fonts.check('13px Inter'),
            loading: [...document.querySelectorAll<HTMLElement>('.loading, .skeleton, .market-loading')].filter(seen).length};
  });
  if (!found.font) problems.push(`${label}: the icon font is not loaded`);
  if (!found.text) problems.push(`${label}: the text font is not loaded`);
  for (const text of found.icons) problems.push(`${label}: icon shown as text "${text}"`);
  for (const text of found.banners) problems.push(`${label}: error banner "${text.slice(0, 120)}"`);
  if (found.loading) problems.push(`${label}: still loading`);
}

async function capture(): Promise<void> {
  const {chromium}: typeof import('playwright') = require('playwright');
  const out = path.resolve(ROOT, argument('out', 'docs/images'));
  const widths = choices('widths', DEFAULT_WIDTHS, Object.keys(WIDTHS));
  const themes = choices('themes', DEFAULT_THEMES, Object.keys(THEMES));
  const only = argument('only') ? choices('only', '', PAGES.map(page => page.name)) : null;
  const pages = PAGES.filter(page => !only || only.includes(page.name));
  fs.mkdirSync(out, {recursive: true});
  const demo: Demo = argument('url') ? {url: argument('url').replace(/\/$/, ''), stop() {}} : await bootDemo();
  const channel = process.env.TICO_BROWSER_CHANNEL ?? 'chrome';
  // Playwright reads an undefined channel as none (its bundled Chromium); the type has no room for undefined.
  const browser = await chromium.launch({channel: channel || undefined, headless: true} as import('playwright').LaunchOptions);
  const problems: string[] = [];
  const written: string[] = [];
  try {
    for (const theme of themes) {
      for (const width of widths) {
        const context = await browser.newContext({...WIDTHS[width], colorScheme: THEMES[theme]!, locale: 'en-US', timezoneId: 'America/Los_Angeles'});
        // ui/theme.ts is dark unless this browser saved a choice, so colorScheme alone shot dark for both themes: save the
        // theme before the page's scripts read it. colorScheme stays for the CSS that follows the system.
        await context.addInitScript(saved => { try { localStorage.setItem('tico.theme', saved); } catch { /* no storage */ } }, theme);
        // A panel still sliding or fading when a page measures itself (the rail's "More" fold) would be measured at a
        // different size each run, so transitions and animations end at once.
        await context.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
          const style = document.createElement('style');
          style.textContent = '*, *::before, *::after { transition: none !important; animation-duration: 0s !important; animation-delay: 0s !important; }';
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
        // The market page's second graph fetch (the note's focus, fill=false) replaces the layout's starting positions, so
        // where the first layout had got to when it arrived decided the picture. That fetch waits until the first layout
        // has stopped drawing: three reads 100 ms apart with the same count of draws (and at least one).
        await page.route(url => url.pathname.endsWith('/v2/market/graph'), async route => {
          if (new URL(route.request().url()).searchParams.get('fill') !== 'false') return route.fallback();
          for (let last = -1, still = 0, tries = 0; still < 3 && tries < 100; tries++) {
            await page.waitForTimeout(100);
            const draws = await page.evaluate(() => window.__marketDraws || 0);
            still = draws > 0 && draws === last ? still + 1 : 0;
            last = draws;
          }
          return route.continue();
        });
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
            await tab.click().catch((error: unknown) => problems.push(`${label}: no ${spec.tab} tab: ${messageOf(error).split('\n')[0]}`));
          }
          await page.getByText(spec.ready).first().waitFor({timeout: 20000}).catch(() => problems.push(`${label}: never showed "${spec.ready}"`));
          if (spec.steps) await spec.steps(page).catch((error: unknown) => problems.push(`${label}: ${messageOf(error).split('\n')[0]}`));
          // Items mark themselves read 700 to 900 ms after they show (ui/app/updates.ts), and side panels fill in as their
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
          await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
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

function main(): void | Promise<void> {
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
