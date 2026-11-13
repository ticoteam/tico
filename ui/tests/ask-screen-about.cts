// The decision view says what each item is about, where to do it, and lets the person reply (ui/app/decide-about.ts):
// - a person's own task: the left side says who asked and when, then the task's text as Markdown with its links and
//   files; neither its title nor the stage's generic line is there; the heading is the task's title;
// - "Go to Strategy ↗" when the text names an app link, "Go to Finance → Access ↗" for a "Page → Tab" phrase, none
//   otherwise; it closes the view, opens the place, and ▶ picks up at the item it left;
// - "Reply to PM" posts a comment that @mentions PM; the item stays, the box clears and says Sent; a task Tico filed
//   itself reads "Note on the task" and names no one;
// - a long question: a short heading cut under 60 characters with "…", the full question once under it and never on
//   the left; an ask's own short title is the heading when it has one; Ask back posts a comment and keeps the question;
// - the text shows a skeleton until its one read comes, and the next item's text is read ahead;
// - at 1040 px Done · Snooze · Give to… and Skip sit on one line in light and dark; at 390 px the buttons are 44 px tall
//   and Skip is reachable.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, task, brief, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function decideOpen(id?: string): boolean;

const card = (page: Page) => page.locator('#need-card[open]');
const LONG = 'Can you confirm the p95 latency budget for the checkout API is still 300 ms after the cache change, or should the validation use the new 450 ms figure from the load test?';
const at = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

/** Ana's tasks: t-3 from PM with Markdown, a #/strategy link and a file; t-8 filed by Tico naming Finance → Access;
    t-2's question is long. */
