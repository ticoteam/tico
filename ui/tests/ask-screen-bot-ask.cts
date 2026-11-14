import type {Route} from 'playwright';
// Offline browser check of a bot's question on the ask screen (Tasks spec "Bots ask people a question with choices",
// picture 2, task #373), against a fake of the new routes, on a 390 px phone and on a desktop:
// - AC-3: the question, "Why it matters: …", each choice with its line, "Recommended" on the one the bot recommends,
//   then "I don't know, explain" and "Give it back" on one row with Skip;
// - AC-13: "Write to <bot>" and Send on every bot question, with or without choices; Send lines up with the box's right
//   edge; a sent text is a comment on the task naming the ask, and the question stays on screen, open;
// - Send goes the way the reply box's does: a text a team rule stops says so and keeps the words, never "Sent"; and
//   moving on while it is sent leaves the next question's box as typed;
// - AC-4, AC-5: "I don't know, explain" and "Give it back" answer the ask with those words;
// - a person's question keeps "Something else", which answers it.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:builder', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
type Brief = {why: string; details: string[] | null; recommend: string | null};
const question = (n: number, line: string, choices: string[] | null, by: string, sayWhy: Brief | null) => ({kind: 'ask', id: `r${n}`,
  task_id: `t${n}`, requirement_id: `r${n}`, line, since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, `Task ${n}`),
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: by, choices, may: ['answer', 'thumbs'],
    met: false, shows: [], standing: 'waiting', answer: null, ...(sayWhy ? {brief: sayWhy} : {})}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
// Picture 2's ask, word for word in its length.
const Q = ['On hosted Tico, may the usage count start before the owner sees the notice?', 'What is this team for, in a sentence or two?',
  'Which colour for the launch page?'];
