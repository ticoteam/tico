// Who wrote each line (ui/app/hub-v2.ts rowSource): rows the server wrote as the keeper are small gray lines, never the
// Assistant. In a bot's Home: two or more task updates in a row are one shut "N task updates" row that opens to a line
// each, and a redraw keeps the choice; a lone one is one gray line; the Assistant passing on a person's words reads as
// "Ana's Assistant" with the Assistant's face; a system line has no sender; a built-in leads with its bold name and an
// automation's name links to it. A task update arriving live joins the line before it, and an older page read in
// keeps the groups right. The same rule from the server's `source` and from the fallback; the Assistant's chat and a
// task's thread fold task updates the same way. At 390px nothing scrolls sideways. Fixtures only.
type Page = import('playwright').Page;
type Browser = import('playwright').Browser;
type Route = import('playwright').Route;
type Locator = import('playwright').Locator;
// The live view the thread renderer draws (its own type is not exported).
type SysLive = Parameters<typeof thvDraw>[0];
declare function uiPartLoad(part: string): Promise<void>;
declare var thvApplyMessage: (view: SysLive, event: unknown) => void;
declare var thvUpdateReveal: (view: SysLive, id: string) => void;
declare var rowSource: (m: {[key: string]: unknown; sender_id: string}, by?: unknown) => {kind: string; name: string};
declare var isSourceLine: (m: unknown) => boolean;
declare var actorLabel: (id: string) => string;
declare var taskBody: (task: {[key: string]: unknown}) => string;
declare var sourceRunsHTML: <T>(items: T[], opts: {isUpdate: (x: T) => boolean; line: (x: T) => string; draw: () => string}) => string;
declare var commentLineHTML: (x: unknown, i: number, all: unknown[]) => string;
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
// Page globals this spec reads (file-local; see ui/tests/tsconfig.json moduleDetection).
type ThvView = {id: string; messages: {id: string}[]; opts: {dm?: boolean}; gone?: boolean};
type ThvLive = ThvView & {thread: {me: {can_post: boolean}; [key: string]: unknown}};
declare var THV_VIEWS: ThvView[];
declare var thvDraw: (view: ThvLive) => void;
declare function v2MessagesHTML(messages: unknown[], goalLine?: unknown, slug?: string): string;

const now = Date.now(), at = (min: number) => new Date(now - min * 60000).toISOString();
const me = {member: true, following: true, notify: 'live', last_read_seq: 20, unread: 0, activity: false, can_post: true, can_manage: false};
const thread = {id: 't-home', title: 'Ops', audience: 'members', last_seq: 20, last_unread_seq: 0, last_message_at: at(1), me, members: []};
const msg = (id: string, seq: number, from: string, body: string, min: number, extra: Record<string, unknown> = {}) => ({id, seq, thread_id: 't-home', sender_id: from, recipient_id: 'bot:ops', body,
  created_at: at(min), reply_to: null, routed: null, kind: 'say', refs: {}, ...extra});
const update = (id: string, seq: number, body: string, min: number, extra: Record<string, unknown> = {}) => msg(id, seq, 'keeper', body, min, {kind: 'notice', refs: {task: 't-' + id}, ...extra});
const messages = () => [
  msg('a1', 1, 'human:ana', 'Keep the Acme launch on track.', 60),
  update('k1', 2, 'Open: Draft the launch post', 50),
  // The server's own label for the same kind of row.
  update('k2', 3, 'Doing: Draft the launch post', 49, {source: {kind: 'task_update', name: 'Task update', link: null}}),
  update('k3', 4, 'Done: Draft the launch post', 48),
  msg('b1', 5, 'bot:ops', 'The post is drafted.', 40, {recipient_id: 'human:ana'}),
  update('k4', 6, 'Blocked: Book the launch venue', 35),
  msg('r1', 7, 'keeper', 'Ana Lima asked the Assistant for this, in their words:\n\n> Add Ben to the launch channel', 30,
    {refs: {task: 't-r1', comment: true, quiet: true, carried: true}}),
  msg('s1', 8, 'keeper', 'Updated to v0.5.160.', 25, {kind: 'notice'}),
  msg('n1', 8.5, 'keeper', 'New task from keeper: Health: Watchers. ' + 'Two watchers stopped reporting and one restarted twice in the last hour. '.repeat(3), 24,
    {kind: 'notice', refs: {source: 'check-health'}}),
  msg('f1', 9, 'keeper', 'checked back on Book the launch venue', 20, {kind: 'notice', refs: {source: 'follow-up-timer'}}),
  msg('au1', 10, 'keeper', 'filed Weekly numbers', 15, {kind: 'notice', refs: {source: 'automation:au-1'},
    source: {kind: 'automation', name: 'Weekly digest', link: '#/automations/au-1'}}),
  msg('b2', 11, 'bot:ops', 'Anything else?', 10, {recipient_id: 'human:ana'}),
];
const KEEPER = ['k1', 'k2', 'k3', 'k4', 'r1', 's1', 'n1', 'f1', 'au1'];

