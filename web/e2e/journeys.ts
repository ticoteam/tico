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
/** A POST to the API as Dana: a JSON body, or a form (a file). */
async function post<T = unknown>(hub: Seeded, path: string, body: unknown): Promise<T> {
  const form = body instanceof FormData;
  const r = await fetch(`${hub.url}/api/v2/${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${hub.tokens.dana}`, 'Idempotency-Key': `e2e-${path}-${Date.now()}`, ...(!form && { 'Content-Type': 'application/json' }) },
    body: form ? body : JSON.stringify(body),
  });
  expect(r.ok, `POST ${path}: ${r.status}`).toBe(true);
  return (await r.json()) as T;
}
/** The task open over the page (plan: the task modal), by its title. Not by role: a toast is a dialog too, and a layer
    over the task (a file) hides the task from the accessibility tree while it is open. */
const modal = (page: Page, title?: string) => {
  const over = page.locator('.vw:has(.tmd)');
  return title ? over.filter({ has: page.locator('.vw-title').getByText(title, { exact: true }) }) : over;
};
/** The layers open: the task, a file over it. */
const layers = (page: Page) => page.locator('.vw');
/** The address's search, as the app wrote it. */
const searchOf = (page: Page) => Object.fromEntries(new URL(page.url()).searchParams);
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

  // An old app address, as mail and agents' messages still carry it, opens the task over Tasks in the new app.
  await page.goto(`${hub.url}/#/task/${draft!.number}`);
  await expect(page).toHaveURL(new RegExp(`/tasks\\?task=${draft!.number}$`));
  await expect(modal(page, draft!.title)).toBeVisible();
  await page.goto(`${hub.url}/#/decide`);
  await expect(page).toHaveURL(new RegExp('/tasks(/|\\?|$)'));
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

