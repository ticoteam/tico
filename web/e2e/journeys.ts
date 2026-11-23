// The web app's journeys: the few runs that prove it works end to end (plan/cutover.html, step 2). Each one starts its
// own made-up company (hub/seeded.py: Acme, Dana the owner, nothing real), signs in as Dana with the local owner link,
// and clicks through the app's build as a person does. A journey fails on any page error, console error or API refusal
// it did not expect. What a click wrote is read back from the server (/_e2e/sql, /_e2e/sent: only that test server has
// them). Run: `npm run e2e` (builds first), `npm run e2e:repeat` (each journey three times).
import { expect, test as base, type Browser, type BrowserContextOptions, type Page } from '@playwright/test';
import { startSeeded, type Seeded } from './hub/seeded.ts';

const DESKTOP: BrowserContextOptions = { viewport: { width: 1440, height: 900 } };
const PHONE: BrowserContextOptions = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 };

/** What went wrong on a page the journey did not expect: page errors, console errors, refused or failed API calls. */
type Watched = Page & { problems: string[] };

const test = base.extend<{ hub: Seeded }>({
  // A fresh company for each journey (and each repeat): no run sees what another one did.
  // Playwright reads a fixture's needs from its first argument's pattern: this one needs no other fixture
  // eslint-disable-next-line no-empty-pattern
  hub: async ({}, run) => {
    const hub = await startSeeded();
    try {
      await run(hub);
    } finally {
      await hub.stop();
    }
  },
});

/** A page signed in as Dana (the local owner link, as a loopback install signs in), watched from its first load. */
async function open(browser: Browser, hub: Seeded, at: string, options: BrowserContextOptions = DESKTOP): Promise<Watched> {
  const context = await browser.newContext(options);
  const page = Object.assign(await context.newPage(), { problems: [] as string[] });
  page.on('pageerror', (e) => page.problems.push(`page error: ${e.message.slice(0, 300)}`));
  page.on('console', (m) => {
    // a refused call is counted once, as its answer below
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) page.problems.push(`console: ${m.text().slice(0, 300)}`);
  });
  page.on('response', (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith('/api/') && r.status() >= 400) page.problems.push(`${r.status()} ${r.request().method()} ${path}`);
  });
  page.on('requestfailed', (r) => {
    const path = new URL(r.url()).pathname;
    // a request the page dropped itself (a navigation, a closed stream) is not a failure
    if (path.startsWith('/api/') && r.failure()?.errorText !== 'net::ERR_ABORTED') page.problems.push(`failed ${r.method()} ${path}`);
  });
  await page.goto(`${hub.url}/api/v2/local-signin?token=${encodeURIComponent(hub.token)}&next=${encodeURIComponent(at)}`);
  return page;
}

function clean(page: Watched) {
  expect(page.problems, 'no page errors, console errors or refused calls').toEqual([]);
}

/** A call to the test server as Dana (her bearer): the API, or the test routes under /_e2e. */
async function call<T = unknown>(hub: Seeded, path: string, method = 'GET'): Promise<T> {
  const r = await fetch(`${hub.url}${path}`, { method, headers: { Authorization: `Bearer ${hub.tokens.dana}` } });
  expect(r.ok, `${method} ${path}: ${r.status}`).toBe(true);
  return (await r.json()) as T;
}
const api = <T = unknown>(hub: Seeded, path: string) => call<T>(hub, `/api/v2/${path}`);
const sql = <T = Record<string, unknown>>(hub: Seeded, query: string) => call<T[]>(hub, `/_e2e/sql?q=${encodeURIComponent(query)}`);

/** Whether the page head's title shows every letter (its words fit its box). */
const titleWhole = (page: Page) =>
  page.locator('.pk-head h1').evaluate((h) => {
    const words = document.createRange();
    words.selectNodeContents(h);
    return words.getBoundingClientRect().width <= h.getBoundingClientRect().width + 0.5;
  });

