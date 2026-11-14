import type {Page, Route} from 'playwright';
// Offline browser check of a stage's rules (ui/app/stage-rules.ts; Stage rules spec, acceptance 8): a stage header on a
// pipeline board with rules of its own ends in a quiet rules mark at its far right, a gavel with no word and no count,
// named for how many it has ("1 rule of its own"); a stage with none of its own (shared rules alone) has no mark at all
// (style guide, Pipeline stage). The editor is the stage's phases, each a heading with a small mark: On entering,
// While here, To leave; every rule that applies sits in the phase where it acts (its When: a task entering in On
// entering, the owner changing, a bot snoozing or a timer in While here), the stage's own first, a shared one with one
// quiet mark, each with its cost; the built-in rules are one closed line at the end. Build's mark opens Build's editor at
// its first phase with rules, its heading focused. On entering is one list: the Limit and the action as rows (each opens
// a small editor in place, with Remove), its rules, and one + Rule or action whose pick list hides what is set; they are
// stored as the old fields stored them (task_limit; on_enter {}, {file_into}, {act: 'github.queue'}). A phase's + Rule
// (On entering's Rule) starts with On = Build and that phase's When, and sends it; Edit sends the rule's revision; Retire
// asks first and sends it; each acts at once, in a form over the editor, and a Goal or a Limit set in the stage
// meanwhile is kept and saved by the stage's own Save. Built in has no controls. A member who may not change the
// pipeline reads the same editor, its fields off, the Limit and action rows with nothing to open, with no Save, + Rule,
// Edit or Retire. The whole pipeline's editor has the phases' rules under each stage. On a phone the
// mark sits at the end of the stage tabs, a 44 px target that nothing else covers, and the editor fills the screen's
// width. Rules › Suggested says what accepting each of a bot's stage rules does: a new rule, a change listing what
// differs from the rule it rewrites, or a retire with the bot's reason and a Retire button; Retire and Skip send their
// decision. The fake server answers as backend/stage_rules_api.py does.
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
// 'leave', or 'always' for the built-in line at the end).
const SEC = (sid = 'build') => `#pe-dialog [data-sr-section="${sid}"]`;
const PH = (phase: string, sid = 'build') => `${SEC(sid)}[data-sr-phase="${phase}"]`;
// A phase's rules, by id (a row has no title).
const phaseRules = (page: Page, phase: string, sid = 'build') => page.locator(`${PH(phase, sid)} [data-sr-rule]`).evaluateAll(els =>
  els.map(e => (e as HTMLElement).dataset['srRule']));
// The built-in rules' text.
const builtIns = (page: Page) => page.locator(`${PH('always')} [data-sr-group="builtin"] .sr-r .sr-text`).allTextContents();
// A phase heading's mark and words ("login On entering").
const phaseHeads = (page: Page) => page.locator('#pe-body .pe-h').evaluateAll(hs =>
  hs.map(h => `${h.querySelector('.nav-icon')?.textContent ?? ''} ${h.lastChild?.textContent ?? ''}`.trim()));
const inView = (page: Page, sel: string) => page.locator(sel).evaluate(el => {
  const b = el.getBoundingClientRect(), body = document.getElementById('pe-body')!.getBoundingClientRect();
  return b.top >= body.top - 1 && b.bottom <= body.bottom + 1;
});
const HERE_H = '#pe-dialog .pe-phase[data-pe-phase="here"] > .pe-phase-h';
// On entering's one list: its Limit and action rows (by kind), its one add button, and that button's pick list.
const ENTER = '#pe-dialog .pe-phase[data-pe-phase="enter"]';
const ENTER_ADD = `${ENTER} [data-pe-enter-add]`;
const enterRows = (page: Page) => page.locator(`${ENTER} [data-pe-enter-row]`).evaluateAll(els =>
  els.map(e => `${(e as HTMLElement).dataset['peEnterRow']}: ${e.querySelector('.sr-text')?.textContent ?? ''}`));