test('Needs you: open the first case over Tasks and approve it; the next case opens, and after the last the task closes', async ({ browser, hub }) => {
  const { mail } = hub.facts;
  const cases = [mail, mail.next];
  const page = await open(browser, hub, '/tasks');
  // Needs you, on top of Tasks: both held emails, in Tico's order
  const needs = page.locator('section[aria-label="Needs you"]');
  const rows = needs.locator('a.row.task');
  await expect(rows).toHaveCount(2);
  const top = (await rows.first().getAttribute('title')) ?? '';
  const [first, second] = top.includes(mail.next.subject) ? [mail.next, mail] : cases;
  await rows.first().click();
  // the task opens over Tasks: its list (Needs you), its agent's chat and the task, Tasks still under it
  const over = modal(page);
  await expect(over.locator('.ap-subj')).toHaveText(first!.subject);
  expect(new URL(page.url()).pathname).toBe('/tasks');
  const list = over.locator('nav[aria-label="List"]');
  await expect(list.locator('.li-head')).toContainText('Needs you');
  await expect(list.locator('a.li')).toHaveCount(2);
  await expect(over.locator('aside[aria-label="Chat"]')).toBeVisible();
  // the email fills the task's column: its subject and letter drawn, its scroll down to the column's foot
  const box = async (sel: string) => (await over.locator(sel).first().boundingBox())!;
  expect((await box('.ap-subj')).height).toBeGreaterThan(0);
  expect((await box('.ap-mail')).height).toBeGreaterThan(0);
  const [scroll, column] = [await box('.ap-scroll'), await box('.tmd-task')];
  expect(scroll.height).toBeGreaterThan(300);
  expect(Math.abs(scroll.y + scroll.height - (column.y + column.height))).toBeLessThan(2);
  await page.screenshot({ path: test.info().outputPath('modal-1440.png') });
  const decision = over.locator('section[aria-label="The decision"]');
  const approve = decision.getByRole('button', { name: 'Approve', exact: true });
  await expect(approve).toBeEnabled();
  const opened = page.url();
  await approve.click();
  // nothing else on it waits on Dana: the next case of Needs you opens in place, its row left the list
  await expect(over.locator('.ap-subj')).toHaveText(second!.subject);
  expect(page.url()).not.toBe(opened);
  await expect(list.locator('a.li')).toHaveCount(1);
  // The OK is Dana's own, bound to the copy she was shown; the other still waits
  const okAt = async (approval: string) => (await sql(hub, `SELECT ok_at FROM ok_grants WHERE requirement_id='${approval}'`))[0]?.ok_at ?? null;
  await expect.poll(() => okAt(first!.approval)).not.toBeNull();
  expect(await okAt(second!.approval)).toBeNull();
  // the last case: approved, the list is done and the task closes, back on Tasks as it was
  await expect(decision.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled();
  await decision.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(over).toHaveCount(0);
  await expect(page).toHaveURL(`${hub.url}/tasks`);
  // the send's toast stays, with its Undo
  await expect(page.getByRole('button', { name: 'Undo' }).first()).toBeVisible();
  await expect.poll(() => okAt(second!.approval)).not.toBeNull();
  await expect(rows).toHaveCount(0);
  // and the server's sender takes them from the queue: each reaches the (fake) mail provider once
  expect(await call(hub, '/_e2e/drain', 'POST')).toEqual({ sent: 2 });
  const sent = await call<{ mime: string }[]>(hub, '/_e2e/sent');
  expect(sent.map((m) => m.mime.includes(`Subject: ${first!.subject}`))).toContain(true);
  clean(page);
});

test('Needs you: Undo on the sent case, with the next one open over Tasks, takes the OK back', async ({ browser, hub }) => {
  const { mail } = hub.facts;
  const page = await open(browser, hub, '/tasks');
  const rows = page.locator('section[aria-label="Needs you"] a.row.task');
  await expect(rows).toHaveCount(2);
  const top = (await rows.first().getAttribute('title')) ?? '';
  const [first, second] = top.includes(mail.next.subject) ? [mail.next, mail] : [mail, mail.next];
  await rows.first().click();
  const over = modal(page);
  await expect(over.locator('.ap-subj')).toHaveText(first!.subject);
  await over.locator('section[aria-label="The decision"]').getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(over.locator('.ap-subj')).toHaveText(second!.subject);
  const grant = async () => (await sql<{ ok_at: string | null; revoked_at: string | null }>(hub, `SELECT ok_at, revoked_at FROM ok_grants WHERE requirement_id='${first!.approval}'`))[0];
  await expect.poll(async () => (await grant())?.ok_at ?? null).not.toBeNull();
  // the send's toast is in the task's layer, its Undo in reach while the next case is open
  await over.getByRole('button', { name: 'Undo' }).click();
  // the OK taken back is revoked for good (answers.undo)
  await expect.poll(async () => (await grant())?.revoked_at ?? null).not.toBeNull();
  await expect(over.locator('.ap-subj')).toHaveText(second!.subject);
  clean(page);
});

test("a task on an agent's page opens over it; Close leaves the page as it was", async ({ browser, hub }) => {
  const title = 'Check the launch checklist';
  await post(hub, 'tasks', { title, owner: 'engineering-manager', pipeline: 'launch' });
  const page = await open(browser, hub, '/team/engineering-manager');
  const row = page.locator(`a.row.task[title="${title}"]`);
  await expect(row).toBeVisible();
  const before = page.url();
  await row.click();
  const over = modal(page, title);
  await expect(over).toBeVisible();
  // over the agent's page, with the agent's tasks as its list
  expect(new URL(page.url()).pathname).toBe('/team/engineering-manager');
  expect(searchOf(page).list).toMatch(/engineering-manager/);
  await expect(over.locator('nav[aria-label="List"] a.li', { hasText: title })).toHaveAttribute('aria-current', 'true');
  await over.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(over).toHaveCount(0);
  await expect(page).toHaveURL(before);
  await expect(row).toBeVisible();
  clean(page);
});

test("a task's link from outside opens it over Tasks with Needs you as its list; a file opens one layer up and Esc closes it first", async ({ browser, hub }) => {
  const [draft] = hub.facts.board.tasks;
  const page = await open(browser, hub, `/task/${draft!.number}`);
  await expect(page).toHaveURL(new RegExp(`/tasks\\?task=${draft!.number}$`));
  const over = modal(page, draft!.title);
  await expect(over.locator('nav[aria-label="List"] .li-head')).toContainText('Needs you');
  // Back from a link opened cold goes nowhere in the app: Close takes the task away and leaves Tasks
  await over.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(over).toHaveCount(0);
  await expect(page).toHaveURL(`${hub.url}/tasks`);

  // a file on the task, opened from its link: the task, and the file a layer over it
  const form = new FormData();
  form.append('file', new Blob(['Launch on Friday.\n'], { type: 'text/plain' }), 'launch-notes.txt');
  const added = await post<{ file_id: string }>(hub, `tasks/${draft!.id}/files`, form);
  await page.goto(`${hub.url}/tasks/launch/${draft!.number}/details?file=${added.file_id}`);
  await expect(layers(page)).toHaveCount(2);
  await expect(modal(page, draft!.title)).toBeAttached();
  expect(searchOf(page)).toMatchObject({ task: String(draft!.number), list: 'launch', 'task.file': added.file_id });
  await page.keyboard.press('Escape');
  await expect(layers(page)).toHaveCount(1);
  await expect(modal(page, draft!.title)).toBeVisible();
  expect(searchOf(page)['task.file']).toBeUndefined();
  await page.keyboard.press('Escape');
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(`${hub.url}/tasks/launch`);
  clean(page);
});

test('one history path: a row, a file from Details opened and closed, Close, then Back and Forward; a Cmd+K hit, Close, Back', async ({ browser, hub }) => {
  const [draft, date] = hub.facts.board.tasks;
  const form = new FormData();
  form.append('file', new Blob(['Launch on Friday.\n'], { type: 'text/plain' }), 'launch-notes.txt');
  await post(hub, `tasks/${draft!.id}/files`, form);
  const board = `${hub.url}/tasks/launch?view=list`;
  const page = await open(browser, hub, '/tasks/launch?view=list');
  await page.locator('a.row.task', { hasText: draft!.title }).click();
  const over = modal(page, draft!.title);
  await expect(over).toBeVisible();
  await over.getByRole('link', { name: 'Details', exact: true }).first().click();
  await over.locator('a.dt-file', { hasText: 'launch-notes' }).click();
  await expect(layers(page)).toHaveCount(2);
  await page.keyboard.press('Escape');
  await expect(layers(page)).toHaveCount(1);
  await over.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);
  // Forward opens the task again as it was left; Back closes it, to the board
  await page.goForward();
  await expect(over).toBeVisible();
  expect(searchOf(page)).toMatchObject({ view: 'list', task: String(draft!.number) });
  expect(searchOf(page)['task.file']).toBeUndefined();
  await page.goBack();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);

  // a Cmd+K hit opens over the board, another hit takes its place: one Back closes it; Close, then Forward, opens it again
  const hit = async (title: string) => {
    await page.keyboard.press('ControlOrMeta+k');
    await page.locator('.pal').getByRole('combobox', { name: 'Search' }).fill(title);
    await page.locator('.pal [role="option"]:not(.pal-ask)', { hasText: title }).first().click();
    await expect(modal(page, title)).toBeVisible();
  };
  await hit(draft!.title);
  await hit(date!.title);
  await page.goBack();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);
  await hit(date!.title);
  await modal(page, date!.title).getByRole('button', { name: 'Close', exact: true }).click();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);
  await page.goForward();
  await expect(modal(page, date!.title)).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(board);
  clean(page);
});

