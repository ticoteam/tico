// Give to… (ui/app/give-to.ts): one searchable picker for every place that hands work on. Search reads the name, the
// slug and the role (AC-1); ↑ ↓ Enter Esc (AC-2); Suggested first, in order (AC-3); one line each, no model text (AC-4);
// a muted placeholder with no ✓ (AC-5); recent picks shared by every place (AC-6); the task's Owner, People, Subtask and
// New task use it (AC-7); a full-height sheet at 390 px (AC-8); who is listed, ✓ and "No one matches" (AC-9); a
// combobox with options and no network call while it is open (AC-10).
import type {Browser, Page} from 'playwright';
declare const S: {emps: Record<string, unknown>[]; people: Record<string, unknown>[]};
declare function giveToEntries(selected?: string): {value: string; actor: string; name: string; role: string}[];
declare function giveToMatch(q: string, entries: unknown[]): {name: {name: string}[]; role: {name: string}[]};
declare function giveToSuggest(entries: unknown[], task: unknown, recent: string[]): {entry: {name: string}; why: string}[];
declare function openTaskCreate(owner?: string, opts?: Record<string, unknown>): void;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open} = require('./task-code.cts') as {open: (browser: Browser, opts?: Record<string, unknown>) => Promise<{page: Page; errors: string[]; data: {tasks: Record<string, unknown>[]}}>};

// The roster the picker reads (S.emps, S.people), shaped as the app holds it, with the roles of the real team. The
// app reads the roster again on its own timer, so the fixture's endpoints serve the same one.
// In production a bot's id is not its slug (BotOps is bot:<uuid>, slug botops); eng and api keep the task fixture's ids.
const bot = (id: string, display_name: string, role: string, extra: Record<string, unknown> = {}) =>
  ({id, name: id, slug: id.replace(/^b-/, ''), display_name, role, status: 'active', state: 'active', host: 'keeper',
    resolved_model: 'claude-opus-5-5', resolved_model_label: 'Opus 5.5', resolved_provider: 'Anthropic', ...extra});
const BOTS = [
  bot('eng', 'Engineer', 'Builds each change on a branch. Never merges.'),
  bot('api', 'API Engineer', 'Owns the payments API.'),
  bot('b-botops', 'BotOps', 'Keeps every bot running: stuck runs, failed jobs, computers.'),
  bot('finance', 'Finance Owner', 'Keeps the books and pays the bills.'),
  bot('finance-po', 'Finance Product Owner', 'Writes the specs for Finance screens.'),
  bot('reviewer-1', 'Reviewer 1', 'Reads each pull request first.'),
  bot('reviewer-2', 'Reviewer 2', 'Reads each pull request after the fixes.'),
  bot('b-release', 'Release Owner', 'Reviews the merge queue and ships each release.'),
  bot('jev', 'Jev', 'Checks each page a person will read.', {description: 'ignored: the role wins'}),
  bot('old', 'Old Helper', 'Retired.', {status: 'archived'})];
const PEOPLE = [{id: 'ana', name: 'Ana', title: 'Founder'}, {id: 'sam', name: 'Sam', title: 'Designer'}, {id: 'gone', name: 'Gone', hidden: true}];
async function roster(page: Page): Promise<void> {
  await page.route('**/api/employees', route => route.fulfill({json: BOTS}));
  await page.route('**/api/humans', route => route.fulfill({json: {people: PEOPLE, teams: {}}}));
  await page.evaluate(([bots, people]) => {
    S.emps.splice(0, S.emps.length, ...bots);
    S.people.splice(0, S.people.length, ...people);
  }, [BOTS, PEOPLE] as const);
}

