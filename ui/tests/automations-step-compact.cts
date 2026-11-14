// A Bot turn step's Runs on (ui/app/automations-editors.ts) has a Compact list, task #237: "Same as bot" first and left
// out of the step, then only the sizes the picked model holds. A size saves on the step as `compact_at`, alone (the bot's
// model) or with a model; a model with a smaller window lowers it to the largest size that fits. On a phone and a computer.
import type {Browser, Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;

type Step = {id: string; type: string; model?: string; compact_at?: number};
const MODELS = [
  {id: 'claude-opus-5-5', label: 'Opus 5.5', context_tokens: 1_000_000, harnesses: ['claude'], efforts: ['low', 'medium', 'high'], default_effort: 'medium'},
  {id: 'grok-4.6', label: 'Grok 4.6', context_tokens: 500_000, harnesses: ['grok'], efforts: ['low', 'high'], default_effort: 'low'}];
const SIZES = ['100K', '150K', '200K', '250K', '300K', '400K', '500K', '600K', '700K', '800K', '900K', '1M'];

const options = (page: Page) => page.locator('[data-b="step.compact_at"] option').allInnerTexts();
async function saved(page: Page, writes: AutomationsFakeSupport.Write[]): Promise<Step> {
  const was = Number((await page.locator('.au-ver').innerText()).slice(1));
  await page.locator('.au-save').click();
  await page.waitForFunction((v: string) => document.querySelector('.au-ver')?.textContent === v, 'v' + (was + 1));
  const w = writes.at(-1)!;
  assert.equal(w.method, 'PATCH');
  return (w.body as {steps: Step[]}).steps.find(s => s.id === 'bt1')!;
}

async function run(browser: Browser, viewport: AutomationsFakeSupport.Viewport, name: string) {
  const {page, errors, writes} = await openAutomations(browser, {viewport, hash: '#/automations/au_inbox', tweak: fx => {
    const inbox = fx.automations.find(a => a.id === 'au_inbox')! as unknown as {steps: Step[]};
    inbox.steps.push({id: 'bt1', type: 'bot_turn', bot: 'support', prompt: 'Answer it.'} as Step);
  }});
  // Registered after the fake's own routes, so it answers first; the editor reads the models when the step opens.
  await page.route('**/api/v2/models', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({models: MODELS})}));
  await page.locator('.au-card[data-open="bt1"]').click();
  const compact = page.locator('[data-b="step.compact_at"]');
  await compact.waitFor();
  // The bot's own model: every size, after Same as bot.
  assert.equal(await compact.inputValue(), '');
  assert.deepEqual(await options(page), ['Same as bot', ...SIZES]);

  // A window alone keeps the bot's model.
  await compact.selectOption('150000');
  let step = await saved(page, writes);
  assert.equal(step.compact_at, 150000);
  assert.equal('model' in step, false);

  // A 1M model, then 700K; then Grok 4.6 (500K) lowers it to 500K and lists nothing larger.
  await page.locator('.au-card[data-open="bt1"]').click();
  await page.selectOption('[data-b="step.model"]', 'claude-opus-5-5');
  await compact.waitFor();
  assert.equal(await compact.inputValue(), '150000', 'the window stays with another model');
  await compact.selectOption('700000');
  await page.selectOption('[data-b="step.model"]', 'grok-4.6');
  await page.waitForFunction(() => (document.querySelector('[data-b="step.compact_at"]') as HTMLSelectElement | null)?.value === '500000');
  assert.deepEqual(await options(page), ['Same as bot', ...SIZES.slice(0, 7)]);
  assert.equal(await sideways(page), false);
  await shot(page, `${name}-step-compact`);
  step = await saved(page, writes);
  assert.deepEqual([step.model, step.compact_at], ['grok-4.6', 500000]);

  // Same as bot leaves it out of the step.
  await page.locator('.au-card[data-open="bt1"]').click();
  await compact.waitFor();
  await compact.selectOption('');
  step = await saved(page, writes);
  assert.equal('compact_at' in step, false);
  assert.deepEqual(errors, []);
}

(async () => {
  const browser = await chromium.launch();
  try {
    await run(browser, PHONE, 'phone');
    await run(browser, DESKTOP, 'desktop');
    console.log('automation step compact ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
