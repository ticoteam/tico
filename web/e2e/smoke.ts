// A first run of the new app in a real browser, against a server already running on a rehearsal copy (e2e/hub).
//   node e2e/smoke.ts <out dir> [step ...]     steps: boot, menu, sheets, keys, phone, responsive, dev, light (default: all)
// Needs the server on 127.0.0.1:8765 and `npx vite` on 127.0.0.1:5173 (HUB= and APP= point elsewhere). Pictures hold
// real data: keep them local.
/// <reference lib="dom" />
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium, webkit, type Browser, type BrowserContext, type Page } from 'playwright';

const HUB = process.env.HUB ?? 'http://127.0.0.1:8765';
const APP = process.env.APP ?? 'http://127.0.0.1:5173';
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

async function open(browser: Browser, phone = false, at = '/', readOnly = false): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext(
    phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : { viewport: { width: 1440, height: 900 } },
  );
  const page = await ctx.newPage();
  if (readOnly) {
    await page.route('**/api/**', (route) =>
      route.request().method() === 'GET' ? route.continue() : route.fulfill({ status: 204 }),
    );
  }
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
  const { ctx, page } = await open(browser, true, '/tasks', true); // gestures and layout only: nothing is written
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

  // a raised card keeps the stack behind it: the menu peeks above Tico, enough of its mark to know it, and a tap on the
  // peek brings it forward without tucking Tico first
  await page.goto(`${APP}/tasks`);
  await settle(page, 2500);
  await page.getByRole('button', { name: 'Open Tico' }).first().tap();
  await settle(page, 900);
  await shot(page, `phone-${name}-tico-peeks`);
  if (await peekTaps(page, 'menu', 'tico', 'the menu behind Tico')) {
    check((await page.locator('.phone').getAttribute('data-card')) === 'menu', 'a tap on the peek brings the menu forward', 'the menu peek did not raise the menu');
  }
  await page.goBack();
  await settle(page, 900);

  // Tico's Dot never sits on a field: not the handle on a raised chat's message box, not a lip on a page's own box
  await page.goto(`${APP}/threads`);
  await settle(page, 2500);
  const thread = await page.evaluate(() =>
    [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') ?? '').find((h) => /^\/threads\/[0-9a-f-]{36}$/.test(h)),
  );
  if (thread) {
    await page.goto(`${APP}${thread}`);
    await settle(page, 2500);
    await noTicoOnFields(page, 'a thread');
  }
  await page.goto(`${APP}/tasks`);
  await settle(page, 2500);
  const task = await page.evaluate(() =>
    [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') ?? '').find((h) => /^\/tasks\/[^/]+\/[^/]+/.test(h) && !h.endsWith('/edit')),
  );
  if (task) {
    await page.goto(`${APP}${task}`);
    await settle(page, 2500);
    const chatLip = page.locator('.phone>.ly-chat>.lip');
    if (await chatLip.isVisible()) {
      await chatLip.tap();
      await settle(page, 1200);
      await noTicoOnFields(page, "a task's chat, raised");
      await page.setViewportSize({ width: 390, height: 508 }); // a keyboard that resizes the page (Android)
      await page.locator('.phone .ly-chat textarea').first().focus();
      await settle(page, 900);
      await noTicoOnFields(page, "a task's chat, typing");
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator('.phone .ly-chat textarea').first().blur();
      await settle(page, 600);
      // Tico raised from the chat: the chat and the menu peek above it in order, and the chat is a tap away
      await page.locator('.phone>.nub').tap();
      await settle(page, 1200);
      await shot(page, `phone-${name}-chat-peeks`);
      await noTicoOnFields(page, 'Tico raised over a task');
      if (await peekTaps(page, 'chat', 'menu', 'the chat behind the menu, behind Tico')) {
        check((await page.locator('.phone').getAttribute('data-card')) === 'chat', 'a tap on the chat peek brings the chat forward', 'the chat peek did not raise the chat');
      }
    }
  }
  await ctx.close();
}

/** A card behind a raised one shows a peek above the card in front of it, and a finger on the peek reaches its lip. */
async function peekTaps(page: Page, card: 'menu' | 'chat', front: 'tico' | 'menu', where: string): Promise<boolean> {
  const g = await page.evaluate(
    ([c, f]) => {
      const lip = document.querySelector(`.phone>.ly-${c}>.lip`);
      const over = document.querySelector(`.phone>.ly-${f}`);
      if (!lip || !over) return null;
      const a = lip.getBoundingClientRect();
      const top = over.getBoundingClientRect().top;
      const x = a.left + a.width / 2;
      const y = (a.top + top) / 2;
      const mark = lip.querySelector('.co-mark')?.getBoundingClientRect();
      return {
        strip: top - a.top,
        x,
        y,
        hit: !!document.elementFromPoint(x, y)?.closest(`.phone>.ly-${c}>.lip`),
        mark: mark ? Math.max(0, Math.min(mark.bottom, top) - mark.top) / mark.height : null,
      };
    },
    [card, front] as const,
  );
  if (!g) return (bad(`${where}: no card`), false);
  check(g.strip >= 15 && g.strip <= 24, `${where} peeks ${Math.round(g.strip)}px`, `${where} peeks ${Math.round(g.strip)}px (want 15-24)`);
  if (g.mark !== null) check(g.mark >= 0.2 && g.mark <= 0.5, `${Math.round(g.mark * 100)}% of the company mark shows`, `${Math.round(g.mark * 100)}% of the company mark shows (want 20-50%)`);
  check(g.hit, `${where} can be tapped`, `${where}: its peek is covered`);
  if (!g.hit) return false;
  await page.touchscreen.tap(g.x, g.y);
  await settle(page, 900);
  return true;
}

/** No Tico mark (the stack's handle, Tico's Dot on its lip) drawn over a message box or a field that can be seen. */
async function noTicoOnFields(page: Page, where: string) {
  const hits = await page.evaluate(() => {
    const seen = (el: Element) => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && el.getBoundingClientRect().height > 0;
    const marks = [...document.querySelectorAll('.phone>.nub, .phone .tico-dot')].filter(seen);
    const fields = [...document.querySelectorAll('.composer, textarea, input:not([type=hidden]), [contenteditable]')].filter(seen);
    const over: string[] = [];
    for (const m of marks)
      for (const f of fields) {
        const a = m.getBoundingClientRect();
        const b = f.getBoundingClientRect();
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) over.push(`${m.className} on ${f.className || f.tagName}`);
      }
    return over;
  });
  check(!hits.length, `no Tico mark on a field: ${where}`, `Tico on a field (${where}): ${hits.join(', ')}`);
}

