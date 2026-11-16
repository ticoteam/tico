import type {Route} from 'playwright';
// Offline browser check of an ask in Flow answered in its task's chat (ui/app/decide.ts decideChatMount), against a fake
// of the task read, its thread's timeline (the chat every thread uses, thread-view.ts) and the answer routes:
// - the right side is the task's thread: its messages, then the ask as the asker's last message (byline with "needs
//   you", the question, its why, its choices to tap with only the Recommended tag); no heading, no Send button;
// - a bot's question: the composer's arrow sends a reply named for the ask (POST …/comments {text, ask}), snoozes it
//   for the person (POST /needs-you/snooze) and moves on; a tapped choice answers;
// - an OK: Approve alone, "Approve with comments" while the composer has words, which go with the approval as its
//   note; the arrow sends it back with the words as the note (answer no); ⋯ holds Close task and Give to…;
// - on a phone the task folds to its title with Read the task, and the chat and its row take the rest.
// FLOW_SHOTS=<folder> saves a question, an OK empty and with words, after a reply, and ⋯ open, desktop and phone.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Item = Record<string, unknown> & {id: string};
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (n: number, title: string, bot: string) => ({id: 't' + n, title, number: n, pipeline_id: 'product', stage_id: 'spec',
  status: 'open', owner_id: bot, requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-15T09:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const question = (): Item => ({kind: 'ask', id: 'v1:r1', task_id: 't1', requirement_id: 'r1', since: '2026-11-15T09:03:00Z',
  line: 'Can you answer my seven setup questions, or take my suggested answers?', task: brief(1, 'Set up: Agent Services', 'bot:asp'),
  ask: {requirement_id: 'r1', kind: 'answer', line: 'Can you answer my seven setup questions, or take my suggested answers?', who: 'human:ana',
    asked_by: 'bot:asp', choices: ['Take my suggestions'], may: ['answer'], met: false, shows: [], standing: 'waiting', answer: null,
    brief: {why: 'Without them I cannot count who uses these services.', details: ['I record each as yours and finish setup today'], recommend: 'Take my suggestions'}}});
const ok = (): Item => ({kind: 'ask', id: 'v2:r2', task_id: 't2', requirement_id: 'r2', since: '2026-11-15T10:00:00Z', line: 'OK the spec?',
  task: brief(2, 'Pipeline boards show where work waits', 'bot:tpo'),
  ask: {requirement_id: 'r2', kind: 'ok', line: 'OK the spec?', who: 'human:ana', asked_by: 'bot:tpo', choices: null, may: ['ok', 'no'],
    met: false, shows: [], standing: 'waiting', answer: null, brief: {why: 'v2 covers your 12 points; one line under each stage name.'}}});
const msg = (id: string, from: string, body: string, at: string) => ({id, seq: Number(id.slice(1)), sender_id: from, body, kind: 'say', created_at: at, conversation_id: 'c1'});

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
    let needs: Item[] = [question(), ok()];
    const page = await browser.newPage({viewport: {width: 1200, height: 860}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{slug: 'asp', id: 'asp', display_name: 'Agent Services Product Owner', status: 'active'},
        {slug: 'tpo', id: 'tpo', display_name: 'Tasks Product Owner', status: 'active'}]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(n => n.id).join(','), next: null});
      if (p === '/api/v2/oks/r2') return json({error: 'not_found'}, 404);   // a plain OK: approved with its note
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      if (p === '/api/v2/flow/suggestions') return json({state: 'ready', suggestions: []});
      const t = p.match(/^\/api\/v2\/tasks\/(t\d)$/);
      if (t && r.method() === 'GET') return json({task: {...(t[1] === 't1' ? question() : ok()).task as object, body: 'Your area and its products.',
        created_at: '2026-11-15T09:00:00Z', attachments: [], links: [], conversation_id: t[1] === 't1' ? 'c1' : 'c2'}});
      const th = p.match(/^\/api\/v2\/threads\/(c\d)\/timeline$/);
      if (th) return json({thread: {id: th[1], title: th[1] === 'c1' ? 'Set up: Agent Services' : 'Pipeline boards', me: {can_post: true}}, messages: th[1] === 'c1'
        ? [msg('m1', 'bot:asp', 'Seven things I could not find written down: 1. Who counts as a user of Search? … 7. Which products are hosted?', '2026-11-15T09:02:00Z')]
        : [msg('m2', 'human:ana', 'Show stuck counts, keep it to one line.', '2026-11-14T10:00:00Z')], next_cursor: null});
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        if (!p.startsWith('/api/v2/threads/')) writes.push({p, body});   // a thread's read marks are its own
        if (p === '/api/v2/tasks/t1/comments') return json({message: {id: 'm9'}});
        if (p === '/api/v2/needs-you/snooze') { needs = needs.filter(n => n.id !== body['id']); return json({id: body['id'], snoozed_until: body['until']}); }
        const a = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
        if (a) { needs = needs.filter(n => n['requirement_id'] !== a[2]); return json({task: {id: a[1]}, answer_id: 'a-' + a[2], requirement_id: a[2]}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const dialog = page.locator('#need-card'), chat = dialog.locator('.dc-chat');
    const composer = chat.locator('.p-text'), arrow = chat.locator('.p-send');
    const shot = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await page.waitForTimeout(900);   // past "Getting ready to flow"
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(400);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-phone.png`)});
      await page.setViewportSize({width: 1200, height: 860});
      await page.waitForTimeout(200);
    };

    // ---- 1. A bot's question: its thread, the ask as the last message, the composer's arrow and no Send button.
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="v1:r1"]').waitFor();
    await page.evaluate('decideFlow(document.body)');
    await chat.getByText('Seven things I could not find written down', {exact: false}).waitFor();
    const ask = chat.locator('.dc-msg-ask');
    await ask.waitFor();
    assert.equal(await chat.locator('.thv-list > :last-child .dc-msg-ask, .thv-list > .dc-msg-ask:last-child').count() >= 1, true, 'the ask is the last message');
    assert.match(await ask.locator('.dc-msg-by').innerText(), /Agent Services Product Owner.*needs you/s);
    assert.equal(await ask.locator('.dc-q').innerText(), 'Can you answer my seven setup questions, or take my suggested answers?');
    assert.equal(await ask.locator('.dc-why').innerText(), 'Without them I cannot count who uses these services.');
    assert.deepEqual(await ask.locator('.dc-rec-tag').allInnerTexts(), ['Recommended']);
    assert.equal(await dialog.locator('[data-dc="sendback"], [data-dc="other"], #dc-notes, #dc-other').count(), 0, 'no Send, no second box');
    assert.equal(await dialog.locator('.dc-side [data-key] [data-dc="approve"]').count(), 0, 'a question has no Approve');
    await shot('question');

    // The arrow: a reply named for the ask, the ask snoozed for Ana until the bot writes back, and the next item.
    await composer.fill('1: anyone who ran a search this month. 2: per call. Take your suggestions for 3 to 7.');
    await arrow.click();
    await chat.locator('.dc-q', {hasText: 'OK the spec?'}).waitFor();
    assert.deepEqual(writes[0], {p: '/api/v2/tasks/t1/comments', body: {text: '1: anyone who ran a search this month. 2: per call. Take your suggestions for 3 to 7.', ask: 'r1'}});
    assert.equal(writes[1]!.p, '/api/v2/needs-you/snooze');
    assert.equal(writes[1]!.body['id'], 'v1:r1');
    assert.ok(Date.parse(String(writes[1]!.body['until'])) > Date.now() + 6 * 86_400_000, 'until the bot writes back, a week at most');
    await shot('after-reply');

    // ---- 2. An OK: Approve alone; words in the composer make it "Approve with comments".
    const approve = dialog.locator('.dc-side [data-key] [data-dc="approve"]');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    assert.equal((await approve.innerText()).trim(), 'Approve');
    assert.equal(await chat.locator('.dc-msg-ask .dc-why').innerText(), 'v2 covers your 12 points; one line under each stage name.');
    await shot('ok-empty');
    await composer.fill('Fine, but amber only when more than 3 are stuck');
    assert.equal((await approve.innerText()).trim(), 'Approve with comments');
    await shot('ok-typed');
    // ⋯: Close task and Give to…, last at the far right.
    const row = dialog.locator('.dc-side [data-key] > .dc-acts');
    assert.equal(await row.locator(':scope > :last-child [data-dc="ask-more"]').count(), 1, '⋯ last');
    await row.locator('[data-dc="ask-more"]').click();
    assert.deepEqual(await dialog.locator('.dc-askw [role="menuitem"]').allInnerTexts(), ['Close task', 'Give to…']);
    await shot('more-open');
    await page.keyboard.press('Escape');
    // The arrow sends it back, the words as the note.
    const n = writes.length;
    await arrow.click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes[n], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'no', version: 3, note: 'Fine, but amber only when more than 3 are stuck'}});

    // ---- 3. On a phone: the task folds to its title, Read the task opens it.
    needs = [ok()];
    await page.setViewportSize({width: 390, height: 844});
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('v2Refresh()');
    await page.evaluate('decideFlow(document.body)');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    assert.equal(await dialog.locator('.dc-thing').isVisible(), false, 'the task folds away');
    assert.equal(await dialog.locator('.dc-fold h3').innerText(), 'Pipeline boards show where work waits');
    await dialog.locator('[data-dc="fold"]').click();
    assert.equal(await dialog.locator('.dc-thing').isVisible(), true, 'Read the task opens it');
    await dialog.locator('[data-dc="fold"]').click();
    // Approve with comments: the approval carries the words as its note.
    await composer.fill('Ship it');
    const m = writes.length;
    await approve.click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes[m], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'ok', version: 3, note: 'Ship it'}});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scroll');

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: an ask in its task\'s chat: the ask as the last message; the arrow replies (a question snoozes until the bot writes) or sends an OK back; Approve with comments; ⋯; the phone folds the task.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
