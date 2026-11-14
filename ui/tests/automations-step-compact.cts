// A Bot turn step's Runs on (ui/app/automations-editors.ts) is the one model picker (ui/app/settings-bots-choice.ts,
// task #476): closed it reads "Same as bot" or "Opus 5.5 HIGH 700K"; open it is the shared panel with "Same as bot" first
// and a Compact dropdown that starts on "Same as bot" and lists only the sizes the picked model holds. Apply sets the
// step; Save writes it. A size saves on the step as `compact_at`, alone (the bot's model) or with a model; a model with
// a smaller window lowers it to the largest size that fits; Same as bot is left out of the step. On a phone and a computer.
import type {Browser, Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;
const {openPicker, choose, chosen, offered, applyPicker, closedText} = require('./support/model-picker.cts') as import('./support/model-picker.cts').Api;

type Step = {id: string; type: string; model?: string; harness?: string; effort?: string; compact_at?: number};
const MODELS = [
  {id: 'claude-opus-5-5', label: 'opus 5.5', context_tokens: 1_000_000, harnesses: ['claude'], efforts: ['low', 'medium', 'high'], default_effort: 'medium'},
  {id: 'grok-4.6', label: 'grok 4.6', context_tokens: 500_000, harnesses: ['codex'], efforts: ['low', 'high'], default_effort: 'low'}];
const HARNESSES = [
  {id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude', capabilities: {compaction: true}},
  {id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex', capabilities: {compaction: true}}];
const SIZES = ['100K', '150K', '200K', '250K', '300K', '400K', '500K', '600K', '700K', '800K', '900K', '1M'];

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
    // The step's bot runs on Opus 5.5 in Claude Code, so Same as bot compacts.
    const support = (fx.employees as Record<string, unknown>[]).find(e => e.name === 'support')!;
    Object.assign(support, {model: 'claude-opus-5-5', harness: 'claude', runtime: 'claude', reasoning_effort: 'medium'});
  }});
  // Registered after the fake's own routes, so it answers first; the picker reads the models when the step opens.
  await page.route('**/api/v2/models', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({models: MODELS, harnesses: HARNESSES})}));
  await page.locator('.au-card[data-open="bt1"]').click();
  const box = page.locator('.au-ed [data-run-picker] [data-bot-choice]');
  await box.waitFor();
  // One picker, not stacked selects: no Runs on, Harness, Effort or Compact select in the step.
  assert.equal(await page.locator('.au-ed [data-b^="step.model"], .au-ed [data-b="step.effort"], .au-ed [data-b="step.harness"], .au-ed [data-b="step.compact_at"]').count(), 0);
  assert.equal(await closedText(box), 'Same as bot');

  // The bot's own model: every size, after Same as bot. A window alone keeps the bot's model.
  let panel = await openPicker(box);
  assert.deepEqual(await offered(panel, 'harness'), ['Same as bot', 'Claude Code', 'Codex']);
  const compact = panel.locator('[data-choice-compact]');
  assert.equal(await compact.inputValue(), '');
  assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES]);
  await compact.selectOption('150000');
  await applyPicker(panel);
  assert.equal(await closedText(box), 'Same as bot 150K');
  let step = await saved(page, writes);
  assert.equal(step.compact_at, 150000);
  assert.equal('model' in step, false);

  // A 1M model at high effort, then 700K; then Grok 4.6 (500K) lowers it to 500K and lists nothing larger.
  await page.locator('.au-card[data-open="bt1"]').click();
  panel = await openPicker(box);
  assert.equal(await compact.inputValue(), '150000', 'the saved window');
  await choose(panel, 'harness', 'claude');
  assert.equal(await chosen(panel, 'model'), 'claude-opus-5-5', 'the only model on that harness');
  await choose(panel, 'effort', 'high');
  assert.equal(await compact.inputValue(), '150000', 'the window stays with another model');
  await compact.selectOption('700000');
  await applyPicker(panel);
  assert.equal(await closedText(box), 'Opus 5.5 HIGH 700K');
  panel = await openPicker(box);
  await choose(panel, 'harness', 'codex');
  assert.equal(await compact.inputValue(), '500000');
  assert.deepEqual(await compact.locator('option').allInnerTexts(), ['Same as bot', ...SIZES.slice(0, 7)]);
  assert.equal(await sideways(page), false);
  await shot(page, `${name}-step-compact`);
  await applyPicker(panel);
  step = await saved(page, writes);
  assert.deepEqual([step.model, step.harness, step.effort, step.compact_at], ['grok-4.6', 'codex', 'low', 500000]);

  // Same as bot leaves the model and the window out of the step.
  await page.locator('.au-card[data-open="bt1"]').click();
  panel = await openPicker(box);
  await choose(panel, 'harness', '');
  await compact.selectOption('');
  await applyPicker(panel);
  step = await saved(page, writes);
  assert.deepEqual(['model', 'harness', 'effort', 'compact_at'].filter(k => k in step), []);
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
