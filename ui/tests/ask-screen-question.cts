import type {Locator, Page, Route} from 'playwright';
// Offline browser check of a question on Flow's ask screen (#3786; Tasks spec "Bots ask people a question with
// choices"), against a fake of the routes, on a 390 px phone and a 1280 px desktop. On Tico Team a bot's question read
// "extremely confusing": its task's long text on top, the question twice, "Something else, or tell it what's wrong"
// and three answers in a row. Now:
// - the question once, in full (no short heading, no "…"), then one meta line: who asked, the date, #N ↗;
// - under it the bot's why line and choices (each with its line, Recommended on the one it recommends), a box that
//   says Answer and Send, the main button, which answers it; Snooze and ⋯ end that row, ⋯ last;
// - ⋯ holds "I don't know, explain" and "Give it back" (a bot's question) and Ask back (to whoever asked): it opens
//   and moves by keyboard, Esc closes it onto ⋯; Ask back with an empty box asks for words and sends nothing;
// - the task is the background: one line, its title, that opens to the text. A wide screen has it on the left, open;
//   a phone puts it under the question and the answer, shut unless the question is short;
// - nothing is said twice: the question and the task's title each show once;
// - Tico's line on a question leaves Send the main button unless it picks one of the choices.
// FLOW_SHOTS_DIR=build/flow-shots writes question-phone.png, question-phone-open.png and question-desktop.png.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const TITLES = ['Hosted usage count', 'Growth setup', 'Launch page'];
// The Tico Team task's body, as bots write to each other.
const BODY = 'Growth Owner → Setup: gather the channel list before the first plan. **Needs from the owner**: every '
  + 'channel the company sells on, where ad spend is reported, and where customer numbers live (CRM, sheet, Stripe).\n\n'
  + '- Strategy Owner reads it next\n- Due before Monday\'s planning';
