// A bot's page and a person's page as the style guide draws them (wave 2, L1): which chat a bot's page opens on (never
// an empty Home while the bot talks in the viewer's DM), ⋯ last in the top line with the details, Incognito and the
// private chat's Archive in it (Esc gives the focus back), the detail tabs in the header's tab look with no "‹ Home"
// link, no tabs inside the Instructions tab, Cancel before Send changes, Settings without a second "Settings" title,
// the Last run dialog closed by ✕, the rail's task tabs that do not fit behind a ⋯ of their own, and a person's page
// on the shared header with quiet icon buttons, an amber "!" only on the exception and the house dialog order.
// Fixtures only, no network. TICO_SCREENSHOT_DIR keeps pictures of each.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
import type {Browser, LaunchOptions, Page} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RoutesSupport from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RoutesSupport.Api;

// The page's own globals the test reaches in the browser (ui/app/hub-v2.ts S, ui/app/bot-page.ts botAlertDraw).
declare const S: {v2: {status: Record<string, Record<string, unknown> | undefined>}};
declare function botAlertDraw(): void;
declare function botTaskTabsFit(slug: string): void;
const shots = process.env.TICO_SCREENSHOT_DIR;
const at = (min: number) => new Date(Date.now() - min * 60e3).toISOString();
const BOT = {name: 'spo', display_name: 'Skills Owner', org_parent: '', host: 'keeper', status: 'active', can_chat: true, runtime: 'codex',
  users: [{id: 'ana', name: 'Ana'}], schedules: [], my_access: {see: true, read: true, write: true}, can_manage: true};
const me = () => ({member: true, following: true, notify: 'live', last_read_seq: 9, unread: 0, can_post: true, can_manage: true, pinned: false});
const HOME = {id: 'h-spo', title: 'Skills Owner', audience: 'home', home: {actor: 'bot:spo'}, last_seq: 5, me: me()};
const DM = {id: 'd-spo', title: '', audience: 'personal', last_seq: 3, me: me(), dm_with: [{actor: 'bot:spo', name: 'Skills Owner'}], dm_with_count: 1,
  archived_at: null, last_message_at: at(30)};
// The Home holds the bot's update card (the rail shows updates; the thread hides them) and, when `talk` says so, a reply
// newer than the DM's.
const homeMessages = (talk: boolean) => [
  {id: 'u1', seq: 1, thread_id: 'h-spo', sender_id: 'bot:spo', kind: 'notice', body: 'Daily update', refs: {card: 'update', update: {id: 'up1'}}, created_at: at(5)},
  ...(talk ? [{id: 'h1', seq: 2, thread_id: 'h-spo', sender_id: 'human:ben', body: 'Team note in the Home.', created_at: at(2)}] : [])];
