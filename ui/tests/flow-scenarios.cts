import type {Locator, Page} from 'playwright';
// Flow's run states at phone width (390x844, keyboard closed), against a fake of the routes the Strategy questions use
// (docs/flow-scenarios.md; the cards themselves are ui/tests/flow-questions.cts). From the Tasks owner's rules (round 2):
// an ask has someone waiting and counts, a question has nobody waiting and never counts; asks always come first; Flow
// opened from an area's page puts that area's questions first; a stage's walk never shows a question.
// - No asks, setup open: Flow opens on the first question, not All done, and the Flow tab stays grey. The four basics
//   are answered (one long answer split: the strike screen's rules), then Publish. An answer the server could not
//   split comes back to the strike screen as one whole line under the part asked.
// - Asks waiting: every ask first, no question read while one is on screen; closed early, the questions wait quietly
//   and the tab stays lit for the asks alone; then the questions, each suggestion tapped (the bet offers the top three
//   proposed, by rank), then Publish, with the part still drafted named above it.
// - A question skipped in this browser is left out of All mine; from the Strategy page it is asked again, Strategy
//   first though an ask waits; the end screen blocked (no Publish, each blocker opens its question); then Publish;
//   then the ask.
// - Opened from a stage: only that stage's asks, never a question, then All done.
// - Everything done: All done, nothing about setup.
// On every card, the UI and design owners' phone checks: nothing scrolls sideways, the question, the suggestion, the
// box and the buttons show on the first screen, the question takes at most two lines, every control is at least 44x44
// and none overlaps another, and the forward button (Send, Save, Publish) shows without scrolling and is the furthest
// right in its one row of actions (Publish beside Keep going and Done). Not here: a question skipped more than once is
// asked again like any other (decide-setup.ts keeps no count of skips, so the Tasks owner's sixth state has no rule).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Json = Record<string, unknown>;
interface Bet {id: string; statement: string; why?: string; status: string; rank: number | null}

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const MARK = '_Unconfirmed: drafted from acme.example._\n\n';
// The fake server's questions: the words are this script's own, the properties are the product's.
const ASK = {
  serve: 'Which customers do you serve first, the ones who need this most?',
  sell: 'What do you sell, and what does a customer pay for it?',
  bet: "What is the one bet you're making now that could turn out wrong?",
  not: "What won't you do, even when a good customer asks for it?",
  proof: 'How do you know customers want it: who uses it, and how often?', now: 'What changed recently that makes this possible, or needed, now?',
  know: "What do you know about this market that your competitors don't?", how: 'Where do your next 100 customers come from?',
} as const;
const TITLE = {serve: 'Who we serve', sell: 'What we sell', bet: 'Bet', not: "What we won't do", proof: 'Proof', now: 'Why now',
  know: 'What we know', how: 'How we win'} as const;
type Key = keyof typeof ASK;
const BASICS: Key[] = ['serve', 'sell', 'bet', 'not'], MORE: Key[] = ['proof', 'now', 'know', 'how'];
const TYPED = {
  serve: 'Landlords with one to twenty units and a day job.',
  sell: 'We sell a bot that answers tenant messages at any hour and chases late rent for the landlord. '
    + 'Forty landlords pay us twenty-nine dollars a unit a month, and most of them have stayed past six months, '
    + 'which is the best proof we have so far.',
  bet: 'Landlords will let a bot answer tenants if they can read every message',
  not: 'No commercial property and no custom software.',
};
const SPLIT = [
  {section: 'sell', text: 'A bot that answers tenant messages at any hour and chases late rent for the landlord.'},
  {section: 'proof', text: 'Forty landlords pay us twenty-nine dollars a unit a month.'},
];
const DRAFTED = {serve: 'Landlords with one to twenty units.', sell: 'Bots that answer tenants, $29 a unit.', not: 'No enterprise deals.'};
const CONFIRMED: Record<string, string> = {...DRAFTED, proof: 'Forty landlords pay.', now: 'Tenants text now.', know: 'Landlords hate apps.', how: 'Meetups.'};
const PROPOSED = 'Small landlords will trust a bot with tenant messages';
// Four proposed bets, by rank: the bet question offers the top three.
const PROPOSALS = [PROPOSED, 'Landlords will pay per unit, not per seat', 'Landlords switch for a bot that files repair tickets',
  'Landlords will pay yearly for a bot that never misses a message'];