async function responsive(browser: Browser) {
  note('responsive controls');
  const { ctx, page } = await open(browser, false, '/company/finance?tab=pnl', true);
  try {
    const head = page.locator('main .pk-head');
    const fits = () => head.evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
    await head.getByRole('group', { name: 'Basis' }).waitFor();
    assert(await fits(), 'the P&L header overflows at 1440');
    await page.setViewportSize({ width: 1080, height: 900 });
    const more = head.getByRole('button', { name: 'More' });
    await more.waitFor();
    assert(await fits(), 'the P&L header overflows at 1080');
    await more.click();
    for (const name of ['Quarter', 'Cash']) {
      assert(await page.getByRole('menuitem', { name, exact: true }).isVisible(), `${name} is missing from More`);
    }
    await page.keyboard.press('Escape');
    for (const query of ['', '?view=list']) {
      await page.goto(`${APP}/calendar${query}`);
      await page.locator('.cal-page .pk-head').getByRole('button', { name: 'More' }).waitFor();
      assert(await fits(), `Calendar${query} header overflows at 1080`);
    }
  } finally {
    await ctx.close();
  }

  const mobile = await open(browser, true, '/flow', true);
  try {
    await mobile.page.locator('main a[href^="/flow/"]').first().click();
    const back = mobile.page.getByRole('link', { name: 'Back to Flow' });
    await back.waitFor();
    const fits = await back.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth;
    });
    assert(fits, 'the Flow back button is clipped at 390');

    // an agent's Model dialog, a sheet at 390: nothing runs off its side, the radios are 16px rings in full-width rows
    await mobile.page.goto(`${APP}/settings/agents`);
    await mobile.page.getByRole('button', { name: /^More for / }).first().tap();
    await mobile.page.getByRole('menuitem', { name: /^Model/ }).first().tap();
    const dialog = mobile.page.getByRole('dialog', { name: 'Model' });
    await dialog.getByRole('radio').first().waitFor();
    const sheet = await dialog.evaluate((el) => ({
      wide: el.scrollWidth - el.clientWidth,
      boxes: [...el.querySelectorAll('.radio-box')].map((b) => Math.round(b.getBoundingClientRect().width)),
      rows: [...el.querySelectorAll('.mp-list .radio')].map((r) => Math.round(r.getBoundingClientRect().width)),
      list: Math.round(el.querySelector('.mp-list')?.parentElement?.getBoundingClientRect().width ?? 0),
    }));
    assert(sheet.wide <= 0, `the Model dialog is ${sheet.wide}px too wide at 390`);
    assert(sheet.boxes.every((w) => w === 16), `the Model dialog's radios are not 16px: ${sheet.boxes.join(', ')}`);
    assert(sheet.rows.every((w) => w === sheet.list), `the Model dialog's rows are not full width: ${sheet.rows.join(', ')} of ${sheet.list}`);
  } finally {
    await mobile.ctx.close();
  }
  ok('P&L, Calendar, Flow and the Model dialog fit their sheets');
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
  if (want('responsive')) await responsive(browser);
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
