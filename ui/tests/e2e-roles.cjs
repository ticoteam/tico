// Journey: who sees which tasks, as the four kinds of teammate, each in the page. Three tasks are made through the API by
// the people and bots that make them in the product:
//   T1  public: the engineering-lead bot's, everyone reads it.
//   P1  private, person-only: Omar asked for it, Dana owns it (its parties are Omar and Dana).
//   P2  private, bot-only: release-manager asks, builder-claude owns it, and Gina is brought in as the guest by a
//       subtask builder-claude hands her.
// Priya is the bot manager: she is above builder-claude on the org chart (the seed `priya-leads` puts engineering-lead
// under her). Task reads follow the org chart, not co-ownership (docs/tasks.md "Managers acting on tasks").
// Expected: Dana sees all three (she is above the bots and a party to P1); Omar sees T1 and P1; Priya sees T1 and P2
// (a manager reads a bot report's private tasks, but nothing else private); Gina sees T1 and P2 (the guest). A private
// task a teammate may not read is refused by the hub, and the page does not open it. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, call, assertClean, expectFailure, t, SEEDS} = require('./support/e2e.cjs');

const TITLES = {T1: 'Team launch checklist', P1: 'Omar private review', P2: 'Private release cut'};
const SEES = {
  dana: {T1: true, P1: true, P2: true},
  omar: {T1: true, P1: true, P2: false},
  priya: {T1: true, P1: false, P2: true},
  gina: {T1: true, P1: false, P2: true},
};

journey('e2e-roles', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const idOf = reply => (reply.task || reply).id;

  const T1 = idOf(await must(hub, 'dana', 'POST', 'tasks', {title: TITLES.T1, body: 'Everyone reads this one.',
    owner_id: 'engineering-lead'}, 'the public task'));
  const P1 = idOf(await must(hub, 'omar', 'POST', 'tasks', {title: TITLES.P1, body: 'Omar and Dana only.',
    owner_id: 'dana', private: true}, 'the person-only private task'));
  const P2 = idOf(await must(hub, 'release-manager', 'POST', 'tasks', {title: TITLES.P2, body: 'Bots, a manager and a guest.',
    owner_id: 'builder-claude', private: true}, 'the bot-only private task'));
  await must(hub, 'builder-claude', 'POST', 'tasks', {title: 'Gina checks the cut', body: 'Gina is brought in here.',
    owner_id: 'gina', private: true, relations: [{task: P2, kind: 'parent'}]}, 'builder-claude brings Gina in');
  const ids = {T1, P1, P2};

  for (const who of Object.keys(SEES)) {
    const page = await open(browser, hub, who, {phone, hash: '#/tasks?view=list'});
    await page.locator('.tl-row', {hasText: TITLES.T1}).first().waitFor({timeout: t(20000)});
    for (const key of ['T1', 'P1', 'P2']) {
      const shown = await page.locator('.tl-row', {hasText: TITLES[key]}).count();
      assert.equal(shown > 0, SEES[who][key], `${where}: ${who} ${SEES[who][key] ? 'sees' : 'does not see'} ${key}`);
    }
    assertClean(page, `${where}: ${who} list`);

    // A task the teammate may read opens its sheet; one they may not is refused by the hub and does not open.
    for (const key of ['P1', 'P2']) {
      if (SEES[who][key]) {
        go(page, `#/task/${ids[key]}`);
        await page.waitForFunction(title => document.querySelector('#task-modal')?.open
          && document.querySelector('#task-modal .tmodal-title')?.textContent.trim() === title, TITLES[key], {timeout: t(20000)});
      } else {
        const refused = await call(hub, who, 'GET', `tasks/${ids[key]}`);
        assert.ok(refused.status === 403 || refused.status === 404, `${where}: ${who} is refused ${key}: ${refused.status}`);
        expectFailure(page, refused.status, `^/api/v2/tasks/${ids[key]}$`, 'GET');
        go(page, `#/task/${ids[key]}`);
        await page.waitForTimeout(t(2000));
        const opened = await page.evaluate(title => document.querySelector('#task-modal')?.open
          && document.querySelector('#task-modal .tmodal-title')?.textContent.trim() === title, TITLES[key]);
        assert.ok(!opened, `${where}: ${who} does not open ${key}`);
      }
      assertClean(page, `${where}: ${who} ${key}`);
    }
  }
}, [...SEEDS, 'priya-leads']);
