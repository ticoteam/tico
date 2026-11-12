// The Ask bar on a pipeline's board, on a phone (ui/app/pipeline-ask.ts; spec "Talk to a pipeline's bots from the
// board", v4), offline on the fake server of tasks-page.cts. Checks:
//  - at 390 px, on any stage tab, the bar sits above the bottom bar, the manager is picked and the box reads
//    "Ask PM…"; the context line reads "Product › Spec · N tasks on screen", N the rows the tab shows;
//  - the second button is the open stage's holder ("Spec · Writer"); tapped, the message goes to that bot;
//  - a message carries refs.board (the pipeline, the stage, the ids of the tasks on screen, search applied) to the bot's
//    direct chat; the chat opens over the board as the chat alone (no Log, no Tasks), the message shows one card;
//  - ✕ puts the chat away: the same tab, the same scroll;
//  - a stage held by "whoever the task is for" has no second button, and one held by the manager shows the manager once;
//    no button has an empty name;
//  - a card opens its task at its conversation, the box reading "Ask PM about this task…";
//  - at 320 px nothing overflows sideways and every tap target in the bar is at least 44 × 44;
//  - a computer, and a pipeline with no manager and no holder, have no bar.
// Run: node scripts/ui-tests.cts pipeline-ask
import type {Page, Route} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, PIPELINES, fixtures, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

interface Sent { path: string; body: {text?: string; members?: string[]; refs?: {board?: {pipeline_id: string; pipeline: string; stage_id: string; stage: string; task_ids: string[]; more: number}}} }

/** A fake for the direct chats: none yet; POST /v2/threads/dm makes one per bot, and its messages are kept. */
function chats(sent: Sent[]) {
  const made = new Map<string, {id: string; with: string; messages: Record<string, unknown>[]}>();
  return async (pg: Page) => pg.route(u => u.pathname.startsWith('/api/v2/threads/'), async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const thread = (t: {id: string; with: string}) => ({id: t.id, kind: 'dm', title: '', members: ['human:ana', t.with], me: {can_post: true, last_read_seq: 0}});
    if (p === '/api/v2/threads/dm' && method === 'GET') {
      const t = [...made.values()].find(x => x.with === url.searchParams.get('with'));
      return t ? json(thread(t)) : json({error: 'not_found', detail: 'Not found'}, 404);
    }
    if (p === '/api/v2/threads/dm' && method === 'POST') {
      const body = req.postDataJSON() as Sent['body'];
      sent.push({path: p, body});
      const who = body.members![0]!;
      const t = [...made.values()].find(x => x.with === who) ?? {id: 'dm-' + (made.size + 1), with: who, messages: []};
      made.set(t.id, t);
      return json(thread(t));
    }
    const at = p.match(/^\/api\/v2\/threads\/([^/]+)\/(timeline|messages)$/), t = at && made.get(at[1]!);
    if (t && at![2] === 'timeline') return json({thread: thread(t), messages: t.messages, next_cursor: null});
    if (t && at![2] === 'messages' && method === 'POST') {
      const body = req.postDataJSON() as Sent['body'];
      sent.push({path: p, body});
      const m = {id: 'm-' + (t.messages.length + 1), thread_id: t.id, root_id: t.id, seq: t.messages.length + 1, sender_id: 'human:ana', kind: 'say',
        body: body.text, refs: body.refs ?? {}, created_at: new Date().toISOString()};
      t.messages.push(m);
      return json(m);
    }
    return route.fallback();
  });
}

const bar = (page: Page) => page.locator('#task-ask .pa-bar');
const buttons = (page: Page) => page.locator('#task-ask .pa-to-b').evaluateAll(els => els.map(e => ({
  text: (e.textContent || '').trim(), on: e.getAttribute('aria-checked') === 'true'})));
const placeholder = (page: Page) => page.locator('#task-ask .pa-text').getAttribute('placeholder');
const rect = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, w: r.width, h: r.height};
});

