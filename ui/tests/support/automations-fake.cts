// A stand-in hub for the Automations page scripts (ui/tests/automations-*.cjs): every /api/v2/automations route in
// redesign CONTRACT §2 over ui/tests/fixtures/redesign-automations.json (the Acme example), the BotOps chat,
// and the boot stubs. Writes change the copy a page reads back and are recorded in `writes`.
// A flow save (steps, trigger or destinations) is a new version; a viewer without edit rights gets 403; a built-in's delete gets 409.
import type {Browser, Page} from 'playwright';
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./page.cts') as import('./page.cts').Api;
const {unrouted} = require('./routes.cts') as import('./routes.cts').Api;
const uiDir = path.join(__dirname, '../..');
const FIXTURE = path.join(__dirname, '../fixtures/redesign-automations.json');

export interface Viewport {
  width: number;
  height: number;
}

type Json = Record<string, unknown>;

// One write the fake hub received (anything but GET). `body` is the parsed JSON the page sent.
export interface Write {
  method: string;
  path: string;
  body: Json | null;
}

// The fixture a spec's tweak edits before the fake hub serves it. Rows are plain objects: a spec that reads one field
// casts the row to its own shape.
export interface AutomationsFixture {
  me: {id: string; name: string; [key: string]: unknown};
  people: Record<string, unknown>[];
  automations: Record<string, unknown>[];
  versions: Record<string, Record<string, unknown>[]>;
  [key: string]: unknown;
}

// A flow step as the hub stores it: `type` picks the shape; the fields a step reads are typed here, the rest stay unknown.
interface Step extends Json {
  id?: string;
  type?: string;
  prompt?: unknown;
  choose?: {to?: unknown[]; default?: unknown};
  questions?: Record<string, Json>;
  rules?: unknown[];
  steps?: unknown;
  then?: unknown;
  else?: unknown;
  paths?: unknown;
  otherwise?: {steps?: unknown};
  set?: string;
}

// An automation row. `hold` is the run guard's hold: held runs wait behind it until released or dropped.
interface Auto extends Json {
  id: string;
  version: number;
  trigger: Json;
  steps: Step[];
  destinations?: Json[];
  hold?: {held?: unknown};
}

// A saved version: its number, who made it, and whether a person has yet to decide on it (pending).
interface Version extends Json {
  n: number;
  status?: string;
  flow?: Json;
}

// A run, as the list, the overview and the Maintenance or Heartbeat logs read it.
interface Run extends Json {
  id: string;
  started_at: string;
  status?: string;
  steps?: Json[];
}

// Maintenance and the Heartbeat as backend/maintenance.py and backend/heartbeat.py list them. Specs read and change
// these rows (db.maint, db.heart); the fake's PATCH handlers write fields by name, hence the Json index signature.
export interface MaintStep extends Json {
  key: string;
  name: string;
  enabled: boolean;
  frequency: string;
  custom_every: number | null;
  custom_unit: string | null;
  max_bots: number | null;
  consent?: boolean;
}
export interface MaintRun extends Json {
  id: string;
  occurrence_key: string | null;
  trigger: string;
  step: string | null;
  started_at: string;
  finished_at: string | null;
  heartbeat_at: string;
  ok: boolean | null;
  result: string | null;
  cost_usd: number;
  steps: Json[];
}
export interface Maint extends Json {
  settings: Json;
  cap_env: number;
  steps: MaintStep[];
  runs: MaintRun[];
  live: MaintRun | null;
}
export interface HeartStep extends Json {
  key: string;
  name: string;
  every: string;
  switch: boolean;
  on: boolean;
}
export interface HeartBeat extends Json {
  id: string;
  started_at: string;
  total_ms: number;
  did: number;
  failed: number;
  steps: {key: string; name: string; text: string | null; ms: number; error: string | null}[];
  repeats?: number;
  last_repeated_at?: string | null;
}
export interface Heart extends Json {
  interval_s: number;
  last_beat_at: string;
  beats_24h: number;
  steps: HeartStep[];
  beats: HeartBeat[];
}

// The AutomationsDb a spec reads: what the fake hub serves and the rows a write changes.
export interface AutomationsDb {
  list: Auto[];
  versions: Record<string, Version[]>;
  runs: Record<string, Run[]>;
  conv: {id: string; kind: string; title: string} | null;
  messages: Json[];
  held: Record<string, {wait: Promise<void>; fail: boolean}>;
  // Set when the page is opened with `maintenance` and `heartbeat` (see openAutomations).
  maint?: Maint;
  heart?: Heart;
}

export interface OpenAutomationsOptions {
  viewport?: Viewport;
  hash?: string;
  viewer?: string;
  tweak?: ((fx: AutomationsFixture) => void) | null;
  draft?: boolean;
  proposal?: boolean;
  system?: boolean;
  maintenance?: boolean;
  heartbeat?: boolean;
  // A script's own routes, set before the page loads: they answer first, and route.fallback() hands a path back here.
  routes?: ((page: Page) => Promise<void>) | null;
}

export interface AutomationsPage {
  page: Page;
  errors: string[];
  writes: Write[];
  db: AutomationsDb;
  me: Json;
  // Holds the request for `id` (an automation, or 'maintenance', or the Heartbeat) until the returned release() runs.
  hold: (id: string, fail?: boolean) => () => void;
}

interface Proposal {
  rule: Json;
  summary: string;
}

function fixture<T = unknown>(name: string): T { return JSON.parse(fs.readFileSync(name, 'utf8')) as T; }

const PHONE: Viewport = {width: 390, height: 844}, DESKTOP: Viewport = {width: 1440, height: 900};
function clone<T>(x: T): T { return JSON.parse(JSON.stringify(x)) as T; }
// A row the hub holds, typed as an automation (the fixture's JSON, as the hub stores it).
const asAuto = (row: Json): Auto => row as Auto;
// The hub stores a flow as json.dumps(flow, sort_keys=True) (backend/automations.py), so every object's keys come back
// sorted, at any depth; arrays keep their order. A flow saved here, and one loaded from the fixture, see the same.
const sortKeys = (x: unknown): unknown => Array.isArray(x) ? x.map(sortKeys)
  : x && typeof x === 'object' ? Object.fromEntries(Object.keys(x).sort().map(k => [k, sortKeys((x as Json)[k])])) : x;
