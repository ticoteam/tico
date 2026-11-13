// 👍 / 👎 on a bot's work (ui/app/feedback.ts), against a fake of the feedback routes (backend/feedback.py). Fixtures only.
//  - an older server (404 on the batched read) shows no control at all, and nothing asks for reasons;
//  - one batched read per subject type for what is drawn, never one per message, and never a person's own message;
//  - 👍 saves in one tap and a second tap removes it; 👎 saves, then its sheet offers recent reasons as chips, says
//    "You've said this before." for a reason used twice (Make a rule becomes the main button), tells the bot, or drafts a
//    rule to edit, widen to All bots and save; a 👎 mark shows the reason and what came of it, and removes it;
//  - on a phone a long press opens 👍, 👎 and Copy, a tap still opens the copy line, and a press that moves is a scroll;
//  - a task a bot asked for has the pair in its window's header; a person's request has none;
//  - a thread message keeps its emoji reactions apart from the thumbs, and copies its own words.
// FEEDBACK_SHOTS=<dir> saves phone and desktop screenshots of each state.
import type {Browser, Locator, Page} from 'playwright';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {assistantView} = require('./support/assistant-chat.cts') as import('./support/assistant-chat.cts').Api;
const {threadsFake} = require('./support/threads-fake.cts') as import('./support/threads-fake.cts').Api;
const {open, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

interface Row {id: string; subject_type: string; subject_id: string; rating?: string; reason?: string; [key: string]: unknown}
interface Write {method: string; p: string; body: {subject_type?: string; [key: string]: unknown} | null}
interface Fake {rows: Row[]; reads: (string | {type: string | null; ids: string[]})[]; writes: Write[]}
interface PageWin {copied?: string}
declare const FB: {on: boolean | null};
declare const V2C: {rendered: boolean; loaded: boolean};
const SHOTS = process.env.FEEDBACK_SHOTS || '';
const iso = () => new Date().toISOString();
const shot = async (page: Page, name: string) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);          // the sheet's fade
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
};

// The routes, kept in memory: one row per person and subject, as the server's upsert keeps it.
function feedbackFake(page: Page, {off = false, seed = []}: {off?: boolean; seed?: Row[]} = {}): Promise<Fake> {
  const rows: Row[] = [...seed], reads: Fake['reads'] = [], writes: Write[] = [];
  let n = 0;
  const reasons = [{text: 'Too long', count: 2}, {text: 'Wrong numbers', count: 1}];
  return page.route('**/api/v2/feedback**', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (off) { reads.push(p); return json({detail: 'Not Found'}, 404); }
    if (p === '/api/v2/feedback' && method === 'GET') {
      const type = url.searchParams.get('subject_type'), ids = url.searchParams.get('subject_ids')!.split(',');
      reads.push({type, ids});
      return json({items: rows.filter(r => r.subject_type === type && ids.includes(r.subject_id))});
    }
    if (p === '/api/v2/feedback/recent-reasons') { reads.push('reasons'); return json({reasons}); }
    const body = method === 'GET' || method === 'DELETE' ? null : req.postDataJSON();
    writes.push({method, p, body});
    if (p === '/api/v2/feedback' && method === 'POST') {
      let row = rows.find(r => r.subject_type === body.subject_type && r.subject_id === body.subject_id);
      if (!row) rows.push(row = {id: 'fb-' + ++n, person_id: 'human:ana', subject_type: body.subject_type, subject_id: body.subject_id,
        bot_id: 'bot:assistant', outcome: 'none', note_id: null, note_status: null, org_rule_id: null, rule_status: null, created_at: iso()});
      Object.assign(row, {rating: body.rating, reason: body.reason ?? row.reason ?? '', updated_at: iso()});
      if (body.tell_bot) Object.assign(row, {outcome: 'told_bot', note_id: 'n-' + n, note_status: 'waiting'});
      return json(row);
    }
    const one = p.match(/^\/api\/v2\/feedback\/([^/]+)(?:\/(rule-draft|rule))?$/);
    const row = one && rows.find(r => r.id === one[1]);
    if (!row) return json({detail: 'Not found'}, 404);
    // The real route answers JSON (backend/feedback.py remove()), never an empty 204.
    if (method === 'DELETE') { rows.splice(rows.indexOf(row), 1); return json({ok: true, id: row.id}); }
    if (one![2] === 'rule-draft') return json({text: 'Keep replies to five lines or fewer.'});
    if (one![2] === 'rule') {
      Object.assign(row, {outcome: 'rule', org_rule_id: 'r-1', rule_status: 'active'});
      return json({feedback: row, rule: {id: 'r-1', status: 'active', text: body.text}});
    }
    return json({detail: 'Not found'}, 404);
  }).then(() => ({rows, reads, writes}));
}