async function desktop(browser: Browser): Promise<void> {
  const {page, errors, data} = await open(browser, {viewport: {width: 1440, height: 900}});
  await roster(page);
  await page.evaluate(() => localStorage.clear());

  // AC-1 and AC-9 on the matching itself.
  const names = (q: string) => page.evaluate(q => { const m = giveToMatch(q, giveToEntries()); return [...m.name, ...m.role].map(e => e.name); }, q);
  for (const q of ['ops', 'bot ops', 'botops', 'BotOps']) assert.equal((await names(q))[0], 'BotOps', `"${q}" puts BotOps first`);
  assert.deepEqual((await names('finance')).slice(0, 2), ['Finance Owner', 'Finance Product Owner']);
  assert.deepEqual(await names('review'), ['Reviewer 1', 'Reviewer 2', 'Release Owner'], 'names first, then a role that mentions review');
  assert.deepEqual(await names('designer'), ['Sam'], "a person's title is their role");
  assert.deepEqual(await names('zzz'), []);
  const everyone = await page.evaluate(() => giveToEntries().map(e => e.name));
  assert.deepEqual(everyone, ['Ana', 'Sam', 'API Engineer', 'BotOps', 'Engineer', 'Finance Owner', 'Finance Product Owner', 'Jev', 'Release Owner', 'Reviewer 1', 'Reviewer 2'],
    'people then bots, A–Z; archived and hidden stay out');

  // AC-3: requester, bots named (title, then body), the area tag's owner, then recent; no one twice; at most 8.
  const suggested = await page.evaluate(() => giveToSuggest(giveToEntries(), {requester_id: 'human:sam', title: 'Ask BotOps about the stuck run',
    body: 'Then finance-po writes it up; botops again.', tags: [{owner: 'bot:b-release'}, {owner: 'human:sam'}]},
  ['bot:eng', 'bot:b-botops', 'bot:api', 'bot:jev', 'bot:reviewer-1', 'bot:reviewer-2']).map(s => `${s.entry.name}: ${s.why}`));
  assert.deepEqual(suggested, ['Sam: Asked for it', 'BotOps: Named in the task', 'Finance Product Owner: Named in the task', 'Release Owner: Owns its area',
    'Engineer: Recent', 'API Engineer: Recent', 'Jev: Recent', 'Reviewer 1: Recent']);

  // A new owner saves as task-owner-set-anyway.cts serves it.
  const owners: unknown[] = [];
  await page.route('**/api/v2/tasks/t-checkout/owner', route => {
    const body = route.request().postDataJSON() as {owner: string}; owners.push(body.owner);
    return route.fulfill({json: {task: {...data.tasks[0], owner_id: body.owner, version: 3}}});
  });

  // The task's Owner opens the picker: a combobox, focus in it, Suggested then Everyone, the owner ✓, no model text.
  const modal = page.locator('#task-modal');
  const owner = modal.locator('[data-prop="owner"]');
  const asked: string[] = [];
  // The reads a picker could make (the roster, tasks, tags); the app's own timers (live stream, sign-in, support) are not its.
  page.on('request', r => { if (/^\/api\/(employees|humans|v2\/(tasks|bots|people|tags))\b/.test(new URL(r.url()).pathname)) asked.push(r.url()); });
  await owner.click();
  const pop = page.locator('.gt-pop:popover-open');
  await pop.waitFor();
  const box = pop.locator('input[role=combobox]');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'combobox', 'focus starts in the search box');
  assert.equal(await box.getAttribute('aria-controls'), await pop.locator('[role=listbox]').getAttribute('id'));
  assert.deepEqual(await pop.locator('.gt-h').allTextContents(), ['Suggested', 'Everyone']);
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gt-name').allTextContents(), ['Ana'], 'only the requester before any pick');
  assert.equal(await pop.locator('[aria-checked=true] .gt-name').first().textContent(), 'Engineer', 'the current owner shows ✓');
  assert.doesNotMatch(await pop.innerText(), /opus|anthropic/i, 'no model text');
  assert.equal(await pop.locator('[role=option]').first().getAttribute('title'), 'Founder', 'the role on hover');
  assert.equal(await pop.locator('[role=option][data-give-pick="b-botops"]').getAttribute('title'), 'Keeps every bot running: stuck runs, failed jobs, computers.');

  // Esc: closed, nothing saved, focus back on the button.
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-prop')), 'owner');
  assert.equal(await page.locator('#task-modal').evaluate(d => (d as HTMLDialogElement).open), true, 'Esc closes only the picker');

  // Typing, ↓ ↑ and Enter.
  await owner.click();
  await pop.waitFor();
  await page.keyboard.type('nobody here');
  assert.equal((await pop.locator('.gt-status').textContent())!.trim(), 'No one matches ‘nobody here’');
  await box.fill('review');
  assert.deepEqual(await pop.locator('[role=option] .gt-name').allTextContents(), ['Reviewer 1', 'Reviewer 2', 'Release Owner']);
  assert.equal(await pop.locator('.gt-row.by-role .gt-role').textContent(), 'Reviews the merge queue and ships each release.', 'a role match shows its role');
  const lit = () => pop.locator('[role=option][aria-selected=true] .gt-name').textContent();
  assert.equal(await lit(), 'Reviewer 1');
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowDown');
  assert.equal(await lit(), 'Release Owner');
  assert.equal(await box.getAttribute('aria-activedescendant'), await pop.locator('[role=option][aria-selected=true]').getAttribute('id'));
  await page.keyboard.press('ArrowUp');
  assert.equal(await lit(), 'Reviewer 2');
  await box.fill('ops');
  assert.deepEqual(asked, [], 'opening and filtering make no network call');
  await page.keyboard.press('Enter');
  await pop.waitFor({state: 'detached'});
  await page.waitForFunction(() => /BotOps/.test(document.querySelector('#task-modal [data-prop="owner"]')?.textContent || ''));
  assert.deepEqual(owners, ['bot:b-botops'], "the owner is saved as BotOps's actor");

  // AC-6: the pick is first in Recent in another place (New task, which has no task: Suggested is recent only).
  await page.keyboard.press('Escape');
  await page.evaluate(() => openTaskCreate());
  const form = page.locator('#task-create-form');
  const field = form.locator('button[data-give-to]');
  assert.equal((await field.textContent())!.trim(), 'Who is this for?');
  assert.ok(await field.evaluate(b => b.classList.contains('empty')), 'the placeholder is muted');
  assert.doesNotMatch((await field.textContent())!, /✓/);
  assert.equal(await form.locator('select').count(), 1, 'only the pipeline is a native list');
  await field.click();
  await pop.waitFor();
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gt-row').evaluateAll(rs => rs.map(r => `${r.querySelector('.gt-name')?.textContent}: ${r.querySelector('.gt-why')?.textContent}`)), ['BotOps: Recent']);
  await pop.locator('[data-give-pick="human:sam"]').last().click();
  await pop.waitFor({state: 'detached'});
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'human:sam');
  assert.equal(await field.locator('.gt-name').textContent(), 'Sam');
  assert.ok(!(await field.evaluate(b => b.classList.contains('empty'))));
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.giveTo.recent:human:ana') || '[]').join(' ')), 'human:sam bot:b-botops');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('give-to: desktop ok');
}