function world(over: {title?: string} = {}) {
  const tasks = fixtures();
  const t3 = tasks.find(t => t.id === 't-3')!;
  Object.assign(t3, {requester_id: 'bot:pm', created_at: at(120),
    body: 'Write the **Vision** on the Strategy page (#/strategy).\n\n- one paragraph\n- by Friday',
    attachments: [{id: 'f-brief', name: 'brief.pdf', content_type: 'application/pdf', size: 10}],
    links: [{id: 'l1', kind: 'session', url: 'https://acme.example/session/abc123', title: 'session:abc123'},
      {id: 'l2', kind: 'link', url: 'https://acme.example/brand', title: 'Brand guide'}]});
  tasks.find(t => t.id === 't-2')!.body = 'Perf I31 compares the cache change against the **load test**.';
  tasks.push(task('t-8', 'Finance access for the owner', 'todo', {owner_id: 'human:ana', requester_id: 'keeper',
    body: 'Open Finance → Access and give the Finance Owner Everything.', turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana',
      since: at(5), until: null, ask: false, requirement_id: null}}));
  const needs = needsOf(tasks);
  const q = needs.find(n => n.id === 'r2')!;
  q.line = LONG; q.ask = {...q.ask!, line: LONG, choices: null, ...(over.title ? {title: over.title} : {})};
  needs.push({kind: 'task', id: 'task:t-8', task_id: 't-8', line: 'Finance access for the owner', since: at(5), task: brief(tasks.find(t => t.id === 't-8')!)});
  return {tasks, needs};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A person's own task, at 1040 px, light and dark.
    for (const theme of ['light', 'dark']) {
      const {tasks, needs} = world();
      let release: () => void = () => {};
      const held = new Promise<void>(r => { release = r; });
      const reads: string[] = [];
      const {page, errors, posts} = await open(browser, {tasks, needs, theme, viewport: {width: 1040, height: 760}, beforeGoto: async p => {
        await p.route(u => /^\/api\/v2\/tasks\/t-\d+$/.test(u.pathname), async route => {
          reads.push(new URL(route.request().url()).pathname);
          if (route.request().url().endsWith('/t-3')) await held;
          return route.fallback();
        });
      }});
      try {
        await page.evaluate(() => { decideOpen('task:t-3'); });
        await card(page).waitFor({timeout: 5000});
        const left = card(page).locator('.dc-thing');
        // Until its one read comes: who asked, and a skeleton.
        await left.locator('.skel').waitFor({timeout: 5000});
        release();
        await left.locator('.dc-md').waitFor({timeout: 5000});
        assert.match(await left.locator('.dc-who').innerText(), /^From PM · \dh ago$/);
        assert.equal(await left.locator('.dc-md strong').innerText(), 'Vision', 'the text is Markdown');
        assert.equal(await left.locator('.dc-md li').count(), 2);
        assert.equal(await left.locator('a.dc-ref-chip[href="/api/v2/files/f-brief"]').innerText(), 'brief.pdf', 'its file is a chip');
        assert.deepEqual(await left.locator('.dc-ref-chip').allInnerTexts(), ['Brand guide', 'brief.pdf'], 'a session link is the builder\'s, not a chip');
        const leftText = await left.innerText();
        assert.ok(!leftText.includes('Book the team offsite'), 'the title is not repeated on the left');
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Book the team offsite', 'the heading is the title');
        assert.equal(await card(page).locator('.dc-q').evaluate(el => parseFloat(getComputedStyle(el).fontSize)), 16);
        assert.equal(reads.filter(r => r.endsWith('/t-3')).length, 1, 'one read of the task');
        // The next item that shows a task's text (t-8) is read ahead.
        assert.ok(reads.some(r => r.endsWith('/t-8')), `the next item is read ahead: ${reads.join(', ')}`);

        // The action row: one line, 36 px, Skip after the buttons.
        const row = card(page).locator('.dc-acts-row');
        const boxes = await row.locator('button').evaluateAll(els => els.map(e => { const r = e.getBoundingClientRect(); return {t: e.textContent, y: r.top + r.height / 2, h: r.height}; }));
        assert.deepEqual(boxes.map(b => b.t), ['Done', 'Snooze', 'Give to…', 'Skip']);
        assert.ok(boxes.every(b => Math.abs(b.y - boxes[0]!.y) < 2), `one line: ${JSON.stringify(boxes)}`);
        assert.ok(boxes.slice(0, 3).every(b => Math.abs(b.h - 36) < 1), `36 px buttons: ${JSON.stringify(boxes)}`);
        assert.ok(await row.evaluate(el => el.scrollWidth <= el.clientWidth), 'the row does not overflow');
        const side = (await card(page).locator('.dc-side').boundingBox())!, skipAt = (await row.locator('[data-dc="skip"]').boundingBox())!;
        assert.ok(skipAt.x + skipAt.width <= side.x + side.width, 'Skip ends inside the side pane');

        // Reply to PM: a comment that names PM; the item stays; the box clears and says Sent.
        assert.equal(await card(page).locator('.dc-reply .dc-notes span').innerText(), 'Reply to PM');
        const count = await card(page).locator('.dc-count').innerText();
        await card(page).locator('#dc-reply').fill('What do you mean by Vision?');
        await card(page).locator('[data-dc="reply"]').click();
        await card(page).locator('.dc-sent', {hasText: 'Sent'}).waitFor({timeout: 5000});
        const sent = posts.filter(x => x.p === '/api/v2/tasks/t-3/comments');
        assert.equal(sent.length, 1);
        assert.equal(sent[0]!.body.text, '@pm What do you mean by Vision?');
        assert.equal(await card(page).locator('#dc-reply').inputValue(), '', 'the box clears');
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Book the team offsite', 'the item stays on screen');
        assert.equal(await card(page).locator('.dc-count').innerText(), count, 'and in the walk');

        // Go to Strategy ↗: the view closes on #/strategy; ▶ picks up at this item.
        const go = card(page).locator('[data-dc="go"]');
        assert.equal(await go.innerText(), 'Go to Strategy ↗');
        await go.click();
        await page.waitForFunction(() => location.hash === '#/strategy');
        assert.equal(await card(page).count(), 0, 'the decision view closed');
        // Back on Tasks, ▶ opens at the item Go to left from (t-3 is the last in the walk).
        await page.evaluate(() => { location.hash = '#/tasks'; });
        await page.locator('#task-body .tk-row').first().waitFor({timeout: 5000});
        await page.locator('#task-play').click();
        await card(page).locator('.dc-q', {hasText: 'Book the team offsite'}).waitFor({timeout: 5000});
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A task Tico filed itself: "Note on the task", names no one; "Page → Tab" opens that tab.
    {
      const {tasks, needs} = world();
      const {page, errors, posts} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('task:t-8'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        assert.match(await card(page).locator('.dc-who').innerText(), /^From Tico · /);
        assert.equal(await card(page).locator('.dc-reply .dc-notes span').innerText(), 'Note on the task');
        await card(page).locator('#dc-reply').fill('Done on my side?');
        await card(page).locator('[data-dc="reply"]').click();
        await card(page).locator('.dc-sent').waitFor({timeout: 5000});
        assert.equal(posts.find(x => x.p === '/api/v2/tasks/t-8/comments')!.body.text, 'Done on my side?');
        const go = card(page).locator('[data-dc="go"]');
        assert.equal(await go.innerText(), 'Go to Finance → Access ↗');
        await go.click();
        await page.waitForFunction(() => location.hash.startsWith('#/finance/access'));
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A long question: a short heading, the full question once under it, never on the left. A bot's question has
    // Write to <bot> (ask-screen-bot-ask.cts): the text names the question and it stays open.
    {
      const {tasks, needs} = world();
      const {page, errors, posts} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        const head = await card(page).locator('.dc-q').innerText();
        assert.ok(head.endsWith('…') && head.length <= 61, `a short heading: ${head}`);
        assert.ok(LONG.startsWith(head.slice(0, -1)), `cut from the question: ${head}`);
        assert.equal(await card(page).locator('.dc-full').innerText(), LONG, 'the full question under it');
        const left = await card(page).locator('.dc-thing').innerText();
        assert.ok(!left.includes(LONG.slice(0, 40)), 'the question is not on the left');
        assert.match(await card(page).locator('.dc-who').innerText(), /^Asked by PM · .* · on Signup plans$/);
        assert.equal(await card(page).locator('.dc-thing .dc-md strong').innerText(), 'load test', 'the task text is the background');
        assert.equal(await card(page).locator('[data-dc="go"]').count(), 0, 'no place named, no Go to');
        await card(page).locator('#dc-other').fill('What do you mean by validation?');
        await card(page).locator('[data-dc="write"]').click();
        await page.waitForFunction(() => !document.querySelector<HTMLTextAreaElement>('#dc-other')?.value);
        assert.deepEqual(posts.find(x => x.p === '/api/v2/tasks/t-2/comments')!.body, {text: 'What do you mean by validation?', ask: 'r2'});
        assert.equal(posts.filter(x => x.p.endsWith('/answer')).length, 0, 'Write to the bot answers nothing');
        assert.equal(await card(page).locator('.dc-full').innerText(), LONG, 'the question stays on screen');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // An ask's own short title is the heading.
    {
      const {tasks, needs} = world({title: 'Which latency budget for Perf I31?'});
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('.dc-q', {hasText: 'Which latency budget for Perf I31?'}).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-full').innerText(), LONG);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A reply a team rule holds: the rule shows under the box, as on the task page. "Ask first" waits for a reviewer, so
    // the box clears (a second Send would post it twice); "Stop" keeps the words. Edit and resend puts them back.
    for (const outcome of ['escalated', 'blocked'] as const) {
      const {tasks, needs} = world();
      const words = 'What do you mean by **Vision**?';
      const {page, errors, posts} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/tasks/t-3/comments', route => route.request().method() !== 'POST' ? route.fallback()
          : route.fulfill({contentType: 'application/json', body: JSON.stringify({held: {id: 'h1', outcome, state: 'held', author: 'human:ana',
            text: `@pm ${words}`, rules: [{id: 'ru1', kind: 'internal', text: 'No Markdown in comments'}], can_withdraw: true}})}));
        await p.route(u => u.pathname === '/api/v2/rule-holds/h1/withdraw', route => {
          posts.push({p: '/api/v2/rule-holds/h1/withdraw', body: {}});
          return route.fulfill({contentType: 'application/json', body: '{}'});
        });
      }});
      try {
        await page.evaluate(() => { decideOpen('task:t-3'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        await card(page).locator('#dc-reply').fill(words);
        await card(page).locator('[data-dc="reply"]').click();
        const held = card(page).locator('[data-dc-held]');
        await held.waitFor({timeout: 5000});
        assert.equal(await held.locator('.dc-held-line').innerText(), outcome === 'escalated'
          ? 'Held for a reviewer: a rule asks first. It is posted if they send it anyway.' : 'Not sent: a rule stops it.', outcome);
        assert.equal(await held.locator('.rh-rule').innerText(), 'No Markdown in comments', `${outcome}: the rule is named`);
        assert.equal(await card(page).locator('.dc-sent').count(), 0, `${outcome}: not "Sent"`);
        assert.equal(await card(page).locator('#dc-reply').inputValue(), outcome === 'escalated' ? '' : words, `${outcome}: the box`);
        await held.locator('[data-hold-edit]').click();
        await held.waitFor({state: 'detached', timeout: 5000});
        assert.equal(await card(page).locator('#dc-reply').inputValue(), words, `${outcome}: Edit and resend puts back the words typed, not the comment as sent`);
        assert.ok(posts.some(x => x.p === '/api/v2/rule-holds/h1/withdraw'));
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Send, then › before the answer comes: the next item's box keeps what is typed there, and a reply a rule held
    // shows on its own item when the walk comes back to it.
    for (const outcome of ['blocked', 'escalated', 'sent'] as const) {
      const {tasks, needs} = world();
      let release: () => void = () => {};
      const answered = new Promise<void>(r => { release = r; });
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/tasks/t-3/comments', async route => {
          if (route.request().method() !== 'POST') return route.fallback();
          await answered;
          return route.fulfill({contentType: 'application/json', body: JSON.stringify(outcome === 'sent' ? {comment: {id: 'm1'}, comments: [], woke: true}
            : {held: {id: 'h1', outcome, state: 'held', author: 'human:ana', text: '@pm First reply', rules: [{id: 'ru1', kind: 'internal', text: 'No Markdown in comments'}], can_withdraw: true}})});
        });
      }});
      try {
        await page.evaluate(() => { decideOpen('task:t-3'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        await card(page).locator('#dc-reply').fill('First reply');
        await card(page).locator('[data-dc="reply"]').click();
        await card(page).locator('[data-dc="next"]').click();
        await card(page).locator('.dc-q', {hasText: 'Finance access for the owner'}).waitFor({timeout: 5000});
        await card(page).locator('#dc-reply').fill('typed on the next item');
        const done = page.waitForResponse(r => r.url().endsWith('/api/v2/tasks/t-3/comments'));
        release();
        await done;
        await page.waitForTimeout(100);
        assert.equal(await card(page).locator('#dc-reply').inputValue(), 'typed on the next item', `${outcome}: the next item's box keeps its words`);
        assert.equal(await card(page).locator('[data-dc-held], .dc-sent').count(), 0, `${outcome}: nothing of the first reply shows here`);
        await card(page).locator('[data-dc="prev"]').click();
        await card(page).locator('.dc-q', {hasText: 'Book the team offsite'}).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('[data-dc-held]').count(), outcome === 'sent' ? 0 : 1, `${outcome}: the hold shows on its item`);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A question nobody named asked (a stage's): no Ask back, Send answer stays.
    {
      const {tasks, needs} = world();
      needs.find(n => n.id === 'r2')!.ask!.asked_by = 'stage';
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('[data-dc="other"]').waitFor({timeout: 5000});
        assert.equal(await card(page).locator('[data-dc="askback"]').count(), 0);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A phone: the buttons are 44 px tall, Skip is reachable.
    {
      const {tasks, needs} = world();
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 390, height: 844}});
      try {
        await page.evaluate(() => { decideOpen('task:t-3'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        for (const sel of ['[data-dc="done"]', '[data-dc="snooze"]', '[data-dc="give"]', '[data-dc="skip"]', '[data-dc="reply"]', '[data-dc="go"]']) {
          const box = (await card(page).locator(sel).boundingBox())!;
          assert.ok(box.height >= 44, `${sel} is ${box.height} px tall on a phone`);
        }
        const skip = (await card(page).locator('[data-dc="skip"]').boundingBox())!;
        assert.ok(skip.y + skip.height <= 844, 'Skip is in view');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) <= 1, 'nothing scrolls sideways');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('PASS: the task text, Go to, the reply and Ask back, short headings, read ahead and a one-line action row.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