const BOTS = [{name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}];
async function chatPage(browser: Browser, {phone = false, theme = 'dark', off = false, hash = '#/assistant', seed = []}: {phone?: boolean; theme?: string; off?: boolean; hash?: string; seed?: Row[]} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1200, height: 800}, hasTouch: phone, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(theme => { try { localStorage.setItem('tico.theme', theme); } catch {} }, theme);
  const snapshot = {execution: null, messages: [
    {id: 'm1', sender_id: 'human:ana', body: 'How did the launch week go?', created_at: iso()},
    {id: 'm2', sender_id: 'bot:assistant', body: 'Signups rose 18% on the week. The pricing page drove most of it; the webinar drove almost none, so I would drop it next month.', created_at: iso()},
    {id: 'm3', sender_id: 'bot:assistant', body: 'Revenue was $41,200, up from $38,900.', created_at: iso()}]};
  await page.context().route('**/*', route => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/assistant') return json(assistantView('c-coo', snapshot));
    if (p === '/api/v2/conversations/c-coo/snapshot') return json(snapshot);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  const fake = await feedbackFake(page, {off, seed});
  await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async (t: string) => { (window as unknown as PageWin).copied = t; }}}); });
  await page.goto('https://tico-ui.test/' + hash);
  if (hash === '#/assistant') await page.waitForFunction(() => V2C?.rendered && V2C.loaded);
  return {page, errors, ...fake};
}
const posts = (writes: Write[], type?: string) => writes.filter(w => w.method === 'POST' && w.p === '/api/v2/feedback' && (!type || w.body!.subject_type === type)).map(w => w.body);

async function olderServer(browser: Browser) {
  const {page, errors, reads} = await chatPage(browser, {off: true});
  await page.waitForFunction(() => FB.on === false);
  await page.locator('.bubble.bot').first().hover();
  assert.equal(await page.locator('.fb-ctl:visible, .fb-mark:visible, .fb-row:visible').count(), 0, 'no thumbs on a server without feedback');
  assert.equal(await page.getByRole('button', {name: 'Copy message'}).first().isVisible(), true, 'Copy is as it was');
  assert.ok(!reads.includes('reasons'), 'nothing asks for reasons');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('feedback older server: ok');
}

