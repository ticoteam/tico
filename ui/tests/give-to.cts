// Give to… (ui/app/give-to.ts): one searchable picker for every place that hands work on. Search reads the name, the
// slug and the role (AC-1); ↑ ↓ Enter Esc (AC-2); Suggested first, in order (AC-3); one line each, no model text (AC-4);
// a muted placeholder with no ✓ (AC-5); recent picks shared by every place (AC-6); the task's Owner, People, Subtask and
// New task use it (AC-7); a full-height sheet at 390 px (AC-8); who is listed, ✓ and "No one matches" (AC-9); a
// combobox with options and no network call while it is open (AC-10).
import type {Browser, Page} from 'playwright';
declare const S: {emps: Record<string, unknown>[]; people: Record<string, unknown>[]; v2: {needs?: {id: string}[] | null}};
declare function v2NeedsRead(): Promise<unknown>;
declare function refresh(force?: boolean): Promise<void>;
declare function giveToOpen(anchor: HTMLElement, opts: Record<string, unknown>): HTMLElement;
declare function giveToEntries(selected?: string): {value: string; actor: string; name: string; role: string}[];
declare function giveToMatch(q: string, entries: unknown[]): {name: {name: string}[]; role: {name: string}[]};
declare function giveToSuggest(entries: unknown[], task: unknown, recent: string[]): {entry: {name: string}; why: string}[];
declare function openTaskCreate(owner?: string, opts?: Record<string, unknown>): void;
declare function decideOpen(id?: string, items?: unknown[]): boolean;
declare const DEC_ABOUT: Map<string, {state: string}>;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open} = require('./task-code.cts') as {open: (browser: Browser, opts?: Record<string, unknown>) => Promise<{page: Page; errors: string[]; data: {tasks: Record<string, unknown>[]}}>};

// The roster the picker reads (S.emps, S.people), shaped as the app holds it, with the roles of the real team. The
// app reads the roster again on its own timer, so the fixture's endpoints serve the same one.
// As production sends them: a built-in bot's id is its slug (botops, assistant); a newer bot's is a uuid (b-release here).
const bot = (id: string, display_name: string, role: string, extra: Record<string, unknown> = {}) =>
  ({id, name: id, slug: id.replace(/^b-/, ''), display_name, role, status: 'active', state: 'active', host: 'keeper',
    resolved_model: 'claude-opus-5-5', resolved_model_label: 'Opus 5.5', resolved_provider: 'Anthropic', ...extra});
