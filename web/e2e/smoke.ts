// A first run of the new app in a real browser, against a server already running on a rehearsal copy (e2e/hub).
//   node e2e/smoke.ts <out dir> [step ...]     steps: boot, menu, sheets, keys, quick, phone, responsive, dev, light (default: all)
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

    // Scrolled up: the asks over the box fade, Jump to latest comes, and a press glides back to the end
    const log = page.locator('[data-sheet="tico"] .chat-body').first();
    const fromEnd = () => log.evaluate((el) => Math.round(el.scrollHeight - el.scrollTop - el.clientHeight));
    check((await fromEnd()) <= 24, "Tico's chat opens at its end", `Tico's chat opened ${await fromEnd()}px above its end`);
    check(!(await page.locator('[data-sheet="tico"] .look').count()), 'no page chip over the box', 'a page chip is over the box');
    if (msgs > 3 && (await log.evaluate((el) => el.scrollHeight > el.clientHeight * 3))) {
      await log.evaluate((el) => el.scrollBy({ top: -el.clientHeight * 2 }));
      await settle(page, 600);
      const jump = page.getByRole('button', { name: /^Jump to latest/ });
      check(await jump.isVisible(), 'two windows up: Jump to latest shows', 'two windows up: no Jump to latest');
      check(!(await page.locator('[data-sheet="tico"] .qchips').isVisible()), 'two windows up: the asks are away', 'two windows up: the asks still show');
      await shot(page, 'desktop-tico-up');
      await jump.click();
      await settle(page, 1500);
      check((await fromEnd()) <= 2, 'Jump to latest lands on the end', `Jump to latest stopped ${await fromEnd()}px short`);
      check(!(await jump.isVisible()), 'at the end, Jump to latest is gone', 'at the end, Jump to latest still shows');
    } else note("  Tico's chat is too short to scroll up");
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

  if (want('quick')) {
    await page.goto(`${APP}/tasks`);
    await page.waitForSelector('[data-sheet="tico"] .composer textarea', { timeout: 15000 });
    const head = page.locator('[data-sheet="tico"] .tico-head');
    if (await head.count()) await head.getByRole('button', { name: 'Fold Tico' }).click();
    await settle(page, 900);
    // folded to its edge: the edge opens Tico ready to type; search waits under the Dot
    await quick(page, 'desktop, Tico folded', {
      sync: false,
      tico: () => page.getByRole('button', { name: 'Open Tico' }).click(),
      reset: async () => {
        await page.locator('[data-sheet="tico"] .tico-head').getByRole('button', { name: 'Fold Tico' }).click();
        await settle(page, 900);
      },
      search: () => page.locator('.shell-edge-act').getByRole('button', { name: 'Search' }).click(),
    });
    await page.goto(`${APP}/tasks`);
    await settle(page, 1500);
    if (await page.locator('.tico.is-fold, [data-edge="tico"]').count()) await page.getByRole('button', { name: 'Open Tico' }).click();
    await settle(page, 900);
    // open: search in Tico's head
    const f = await pressAndLook(page, () => page.locator('[data-sheet="tico"] .tico-head').getByRole('button', { name: 'Search' }).click());
    check(f.during === 'Search', "Tico's head: search opens with the cursor in its field", `Tico's head: the cursor was on ${f.during || 'nothing'}`);
    await page.keyboard.press('Escape');
    check(!(await page.locator('[data-sheet="menu"] .nav-head').getByRole('button', { name: 'Search' }).count()), 'the menu has no search of its own', 'the menu still has a search button');
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

/** What has the cursor, read inside the press's own click (iOS raises the keyboard only for a focus made there), and
    again once the press is over. */
async function pressAndLook(page: Page, press: () => Promise<void>): Promise<{ during: string; after: string }> {
  await page.evaluate(() => {
    const w = window as unknown as { __during?: string };
    w.__during = '';
    window.addEventListener('click', () => (w.__during = document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName ?? ''), { once: true });
  });
  await press();
  return page.evaluate(() => ({
    during: (window as unknown as { __during?: string }).__during ?? '',
    after: document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName ?? '',
  }));
}

/** Tico and search, side by side: one press to type to Tico, one to type a search, then a result picked. On a phone
    the cursor must be in place inside the press itself (`sync`); `reset` puts the search control back in reach. */
async function quick(page: Page, where: string, o: { tico: () => Promise<void>; search: () => Promise<void>; reset?: () => Promise<void>; sync: boolean }) {
  note(`quick actions (${where})`);
  const t = await pressAndLook(page, o.tico);
  await settle(page, 600);
  const now = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '');
  check((!o.sync || t.during === 'Message Tico') && now === 'Message Tico', "Tico: its box has the cursor", `Tico: the cursor was on ${t.during || 'nothing'}, then ${now || 'nothing'}`);
  await settle(page, 300);
  await page.keyboard.press('Escape');
  await settle(page, 900);
  await o.reset?.();
  const before = page.url();
  const f = await pressAndLook(page, o.search);
  check((!o.sync || f.during === 'Search') && f.after === 'Search', 'search: its field has the cursor inside the press', `search: the cursor was on ${f.during || 'nothing'}, then ${f.after || 'nothing'}`);
  await page.keyboard.type('settings', { delay: 40 });
  await page.getByRole('option', { name: /Settings/ }).first().waitFor({ timeout: 8000 }).catch(() => bad('search: no result for "settings"'));
  check((await page.locator('.pal-text').inputValue()) === 'settings', 'search: every letter typed is in the field', `search: the field holds "${await page.locator('.pal-text').inputValue()}"`);
  await page.keyboard.press('Enter');
  await settle(page, 1500);
  check(!(await page.getByRole('dialog', { name: 'Search' }).count()) && page.url() !== before, `search: Enter opened the top result (${new URL(page.url()).pathname})`, 'search: Enter did not open a result');
}

