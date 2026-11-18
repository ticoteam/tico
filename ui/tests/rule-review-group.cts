import type * as Tasks from './tasks-page.cts';
import type {Browser, Page} from 'playwright';
// A rule's holds as one review (ui/app/org-rule-holds.ts ruleReviewHTML, backend/org_rule_check.py open_review), on its
// Needs you item: "Held by: <rule>" on one line, the count, the first two held items in brief with the part that broke
// the rule marked (fail tint and a 2px bar) and a line around it, "+N more" and Show each; one filled button, Keep all
// N blocked, on the right, Send all N anyway plain, Edit rule on the left; Keep all and Send all decide only the holds
// drawn (one that joins later stays held); a hold Send all could not send keeps its reason under it. At 375px the
// buttons stack, Keep all full-width at the bottom, with no sideways scroll. The task window heads its cards with the
// same group. Every request is intercepted (tasks-page.cts).
// TICO_SHOTS=<dir> saves the screenshots (1440 and 375, dark and light).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
declare function decideOpen(id?: string): boolean;
declare function ruleReviewTick(): void;
const SHOTS = process.env.TICO_SHOTS || '';
const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();

const RULE_TEXT = "Never share a customer's name or order with anyone outside the team, in any message, comment or question.";
const RULE = {id: 'r-names', kind: 'internal', text: RULE_TEXT, version: 1, status: 'active', if_broken: 'escalate'};
// A Warn rule the same output broke, listed first by an older hold: the card still names the rule that held it.
const WARN = {id: 'r-date', kind: 'internal', text: 'Avoid mentioning the launch date.', version: 1, status: 'active', if_broken: 'warn'};
const LONG = 'The whole note is about Brightway and Lee together. ' + 'More of the same note. '.repeat(40);
const REVIEW = task('t-review', `Held by: ${RULE_TEXT}`, 'todo', {owner_id: 'human:ana', requester_id: 'tico', labels: ['rule-review'],
  body: 'Messages or outputs held by this rule wait for your decision.\n\nHold: h-1', version: 1, created_at: ago(120), updated_at: ago(5)});
const NEED = {kind: 'question', id: 'rq', mark_id: 'q-r', task_id: 't-review', message_id: 'm-r', conversation_id: 'c-t-review',
  root_id: 'c-t-review', from_id: 'bot:ops', requirement_id: null, line: 'Keep them blocked, send them anyway, or edit the rule?',
  since: ago(5), task: {id: 't-review', title: REVIEW.title, status: 'todo', owner_id: 'human:ana', version: 1}};

interface Hold { id: string; text: string; state: string; rules: ((typeof RULE | typeof WARN) & {part: string | null})[]; [field: string]: unknown; }
const hold = (id: string, minutes: number, author: string, to: string, text: string, part: string | null, operation = 'message'): Hold => ({
  id, operation, author: `bot:${author}`, author_name: author === 'ops' ? 'Support Owner' : 'Growth Owner', state: 'held', outcome: 'escalated',
  rules: [{...RULE, part}], text, to: [to], reviewer: 'human:ana', reviewer_name: 'Ana', task_id: 't-review', created_at: ago(minutes),
  decided_at: null, decided_by: null, can_send: true, nonce: `n-${id}`, why: ''});
const HOLDS = (): Hold[] => [
  hold('h-1', 120, 'ops', 'Dana (Acme)', 'Can I share the refund note with Acme?\nIt quotes Lee Park\'s order from Brightway.\nThanks!',
    "It quotes Lee Park's order from Brightway.", 'question'),
  {...hold('h-2', 60 * 24, 'ops', '#partners', "Brightway's renewal is at risk this month. Let's plan a call.", "Brightway's renewal is at risk this month."),
    rules: [{...WARN, part: null}, {...RULE, part: "Brightway's renewal is at risk this month."}]},
  hold('h-3', 30, 'ops', 'Sam', LONG, null),
];
// A review of more holds than one page of the list (50) holds.
const MANY = (): Hold[] => Array.from({length: 53}, (_, n) => hold(`m-${n}`, 60 + n, 'ops', 'Sam', `Note ${n} names Brightway.`, null));

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
}