async function chatDesktop(browser: Browser, theme = 'dark') {
  const {page, errors, reads, writes, rows} = await chatPage(browser, {theme});
  const t = theme === 'dark' ? '' : '-' + theme;
  await page.waitForFunction(() => document.body.classList.contains('fb-on'));
  // One read for the drawn bot messages; the person's own message is never rated.
  assert.deepEqual(reads.filter(r => (r as {type?: string}).type), [{type: 'message', ids: ['m2', 'm3']}]);
  assert.equal(await page.locator('.bubble.you [data-fb]').count(), 0);
  const reply = page.locator('.bubble.bot').first(), up = reply.getByRole('button', {name: 'Good', exact: true}), down = reply.getByRole('button', {name: 'Not good', exact: true});
  await reply.hover();
  assert.equal(await up.isVisible(), true);
  // One row under the reply, in order Copy, 👍, 👎; its room is kept, so hovering moves nothing.
  const row = await reply.evaluate(b => { const md = (b as HTMLElement).querySelector('.md')!.getBoundingClientRect(), a = (b as HTMLElement).querySelector('.chat-message-actions')!;
    return {under: a.getBoundingClientRect().top >= md.bottom - 1, labels: [...a.querySelectorAll('button')].map(x => x.getAttribute('aria-label')), h: (b as HTMLElement).offsetHeight}; });
  assert.equal(row.under, true, 'the row sits under the reply text');
  // A reply nobody rated keeps its height: the row floats below its words.
  assert.equal(await reply.evaluate(b => getComputedStyle((b as HTMLElement).querySelector('.chat-message-actions')!).position), 'absolute');
  assert.ok(await reply.evaluate(b => (b as HTMLElement).offsetHeight - (b as HTMLElement).querySelector<HTMLElement>('.md')!.offsetHeight) < 4, 'no room kept under an unrated reply');
  assert.deepEqual(row.labels, ['Copy message', 'Good', 'Not good']);
  await page.mouse.move(0, 0);
  assert.equal(await reply.evaluate(b => (b as HTMLElement).offsetHeight), row.h, 'no jump when the hover ends');
  await page.waitForFunction(b => getComputedStyle(b).opacity === '0', await up.elementHandle());     // the row rests hidden, after its fade
  await reply.hover();
  await shot(page, 'chat-desktop-hover' + t);
  // 👍: one tap saves; again removes it. Neither redraws into an answer or toggles anything else.
  await up.click();
  await page.waitForFunction(() => document.querySelector('[data-fb="message:m2"] [data-fb-rate="up"]')?.getAttribute('aria-pressed') === 'true');
  await page.mouse.move(0, 0);
  await page.waitForTimeout(300);
  assert.equal(await up.evaluate(b => getComputedStyle(b).opacity), '1', 'the chosen thumb stays when the hover ends');
  assert.equal(await reply.evaluate(b => getComputedStyle((b as HTMLElement).querySelector('.chat-message-actions')!).position), 'static', 'a rated reply keeps its row');
  assert.equal(await up.locator('.fb-i').evaluate(i => getComputedStyle(i).fontVariationSettings.includes('"FILL" 1')), true, 'filled');
  await shot(page, 'chat-desktop-rated' + t);
  await reply.hover();
  assert.equal(await up.getAttribute('aria-pressed'), 'true');
  assert.deepEqual(posts(writes), [{subject_type: 'message', subject_id: 'm2', rating: 'up'}]);
  await up.click();
  await page.waitForFunction(() => !document.querySelector('[data-fb="message:m2"] [aria-pressed="true"]'));
  assert.equal(writes.at(-1)!.method, 'DELETE');
  assert.equal(rows.length, 0);
  // 👎: saved at once, then the sheet. Chips are the person's recent reasons; a message has no task chips.
  await reply.hover();
  await down.click();
  const sheet = page.locator('dialog.fb-down[open]');
  await sheet.waitFor();
  assert.equal(rows[0]?.rating, 'down', '👎 is saved before any reason');
  await page.waitForFunction(() => document.querySelectorAll('dialog.fb-down .fb-chip').length === 2);
  assert.deepEqual(await sheet.locator('.fb-chip').allInnerTexts(), ['Too long', 'Wrong numbers']);
  assert.equal(await sheet.locator('.fb-nudge').isVisible(), false);
  // A reason said twice before: one line, and Make a rule is the main button.
  await sheet.getByRole('button', {name: 'Too long'}).click();
  assert.equal(await sheet.getByRole('textbox', {name: 'What was wrong?'}).inputValue(), 'Too long');
  assert.equal(await sheet.locator('.fb-nudge').innerText(), "You've said this before.");
  assert.equal(await sheet.getByRole('button', {name: 'Make a rule'}).getAttribute('class'), 'primary');
  assert.equal(await sheet.getByRole('button', {name: 'Tell the bot'}).getAttribute('class'), 'ghost');
  await shot(page, 'chat-desktop-down-sheet' + t);
  // The chip fills the box, which stays editable; a reason used once is no nudge.
  await sheet.getByRole('button', {name: 'Wrong numbers'}).click();
  assert.equal(await sheet.locator('.fb-nudge').isVisible(), false);
  await sheet.getByRole('textbox').fill('Too long');
  assert.equal(await sheet.locator('.fb-nudge').isVisible(), true, 'typing a repeated reason nudges too');
  // Make a rule: the reason is saved first, then drafted; the sentence is editable and can cover every bot.
  await sheet.getByRole('button', {name: 'Make a rule'}).click();
  const rule = page.locator('dialog.fb-rule[open]');
  await rule.waitFor();
  assert.equal(rows[0]!.reason, 'Too long');
  assert.deepEqual(writes.slice(-2).map(w => w.p), ['/api/v2/feedback', `/api/v2/feedback/${rows[0]!.id}/rule-draft`]);
  const text = rule.getByRole('textbox', {name: 'Rule'});
  assert.equal(await text.getAttribute('maxlength'), '1000', "the API's cap");
  assert.equal(await text.inputValue(), 'Keep replies to five lines or fewer.');
  await text.fill('Keep replies to five lines.');
  await rule.getByRole('switch', {name: 'All bots'}).check();
  await shot(page, 'chat-desktop-rule' + t);
  await rule.getByRole('button', {name: 'Save rule'}).click();
  await page.locator('.toast', {hasText: 'Rule added'}).waitFor();
  assert.deepEqual(writes.at(-1)!.body, {text: 'Keep replies to five lines.', all_bots: true});
  assert.equal(await page.locator('dialog.fb-sheet[open]').count(), 0);
  // Its chosen 👎: the reason, a link to the rule, and Remove.
  await reply.hover();
  await down.click();
  const show = page.locator('dialog.fb-show[open]');
  await show.waitFor();
  assert.equal(await show.locator('.fb-reason').innerText(), 'Too long');
  assert.match((await show.getByRole('link', {name: 'Rule'}).getAttribute('href'))!, /^#\/rules\?.*rule=r-1/);
  await show.getByRole('button', {name: 'Remove'}).click();
  await page.waitForFunction(() => !document.querySelector('dialog.fb-show[open]') && !document.querySelector('[data-fb="message:m2"] [aria-pressed="true"]'));
  // Tell the bot: the reason as typed goes with tell_bot; the mark then says it was sent.
  const second = page.locator('.bubble.bot').nth(1);
  await second.hover();
  await second.getByRole('button', {name: 'Not good', exact: true}).click();
  await sheet.waitFor();
  await sheet.getByRole('textbox').fill('The totals are wrong');
  await sheet.getByRole('button', {name: 'Tell the bot'}).click();
  await page.locator('.toast', {hasText: 'Sent to bot'}).waitFor();
  assert.deepEqual(posts(writes).at(-1), {subject_type: 'message', subject_id: 'm3', rating: 'down', reason: 'The totals are wrong', tell_bot: true});
  await second.hover();
  await second.getByRole('button', {name: 'Not good', exact: true}).click();
  assert.equal(await show.locator('.fb-out').innerText(), 'Sent to bot');
  await page.keyboard.press('Escape');
  // Both rated replies at rest: each keeps only its chosen thumb, filled, on its row.
  await page.mouse.move(0, 0);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());      // the resting look, not the keyboard's ring
  await shot(page, 'chat-desktop-rest' + t);
  assert.deepEqual(errors, []);
  await page.close();
  console.log(`feedback chat desktop (${theme}): ok`);
}

