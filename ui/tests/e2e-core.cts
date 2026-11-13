// Journey: the default-seed pages, as Dana, in one browser on one hub per viewport. It runs, in this order, what
// were six journeys: a pipeline's task and its sheet (tasks), Needs you (needsYou), a new bot in every page (newBot, which
// also checks the DM's message bubble, sender, time and sideways scroll), Settings (settings), Finance (finance) and
// Strategy with goals (strategy). Each step opens its own page, keeps its own assertions and names itself in a
// failure ("[needsYou] ..."). Runs on a phone and a desktop.
import type {Page} from 'playwright';
import type * as E2E from './support/e2e.cts';
import type {PassContext} from './support/e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {journey, open, go, must, call, assertClean, drawn, expectFailure, shot, until, t} = require('./support/e2e.cts') as E2E.Api;

interface TaskRecord {
  id: string;
  version: number;
  status: string;
  stage?: {name: string} | null;
  to_leave?: {requirement_id: string; met: boolean; answer: {value: string} | null}[];
}

// ---- e2e-tasks
const tasks = (() => {
  const TITLE = 'Draft the launch note';
  const CREATED = /[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M/;

  async function detail(page: Page) {
    return page.evaluate(() => {
      const m = document.querySelector('#task-modal') as HTMLDialogElement | null;
      const row = (label: string) => [...(m?.querySelectorAll<HTMLElement>('.prop') || [])]
        .find(r => r.querySelector<HTMLElement>('.prop-k')?.textContent!.trim() === label)?.innerText.replace(/\s+/g, ' ').trim() || '';
      return {
        open: !!m?.open,
        id: m?.dataset.task || '',
        title: m?.querySelector<HTMLElement>('.tmodal-title')?.textContent!.trim() || '',
        stage: row('Stage'),
        created_at: m?.querySelector<HTMLElement>('.tmodal-age')?.getAttribute('title') || '',
        owner_id: row('Owner'),
        asked: row('Asked by'),
        history: m?.querySelector<HTMLElement>('.task-comments')?.innerText.replace(/\s+/g, ' ').trim() || '',
        canDone: !!m?.querySelector<HTMLElement>('[data-task-finish="done"]'),
        canClose: !!m?.querySelector<HTMLElement>('[data-task-finish="close"]'),
      };
    });
  }

  async function sheet(page: Page, title: string) {
    await page.waitForFunction(text => {
      const m = document.querySelector('#task-modal') as HTMLDialogElement | null;
      return m?.open && m.querySelector<HTMLElement>('.tmodal-title')?.textContent!.trim() === text;
    }, title, {timeout: t(20000)});
    return detail(page);
  }

  // A task's stage is its Stage property (the header carries no state).
  async function waitStage(page: Page, re: RegExp) {
    await page.waitForFunction(src => new RegExp(src, 'i').test([...document.querySelectorAll<HTMLElement>('#task-modal .prop')]
      .find(r => r.querySelector<HTMLElement>('.prop-k')?.textContent!.trim() === 'Stage')?.innerText || ''), re.source, {timeout: t(15000)});
  }
  async function waitHistory(page: Page, text: string) {
    await page.waitForFunction(text => document.querySelector<HTMLElement>('#task-modal .task-comments')?.innerText.includes(text),
      text, {timeout: t(15000)});
  }

  async function pick(page: Page, text: string) {
    await page.locator('.prop-pop:popover-open .tl-mi', {hasText: text}).first().click();
  }
  // Reopen, from the task menu, with the note a reopen needs.
  async function reopen(page: Page, why: string) {
    await page.locator('#task-modal [data-task-more]').click();
    await pick(page, 'Reopen');
    const entry = page.locator('.prop-pop:popover-open .tl-menu-q').first();
    await entry.fill(why);
    await entry.press('Enter');
  }
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    const pipeline = phone ? 'launch-phone' : 'launch';
    await must(hub, 'dana', 'POST', `pipelines/${pipeline}`, {name: phone ? 'Launch on phone' : 'Launch', goal: 'Launch notes go out.',
      stages: ['Plan', 'Build', 'Ship'].map(name => ({name, owner: 'assignee'}))}, 'make the Launch pipeline');

    // Dana makes the task in the page, for the engineering-manager bot, in the Launch pipeline: it opens once made.
    const page = await open(browser, hub, 'dana', {phone, hash: `#/tasks?pipeline=${pipeline}`});
    await page.click('#task-new');
    const form = page.locator('#task-create-form');
    await form.locator('input[name=title]').fill(TITLE);
    const owner = await form.locator('select[name=owner] option', {hasText: 'engineering-manager'}).getAttribute('value');
    await form.locator('select[name=owner]').selectOption(owner!);
    await form.locator(`select[name=pipeline] option[value="${pipeline}"]`).waitFor({state: 'attached', timeout: t(20000)});
    assert.equal(await form.locator('select[name=pipeline]').inputValue(), pipeline, `${where}: the form files it in the pipeline shown`);
    await form.locator('textarea[name=body]').fill('Write the note and send it to the list.');
    await form.locator('button[type=submit]').click();
    let d = await sheet(page, TITLE);
    const id = d.id;
    assert.ok(id, `${where}: the new task opens`);
    await page.keyboard.press('Escape');
    await page.locator(`#task-body [data-task="${id}"]`).waitFor({timeout: t(20000)});
    assertClean(page, `${where}: after create`);

    // The sheet draws what the task says. Dana asked for it, so she can close it; she is not its owner, so no Done.
    go(page, '#/task/' + id);
    d = await sheet(page, TITLE);
    assert.match(d.owner_id, /engineering-manager/, `${where}: owner row names the bot`);
    assert.match(d.asked, /You/, `${where}: asked-by row names the requester`);
    assert.match(d.created_at, CREATED, `${where}: created time is drawn`);
    assert.match(d.stage, /Plan/, `${where}: a new task is in the pipeline's first stage`);
    assert.ok(d.canClose, `${where}: the requester can close`);
    assert.ok(!d.canDone, `${where}: the requester cannot mark it done`);
    assertClean(page, `${where}: sheet`);

    // Moved on to Build from the Stage menu. The history shows the move.
    await page.locator('#task-modal [data-prop="stage"]').click();
    await pick(page, 'Move on');
    await waitStage(page, /Build/);
    await waitHistory(page, 'moved it to Build');
    assertClean(page, `${where}: after the move`);

    // The bot (the owner) moves it to Ship and finishes it there: in the last stage, done is Done.
    const read = async (who: string) => (await must<{task: TaskRecord}>(hub, who, 'GET', `tasks/${id}`, undefined, `${who} reads the task`)).task;
    await must(hub, 'engineering-manager', 'POST', `tasks/${id}/move`, {to: 'Ship', version: (await read('engineering-manager')).version}, 'the bot moves it to Ship');
    await must(hub, 'engineering-manager', 'POST', `tasks/${id}/done`, {version: (await read('engineering-manager')).version}, 'the bot marks it done');
    assert.equal((await read('dana')).status, 'done', 'the task is done in the hub');
    await page.reload();
    d = await sheet(page, TITLE);
    await waitHistory(page, 'moved it to Ship');
    d = await detail(page);
    assert.match(d.stage, /Done/, `${where}: the sheet shows it done`);
    assert.ok(!d.canClose && !d.canDone, `${where}: a done task offers neither Done nor Close`);
    assertClean(page, `${where}: after done`);

    // Dana reopens it from its task menu, saying why, then closes it: the sheet closes with the task. Opened again, a closed task
    // offers no close.
    await reopen(page, 'The note needs the new date');
    await page.waitForFunction(() => !!document.querySelector<HTMLElement>('#task-modal [data-task-finish="close"]'), null, {timeout: t(15000)});
    assert.equal((await read('dana')).status, 'open', `${where}: the hub has it reopened`);
    assertClean(page, `${where}: after reopen`);
    await page.locator('#task-modal [data-task-finish="close"]').click();
    await page.waitForFunction(() => !(document.querySelector('#task-modal') as HTMLDialogElement | null)?.open, null, {timeout: t(15000)});
    assert.equal((await read('dana')).status, 'closed', `${where}: the hub has it closed`);
    assertClean(page, `${where}: after close`);
    go(page, '#/task/' + id);
    d = await sheet(page, TITLE);
    assert.ok(!d.canClose, `${where}: a closed task offers no close`);
    await reopen(page, 'Send it after all');
    await page.waitForFunction(() => !!document.querySelector<HTMLElement>('#task-modal [data-task-finish="close"]'), null, {timeout: t(15000)});
    assert.equal((await read('dana')).status, 'open', `${where}: the hub has it reopened again`);
    assertClean(page, `${where}: after the second reopen`);

    // The pipeline's columns draw the task, and the page does not scroll sideways.
    go(page, `#/tasks?pipeline=${pipeline}&layout=columns`);
    // A phone shows one stage at a time, under its tabs.
    await page.locator(phone ? `.pv-phone .tk-card[data-task="${id}"]` : `.tk-card[data-task="${id}"]`).waitFor({timeout: t(20000)});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: the columns scroll the page sideways by ${overflow}px`);
    assertClean(page, `${where}: columns`);
    await shot(page, `e2e-tasks-columns-${where}`);
  };
})();

// ---- e2e-needs-you
const needsYou = (() => {
  // A bot asks Dana on two of its own tasks: a question with two choices, and an OK. A task is one row of You (its newest
  // ask), and the row opens its card.
  const QUESTION = 'Should we ship the beta on Nov 3?';
  const OK = 'OK a $40 daily limit for builder-claude';
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    const read = async (id: string) => (await must<{task: TaskRecord}>(hub, 'dana', 'GET', `tasks/${id}`, undefined, 'read the task')).task;
    const ask = async (title: string, body: Record<string, unknown>, what: string) => {
      const id = (await must<{task: {id: string}}>(hub, 'engineering-manager', 'POST', 'tasks', {title, body: 'For the beta.', owner: 'engineering-manager'},
        'the bot files its task')).task.id;
      const rid = (await must<{requirement_id: string}>(hub, 'engineering-manager', 'POST', `tasks/${id}/asks`,
        {to: 'dana', version: (await read(id)).version, ...body}, what)).requirement_id;
      assert.ok(rid, `${where}: ${what} is recorded`);
      return async () => (await read(id)).to_leave?.find(r => r.requirement_id === rid);
    };
    const question = await ask('Plan the beta', {line: QUESTION, choices: ['Yes', 'No']}, 'the bot asks a question');
    const ok = await ask('Set the beta budget', {line: OK, kind: 'ok'}, 'the bot asks for an OK');

    const page = await open(browser, hub, 'dana', {phone, hash: '#/tasks'});
    // This OK binds no content (no grant), so its card's preview read is a 404 and the card shows the ask's own line.
    expectFailure(page, 404, '^/api/v2/oks/[^/]+$', 'GET');
    const row = (text: string) => page.locator('#task-body [data-need]', {hasText: text}).first();
    // An OK's row reads its task's title; the ask's own line is the row's tooltip (tasks-page.ts needsRowHTML).
    const okRow = page.locator(`#task-body [data-need][title="${OK}"]`).first();
    await row(QUESTION).waitFor({timeout: t(20000)});
    await okRow.waitFor({timeout: t(20000)});
    assertClean(page, `${where}: needs you drawn`);

    // The question: answer it on its card.
    await row(QUESTION).click();
    const card = page.locator('#need-card');
    await card.locator('.dc-q', {hasText: QUESTION}).first().waitFor({timeout: t(20000)});
    await card.locator('[data-dc-choice="Yes"]').first().click();
    // The card sends the answer when its undo runs out, so the hub is read until the answer is there.
    const reply = await until(async () => (await question())?.answer?.value, `${where}: the answer on the hub`);
    assert.equal(reply, 'Yes', `${where}: the hub has the answer to the question`);
    assertClean(page, `${where}: answered`);

    // The OK: the card goes on to it (or its row opens it), and she approves it there.
    if (!await card.evaluate(c => (c as HTMLDialogElement).open)) await okRow.click();
    await card.locator('.dc-q', {hasText: OK}).first().waitFor({timeout: t(20000)});
    await card.locator('[data-dc="approve"]:not([disabled])').first().click();
    await until(async () => (await ok())?.met, `${where}: the OK given on the hub`);
    assertClean(page, `${where}: approved`);
    await shot(page, `e2e-needs-you-${where}`);
  };
})();

