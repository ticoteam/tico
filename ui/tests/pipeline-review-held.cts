// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-review-held.cjs
// ▶ walks everything the pipeline waits on this person for, in one queue: two reviews, then a question (answered on its card,
// the question card Decide draws), then two subtasks (one Done, one Sent back with a note). Each answer or finish takes its
// item out: the next one comes up, "For you · i of N" and the header's count drop at once. At a phone's 390 by 844, dark and
// light. GC_SHOTS=<dir> writes screenshots there.
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {install} = require('./support/pipeline-fixture.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: (ms: number) => ms}; } })();

const SHOTS = process.env.GC_SHOTS || '';
const PHONE = {width: 390, height: 844};
const QUESTION_TEXT = "Does 'close all' include bot-owned tasks?";

async function openReview(browser: Browser, theme: string) {
  const context = await browser.newContext({viewport: PHONE, serviceWorkers: 'block'});
  await context.addInitScript(([theme]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  }, [theme] as const);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = await install(page, {held: true});
  await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
  await page.waitForSelector('.tl-head .pipe-play', {timeout: t(8000)});
  // The card a question holds says whom it waits on, not "Blocked:" with the question in it. On a phone the board shows one
  // stage at a time: Build is the chip that holds D17.
  await page.locator('.pchips .pchip[data-pipe-chip="build"]').click();
  await page.waitForSelector('[data-task-key="tD17"] .bcard-blocked', {timeout: t(8000)});
  assert.equal((await page.locator('[data-task-key="tD17"] .bcard-blocked').innerText()).trim(), 'Waiting on Ana',
    theme + ': the held card waits on Ana');
  await page.locator('.tl-head .pipe-play').click();
  await page.waitForSelector('.pipe-review .pipe-review-count', {timeout: t(5000)});
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), theme, 'the page is in the ' + theme + ' theme');
  return {context, page, state, errors};
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.screenshot({path: path.join(SHOTS, name)});
}

