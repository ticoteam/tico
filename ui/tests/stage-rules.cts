import type {Page, Route} from 'playwright';
// Offline browser check of a stage's rules (ui/app/stage-rules.ts; Stage rules spec, acceptance 8): every stage header
// on a pipeline board has a quiet rules mark, a gavel with no word and no count, named for how many rules apply there
// ("3 rules"), struck through and named "No rules" where none do (style guide, Pipeline stage); clicking Build's opens its panel
// with every rule that applies, grouped (this stage, the whole pipeline, company, built in), each with its cost. Add
// rule starts with On = Build and sends it; Edit sends the rule's revision; Retire asks first and sends it; Built in has
// no controls. A member who may not change the pipeline reads the same panel with no Add, Edit or Retire. On a phone
// the mark sits at the end of the stage tabs, a 44 px target that nothing else covers, and the panel fills the screen. Rules › Suggested says what accepting each of a
// bot's stage rules does: a new rule, a change listing what differs from the rule it rewrites, or a retire with the
// bot's reason and a Retire button; Retire and Skip send their decision. The fake server answers as
// backend/stage_rules_api.py does.
// Run: npm run build:ui && node ui/tests/stage-rules.cts
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipelineOf = (mayChange: boolean) => ({id: 'eng', name: 'Engineering', goal: '', prefix: 'E', manager_id: null, owner_id: 'human:ana',
  private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: mayChange});
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
    if (p === '/api/v2/pipelines') return json({pipelines: [pipelineOf(mayChange)]});
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

