import type {Page} from 'playwright';
// Offline browser check of the owner's Strategy questions in Flow (ui/app/decide-setup.ts, docs/strategy.md#flow),
// against a fake of the routes they use (GET /v2/flow/questions, POST /v2/flow/ramble, the Strategy draft routes):
// - after the owner's asks the questions come, as a card of their own: the suggested answer first, a box, Skip;
// - tapping the suggestion saves it to the draft with the draft's revision (a drafted section loses its marker; a
//   proposed bet is made active); a 409 is read again and sent again, once;
// - at phone width each screen has one row of actions, the forward one (Send, Save, Publish) at the far right and in
//   view, every control at least 44x44, nothing wider than the screen; the bet offers up to three proposed bets;
// - the box holds what the question keeps whole, a count showing near it; Send makes one call however often pressed;
// - a long answer is split into lines under their sections' names, what a limit cut struck through; ✕ drops one, one
//   Save sends the rest; one with nothing usable in it is that screen's one line, whole, never saved unseen; a line its
//   section already says is not added again;
// - the end screen shows each part Publish changes with its text, edits made elsewhere included; ✕ takes a line out
//   again (a draft save) only where Flow put it, and leaves one edited since; Publish sends the revision on screen, so
//   a draft changed since is read again for a second tap, never published unseen; after Publish, Keep going reads the
//   questions again (a part filled by a long answer is not asked) and Publish comes back for what is new; parts still
//   marked Unconfirmed are named right above Publish (pinned with it on a phone), each opening its question;
// - opened from the Strategy page (#/decide?setup=strategy) the questions come first, an ask waiting after them; with
//   Publish refused the end screen says what is missing instead; Skip is kept in this browser;
// - anyone but the owner sees none of it, and nothing is read for them.
// FLOW_SHOTS=<folder> saves the question card, the lines and the end screen at phone width (390x844).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Json = Record<string, unknown>;
interface Bet {id: string; statement: string; why?: string; status: string; rank: number | null}
interface Sent {method: string; path: string; body: Json}

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const MARK = '_Unconfirmed: drafted from acme.example._\n\n';
const DRAFTED = 'Landlords with one to twenty units.';
const SELL = 'Bots that answer tenants, $29 a unit.';
const PROPOSED = 'Small landlords will trust a bot with tenant messages';
const RAMBLE = 'We will not take enterprise property managers, even when they ask, because they want custom work. '
  + 'Forty landlords already pay us, which is our proof so far. We also think we should hire a support person next quarter '
  + 'but that is not settled yet and may change.';
const KNOW = 'We ran rentals for ten years.';
const LINES = [
  {section: 'not', text: 'We will not take enterprise property managers, even when they ask.'},
  {section: 'proof', text: 'Forty landlords already pay us.', left: 'which is our proof so far'},
  {section: 'how', text: 'We should hire a support person next quarter.'},
  {section: 'know', text: 'Landlords hate portals.'},
];
const NOW = 'Rents went up a fifth this year.';
const HOW = 'Hmm, how we win. Honestly I do not know yet, I would have to think about it, maybe ask me again next week when '
  + 'I have talked to my business partner about it, sorry, it has been a really long day and I cannot think straight now.';
const MAX = {bet: 2180, section: 4000};
const ASK = {
  serve: 'Who has this problem worst?', sell: 'What do you make?', bet: 'What do you believe that others do not?',
  not: "What won't you do?", proof: 'What shows it works?', now: 'Why now?', know: 'What do you know that others do not?',
  how: 'How will you win?',
} as const;
const TITLE = {serve: 'Who we serve', sell: 'What we sell', bet: 'Bet', not: "What we won't do", proof: 'Proof', now: 'Why now',
  know: 'What we know', how: 'How we win'} as const;
