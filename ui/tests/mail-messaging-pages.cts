// Message bots (ui/app/mail.ts, #/mail) and a message bot's page (ui/app/messaging.ts, #/messaging) as the style guide
// draws a page: the one page header (title, tabs only when there are two views, search, the shared filter icon, ⋯ last),
// filters behind the icon with chips under the header, every empty state said once, no side box repeating the title, a
// failed sync said in red, instructions as reading text (not monospace) with no subtitle, and a bot's Automations, Bot
// settings and Pause in its ⋯ rather than stacked buttons. At 1280 and 390, light and dark; nothing scrolls sideways.
// Fixtures only, no network.
import type {Browser, LaunchOptions, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

const ago = (hours: number) => new Date(Date.now() - hours * 36e5).toISOString();
type Box = {address: string; name: string; synced_at: string | null; message_count: number; error?: string | null; agent_bot?: boolean; agent_instructions?: string | null};
type World = {boxes: Box[]; messages: Record<string, unknown>[]; reads: URLSearchParams[]; posts: {path: string; body: Record<string, unknown>}[]};
const ANA: Box = {address: 'ana@acme.example', name: 'Ana Rivera', synced_at: null, message_count: 0};
const SUPPORT: Box = {address: 'support@acme.example', name: 'Support', synced_at: ago(2), message_count: 2, agent_bot: true, agent_instructions: 'AGENTS.md'};
const MESSAGES = [
  {thread_id: 't1', mailbox: 'support@acme.example', date: ago(1), from_header: 'Kim <kim@example.test>', from_addr: 'kim@example.test', subject: 'Calibration steps', snippet: 'The lens drifts after an hour', labels: ['INBOX', 'UNREAD']},
  {thread_id: 't2', mailbox: 'support@acme.example', date: ago(30), from_header: 'Lee <lee@example.test>', from_addr: 'lee@example.test', subject: 'Invoice copy', snippet: 'Could you resend it', labels: ['INBOX'], has_attachment: true},
];
const BOT = {bot: 'support-owner', name: 'Support Owner', description: 'Owns the support queue and drafts every reply.', state: 'active', can_manage: true,
  sources: [{id: 'slack:CSUP', kind: 'slack', name: '#support', post: false}, {id: 'email:support@acme.example', kind: 'email', name: 'support@acme.example', synced_at: ago(2)}],
  last_run: ago(4), next_run: null};

async function open(browser: Browser, viewport: {width: number; height: number}, w: World, theme: string): Promise<{page: Page; errors: string[]; context: import('playwright').BrowserContext}> {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760, isMobile: viewport.width < 760});
  await context.addInitScript(t => { localStorage.setItem('tico.theme', t); }, theme);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, mail_access: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/employees') return json([{id: 'support-owner', slug: 'support-owner', display_name: 'Support Owner', status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/setup/getting-started') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true, cards_dismissed: [], can_build: true, owner: true, empty: {}});
    if (p === '/api/v2/mail/mailboxes') return json({mailboxes: w.boxes});
    if (p === '/api/v2/mail/messages') {
      w.reads.push(url.searchParams);
      const label = url.searchParams.get('label'), box = url.searchParams.get('mailbox');
      return json({messages: w.messages.filter(m => (!box || m.mailbox === box) && (!label || (m.labels as string[]).includes(label))), next_cursor: null});
    }
    if (p.startsWith('/api/v2/mail/threads/')) return json({messages: w.messages.filter(m => m.thread_id === p.split('/').pop()).map(m => ({...m, body: 'Hello,\nthe lens drifts.'}))});
    if (p === '/api/v2/mail/agent') return json({updated_at: ago(3), instructions: 'Answer every support message within the hour.\nAsk before refunds.'});
    if (p === '/api/v2/messaging/bots') return json({bots: [{bot: BOT.bot, state: BOT.state, revision: 4}]});
    if (p === `/api/v2/messaging/bots/${BOT.bot}`) return json({bot: BOT, slack: [{ts: String(Date.now() / 1000 - 3600), thread_ts: '1', channel: 'CSUP', author: 'Kim', text: 'The calibration steps again?'}],
      slack_posts: [], mail: [], instructions: {published: false, content: 'Owns the support queue and drafts every reply.'}});
    if (p === `/api/v2/bots/${BOT.bot}/definition` && method === 'POST') { w.posts.push({path: p, body: JSON.parse(route.request().postData() || '{}')}); return json({}); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, context};
}
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
// The header's controls in order, as what a person reads: the last must be ⋯ when there is one.
// The header's tools fold into its ⋯ when a phone has no room for them (ui/app/page-head.ts): the filter is then one tap further.
async function filterReach(page: Page) {
  const filter = page.locator('#mail-filter'), folded = !await filter.isVisible();
  if (folded) await page.locator('#main header.ph > .ph-more').click();
  await filter.waitFor();
  return folded;
}
const lastInHeader = (page: Page) => page.locator('#main header.ph').first().evaluate(h => {
  const shown = [...h.querySelectorAll<HTMLElement>(':scope > .ph-tools > *, :scope > .ph-act > *, :scope > .ph-more')].filter(el => el.getClientRects().length);
  return shown.at(-1)?.className || '';
});
const SHOTS = process.env.TICO_MAIL_SCREENSHOTS;
async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name + '.png'), animations: 'disabled'});
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    for (const [size, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 844}]] as const) {
      for (const theme of ['light', 'dark'] as const) {
        const where = `${size}-${theme}`;
        // ---- Mail, one mailbox and no messages in its Gmail: one header, no tabs, no side box, the empty state once.
        {
          const w: World = {boxes: [ANA], messages: [], reads: [], posts: []};
          const {page, errors, context} = await open(browser, viewport, w, theme);
          await page.goto('https://tico-ui.test/#/mail');
          await page.locator('#mail-list .empty-state').waitFor();
          assert.equal(await page.locator('#main h1').innerText(), 'Message bots', `${where}: the page title`);
          assert.equal(await page.locator('#main .ph-tab').count(), 0, `${where}: one view is no tabs`);
          assert.equal(await page.locator('.mail-boxes').isVisible(), false, `${where}: no mailboxes box for one mailbox`);
          const text = await page.locator('#main').innerText();
          assert.equal(text.match(/Message bots/g)?.length, 1, `${where}: the title is said once`);
          assert.equal(text.match(/No messages/g)?.length, 1, `${where}: the empty state is said once: ${text}`);
          assert.doesNotMatch(text, /sync/i, `${where}: mail is read live from Gmail, never "synced": ${text}`);
          assert.doesNotMatch(text, /Select a message/, `${where}: no text in an empty reading pane`);
          assert.equal(await page.locator('#mail-thread').isVisible(), false, `${where}: no reading pane beside an empty list`);
          assert.equal(await page.locator('#mail-fresh').isVisible(), false, `${where}: no sync caption`);
          await filterReach(page);
          assert.equal(await page.locator('#mail-filter').isVisible(), true, `${where}: the shared filter icon`);
          assert.equal(await page.locator('#main .mail-chip').count(), 0, `${where}: no row of bare filter chips`);
          assert.equal(await sideways(page), false, `${where}: nothing scrolls sideways`);
          await shot(page, `mail-empty-${where}`);
          assert.deepEqual(errors, [], where);
          await context.close();
        }
        // ---- Mail, two mailboxes, one with instructions and a failed Gmail check: the list beside, tabs, the filter.
        {
          const w: World = {boxes: [SUPPORT, {...ANA, synced_at: ago(5), error: 'The Gmail key was refused'}], messages: MESSAGES, reads: [], posts: []};
          const {page, errors, context} = await open(browser, viewport, w, theme);
          await page.goto('https://tico-ui.test/#/mail?mailbox=support%40acme.example');
          await page.locator('.mail-row').first().waitFor();
          assert.equal(await page.locator('.mail-boxes').isVisible(), true, `${where}: the mailboxes list with two to choose from`);
          assert.equal(await page.locator('.mail-boxes h2').count(), 0, `${where}: no heading repeating the page title`);
          assert.deepEqual(await page.locator('#main .ph-tab').allInnerTexts(), ['Messages', 'Instructions'], `${where}: two views, two tabs`);
          assert.equal(await page.locator('#mail-thread').innerText(), '', `${where}: an unread pane says nothing`);
          // The filter: Unread through the icon, a chip with ✕, Esc back to the icon.
          const folded = await filterReach(page);
          await page.locator('#mail-filter').click();
          await page.locator('#task-filter-pop .tl-opt', {hasText: 'Unread'}).click();
          await page.waitForFunction(() => /label=UNREAD/.test(location.hash));
          await page.waitForFunction(() => document.querySelectorAll('.mail-row').length === 1);
          await page.keyboard.press('Escape');
          await page.locator('#task-filter-pop').waitFor({state: 'hidden'});
// Esc hands the keyboard back to the icon where the icon is in the header; folded into ⋯ (a phone) there is no keyboard to hand back.
          if (!folded) assert.equal(await page.evaluate(() => document.activeElement?.id), 'mail-filter', `${where}: Esc returns to the filter icon`);
          assert.match(await page.locator('#mail-chips').innerText(), /Label\s*Unread/, `${where}: the filter's chip`);
          assert.equal(await page.locator('#mail-filter .tl-fbadge').innerText(), '1', `${where}: the icon's count`);
          await page.locator('[data-mail-drop=label]').click();
          await page.waitForFunction(() => !/label=/.test(location.hash) && document.querySelectorAll('.mail-row').length === 2);
          assert.equal(await page.locator('#mail-filters').isVisible(), false, `${where}: no chips once the filter is off`);
          // The thread opens beside the list (over it on a phone).
          await page.locator('.mail-row', {hasText: 'Calibration steps'}).click();
          await page.locator('.mail-msg').first().waitFor();
          assert.match(await page.locator('#mail-thread h2').innerText(), /Calibration steps/);
          // Instructions read as text.
          await page.locator('#main .ph-tab', {hasText: 'Instructions'}).click();
          await page.locator('.mail-agent-text').waitFor();
          assert.doesNotMatch(await page.locator('.mail-agent-text').evaluate(el => getComputedStyle(el).fontFamily), /mono/i, `${where}: instructions are not monospace`);
          assert.equal(await page.locator('#main .ph-tab[aria-selected=true]').innerText(), 'Instructions');
          await shot(page, `mail-instructions-${where}`);
          await page.locator('#main .ph-tab', {hasText: 'Messages'}).click();
          await page.locator('#mail-inbox-view').waitFor();      // the open thread stays open (over the list on a phone)
          // The other mailbox's Gmail check failed: why is said in red, and it stays.
          await page.locator(`[data-mailbox="${ANA.address}"]`).click();
          await page.locator('#mail-fresh').waitFor();
          assert.match(await page.locator('#mail-fresh').innerText(), /Ana Rivera: The Gmail key was refused/, `${where}: a failed check says why`);
          assert.equal(await page.locator('#mail-fresh').evaluate(el => el.classList.contains('err')), true);
          assert.equal(await sideways(page), false, `${where}: nothing scrolls sideways`);
          await shot(page, `mail-list-${where}`);
          assert.deepEqual(errors, [], where);
          await context.close();
        }
        // ---- A message bot: its name in the one header, its tools in ⋯ (last), no paragraph, no subtitle, text instructions.
        {
          const w: World = {boxes: [], messages: [], reads: [], posts: []};
          const {page, errors, context} = await open(browser, viewport, w, theme);
          await page.goto('https://tico-ui.test/#/messaging');
          await page.locator('.msg-instructions').waitFor();
          assert.equal(await page.locator('#main h1').innerText(), 'Support Owner', `${where}: the bot's name is the title`);
          assert.equal(await page.locator('.msg-actions, .msg-detail-head').count(), 0, `${where}: no stacked buttons under the title`);
          assert.equal(await page.locator('#main .ph-tools a, #main .ph-tools button, #main .ph-act button').count(), 0, `${where}: nothing daily on the bar`);
          assert.equal(await lastInHeader(page), 'ph-more', `${where}: ⋯ is last`);
          const text = await page.locator('#main').innerText();
          assert.doesNotMatch(text, /Published summary|have not been reported/, `${where}: no subtitle`);
          assert.equal(text.split('Owns the support queue').length - 1, 1, `${where}: the description is not repeated as a paragraph`);
          assert.doesNotMatch(await page.locator('.msg-instructions').evaluate(el => getComputedStyle(el).fontFamily), /mono/i, `${where}: instructions are not monospace`);
          assert.deepEqual(await page.locator('.msg-sources.ph-seg [data-msg-source]').evaluateAll(els => els.map(e => e.getAttribute('aria-pressed'))), ['true', 'false', 'false'], `${where}: the sources are one segment`);
          await page.locator('#main header.ph > .ph-more').click();
          const menu = page.locator('#main header.ph > .ph-menu');
          await page.waitForFunction(() => !!document.querySelector<HTMLElement>('#main header.ph > .ph-menu')?.style.top);   // placed under ⋯
          assert.deepEqual((await menu.locator('a, button').allInnerTexts()).map(t => t.trim()), ['Automations', 'Bot settings', 'Pause bot'], `${where}: the bot's tools in ⋯`);
          await shot(page, `messaging-more-${where}`);
          await menu.getByText('Pause bot').click();
          await page.waitForFunction(() => !!document.querySelector('.msg-instructions'));
          assert.deepEqual(w.posts.at(-1)?.body, {status: 'paused', expected_revision: 4}, `${where}: Pause still pauses`);
          assert.equal(await sideways(page), false, `${where}: nothing scrolls sideways`);
          assert.deepEqual(errors, [], where);
          await context.close();
        }
      }
    }
    console.log('mail-messaging-pages ok: Message bots and a message bot at 1280 and 390, light and dark');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
