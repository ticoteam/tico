// The decision view says what a question is about and where to do it (ui/app/decide-about.ts):
// - a long question: itself once, in full, never on the left, its task the background there; a reply in its task's chat
//   posts a comment naming it and answers nothing (ask-screen-question.cts has the rest of a question's screen);
// - "Go to Strategy ↗" when its task's text names an app link, none otherwise; it closes the view, opens the place, and
//   ▶ picks up at the item it left;
// - a question a stage asked: answered in the chat, with Close task and Give to… in ⋯.
// Needs you holds no task only because it is the person's (backend/waiting_on.py needs), so Flow has no layout for one.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function decideOpen(id?: string): boolean;

const card = (page: Page) => page.locator('#need-card[open]');
const LONG = 'Can you confirm the p95 latency budget for the checkout API is still 300 ms after the cache change, or should the validation use the new 450 ms figure from the load test?';

/** Ana's questions and OK (tasks-page.cts needsOf, without its task of her own): t-2's question is long. */
function world(over: {title?: string; body?: string} = {}) {
  const tasks = fixtures();
  tasks.find(t => t.id === 't-2')!.body = over.body ?? 'Perf I31 compares the cache change against the **load test**.';
  const needs = needsOf(tasks).filter(n => n.kind !== 'task');
  const q = needs.find(n => n.id === 'r2')!;
  q.line = LONG; q.ask = {...q.ask!, line: LONG, choices: null, ...(over.title ? {title: over.title} : {})};
  return {tasks, needs};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A long question: itself once, in full, never on the left; the task's text is the background there. A reply in its
    // task's chat writes to the bot that asked, naming the question, which stays open, with the bot.
    for (const title of ['', 'Which latency budget for Perf I31?']) {
      const {tasks, needs} = world({title});
      const {page, errors, posts} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), LONG, 'the question in full, never its short title');
        const left = await card(page).locator('.dc-thing').innerText();
        assert.ok(!left.includes(LONG.slice(0, 40)), 'the question is not on the left');
        assert.match(await card(page).locator('.dc-ask-meta').evaluate(el => el.textContent || ''), /^PM·.+#\d+ ↗$/);
        assert.equal(await card(page).locator('.dc-thing .dc-md strong').innerText(), 'load test', 'the task text is the background');
        assert.equal(await card(page).locator('[data-dc="go"]').count(), 0, 'no place named, no Go to');
        await card(page).locator('.dc-chat .p-text').fill('What do you mean by validation?');
        await card(page).locator('.dc-chat .p-send').click();
        await page.waitForFunction(() => !document.querySelector<HTMLTextAreaElement>('.dc-chat .p-text')?.value);
        assert.deepEqual(posts.find(x => x.p === '/api/v2/tasks/t-2/comments')!.body, {text: 'What do you mean by validation?', ask: 'r2'});
        assert.equal(posts.filter(x => x.p.endsWith('/answer')).length, 0, 'a reply answers nothing');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Go to Strategy ↗ on a typed question (no chat of its own): its task's text names #/strategy. The view closes on
    // it; ▶ picks up at this item.
    {
      const {tasks, needs} = world({body: 'Write the **Vision** on the Strategy page (#/strategy) first.'});
      const q = needs.find(n => n.id === 'r2')!;
      q.ask = {...q.ask!, typed: {name: 'budget', type: 'number', min: 0, max: 1000, question: LONG}};
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('.dc-md').waitFor({timeout: 5000});
        const go = card(page).locator('[data-dc="go"]');
        assert.equal(await go.innerText(), 'Go to Strategy ↗');
        await go.click();
        await page.waitForFunction(() => location.hash === '#/strategy');
        assert.equal(await card(page).count(), 0, 'the decision view closed');
        await page.evaluate(() => { location.hash = '#/tasks'; });
        await page.locator('#task-body .tk-row').first().waitFor({timeout: 5000});
        await page.locator('#task-play').click();
        await card(page).waitFor({timeout: 5000});
        await page.waitForFunction('typeof DEC !== "undefined" && !!DEC && DEC.at === "r2"', null, {timeout: 5000});
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A question nobody named asked (a stage's): answered in the chat; ⋯ holds Close task and Give to….
    {
      const {tasks, needs} = world();
      needs.find(n => n.id === 'r2')!.ask!.asked_by = 'stage';
      const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
      try {
        await page.evaluate(() => { decideOpen('r2'); });
        await card(page).locator('.dc-chat .p-text').waitFor({timeout: 5000});
        await card(page).locator('[data-dc="ask-more"]').click();
        assert.deepEqual(await card(page).locator('.dc-askw [role="menuitem"]').allInnerTexts(), ['Close task', 'Give to…']);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    console.log('PASS: a question in full with its task as the background, Go to, and a stage\'s question in the chat.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
