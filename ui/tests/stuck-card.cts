// The stuck card on the ask screen (ui/app/decide.ts, task #392): a task the ladder brought to the person is stuck,
// never their own to-do.
//  - a private task stuck because the next step's person can't see it: its plain line, Let <them> see it, Reassign,
//    Move back and Close (no Done, Snooze or Give to), and what they will see before the press;
//  - the press sends one move to next with let_see naming exactly them;
//  - when the task now waits on someone else, the refusal shows, the list is read again and the card names the new one;
//  - any other task stuck with the person (no one to let in) gets the stuck card's buttons too, with Try again.
// Fixtures only (tasks-page.cts).
import type {Browser, Page, Route} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Need = import('./tasks-page.cts').Need;
type Task = import('./tasks-page.cts').Task;

const STUCK = 'Stuck in Build: Engineer can\'t see this task';
const side = (page: Page) => page.locator('#need-card[open] .dc-side');
const buttons = (page: Page) => side(page).locator('.dc-acts button').evaluateAll(els => els.map(e => e.textContent?.trim()));

/** Ana's private task in Build, stuck with her: `share` is who it waits on to be let in (null: stuck for another reason). */
function stuck(share: string | null, line = STUCK): {tasks: Task[]; needs: Need[]} {
  const t = task('t-9', 'Fix the card', 'build', {private: true, owner_id: 'bot:pm', version: 4,
    turn: {state: 'person', who: 'human:ana', line, since: new Date().toISOString(), until: null, ask: false, requirement_id: null, ...(share ? {share} : {})}});
  const tasks = [...fixtures(), t];
  return {tasks, needs: [{kind: 'task', id: 'task:t-9', task_id: 't-9', line, since: new Date().toISOString(), task: Object.assign(brief(t), {stuck: true})}]};
}

async function letSee(browser: Browser) {
  const {tasks, needs} = stuck('bot:eng');
  const moves: unknown[] = [];
  const {page, errors} = await open(browser, {tasks, needs, hash: '#/decide?stage=build', beforeGoto: async (p: Page) => {
    await p.route('**/api/v2/tasks/t-9/move', async (route: Route) => {
      moves.push(route.request().postDataJSON());
      needs.splice(0);  // it moved on to Review: it left Ana's list
      await route.fulfill({contentType: 'application/json', body: JSON.stringify({task: {...tasks.at(-1), stage_id: 'review'}})});
    });
  }});
  try {
    await side(page).locator('.dc-q').waitFor({timeout: 5000});
    assert.equal(await side(page).locator('.dc-q').innerText(), STUCK);
    assert.deepEqual(await buttons(page), ['Let Engineer see it', 'Reassign', 'Move back', 'Close']);
    assert.equal(await side(page).locator('.dc-gives').innerText(),
      'Engineer will see this task, its conversation and its files, until someone removes them.');
    await side(page).locator('[data-dc="let-see"]').click();
    await page.waitForFunction(() => !document.querySelector('#need-card[open] [data-dc="let-see"]'));
    assert.deepEqual(moves, [{to: 'next', let_see: 'bot:eng', version: 4}], 'one move, letting exactly Engineer in');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
  console.log('stuck card: plain line, Let Engineer see it, what they get, one move with let_see: ok');
}

async function changed(browser: Browser) {
  const {tasks, needs} = stuck('bot:eng');
  const {page, errors} = await open(browser, {tasks, needs, hash: '#/decide?stage=build', beforeGoto: async (p: Page) => {
    await p.route('**/api/v2/tasks/t-9/move', async (route: Route) => {
      // Review 1 is Writer's now: nobody is added, and the list says so.
      const now = 'Stuck in Build: Writer can\'t see this task', t = tasks.at(-1)!;
      t.turn = {...t.turn!, line: now, share: 'bot:writer'};
      needs.splice(0, 1, {...needs[0]!, line: now, task: Object.assign(brief(t), {stuck: true})});
      await route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {code: 'task_changed',
        detail: 'Writer is the one who can\'t see it now. Nobody was added.'}})});
    });
  }});
  try {
    await side(page).locator('[data-dc="let-see"]').click();
    await side(page).locator('[data-dc="let-see"]', {hasText: 'Let Writer see it'}).waitFor({timeout: 5000});
    assert.equal(await side(page).locator('.dc-q').innerText(), 'Stuck in Build: Writer can\'t see this task');
    assert.match(await side(page).locator('.err').innerText(), /Nobody was added/);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
  console.log('stuck card: a changed next person adds nobody and the card names the new one: ok');
}

async function otherStuck(browser: Browser) {
  const {tasks, needs} = stuck(null, 'Stuck in Build');
  const {page, errors} = await open(browser, {tasks, needs, hash: '#/decide?stage=build'});
  try {
    await side(page).locator('.dc-q').waitFor({timeout: 5000});
    assert.deepEqual(await buttons(page), ['Try again', 'Reassign', 'Move back', 'Close'], 'never Done, Snooze or Give to');
    assert.equal(await side(page).locator('.dc-gives').count(), 0);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
  console.log('stuck card: a task stuck with the person for another reason has the stuck buttons: ok');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    await letSee(browser);
    await changed(browser);
    await otherStuck(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
