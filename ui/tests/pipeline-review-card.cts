// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-review-card.cts
// The review screen shows what is being reviewed, at a phone's 390 by 844, dark and light. The card's title is always there.
// With an attachment the attachment is shown; with none, the card's body, acceptance criteria and checkpoint, under "No spec
// attached". "Why you" names the requirement and the pipeline. The footer names the card by its title, never its id. The two
// Message buttons name their bots and fit. A markdown file on the ticket shows as text. GC_SHOTS=<dir> writes screenshots there.
import type {Browser, Page} from 'playwright';
import type {PipelineInstallOptions} from './support/pipeline-fixture.cts';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {install} = require('./support/pipeline-fixture.cts');
const {t} = (() => { try { return require('./support/load.cts'); } catch { return {t: (ms: number) => ms}; } })();

const SHOTS = process.env.GC_SHOTS || '';
const PHONE = {width: 390, height: 844};
const BUTTONS = ['Message Product Manager', 'Message Product Pipeline Manager'];

async function openReview(browser: Browser, theme: string, options: PipelineInstallOptions = {}) {
  const context = await browser.newContext({viewport: PHONE, serviceWorkers: 'block'});
  await context.addInitScript(([theme]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  }, [theme] as const);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = await install(page, options);
  await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
  await page.waitForSelector('.tl-head .pipe-play', {timeout: t(8000)});
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

// What every card shows: its title, the Why line, the two named Message buttons (none clipped), no ticket id, no sideways scroll.
async function checkCard(page: Page, label: string, {title, why, buttons = BUTTONS}: {title: string; why: string; buttons?: string[]}) {
  assert.equal((await page.locator('.pipe-review-card-title').innerText()).trim(), title, label + ': the card title');
  assert.equal((await page.locator('.pipe-review-side > p').first().innerText()).trim(), why, label + ': why you');
  const names = (await page.locator('.pipe-review-sendback .btn').allInnerTexts()).map(s => s.trim());
  assert.deepEqual(names, buttons, label + ': the Message buttons name the bots');
  const clipped = await page.locator('.pipe-review-sendback .btn').evaluateAll(els =>
    els.filter(el => el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight).length);
  assert.equal(clipped, 0, label + ': no Message button clips its name');
  assert.doesNotMatch(await page.locator('.pipe-review').innerText(), /\bD\d+\b/, label + ': no ticket id on the screen');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, label + ': no sideways scroll');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      // With an attachment: the picture is shown under the title; the footer names the next card by its title.
      {
        const label = `${theme} attached`;
        const {context, page, errors} = await openReview(browser, theme);
        assert.equal((await page.locator('.pipe-review-count').first().innerText()).trim(), 'For you · 1 of 2', label + ': the first card');
        await checkCard(page, label, {title: 'Assistant can approve a spec', why: 'Screenshots · Dev tickets'});
        assert.equal(await page.locator('.pipe-review-main img.pipe-review-img').count(), 1, label + ': the attachment is shown');
        assert.equal(await page.locator('.pipe-review-spec').count(), 0, label + ': no card body beside the attachment');
        assert.equal(await page.locator('.pipe-review-nospec').count(), 0, label + ': no "No spec attached"');
        await shot(page, `attached-${theme}.png`);

        await page.locator('[data-review-next]').click();
        await page.waitForFunction(() => document.querySelector('.pipe-review-count')?.textContent!.includes('2 of 2'), null, {timeout: t(5000)});
        await checkCard(page, label + ' next', {title: 'Pipeline stages, requirements and the manager rules', why: 'Spec page · Dev tickets'});
        assert.equal((await page.locator('.pipe-review-moves').innerText()).trim(),
          'Last requirement · moves Pipeline stages, requirements and the… to Build', label + ': the footer names the card by its title');
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }

      // With nothing attached: the card's words stand in for the picture, under "No spec attached".
      {
        const label = `${theme} bare`;
        const {context, page, errors} = await openReview(browser, theme, {bare: true});
        await checkCard(page, label, {title: 'Assistant can approve a spec', why: 'Screenshots · Dev tickets'});
        assert.equal((await page.locator('.pipe-review-nospec').innerText()).trim(), 'No spec attached', label + ': says so');
        const spec = await page.locator('.pipe-review-spec').innerText();
        for (const text of ['Assistant can approve a spec when every check passes.', 'Approved in one tap', 'Works on a phone',
          'Screenshots taken on the dev branch; shot 2 is cropped.']) {
          assert.ok(spec.includes(text), label + ': the card shows "' + text + '"');
        }
        assert.equal(await page.locator('.pipe-review-main img').count(), 0, label + ': no picture');
        assert.equal(await page.locator('.pipe-review-tab').count(), 0, label + ': no attachment tab');
        await shot(page, `bare-${theme}.png`);
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }

      // A Product card in Review with its Spec page: the spec leads as its own tab, the card's evidence follows it.
      {
        const label = `${theme} product spec`;
        const {context, page, errors} = await openReview(browser, theme, {spec: true, product: true});
        await checkCard(page, label, {title: 'Assistant can approve a spec', why: 'You approve specs in Product'});
        assert.equal((await page.locator('.pipe-review-tab').allInnerTexts()).map(s => s.trim()).join('|'), 'Spec page|shot.png',
          label + ': the spec leads, then the evidence');
        assert.equal((await page.locator('.pipe-review-tab.on').innerText()).trim(), 'Spec page', label + ': the spec is on show');
        await page.waitForSelector('pre.pipe-review-file-text', {timeout: t(5000)});
        assert.match(await page.locator('pre.pipe-review-file-text').innerText(), /^# Assistant can approve a spec/, label + ': the spec text');
        assert.equal(await page.locator('.pipe-review-spec').count(), 0, label + ': no card body beside the spec');
        assert.equal(await page.locator('.pipe-review-nospec').count(), 0, label + ': no "No spec attached"');
        assert.equal((await page.locator('.pipe-review-moves').innerText()).trim(),
          'Last requirement · moves Assistant can approve a spec to In development', label + ': the footer names the move by the card title');
        await shot(page, `product-spec-${theme}.png`);
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }

      // A Product card in Review with no spec and nothing attached: the card's words, under "No spec attached".
      {
        const label = `${theme} product bare`;
        const {context, page, errors} = await openReview(browser, theme, {bare: true, product: true});
        await checkCard(page, label, {title: 'Assistant can approve a spec', why: 'You approve specs in Product'});
        assert.equal((await page.locator('.pipe-review-nospec').innerText()).trim(), 'No spec attached', label + ': says so');
        assert.ok((await page.locator('.pipe-review-spec').innerText()).includes('Approved in one tap'), label + ': the card shows its criteria');
        assert.equal((await page.locator('.pipe-review-moves').innerText()).trim(),
          'Last requirement · moves Assistant can approve a spec to In development', label + ': the footer names the move by the card title');
        await shot(page, `product-nospec-${theme}.png`);
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }

      // A markdown file on the ticket: shown as its text, as written, not rendered; Open still opens the file.
      {
        const label = `${theme} text`;
        const {context, page, errors} = await openReview(browser, theme, {text: true});
        await page.locator('[data-review-next]').click();
        await page.waitForSelector('pre.pipe-review-file-text', {timeout: t(5000)});
        const shown = await page.locator('pre.pipe-review-file-text').evaluate(el => ({text: el.textContent!, headings: el.querySelectorAll('h1').length}));
        assert.match(shown.text, /^# Pipeline manager spec/, label + ': the markdown shows as written');
        assert.equal(shown.headings, 0, label + ': it is not rendered as markdown');
        assert.equal((await page.locator('.pipe-review-tab').innerText()).trim(), 'Pipeline manager spec v1.md', label + ': the file is named');
        assert.equal(await page.locator('.pipe-review-open').getAttribute('href'), '/api/v2/files/blob-spec', label + ': Open is the file');
        await checkCard(page, label, {title: 'Pipeline stages, requirements and the manager rules', why: 'Spec page · Dev tickets'});
        await shot(page, `text-preview-${theme}.png`);
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-review-card: ok');
})().catch(error => { console.error(error); process.exit(1); });