// A long press through the browser's own touch input: pointer events with pointerType touch.
async function press(page: Page, locator: Locator, {ms = 650, move = 0}: {ms?: number; move?: number} = {}) {
  const box = (await locator.boundingBox())!, x = box.x + box.width / 2, y = box.y + Math.min(box.height / 2, 30);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x, y}]});
  if (move) await cdp.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{x, y: y + move}]});
  await page.waitForTimeout(ms);
  await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
  await cdp.detach();
}
async function chatPhone(browser: Browser, theme = 'dark') {
  const {page, errors, writes} = await chatPage(browser, {phone: true, theme});
  const t = theme === 'dark' ? '' : '-' + theme;
  await page.waitForFunction(() => document.body.classList.contains('fb-on'));
  const reply = page.locator('.bubble.bot').first();
  // A tap shows the copy line, now with the thumbs; no menu.
  await reply.tap();
  assert.equal(await reply.evaluate(b => b.classList.contains('acts')), true);
  assert.equal(await reply.getByRole('button', {name: 'Good', exact: true}).isVisible(), true);
  assert.equal(await page.locator('dialog.fb-menu[open]').count(), 0);
  await shot(page, 'chat-phone-tap' + t);
  await reply.tap();
  // A press that moves is a scroll: nothing opens.
  await press(page, reply, {move: 40});
  assert.equal(await page.locator('dialog.fb-menu[open]').count(), 0, 'a moving press opens nothing');
  // A long press opens 👍, 👎 and Copy; its own lift does not also tap the message.
  await press(page, reply);
  const menu = page.locator('dialog.fb-menu[open]');
  await menu.waitFor();
  assert.deepEqual(await menu.locator('button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label'))), ['Good', 'Not good', 'Copy']);
  const fits = await menu.evaluate(d => { const r = d.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && Math.round(r.bottom) >= innerHeight - 1; });
  assert.equal(fits, true, 'a bottom sheet across the phone');
  await shot(page, 'chat-phone-longpress' + t);
  await menu.getByRole('button', {name: 'Copy'}).tap();
  await page.waitForFunction(() => (window as unknown as PageWin).copied?.startsWith('Signups rose 18%'));
  await press(page, reply);
  await menu.getByRole('button', {name: 'Not good', exact: true}).tap();
  const sheet = page.locator('dialog.fb-down[open]');
  await sheet.waitFor();
  await page.waitForFunction(() => document.querySelectorAll('dialog.fb-down .fb-chip').length === 2);
  await sheet.getByRole('button', {name: 'Too long'}).tap();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
  const gutter = await sheet.locator('.fb-chips').evaluate(el => Math.round(el.getBoundingClientRect().left));
  assert.equal(gutter, 16, '16px gutter');
  await shot(page, 'chat-phone-down-sheet' + t);
  await sheet.getByRole('button', {name: 'Make a rule'}).tap();
  await page.locator('dialog.fb-rule[open]').waitFor();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await shot(page, 'chat-phone-rule' + t);
  assert.equal(posts(writes).length, 2, 'the 👎, then its reason');
  // At rest a phone shows only the chosen thumb, filled, where the row puts it.
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('dialog.fb-sheet[open]'));
  const rest = await reply.evaluate(b => { const a = (b as HTMLElement).querySelector('.chat-message-actions')!, v = (el: Element) => getComputedStyle(el).visibility;
    return {acts: b.classList.contains('acts'), shown: getComputedStyle(a).display !== 'none', down: v(a.querySelector('[data-fb-rate="down"]')!),
      up: v(a.querySelector('[data-fb-rate="up"]')!), copy: v(a.querySelector('.chat-message-copy')!)}; });
  assert.deepEqual(rest, {acts: false, shown: true, down: 'visible', up: 'hidden', copy: 'hidden'});
  await shot(page, 'chat-phone-rated' + t);
  assert.deepEqual(errors, []);
  await page.close();
  console.log(`feedback chat phone (${theme}): ok`);
}