// ---- e2e-new-bot
const newBot = (() => {
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const TITLE = 'Send the pricing sheet to Acme';
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    await must(hub, 'dana', 'POST', 'bots', {slug: 'sales-helper', display_name: 'Sales Helper', status: 'active',
      description: 'Answers sales questions for the Acme team.'}, 'add Sales Helper');
    const bot = (await must<{id: string}>(hub, 'dana', 'GET', 'bots/sales-helper')).id;
    assert.match(bot, UUID, `${where}: a new bot's id is a UUIDv7`);

    // A link by its slug moves to its id; the page names it.
    const page = await open(browser, hub, 'dana', {phone, hash: '#/bot/sales-helper/dm'});
    // No team icon is set on the scenario install: Settings asks for it and is answered 404 ("No team icon is set").
    expectFailure(page, 404, '^/api/v2/team/icon$', 'GET');
    expectFailure(page, 404, '^/api/v2/threads/dm$', 'GET');     // the DM is made by the first message
    await page.waitForFunction(id => location.hash.startsWith('#/bot/' + id), bot, {timeout: t(20000)});
    await drawn(page, '.bot-nameline h1', text => text.includes('Sales Helper'), `${where}: the bot's page names it`);

    // DM: the message goes to the bot, by its id.
    const composer = page.locator('#bot-thread .thv-composer textarea.p-text').first();
    await composer.waitFor({timeout: t(20000)});
    const sent = page.waitForRequest(r => r.method() === 'POST' && /^\/api\/v2\/threads\/dm$/.test(new URL(r.url()).pathname));
    await composer.fill('What did we sell last week?');
    await page.locator('#bot-thread .thv-composer button.p-send').first().click();
    assert.deepEqual((await sent).postDataJSON().members, ['bot:' + bot], `${where}: the DM names the bot by its id`);
    const bubble = page.locator('#bot-thread [data-message]', {hasText: 'What did we sell last week?'}).first();
    await bubble.waitFor({timeout: t(20000)});
    // What e2e-chat checked on a DM: the bubble names its sender and carries its time, the thread loaded, no sideways scroll.
    const who = (await bubble.locator('.th-who').first().innerText()).trim();
    assert.ok(who.length > 0, `${where}: the message names its sender`);
    const when = await bubble.evaluate(el => el.querySelector<HTMLElement>('time')?.getAttribute('datetime')
      || el.querySelector<HTMLElement>('time')?.getAttribute('title') || el.querySelector<HTMLElement>('[title]')?.getAttribute('title') || '');
    assert.ok(when.length > 0, `${where}: the message carries its time`);
    assert.ok(!(await page.locator('#bot-thread .thv-gone, #bot-thread .thv-error').count()), `${where}: thread loaded`);
    const chatOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(chatOverflow <= 1, `${where}: chat page scrolls sideways by ${chatOverflow}px`);
    assertClean(page, `${where}: chat`);

    // A task for it, made in the Tasks page: the owner choice is its id, labelled with its name.
    go(page, '#/tasks');
    await page.click('#task-new');
    const form = page.locator('#task-create-form');
    await form.locator('input[name=title]').fill(TITLE);
    const option = form.locator('select[name=owner] option', {hasText: 'Sales Helper'}).first();
    assert.equal(await option.getAttribute('value'), bot, `${where}: the owner choice is the bot's id`);
    await form.locator('select[name=owner]').selectOption(bot);
    await form.locator('textarea[name=body]').fill('The sheet is in Files; send it with a short note.');
    await form.locator('button[type=submit]').click();
    // The new task opens once made.
    await page.waitForFunction(text => (document.querySelector('#task-modal') as HTMLDialogElement | null)?.open
      && document.querySelector<HTMLElement>('#task-modal .tmodal-title')?.textContent!.trim() === text, TITLE, {timeout: t(20000)});
    const task = (await page.locator('#task-modal').getAttribute('data-task'))!;
    assert.equal((await must<{task: {owner_id: string}}>(hub, 'dana', 'GET', 'tasks/' + task)).task.owner_id, 'bot:' + bot, `${where}: the task's owner is bot:<id>`);
    assertClean(page, `${where}: task made`);

    // Rename its slug in its settings.
    go(page, `#/bot/${encodeURIComponent(bot)}/settings`);
    const editor = page.locator('#pane-settings form').first();
    await editor.waitFor({timeout: t(20000)});
    await editor.locator('details[data-bot-advanced] summary').click();
    const slug = editor.locator('input[name=slug]');
    assert.equal(await slug.inputValue(), 'sales-helper', `${where}: the slug field shows its slug`);
    await slug.fill('revenue-helper');
    const saved = page.waitForResponse(r => r.request().method() === 'POST'
      && new URL(r.url()).pathname === `/api/v2/bots/${bot}/definition`);
    await editor.locator('button[type=submit]').click();
    assert.equal((await saved).status(), 200, `${where}: the rename saves`);
    await until(async () => (await call<{id: string}>(hub, 'dana', 'GET', 'bots/revenue-helper')).body?.id === bot, `${where}: the new slug names it`);
    assert.equal((await call(hub, 'dana', 'GET', 'bots/sales-helper')).status, 404, `${where}: the old slug names nothing`);
    assertClean(page, `${where}: renamed`);

    // After the rename: Settings > Bots names it and links to its id; its settings show the new slug.
    go(page, '#/settings/bots');
    await drawn(page, '#set-bots', text => text.includes('Sales Helper'), `${where}: the bots list names it`);
    const links = await page.locator('#set-bots a[href^="#/bot/"]').evaluateAll(as => as.map(a => a.getAttribute('href')!));
    assert.ok(links.includes('#/bot/' + encodeURIComponent(bot)), `${where}: the bots list links to its id`);
    assert.ok(!links.some(href => /sales-helper|revenue-helper/.test(href)), `${where}: no link names it by slug`);
    go(page, `#/bot/${encodeURIComponent(bot)}/settings`);
    await until(async () => (await page.locator('#pane-settings input[name=slug]').first().inputValue().catch(() => '')) === 'revenue-helper',
      `${where}: its settings show the new slug`);

    // A link by its new slug opens the same page; its task is still its own.
    go(page, '#/bot/revenue-helper');
    await page.waitForFunction(id => location.hash === '#/bot/' + id, bot, {timeout: t(20000)});
    await drawn(page, '.bot-nameline h1', text => text.includes('Sales Helper'), `${where}: the renamed bot's page`);
    const mine = await must<{id: string}[] | {tasks?: {id: string}[]; items?: {id: string}[]}>(hub, 'dana', 'GET', `tasks?owner=${encodeURIComponent('bot:' + bot)}`);
    assert.ok((Array.isArray(mine) ? mine : mine.tasks || mine.items)!.some(item => item.id === task), `${where}: the task stays the bot's`);
    go(page, '#/task/' + task);
    await page.waitForFunction(text => (document.querySelector('#task-modal') as HTMLDialogElement | null)?.open
      && document.querySelector<HTMLElement>('#task-modal .tmodal-title')?.textContent!.trim() === text, TITLE, {timeout: t(20000)});
    const owner = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('#task-modal .prop')]
      .find(r => r.querySelector<HTMLElement>('.prop-k')?.textContent!.trim() === 'Owner')?.innerText || '');
    assert.match(owner, /Sales Helper|revenue-helper/, `${where}: the task's owner row names the bot`);
    assert.doesNotMatch(owner, new RegExp(bot), `${where}: the owner row shows no id`);
    assertClean(page, `${where}: after the rename`);
  };
})();

