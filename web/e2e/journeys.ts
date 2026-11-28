// The web app's journeys: the few runs that prove it works end to end (plan/cutover.html, step 2). Each one starts its
// own made-up company (hub/seeded.py: Acme, Dana the owner, nothing real), signs in as Dana with the local owner link,
// and clicks through the app's build as a person does. A journey fails on any page error, console error or API refusal
// it did not expect. What a click wrote is read back from the server (/_e2e/sql, /_e2e/sent: only that test server has
// them). Run: `npm run e2e` (builds first), `npm run e2e:repeat` (each journey three times).
import { expect, test as base, type Browser, type BrowserContextOptions, type Locator, type Page } from '@playwright/test';
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

/** Each of a list's `n` previews drawn: its picture or poster read and decoded, never a grey tile. The list is
    scrolled to first, so a lazy one (a task's) reads what it shows. */
const shown = async (list: Locator, n: number) => {
  await list.scrollIntoViewIfNeeded();
  await expect
    .poll(() => list.locator('img').evaluateAll((imgs) => imgs.map((i) => (i as HTMLImageElement).complete && (i as HTMLImageElement).naturalWidth > 0)))
    .toEqual(Array(n).fill(true));
};

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

test("an agent's newest pictures and videos lead its sidebar and its task; each opens in the viewer", async ({ browser, hub }) => {
  const made = await call<{ id: string; number: number; title: string; video: string }>(hub, `/_e2e/made?title=${encodeURIComponent('Beach clean promo')}`, 'POST');
  const page = await open(browser, hub, '/team/engineering-manager');
  // the sidebar's Made recently: the video and the two pictures as tiles, each captioned with its task
  const recent = page.getByRole('region', { name: 'Made recently' }).getByRole('list', { name: 'Pictures and videos' });
  await expect(recent.getByRole('button')).toHaveCount(3);
  await expect(recent.locator('.thumb.is-video img')).toHaveAttribute('src', /\/poster\?v=1$/);
  await expect(recent.getByRole('link', { name: made.title })).toHaveCount(3);
  await expect(recent.locator('img[loading]')).toHaveCount(0);
  await shown(recent, 3);
  await page.screenshot({ path: test.info().outputPath('made-recently-1440.png') });
  // a picture opens in the viewer over the page
  await recent.getByRole('button', { name: /beach.close/i }).click();
  await expect(page.locator('.pv-img')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.pv-img')).toHaveCount(0);

  // its task is a click away: it opens over the page, its pictures and videos first, above the page it opens on
  await recent.getByRole('link', { name: made.title }).first().click();
  const over = modal(page, made.title);
  await expect(over).toBeVisible();
  const strip = over.locator('[data-target="task-media"]').getByRole('list', { name: 'Pictures and videos' });
  await expect(strip.getByRole('button')).toHaveCount(3);
  await expect(over.locator('.tv-view > *').first()).toHaveAttribute('data-target', 'task-media');
  // a task's strip reads each picture as it nears the screen; the sidebar's six at most read at once
  await expect(strip.locator('img[loading="lazy"]')).toHaveCount(3);
  await shown(strip, 3);
  await page.screenshot({ path: test.info().outputPath('made-task-1440.png') });
  // the video opens one layer up, on the tab the task was on
  const tab = searchOf(page)['task.tab'];
  await strip.getByRole('button', { name: /beach.cut/i }).click();
  await expect(layers(page)).toHaveCount(2);
  expect(searchOf(page)).toMatchObject({ 'task.file': made.video });
  expect(searchOf(page)['task.tab']).toBe(tab);
  // one title over the dim, the video's: the task's bar stays under the layer over it
  await expect(page.locator('.vw-title:visible')).toHaveCount(1);
  await expect(page.locator('.vw-title:visible')).not.toHaveText(made.title);
  // the video shows its still until it plays
  await expect(page.locator('video.fv-img')).toHaveAttribute('poster', /\/poster\?v=1$/);
  // the layer's way in finished (its title no longer crossing the task's)
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getComputedTiming().endTime === Infinity));
  await page.screenshot({ path: test.info().outputPath('made-video-1440.png') });
  await page.keyboard.press('Escape');
  await expect(layers(page)).toHaveCount(1);
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

// A long stage runs on in the page's own scroll to its last task, with no "N more" (task 3535). On a phone the board
// runs down to the bottom bar: the point just above the bar is the board's, not blank page under it.
test('a long stage runs on to its last task: the phone board reaches the bottom bar, the computer list scrolls to the end', async ({ browser, hub }) => {
  const N = 130; // past the 100 rows after which only those near the screen are drawn
  await call(hub, `/_e2e/many?n=${N}`, 'POST');
  const titles = Array.from({ length: N }, (_, i) => `Long list ${i + 1}`);
  const noMore = (page: Page) => expect(page.getByRole('button', { name: /^\d+\+? more$|^More tasks$|^Show more$/ })).toHaveCount(0);
  /** Scroll a scroller down most of a screen at a time to its very end, as a person's thumb would; `title` is then on screen. */
  const scrollTo = async (page: Page, scroller: string, title: string) => {
    const box = page.locator(scroller).first();
    for (let i = 0; i < 120; i++) {
      const end = await box.evaluate((el) => {
        el.scrollBy(0, el.clientHeight * 0.8);
        return el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
      });
      await page.waitForTimeout(50);
      if (end) break;
    }
    await expect(page.getByTitle(title, { exact: true })).toBeInViewport();
  };
  // which stage the long list is in on screen: Plan, the first, holds them all
  const last = titles.at(-1)!;

  const phone = await open(browser, hub, '/tasks/launch?view=board', PHONE);
  const plan = phone.locator('.col[data-target="stage:launch-plan"]');
  await expect(plan.getByTitle(titles[0]!, { exact: true })).toBeVisible();
  await noMore(phone);
  // a fresh company's first visit shows a tip under the board, which may come a moment after the cards; a person puts
  // it away once
  const tip = phone.getByRole('button', { name: 'Got it' });
  await tip.click();
  await expect(tip).toBeHidden();
  const reach = await phone.evaluate(() => {
    const bar = document.querySelector('.tabbar')!.getBoundingClientRect();
    const board = document.querySelector('.board')!;
    const at = document.elementFromPoint(window.innerWidth / 2, bar.top - 4);
    return { board: !!at && board.contains(at), gap: Math.round(bar.top - board.getBoundingClientRect().bottom), inner: getComputedStyle(board).overflowY };
  });
  expect(reach, 'the board runs down to the bar, with no blank band above it').toEqual({ board: true, gap: 0, inner: 'auto' });
  // one scroller: the column holds no scrollbar of its own
  expect(await plan.evaluate((col) => [col, ...col.querySelectorAll('*')].some((el) => el.scrollHeight > el.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(el).overflowY)))).toBe(false);
  await scrollTo(phone, '.board', last);
  await phone.screenshot({ path: test.info().outputPath('long-board-390.png') });
  clean(phone);

  const page = await open(browser, hub, '/tasks/launch?view=list');
  await expect(page.getByTitle(titles[0]!, { exact: true })).toBeVisible();
  await noMore(page);
  await scrollTo(page, '.pk-body', last);
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

test("an agent answering: an @mention in a task's comments shows it got it, its reply being written, then the reply, on every page open on it; its own chat shows the same row", async ({ browser, hub }) => {
  const [draft] = hub.facts.board.tasks;
  const page = await open(browser, hub, `/task/${draft!.number}`);
  const chat = modal(page, draft!.title).locator('aside[aria-label="Chat"]');
  // the same task open on a phone: what it shows comes pushed, not from the click
  const phone = await open(browser, hub, `/task/${draft!.number}`, PHONE);
  const over = modal(phone, draft!.title);
  await over.locator('.tmd-panes').getByRole('button', { name: 'Chat', exact: true }).tap();
  const small = over.locator('aside[aria-label="Chat"]');
  const both = [chat, small];

  const box = chat.getByRole('textbox', { name: 'Add a comment' });
  await box.fill('@engineering-manager can you check the launch note reads well?');
  await box.press('Enter');
  await expect(chat.locator('.msg', { hasText: 'can you check the launch note reads well?' }).first()).toBeVisible();
  for (const c of both) await expect(c.locator('.msg.is-live')).toContainText('Got it');
  await page.screenshot({ path: test.info().outputPath('chat-working-task-got-1440.png') });

  const started = await call<{ attempt: string }>(hub, `/_e2e/turn?step=start&bot=engineering-manager&text=${encodeURIComponent('Reading the launch note now')}`, 'POST');
  for (const c of both) {
    await expect(c.locator('.msg.is-live')).toHaveCount(1);
    await expect(c.locator('.msg.is-live')).toContainText('Reading the launch note now');
  }
  await page.screenshot({ path: test.info().outputPath('chat-working-task-1440.png') });
  await phone.screenshot({ path: test.info().outputPath('chat-working-task-390.png') });

  const reply = 'The launch note reads well; one typo fixed in the second line.';
  await call(hub, `/_e2e/turn?step=done&attempt=${started.attempt}&text=${encodeURIComponent(reply)}`, 'POST');
  for (const c of both) {
    await expect(c.locator('.msg', { hasText: reply })).toBeVisible();
    await expect(c.locator('.msg.is-live')).toHaveCount(0);
  }
  clean(page);
  clean(phone);

  // the agent's own chat draws the same row
  const dm = await open(browser, hub, '/team/engineering-manager');
  const own = dm.locator('[role="log"]');
  const say = dm.getByRole('textbox', { name: /^Message (?!Tico$)/ });
  await say.fill('Is the launch date set?');
  await say.press('Enter');
  await expect(own.locator('.msg', { hasText: 'Is the launch date set?' }).first()).toBeVisible();
  await expect(own.locator('.msg.is-live')).toContainText('Got it');
  const next = await call<{ attempt: string }>(hub, `/_e2e/turn?step=start&bot=engineering-manager&text=${encodeURIComponent('Checking the board')}`, 'POST');
  await expect(own.locator('.msg.is-live')).toContainText('Checking the board');
  await dm.screenshot({ path: test.info().outputPath('chat-working-dm-1440.png') });
  await call(hub, `/_e2e/turn?step=done&attempt=${next.attempt}&text=${encodeURIComponent('Friday, on the board.')}`, 'POST');
  await expect(own.locator('.msg', { hasText: 'Friday, on the board.' })).toBeVisible();
  await expect(own.locator('.msg.is-live')).toHaveCount(0);

  // a run that ends with no reply leaves one line that says so
  await say.fill('And the budget?');
  await say.press('Enter');
  await expect(own.locator('.msg.is-live')).toContainText('Got it');
  const quiet = await call<{ attempt: string }>(hub, '/_e2e/turn?step=start&bot=engineering-manager&text=', 'POST');
  await call(hub, `/_e2e/turn?step=done&attempt=${quiet.attempt}&text=`, 'POST');
  await expect(own.getByText(/finished without replying/)).toBeVisible();
  await expect(own.locator('.msg.is-live')).toHaveCount(0);
  await dm.screenshot({ path: test.info().outputPath('chat-working-dm-ended-1440.png') });
  clean(dm);
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

  // every mail call and every change to the rows, printed if the archived row stays (or comes back)
  const t0 = Date.now();
  const trace: string[] = [];
  const at = () => `${String(Date.now() - t0).padStart(6)}ms`;
  page.on('request', (r) => r.url().includes('/api/v2/') && trace.push(`${at()} > ${r.method()} ${new URL(r.url()).pathname}${new URL(r.url()).search}`));
  page.on('response', async (r) => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith('/api/v2/')) return;
    let note = '';
    if (u.pathname.endsWith('/mail/messages')) note = ` ${(await r.text().catch(() => '')).match(/"subject":"[^"]*"/g)?.join(',') ?? ''}`;
    trace.push(`${at()} < ${r.status()} ${r.request().method()} ${u.pathname}${u.search}${note}`);
  });
  page.on('framenavigated', (f) => f === page.mainFrame() && trace.push(`${at()} nav ${f.url()}`));
  page.on('console', (m) => trace.push(`${at()} console.${m.type()} ${m.text().slice(0, 200)}`));
  await page.exposeFunction('__traceRows', (s: string) => trace.push(`${at()} rows ${s}`));
  await page.goto(`${hub.url}/threads/mail?box=${encodeURIComponent(hub.facts.mail.mailbox)}`);
  await page.evaluate(() => {
    const say = (window as unknown as { __traceRows: (s: string) => void }).__traceRows;
    let last = '';
    new MutationObserver(() => {
      const now = [...document.querySelectorAll('a.ml-row')].map((a) => a.getAttribute('title') ?? '').join(' | ') + ` @ ${location.search}`;
      if (now !== last) say((last = now));
    }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['href', 'title'] });
  });
  const row = page.locator('a.ml-row', { hasText: 'Order 1042 shipped' });
  await row.click();
  trace.push(`${at()} clicked row`);
  await page.getByRole('button', { name: 'Archive' }).click();
  trace.push(`${at()} clicked Archive`);
  try {
    await expect(row).toHaveCount(0);
    // the list read again (as the write does, and a reload): the archived mail is out of the inbox, not only hidden
    await page.reload();
    await expect(page.locator('a.ml-row').first()).toBeVisible();
    await expect(row).toHaveCount(0);
  } catch (e) {
    console.log(`TRACE archive-a-mail-row\n${trace.join('\n')}`);
    throw e;
  }
  const inbox = await api<{ messages: { msg_id: string; labels: string[] }[] }>(hub, `mail/messages?mailbox=${hub.facts.mail.mailbox}`);
  expect(inbox.messages.find((m) => m.msg_id === 'm-1')?.labels ?? []).not.toContain('INBOX');
  clean(page);
});