// A stage's count is the mark's tip, "3 rules", "No rules"; its name adds the stage, "Build: 3 rules".
const count = async (page: Page, sid: string) => (await page.locator(`[data-sr-open="${sid}"]`).first().getAttribute('title'))?.trim();
const groupNames = (page: Page, group: string) => page.locator(`#sr-panel [data-sr-group="${group}"] .sr-name`).allInnerTexts();
const waitCount = (page: Page, sid: string, n: string) => page.waitForFunction(([s, want]) =>
  (document.querySelector(`[data-sr-open="${s}"]`)?.getAttribute('title') ?? '').trim() === want, [sid, n] as const);

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const desk = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    const served = await serve(desk, errors, true);
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    // Each stage's count: its own rules, the whole pipeline's and the company's.
    await waitCount(desk, 'build', '3 rules');
    assert.equal(await count(desk, 'spec'), '3 rules', 'Spec counts its own rule, the pipeline\'s and the company\'s');
    // The mark: the gavel alone, no word and no count on the board; its name and tip say how many.
    const mark = desk.locator('[data-pv-col="build"] [data-sr-open]');
    assert.deepEqual(await mark.evaluate(el => ({kids: [...el.childNodes].map(n => n.nodeType === 1 ? (n as Element).className : '#text'),
      icon: el.querySelector('.nav-icon')?.textContent, title: (el as HTMLElement).title, none: el.classList.contains('sr-mark-none')})),
      {kids: ['nav-icon'], icon: 'gavel', title: '3 rules', none: false});
    // A screen reader hears whose rules: the stage, then the count (each column has one).
    assert.equal(await mark.getAttribute('aria-label'), 'Build: 3 rules');
    assert.equal(await desk.locator('[data-pv-col="spec"] [data-sr-open]').getAttribute('aria-label'), 'Spec: 3 rules');

    await desk.locator('[data-pv-col="build"] [data-sr-open]').click();
    await desk.locator('#sr-panel [data-sr-group="stage"] .sr-name').first().waitFor();
    assert.deepEqual(await groupNames(desk, 'stage'), ['Owner on the team'], 'This stage');
    assert.deepEqual(await groupNames(desk, 'pipeline'), ['Quiet bot tasks'], 'The whole pipeline');
    assert.deepEqual(await groupNames(desk, 'company'), ['Snooze only for a real wait'], 'Company');
    assert.deepEqual(await groupNames(desk, 'builtin'), ['Hand-off files with no assignee'], 'Built in');
    assert.equal(await desk.locator('#sr-panel [data-sr-group="builtin"] button').count(), 0, 'Built in is read-only');
    assert.deepEqual((await desk.locator('#sr-panel .sr-group h3').allTextContents()).map(x => x.replace(/\s+/g, ' ').trim()),
      ['This stage 1', 'The whole pipeline 1', 'Company 1', 'Built in 1 in code, read-only']);
    // A pill only for a rule that costs: a free rule has none.
    assert.equal(await desk.locator('#sr-panel [data-sr-rule="sr_owner"] [data-sr-cost]').count(), 0, 'no Free pill');
    assert.equal(await desk.locator('#sr-panel [data-sr-rule="sr_snooze"] [data-sr-cost]').innerText(), 'One Jev call per event');
    // A row is its name and marks, no word buttons: the row opens the rule, a quiet ⋯ ends it; Built in has no ⋯.
    assert.equal(await desk.locator('#sr-panel .sr-rule > .sr-line > button:not(.sr-open, .sr-more)').count(), 0, 'no Edit or Retire words on a row');
    assert.equal(await desk.locator('#sr-panel [data-sr-rule="sr_owner"] .sr-line > :last-child').getAttribute('data-sr-more'), 'sr_owner', '⋯ is last');
    assert.equal(await desk.locator('#sr-panel .sr-builtin [data-sr-more]').count(), 0);
    // ⋯: Edit, then after a line, last and red, Retire; Esc closes it and gives ⋯ the focus back.
    const more = desk.locator('#sr-panel [data-sr-rule="sr_owner"] [data-sr-more]');
    await more.click();
    const pop = desk.locator('#sr-menu:popover-open');
    await pop.waitFor();
    assert.deepEqual(await pop.locator('.tl-menu > *').evaluateAll(els => els.map(e => e.getAttribute('role') === 'separator' ? '—' : (e.textContent || '').trim())), ['Edit', '—', 'Retire']);
    const red = await desk.evaluate(() => { const p = document.createElement('i'); p.style.color = 'var(--fail)'; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; });
    assert.equal(await pop.locator('[data-sr-retire]').evaluate(el => getComputedStyle(el).color), red, 'Retire is red');
    assert.equal(await more.getAttribute('aria-expanded'), 'true');
    await desk.keyboard.press('Escape');
    await desk.locator('#sr-menu:popover-open').waitFor({state: 'detached'}).catch(() => {});
    await desk.waitForFunction(() => !document.querySelector('#sr-menu:popover-open'));
    await desk.waitForFunction(() => document.activeElement?.getAttribute('data-sr-more') === 'sr_owner');
    assert.equal(await desk.locator('#sr-panel').isVisible(), true, 'Esc closed the menu, not the panel');
    assert.match(await desk.locator('#sr-panel [data-sr-rule="sr_owner"] .sr-what').innerText(), /^The owner changes → owner not on engineering → Block$/);
    assert.match(await desk.locator('#sr-panel [data-sr-rule="sr_quiet"] .sr-what').innerText(), /^6 h quiet → Tell the manager$/);

    // Add rule starts with On = this stage.
    await desk.locator('#sr-panel [data-sr-add]').click();
    const form = desk.locator('#sr-panel form[data-sr-form]');
    assert.equal(await form.locator('[name=on]').inputValue(), 'stage', 'On starts as this stage');
    assert.match(await form.locator('[name=on] option[value=stage]').innerText(), /Build/);
    assert.equal(await form.locator('[data-sr-costline]').innerText(), 'Cost: free');
    await form.locator('[name=jev]').fill('Is it blocked?');
    assert.equal(await form.locator('[data-sr-costline]').innerText(), 'Cost: one Jev call per event', 'a Jev question costs a call');
    await form.locator('[name=jev]').fill('');
    assert.equal(await form.locator('[data-sr-timer]').isHidden(), true, 'no timer unless When is a timer');
    await form.locator('[name=name]').fill('Sent back twice');
    // An action When offers Block alone, and says why: Tell and Move back need a timer in step 1.
    assert.deepEqual(await form.locator('[name=then] option').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value)), ['block']);
    assert.equal(await form.locator('[data-sr-thenline]').innerText(), `${NEEDS_TIMER}.`);
    // A timer keeps all four.
    await form.locator('[name=event]').selectOption('timer');
    assert.deepEqual(await form.locator('[name=then] option').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value)),
      ['block', 'tell_manager', 'tell_owner', 'move_back']);
    assert.equal(await form.locator('[data-sr-thenline]').isHidden(), true);
    await form.locator('[name=then]').selectOption('tell_owner');
    // Owner not on team with no team named: the pipeline manager's team.
    await form.locator('[name=owner_check]').selectOption('not_on');
    await form.locator('[type=submit]').click();
    await desk.locator('#sr-panel [data-sr-rule="sr_new"]').waitFor();
    const added = served.writes.at(-1)!;
    assert.equal(added.path, '/api/v2/stage-rules');
    assert.deepEqual([added.body['pipeline'], added.body['stages'], added.body['then'], added.body['name']], ['eng', ['build'], 'tell_owner', 'Sent back twice']);
    assert.deepEqual(added.body['cond'], {owner_not_on_team: ''});
    assert.deepEqual(await groupNames(desk, 'stage'), ['Owner on the team', 'Sent back twice']);
    await waitCount(desk, 'build', '4 rules');
    assert.equal(await count(desk, 'spec'), '3 rules', 'a rule on Build is not counted on Spec');

    // A click anywhere on the row opens it for editing; Edit sends the revision read.
    const newRow = desk.locator('#sr-panel [data-sr-rule="sr_new"]'), rowBox = (await newRow.boundingBox())!;
    // a point on its sentence, under the name: the row's button reaches it
    await newRow.click({position: {x: rowBox.width / 2, y: rowBox.height - 6}});
    await form.locator('[name=name]').fill('Sent back twice or more');
    await form.locator('[type=submit]').click();
    await desk.locator('#sr-panel [data-sr-rule="sr_new"] .sr-name', {hasText: 'or more'}).waitFor();
    const edited = served.writes.at(-1)!;
    assert.equal(edited.path, '/api/v2/stage-rules/sr_new');
    assert.equal(edited.body['revision'], 1);

    // Retire, in ⋯, asks first (accepted here) and sends the revision read.
    await desk.locator('#sr-panel [data-sr-rule="sr_new"] [data-sr-more]').click();
    await desk.locator('#sr-menu:popover-open [data-sr-retire]').click();
    await desk.locator('#sr-panel [data-sr-rule="sr_new"]').waitFor({state: 'detached'});
    assert.deepEqual(served.writes.at(-1), {path: '/api/v2/stage-rules/sr_new/retire', body: {revision: 2}});
    await waitCount(desk, 'build', '3 rules');
    await desk.locator('#sr-panel [data-sr-close]').click();
    assert.equal(await desk.locator('#sr-panel').isVisible(), false);

    // Edit on an old rule that never acts (A task enters → Tell the manager) keeps no Then picked: Save sends nothing
    // until the person picks, so a rename never turns it into a Block that clears every entering task's owner.
    await desk.locator('[data-sr-open="spec"]').first().click();
    await desk.locator('#sr-panel [data-sr-rule="sr_spec"] [data-sr-more]').click();
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

    // Rules › Pipelines and stages: a rule saved before Tell needed a timer is listed as not acting, with why.
    await desk.goto('http://tico-ui.test/#/rules/stages');
    await desk.locator('[data-stage-rule="sr_spec"]').waitFor();
    assert.equal(await desk.locator('[data-stage-rule="sr_spec"] [data-sr-idle]').innerText(), `Not acting: ${NEEDS_TIMER}`);
    assert.equal(await desk.locator('[data-stage-rule="sr_owner"] [data-sr-idle]').count(), 0, 'a Block on an action acts');

    // Rules › Suggested: what accepting each of a bot's stage rules does.
    await desk.goto('http://tico-ui.test/#/rules/suggested');
    const sg = desk.locator('[data-group="stage-rules"]');
    await sg.locator('[data-stage-rule]').first().waitFor();
    const row = (id: string) => sg.locator(`[data-stage-rule="${id}"]`);
    assert.deepEqual(await sg.locator('.sr-name').allTextContents(), ['New: Ask before Move back', 'Change to Owner on the team', 'Retire: Quiet bot tasks']);
    assert.equal(await row('sr_sg_new').locator('[data-sr-decide="accept"]').innerText(), 'Accept');
    // A suggestion of a shape that would never act says so before Accept is pressed.
    assert.equal(await row('sr_sg_new').locator('[data-sr-idle]').innerText(), `Not acting: ${NEEDS_TIMER}`);
    // A change lists what differs from the rule as it is now, and only that.
    assert.deepEqual(await row('sr_sg_change').locator('[data-sr-diff] li').allTextContents(), ['Then Block → Tell the manager']);
    assert.equal(await row('sr_sg_change').locator('[data-sr-decide="accept"]').innerText(), 'Accept change');
    // A retire says so, with the bot's reason, and its button is Retire.
    assert.equal(await row('sr_sg_retire').locator('[data-sr-why]').innerText(), 'Why: Covered by the Late rule.');
    assert.equal(await row('sr_sg_retire').locator('[data-sr-decide="accept"]').innerText(), 'Retire');
    await row('sr_sg_retire').locator('[data-sr-decide="accept"]').click();
    await row('sr_sg_retire').waitFor({state: 'detached'});
    assert.equal(served.writes.at(-1)?.path, '/api/v2/stage-rules/sr_sg_retire/accept');
    assert.equal(served.rules.find(x => x.id === 'sr_quiet')?.retired, true);
    await row('sr_sg_change').locator('[data-sr-decide="skip"]').click();
    await row('sr_sg_change').waitFor({state: 'detached'});
    assert.equal(served.writes.at(-1)?.path, '/api/v2/stage-rules/sr_sg_change/skip');

    // No rule applies anywhere: each stage's mark is the struck-through gavel, named "No rules", and still opens the
    // stage's panel in one click (where a rule is added).
    for (const r of served.rules) r.retired = true;
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    await desk.reload();
    await waitCount(desk, 'build', 'No rules');
    assert.equal(await count(desk, 'spec'), 'No rules');
    assert.deepEqual(await desk.locator('[data-pv-col="build"] [data-sr-open]').evaluate(el =>
      ({none: el.classList.contains('sr-mark-none'), title: (el as HTMLElement).title, text: el.querySelector('.nav-icon')?.textContent})),
      {none: true, title: 'No rules', text: 'gavel'});
    assert.equal(await desk.locator('[data-pv-col="build"] [data-sr-open]').getAttribute('aria-label'), 'Build: No rules');
    await desk.locator('[data-pv-col="build"] [data-sr-open]').click();
    await desk.locator('#sr-panel [data-sr-group="stage"] .sr-none').waitFor();
    assert.equal(await desk.locator('#sr-panel [data-sr-add]').count(), 1, 'a rule can be added from there');
    await desk.locator('#sr-panel [data-sr-close]').click();

    // A member reads the same rules and changes none.
    const member = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(member, errors, false);
    await member.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=list');
    await waitCount(member, 'build', '3 rules');
    await member.locator('.pv-group[aria-label="Build"] [data-sr-open]').click();
    await member.locator('#sr-panel [data-sr-group="stage"] .sr-name').first().waitFor();
    assert.equal(await member.locator('#sr-panel [data-sr-add], #sr-panel [data-sr-edit], #sr-panel [data-sr-retire], #sr-panel [data-sr-more]').count(), 0);
    assert.match(await member.locator('#sr-panel .sr-who').innerText(), /owner, its manager and Admins/);

    // A phone: the button beside the stage tabs, and the panel fills the screen.
    const phone = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC',
      isMobile: true, hasTouch: true});
    await serve(phone, errors, true);
    await phone.goto('http://tico-ui.test/#/tasks?pipeline=eng');
    await phone.locator('.pv-is-phone').waitFor();
    await phone.locator('[data-pv-tab="build"]').click();
    await waitCount(phone, 'build', '3 rules');
    const button = await phone.locator('.pv-phone-head [data-sr-open="build"]').boundingBox();
    assert.ok(button && button.height >= 44 && button.width >= 44, 'the phone mark is a 44 px target');
    // Its whole target is its own: a tap near each corner (inside its rounding) and at the centre lands on the mark, not
    // on a stage tab beside it.
    assert.deepEqual(await phone.locator('.pv-phone-head [data-sr-open="build"]').evaluate(el => {
      const r = el.getBoundingClientRect();
      return [[r.left + 6, r.top + 6], [r.right - 6, r.top + 6], [r.left + 6, r.bottom - 6], [r.right - 6, r.bottom - 6], [r.left + r.width / 2, r.top + r.height / 2]]
        .map(([x, y]) => el.contains(document.elementFromPoint(x!, y!)));
    }), [true, true, true, true, true]);
    await phone.locator('.pv-phone-head [data-sr-open="build"]').click();
    await phone.locator('#sr-panel [data-sr-group="stage"] .sr-name').first().waitFor();
    const box = await phone.locator('#sr-panel').boundingBox();
    assert.ok(box && box.width >= 389, `the panel fills the phone's width (${box?.width})`);
    const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 390, `the phone page does not scroll sideways (${wide} px)`);

    assert.deepEqual(errors, []);
    console.log('stage rules: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