const brief = (n: number) => ({id: `t${n}`, title: TITLES[n - 1], number: n, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:builder', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
type Brief = {why: string; details: string[] | null; recommend: string | null};
const question = (n: number, line: string, choices: string[] | null, by: string, sayWhy: Brief | null) => ({kind: 'ask', id: `r${n}`,
  task_id: `t${n}`, requirement_id: `r${n}`, line, since: `2026-11-09T1${n}:00:00Z`, task: brief(n),
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: by, choices, may: ['answer', 'thumbs'],
    met: false, shows: [], standing: 'waiting', answer: null, title: `Short title ${n}`, ...(sayWhy ? {brief: sayWhy} : {})}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const Q = ['On hosted Tico, may the usage count start before the owner sees the notice?',
  'Which channels do you sell on, and where do your ad spend and customer numbers live?', 'Which colour for the page?'];
const WHY = 'our privacy promise says nothing is sent before the owner has seen the notice.';
const CHOICES = ['No: show the notice first', 'Yes: change the rule for hosted', 'No count on hosted'];
const DETAILS = ['Builder adds the notice on hosted. About half a day; the PR waits for it.',
  'PRIVACY.md changes in the same PR. Hosted owners are counted before they see a notice.',
  'Hosted servers send nothing. We lose hosted usage numbers.'];
const WANTS = 'Wants a list: your channels, and where spend and customer numbers live';
const SHOTS = process.env.FLOW_SHOTS_DIR ? path.resolve(process.env.FLOW_SHOTS_DIR) : '';

const box = async (l: Locator) => (await l.boundingBox())!;
const times = (all: string, part: string) => all.split(part).length - 1;

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
  try {
    for (const [label, viewport] of [['phone', {width: 390, height: 844}], ['desktop', {width: 1280, height: 800}]] as const) {
      const phone = label === 'phone', errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
      let needs = [
        question(1, Q[0]!, CHOICES, 'bot:reviewer-2', {why: WHY, details: DETAILS, recommend: CHOICES[0]!}),
        question(2, Q[1]!, null, 'bot:growth', null),
        question(3, Q[2]!, ['Blue', 'Green'], 'human:ben', null),
      ];
      const page: Page = await browser.newPage({viewport, serviceWorkers: 'block', reducedMotion: 'reduce'});   // no "Getting ready" cover
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', async (route: Route) => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname;
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
        if (p === '/api/me') return json({...me, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([{name: 'reviewer-2', display_name: 'Reviewer 2'}, {name: 'growth', display_name: 'Growth Owner'}]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
        // Tico's line on the Growth question says what kind of answer is wanted and picks nothing.
        if (p === '/api/v2/flow/opinions') {
          const ids = (JSON.parse(r.postData() || '{}') as {items?: string[]}).items || [];
          return json({opinions: ids.map(id => id === 'r2' ? {id, state: 'ready', pick: '', line: WANTS, note: '', score: 50}
            : {id, state: 'none', pick: '', line: '', note: '', score: null})});
        }
        const read = r.method() === 'GET' ? p.match(/^\/api\/v2\/tasks\/t(\d)$/) : null;
        if (read) return json({task: {...brief(Number(read[1])), body: read[1] === '3' ? '' : BODY, requester_id: 'human:ana'}});
        if (r.method() !== 'GET' && p.startsWith('/api/')) {
          writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
          const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
          if (m) { needs = needs.filter(n => n.id !== m[2]); return json({task: brief(Number(m[1]!.slice(1))), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
          if (/^\/api\/v2\/tasks\/t\d\/comments$/.test(p)) return json({comment: {id: 'c1'}, comments: []});
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('[data-need="r1"]').waitFor();
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      const calm = () => page.waitForTimeout(450);   // a new item's buttons take no taps for 400 ms (decideCalm)
      const bg = dialog.locator('.dc-bg');
      await dialog.locator('.dc-q', {hasText: Q[0]!}).waitFor();
      await bg.locator('.dc-md').waitFor({state: 'attached'});   // the task's read has come

      // The question once, in full: no short title, no "…", no repeat; the task's title once, in the background.
      const all = await dialog.evaluate(el => (el as HTMLElement).innerText + ' ' + [...el.querySelectorAll('details:not([open]) .dc-bg-body')].map(d => d.textContent).join(' '));
      assert.equal(await dialog.locator('.dc-q').count(), 1, label);
      assert.equal(await dialog.locator('.dc-q').innerText(), Q[0], `${label}: the question as asked`);
      assert.equal(times(all, Q[0]!), 1, `${label}: the question shows once`);
      assert.equal(times(all, TITLES[0]!), 1, `${label}: the task's title shows once`);
      assert.equal(times(all, 'Short title 1'), 0, `${label}: the ask's short title is not a heading`);
      assert.equal(await dialog.locator('.dc-who').count(), 0, `${label}: no "Asked by … on …" line beside the meta line`);
      assert.match(await dialog.locator('.dc-ask-meta').evaluate(el => el.textContent || ''), /^Reviewer 2·.+·#1 ↗$/, label);

      // The bot's why line and choice cards, then the answer box (Answer) and Send, the main button.
      assert.equal(await dialog.locator('.dc-why').innerText(), `Why it matters: ${WHY}`, label);
      assert.deepEqual(await dialog.locator('.dc-choice .dc-choice-name').allInnerTexts(), CHOICES, label);
      assert.deepEqual(await dialog.locator('.dc-choice .dc-choice-line').allInnerTexts(), DETAILS, label);
      assert.deepEqual(await dialog.locator('.dc-rec-tag').allInnerTexts(), ['Recommended'], label);
      const answer = dialog.locator('#dc-other'), send = dialog.locator('[data-dc="other"]');
      assert.equal(await answer.getAttribute('placeholder'), 'Answer', label);
      assert.equal(await send.innerText(), 'Send', label);
      assert.ok(await send.evaluate(el => el.classList.contains('primary')), `${label}: Send is the main button`);
      assert.equal(await send.isDisabled(), true, `${label}: Send waits for words`);
      const q = await box(dialog.locator('.dc-q')), list = await box(dialog.locator('.dc-choice-list')), field = await box(answer);
      assert.ok(q.y < list.y && list.y < field.y, `${label}: question, choices, then the box`);

      // One row: Send, Snooze, ⋯ last at its far end; Tico's two answers are not on it.
      const row = await Promise.all(['other', 'snooze', 'ask-more'].map(k => box(dialog.locator(`[data-dc="${k}"]`))));
      for (const b of row) {
        assert.ok(Math.abs(b.y + b.height / 2 - (row[0]!.y + row[0]!.height / 2)) <= 1, `${label}: one row (${JSON.stringify(row)})`);
        assert.ok(b.height >= (phone ? 44 : 40) && b.x >= 0 && b.x + b.width <= viewport.width && b.y + b.height <= viewport.height, `${label}: tappable, in view`);
      }
      assert.ok(row[0]!.x < row[1]!.x && row[1]!.x < row[2]!.x, `${label}: Send · Snooze · ⋯`);
      const end = await box(dialog.locator('.dc-side [data-key] > .dc-acts'));
      assert.ok(row[2]!.x - (row[1]!.x + row[1]!.width) <= 12, `${label}: ⋯ sits beside Snooze`);
      assert.ok(end.x + end.width - (row[2]!.x + row[2]!.width) <= 20, `${label}: ⋯ is last, at the row's far end`);
      assert.equal(await dialog.locator('[data-dc="dont-know"], [data-dc="give-back"], [data-dc="askback"], [data-dc="write"]').count(), 0, `${label}: in ⋯ only`);

      // The background: on the left and open on a wide screen; on a phone under the answer, one line, shut.
      assert.equal(await bg.locator('summary .dc-bg-t').innerText(), TITLES[0], label);
      const thing = await box(dialog.locator('.dc-thing')), side = await box(dialog.locator('.dc-side'));
      if (phone) {
        assert.equal(await bg.evaluate(el => (el as HTMLDetailsElement).open), false, `${label}: a long question's background is shut`);
        assert.ok(thing.y >= field.y + field.height, `${label}: the background comes after the question and its box`);
        const sum = await box(bg.locator('summary'));
        assert.ok(sum.height >= 44 && sum.height <= 56, `${label}: one line (${sum.height} px)`);
        await bg.locator('summary').evaluate(el => el.scrollIntoView({block: 'nearest'}));
        await bg.locator('summary').click();
        await bg.locator('.dc-md').waitFor();
        assert.match(await bg.locator('.dc-md').innerText(), /Needs from the owner/, `${label}: it opens to the task's text`);
        await page.evaluate(() => document.querySelector('.dc-main')?.scrollTo(0, 0));
      } else {
        assert.equal(await bg.evaluate(el => (el as HTMLDetailsElement).open), true, `${label}: open on the left`);
        assert.ok(thing.x + thing.width <= side.x + 1, `${label}: the background is the left pane`);
        assert.ok(await bg.locator('.dc-md').isVisible(), label);
      }
      const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
      assert.ok(sideways <= 0, `${label}: nothing scrolls sideways (${sideways} px)`);

      // ⋯ by keyboard: Enter opens it on its first answer, ↓ ↑ move and wrap, Esc closes it back onto ⋯.
      const more = dialog.locator('[data-dc="ask-more"]'), focused = () => page.evaluate(() => document.activeElement?.textContent || '');
      await more.focus();
      await page.keyboard.press('Enter');
      await dialog.locator('.dc-askw [role="menu"]').waitFor();
      assert.equal(await more.getAttribute('aria-expanded'), 'true', label);
      assert.deepEqual(await dialog.locator('.dc-askw [role="menuitem"]').allInnerTexts(), ["I don't know, explain", 'Give it back', 'Ask back', 'Close task'], label);
      assert.equal(await focused(), "I don't know, explain", label);
      await page.keyboard.press('ArrowDown');
      assert.equal(await focused(), 'Give it back', label);
      await page.keyboard.press('ArrowUp');
      await page.keyboard.press('ArrowUp');
      assert.equal(await focused(), 'Close task', `${label}: ↑ wraps`);
      const menu = await box(dialog.locator('.dc-askw [role="menu"]'));
      assert.ok(menu.x >= 0 && menu.x + menu.width <= viewport.width && menu.y >= 0, `${label}: the menu is in view (${JSON.stringify(menu)})`);
      await page.keyboard.press('Escape');
      await dialog.locator('.dc-askw [role="menu"]').waitFor({state: 'detached'});
      assert.equal(await dialog.evaluate(el => (el as HTMLDialogElement).open), true, `${label}: Esc closes the menu, not Flow`);
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-dc')), 'ask-more', `${label}: focus back on ⋯`);

      // Ask back with nothing typed asks for the words and sends nothing; with words it writes to the bot, naming
      // the question, which stays open.
      await more.click();
      await dialog.locator('.dc-askw [role="menuitem"]', {hasText: 'Ask back'}).click();
      assert.equal(await answer.getAttribute('placeholder'), 'Ask what they mean', label);
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'dc-other', label);
      assert.equal(writes.length, 0, `${label}: nothing sent`);
      await answer.fill('Why not show it on the first page instead?');
      await more.click();
      await dialog.locator('.dc-askw [role="menuitem"]', {hasText: 'Ask back'}).click();
      await dialog.locator('.dc-sent', {hasText: 'Sent'}).waitFor();
      assert.deepEqual(writes, [{p: '/api/v2/tasks/t1/comments', body: {text: 'Why not show it on the first page instead?', ask: 'r1'}}], label);
      assert.equal(await dialog.locator('.dc-q').innerText(), Q[0], `${label}: the question stays`);

      // Send answers it in the person's words; the next question comes.
      await answer.fill('No, show the notice first. Half a day is fine.');
      await send.click();
      await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
      assert.deepEqual(writes[1], {p: '/api/v2/tasks/t1/asks/r1/answer', body: {value: 'No, show the notice first. Half a day is fine.', version: 3}}, label);
      await calm();

      // The Growth question: Tico's line says what is wanted; Send stays the main button, nothing else is lit.
      await page.locator('.dc-say', {hasText: WANTS}).waitFor();
      assert.equal(await dialog.locator('.dc-choice, .dc-choices').count(), 0, label);
      assert.ok(await send.evaluate(el => el.classList.contains('primary')), `${label}: Tico picks nothing on a question it only explains`);
      assert.equal(await dialog.locator('.dc-acts .primary').count(), 1, label);
      assert.equal(times(await dialog.innerText(), Q[1]!), 1, `${label}: the question shows once`);
      // The pictures are of this one, the question Tico Team's owner found confusing.
      if (SHOTS) {
        await page.waitForTimeout(200);
        await page.screenshot({path: path.join(SHOTS, phone ? 'question-phone.png' : 'question-desktop.png')});
        if (phone) {
          await bg.locator('summary').click();
          await page.waitForTimeout(200);
          await page.screenshot({path: path.join(SHOTS, 'question-phone-open.png')});
          await bg.locator('summary').click();
        }
      }
      // A bot's other answers from ⋯: I don't know, explain on a phone, Give it back on a desktop.
      const [k, value] = phone ? ['dont-know', "I don't know, explain"] : ['give-back', 'Give it back'];
      await more.click();
      await dialog.locator(`.dc-askw [data-dc="${k}"]`).focus();
      await page.keyboard.press('Enter');
      await dialog.locator('.dc-q', {hasText: Q[2]!}).waitFor();
      assert.deepEqual(writes[2], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value, version: 3}}, label);
      await calm();

      // A person's short question: its background opens on a phone too; ⋯ holds Ask back and Close task; a choice answers it.
      await bg.waitFor();
      assert.equal(await bg.evaluate(el => el.matches('.dc-bg-only') || (el as HTMLDetailsElement).open), true, `${label}: a short question shows its task`);
      assert.match(await dialog.locator('.dc-ask-meta').evaluate(el => el.textContent || ''), /^Ben·.+·#3 ↗$/, label);
      await more.click();
      assert.deepEqual(await dialog.locator('.dc-askw [role="menuitem"]').allInnerTexts(), ['Ask back', 'Close task'], `${label}: a person's question`);
      await page.keyboard.press('Escape');
      await dialog.locator('[data-dc-choice="Green"]').click();
      await page.waitForFunction(() => !document.querySelector('#need-card [data-dc-choice="Green"]'));
      assert.deepEqual(writes[3], {p: '/api/v2/tasks/t3/asks/r3/answer', body: {value: 'Green', version: 3}}, label);
      assert.deepEqual(errors, [], label);
      await page.close();
    }
    console.log('PASS: a question shows once in full with who asked; its choices, Answer and Send under it; ⋯ holds the bot\'s two answers and Ask back by keyboard; the task is one line that opens.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