test('Needs you: answer a ❓ by tapping an answer on its card, on Tasks, without opening the task (desktop and phone)', async ({ browser, hub }) => {
  const YES = 'Yes, ship it on Friday morning as planned';
  const ask = (title: string) => call<{ id: string; number: number; title: string; mark: string }>(hub, `/_e2e/ask?title=${encodeURIComponent(title)}`, 'POST');
  const asks = [await ask('Launch note timing'), await ask('Launch note on the phone')];
  for (const [i, options] of [DESKTOP, PHONE].entries()) {
    const it = asks[i]!;
    const page = await open(browser, hub, '/tasks', options);
    const needs = page.locator('section[aria-label="Needs you"]');
    const row = needs.locator(`a.row.task[title="${it.title}"]`);
    // the row's title still opens the task; the ❓ is a card under it, whole, with the agent's answers and a reply box
    await expect(row).toHaveAttribute('href', new RegExp(`/task/${it.number}$`));
    const card = row.locator('xpath=ancestor::div[contains(@class,"flow-item")]').getByRole('region', { name: 'Question' });
    await expect(card).toContainText('Ship the launch note on Friday?');
    await expect(card).toContainText('2. Should the beta list get it too?');
    await expect(card.getByRole('textbox', { name: 'Reply' })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath(`needs-you-question-${options.viewport!.width}.png`), fullPage: true });
    const opened = page.url();
    // three answers of about 40 letters: each wraps whole on a phone, never squeezed into a narrow column
    const answer = card.getByRole('button', { name: YES });
    if (options === PHONE) expect((await answer.boundingBox())!.width).toBeGreaterThan(200);
    await answer.click();
    // answered: the row leaves Needs you, and the page never left Tasks
    await expect(row).toHaveCount(0);
    expect(page.url()).toBe(opened);
    await page.screenshot({ path: test.info().outputPath(`needs-you-answered-${options.viewport!.width}.png`), fullPage: true });
    // the tap was Dana's reply on the task's chat, and it cleared that ❓
    await expect.poll(async () => (await sql(hub, `SELECT cleared_at FROM question_marks WHERE id='${it.mark}'`))[0]?.cleared_at ?? null).not.toBeNull();
    const said = await sql(hub, `SELECT body FROM messages WHERE sender_id='human:dana' AND body='${YES}' ORDER BY created_at`);
    expect(said).toHaveLength(i + 1);
    clean(page);
  }
});

