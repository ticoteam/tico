import type {Page, Route} from 'playwright';
// Offline browser check of a stage's rules (ui/app/stage-rules.ts; Stage rules spec, acceptance 8): a stage header on a
// pipeline board with rules of its own ends in a quiet rules mark at its far right, a gavel with no word and no count,
// named for how many it has ("1 rule of its own"); a stage with none of its own (shared rules alone) has no mark at all
// (style guide, Pipeline stage). The editor is the stage's phases, each a heading with a small mark: On entering (rules
// first, then actions; + Rule and + Action; with no owner set it says the manager picks one), While here (Late after,
// the timers, + Rule), To leave (requirement rows) and More rules, one shut line that opens the rules that act on
// neither entering nor a timer (a bot's snooze, an owner change; + Rule) and, after them, the built-in ones; it stays
// open through a redraw and a save. A rule's row is one line: the rule mark, what it checks in its own words (never
// "Decision model decides", no arrows), a bolt when the Decision model checks it, its action as a mark, then ⋯. A rule's card, opened from a
// stage, has no Name and no When: a timer's After, Then and Message; a snooze rule's "Refuse the snooze when". What is
// not ready yet is listed, off, with a small Soon. A rule saves at once, in a card over the editor, and a Goal or a
// Limit set in the stage meanwhile is kept and saved by the stage's own Save. Built in has no controls. A member who
// may not change the pipeline reads the same editor, its fields off, with no Save, + Rule, Edit or Retire. On a phone
// the mark sits at the end of the stage tabs, a 44 px target that nothing else covers, and the editor fills the
// screen's width. Rules › Suggested says what accepting each of a bot's stage rules does: a new rule, a change listing
// what differs from the rule it rewrites, or a retire with the bot's reason and a Retire button; Retire and Skip send
// their decision. The fake server answers as backend/stage_rules_api.py does.
// Run: npm run build:ui && node ui/tests/stage-rules.cts
// The editor's rules and draft, and their draws, read inside page.evaluate (ui/app/stage-rules.ts, pipeline-editor.ts).
declare const SR_ED: unknown;
declare const PE: unknown;
declare function srSectionsPaint(): void;
declare function stageRulesEditorLoad(pipelineId: string): Promise<void>;
declare function openPipelineEditor(pipelineId: string, stageId?: string, at?: string): Promise<void>;
declare function srSentence(r: never, short?: boolean): string;
declare const srPhaseOf: (event: string) => string;
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
// A member reads a Build with a Limit and an action set (rows shown, nothing to open); Ana's Build starts with neither.
const pipelineOf = (mayChange: boolean) => ({id: 'eng', name: 'Engineering', goal: '', prefix: 'E', manager_id: null, owner_id: 'human:ana',
  private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [stage('spec', 'Spec'), {...stage('build', 'Build'), ...(mayChange ? {} : {task_limit: 2, on_enter: {act: 'github.queue'}})}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: mayChange});
// Another pipeline, for On entering's "Open a linked task in…".
const product = {id: 'prod', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null,
  settings_version: 1, retired_at: null, stages: [stage('idea', 'Idea')], done: {id: 'p-done', name: 'Done'}, closed: {id: 'p-closed', name: 'Closed'}, may_change: true};
const task = {id: 't1', title: 'Saved carts', number: 1, pipeline_id: 'eng', stage_id: 'build', status: 'open', owner_id: 'human:ana',
  requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false, turn: null, version: 1,
  created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'};
const NEEDS_TIMER = 'Tell and Move back need a timer in step 1: pick N h in the stage, quiet or waiting on a person';
const BUILT_IN = [{name: 'Hand-off files with no assignee', what: 'A stage hand-off files its task with no assignee.', where: 'every stage'}];

interface Rule { id: string; name: string; pipeline_id: string; stages: string[]; event: string; timer: Record<string, unknown>;
  cond: Record<string, unknown>; jev: string; then: string; reason: string; status: string; revision: number; retired: boolean }
const rule = (id: string, name: string, pipeline: string, stages: string[], extra: Partial<Rule> = {}): Rule => ({id, name, pipeline_id: pipeline, stages,
  event: 'enters', timer: {}, cond: {}, jev: '', then: 'tell_manager', reason: '', status: 'active', revision: 1, retired: false, ...extra});

interface Suggestion extends Rule { suggests: string; retire_why?: string }
interface Served { rules: Rule[]; suggested: Suggestion[]; writes: {path: string; body: Record<string, unknown>}[] }

async function serve(page: Page, errors: string[], mayChange: boolean): Promise<Served> {
  const state: Served = {writes: [], rules: [
    rule('sr_owner', 'Owner on the team', 'eng', ['build'], {event: 'owner_changes', cond: {owner_not_on_team: 'engineering'}, then: 'block', reason: 'Not on Engineering.'}),
    rule('sr_quiet', 'Quiet bot tasks', 'eng', [], {event: 'timer', timer: {kind: 'quiet', hours: 6}}),
    rule('sr_snooze', 'Snooze only for a real wait', '', [], {event: 'bot_snoozes', jev: 'Waiting on something outside, or blocked?', then: 'block'}),
    rule('sr_spec', 'Spec has a page', 'eng', ['spec']),
  ], suggested: []};
  state.suggested = [
    {...rule('sr_sg_new', 'Ask before Move back', 'eng', ['build'], {status: 'suggested', then: 'tell_owner'}), suggests: ''},
    {...rule('sr_sg_change', 'Owner on the team', 'eng', ['build'], {status: 'suggested', event: 'owner_changes',
      cond: {owner_not_on_team: 'engineering'}, then: 'tell_manager', reason: 'Not on Engineering.'}), suggests: 'sr_owner'},
    {...rule('sr_sg_retire', 'Quiet bot tasks', 'eng', [], {status: 'suggested', event: 'timer', timer: {kind: 'quiet', hours: 6}}),
      suggests: 'sr_quiet', retire_why: 'Covered by the Late rule.'},
  ];
  const view = (r: Rule) => ({...r, scope: !r.pipeline_id ? 'company' : r.stages.length ? 'stage' : 'pipeline', pipeline_name: r.pipeline_id ? 'Engineering' : '',
    stage_names: r.stages.map(s => s === 'build' ? 'Build' : 'Spec'), cost: r.jev ? 'one Jev call per event' : 'free', can_edit: mayChange,
    // As backend/stage_rules.py not_acting: Tell or Move back on an action When never acts in step 1.
    not_acting: r.event !== 'timer' && r.then !== 'block' ? NEEDS_TIMER : ''});
  const order = ['stage', 'pipeline', 'company'];
  page.on('pageerror', (error: Error) => errors.push(error.message));
  page.on('dialog', d => { void d.accept(); });
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', role: mayChange ? 'owner' : 'member', mover: true, cloud: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipelineOf(mayChange), product]});
    if (p === '/api/v2/pipelines/eng' && r.method() === 'POST') {
      const body = (r.postDataJSON() || {}) as Record<string, unknown>;
      state.writes.push({path: p, body});
      return json({pipeline: {...pipelineOf(mayChange), ...body, settings_version: 2}});
    }
    if (p === '/api/v2/pipelines/eng') return json({pipeline: pipelineOf(mayChange)});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: [task]});
    if (p === '/api/v2/rules' && r.method() === 'GET') return json({rules: [], next_cursor: null});
    if (p === '/api/v2/stage-rules' && r.method() === 'GET' && url.searchParams.get('status') === 'suggested') {
      return json({items: state.suggested.map(x => {
        const old = state.rules.find(o => o.id === x.suggests);
        return {...view(x), retires: x.retire_why !== undefined, retire_why: x.retire_why ?? '', ...(old ? {replaces: view(old)} : {})};
      }), built_in: BUILT_IN, may_add: false, may_suggest: false});
    }
    const decided = p.match(/^\/api\/v2\/stage-rules\/([^/]+)\/(accept|skip)$/);
    if (decided && r.method() === 'POST') {
      state.writes.push({path: p, body: (r.postDataJSON() || {}) as Record<string, unknown>});
      const sg = state.suggested.find(x => x.id === decided[1]);
      state.suggested = state.suggested.filter(x => x.id !== decided[1]);
      const old = state.rules.find(o => o.id === sg?.suggests);
      if (decided[2] === 'accept' && old && sg?.retire_why !== undefined) old.retired = true;
      return json(view(sg ?? rule(decided[1]!, '', '', [])));
    }
    if (p === '/api/v2/stage-rules' && r.method() === 'GET') {
      const pid = url.searchParams.get('pipeline'), sid = url.searchParams.get('stage');
      const items = state.rules.filter(x => !x.retired && x.status === 'active' && (!pid || ['', pid].includes(x.pipeline_id))
        && (!sid || !x.stages.length || x.stages.includes(sid))).map(view).sort((a, b) => order.indexOf(a.scope) - order.indexOf(b.scope));
      return json({items, built_in: BUILT_IN, may_add: mayChange && !!pid, may_suggest: false});
    }
    if (p.startsWith('/api/v2/stage-rules') && r.method() === 'POST') {
      if (!mayChange) return json({error: {code: 'forbidden', detail: 'Only the pipeline\'s owner, its manager and Admins change its rules.'}}, 403);
      const body = (r.postDataJSON() || {}) as Record<string, unknown>;
      state.writes.push({path: p, body});
      const m = p.match(/^\/api\/v2\/stage-rules(?:\/([^/]+))?(?:\/(retire))?$/);
      if (m && !m[1]) {
        const made = rule('sr_new', String(body['name']), String(body['pipeline'] ?? ''), (body['stages'] as string[]) ?? [],
          {event: String(body['event']), timer: (body['timer'] as Record<string, unknown>) ?? {}, then: String(body['then']), reason: String(body['reason'] ?? ''), jev: String(body['jev'] ?? '')});
        state.rules.push(made);
        return json(view(made));
      }
      const found = state.rules.find(x => x.id === m?.[1]);
      if (!found) return json({error: {code: 'not_found', detail: 'No such stage rule'}}, 404);
      if (body['revision'] !== found.revision) return json({error: {code: 'conflict', detail: 'The rule changed since you read it.'}}, 409);
      if (m?.[2] === 'retire') found.retired = true;
      else Object.assign(found, {name: body['name'] ?? found.name, then: body['then'] ?? found.then, stages: body['stages'] ?? found.stages});
      found.revision += 1;
      return json(view(found));
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return state;
}

// A stage's count is the mark's tip, "1 rule of its own", "None of its own"; its name adds the stage, "Build: 1 rule of its own".
const count = async (page: Page, sid: string) => (await page.locator(`[data-sr-open="${sid}"]`).first().getAttribute('title'))?.trim();
// A stage's rules in the open stage editor (Build's unless named), in every phase; and in one phase ('enter', 'here',
// 'more', or 'always' for the built-in rules inside More rules).
const SEC = (sid = 'build') => `#pe-dialog [data-sr-section="${sid}"]`;
const PH = (phase: string, sid = 'build') => `${SEC(sid)}[data-sr-phase="${phase}"]`;
// A phase's rules, by id (a row has no title).
const phaseRules = (page: Page, phase: string, sid = 'build') => page.locator(`${PH(phase, sid)} [data-sr-rule]`).evaluateAll(els =>
  els.map(e => (e as HTMLElement).dataset['srRule']));
// The built-in rules' text.
const builtIns = (page: Page) => page.locator(`${PH('always')} [data-sr-group="builtin"] .sr-r .sr-text`).allTextContents();
// A phase heading's mark and words ("login On entering").
const phaseHeads = (page: Page) => page.locator('#pe-body .pe-h').evaluateAll(hs =>
  hs.map(h => `${h.querySelector('.nav-icon')?.textContent ?? ''} ${[...h.childNodes].find(n => n.nodeType === 3)?.textContent ?? ''}`.trim()));
const inView = (page: Page, sel: string) => page.locator(sel).evaluate(el => {
  const b = el.getBoundingClientRect(), body = document.getElementById('pe-body')!.getBoundingClientRect();
  return b.top >= body.top - 1 && b.bottom <= body.bottom + 1;
});
const HERE_H = '#pe-dialog .pe-phase[data-pe-phase="here"] > .pe-phase-h';
// On entering's one list: its Limit and action rows (by kind); + Rule and + Action, each with its pick list.
const ENTER = '#pe-dialog .pe-phase[data-pe-phase="enter"]';
const enterRows = (page: Page) => page.locator(`${ENTER} [data-pe-enter-row]`).evaluateAll(els =>
  els.map(e => `${(e as HTMLElement).dataset['peEnterRow']}: ${e.querySelector('.sr-text')?.textContent ?? ''}`));
const PHASES = ['login On entering', 'schedule While here', 'logout To leave', 'gavel More rules'];
const MORE = '#pe-dialog [data-pe-more]';
const draftStage = (page: Page) => page.evaluate(() => {
  const s = (PE as unknown as {draft: {stages: {id: string; task_limit: number | null; on_enter: unknown}[]}} | null)?.draft.stages.find(x => x.id === 'build');
  return {task_limit: s?.task_limit, on_enter: s?.on_enter};
});
const rowText = (page: Page, id: string) => page.locator(`${SEC()} [data-sr-rule="${id}"] .sr-text`).innerText();
// The stage's Goal as typed, in its field and in the editor's draft.
const goalNow = (page: Page) => page.evaluate(() => [document.querySelector<HTMLInputElement>('#pe-body [data-pe-sf="purpose"]')?.value,
  (PE as unknown as {draft: {stages: {id: string; purpose: string}[]}} | null)?.draft.stages.find(s => s.id === 'build')?.purpose]);
const waitCount = (page: Page, sid: string, n: string) => page.waitForFunction(([s, want]) =>
  (document.querySelector(`[data-sr-open="${s}"]`)?.getAttribute('title') ?? '').trim() === want, [sid, n] as const);

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const desk = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    const served = await serve(desk, errors, true);
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    // Each stage's count is its own rules (the whole pipeline's and the company's are shared, not counted).
    await waitCount(desk, 'build', '1 rule of its own');
    assert.equal(await count(desk, 'spec'), '1 rule of its own', 'Spec counts its own rule, not the pipeline\'s or the company\'s');
    // The mark: the gavel alone, no word and no count on the board; its name and tip say how many.
    const mark = desk.locator('[data-pv-col="build"] [data-sr-open]');
    assert.deepEqual(await mark.evaluate(el => ({kids: [...el.childNodes].map(n => n.nodeType === 1 ? (n as Element).className : '#text'),
      icon: el.querySelector('.nav-icon')?.textContent, title: (el as HTMLElement).title, hidden: (el as HTMLElement).hidden})),
      {kids: ['nav-icon'], icon: 'gavel', title: '1 rule of its own', hidden: false});
    // A screen reader hears whose rules: the stage, then the count (each column has one).
    assert.equal(await mark.getAttribute('aria-label'), 'Build: 1 rule of its own');
    assert.equal(await desk.locator('[data-pv-col="spec"] [data-sr-open]').getAttribute('aria-label'), 'Spec: 1 rule of its own');
    // The muted gavel, never dimmed or struck through; last in the header, at its far right edge (▶'s slot before it).
    assert.equal(await mark.evaluate(el => getComputedStyle(el.querySelector('.nav-icon')!).opacity), '1');
    assert.equal(await mark.evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no strike-through');
    const edge = (sel: string) => desk.locator(sel).evaluateAll(els => els.map(el => {
      const h = el.parentElement!, hb = h.getBoundingClientRect(), b = el.getBoundingClientRect();
      return {last: h.lastElementChild === el, gap: Math.round(hb.right - parseFloat(getComputedStyle(h).paddingRight) - b.right)};
    }));
    assert.deepEqual(await edge('.pv-col-head [data-sr-open]'), [{last: true, gap: 0}, {last: true, gap: 0}], 'at the far right of each column header');

    // Build's mark opens Build's editor at its first phase with rules (While here: the pipeline's timer), scrolled to,
    // its heading focused. No other dialog.
    await mark.click();
    await desk.locator(`${PH('here')} [data-sr-rule]`).first().waitFor();
    assert.equal((await desk.locator('#pe-title').innerText()).trim(), 'Engineering › Build');
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, HERE_H);
    assert.equal(await desk.locator('#sr-panel').isVisible().catch(() => false), false, 'no separate rules dialog');
    assert.ok(await inView(desk, HERE_H), 'scrolled to While here');
    assert.deepEqual(await phaseHeads(desk), PHASES);

    // On entering: nothing set, so no row and no field; with no owner set it says what then happens; + Rule and
    // + Action are two quiet buttons.
    assert.deepEqual(await enterRows(desk), []);
    assert.equal(await desk.locator(`${ENTER} [data-pe-no-owner]`).innerText(), 'No owner set: the manager picks one.');
    assert.equal(await desk.locator(`${ENTER} :is(input, select, fieldset)`).count(), 0, 'On entering has no fields');
    assert.deepEqual(await desk.locator(`${ENTER} [data-pe-enter-add]`).evaluateAll(els => els.map(el => [(el.textContent || '').trim(), el.getAttribute('data-pe-enter-add'),
      el.classList.contains('ghost')])), [['+ Rule', 'rule', true], ['+ Action', 'act', true]]);
    // While here: Late after (the stage's expected time); no Owner.
    assert.equal(await desk.locator('#pe-dialog .pe-phase[data-pe-phase="here"] .pe-f-time > span').first().innerText(), 'Late after');
    assert.equal(await desk.locator('#pe-dialog :is([data-pe-sf="owner"], [data-pe-seg="by_hand"])').count(), 0);
    // Each rule sits where it acts: a timer While here; an owner change and a bot's snooze under More rules, the stage's
    // own first. Every When lands in exactly one place.
    assert.deepEqual(await phaseRules(desk, 'enter'), []);
    assert.deepEqual(await phaseRules(desk, 'here'), ['sr_quiet']);
    assert.deepEqual(await phaseRules(desk, 'more'), ['sr_owner', 'sr_snooze'], 'More rules, its own first');
    assert.deepEqual(await desk.evaluate(() => ['enters', 'enters,owner_changes', 'owner_changes,enters', 'owner_changes', 'bot_snoozes', 'timer',
      'owner_changes,bot_snoozes', 'a_new_event'].map(e => srPhaseOf(e))), ['enter', 'enter', 'enter', 'more', 'more', 'here', 'more', 'more']);

    // More rules is one shut line at the end; opened, the snooze and owner-change rules with + Rule, then the built-in
    // ones under "Always on" (no second line to open).
    const moreH = desk.locator(MORE);
    assert.equal(await moreH.getAttribute('aria-expanded'), 'false');
    assert.equal(await desk.locator(`${PH('more')} [data-sr-rule]`).first().isVisible(), false, 'shut at first');
    assert.equal(await desk.locator(`${PH('always')} .sr-r`).first().isVisible(), false);
    assert.equal(await desk.locator('#pe-body .pe-stage-body > :last-child').getAttribute('data-pe-phase'), 'more', 'More rules ends the stage');
    await moreH.click();
    assert.equal(await moreH.getAttribute('aria-expanded'), 'true');
    assert.deepEqual(await builtIns(desk), ['A stage hand-off files its task with no assignee.'], 'Built in: its what');
    assert.equal(await desk.locator(`${PH('always')} .sr-always-cap`).innerText(), 'Always on');
    assert.equal(await desk.locator(`${SEC()} [data-sr-always]`).count(), 0, 'no second line to open');
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] :is([data-sr-edit], [data-sr-more])`).count(), 0, 'Built in is read-only');
    assert.ok((await desk.locator(`${PH('always')} .sr-r`).first().boundingBox())!.y > (await desk.locator(`${PH('more')} [data-sr-add]`).boundingBox())!.y, 'the built-in rules come last');
    // A phase's + Rule: While here's (a timer) and More rules' (a snooze rule); Save, in the footer, is the one filled button.
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-add]`).evaluateAll(els => els.map(el => [el.closest('[data-sr-phase]')?.getAttribute('data-sr-phase'),
      (el.textContent || '').trim(), el.classList.contains('ghost')])), [['here', '+ Rule', true], ['more', '+ Rule', true]]);
    assert.deepEqual(await desk.locator('#pe-dialog button.primary').evaluateAll(els => els.map(e => (e.textContent || '').trim())), ['Save']);

    // One dense line per rule, 36 px: the rule mark, what it checks in its own words, its marks, then ⋯.
    assert.equal(await desk.locator(`${SEC()} .sr-r :is(.sr-name, .sr-what, .sr-says)`).count(), 0, 'no title or second line');
    assert.deepEqual(await desk.locator(`${SEC()} .sr-r`).evaluateAll(els => [...new Set(els.map(e => Math.round(e.querySelector('.sr-line')!.getBoundingClientRect().height)))]), [36]);
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_quiet"] .sr-then .rule-outcome`).getAttribute('data-icon'), 'gavel', 'every rule leads with the rule mark');
    // The line: no When for the phase it sits in, no arrows, never "Decision model decides"; a Decision model rule's line is its question.
    assert.equal(await rowText(desk, 'sr_quiet'), '6 h quiet');
    assert.equal(await rowText(desk, 'sr_owner'), 'The owner changes · owner not on engineering');
    assert.equal(await rowText(desk, 'sr_snooze'), 'A bot snoozes · Waiting on something outside, or blocked?');
    assert.doesNotMatch(await desk.locator('#pe-body').innerText(), /Decision model decides|→/);
    // Its action is a mark at the right, named for what it does.
    const act = (id: string) => desk.locator(`${SEC()} [data-sr-rule="${id}"] .sr-marks [data-sr-then]`).evaluate(el => [el.getAttribute('role'), el.getAttribute('aria-label'), el.getAttribute('data-icon')]);
    assert.deepEqual(await act('sr_owner'), ['img', 'Block', 'block']);
    assert.deepEqual(await act('sr_quiet'), ['img', 'Tell the manager', 'supervisor_account']);
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] [data-sr-then]`).count(), 0, 'a built-in has no action mark');
    // The Decision model checking it is a bolt, named for what it costs (the server's words); a fixed check has no mark. No pill on a row.
    assert.equal(await desk.locator(`${SEC()} :is(.sr-cost, .pill)`).count(), 0, 'no pill');
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] [data-sr-cost]`).count(), 0, 'a free rule has no mark');
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-rule="sr_snooze"] [data-sr-cost]`).evaluate(el => [el.getAttribute('role'), el.getAttribute('aria-label'), el.textContent]),
      ['img', 'One Jev call per event', 'bolt']);
    // A shared rule wears one quiet mark saying where else it applies; the stage's own wears none.
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-rule]`).evaluateAll(els => els.map(e => `${(e as HTMLElement).dataset['srRule']}: ${e.querySelector('[data-sr-shared]')?.getAttribute('aria-label') ?? ''}`)),
      ['sr_quiet: In every stage of Engineering', 'sr_owner: ', 'sr_snooze: In every pipeline']);
    // A row's marks sit in one column: ⋯ is last.
    assert.equal(await desk.locator(`${SEC()} .sr-r > .sr-line > button:not(.sr-open, .sr-more)`).count(), 0, 'no Edit or Retire words on a row');
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-line > :last-child`).getAttribute('data-sr-more'), 'sr_owner', '⋯ is last');
    assert.equal(await desk.locator(`${SEC()} .sr-builtin [data-sr-more]`).count(), 0);
    // ⋯: Edit, then after a line, last and red, Retire; Esc closes it and gives ⋯ the focus back.
    const more = desk.locator(`${SEC()} [data-sr-rule="sr_owner"] [data-sr-more]`);
    await more.click();
    const pop = desk.locator('#pe-dialog > #sr-menu:popover-open');
    await pop.waitFor();
    assert.deepEqual(await pop.locator('.tl-menu > *').evaluateAll(els => els.map(e => e.getAttribute('role') === 'separator' ? '—' : (e.textContent || '').trim())), ['Edit', '—', 'Retire']);
    const red = await desk.evaluate(() => { const p = document.createElement('i'); p.style.color = 'var(--fail)'; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; });
    assert.equal(await pop.locator('[data-sr-retire]').evaluate(el => getComputedStyle(el).color), red, 'Retire is red');
    await desk.keyboard.press('Escape');
    await desk.waitForFunction(() => !document.querySelector('#sr-menu:popover-open'));
    await desk.waitForFunction(() => document.activeElement?.getAttribute('data-sr-more') === 'sr_owner');
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'Esc closed the menu, not the editor');
    // The sentence used elsewhere (the Rules page) still reads a false condition as what it is.
    assert.equal(await desk.evaluate(() => srSentence({id: 'x', name: 'x', pipeline_id: 'eng', stages: ['build'], event: 'enters', cond: {requester_person: false},
      then: 'block', scope: 'stage', revision: 1} as never)), 'A task enters → asked by a bot → Block');

    // A built-in opens its name and whole text in place under its row; Esc closes it and the editor stays.
    const built = desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-open`);
    await built.click();
    assert.equal(await built.getAttribute('aria-expanded'), 'true');
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-detail .sr-d-name`).innerText(), 'Hand-off files with no assignee');
    await desk.keyboard.press('Escape');
    assert.equal(await built.getAttribute('aria-expanded'), 'false');
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'Esc closed the read view, not the editor');

    // + Rule under On entering lists the owner's team rule and the Limit, then what is not ready yet, off, with Soon.
    // A Goal typed and a Limit picked wait for the stage's Save; the redraw keeps More rules open.
    await desk.locator('#pe-body [data-pe-sf="purpose"]').fill('Keep the build green');
    await desk.locator(`${ENTER} [data-pe-enter-add="rule"]`).click();
    const rulePick = desk.locator('#pe-rule-pick:popover-open');
    await rulePick.waitFor();
    assert.deepEqual(await rulePick.locator('[data-pe-enter-pick]').allTextContents(), ["The owner's team…", 'Limit']);
    assert.deepEqual(await rulePick.locator('.pe-mi-soon').evaluateAll(els => els.map(e => [e.firstChild?.textContent, e.querySelector('.pill.soon')?.textContent, (e as HTMLButtonElement).disabled])),
      [['A question Decision model answers…', 'Soon', true], ['In your own words…', 'Soon', true]]);
    await rulePick.locator('[data-pe-enter-pick="limit"]').click();
    await desk.locator(`${ENTER} [data-pe-sf="task_limit"]`).fill('3');
    assert.equal((await draftStage(desk)).task_limit, 3);
    assert.equal(await desk.locator(MORE).getAttribute('aria-expanded'), 'true', 'a redraw keeps More rules open');
    assert.equal(await desk.locator(`${PH('more')} [data-sr-rule]`).first().isVisible(), true);
    // + Action lists the actions; nothing of + Rule's
    await desk.locator(`${ENTER} [data-pe-enter-add="act"]`).click();
    const actPick = desk.locator('#pe-act-pick:popover-open');
    await actPick.waitFor();
    assert.deepEqual(await actPick.locator('.tl-mi').allTextContents(), ['Change the owner…', 'Open a linked task in…', 'Add its PRs to the merge queue']);
    await desk.keyboard.press('Escape');

    // A rule's card, opened from the stage: a snooze rule has no Name and no When, only what it refuses and what the bot
    // is told. Cancel gives the row the focus back; the Goal typed is kept.
    await desk.locator(`${SEC()} [data-sr-rule="sr_snooze"] .sr-open`).click();
    const card = desk.locator('#sr-panel form[data-sr-form="sr_snooze"]');
    await card.waitFor();
    assert.equal(await desk.locator('#sr-panel h2').innerText(), 'Snooze rule');
    assert.equal(await card.locator(':is([name=name], [name=event], [name=stage]):not([type=hidden])').count(), 0, 'no Name, no When, no On');
    assert.deepEqual(await card.locator('.ru-lbl').allInnerTexts().then(a => a.map(t => t.toLowerCase())), ['refuse the snooze when', 'the bot is told']);
    assert.equal(await card.locator('textarea[name=jev]').inputValue(), 'Waiting on something outside, or blocked?');
    assert.doesNotMatch(await desk.locator('#sr-panel').innerText(), /Decision model decides|Cost:/i);
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'the editor stays under the card');
    await card.locator('[data-sr-back]').last().click();
    await desk.waitForFunction(() => document.activeElement?.closest('#pe-dialog [data-sr-rule]')?.getAttribute('data-sr-rule') === 'sr_snooze');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green'], 'the Goal typed is kept');
    // A timer's card: After, Then, Message; a timer never blocks.
    await desk.locator(`${SEC()} [data-sr-rule="sr_quiet"] .sr-open`).click();
    const timer = desk.locator('#sr-panel form[data-sr-form="sr_quiet"]');
    await timer.waitFor();
    assert.equal(await desk.locator('#sr-panel h2').innerText(), 'Timer');
    assert.deepEqual([await timer.locator('[name=hours]').inputValue(), await timer.locator('[name=timer_kind]').inputValue(), await timer.locator('[name=then]').inputValue()], ['6', 'quiet', 'tell_manager']);
    assert.equal(await timer.locator('[name=then] option[value=block]:not([disabled])').count(), 0, 'a timer never blocks');
    assert.equal(await timer.locator(':is([name=name], [name=event]):not([type=hidden])').count(), 0);
    await timer.locator('[data-sr-back]').last().click();

    // + Rule under More rules is a new snooze rule: it sends this stage, a bot's snooze, the question and Block, named
    // from what it says; it acts at once, and its row is there with More rules still open.
    await desk.locator(`${PH('more')} [data-sr-add]`).click();
    const fresh = desk.locator('#sr-panel form[data-sr-form=""]');
    await fresh.waitFor();
    assert.equal(await desk.locator('#sr-panel h2').innerText(), 'Snooze rule');
    await fresh.locator('textarea[name=jev]').fill('Is it waiting on a person?');
    await fresh.locator('[name=reason]').fill('Say who you are waiting on.');
    await fresh.locator('[type=submit]').click();
    await desk.locator(`${PH('more')} [data-sr-rule="sr_new"]`).waitFor();
    const added = served.writes.at(-1)!;
    assert.equal(added.path, '/api/v2/stage-rules');
    assert.deepEqual([added.body['event'], added.body['then'], added.body['jev'], added.body['reason'], added.body['stages'], added.body['pipeline']],
      ['bot_snoozes', 'block', 'Is it waiting on a person?', 'Say who you are waiting on.', ['build'], 'eng']);
    assert.ok(String(added.body['name'] ?? '').length > 0, 'the server needs a name: made from the rule');
    assert.equal(await desk.locator(MORE).getAttribute('aria-expanded'), 'true', 'a save keeps More rules open');
    assert.equal(await rowText(desk, 'sr_new'), 'A bot snoozes · Is it waiting on a person?');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green']);
    // Retire, in ⋯, asks first (accepted here) and sends the revision read.
    await desk.locator(`${SEC()} [data-sr-rule="sr_new"] [data-sr-more]`).click();
    await desk.locator('#sr-menu:popover-open [data-sr-retire]').click();
    await desk.locator(`${SEC()} [data-sr-rule="sr_new"]`).waitFor({state: 'detached'});
    assert.deepEqual(served.writes.at(-1), {path: '/api/v2/stage-rules/sr_new/retire', body: {revision: 1}});

    // The stage's own Save sends what was set in it meanwhile, and no rule.
    assert.equal(served.writes.filter(w => w.path === '/api/v2/pipelines/eng').length, 0, 'the rules saved on their own');
    await desk.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 100 && !served.writes.some(w => w.path === '/api/v2/pipelines/eng'); i++) await desk.waitForTimeout(20);
    const saved = (served.writes.find(w => w.path === '/api/v2/pipelines/eng')!.body as {stages: {id: string; purpose: string; task_limit: number | null}[]}).stages.find(x => x.id === 'build')!;
    assert.deepEqual([saved.purpose, saved.task_limit], ['Keep the build green', 3]);
    assert.doesNotMatch(JSON.stringify(served.writes.find(w => w.path === '/api/v2/pipelines/eng')!.body), /"sr_/, 'no rule in the stage\'s save');
    await desk.locator('#pe-dialog [data-pe-close]').click();
    await desk.locator('#pe-dialog').waitFor({state: 'detached'});

    // Rules › Pipelines and stages: a rule saved before Tell needed a timer is listed as not acting, with why.
    await desk.goto('http://tico-ui.test/#/rules/stages');
    await desk.locator('[data-stage-rule="sr_spec"]').waitFor();
    assert.equal(await desk.locator('[data-stage-rule="sr_spec"] [data-sr-idle]').getAttribute('aria-label'), `Not acting: ${NEEDS_TIMER}`);
    // The Rules page draws the panel's row: one line, the Then mark, no title.
    assert.equal(await desk.locator('[data-stage-rule="sr_spec"] .sr-name').count(), 0);
    assert.equal(await desk.locator('[data-stage-rule="sr_snooze"] [data-sr-then]').getAttribute('title'), 'Block');
    // A rule the viewer may change, of any scope, opens the panel's own form for it here, and its ⋯ has Edit and Retire.
    await desk.locator('[data-stage-rule="sr_owner"] .sr-open').click();
    await desk.locator('#sr-panel form[data-sr-form="sr_owner"]').waitFor();
    await desk.locator('#sr-panel form [data-sr-back]').last().click();
    assert.equal(await desk.locator('#sr-panel').isVisible(), false, 'Cancel closes the form');
    await desk.waitForFunction(() => document.activeElement?.closest('[data-stage-rule]')?.getAttribute('data-stage-rule') === 'sr_owner');
    const pageMore = desk.locator('[data-stage-rule="sr_quiet"] [data-sr-more]');
    assert.equal(await pageMore.count(), 1, 'a whole-pipeline rule has ⋯ here too');
    await pageMore.click();
    const pagePop = desk.locator('#sr-menu-page:popover-open');
    assert.deepEqual(await pagePop.locator('.tl-menu > *').evaluateAll(els => els.map(e => e.getAttribute('role') === 'separator' ? '—' : (e.textContent || '').trim())), ['Edit', '—', 'Retire']);
    await pagePop.locator('[data-sr-edit]').click();
    const pageForm = desk.locator('#sr-panel form[data-sr-form="sr_quiet"]');
    await pageForm.waitFor();
    assert.deepEqual(await pageForm.locator('[name=on] option').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value)), ['pipeline'], 'a whole-pipeline rule: no stage to offer');
    await pageForm.locator('[name=reason]').fill('Quiet for 6 h on the bot\'s turn');
    const writesAt = served.writes.length;
    await pageForm.locator('[type=submit]').click();
    await desk.locator('#sr-panel').waitFor({state: 'hidden'});
    assert.equal(served.writes.length, writesAt + 1);
    assert.deepEqual([served.writes.at(-1)!.path, served.writes.at(-1)!.body['reason'], served.writes.at(-1)!.body['stages']], ['/api/v2/stage-rules/sr_quiet', 'Quiet for 6 h on the bot\'s turn', []]);
    await desk.waitForFunction(() => document.activeElement?.closest('[data-stage-rule]')?.getAttribute('data-stage-rule') === 'sr_quiet');
    // a second click on ⋯ here closes its menu too
    await pageMore.click();
    await pagePop.waitFor();
    await pageMore.click();
    await desk.waitForTimeout(150);
    assert.equal(await desk.locator('#sr-menu-page:popover-open').count(), 0, 'a second click closes it');
    assert.equal(await desk.locator('[data-stage-rule="sr_owner"] [data-sr-idle]').count(), 0, 'a Block on an action acts');

    // Rules › Suggested: what accepting each of a bot's stage rules does.
    await desk.goto('http://tico-ui.test/#/rules/suggested');
    const sg = desk.locator('[data-group="stage-rules"]');
    await sg.locator('[data-stage-rule]').first().waitFor();
    const row = (id: string) => sg.locator(`[data-stage-rule="${id}"]`);
    // One line each with the suggestion's mark; what each is, and its Skip and Accept, open under it.
    assert.deepEqual(await sg.locator('[data-sr-suggest]').evaluateAll(els => els.map(e => e.getAttribute('aria-label'))),
      ['Suggested rule', 'Suggested change', 'Suggested retire']);
    assert.deepEqual(await sg.locator('[data-sr-title]').allTextContents(), ['New: Ask before Move back', 'Change to Owner on the team', 'Retire: Quiet bot tasks']);
    assert.equal(await row('sr_sg_new').locator('[data-sr-decide]').first().isVisible(), false, 'closed until opened');
    for (const id of ['sr_sg_new', 'sr_sg_change', 'sr_sg_retire']) {
      await row(id).locator('.sr-open').click();
      await row(id).locator('[data-sr-decide="accept"]').waitFor();
      // Skip left, the one filled button right and last
      assert.deepEqual(await row(id).locator('.ru-pacts > button').evaluateAll(els => els.map(e => [e.getAttribute('data-sr-decide'), e.classList.contains('primary')])),
        [['skip', false], ['accept', true]]);
    }
    assert.equal(await row('sr_sg_new').locator('[data-sr-decide="accept"]').isVisible(), false, 'one open at a time');
    await row('sr_sg_new').locator('.sr-open').click();
    assert.equal(await row('sr_sg_new').locator('[data-sr-decide="accept"]').innerText(), 'Accept');
    // A suggestion of a shape that would never act says so before Accept is pressed.
    assert.equal(await row('sr_sg_new').locator('.sr-marks [data-sr-idle]').getAttribute('aria-label'), `Not acting: ${NEEDS_TIMER}`);
    assert.equal(await row('sr_sg_new').locator('.sr-detail [data-sr-idle]').innerText(), `Not acting: ${NEEDS_TIMER}`);
    // A change lists what differs from the rule as it is now, and only that.
    await row('sr_sg_change').locator('.sr-open').click();
    assert.deepEqual(await row('sr_sg_change').locator('[data-sr-diff] li').allTextContents(), ['Then Block → Tell the manager']);
    assert.equal(await row('sr_sg_change').locator('[data-sr-decide="accept"]').innerText(), 'Accept change');
    // A retire says so, with the bot's reason, and its button is Retire.
    await row('sr_sg_retire').locator('.sr-open').click();
    assert.equal(await row('sr_sg_retire').locator('[data-sr-why]').innerText(), 'Why: Covered by the Late rule.');
    assert.equal(await row('sr_sg_retire').locator('[data-sr-decide="accept"]').innerText(), 'Retire');
    await row('sr_sg_retire').locator('[data-sr-decide="accept"]').click();
    await row('sr_sg_retire').waitFor({state: 'detached'});
    assert.equal(served.writes.at(-1)?.path, '/api/v2/stage-rules/sr_sg_retire/accept');
    assert.equal(served.rules.find(x => x.id === 'sr_quiet')?.retired, true);
    await row('sr_sg_change').locator('.sr-open').click();
    await row('sr_sg_change').locator('[data-sr-decide="skip"]').click();
    await row('sr_sg_change').waitFor({state: 'detached'});
    assert.equal(served.writes.at(-1)?.path, '/api/v2/stage-rules/sr_sg_change/skip');

    // Only shared rules apply (the company's snooze rule): no stage has a mark at all, and the stage's name opens its editor,
    // whose More rules lists the shared rule with its mark.
    for (const r of served.rules) if (r.id === 'sr_owner' || r.id === 'sr_spec') r.retired = true;
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    await desk.reload();
    await desk.locator('[data-pv-col="build"] [data-sr-open][hidden]').waitFor({state: 'attached'});
    assert.equal(await desk.locator('[data-pv-col="spec"] [data-sr-open]').isHidden(), true, 'no mark without rules of its own');
    assert.equal(await desk.locator('[data-pv-col="build"] [data-sr-open]').isVisible(), false);
    assert.equal(await count(desk, 'build'), 'None of its own', 'its name still says so');
    assert.equal(await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').getAttribute('title'), 'Edit Build', 'the edit entry is the stage name');
    await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').click();
    await desk.locator(`${PH('here')} [data-sr-add]`).waitFor();
    await desk.locator(`${PH('more')} [data-sr-rule]`).first().waitFor({state: 'attached'});
    assert.equal(await desk.locator(MORE).getAttribute('aria-expanded'), 'false', 'More rules starts shut each time');
    await desk.locator(MORE).click();
    assert.deepEqual(await phaseRules(desk, 'more'), ['sr_snooze']);
    assert.equal(await desk.locator(`${PH('more')} [data-sr-rule="sr_snooze"] [data-sr-shared]`).getAttribute('aria-label'), 'In every pipeline');
    assert.deepEqual(await phaseRules(desk, 'enter'), []);
    assert.deepEqual(await phaseRules(desk, 'here'), []);
    assert.equal(await desk.locator(`${SEC()} [data-sr-add]`).count(), 2, 'While here and More rules offer + Rule');
    assert.equal(await desk.locator(`${ENTER} [data-pe-enter-add]`).count(), 2, 'On entering offers + Rule and + Action');
    await desk.locator('#pe-dialog [data-pe-close]').click();

    // No rule applies anywhere: still no mark; each phase that can have one offers only + Rule, with no empty text; the
    // built-in rules still end the stage, inside More rules.
    for (const r of served.rules) r.retired = true;
    await desk.reload();
    await desk.locator('[data-pv-col="build"] [data-sr-open][hidden]').waitFor({state: 'attached'});
    await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').click();
    await desk.locator(`${PH('here')} [data-sr-add]`).waitFor();
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule]`).count(), 0);
    assert.equal(await desk.locator(`${SEC()} :is([data-sr-none], .sr-none)`).count(), 0, 'no "no rules" words');
    assert.deepEqual(await desk.locator(`${ENTER} [data-pe-enter-add]`).evaluateAll(els => els.map(e => (e.textContent || '').trim())), ['+ Rule', '+ Action']);
    await desk.locator(MORE).click();
    assert.equal(await desk.locator(`${PH('more')} [data-sr-add]`).innerText(), '+ Rule');
    assert.deepEqual(await builtIns(desk), ['A stage hand-off files its task with no assignee.']);
    await desk.locator('#pe-dialog [data-pe-close]').click();

    // A member reads the same editor and changes nothing: the mark opens it at its first phase with rules, read only;
    // the phases, their rules and the shared marks are all there.
    const member = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(member, errors, false);
    await member.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=list');
    await waitCount(member, 'build', '1 rule of its own');
    const listMark = member.locator('.pv-group[aria-label="Build"] .pv-group-head > :last-child');
    assert.equal(await listMark.getAttribute('data-sr-open'), 'build', 'the list\'s mark ends its header too');
    await listMark.click();
    await member.locator(`${PH('here')} [data-sr-rule]`).first().waitFor();
    await member.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, HERE_H);
    assert.deepEqual(await phaseHeads(member), PHASES);
    assert.deepEqual(await phaseRules(member, 'here'), ['sr_quiet']);
    await member.locator(MORE).click();
    assert.deepEqual(await phaseRules(member, 'more'), ['sr_owner', 'sr_snooze']);
    assert.equal(await member.locator(`${SEC()} [data-sr-shared]`).count(), 2, 'the shared marks show read only too');
    assert.equal(await member.locator('#pe-dialog fieldset.pe-ro[disabled]').count(), 2, 'the top row and the fields of While here are read only');
    assert.deepEqual(await member.locator('#pe-dialog [data-pe-leave-row] .sr-open').evaluateAll(els => els.map(e => (e as HTMLButtonElement).disabled)), [true], 'To leave: its row, nothing to open');
    assert.equal(await member.locator('#pe-dialog fieldset [data-sr-rule]').count(), 0, 'the rules sit outside the switched-off fields');
    assert.equal(await member.locator('#pe-dialog [data-pe-sf="name"]').isDisabled(), true);
    // On entering's Limit and action: their rows, with nothing to open or remove and nothing to add
    assert.deepEqual(await enterRows(member), ['limit: At most 2 tasks at once', 'act: Add its PRs to the merge queue']);
    assert.deepEqual(await member.locator(`${ENTER} [data-pe-enter-row] .sr-open`).evaluateAll(els => els.map(e => (e as HTMLButtonElement).disabled)), [true, true]);
    assert.equal(await member.locator(`${ENTER} :is([data-pe-enter-add], [data-pe-row-x], .pe-row-ed, .sr-more)`).count(), 0);
    assert.equal(await member.locator('#pe-foot').isHidden(), true, 'no Save');
    assert.equal(await member.locator('#pe-dialog [data-pe-save], #pe-dialog [data-pe-req-add], #pe-dialog [data-pe-row-x]').count(), 0);
    // Read only: the row opens the rule's whole text in place, with its name and what it says.
    await member.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-open`).click();
    assert.equal(await member.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-detail .sr-d-name`).innerText(), 'Owner on the team');
    assert.match(await member.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-detail`).innerText(), /Not on Engineering\./);
    assert.equal(await member.locator('#pe-dialog [data-sr-add], #pe-dialog [data-sr-edit], #pe-dialog [data-sr-retire], #pe-dialog [data-sr-more]').count(), 0);
    assert.match(await member.locator(`${SEC()} .sr-who`).innerText(), /owner, its manager and Admins/);
    await member.locator('#pe-dialog [data-pe-close]').click();

    // A phone: the mark ends the stage tabs' row, and the editor it opens fills the screen.
    const phone = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC',
      isMobile: true, hasTouch: true});
    await serve(phone, errors, true);
    await phone.goto('http://tico-ui.test/#/tasks?pipeline=eng');
    await phone.locator('.pv-is-phone').waitFor();
    await phone.locator('[data-pv-tab="build"]').click();
    await waitCount(phone, 'build', '1 rule of its own');
    const button = await phone.locator('.pv-phone-head [data-sr-open="build"]').boundingBox();
    assert.ok(button && button.height >= 44 && button.width >= 44, 'the phone mark is a 44 px target');
    assert.equal(await phone.locator('.pv-phone-head > :last-child').getAttribute('data-sr-open'), 'build', 'last in the row');
    // Its whole target is its own: a tap near each corner (inside its rounding) and at the centre lands on the mark, not
    // on a stage tab beside it.
    assert.deepEqual(await phone.locator('.pv-phone-head [data-sr-open="build"]').evaluate(el => {
      const r = el.getBoundingClientRect();
      return [[r.left + 6, r.top + 6], [r.right - 6, r.top + 6], [r.left + 6, r.bottom - 6], [r.right - 6, r.bottom - 6], [r.left + r.width / 2, r.top + r.height / 2]]
        .map(([x, y]) => el.contains(document.elementFromPoint(x!, y!)));
    }), [true, true, true, true, true]);
    await phone.locator('.pv-phone-head [data-sr-open="build"]').click();
    await phone.locator(`${PH('here')} [data-sr-rule]`).first().waitFor();
    await phone.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, HERE_H);
    await phone.locator(MORE).click();
    // 44 px rows and ⋯ on a phone; a tap at ⋯'s centre is ⋯'s, and at the row's text the row's: neither covers the other.
    assert.deepEqual(await phone.locator(`${SEC()} .sr-r:not(.sr-builtin)`).evaluateAll(els => [...new Set(els.map(e => Math.round(e.querySelector('.sr-line')!.getBoundingClientRect().height)))]), [44]);
    await phone.locator(`${SEC()} [data-sr-rule="sr_owner"]`).scrollIntoViewIfNeeded();
    assert.deepEqual(await phone.locator(`${SEC()} [data-sr-rule="sr_owner"]`).evaluate(el => {
      const more = el.querySelector<HTMLElement>('.sr-more')!, open = el.querySelector<HTMLElement>('.sr-open')!, m = more.getBoundingClientRect(), o = open.getBoundingClientRect();
      const at = (x: number, y: number) => document.elementFromPoint(x, y);
      return {more: [m.width, m.height], moreHit: more.contains(at(m.left + 2, m.top + m.height / 2)) && more.contains(at(m.right - 2, m.top + m.height / 2)),
        rowHit: open.contains(at(o.right - 2, o.top + o.height / 2)) && open.contains(at(o.left + 2, o.top + o.height / 2))};
    }), {more: [44, 44], moreHit: true, rowHit: true});
    // Each phase heading's mark starts at the fields' left edge, More rules' too.
    assert.deepEqual(await phone.evaluate(() => {
      const left = (el: Element | null) => Math.round(el!.getBoundingClientRect().left);
      const field = left(document.querySelector('#pe-body [data-pe-sf="name"]'));
      return [...document.querySelectorAll('#pe-body .pe-phase-h .nav-icon')].map(i => left(i) - field);
    }), [0, 0, 0, 0]);
    // More rules is a 44 px line on a phone.
    assert.equal(Math.round((await phone.locator(MORE).boundingBox())!.height), 44);
    const box = await phone.locator('#pe-dialog').boundingBox();
    assert.ok(box && box.width >= 389, `the editor fills the phone's width (${box?.width})`);
    const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 390, `the phone page does not scroll sideways (${wide} px)`);
    assert.equal(await phone.evaluate(() => { const b = document.getElementById('pe-body')!; return b.scrollWidth - b.clientWidth; }), 0, 'the editor does not scroll sideways');

    assert.deepEqual(errors, []);
    console.log('stage rules: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
