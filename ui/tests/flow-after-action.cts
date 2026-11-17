import type {Page, Route} from 'playwright';
// Offline browser check of Flow right after each action (ui/app/decide.ts decideDo, decideApprove, decideBar), at
// 1200 px and on a 390 px phone: Approve, Approve with comments (an OK with nothing bound, and one bound to what it
// shows), a comment a rule holds, Send back, a reply to a bot's ❓, Snooze, Skip, Undo after each that has one (and an
// Undo that fails), and the last item (All done). After each the pane shows the next item (its card and
// buttons) or All done, the top bar is whole (where you are, ✕), and nothing throws. The undo notice is quiet text in
// the top bar: no box of its own, over no control, the pane or its buttons, and nothing moves for it.
// FLOW_SHOTS=<folder> saves the screen after Approve with comments, after a held comment and after a failed Undo,
// desktop and phone, light and dark.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Item = Record<string, unknown> & {id: string};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (n: number, title: string) => ({id: 't' + n, title, number: n, pipeline_id: 'product', stage_id: 'spec',
  status: 'open', owner_id: 'bot:tpo', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-15T09:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const ok = (n: number): Item => ({kind: 'ask', id: `v${n}:r${n}`,
  task_id: 't' + n, requirement_id: 'r' + n, since: `2026-11-15T10:${String(n).padStart(2, '0')}:00Z`, line: `OK the spec ${n}?`,
  task: brief(n, `Task ${n}: OK the spec ${n}?`), ask: {requirement_id: 'r' + n, kind: 'ok', line: `OK the spec ${n}?`, who: 'human:ana',
    asked_by: 'bot:tpo', may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null}});
// A bot's ❓ on a message of its task's chat (backend/question_marks.py): the reply is a message there.
const mark = (n: number): Item => ({kind: 'question', id: `qmark:q${n}`, mark_id: 'q' + n, task_id: 't' + n, message_id: 'm' + n,
  conversation_id: 'c' + n, root_id: 'c' + n, from_id: 'bot:tpo', requirement_id: null, line: `Which plan for ${n}?`,
  since: `2026-11-15T10:${String(n).padStart(2, '0')}:00Z`, task: brief(n, `Task ${n}`)});
const mention = (n: number): Item => ({kind: 'mention', id: `mn${n}`, line: `Can you look at the numbers ${n}?`, from_id: 'human:bo',
  conversation_id: 'c' + n, since: `2026-11-15T08:${String(n).padStart(2, '0')}:00Z`});
