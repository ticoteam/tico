import type {Route} from 'playwright';
// Offline browser check of an OK in Flow answered in its task's chat (ui/app/decide.ts decideChatMount), against a fake
// of the task read, its thread's timeline (the chat every thread uses, thread-view.ts) and the answer routes:
// - the right side is the task's thread: its messages, then the ask as the asker's last message (byline, the
//   question); no heading, no Send button, no second box;
// - an OK: Approve alone; once the composer has words, Send back, Close and Approve, each with a comment icon: the
//   words go with Approve as its note, Send back answers no with them, Close closes the task with them; the arrow
//   sends it back with the words as the note (answer no); ⋯ holds Close task and Give to…;
// - on a phone two tabs in one tight row, Chat and Task (the task open, no chevron); a bot's ❓ (its reply path is
//   question-marks.cts) opens on its chat, and on a task with no documents has no tabs at all; an OK whose task has
//   documents (ui/app/task-docs.ts) lists Chat, then them, and opens on the one it approves; the ask's byline stays one
//   line, the name cut first; Flow's chat has the day divider only, no "New" line; a ❓ under unread messages shows its
//   question with no Unread pill over it.
// FLOW_SHOTS=<folder> saves an OK empty and with words, and ⋯ open, desktop and phone; the words row in light and dark too.
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
// A bot's ❓ on its last message in t1's chat (backend/question_marks.py), under thirty Ana has not read.
const mark = (): Item => ({kind: 'question', id: 'qmark:q1', mark_id: 'q1', task_id: 't1', message_id: 'm50', conversation_id: 'c1', root_id: 'c1',
  from_id: 'bot:asp', requirement_id: null, since: '2026-11-15T09:02:00Z', line: 'Who counts as a user of Search?', task: brief(1, 'Set up: Agent Services', 'bot:asp')});
