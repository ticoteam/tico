// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-review-fixes.cjs
// The ▶ review queue's held forms, checked one scenario at a time at a phone's 390 by 844, dark and light:
//   - a plain question (no picks) is answered in words, as the task page answers one (the same answers call);
//   - the question card's held copy is scoped to the open ▶: closing ▶ drops it, and fresh Needs you data wins over it;
//   - answering a question takes out every queue row of that question, so the count matches the header;
//   - a subtask shows its body, acceptance criteria and files, with an Open link to the task;
//   - Done on a subtask sends the note typed in its box, as the task page saves a note on Done.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const PHONE = {width: 390, height: 844};
const QUESTION_TEXT = "Does 'close all' include bot-owned tasks?";

async function openReview(browser, theme, options) {
  const context = await browser.newContext({viewport: PHONE, serviceWorkers: 'block'});
  await context.addInitScript(([theme]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  }, [theme]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = await install(page, {held: true, ...options});
  await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
  await page.waitForSelector('.tl-head .pipe-play', {timeout: t(8000)});
  await page.locator('.pchips .pchip[data-pipe-chip="build"]').click();
  await page.waitForSelector('[data-task-key="tD17"] .bcard-blocked', {timeout: t(8000)});
  await page.locator('.tl-head .pipe-play').click();
  await page.waitForSelector('.pipe-review .pipe-review-count', {timeout: t(5000)});
  return {context, page, state, errors};
}

const countText = page => page.locator('.pipe-review .pipe-review-count').innerText().then(s => s.replace(/\s+/g, ' ').trim());
async function waitCount(page, text) {
  await page.waitForFunction(want => document.querySelector('.pipe-review .pipe-review-count')?.textContent.replace(/\s+/g, ' ').trim() === want,
    text, {timeout: t(5000)});
}
async function waitPlay(page, text) {
  await page.waitForFunction(want => document.querySelector('.pipe-play')?.getAttribute('aria-label') === want, text, {timeout: t(5000)});
}
// The queue's order: two reviews, then the question (its card's row), then the two subtasks.
async function toQuestion(page, total = 5) {
  await page.locator('[data-review-next]').click();
  await waitCount(page, `For you · 2 of ${total}`);
  await page.locator('[data-review-next]').click();
  await waitCount(page, `For you · 3 of ${total}`);
}
async function toFirstSubtask(page) {
  for (const text of ['For you · 2 of 5', 'For you · 3 of 5', 'For you · 4 of 5']) {
    await page.locator('[data-review-next]').click();
    await waitCount(page, text);
  }
}

// A plain question: no picks, so the ▶ shows an answer box and Send. The text goes to the answers call the task page makes for a
// plain question: the question's id with no picks, and the words as Other.
async function plainQuestion(browser, theme) {
  const {context, page, state, errors} = await openReview(browser, theme, {plain: true});
  await page.locator('[data-review-next]').click();
  await waitCount(page, 'For you · 2 of 5');
  await page.locator('[data-review-next]').click();
  await waitCount(page, 'For you · 3 of 5');
  assert.equal(await page.locator('.pipe-review .qcard').count(), 0, theme + ': a plain question draws no pick card');
  assert.ok((await page.locator('.pipe-review-held').innerText()).includes(QUESTION_TEXT), theme + ': the question is shown');
  await page.locator('.pipe-review textarea[name="answer"]').fill('Ask Ben first');
  await page.locator('[data-review-answer]').click();
  await waitCount(page, 'For you · 3 of 4');
  assert.deepEqual(state.posts.at(-1), {path: '/v2/tasks/Q1/answers', body: {target: {task: 'Q1'}, answers: {decision: []}, other: 'Ask Ben first'}},
    theme + ': the words are the task page\'s answer to a plain question');
  await waitPlay(page, 'Review 4 waiting on you');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}

// The held copy of a question lives only while ▶ is open: fresh Needs you data wins over it, and closing ▶ drops it.
async function heldCopy(browser, theme) {
  const {context, page, errors} = await openReview(browser, theme, {});
  await toQuestion(page);
  assert.equal(await page.evaluate(() => QC_HELD.size), 1, theme + ': the open ▶ holds its question');
  const fresh = await page.evaluate(() => {
    const held = QC_HELD.get('Q1');
    const copy = {...held, kind: 'question', ask: {...held.ask, refs: {...held.ask.refs,
      questions: [{...held.ask.refs.questions[0], question: 'Fresh wording?'}]}}};
    S.v2 = {...S.v2, needs: [copy]};
    return qcardItem({id: 'Q1'})?.it?.ask?.refs?.questions?.[0]?.question ?? null;
  });
  assert.equal(fresh, 'Fresh wording?', theme + ': fresh Needs you data wins over the held copy');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}
async function closeDropsHeld(browser, theme) {
  const {context, page, errors} = await openReview(browser, theme, {});
  await toQuestion(page);
  await page.locator('.pipe-review-exit').click();
  await page.waitForSelector('.pipe-review', {state: 'detached', timeout: t(5000)});
  assert.equal(await page.evaluate(() => QC_HELD.size), 0, theme + ': closing ▶ drops the held copy');
  assert.equal(await page.evaluate(() => qcardItem({id: 'Q1'}) === null), true,
    theme + ': with ▶ closed, the question is not found on its own');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}

// A question that holds two cards has a row for each: answering takes both out, and the count is the queue's.
async function twoCardsOneAnswer(browser, theme) {
  const {context, page, state, errors} = await openReview(browser, theme, {twoCards: true});
  await waitPlay(page, 'Review 6 waiting on you');
  await toQuestion(page, 6);
  await page.waitForSelector('.pipe-review .qcard [data-qc="pick"]', {timeout: t(5000)});
  await page.locator('.pipe-review .qcard [data-qc="pick"][data-i="0"]').click();
  await waitPlay(page, 'Review 4 waiting on you');
  assert.equal(await countText(page), 'For you · 3 of 4', theme + ': both rows of the answered question leave the queue, as the count says');
  assert.equal(state.posts.filter(p => p.path === '/v2/tasks/Q1/answers').length, 1, theme + ': one answer is sent');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}

// A subtask shows its body, acceptance criteria and files, and an Open link to the task.
async function subtaskCard(browser, theme) {
  const {context, page, errors} = await openReview(browser, theme, {});
  await toFirstSubtask(page);
  assert.equal((await page.locator('.pipe-review-subject').innerText()).trim(), 'Check the shot crop', theme + ': the subtask');
  const held = page.locator('.pipe-review-held');
  assert.match(await held.innerText(), /Crop the second shot to the frame\./, theme + ': the subtask\'s body');
  assert.deepEqual((await held.locator('li').allInnerTexts()).map(s => s.trim()), ['Shot 2 is cropped to the frame'],
    theme + ': its acceptance criteria');
  assert.match(await held.locator('[data-tf-file]').innerText(), /crop-notes\.txt/, theme + ': its file');
  assert.equal((await held.locator('a[href="#/task/S1"]').innerText()).trim(), 'Open', theme + ': an Open link to the task');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}

// Done on a subtask sends the note typed in its box, as the task page saves a note when it marks a task done.
async function doneNote(browser, theme) {
  const {context, page, state, errors} = await openReview(browser, theme, {});
  await toFirstSubtask(page);
  await page.locator('.pipe-review textarea[name="note"]').fill('Crop is fine');
  await page.locator('[data-review-subject="done"]').click();
  await waitCount(page, 'For you · 4 of 4');
  assert.deepEqual(state.posts.at(-1), {path: '/v2/tasks/S1', body: {status: 'done', version: 2, note: 'Crop is fine'}},
    theme + ': Done sends the typed note');
  assert.deepEqual(errors, [], theme + ': no page errors');
  await context.close();
}

// Each scenario runs on its own: a failure is reported with the others, so one run shows every broken item.
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const failures = [];
  const scenarios = [plainQuestion, heldCopy, closeDropsHeld, twoCardsOneAnswer, subtaskCard, doneNote];
  try {
    for (const theme of ['dark', 'light']) {
      for (const scenario of scenarios) {
        try {
          await scenario(browser, theme);
        } catch (error) {
          failures.push(`${scenario.name} (${theme}): ${String(error.message).split('\n')[0]}`);
        }
      }
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.join('\n'));
    process.exit(1);
  }
  console.log('pipeline-review-fixes: ok');
})().catch(error => { console.error(error); process.exit(1); });