async function open(browser: Browser, {phone = false}: {phone?: boolean} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Lima', role: 'owner', registered: true});
    if (p === '/api/employees') return json([
      {name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [], my_access: {see: true, read: true, write: true}},
      {name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [], my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Lima', email: 'ana@acme.example'}], org_groups: []});
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [thread], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [thread], dms: [], next_cursor: null});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    if (p === '/api/v2/threads/t-home/timeline') return json({thread, breadcrumb: [], next_cursor: null, messages: messages(), bot_status: [], read_marks: {'t-home': 20}});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false}
      : p.endsWith('members') ? {members: [], next_cursor: null} : {ok: true});
    if (/^\/api\/v2\/threads\/[^/]+\/bot-status$/.test(p)) return json({bots: []});
    return json(unrouted(p));
  });
  return {page, errors};
}
const view = (page: Page) => page.evaluateHandle(() => [...THV_VIEWS].find(v => v.id === 't-home') as SysLive);
const line = (page: Page, id: string) => page.locator(`.th-line[data-mid="${id}"]`);
const msgRow = (page: Page, id: string) => page.locator(`.th-msg[data-mid="${id}"]`);

let browser: Browser | null = null;
(async () => {
  browser = await chromium.launch();

  // ---- a bot's Home, desktop
  {
    const {page, errors} = await open(browser);
    await page.goto('https://tico-ui.test/#/thread/t-home');
    await msgRow(page, 'b2').waitFor();

    // Three task updates in a row: one shut row, server label or not.
    const group = page.locator('.thv-list details.src-group');
    assert.equal(await group.count(), 1, 'one group for the run of three');
    assert.match(await group.locator('> summary').innerText(), /^3 task updates/);
    assert.equal(await group.evaluate((el: HTMLDetailsElement) => el.open), false, 'shut by default');
    assert.equal(await group.locator('.th-line').count(), 3);
    // A lone task update is one gray line, outside any group.
    assert.equal(await line(page, 'k4').count(), 1);
    assert.equal(await line(page, 'k4').evaluate(el => !!el.closest('details')), false, 'a lone update is not folded');
    assert.match(await line(page, 'k4').innerText(), /Blocked: Book the launch venue/);
    // No keeper row is a message with a sender, except the Assistant's relay.
    for (const id of KEEPER.filter(id => id !== 'r1')) {
      assert.equal(await msgRow(page, id).count(), 0, id + ' is not a message row');
      assert.equal(await line(page, id).locator('.th-who, .th-av').count(), 0, id + ' has no sender row or avatar');
    }
    // The Assistant passing on a person's words: a normal message from "Ana's Assistant", with the Assistant's face.
    assert.equal(await msgRow(page, 'r1').locator('.th-who').innerText(), "Ana's Assistant");
    assert.equal(await msgRow(page, 'r1').locator('.th-av [data-av="assistant"]').count(), 1, "the Assistant's face");
    // A system line has no sender at all; a built-in leads with its name; an automation's name links to it.
    assert.equal(await line(page, 's1').locator('.src-name').count(), 0, 'a system line names no one');
    assert.equal(await line(page, 's1').innerText().then(t => t.trim().startsWith('Updated to v0.5.160.')), true);
    assert.equal(await line(page, 'f1').locator('b.src-name').innerText(), 'Follow-up timer');
    assert.match(await line(page, 'f1').getAttribute('title'), /^Follow-up timer · /, 'the hover says what ran and when');
    assert.equal(await line(page, 'au1').locator('a.src-name').getAttribute('href'), '#/automations/au-1');
    assert.equal(await line(page, 'au1').locator('a.src-name').innerText(), 'Weekly digest');
    // A task the server filed reads "New task:", never "from keeper"; a long line stays one line, cut with an ellipsis.
    assert.match(await line(page, 'n1').innerText(), /^Health check\s+New task: Health: Watchers\./);
    const tall = await page.locator('.thv-list .src-line').evaluateAll(els => els.filter(el => el.getBoundingClientRect().height > 30).map(el => el.textContent.slice(0, 40)));
    assert.deepEqual(tall, [], 'every gray line is one line');
    assert.match(await line(page, 'n1').getAttribute('title'), /\n\nNew task: Health: Watchers\./, 'the hover has the whole of a long line');
    // The group's row lines up with the lines around it.
    const pad = (el: Locator) => el.evaluate((e: HTMLElement) => getComputedStyle(e).paddingLeft);
    assert.equal(await pad(group.locator('> summary')), await pad(line(page, 'k4')));
    // Nothing the keeper wrote says "Assistant" but the relay.
    const lineText = await page.locator('.thv-list .src-line').allInnerTexts();
    assert.ok(lineText.every(t => !/Assistant|keeper/.test(t)), 'no gray line names the Assistant: ' + lineText.join(' | '));
    const whos = await page.locator('.thv-list .th-who').allInnerTexts();
    assert.deepEqual(whos.filter(w => /Assistant/.test(w)), ["Ana's Assistant"], 'only the relay names the Assistant');
    // A normal message after gray lines starts its own group: it keeps its header.
    assert.equal(await msgRow(page, 'b2').locator('.th-head').count(), 1);

    // Open the group: a line each; a redraw keeps it open.
    await group.locator('> summary').click();
    await page.waitForFunction(() => [...THV_VIEWS].some(v => ['k1', 'k2', 'k3'].every(id => (v as unknown as {openLines: Set<string>}).openLines.has(id))));
    assert.equal(await line(page, 'k1').isVisible(), true);
    await page.evaluate((v: SysLive) => thvDraw(v), await view(page));
    assert.equal(await page.locator('.thv-list details.src-group').evaluate((el: HTMLDetailsElement) => el.open), true, 'a redraw keeps it open');

    // A task update arriving live after the lone one: the two are now a shut group of two; the open group stays open.
    await page.evaluate((v: SysLive) => {
      thvApplyMessage(v, {id: 'k5', thread_id: 't-home', message: {id: 'k5', seq: 6.5, thread_id: 't-home', sender_id: 'keeper', kind: 'notice',
        body: 'Open: Book the launch venue', created_at: new Date().toISOString(), refs: {task: 't-k5'}}});
      thvDraw(v);
    }, await view(page));
    assert.equal(await page.locator('.thv-list details.src-group').count(), 2, 'the lone update and the new one are a group');
    assert.match(await page.locator('.thv-list details.src-group').nth(1).locator('> summary').innerText(), /^2 task updates/);
    assert.equal(await page.locator('.thv-list details.src-group').nth(0).evaluate((el: HTMLDetailsElement) => el.open), true);
    assert.equal(await page.locator('.thv-list details.src-group').nth(1).evaluate((el: HTMLDetailsElement) => el.open), false);

    // An older page read in: a task update before the open group joins it at the top, and it stays open.
    await page.evaluate((v: SysLive) => {
      v.messages.splice(1, 0, {id: 'k0', seq: 1.5, thread_id: 't-home', sender_id: 'keeper', kind: 'notice', body: 'New task: Draft the launch post',
        created_at: new Date(Date.now() - 55 * 60000).toISOString(), refs: {task: 't-k0'}} as {id: string});
      v.messages.unshift({id: 'k00', seq: 0.5, thread_id: 't-home', sender_id: 'keeper', kind: 'notice', body: 'Open: Plan the launch',
        created_at: new Date(Date.now() - 70 * 60000).toISOString(), refs: {task: 't-k00'}} as {id: string});
      thvDraw(v);
    }, await view(page));
    assert.match(await page.locator('.thv-list details.src-group').nth(0).locator('> summary').innerText(), /^4 task updates/);
    assert.equal(await page.locator('.thv-list details.src-group').nth(0).evaluate((el: HTMLDetailsElement) => el.open), true, 'still open');
    assert.equal(await line(page, 'k00').evaluate(el => !!el.closest('details')), false, 'a person\'s message breaks the run');

    // Focusing a message inside a shut group opens it.
    await page.evaluate((v: SysLive) => { thvUpdateReveal(v, 'k5'); thvDraw(v); }, await view(page));
    assert.equal(await page.locator('.thv-list details.src-group').nth(1).evaluate((el: HTMLDetailsElement) => el.open), true);

    // The fallback rule, from sender and refs, without the server's `source`.
    const kinds = await page.evaluate(() => [
      {sender_id: 'keeper', refs: {task: 't'}}, {sender_id: 'keeper', refs: {}}, {sender_id: 'keeper', refs: {source: 'system:update'}},
      {sender_id: 'keeper', refs: {source: 'task-roundup'}}, {sender_id: 'keeper', refs: {source: 'automation:x'}},
      {sender_id: 'human:ana', refs: {}}, {sender_id: 'bot:ops', refs: {}}, {sender_id: 'bot:assistant', refs: {for: 'human:ana'}},
    ].map(m => [rowSource(m).kind, rowSource(m).name]));
    assert.deepEqual(kinds, [['task_update', 'Task update'], ['system', ''], ['system', ''], ['builtin', 'Task roundup'], ['automation', 'Automation'],
      ['person', 'You'], ['bot', 'Ops'], ['assistant_for', "Ana's Assistant"]]);
    // A keeper row that asks something keeps its own row: its card is never folded away.
    assert.equal(await page.evaluate(() => isSourceLine({sender_id: 'keeper', kind: 'ask', refs: {task: 't', questions: [{question: 'Which?'}]}})), false);
    assert.equal(await page.evaluate(() => actorLabel('keeper')), 'Automatic', 'the keeper is never the Assistant or Tico');
    assert.ok(lineText.every(t => !/\bTico\b/.test(t)), 'no gray line says Tico');
    assert.match(await page.evaluate(() => taskBody({requester_id: 'keeper', owner_id: 'bot:ops', title: 'Weekly',
      body: 'Run playbook Weekly.\n\n_Created by the keeper from schedule `weekly`._'})), /_Created from schedule `weekly`\._$/);

    // The Assistant's chat folds a run of task updates the same way, and names no Assistant for them.
    const chat = await page.evaluate(() => {
      const el = document.createElement('div');
      const u = (id: string, n: number) => ({id, sender_id: 'keeper', recipient_id: 'bot:ops', kind: 'notice', body: 'Done: step ' + n, refs: {task: 't'}, created_at: new Date(Date.now() - (10 - n) * 60000).toISOString()});
      el.innerHTML = v2MessagesHTML([{id: 'h', sender_id: 'human:ana', recipient_id: 'bot:ops', kind: 'say', body: 'Go', created_at: new Date(Date.now() - 20 * 60000).toISOString()},
        u('u1', 1), u('u2', 2), u('u3', 3), {id: 'z', sender_id: 'bot:ops', recipient_id: 'human:ana', kind: 'say', body: 'Done', in_reply_to: 'h', created_at: new Date().toISOString()}], null, 'ops');
      return {summary: el.querySelector('details.chat-side > summary b')?.textContent, text: el.textContent};
    });
    assert.equal(chat.summary, '3 task updates');
    assert.ok(!/Assistant/.test(chat.text), 'the chat names no Assistant for them');

    // A task's thread: two task updates in a row are one shut group; the event the server made has no sender.
    // The task window's code is a page part (ui/parts.json), loaded as opening a task would.
    await page.evaluate(() => uiPartLoad('task'));
    const task = await page.evaluate(() => {
      const c = (id: string, n: number) => ({kind: 'comment', ts: new Date(Date.now() - (10 - n) * 60000).toISOString(),
        message: {id, sender_id: 'keeper', kind: 'notice', body: 'Doing: step ' + n, refs: {task: 't'}, created_at: new Date(Date.now() - (10 - n) * 60000).toISOString()}});
      const items = [c('c1', 1), c('c2', 2)];
      const el = document.createElement('div');
      el.innerHTML = sourceRunsHTML(items, {isUpdate: x => rowSource(x.message).kind === 'task_update', line: x => commentLineHTML(x, 0, items), draw: () => ''})
        + commentLineHTML({kind: 'event', field: 'status', old: 'open', new: 'doing', actor: 'keeper', ts: new Date().toISOString()}, 0, []);
      return {groups: el.querySelectorAll('details.src-group').length, lines: el.querySelectorAll('details .tline').length,
        event: el.querySelector('.tmark')?.textContent.replace(/\s+/g, ' ').trim(), who: el.querySelectorAll('.tmark .tcomment-who').length};
    });
    assert.deepEqual([task.groups, task.lines, task.who], [1, 2, 0]);
    assert.match(task.event, /^Moved it to /, 'the server\'s change reads as a sentence, no sender');

    assert.deepEqual(errors, []);
    await page.close();
  }

  // ---- a phone at 390px: nothing scrolls sideways, the group's row has a finger's height
  {
    const {page, errors} = await open(browser, {phone: true});
    await page.goto('https://tico-ui.test/#/thread/t-home');
    await msgRow(page, 'b2').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll');
    const summary = page.locator('.thv-list details.src-group > summary');
    assert.ok((await summary.boundingBox())!.height >= 44, "the group's row is a finger's height");
    await summary.tap();
    await line(page, 'k1').waitFor({state: 'visible'});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'no sideways scroll with it open');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('system lines: ok');
})().then(() => browser?.close(), async e => { console.error(e); await browser?.close(); process.exit(1); });
