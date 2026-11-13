// The board's bots on a phone: a floating face instead of the Ask block (ui/app/pipeline-ask.ts; spec "Pipeline board:
// one-row header, one count per stage, a floating face", part C), offline on the fake server of tasks-page.cts. Checks:
//  - at 390 px the manager's 44 px round face floats 12 px from the right and 12 px above the bottom bar; no context
//    line, no bot picker and no box sit on the board, and the list keeps 64 px under its last row;
//  - tapped, a menu above the face lists the board's bots: the manager first, then the open stage's holder, each with
//    its role ("Manager", "Holds Spec"); Escape closes it and gives the face the focus back;
//  - picked, the bot's direct chat opens over the board, the chat alone (no Log, no Tasks), its box focused, with the
//    board as a chip above the box ("Product › Spec · 3 tasks"); a message carries refs.board (the pipeline, the stage,
//    the ids of the tasks on screen, search applied) and shows one card; ✕ on the chip stops sending it;
//  - ✕ puts the chat away: the same tab, the same scroll;
//  - with one bot on the board the face opens its chat at once, with no menu;
//  - at 320 px nothing overflows sideways and the face and each menu row are at least 44 px;
//  - a computer, and a pipeline with no manager and no holder, have no face.
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

const face = (page: Page) => page.locator('#task-ask .pa-fab');
const picks = (page: Page) => page.locator('#task-ask .pa-pick').evaluateAll(els => els.map(e => ({
  name: (e.querySelector('.pa-pick-n')?.textContent || '').trim(), role: (e.querySelector('.pa-pick-r')?.textContent || '').trim()})));