const BOTS = [
  bot('eng', 'Engineer', 'Builds each change on a branch. Never merges.'),
  bot('api', 'API Engineer', 'Owns the payments API.'),
  bot('botops', 'BotOps', 'Keeps every bot running: stuck runs, failed jobs, computers.'),
  bot('finance', 'Finance Owner', 'Keeps the books and pays the bills.'),
  bot('finance-po', 'Finance Product Owner', 'Writes the specs for Finance screens.'),
  bot('reviewer-1', 'Reviewer 1', 'Reads each pull request first.'),
  bot('reviewer-2', 'Reviewer 2', 'Reads each pull request after the fixes.'),
  bot('b-release', 'Release Owner', 'Reviews the merge queue and ships each release.'),
  bot('assistant', 'Assistant', "Every human's private Assistant."),
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
  // A roster read already on its way when the routes changed could still land with the fixture's bots: one read after
  // them is shown last (refresh.ts keeps the newest read), so wait for it.
  await page.evaluate(() => refresh(true));
  await page.waitForFunction(() => S.emps.some(e => e['id'] === 'botops') && S.people.some(p => p['id'] === 'gone'));
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
    'people then bots, A–Z; archived bots and the Assistant stay out, BotOps (id = slug) is listed');

  // AC-3: requester, bots named (title, then body), the area tag's owner, then recent; no one twice; at most 8.
  const suggested = await page.evaluate(() => giveToSuggest(giveToEntries(), {requester_id: 'human:sam', title: 'Ask BotOps about the stuck run',
    body: 'Then finance-po writes it up; botops again.', tags: [{owner: 'bot:b-release'}, {owner: 'human:sam'}]},
  ['bot:eng', 'bot:botops', 'bot:api', 'bot:jev', 'bot:reviewer-1', 'bot:reviewer-2']).map(s => `${s.entry.name}: ${s.why}`));
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
  // Owner is a row of the task's Details, which is one closed line until opened.
  await modal.locator('details.task-details, [data-prop="owner"]').first().waitFor({state: 'attached'});
  const details = modal.locator('details.task-details:not([open]) > summary');
  if (await details.count()) await details.click();
  const owner = modal.locator('[data-prop="owner"]');
  const asked: string[] = [];
  // The reads a picker could make (the roster, tasks, tags); the app's own timers (live stream, sign-in, support) are not its.
  page.on('request', r => { if (/^\/api\/(employees|humans|v2\/(tasks|bots|people|tags))\b/.test(new URL(r.url()).pathname)) asked.push(r.url()); });
  await owner.click();
  const pop = page.locator('.gv-pop:popover-open');
  await pop.waitFor();
  const box = pop.locator('input[role=combobox]');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'combobox', 'focus starts in the search box');
  assert.equal(await box.getAttribute('aria-controls'), await pop.locator('[role=listbox]').getAttribute('id'));
  assert.deepEqual(await pop.locator('.gv-h').allTextContents(), ['Suggested', 'Everyone']);
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gv-name').allTextContents(), ['Ana'], 'only the requester before any pick');
  assert.equal(await pop.locator('[aria-checked=true] .gv-name').first().textContent(), 'Engineer', 'the current owner shows ✓');
  assert.doesNotMatch(await pop.innerText(), /opus|anthropic/i, 'no model text');
  assert.equal(await pop.locator('[role=option]').first().getAttribute('title'), 'Founder', 'the role on hover');
  assert.equal(await pop.locator('[role=option][data-give-pick="botops"]').getAttribute('title'), 'Keeps every bot running: stuck runs, failed jobs, computers.');

  // Esc: closed, nothing saved, focus back on the button.
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-prop')), 'owner');
  assert.equal(await page.locator('#task-modal').evaluate(d => (d as HTMLDialogElement).open), true, 'Esc closes only the picker');

  // The picker stays under its button when the window changes size. At 700 px the full picker fits neither below nor
  // above the Owner row, so it takes the roomier side, shortened, and never covers its button.
  await owner.click();
  await pop.waitFor();
  await page.setViewportSize({width: 1000, height: 700});
  await page.waitForFunction(() => {
    const p = document.querySelector('.gv-pop:popover-open')?.getBoundingClientRect(), b = document.querySelector('#task-modal [data-prop="owner"]')?.getBoundingClientRect();
    return !!p && !!b && p.right <= innerWidth - 8 && Math.abs(p.top - (b.bottom + 4)) <= 20;
  });
  const fit = await page.evaluate(() => {
    const p = document.querySelector('.gv-pop:popover-open')!.getBoundingClientRect(), b = document.querySelector('#task-modal [data-prop="owner"]')!.getBoundingClientRect();
    return {clear: p.top >= b.bottom || p.bottom <= b.top, inside: p.top >= 8 && p.bottom <= innerHeight - 8};
  });
  assert.deepEqual(fit, {clear: true, inside: true}, 'the picker leaves its button in view and stays on screen');
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  await page.setViewportSize({width: 1440, height: 900});

  // Removed without being hidden (its dialog drew again): the next resize cleans up and the button is no longer expanded.
  await owner.click();
  await pop.waitFor();
  await page.evaluate(() => document.querySelector('.gv-pop')!.remove());
  await page.setViewportSize({width: 1400, height: 900});
  await page.waitForFunction(() => document.querySelector('#task-modal [data-prop="owner"]')?.getAttribute('aria-expanded') === 'false');
  await page.setViewportSize({width: 1440, height: 900});

  // Everyone already on it (People with a role that holds everyone): words, not an empty list.
  await page.evaluate(() => giveToOpen(document.querySelector<HTMLElement>('#task-modal [data-prop="owner"]')!,
    {label: 'Add as reviewer', exclude: giveToEntries().map(e => e.actor), none: 'Everyone is on it', onPick: () => {}}));
  await pop.waitFor();
  assert.equal(await pop.locator('[role=option]').count(), 0);
  assert.equal((await pop.locator('.gv-status').textContent())!.trim(), 'Everyone is on it');
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});

  // Typing, ↓ ↑ and Enter.
  await owner.click();
  await pop.waitFor();
  await page.keyboard.type('nobody here');
  assert.equal((await pop.locator('.gv-status').textContent())!.trim(), 'No one matches ‘nobody here’');
  await box.fill('review');
  assert.deepEqual(await pop.locator('[role=option] .gv-name').allTextContents(), ['Reviewer 1', 'Reviewer 2', 'Release Owner']);
  assert.equal(await pop.locator('.gv-row.by-role .gv-role').textContent(), 'Reviews the merge queue and ships each release.', 'a role match shows its role');
  const lit = () => pop.locator('[role=option][aria-selected=true] .gv-name').textContent();
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
  assert.deepEqual(owners, ['bot:botops'], "the owner is saved as BotOps's actor");

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
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gv-row').evaluateAll(rs => rs.map(r => `${r.querySelector('.gv-name')?.textContent}: ${r.querySelector('.gv-why')?.textContent}`)), ['BotOps: Recent']);
  await pop.locator('[data-give-pick="human:sam"]').last().click();
  await pop.waitFor({state: 'detached'});
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'human:sam');
  assert.equal(await field.locator('.gv-name').textContent(), 'Sam');
  assert.ok(!(await field.evaluate(b => b.classList.contains('empty'))));
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.giveTo.recent:human:ana') || '[]').join(' ')), 'human:sam bot:botops');
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
  const pop = page.locator('.gv-pop:popover-open');
  await pop.waitFor();
  const r = (await pop.boundingBox())!;
  assert.deepEqual([r.x, r.y, r.width, r.height], [0, 0, 390, 844], 'full height');
  const x = (await pop.locator('.gv-x').boundingBox())!, q = (await pop.locator('.gv-q').boundingBox())!;
  assert.ok(x.x < 20 && x.y < 20 && x.width >= 44 && x.height >= 44, '✕ top left');
  assert.ok(q.y < 80 && q.height >= 44, 'search at the top');
  const heights = await pop.locator('.gv-row').evaluateAll(rs => rs.map(r => r.getBoundingClientRect().height));
  assert.ok(heights.length > 5 && heights.every(h => h >= 44), `44px rows: ${heights}`);
  assert.equal(await pop.locator('.gv-row[data-give-pick="botops"] .gv-role').isVisible(), true, 'the role shows after the name on a phone');
  assert.ok(await pop.evaluate(p => p.scrollWidth <= p.clientWidth + 1), 'no sideways scroll');
  await pop.locator('.gv-x').click();
  await pop.waitFor({state: 'detached'});
  assert.deepEqual(errors, []);
  await page.close();
  console.log('give-to: phone ok');
}