async function phone(browser: Browser, name: string) {
  note(`phone (${name})`);
  const { ctx, page } = await open(browser, true, '/tasks', true); // taps and layout only: nothing is written
  await settle(page, 4000);
  await shot(page, `phone-${name}-rest`);
  const tab = (label: string) => page.locator('.phone>.tabbar').getByRole('button', { name: label, exact: true });
  const card = () => page.locator('.phone').getAttribute('data-card');
  if (want('quick')) {
    // the bar: Tico and search, each one tap to type
    await quick(page, `phone, ${name}`, { sync: true, tico: () => tab('Tico').tap(), search: () => tab('Search').tap() });
    if (!want('phone')) return void (await ctx.close());
    await page.goto(`${APP}/tasks`);
    await settle(page, 2500);
  }
  const icons = () => page.locator('.phone>.tabbar button').evaluateAll((bs) => bs.map((b) => b.getAttribute('aria-label') ?? ''));
  const first = await icons();
  check(first.slice(0, 3).join() === 'Search,Tico,Company' && !first.includes('Task'), `the bar on a board: ${first.join(', ')}`, `the bar on a board: ${first.join(', ') || 'none'}`);
  const histBefore = await page.evaluate(() => history.length);
  const urlBefore = page.url();
  // Company on the page opens the menu, and again puts it away; neither is a step in the history
  await tab('Company').tap();
  await settle(page, 600);
  check((await card()) === 'menu', 'Company on the page raises the menu', `menu not up after Company (card: ${await card()})`);
  await shot(page, `phone-${name}-menu`);
  await tab('Company').tap();
  await settle(page, 600);
  check((await card()) === 'none', 'Company again brings the page back', `the menu stayed (card: ${await card()})`);
  await tab('Tico').tap();
  await settle(page, 600);
  await page.locator('.phone .ly-tico textarea').first().blur();
  await tab('Company').tap();
  await settle(page, 600);
  check((await card()) === 'none' && page.url() === urlBefore, 'Company from Tico brings the page back', `Company from Tico: card ${await card()}, ${page.url()}`);
  check((await page.evaluate(() => history.length)) === histBefore, 'the bar adds nothing to the history', 'a tap on the bar pushed a history entry');
  // a place picked in the menu: the menu goes, the page comes, and Back is the page before
  await tab('Company').tap();
  await settle(page, 600);
  await page.locator('.ly-menu .nav-item', { hasText: 'Calendar' }).first().tap();
  await settle(page, 900);
  check((await card()) === 'none' && new URL(page.url()).pathname === '/calendar', 'a place in the menu opens with the menu down', `after Calendar: card ${await card()}, ${page.url()}`);
  await page.goBack();
  await settle(page, 900);
  check(page.url() === urlBefore, 'Back after a place in the menu is the page before', `Back went to ${page.url()}`);

  // no field under the bar: a thread's own box ends above it
  await page.goto(`${APP}/threads`);
  await settle(page, 2500);
  const thread = await page.evaluate(() =>
    [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') ?? '').find((h) => /^\/threads\/[0-9a-f-]{36}$/.test(h)),
  );
  if (thread) {
    await page.goto(`${APP}${thread}`);
    await settle(page, 2500);
    await noBarOnFields(page, 'a thread');
  }
  // a task: it opens full screen over its board, the task and its chat as tabs; Close goes back to the board
  await page.goto(`${APP}/tasks`);
  await settle(page, 2500);
  // Tasks lists the pipelines under Needs you: the first one's board
  const board = await page.evaluate(() => [...document.querySelectorAll('section[aria-label="Pipelines"] a[href]')].map((a) => a.getAttribute('href') ?? '')[0]);
  if (board) {
    await page.goto(`${APP}${board}`);
    await settle(page, 2500);
  }
  const list = new URL(page.url()).pathname;
  const href = await page.evaluate(() =>
    [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href') ?? '').find((h) => /^\/tasks\/[^/]+\/[^/]+/.test(h) && !h.endsWith('/edit')),
  );
  if (href) {
    await page.locator(`a[href="${href}"]`).first().tap();
    await settle(page, 2500);
    const over = page.getByRole('dialog');
    check((await over.count()) === 1, 'a task opens over its board', `a task: ${await over.count()} dialogs at ${page.url()}`);
    await shot(page, `phone-${name}-task`);
    const chat = over.locator('.tmd-panes').getByRole('button', { name: 'Chat', exact: true });
    if (await chat.count()) {
      await chat.tap();
      await settle(page, 1200);
      check((await page.locator('.phone textarea:visible, .vw textarea:visible').count()) <= 1, 'one message box on screen', 'two message boxes on screen');
      await shot(page, `phone-${name}-task-chat`);
    }
    await over.getByRole('button', { name: 'Close', exact: true }).tap();
    await settle(page, 1500);
    check(new URL(page.url()).pathname === list && !new URL(page.url()).searchParams.has('task'), 'Close on a task goes back to the board', `Close on a task: ${page.url()} (from ${list})`);
  }
  await ctx.close();
}

/** Nothing that can be typed in, under the bar at the bottom. */
async function noBarOnFields(page: Page, where: string) {
  const hits = await page.evaluate(() => {
    const seen = (el: Element) => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && el.getBoundingClientRect().height > 0;
    const bar = document.querySelector('.phone>.tabbar')?.getBoundingClientRect();
    if (!bar) return ['no bar'];
    return [...document.querySelectorAll('.composer, textarea, input:not([type=hidden]), [contenteditable]')]
      .filter(seen)
      .filter((f) => f.getBoundingClientRect().bottom > bar.top + 1)
      .map((f) => f.className || f.tagName);
  });
  check(!hits.length, `nothing to type in under the bar: ${where}`, `under the bar (${where}): ${hits.join(', ')}`);
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

  const mobile = await open(browser, true, '/needs-you', true);
  try {
    await mobile.page.locator('main a[href^="/task/"]').first().click();
    const back = mobile.page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true });
    await back.waitFor();
    const fits = await back.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth;
    });
    assert(fits, "the task's Close is clipped at 390");

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
  ok('P&L, Calendar, a task opened from Needs you and the Model dialog fit their sheets');
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
  if (['boot', 'menu', 'sheets', 'quick', 'keys'].some(want)) await desktop(browser);
  if (want('phone') || want('quick')) await phone(browser, 'chromium');
  if (want('responsive')) await responsive(browser);
  if (want('dev')) await dev(browser);
  if (want('light')) await light(browser);
} finally {
  await browser.close();
}
if ((want('phone') || want('quick')) && process.env.WEBKIT !== '0') {
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