const ok = (): Item => ({kind: 'ask', id: 'v2:r2', task_id: 't2', requirement_id: 'r2', since: '2026-11-15T10:00:00Z', line: 'OK the spec?',
  task: brief(2, 'Pipeline boards show where work waits', 'bot:tpo'),
  ask: {requirement_id: 'r2', kind: 'ok', line: 'OK the spec?', who: 'human:ana', asked_by: 'bot:tpo', may: ['ok', 'no'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
// t3's documents, as GET /v2/tasks/{id} lists them: newest change first.
const documents = () => [
  {name: 'QA report', file_id: 'f-qa', version: 1, by: 'bot:tpo', at: ago(20), versions: [{n: 1, by: 'bot:tpo', at: ago(20), note: 'All checks pass'}]},
  {name: 'Spec', file_id: 'f-spec', version: 3, by: 'bot:tpo', at: ago(125), versions: [{n: 3, by: 'bot:tpo', at: ago(125)}, {n: 2, by: 'human:ana', at: ago(60 * 26)}, {n: 1, by: 'bot:tpo', at: ago(60 * 50)}]},
];
const msg = (id: string, from: string, body: string, at: string) => ({id, seq: Number(id.slice(1)), sender_id: from, body, kind: 'say', created_at: at, conversation_id: 'c1'});

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
    let needs: Item[] = [ok()];
    // `fail`: comment posts to answer 500, in turn.
    const fail: Record<string, number> = {};
    let uploads = 0;
    // https: an upload hashes its bytes (crypto.subtle), which a page has only in a secure context.
    const page = await browser.newPage({viewport: {width: 1200, height: 860}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', async (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{slug: 'asp', id: 'asp', display_name: 'Agent Services Product Owner', status: 'active'},
        {slug: 'tpo', id: 'tpo', display_name: 'Tasks Product Owner', status: 'active'}]);
      if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: []});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: needs.map(n => n.id).join(','), next: null});
      if (/^\/api\/v2\/oks\/r[245]$/.test(p)) return json({error: 'not_found'}, 404);   // a plain OK: approved with its note
      if (p === '/api/v2/oks/r3') return json({requirement_id: 'r3', subject: 'spec:3', content: {title: 'Launch email', body: 'Hello.'}, shown_hash: 'e'.repeat(64), standing: 'waiting', task_id: 't3'});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});
      const t = p.match(/^\/api\/v2\/tasks\/(t\d)$/);
      if (t && r.method() === 'GET') return json({task: {...(t[1] === 't1' ? mark() : ok()).task as object, id: t[1], request: {text: 'Your area and its products.'},
        created_at: '2026-11-15T09:00:00Z', attachments: [], links: [], conversation_id: t[1] === 't1' ? 'c1' : 'c2', documents: t[1] === 't3' ? documents() : []}});
      if (p.startsWith('/api/v2/files/f-')) return route.fulfill({contentType: 'text/html', body: p.startsWith('/api/v2/files/f-spec')
        ? `<h1>Spec</h1><p>Spec v${url.searchParams.get('v')}</p>` : '<h1>QA report</h1><p>Boards show stuck counts ✓</p>'});
      const th = p.match(/^\/api\/v2\/threads\/(c\d)\/timeline$/);
      if (th) return json({thread: {id: th[1], title: th[1] === 'c1' ? 'Set up: Agent Services' : 'Pipeline boards', me: {can_post: true}}, messages: th[1] === 'c1'
        ? [...Array.from({length: 30}, (_, i) => msg('m' + (10 + i), 'bot:asp', `Read part ${i + 1} of the area.`, `2026-11-15T08:${String(10 + i).padStart(2, '0')}:00Z`)),
          msg('m50', 'bot:asp', 'Seven things I could not find written down: 1. Who counts as a user of Search? … 7. Which products are hosted?', '2026-11-15T09:02:00Z')]
        : [msg('m2', 'human:ana', 'Show stuck counts, keep it to one line.', '2026-11-14T10:00:00Z')], next_cursor: null});
      if (r.method() === 'POST' && p.startsWith('/api/v2/')) {
        const body = (/\/files$/.test(p) ? {} : JSON.parse(r.postData() || '{}')) as Record<string, unknown>;   // an upload is a form
        if (!p.startsWith('/api/v2/threads/')) writes.push({p, body});   // a thread's read marks are its own
        const said = p.match(/^\/api\/v2\/tasks\/(t\d)\/comments$/);
        if (said) {
          if (fail[said[1]!]) { fail[said[1]!]!--; return json({error: 'internal', detail: 'Try again'}, 500); }
          return json({message: {id: 'm9'}});
        }
        if (/^\/api\/v2\/tasks\/t\d\/files$/.test(p)) return json({file_id: `f${++uploads}`, version: 1});
        if (p === '/api/v2/tasks/t3/asks/r3/ok') { needs = needs.filter(n => n['requirement_id'] !== 'r3'); return json({task: {id: 't3'}, answer_id: 'a-r3', requirement_id: 'r3'}); }
        const a = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
        if (a) { needs = needs.filter(n => n['requirement_id'] !== a[2]); return json({task: {id: a[1]}, answer_id: 'a-' + a[2], requirement_id: a[2]}); }
        const mv = p.match(/^\/api\/v2\/tasks\/(t\d)\/move$/);
        if (mv) { needs = needs.filter(n => n['task_id'] !== mv[1]); return json({task: {id: mv[1], status: 'closed'}}); }
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

    // ---- 1. An OK: its thread, the ask as the last message, the composer's arrow and no Send button.
    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('[data-need="v2:r2"]').waitFor();
    await page.evaluate('decideFlow(document.body)');
    await chat.getByText('Show stuck counts, keep it to one line.', {exact: false}).waitFor();
    const ask = chat.locator('.dc-msg-ask');
    await ask.waitFor();
    assert.equal(await chat.locator('.thv-list > :last-child .dc-msg-ask, .thv-list > .dc-msg-ask:last-child').count() >= 1, true, 'the ask is the last message');
    assert.match(await ask.locator('.dc-msg-by').innerText(), /^Tasks Product Owner/);
    assert.equal(await ask.locator('.dc-q').innerText(), 'OK the spec?');
    assert.equal(await dialog.locator('[data-dc="sendback"], [data-dc="other"], #dc-notes, #dc-other').count(), 0, 'no Send, no second box');
    assert.equal(await chat.locator('.th-newline').isVisible(), false, 'the day divider only: no "New" line in Flow');

    // Attach offers uploads only: the comment and answer routes carry no library file.
    await chat.locator('.p-attach').click();
    assert.deepEqual(await chat.locator('.thv-attach-menu [role="menuitem"]').allInnerTexts(), ['Upload']);
    await chat.locator('.p-attach').click();

    // ---- 2. An OK: Approve alone; words in the composer add Send back and Close, and Approve takes the words as its note.
    const approve = dialog.locator('.dc-side [data-key] [data-dc="approve"]');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    assert.equal((await approve.innerText()).trim(), 'Approve');
    await shot('ok-empty');
    await composer.fill('Fine, but amber only when more than 3 are stuck');
    await page.waitForFunction(() => document.querySelector('#need-card .dc-side [data-key] > .dc-acts-row')?.classList.contains('dc-with-words'));
    assert.equal(await approve.getAttribute('aria-label'), 'Approve with your comment');
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

    // ---- 2b. Words in the box: Send back, Close and Approve, each with the comment icon, in that order. Send back answers
    // no with the words; Close closes the task with them. On a phone the three stay on one line; Snooze and ⋯ take the next.
    const sides = dialog.locator('.dc-side [data-key] > .dc-acts-row'), shown = sides.locator('.dc-note-btn:visible');
    const note = (dc: string) => sides.locator(`[data-dc="${dc}"]`);
    const okOn = (n: number): Item => ({...ok(), id: `v${n}:r${n}`, task_id: `t${n}`, requirement_id: `r${n}`,
      task: brief(n, 'Boards show stuck counts', 'bot:tpo'), ask: {...(ok().ask as object), requirement_id: `r${n}`}});
    const showOn = async (item: Item) => {
      needs = [item];
      await dialog.locator('[data-dc="close"]').first().click();
      await page.evaluate('v2Refresh()');
      await page.evaluate('decideFlow(document.body)');
      await approve.and(page.locator(':not([disabled])')).waitFor();
    };
    const wordsRow = () => page.waitForFunction(() => document.querySelector('#need-card .dc-side [data-key] > .dc-acts-row')?.classList.contains('dc-with-words'));
    const themed = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
        await page.waitForTimeout(150);
        await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-${theme}.png`)});
      }
      await page.evaluate(() => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set('dark'));
    };

    // Empty: Approve alone; Send back, Close and the comment icon hidden.
    await showOn(okOn(4));
    assert.equal(await note('chat-back').count(), 1, 'an OK whose task the person may answer no to has Send back');
    assert.equal(await note('chat-back').isVisible(), false, 'empty: no Send back');
    assert.equal(await note('close-task').isVisible(), false, 'empty: no Close');
    assert.equal(await approve.getAttribute('aria-label'), 'Approve', 'empty: Approve');
    assert.equal(await approve.locator('.dc-note-ic').isVisible(), false, 'empty: no comment icon');
    await composer.fill('Stuck counts only, no legend');
    await wordsRow();
    assert.deepEqual(await shown.evaluateAll(els => els.map(e => e.getAttribute('data-dc'))), ['chat-back', 'close-task', 'approve'], 'typed: Send back, Close, Approve in that order');
    assert.deepEqual(await shown.evaluateAll(els => els.map(e => e.getAttribute('aria-label'))), ['Send back with your comment', 'Close with your comment', 'Approve with your comment']);
    for (const dc of ['chat-back', 'close-task', 'approve']) {
      const icon = note(dc).locator('.dc-note-ic');
      assert.equal(await icon.innerText(), 'chat_bubble', `${dc} has the comment icon`);
      assert.equal(await icon.isVisible(), true, `${dc}: the icon shows`);
    }
    assert.match((await note('approve').getAttribute('class')) ?? '', /\bprimary\b/, 'Approve is the primary button');
    assert.match((await note('chat-back').getAttribute('class')) ?? '', /\bghost\b/, 'Send back is a ghost button');
    assert.match((await note('close-task').getAttribute('class')) ?? '', /\bghost\b/, 'Close is a ghost button');
    await themed('ok-typed-desktop');

    // On a phone the three stay on one line, each a 44 px target; Snooze and ⋯ take the line under them.
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(450);   // past a phone's calm (decideCalm)
    const boxes = await shown.evaluateAll(els => els.map(e => { const r = e.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, fits: e.scrollWidth <= e.clientWidth}; }));
    assert.equal(boxes.length, 3, 'three buttons');
    assert.ok(boxes.every(b => b.left >= 0 && b.right <= 390), `the three fit a 390 px phone (${JSON.stringify(boxes)})`);
    assert.ok(boxes.every(b => b.fits), `no label runs out of its button (${JSON.stringify(boxes)})`);
    assert.ok(boxes.every(b => Math.abs(b.top - boxes[0]!.top) <= 2 && b.bottom - b.top >= 44), `one line of 44 px targets (${JSON.stringify(boxes)})`);
    const [snooze, more] = [await sides.locator('.dc-snzw').boundingBox(), await dialog.locator('.dc-side [data-key] [data-dc="ask-more"]').boundingBox()];
    assert.ok(snooze && more && snooze.y >= boxes[0]!.bottom - 1 && more.y >= boxes[0]!.bottom - 1 && more.x > snooze.x, `Snooze and ⋯ take the line under them, ⋯ last (${JSON.stringify([snooze, more])})`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'no sideways scroll');
    await themed('ok-typed-phone');

    // Send back: the words are the note of a "no", as the arrow sends them.
    const w = writes.length;
    await note('chat-back').click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes.slice(w), [{p: '/api/v2/tasks/t4/asks/r4/answer', body: {value: 'no', version: 3, note: 'Stuck counts only, no legend'}}]);

    // Close: the words are the reason on the close, which moves the task to closed at its version.
    await showOn(okOn(5));
    await composer.fill('Covered in the boards spec');
    await wordsRow();
    await page.waitForTimeout(450);   // a phone's new item takes no taps for 400 ms (decideCalm)
    const c = writes.length;
    await note('close-task').click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes.slice(c), [{p: '/api/v2/tasks/t5/move', body: {to: 'closed', why: 'Covered in the boards spec', version: 3}}]);

    // ---- 3. On a phone: two tabs, Chat and the document's (open, no chevron). A bot's ❓ opens on its chat, with a
    // full-width box and no "To <name>" line under it, Snooze and ⋯ in the tabs row; an OK opens on its document.
    const chatTab = dialog.locator('[data-dc="tab"][data-dc-tab="chat"]'), taskTab = dialog.locator('[data-dc="tab"][data-dc-tab="task"]');
    needs = [mark()];
    await page.setViewportSize({width: 390, height: 844});
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('v2Refresh()');
    await page.evaluate('decideFlow(document.body)');
    await chat.getByText('Seven things I could not find written down', {exact: false}).waitFor();
    assert.equal(await dialog.locator('[data-dc="tab"][role="tab"]').count(), 0, 'a ❓ on a task with no documents: no tabs, not even Chat');
    assert.equal(await chat.locator('.p-foot, .p-reach').count(), 0, 'no "To <name>" line under the box');
    // Opened on its question, under thirty unread: the question shows, and no Unread pill sits on it.
    await page.waitForFunction(() => {
      const box = document.querySelector('#need-card .dc-chat .thv-scroll'), q = document.querySelector('#need-card .dc-chat [data-mid="m50"]');
      if (!box || !q) return false;
      const b = box.getBoundingClientRect(), r = q.getBoundingClientRect();
      return r.top >= b.top - 1 && r.top < b.bottom;
    });
    assert.equal(await chat.locator('.th-new').count() > 0, true, 'the messages above are unread');
    assert.equal(await chat.locator('.thv-unread').isVisible(), false, 'no Unread pill over the question');
    const [tabsRow, icons, pill] = [await dialog.locator('.dc-tabs').boundingBox(), await dialog.locator('.dc-side [data-key]').boundingBox(), await chat.locator('.p-pill').boundingBox()];
    assert.ok(tabsRow && icons && icons.y >= tabsRow.y - 1 && icons.y + icons.height <= tabsRow.y + tabsRow.height + 1, `Snooze and ⋯ sit in the tabs row (${JSON.stringify([tabsRow, icons])})`);
    assert.ok(pill && pill.width >= 350, `the box is full width (${pill?.width})`);
    if (process.env.FLOW_SHOTS) {
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, 'phone-question-opens-chat.png')});
    }
    needs = [ok()];
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('v2Refresh()');
    await page.evaluate('decideFlow(document.body)');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    assert.equal(await taskTab.getAttribute('aria-selected'), 'true', 'an OK opens on its document tab');
    assert.equal(await dialog.locator('.dc-chat').isVisible(), false, 'the chat waits in its tab');
    await chatTab.click();
    assert.equal(await chatTab.getAttribute('aria-selected'), 'true', 'Chat is selected');
    assert.equal(await dialog.locator('.dc-thing').isVisible(), false, 'Chat: the task is in its own tab');
    const [c, t] = [await chatTab.boundingBox(), await taskTab.boundingBox()];
    assert.ok(c && t && c.height >= 44 && Math.abs(c.y - t.y) <= 1, `the tabs are one row of 44 px targets (${JSON.stringify([c, t])})`);
    // The ask's byline on one line, #N whole.
    const by = chat.locator('.dc-msg-ask .dc-msg-by');
    await by.evaluate(e => { e.querySelector<HTMLElement>('.dc-meta-who')!.textContent = 'A very long name for the bot that asked, longer than a phone line'; });
    const [line, tag] = [await by.boundingBox(), await by.locator('.dc-task-link').boundingBox()];
    assert.ok(line && tag && Math.abs((tag.y + tag.height / 2) - (line.y + line.height / 2)) <= 2 && tag.x + tag.width <= line.x + line.width + 5, `the byline is one line, #N whole (${JSON.stringify([line, tag])})`);
    assert.equal(await by.locator('.dc-meta-who').evaluate(e => e.scrollWidth > e.clientWidth), true, 'the name gives way first');
    await taskTab.click();
    assert.equal(await dialog.locator('.dc-thing').isVisible(), true, 'Task shows the task');
    assert.equal(await dialog.locator('.dc-chat').isVisible(), false, 'and not the chat');
    assert.match(await dialog.locator('.dc-thing').innerText(), /Your area and its products\./, 'its body, open');
    assert.equal(await dialog.locator('.dc-thing details, .dc-thing summary').count(), 0, 'no chevron');
    await chatTab.click();
    // ⋯ is a sheet from the bottom edge: a grab line, full-width rows apart from the page, a dim backdrop over the rest;
    // a tap on the backdrop closes it, and so does Esc.
    await dialog.locator('.dc-side [data-key] [data-dc="ask-more"]').click();
    const sheet = dialog.locator('.dc-askw .dc-snz');
    await sheet.waitFor();
    const look = await sheet.evaluate(e => {
      const r = e.getBoundingClientRect(), grab = getComputedStyle(e, '::before'), dim = getComputedStyle(e.parentElement!, '::before');
      const rows = [...e.querySelectorAll<HTMLElement>('.dc-mi')].map(m => [m.getBoundingClientRect().width, m.getBoundingClientRect().height]);
      return {x: r.left, w: r.width, bottom: r.bottom, grab: [grab.width, grab.height], dim: [dim.position, dim.backgroundColor], rows};
    });
    assert.ok(look.x === 0 && look.w === 390 && look.bottom === 844, `the sheet is full width at the bottom (${JSON.stringify(look)})`);
    assert.deepEqual(look.grab, ['36px', '4px'], 'it has a grab line');
    assert.ok(look.dim[0] === 'fixed' && /rgba\(0, 0, 0, 0\.5\)/.test(look.dim[1]!), `a dim backdrop (${look.dim})`);
    assert.ok(look.rows.every(([w, h]) => w === 390 && h! >= 52), `full-width 52 px rows (${JSON.stringify(look.rows)})`);
    if (process.env.FLOW_SHOTS) {
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      await page.screenshot({path: require('node:path').join(process.env.FLOW_SHOTS, 'more-sheet-phone.png')});
    }
    await page.mouse.click(195, 200);
    await sheet.waitFor({state: 'detached'});
    assert.equal(await dialog.isVisible(), true, 'a tap on the backdrop closes the sheet, not Flow');
    await dialog.locator('.dc-side [data-key] [data-dc="ask-more"]').click();
    await sheet.waitFor();
    await page.keyboard.press('Escape');
    await sheet.waitFor({state: 'detached'});
    // Approve with words: the words and a file in the box, put back from a draft, go with the approval as its note.
    await composer.fill('Ship it');
    await chat.locator('.p-file').setInputFiles({name: 'proof.txt', mimeType: 'text/plain', buffer: Buffer.from('ok')});
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('decideFlow(document.body)');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    await page.waitForFunction(() => document.querySelector('#need-card .dc-side [data-key] > .dc-acts-row')?.classList.contains('dc-with-words'));
    const m = writes.length;
    await page.waitForTimeout(450);   // a phone's new item takes no taps for 400 ms (decideCalm)
    await approve.click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes.slice(m).map(w => w.p), ['/api/v2/tasks/t2/files', '/api/v2/tasks/t2/asks/r2/answer']);
    assert.deepEqual(writes.at(-1), {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'ok', version: 3, note: `Ship it\n\nFiles:\n- proof.txt (f${uploads})`}});

    // An OK bound to what it shows: approved, then the words as a comment; one that does not go stays in the box, with
    // why, the approval done, and the arrow sends it.
    const bound = ok(); bound.id = 'v3:r3'; bound['task_id'] = 't3'; bound['requirement_id'] = 'r3';
    (bound['ask'] as Record<string, unknown>)['requirement_id'] = 'r3'; (bound['task'] as Record<string, unknown>)['id'] = 't3';
    needs = [bound];
    fail['t3'] = 1;
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('v2Refresh()');
    await page.evaluate('decideFlow(document.body)');
    await approve.and(page.locator(':not([disabled])')).waitFor();
    // Its task has a Spec and a QA report: Chat, then them, newest change first. What this OK shows is no file of
    // theirs, so it has its own tab, named for what it shows, and opens there; the Spec stays the Spec.
    const tabs = dialog.locator('.dc-tabs [role="tab"]');
    const frameText = async () => (await dialog.locator('.dc-side > .dc-docpane iframe.html-view').contentFrame().locator('body').innerText()).trim();
    await page.waitForFunction(() => document.querySelectorAll('.decide .dc-tabs [role="tab"]').length === 4);
    assert.deepEqual(await tabs.allInnerTexts(), ['Chat', 'Launch email', 'QA report', 'Spec']);
    assert.equal(await taskTab.getAttribute('aria-selected'), 'true', 'an OK opens on what it shows');
    assert.match(await dialog.locator('.dc-thing').innerText(), /Hello\./, 'its own tab shows what it approves');
    if (process.env.TICO_SHOTS) {
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      await page.waitForTimeout(450);   // past a phone's calm (decideCalm)
      for (const theme of ['dark', 'light']) {
        await page.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
        await page.waitForTimeout(100);
        await page.screenshot({path: path.join(process.env.TICO_SHOTS, `flow-approval-own-tab-phone-${theme}.png`)});
      }
      await page.evaluate(() => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set('dark'));
    }
    const specTab = dialog.locator('[data-dc-tab="doc:Spec"]');
    await specTab.click();
    assert.equal(await dialog.locator('.dc-thing').isVisible(), false, 'the Spec tab is the Spec, not the approval');
    await dialog.locator('.dc-side > .dc-docpane iframe.html-view').contentFrame().locator('body', {hasText: 'Spec v3'}).waitFor();
    assert.match(await frameText(), /Spec v3/, 'the Spec document, newest version');
    assert.match(await dialog.locator('.dc-side > .dc-docpane [data-doc-ver]').innerText(), /^v3 · Tasks Product Owner/);
    await taskTab.click();
    // An OK whose page is the Spec's own file opens on the Spec tab: the document itself, with its version line.
    needs = [{...bound, id: 'v4:r3', ask: {...(bound['ask'] as object), page: {file_id: 'f-spec', ref: 'f-spec@3', version: 3}}}];
    await dialog.locator('[data-dc="close"]').first().click();
    await page.evaluate('v2Refresh()');
    await page.evaluate('decideFlow(document.body)');
    await page.waitForFunction(() => document.querySelector('.decide [data-dc-tab="doc:Spec"]')?.getAttribute('aria-selected') === 'true');
    assert.deepEqual(await tabs.allInnerTexts(), ['Chat', 'QA report', 'Spec']);
    assert.equal(await taskTab.count(), 0, 'no second tab for the same file');
    await dialog.locator('.dc-side > .dc-docpane iframe.html-view').contentFrame().locator('body', {hasText: 'Spec v3'}).waitFor();
    assert.match(await frameText(), /Spec v3/);
    assert.match(await dialog.locator('.dc-side > .dc-docpane [data-doc-ver]').innerText(), /^v3 · /);
    await approve.and(page.locator(':not([disabled])')).waitFor();
    if (process.env.TICO_SHOTS) {
      await dialog.locator('.dc-start').waitFor({state: 'detached'});
      await page.waitForTimeout(450);   // past a phone's calm (decideCalm): Approve as it is to tap
      for (const theme of ['dark', 'light']) {
        await page.evaluate(t => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set(t), theme);
        await page.waitForTimeout(100);
        await page.screenshot({path: path.join(process.env.TICO_SHOTS, `flow-approval-on-its-document-phone-${theme}.png`)});
      }
      await page.evaluate(() => (window as unknown as {ticoTheme?: {set(t: string): void}}).ticoTheme?.set('dark'));
    }
    // Another document: its one version line over it.
    await dialog.locator('[data-dc-tab="doc:QA report"]').click();
    await dialog.locator('.dc-docpane iframe.html-view').waitFor();
    assert.match(await dialog.locator('.dc-docpane [data-doc-ver]').innerText(), /^v1 · Tasks Product Owner · 20m/);
    assert.equal(await dialog.locator('.dc-thing').isVisible(), false);
    await chatTab.click();
    await composer.fill('Send it Monday');
    await page.waitForTimeout(450);
    const k = writes.length;
    await approve.click();
    await dialog.locator('.err', {hasText: 'Approved. Your comment did not go'}).waitFor();
    assert.deepEqual(writes.slice(k).map(w => w.p), ['/api/v2/tasks/t3/asks/r3/ok', '/api/v2/tasks/t3/comments']);
    assert.equal(await composer.inputValue(), 'Send it Monday', 'the words stay');
    assert.equal(await approve.count(), 0, 'approved: no Approve left');
    await arrow.click();
    await dialog.getByText('All done').waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/tasks/t3/comments', body: {text: 'Send it Monday'}});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no sideways scroll');

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: an OK in its task\'s chat: the ask as the last message; the arrow sends it back; words add Send back, Close and Approve; ⋯; a phone\'s Chat and Task tabs.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