const WHY = 'our privacy promise says nothing is sent before the owner has seen the notice.';
const CHOICES = ['No: show the notice first', 'Yes: change the rule for hosted', 'No count on hosted'];
const DETAILS = ['Builder adds the notice on hosted. About half a day; the PR waits for it.',
  'PRIVACY.md changes in the same PR. Hosted owners are counted before they see a notice.',
  'Hosted servers send nothing. We lose hosted usage numbers.'];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    for (const [label, viewport] of [['phone', {width: 390, height: 844}], ['desktop', {width: 1200, height: 900}]] as const) {
      const errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
      let needs = [
        question(1, Q[0]!, CHOICES, 'bot:reviewer-2', {why: WHY, details: DETAILS, recommend: CHOICES[0]!}),
        question(2, Q[1]!, null, 'bot:strategy', {why: 'Every bot plans its work from it.', details: null, recommend: null}),
        question(3, Q[2]!, ['Blue', 'Green'], 'human:ben', null),
      ];
      // How the comment route answers: sent, or held by a rule that stops it; `gate` holds the answer back until released.
      let commentHeld = false, gate: Promise<void> | null = null;
      const page = await browser.newPage({viewport, serviceWorkers: 'block'});
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
        if (p === '/api/employees') return json([{name: 'reviewer-2', display_name: 'Reviewer 2'}, {name: 'strategy', display_name: 'Strategy Owner'}]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
        if (r.method() !== 'GET' && p.startsWith('/api/')) {
          writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
          const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
          if (m) { needs = needs.filter(n => n.id !== m[2]); return json({task: brief(m[1]!, 'Task'), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
          if (/^\/api\/v2\/tasks\/t\d\/comments$/.test(p)) {
            if (gate) await gate;
            return json(commentHeld ? {held: {id: 'h1', outcome: 'blocked', state: 'held', author: 'human:ana', text: 'x',
              rules: [{id: 'ru1', kind: 'internal', text: 'No talk of hosted pricing'}], can_withdraw: true}} : {comment: {id: 'c1'}, comments: []});
          }
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/#/tasks');
      await page.locator('[data-need="r1"]').waitFor();
      await page.locator('#task-play').click();
      const dialog = page.locator('#need-card');
      await dialog.locator('.dc-q', {hasText: Q[0]!}).waitFor();

      // AC-3: the why line, each choice with its line, "Recommended" on the bot's choice only.
      assert.equal(await dialog.locator('.dc-why').innerText(), `Why it matters: ${WHY}`, label);
      const cards = dialog.locator('.dc-choice');
      assert.deepEqual(await cards.locator('.dc-choice-name').allInnerTexts(), CHOICES, label);
      assert.deepEqual(await cards.locator('.dc-choice-line').allInnerTexts(), DETAILS, label);
      assert.deepEqual(await dialog.locator('.dc-rec-tag').allInnerTexts(), ['Recommended'], `${label}: one Recommended`);
      assert.equal(await dialog.locator('.dc-choice.dc-rec .dc-choice-name').innerText(), CHOICES[0], label);

      // AC-13 and the row: Write to <bot>, Send at the box's right edge, then the fixed answers on one row with Skip.
      const layout = async (what: string) => {
        assert.equal(await dialog.locator('.dc-notes > span').innerText(), what, label);
        const box = (await dialog.locator('#dc-other').boundingBox())!, send = (await dialog.locator('[data-dc="write"]').boundingBox())!;
        assert.ok(Math.abs(box.x + box.width - (send.x + send.width)) <= 1, `${label}: Send ends at the box's right edge (${box.x + box.width} vs ${send.x + send.width})`);
        assert.ok(send.y >= box.y + box.height, `${label}: Send is under the box`);
        const row = await Promise.all(['dont-know', 'give-back', 'skip'].map(async k => (await dialog.locator(`[data-dc="${k}"]`).boundingBox())!));
        assert.deepEqual(await Promise.all(['dont-know', 'give-back'].map(k => dialog.locator(`[data-dc="${k}"]`).innerText())),
          ["I don't know, explain", 'Give it back'], label);
        for (const b of row) {
          // Skip is a quiet 40 px link beside 44 px buttons: one row means their middles line up
          assert.ok(Math.abs(b.y + b.height / 2 - (row[0]!.y + row[0]!.height / 2)) <= 1 && b.height >= 40, `${label}: one row of tappable answers (${JSON.stringify(row)})`);
          assert.ok(b.x >= 0 && b.x + b.width <= viewport.width && b.y + b.height <= viewport.height, `${label}: in view`);
        }
        // The answers come after Send. On a phone they stay pinned at the bottom while the screen scrolls, so Send,
        // scrolled to, must not be under them.
        assert.ok(await dialog.locator('[data-dc="write"]').evaluate(el => !!(el.compareDocumentPosition(document.querySelector('[data-dc="dont-know"]')!) & Node.DOCUMENT_POSITION_FOLLOWING)), label);
        if (label === 'desktop') assert.ok(row[0]!.y > send.y, `${label}: the answers come after Send`);
        const covered = await dialog.locator('[data-dc="write"]').evaluate(el => {
          el.scrollIntoView({block: 'nearest'});
          const r = el.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return hit && hit !== el && !el.contains(hit) ? `${hit.tagName}.${hit.className}` : '';
        });
        assert.equal(covered, '', `${label}: Send, scrolled to, is not covered`);
        const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
        assert.ok(sideways <= 0, `${label}: nothing scrolls sideways (${sideways} px)`);
      };
      await layout('Write to Reviewer 2');
      await page.screenshot({path: `/tmp/ask-screen-bot-ask-${label}.png`});

      // A team rule that stops the text: the screen says so and names the rule; the words stay; nothing says Sent.
      const send = dialog.locator('[data-dc="write"]');
      assert.equal(await send.isDisabled(), true, `${label}: Send waits for words`);
      commentHeld = true;
      await dialog.locator('#dc-other').fill('Is hosted pricing settled?');
      await send.click();
      const held = dialog.locator('[data-dc-held]');
      await held.waitFor();
      assert.equal(await held.locator('.dc-held-line').innerText(), 'Not sent: a rule stops it.', label);
      assert.equal(await held.locator('.rh-rule').innerText(), 'No talk of hosted pricing', label);
      assert.equal(await dialog.locator('.dc-sent').count(), 0, `${label}: a held text is not "Sent"`);
      assert.equal(await dialog.locator('#dc-other').inputValue(), 'Is hosted pricing settled?', `${label}: the words stay`);
      commentHeld = false;

      // AC-13: Send writes to the bot on the task, naming the ask; the question stays, the box empties.
      await dialog.locator('#dc-other').fill('Why not show it on the first page instead?');
      await send.click();
      await dialog.locator('.dc-sent', {hasText: 'Sent to Reviewer 2'}).waitFor();
      assert.equal(await held.count(), 0, `${label}: the hold is gone once a text is sent`);
      assert.deepEqual(writes.slice(1), [{p: '/api/v2/tasks/t1/comments', body: {text: 'Why not show it on the first page instead?', ask: 'r1'}}], label);
      writes.splice(0, 1);
      assert.equal(await dialog.locator('.dc-q').innerText(), Q[0], `${label}: the question stays`);
      assert.equal(await dialog.locator('.dc-count').innerText(), '1 of 3', label);
      assert.equal(await dialog.locator('#dc-other').inputValue(), '', `${label}: the box is emptied`);

      // AC-4: I don't know, explain answers it with those words, and the next one comes.
      await dialog.locator('[data-dc="dont-know"]').click();
      await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
      assert.deepEqual(writes[1], {p: '/api/v2/tasks/t1/asks/r1/answer', body: {value: "I don't know, explain", version: 3}}, label);

      // A question with no choices: the same screen without them (picture 3's vision ask).
      assert.equal(await dialog.locator('.dc-choice').count(), 0, label);
      assert.equal(await dialog.locator('.dc-why').innerText(), 'Why it matters: Every bot plans its work from it.', label);
      await page.waitForTimeout(450);     // a new item's buttons take no taps for 400 ms (decideCalm, task #454)
      await layout('Write to Strategy Owner');

      // Send, then › before the answer comes: the next question's box keeps what is typed there, and nothing of the
      // first text shows on it. The desktop runs it.
      if (label === 'desktop') {
        let release: () => void = () => {};
        gate = new Promise<void>(r => { release = r; });
        await dialog.locator('#dc-other').fill('Two sentences, or one?');
        await dialog.locator('[data-dc="write"]').click();
        await dialog.locator('[data-dc="next"]').click();
        await dialog.locator('.dc-q', {hasText: Q[2]!}).waitFor();
        await dialog.locator('#dc-other').fill('typed on the next question');
        const done = page.waitForResponse(r => r.url().endsWith('/api/v2/tasks/t2/comments'));
        release();
        await done;
        gate = null;
        await page.waitForTimeout(100);
        assert.equal(await dialog.locator('#dc-other').inputValue(), 'typed on the next question', `${label}: the next box keeps its words`);
        assert.equal(await dialog.locator('[data-dc-held], .dc-sent').count(), 0, `${label}: nothing of the first text shows here`);
        assert.deepEqual(writes[2], {p: '/api/v2/tasks/t2/comments', body: {text: 'Two sentences, or one?', ask: 'r2'}}, label);
        writes.splice(2, 1);
        await dialog.locator('[data-dc="prev"]').click();
        await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
      }

      // AC-5: Give it back answers it with those words.
      await dialog.locator('[data-dc="give-back"]').click();
      await dialog.locator('.dc-q', {hasText: Q[2]!}).waitFor();
      assert.deepEqual(writes[2], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'Give it back', version: 3}}, label);

      // A person's question keeps Something else, which answers it; Tico's two answers are a bot's only.
      assert.equal(await dialog.locator('.dc-notes > span').innerText(), 'Something else', label);
      assert.equal(await dialog.locator('[data-dc="dont-know"], [data-dc="give-back"], [data-dc="write"]').count(), 0, label);
      assert.deepEqual(errors, [], label);
      await page.close();
    }
    console.log("PASS: a bot's question shows why, each choice's line and Recommended; Write to <bot> sends and keeps it open; I don't know and Give it back answer it, on one row with Skip.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