// ---- e2e-settings
const settings = (() => {
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    const page = await open(browser, hub, 'dana', {phone, hash: '#/settings/people'});
    // No team icon is set on the scenario install, so the icon request Settings makes (when its icon control is drawn, on
    // whichever tab loads first) is answered 404 ("No team icon is set"). Expected on every tab of this journey.
    expectFailure(page, 404, '^/api/v2/team/icon$', 'GET');
    const text = await drawn(page, '#main', text => ['Dana', 'Omar', 'Priya', 'Gina'].every(name => text.includes(name)),
      `${where}: the people tab draws the four people`);
    for (const name of ['Dana', 'Omar', 'Priya', 'Gina']) assert.match(text, new RegExp(name), `${where}: people tab names ${name}`);
    assertClean(page, `${where}: people`);

    go(page, '#/settings/bots');
    // The bots list draws into #set-bots after its skeleton rows load: wait for the bot's row there, not elsewhere on the page.
    const bots = await drawn(page, '#set-bots', text => text.includes('engineering-manager'), `${where}: the bots tab draws the bot`);
    assert.match(bots, /engineering-manager/, `${where}: bots tab names the bot`);
    assertClean(page, `${where}: bots`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: settings scrolls sideways by ${overflow}px`);
  };
})();

// ---- e2e-finance
const finance = (() => {
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    const page = await open(browser, hub, 'dana', {phone, hash: '#/finance'});
    await page.locator('#main', {hasText: 'Revenue'}).first().waitFor({timeout: t(20000)});
    // The balance sheet lists the accounts Finance was set up with.
    await page.locator('#main button, #main a', {hasText: /^Balance sheet$/}).first().click();
    const text = await drawn(page, '#main', text => ['Bank', "Owner's equity"].every(account => text.includes(account)),
      `${where}: the balance sheet draws its accounts`);
    for (const account of ['Bank', "Owner's equity"]) {
      assert.match(text, new RegExp(account), `${where}: balance sheet draws ${account}`);
    }
    assertClean(page, `${where}: finance`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: finance scrolls sideways by ${overflow}px`);
  };
})();