const LONG = 'Honestly I am not sure who we serve yet, I keep changing my mind about it every week, and my cofounder and I '
  + 'argue about it at every lunch, so I would rather think about it some more and come back to this question later on.';
const MAX = {bet: 480, section: 4000};
const QUESTION = (i: number) => `Ship the Acme pricing page ${['on Monday', 'with annual plans', 'in Spanish too'][i - 1]}?`;
const brief = (id: string) => ({id, title: `Task ${id}`, number: Number(id.slice(1)), pipeline_id: 'product', stage_id: 'spec', status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const ask = (i: number) => ({kind: 'ask', id: `r${i}`, task_id: `t${i}`, requirement_id: `r${i}`, line: QUESTION(i), since: `2026-11-09T1${i}:00:00Z`, task: brief(`t${i}`),
  ask: {requirement_id: `r${i}`, kind: 'answer', line: QUESTION(i), who: 'human:ana', asked_by: 'bot:pm', choices: ['Yes', 'No'], may: ['answer'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'spec', name: 'Spec', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    // The draft as the fake server holds it.
    let sections: Record<string, string> = {}, bets: Bet[] = [], revision = 1, current: Json | null = null, n = 0, needs: Json[] = [];
    let splits = true;  // false: the ramble route cannot split (no model), and gives the whole answer back
    const errors: string[] = [], reads: string[] = [];
    const reset = (s: Record<string, string>, b: Bet[], asks: Json[] = []) => {
      sections = {...s}; bets = b.map(x => ({...x})); revision = 1; current = null; n = 0; reads.length = 0; needs = asks; splits = true;
    };
    const open = (k: string) => !(sections[k] || '').trim() || (sections[k] || '').startsWith('_Unconfirmed:');
    const item = (key: Key): Json[] => {
      if (key === 'bet') {
        if (bets.some(b => b.status === 'active')) return [];
        const top = bets.filter(b => b.status === 'proposed').sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).slice(0, 3);
        return [{key, title: TITLE[key], question: ASK[key], choices: top.map(b => ({text: b.statement, bet_id: b.id})), max: MAX.bet}];
      }
      const drafted = (sections[key] || '').replace(MARK, '');
      return open(key) ? [{key, title: TITLE[key], question: ASK[key], choices: drafted ? [{text: drafted, bet_id: null}] : [], max: MAX.section}] : [];
    };
    const questions = () => ({questions: BASICS.flatMap(item), more: MORE.flatMap(item), revision, titles: TITLE});
    const draft = () => ({revision, guide: 'default', summary: '', vision: '', sections: {...sections}, bets: bets.map(b => ({...b}))});
    const strategy = () => ({may_edit: true, draft: draft(), current, bets, label: 'Bets', label_one: 'Bet', goals_by_bet: {}, health_by_bet: {}});

    const start = async (hash: string, skipped: string[] = []): Promise<Page> => {
      const context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true,
        serviceWorkers: 'block', reducedMotion: 'reduce'});
      if (skipped.length) await context.addInitScript(keys => localStorage.setItem('hub.flow.strategy-skipped', JSON.stringify(keys)), skipped);
      const page = await context.newPage();
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', route => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname, method = r.method();
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (isUiPath(p)) return serveUi(route, p);
        if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true, config});
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
        const m = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
        if (m) { needs = needs.filter(x => x['id'] !== m[2]); return json({task: brief(m[1]!), answer_id: 'a-' + m[2], requirement_id: m[2]}); }
        if (p === '/api/v2/flow/ramble') return json(splits ? {lines: SPLIT.map(l => ({...l})), split: true, why: null}
          : {lines: [{section: body['section'], text: String(body['text']).trim()}], split: false, why: 'no_model'});
        if (p.startsWith('/api/v2/strategy') && body['revision'] !== revision)
          return json({error: {code: 'conflict', message: 'The draft changed.'}, revision}, 409);
        if (p === '/api/v2/strategy/draft' && method === 'PUT') {
          Object.assign(sections, body['sections'] as Record<string, string>); revision++;
          return json({draft: draft()});
        }
        if (p === '/api/v2/strategy/bets') {
          const bet: Bet = {id: `b${bets.length + 1}`, statement: String(body['statement']), status: String(body['status'] || 'proposed'), rank: null};
          bets.push(bet); revision++;
          return json({bet, revision});
        }
        const one = p.match(/^\/api\/v2\/strategy\/bets\/(b\d+)$/);
        if (one) { const bet = bets.find(b => b.id === one[1])!; bet.status = String(body['status']); revision++; return json({bet, revision}); }
        if (p === '/api/v2/strategy/publish') {
          // Every bet not retired, as backend/strategy.py publish snapshots them.
          n++; current = {...draft(), n, bets: bets.filter(b => b.status !== 'retired').map(b => ({...b}))}; revision++;
          return json({...current});
        }
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/' + hash);
      await page.waitForFunction(() => typeof S !== 'undefined' && !!S.me);
      return page;
    };

    type Box = {x: number; y: number; width: number; height: number};
    const box = async (l: Locator): Promise<Box> => { const b = await l.boundingBox(); assert.ok(b, 'on screen'); return b!; };
    /** The phone checks on the screen showing now. `forward` is its main button, `row` the other buttons beside it. */
    const phone = async (page: Page, where: string, forward: Locator) => {
      await forward.waitFor();
      const height = await page.evaluate(() => innerHeight);
      const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
      assert.ok(sideways <= 1, `${where}: nothing scrolls sideways (${sideways}px)`);
      assert.equal(await page.evaluate(() => scrollY), 0, `${where}: the page did not scroll`);
      const shown = async (l: Locator, what: string) => { const b = await box(l); assert.ok(b.y >= 0 && b.y + b.height <= height, `${where}: ${what} shows on the first screen (${JSON.stringify(b)})`); };
      await shown(forward, 'the forward button');
      // Every control on the card: at least 44x44, inside the screen, none over another.
      const card = page.locator('#need-card .dc-qs').first();
      const controls = card.locator('button:visible, textarea:visible');
      const boxes: Box[] = [];
      for (let i = 0; i < await controls.count(); i++) {
        const c = controls.nth(i), b = await box(c), name = (await c.getAttribute('aria-label')) || (await c.textContent())?.trim() || await c.evaluate(e => e.tagName);
        assert.ok(b.width >= 44 - 0.5 && b.height >= 44 - 0.5, `${where}: "${name}" is ${Math.round(b.width)}x${Math.round(b.height)}, under 44x44`);
        await shown(c, `"${name}"`);
        for (const o of boxes) {
          const apart = b.x >= o.x + o.width || o.x >= b.x + b.width || b.y >= o.y + o.height || o.y >= b.y + b.height;
          assert.ok(apart, `${where}: "${name}" overlaps another control`);
        }
        boxes.push(b);
      }
      // The question: at most two lines.
      const q = card.locator('.dc-q');
      if (await q.count()) {
        const [h, line] = await q.evaluate(e => { const s = getComputedStyle(e); return [e.getBoundingClientRect().height, parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2]; });
        assert.ok(h <= 2 * line! + 1, `${where}: the question takes ${Math.round(h! / line!)} lines, over two`);
        assert.match((await q.textContent()) || '', /^[^?]+\?$/, `${where}: one question, ending in ?`);
      }
    };
    /** The main button and `row` (the others) share one row, the main button furthest right. */
    const farRight = async (forward: Locator, row: Locator) => {
      const f = await box(forward);
      assert.ok(await row.count() > 0, 'something beside the main button');
      for (let i = 0; i < await row.count(); i++) {
        const b = await box(row.nth(i));
        assert.ok(Math.abs((b.y + b.height / 2) - (f.y + f.height / 2)) < f.height / 2, 'one row');
        assert.ok(b.x + b.width <= f.x + 1, 'the main button is furthest right');
      }
    };
    const card = (d: Locator, key: Key) => d.locator(`.dc-qs[data-q="${key}"]`);
    const send = (d: Locator) => d.locator('[data-dc="q-send"]');
    const ask1 = async (page: Page, d: Locator, key: Key) => {
      await card(d, key).waitFor();
      assert.equal(await card(d, key).locator('.dc-q').textContent(), ASK[key]);
      await phone(page, `${key} card`, send(d));
      await farRight(send(d), card(d, key).locator('.dc-acts .dc-skip'));
    };
    const type = async (page: Page, d: Locator, key: Key, text: string, split = false) => {
      await ask1(page, d, key);
      await d.locator('#dc-q-say').fill(text);
      await send(d).tap();
      if (!split) await card(d, key).waitFor({state: 'detached'});
    };
    const skip = async (d: Locator, key: Key) => {
      await card(d, key).waitFor();
      await card(d, key).locator('[data-dc="q-skip"]').tap();
      await card(d, key).waitFor({state: 'detached'});
    };
    const end = (d: Locator) => d.locator('[data-q-end]');
    const publishes = async (page: Page, d: Locator, hash: string, version = 1) => {
      const e = end(d), pub = e.locator('[data-dc="q-publish"]');
      await phone(page, 'end screen', pub);
      await farRight(pub, e.locator('[data-dc="q-more"], [data-dc="q-done"]'));
      await pub.tap();
      await e.locator('.dc-q-said', {hasText: `Published v${version}`}).waitFor();
      assert.equal(n, version, 'published');
      assert.equal(await page.evaluate(() => location.hash), hash, 'Flow never sent the person to another page');
    };
    const lit = (page: Page) => page.locator('#mobile-flow').evaluate(e => e.hasAttribute('data-waiting'));
    const askOn = async (d: Locator, i: number) => {
      await d.locator('.dc-q', {hasText: QUESTION(i)}).waitFor();
      assert.equal(await d.locator('.dc-qs[data-q]').count(), 0, 'no question beside an ask');
    };

    // ---- No asks, setup open: the first question, not All done, the tab grey; the four basics; Publish.
    {
      reset({}, []);
      const page = await start('#/updates'), d = page.locator('#need-card');
      assert.equal(await lit(page), false, 'questions never light the Flow tab');
      await page.locator('#mobile-flow').tap();
      await card(d, 'serve').waitFor();
      assert.equal(await d.locator('.dc-done').count(), 0, 'not All done');
      await type(page, d, 'serve', TYPED.serve);
      await type(page, d, 'sell', TYPED.sell, true);
      // The strike screen: each line under its part's name; ✕ strikes it and turns into ↩; all struck, no Save.
      const strike = d.locator('[data-q-strike]'), save = d.locator('[data-dc="q-save"]'), rows = strike.locator('.dc-ql');
      await strike.waitFor();
      assert.deepEqual(await rows.locator('.tx b').allTextContents(), SPLIT.map(l => l.text));
      assert.deepEqual(await rows.locator('.tx span').allTextContents(), SPLIT.map(l => TITLE[l.section as Key]));
      await phone(page, 'the lines', save);
      await farRight(save, strike.locator('.dc-acts .dc-skip'));
      for (let i = 0; i < SPLIT.length; i++) {
        await rows.nth(i).locator('[data-dc="q-drop"]').tap();
        await rows.nth(i).locator('[data-dc="q-drop"][aria-label="Keep"]').waitFor();
        assert.ok(await rows.nth(i).evaluate(e => e.classList.contains('off')), 'a struck line shows struck');
      }
      assert.equal(await save.isDisabled(), true, 'with every line struck, Save is disabled');
      for (let i = 0; i < SPLIT.length; i++) await rows.nth(i).locator('[data-dc="q-drop"]').tap();
      await rows.nth(SPLIT.length - 1).locator('[data-dc="q-drop"][aria-label="Drop"]').waitFor();
      assert.equal(await save.isDisabled(), false, '↩ keeps a line again');
      await save.tap();
      await card(d, 'sell').waitFor({state: 'detached'});
      await type(page, d, 'bet', TYPED.bet);
      await type(page, d, 'not', TYPED.not);
      assert.equal(sections['serve'], TYPED.serve);
      assert.equal(sections['sell'], SPLIT[0]!.text);
      assert.equal(sections['proof'], SPLIT[1]!.text);
      assert.equal(sections['not'], TYPED.not);
      assert.deepEqual(bets.map(b => [b.statement, b.status]), [[TYPED.bet, 'active']]);
      await publishes(page, d, '#/updates');
      assert.equal(await lit(page), false, 'still grey');
      await end(d).locator('[data-dc="q-done"]').tap();
      await d.waitFor({state: 'hidden'});
      await page.context().close();
    }

    // ---- Three asks waiting: every ask first; closed early, the questions wait; then the questions; Publish.
    {
      const drafted = Object.fromEntries(Object.entries({...DRAFTED, proof: 'Forty landlords pay.'}).map(([k, v]) => [k, MARK + v]));
      // Ranked out of order as stored: the choices follow the rank.
      const ranks = [2, 1, 4, 3], proposed = PROPOSALS.map((statement, i) => ({id: `b${i + 1}`, statement, status: 'proposed', rank: i + 1}));
      reset(drafted, ranks.map(r => proposed[r - 1]!), [ask(1), ask(2), ask(3)]);
      const page = await start('#/updates'), d = page.locator('#need-card');
      await page.locator('#mobile-flow[data-waiting]').waitFor({state: 'attached'});
      await page.locator('#mobile-flow').tap();
      await askOn(d, 1);
      assert.deepEqual(reads, [], 'no question is read while an ask is on screen');
      await d.locator('[data-dc-choice="Yes"]').tap();
      await askOn(d, 2);
      // Closed early: the questions wait quietly, the tab lit for the asks still waiting and nothing else.
      await page.keyboard.press('Escape');
      await d.waitFor({state: 'hidden'});
      assert.equal(await lit(page), true, 'the tab stays lit for the asks');
      assert.deepEqual(reads, [], 'no question read while asks wait');
      await page.locator('#mobile-flow').tap();
      for (const i of [2, 3]) { await askOn(d, i); await d.locator('[data-dc-choice="Yes"]').tap(); }
      for (const key of BASICS) {
        const c = card(d, key), take = c.locator('.dc-choice.dc-rec');
        await ask1(page, d, key);
        await phone(page, `${key} suggestion`, take);
        // The suggestion sits under the question, above the box.
        const [qb, tb, bb] = [await box(c.locator('.dc-q')), await box(take), await box(d.locator('#dc-q-say'))];
        assert.ok(qb.y + qb.height <= tb.y + 1 && tb.y + tb.height <= bb.y + 1, `${key}: question, then suggestion, then box`);
        assert.equal(await take.textContent(), key === 'bet' ? PROPOSED : DRAFTED[key as keyof typeof DRAFTED]);
        // Drafted text is one choice; the bet offers the top three proposed, by rank, the first marked.
        assert.deepEqual(await c.locator('.dc-choice').allTextContents(), key === 'bet' ? PROPOSALS.slice(0, 3) : [DRAFTED[key as keyof typeof DRAFTED]]);
        assert.equal(await c.locator('.dc-choice.dc-rec').count(), 1, 'one choice marked');
        await take.tap();
        await c.waitFor({state: 'detached'});
      }
      assert.deepEqual(Object.keys(DRAFTED).map(k => sections[k]), Object.values(DRAFTED), 'each drafted part kept, its marker gone');
      assert.deepEqual(bets.filter(b => b.status === 'active').map(b => b.statement), [PROPOSED], 'the top bet made active, the rest left');
      assert.equal(await lit(page), false, 'grey once the asks are answered, though the questions were open');
      // Proof is still Unconfirmed: Publish would send it out as drafted.
      await end(d).waitFor();
      const named = end(d).locator('[data-q-drafted] [data-dc="q-fix"], [data-q-drafted] .dc-q-fix');
      assert.deepEqual(await named.allTextContents(), ['Unconfirmed: Proof'], 'the part still drafted is named above Publish');
      assert.match((await end(d).textContent()) || '', /Unconfirmed[^]*Proof/);
      await publishes(page, d, '#/updates');
      await page.context().close();
    }

    // ---- An answer the server could not split (no model): the strike screen, one whole line under the part asked.
    {
      reset({}, []);
      const page = await start('#/decide?setup=strategy'), d = page.locator('#need-card');
      splits = false;
      await type(page, d, 'serve', LONG, true);
      const strike = d.locator('[data-q-strike]'), save = d.locator('[data-dc="q-save"]'), rows = strike.locator('.dc-ql');
      await strike.waitFor();
      assert.deepEqual(await rows.locator('.tx b').allTextContents(), [LONG], 'the whole answer, one line, uncut');
      assert.deepEqual(await rows.locator('.tx span').allTextContents(), [TITLE.serve]);
      assert.equal(await rows.locator('.dc-q-left').count(), 0, 'nothing struck as cut');
      await phone(page, 'the whole line', save);
      await farRight(save, strike.locator('.dc-acts .dc-skip'));
      await save.tap();
      await card(d, 'sell').waitFor();
      assert.equal(sections['serve'], LONG);
      await page.context().close();
    }

    // ---- Skipped here: left out of All mine; from the Strategy page asked again, Strategy before the waiting ask.
    {
      reset({}, []);
      const page = await start('#/updates', ['serve']), d = page.locator('#need-card');
      await page.locator('#mobile-flow').tap();
      await card(d, 'sell').waitFor();
      assert.equal(await card(d, 'serve').count(), 0, 'the skipped question is not asked in All mine');
      await page.context().close();
    }
    {
      reset({}, [], [ask(1)]);
      const page = await start('#/decide?setup=strategy', ['serve']), d = page.locator('#need-card');
      await card(d, 'serve').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/strategy', 'Flow over the Strategy page');
      assert.equal(await d.locator('.dc-q', {hasText: QUESTION(1)}).count(), 0, 'the area first, the ask after');
      await type(page, d, 'serve', TYPED.serve);
      for (const key of ['sell', 'bet', 'not'] as Key[]) await skip(d, key);
      // Blocked: no Publish; the saved line and "Saved to your draft" stay; each blocker opens its question.
      const e = end(d), fixes = e.locator('[data-dc="q-fix"]');
      await fixes.first().waitFor();
      assert.equal(await e.locator('[data-dc="q-publish"]').count(), 0, 'no Publish while something stops it');
      assert.deepEqual(await fixes.evaluateAll(xs => xs.map(x => (x as HTMLElement).dataset['qKey'])), ['bet', 'not']);
      await e.locator('.dc-q-said', {hasText: 'Saved to your draft'}).waitFor();
      await e.locator('li', {hasText: TYPED.serve}).waitFor();
      await phone(page, 'end screen, blocked', fixes.first());
      await fixes.first().tap();
      await type(page, d, 'bet', TYPED.bet);
      await end(d).locator('[data-dc="q-fix"][data-q-key="not"]').tap();
      await type(page, d, 'not', TYPED.not);
      await publishes(page, d, '#/strategy');
      // Keep going: the other parts, skipped here, then the ask that waited.
      await end(d).locator('[data-dc="q-more"]').tap();
      for (const key of MORE) if (key !== 'proof' || open('proof')) await skip(d, key);
      await end(d).locator('[data-dc="q-more"]').tap();
      await askOn(d, 1);
      assert.equal(sections['serve'], TYPED.serve);
      await page.context().close();
    }

    // ---- Opened from a stage: only its asks, never a question, then All done.
    {
      reset({}, [], [ask(1)]);
      const page = await start('#/decide?stage=spec'), d = page.locator('#need-card');
      await askOn(d, 1);
      await d.locator('[data-dc-choice="Yes"]').tap();
      await d.locator('.dc-done').waitFor();
      assert.equal(await d.locator('.dc-qs').count(), 0, 'no setup question in a stage walk');
      assert.deepEqual(reads, [], 'a stage walk never reads the questions');
      await page.context().close();
    }

    // ---- Everything done: All done, nothing about setup.
    {
      reset(CONFIRMED, [{id: 'b1', statement: PROPOSED, status: 'active', rank: 1}]);
      const page = await start('#/updates'), d = page.locator('#need-card');
      assert.equal(await lit(page), false);
      await page.locator('#mobile-flow').tap();
      await d.locator('.dc-done').waitFor();
      assert.equal(await d.locator('.dc-qs').count(), 0, 'nothing about setup');
      await page.context().close();
    }

    assert.deepEqual(errors, []);
    console.log("PASS: Flow's run states at phone width: asks first, questions never counted, the area first from its page, none in a stage walk, All done when nothing is open, an unsplit answer one whole line; the cards fit the phone, the main button far right, Publish in the row, drafted parts named.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

declare const S: {me?: unknown};
