// A stage's review (the ▶ on a stage, ui/app/decide.ts decideStageOpen), offline against the Tasks page's fake server
// (tasks-page.cts). Checks, as the spec's acceptance criteria give them:
//  - a pipeline shows "▶ n" on each stage with items waiting on the person, and no ▶ on a stage with none (columns, the
//    list's group headers; a phone has none beside its tabs, the header's ▶ Review opens the open stage's items);
//  - nothing moves when Needs you arrives after the board draws: ▶ keeps its 40 px slot at the right of a stage's
//    header (columns and list alike), and a phone's tabs and tab strip keep their widths as Needs you and the Rules
//    counts arrive and across stages whose Rules counts differ in digits, a stage with items getting a dot;
//  - ▶ opens the review screen full screen on a phone, "Spec · 1 of 3", with only Spec's items in the walk;
//  - an answer, a send back with a note, or an OK is saved and the next item shows without leaving the screen;
//  - a clear stage says "Spec is clear" and offers "▶ Review · 2", which walks Review's items;
//  - a new ask in the stage joins the walk while it is open, without moving off the item on screen;
//  - Tasks › You shows one chip per stage with items, and Other; a chip opens that stage's review;
//  - #/decide?stage=<id> opens that stage's review, or, with nothing there, its pipeline on that stage saying so;
//  - on a 390 px phone, the Tasks page, a pipeline, a task and the review screen: every control has a 44 px hit area,
//    nothing scrolls sideways but the stage tabs. STAGE_SHOTS=<folder> saves each screen, light and dark.
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {open, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Need = import('./tasks-page.cts').Need;
type Task = import('./tasks-page.cts').Task;
type Requirement = import('../types/api').TaskRequirement;

const PHONE = {width: 390, height: 844};
const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
const waiting = {state: 'person' as const, who: 'human:ana', line: 'Waiting on Ana', since: ago(60), until: null, ask: true, requirement_id: null};

function tasks(): Task[] {
  return [
    task('t-1', 'Review a stage with ▶ and walk every item in it', 'spec', {turn: waiting}),
    task('t-2', 'Funnel export for the growth dashboard that the growth team asked for', 'spec', {turn: waiting}),
    task('t-3', 'Invoice reminders', 'spec', {turn: waiting}),
    task('t-4', 'Rounding in the API', 'build', {owner_id: 'bot:eng'}),
    task('t-5', 'Task page clarity', 'review', {turn: waiting}),
    task('t-6', 'Answer from a task', 'review', {turn: waiting}),
    task('t-7', 'Book the team offsite', 'todo', {owner_id: 'human:ana', turn: {...waiting, ask: false}}),
  ];
}
const ask = (rid: string, kind: 'ok' | 'answer', line: string, choices: string[] | null = null): Requirement => ({requirement_id: rid, kind, line,
  who: 'human:ana', asked_by: 'bot:pm', choices, may: kind === 'ok' ? ['ok', 'no', 'thumbs'] : ['answer', 'thumbs'], met: false, shows: [],
  standing: 'waiting', answer: null});
function needsOf(list: Task[]): Need[] {
  const of = (id: string) => brief(list.find(t => t.id === id)!);
  const asked = (id: string, rid: string, kind: 'ok' | 'answer', line: string, minutes: number, choices: string[] | null = null): Need =>
    ({kind: 'ask', id: `a-${rid}:${rid}`, task_id: id, requirement_id: rid, line, since: ago(minutes), task: of(id), ask: ask(rid, kind, line, choices)});
  return [
    asked('t-1', 'r1', 'ok', 'OK the spec for Review a stage', 300),
    asked('t-2', 'r2', 'ok', 'OK the spec for Funnel export', 200),
    asked('t-3', 'r3', 'answer', 'Which reminder goes first?', 100, ['Email', 'Text']),
    asked('t-5', 'r5', 'ok', 'OK the release note for Task page clarity', 90),
    asked('t-6', 'r6', 'answer', 'Ship it this week?', 80, ['Yes', 'No']),
    {kind: 'task', id: 'task:t-7', task_id: 't-7', line: 'Book the team offsite', since: ago(400), task: of('t-7')},
    {kind: 'paused', id: 'paused:github', line: 'Connect GitHub', since: ago(500), task: null, task_ids: ['t-4'], bots: ['eng']},
  ];
}
// A stage's OK names no grant (GET /api/v2/oks/{id} 404): Approve is a plain answer.
const noGrant = async (page: Page) => { await page.route('**/api/v2/oks/**', r => r.fulfill({status: 404, contentType: 'application/json', body: '{"error":"not_found"}'})); };
const opened = (b: Browser, o: Parameters<typeof open>[1] = {}) => { const list = tasks(); return open(b, {tasks: list, needs: needsOf(list), beforeGoto: noGrant, ...o}); };
const count = (page: Page) => page.locator('#need-card .dc-count').innerText();
const question = (page: Page) => page.locator('#need-card .dc-side .dc-q').innerText();
const plays = (page: Page) => page.locator('[data-pv-play]').evaluateAll(els => els.map(e => [(e as HTMLElement).dataset.pvPlay, e.textContent?.trim()]));

async function playOnStages(browser: Browser) {
  const {page, errors} = await opened(browser, {hash: '#/tasks?pipeline=product&layout=columns'});
  await page.locator('[data-pv-play]').first().waitFor();
  assert.deepEqual(await plays(page), [['spec', '3'], ['review', '2']], 'Spec and Review show ▶ with their counts; Build none');
  await page.locator('#task-menu').click();
  await page.locator('#task-mi-layout [data-layout="list"]').click();
  await page.locator('.pv-group-head [data-pv-play]').first().waitFor();
  assert.deepEqual(await plays(page), [['spec', '3'], ['review', '2']], 'the list layout\'s group headers too');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('▶ n on each stage with items, none on a stage without: ok');
}

/** Where each stage header's parts sit, and each phone tab's width. */
const layoutOf = (page: Page) => page.evaluate(() => {
  const box = (el: Element | null) => { const r = el?.getBoundingClientRect(); return r ? [Math.round(r.left), Math.round(r.right)] : null; };
  return {
    heads: [...document.querySelectorAll('.pv-col-head, .pv-group-head')].map(h => [box(h.querySelector('.pv-stage-n')), box(h.querySelector('.pv-play'))]),
    tabs: [...document.querySelectorAll('.pv-tab')].map(t => Math.round(t.getBoundingClientRect().width)),
    strip: Math.round(document.querySelector('.pv-tabs')?.getBoundingClientRect().width ?? 0),
  };
});

/** Needs you held back until `let go()`: the board draws first, as it does on a slow network. */
async function heldNeeds(page: Page): Promise<() => void> {
  let go = () => {};
  const held = new Promise<void>(r => { go = r; });
  await page.route('**/api/v2/needs-you**', async route => { await held; await route.fallback(); });
  return () => go();
}

async function nothingMoves(browser: Browser) {
  for (const layout of ['columns', 'list']) {
    let release = () => {};
    const {page, errors} = await opened(browser, {hash: `#/tasks?pipeline=product&layout=${layout}`, beforeGoto: async pg => { await noGrant(pg); release = await heldNeeds(pg); }});
    await page.locator('.pv-stage-n').first().waitFor();
    assert.equal(await page.locator('[data-pv-play]').count(), 0, 'no ▶ before Needs you');
    const before = await layoutOf(page);
    release();
    await page.locator('[data-pv-play]').first().waitFor();
    assert.deepEqual(await layoutOf(page), before, `${layout}: the counts and ▶'s slot stay where they were`);
    const right = await page.locator('.pv-play').evaluateAll(els => els.map(e => {
      const h = e.parentElement!.getBoundingClientRect(), r = e.getBoundingClientRect();
      return {gap: Math.round(h.right - r.right - parseFloat(getComputedStyle(e.parentElement!).paddingRight)), w: Math.round(r.width)};
    }));
    assert.ok(right.every(x => x.gap === 0 && x.w >= 40), `${layout}: ▶ at the header's right edge, 40 px or more: ${JSON.stringify(right)}`);
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // The stage's Rules count also arrives late, and differs in digits between stages (Spec 12, Review 1, Build 0).
    let release = () => {}, rulesGo = () => {};
    const rulesHeld = new Promise<void>(r => { rulesGo = r; });
    const rule = (i: number, stages: string[]) => ({id: `r${i}`, scope: 'stage', stages});
    const items = [...Array.from({length: 12}, (_, i) => rule(i, ['spec'])), rule(99, ['review'])];
    const {page, errors} = await opened(browser, {viewport: PHONE, hash: '#/tasks?pipeline=product', beforeGoto: async pg => {
      await noGrant(pg); release = await heldNeeds(pg);
      await pg.route('**/api/v2/stage-rules**', async route => { await rulesHeld; await route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}); });
    }});
    await page.locator('.pv-tab[data-pv-tab="build"]').click();
    const before = await layoutOf(page);
    release(); rulesGo();
    await page.locator('.pv-tab-dot').first().waitFor();
    await page.waitForFunction(() => document.querySelector('.pv-phone-head .sr-btn .tnum')?.textContent === '0');
    const after = await layoutOf(page);
    assert.deepEqual([after.tabs, after.strip], [before.tabs, before.strip], 'the tabs keep their widths when the dots arrive');
    assert.deepEqual(await page.locator('.pv-tab:has(.pv-tab-dot)').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.pvTab)), ['spec', 'review']);
    assert.equal(await page.locator('.pv-tab[data-pv-tab="spec"]').getAttribute('aria-label'), 'Spec 3, 3 wait on you');
    for (const id of ['spec', 'review', 'build']) {
      await page.locator(`.pv-tab[data-pv-tab="${id}"]`).click();
      const now = await layoutOf(page);
      assert.deepEqual([now.tabs, now.strip], [before.tabs, before.strip], `the tabs do not move on ${id}`);
      if (id === 'spec') assert.equal((await page.locator('.pv-phone-head .sr-btn').textContent())?.trim(), 'Rules 12', 'a two-digit Rules count, same strip');
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('Needs you arriving late moves nothing: ▶ keeps its slot, a phone\'s tabs keep their widths: ok');
}

