// A first run of the new app in a real browser, against a server already running on a rehearsal copy (e2e/hub).
//   node e2e/smoke.ts <out dir> [step ...]     steps: boot, menu, sheets, keys, phone, dev, light (default: all)
// Needs the server on 127.0.0.1:8765 and `npx vite` on 127.0.0.1:5173. Pictures hold real data: keep them local.
/// <reference lib="dom" />
import { readFileSync } from 'node:fs';
import { chromium, webkit, type Browser, type BrowserContext, type Page } from 'playwright';

const HUB = 'http://127.0.0.1:8765';
const APP = 'http://127.0.0.1:5173';
const TOKEN_FILE = process.env.HUB_TOKEN_FILE ?? '';
const [out = '.', ...wanted] = process.argv.slice(2);
const want = (s: string) => !wanted.length || wanted.includes(s);
const problems: string[] = [];
const note = (s: string) => console.log(s);
const bad = (s: string) => {
  problems.push(s);
  console.log(`  ✗ ${s}`);
};
const ok = (s: string) => console.log(`  ✓ ${s}`);
const check = (good: unknown, yes: string, no: string) => (good ? ok(yes) : bad(no));

async function open(browser: Browser, phone = false, at = '/'): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext(
    phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : { viewport: { width: 1440, height: 900 } },
  );
  const page = await ctx.newPage();
  page.on('console', (m) => {
    if (/page-history|status of 405/.test(m.text())) return;
    if (m.type() === 'error' || m.type() === 'warning') bad(`console ${m.type()}: ${m.text().slice(0, 300)}`);
  });
  page.on('pageerror', (e) => bad(`page error: ${e.message.slice(0, 300)}`));
  page.on('response', (r) => {
    // page history is PR #4453, not on this server yet: the app stops asking after the first refusal
    if (r.status() >= 400 && r.url().includes('/api/') && !r.url().endsWith('/page-history')) bad(`${r.status()} ${r.request().method()} ${r.url().replace(/^https?:\/\/[^/]+/, '')}`);
  });
  const token = readFileSync(TOKEN_FILE, 'utf8').trim();
  await page.goto(`${HUB}/api/v2/local-signin?token=${encodeURIComponent(token)}&next=/healthz`);
  await page.goto(`${APP}${at}`);
  return { ctx, page };
}

const shot = (page: Page, name: string) => page.screenshot({ path: `${out}/${name}.png` });
const settle = (page: Page, ms = 1200) => page.waitForTimeout(ms);
const sheetW = (page: Page, id: string) => page.locator(`[data-sheet="${id}"]`).evaluate((el) => (el as HTMLElement).getBoundingClientRect().width).catch(() => -1);

async function desktop(browser: Browser) {
  const { ctx, page } = await open(browser);
  let streams = 0;
  page.on('request', (r) => {
    if (r.url().includes('/api/v2/events')) streams++;
  });

  if (want('boot')) {
    note('boot');
    await page.waitForSelector('[data-sheet="menu"] nav', { timeout: 15000 }).then(() => ok('menu drawn'), () => bad('no menu'));
    await page.waitForSelector('[data-sheet="tico"] .chat-body', { timeout: 15000 }).then(() => ok('Tico drawn'), () => bad('no Tico sheet'));
    // the first read can move the stream back once (liveFrom); after that it stays
    await settle(page, 3000);
    const settled = streams;
    await settle(page, 8000);
    const msgs = await page.locator('[data-sheet="tico"] .msg').count();
    check(msgs, `Tico's room: ${msgs} messages drawn`, "Tico's room shows no messages");
    check(streams === settled && settled <= 2, `stream opened ${settled}× at boot, then stayed 8 s`, `stream opened ${streams} times (${settled} at boot)`);
    await shot(page, 'desktop-home');
  }

  if (want('menu')) {
    note('menu');
    for (const [label, path] of [
      ['Calendar', '/calendar'],
      ['Company', '/company'],
      ['Library', '/library'],
      ['Tasks', '/tasks'],
    ] as const) {
      await page.locator('[data-sheet="menu"] .nav-item', { hasText: label }).first().click();
      await settle(page, 500);
      const at = new URL(page.url()).pathname;
      check(at.startsWith(path), `${label} → ${at}`, `${label} went to ${at}`);
    }
    await shot(page, 'desktop-tasks');
  }

  if (want('sheets')) {
    note('sheets');
    const handle = page.locator('.shell-handle').first();
    const before = await sheetW(page, 'tico');
    const box = await handle.boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + 200);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 60, box.y + 200, { steps: 8 });
      await page.mouse.up();
      await settle(page, 300);
      const after = await sheetW(page, 'tico');
      check(after !== before, `dragged Tico ${Math.round(before)} → ${Math.round(after)}`, `drag did not resize Tico (${before})`);
      await page.reload();
      await page.waitForSelector('[data-sheet="tico"]');
      await settle(page, 800);
      const kept = await sheetW(page, 'tico');
      check(Math.abs(kept - after) < 2, 'width remembered after reload', `width not remembered (${after} → ${kept})`);
      await handle.dblclick();
      await settle(page, 600);
    } else bad('no resize handle');
    await page.getByRole('button', { name: 'Fold the menu' }).click();
    await settle(page, 900);
    check(await page.locator('.nav.is-rail').count(), 'menu folds to its rail', 'menu did not fold');
    await shot(page, 'desktop-menu-folded');
    await page.reload();
    await settle(page, 1500);
    check(await page.locator('.nav.is-rail').count(), 'fold remembered', 'fold not remembered');
    await page.getByRole('button', { name: 'Open the menu' }).click();
    await settle(page, 900);
    check(!(await page.locator('.nav.is-rail').count()), 'menu opens again', 'menu did not open again');
  }

  if (want('keys')) {
    note('keys');
    await page.locator('body').click({ position: { x: 1300, y: 600 } });
    await page.keyboard.press('[');
    await settle(page, 900);
    check(await page.locator('.nav.is-rail').count(), '[ folds the menu', '[ did nothing');
    await page.keyboard.press('[');
    await settle(page, 900);
    check(!(await page.locator('.nav.is-rail').count()), '[ again opens it', '[ again did not open it');
    await page.keyboard.press('ControlOrMeta+j');
    await settle(page, 900);
    check(!(await page.locator('.tico.is-fold').count()), 'Cmd+J with Tico open: stays open', 'Cmd+J folded Tico instead of focusing it');
    const focused = await page.evaluate(() => !!document.activeElement?.closest('[data-sheet="tico"]'));
    check(focused, "Cmd+J puts the cursor in Tico's box", "Cmd+J did not focus Tico's box");
    await page.keyboard.press('ControlOrMeta+j');
    await settle(page, 900);
    check(await page.locator('.tico.is-fold').count(), 'Cmd+J again folds Tico', 'Cmd+J again did not fold Tico');
    await shot(page, 'desktop-tico-folded');
    await page.keyboard.press('ControlOrMeta+j');
    await settle(page, 900);
    check(!(await page.locator('.tico.is-fold').count()), 'Cmd+J opens Tico', 'Cmd+J did not open Tico');
    await page.keyboard.press('Escape');
    await page.locator('body').click({ position: { x: 1300, y: 600 } });
    await page.keyboard.press('ControlOrMeta+k');
    await settle(page, 600);
    check(await page.getByRole('dialog').count(), 'Cmd+K opens search', 'Cmd+K did not open search');
    await page.keyboard.type('cal');
    await settle(page, 800);
    await shot(page, 'desktop-search');
    await page.keyboard.press('Escape');
    await settle(page, 400);
    check(!(await page.getByRole('dialog').count()), 'Escape closes search', 'Escape did not close search');
  }
  await ctx.close();
}