test("the menu counts Catch up without reading its cards, at most once a second; a mention and a person's DM both ask Dana", async ({ browser, hub }) => {
  // Omar writes as a teammate does: a thread that @s Dana, and a line in his DM with her.
  const asOmar = async (path: string, body: unknown) => {
    const r = await fetch(`${hub.url}/api/v2/${path}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${hub.tokens.omar}`, 'Idempotency-Key': `e2e-${path}-${Math.random()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    expect(r.ok, `POST ${path}: ${r.status}`).toBe(true);
    return (await r.json()) as { id: string };
  };
  const room = await asOmar('threads', { title: 'Launch copy', audience: 'members', members: ['human:dana', 'human:priya'] });
  await asOmar(`threads/${room.id}/messages`, { text: '@dana can you sign off the launch copy?' });
  await asOmar(`threads/${hub.facts.thread}/messages`, { text: 'Lunch at one?' });

  const page = await open(browser, hub, '/tasks');
  const reads: { path: string; at: number }[] = [];
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith('/api/v2/threads/catch-up')) reads.push({ path, at: Date.now() });
  });
  await page.reload();
  const menu = page.getByRole('navigation', { name: 'Main' });
  await expect.poll(() => reads.filter((r) => r.path.endsWith('/count')).length).toBeGreaterThan(0);

  // the thread that @s her asks her in Threads, and Omar asks her on his row in Team
  await menu.getByRole('tab', { name: /Threads/ }).click();
  await expect(menu.getByRole('link', { name: /Launch copy/ }).getByRole('img', { name: 'Asks you' })).toBeVisible();
  await menu.getByRole('tab', { name: /Team/ }).click();
  await expect(menu.getByRole('link', { name: /Omar/ }).first().locator('.dot')).toBeVisible();

  // ten new messages: the menu reads the count again, never the cards, and never twice in one second
  const from = Date.now();
  for (let i = 0; i < 10; i++) await asOmar(`threads/${room.id}/messages`, { text: `Note ${i}` });
  await expect.poll(() => reads.filter((r) => r.at > from).length).toBeGreaterThan(0);
  await page.waitForTimeout(1500);
  expect(reads.filter((r) => !r.path.endsWith('/count')), 'no read of the cards').toEqual([]);
  const counts = reads.map((r) => r.at).filter((at) => at > from);
  for (let i = 1; i < counts.length; i++) expect(counts[i]! - counts[i - 1]!, 'a second between count reads').toBeGreaterThanOrEqual(900);
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

test("a read that failed says why in the page's one line; a restart has no Try again (it is read again by itself)", async ({ browser, hub }) => {
  const page = await open(browser, hub, '/tasks');
  await expect(page.locator('.pk-head h1')).toHaveText('Tasks');
  // Tico restarting: Usage's reads answer 503 starting (said not to wait, so the page shows it at once)
  const starting = JSON.stringify({ error: { code: 'starting', detail: 'Tico is starting', retryable: false } });
  await page.route('**/api/v2/usage?**', (r) => r.fulfill({ status: 503, contentType: 'application/problem+json', body: starting }));
  await page.goto(`${hub.url}/company/usage`);
  await expect(page.locator('.pk-head h1')).toHaveText('Usage');
  await expect(page.getByRole('heading', { name: "Usage didn't load · server restarting" })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('read-fault-1440.png') });
  expect(page.problems.filter((p) => p !== '503 GET /api/v2/usage')).toEqual([]);
});

test("phone: a case opening shows its page while it loads; its chat's card for the email raises the email with its Approve", async ({ browser, hub }) => {
  const { subject } = hub.facts.mail;
  const page = await open(browser, hub, '/tasks', PHONE);
  const row = page.locator('section[aria-label="Needs you"] a.row.task', { hasText: subject });
  const href = (await row.getAttribute('href'))!;
  // the task's reads held until it is seen loading: its page's column shows, never a blank sheet
  let release = () => {};
  const held = new Promise<void>((go) => (release = go));
  await page.route('**/api/v2/tasks/**', async (r) => {
    await held;
    await r.continue();
  });
  await page.goto(`${hub.url}${href}`);
  const over = modal(page);
  await expect(over.locator('.tmd-task')).toBeVisible();
  await expect(over.locator('.ap-subj')).toHaveCount(0);
  release();
  await expect(over.locator('.ap-subj')).toHaveText(subject);
  // its chat up: the decision docked under the page goes with it
  const decision = over.locator('section[aria-label="The decision"]');
  await expect(decision).toBeVisible();
  await over.locator('.tmd-panes').getByRole('button', { name: 'Chat', exact: true }).tap();
  const chat = over.locator('aside[aria-label="Chat"]');
  await expect(chat).toBeVisible();
  await expect(decision).toBeHidden();
  // the email's card in the chat: the email itself shows again, its Approve with it
  await chat.locator('a[data-target^="approval:"]').first().tap();
  await expect(over.locator('.tmd-task')).toBeVisible();
  await expect(over.locator('.ap-subj')).toHaveText(subject);
  await expect(decision.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  await expect(chat).toBeHidden();
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
  const next = modal(page, date!.title);
  await expect(next).toBeVisible();
  // the list under the task now open: drawn, and down again
  const nextList = next.locator('nav[aria-label="List"]');
  await expect(nextList).toHaveCount(1);
  await expect(nextList).toBeHidden();
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

test("Library: an app page an agent made is marked App in Pages; it opens as a page, and Back returns to the list", async ({ browser, hub }) => {
  // the shape of an agent's library page (a Creative Library): an app that reads files and keeps notes in its store
  const text =
    '<!doctype html><html><head><title>Creative library</title></head><body><h1>Creative library</h1>' +
    '<p>Winners first, then the tired ones.</p></body></html>';
  const { page: made } = await post<{ page: { file_id: string } }>(hub, 'pages', { name: 'creative-library.html', title: 'Creative library', text, app: 'reads=files,store' });
  const page = await open(browser, hub, '/library/pages');
  const row = page.locator('.page-line').filter({ hasText: 'Creative library' });
  await expect(row).toBeVisible();
  await expect(row.locator('.tag', { hasText: 'App' })).toBeVisible();
  await row.click();
  await expect(page).toHaveURL(new RegExp(`[?&]file=${made.file_id}`));
  // the page itself, framed: its own heading inside the frame, under the page's title
  await expect(page.frameLocator('iframe[title="Creative library"]').getByRole('heading', { name: 'Creative library' })).toBeVisible();
  await page.goBack();
  await expect(page.locator('.page-line').filter({ hasText: 'Creative library' })).toBeVisible();
  clean(page);
});