async function walkOnAPhone(browser: Browser) {
  const {page, errors, posts} = await opened(browser, {viewport: PHONE, hash: '#/tasks?pipeline=product'});
  await page.locator('.pv-tab[data-pv-tab="spec"]').click();
  assert.deepEqual(await plays(page), [], 'no ▶ beside the tabs');
  await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '5');
  await page.locator('#task-play').click();      // the header's ▶ Review: the open stage first
  await page.locator('#need-card[open]').waitFor();
  const box = await page.locator('#need-card').boundingBox();
  assert.ok(box && box.width === PHONE.width && box.height >= PHONE.height - 1, `full screen on a phone: ${JSON.stringify(box)}`);
  assert.equal(await count(page), 'Spec · 1 of 3');
  // Only Spec's items, answers first, then OKs: page through them, and back.
  const seen = [await question(page)];
  for (let i = 0; i < 2; i++) { await page.locator('#need-card [data-dc="next"]').dispatchEvent('click'); seen.push(await question(page)); }
  assert.deepEqual(seen, ['Which reminder goes first?', 'OK the spec for Review a stage', 'OK the spec for Funnel export'], `the walk is Spec's: ${seen.join(' | ')}`);
  for (let i = 0; i < 2; i++) await page.locator('#need-card [data-dc="prev"]').dispatchEvent('click');
  // A question's answer: saved, and the next shows without leaving the screen.
  await page.locator('#need-card [data-dc-choice="Email"]').click();
  await page.waitForFunction(() => document.querySelector('#need-card .dc-count')?.textContent === 'Spec · 1 of 2');
  assert.ok(posts.some(x => x.p === '/api/v2/tasks/t-3/asks/r3/answer' && x.body.value === 'Email'));
  // Approve.
  assert.equal(await question(page), 'OK the spec for Review a stage');
  await page.locator('#need-card [data-dc="approve"]:not([disabled])').click();
  await page.waitForFunction(() => document.querySelector('#need-card .dc-count')?.textContent === 'Spec · 1 of 1');
  assert.ok(posts.some(x => x.p === '/api/v2/tasks/t-1/asks/r1/answer' && x.body.value === 'ok'), 'the OK was sent');
  // A new OK on a task in Spec joins the walk; the item on screen stays.
  const before = await question(page);
  // DEC and decideRefresh are the app's script globals: a live refresh hands the screen the new list.
  await page.evaluate(`S.v2.needs = [...S.v2.needs, {...S.v2.needs.find(n => n.requirement_id === 'r2'), id: 'a-r9:r9', requirement_id: 'r9',
    line: 'OK the late spec'}]; decideRefresh(DEC, S.v2.needs)`);
  assert.equal(await count(page), 'Spec · 1 of 2', 'the new one joins the count');
  assert.equal(await question(page), before, 'and the item on screen stays');
  // Send back with a note. The server never had the late OK, so the read after the answer takes it out (answered
  // elsewhere, it leaves): the stage is clear, and ▶ Review · 2 walks Review's items.
  await page.locator('#dc-notes').fill('Name the stage in the top bar.');
  await page.locator('#need-card [data-dc="sendback"]').click();
  await page.locator('#need-card .dc-done h2', {hasText: 'Spec is clear'}).waitFor();
  assert.ok(posts.some(x => x.p === '/api/v2/tasks/t-2/asks/r2/answer' && x.body.value === 'no' && x.body['note'] === 'Name the stage in the top bar.'));
  assert.match(await page.locator('#need-card .dc-done').innerText(), /3 answered/);
  assert.equal((await page.locator('#need-card [data-dc-walk]').innerText()).replace(/\s+/g, ' '), '▶ Review · 2');
  await page.locator('#need-card [data-dc-walk="review"]').click();
  await page.waitForFunction(() => document.querySelector('#need-card .dc-count')?.textContent === 'Review · 1 of 2');
  assert.equal(await question(page), 'Ship it this week?');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('▶ on a phone: full screen, only the stage, answers move on, new items join, clear offers the next stage: ok');
}

