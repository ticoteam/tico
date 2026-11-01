// Offline browser regression: a bot-owned task talks in its own room. In the bot's main chat a task's start and its
// milestone reports are one quiet row each, and a tap opens the task's thread; a "What needs you" row opens the same
// thread, and what is typed there goes to the task (POST /v2/tasks/{id}/comments), never to the main chat.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const SHOTS = process.env.TICO_UI_SHOTS || '';

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const asked = {id: 't-key', title: 'Pick the API key for staging', owner: 'bot:botops', requester: 'human:ana', status: 'waiting',
  lane: 'company', version: 3, created_at: at(90), updated_at: at(5), ask: {to_actor: 'human:ana', body: 'Which key should staging use?'}};
const docs = {id: 't-docs', title: 'Ship the docs refresh', owner: 'bot:botops', requester: 'human:ana', status: 'doing',
  lane: 'company', version: 2, created_at: at(60), updated_at: at(10)};
const tasks = {[asked.id]: asked, [docs.id]: docs};
const messages = [
  {id: 'm1', conversation_id: 'c1', from_actor: 'human:ana', to_actor: 'bot:botops', kind: 'say', body: 'Refresh the docs please', created_at: at(61)},
  {id: 'm2', conversation_id: 'c1', from_actor: 'bot:botops', to_actor: 'human:ana', kind: 'say', in_reply_to: 'm1', body: 'On it.', created_at: at(60)},
  {id: 'm3', conversation_id: 'c1', from_actor: 'bot:botops', to_actor: 'human:ana', kind: 'say', body: 'Started task: Ship the docs refresh',
    refs: {task: 't-docs', task_started: true}, created_at: at(59)},
  {id: 'm4', conversation_id: 'c1', from_actor: 'bot:botops', to_actor: 'human:ana', kind: 'say', body: 'Blocked: the docs build needs a staging key.',
    refs: {task: 't-docs', task_report: 'blocked'}, ref_tasks: {'t-docs': {title: 'Ship the docs refresh'}}, created_at: at(10)},
];
const comments = {
  't-key': [{id: 'k1', conversation_id: 'r-key', from_actor: 'bot:botops', to_actor: 'human:ana', kind: 'ask', body: 'Which key should staging use?', created_at: at(5)}],
  // A long thread: it opens at its newest line, like the chat.
  't-docs': [...Array.from({length: 30}, (_, i) => ({id: 'e' + i, conversation_id: 'r-docs', from_actor: 'bot:botops', kind: 'say',
    body: `Build step ${i + 1} of the docs refresh ran.`, created_at: at(50 - i)})),
    {id: 'd1', conversation_id: 'r-docs', from_actor: 'bot:botops', kind: 'say', body: 'Docs build fails without a staging key.', created_at: at(10)}],
};

