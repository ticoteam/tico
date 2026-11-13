// The chat composer's shape (#298): every composer on .p-pill keeps whole lines and its buttons on the last line.
// Typed 1, 2, 4, 8 and 12 lines, each composer shows every line whole, its first line's box 13px below the top border,
// the Send circle 6px above the bottom border, Attach, the ways caret and Send at the same x, and a 9px corner at every
// height, focused or not. One line is 48px on a computer; the box stops at eight lines (160px, 176px on a phone) and
// scrolls inside with the caret's line in view; emptied, it is one line again. Run on a computer (1440x900) and a phone
// (390x844) for the five composers: the bot chat (a bot's DM, with the ways caret), a thread, a DM, a task's chat and
// the ask panel's (makePill), plus the Assistant page's makePill in #chat-composer, whose phone layout is a grid. Every request is answered offline by the tasks page's fake.
// TICO_SCREENSHOT_DIR=<dir> saves each composer at 5 lines.
import type * as Tasks from './tasks-page.cts';
import type * as ThreadsFake from './support/threads-fake.cts';
import type {Browser, Locator, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {open} = require('./tasks-page.cts') as Tasks.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFake.Api;
const CHAN = 't-launch';
const shots = process.env.TICO_SCREENSHOT_DIR;
declare function threadMount(host: Element, opts: Record<string, unknown>): void;
declare function makePill(cfg: Record<string, unknown>): {el: HTMLElement};

interface Shape {
  pill: {top: number; bottom: number; height: number; radius: string; bt: number; bb: number};
  text: {top: number; client: number; scroll: number; scrollTop: number; line: number; padTop: number; padBottom: number; font: number};
  send: {bottom: number; x: number; w: number; h: number} | null;
  xs: number[];
  targets: {cls: string; w: number; h: number}[];
}

// Where the parts of one composer sit, read in the page.
const measure = (pill: Locator): Promise<Shape> => pill.evaluate((el: HTMLElement) => {
  const cs = getComputedStyle(el), r = el.getBoundingClientRect();
  const box = el.querySelector('.p-text') as HTMLTextAreaElement, tc = getComputedStyle(box), tr = box.getBoundingClientRect();
  const shown = (b: Element) => (b as HTMLElement).offsetParent !== null && getComputedStyle(b).display !== 'none';
  const send = [...el.querySelectorAll('.p-send-icon')].find(shown) as HTMLElement | undefined;
  // The circle is the button less its transparent hit-area border.
  const circle = send ? (() => { const s = send.getBoundingClientRect(), b = parseFloat(getComputedStyle(send).borderTopWidth) || 0;
    return {bottom: s.bottom - b, x: s.left, w: s.width - 2 * b, h: s.height - 2 * b}; })() : null;
  const buttons = [...el.querySelectorAll('.p-attach, .p-way, .p-send-icon')].filter(shown);
  return {pill: {top: r.top, bottom: r.bottom, height: r.height, radius: cs.borderTopLeftRadius, bt: parseFloat(cs.borderTopWidth), bb: parseFloat(cs.borderBottomWidth)},
    text: {top: tr.top, client: box.clientHeight, scroll: box.scrollHeight, scrollTop: box.scrollTop, line: parseFloat(tc.lineHeight),
      padTop: parseFloat(tc.paddingTop), padBottom: parseFloat(tc.paddingBottom), font: parseFloat(tc.fontSize)},
    send: circle, xs: buttons.map(b => Math.round(b.getBoundingClientRect().left)),
    targets: [...el.querySelectorAll('.p-bot, .p-attach, .p-way, .p-send-icon')].filter(shown).map(b => {
      const t = b.getBoundingClientRect(); return {cls: b.className, w: Math.round(t.width), h: Math.round(t.height)}; })};
});

const lines = (n: number) => Array.from({length: n}, (_, i) => `Line ${i + 1} of the plan`).join('\n');
const near = (a: number, b: number, what: string, slack = 1) => assert.ok(Math.abs(a - b) <= slack, `${what}: ${a} (want ${b} ±${slack})`);

async function check(page: Page, name: string, pill: Locator, phone: boolean) {
  const box = pill.locator('.p-text');
  const cap = phone ? 176 : 160, row = phone ? 22 : 20;
  let xs: number[] | null = null;
  for (const n of [1, 2, 4, 5, 8, 12]) {
    await box.fill(lines(n));
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const s = await measure(pill), at = `${name} ${phone ? 'phone' : 'computer'}, ${n} lines`;
    if (n === 5 && shots) await pill.screenshot({path: path.join(shots, `pill-${name}-${phone ? 'phone' : 'computer'}-5.png`)});
    assert.equal(s.pill.radius, '9px', `${at}: the corner`);
    assert.equal(s.text.line, row, `${at}: the line height`);
    if (phone) assert.equal(s.text.font, 16, `${at}: 16px text on a phone`);
    // Whole lines: no padding inside the scroller, and its height a whole number of lines.
    assert.equal(s.text.padTop + s.text.padBottom, 0, `${at}: no padding inside the scroller`);
    assert.equal(s.text.client % row, 0, `${at}: the box shows whole lines (${s.text.client}px)`);
    near(s.text.top - s.pill.top - s.pill.bt, 13, `${at}: the first line's box below the top border`);
    assert.ok(s.send, `${at}: Send shows`);
    near(s.pill.bottom - s.pill.bb - s.send!.bottom, 6, `${at}: Send's circle above the bottom border`);
    near(s.send!.w, 34, `${at}: Send's circle`);
    if (xs) assert.deepEqual(s.xs, xs, `${at}: the buttons keep their x`); else xs = s.xs;
    if (n <= 8) {
      assert.equal(s.text.client, n * row, `${at}: every line shows`);
      assert.ok(s.text.scroll <= s.text.client, `${at}: nothing scrolls`);
    } else {
      assert.equal(s.text.client, cap, `${at}: the box stops at eight lines`);
      assert.ok(s.text.scrollTop + s.text.client >= s.text.scroll - 1, `${at}: the caret's line is in view`);
      assert.equal(s.text.scrollTop % row, 0, `${at}: the last line shows whole`);
    }
    if (n === 1 && !phone) near(s.pill.height, 48, `${at}: one line is 48px`);
    // A phone keeps 44px targets (the row still takes 34px); a computer's Attach and caret are 34px squares.
    if (n === 1) for (const t of s.targets) {
      const want = phone || /p-send-icon/.test(t.cls) ? 44 : 34;
      assert.ok(t.w >= want && t.h === want, `${at}: ${t.cls} is a ${want}px target (${t.w}x${t.h})`);
    }
  }
  await box.focus();
  assert.equal((await measure(pill)).pill.radius, '9px', `${name}: the corner while focused`);
  await box.fill('');
  await page.evaluate(() => new Promise(requestAnimationFrame));
  const empty = await measure(pill);
  assert.equal(empty.text.client, row, `${name}: emptied, the box is one line again`);
  if (!phone) near(empty.pill.height, 48, `${name}: emptied, 48px again`);
}

async function run(browser: Browser, phone: boolean) {
  const viewport = phone ? {width: 390, height: 844} : {width: 1440, height: 900};
  const {page, errors} = await open(browser, {viewport, hash: '#/task/t-3'});
  const task = page.locator('#task-modal .task-chat .p-pill');
  await task.waitFor();
  await check(page, 'task', task, phone);
  await page.keyboard.press('Escape');
  // Later routes win: a bot's DM and a company chat answer from the threads fake, anything else from the tasks page's.
  const fake = threadsFake({bot: 'engineering-manager', sent: []});
  await page.route('**/api/v2/threads/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, now = new Date().toISOString();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === `/api/v2/threads/${CHAN}/timeline`) return json({thread: {id: CHAN, title: 'launch', audience: 'company', root_id: CHAN, path: [CHAN],
      last_seq: 1, last_unread_seq: 0, last_message_at: now, me: {member: true, following: true, follows: true, notify: 'live', last_read_seq: 1,
        unread: 0, activity: false, can_post: true, can_manage: false}}, breadcrumb: [], next_cursor: null,
      messages: [{id: 'l1', seq: 1, depth: 0, thread_id: CHAN, sender_id: 'human:ben', body: 'Launch is Friday.', created_at: now}]});
    const answer = fake(p, url, req.method(), null);
    return answer === undefined ? route.fallback() : json(answer);
  });
  // The thread view's composers and the ask panel's pill, each in its own frame over the page.
  await page.evaluate(() => {
    const host = (id: string) => {
      const el = document.createElement('div'); el.id = id;
      el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:9999;max-width:760px;margin:0 auto;padding:8px;background:var(--bg)';
      el.hidden = true; document.body.append(el); return el;
    };
    threadMount(host('shape-bot'), {dm: 'engineering-manager', goals: 'engineering-manager', header: false});
    threadMount(host('shape-thread'), {id: 't-launch', header: false});
    threadMount(host('shape-dm'), {dm: 'sam', header: false});
    host('shape-ask').append(makePill({mode: 'chat', label: 'Message the Assistant', placeholder: 'Message the Assistant'}).el);
    // The Assistant page's: makePill with its bot inside #chat-composer, whose phone layout is a grid (chat.css).
    const page = document.createElement('div'); page.id = 'chat-composer';
    page.append(makePill({mode: 'chat', slug: 'engineering-manager', label: 'Message the Assistant'}).el);
    host('shape-assistant').append(page);
  });
  for (const name of ['bot', 'thread', 'dm', 'ask', 'assistant']) {
    await page.evaluate(n => { for (const el of document.querySelectorAll<HTMLElement>('[id^="shape-"]')) el.hidden = el.id !== `shape-${n}`; }, name);
    const pill = page.locator(`#shape-${name} .p-pill`);
    await pill.waitFor();
    if (name === 'bot') assert.equal(await pill.locator('.p-way').count(), 1, 'the bot chat has the ways caret');
    await check(page, name, pill, phone);
  }
  assert.deepEqual(errors.filter(e => !/fetch|Failed to load|404/i.test(e)), [], 'no page errors');
  await page.context().close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined
    ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    await run(browser, false);
    await run(browser, true);
    console.log('pill shape: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