(async () => {
  const product = PIPELINES.find(p => p.id === 'product')!;
  (product as {manager_id: string | null}).manager_id = 'bot:pm';
  const holders: Record<string, string> = {spec: 'bot:writer', build: 'assignee', review: 'bot:pm'};
  for (const s of product.stages) (s as {owner: string | null}).owner = holders[s.id] ?? null;
  // A third task in Spec, so the tab shows three.
  const tasks = () => [...fixtures(), task('t-8', 'Pricing page copy', 'spec', {owner_id: 'bot:writer'})];
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const sent: Sent[] = [];
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, tasks: tasks(), needs: [],
        hash: '#/tasks?pipeline=product', beforeGoto: chats(sent)});
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await bar(page).waitFor();

      // Above the bottom bar, the manager picked, the box asking it.
      const ask = await rect(page, '#task-ask'), nav = await rect(page, '#mobile-nav');
      assert.ok(ask.bottom <= nav.top + 1 && ask.h < 140, `the bar sits above the bottom bar: ${JSON.stringify({ask, nav})}`);
      assert.equal(await placeholder(page), 'Ask PM…');
      const rows = await page.locator('#task-body .pv-phone [data-task]').count();
      assert.equal(rows, 3);
      assert.equal((await page.locator('#task-ask .pa-ctx').textContent())?.trim(), 'Product › Spec · 3 tasks on screen');
      assert.deepEqual(await buttons(page), [{text: 'PM', on: true}, {text: 'Spec · Writer', on: false}]);
      // The last row is not hidden under the bar.
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      const last = await page.locator('#task-body .pv-phone [data-task]').last().evaluate(el => el.getBoundingClientRect().bottom);
      assert.ok(last <= (await rect(page, '#task-ask')).top + 1, `the list keeps room under its last row: ${last}`);
      await page.evaluate(() => window.scrollTo(0, 0));

      // The stage's holder, picked: the message goes to its direct chat with the board.
      await page.locator('#task-ask [data-pa-to="stage"]').click();
      assert.equal(await placeholder(page), 'Ask Writer…');
      await page.locator('#task-ask .pa-text').fill('Which of these is closest to ready?');
      await page.locator('#task-ask .pa-send').click();
      const sheet = page.locator('dialog.pa-sheet[open]');
      await sheet.locator('.th-board').waitFor();
      assert.deepEqual(sent.map(s => s.path), ['/api/v2/threads/dm', '/api/v2/threads/dm-1/messages'], 'the chat is made, then the message goes in it');
      assert.deepEqual(sent[0]!.body.members, ['bot:writer']);
      assert.equal(sent[1]!.body.text, 'Which of these is closest to ready?');
      assert.deepEqual(sent[1]!.body.refs, {board: {pipeline_id: 'product', pipeline: 'Product', stage_id: 'spec', stage: 'Spec',
        task_ids: ['t-1', 't-2', 't-8'], more: 0}}, 'the message carries the pipeline, the stage and the tasks on screen');
      assert.equal((await sheet.locator('.pa-name').textContent())?.trim(), 'Writer');
      assert.equal((await sheet.locator('.th-board').textContent())?.replace(/\s+/g, ' ').trim(), 'Product › Spec 3 tasks', 'one card under the message');
      assert.equal(await sheet.locator('.pm-tabs, .pm-body, .pm-tab').count(), 0, 'the chat alone: no Log, no Tasks');
      assert.equal(await page.locator('#task-ask .pa-text').inputValue(), '', 'the box is empty once sent');

      // ✕: the board as it was.
      await sheet.locator('[data-pa-close]').click();
      await page.locator('dialog.pa-sheet').waitFor({state: 'detached'});
      assert.equal(await page.locator('#task-body .pv-tab.on').getAttribute('data-pv-tab'), 'spec', 'the same tab');

      // A search narrows what goes with the next message.
      sent.length = 0;
      await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = 'pricing'; q.dispatchEvent(new Event('input')); });
      await page.waitForFunction(() => document.querySelector('#task-ask .pa-ctx')?.textContent?.includes('1 task on screen'));
      await page.locator('#task-ask [data-pa-to="manager"]').click();
      await page.locator('#task-ask .pa-text').fill('Is this one blocked?');
      await page.locator('#task-ask .pa-send').click();
      await page.locator('dialog.pa-sheet[open] .th-board').waitFor();
      assert.deepEqual(sent.at(-1)!.body.refs?.board?.task_ids, ['t-8'], 'the ids of the rows the search lets through');
      assert.ok(sent.some(s => s.body.members?.[0] === 'bot:pm'), 'the manager this time');
      await page.keyboard.press('Escape');
      await page.locator('dialog.pa-sheet').waitFor({state: 'detached'});
      await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = ''; q.dispatchEvent(new Event('input')); });

      // Build is held by whoever the task is for: no second button. Review is held by the manager: it shows once.
      await page.locator('#task-body .pv-tab[data-pv-tab="build"]').click();
      await page.waitForFunction(() => document.querySelector('#task-ask .pa-ctx')?.textContent?.includes('› Build'));
      assert.deepEqual(await buttons(page), [{text: 'PM', on: true}]);
      await page.locator('#task-body .pv-tab[data-pv-tab="review"]').click();
      await page.waitForFunction(() => document.querySelector('#task-ask .pa-ctx')?.textContent?.includes('› Review · 0 tasks'));
      assert.deepEqual(await buttons(page), [{text: 'PM', on: true}]);
      assert.equal(await placeholder(page), 'Ask PM…');

      // A card opens its task at its conversation, asking its owner.
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await page.locator('#task-body .pv-phone [data-task="t-1"]').click();
      const modal = page.locator('#task-modal[open]');
      await modal.locator('.task-chat textarea').waitFor();
      assert.equal(await modal.evaluate(d => d.classList.contains('thread')), true, 'the conversation, not the details');
      await page.waitForFunction(() => document.querySelector<HTMLTextAreaElement>('#task-modal .task-chat textarea')?.placeholder === 'Ask PM about this task…');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // 320 px: nothing sideways, every tap target 44 × 44.
      const {page, errors} = await open(browser, {viewport: {width: 320, height: 640}, tasks: tasks(), needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await bar(page).waitFor();
      const wide = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(wide <= 320, `nothing overflows sideways: ${wide}`);
      const targets = await page.locator('#task-ask button, #task-ask textarea').evaluateAll(els => els.map(e => {
        const r = e.getBoundingClientRect();
        return {what: e.className, w: r.width, h: r.height, right: r.right};
      }));
      assert.ok(targets.length === 4 && targets.every(t => t.w >= 44 && t.h >= 44 && t.right <= 320), `tap targets: ${JSON.stringify(targets)}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv').waitFor();
      assert.equal(await page.locator('#task-ask').isVisible(), false, 'a computer has the rail, no bar');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, needs: [], hash: '#/tasks?pipeline=ops'});
      await page.locator('#task-body .pv').waitFor();
      assert.equal(await page.locator('#task-ask').isVisible(), false, 'no manager and no holder: no bar');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('a phone board asks its manager or the stage holder with the tasks on screen; the chat opens alone over the board; a card asks its owner: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