async function chipsOnYou(browser: Browser) {
  const {page, errors} = await opened(browser, {hash: '#/tasks?pipeline='});
  // The chips count the shared Needs you list, which can land after the first rows; it redraws them when it does.
  await page.waitForFunction(() => document.querySelectorAll('.tk-stages [data-need-stage]').length === 4, null, {timeout: 15_000}).catch(() => {});
  const chips = await page.locator('.tk-stages [data-need-stage]').evaluateAll(els => els.map(e => [...e.children].map(c => c.textContent).join(' ')));
  assert.deepEqual(chips, ['Spec 3', 'Review 2', 'To do 1', 'Other 1'], `chips: ${chips.join(' | ')}`);
  await page.locator('[data-need-stage="review"]').click();
  await page.waitForFunction(() => document.querySelector('#need-card .dc-count')?.textContent === 'Review · 1 of 2');
  await page.locator('#need-card [data-dc="close"]').click();
  // "Go through" still walks everything.
  await page.locator('#task-play').click();
  await page.waitForFunction(() => /^1 of 7$/.test(document.querySelector('#need-card .dc-count')?.textContent || ''));
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Needs you by stage: a chip per stage, each opening its review; play still walks all: ok');
}

async function deepLink(browser: Browser) {
  {
    const {page, errors} = await opened(browser, {hash: '#/decide?stage=spec'});
    await page.waitForFunction(() => document.querySelector('#need-card .dc-count')?.textContent === 'Spec · 1 of 3');
    assert.deepEqual(errors, []);
    await page.close();
  }
  const {page, errors} = await opened(browser, {viewport: PHONE, hash: '#/decide?stage=build'});
  await page.locator('.pv-bar', {hasText: 'Nothing in Build waits on you'}).waitFor();
  assert.equal(await page.locator('#need-card[open]').count(), 0);
  assert.equal(await page.locator('.pv-tab.on').getAttribute('data-pv-tab'), 'build');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('#/decide?stage=: the stage\'s review, or its pipeline saying nothing waits: ok');
}

