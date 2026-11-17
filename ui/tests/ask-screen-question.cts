import type {Locator, Page, Route} from 'playwright';
// Offline browser check of a question on Flow's ask screen (#3786; Tasks spec "Bots ask people a question with
// choices"), against a fake of the routes, on a 390 px phone and a 1280 px desktop. The question is answered in its
// task's chat (decide.ts decideChatMount):
// - the question once, in full, as the asker's last message: who asked, the date, #N ↗ at the right;
// - in it the bot's why line and its choices (each with its line, Recommended on the one it recommends) to tap;
// - under the chat's box only Snooze and ⋯ (Close task, Give to…), ⋯ last; it opens and moves by keyboard, Esc closes
//   it onto ⋯;
// - the composer's arrow sends a reply named for the question and leaves it with the bot (snoozed for the person);
//   a person's question takes the words as its answer;
// - the task, open (no chevron): on the left on a wide screen; in the Task tab on a phone, which opens on Chat;
// - nothing is said twice: the question and the task's title each show once.
// FLOW_SHOTS_DIR=build/flow-shots writes question-phone.png and question-desktop.png.
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
        if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
        // Tico's opinion on the Growth question is ready and says what kind of answer is wanted: it picks nothing and shows no line.
        if (p === '/api/v2/flow/opinions') {
          const ids = (JSON.parse(r.postData() || '{}') as {items?: string[]}).items || [];
          return json({opinions: ids.map(id => id === 'r2' ? {id, state: 'ready', pick: '', line: WANTS, note: '', score: 50}
            : {id, state: 'none', pick: '', line: '', note: '', score: null})});
        }
        const read = r.method() === 'GET' ? p.match(/^\/api\/v2\/tasks\/t(\d)$/) : null;
        if (read) return json({task: {...brief(Number(read[1])), body: read[1] === '3' ? '' : BODY, requester_id: 'human:ana', conversation_id: 'c' + read[1]}});
        if (r.method() !== 'GET' && p.startsWith('/api/') && !p.startsWith('/api/v2/threads/')) {
          writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
          if (p === '/api/v2/needs-you/snooze') { const id = (JSON.parse(r.postData() || '{}') as {id: string}).id; needs = needs.filter(n => n.id !== id); return json({id}); }
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

      // The question once, in full, as the asker's last message; the task's title once, on the left.
      const all = await dialog.evaluate(el => (el as HTMLElement).innerText + ' ' + '');
      assert.equal(await dialog.locator('.dc-q').count(), 1, label);
      assert.equal(await dialog.locator('.dc-chat .dc-msg-ask .dc-q').innerText(), Q[0], `${label}: the question as asked`);
      assert.equal(times(all, Q[0]!), 1, `${label}: the question shows once`);
      assert.equal(times(all, 'Short title 1'), 0, `${label}: the ask's short title is not a heading`);
      assert.match(await dialog.locator('.dc-ask-meta').evaluate(el => el.textContent || ''), /^Reviewer 2·.+#1 ↗$/, label);
      assert.equal(await dialog.locator('.dc-why').innerText(), WHY, label);
      assert.deepEqual(await dialog.locator('.dc-choice .dc-choice-name').allInnerTexts(), CHOICES, label);
      assert.deepEqual(await dialog.locator('.dc-choice .dc-choice-line').allInnerTexts(), DETAILS, label);
      assert.deepEqual(await dialog.locator('.dc-rec-tag').allInnerTexts(), ['Recommended'], label);
      const answer = dialog.locator('.dc-chat .p-text'), arrow = dialog.locator('.dc-chat .p-send');
      await answer.waitFor();
      assert.equal(await dialog.locator('[data-dc="other"], #dc-other, [data-dc="approve"]').count(), 0, `${label}: the chat's arrow sends: no Send, no Approve`);

      // Under the box: Snooze, then ⋯ last at the row's far end.
      const row = await Promise.all(['snooze', 'ask-more'].map(k => box(dialog.locator(`.dc-side [data-key] [data-dc="${k}"]`))));
      for (const b of row) assert.ok(b.height >= (phone ? 44 : 40) && b.x >= 0 && b.x + b.width <= viewport.width && b.y + b.height <= viewport.height, `${label}: tappable, in view`);
      const end = await box(dialog.locator('.dc-side [data-key] > .dc-acts'));
      assert.ok(row[0]!.x < row[1]!.x && end.x + end.width - (row[1]!.x + row[1]!.width) <= 20, `${label}: Snooze · ⋯, ⋯ last`);

      // The task: on the left, open, on a wide screen; on a phone in the Task tab, open, no chevron.
      if (phone) {
        assert.equal(await dialog.locator('.dc-thing').isVisible(), false, `${label}: it opens on the Chat tab`);
        await dialog.locator('[data-dc="tab"][data-dc-tab="task"]').click();
        assert.equal(await bg.locator('.dc-bg-t').innerText(), TITLES[0], label);
        assert.equal(await dialog.locator('.dc-thing details, .dc-thing summary').count(), 0, `${label}: the task open, no chevron`);
        await bg.locator('.dc-md').waitFor();
        assert.match(await bg.locator('.dc-md').innerText(), /Needs from the owner/, `${label}: it opens to the task's text`);
        await dialog.locator('[data-dc="tab"][data-dc-tab="chat"]').click();
      } else {
        const thing = await box(dialog.locator('.dc-thing')), side = await box(dialog.locator('.dc-side'));
        assert.ok(await bg.locator('.dc-md').isVisible(), `${label}: open on the left, no chevron`);
        assert.ok(thing.x + thing.width <= side.x + 1, `${label}: the task is the left pane`);
      }
      const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
      assert.ok(sideways <= 0, `${label}: nothing scrolls sideways (${sideways} px)`);

      // ⋯ by keyboard: Enter opens it on Close task, ↓ ↑ move and wrap, Esc closes it back onto ⋯.
      const more = dialog.locator('[data-dc="ask-more"]'), focused = () => page.evaluate(() => document.activeElement?.textContent || '');
      await more.focus();
      await page.keyboard.press('Enter');
      await dialog.locator('.dc-askw [role="menu"]').waitFor();
      assert.deepEqual(await dialog.locator('.dc-askw [role="menuitem"]').allInnerTexts(), ['Close task', 'Give to…'], label);
      assert.equal(await focused(), 'Close task', label);
      await page.keyboard.press('ArrowUp');
      assert.equal(await focused(), 'Give to…', `${label}: ↑ wraps`);
      await page.keyboard.press('Escape');
      await dialog.locator('.dc-askw [role="menu"]').waitFor({state: 'detached'});
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-dc')), 'ask-more', `${label}: focus back on ⋯`);
      if (SHOTS) { await page.waitForTimeout(200); await page.screenshot({path: path.join(SHOTS, phone ? 'question-phone.png' : 'question-desktop.png')}); }

      // The arrow: a reply named for the question, which leaves it with the bot until it writes back; the next comes.
      await answer.fill('Why not show it on the first page instead?');
      await arrow.click();
      await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
      assert.equal(writes[0]!.p, '/api/v2/needs-you/snooze', `${label}: the snooze first`);
      assert.deepEqual(writes[1], {p: '/api/v2/tasks/t1/comments', body: {text: 'Why not show it on the first page instead?', ask: 'r1'}}, label);
      await calm();

      // The Growth question: its opinion is ready, and no line shows; nothing on the row is lit.
      await page.waitForFunction("DEC_OP.state.get('r2') === 'ready'");
      assert.equal(await dialog.locator('.dc-say').textContent(), '', label);
      assert.equal(await dialog.locator('.dc-choice, .dc-choices').count(), 0, label);
      assert.equal(await dialog.locator('.dc-side [data-key] .primary').count(), 0, label);
      await answer.waitFor();
      await answer.fill('Shopify and Amazon; spend in Meta Ads; customers in HubSpot.');
      await arrow.click();
      await dialog.locator('.dc-q', {hasText: Q[2]!}).waitFor();
      await calm();

      // A person's question: its choices answer it.
      assert.match(await dialog.locator('.dc-ask-meta').evaluate(el => el.textContent || ''), /^Ben·.+#3 ↗$/, label);
      await dialog.locator('[data-dc-choice="Green"]').click();
      await page.waitForFunction(() => !document.querySelector('#need-card [data-dc-choice="Green"]'));
      assert.deepEqual(writes.find(w => w.p.endsWith('/r3/answer')), {p: '/api/v2/tasks/t3/asks/r3/answer', body: {value: 'Green', version: 3}}, label);
      assert.deepEqual(errors, [], label);
      await page.close();
    }
    console.log('PASS: a question in its task\'s chat: once, in full, with who asked; its choices to tap; the arrow replies and leaves it with the bot; ⋯ by keyboard; the task open on the left or in a phone\'s Task tab.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