// A task a bot asked for: the task window's header carries 👍 / 👎, and a tap saves without leaving the window; a
// person's request has none.
async function taskWindow(browser: Browser) {
  const tasks = [task('t-bot', 'Draft the renewal emails', 'todo', {owner_id: 'human:ana', requester_id: 'bot:writer'}),
    task('t-person', 'Book the venue', 'todo', {owner_id: 'human:ana', requester_id: 'human:sam'})];
  let feedback!: Fake;
  const {page, errors} = await open(browser, {hash: '#/task/t-person', tasks, needs: [], beforeGoto: async page => { feedback = await feedbackFake(page); }});
  assert.equal(await page.locator('#task-modal .tmodal-head [data-fb]').count(), 0, "a person's request has no pair");
  await page.evaluate(() => { location.hash = '#/task/t-bot'; });
  const head = page.locator('#task-modal[open] .tmodal-head [data-fb="task:t-bot"]');
  await head.waitFor();
  await page.waitForFunction(() => document.body.classList.contains('fb-on'));
  assert.deepEqual(feedback.reads.filter(r => (r as {type?: string}).type).map(r => JSON.stringify(r)), [JSON.stringify({type: 'task', ids: ['t-bot']})],
    'one read, for the task a bot asked for');
  await head.getByRole('button', {name: 'Good', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#task-modal [data-fb="task:t-bot"] [data-fb-rate="up"]')?.getAttribute('aria-pressed') === 'true');
  assert.deepEqual(posts(feedback.writes), [{subject_type: 'task', subject_id: 't-bot', rating: 'up'}]);
  assert.equal(await page.locator('#task-modal[open]').count(), 1, 'the window stays open');
  await shot(page, 'task-window');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('feedback task window: ok');
}

async function threadMessage(browser: Browser) {
  const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
  const errors: string[] = [], reacts: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const bot = {name: 'finance', display_name: 'Finance', host: 'keeper', status: 'active', can_chat: true, schedules: []};
  const threads = threadsFake({bot: 'finance', messages: [
    {id: 'h1', sender_id: 'human:ana', body: 'Is the October close done?'},
    {id: 'b1', sender_id: 'bot:finance', body: 'Closed. Two invoices are still unpaid.', reactions: [{emoji: '🎉', count: 1, actors: ['human:sam']}]}]});
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([bot]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p.includes('/reactions/')) { reacts.push(p); return json({reactions: []}); }
    const answer = threads(p, url, route.request().method());
    if (answer !== undefined) return json(answer);
    return json(unrouted(p));
  });
  const {writes} = await feedbackFake(page);
  await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async (t: string) => { (window as unknown as PageWin).copied = t; }}}); });
  await page.goto('https://tico-ui.test/#/bot/finance/dm');
  const msg = page.locator('.th-msg[data-mid="b1"]');
  await msg.waitFor();
  await page.waitForFunction(() => document.body.classList.contains('fb-on'));
  assert.equal(await page.locator('.th-msg[data-mid="h1"] [data-fb]').count(), 0);
  await msg.hover();
  assert.deepEqual(await msg.locator('.th-acts button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label'))), ['React', 'Reply', 'Copy'], 'the hover bar');
  assert.deepEqual(await msg.locator('.fb-row button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label'))), ['Copy message', 'Good', 'Not good']);
  const under = await msg.evaluate(m => (m as HTMLElement).querySelector('.fb-row')!.getBoundingClientRect().top >= (m as HTMLElement).querySelector('.th-react')!.getBoundingClientRect().bottom - 1);
  assert.equal(under, true, 'the row sits under the words and reactions');
  await shot(page, 'thread-hover');
  await msg.getByRole('button', {name: 'Good', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('[data-fb="message:b1"] [data-fb-rate="up"]')?.getAttribute('aria-pressed') === 'true');
  assert.deepEqual(reacts, [], 'a thumb is not an emoji reaction');
  assert.match(await msg.locator('.th-react').innerText(), /^🎉\s+1$/, 'reactions stay as they were');
  assert.deepEqual(posts(writes), [{subject_type: 'message', subject_id: 'b1', rating: 'up'}]);
  await msg.getByRole('button', {name: 'Copy message'}).click();
  await page.waitForFunction(() => (window as unknown as PageWin).copied === 'Closed. Two invoices are still unpaid.');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('feedback thread message: ok');
}

