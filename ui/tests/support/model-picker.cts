// Drive the one model picker (ui/app/settings-bots-choice.ts) from a browser script: a box's closed button opens its
// panel (#sb-choice-pop); Harness, Model and Effort are radio rows, then More folds Compact (and a bot's Output style
// or Verbosity) as selects, then Apply.
type Locator = import('playwright').Locator;

// The panel a box's button opens, once it is drawn, with More unfolded unless `more` is false.
async function openPicker(box: Locator, more = true): Promise<Locator> {
  await box.locator('[data-choice-open]').click();
  const panel = box.page().locator('#sb-choice-pop');
  await panel.locator('form [data-choice-harness]').waitFor();
  if (more) await openMore(panel);
  return panel;
}
// Unfold More, leaving the keyboard where it is; a More still hidden (no model yet) stays unfolded once it shows.
async function openMore(panel: Locator) {
  await panel.locator('[data-choice-more]').evaluate(details => { (details as HTMLDetailsElement).open = true; });
}
// Whether More is unfolded.
async function moreOpen(panel: Locator) {
  return panel.locator('[data-choice-more]').evaluate(details => (details as HTMLDetailsElement).open);
}
// Pick `value` in a row by clicking its label, as a person does.
async function choose(panel: Locator, row: 'harness' | 'model' | 'effort', value: string) {
  await panel.locator(`[data-choice-${row}] label:has(input[value="${value}"])`).click();
}
// The value a row has checked ('' for none, or Same as bot).
async function chosen(panel: Locator, row: 'harness' | 'model' | 'effort') {
  const on = panel.locator(`[data-choice-${row}] input:checked`);
  return await on.count() ? on.inputValue() : '';
}
// The labels a row offers, each as drawn: "Codex", "gpt 6 luna(default)1.05M".
async function offered(panel: Locator, row: 'harness' | 'model' | 'effort') {
  return panel.locator(`[data-choice-${row}] label`).allInnerTexts();
}
async function applyPicker(panel: Locator) {
  await panel.locator('[data-choice-apply]').click();
  await panel.waitFor({state: 'hidden'});
}
// The text on a box's closed button.
async function closedText(box: Locator) {
  return (await box.locator('.sb-pick-text').innerText()).trim();
}
const api = {openPicker, openMore, moreOpen, choose, chosen, offered, applyPicker, closedText};
export type Api = typeof api;
module.exports = api;