// OKs bound to what they show (GET /oks/rN has content and its hash): okGive, then the words as a comment.
const BOUND = new Set([3, 4, 13, 14]);

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: string[] = [];
    let needs: Item[] = [];
    const away = new Map<string, Item>();        // what an answer or a snooze took, for its undo
    const holdOnce = new Set<string>();          // tasks whose next comment a rule holds
    const failUndo = new Set<string>();          // answers whose next undo fails
    const take = (pred: (n: Item) => boolean) => { for (const n of needs.filter(pred)) away.set(n.id, n); needs = needs.filter(n => !pred(n)); };
    const back = (id: string) => { const n = away.get(id); if (n && !needs.some(x => x.id === id)) needs = [n, ...needs]; away.delete(id); };
    const page: Page = await browser.newPage({viewport: {width: 1200, height: 860}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{slug: 'tpo', id: 'tpo', display_name: 'Tasks Product Owner', status: 'active'}]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'bo', name: 'Bo'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(n => n.id).join(','), next: null});
      if (p === '/api/v2/needs-you/snoozed') return json({items: [], next: null});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      const o = p.match(/^\/api\/v2\/oks\/r(\d+)$/);
      if (o) return BOUND.has(Number(o[1])) ? json({requirement_id: 'r' + o[1], subject: `spec:${o[1]}`, content: {title: 'Launch email', body: 'Hello.'},
        shown_hash: 'e'.repeat(64), standing: 'waiting', task_id: 't' + o[1]}) : json({error: 'not_found'}, 404);
      const t = p.match(/^\/api\/v2\/tasks\/t(\d+)$/);
      if (t && r.method() === 'GET') return json({task: {...brief(Number(t[1]), `Task ${t[1]}`), body: 'What the task is about.',
        created_at: '2026-11-15T09:00:00Z', attachments: [], links: [], conversation_id: 'c' + t[1]}});
      // A reply in a ❓'s chat clears it (backend/question_marks.py).
      const said = r.method() === 'POST' && p.match(/^\/api\/v2\/threads\/(c\d+)\/messages$/);
      if (said) { writes.push(p); take(n => n['root_id'] === said[1]); return json({message: {id: 'm99'}}); }
      if (r.method() === 'POST' && p.startsWith('/api/v2/') && !p.startsWith('/api/v2/threads/')) {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        writes.push(p);
        const c = p.match(/^\/api\/v2\/tasks\/(t\d+)\/comments$/);
        if (c) {
          if (holdOnce.delete(c[1]!)) return json({held: {outcome: 'escalated'}});
          return json({message: {id: 'm9'}});
        }
        const a = p.match(/^\/api\/v2\/tasks\/(t\d+)\/asks\/(r\d+)\/(answer|ok)$/);
        if (a) { take(n => n['requirement_id'] === a[2]); return json({task: {id: a[1]}, answer_id: 'a-' + a[2], requirement_id: a[2]}); }
        const u = p.match(/^\/api\/v2\/answers\/a-r(\d+)\/undo$/);
        if (u && failUndo.delete('a-r' + u[1])) return json({error: 'internal', detail: 'Not undone: try again'}, 500);
        if (u) { back(`v${u[1]}:r${u[1]}`); return json({task: {id: 't' + u[1]}}); }
        if (p === '/api/v2/needs-you/snooze') { take(n => n.id === body['id']); return json({id: body['id'], snoozed_until: body['until']}); }
        if (p === '/api/v2/needs-you/unsnooze') { back(String(body['id'])); return json({id: body['id']}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card'), side = dialog.locator('.dc-side');
    const composer = dialog.locator('.dc-chat .p-text'), arrow = dialog.locator('.dc-chat .p-send');
    const approve = side.locator('[data-key] [data-dc="approve"]');
    const phone = () => (page.viewportSize()?.width || 0) <= 760;

    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('#tasks-root, main').first().waitFor();

    let paneTop = 0;   // where the pane starts under the top bar, which nothing moves
    /** Flow on exactly `items`, on the first of them. */
    const open = async (items: Item[]) => {
      if (await dialog.isVisible()) await dialog.locator('[data-dc="close"]').click();
      needs = items; away.clear();
      await page.evaluate('v2Refresh()');
      await page.evaluate('decideFlow(document.body)');
      await side.locator(`[data-key="${items[0]!.id}"]`).waitFor();
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      await page.waitForTimeout(450);   // a phone's new item takes no taps for 400 ms (decideCalm)
      paneTop = await dialog.locator('.dc-main').evaluate(e => e.getBoundingClientRect().top);
    };
    /** The pane after an action: `next` on screen with its buttons, or All done; the top bar whole; the undo notice
        plain text in the top bar, over nothing, and nothing moved for it. */
    const whole = async (what: string, next: string | null) => {
      if (next) await side.locator(`[data-key="${next}"]`).waitFor();
      else await side.locator('.dc-done h2', {hasText: 'All done'}).waitFor();
      const look = await page.evaluate(() => {
        const q = (s: string) => document.querySelector<HTMLElement>(`#need-card ${s}`);
        const box = (e: HTMLElement | null) => e ? e.getBoundingClientRect() : null;
        const shown = (e: HTMLElement | null) => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
        const head = box(q('.dc-head')), bar = q('.dc-bar'), on = bar && !bar.hidden ? bar : null, barBox = box(on);
        // What the notice never lies over: the top bar's own controls, the pane, and every button in it.
        const meet = (o: DOMRect) => !!barBox && o.width > 0 && o.left < barBox.right && o.right > barBox.left && o.top < barBox.bottom && o.bottom > barBox.top;
        const over = [...document.querySelectorAll<HTMLElement>('#need-card .dc-head > :not(.dc-bar):not(.dc-sayw):not(.dc-up), #need-card .dc-nav > button, #need-card .dc-main, #need-card .dc-main button')]
          .filter(shown).filter(e => meet(e.getBoundingClientRect())).map(e => e.className || e.tagName);
        // Nothing moves for it: the pane and its first heading sit where they sit without it.
        const where = () => { const h = [...document.querySelectorAll<HTMLElement>('#need-card .dc-main h2, #need-card .dc-main h3')].find(shown);
          return JSON.stringify([box(q('.dc-main')), box(h || null)]); };
        let shift = '';
        if (on) { const withIt = where(); on.hidden = true; const without = where(); on.hidden = false; if (withIt !== without) shift = `${withIt} vs ${without}`; }
        // The middle of the pane as it shows (a phone's is one scroll: the side may be under the fold).
        const m = box(q('.dc-main'))!, bottom = Math.min(m.bottom, innerHeight), hit = document.elementFromPoint(m.left + m.width / 2, (m.top + bottom) / 2);
        const buttons = [...document.querySelectorAll<HTMLElement>('#need-card .dc-side [data-key] button, #need-card .dc-side .dc-done button, #need-card .dc-chat button')].filter(shown);
        return {crumb: shown(q('.dc-crumb')), close: shown(q('[data-dc="close"]')), head, bar: barBox, buttons: buttons.length,
          covered: !!hit?.closest('.dc-bar'), onMain: !!hit?.closest('.dc-main'), over, shift, paneTop: box(q('.dc-main'))!.top,
          fill: on ? getComputedStyle(on).backgroundColor : '', undo: box(on?.querySelector<HTMLElement>('[data-dc="undo"]') || null)};
      });
      assert.ok(look.crumb && look.close, `${what}: the top bar shows where you are and ✕ (${JSON.stringify(look)})`);
      assert.ok(look.buttons > 0, `${what}: the pane has its buttons (${JSON.stringify(look)})`);
      assert.ok(!look.covered && look.onMain, `${what}: nothing lies over the pane (${JSON.stringify(look)})`);
      assert.equal(look.paneTop, paneTop, `${what}: the pane starts where it did`);
      if (look.bar) {
        assert.ok(look.head && look.bar.top >= look.head.top - 1 && look.bar.bottom <= look.head.bottom + 1,
          `${what}: the undo notice is in the top bar (${JSON.stringify(look)})`);
        assert.deepEqual(look.over, [], `${what}: the undo notice lies over nothing`);
        assert.equal(look.shift, '', `${what}: nothing moves for the undo notice`);
        assert.equal(look.fill, 'rgba(0, 0, 0, 0)', `${what}: the undo notice has no box of its own`);
        if (look.undo) assert.ok(look.undo.width >= 44 && look.undo.height >= 44 && look.undo.right <= look.bar.right + 1, `${what}: Undo shows whole, a 44 px target (${JSON.stringify(look)})`);
      }
      assert.deepEqual(errors, [], `${what}: nothing threw`);
    };
    /** Undo from the top bar: the item is back in the walk, and the pane still whole. */
    const undo = async (what: string, id: string, onScreen: string | null) => {
      await dialog.locator('.dc-bar [data-dc="undo"]').click();
      await dialog.locator('.dc-bar[hidden]').waitFor({state: 'attached'});
      await page.waitForFunction(`!!DEC && DEC.items.has(${JSON.stringify(id)})`);   // DEC is a top-level let
      await whole(`${what}, then Undo`, onScreen);
    };
    const toChat = async () => { if (phone()) await dialog.locator('[data-dc="tab"][data-dc-tab="chat"]').click(); };
    const shots = process.env.FLOW_SHOTS || '';
    const shoot = async (name: string) => {
      if (!shots) return;
      for (const scheme of ['light', 'dark']) {
        await page.evaluate((s: string) => document.documentElement.setAttribute('data-theme', s), scheme);
        await page.screenshot({path: path.join(shots, `${name}-${scheme}.png`)});
      }
      await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
    };

    for (const width of [1200, 390]) {
      await page.setViewportSize(width === 390 ? {width: 390, height: 844} : {width: 1200, height: 860});
      const at = width === 390 ? 'phone' : 'desktop';

      // Approve, an OK with nothing bound.
      await open([ok(2), ok(90)]);
      await approve.click();
      await whole(`${at} Approve`, 'v90:r90');
      await undo(`${at} Approve`, 'v2:r2', 'v90:r90');

      // Approve with comments, an OK with nothing bound: the words go as its note.
      await open([ok(2), ok(90)]);
      await toChat();
      await composer.fill('Fine, ship it');
      await page.waitForFunction(() => document.querySelector('#need-card [data-dc="approve"]')?.textContent === 'Approve with comments');
      await approve.click();
      await whole(`${at} Approve with comments (unbound)`, 'v90:r90');
      assert.match(await dialog.locator('.dc-bar').innerText(), /Approved with comments/);
      await shoot(`approve-with-comments-${at}`);
      await undo(`${at} Approve with comments (unbound)`, 'v2:r2', 'v90:r90');

      // An Undo that fails says so in the same place, in the failure colour.
      await open([ok(15), ok(90)]);
      await approve.click();
      await whole(`${at} Approve before a failed Undo`, 'v90:r90');
      failUndo.add('a-r15');
      await dialog.locator('.dc-bar [data-dc="undo"]').click();
      await dialog.locator('.dc-bar.dc-bar-err', {hasText: 'Not undone: try again'}).waitFor();
      await whole(`${at} a failed Undo`, 'v90:r90');
      const tint = await dialog.locator('.dc-bar').evaluate(e => {
        const probe = document.createElement('span'); probe.style.color = 'var(--fail)'; e.after(probe);
        const fail = getComputedStyle(probe).color; probe.remove(); return [getComputedStyle(e).color, fail];
      });
      assert.equal(tint[0], tint[1], 'it is in the failure colour');
      await shoot(`undo-failed-${at}`);

      // Approve with comments, an OK bound to what it shows: the OK, then the words as a comment.
      await open([ok(3), ok(90)]);
      await toChat();
      await composer.fill('Send it Monday');
      await page.waitForFunction(() => document.querySelector('#need-card [data-dc="approve"]')?.textContent === 'Approve with comments');
      let n = writes.length;
      await approve.click();
      await whole(`${at} Approve with comments (bound)`, 'v90:r90');
      assert.deepEqual(writes.slice(n), ['/api/v2/tasks/t3/asks/r3/ok', '/api/v2/tasks/t3/comments']);
      await undo(`${at} Approve with comments (bound)`, 'v3:r3', 'v90:r90');

      // A comment a rule holds: approved, the item stays with why and no Approve; the arrow sends it on.
      holdOnce.add('t4');
      await open([ok(4), ok(90)]);
      await toChat();
      await composer.fill('Only after legal');
      await approve.click();
      await side.locator('.err', {hasText: 'Your comment did not go'}).waitFor();
      await whole(`${at} a held comment`, 'v4:r4');
      await shoot(`comment-held-${at}`);
      assert.equal(await approve.count(), 0, 'approved: no Approve left');
      // Undo takes the OK back: Approve is there again, and nothing says it was approved.
      await undo(`${at} a held comment`, 'v4:r4', 'v4:r4');
      await approve.waitFor();
      assert.equal(await side.locator('.err').count(), 0, 'after Undo nothing says it was approved');
      // Approved again with the held words: the arrow is not needed, they go as the comment.
      await approve.click();
      await whole(`${at} approved again after Undo`, 'v90:r90');

      holdOnce.add('t14');
      await open([ok(14), ok(90)]);
      await toChat();
      await composer.fill('Only after legal');
      await approve.click();
      await side.locator('.err', {hasText: 'Your comment did not go'}).waitFor();
      n = writes.length;
      await arrow.click();
      await whole(`${at} a held comment sent again`, 'v90:r90');
      assert.deepEqual(writes.slice(n), ['/api/v2/tasks/t14/comments']);

      // Send back: the arrow with words on an OK.
      await open([ok(5), ok(90)]);
      await toChat();
      await composer.fill('Say what it costs');
      await arrow.click();
      await whole(`${at} Send back`, 'v90:r90');
      await undo(`${at} Send back`, 'v5:r5', 'v90:r90');

      // A reply to a bot's ❓: a message of its chat (no Undo: a sent message stays sent).
      await open([mark(6), ok(90)]);
      n = writes.length;
      await composer.fill('The small one');
      await arrow.click();
      await whole(`${at} a reply`, 'v90:r90');
      assert.deepEqual(writes.slice(n), ['/api/v2/threads/c6/messages']);

      // Snooze, 1 hour.
      await open([ok(8), ok(90)]);
      await side.locator('[data-dc="snooze"]').click();
      await side.locator('[data-dc="snooze-hour"]').click();
      await whole(`${at} Snooze`, 'v90:r90');
      await undo(`${at} Snooze`, 'v8:r8', 'v90:r90');

      // Skip: the next item, nothing answered.
      await open([mention(9), mention(91)]);
      n = writes.length;
      await side.locator('[data-dc="skip"]').click();
      await whole(`${at} Skip`, 'mn91');
      assert.equal(writes.length, n, 'Skip sends nothing');

      // The last item: All done; Undo brings it back on screen.
      await open([ok(12)]);
      await approve.click();
      await whole(`${at} the last item`, null);
      await undo(`${at} the last item`, 'v12:r12', 'v12:r12');
      await open([ok(13)]);
      await toChat();
      await composer.fill('Good');
      await approve.click();
      await whole(`${at} the last item with comments`, null);
      await undo(`${at} the last item with comments`, 'v13:r13', 'v13:r13');
    }
    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: after every Flow action the next item or All done shows under a whole top bar, the undo notice in its slot, and Undo leaves it whole.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
