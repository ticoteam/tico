// A review or QA subtask's verdict (ui/app/task-code.js taskVerdictHTML) and refusals said where they were tried.
// Guards: the chip shows on the row, in the parent's Subtasks and in the subtask's own properties, with the judged head
// as 7 characters, good and bad in different colours, and nothing for a subtask with no verdict; a refused subtask
// ("already open") reads under the field it was typed in, the title kept; a refused status change reads on its row,
// also in a narrow list where row notes hide; a person's QA waiver reads "QA waived", never as a pass. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = mins => new Date(Date.now() - mins * 60000).toISOString();
const t = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:reviewer', requester: 'bot:lead', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3,
  created_at: at(600), updated_at: at(30), verdict: null, verdict_head: null, ...over});
const ALREADY = 'Review 1 is already open on this ticket for this head: task t-r1, with reviewer. Wait for its verdict.';
const REVIEWING = 'You are reviewing this ticket, so you don\'t move it. Finish your review task t-r1 with your verdict.';
const baseTasks = () => [
  t('t-dev', 'Dev 99: fix the checkout total', {owner: 'bot:builder', children_summary: {total: 3, done: 0, direct_total: 3, direct_done: 0}, parts: {total: 3, done: 0}}),
  t('t-r1', 'Review 1: PR #12', {_parent: 't-dev', status: 'review', verdict: 'changes', verdict_head: 'a1b2c3d4e5f6a7b8', updated_at: at(20)}),
  t('t-qa', 'QA: PR #12', {_parent: 't-dev', status: 'review', verdict: 'pass', verdict_head: '0f9e8d7c6b5a4938', updated_at: at(22)}),
  t('t-r2', 'Review 2: PR #12', {_parent: 't-dev', status: 'review', updated_at: at(24)}),
];
// Grouped by owner, so each row carries its status chip (a status grouping says it in the heading instead).
const local = {'hub.tasks.layout': JSON.stringify({group: {list: 'owner', foryou: 'requester'}})};
const refuse = (page, path, method, status, detail, code) => page.route('**' + path, route => route.request().method() === method
  ? route.fulfill({status, contentType: 'application/json', body: JSON.stringify({error: {detail, code}})}) : route.fallback());

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors} = await open(browser, {hash: '#/tasks?view=list', baseTasks: baseTasks(), local});
      const chip = key => page.locator(`#task-body [data-task-key="${key}"] [data-verdict]`);
      await chip('tt-r1').waitFor();
      assert.equal(await chip('tt-r1').locator('[data-verdict-head]').innerText(), 'a1b2c3d');
      assert.equal(await chip('tt-qa').getAttribute('data-verdict'), 'pass');
      assert.equal(await chip('tt-r2').count(), 0, 'no verdict, no chip');
      assert.equal(await chip('tt-dev').count(), 0);
      const bg = key => chip(key).evaluate(el => getComputedStyle(el).backgroundColor);
      assert.notEqual(await bg('tt-r1'), await bg('tt-qa'), 'changes and pass read differently');
      // The parent's Subtasks: the same chip on each judged child.
      const peek = page.locator('#task-peek');
      await page.locator('[data-task-key="tt-dev"] .tl-title').click();
      await peek.locator('[data-sub="t-r1"] [data-verdict="changes"]').waitFor();
      assert.equal(await peek.locator('[data-sub="t-r2"] [data-verdict]').count(), 0);
      // A second Review 1 for the same head: the server's refusal under the field, the title kept to fix.
      await refuse(page, '/api/v2/tasks', 'POST', 409, ALREADY, 'duplicate');
      await peek.locator('[data-sub-add] input[name=title]').fill('Review 1: PR #12');
      await peek.locator('[data-sub-add] input[name=title]').press('Enter');
      await peek.locator('[data-sub-add-error]', {hasText: 'already open'}).waitFor();
      assert.equal(await peek.locator('[data-sub-add] input[name=title]').inputValue(), 'Review 1: PR #12');
      assert.equal(await page.locator('.toast', {hasText: 'already open'}).count(), 0, 'not a toast');
      // The subtask's own properties carry the verdict too.
      await peek.locator('[data-sub-open="t-qa"]').click();
      await peek.locator('[data-prop-row="verdict"] [data-verdict="pass"]').waitFor();
      await peek.locator('[data-modal-close]').click();
      // A refused status change: the reason on the row it was tried on.
      await refuse(page, '/api/v2/tasks/t-r2', 'POST', 403, REVIEWING, 'reviewer');
      await page.locator('[data-row-status="tt-r2"]').click();
      await page.locator('.prop-pop .tl-mi', {hasText: 'Done'}).first().click();
      const said = page.locator('#task-body [data-task-key="tt-r2"] [data-task-refused]');
      await said.waitFor();
      assert.match(await said.innerText(), /You are reviewing this ticket/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A narrow list hides row notes; a refusal still reads.
      const {page, errors} = await open(browser, {hash: '#/tasks?view=list', baseTasks: baseTasks(), local, viewport: {width: 900, height: 800}});
      await refuse(page, '/api/v2/tasks/t-r2', 'POST', 403, REVIEWING, 'reviewer');
      await page.locator('[data-row-status="tt-r2"]').click();
      await page.locator('.prop-pop .tl-mi', {hasText: 'Done'}).first().click();
      const said = page.locator('#task-body [data-task-key="tt-r2"] [data-task-refused] .tl-note-text');
      await said.waitFor();
      assert.ok((await said.boundingBox())?.width > 40, 'the words are drawn');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A person's QA waiver (ui/app/task-code.js taskQaWaiverHTML): "QA waived" in amber in the ticket's properties
      // and in its history, never a green pass, with who, why and the head; a waiver for an old head says so.
      const HEAD = 'c0ffee1234567890c0ffee1234567890c0ffee12';
      const waiver = {head: HEAD, step: 'In QA', pr: 'https://github.com/acme/shop/pull/12', actor: 'human:ana',
        reason: 'Urgent checkout fix; the QA bot is down and both reviews approved this head.', at: at(10), current: true};
      const tasks = baseTasks();
      tasks[0] = {...tasks[0], qa_waivers: [waiver]};
      const {page, errors} = await open(browser, {hash: '#/tasks?view=list', baseTasks: tasks, local});
      await page.route('**/api/v2/tasks/t-dev', route => route.request().method() === 'GET'
        ? route.fulfill({contentType: 'application/json', body: JSON.stringify({task: tasks[0], children: [], comments: [], messages: [],
          events: [{id: 'e1', ts: at(12), actor: 'bot:qa', field: 'verdict', old: null, new: 'pass 0f9e8d7c'},
                   {id: 'e2', ts: at(10), actor: 'human:ana', field: 'qa_waiver', old: null, note: waiver.reason,
                    new: JSON.stringify({head: HEAD, step: 'In QA', pr: waiver.pr})}]})}) : route.fallback());
      await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't-dev')));
      const line = page.locator('#task-modal .task-comments .tcomment.qa-waiver');
      await line.waitFor();
      const said = await line.innerText();
      assert.match(said, /QA waived/);
      assert.match(said, /waived QA on c0ffee1: a waiver, not a QA pass/);
      assert.ok(said.includes(waiver.reason), 'the whole reason');
      assert.doesNotMatch(said, /Passed/);
      const verdict = page.locator('#task-modal .task-comments .tcomment', {hasText: 'recorded the verdict pass'});
      assert.equal(await verdict.count(), 1, 'a real verdict reads as one');
      assert.equal(await verdict.locator('[data-qa-waiver]').count(), 0, 'and carries no waiver mark');
      const prop = page.locator('#task-modal [data-prop-row="qa-waiver"]');
      await prop.waitFor();
      assert.equal(await prop.locator('[data-qa-waiver]').getAttribute('data-current'), 'yes');
      assert.match(await prop.innerText(), /QA waived\s+c0ffee1/);
      assert.ok((await prop.innerText()).includes(waiver.reason));
      assert.match(await prop.locator('[data-qa-waiver]').getAttribute('title'), /A waiver, not a QA pass/);
      assert.equal(await page.locator('#task-modal [data-prop-row="verdict"]').count(), 0, 'no verdict row on the ticket');
      const amber = await prop.locator('[data-qa-waiver]').evaluate(el => getComputedStyle(el).backgroundColor);
      const passBg = await page.locator('#task-body [data-task-key="tt-qa"] [data-verdict="pass"]')
        .evaluate(el => getComputedStyle(el).backgroundColor);
      assert.notEqual(amber, passBg, 'a waiver never looks like a pass');
      // After a push the waiver no longer covers the pull request's head.
      tasks[0].qa_waivers = [{...waiver, current: false}];
      await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't-dev')));
      await page.locator('#task-modal [data-prop-row="qa-waiver"] [data-current="no"]').waitFor();
      assert.match(await page.locator('#task-modal [data-prop-row="qa-waiver"]').innerText(), /old head/);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('task-verdict ok');
})().catch(error => { console.error(error); process.exit(1); });