// ---- e2e-strategy
const strategy = (() => {
  const BET = 'Win the small-team segment this quarter';
  const GOAL = 'Reach 100 paying teams';
  return async ({hub, browser, phone}: PassContext) => {
    const where = phone ? 'phone' : 'desktop';
    await must(hub, 'dana', 'POST', 'strategy/bets', {statement: BET, why: 'Small teams close fastest.', status: 'active'}, 'make a bet');
    await must(hub, 'dana', 'POST', 'goals', {title: GOAL, owner: 'dana', top: true}, 'make a goal');

    const page = await open(browser, hub, 'dana', {phone, hash: '#/strategy'});
    await page.locator('#main', {hasText: BET}).first().waitFor({timeout: t(20000)});
    assertClean(page, `${where}: strategy`);

    go(page, '#/strategy/goals');
    await page.locator('#main', {hasText: GOAL}).first().waitFor({timeout: t(20000)});
    assertClean(page, `${where}: goals`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: strategy scrolls sideways by ${overflow}px`);
  };
})();

const STEPS = {tasks, needsYou, newBot, settings, finance, strategy};

journey('e2e-core', async ctx => {
  for (const [name, step] of Object.entries(STEPS)) {
    try { await step(ctx); }
    catch (error) { (error as Error).message = `[${name}, ${ctx.phone ? 'phone' : 'desktop'}] ${(error as Error).message}`; throw error; }
    finally { for (const context of ctx.browser.contexts()) await context.close().catch(() => {}); }   // each step's pages end with it
  }
});