const sortFlow = (a: Json): Json => {
  const out: Json = {...a};
  for (const k of ['trigger', 'steps', 'destinations']) if (out[k] !== undefined) out[k] = sortKeys(out[k]);
  return out;
};
// A version's flow: the trigger, the steps and (a board's) destinations.
const flowOf = (a: Auto): Json => ({trigger: clone(a.trigger), steps: clone(a.steps), ...(a.destinations ? {destinations: clone(a.destinations)} : {})});
// The hub's own refusals that a board save could trip (backend/automation_flow.py): a decide question needs
// instructions, a choice two options, an llm step a prompt and a choose's default one of its to; steps nest in paths
// and branches.
function flowProblem(steps: unknown): string {
  for (const s of (steps || []) as (Step | null)[]) {
    if (!s || typeof s !== 'object') continue;
    if (s.type === 'llm' && !String(s.prompt ?? '').trim()) return `${s.id}: an llm step needs a prompt`;
    if (s.type === 'llm' && s.choose && !(s.choose.to || []).includes(s.choose.default)) return `${s.id}: choose.default is one of choose.to`;
    const questions: Record<string, Json> = s.questions || {};
    if (s.type === 'decide') for (const [id, q] of Object.entries(questions)) {
      if (q?.instructions == null || (typeof q.instructions === 'string' && !q.instructions.trim())) return `${s.id}.${id}: a question needs instructions`;
      // clients/judge.py MAX_INSTRUCTIONS, counted in characters as Python counts them.
      if ([...(typeof q?.instructions === 'string' ? q.instructions : JSON.stringify(q?.instructions ?? ''))].length > 8000) return `${id}: instructions over 8000 characters`;
      if (q?.type === 'choice' && (Array.isArray(q.criteria) ? q.criteria : Object.keys((q.criteria || {}) as object)).length < 2) return `${s.id}.${id}: a choice is a criteria map of at least two options`;
    }
    const paths = Array.isArray(s.paths) ? s.paths as ({steps?: unknown} | null)[] : [];
    for (const kid of [s.steps, s.then, s.else, ...paths.map(x => x?.steps)]) {
      if (Array.isArray(kid)) { const bad = flowProblem(kid); if (bad) return bad; }
    }
  }
  return '';
}

// The inbox templates (backend/automation_templates): the listing, and a fixed flow for each. Blank is the real one's
// shape: no rules, no questions, Sort to Me or Archive. The others fill to one flow (their names aside): a rule, a
// question, and an LLM that is a draft turn as a person's or a bot's owner, and Sort, every option routed, for a group.
const TEMPLATES = [{id: 'founder', name: 'Founder', destinations: ['Me', 'Legal', 'Engineering', 'Archive']},
  {id: 'support', name: 'Support desk', destinations: ['Support', 'Engineering', 'Archive']},
  {id: 'sales', name: 'Sales', destinations: ['Sales', 'Archive']}, {id: 'blank', name: 'Blank', destinations: ['Me', 'Archive']}];
interface Dest extends Json {
  id: string;
  name: string;
}
function filledTemplate(tid: string, owner: string, inbox: string): Json {
  const [me, legal, eng, archive] = [
    {id: 'me', name: 'Me', color: 'amber', then: [{do: 'leave_for_you', args: {}}]},
    {id: 'legal', name: 'Legal', color: 'orange', then: [{do: 'create_task', args: {title: 'Legal: {{message.subject}}', owner: 'bot:support'}}]},
    {id: 'eng', name: 'Engineering', color: 'violet', then: [{do: 'create_task', args: {title: 'Bug: {{message.subject}}', owner: 'bot:eng-lead'}}]},
    {id: 'archive', name: 'Archive', color: 'gray', then: [{do: 'archive', args: {}}]},
  ] as [Dest, Dest, Dest, Dest];
  const destinations = [me, legal, eng, archive];
  const name = TEMPLATES.find(t => t.id === tid)!.name, trigger = {type: 'message', inbox};
  if (tid === 'blank') {
    const two = [me, archive];
    return {name, owner, enabled: false, trigger, destinations: two, steps: [
      {id: 'rules', type: 'filter', rules: []},
      {id: 'sort', type: 'decide', out: 'sort', questions: {route: {type: 'choice',
        instructions: 'Where does this message belong? Pick the one that fits best.', criteria: {me: 'The owner should see it', archive: 'Nothing is asked of anyone'}}}},
      {id: 'route', type: 'filter', rules: [...two.map(d => ({when: [{field: 'sort.route', op: 'is', value: d.id, min: 0.5}], to: d.id, next: 'end', stop: true})),
        {when: [], to: 'me', next: 'end', stop: true}]}]};
  }
  const to = destinations.map(d => d.id), [kind, id] = [owner.split(':')[0], owner.split(':').slice(1).join(':')];
  const steps: Json[] = [
    {id: 'rules', type: 'filter', rules: [{when: [{field: 'message.subject', op: 'matches', value: 'subpoena|lawsuit'}], match: 'all',
      then: [{do: 'label', args: {name: 'STARRED'}}], to: 'legal', next: 'end', stop: true}]},
    {id: 'ask', type: 'decide', out: 'q', questions: {bug: {type: 'noul', instructions: 'Is it a bug report?'}}},
    {id: 'route', type: 'filter', rules: [{when: [{field: 'q.bug', op: 'is', value: 'yes', min: 0.75}], to: 'eng', next: 'end', stop: true}]}];
  if (kind === 'group') steps.push(
    {id: 'draft', type: 'decide', out: 'draft', questions: {route: {type: 'choice', instructions: 'Where does this message belong?',
      criteria: Object.fromEntries(destinations.map(d => [d.id, d.name]))}}},
    {id: 'draft_route', type: 'filter', rules: [...to.map(d => ({when: [{field: 'draft.route', op: 'is', value: d, min: 0.5}], to: d, next: 'end', stop: true})),
      {when: [], to: 'me', next: 'end', stop: true}]});
  else steps.push({id: 'draft', type: 'llm', out: 'draft', as: (kind === 'person' ? 'assistant:' : 'bot:') + id,
    prompt: 'Route this message first. When unsure, choose me.', choose: {to, default: 'me', mode: 'draft'}});
  return {name, owner, enabled: false, trigger, steps, destinations};
}

// A bot's draft (`draft: true` in openAutomations): off, its version 1 pending until Turn on (CONTRACT §2).
const DRAFT: Json = {id: 'au_draft', name: 'Receipts to archive', owner_id: 'person:dana', enabled: false, builtin: false, version: 1,
  trigger: {type: 'message', inbox: 'dana@acme.example', filters: []},
  steps: [{id: 'f1', type: 'filter', enabled: true, rules: [
    {when: [{field: 'message.subject', op: 'contains', value: 'receipt'}], match: 'all', then: ['archive'], stop: true}]}]};
const DRAFT_VERSIONS: Json[] = [{n: 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
  summary: 'Archive receipts from billing'}];