type Key = keyof typeof ASK;
const brief = (id: string) => ({id, title: `Task ${id}`, number: 1, pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const QUESTION = 'Ship the Acme pricing page on Monday?';
const ask = () => ({kind: 'ask', id: 'r1', task_id: 't1', requirement_id: 'r1', line: QUESTION, since: '2026-11-09T11:00:00Z', task: brief('t1'),
  ask: {requirement_id: 'r1', kind: 'answer', line: QUESTION, who: 'human:ana', asked_by: 'bot:pm', choices: ['Yes', 'No'], may: ['answer'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const shots = process.env.FLOW_SHOTS || '';
  try {
    // The draft as the server holds it, and what was sent to it.
    let sections: Record<string, string> = {}, bets: Bet[] = [], revision = 1, current: Json | null = null, n = 0;
    let needs: Json[] = [], conflicts = 0, role = 'owner', hold: Promise<void> | null = null;
    // What the split answers: the lines, or (nothing usable in the answer) the whole text and why not.
    let split: (body: Json) => Json = () => ({lines: LINES, split: true, why: null});
    const errors: string[] = [], sent: Sent[] = [], reads: string[] = [];
    const reset = (s: Record<string, string>, b: Bet[]) => { sections = {...s}; bets = b.map(x => ({...x})); revision = 1; current = null; n = 0; sent.length = 0; reads.length = 0; };
    const open = (k: string) => !(sections[k] || '').trim() || (sections[k] || '').startsWith('_Unconfirmed:');
    const questions = () => {
      if (role !== 'owner') return {questions: [], more: [], revision: null, titles: {}};
      const q = (key: Key): Json[] => {
        if (key === 'bet') {
          if (bets.some(b => b.status === 'active')) return [];
          const top = bets.filter(b => b.status === 'proposed').slice(0, 3).map(b => ({text: b.statement, bet_id: b.id}));
          return [{key, title: TITLE[key], question: ASK[key], choices: top, max: MAX.bet}];
        }
        if (!open(key)) return [];
        const drafted = (sections[key] || '').startsWith(MARK) ? (sections[key] || '').replace(MARK, '') : '';
        return [{key, title: TITLE[key], question: ASK[key], choices: drafted ? [{text: drafted, bet_id: null}] : [], max: MAX.section}];
      };
      return {questions: (['serve', 'sell', 'bet', 'not'] as Key[]).flatMap(q), more: (['proof', 'now', 'know', 'how'] as Key[]).flatMap(q), revision, titles: TITLE};
    };
    const draft = () => ({revision, guide: 'default', summary: '', vision: '', sections: {...sections}, bets: bets.map(b => ({...b}))});
    const strategy = () => ({may_edit: role === 'owner', draft: role === 'owner' ? draft() : null, current, bets: role === 'owner' ? bets : [],
      label: 'Bets', label_one: 'Bet', goals_by_bet: {}, health_by_bet: {}});
    const stale = (body: Json) => body['revision'] !== revision || (conflicts > 0 && (conflicts--, revision++, true));

    const start = async (phone: boolean, hash: string): Promise<Page> => {
      const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block',
        reducedMotion: 'reduce', ...(phone ? {isMobile: true, hasTouch: true} : {})});
      const page = await context.newPage();
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', async route => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname, method = r.method();
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const conflict = () => json({error: {code: 'conflict', message: 'The draft changed. Reload it.'}, revision}, 409);
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (isUiPath(p)) return serveUi(route, p);
        if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role, mover: true, cloud: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: 'v' + needs.length, next: null});
        if (p === '/api/v2/flow/opinions') return json({opinions: []});
        if (p === '/api/v2/flow/suggestions') return json({state: 'ready', suggestions: []});
        if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        if (method === 'GET' && p === '/api/v2/flow/questions') { reads.push(p); return json(questions()); }
        if (method === 'GET' && p === '/api/v2/strategy') return json(strategy());
        if (method === 'GET') return json(unrouted(p));
        const body = (r.postDataJSON() || {}) as Json;
        if (p.startsWith('/api/v2/strategy') || p.startsWith('/api/v2/flow/ramble') || p.includes('/asks/')) sent.push({method, path: p, body});
        const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
        if (m) { needs = []; return json({task: brief(m[1]!), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
        if (p === '/api/v2/flow/ramble') { if (hold) await hold; return json(split(body)); }
        if (p === '/api/v2/strategy/draft' && method === 'PUT') {
          if (stale(body)) return conflict();
          Object.assign(sections, body['sections'] as Record<string, string>);
          revision++;
          return json({draft: draft()});
        }
        if (p === '/api/v2/strategy/bets') {
          if (stale(body)) return conflict();
          const bet: Bet = {id: `b${bets.length + 1}`, statement: String(body['statement']), status: String(body['status'] || 'proposed'), rank: null,
            ...(body['why'] ? {why: String(body['why'])} : {})};
          bets.push(bet); revision++;
          return json({bet, revision});
        }
        const one = p.match(/^\/api\/v2\/strategy\/bets\/(b\d+)$/);
        if (one) {
          if (stale(body)) return conflict();
          const bet = bets.find(b => b.id === one[1])!;
          bet.status = String(body['status']); revision++;
          return json({bet, revision});
        }
        if (p === '/api/v2/strategy/publish') {
          if (stale(body)) return conflict();
          // A version keeps every bet not retired (strategy.publish).
          n++; current = {...draft(), n, bets: bets.filter(b => b.status !== 'retired').map(b => ({...b}))}; revision++;
          return json({...current});
        }
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/' + hash);
      await page.waitForFunction(() => typeof S !== 'undefined' && !!S.me);
      return page;
    };
    const shot = async (page: Page, name: string) => {
      if (!shots) return;
      await page.waitForTimeout(300);
      await page.screenshot({path: path.join(shots, `${name}.png`)});
    };
    const writes = () => sent.filter(s => s.path.startsWith('/api/v2/strategy'));
    // At phone width: one row of actions, the forward one (if any) furthest right and fully in view, uncovered; every
    // control on the screen at least 44x44; nothing wider than the screen.
    const layout = async (page: Page, what: string) => {
      const got = await page.evaluate(() => {
        const row = document.querySelector<HTMLElement>('#need-card .dc-qs .dc-q-acts')!;
        const shown = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('button')].filter(b => b.offsetParent);
        const rects = shown(row).map(b => ({dc: b.dataset['dc'] || '', r: b.getBoundingClientRect()}));
        const right = rects.reduce((a, b) => b.r.right > a.r.right ? b : a);
        const main = row.querySelector<HTMLElement>('.primary'), m = main?.getBoundingClientRect();
        const hit = m ? document.elementFromPoint(m.left + m.width / 2, m.top + m.height / 2) : null;
        return {
          rows: new Set(rects.map(x => Math.round(x.r.top))).size, right: right.dc, main: main?.dataset['dc'] || '',
          seen: !m || (m.top >= 0 && m.bottom <= innerHeight && m.left >= 0 && m.right <= innerWidth && !!hit && row.contains(hit)),
          small: shown(document.querySelector('#need-card .dc-qs')!).filter(b => { const r = b.getBoundingClientRect(); return r.width < 44 || r.height < 44; })
            .map(b => b.dataset['dc'] || b.textContent),
          wide: document.documentElement.scrollWidth,
        };
      });
      assert.equal(got.rows, 1, `${what}: one row of actions`);
      if (got.main) assert.equal(got.right, got.main, `${what}: the forward action at the far right`);
      assert.equal(got.seen, true, `${what}: the forward action in view, nothing over it`);
      assert.deepEqual(got.small, [], `${what}: every control at least 44x44`);
      assert.ok(got.wide <= 390, `${what}: nothing wider than the screen (${got.wide})`);
      return got.main;
    };

    // ---- A phone: an ask first, then the questions; each kind of save; the end screen and Publish.
    {
      reset({serve: MARK + DRAFTED, know: KNOW, how: MARK + 'Referrals.'}, [{id: 'b1', statement: PROPOSED, status: 'proposed', rank: 1},
        {id: 'b2', statement: 'Landlords pay per unit', status: 'proposed', rank: 2}, {id: 'b3', statement: 'Portals lose to texts', status: 'proposed', rank: 3},
        {id: 'b4', statement: 'Rent apps consolidate', status: 'proposed', rank: 4}]);
      needs = [ask()];
      const page = await start(true, '#/updates');
      const d = page.locator('#need-card');
      await page.locator('#mobile-flow[data-waiting]').waitFor({state: 'attached'});
      await page.locator('#mobile-flow').tap();
      await d.locator('.dc-q', {hasText: QUESTION}).waitFor();
      assert.deepEqual(reads, [], 'the questions are not read while an ask is on screen');
      await d.locator('[data-dc-choice="Yes"]').tap();

      // After the ask: who we serve, its drafted text offered first, then the box and Skip.
      const card = d.locator('.dc-qs[data-q="serve"]');
      await card.waitFor();
      assert.equal(await card.locator('.dc-q').textContent(), ASK.serve);
      assert.equal(await card.locator('.dc-choice.dc-rec').textContent(), DRAFTED, 'the suggestion, its marker left out');
      assert.equal(await card.locator('.dc-choice-list + .dc-note-row textarea').count(), 1, 'the box under the suggestion');
      assert.equal(await card.locator('[data-dc="q-skip"]').isVisible(), true);
      assert.equal(await page.locator('#mobile-flow').getAttribute('data-waiting'), null, 'the questions never light the Flow tab');
      assert.equal(await d.locator('[data-dc="prev"]').isDisabled(), true);
      // The mic is the box: it takes focus, so the keyboard's own dictation types into it.
      await d.locator('.dc-mic').tap();
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'dc-q-say');
      assert.equal(await d.locator('.dc-wave').isVisible(), false, 'no voice mode');
      assert.equal(await layout(page, 'question'), 'q-send');
      await shot(page, 'question-card');

      // The suggestion tapped: the drafted section is the owner's now, sent with the revision.
      await card.locator('.dc-choice').tap();
      await d.locator('.dc-qs[data-q="sell"]').waitFor();
      assert.deepEqual(writes().map(s => [s.method, s.path, s.body]), [['PUT', '/api/v2/strategy/draft', {revision: 1, sections: {serve: DRAFTED}}]]);
      assert.equal(sections['serve'], DRAFTED);

      // A short answer, typed; the draft changed elsewhere meanwhile: read again and sent again with the new revision.
      conflicts = 1;
      await d.locator('#dc-q-say').fill(SELL);
      await d.locator('[data-dc="q-send"]').tap();
      await d.locator('.dc-qs[data-q="bet"]').waitFor();
      const sells = writes().slice(1);
      assert.deepEqual(sells.map(s => s.body['revision']), [2, 3], 'a 409, then once more with the revision read again');
      assert.deepEqual(sells.map(s => s.body['sections']), [{sell: SELL}, {sell: SELL}]);
      assert.equal(sections['sell'], SELL);

      // The bet: the top three proposed offered, the top one first and marked; tapped, made active.
      assert.deepEqual(await d.locator('.dc-qs[data-q="bet"] .dc-choice').allTextContents(), [PROPOSED, 'Landlords pay per unit', 'Portals lose to texts']);
      assert.equal(await d.locator('.dc-qs[data-q="bet"] .dc-choice.dc-rec').textContent(), PROPOSED);
      await d.locator('.dc-qs[data-q="bet"] .dc-choice').first().tap();
      await d.locator('.dc-qs[data-q="not"]').waitFor();
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/bets/b1', body: {status: 'active', revision: 4}});

      // The box holds what this question keeps whole; its count shows only near that.
      const box = d.locator('#dc-q-say');
      assert.equal(await box.getAttribute('maxlength'), String(MAX.section));
      await box.fill('x'.repeat(3700));
      assert.equal(await d.locator('.dc-q-count').textContent(), `3700/${MAX.section}`);
      assert.equal(await d.locator('.dc-q-count').isVisible(), true);
      // A long answer: one call however often Send is pressed while it runs.
      await box.fill(RAMBLE);
      assert.equal(await d.locator('.dc-q-count').isVisible(), false, 'no count far from the limit');
      let release = () => {};
      hold = new Promise<void>(resolve => { release = resolve; });
      await d.locator('[data-dc="q-send"]').tap();
      await page.waitForFunction(() => DEC_Q.busy);
      assert.equal(await d.locator('[data-dc="q-send"]').isDisabled(), true, 'Send stays off while the split runs');
      await page.evaluate(() => { const b = document.querySelector<HTMLButtonElement>('[data-dc="q-send"]')!; b.disabled = false; b.click(); });
      release(); hold = null;
      // Its lines under their sections' names, what a limit cut struck through; ✕ drops one, then one Save.
      await d.locator('[data-q-strike]').waitFor();
      const rambles = sent.filter(s => s.path === '/api/v2/flow/ramble');
      assert.equal(rambles.length, 1, 'one call for one Send');
      assert.deepEqual(rambles[0]!.body, {text: RAMBLE, section: 'not'}, 'only the words and the section asked');
      assert.deepEqual(await d.locator('.dc-ql .tx span').allTextContents(), [TITLE.not, TITLE.proof, TITLE.how, TITLE.know]);
      assert.deepEqual(await d.locator('.dc-ql .tx b').allTextContents(), LINES.map(l => l.text));
      assert.deepEqual(await d.locator('.dc-ql .tx del[aria-label="Not kept"]').allTextContents(), [LINES[1]!.left]);
      await d.locator('.dc-ql').nth(2).locator('[data-dc="q-drop"]').tap();
      assert.equal(await d.locator('.dc-ql.off').count(), 1);
      assert.equal(await layout(page, 'strike'), 'q-save');
      await shot(page, 'strike-screen');
      const before = writes().length;
      await d.locator('[data-dc="q-save"]').tap();

      // The end: each part Publish changes, with its text as it would publish; under it the lines Flow saved there.
      const end = d.locator('[data-q-end]');
      await end.locator('[data-dc="q-publish"]').waitFor();
      const appended = `${KNOW}\n\n${LINES[3]!.text}`;
      assert.deepEqual(writes().slice(before).map(s => s.body),
        [{revision: 5, sections: {not: LINES[0]!.text, proof: LINES[1]!.text, know: appended}}], 'one save');
      assert.equal(sections['how'], MARK + 'Referrals.', 'the dropped line is not saved');
      assert.equal(await end.locator('.dc-q-said').textContent(), 'Saved to your draft. Bots use it after you publish.');
      const groups = async () => end.locator('.dc-qg').evaluateAll(gs => gs.map(g => [g.querySelector('h4')?.textContent,
        g.querySelector('.dc-qt')?.textContent ?? null, [...g.querySelectorAll('li>span')].map(x => x.textContent)]));
      assert.deepEqual(await groups(), [
        [TITLE.serve, DRAFTED, []], [TITLE.sell, SELL, []], [TITLE.not, LINES[0]!.text, []], [TITLE.proof, LINES[1]!.text, []],
        [TITLE.know, appended, [LINES[3]!.text]], [TITLE.how, 'Referrals.', []], ['Bets', 'Small landlords will trust a bot with tenant messages\nLandlords pay per unit (proposed)\nPortals lose to texts (proposed)\nRent apps consolidate (proposed)', [PROPOSED]],
      ], 'a part that is just Flow\'s line carries its ✕; else Flow\'s lines are listed under the text');
      assert.equal(await end.locator('.dc-qg li [data-dc="q-unsave"]').count(), 6);
      // A part still drafted is named above Publish (it would go out as drafted); Publish is not stopped by it.
      assert.deepEqual(await end.locator('[data-q-drafted] .dc-q-fix').allTextContents(), [`Unconfirmed: ${TITLE.how}`]);
      assert.equal(await layout(page, 'end'), 'q-publish');
      // On a phone they are pinned on the row: in view with Publish, nothing to scroll, nothing over them.
      const pinned = await page.evaluate(() => {
        const note = document.querySelector<HTMLElement>('#need-card [data-q-drafted] .dc-q-fix')!.getBoundingClientRect();
        const row = document.querySelector<HTMLElement>('#need-card .dc-q-acts')!.getBoundingClientRect();
        const hit = document.elementFromPoint(note.left + note.width / 2, note.top + note.height / 2);
        return note.top >= 0 && note.bottom <= row.top + 1 && !!hit?.closest('[data-q-drafted]');
      });
      assert.equal(pinned, true, 'the parts still drafted show right above Publish without scrolling');
      assert.equal(await end.locator('.dc-qt', {hasText: '_Unconfirmed'}).count(), 0, 'no marker markdown on the end screen');
      assert.deepEqual(await end.locator('.dc-q-acts button').evaluateAll(bs => bs.map(b => b.getAttribute('data-dc'))), ['q-more', 'q-done', 'q-publish']);
      // A long text is clamped; a tap shows it whole.
      const know = end.locator('.dc-qg', {hasText: TITLE.know}).locator('.dc-qt');
      await know.click();
      assert.equal(await know.getAttribute('aria-expanded'), 'true');
      await shot(page, 'end-screen');

      // ✕ on a line edited elsewhere since: nothing written, the edit kept, and its ✕ gone.
      sections['proof'] = `${LINES[1]!.text} Twelve more signed in May.`; revision++;
      const edited = writes().length;
      await end.locator('.dc-qg li', {hasText: LINES[1]!.text}).locator('[data-dc="q-unsave"]').tap();
      await end.locator('.dc-qt', {hasText: 'Twelve more'}).waitFor();
      assert.equal(writes().length, edited, 'no write over the edit');
      assert.equal(sections['proof'], `${LINES[1]!.text} Twelve more signed in May.`);
      assert.equal(await end.locator('.dc-qg', {hasText: TITLE.proof}).locator('[data-dc="q-unsave"]').count(), 0);
      // ✕ on a line Flow added after what was there: exactly that taken out, the rest as it was.
      await end.locator('.dc-qg li', {hasText: LINES[3]!.text}).locator('[data-dc="q-unsave"]').tap();
      await end.locator('.dc-qg li', {hasText: LINES[3]!.text}).waitFor({state: 'detached'});
      assert.deepEqual(writes().at(-1)!.body, {revision: 7, sections: {know: KNOW}});

      // Publish sends the revision on screen: the draft changed elsewhere since, so nothing is published; the screen is
      // read again, showing that change, and Publish waits for another tap.
      sections['now'] = NOW; revision++;
      await end.locator('[data-dc="q-publish"]').tap();
      await end.locator('.dc-qt', {hasText: NOW}).waitFor();
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/publish', body: {note: '', revision: 8}});
      assert.equal(n, 0, 'nothing published unseen');
      assert.equal(await end.locator('[role="alert"]').textContent(), 'It changed. Check it, then Publish.');
      await end.locator('[data-dc="q-publish"]').tap();
      await end.locator('.dc-q-said', {hasText: 'Published v1'}).waitFor();
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/publish', body: {note: '', revision: 9}});
      assert.equal(await end.locator('[data-dc="q-unsave"]').count(), 0);
      assert.equal(await end.locator('[data-dc="q-publish"]').count(), 0, 'nothing left to publish');
      assert.equal(await d.locator('[data-dc="undo"][aria-label*="ublish"]').count(), 0, 'no Undo for Publish');

      // Keep going reads the questions again: proof, know and now are filled now, so only how (still drafted) is asked.
      const read = reads.length;
      await end.locator('[data-dc="q-more"]').tap();
      await d.locator('.dc-qs[data-q="how"]').waitFor();
      assert.equal(await d.locator('.dc-qs[data-q="how"] .dc-choice').textContent(), 'Referrals.');
      assert.equal(reads.length, read + 1);
      // A long answer with nothing usable in it: never saved unseen, but shown whole as the one line for the part
      // asked, to ✕ or Save.
      split = body => ({lines: [{section: body['section'], text: body['text']}], split: false, why: 'failed'});
      const saves = writes().length;
      await d.locator('#dc-q-say').fill(HOW);
      await d.locator('[data-dc="q-send"]').tap();
      await d.locator('[data-q-strike]').waitFor();
      assert.equal(writes().length, saves, 'nothing saved before the owner sees it');
      assert.deepEqual(await d.locator('.dc-ql .tx span').allTextContents(), [TITLE.how]);
      assert.deepEqual(await d.locator('.dc-ql .tx b').allTextContents(), [HOW]);
      await d.locator('.dc-ql [data-dc="q-drop"]').tap();
      assert.equal(await d.locator('[data-dc="q-save"]').isDisabled(), true, 'nothing left to save');
      await d.locator('.dc-ql [data-dc="q-drop"]').tap();
      await d.locator('[data-dc="q-save"]').tap();
      // A new answer after Publish: Publish again, with ✕ for it alone.
      await end.locator('[data-dc="q-publish"]').waitFor();
      assert.equal(sections['how'], HOW, 'saved whole, in place of the drafted text');
      assert.equal(await end.locator('.dc-q-said').textContent(), 'Saved to your draft. Bots use it after you publish.');
      assert.deepEqual(await end.locator('.dc-qg', {has: page.locator('[data-dc="q-unsave"]')}).locator('h4').allTextContents(), [TITLE.how]);
      await end.locator('[data-dc="q-publish"]').tap();
      await end.locator('.dc-q-said', {hasText: 'Published v2'}).waitFor();
      assert.equal(await end.locator('[data-q-drafted]').count(), 0, 'nothing still drafted');
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/publish', body: {note: '', revision: 11}});
      await end.locator('[data-dc="q-done"]').tap();
      await d.waitFor({state: 'hidden'});
      await page.context().close();
    }

    // ---- From the Strategy page: the questions first, an ask after them; Publish refused says what is missing; Skip kept.
    {
      reset({know: MARK + KNOW}, []);
      needs = [ask()];
      const page = await start(false, '#/decide?setup=strategy');
      const d = page.locator('#need-card');
      await d.locator('.dc-qs[data-q="serve"]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/strategy', 'Flow over the Strategy page');
      assert.equal(await d.locator('.dc-choice').count(), 0, 'nothing to suggest: the box alone');
      for (const key of ['serve', 'sell', 'bet', 'not']) {
        await d.locator(`.dc-qs[data-q="${key}"]`).waitFor();
        await d.locator('[data-dc="q-skip"]').click();
      }
      const end = d.locator('[data-q-end]');
      await end.locator('.dc-q-fix').first().waitFor();
      assert.deepEqual(await end.locator('[data-q-needs] .dc-q-fix').allTextContents(), ['Needs an active bet', "Needs What we won't do"]);
      // A part still drafted is named too, and opens its question, its drafted text offered.
      await end.locator('[data-q-drafted] .dc-q-fix', {hasText: TITLE.know}).click();
      await d.locator('.dc-qs[data-q="know"] .dc-choice', {hasText: KNOW}).waitFor();
      await d.locator('[data-dc="q-skip"]').click();
      await end.locator('[data-q-drafted]').waitFor();
      assert.equal(await end.locator('[data-dc="q-publish"]').count(), 0, 'no Publish while it would be refused');
      assert.equal(await end.locator('.dc-q-said').count(), 0, 'nothing saved, nothing said');
      // What is missing is its question again.
      await end.locator('.dc-q-fix', {hasText: 'bet'}).click();
      await d.locator('.dc-qs[data-q="bet"]').waitFor();
      await d.locator('[data-dc="q-skip"]').click();
      // Keep going: the other sections; then the ask that waits.
      await end.locator('[data-dc="q-more"]').click();
      for (const key of ['proof', 'now', 'know', 'how']) {
        await d.locator(`.dc-qs[data-q="${key}"]`).waitFor();
        await d.locator('[data-dc="q-skip"]').click();
      }
      await end.locator('[data-dc="q-more"]').click();
      await d.locator('.dc-q', {hasText: QUESTION}).waitFor();
      assert.deepEqual(writes(), [], 'Skip writes nothing');
      const kept = await page.evaluate(() => JSON.parse(localStorage.getItem('hub.flow.strategy-skipped') || '[]') as string[]);
      assert.deepEqual(kept.sort(), ['bet', 'how', 'know', 'not', 'now', 'proof', 'sell', 'serve']);
      await page.keyboard.press('Escape');
      await d.waitFor({state: 'hidden'});
      // Flow from the sidebar with nothing waiting: every question was skipped here, so All done.
      needs = [];
      await page.evaluate(() => v2Refresh());
      await page.evaluate(() => decideFlow(document.body));
      await d.locator('.dc-done h2', {hasText: 'All done'}).waitFor();
      await page.keyboard.press('Escape');
      // Not in a pipeline's or a stage's walk.
      needs = [ask()];
      await page.evaluate(() => localStorage.removeItem('hub.flow.strategy-skipped'));
      await page.evaluate(() => v2Refresh());
      reads.length = 0;
      await page.evaluate(() => decideStageOpen('spec'));
      await d.locator('.dc-q', {hasText: QUESTION}).waitFor();
      await d.locator('[data-dc-choice="Yes"]').click();
      await d.locator('.dc-done').waitFor();
      assert.deepEqual(reads, [], 'a stage\'s walk never reads the questions');
      await page.keyboard.press('Escape');
      // The Strategy page's own way in: the mark beside Publish.
      await page.locator('#strat-actions [data-strat-flow]').click();
      await d.locator('.dc-qs[data-q="serve"]').waitFor();
      await page.keyboard.press('Escape');
      await page.context().close();
    }

    // ---- Anyone but the owner: none of it, and nothing read.
    {
      reset({}, []);
      needs = [];
      role = 'member';
      const page = await start(true, '#/updates');
      const d = page.locator('#need-card');
      await page.locator('#mobile-flow').tap();
      await d.locator('.dc-done h2', {hasText: 'All done'}).waitFor();
      assert.equal(await d.locator('.dc-qs').count(), 0);
      await page.keyboard.press('Escape');
      await page.goto('http://tico-ui.test/#/decide?setup=strategy');
      await page.locator('#strat-body').waitFor();
      await page.waitForTimeout(300);
      assert.equal(await d.isVisible(), false, 'the setup link opens nothing for them');
      assert.equal(await page.locator('[data-strat-flow]').count(), 0, 'no mark on their Strategy page');
      assert.deepEqual(reads, [], 'the questions are never read for them');
      await page.context().close();
    }

    assert.deepEqual(writes(), []);
    // A line its section already says is not added again (case and punctuation aside); a new one is.
    {
      const page = await start(false, '#/updates');
      assert.deepEqual(await page.evaluate((know: string) => [decideQMerge(`${know}\n\nLandlords hate portals.`, 'landlords hate portals'),
        decideQMerge(know, 'Landlords hate portals.')], KNOW), [`${KNOW}\n\nLandlords hate portals.`, `${KNOW}\n\nLandlords hate portals.`]);
      await page.context().close();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: the owner\'s Strategy questions come after their asks and first from the Strategy page; saves carry the revision (a 409 sent again once); one Send, one split, cut words shown; ✕ takes out only Flow\'s own line; Publish sends the revision on screen and comes back for new answers; Keep going reads again; no one else sees them.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

declare const S: {me?: unknown};
declare const DEC_Q: {busy: boolean};
declare function v2Refresh(): Promise<void>;
declare function decideQMerge(body: string, line: string): string;
declare function decideFlow(from: HTMLElement): void;
declare function decideStageOpen(stage: string): Promise<boolean>;