// Needs you (decide.ts): Give to… on a task of yours. Suggested is who asked and the bots the title names; the current
// owner shows ✓ but is not chosen, so Give stays off until a pick; Esc closes only the picker; Give sends the pick.
async function needsYou(browser: Browser): Promise<void> {
  const {page, errors} = await open(browser, {viewport: {width: 1440, height: 900}, at: '#/tasks'});
  await roster(page);
  await page.evaluate(() => localStorage.clear());
  const now = new Date().toISOString();
  const brief = {id: 't-ny', number: 7, title: 'Ask BotOps about the stuck run', status: 'open', owner_id: 'bot:eng', requester_id: 'human:sam',
    assignee_id: null, due_at: null, late: null, on_person: false, pipeline_id: null, pushed_past: false, snoozed: false, stage_id: null,
    stage_since: null, turn: null, version: 2};
  const item = {id: 't-ny', kind: 'task', line: 'Ask BotOps about the stuck run', since: now, task: brief, task_id: 't-ny'};
  await page.route('**/api/v2/needs-you**', route => route.fulfill({json: {items: [item]}}));
  const sent: unknown[] = [];
  await page.route('**/api/v2/tasks/t-ny/owner', route => { sent.push(route.request().postDataJSON()); return route.fulfill({json: {task: {...brief, owner_id: 'bot:botops', version: 3}}}); });
  // The app's own Needs you list must hold the item first: a refresh that lands after the screen opens otherwise
  // reads it as answered elsewhere and moves on.
  await page.evaluate(() => v2NeedsRead());
  await page.waitForFunction(() => (S.v2.needs || []).some(n => n.id === 't-ny'));
  assert.equal(await page.evaluate(i => decideOpen('t-ny', [i]), item), true);
  await page.locator('.decide [data-dc="give"]').click();
  const field = page.locator('.decide button[data-give-to]'), give = page.locator('.decide [data-dc="give-to"]');
  assert.equal(await field.locator('.gv-name').textContent(), 'Give to…');
  assert.equal(await give.isDisabled(), true, 'nothing chosen yet');
  await field.click();
  const pop = page.locator('.gv-pop:popover-open');
  await pop.waitFor();
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gv-row').evaluateAll(rs => rs.map(r => `${r.querySelector('.gv-name')?.textContent}: ${r.querySelector('.gv-why')?.textContent}`)),
    ['Sam: Asked for it', 'BotOps: Named in the task']);
  assert.deepEqual(await pop.locator('[aria-checked=true] .gv-name').allTextContents(), ['Engineer'], 'the current owner shows ✓');
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-give-to')), true, 'focus back on the field');
  assert.equal(await page.locator('.decide').isVisible(), true, 'Esc closes only the picker');
  assert.equal(await give.isDisabled(), true, 'the ✓ did not choose anyone');
  await field.click();
  await pop.waitFor();
  await page.keyboard.type('ops');
  await page.keyboard.press('Enter');
  await pop.waitFor({state: 'detached'});
  assert.equal(await give.isDisabled(), false);
  await give.click();
  for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(100);
  assert.deepEqual(sent.map(b => (b as {owner: string}).owner), ['botops'], 'Give sends the value the old list sent');

  // Suggested reads the whole task the screen loads, not only the item's brief (title and requester): a bot named only
  // in the body and the area tag's owner are suggested too, the same as the task's Owner menu (#385 in production).
  const bare = {...brief, id: 't-body', number: 9, title: 'The board shows the wrong stage', owner_id: 'bot:jev'};
  const full = {...bare, body: 'BotOps sees it on every run; Engineer last touched the stage code.', tags: [{id: 'g-bugs', key: 'bugs', owner: 'bot:b-release'}]};
  const bodyItem = {id: 't-body', kind: 'task', line: bare.title, since: now, task: bare, task_id: 't-body'};
  let reads = 0;
  await page.route('**/api/v2/tasks/t-body', route => { reads++; return route.fulfill({json: {task: full}}); });
  await page.unroute('**/api/v2/needs-you**');
  await page.route('**/api/v2/needs-you**', route => route.fulfill({json: {items: [bodyItem]}}));
  await page.evaluate(() => v2NeedsRead());
  await page.waitForFunction(() => (S.v2.needs || []).some(n => n.id === 't-body'));
  assert.equal(await page.evaluate(i => decideOpen('t-body', [i]), bodyItem), true);
  await page.locator('.decide .dc-md').waitFor();
  assert.equal(reads, 1, 'the screen read the task once');
  await page.locator('.decide [data-dc="give"]').click();
  await field.click();
  await pop.waitFor();
  assert.deepEqual(await pop.locator('[role=group]').first().locator('.gt-row').evaluateAll(rs => rs.map(r => `${r.querySelector('.gt-name')?.textContent}: ${r.querySelector('.gt-why')?.textContent}`)),
    ['Sam: Asked for it', 'BotOps: Named in the task', 'Engineer: Named in the task', 'Release Owner: Owns its area']);
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  assert.equal(reads, 1, 'opening the picker made no network call');

  // 👎 on a bot's question, Wrong person: the same field, ✓ on the task's owner, and Give to… sends the pick.
  const ask = {kind: 'ask', id: 'r-ny', task_id: 't-ask', requirement_id: 'r-ny', line: 'Which day does the beta start?', since: now,
    task: {...brief, id: 't-ask', number: 8, title: 'Plan the beta with Finance Owner', owner_id: 'bot:jev'},
    ask: {requirement_id: 'r-ny', kind: 'answer', line: 'Which day does the beta start?', asked_by: 'bot:eng', who: 'human:ana', choices: null,
      may: ['answer', 'give', 'thumbs'], met: false, shows: [], standing: 'Waiting on Ana', answer: null}};
  await page.unroute('**/api/v2/needs-you**');
  await page.route('**/api/v2/needs-you**', route => route.fulfill({json: {items: [ask]}}));
  const given: unknown[] = [];
  await page.route('**/api/v2/tasks/t-ask', route => route.fulfill({json: {task: {...ask.task, body: 'API Engineer has the dates.'}}}));
  await page.route('**/api/v2/tasks/t-ask/asks/r-ny/give', route => { given.push(route.request().postDataJSON()); return route.fulfill({json: {task: ask.task}}); });
  await page.evaluate(() => v2NeedsRead());
  await page.waitForFunction(() => (S.v2.needs || []).some(n => n.id === 'r-ny'));
  assert.equal(await page.evaluate(i => decideOpen('r-ny', [i]), ask), true);
  await page.locator('.decide .dc-md').waitFor();
  await page.locator('.decide [data-dc="down"]').click();
  await page.locator('.decide [data-dc-chip="person"]').click();
  const why = page.locator('.decide button[data-give-to]'), whyGive = page.locator('.decide [data-dc="why-give"]');
  assert.equal(await whyGive.isDisabled(), true, 'nothing chosen yet');
  await why.click();
  await pop.waitFor();
  assert.deepEqual(await pop.locator('[aria-checked=true] .gv-name').allTextContents(), ['Jev'], "the task's owner shows ✓");
  assert.equal(await pop.locator('[role=group]').first().locator('[data-give-pick="finance"]').count(), 1, 'a bot the title names is suggested');
  assert.equal(await pop.locator('[role=group]').first().locator('[data-give-pick="api"]').count(), 1, 'a bot only the body names is suggested');
  await page.keyboard.type('sam');
  await page.keyboard.press('Enter');
  await pop.waitFor({state: 'detached'});
  await whyGive.click();
  for (let i = 0; i < 50 && !given.length; i++) await page.waitForTimeout(100);
  assert.deepEqual(given.map(b => (b as {to: string}).to), ['human:sam']);

  // 👎 on a bot's OK ask: an OK shows its own content, so the task is read only when 👎 opens; Suggested then has the
  // bot its body names. The read lands while the picker is open: the picker stays and its pick is the one given.
  const okAsk = {...ask, id: 'r-ok', requirement_id: 'r-ok', task_id: 't-ok', line: 'OK to start the beta?',
    task: {...ask.task, id: 't-ok', number: 10, title: 'Start the beta'},
    ask: {...ask.ask, requirement_id: 'r-ok', kind: 'ok', line: 'OK to start the beta?', may: ['ok', 'no', 'give', 'thumbs']}};
  let okReads = 0, release: () => void = () => {};
  const held = new Promise<void>(r => { release = r; });
  await page.route('**/api/v2/tasks/t-ok', async route => { okReads++; await held; return route.fulfill({json: {task: {...okAsk.task, body: 'API Engineer has the dates.'}}}); });
  const okGiven: unknown[] = [];
  await page.route('**/api/v2/tasks/t-ok/asks/r-ok/give', route => { okGiven.push(route.request().postDataJSON()); return route.fulfill({json: {task: okAsk.task}}); });
  await page.unroute('**/api/v2/needs-you**');
  await page.route('**/api/v2/needs-you**', route => route.fulfill({json: {items: [okAsk]}}));
  await page.evaluate(() => v2NeedsRead());
  await page.waitForFunction(() => (S.v2.needs || []).some(n => n.id === 'r-ok'));
  assert.equal(await page.evaluate(i => decideOpen('r-ok', [i]), okAsk), true);
  await page.locator('.decide [data-dc="down"]').click();
  for (let i = 0; i < 50 && !okReads; i++) await page.waitForTimeout(100);
  assert.equal(okReads, 1, '👎 on an OK reads its task');
  await page.locator('.decide [data-dc-chip="person"]').click();
  await why.click();
  await pop.waitFor();
  assert.equal(await pop.locator('[role=group]').first().locator('[data-give-pick="api"]').count(), 0, 'before the read lands: the brief only');
  release();
  await page.waitForFunction(() => DEC_ABOUT.get('t-ok')?.state === 'ready');
  await page.waitForTimeout(200);
  assert.equal(await pop.isVisible(), true, 'the read landing does not close the open picker');
  await page.keyboard.type('sam');
  await page.keyboard.press('Enter');
  await pop.waitFor({state: 'detached'});
  assert.equal(await why.locator('.gt-name').textContent(), 'Sam', 'the pick is on the field on screen');
  await why.click();
  await pop.waitFor();
  assert.equal(await pop.locator('[role=group]').first().locator('[data-give-pick="api"]').count(), 1, 'opened again: a bot only the body names is suggested');
  await page.keyboard.press('Escape');
  await pop.waitFor({state: 'detached'});
  await whyGive.click();
  for (let i = 0; i < 50 && !okGiven.length; i++) await page.waitForTimeout(100);
  assert.deepEqual(okGiven.map(b => (b as {to: string}).to), ['human:sam'], 'the pick made while the read was on its way is given');
  assert.equal(okReads, 1, 'one read for the task');

  // The read lands after a pick, with the picker closed: the side draws again and keeps the pick, Give still on.
  const ok2 = {...okAsk, id: 'r-ok2', requirement_id: 'r-ok2', task_id: 't-ok2', task: {...okAsk.task, id: 't-ok2', number: 11},
    ask: {...okAsk.ask, requirement_id: 'r-ok2'}};
  let release2: () => void = () => {};
  const held2 = new Promise<void>(r => { release2 = r; });
  await page.route('**/api/v2/tasks/t-ok2', async route => { await held2; return route.fulfill({json: {task: {...ok2.task, body: 'API Engineer has the dates.'}}}); });
  await page.unroute('**/api/v2/needs-you**');
  await page.route('**/api/v2/needs-you**', route => route.fulfill({json: {items: [ok2]}}));
  await page.evaluate(() => v2NeedsRead());
  await page.waitForFunction(() => (S.v2.needs || []).some(n => n.id === 'r-ok2'));
  assert.equal(await page.evaluate(i => decideOpen('r-ok2', [i]), ok2), true);
  await page.locator('.decide [data-dc="down"]').click();
  await page.locator('.decide [data-dc-chip="person"]').click();
  await why.click();
  await pop.waitFor();
  await page.keyboard.type('sam');
  await page.keyboard.press('Enter');
  await pop.waitFor({state: 'detached'});
  await page.evaluate(() => { document.querySelector('.decide button[data-give-to]')!.setAttribute('data-before-read', ''); });
  release2();
  await page.waitForFunction(() => DEC_ABOUT.get('t-ok2')?.state === 'ready');
  await page.waitForFunction(() => !document.querySelector('.decide [data-before-read]'));
  assert.equal(await why.locator('.gt-name').textContent(), 'Sam', 'the pick survives the side drawing again');
  assert.equal(await whyGive.isDisabled(), false, 'Give stays on');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('give-to: needs you ok');
}

if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try { await desktop(browser); await phone(browser); await needsYou(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