const dmMessages = [{id: 'd1', seq: 1, thread_id: 'd-spo', sender_id: 'bot:spo', body: 'Just us here.', created_at: at(30)}];
// Ben's tasks: one waiting on Ben (his own page's rule: no "!"), one waiting on Ana (the exception: "!").
const benTasks = [
  {id: 't-ben', title: 'Sign the vendor form', status: 'open', owner_id: 'human:ben', turn: {state: 'person', who: 'human:ben', line: 'Waiting on Ben'}, created_at: at(90), updated_at: at(60)},
  {id: 't-ana', title: 'Approve the budget line', status: 'open', owner_id: 'human:ben', turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana'}, created_at: at(80), updated_at: at(50)}];
// Five tabs whose names do not fit the rail beside each other.
const TABS = [{builtin: 'needs', name: 'Needs you'}, {builtin: 'active', name: 'Open'}, {builtin: 'blocked', name: 'Waiting'},
  {name: 'Assigned to other teammates', conditions: [{field: 'assigned_to', value: 'others'}]}, {builtin: 'done', name: 'Done recently'}];

// `dm`: the viewer's DM with the bot as the server answers it: there, archived, or a read that fails.
async function open(browser: Browser, {phone = false, talk = false, theme = 'dark', dm = 'ok'}: {phone?: boolean; talk?: boolean; theme?: string; dm?: 'ok' | 'archived' | 'fail'} = {}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1440, height: 900}, serviceWorkers: 'block',
    reducedMotion: 'reduce', ...(phone ? {isMobile: true, hasTouch: true} : {})});
  await context.addInitScript((t: string) => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const page = await context.newPage();
  const seen = {errors: [] as string[], posts: [] as {path: string; body: Record<string, unknown>}[]};
  page.on('pageerror', e => seen.errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const vendor = p.match(/^\/vendor\/((?:fonts\/)?[\w.-]+)$/);
    if (vendor && fs.existsSync(uiFile('vendor/' + vendor[1]))) return route.fulfill({path: uiFile('vendor/' + vendor[1])});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (req.method() === 'POST') { seen.posts.push({path: p, body: JSON.parse(req.postData() || '{}')}); return json({id: 't-new', title: 'x', status: 'open'}); }
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true, bot_admin: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Rivera', email: 'ana@example.test'},
      {id: 'ben', name: 'Ben Okafor', email: 'ben@example.test', reports_to: 'ana', title: 'Operations', my_access: {see: true, read: true, write: true, manage: true}}]});
    if (p === '/api/employees') return json([BOT]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'spo', state: 'idle', bot_state: 'active'}]});
    if (p === '/api/v2/bots/spo') return json({display_name: 'Skills Owner', access: {}, cant_run: null});
    if (p === '/api/v2/bots/spo/task-tabs') return json({tabs: TABS, widgets: [{id: 'tasks', hidden: false, height: null}, {id: 'updates', hidden: false, height: null}, {id: 'files', hidden: false, height: null}], can_edit: true});
    if (p === '/api/v2/threads/home/bot:spo' || p === '/api/v2/threads/home/' + encodeURIComponent('bot:spo')) return json(HOME);
    if (p === '/api/v2/threads/dm') return dm === 'fail' ? json({error: {code: 'unavailable', message: 'The hub is restarting.'}}, 503)
      : json(dm === 'archived' ? {...DM, archived_at: at(1)} : DM);
    if (p === '/api/v2/threads/h-spo/timeline') return json({thread: HOME, breadcrumb: [], next_cursor: null, messages: homeMessages(talk), bot_status: []});
    if (p === '/api/v2/threads/d-spo/timeline') return json({thread: DM, breadcrumb: [], next_cursor: null, messages: dmMessages, bot_status: []});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls|reach)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false} : p.endsWith('members') ? {members: [], next_cursor: null} : p.endsWith('reach') ? {reaches: []} : {ok: true});
    if (p === '/api/v2/threads/home/human:ben' || p === '/api/v2/threads/home/' + encodeURIComponent('human:ben')) return json({error: 'not_found'}, 404);
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: q.get('owner') === 'human:ben' ? benTasks : [], cursor: 'c1'});
    if (p === '/employees/spo/files' || p === '/api/employees/spo/files') return json({'AGENTS.md': '# Skills Owner\nKeep the skills list.', 'memory/learnings.md': '- Ask before merging.', 'bot.yaml': 'name: spo'});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, seen, context};
}
const shot = async (page: Page, name: string) => { if (shots) await page.screenshot({path: path.join(shots, `bot-page-guide-${name}.png`)}); };
// The rightmost shown control in the bot's top line, by where it is drawn.
const lastInLine = (page: Page) => page.locator('#bot-top').evaluate(top => [...top.querySelectorAll<HTMLElement>('button, a')]
  .filter(b => b.getClientRects().length && !b.closest('[popover]')).sort((a, b) => a.getBoundingClientRect().right - b.getBoundingClientRect().right).at(-1)?.id);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as LaunchOptions);
  try {
    // ---- Which chat the page opens on, on a computer.
    {
      const {page, seen, context} = await open(browser);
      await page.goto('https://tico-ui.test/#/person/ben');   // where the person was before the bot (Back returns here)
      await page.locator('#main > header.person-ph').waitFor();
      await page.evaluate(() => { location.hash = '#/bot/spo'; });
      // The Home holds only an update card, the DM a message: the page opens on the DM, the address stays bare.
      await page.getByText('Just us here.').waitFor();
      await page.locator('#bot-incognito[aria-pressed="true"]').waitFor();   // the DM shows: the lock is pressed
      // Across 1100px (a window dragged narrower, a tablet turned) the chat on show stays the DM, and from 761 to 1099px
      // the lock is an icon in the top line.
      await page.setViewportSize({width: 1000, height: 900});
      await page.waitForTimeout(200);
      assert.equal(await page.getByText('Just us here.').isVisible(), true, 'still the DM below 1100px');
      const lock = page.locator('#bot-incognito');
      assert.equal(await lock.getAttribute('aria-pressed'), 'true');
      assert.equal(await lock.isVisible(), true, 'the lock is in the line at 1000px');
      assert.equal(await lock.locator('.bi-label').isVisible(), false, 'an icon, no word');
      assert.ok(((await lock.boundingBox())?.width ?? 99) <= 44, 'an icon\'s width');
      await shot(page, 'dm-1000');
      await page.setViewportSize({width: 1440, height: 900});
      await page.waitForTimeout(200);
      assert.equal(await page.getByText('Just us here.').isVisible(), true, 'still the DM back above 1100px');
      assert.equal(await page.evaluate(() => location.hash), '#/bot/spo', 'nothing is rewritten');
      assert.equal(await page.locator('#bot-thread .th-empty', {hasText: 'No messages yet'}).count(), 0, 'never an empty Home');
      await shot(page, 'dm-1440');
      // The lock names the Home in the address, in place of the plain one: Back then leaves the bot (never a dead Back).
      await page.locator('#bot-incognito').click();
      await page.waitForFunction(() => location.hash === '#/bot/spo/home');
      await page.locator('#bot-incognito[aria-pressed="false"]').waitFor();
      await page.goBack();
      await page.waitForFunction(() => location.hash === '#/person/ben');
      await page.goForward();
      await page.waitForFunction(() => location.hash === '#/bot/spo/home');
      await page.locator('#bot-incognito[aria-pressed="false"]').waitFor();
      // ⋯ is the line's last control; its list is the details, then the private chat's Archive after a line.
      assert.equal(await lastInLine(page), 'bot-more-btn');
      const more = page.locator('#bot-more-btn'), menu = page.locator('#bot-more-menu');
      await more.focus();
      await page.keyboard.press('Enter');
      await menu.waitFor({state: 'visible'});
      await page.locator('#bot-more-btn[aria-expanded="true"]').waitFor();
      assert.deepEqual(await menu.locator('.bot-mgroup [data-bot-mtab]').allInnerTexts(), ['Info', 'Rules', 'Instructions', 'History', 'Settings']);
      assert.equal(await menu.locator('[data-bot-incognito]').isVisible(), false, 'a computer has the lock in the line, not in ⋯');
      await menu.locator('#bot-dm-arch').waitFor();
      assert.equal((await menu.locator('#bot-dm-arch .bot-mi-w').innerText()).trim(), 'Archive private chat');
      assert.equal(await menu.locator('[role=menuitem], [role=menu]').count(), 0, 'a named group of plain buttons, not an ARIA menu');
      assert.equal(await menu.getAttribute('role'), 'group');
      await shot(page, 'more-1440');
      await page.keyboard.press('Escape');
      await menu.waitFor({state: 'hidden'});
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'bot-more-btn', 'Esc gives the focus back to ⋯');
      // The rail's tabs keep to one line: those that fit, the current one among them, the rest behind the bar's ⋯.
      const rail = page.locator('#pane-tasks'), bar = rail.locator('.bot-task-tabs');
      const tabsMore = rail.locator('.bot-task-tabline > .bot-tab-more');
      await tabsMore.waitFor();
      assert.equal(await bar.evaluate(b => b.scrollWidth <= b.clientWidth + 1), true, 'the tab bar does not scroll sideways');
      assert.equal(await tabsMore.evaluate(m => !m.closest('[role=tablist]') && m.previousElementSibling?.getAttribute('role') === 'tablist' && !m.nextElementSibling), true, '⋯ is last on the line, outside the tab list');
      assert.equal(await tabsMore.getAttribute('aria-haspopup'), null, 'no menu announced: it opens plain buttons');
      const folded = await bar.locator('.bot-tab-over').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.botTaskTab));
      assert.ok(folded.length > 0, 'a tab waits behind ⋯');
      await tabsMore.click();
      const pop = page.locator('#task-filter-pop');
      await pop.waitFor({state: 'visible'});
      await rail.locator('.bot-tab-more[aria-expanded="true"]').waitFor();
      assert.deepEqual(await pop.locator('[data-bot-tab-pick]').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.botTabPick)), folded);
      await page.keyboard.press('Escape');
      await pop.waitFor({state: 'hidden'});
      assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('bot-tab-more')), true, 'Esc gives the focus back to the bar\'s ⋯');
      await rail.locator('.bot-tab-more[aria-expanded="false"]').waitFor();
      // Picking a folded tab shows it on the line, current and focused.
      await tabsMore.click();
      await pop.locator(`[data-bot-tab-pick="${folded.at(-1)}"]`).click();
      const picked = rail.locator(`#bot-tab-t-${folded.at(-1)}`);
      await picked.waitFor({state: 'visible'});
      assert.equal(await picked.getAttribute('aria-selected'), 'true');
      assert.equal(await picked.evaluate(el => el === document.activeElement), true, 'the picked tab has the focus');
      assert.equal(await bar.evaluate(b => b.scrollWidth <= b.clientWidth + 1), true, 'still one line');
      await shot(page, 'rail-1440');
      // Tabs that fit only once ⋯ is gone fit without it: ⋯ is measured out, never kept for its own width.
      const fits = await page.evaluate(() => {
        const bar = document.querySelector<HTMLElement>('#pane-tasks .bot-task-tabs')!, more = document.querySelector<HTMLElement>('#pane-tasks .bot-tab-more')!;
        const tabs = [...bar.querySelectorAll<HTMLElement>('[data-bot-task-tab]')];
        for (const tab of tabs) { tab.classList.remove('bot-tab-over'); tab.firstChild!.textContent = 'T '; }
        more.hidden = true;
        const last = tabs.at(-1)!, spare = bar.clientWidth - bar.scrollWidth - 4;   // the line's room less 4px: no room for ⋯
        last.style.width = `${last.offsetWidth + spare}px`;
        more.hidden = false;
        botTaskTabsFit('spo');
        return {more: !more.hidden, over: bar.querySelectorAll('.bot-tab-over').length, spare};
      });
      assert.deepEqual({more: fits.more, over: fits.over}, {more: false, over: 0}, 'every tab fits without ⋯: ' + JSON.stringify(fits));

      // The detail views: the header's tab look, no "‹ Home" link, the Chat mark the way back, no repeated card title.
      await page.goto('https://tico-ui.test/#/bot/spo/info');
      await page.locator('#bot-detail-tabs .ph-tab.cur', {hasText: 'Info'}).waitFor();
      assert.deepEqual(await page.locator('#bot-detail-tabs > button').evaluateAll(els => els.map(el => el.className)), ['ph-tab cur', 'ph-tab', 'ph-tab', 'ph-tab', 'ph-tab']);
      assert.equal(await page.locator('#bot-back, .bot-back').count(), 0, 'no ‹ Home link');
      assert.equal(await page.locator('#bot-setup-card > header').count(), 0, 'the Info card does not say Info again');
      assert.equal(await page.locator('#pane-more #bot-dm-arch, #pane-more .bot-learn#bot-dm-arch').count(), 0, 'no Archive word button over the card');
      assert.equal(await page.locator('#bot-goals-card .linkish').count(), 0, 'Add goal is a quiet + button, not a link');
      assert.equal(await page.locator('#bot-goals-add').getAttribute('aria-label'), 'Add goal');
      assert.equal(await page.locator('#bot-more-menu [data-bot-mtab="info"]').getAttribute('aria-current'), 'page');
      const chat = page.locator('#btabs [data-bt="home"]');
      assert.equal(await chat.isVisible(), true, 'a computer shows the Chat mark in a detail view');
      await shot(page, 'info-1440');
      await chat.click();
      await page.waitForFunction(() => location.hash === '#/bot/spo');
      await page.locator('#pane-chat:not([hidden])').waitFor();
      assert.equal(await chat.isVisible(), false, 'beside the chat the switch goes again');
      // Instructions: the working notes are sections, not tabs in a tab; Cancel sits before Send changes.
      await page.goto('https://tico-ui.test/#/bot/spo/instructions');
      await page.locator('#bot-notes details.bot-note[open] > summary', {hasText: 'Learnings'}).waitFor();
      assert.equal(await page.locator('#pane-docs .tabs, #pane-docs [role=tab], #tabs').count(), 0, 'no tabs inside the Instructions tab');
      assert.deepEqual(await page.locator('#bot-notes .bot-note').evaluateAll(els => els.map(el => el.querySelector('summary, .bot-note-h')?.textContent)),
        ['Learnings', 'Decisions', 'Manifest']);
      assert.equal(await page.locator('#bot-notes .bot-note-empty').innerText().then(s => s.replace(/\s+/g, ' ').trim()), 'Decisions Empty');
      assert.deepEqual(await page.locator('.bot-instructions-acts button').allInnerTexts(), ['Cancel', 'Send changes'], 'Cancel, then the forward action last');
      await page.locator('#bot-instructions-changes').fill('Add the review step.');
      await page.locator('.bot-instructions-acts .primary').click();
      await page.waitForFunction(() => document.querySelector('[data-instructions-status]')?.textContent === 'Changes sent.');
      // The last thing sent is the message; a page visit's own preference save (org.history, sent a moment after the
      // visit: ui/app/sidebar.ts orgHistorySave) can land after it.
      assert.equal(seen.posts.filter(x => !x.path.startsWith('/api/v2/preferences/')).at(-1)?.path, '/api/v2/assistant/messages');
      await shot(page, 'instructions-1440');
      // Settings: the editor's own "Settings" title is not drawn under the Settings tab (a screen reader still has it).
      await page.goto('https://tico-ui.test/#/bot/spo/settings');
      const title = page.locator('#bot-editor #bot-editor-title');
      await title.waitFor({state: 'attached'});
      assert.equal(await title.evaluate(el => el.getBoundingClientRect().width <= 1 && el.textContent === 'Settings'), true, 'no second Settings title');
      await shot(page, 'settings-1440');
      // Last run: ✕ closes it (never the word Close); its one action sits at the foot's right.
      await page.goto('https://tico-ui.test/#/bot/spo');
      await page.locator('#bot-top').waitFor();
      await page.evaluate(() => {
        S.v2.status.spo = {...(S.v2.status.spo || {}), state: 'crashed', last_stop: {text: 'Stopped at 9:12 AM: the computer went to sleep', reason: 'The lease ran out.', needs_person: true, action: 'review', at: new Date().toISOString()}} as never;
        botAlertDraw();
      });
      await page.locator('#bot-alert [data-last-stop]').click();
      const dialog = page.locator('dialog#last-stop');
      await dialog.waitFor();
      assert.equal(await dialog.locator('button', {hasText: /^Close$/}).count(), 0, 'no Close word');
      assert.equal(await dialog.locator('.tmodal-head .tmodal-x[aria-label="Close"] .nav-icon').innerText(), 'close');
      assert.equal(await dialog.locator('.tmodal-foot > :last-child').innerText(), 'Retry or dismiss');
      await shot(page, 'last-run-1440');
      await dialog.locator('.tmodal-x').click();
      await dialog.waitFor({state: 'detached'});
      assert.deepEqual(seen.errors, []);
      await context.close();
    }
    // ---- A Home with talk newer than the DM opens on the Home.
    {
      const {page, seen, context} = await open(browser, {talk: true});
      await page.goto('https://tico-ui.test/#/bot/spo');
      await page.getByText('Team note in the Home.').waitFor();
      assert.equal(await page.locator('#bot-incognito').getAttribute('aria-pressed'), 'false');
      assert.deepEqual(seen.errors, []);
      await context.close();
    }
    // ---- A DM that cannot be read, or one that is archived: the page opens on the Home, never an endless Loading.
    for (const dm of ['fail', 'archived'] as const) {
      const {page, seen, context} = await open(browser, {dm});
      await page.goto('https://tico-ui.test/#/bot/spo');
      await page.locator('#pane-chat:not(.bot-picking) #bot-thread .th-empty', {hasText: 'No messages yet'}).waitFor();
      assert.equal(await page.locator('#pane-chat .bot-pick-wait').isVisible(), false, dm + ': no Loading left');
      assert.equal(await page.locator('#bot-incognito').getAttribute('aria-pressed'), 'false', dm + ': the Home');
      assert.equal(await page.evaluate(() => location.hash), '#/bot/spo');
      assert.deepEqual(seen.errors, []);
      await context.close();
    }
    // ---- A phone: back, the bot, Chat and Tasks, then ⋯; Incognito is in ⋯ and the name is whole.
    {
      const {page, seen, context} = await open(browser, {phone: true, theme: 'light'});
      await page.goto('https://tico-ui.test/#/bot/spo');
      await page.getByText('Just us here.').waitFor();
      assert.equal(await lastInLine(page), 'bot-more-btn', '⋯ is last');
      assert.equal(await page.locator('#bot-incognito').isVisible(), false, 'no lock in the line');
      await shot(page, 'chat-390-light');
      assert.equal(await page.locator('.bot-ident h1').evaluate(h => h.scrollWidth <= h.clientWidth), true, 'the name is not cut');
      for (const box of await page.locator('#bot-top :is(#btabs button, #bot-more-btn, #bot-back-arrow)').evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON())))
        assert.ok(box.height >= 44 && box.width >= 44 && box.right <= 390, 'a 44px control: ' + JSON.stringify(box));
      assert.equal(await page.locator('#bot-top-acts [data-thv-members]').isVisible(), false, 'the chat\'s Members leaves the line');
      await page.locator('#bot-more-btn').tap();
      const item = page.locator('#bot-more-menu [data-bot-incognito]');
      assert.equal(await item.getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('#bot-more-menu [data-bot-members]').isVisible(), true, 'Members is in ⋯ instead');
      await shot(page, 'more-390-light');
      // Members from ⋯: the list closes and ⋯ has the focus, so closing the dialog gives the focus back to ⋯.
      await page.locator('#bot-more-menu [data-bot-members]').tap();
      const members = page.locator('dialog[open]', {has: page.locator('text=Members')});
      await members.waitFor();
      assert.equal(await page.locator('#bot-more-menu').isVisible(), false, 'the list closed');
      await members.locator('[aria-label^="Close"]').first().tap();
      await members.waitFor({state: 'hidden'});
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'bot-more-btn', 'the focus is back on ⋯');
      await page.locator('#bot-more-btn').tap();
      await item.tap();
      await page.waitForFunction(() => location.hash === '#/bot/spo/home');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'nothing scrolls sideways');
      assert.deepEqual(seen.errors, []);
      await context.close();
    }
    // ---- A person's page: the shared header, quiet icon buttons, "!" only on the exception, the house dialog.
    for (const phone of [false, true]) {
      const {page, seen, context} = await open(browser, {phone});
      await page.goto('https://tico-ui.test/#/person/ben');
      const head = page.locator('#main > header.ph.person-ph');
      await head.waitFor();
      assert.equal((await head.locator('.ph-t').innerText()).replace(/\s+/g, ' ').trim().endsWith('Ben Okafor'), true);
      assert.equal(await head.locator('.ph-t .av').count(), 1, 'their face in the title');
      assert.deepEqual(await page.locator('#ptabs .ph-tab').allInnerTexts(), ['Profile', 'Slack']);
      assert.equal(await page.locator('.bothead, .tabs.bottabs').count(), 0, 'no page header of its own');
      assert.equal(await head.locator(':scope > .ph-more').count(), 1, '⋯ last in the header');
      await page.locator('#person-tasks .tk-row').first().waitFor();
      assert.equal(await page.locator('#pane-profile section.card > header .linkish, #person-github .linkish').count(), 0, 'Edit and Add are buttons, not links');
      assert.deepEqual(await page.locator('#pane-profile .bp-ico').evaluateAll(els => els.map(el => el.getAttribute('aria-label')).filter(l => l !== 'Edit access')),
        ['Add GitHub login', 'Edit About', 'Add task', 'Edit Goals', 'Edit Notes']);
      const marks = await page.locator('#person-tasks .tk-row').evaluateAll(rows => rows.map(r => [r.querySelector('.tk-title')?.textContent, !!r.querySelector('.tk-mark-person')]));
      assert.deepEqual(marks, [['Sign the vendor form', false], ['Approve the budget line', true]], 'a task waiting on Ben has no "!" on his page; one waiting on Ana does');
      await shot(page, `person-${phone ? 390 : 1440}`);
      // An edit gives the focus back to its button when cancelled.
      await page.locator('[data-edit][aria-label="Edit Goals"]').click();
      const goals = page.locator('#pane-profile section.card', {has: page.locator('h2', {hasText: 'Goals'})});
      assert.deepEqual(await goals.locator('form .person-form-acts button').allInnerTexts(), ['Cancel', 'Save']);
      await goals.locator('[data-cancel]').click();
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Edit Goals');
      // Add task: ✕ to dismiss, Cancel then Create task, and Enter in the title creates it.
      await page.locator('#person-task-add').click();
      const dlg = page.locator('#person-task-modal');
      await dlg.waitFor();
      assert.equal(await dlg.locator('.tmodal-head .tmodal-x .nav-icon').innerText(), 'close');
      assert.deepEqual(await dlg.locator('.tmodal-foot button').allInnerTexts(), ['Cancel', 'Create task']);
      await shot(page, `person-task-${phone ? 390 : 1440}`);
      await dlg.locator('[name=message]').fill('The signed copy, please.');
      await dlg.locator('[name=title]').fill('Send the signed form');
      await dlg.locator('[name=title]').press('Enter');
      await page.waitForFunction(() => !(document.querySelector('#person-task-modal') as HTMLDialogElement | null)?.open);
      assert.ok(seen.posts.some(p => p.path === '/api/v2/tasks'), 'Enter submitted Create task');
      if (phone) for (const box of await page.locator('#pane-profile .bp-ico:visible').evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON())))
        assert.ok(box.height >= 44 && box.width >= 44, 'a 44px button on a phone: ' + JSON.stringify(box));
      assert.deepEqual(seen.errors, []);
      await context.close();
    }
    console.log('bot page guide: chat pick, ⋯ last with details and Archive, rail tabs behind ⋯, header tabs, notes as sections, dialogs, person page');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