test("an approval card in an agent's chat opens the case over the agent's page; Close goes back", async ({ browser, hub }) => {
  const { subject } = hub.facts.mail;
  const page = await open(browser, hub, '/team/engineering-manager');
  const card = page.locator('a[data-target^="approval:"]');
  await expect(card).toBeVisible();
  const before = page.url();
  await card.click();
  const over = modal(page);
  await expect(over.locator('.ap-subj')).toHaveText(subject);
  expect(new URL(page.url()).pathname).toBe('/team/engineering-manager');
  // the case's own chat is beside it
  await expect(over.locator('aside[aria-label="Chat"]')).toBeVisible();
  await over.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(over).toHaveCount(0);
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

test('phone: a task opens full screen over its board; its list pulls up from the bottom; Back or Close returns to the board', async ({ browser, hub }) => {
  const [draft, date] = hub.facts.board.tasks;
  const page = await open(browser, hub, '/tasks/launch?view=list', PHONE);
  const board = `${hub.url}/tasks/launch?view=list`;
  await page.locator('a.row.task', { hasText: draft!.title }).tap();
  const over = modal(page, draft!.title);
  await expect(over).toBeVisible();
  expect(searchOf(page)).toMatchObject({ view: 'list', task: String(draft!.number), list: 'launch' });
  // the task, or its chat: two tabs
  const panes = over.locator('.tmd-panes');
  await expect(over.locator('.tmd-task')).toBeVisible();
  await panes.getByRole('button', { name: 'Chat', exact: true }).tap();
  await expect(over.locator('aside[aria-label="Chat"]')).toBeVisible();
  await expect(over.locator('.tmd-task')).toBeHidden();
  // its fields at 16px or more: iOS zooms the page into a smaller one when it is typed in
  const sizes = await over.locator('input:not([type=file]), textarea, [contenteditable="true"]').evaluateAll((els) => els.map((e) => parseFloat(getComputedStyle(e).fontSize)));
  expect(sizes.length).toBeGreaterThan(0);
  expect(Math.min(...sizes)).toBeGreaterThanOrEqual(16);
  await panes.getByRole('button', { name: 'Task', exact: true }).tap();
  await page.screenshot({ path: test.info().outputPath('modal-390.png') });
  // the board's list pulls up from the bottom; another task from it opens in place
  const list = over.locator('nav[aria-label="List"]');
  await expect(list).toBeHidden();
  await over.locator('.tmd-more').tap();
  await expect(list).toBeVisible();
  await list.locator('a.li', { hasText: date!.title }).tap();
  await expect(modal(page, date!.title)).toBeVisible();
  await expect(list).toBeHidden();
  // one Back closes it, however many tasks were opened in it
  await page.goBack();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);
  await expect(page.locator('a.row.task', { hasText: draft!.title })).toBeVisible();
  await page.locator('a.row.task', { hasText: date!.title }).tap();
  await modal(page, date!.title).getByRole('button', { name: 'Close', exact: true }).tap();
  await expect(layers(page)).toHaveCount(0);
  await expect(page).toHaveURL(board);
  clean(page);
});
