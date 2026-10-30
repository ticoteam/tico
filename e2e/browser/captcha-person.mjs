#!/usr/bin/env node
// The person's side of the captcha hand-off in scripts/browser-scenarios.sh: opens the computer's screen on a phone
// (390x844, touch), takes control, and presses and holds the centre of the bot's Chrome for longer than the check asks.
// It prints one JSON object, {ok, checks}; the check names and results only, never the page's text.
//
//   node captcha-person.mjs <base url> <computer id>
//
// The owner token (TOKEN_FILE) goes in as a bearer header, the way the screen-check step signs in.
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const [base, computer] = process.argv.slice(2);
const token = readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const HOLD_MS = 3500;   // the check asks for three seconds
const checks = [];
// A failed click's log is long; the first lines of it name what blocked the click.
const check = (name, ok, detail = '') => checks.push({name, ok: Boolean(ok), detail: String(detail).slice(0, 900)});

const browser = await chromium.launch({args: ['--no-sandbox']});
try {
  const context = await browser.newContext({
    viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    extraHTTPHeaders: {Authorization: `Bearer ${token}`},
  });
  const page = await context.newPage();
  await page.goto(`${base}/tico/ui/#/computers/${encodeURIComponent(computer)}/screen`, {waitUntil: 'load', timeout: 60000});
  await page.waitForSelector('#scr', {timeout: 30000});
  // What sits at the Control button's centre: a click that never lands is an overlay or a button that moved.
  const control = page.locator('[data-scr-control="1"]').first();
  await control.waitFor({state: 'visible', timeout: 30000});
  const where = await control.evaluate(el => {
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return `box ${[r.x, r.y, r.width, r.height].map(Math.round).join(',')} at ${innerWidth}px; hit ${hit ? hit.tagName + (hit.id ? '#' + hit.id : '') + (hit.className && typeof hit.className === 'string' ? '.' + hit.className.split(' ')[0] : '') : 'none'}`;
  });
  check('the control is visible and at the centre', true, where);
  await control.click({timeout: 15000});
  const canvas = page.locator('#scr-target canvas').first();
  await canvas.waitFor({timeout: 30000});
  const box = await canvas.boundingBox();
  check('the screen is on the page in control mode', Boolean(box && box.width > 0 && box.height > 0));
  if (box) {
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.waitForTimeout(HOLD_MS);
    await page.mouse.up();
    check('the centre of the screen was held', true, `${HOLD_MS} ms`);
  }
} catch (error) {
  check('the person could hold the bot\'s screen', false, error.message);
} finally {
  await browser.close();
}

const ok = checks.length > 0 && checks.every(item => item.ok);
console.log(JSON.stringify({ok, checks}));
process.exit(ok ? 0 : 1);