// A system job's mirror (`system: true`): Tico runs it from its own code, so it reads only; the top of the org may pause it.
const SYSTEM: Json = {id: 'au_sys_still', name: 'KPI stale asks check', owner_id: 'person:dana', enabled: true, builtin: true, readonly: true,
  kind: 'system', version: 1, schedule: 'Daily at 07:00', can_edit: false, can_pause: true,
  description: 'Asks a KPI keeper for a reading when it goes out of date.',
  trigger: {type: 'event', name: 'tico.builtin.job.kpi_asks'}, steps: [], controls: {on: true},
  source: {kind: 'system_job', id: 'job:kpi_asks'}};
const systemRuns = (now = Date.now()): Run[] => Array.from({length: 8}, (_, i) => ({id: 'sys_' + (50 - i), automation_id: SYSTEM.id,
  started_at: new Date(now - (i * 3 + 1) * 3600000).toISOString(), finished_at: new Date(now - (i * 3 + 1) * 3600000 + 4000).toISOString(),
  status: i === 2 ? 'failed' : 'ok', cost: 0, steps: [], started_by: 'Schedule', outcome: i === 2 ? 'Mail down' : 'Asked 3 owners',
  summary: i === 2 ? 'Mail down' : 'Asked 3 owners'}));

// Maintenance (`maintenance: true`): backend/maintenance.py's listing at the migration's defaults, Learn's beta numbers
// (backend/learnings.py beta_stats), and 30 hourly runs, newest first: one failed at Dream, one a Run now of one step.
const MAINT_ORDER = ['collect', 'clean_up', 'check_health', 'learn', 'suggest', 'update', 'dream'];
const MAINT_NAMES: Record<string, string> = {collect: 'Collect', clean_up: 'Clean up', check_health: 'Check health', learn: 'Learn',
  suggest: 'Suggest', update: 'Update', dream: 'Dream'};
const MAINT_EVERY_RUN = ['collect', 'clean_up', 'check_health'], MAINT_BOTS = ['dream'];
function maintSteps(): MaintStep[] {
  const d: Record<string, Partial<MaintStep>> = {learn: {enabled: false, frequency: 'day'},
    suggest: {frequency: 'day'}, update: {frequency: 'day'}, dream: {frequency: 'custom', custom_every: 6, custom_unit: 'hours', max_bots: 10}};
  return MAINT_ORDER.map(key => ({key, name: MAINT_NAMES[key]!, enabled: true, frequency: 'run', custom_every: null, custom_unit: null,
    max_bots: null, ...d[key], fixed: MAINT_EVERY_RUN.includes(key),
    last_started_at: null, last_ran_at: key === 'learn' ? null : new Date(Date.now() - 3600000).toISOString(), cursor_at: null,
    ...(key === 'suggest' ? {consent: false} : {}),
    ...(key === 'learn' ? {stats: {days: 7, runs: 6, items_read: 42, items_sent: {support: 3, 'eng-lead': 2}, saved: 4, dropped: 1,
      unreported: 0, not_sent: 0, expired: 0, cost_usd: 0.18}} : {})}));
}
function maintRuns(now = Date.now()): MaintRun[] {
  return Array.from({length: 30}, (_, i) => {
    const at = now - (i + 1) * 3600000, failed = i === 2, one = i === 4;
    const line = (step: string, status: string, extra: Json = {}): Json => ({step, status, reason: '', checked: null, found: null, woke: null, cost_usd: 0,
      started_at: new Date(at).toISOString(), finished_at: new Date(at + 2000).toISOString(), ...extra});
    const steps = one ? [line('dream', 'ran', {checked: 5, found: 2, woke: 2, cost_usd: 0.05})] : [
      line('collect', 'ran', {checked: 120, found: 14}), line('clean_up', 'ran'), line('check_health', 'skipped', {reason: 'not due'}),
      line('learn', 'off'), line('suggest', 'skipped', {reason: 'Privacy consent is off'}),
      line('update', 'skipped', {reason: 'not due'}),
      line('dream', failed ? 'failed' : 'skipped', failed ? {reason: 'model timed out', cost_usd: 0.02} : {reason: 'not due'})];
    return {id: 'mrun_' + (100 - i), occurrence_key: one ? null : 'hourly:' + i, trigger: one ? 'run_now' : 'schedule', step: one ? 'dream' : null,
      started_at: new Date(at).toISOString(), finished_at: new Date(at + 9000).toISOString(), heartbeat_at: new Date(at + 9000).toISOString(),
      ok: !failed, result: failed ? '1 failed, 2 ran, 3 skipped' : one ? '1 ran' : '2 ran, 4 skipped',
      cost_usd: steps.reduce((t, x) => t + Number(x.cost_usd), 0), steps};
  });
}

// The Heartbeat (`heartbeat: true`): backend/heartbeat.py's listing with one step of each kind and Ask go-ahead's switch,
// Queue check failing now and GitHub sync's old error, and 30 beats that did something, one every 2 minutes, newest first:
// every fifth one failed, and the seventh is a failure folded three times.
const HEART_STEPS: [string, string, string, string][] = [['due_reminders', 'Due reminders', 'due', 'Every beat'], ['ask_deadlines', 'Ask go-ahead', 'due', 'Hourly'],
  ['stall_checks', 'Stall checks', 'recovery', 'Every minute'], ['queue_check', 'Queue check', 'recovery', 'Every beat'],
  ['github_sync', 'GitHub sync', 'poll', 'Every 10 s']];
function heartSteps(now = Date.now()): HeartStep[] {
  const kindNames: Record<string, string> = {due: 'Due times', recovery: 'Recovery', poll: 'Outside polls'};
  const errors: Record<string, string> = {queue_check: 'Failed: unexpected error (RuntimeError)', github_sync: 'Failed: GitHub did not answer'};
  const errorAt: Record<string, string> = {queue_check: new Date(now - 4000).toISOString(), github_sync: new Date(now - 600000).toISOString()};
  return HEART_STEPS.map(([key, name, kind, every]) => ({key, name, kind, kind_name: kindNames[kind], every_s: null, every, switch: key === 'ask_deadlines', on: true,
    can_change: false, last_run_at: new Date(now - 4000).toISOString(), last_ms: key === 'github_sync' ? 1240 : 3,
    last_result: key === 'due_reminders' ? '1 reminder sent' : 'Nothing due', last_did_at: key === 'due_reminders' ? new Date(now - 4000).toISOString() : null,
    last_did: key === 'due_reminders' ? '1 reminder sent' : null, last_error: errors[key] || null, last_error_at: errorAt[key] || null,
    failing: key === 'queue_check', runs_24h: 8640, did_24h: key === 'due_reminders' ? 24 : 0, failed_24h: key === 'queue_check' ? 6 : 0}));
}
function heartBeats(now = Date.now()): HeartBeat[] {
  return Array.from({length: 30}, (_, i) => {
    const failed = i % 5 === 1, folded = i === 6;
    const steps: HeartBeat['steps'] = folded ? [] : [{key: 'due_reminders', name: 'Due reminders', text: '1 reminder sent', ms: 3, error: null}];
    if (failed) steps.push({key: 'queue_check', name: 'Queue check', text: null, ms: 2, error: 'Failed: unexpected error (RuntimeError)'});
    return {id: 'hbt_' + String(100 - i).padStart(3, '0'), started_at: new Date(now - (i * 2 + 1) * 60000).toISOString(), total_ms: 7,
      did: folded ? 0 : 1, failed: failed ? 1 : 0, steps, repeats: folded ? 3 : 1,
      last_repeated_at: folded ? new Date(now - i * 2 * 60000).toISOString() : null};
  });
}

