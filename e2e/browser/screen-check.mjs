#!/usr/bin/env node
// The screen step's host side: opens one computer's screen page the way a phone does (390x844, touch, mobile
// emulation) and reports what it can see. It prints one JSON object, {ok, checks}, with check names, results and the
// HTTP statuses of the page's API calls: the page's text is never printed.
//
//   node screen-check.mjs <base url> <computer id> <screenshot path>
//
// The page is a signed-in page: the owner token (TOKEN_FILE) goes in as a bearer header, so the browser is the owner
// on this loopback install, the way the owner's own tools are. The token is never printed.
// Playwright comes from the repository's node_modules, which the browser setup installs with its Chromium.
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const [base, computer, shot] = process.argv.slice(2);
const token = readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const checks = [];
const check = (name, ok, detail = '') => checks.push({name, ok: Boolean(ok), detail: String(detail).slice(0, 240)});

const browser = await chromium.launch({args: ['--no-sandbox']});
try {
  const context = await browser.newContext({
    viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    extraHTTPHeaders: {Authorization: `Bearer ${token}`},
  });
  const page = await context.newPage();
  const seen = [];
  const errors = [];
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.origin === new URL(base).origin) seen.push(`${response.status()} ${url.pathname}`);
  });
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text().slice(0, 120));
  });
  page.on('pageerror', error => errors.push(`page error: ${error.message.slice(0, 120)}`));
  await page.goto(`${base}/tico/ui/#/computers/${encodeURIComponent(computer)}/screen`, {waitUntil: 'load', timeout: 60000});
  const shown = await page.waitForSelector('#scr', {timeout: 30000}).then(() => true, () => false);
  const apiSeen = [...new Set(seen.filter(line => line.includes('/api/v2/')))].slice(0, 8).join(', ') || 'none';
  const problems = [...new Set(errors)].slice(0, 4).join(' | ') || 'none';
  check('the screen element is on the page', shown,
        `responses: ${[...new Set(seen)].slice(0, 12).join(', ') || 'none'}; api: ${apiSeen}; console: ${problems}`);
  const state = await page.locator('#scr-state').first().textContent({timeout: 5000}).catch(() => null);
  check('the screen state line is shown', typeof state === 'string' && state.trim() !== '');
  const mode = await page.locator('#scr-mode').first().textContent({timeout: 5000}).catch(() => null);
  check('the screen mode control is shown', mode !== null);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('no horizontal scroll at 390px', overflow <= 0, `${overflow}px wider than the screen`);
  await page.screenshot({path: shot});
} catch (error) {
  check('the screen page loaded', false, error.message);
} finally {
  await browser.close();
}

const ok = checks.length > 0 && checks.every(item => item.ok);
console.log(JSON.stringify({ok, checks}));
process.exit(ok ? 0 : 1);
