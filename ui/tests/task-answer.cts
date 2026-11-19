// A task page's Answer opens that task's own ask on the Needs you card. Needs you items are keyed `<ask id>:<requirement
// id>` (GET /api/v2/needs-you items[].id), and a stage's requirement id is the same on every task in the stage, so Answer
// matches the task and the requirement, never the requirement alone:
// - two tasks ask one stage requirement: the second task opens on its own ask, and Answer on its page (Open task ↗)
//   comes back to that ask;
// - an ask not yet in the held list: Answer reads that one ask by its id (?ids=<visit>:<requirement>, the task's
//   visit_id), not the list, then opens it;
// - an ask past the first page of Needs you: the same, with no page of the list read;
// - an ask that is not in the list at all: Answer says why, never nothing.
// Fixtures only (tasks-page.cts).
import type {Need, Task} from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
type Route = import('playwright').Route;
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const RID = 'spec-ok-ana';
const asking = (id: string, title: string, visit = ''): Task => task(id, title, 'spec', {
  ...(visit ? {visit_id: visit} : {}), turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: null, until: null, ask: true, requirement_id: RID}});
const need = (t: Task, ask: string, line: string): Need => ({kind: 'ask', id: `${ask}:${RID}`, task_id: t.id, requirement_id: RID, line,
  since: '2026-11-12T07:00:00Z', task: brief(t), ask: {requirement_id: RID, kind: 'ok', line, who: 'human:ana', asked_by: 'bot:pm',
    may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null}});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const a = asking('t-1', 'Checkout flow'), b = asking('t-2', 'Signup plans'), c = asking('t-3', 'Pricing page', 'ask-c');
    /** Needs you as the server answers it: `ids=` only those items, with their places; else the list, a page with `limit`. */
    const needsRoute = (route: Route, items: Need[], seen: (kind: 'ids' | 'page' | 'whole') => void) => {
      const q = new URL(route.request().url()).searchParams, version = 'v' + items.length, ids = q.get('ids');
      if (ids) {
        const want = ids.split(','), got = items.filter(it => want.includes(it.id));
        seen('ids');
        return route.fulfill({json: {actor: 'human:ana', count: items.length, version, items: got,
          positions: Object.fromEntries(got.map(it => [it.id, items.indexOf(it)]))}});
      }
      const from = Number((q.get('cursor') || '').split('.')[1] || 0), limit = Number(q.get('limit') || items.length);
      const next = from + limit < items.length ? `${version}.${from + limit}` : null;
      seen(q.get('cursor') ? 'page' : 'whole');
      return route.fulfill({json: {actor: 'human:ana', count: items.length, items: items.slice(from, from + limit), version, next}});
    };
    const needs = [need(a, 'ask-a', 'Is the checkout spec OK?'), need(b, 'ask-b', 'Is the signup spec OK?')];

    // The second of two tasks sharing one requirement id: its own ask opens.
    {
      const {page, errors} = await open(browser, {hash: '#/task/t-2', tasks: [a, b, c], needs});
      try {
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the signup spec OK?', "the task opens on its own ask, not another task's");
        await card.locator('[data-dc="task"]').click();
        await page.locator('#task-modal[open] [data-task-answer]').click();
        await card.waitFor({timeout: 5000});
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the signup spec OK?', "Answer opens this task's ask, not another task's");
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Not in the held list yet: that one ask is read by its id, then opened. The task opened on its page first (its
    // ask, read by its id as it opened, was not there yet), so Answer is on screen.
    {
      let added = false, firstRead = () => {};
      const read = new Promise<void>(r => { firstRead = r; }), after: string[] = [];
      const late = [...needs, need(c, 'ask-c', 'Is the pricing spec OK?')];
      const {page, errors} = await open(browser, {hash: '#/task/t-3', tasks: [a, b, c], needs, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/needs-you', route => needsRoute(route, added ? late : needs, kind => {
          if (added) after.push(kind); else if (kind === 'ids') firstRead();
        }));
      }});
      try {
        await read;
        added = true;    // the ask reaches the server after the task opened
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.deepEqual(after, ['ids'], 'only that ask was read, by its id');
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the pricing spec OK?');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Past the first page: with more than a page of Needs you, the ask that came last is past it. Answer reads it by
    // its id, and no page of the list.
    {
      let added = false, firstRead = () => {};
      const read = new Promise<void>(r => { firstRead = r; }), after: string[] = [];
      const many = Array.from({length: 60}, (_, i) => asking(`t-${i + 10}`, `Task ${i + 10}`));
      const d = asking('t-99', 'Refund page', 'ask-d'), held = many.map((t, i) => need(t, `ask-${i}`, `Is spec ${i} OK?`));
      const all = () => added ? [...held, need(d, 'ask-d', 'Is the refund spec OK?')] : held;
      const {page, errors} = await open(browser, {hash: '#/task/t-99', tasks: [...many, d], needs: held, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/needs-you', route => needsRoute(route, all(), kind => {
          if (added) after.push(kind); else if (kind === 'ids') firstRead();
        }));
      }});
      try {
        await read;
        added = true;
        await page.locator('#task-modal[open] [data-task-answer]').click();
        const card = page.locator('#need-card[open]');
        await card.waitFor({timeout: 5000});
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the refund spec OK?');
        assert.deepEqual(after, ['ids'], 'no page of the list was read');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Opened while its ask is past the pages held: the task's window comes at once, and the ask, read by its id, comes
    // over it.
    {
      let answerIds = () => {};
      const gate = new Promise<void>(r => { answerIds = r; });
      const withC = [...needs, need(c, 'ask-c', 'Is the pricing spec OK?')];
      const {page, errors} = await open(browser, {hash: '#/task/t-3', tasks: [a, b, c], needs, settle: false, beforeGoto: async p => {
        await p.route(u => u.pathname === '/api/v2/needs-you', async route => {
          if (new URL(route.request().url()).searchParams.get('ids')) await gate;   // the server is slow with it
          return needsRoute(route, new URL(route.request().url()).searchParams.get('ids') ? withC : needs, () => {});
        });
      }});
      try {
        await page.locator('#task-modal[open] .tmodal-title').waitFor();
        assert.equal(await page.locator('#need-card[open]').count(), 0, 'the task window is up before its ask is read');
        answerIds();
        const card = page.locator('#need-card[open]');
        await card.waitFor();
        assert.equal(await card.locator('.dc-q').innerText(), 'Is the pricing spec OK?', 'its ask came over the window');
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