// Ask, the floating Assistant panel on any page, reads its replies' feedback itself: a page with nothing to rate never
// turned feedback on, and a reply rated earlier shows its chosen thumb.
async function askPanel(browser: Browser) {
  const seed = [{id: 'fb-0', person_id: 'human:ana', subject_type: 'message', subject_id: 'm3', rating: 'up', reason: '', bot_id: 'bot:assistant',
    outcome: 'none', note_id: null, note_status: null, org_rule_id: null, rule_status: null, created_at: iso(), updated_at: iso()}];
  const {page, errors, reads, writes} = await chatPage(browser, {hash: '#/automations', seed});
  await page.locator('#ask-row').waitFor();
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => FB.on), null, 'nothing on the page was read');
  await page.locator('#ask-row').click();
  const reply = page.locator('#ask-panel .bubble.bot').nth(1);
  await reply.waitFor();
  await page.waitForFunction(() => document.body.classList.contains('fb-on'));
  assert.deepEqual(reads.filter(r => (r as {type?: string}).type), [{type: 'message', ids: ['m2', 'm3']}]);
  await page.waitForFunction(() => document.querySelector('#ask-panel [data-fb="message:m3"] [data-fb-rate="up"]')?.getAttribute('aria-pressed') === 'true');
  const first = page.locator('#ask-panel .bubble.bot').first();
  await first.hover();
  await first.getByRole('button', {name: 'Good', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#ask-panel [data-fb="message:m2"] [data-fb-rate="up"]')?.getAttribute('aria-pressed') === 'true');
  assert.deepEqual(posts(writes), [{subject_type: 'message', subject_id: 'm2', rating: 'up'}]);
  await shot(page, 'ask-panel');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('feedback ask panel: ok');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    await olderServer(browser);
    await chatDesktop(browser);
    await chatDesktop(browser, 'light');
    await chatPhone(browser);
    await chatPhone(browser, 'light');
    await taskWindow(browser);
    await threadMessage(browser);
    await askPanel(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