async function phone(browser: Browser, name: string) {
  note(`phone (${name})`);
  const { ctx, page } = await open(browser, true, '/tasks');
  await settle(page, 4000);
  await shot(page, `phone-${name}-rest`);
  const lip = page.locator('.phone [data-card="tico"], .phone .lip').first();
  check(await lip.count(), 'lips drawn', 'no lips on the phone');
  const histBefore = await page.evaluate(() => history.length);
  const urlBefore = page.url();
  const menuLip = page.getByRole('button', { name: 'Open Menu' });
  // a finger on the lip's middle: the grab handle's wide hit area lies over it, so this is what a person's tap hits
  const tapMenu = () => menuLip.tap();
  if (await menuLip.count()) {
    await tapMenu();
    await settle(page, 900);
    check(await page.locator('.phone[data-card="menu"]').count(), 'a tap on the menu lip raises the menu', `menu not up after a tap (card: ${await page.locator('.phone').getAttribute('data-card')})`);
    await shot(page, `phone-${name}-menu`);
    await page.goBack();
    await settle(page, 900);
    check(page.url() === urlBefore, 'Back closes the card and stays on the page', `Back navigated: ${urlBefore} → ${page.url()}`);
    await shot(page, `phone-${name}-after-back`);
    // a card open, then a navigation from inside it: Back afterwards should go to the page before, not a stranded card
    await tapMenu();
    await settle(page, 900);
    await page.locator('.ly-menu .nav-item', { hasText: 'Calendar' }).first().tap();
    await settle(page, 900);
    const atCal = page.url();
    await page.goBack();
    await settle(page, 900);
    check(page.url() !== atCal, `Back after a navigation from a card: ${new URL(page.url()).pathname}`, 'Back after navigating from a card stayed (stranded card entry)');
    note(`  history ${histBefore} → ${await page.evaluate(() => history.length)}`);
  } else bad('no menu lip button');
  await ctx.close();
}

async function dev(browser: Browser) {
  note('dev pages');
  const { ctx, page } = await open(browser, false, '/_dev/dot');
  await settle(page, 2500);
  await shot(page, 'dev-dot');
  await page.goto(`${APP}/_dev/kinds`);
  await settle(page, 2500);
  await shot(page, 'dev-kinds');
  await ctx.close();
}

async function light(browser: Browser) {
  note('light');
  const { ctx, page } = await open(browser, false, '/');
  await page.evaluate(() => localStorage.setItem('tico-next:theme.v1', JSON.stringify('light')));
  await page.reload();
  await settle(page, 4000);
  await shot(page, 'desktop-light');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  if (['boot', 'menu', 'sheets', 'keys'].some(want)) await desktop(browser);
  if (want('phone')) await phone(browser, 'chromium');
  if (want('dev')) await dev(browser);
  if (want('light')) await light(browser);
} finally {
  await browser.close();
}
if (want('phone') && process.env.WEBKIT !== '0') {
  const wk = await webkit.launch().catch(() => null);
  if (wk) {
    try {
      await phone(wk, 'webkit');
    } finally {
      await wk.close();
    }
  } else note('webkit not installed');
}
console.log(problems.length ? `\n${problems.length} problems` : '\nno problems');