test('sign in; an old link and a /next link land on their page; a head title never gives way before its search', async ({ browser, hub }) => {
  const [draft] = hub.facts.board.tasks;
  const page = await open(browser, hub, '/');
  // signed in: home is Tasks, and the menu names Dana
  await expect(page).toHaveURL(new RegExp(`^${hub.url}/tasks`));
  await expect(page.locator('[data-sheet="menu"]')).toContainText('Dana');

  // An old app address, as mail and agents' messages still carry it, opens its page in the new app.
  await page.goto(`${hub.url}/#/task/${draft!.number}`);
  await expect(page).toHaveURL(new RegExp(`/tasks/launch/${draft!.number}(/|\\?|$)`));
  await expect(page.locator('.pk-head h1')).toHaveText(draft!.title);
  await page.goto(`${hub.url}/#/decide`);
  await expect(page).toHaveURL(new RegExp('/flow(/|\\?|$)'));
  // A link from when the app lived at /next drops the prefix and keeps its query.
  await page.goto(`${hub.url}/next/tasks/launch?view=list`);
  await expect(page).toHaveURL(`${hub.url}/tasks/launch?view=list`);
  await expect(page.locator('.pk-head h1')).toHaveText('Tasks');
  await expect(page.getByText(draft!.title)).toBeVisible();

  // The Tasks head, from a narrow desktop to a wide one: the search shrinks to its magnifier before the title loses a
  // letter (seen on live as "Ta…"). At 1080 the menu and Tico stay open, so the page is at its narrowest.
  for (const width of [1000, 1080, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${hub.url}/tasks/launch`);
    const search = page.locator('.pk-head .search');
    await expect(search).toBeVisible();
    await expect(page.locator('.pk-head h1')).toHaveText('Tasks');
    expect(await titleWhole(page), `the title is whole at ${width}`).toBe(true);
    const w = (await search.boundingBox())!.width;
    expect(w, `the search is between its magnifier and its width at ${width}`).toBeGreaterThanOrEqual(31.5);
    expect(w).toBeLessThanOrEqual(208.5);
  }
  clean(page);
});

test('Needs you: open the case and approve the email; the send goes out', async ({ browser, hub }) => {
  const { subject, approval } = hub.facts.mail;
  const page = await open(browser, hub, '/tasks');
  // Needs you is Flow, from the Tasks head
  await page.getByRole('link', { name: 'Flow' }).click();
  await expect(page).toHaveURL(/\/flow/);
  await page.locator('a.row.task', { hasText: subject }).click();
  await expect(page.locator('.ap-subj')).toHaveText(subject);
  await expect(page.locator('article[aria-label="Email"]')).toContainText('order 1042 left our warehouse');
  const decision = page.locator('section[aria-label="The decision"]');
  const approve = decision.getByRole('button', { name: 'Approve', exact: true });
  await expect(approve).toBeEnabled();
  await approve.click();
  // The OK is Dana's own, bound to the copy she was shown
  await expect
    .poll(async () => (await sql(hub, `SELECT ok_at FROM ok_grants WHERE requirement_id='${approval}'`))[0]?.ok_at ?? null)
    .not.toBeNull();
  // and the server's sender takes it from the queue: it reaches the (fake) mail provider once
  expect(await call(hub, '/_e2e/drain', 'POST')).toEqual({ sent: 1 });
  const sent = await call<{ mime: string }[]>(hub, '/_e2e/sent');
  expect(sent[0]?.mime).toContain(`Subject: ${subject}`);
  clean(page);
});

test("an approval card in an agent's chat opens the case beside it; Close goes back", async ({ browser, hub }) => {
  const { subject } = hub.facts.mail;
  const page = await open(browser, hub, '/team/engineering-manager');
  const card = page.locator('a[data-target^="approval:"]');
  await expect(card).toBeVisible();
  const before = page.url();
  await card.click();
  await expect(page).toHaveURL(/via=thread/);
  await expect(page.locator('.ap-subj')).toHaveText(subject);
  // the chat stays beside the case
  await expect(page.getByRole('textbox', { name: 'Message engineering-manager' })).toBeVisible();
  await page.locator('.pk-close').click();
  await expect(page).toHaveURL(before);
  await expect(card).toBeVisible();
  clean(page);
});

test('board: drag a card to another stage, and Move to does the same', async ({ browser, hub }) => {
  const [draft, date] = hub.facts.board.tasks;
  const page = await open(browser, hub, '/tasks/launch?view=board');
  const col = (stage: string) => page.locator(`.col[data-target="stage:launch-${stage}"]`);
  const stageOf = async (id: string) => (await sql<{ stage_id: string }>(hub, `SELECT stage_id FROM tasks WHERE id='${id}'`))[0]?.stage_id;
  await expect(col('plan')).toContainText(draft!.title);

  await page.locator(`.tk-item[data-task="${draft!.id}"]`).dragTo(col('build'));
  await expect(col('build')).toContainText(draft!.title);
  await expect.poll(() => stageOf(draft!.id)).toBe('launch-build');

  const card = page.locator(`.tk-item[data-task="${date!.id}"]`);
  await card.hover();
  await card.getByRole('button', { name: 'Move to' }).click();
  await page.getByRole('menuitem', { name: 'Ship' }).click();
  await expect(col('ship')).toContainText(date!.title);
  await expect.poll(() => stageOf(date!.id)).toBe('launch-ship');
  // both stay where they went after a reload
  await page.reload();
  await expect(col('build')).toContainText(draft!.title);
  await expect(col('ship')).toContainText(date!.title);
  clean(page);
});

test('a thread: send a message; archive a mail row', async ({ browser, hub }) => {
  const page = await open(browser, hub, `/threads/${hub.facts.thread}`);
  const box = page.getByRole('textbox', { name: 'Message Omar' });
  await box.fill('The launch note is in Plan.');
  await box.press('Enter');
  await expect(page.locator('.msg', { hasText: 'The launch note is in Plan.' })).toBeVisible();
  await expect
    .poll(async () => (await sql(hub, `SELECT COUNT(*) AS n FROM messages WHERE body='The launch note is in Plan.'`))[0]?.n)
    .toBe(1);

  await page.goto(`${hub.url}/threads/mail?box=${encodeURIComponent(hub.facts.mail.mailbox)}`);
  const row = page.locator('a.ml-row', { hasText: 'Order 1042 shipped' });
  await row.click();
  await page.getByRole('button', { name: 'Archive' }).click();
  await expect(row).toHaveCount(0);
  const inbox = await api<{ messages: { msg_id: string; labels: string[] }[] }>(hub, `mail/messages?mailbox=${hub.facts.mail.mailbox}`);
  expect(inbox.messages.find((m) => m.msg_id === 'm-1')?.labels ?? []).not.toContain('INBOX');
  clean(page);
});

test('settings: change the model a new agent uses; add a Cloud agent', async ({ browser, hub }) => {
  const page = await open(browser, hub, '/settings/agents');
  const pick = page.locator('[data-target="setting:default-model"]').getByRole('combobox');
  await pick.click();
  const option = page.getByRole('option').filter({ hasNotText: /^$/ }).nth(1);
  const label = (await option.innerText()).trim();
  await option.click();
  await expect(pick).toContainText(label);
  await expect.poll(async () => (await api<{ default: { model: string } }>(hub, 'providers')).default.model).not.toBe('');

  await page.getByRole('button', { name: 'Add agent' }).click();
  await page.locator('[data-target="team:cloud"]').click();
  await page.locator('[data-target="cloud:claude"]').getByRole('button', { name: 'Add' }).click();
  await expect
    .poll(async () => (await sql<{ n: number }>(hub, `SELECT COUNT(*) AS n FROM bots WHERE slug='claude-code'`))[0]?.n)
    .toBeGreaterThan(0);
  clean(page);
});

test('phone: open a task, and Back returns to the list', async ({ browser, hub }) => {
  const [draft] = hub.facts.board.tasks;
  const page = await open(browser, hub, '/tasks/launch?view=list', PHONE);
  await page.locator('a.row.task', { hasText: draft!.title }).tap();
  await expect(page).toHaveURL(new RegExp(`/tasks/launch/${draft!.number}`));
  await expect(page.locator('.pk-head h1')).toHaveText(draft!.title);
  await page.getByRole('link', { name: 'Back to Launch' }).tap();
  await expect(page).toHaveURL(/\/tasks\/launch(\?|$)/);
  await expect(page.locator('a.row.task', { hasText: draft!.title })).toBeVisible();
  clean(page);
});