// 30 runs of the inbox automation, newest first: most reply, a few fail at the LLM, a few hit the spend limit.
function inboxRuns(now = Date.now()): Run[] {
  return Array.from({length: 30}, (_, i) => {
    const failed = i % 9 === 4, capped = i % 7 === 3 && !failed;
    const steps: Json[] = [
      {id: 'f1', type: 'filter', status: 'ok', outputs: {rule: null}, cost: 0},
      {id: 'dc1', type: 'decide', status: 'ok', cost: 0.004, outputs: {
        bucket: {choice: 'reply', confidence: 0.82, probabilities: {reply: 0.82, archive: 0.1, route: 0.08}},
        is_ask: {noul: 0.64}, money: {noul: 0.12}, legal: {noul: 0.03}}},
      {id: 'p1', type: 'paths', status: 'ok', outputs: {path: 0}, cost: 0},
      {id: 'l1s', type: 'llm', status: failed ? 'failed' : capped ? 'spend_cap' : 'ok', cost: failed || capped ? 0 : 0.03,
        outputs: failed ? {error: 'model timed out'} : capped ? {} : {text: 'Thanks Sam, Thursday at 3 works.'}},
    ];
    if (!failed && !capped) steps.push({id: 'a1', type: 'action', status: 'ok', outputs: {result: 'draft saved'}, cost: 0});
    return {id: 'run_' + (100 - i), automation_id: 'au_inbox', version: i < 12 ? 7 : 6, trigger_ref: 'mail:m' + i,
      started_at: new Date(now - i * 17 * 60000).toISOString(), finished_at: new Date(now - i * 17 * 60000 + 9000).toISOString(),
      status: failed ? 'failed' : 'ok', cost: steps.reduce((t, s) => t + Number(s.cost), 0), steps};
  });
}

// Listening (ui/app/listening.ts): each source's health as GET /api/v2/listening/sources lists it, by the source's id in
// its listening's trigger: [status, minutes since the last read, the last error]. A source not named here was never read.
const LISTEN_HEALTH: Record<string, [string, number, string?]> = {s_smb: ['ok', 120], s_agency: ['ok', 120], s_x: ['blocked', 180, 'Sign-in wall'],
  s_nw: ['ok', 300], s_syften: ['ok', 12], s_acme: ['ok', 60]};
// Listenings whose posts have waited unscored: the server gives them a hollow dot though every source read well.
const LISTEN_STALLED = new Set(['au_pricing']);
const LISTEN_TEXT = `Market listening · Tue Nov 10, 14:00 · 3 of 30

1. competitor  Northwind Team plan is now $15 a seat
   Page · northwind.com/pricing
2. question    Which tracker works for a 6-person agency?
   Reddit · 3 copies · reddit.com/r/agency/…
3. buyer       We're 8 people and need to replace our spreadsheet this month
   Reddit · reddit.com/r/smallbusiness/…`;
// A listening's sends: one run an hour, each carrying what was sent and the counts since the send before.
function listenRuns(now = Date.now()): Run[] {
  return Array.from({length: 6}, (_, i) => {
    const at = now - (i * 60 + 20) * 60000;
    return {id: 'lrun_' + (60 - i), automation_id: 'au_market', version: 3, trigger_ref: 'listening:' + (60 - i),
      started_at: new Date(at).toISOString(), finished_at: new Date(at + 21000).toISOString(), status: 'ok', cost: 0.06, summary: '3 sent',
      listening: {name: 'Market listening', sent_at: new Date(at).toISOString(), count: 3, waiting: 27, text: LISTEN_TEXT,
        counts: {read: 212, duplicates: 41, dropped: 136, kept: 33, sent: 3, waiting: 27, failed: 2}},
      steps: [{id: 'lp1', type: 'paths', status: 'ok', outputs: {summary: 'competitor 1 · question 1 · Otherwise 1'}, cost: 0},
        {id: 'lb1', type: 'bot_turn', status: 'ok', outputs: {}, cost: 0.06},
        {id: 'lt1', type: 'action', status: 'ok', outputs: {result: '1 task'}, cost: 0},
        {id: 'lu1', type: 'action', status: 'ok', outputs: {result: 'posted'}, cost: 0}]};
  });
}

