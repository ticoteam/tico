#!/usr/bin/env node
// Regenerates the product screenshots in docs/images/ from demo mode, so they show the product as it is
// today and never a stale or half-loaded page. It boots `python -m backend.demo` (or uses --url), visits a
// fixed list of pages at desktop and phone widths in light and dark, and FAILS, saving nothing further to
// trust, if any page logs a console error, makes a failing request, shows an error banner, or renders an
// icon as its ligature text (the icon font not loaded yet).
//
//   node scripts/screenshots                        # all pages, both widths, both themes -> docs/images
//   node scripts/screenshots --only updates,settings-health  # some pages
//   node scripts/screenshots --url http://127.0.0.1:8765 --out /tmp/shots
//
// Needs `npm ci` and a browser: TICO_BROWSER_CHANNEL=chrome (the default, your own Chrome) or empty for
// Playwright's Chromium (`npx playwright install chromium`, what CI does). TICO_PYTHON picks the interpreter.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const WIDTHS = {
  desktop: {viewport: {width: 1280, height: 800}, deviceScaleFactor: 1},
  phone: {viewport: {width: 390, height: 844}, deviceScaleFactor: 2, isMobile: true, hasTouch: true},
};
const THEMES = ['light', 'dark'];

// name: the file is <name>-<width>-<theme>.png. route: the hash to open. ready: text that means the page has
// its data. tab: a Settings tab to select first (Settings remembers the last tab in sessionStorage, so a
// capture never assumes the one it wants is the one showing). steps: what to do before the picture (click,
// open a dialog).
const PAGES = [
  {name: 'updates', route: '#/updates', ready: 'Reply'},
  {name: 'tasks', route: '#/tasks', ready: 'Needs you', steps: async page => {
    await page.click('#task-view [data-view="list"]');
    await page.getByText('Draft replies for the open support tickets').first().waitFor();
  }},
  {name: 'needs-you', route: '#/tasks', ready: 'Approve the follow-up email to Dana Reyes'},
  {name: 'org-chart', route: '#/goals', ready: 'Grow to 200 paying studios by December'},
  {name: 'bot-chat', route: '#/bot/support', ready: 'How many tickets are waiting on the refund decision?'},
  {name: 'meetings', route: '#/meetings', ready: 'Brightline renewal call with Dana Reyes'},
  {name: 'meeting', route: async page => '#/meetings?meeting=' + encodeURIComponent(await page.evaluate(async () => {
    const list = await (await fetch('/api/meetings')).json();
    return list.find(m => m.title === 'Weekly ops sync').id;
  })), ready: 'Meeting context', steps: async page => {
    await page.getByText('Transcript \u00b7 Zoom').click();
    await page.getByText('Ana').first().waitFor();
  }},
  {name: 'usage', route: '#/usage', ready: 'Estimated', steps: async page => {
    await page.locator('.use-line').first().click();
    await page.locator('.use-col').first().waitFor();
  }},
  {name: 'settings-health', route: '#/settings', tab: 'health', ready: 'Failed runs'},
  {name: 'devices', route: '#/settings', tab: 'devices', ready: "Ana's MacBook"},
  {name: 'help', route: '#/help', ready: 'Learn more'},
  {name: 'tour', route: '#/settings', tab: 'devices', ready: "Ana's MacBook", steps: async page => {
    await page.click('#account');
    await page.click('#account-tour');
    await page.locator('.gs-tour-card').waitFor();
    await page.click('[data-tour-next]');
    await page.waitForTimeout(400);
  }},
  {name: 'docs', route: async page => '#/docs/' + encodeURIComponent(await page.evaluate(async () => {
    const {docs} = await (await fetch('/api/v2/docs?limit=500')).json();
    return docs.find(doc => doc.title === 'Refund policy').id;
  })), ready: 'Support may refund up to the limit Ana sets.', steps: async page => {
    if (page.viewportSize().width < 600) {
      await page.locator('#docs-reader .docs-back').click();
      await page.locator('#docs-h-linked').waitFor();
      const folders = page.locator('details[data-docs-folder][open] > summary');
      while (await folders.count()) await folders.first().click();
    }
    await page.locator('#docs-h-internal').waitFor();
    await page.locator('#docs-h-linked').waitFor();
  }},
  {name: 'market', route: '#/market?note=company%2Fnorthwind', ready: 'Cheapest starter plan'},
];

function argument(name, fallback = '') {
  const at = process.argv.indexOf('--' + name);
  return at > 0 ? process.argv[at + 1] : fallback;
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
    {cwd: ROOT, stdio: ['ignore', 'inherit', 'inherit']});
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
    return {banners, icons, font: document.fonts.check('24px "Material Symbols Outlined"'),
            loading: [...document.querySelectorAll('.loading, .skeleton, .market-loading')].filter(seen).length};
  });
  if (!found.font) problems.push(`${label}: the icon font is not loaded`);
  for (const text of found.icons) problems.push(`${label}: icon shown as text "${text}"`);
  for (const text of found.banners) problems.push(`${label}: error banner "${text.slice(0, 120)}"`);
  if (found.loading) problems.push(`${label}: still loading`);
}

async function main() {
  const out = path.resolve(ROOT, argument('out', 'docs/images'));
  const only = argument('only') ? new Set(argument('only').split(',')) : null;
  fs.mkdirSync(out, {recursive: true});
  const demo = argument('url') ? {url: argument('url').replace(/\/$/, ''), stop() {}} : await bootDemo();
  const channel = process.env.TICO_BROWSER_CHANNEL ?? 'chrome';
  const browser = await chromium.launch({channel: channel || undefined, headless: true});
  const problems = [];
  let written = 0;
  try {
    for (const theme of THEMES) {
      for (const [width, device] of Object.entries(WIDTHS)) {
        const context = await browser.newContext({...device, colorScheme: theme, locale: 'en-US', timezoneId: 'America/Los_Angeles'});
        const page = await context.newPage();
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
        for (const spec of PAGES) {
          if (only && !only.has(spec.name)) continue;
          const label = `${spec.name}-${width}-${theme}`;
          where = label;
          const route = typeof spec.route === 'function' ? await spec.route(page) : spec.route;
          await page.evaluate(hash => { location.hash = hash; }, route);
          if (spec.tab) await page.locator(`[data-settings-tab="${spec.tab}"]`).click().catch(error => problems.push(`${label}: no ${spec.tab} tab: ${error.message.split('\n')[0]}`));
          await page.getByText(spec.ready).first().waitFor({timeout: 20000}).catch(() => problems.push(`${label}: never showed "${spec.ready}"`));
          if (spec.steps) await spec.steps(page).catch(error => problems.push(`${label}: ${error.message.split('\n')[0]}`));
          await page.waitForTimeout(600);
          await verify(page, problems, label);
          await page.screenshot({path: path.join(out, label + '.png')});
          written++;
          if (spec.name === 'tour') await page.keyboard.press('Escape');
        }
        await context.close();
      }
    }
  } finally {
    await browser.close();
    demo.stop();
  }
  console.log(`${written} screenshots in ${path.relative(ROOT, out) || '.'}`);
  if (problems.length) {
    console.error('\nScreenshots are not trustworthy:\n  ' + [...new Set(problems)].join('\n  '));
    process.exit(1);
  }
}

main().catch(error => { console.error(error); process.exit(1); });
