import type {Page} from 'playwright';
// Offline browser check of the owner's Strategy questions in Flow (ui/app/decide-setup.ts, docs/strategy.md#flow),
// against a fake of the routes they use (GET /v2/flow/questions, POST /v2/flow/ramble, the Strategy draft routes):
// - after the owner's asks the questions come, as a card of their own: the suggested answer first, a box, Skip;
// - tapping the suggestion saves it to the draft with the draft's revision (a drafted section loses its marker; a
//   proposed bet is made active); a 409 is read again and sent again, once;
// - a long answer is split into lines under their sections' names; ✕ drops one, one Save sends the rest;
// - the end screen lists what was saved, by section, what Publish changes, and Publish; ✕ takes a line out again (a
//   draft save) until Publish, and there is none after it;
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
const LINES = [
  {section: 'not', text: 'We will not take enterprise property managers, even when they ask.'},
  {section: 'proof', text: 'Forty landlords already pay us.'},
  {section: 'how', text: 'We should hire a support person next quarter.'},
];
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
    let needs: Json[] = [], conflicts = 0, role = 'owner';
    const errors: string[] = [], sent: Sent[] = [], reads: string[] = [];
    const reset = (s: Record<string, string>, b: Bet[]) => { sections = {...s}; bets = b.map(x => ({...x})); revision = 1; current = null; n = 0; sent.length = 0; reads.length = 0; };
    const open = (k: string) => !(sections[k] || '').trim() || (sections[k] || '').startsWith('_Unconfirmed:');
    const questions = () => {
      if (role !== 'owner') return {questions: [], more: [], revision: null, titles: {}};
      const q = (key: Key): Json[] => {
        if (key === 'bet') {
          if (bets.some(b => b.status === 'active')) return [];
          const top = bets.find(b => b.status === 'proposed');
          return [{key, title: TITLE[key], question: ASK[key], suggest: top?.statement || '', bet_id: top?.id || null}];
        }
        if (!open(key)) return [];
        return [{key, title: TITLE[key], question: ASK[key], suggest: (sections[key] || '').replace(MARK, ''), bet_id: null}];
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
      await page.route('**/*', route => {
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
        if (p === '/api/v2/flow/ramble') return json({lines: LINES, split: true, why: null});
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
          n++; current = {...draft(), n, bets: bets.filter(b => b.status === 'active')}; revision++;
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

    // ---- A phone: an ask first, then the questions; each kind of save; the end screen and Publish.
    {
      reset({serve: MARK + DRAFTED}, [{id: 'b1', statement: PROPOSED, status: 'proposed', rank: 1}]);
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

      // The bet: the proposed one offered, tapped, made active.
      assert.equal(await d.locator('.dc-qs[data-q="bet"] .dc-choice').textContent(), PROPOSED);
      await d.locator('.dc-qs[data-q="bet"] .dc-choice').tap();
      await d.locator('.dc-qs[data-q="not"]').waitFor();
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/bets/b1', body: {status: 'active', revision: 4}});

      // A long answer: its lines under their sections' names; ✕ drops one, then one Save.
      await d.locator('#dc-q-say').fill(RAMBLE);
      await d.locator('[data-dc="q-send"]').tap();
      await d.locator('[data-q-strike]').waitFor();
      const ramble = sent.find(s => s.path === '/api/v2/flow/ramble')!;
      assert.deepEqual(ramble.body, {text: RAMBLE, section: 'not'}, 'only the words and the section asked');
      assert.deepEqual(await d.locator('.dc-ql .tx span').allTextContents(), [TITLE.not, TITLE.proof, TITLE.how]);
      assert.deepEqual(await d.locator('.dc-ql .tx b').allTextContents(), LINES.map(l => l.text));
      await d.locator('.dc-ql').nth(2).locator('[data-dc="q-drop"]').tap();
      assert.equal(await d.locator('.dc-ql.off').count(), 1);
      await shot(page, 'strike-screen');
      const before = writes().length;
      await d.locator('[data-dc="q-save"]').tap();

      // The end: what was saved, by section; what Publish changes; Publish.
      const end = d.locator('[data-q-end]');
      await end.locator('[data-dc="q-publish"]').waitFor();
      assert.deepEqual(writes().slice(before).map(s => s.body), [{revision: 5, sections: {not: LINES[0]!.text, proof: LINES[1]!.text}}], 'one save');
      assert.equal(sections['how'], undefined, 'the dropped line is not saved');
      assert.equal(await end.locator('.dc-q-said').textContent(), 'Saved to your draft. Bots use it after you publish.');
      assert.deepEqual(await end.locator('.dc-qg h4').allTextContents(), [TITLE.serve, TITLE.sell, TITLE.bet, TITLE.not, TITLE.proof]);
      assert.deepEqual(await end.locator('.dc-qg li>span').allTextContents(), [DRAFTED, SELL, PROPOSED, LINES[0]!.text, LINES[1]!.text]);
      assert.equal(await end.locator('[aria-label="Publish changes"]').textContent(), `${TITLE.serve} · ${TITLE.sell} · ${TITLE.not} · ${TITLE.proof} · Bets`);
      await shot(page, 'end-screen');

      // ✕ before Publish: the line taken out of the draft again, a revision-checked save.
      await end.locator('.dc-qg li', {hasText: LINES[1]!.text}).locator('[data-dc="q-unsave"]').tap();
      await end.locator('.dc-qg li', {hasText: LINES[1]!.text}).waitFor({state: 'detached'});
      assert.deepEqual(writes().at(-1)!.body, {revision: 6, sections: {proof: ''}});
      assert.equal(await end.locator('[aria-label="Publish changes"]').textContent(), `${TITLE.serve} · ${TITLE.sell} · ${TITLE.not} · Bets`);

      // Publish: the whole draft, no note; then no ✕ and no Undo.
      await end.locator('[data-dc="q-publish"]').tap();
      await end.locator('.dc-q-said', {hasText: 'Published v1'}).waitFor();
      assert.deepEqual(writes().at(-1), {method: 'POST', path: '/api/v2/strategy/publish', body: {note: '', revision: 7}});
      assert.equal(await end.locator('[data-dc="q-unsave"]').count(), 0);
      assert.equal(await d.locator('[data-dc="undo"][aria-label*="ublish"]').count(), 0, 'no Undo for Publish');
      await end.locator('[data-dc="q-done"]').tap();
      await d.waitFor({state: 'hidden'});
      await page.context().close();
    }

    // ---- From the Strategy page: the questions first, an ask after them; Publish refused says what is missing; Skip kept.
    {
      reset({}, []);
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
      assert.deepEqual(await end.locator('.dc-q-fix').allTextContents(), ['Needs an active bet', "Needs What we won't do"]);
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
    assert.deepEqual(errors, []);
    console.log('PASS: the owner\'s Strategy questions come after their asks and first from the Strategy page; saves carry the revision (a 409 sent again once); a long answer\'s lines are struck and saved once; the end screen publishes or says what is missing; no one else sees them.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

declare const S: {me?: unknown};
declare function v2Refresh(): Promise<void>;
declare function decideFlow(from: HTMLElement): void;
declare function decideStageOpen(stage: string): Promise<boolean>;