async function run(viewport, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block'});
    const errors = [], chatSends = [], taskSends = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']}] : []});
      if (p === '/api/v2/conversations/c1/snapshot' || p === '/api/v2/conversations/c1/messages') return json({messages, execution: null});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/chat/botops') { chatSends.push(req.postDataJSON().text); return json({error: {detail: 'not here'}}, 500); }
      if (p === '/api/v2/tasks') {
        const owned = url.searchParams.get('owner') === 'botops';
        return json({tasks: owned ? Object.values(tasks) : []});
      }
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)(\/comments)?$/);
      if (one && tasks[one[1]]) {
        const id = one[1];
        if (one[2] && req.method() === 'POST') {
          const text = req.postDataJSON().text;
          taskSends.push({id, text});
          const msg = {id: 'c' + taskSends.length, conversation_id: 'r-' + id, from_actor: 'human:ana', kind: 'say', body: text, created_at: new Date().toISOString()};
          comments[id].push(msg);
          return json({comment: msg, comments: comments[id], woke: true});
        }
        return json({task: {...tasks[id], conversation_id: 'r-' + id}, comments: comments[id], events: [], can_comment: true, children: []});
      }
      if (p.startsWith('/api/v2/tasks/') && p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    await page.waitForFunction(() => V2C?.loaded && document.querySelector('#conv-thread .chat-task-row'));

    // The main chat folds the task's start and its report to one quiet row each; its report text is not a bubble.
    const thread = page.locator('#conv-thread');
    const rows = thread.locator('.chat-task-row');
    assert.equal(await rows.count(), 2, 'one row for the start, one for the report');
    assert.match(await rows.nth(0).innerText(), /Ship the docs refresh\s*· started/);
    assert.match(await rows.nth(1).innerText(), /Ship the docs refresh\s*· blocked/);
    assert.equal(await thread.locator('.bubble', {hasText: 'docs build needs a staging key'}).count(), 0, 'the report is not carried as a message');
    assert.equal(await thread.locator('.bubble', {hasText: 'On it.'}).count(), 1, 'the conversation itself is unchanged');

    // Four or more task rows in a row fold to the oldest, "...N more..." and the newest; a real message starts a new run.
    const folded = await page.evaluate(() => {
      const row = (n, state = 'done') => ({id: 'r' + n, from_actor: 'keeper', to_actor: 'human:ana', kind: 'notice', body: `Review ${n}: ${state}.`,
        refs: {task: 't' + n, task_report: state}, ref_tasks: {['t' + n]: {title: `Review ${n}`}}, created_at: new Date(Date.now() - (60 - n) * 60000).toISOString()});
      const say = {id: 'say', from_actor: 'bot:botops', to_actor: 'human:ana', kind: 'say', body: 'Halfway there.', created_at: new Date().toISOString()};
      const lines = list => { const el = document.createElement('div'); el.innerHTML = v2MessagesHTML(list, null, 'botops');
        return [...el.querySelectorAll('.chat-task-row, .conv-run')].map(r => r.matches('.chat-task-more') ? r.textContent.trim()
          : r.querySelector('.ct-title')?.textContent || 'message'); };
      return {five: lines([1, 2, 3, 4, 5].map(n => row(n))), three: lines([1, 2, 3].map(n => row(n))),
        split: lines([row(1), row(2), say, row(3), row(4), row(5)])};
    });
    assert.deepEqual(folded.five, ['Review 1', '...3 more...', 'Review 5'], 'five rows show the oldest, the hidden count and the newest');
    assert.deepEqual(folded.three, ['Review 1', 'Review 2', 'Review 3'], 'three rows show unchanged');
    assert.deepEqual(folded.split, ['Review 1', 'Review 2', 'message', 'Review 3', 'Review 4', 'Review 5'], 'a message breaks the run');

    if (shot) await page.screenshot({path: shot.replace('.png', '-chat.png')});
    // A tap on the report opens the task's own thread: its header and its conversation, Back to the chat.
    await rows.nth(1).click();
    const modal = page.locator('#task-modal.thread[open]');
    await modal.locator('.task-comments', {hasText: 'Docs build fails without a staging key.'}).waitFor();
    assert.equal(await modal.locator('.tthread-title').innerText(), 'Ship the docs refresh');
    assert.ok(await modal.locator('[data-thread-back]').isVisible(), 'Back shows');
    assert.ok(!(await modal.locator('.task-side').isVisible()), 'the properties wait behind Details');
    await page.waitForFunction(() => { const t = document.querySelector('#task-modal .task-comments'); return t.scrollHeight > t.clientHeight && t.scrollHeight - t.scrollTop - t.clientHeight < 4; });
    // The box is the main chat's composer: a one-line pill with the send arrow.
    const box = modal.locator('.task-pill .p-text');
    assert.equal(await box.getAttribute('placeholder'), 'Message…');
    assert.equal(await box.getAttribute('rows'), '1');
    assert.ok(await modal.locator('.task-pill .p-send-icon').isVisible(), 'the send arrow');
    await modal.locator('[data-thread-back]').click();
    await page.waitForFunction(() => !document.querySelector('#task-modal')?.open);

    // A "What needs you" row (above the chat where chat and tasks do not sit side by side; beside them the rail lists
    // the tasks) opens that task's thread, and a message sent there goes to the task.
    const split = await page.evaluate(() => document.querySelector('#main').classList.contains('bot-split-layout'));
    if (split) {
      await rows.nth(1).click();
      await modal.locator('.task-comments', {hasText: 'Docs build fails'}).waitFor();
    } else {
      const banner = page.locator('#bot-chat-tasks [data-bot-chat-task="t-key"]');
      await banner.waitFor();
      await banner.click();
      await modal.locator('.task-comments', {hasText: 'Which key should staging use?'}).waitFor();
      assert.equal(await modal.locator('.tthread-title').innerText(), 'Pick the API key for staging');
    }
    const id = split ? 't-docs' : 't-key';
    if (shot) { await page.waitForTimeout(600); await page.screenshot({path: shot}); }
    // Return sends on a computer; the arrow sends anywhere.
    await modal.locator('.task-pill .p-text').fill('Use the staging key in the vault');
    if (split) await page.keyboard.press('Enter'); else await modal.locator('.task-pill .p-send').click();
    await modal.locator('.task-comments', {hasText: 'Use the staging key in the vault'}).waitFor();
    assert.deepEqual(taskSends, [{id, text: 'Use the staging key in the vault'}], 'posted to the task');
    assert.deepEqual(chatSends, [], 'nothing went to the main chat');

    // Details shows the full task in place.
    await modal.locator('[data-thread-details]').click();
    await page.waitForFunction(() => !document.querySelector('#task-modal').classList.contains('thread'));
    assert.ok(await page.locator('#task-modal .task-side').isVisible() || viewport.width < 761, 'the full task shows');
    assert.deepEqual(errors, []);
    return split;
  } finally { await browser.close(); }
}

(async () => {
  const shot = name => SHOTS ? path.join(SHOTS, name) : '';
  assert.equal(await run({width: 1200, height: 800}, shot('task-thread-desktop.png')), true, 'desktop: chat and tasks side by side');
  assert.equal(await run({width: 390, height: 844}, shot('task-thread-mobile.png')), false, 'phone: the banner path');
  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