async function world(browser: Browser, viewport: {width: number; height: number}, theme: string, hash = '#/tasks', start = HOLDS) {
  let holds = start();
  const posted: string[] = [];
  const failing = new Set<string>();
  const routes = async (page: Page) => {
    await page.route(url => url.pathname === '/api/v2/rule-holds', route => {
      // Pages of 50, as the server's: a cursor continues after the last one.
      const at = Number(new URL(route.request().url()).searchParams.get('cursor') || 0);
      const summaries = holds.slice(at, at + 50).map(({text, why, nonce, ...rest}) => ({...rest, appealed: false, rules: rest.rules.map(({part, ...r}) => r)}));
      const next = at + 50 < holds.length ? String(at + 50) : null;
      return route.fulfill({contentType: 'application/json', body: JSON.stringify({holds: summaries, next_cursor: next})});
    });
    await page.route(url => /^\/api\/v2\/rule-holds\/[^/]+$/.test(url.pathname), route => {
      const id = new URL(route.request().url()).pathname.split('/').pop();
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(holds.find(h => h.id === id))});
    });
    await page.route(url => /^\/api\/v2\/rule-holds\/[^/]+\/(keep|send)$/.test(url.pathname), route => {
      const [, id, act] = new URL(route.request().url()).pathname.match(/rule-holds\/([^/]+)\/([^/]+)$/)!;
      posted.push(`${act}:${id}`);
      if (failing.has(id!)) return route.fulfill({status: 409, contentType: 'application/json',
        body: JSON.stringify({error: {code: 'conflict', detail: 'The rule changed since: it was checked again and still breaks it.'}})});
      holds = holds.map(h => h.id === id ? {...h, state: act === 'send' ? 'sent' : 'kept', nonce: undefined, can_send: false} : h);
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(holds.find(h => h.id === id))});
    });
  };
  const {page, errors} = await open(browser, {viewport, theme, hash, tasks: [REVIEW], needs: [NEED] as never, beforeGoto: routes});
  return {page, errors, posted, failing, join: (h: Hold) => { holds = [...holds, h]; }};
}

