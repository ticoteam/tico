// Journey: the default-seed pages, as Dana, in one browser on one hub per viewport. It runs, in this order, what
// were six journeys: the task board and sheet (tasks), Needs you (needsYou), a new bot in every page (newBot, which
// also checks the DM's message bubble, sender, time and sideways scroll), Settings (settings), Finance (finance) and
// Strategy with goals (strategy). Each step opens its own page, keeps its own assertions and names itself in a
// failure ("[needsYou] ..."). Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, call, assertClean, drawn, expectFailure, shot, until, t} = require('./support/e2e.cjs');

// ---- e2e-tasks
const tasks = (() => {
  const TITLE = 'Draft the launch note';
  const CREATED = /[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M/;

  async function detail(page) {
    return page.evaluate(() => {
      const m = document.querySelector('#task-modal');
      const row = label => [...(m?.querySelectorAll('.prop') || [])]
        .find(r => r.querySelector('.prop-k')?.textContent.trim() === label)?.innerText.replace(/\s+/g, ' ').trim() || '';
      return {
        open: !!m?.open,
        title: m?.querySelector('.tmodal-title')?.textContent.trim() || '',
        status: m?.querySelector('.tstatus')?.textContent.trim() || '',
        created_at: m?.querySelector('.tmodal-age')?.getAttribute('title') || '',
        owner_id: row('Owner'),
        asked: row('Asked by'),
        history: m?.querySelector('.task-comments')?.innerText.replace(/\s+/g, ' ').trim() || '',
        canDone: !!m?.querySelector('[data-task-finish="done"]'),
        canClose: !!m?.querySelector('[data-task-finish="close"]'),
      };
    });
  }

  async function sheet(page, title) {
    await page.waitForFunction(text => {
      const m = document.querySelector('#task-modal');
      return m?.open && m.querySelector('.tmodal-title')?.textContent.trim() === text;
    }, title, {timeout: t(20000)});
    return detail(page);
  }

  async function waitStatus(page, re) {
    await page.waitForFunction(src => new RegExp(src, 'i').test(document.querySelector('#task-modal .tstatus')?.textContent || ''),
      re.source, {timeout: t(15000)});
  }

  async function pick(page, text) {
    await page.locator('.prop-pop:popover-open .tl-mi', {hasText: text}).first().click();
  }
  return async ({hub, browser, phone}) => {
    const where = phone ? 'phone' : 'desktop';
    const launch = (await must(hub, 'dana', 'POST', 'task-types', {name: 'Launch', steps: [
      {name: 'Plan', status: 'open'}, {name: 'Build', status: 'doing'}, {name: 'Ship', status: 'done'}]}, 'make the Launch type')).type;
    const ship = launch.steps.find(s => s.name === 'Ship');

    // Dana makes the task in the page, for the engineering-lead bot.
    const page = await open(browser, hub, 'dana', {phone, hash: `#/tasks?view=list&type=${launch.id}`});
    await page.click('#task-new');
    const form = page.locator('#task-create-form');
    await form.locator('input[name=title]').fill(TITLE);
    const owner = await form.locator('select[name=owner] option', {hasText: 'engineering-lead'}).getAttribute('value');
    await form.locator('select[name=owner]').selectOption(owner);
    await form.locator('select[name=type]').waitFor({timeout: t(20000)});   // the Type field appears once a second type exists
    await form.locator('select[name=type]').selectOption(launch.id);
    await form.locator('textarea[name=body]').fill('Write the note and send it to the list.');
    await form.locator('button[type=submit]').click();
    const row = page.locator('.tl-row', {hasText: TITLE}).first();
    await row.waitFor({timeout: t(20000)});
    const id = (await row.getAttribute('data-task-key')).slice(1);
    assertClean(page, `${where}: after create`);

    // The sheet draws what the task says. Dana asked for it, so she can close it; she is not its owner, so no Done.
    go(page, '#/task/' + id);
    let d = await sheet(page, TITLE);
    assert.match(d.owner_id, /engineering-lead/, `${where}: owner row names the bot`);
    assert.match(d.asked, /You/, `${where}: asked-by row names the requester`);
    assert.match(d.created_at, CREATED, `${where}: created time is drawn`);
    assert.match(d.status, /Plan/, `${where}: a typed task shows its first step`);
    assert.ok(d.canClose, `${where}: the requester can close`);
    assert.ok(!d.canDone, `${where}: the requester cannot mark it done`);
    assertClean(page, `${where}: sheet`);

    // Moved to Build from the Step menu. The history shows the move.
    await page.locator('#task-modal [data-prop="step"]').click();
    await pick(page, 'Build');
    await waitStatus(page, /Build/);
    await page.waitForFunction(() => document.querySelector('#task-modal .task-comments')?.innerText.includes('Build'),
      null, {timeout: t(15000)});
    d = await detail(page);
    assert.match(d.history, /Build/, `${where}: history shows the step move`);
    assertClean(page, `${where}: after the step move`);

    // The bot (the owner) finishes the work: the step moves to Ship, which is done.
    const current = await must(hub, 'engineering-lead', 'GET', `tasks/${id}`, undefined, 'the bot reads the task');
    const version = (current.task || current).version;
    await must(hub, 'engineering-lead', 'POST', `tasks/${id}`, {version, step: ship.id, status: 'done'}, 'the bot marks it done');
    await page.reload();
    d = await sheet(page, TITLE);
    assert.match(d.status, /Ship/, `${where}: the sheet shows the step the bot moved it to`);
    await page.waitForFunction(() => document.querySelector('#task-modal .task-comments')?.innerText.includes('Ship'),
      null, {timeout: t(15000)});
    d = await detail(page);
    assert.match(d.history, /Ship/, `${where}: history shows the bot's move`);
    assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'done', 'the task is done in the hub');
    assertClean(page, `${where}: after done`);

    // Dana accepts it by closing it: the sheet closes with the task. She opens it again: a closed task offers no close, and
    // its task menu reopens it.
    await page.locator('#task-modal [data-task-finish="close"]').click();
    await page.waitForFunction(() => !document.querySelector('#task-modal')?.open, null, {timeout: t(15000)});
    assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'closed', `${where}: the hub has it closed`);
    assertClean(page, `${where}: after close`);
    go(page, `#/tasks?view=list&type=${launch.id}`);
    go(page, '#/task/' + id);
    d = await sheet(page, TITLE);
    assert.ok(!d.canClose, `${where}: a closed task offers no close`);
    await page.locator('#task-modal [data-task-more]').click();
    await pick(page, 'Reopen');
    await page.waitForFunction(() => !!document.querySelector('#task-modal [data-task-finish="close"]'), null, {timeout: t(15000)});
    assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'open', `${where}: the hub has it reopened`);
    assertClean(page, `${where}: after reopen`);

    // The board for the type draws the card, and the page does not scroll sideways.
    go(page, `#/tasks?view=board&type=${launch.id}`);
    await page.locator(`.bcard[data-task-key="t${id}"]`).waitFor({timeout: t(20000)});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: board page scrolls sideways by ${overflow}px`);
    assertClean(page, `${where}: board`);
    await shot(page, `e2e-tasks-board-${where}`);
  };
})();

// ---- e2e-needs-you
const needsYou = (() => {
  const QUESTION = 'Ship the beta on Nov 3?';
  const ANSWER = 'Yes, Nov 3.';
  return async ({hub, browser, phone}) => {
    const where = phone ? 'phone' : 'desktop';
    const asked = await must(hub, 'engineering-lead', 'POST', 'messages', {
      to: 'dana', text: QUESTION, kind: 'ask', needs_action: true,
    }, 'the bot asks a question');
    const questionId = asked.id;
    const approval = await must(hub, 'builder-claude', 'POST', 'approvals', {
      kind: 'spend_limit', payload: {bot: 'builder-claude', daily_usd: 40},
    }, 'the bot asks for an approval');
    const approvalId = approval.id || approval.approval?.id;
    assert.ok(questionId && approvalId, `${where}: both are recorded`);

    const page = await open(browser, hub, 'dana', {phone, hash: '#/tasks?view=foryou'});
    const card = page.locator('[data-need-card]');
    await card.first().waitFor({timeout: t(20000)});
    assertClean(page, `${where}: needs you drawn`);

    // The question: answer it on its card.
    await page.locator(`[data-need-card="ask:${questionId}"]`).first().click();
    await page.locator('#need-card', {hasText: QUESTION}).first().waitFor({timeout: t(20000)});
    await page.locator('#need-card .dc-note textarea[aria-label="Your answer"]').fill(ANSWER);
    await page.locator('#need-card [data-dc="answer"]').first().click();
    // The card sends the answer when its undo toast runs out, so the hub is read until the answer is there.
    const reply = await until(async () => {
      const thread = await must(hub, 'dana', 'GET', `conversations/${asked.conversation_id}/messages`, undefined, 'read the thread');
      return (thread.messages || []).find(m => m.kind === 'answer' && m.in_reply_to === questionId);
    }, `${where}: the answer on the hub`);
    assert.equal(reply.body, ANSWER, `${where}: the hub has the answer to the question`);
    assertClean(page, `${where}: answered`);

    // The approval: approve it on its card.
    go(page, '#/tasks?view=foryou');
    await page.locator(`[data-need-card="approval:${approvalId}"]`).first().click();
    await page.locator('#need-card [data-dc="yes"]').first().click();
    await until(async () => {
      const decided = await must(hub, 'dana', 'GET', `approvals/${approvalId}`, undefined, 'read the approval');
      return decided.decision === 'approved';
    }, `${where}: the approval approved on the hub`);
    assertClean(page, `${where}: approved`);
    await shot(page, `e2e-needs-you-${where}`);
  };
})();

// ---- e2e-new-bot
const newBot = (() => {
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const TITLE = 'Send the pricing sheet to Acme';
  return async ({hub, browser, phone}) => {
    const where = phone ? 'phone' : 'desktop';
    await must(hub, 'dana', 'POST', 'bots', {slug: 'sales-helper', display_name: 'Sales Helper', status: 'active',
      description: 'Answers sales questions for the Acme team.'}, 'add Sales Helper');
    const bot = (await must(hub, 'dana', 'GET', 'bots/sales-helper')).id;
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
    const when = await bubble.evaluate(el => el.querySelector('time')?.getAttribute('datetime')
      || el.querySelector('time')?.getAttribute('title') || el.querySelector('[title]')?.getAttribute('title') || '');
    assert.ok(when.length > 0, `${where}: the message carries its time`);
    assert.ok(!(await page.locator('#bot-thread .thv-gone, #bot-thread .thv-error').count()), `${where}: thread loaded`);
    const chatOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(chatOverflow <= 1, `${where}: chat page scrolls sideways by ${chatOverflow}px`);
    assertClean(page, `${where}: chat`);

    // A task for it, made in the Tasks page: the owner choice is its id, labelled with its name.
    go(page, '#/tasks?view=list');
    await page.click('#task-new');
    const form = page.locator('#task-create-form');
    await form.locator('input[name=title]').fill(TITLE);
    const option = form.locator('select[name=owner] option', {hasText: 'Sales Helper'}).first();
    assert.equal(await option.getAttribute('value'), bot, `${where}: the owner choice is the bot's id`);
    await form.locator('select[name=owner]').selectOption(bot);
    await form.locator('textarea[name=body]').fill('The sheet is in Files; send it with a short note.');
    await form.locator('button[type=submit]').click();
    const row = page.locator('.tl-row', {hasText: TITLE}).first();
    await row.waitFor({timeout: t(20000)});
    const task = (await row.getAttribute('data-task-key')).slice(1);
    assert.equal((await must(hub, 'dana', 'GET', 'tasks/' + task)).task.owner_id, 'bot:' + bot, `${where}: the task's owner is bot:<id>`);
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
    await until(async () => (await call(hub, 'dana', 'GET', 'bots/revenue-helper')).body?.id === bot, `${where}: the new slug names it`);
    assert.equal((await call(hub, 'dana', 'GET', 'bots/sales-helper')).status, 404, `${where}: the old slug names nothing`);
    assertClean(page, `${where}: renamed`);

    // After the rename: Settings > Bots names it and links to its id; its settings show the new slug.
    go(page, '#/settings/bots');
    await drawn(page, '#set-bots', text => text.includes('Sales Helper'), `${where}: the bots list names it`);
    const links = await page.locator('#set-bots a[href^="#/bot/"]').evaluateAll(as => as.map(a => a.getAttribute('href')));
    assert.ok(links.includes('#/bot/' + encodeURIComponent(bot)), `${where}: the bots list links to its id`);
    assert.ok(!links.some(href => /sales-helper|revenue-helper/.test(href)), `${where}: no link names it by slug`);
    go(page, `#/bot/${encodeURIComponent(bot)}/settings`);
    await until(async () => (await page.locator('#pane-settings input[name=slug]').first().inputValue().catch(() => '')) === 'revenue-helper',
      `${where}: its settings show the new slug`);

    // A link by its new slug opens the same page; its task is still its own.
    go(page, '#/bot/revenue-helper');
    await page.waitForFunction(id => location.hash === '#/bot/' + id, bot, {timeout: t(20000)});
    await drawn(page, '.bot-nameline h1', text => text.includes('Sales Helper'), `${where}: the renamed bot's page`);
    const mine = await must(hub, 'dana', 'GET', `tasks?owner=${encodeURIComponent('bot:' + bot)}`);
    assert.ok((Array.isArray(mine) ? mine : mine.tasks || mine.items).some(item => item.id === task), `${where}: the task stays the bot's`);
    go(page, '#/task/' + task);
    await page.waitForFunction(text => document.querySelector('#task-modal')?.open
      && document.querySelector('#task-modal .tmodal-title')?.textContent.trim() === text, TITLE, {timeout: t(20000)});
    const owner = await page.evaluate(() => [...document.querySelectorAll('#task-modal .prop')]
      .find(r => r.querySelector('.prop-k')?.textContent.trim() === 'Owner')?.innerText || '');
    assert.match(owner, /Sales Helper|revenue-helper/, `${where}: the task's owner row names the bot`);
    assert.doesNotMatch(owner, new RegExp(bot), `${where}: the owner row shows no id`);
    assertClean(page, `${where}: after the rename`);
  };
})();

// ---- e2e-settings
const settings = (() => {
  return async ({hub, browser, phone}) => {
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
    const bots = await drawn(page, '#set-bots', text => text.includes('engineering-lead'), `${where}: the bots tab draws the bot`);
    assert.match(bots, /engineering-lead/, `${where}: bots tab names the bot`);
    assertClean(page, `${where}: bots`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: settings scrolls sideways by ${overflow}px`);
  };
})();

// ---- e2e-finance
const finance = (() => {
  return async ({hub, browser, phone}) => {
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
  return async ({hub, browser, phone}) => {
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
    catch (error) { error.message = `[${name}, ${ctx.phone ? 'phone' : 'desktop'}] ${error.message}`; throw error; }
  }
});