// One page on the fake hub. viewer: the person signed in (any but the fixture's owner sees nothing editable).
// tweak(fx) changes the fixture copy before the page reads it. proposal: a bot's pending version on the inbox one.
async function openAutomations(browser: Browser, {viewport = PHONE, hash = '#/automations', viewer = 'dana', tweak = null, draft = false,
                                         proposal = false, system = false, maintenance = false, heartbeat = false, routes = null}: OpenAutomationsOptions = {}): Promise<AutomationsPage> {
  const fx = clone(fixture<AutomationsFixture>(FIXTURE));
  const proposalFx = fx.proposal as Proposal;
  if (system) fx.automations.push(clone(SYSTEM));
  if (draft) { fx.automations.push(clone(DRAFT)); fx.versions = {...fx.versions, au_draft: clone(DRAFT_VERSIONS)}; }
  tweak?.(fx);
  const questionSets = fx.question_sets as {id: string; questions?: Record<string, Json>}[];
  const owner = viewer === fx.me.id;
  const me: Json = owner ? fx.me : {id: viewer, name: fx.people.find(p => p.id === viewer)?.name || viewer, email: viewer + '@acme.example',
    role: 'member', company_role: 'member', cloud: true};
  const config = {};
  const db: AutomationsDb = {
    list: fx.automations.map(a => asAuto({...sortFlow(a), can_edit: owner && a.can_edit !== false, ...(a.can_pause != null ? {can_pause: owner && a.can_pause} : {})})),
    versions: {}, runs: {au_inbox: inboxRuns(), au_market: listenRuns(), ...(system ? {[String(SYSTEM.id)]: systemRuns()} : {})}, conv: null, messages: [],
    held: {},   // id → {wait, fail}: an automation's GET or Maintenance's PATCH waits for release()
  };
  for (const a of db.list) {
    const old = (fx.versions[a.id] || [{n: a.version, actor: a.owner_id, at: '2026-10-27T15:00:00Z'}]) as Json[];
    db.versions[a.id] = old.map(v => ({...v, status: v.status || 'active', flow: flowOf(a)}) as Version);
  }
  if (proposal) {
    const a = db.list.find(x => x.id === 'au_inbox')!, steps = clone(a.steps);
    steps.find(s => s.type === 'filter')?.rules!.unshift(sortKeys(proposalFx.rule));
    db.versions['au_inbox']!.push({n: a.version + 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
      summary: proposalFx.summary, flow: {trigger: clone(a.trigger), steps}});
  }
  if (maintenance) db.maint = {settings: {schedule: 'hourly', paused: false, monthly_cap_usd: null}, cap_env: 50, steps: maintSteps(),
    runs: maintRuns(), live: null};
  // GET only reads; the test ends a live run as the backend's worker does.
  const maintListing = () => {
    const mt = db.maint!, settings = mt.settings as Json & {paused?: boolean; monthly_cap_usd?: number | null};
    return clone({settings: {...settings, monthly_cap_usd_in_effect: settings.monthly_cap_usd ?? mt.cap_env}, steps: mt.steps,
      last_run: mt.runs[0] || null, live_run_id: mt.live?.id || null, can_change: owner,
      next_run_at: settings.paused ? null : new Date(Date.now() + 1800000).toISOString()});
  };
  if (heartbeat) db.heart = {interval_s: 10, last_beat_at: new Date(Date.now() - 4000).toISOString(), beats_24h: 8640, steps: heartSteps(),
    beats: heartBeats()};
  // A page of beats as GET /api/v2/heartbeat lists them; `next` is the last one's "started_at|id".
  const heartListing = (limit = 20, before: string | null = null) => {
    const hb = db.heart!, cut = before ? before.split('|')[0] : null;
    const rows = hb.beats.filter(b => !cut || b.started_at < cut).slice(0, limit);
    const more = hb.beats.filter(b => !cut || b.started_at < cut).length > limit;
    return clone({interval_s: hb.interval_s, interval_min: 5, interval_max: 60, last_beat_at: hb.last_beat_at, beats_24h: hb.beats_24h,
      quiet_beats: 12, quiet_since_at: null, kinds: [{key: 'due', name: 'Due times'}, {key: 'recovery', name: 'Recovery'}, {key: 'poll', name: 'Outside polls'}],
      steps: hb.steps.map(x => ({...x, can_change: x.switch && owner})), beats: rows,
      next: more ? `${rows.at(-1)!.started_at}|${rows.at(-1)!.id}` : null, can_change: owner});
  };
  const writes: Write[] = [];
  const find = (id: string) => db.list.find(a => a.id === id);
  // A Decide step on a saved set comes back with the set's questions alongside, read-only (CONTRACT §2).
  const withSets = (steps: unknown): unknown[] => ((steps || []) as Step[]).map(s => {
    const out: Step = {...s};
    if (s.type === 'decide' && s.set) out.questions = clone(questionSets.find(q => q.id === s.set)?.questions || {});
    if (s.type === 'paths') {
      out.paths = ((s.paths || []) as {steps?: unknown}[]).map(p => ({...p, steps: withSets(p.steps)}));
      out.otherwise = {steps: withSets(s.otherwise?.steps)};
    }
    return out;
  });
  // A listening's summary as the server gives it: its one dot is filled when every source read well and nothing waits unscored.
  const heard = (a: Auto): Json => {
    const t = a.trigger as Json, sources = (t.sources || []) as Json[];
    return t.type !== 'listening' ? {} : {listening: {dot: sources.length && !LISTEN_STALLED.has(a.id) && sources.every(s => LISTEN_HEALTH[String(s.id)]?.[0] === 'ok') ? 'filled' : 'hollow'}};
  };
  const full = (a: Auto): Json => ({...clone(a), steps: withSets(a.steps), ...heard(a)});
  // A list row carries how many versions wait for a person (the Pending pill)
  // and its newest run (the Last run column).
  const row = (a: Auto): Json => {
    const {steps, ...rest} = clone(a), last = db.runs[a.id]?.[0];
    return {...rest, ...heard(a), pending: db.versions[a.id]!.filter(v => v.status === 'pending').length,
      ...(last && !rest.last_run ? {last_run: {id: last.id, started_at: last.started_at, status: last.status}} : {})};
  };
  const nextN = (id: string) => Math.max(...db.versions[id]!.map(v => v.n)) + 1;
  const newVersion = (a: Auto, actor = 'person:' + me.id) => {
    a.version = nextN(a.id);
    db.versions[a.id]!.push({n: a.version, actor, at: new Date().toISOString(), status: 'active', flow: flowOf(a)});
  };

  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const refuse = (status: number, code: string, detail: string) => json({error: {code, detail}}, status);
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(fx.employees);
    if (p === '/api/humans') return json({people: fx.people.map(x => ({...x, org_parent: ''})), org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json({groups: fx.groups});
    const body = (method === 'GET' ? null : req.postDataJSON?.() ?? null) as Json | null;
    const b: Json = body ?? {};
    if (method !== 'GET') writes.push({method, path: p, body});

    // System jobs beside Maintenance (ui/app/settings-recurring.ts draws them): two of Tico's own jobs.
    if (db.maint && p === '/api/v2/system-routines' && method === 'GET') {
      const job = (key: string, name: string, extra: Json) => ({key, name, what: name + '.', schedule: 'Every hour', when: ['every hour'], then: [name.toLowerCase()],
        department: {name: 'Engineering'}, records: true, last: null, next: null, presets: [], search: name.toLowerCase(), ...extra});
      return json({can_create_task_rule: false, jobs: [job('stale_review', 'Stale ticket review', {switch: true, on: true, can_change: owner, controllable: true}),
        job('stall_checks', 'Stall checks', {needed: true, schedule: 'Every minute', when: ['every minute']})]});
    }
    if (db.heart && p === '/api/v2/heartbeat') {
      if (method === 'GET') return json(heartListing(Number(url.searchParams.get('limit') || 20), url.searchParams.get('before')));
      if (method === 'PUT') {
        if (!owner) return refuse(403, 'forbidden', 'Only an owner or an admin changes the Heartbeat');
        const hold = db.held['heartbeat'];
        if (hold) { await hold.wait; if (hold.fail) return refuse(500, 'boom', 'The hub stalled'); }
        const interval = Number(b.interval_s);
        if (!(interval >= 5 && interval <= 60)) return refuse(422, 'invalid', 'interval_s is 5 to 60');
        db.heart!.interval_s = b.interval_s as number;
        return json(heartListing());
      }
    }
    // A Heartbeat step's switch is its job's (backend/system_routines.py `row_of`): the answer is the step's row.
    if (db.heart && p === '/api/v2/system-routines/ask_deadlines' && method === 'PUT') {
      if (!owner) return refuse(403, 'forbidden', 'Only an owner or an admin changes this job');
      const st = db.heart.steps.find(x => x.key === 'ask_deadlines')!;
      st.on = !!b.on; st.every = st.on ? 'Hourly' : 'Off';
      return json({job: clone({...st, can_change: true})});
    }
    if (db.maint && p.startsWith('/api/v2/maintenance')) {
      const mt = db.maint;
      if (method !== 'GET' && !owner) return refuse(403, 'forbidden', 'Only an owner or an admin changes Maintenance');
      if (p === '/api/v2/maintenance' && method === 'GET') return json(maintListing());
      if (p === '/api/v2/maintenance' && method === 'PATCH') {
        const hold = db.held['maintenance'];
        if (hold) { await hold.wait; if (hold.fail) return refuse(500, 'boom', 'The hub stalled'); }
        const changes = (b.steps || []) as Json[];
        // backend/maintenance.py save_settings and save_step's refusals.
        if (b.schedule != null && !['hourly', '2h', '3h', '6h', '12h', 'daily'].includes(String(b.schedule))) return refuse(422, 'invalid', 'No such schedule');
        for (const ch of changes) {
          const st = mt.steps.find(x => x.key === ch.key);
          if (!st) return refuse(404, 'not_found', 'No such Maintenance step');
          const after: Json = {...st, ...Object.fromEntries(Object.entries(ch).filter(([, v]) => v != null))};
          if (MAINT_EVERY_RUN.includes(String(st.key)) && after.frequency !== 'run') return refuse(422, 'invalid', `${st.name} runs every run`);
          if (st.key === 'collect' && !after.enabled) return refuse(422, 'invalid', 'Collect is always on');
          if (after.frequency === 'custom' && (after.custom_every == null || !['hours', 'days'].includes(String(after.custom_unit)))) return refuse(422, 'invalid', 'A custom schedule needs a number and hours or days');
          if (ch.max_bots != null && !MAINT_BOTS.includes(String(st.key))) return refuse(422, 'invalid', `${st.name} takes no number of bots`);
        }
        for (const k of ['schedule', 'paused', 'monthly_cap_usd']) if (b[k] != null) mt.settings[k] = b[k];
        for (const ch of changes) {
          const st = mt.steps.find(x => x.key === ch.key)!;
          for (const [k, v] of Object.entries(ch)) if (k !== 'key' && v != null) st[k] = v;
          if (st.frequency !== 'custom') st.custom_every = st.custom_unit = null;
        }
        return json(maintListing());
      }
      if (p === '/api/v2/maintenance/runs') {
        const limit = Number(url.searchParams.get('limit') || 25), before = url.searchParams.get('before');
        const rows = mt.runs.filter(r => !before || r.started_at < before).slice(0, limit);
        return json({runs: clone(rows), next: rows.length === limit ? rows.at(-1)!.started_at : null});
      }
      if (p === '/api/v2/maintenance/run' && method === 'POST') {
        if (mt.live) return json({run: clone(mt.live), started: false});
        const at = new Date().toISOString();
        mt.live = {id: 'mrun_now', occurrence_key: null, trigger: 'run_now', step: b.step || null, started_at: at, finished_at: null,
          heartbeat_at: at, ok: null, result: null, cost_usd: 0, steps: [{step: 'collect', status: 'ran', reason: '', checked: 3, found: 1, woke: null,
            cost_usd: 0, started_at: at, finished_at: at}]} as MaintRun;
        mt.runs.unshift(mt.live);
        return json({run: clone(mt.live), started: true});
      }
    }
    // Listening: every listening's sources with how their last read went; a push source's new address.
    if (p === '/api/v2/listening/sources' && method === 'GET') {
      return json({sources: db.list.filter(a => (a.trigger as Json).type === 'listening').flatMap(a => (((a.trigger as Json).sources || []) as Json[]).map(s => {
        const [status, mins, error] = LISTEN_HEALTH[String(s.id)] || ['never', 0], at = status === 'never' ? null : new Date(Date.now() - mins * 60000).toISOString();
        return {id: 'ls_' + s.id, automation_id: a.id, key: s.id, kind: s.kind, site: s.site ?? null, target: s.target ?? null, status,
          last_read_at: at, last_ok_at: status === 'ok' ? at : null, last_error: error || null};
      }))});
    }
    const addrMatch = p.match(/^\/api\/v2\/listening\/sources\/([^/]+)\/address$/);
    if (addrMatch && method === 'POST') return json({address: `https://tico.example/api/v2/listening/in/new_${decodeURIComponent(addrMatch[1] ?? '')}`});
    if (p === '/api/v2/automations/question-sets') return json({sets: fx.question_sets});
    // A template fill saves nothing; like the hub it refuses an owner the viewer may not make an automation for (another
    // person) and one that can't hear the inbox (a group on an email address).
    if (p === '/api/v2/automations/templates') return json({templates: clone(TEMPLATES)});
    const tplMatch = p.match(/^\/api\/v2\/automations\/templates\/([^/]+)$/);
    if (tplMatch) {
      const tid = decodeURIComponent(tplMatch[1] ?? ''), owner = url.searchParams.get('owner') || '', inbox = url.searchParams.get('inbox') || '';
      if (!TEMPLATES.some(t => t.id === tid)) return refuse(404, 'not_found', 'No such template');
      if (!/^(person|bot|group):./.test(owner) || !inbox) return refuse(422, 'invalid', 'owner is person:<id>, bot:<slug> or group:<id>');
      if (owner.startsWith('person:') && owner !== 'person:' + me.id) return refuse(403, 'forbidden', `You may not make an automation for ${owner}`);
      if (owner.startsWith('group:') && inbox.includes('@')) return refuse(403, 'forbidden', `${owner} may not read ${inbox}`);
      return json(filledTemplate(tid, owner, inbox));
    }
    if (p === '/api/v2/automations' && method === 'POST') {
      // A new one (Blank, or New inbox's): version 1.
      const bad = flowProblem(b.steps);
      if (bad) return refuse(422, 'invalid_flow', bad);
      const a = asAuto({...sortFlow(b), owner_id: b.owner, id: 'au_' + (db.list.length + 1), version: 1, can_edit: true});
      db.list.push(a);
      db.versions[a.id] = [{n: 1, actor: 'person:' + me.id, at: new Date().toISOString(), status: 'active', flow: flowOf(a)}];
      return json(full(a));
    }
    if (p === '/api/v2/automations') return json({automations: db.list.map(row), next: null});
    // The overview's reads: every run in the range across the list, newest first, and its counts per bucket.
    const RANGES: Record<string, [number, number]> = {'5m': [300, 10], '1h': [3600, 12], '24h': [86400, 24], '7d': [604800, 28]};
    // The Maintenance run's log is listed under its row, as backend/automation_overview.py does (`maint_row`).
    const maintRunRows = (): Run[] => (db.maint?.runs || []).map(r => {
      const status = !r.finished_at ? 'running' : r.ok ? 'ok' : 'failed';
      return {id: 'mnt_' + r.id, automation_id: 'maintenance', automation_name: 'Maintenance', trigger: 'system', owner_id: 'tico',
        started_at: r.started_at, finished_at: r.finished_at, status, cost: r.cost_usd || 0,
        started_by: r.trigger === 'run_now' ? 'Run now' : 'Scheduled', outcome: r.result || (status === 'ok' ? 'Done' : status === 'failed' ? 'Failed' : 'Running'),
        step_count: null, count: 1, maint: true} as Run;
    });
    // The Heartbeat's runs (system_runs job heartbeat) too (`heart_row`): one a minute, the last failed.
    const heartRuns = (): Run[] => !db.heart ? [] : Array.from({length: 6}, (_, i) => ({id: 'hb_r' + i, automation_id: 'heartbeat',
      automation_name: 'Heartbeat', trigger: 'system', owner_id: 'tico', started_at: new Date(Date.now() - (i * 60 + 30) * 1000).toISOString(),
      finished_at: new Date(Date.now() - (i * 60 + 30) * 1000 + 7).toISOString(), status: i === 0 ? 'failed' : 'ok', cost: 0,
      started_by: 'Scheduled', outcome: i === 0 ? 'Failed: Queue check' : '1 step did something', step_count: null, count: 1, maint: true} as Run));
    const inRange = (range: string | null): Run[] => {
      const [secs] = RANGES[range ?? ''] || RANGES['1h']!, since = Date.now() - secs * 1000;
      return [...Object.values(db.runs).flat().filter(r => find(String(r.automation_id))), ...maintRunRows(), ...heartRuns()]
        .filter(r => new Date(r.started_at).getTime() >= since)
        .sort((x, y) => y.started_at.localeCompare(x.started_at));
    };
    if (p === '/api/v2/automations/runs') {
      const q = url.searchParams, ids = (q.get('ids') || '').split(',').filter(Boolean), status = q.get('status') || '';
      const all = inRange(q.get('range')).filter(r => (!ids.length || ids.includes(String(r.automation_id)))
        && (!status || (status === 'done' ? ['ok', 'empty', 'skipped'].includes(String(r.status)) : r.status === status)));
      const from = q.get('before') ? all.findIndex(r => r.id === q.get('before')) + 1 : 0, limit = Number(q.get('limit') || 50);
      const rows = all.slice(from, from + limit).map(r => {
        if (r.maint) return {...r, maint: undefined};
        const a = find(String(r.automation_id))!;
        return {...r, steps: undefined, automation_name: a.name, trigger: a.kind || (a.trigger as Json).type, owner_id: a.owner_id,
          started_by: r.started_by || 'From Sam Lee · Thursday?', outcome: r.outcome || (r.status === 'failed' ? 'LLM failed: model timed out' : 'Draft reply'),
          step_count: (r.steps || []).length, count: 1};
      });
      return json({runs: rows, next: from + limit < all.length ? rows.at(-1)!.id : null});
    }
    if (p === '/api/v2/automations/stats') {
      const range = url.searchParams.get('range') || '1h', [secs, n] = RANGES[range]!, width = secs * 1000 / n, since = Date.now() - secs * 1000;
      const per = new Map<string, {id: string; runs: number; failed: number; cost: number; buckets: {ok: number; failed: number}[]}>();
      const blank = () => Array.from({length: n}, () => ({ok: 0, failed: 0}));
      for (const r of inRange(range)) {
        const id = String(r.automation_id);
        const s = per.get(id) || {id, runs: 0, failed: 0, cost: 0, buckets: blank()};
        const b = Math.min(n - 1, Math.floor((new Date(r.started_at).getTime() - since) / width)), bad = r.status === 'failed';
        s.runs++; s.failed += bad ? 1 : 0; s.cost += Number(r.cost || 0); s.buckets[b]![bad ? 'failed' : 'ok']++;
        per.set(id, s);
      }
      const totals = {runs: 0, failed: 0, cost: 0, buckets: blank()};
      for (const s of per.values()) { totals.runs += s.runs; totals.failed += s.failed; totals.cost += s.cost;
        s.buckets.forEach((x, i) => { totals.buckets[i]!.ok += x.ok; totals.buckets[i]!.failed += x.failed; }); }
      return json({range, bucket_seconds: width / 1000, automations: [...per.values()], totals});
    }
    const m = p.match(/^\/api\/v2\/automations\/([^/]+)(?:\/(.*))?$/);
    if (m) {
      const a = find(decodeURIComponent(m[1] ?? ''));
      const rest = m[2] || '';
      if (!a) return refuse(404, 'not_found', 'No such automation');
      if (method !== 'GET' && !a.can_edit) return refuse(403, 'forbidden', 'You can’t change this automation');
      if (!rest && method === 'GET') {
        const hold = db.held[a.id];
        if (hold) { await hold.wait; if (hold.fail) return refuse(500, 'boom', 'The hub stalled'); }
        return json(full(a));
      }
      if (!rest && method === 'PATCH') {
        for (const k of ['enabled', 'spend_cap', 'name']) if (k in b) { if (b[k] === null) delete a[k]; else a[k] = b[k]; }
        if (b.steps || b.trigger || b.destinations) {
          const bad = b.steps ? flowProblem(b.steps) : '';
          if (bad) return refuse(422, 'invalid_flow', bad);
          // A listening's new push source gets its address, sent with this answer only.
          const had = new Set((((a.trigger as Json).sources || []) as Json[]).map(s => s.id));
          const pushed = (((b.trigger as Json | undefined)?.sources || []) as Json[]).filter(s => s.kind === 'push' && !had.has(s.id));
          if (b.steps) a.steps = sortKeys(b.steps) as Step[];
          if (b.trigger) a.trigger = sortKeys(b.trigger) as Json;
          if (Array.isArray(b.destinations)) a.destinations = sortKeys(b.destinations) as Json[];
          newVersion(a);
          if (pushed.length) return json({...full(a), push_addresses: Object.fromEntries(pushed.map(s => [s.id, `https://tico.example/api/v2/listening/in/tok_${s.id}`]))});
        }
        return json(full(a));
      }
      if (!rest && method === 'DELETE') {
        if (a.builtin) return refuse(409, 'builtin', 'Built-in automations can’t be deleted');
        db.list = db.list.filter(x => x !== a);
        return json({ok: true});
      }
      if (rest === 'runs') {
        const all = db.runs[a.id] || [], limit = Number(url.searchParams.get('limit') || 25), before = url.searchParams.get('before');
        const from = before ? all.findIndex(r => r.id === before) + 1 : 0, rows = all.slice(from, from + limit);
        return json({runs: rows, next: from + limit < all.length ? rows.at(-1)!.id : null});
      }
      // The run guard's hold: release queues its held runs, drop skips them (backend/automations.py `_unhold`).
      if (rest === 'held' && method === 'POST') {
        if (!a.hold) return refuse(409, 'not_on_hold', 'The automation is not on hold');
        const moved = a.hold.held;
        delete a.hold;
        for (const r of db.runs[a.id] || []) if (r.status === 'held') r.status = b.action === 'release' ? 'queued' : 'skipped';
        return json({...full(a), moved});
      }
      if (rest === 'versions') {
        const status = url.searchParams.get('status');
        // A version's flow comes back key-sorted as the hub stores it, including one a test put in the list directly.
        return json({versions: db.versions[a.id]!.filter(v => !status || v.status === status).map(v => ({...v, flow: sortFlow(v.flow || {})}))});
      }
      const restore = rest.match(/^versions\/(\d+)\/restore$/);
      if (restore && method === 'POST') {
        const v = db.versions[a.id]!.find(x => x.n === +restore[1]!);
        if (!v) return refuse(404, 'not_found', 'No such version');
        const f = sortFlow(v.flow || {}) as Json & {trigger: Json; steps: Step[]; destinations?: Json[]};
        a.trigger = f.trigger; a.steps = f.steps;
        if (f.destinations) a.destinations = f.destinations; else delete a.destinations;
        // A pending version turned on becomes the current one; an older one comes back as a new version. A draft (off,
        // no version live yet) is turned on by its first pending version.
        if (v.status === 'pending') {
          const draftNow = !a.enabled && !db.versions[a.id]!.some(x => x.status === 'active');
          v.status = 'active'; a.version = v.n;
          if (draftNow) a.enabled = true;
        } else newVersion(a);
        return json(full(a));
      }
      const version = rest.match(/^versions\/(\d+)$/);
      if (version && method === 'DELETE') {
        const v = db.versions[a.id]!.find(x => x.n === +version[1]! && x.status === 'pending');
        if (!v) return refuse(409, 'not_pending', 'Only a proposal can be discarded');
        db.versions[a.id] = db.versions[a.id]!.filter(x => x !== v);
        return json({ok: true});
      }
    }

    // BotOps, with the viewer's rights: a message about an automation saves the change as its new version; one from
    // New makes an automation, off unless the message says to turn it on; "?" in the text gets a question back.
    if (p === '/api/v2/conversations') return json({conversations: db.conv && url.searchParams.get('chat_with') === 'botops' ? [db.conv] : []});
    if (db.conv && p === `/api/v2/conversations/${db.conv.id}/snapshot`) return json({messages: db.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/chat/botops' && method === 'POST') {
      db.conv ||= {id: 'conv_au', kind: 'chat', title: 'BotOps'};
      const n = db.messages.length, at = new Date().toISOString();
      const mine: Json = {id: 'msg' + n, sender_id: 'human:' + me.id, body: b.text, refs: b.refs || {}, created_at: at, kind: 'say'};
      db.messages.push(mine);
      const refAuto = (b.refs as Json | undefined)?.automation;
      const a = find(String(refAuto));
      let reply = 'Which inbox?';
      if (a) {
        a.steps.find(s => s.type === 'filter')?.rules!.unshift(sortKeys(proposalFx.rule));
        newVersion(a);
        reply = `Saved v${a.version}: receipts are archived first.`;
      } else if (!/\?/.test(String(b.text))) {
        const on = /turn it on/i.test(String(b.text));
        db.list.push(sortFlow({id: 'au_new', name: 'Sales leads', owner_id: 'person:' + me.id, enabled: on, builtin: false, version: 1, can_edit: true,
          trigger: {type: 'message', inbox: 'sales@acme.example', filters: []},
          steps: [{id: 'f1', type: 'filter', enabled: true, rules: [{when: [{field: 'message.subject', op: 'contains', value: 'demo'}], match: 'all', then: [{do: 'label', args: {name: 'Lead'}}], stop: false}]}]}) as Auto);
        const created = db.list.at(-1)!;
        db.versions['au_new'] = [{n: 1, actor: 'person:' + me.id, at, status: 'active', flow: {trigger: clone(created.trigger), steps: clone(created.steps)}}];
        reply = `Saved Sales leads: mail to sales@acme.example about a demo gets the Lead label. It is ${on ? 'on' : 'off'}.`;
      }
      db.messages.push({id: 'msg' + (n + 1), sender_id: 'bot:botops', body: reply, refs: {}, created_at: at, kind: 'say'});
      return json({conversation: db.conv, message: mine});
    }
    return json(unrouted(p));
  });
  await routes?.(page);
  await page.goto('http://tico-ui.test/' + hash);
  // Hold an automation's GET, Maintenance's PATCH or the Heartbeat's PUT until release() (fail: then answer 500).
  const hold = (id: string, fail = false) => {
    let release = (): void => undefined;
    const wait = new Promise<void>(resolve => { release = () => resolve(); });
    db.held[id] = {wait, fail};
    return () => { delete db.held[id]; release(); };
  };
  return {page, errors, writes, db, me, hold};
}

// No sideways scroll on the page itself (a path strip scrolls inside its own box).
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
// A screenshot for the build notes, when TICO_UI_SHOTS names a folder.
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  // The route's fade and other one-offs; the Liquid Dot breathes forever, so an endless animation is not waited on.
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

const api = {openAutomations, filledTemplate, PHONE, DESKTOP, sideways, shot};
export type Api = typeof api;
module.exports = api;