const group = (page: Page) => page.locator('#need-card[open] .dc-thing .rh-group');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      for (const viewport of [{width: 1440, height: 900}, {width: 375, height: 812}]) {
        const phone = viewport.width < 760;
        const {page, errors, posted, failing, join} = await world(browser, viewport, theme);
        await page.evaluate(() => { decideOpen('rq'); });
        // On a phone a ❓ opens on its Chat tab; the held items are on the Task tab.
        if (phone) await page.locator('#need-card [data-dc="tab"][data-dc-tab="task"]').click();
        const g = group(page);
        await g.locator('.rh-brief').first().waitFor({timeout: t(5000)});
        // The rule on one line, cut with an ellipsis; the count beside it.
        const title = g.locator('.rh-gtitle');
        assert.equal(await title.innerText(), `Held by: ${RULE_TEXT}`);
        assert.ok(await title.evaluate(e => e.scrollWidth > e.clientWidth || e.getBoundingClientRect().height < 30), 'one line');
        assert.equal(await g.locator('.rh-gcount').innerText(), '· 3 holds');
        // Two in brief, the part marked with a line around it, then +1 more.
        assert.equal(await g.locator('.rh-brief').count(), 2);
        const hit = g.locator('[data-rh-brief="h-1"] .rh-hit');
        assert.equal(await hit.innerText(), "It quotes Lee Park's order from Brightway.");
        assert.equal(await hit.evaluate(e => getComputedStyle(e).borderLeftWidth), '2px', 'a bar, not colour alone');
        assert.equal(await g.locator('[data-rh-brief="h-1"] .rh-line').count(), 3, 'the part and one line each side');
        assert.match(await g.locator('[data-rh-brief="h-1"] .rh-who').innerText(), /Support Owner → ❓ Dana \(Acme\)/);
        assert.match(await g.locator('.rh-rest').innerText(), /\+1 more/);
        // One filled button, Keep all, on the right; Send all plain; Edit rule on the left.
        assert.equal(await g.locator('.rh-gacts .primary').innerText(), 'Keep all 3 blocked');
        assert.equal(await g.locator('.rh-gacts button.primary').count(), 1);
        assert.equal(await g.locator('[data-rh-send-all]').innerText(), 'Send all 3 anyway');
        const [edit, send, keep] = await Promise.all(['a.rh-btn', '[data-rh-send-all]', '[data-rh-keep-all]'].map(s => g.locator(s).boundingBox()));
        if (phone) {
          // Stacked, full width, Keep all at the bottom.
          assert.ok(edit!.y < send!.y && send!.y < keep!.y, 'stacked, Keep all last');
          assert.ok(Math.abs(keep!.width - send!.width) <= 1 && keep!.width > viewport.width * 0.6, 'full width');
        } else {
          assert.ok(edit!.x < send!.x && send!.x < keep!.x, 'Edit rule left, Keep all right');
        }
        const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        assert.ok(wide <= 1, `no sideways scroll (${wide}px)`);
        await shot(page, `rule-review-${viewport.width}-${theme}`);
        // Show each: the full cards, the part marked in the whole text, and "The whole text breaks it" where none is.
        await g.locator('[data-rh-each]').click();
        await g.locator('.rh-each .rh-card').nth(2).waitFor();
        assert.equal(await g.locator('.rh-each .rh-card').count(), 3);
        assert.equal(await g.locator('.rh-each [data-hold="h-2"] mark.rh-part').innerText(), "Brightway's renewal is at risk this month.");
        assert.match(await g.locator('.rh-each [data-hold="h-3"]').innerText(), /The whole text breaks it/);
        // The full text is cut at 600 characters, then "…".
        const long = await g.locator('.rh-each [data-hold="h-3"] .rh-msg').innerText();
        assert.ok(long.endsWith('…') && long.length <= 601, `cut at 600 (${long.length})`);
        await shot(page, `rule-review-each-${viewport.width}-${theme}`);
        if (theme === 'dark') {
          // A hold that joins an open card is counted when the list is read again, and on both buttons.
          join(hold('h-4', 1, 'ops', 'Lee', 'Another one about Brightway.', null));
          await page.evaluate(() => { ruleReviewTick(); });
          await page.waitForFunction(() => document.querySelector('#need-card[open] .rh-gcount')?.textContent === '· 4 holds', null, {timeout: t(5000)});
          assert.equal(await group(page).locator('[data-rh-keep-all]').innerText(), 'Keep all 4 blocked');
          // One that joins after the card is drawn is not decided by Keep all.
          join(hold('h-5', 1, 'ops', 'Lee', 'One more about Brightway.', null));
          await group(page).locator('[data-rh-keep-all]').click();
          await page.waitForFunction(() => document.querySelector('#need-card[open] .rh-gcount')?.textContent === '· 1 hold', null, {timeout: t(5000)});
          assert.deepEqual(posted, ['keep:h-1', 'keep:h-2', 'keep:h-3', 'keep:h-4'], 'only the holds shown');
          assert.equal(await group(page).locator('.rh-each [data-hold="h-5"]').count(), 1, 'the one that joined waits');
        } else {
          // Send all: one that cannot be sent stays, its reason under it, and the buttons count what is left.
          failing.add('h-2');
          await g.locator('[data-rh-send-all]').click();
          await page.waitForFunction(() => document.querySelector('#need-card[open] .rh-gcount')?.textContent === '· 1 hold', null, {timeout: t(5000)});
          assert.deepEqual(posted, ['send:h-1', 'send:h-2', 'send:h-3']);
          const left = group(page);
          assert.match(await left.locator('[data-hold="h-2"] .rh-err').first().innerText(), /checked again and still breaks it/);
          assert.equal(await left.locator('[data-rh-keep-all]').innerText(), 'Keep blocked');
        }
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    // More holds than one page: Needs you counts and decides them all.
    {
      const {page, errors, posted} = await world(browser, {width: 1440, height: 900}, 'dark', '#/tasks', MANY);
      await page.evaluate(() => { decideOpen('rq'); });
      const g = group(page);
      await g.locator('.rh-brief').first().waitFor({timeout: t(5000)});
      assert.equal(await g.locator('.rh-gcount').innerText(), '· 53 holds');
      await g.locator('[data-rh-keep-all]').click();
      await page.waitForFunction(() => document.querySelector('#need-card[open] .rh-done') !== null, null, {timeout: t(15000)});
      assert.equal(posted.length, 53, 'every page decided');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // The task window: the same group over its cards.
    const {page, errors} = await world(browser, {width: 1440, height: 900}, 'dark', '#/task/t-review');
    const head = page.locator('#task-modal .rule-holds .rh-group, #task-modal [data-rule-holds] .rh-group');
    await head.waitFor({timeout: t(5000)});
    assert.equal(await head.locator('[data-rh-keep-all]').innerText(), 'Keep all 3 blocked');
    assert.equal(await page.locator('#task-modal .rh-card').count(), 3);
    assert.equal(await page.locator('#task-modal [data-hold="h-1"] mark.rh-part').innerText(), "It quotes Lee Park's order from Brightway.");
    assert.deepEqual(errors, []);
    await page.close();
    console.log('rule review group: brief holds with the part marked, one decision for the holds shown, phone stack, task window: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