const enterPicks = async (page: Page) => {
  await page.locator(ENTER_ADD).click();
  await page.locator('#pe-enter-pick:popover-open').waitFor();
  return page.locator('#pe-enter-pick [data-pe-enter-pick]').allTextContents();
};
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

    // Build's mark opens Build's editor at its first phase with rules (While here: none of Build's acts as a task
    // enters), scrolled to, its heading focused. No other dialog.
    await mark.click();
    await desk.locator(`${PH('here')} [data-sr-rule]`).first().waitFor();
    assert.equal((await desk.locator('#pe-title').innerText()).trim(), 'Engineering › Build');
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, HERE_H);
    assert.equal(await desk.locator('#sr-panel').isVisible().catch(() => false), false, 'no separate rules dialog');
    assert.ok(await inView(desk, HERE_H), 'scrolled to While here');
    // The phases, top to bottom, each with its small leading mark; no Rules heading; the built-in line ends the stage.
    assert.deepEqual(await phaseHeads(desk), ['login On entering', 'schedule While here', 'logout To leave']);
    assert.equal(await desk.locator('#pe-body .pe-stage-body > :last-child').getAttribute('data-sr-phase'), 'always', 'the built-in line is last');
    // On entering is one list: nothing set (no Limit, no action, no rule acting there), so its one add button alone; no
    // fields, no "Do nothing".
    assert.deepEqual(await enterRows(desk), []);
    assert.equal(await desk.locator(`${ENTER} :is(input, select, fieldset)`).count(), 0, 'On entering has no fields');
    // Each rule sits in the phase where it acts: the owner changing, a timer and a bot snoozing act While here; nothing
    // acts as a task leaves yet. The stage's own come first.
    assert.deepEqual(await phaseRules(desk, 'enter'), [], 'no rule of Build\'s acts as a task enters');
    assert.deepEqual(await phaseRules(desk, 'here'), ['sr_owner', 'sr_quiet', 'sr_snooze'], 'While here, its own first');
    assert.equal(await desk.locator(`${PH('leave')} :is([data-sr-rule], [data-sr-add])`).count(), 0, 'To leave has no rule and no + Rule');
    // Every When lands in exactly one phase: any that includes a task entering in On entering, the rest While here.
    assert.deepEqual(await desk.evaluate(() => ['enters', 'enters,owner_changes', 'owner_changes,enters', 'owner_changes', 'bot_snoozes', 'timer',
      'owner_changes,bot_snoozes', 'a_new_event'].map(e => srPhaseOf(e))), ['enter', 'enter', 'enter', 'here', 'here', 'here', 'here', 'here']);
    // A shared rule wears one quiet mark saying where else it applies; the stage's own wears none. No group captions.
    assert.deepEqual(await desk.locator(`${PH('here')} [data-sr-rule]`).evaluateAll(els => els.map(e => e.querySelector('[data-sr-shared]')?.getAttribute('aria-label') ?? '')),
      ['', 'In every stage of Engineering', 'In every pipeline']);
    assert.equal(await desk.locator(`${PH('here')} [data-sr-shared]`).first().evaluate(el => [el.textContent, el.getAttribute('title')].join(' | ')),
      'dynamic_feed | In every stage of Engineering');
    assert.equal(await desk.locator(`${SEC()} :is(h5, .sr-group:not(.sr-always))`).count(), 0, 'no group captions');
    // Each phase a rule can act in ends in one quiet add button: On entering's + Rule or action (rules, a Limit, an
    // action), While here's + Rule; Save, in the footer, stays the one filled button.
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-add]`).evaluateAll(els => els.map(el => [el.closest('[data-sr-phase]')?.getAttribute('data-sr-phase'),
      (el.textContent || '').trim(), el.classList.contains('primary'), el.classList.contains('ghost')])), [['here', '+ Rule', false, true]]);
    assert.deepEqual(await desk.locator(`${ENTER} :is([data-pe-enter-add], [data-sr-add])`).evaluateAll(els => els.map(el => [(el.textContent || '').trim(),
      el.classList.contains('ghost'), el.getAttribute('aria-haspopup')])), [['+ Rule or action', true, 'menu']], 'On entering: one add button');
    assert.deepEqual(await desk.locator('#pe-dialog button.primary').evaluateAll(els => els.map(e => (e.textContent || '').trim())), ['Save']);
    assert.deepEqual(await builtIns(desk), ['A stage hand-off files its task with no assignee.'], 'Built in: its what');
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] :is([data-sr-edit], [data-sr-more])`).count(), 0, 'Built in is read-only');
    // The built-in rules are one closed line at the end, "Always on, can't be changed (1)"; opened, their rows.
    const always = desk.locator(`${SEC()} [data-sr-always]`);
    assert.equal((await always.innerText()).replace(/\s+/g, ' ').replace('›', '').trim(), "Always on, can't be changed (1)");
    assert.equal(await always.getAttribute('aria-expanded'), 'false');
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-r`).isVisible(), false, 'closed at first');
    await always.click();
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-r`).isVisible(), true);
    // One dense line per rule, 36 px: no title; what it does as a mark, the rule, its marks, then ⋯.
    assert.equal(await desk.locator(`${SEC()} .sr-r :is(.sr-name, .sr-what, .sr-says)`).count(), 0, 'no title or second line');
    assert.deepEqual(await desk.locator(`${SEC()} .sr-r`).evaluateAll(els => [...new Set(els.map(e => Math.round(e.querySelector('.sr-line')!.getBoundingClientRect().height)))]), [36]);
    // What it does: a mark with its Then as its tip; hidden from a reader, whose row's name already ends with it (no
    // "Block … → Block").
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] [data-sr-then]`).evaluate(el => [el.getAttribute('aria-hidden'), el.getAttribute('data-icon'), (el as HTMLElement).title]),
      ['true', 'block', 'Block'], 'what it does: a mark with its Then as its tip');
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_quiet"] [data-sr-then]`).getAttribute('title'), 'Tell the manager');
    // A row's marks sit in one column: a row without ⋯ keeps its slot.
    const marksRight = await desk.locator(`${SEC()} .sr-r:not(.sr-builtin)`).evaluateAll(els => els.map(e => {
      const line = e.querySelector('.sr-line')!.lastElementChild!.getBoundingClientRect(); return Math.round(line.left);
    }));
    assert.equal(new Set(marksRight).size, 1, `⋯ or its slot in one column: ${marksRight}`);
    assert.equal(await desk.locator(`${SEC()} [data-sr-group="builtin"] [data-sr-then]`).count(), 0, 'a built-in has no Then');
    // The cost is a mark, not a pill: none for a free rule, the brain for a Jev call, named for what it costs.
    assert.equal(await desk.locator(`${SEC()} :is(.sr-cost, .pill)`).count(), 0, 'no pill');
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] [data-sr-cost]`).count(), 0, 'a free rule has no mark');
    assert.deepEqual(await desk.locator(`${SEC()} [data-sr-rule="sr_snooze"] [data-sr-cost]`).evaluate(el => [el.getAttribute('role'), el.getAttribute('aria-label'), el.textContent]),
      ['img', 'One Jev call per event', 'psychology']);
    // A Jev rule's line says Jev decides; its question is in the rule itself (its form here).
    assert.equal(await rowText(desk, 'sr_snooze'), 'A bot snoozes → Jev decides → Block');
    // The whole rule is the row's tip and inside its name.
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-open`).getAttribute('title'), 'The owner changes → owner not on engineering → Block');
    // No word buttons on a row: the row opens the rule, a quiet ⋯ ends it; Built in has no ⋯.
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
    assert.equal(await more.getAttribute('aria-expanded'), 'true');
    await desk.keyboard.press('Escape');
    await desk.waitForFunction(() => !document.querySelector('#sr-menu:popover-open'));
    await desk.waitForFunction(() => document.activeElement?.getAttribute('data-sr-more') === 'sr_owner');
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'Esc closed the menu, not the editor');
    // A second click on ⋯ closes the menu it opened.
    await more.click();
    await pop.waitFor();
    await more.click();
    await desk.waitForTimeout(150);
    assert.equal(await desk.locator('#sr-menu:popover-open').count(), 0, 'a second click closes it');
    // A false condition reads as what it is: requester_person false is "asked by a bot".
    assert.equal(await desk.evaluate(() => srSentence({id: 'x', name: 'x', pipeline_id: 'eng', stages: ['build'], event: 'enters', cond: {requester_person: false},
      then: 'block', scope: 'stage', revision: 1} as never)), 'A task enters → asked by a bot → Block');
    assert.equal(await desk.evaluate(() => srSentence({id: 'x', name: 'x', pipeline_id: 'eng', stages: ['build'], event: 'enters', cond: {requester_person: true},
      then: 'block', scope: 'stage', revision: 1} as never)), 'A task enters → asked by a person → Block');
    assert.equal(await rowText(desk, 'sr_owner'), 'The owner changes → owner not on engineering → Block');
    assert.equal(await rowText(desk, 'sr_quiet'), '6 h quiet → Tell the manager');

    // A built-in opens its name and whole text in place under its row; Esc closes it, the row has the focus back and the
    // editor stays. Enter on it opens it again.
    const built = desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-open`);
    await built.click();
    assert.equal(await built.getAttribute('aria-expanded'), 'true');
    const builtDetail = desk.locator(`${SEC()} [data-sr-group="builtin"] .sr-detail`);
    assert.equal(await builtDetail.isVisible(), true);
    assert.equal(await builtDetail.locator('.sr-d-name').innerText(), 'Hand-off files with no assignee', 'its name is in the read view');
    await desk.keyboard.press('Escape');
    assert.equal(await built.getAttribute('aria-expanded'), 'false');
    assert.equal(await builtDetail.isVisible(), false);
    assert.equal(await built.evaluate(el => el === document.activeElement), true, 'the row has the focus back');
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'Esc closed the read view, not the editor');
    await desk.keyboard.press('Enter');
    assert.equal(await built.getAttribute('aria-expanded'), 'true', 'Enter opens it');
    await desk.keyboard.press('Escape');

    // A Goal typed in the stage and not saved yet stays through every rule's form below (the rules act at once, the
    // stage's fields wait for its Save).
    await desk.locator('#pe-body [data-pe-sf="purpose"]').fill('Keep the build green');
    const scrolled = await desk.locator('#pe-body').evaluate(el => el.scrollTop);

    // Enter on a rule the person may change opens its form, over the editor; Cancel brings the focus back to that row,
    // with the editor where it was.
    await desk.locator(`${SEC()} [data-sr-rule="sr_owner"] .sr-open`).focus();
    await desk.keyboard.press('Enter');
    await desk.locator('#sr-panel form[data-sr-form="sr_owner"]').waitFor();
    assert.equal(await desk.locator('#sr-panel form [name=name]').inputValue(), 'Owner on the team', 'the name is in the form');
    assert.equal(await desk.locator('#pe-dialog').isVisible(), true, 'the editor stays under the form');
    await desk.locator('#sr-panel form [data-sr-back]').last().click();
    await desk.waitForFunction(() => document.activeElement?.closest('#pe-dialog [data-sr-rule]')?.getAttribute('data-sr-rule') === 'sr_owner');
    assert.equal(await desk.locator('#pe-body').evaluate(el => el.scrollTop), scrolled, 'the editor is scrolled where it was');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green'], 'the Goal typed is kept');

    // A 300-character rule is cut with "…" on its one line: the editor keeps its width, its marks and ⋯ stay whole.
    const width = (await desk.locator('#pe-dialog').boundingBox())!.width;
    await desk.evaluate(() => {
      const items = (SR_ED as unknown as {list: {items: Record<string, unknown>[]}}).list.items, a = items[0]!;
      items.push({...a, id: 'sr_long', cond: {label: 'needs a careful look from someone who knows the payment flow and its edge cases '.repeat(4)},
        jev: 'Is it real?', cost: 'one Jev call per event', not_acting: 'Tell needs a timer'});
      srSectionsPaint();
    });
    const long = await desk.locator(`${SEC()} [data-sr-rule="sr_long"]`).evaluate(el => {
      const line = el.querySelector('.sr-line')!.getBoundingClientRect(), text = el.querySelector<HTMLElement>('.sr-text')!, more = el.querySelector('.sr-more')!.getBoundingClientRect();
      const marks = el.querySelector('.sr-marks')!.getBoundingClientRect(), body = document.querySelector('#pe-body')!;
      return {chars: (text.textContent || '').length, cut: text.scrollWidth > text.clientWidth, h: Math.round(line.height),
        inside: more.right <= line.right + 0.5 && marks.right <= more.left + 0.5 && marks.left >= text.getBoundingClientRect().right - 0.5,
        sideways: body.scrollWidth - body.clientWidth};
    });
    assert.ok(long.chars > 300, `a long rule: ${long.chars}`);
    assert.deepEqual({cut: long.cut, h: long.h, inside: long.inside, sideways: long.sideways}, {cut: true, h: 36, inside: true, sideways: 0});
    assert.equal((await desk.locator('#pe-dialog').boundingBox())!.width, width, 'the editor keeps its width');
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule="sr_long"] [data-sr-idle]`).getAttribute('aria-label'), 'Not acting: Tell needs a timer');
    await desk.evaluate(() => stageRulesEditorLoad('eng'));
    await desk.locator(`${SEC()} [data-sr-rule="sr_long"]`).waitFor({state: 'detached'});

    // While here's + Rule starts with that phase's When, a timer first, and offers only the When that act there; a timer
    // keeps all four Thens. Cancel gives its + Rule the focus back.
    const form = desk.locator('#sr-panel form[data-sr-form]');
    const values = (sel: string) => form.locator(sel).evaluateAll(os => os.map(o => (o as HTMLOptionElement).value));
    await desk.locator(`${PH('here')} [data-sr-add]`).click();
    await form.waitFor();
    assert.deepEqual(await values('[name=event] option'), ['timer', 'bot_snoozes', 'owner_changes'], 'While here offers its own When');
    assert.equal(await form.locator('[name=event]').inputValue(), 'timer');
    assert.equal(await form.locator('[data-sr-timer]').isVisible(), true, 'the timer shows');
    assert.deepEqual(await values('[name=then] option'), ['block', 'tell_manager', 'tell_owner', 'move_back']);
    assert.equal(await form.locator('[data-sr-thenline]').isHidden(), true);
    await desk.locator('#sr-panel form [data-sr-back]').last().click();
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-sr-phase="here"] [data-sr-add]') ?? false);

    // On entering's + Rule or action lists what the stage can take: a Rule, a Limit and the two actions (another
    // pipeline exists to open a linked task in). A second click on the button closes it, the focus back on it.
    assert.deepEqual(await enterPicks(desk), ['Rule', 'Limit', 'Open a linked task in…', 'Add its PRs to the merge queue']);
    assert.equal(await desk.locator(ENTER_ADD).getAttribute('aria-expanded'), 'true');
    await desk.locator(ENTER_ADD).click();
    await desk.waitForFunction(() => !document.querySelector('#pe-enter-pick:popover-open'));
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, ENTER_ADD);
    // Limit: a row of the draft, "At most 3 tasks at once", its small editor open in place with the number chosen; a
    // number typed says itself on the row at once. Nothing is saved yet.
    await enterPicks(desk);
    await desk.locator('#pe-enter-pick [data-pe-enter-pick="limit"]').click();
    const limitBox = desk.locator(`${ENTER} [data-pe-enter-row="limit"] [data-pe-sf="task_limit"]`);
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-pe-sf="task_limit"]') ?? false);
    assert.deepEqual(await enterRows(desk), ['limit: At most 3 tasks at once']);
    assert.equal(await desk.locator(`${ENTER} [data-pe-enter-row="limit"] .sr-open`).getAttribute('aria-expanded'), 'true');
    assert.equal(await desk.locator(`${ENTER} [data-pe-enter-row="limit"] .sr-then .nav-icon`).getAttribute('data-icon'), 'vertical_align_top', 'its own leading mark');
    await limitBox.fill('5');
    assert.deepEqual(await enterRows(desk), ['limit: At most 5 tasks at once']);
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {}}, 'the Limit is the stage\'s task_limit, as the field stored it');
    // A click on the row closes its editor, the row keeping the focus; a Limit set is no longer offered.
    await desk.locator(`${ENTER} [data-pe-enter-row="limit"] .sr-open`).click();
    assert.equal(await limitBox.count(), 0, 'closed');
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-pe-enter-row="limit"] .sr-open') ?? false);
    assert.deepEqual(await enterPicks(desk), ['Rule', 'Open a linked task in…', 'Add its PRs to the merge queue'], 'a Limit set is not offered again');

    // Rule starts with On = this stage and When = a task enters, offering only the When that act as a task enters; the
    // Limit typed, not saved yet, stays in the draft and on its row.
    await desk.locator('#pe-enter-pick [data-pe-enter-pick="rule"]').click();
    await form.waitFor();
    assert.equal(await form.locator('[name=on]').inputValue(), 'stage', 'On starts as this stage');
    assert.match(await form.locator('[name=on] option[value=stage]').innerText(), /Build/);
    assert.deepEqual(await values('[name=event] option'), ['enters', 'enters,owner_changes'], 'On entering offers its own When');
    assert.equal(await form.locator('[name=event]').inputValue(), 'enters');
    assert.equal(await form.locator('[data-sr-costline]').innerText(), 'Cost: free');
    await form.locator('[name=jev]').fill('Is it blocked?');
    assert.equal(await form.locator('[data-sr-costline]').innerText(), 'Cost: one Jev call per event', 'a Jev question costs a call');
    await form.locator('[name=jev]').fill('');
    assert.equal(await form.locator('[data-sr-timer]').isHidden(), true, 'no timer unless When is a timer');
    await form.locator('[name=name]').fill('Owner on entry');
    // An action When offers Block alone, and says why: Tell and Move back need a timer in step 1.
    assert.deepEqual(await values('[name=then] option'), ['block']);
    assert.equal(await form.locator('[data-sr-thenline]').innerText(), `${NEEDS_TIMER}.`);
    // Owner not on team with no team named: the pipeline manager's team.
    await form.locator('[name=owner_check]').selectOption('not_on');
    await form.locator('[type=submit]').click();
    // It lands in On entering, where it acts.
    await desk.locator(`${PH('enter')} [data-sr-rule="sr_new"]`).waitFor();
    // the form closes and the saved rule's row has the focus, in the editor as it was
    await desk.waitForFunction(() => document.activeElement?.closest('#pe-dialog [data-sr-rule]')?.getAttribute('data-sr-rule') === 'sr_new');
    assert.equal(await desk.locator('#sr-panel').isVisible(), false, 'the form closed');
    assert.ok(await inView(desk, `${SEC()} [data-sr-rule="sr_new"]`), 'the new row is in view');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green'], 'adding a rule keeps the Goal typed');
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {}}, 'adding a rule keeps the unsaved Limit');
    // one list: the Limit row, then the rule acting as a task enters, then the add button
    assert.deepEqual(await desk.locator(`${ENTER} :is([data-pe-enter-row], [data-sr-rule], [data-pe-enter-add])`).evaluateAll(els =>
      els.map(e => (e as HTMLElement).dataset['peEnterRow'] ?? (e as HTMLElement).dataset['srRule'] ?? 'add')), ['limit', 'sr_new', 'add']);
    const added = served.writes.at(-1)!;
    assert.equal(added.path, '/api/v2/stage-rules');
    assert.deepEqual([added.body['pipeline'], added.body['stages'], added.body['event'], added.body['then'], added.body['name']],
      ['eng', ['build'], 'enters', 'block', 'Owner on entry']);
    assert.deepEqual(added.body['cond'], {owner_not_on_team: ''});
    assert.deepEqual(await phaseRules(desk, 'enter'), ['sr_new']);
    assert.deepEqual(await phaseRules(desk, 'here'), ['sr_owner', 'sr_quiet', 'sr_snooze']);
    await waitCount(desk, 'build', '2 rules of its own');
    assert.equal(await count(desk, 'spec'), '1 rule of its own', 'a rule on Build is not counted on Spec');

    // A click on the row opens it for editing, with every When (a rule may move to another phase); Edit sends the
    // revision read.
    await desk.locator(`${SEC()} [data-sr-rule="sr_new"] .sr-open`).click();
    await form.waitFor();
    assert.ok((await values('[name=event] option')).includes('timer'), 'editing offers every When');
    await form.locator('[name=name]').fill('Owner on entry, checked');
    const before = served.writes.length;
    await form.locator('[type=submit]').click();
    await desk.locator('#sr-panel').waitFor({state: 'hidden'});
    for (let i = 0; i < 100 && served.writes.length === before; i++) await desk.waitForTimeout(20);
    const edited = served.writes.at(-1)!;
    assert.equal(edited.path, '/api/v2/stage-rules/sr_new');
    assert.equal(edited.body['revision'], 1);
    await desk.waitForFunction(() => document.activeElement?.closest('#pe-dialog [data-sr-rule]')?.getAttribute('data-sr-rule') === 'sr_new');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green'], 'editing a rule keeps the Goal typed');

    // Retire, in ⋯, asks first (accepted here) and sends the revision read.
    await desk.locator(`${SEC()} [data-sr-rule="sr_new"] [data-sr-more]`).click();
    await desk.locator('#sr-menu:popover-open [data-sr-retire]').click();
    await desk.locator(`${SEC()} [data-sr-rule="sr_new"]`).waitFor({state: 'detached'});
    assert.deepEqual(served.writes.at(-1), {path: '/api/v2/stage-rules/sr_new/retire', body: {revision: 2}});
    // the next row in its phase has the focus; it was the only rule there, so On entering's add button has it
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, ENTER_ADD);
    assert.deepEqual(await phaseRules(desk, 'enter'), [], 'no rule acts as a task enters now');
    assert.equal(await desk.locator(`${PH('enter')} > .sr-sec-body > *`).count(), 0, 'no empty-state words');
    await waitCount(desk, 'build', '1 rule of its own');
    assert.deepEqual(await goalNow(desk), ['Keep the build green', 'Keep the build green'], 'retiring a rule keeps the Goal typed');
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {}}, 'retiring a rule keeps the unsaved Limit');

    // An action: Add its PRs to the merge queue is a row at once (nothing to choose), the row focused; the stage stores
    // one action, so neither is offered again, and with the Limit set the button adds a Rule alone.
    await enterPicks(desk);
    await desk.locator('#pe-enter-pick [data-pe-enter-pick="queue"]').click();
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-pe-enter-row="act"] .sr-open') ?? false);
    assert.deepEqual(await enterRows(desk), ['limit: At most 5 tasks at once', 'act: Add its PRs to the merge queue']);
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {act: 'github.queue'}}, 'stored as before');
    assert.equal((await desk.locator(ENTER_ADD).innerText()).trim(), '+ Rule', 'only a Rule left to add');
    assert.deepEqual(await enterPicks(desk), ['Rule']);
    await desk.keyboard.press('Escape');
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, ENTER_ADD);
    // The action's row opens its editor: the action (no "Do nothing") and, for a linked task, the pipeline; the row says
    // the pipeline picked.
    await desk.locator(`${ENTER} [data-pe-enter-row="act"] .sr-open`).click();
    const kind = desk.locator(`${ENTER} [data-pe-sf="on_enter_kind"]`);
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-pe-sf="on_enter_kind"]') ?? false);
    assert.deepEqual(await kind.locator('option').evaluateAll(os => os.map(o => [(o as HTMLOptionElement).value, o.textContent])),
      [['file_into', 'Open a linked task in…'], ['act:github.queue', 'Add its PRs to the merge queue']]);
    await kind.selectOption('file_into');
    assert.deepEqual(await enterRows(desk), ['limit: At most 5 tasks at once', 'act: Open a linked task in Product']);
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {file_into: 'prod'}}, 'stored as before: {file_into: id}');
    assert.deepEqual(await desk.locator(`${ENTER} [data-pe-sf="on_enter"] option`).allTextContents(), ['Product'], 'the other pipelines');
    await desk.waitForFunction(() => document.activeElement?.matches('#pe-dialog [data-pe-sf="on_enter_kind"]') ?? false);
    await kind.selectOption('act:github.queue');
    assert.deepEqual(await draftStage(desk), {task_limit: 5, on_enter: {act: 'github.queue'}});
    // No rule's change saved the stage; its Save does, with the Goal, the Limit and the action, and no rule in it.
    assert.equal(served.writes.filter(w => w.path === '/api/v2/pipelines/eng').length, 0, 'the rules saved on their own');
    await desk.locator('#pe-foot [data-pe-save]').click();
    for (let i = 0; i < 100 && !served.writes.some(w => w.path === '/api/v2/pipelines/eng'); i++) await desk.waitForTimeout(20);
    type SavedStage = {id: string; purpose: string; task_limit: number | null; on_enter: unknown};
    const savedBuild = (n: number) => (served.writes.filter(w => w.path === '/api/v2/pipelines/eng')[n]!.body as {stages: SavedStage[]}).stages.find(s => s.id === 'build');
    assert.equal(savedBuild(0)?.purpose, 'Keep the build green');
    assert.equal(savedBuild(0)?.task_limit, 5, 'the Limit is saved as the field saved it');
    assert.deepEqual(savedBuild(0)?.on_enter, {act: 'github.queue'}, 'Add its PRs to the merge queue is stored as before');
    assert.deepEqual(JSON.stringify(served.writes.find(w => w.path === '/api/v2/pipelines/eng')!.body).match(/.{30}"sr_.{30}/g), null, 'no rule in the stage\'s save');
    // Remove, in a row's editor, takes it off; the add button has the focus and offers it again. Saved: no limit, {}.
    await desk.locator(`${ENTER} [data-pe-enter-row="limit"] .sr-open`).click();
    await desk.locator(`${ENTER} [data-pe-row-x="limit"]`).click();
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, ENTER_ADD);
    await desk.locator(`${ENTER} [data-pe-enter-row="act"] .sr-open`).click();
    await desk.locator(`${ENTER} [data-pe-row-x="act"]`).click();
    await desk.waitForFunction(sel => document.activeElement?.matches(sel) ?? false, ENTER_ADD);
    assert.deepEqual(await enterRows(desk), []);
    assert.deepEqual(await enterPicks(desk), ['Rule', 'Limit', 'Open a linked task in…', 'Add its PRs to the merge queue'], 'offered again');
    await desk.keyboard.press('Escape');
    await desk.locator('#pe-foot [data-pe-save]').click();
    for (let i = 0; i < 100 && served.writes.filter(w => w.path === '/api/v2/pipelines/eng').length < 2; i++) await desk.waitForTimeout(20);
    assert.deepEqual([savedBuild(1)?.task_limit, savedBuild(1)?.on_enter], [null, {}], 'removed: no limit, and {} as "do nothing" was');
    await desk.locator('#pe-dialog [data-pe-close]').click();
    await desk.locator('#pe-dialog').waitFor({state: 'detached'});

    // The whole pipeline's editor: each stage has the same phases with their rules, its own first.
    await desk.evaluate(() => openPipelineEditor('eng'));
    await desk.locator(`${SEC('spec')} [data-sr-rule]`).first().waitFor({state: 'attached'});
    assert.deepEqual(await desk.locator('#pe-dialog [data-sr-section]').evaluateAll(els => els.map(e => `${(e as HTMLElement).dataset['srSection']} ${(e as HTMLElement).dataset['srPhase']}`)),
      ['spec enter', 'spec here', 'spec leave', 'spec always', 'build enter', 'build here', 'build leave', 'build always']);
    assert.deepEqual(await phaseRules(desk, 'enter', 'spec'), ['sr_spec'], 'a task entering: On entering');
    assert.deepEqual(await phaseRules(desk, 'here', 'spec'), ['sr_quiet', 'sr_snooze'], 'the shared rules under each stage');
    assert.equal(await desk.evaluate(() => { const ids = [...document.querySelectorAll('#pe-dialog [id]')].map(e => e.id); return ids.length - new Set(ids).size; }), 0, 'no id twice');
    await desk.locator('#pe-dialog [data-pe-close]').click();

    // Edit on an old rule that never acts (A task enters → Tell the manager) keeps no Then picked: Save sends nothing
    // until the person picks, so a rename never turns it into a Block that clears every entering task's owner.
    await desk.locator('[data-sr-open="spec"]').first().click();
    await desk.locator(`${SEC('spec')} [data-sr-rule="sr_spec"] [data-sr-more]`).click();
    await desk.locator('#sr-menu:popover-open [data-sr-edit]').click();
    assert.equal(await form.locator('[name=then]').inputValue(), '', 'no Then is picked for the person');
    assert.deepEqual(await form.locator('[name=then] option:not([disabled])').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value)), ['block']);
    const writes = served.writes.length;
    await form.locator('[name=name]').fill('Spec has a page (renamed)');
    await form.locator('[type=submit]').click();
    assert.equal(await form.isVisible(), true, 'the form stays open');
    assert.equal(served.writes.length, writes, 'Save sent nothing');
    // A timer offers all four again, still with none picked.
    await form.locator('[name=event]').selectOption('timer');
    assert.equal(await form.locator('[name=then]').inputValue(), '');
    assert.equal(await form.locator('[name=then] option:not([disabled])').count(), 4);
    await desk.locator('#sr-panel [data-sr-close]').click();
    await desk.waitForFunction(() => document.activeElement?.closest('#pe-dialog [data-sr-rule]')?.getAttribute('data-sr-rule') === 'sr_spec');
    await desk.locator('#pe-dialog [data-pe-close]').click();

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
    // whose While here lists the shared rule with its mark.
    for (const r of served.rules) if (r.id === 'sr_owner' || r.id === 'sr_spec') r.retired = true;
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    await desk.reload();
    await desk.locator('[data-pv-col="build"] [data-sr-open][hidden]').waitFor({state: 'attached'});
    assert.equal(await desk.locator('[data-pv-col="spec"] [data-sr-open]').isHidden(), true, 'no mark without rules of its own');
    assert.equal(await desk.locator('[data-pv-col="build"] [data-sr-open]').isVisible(), false);
    assert.equal(await count(desk, 'build'), 'None of its own', 'its name still says so');
    assert.equal(await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').getAttribute('title'), 'Edit Build', 'the edit entry is the stage name');
    await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').click();
    await desk.locator(`${PH('here')} [data-sr-rule]`).first().waitFor();
    assert.deepEqual(await phaseRules(desk, 'here'), ['sr_snooze']);
    assert.equal(await desk.locator(`${PH('here')} [data-sr-rule="sr_snooze"] [data-sr-shared]`).getAttribute('aria-label'), 'In every pipeline');
    assert.deepEqual(await phaseRules(desk, 'enter'), []);
    assert.equal(await desk.locator(`${SEC()} [data-sr-add]`).count(), 1, 'While here offers + Rule');
    assert.equal(await desk.locator(ENTER_ADD).count(), 1, 'On entering offers + Rule or action');
    await desk.locator('#pe-dialog [data-pe-close]').click();

    // No rule applies anywhere: still no mark; each phase that can have one offers only + Rule, with no empty text; the
    // built-in line still ends the stage.
    for (const r of served.rules) r.retired = true;
    await desk.reload();
    await desk.locator('[data-pv-col="build"] [data-sr-open][hidden]').waitFor({state: 'attached'});
    await desk.locator('[data-pv-col="build"] [data-pv-settings="build"]').click();
    await desk.locator(`${PH('here')} [data-sr-add]`).waitFor();
    assert.equal(await desk.locator(`${SEC()} [data-sr-rule]`).count(), 0);
    assert.equal(await desk.locator(`${SEC()} :is([data-sr-none], .sr-none)`).count(), 0, 'no "no rules" words');
    assert.deepEqual(await desk.locator(`${SEC()}:not([data-sr-phase="always"]) > .sr-sec-body`).evaluateAll(els => els.map(e => [...e.children].map(c => (c.textContent || '').trim()))),
      [[], ['+ Rule'], []], 'each phase: its add button alone (On entering\'s ends its list; To leave: nothing)');
    assert.deepEqual(await desk.locator(`${ENTER} .pe-list > :not([hidden])`).evaluateAll(els => els.filter(e => getComputedStyle(e).display !== 'none').map(e => (e.textContent || '').trim())),
      ['+ Rule or action'], 'On entering with nothing set: its add button alone');
    assert.equal(await desk.locator(`${PH('leave')}`).isVisible(), false, 'an empty To leave rules block takes no room');
    assert.equal((await desk.locator(`${PH('always')} [data-sr-always]`).innerText()).replace(/\s+/g, ' ').replace('›', '').trim(), "Always on, can't be changed (1)");
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
    assert.deepEqual(await phaseHeads(member), ['login On entering', 'schedule While here', 'logout To leave']);
    assert.deepEqual(await phaseRules(member, 'here'), ['sr_owner', 'sr_quiet', 'sr_snooze']);
    assert.equal(await member.locator(`${PH('here')} [data-sr-shared]`).count(), 2, 'the shared marks show read only too');
    assert.equal(await member.locator('#pe-dialog fieldset.pe-ro[disabled]').count(), 3, 'the top row and the fields of While here and To leave are read only');
    assert.equal(await member.locator('#pe-dialog fieldset [data-sr-rule]').count(), 0, 'the rules sit outside the switched-off fields');
    assert.equal(await member.locator('#pe-dialog [data-pe-sf="name"]').isDisabled(), true);
    // On entering's Limit and action: their rows, with nothing to open or remove and nothing to add
    assert.deepEqual(await enterRows(member), ['limit: At most 2 tasks at once', 'act: Add its PRs to the merge queue']);
    assert.deepEqual(await member.locator(`${ENTER} [data-pe-enter-row] .sr-open`).evaluateAll(els => els.map(e => (e as HTMLButtonElement).disabled)), [true, true]);
    assert.equal(await member.locator(`${ENTER} :is([data-pe-enter-add], [data-pe-row-x], .pe-row-ed, .sr-more)`).count(), 0);
    assert.equal(await member.locator('#pe-foot').isHidden(), true, 'no Save');
    assert.equal(await member.locator('#pe-dialog [data-pe-save], #pe-dialog [data-pe-req-add], #pe-dialog [data-pe-req-x]').count(), 0);
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
    // 44 px rows and ⋯ on a phone; a tap at ⋯'s centre is ⋯'s, and at the row's text the row's: neither covers the other.
    assert.deepEqual(await phone.locator(`${SEC()} .sr-r:not(.sr-builtin)`).evaluateAll(els => [...new Set(els.map(e => Math.round(e.querySelector('.sr-line')!.getBoundingClientRect().height)))]), [44]);
    await phone.locator(`${SEC()} [data-sr-rule="sr_owner"]`).scrollIntoViewIfNeeded();
    assert.deepEqual(await phone.locator(`${SEC()} [data-sr-rule="sr_owner"]`).evaluate(el => {
      const more = el.querySelector<HTMLElement>('.sr-more')!, open = el.querySelector<HTMLElement>('.sr-open')!, m = more.getBoundingClientRect(), o = open.getBoundingClientRect();
      const at = (x: number, y: number) => document.elementFromPoint(x, y);
      return {more: [m.width, m.height], moreHit: more.contains(at(m.left + 2, m.top + m.height / 2)) && more.contains(at(m.right - 2, m.top + m.height / 2)),
        rowHit: open.contains(at(o.right - 2, o.top + o.height / 2)) && open.contains(at(o.left + 2, o.top + o.height / 2))};
    }), {more: [44, 44], moreHit: true, rowHit: true});
    // Each phase heading's mark starts at the fields' left edge; Owner has the whole row, Expected time and Keep warm
    // share the next.
    assert.deepEqual(await phone.evaluate(() => {
      const left = (el: Element | null) => Math.round(el!.getBoundingClientRect().left), top = (sel: string) => Math.round(document.querySelector(`#pe-body ${sel}`)!.getBoundingClientRect().top);
      const field = left(document.querySelector('#pe-body [data-pe-sf="name"]'));
      const owner = document.querySelector('#pe-body [data-pe-sf="owner"]')!.closest('.pe-f')!, row = owner.parentElement!;
      return {marks: [...document.querySelectorAll('#pe-body .pe-phase-h .nav-icon')].map(i => left(i) - field),
        ownerWhole: Math.round(owner.getBoundingClientRect().width) === Math.round(row.getBoundingClientRect().width),
        sameRow: top('[data-pe-sf="expected_n"]') === top('[data-pe-sf="keep_warm_hours"]') && top('[data-pe-sf="expected_n"]') > top('[data-pe-sf="owner"]')};
    }), {marks: [0, 0, 0], ownerWhole: true, sameRow: true});
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