const countText = (page: Page) => page.locator('.pipe-review .pipe-review-count').innerText().then(s => s.replace(/\s+/g, ' ').trim());
const cardTitle = (page: Page) => page.locator('.pipe-review-card-title').innerText().then(s => s.trim());
async function waitCount(page: Page, text: string) {
  await page.waitForFunction(want => document.querySelector('.pipe-review .pipe-review-count')?.textContent!.replace(/\s+/g, ' ').trim() === want,
    text, {timeout: t(5000)});
}
async function waitPlay(page: Page, text: string) {
  await page.waitForFunction(want => document.querySelector('.pipe-play')?.getAttribute('aria-label') === want, text, {timeout: t(5000)});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      const label = theme;
      const {context, page, state, errors} = await openReview(browser, theme);
      assert.equal(await page.locator('.pipe-play').first().getAttribute('aria-label'), 'Review 5 waiting on you', label + ': the header counts five');

      // 1 of 5: a review, as today.
      assert.equal(await countText(page), 'For you · 1 of 5', label + ': the first of five');
      assert.equal(await cardTitle(page), 'Assistant can approve a spec', label + ': the first review');
      await shot(page, `held-1-review-${theme}.png`);

      // 2 of 5: the next review.
      await page.locator('[data-review-next]').click();
      await waitCount(page, 'For you · 2 of 5');
      assert.equal(await cardTitle(page), 'Pipeline stages, requirements and the manager rules', label + ': the second review');

      // 3 of 5: a question. The question card draws it: the question, its two picks, who asks and what it holds up.
      await page.locator('[data-review-next]').click();
      await waitCount(page, 'For you · 3 of 5');
      await page.waitForSelector('.pipe-review .qcard [data-qc="pick"]', {timeout: t(5000)});
      assert.equal(await cardTitle(page), 'Sim stuck checks', label + ': the question sits on its card');
      assert.equal((await page.locator('.pipe-review .qcard .dc-q').innerText()).trim(), QUESTION_TEXT, label + ': the question is shown');
      assert.deepEqual((await page.locator('.pipe-review .qcard .qc-actions [data-qc="pick"] .qc-bl').allInnerTexts()).map(s => s.trim()),
        ['No', 'Yes'], label + ': the two picks, as the question card orders them');
      assert.match(await page.locator('.pipe-review .qcard .qc-sub').innerText(), /holds up: Sim stuck checks/, label + ': what it holds up');
      assert.equal((await page.locator('.pipe-review-side > p').first().innerText()).trim(), 'Question for you · Dev tickets', label + ': why you');
      assert.equal(await page.locator('.pipe-review-sendback').count(), 0, label + ': a question has no Done or Send back');
      assert.doesNotMatch(await page.locator('.pipe-review').innerText(), /\bD\d+\b|\bQ1\b/, label + ': no ticket id on the screen');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, label + ': no sideways scroll');
      await shot(page, `held-2-question-${theme}.png`);

      // Answering on the card sends the question's answer (the card's own call) and the count drops at once.
      await page.locator('.pipe-review .qcard [data-qc="pick"][data-i="0"]').click();
      await waitCount(page, 'For you · 3 of 4');
      assert.deepEqual(state.posts.at(-1), {path: '/v2/tasks/Q1/answers', body: {target: {task: 'Q1'}, answers: {decision: ['Yes']}}},
        label + ': the answer is the card\'s call');
      await waitPlay(page, 'Review 4 waiting on you');

      // 3 of 4: the next item is the first subtask, with Done and Send back.
      assert.equal(await cardTitle(page), 'Assistant can approve a spec', label + ': the subtask\'s card');
      assert.equal((await page.locator('.pipe-review-subject').innerText()).trim(), 'Check the shot crop', label + ': the subtask');
      assert.equal((await page.locator('.pipe-review-side > p').first().innerText()).trim(), 'Your subtask · Dev tickets', label + ': why you');
      assert.deepEqual((await page.locator('[data-review-subject]').allInnerTexts()).map(s => s.trim()), ['Done', 'Send back'],
        label + ': Done and Send back');

      // Done finishes the subtask (its task is done at its version): it leaves the queue.
      await page.locator('[data-review-subject="done"]').click();
      await waitCount(page, 'For you · 3 of 3');
      assert.deepEqual(state.posts.at(-1), {path: '/v2/tasks/S1', body: {status: 'done', version: 2}}, label + ': Done is the task done');
      assert.equal((await page.locator('.pipe-review-subject').innerText()).trim(), 'Write the release note', label + ': the next subtask');

      // Send back needs its note: without one nothing is sent.
      const writes = state.posts.length;
      await page.locator('[data-review-subject="back"]').click();
      await page.waitForSelector('.pipe-review-err', {timeout: t(5000)});
      assert.equal((await page.locator('.pipe-review-err').innerText()).trim(), 'Write a note first.', label + ': a note is asked for');
      assert.equal(state.posts.length, writes, label + ': nothing is sent without the note');
      await page.locator('.pipe-review textarea[name="note"]').fill('Done by the release bot');
      await shot(page, `held-3-subtask-${theme}.png`);

      // Send back with the note declines the subtask with it; the queue goes on at the review it was next to.
      await page.locator('[data-review-subject="back"]').click();
      await waitCount(page, 'For you · 2 of 2');
      assert.deepEqual(state.posts.at(-1), {path: '/v2/tasks/S2', body: {status: 'declined', version: 2, note: 'Done by the release bot'}},
        label + ': Send back declines with the note');
      assert.equal(await cardTitle(page), 'Pipeline stages, requirements and the manager rules', label + ': the queue goes on');
      await waitPlay(page, 'Review 2 waiting on you');
      await shot(page, `held-4-after-${theme}.png`);

      assert.deepEqual(errors, [], label + ': no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-review-held: ok');
})().catch(error => { console.error(error); process.exit(1); });