// AC-8: at 390 px the picker is a full-height sheet: ✕ top left, search at the top, 44px rows, no sideways scroll.
async function phone(browser: Browser): Promise<void> {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, at: '#/tasks'});
  await roster(page);
  await page.evaluate(() => openTaskCreate());
  await page.locator('#task-create-form button[data-give-to]').click();
  const pop = page.locator('.gt-pop:popover-open');
  await pop.waitFor();
  const r = (await pop.boundingBox())!;
  assert.deepEqual([r.x, r.y, r.width, r.height], [0, 0, 390, 844], 'full height');
  const x = (await pop.locator('.gt-x').boundingBox())!, q = (await pop.locator('.gt-q').boundingBox())!;
  assert.ok(x.x < 20 && x.y < 20 && x.width >= 44 && x.height >= 44, '✕ top left');
  assert.ok(q.y < 80 && q.height >= 44, 'search at the top');
  const heights = await pop.locator('.gt-row').evaluateAll(rs => rs.map(r => r.getBoundingClientRect().height));
  assert.ok(heights.length > 5 && heights.every(h => h >= 44), `44px rows: ${heights}`);
  assert.equal(await pop.locator('.gt-row[data-give-pick="b-botops"] .gt-role').isVisible(), true, 'the role shows after the name on a phone');
  assert.ok(await pop.evaluate(p => p.scrollWidth <= p.clientWidth + 1), 'no sideways scroll');
  await pop.locator('.gt-x').click();
  await pop.waitFor({state: 'detached'});
  assert.deepEqual(errors, []);
  await page.close();
  console.log('give-to: phone ok');
}

if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try { await desktop(browser); await phone(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