// Every visible control under `root` with a hit area under 44 px either way, and how far the page scrolls sideways.
function smallTargets(page: Page, root: string) {
  return page.evaluate(sel => {
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>(`${sel} :is(button, [role="button"], [role="tab"], a[href], select, input:not([type="hidden"]))`)) {
      // A box ticked through its label is hit anywhere on the label; a 1 px field opened by its button is not a target.
      const r = (el.matches('input[type="checkbox"], input[type="radio"]') && el.closest('label') || el).getBoundingClientRect(), cs = getComputedStyle(el);
      if (r.width <= 2 || r.height <= 2 || cs.visibility === 'hidden' || el.closest('[hidden], .dc-bar') || el.matches('textarea')) continue;
      if (el.closest('.pv-tabs') && (r.right < 0 || r.left > innerWidth)) continue;   // a tab scrolled out of the row
      if (Math.min(r.width, r.height) < 43.5) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')}[${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24)}] ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    return {small: out, sideways: document.documentElement.scrollWidth - innerWidth};
  }, root);
}

async function phonePolish(browser: Browser) {
  const shots = process.env.STAGE_SHOTS || '';
  for (const theme of shots ? ['light', 'dark'] : ['dark']) {
    const shot = (page: Page, name: string) => shots ? page.screenshot({path: path.join(shots, `${name}-${theme}.png`)}) : null;
    const {page, errors} = await opened(browser, {viewport: PHONE, theme, hash: '#/tasks?pipeline='});
    let r = await smallTargets(page, '.tasks-page');
    assert.deepEqual(r.small, [], 'Tasks › You: 44 px');
    assert.ok(r.sideways <= 1, 'Tasks › You: no sideways scroll');
    await shot(page, '1-tasks');
    await page.evaluate(() => { location.hash = '#/tasks?pipeline=product'; });
    await page.locator('.pv-tab[data-pv-tab="spec"]').click();
    r = await smallTargets(page, '.tasks-page');
    assert.deepEqual(r.small, [], 'a pipeline: 44 px');
    assert.ok(r.sideways <= 1, 'a pipeline: no sideways scroll');
    const rows = await page.locator('.pv-phone .tk-card').evaluateAll(els => els.map(e => [e.getBoundingClientRect().height, !!e.querySelector('.tk-asker .av, .tk-asker img')]));
    assert.ok(rows.every(([h]) => Number(h) >= 44), `rows 44 px: ${JSON.stringify(rows)}`);
    await shot(page, '2-pipeline');
    await page.locator('#task-play').click();
    await page.locator('#need-card[open] [data-dc-choice]').first().waitFor();
    r = await smallTargets(page, '#need-card');
    assert.deepEqual(r.small, [], 'the review screen, a question: 44 px');
    await page.locator('#need-card [data-dc="next"]').dispatchEvent('click');
    await page.locator('#need-card[open] [data-dc="approve"]:not([disabled])').waitFor();
    r = await smallTargets(page, '#need-card');
    assert.deepEqual(r.small, [], 'the review screen, an OK: 44 px');
    // With the keyboard up (the visible height cut to 420 px), Approve and Send back stay on screen.
    await page.locator('#dc-notes').focus();
    await page.evaluate(`document.documentElement.style.setProperty('--chat-viewport-height', '420px')`);
    const acts = await page.locator('#need-card [data-dc="approve"]').boundingBox();
    assert.ok(acts && acts.y + acts.height <= 420, `Approve above the keyboard: ${JSON.stringify(acts)}`);
    await shot(page, '3-review-keyboard');
    await page.evaluate(`document.documentElement.style.setProperty('--chat-viewport-height', '${PHONE.height}px')`);
    await shot(page, '3-review');
    await page.locator('#need-card [data-dc="close"]').click();
    // On a pipeline's board a card opens at its conversation (pipeline-ask.ts); Details shows the rest. A task with no
    // ask of Ana's opens so (one that waits on her ask opens the ask, ask-screen-open-task.cts).
    await page.locator('.pv-tab[data-pv-tab="build"]').click();
    await page.locator('.pv-phone .tk-card', {hasText: 'Rounding in the API'}).click();
    await page.locator('#task-modal[open] .tthread-title').waitFor();
    await page.waitForTimeout(300);
    r = await smallTargets(page, '#task-modal');
    assert.deepEqual(r.small, [], 'a task\'s conversation: 44 px');
    assert.ok(r.sideways <= 1, 'a task\'s conversation: no sideways scroll');
    await page.locator('#task-modal [data-thread-details]').click();
    await page.locator('#task-modal[open] .tmodal-title').waitFor();
    await page.waitForTimeout(300);
    r = await smallTargets(page, '#task-modal');
    assert.deepEqual(r.small, [], 'a task: 44 px');
    assert.ok(r.sideways <= 1, 'a task: no sideways scroll');
    await shot(page, '4-task');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('a 390 px phone: Tasks, a pipeline, a task and the review screen have 44 px controls, nothing sideways: ok');
}

if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const run of [playOnStages, nothingMoves, walkOnAPhone, chipsOnYou, deepLink, phonePolish]) await run(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
