// A task page's Answer opens that task's own ask on the Needs you card. Needs you items are keyed `<ask id>:<requirement
// id>` (GET /api/v2/needs-you items[].id), and a stage's requirement id is the same on every task in the stage, so Answer
// matches the task and the requirement, never the requirement alone:
// - two tasks ask one stage requirement: Answer on the second task's page opens the second task's ask;
// - an ask not yet in the held list: Answer reads Needs you once more, then opens it;
// - an ask past the first page of Needs you: Answer reads on, page by page, then opens it;
// - an ask that is not in the list at all: Answer says why, never nothing.
// Fixtures only (tasks-page.cts).
import type {Need, Task} from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const RID = 'spec-ok-ana';
const asking = (id: string, title: string): Task => task(id, title, 'spec', {
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: null, until: null, ask: true, requirement_id: RID}});
const need = (t: Task, ask: string, line: string): Need => ({kind: 'ask', id: `${ask}:${RID}`, task_id: t.id, requirement_id: RID, line,
  since: '2026-11-12T07:00:00Z', task: brief(t), ask: {requirement_id: RID, kind: 'answer', line, who: 'human:ana', asked_by: 'bot:pm',
    choices: ['Yes', 'No'], may: ['answer', 'thumbs'], met: false, shows: [], standing: 'waiting', answer: null}});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const a = asking('t-1', 'Checkout flow'), b = asking('t-2', 'Signup plans'), c = asking('t-3', 'Pricing page');
    const needs = [need(a, 'ask-a', 'Is the checkout spec OK?'), need(b, 'ask-b', 'Is the signup spec OK?')];

    // The second of two tasks sharing one requirement id: its own ask opens.
    {
      const {page, errors} = await open(browser, {hash: '#/task/t-2', tasks: [a, b, c], needs});
      try {
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the signup spec OK?', "Answer opens this task's ask, not another task's");
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Not in the held list yet: read once more, then open it.
    {
      let added = false, readsAfter = 0;
      const late = [...needs, need(c, 'ask-c', 'Is the pricing spec OK?')];
      const {page, errors} = await open(browser, {hash: '#/task/t-3', tasks: [a, b, c], needs, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/needs-you', route => {
          const items = added ? late : needs;
          if (added) readsAfter++;
          return route.fulfill({json: {actor: 'human:ana', count: items.length, items, version: added ? 'late' : 'early', next: null}});
        });
      }});
      try {
        added = true;    // the ask reaches the server after the page read Needs you
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.ok(readsAfter >= 1, 'Needs you was read again');
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the pricing spec OK?');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Past the first page: with more than a page of Needs you, the ask that came last is past it. Answer reads on
    // through the pages (the server's limit and cursor) until it finds it.
    {
      let added = false;
      const many = Array.from({length: 60}, (_, i) => asking(`t-${i + 10}`, `Task ${i + 10}`));
      const d = asking('t-99', 'Refund page'), held = many.map((t, i) => need(t, `ask-${i}`, `Is spec ${i} OK?`));
      const all = () => added ? [...held, need(d, 'ask-d', 'Is the refund spec OK?')] : held;
      const {page, errors} = await open(browser, {hash: '#/task/t-99', tasks: [...many, d], needs: held, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/needs-you', route => {
          const q = new URL(route.request().url()).searchParams, items = all(), version = 'v' + items.length;
          const from = Number((q.get('cursor') || '').split('.')[1] || 0), limit = Number(q.get('limit') || items.length);
          const next = from + limit < items.length ? `${version}.${from + limit}` : null;
          return route.fulfill({json: {actor: 'human:ana', count: items.length, items: items.slice(from, from + limit), version, next}});
        });
      }});
      try {
        added = true;
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the refund spec OK?');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Not in the list at all: say why, and the card stays shut.
    {
      const {page, errors} = await open(browser, {hash: '#/task/t-3', tasks: [a, b, c], needs});
      try {
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const said = page.locator('.toast', {hasText: /Needs you/});
        await said.waitFor({timeout: 5000});
        assert.equal(await page.locator('#need-card[open]').count(), 0);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log("PASS: Answer on a task page opens that task's own ask (shared requirement id, after a refresh) or says why it can't.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