const chip = (page: Page) => page.locator('dialog.pa-sheet[open] .thv-ctx-l');
const focused = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => el === document.activeElement);
const rect = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, w: r.width, h: r.height};
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
      await face(page).waitFor();

      // The face: 44 px, round, 12 px from the right and above the bottom bar. Nothing else of the old Ask block.
      const f = await rect(page, '#task-ask .pa-fab'), nav = await rect(page, '#mobile-nav');
      const width = await page.evaluate(() => window.innerWidth);
      assert.ok(Math.abs(f.w - 44) < 1 && Math.abs(f.h - 44) < 1, `a 44 px face: ${JSON.stringify(f)}`);
      assert.ok(Math.abs(nav.top - f.bottom - 12) < 1.5, `12 px above the bottom bar: ${f.bottom} vs ${nav.top}`);
      assert.ok(Math.abs(width - f.right - 12) < 1.5, `12 px from the right: ${f.right} of ${width}`);
      assert.equal(await face(page).evaluate(el => getComputedStyle(el).borderRadius), '50%');
      assert.equal(await page.locator('#task-ask textarea, #task-ask .pa-ctx, #task-ask .pa-to, #task-ask form').count(), 0, 'no context line, picker or box');
      assert.equal(await face(page).getAttribute('aria-label'), 'Chat with…');
      assert.equal(await page.locator('#task-body .pv-phone [data-task]').count(), 3);
      // The last row clears the face: the list keeps 64 px under it.
      assert.equal(await page.locator('#tasks-pane').evaluate(el => getComputedStyle(el).paddingBottom), '64px');

      // Tap: the menu lists the manager, then the open stage's holder.
      await face(page).click();
      await page.locator('#task-ask .pa-pop:not([hidden])').waitFor();
      assert.deepEqual(await picks(page), [{name: 'PM', role: 'Manager'}, {name: 'Writer', role: 'Holds Spec'}]);
      assert.equal(await face(page).getAttribute('aria-expanded'), 'true');
      const pop = await rect(page, '#task-ask .pa-pop');
      assert.ok(Math.abs(pop.w - 220) < 1 && pop.bottom <= f.top, `a 220 px menu above the face: ${JSON.stringify(pop)}`);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#task-ask .pa-pop').isHidden(), true, 'Escape closes the menu');
      assert.ok(await focused(page, '#task-ask .pa-fab'), 'and the face has the focus back');

      // Pick the stage's holder: its chat opens over the board, the box focused, the board as a chip.
      await page.evaluate(() => window.scrollTo(0, 40));
      const scrolled = await page.evaluate(() => window.scrollY);
      await face(page).click();
      await page.locator('#task-ask .pa-pick[data-pa-who="bot:writer"]').click();
      const sheet = page.locator('dialog.pa-sheet[open]');
      await sheet.locator('.thv-composer .p-text').waitFor();
      assert.equal((await sheet.locator('.pa-name').textContent())?.trim(), 'Writer');
      assert.equal(await sheet.locator('.pm-tabs, .pm-body, .pm-tab').count(), 0, 'the chat alone: no Log, no Tasks');
      await page.waitForFunction(() => document.activeElement?.matches('dialog.pa-sheet .thv-composer .p-text') ?? false);
      assert.equal((await chip(page).textContent())?.trim(), 'Product › Spec · 3 tasks', 'the board as a chip above the box');
      await sheet.locator('.thv-composer .p-text').fill('Which of these is closest to ready?');
      await sheet.locator('.thv-composer .p-send').click();
      await sheet.locator('.th-board').waitFor();
      assert.deepEqual(sent.map(s => s.path), ['/api/v2/threads/dm', '/api/v2/threads/dm-1/messages'], 'the chat is made, then the message goes in it');
      assert.deepEqual(sent[0]!.body.members, ['bot:writer']);
      assert.equal(sent[1]!.body.text, 'Which of these is closest to ready?');
      assert.deepEqual(sent[1]!.body.refs, {board: {pipeline_id: 'product', pipeline: 'Product', stage_id: 'spec', stage: 'Spec',
        task_ids: ['t-1', 't-2', 't-8'], more: 0}}, 'the message carries the pipeline, the stage and the tasks on screen');
      assert.equal((await sheet.locator('.th-board').textContent())?.replace(/\s+/g, ' ').trim(), 'Product › Spec · 3 tasks', 'one card under the message');
      // The chat fills the sheet: its box sits at the bottom, inside the screen's edges.
      await page.waitForFunction(() => { const a = document.querySelector('dialog.pa-sheet')?.getAnimations({subtree: true}) ?? []; return a.every(x => x.playState !== 'running'); });
      const box = await sheet.locator('.thv-composer').evaluate(el => el.getBoundingClientRect().toJSON() as DOMRect);
      const panel = await sheet.evaluate(el => el.getBoundingClientRect().toJSON() as DOMRect);
      assert.ok(panel.bottom - box.bottom >= 0 && panel.bottom - box.bottom <= 24, `the box is at the sheet's bottom: ${box.bottom} of ${panel.bottom}`);
      assert.ok(box.left >= 4 && box.right <= width - 4, `the box is inside the screen: ${box.left}–${box.right} of ${width}`);

      // ✕ on the chip: the next message goes without the board.
      await sheet.locator('.thv-ctx-x').click();
      assert.equal(await sheet.locator('.thv-ctx').isHidden(), true, 'the chip is gone');
      await sheet.locator('.thv-composer .p-text').fill('And in general?');
      await sheet.locator('.thv-composer .p-send').click();
      for (let i = 0; i < 100 && sent.length < 3; i++) await page.waitForTimeout(20);
      assert.equal(sent.at(-1)!.body.text, 'And in general?');
      assert.equal(sent.at(-1)!.body.refs?.board, undefined, 'no board once the chip is removed');

      // ✕: the board as it was.
      await sheet.locator('[data-pa-close]').click();
      await page.locator('dialog.pa-sheet').waitFor({state: 'detached'});
      assert.equal(await page.locator('#task-body .pv-tab.on').getAttribute('data-pv-tab'), 'spec', 'the same tab');
      assert.equal(await page.evaluate(() => window.scrollY), scrolled, 'the same scroll position');

      // A search narrows what goes with the next message, and the chip says so.
      sent.length = 0;
      await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = 'pricing'; q.dispatchEvent(new Event('input')); });
      await page.waitForFunction(() => document.querySelectorAll('#task-body .pv-phone [data-task]').length === 1);
      await face(page).click();
      await page.locator('#task-ask .pa-pick[data-pa-who="bot:pm"]').click();
      assert.equal((await chip(page).textContent())?.trim(), 'Product › Spec · 1 task');
      await page.locator('dialog.pa-sheet[open] .thv-composer .p-text').fill('Is this one blocked?');
      await page.locator('dialog.pa-sheet[open] .thv-composer .p-send').click();
      await page.locator('dialog.pa-sheet[open] .th-board').waitFor();
      assert.deepEqual(sent.at(-1)!.body.refs?.board?.task_ids, ['t-8'], 'the ids of the rows the search lets through');
      assert.ok(sent.some(s => s.body.members?.[0] === 'bot:pm'), 'the manager this time');
      await page.keyboard.press('Escape');
      await page.locator('dialog.pa-sheet').waitFor({state: 'detached'});
      assert.ok(await focused(page, '#task-ask .pa-fab'), 'after Escape the face has the focus');
      await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = ''; q.dispatchEvent(new Event('input')); });

      // On Review (held by the manager) the holder of Spec still shows, after the manager.
      await page.locator('#task-body .pv-tab[data-pv-tab="review"]').click();
      await face(page).click();
      assert.deepEqual(await picks(page), [{name: 'PM', role: 'Manager'}, {name: 'Writer', role: 'Holds Spec'}]);
      // A tap outside closes the menu and opens nothing under it.
      await page.locator('#task-body .pv-tabs').click({position: {x: 5, y: 5}});
      assert.equal(await page.locator('#task-ask .pa-pop').isHidden(), true, 'a tap outside closes the menu');

      // A card still opens its task at its conversation, asking its owner.
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
      // 320 px: nothing sideways, the face and each menu row at least 44 px tall.
      const {page, errors} = await open(browser, {viewport: {width: 320, height: 640}, tasks: tasks(), needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await face(page).click();
      await page.locator('#task-ask .pa-pop:not([hidden])').waitFor();
      const wide = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(wide <= 320, `nothing overflows sideways: ${wide}`);
      const targets = await page.locator('#task-ask button').evaluateAll(els => els.map(e => {
        const r = e.getBoundingClientRect();
        return {what: e.className, w: r.width, h: r.height, left: r.left, right: r.right};
      }));
      assert.ok(targets.length === 3 && targets.every(t => t.w >= 44 && t.h >= 44 && t.left >= 0 && t.right <= 320), `tap targets: ${JSON.stringify(targets)}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // One bot on the board: the face opens its chat at once.
      const saved = product.stages.map(s => s.owner);
      for (const s of product.stages) (s as {owner: string | null}).owner = null;
      try {
        const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, tasks: tasks(), needs: [],
          hash: '#/tasks?pipeline=product', beforeGoto: chats([])});
        await face(page).waitFor();
        assert.equal(await face(page).getAttribute('aria-label'), 'Chat with PM');
        assert.equal(await face(page).getAttribute('aria-haspopup'), null, 'no menu');
        await face(page).click();
        await page.locator('dialog.pa-sheet[open] .thv-composer .p-text').waitFor();
        assert.equal((await page.locator('dialog.pa-sheet[open] .pa-name').textContent())?.trim(), 'PM');
        assert.deepEqual(errors, []);
        await page.close();
      } finally { product.stages.forEach((s, i) => { (s as {owner: string | null}).owner = saved[i] ?? null; }); }
    }
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv').waitFor();
      assert.equal(await page.locator('#task-ask').isVisible(), false, 'a computer has the rail, no face');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, needs: [], hash: '#/tasks?pipeline=ops'});
      await page.locator('#task-body .pv').waitFor();
      assert.equal(await page.locator('#task-ask').isVisible(), false, 'no manager and no holder: no face');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('a phone board has a floating face; its menu lists the board\'s bots; the chat opens alone over the board with the board as a chip: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
