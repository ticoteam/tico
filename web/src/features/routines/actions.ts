import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s, type Ctx } from '@/actions';
import { meQuery } from '@/data';
import { ACTS, applyFlow, dropItem, flowOf, putItem, routineBoard, type Part } from './board';
import {
  addDue,
  addOrgRule,
  addRuleSource,
  createInbox,
  fillTemplate,
  RUN_RANGES,
  RUN_STATUSES,
  setFlow,
  addStageRule,
  createHook,
  updateHook,
  createRoutine,
  createWatch,
  decideOrgRule,
  endHold,
  decideStageRule,
  discardVersion,
  hooksQuery,
  orgRulesQuery,
  partiesOf,
  readDocument,
  removeDue,
  removeHook,
  removeRoutine,
  restoreVersion,
  rotateHook,
  routineQuery,
  routinesQuery,
  runJob,
  runMaintenance,
  runRoutine,
  setHeartbeat,
  setJob,
  setJobSchedule,
  setMaintenance,
  setReach,
  setRoutineLimit,
  setRoutineOn,
  setWatchTrigger,
  satisfyDue,
  signHook,
  signHookOff,
  stageRulesQuery,
  updateDue,
  updateOrgRule,
  updateRoutine,
  updateStageRule,
  watchesQuery,
  watchQuery,
  type OrgRuleFields,
  type RoutineFields,
  type StageRules,
} from './queries';

import type { ListenSource, OrgRule, Routine, StageRule, Trigger } from './types';
import { browserZone } from './words';

/* Every change and every move a person makes on Routines, Rules and Listening. A tab is `?tab=` and one routine, rule
   or webhook opens in `?panel=`, so Back closes it; one watch is `/company/listening?watch=<id>`. Removing asks first in the page (the confirm is the control's, not the
   action's). Anything that makes or rotates a webhook's secret is the person's own press: `tico: false`. */

export const ROUTINES = '/company/routines';
export const RULES = '/company/rules';
export const LISTENING = '/company/listening';

/** The Rules tabs, in the mock's order: `?tab=` ('' is Company). */
export const RULE_TABS = ['', 'stages', 'built-in', 'outside', 'due', 'wakes', 'on-tasks', 'suggested'] as const;
export type RuleTab = (typeof RULE_TABS)[number];
export const BUILTINS = ['maintenance', 'heartbeat', 'system'] as const;
export type Builtin = (typeof BUILTINS)[number];

const id = s.string({ min: 1, max: 200 });
const agent = s.string({ min: 1, max: 200, pattern: /^bot:\S+$/, what: 'bot:<id>' });
const party = s.string({ min: 1, max: 200, pattern: /^(\*|(group|bot|human):\S+)$/, what: '*, group:<id>, bot:<id> or human:<id>' });
const cron = s.string({ min: 9, max: 100, pattern: /^\S+( \S+){4}$/, what: 'a five-part cron, like 0 9 * * 1-5' });
const name = s.string({ min: 1, max: 200 });
const owner = s.string({ min: 1, max: 200, pattern: /^(bot|human):\S+$/, what: 'bot:<id> or human:<id>' });
const day = s.string({ min: 10, max: 10, pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a day, YYYY-MM-DD' });
const REPEATS = ['once', 'weekly', 'monthly', 'quarterly', 'yearly'] as const;
/** The range the Runs tab shows when the address names none: the last hour, as the old app opened it. */
export const RUNS_RANGE = '1h';

const go = (router: AnyRouter, to: string, search: Record<string, string | undefined> = {}) => router.navigate({ to, search });
const panelOff = (router: AnyRouter) =>
  router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel: undefined }) });

/** The error as "code: reason", so the control can say why (task/actions.ts said). */
async function said<T>(p: Promise<T>): Promise<T> {
  try {
    return await p;
  } catch (e) {
    const err = e as { status?: number; code?: string; message?: string };
    if (err.status === 401 || err.status === 403 || !err.code) throw e;
    throw Object.assign(new Error(err.message), { status: err.status, code: `${err.code}: ${err.message ?? ''}` });
  }
}

// ---------------------------------------------------------------- routines: views

export const openRoutine = defineAction({
  id: 'routines.open',
  title: 'Open a routine',
  kind: 'view',
  input: s.object({ routine: id }),
  run: ({ router }, { routine }) => go(router, ROUTINES, { panel: `routine:${routine}` }),
  did: () => 'Opened the routine',
  target: ({ routine }) => ({ kind: 'routine', id: routine }),
});

export const openBuiltin = defineAction({
  id: 'routines.openBuiltin',
  title: 'Open Maintenance, Heartbeat or System jobs',
  kind: 'view',
  input: s.object({ which: s.oneOf(BUILTINS) }),
  run: ({ router }, { which }) => go(router, ROUTINES, { panel: `builtin:${which}` }),
  did: () => 'Opened it',
  target: ({ which }) => ({ kind: 'routine', id: which }),
});

export const newRoutine = defineAction({
  id: 'routines.new',
  title: 'Start a new routine',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => go(router, ROUTINES, { panel: 'routine:new' }),
  did: () => 'Opened a new routine',
  target: () => ({ kind: 'panel', id: 'routine:new' }),
});

export const closePanel = defineAction({
  id: 'routines.closePanel',
  title: 'Close the panel',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => panelOff(router),
  did: () => 'Closed the panel',
});

export const showRuns = defineAction({
  id: 'routines.showRuns',
  title: 'Show every run across routines',
  kind: 'view',
  input: s.object({
    status: s.optional(s.oneOf(RUN_STATUSES)),
    range: s.optional(s.oneOf(RUN_RANGES)),
  }),
  run: ({ router }, i) => go(router, ROUTINES, { tab: 'runs', status: i.status, range: i.range === RUNS_RANGE ? undefined : i.range }),
  did: () => 'Opened the runs',
  target: () => ({ kind: 'tab', id: 'runs' }),
});

// ---------------------------------------------------------------- routines: changes

const cached = (ctx: Ctx, rid: string): Routine | undefined =>
  ctx.queryClient.getQueryData<Routine>(routineQuery(rid).queryKey) ??
  ctx.queryClient.getQueryData<Routine[]>(routinesQuery.queryKey)?.find((r) => r.id === rid);

/** The trigger an edit saves: a change to the same kind is merged into the one it has, so its filters, its zone (and
   anything else the editor does not draw) stay; another kind starts fresh, as the old editor did (its filters test
   another kind's fields). A new schedule runs in the person's zone. */
const triggerOf = (i: { cron?: string; event?: string }, was?: Trigger): Trigger => {
  if (i.cron) return was?.type === 'schedule' ? { ...was, cron: i.cron, tz: was.tz || browserZone() } : { type: 'schedule', cron: i.cron, tz: browserZone() };
  if (i.event) return was?.type === 'event' ? { ...was, name: i.event } : { type: 'event', name: i.event };
  return was ?? { type: 'schedule', cron: '0 9 * * 1-5', tz: browserZone() };
};

export const createRoutineAction = defineAction({
  id: 'routines.create',
  title: 'Add a routine',
  kind: 'change',
  input: s.object({
    name,
    agent,
    instruction: s.string({ min: 1, max: 20_000 }),
    cron: s.optional(cron),
    event: s.optional(s.string({ min: 1, max: 100 })),
  }),
  run: async ({ router }, i) => {
    const made = await said(createRoutine({ name: i.name.trim(), agent: i.agent, instruction: i.instruction.trim(), trigger: triggerOf(i) }));
    await go(router, ROUTINES, { panel: `routine:${made.id}` });
    return made;
  },
  did: (i) => `Added ${i.name}`,
  target: (_i, out) => (out ? { kind: 'routine', id: out.id } : null),
});

export const updateRoutineAction = defineAction({
  id: 'routines.update',
  title: 'Change a routine',
  kind: 'change',
  input: s.object({
    routine: id,
    name: s.optional(name),
    agent: s.optional(agent),
    instruction: s.optional(s.string({ min: 1, max: 20_000 })),
    cron: s.optional(cron),
    event: s.optional(s.string({ min: 1, max: 100, pattern: /^[a-z][a-z0-9_]*(\.[a-z0-9_*-]+)+$/, what: 'an event name, like task.created' })),
  }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (ctx, i) => {
    // read it first when it is not loaded, so its trigger's filters and its other steps are kept
    const was = cached(ctx, i.routine) ?? (await ctx.queryClient.ensureQueryData(routineQuery(i.routine)));
    const step = was?.steps?.find((x) => x.type === 'bot_turn');
    const f: RoutineFields = {
      name: (i.name ?? was?.name ?? '').trim(),
      agent: i.agent ?? step?.bot ?? was?.owner_id ?? '',
      instruction: (i.instruction ?? step?.prompt ?? '').trim(),
      trigger: triggerOf(i, was?.trigger),
    };
    return said(updateRoutine(i.routine, f, was?.steps));
  },
  did: () => 'Saved',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const runNow = defineAction({
  id: 'routines.runNow',
  title: 'Run a routine now',
  kind: 'change',
  input: s.object({ routine: id }),
  run: (_ctx, i) => said(runRoutine(i.routine)),
  did: () => 'Started a run',
  target: (i) => ({ kind: 'routine-runs', id: i.routine }),
});

/** The sample as an object: blank is none, anything else must be a JSON object. */
const sampleOf = (text: string | undefined): Record<string, unknown> | undefined => {
  if (!text?.trim()) return undefined;
  let v: unknown;
  try {
    v = JSON.parse(text);
  } catch {
    v = null;
  }
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw Object.assign(new Error("Sample input isn't a JSON object"), { code: "bad_input: Sample input isn't a JSON object" });
  return v as Record<string, unknown>;
};

export const testRun = defineAction({
  id: 'routines.testRun',
  title: 'Test a routine with sample input',
  kind: 'change',
  // `sample`: JSON standing in for what its trigger gives (a message, an event, a webhook body)
  input: s.object({ routine: id, sample: s.optional(s.string({ max: 50_000 })) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (_ctx, i) => {
    const r = await said(runRoutine(i.routine, sampleOf(i.sample)));
    return { dropped: !!r.dropped };
  },
  did: (_i, out) => (out?.dropped ? "Dropped by the trigger's filters" : 'Started a test run'),
  target: (i) => ({ kind: 'routine-runs', id: i.routine }),
});

export const restoreVersionAction = defineAction({
  id: 'routines.restoreVersion',
  title: "Restore a routine's version, or turn on a proposed one",
  kind: 'change',
  input: s.object({ routine: id, n: s.number({ int: true, min: 1, max: 1_000_000 }) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(restoreVersion(i.routine, i.n)).then(() => undefined),
  did: (i) => `Restored v${i.n}`,
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const discardVersionAction = defineAction({
  id: 'routines.discardVersion',
  title: "Discard an agent's proposed version",
  kind: 'change',
  input: s.object({ routine: id, n: s.number({ int: true, min: 1, max: 1_000_000 }) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(discardVersion(i.routine, i.n)).then(() => undefined),
  did: (i) => `Discarded v${i.n}`,
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const pause = defineAction({
  id: 'routines.pause',
  title: 'Pause a routine',
  kind: 'change',
  input: s.object({ routine: id }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_pause !== false,
  run: (_ctx, i) => said(setRoutineOn(i.routine, false)),
  did: () => 'Paused',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const setLimit = defineAction({
  id: 'routines.setLimit',
  title: "Set a routine's spend limit",
  kind: 'change',
  input: s.object({ routine: id, per30m: s.nullable(s.number({ min: 0, max: 100_000 })) }),
  // A spending limit is budget: the person's own press.
  tico: false,
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(setRoutineLimit(i.routine, i.per30m)),
  did: (i) => (i.per30m === null ? "Uses the agent's limit" : 'Limit set'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const resume = defineAction({
  id: 'routines.resume',
  title: 'Turn a routine back on',
  kind: 'change',
  input: s.object({ routine: id }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_pause !== false,
  run: (_ctx, i) => said(setRoutineOn(i.routine, true)),
  did: () => 'Turned on',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

/** The run guard held its runs (too many at once): release them in order, or drop them. */
export const endHoldAction = defineAction({
  id: 'routines.endHold',
  title: "Release or drop a routine's held runs",
  kind: 'change',
  input: s.object({ routine: id, action: s.oneOf(['release', 'drop'] as const) }),
  // Dropped runs are skipped for good: the person's own press.
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false && !(i.action === 'drop' && ctx.via === 'tico'),
  run: (_ctx, i) => said(endHold(i.routine, i.action)).then(() => undefined),
  did: (i) => (i.action === 'release' ? 'Released' : 'Dropped'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const removeRoutineAction = defineAction({
  id: 'routines.remove',
  title: 'Remove a routine',
  kind: 'change',
  input: s.object({ routine: id }),
  // Nothing brings a removed routine back, and what it held is dropped: the person's own press.
  tico: false,
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async ({ router }, i) => {
    await said(removeRoutine(i.routine));
    await panelOff(router);
  },
  did: () => 'Removed the routine',
});

export const setMaintenanceAction = defineAction({
  id: 'routines.setMaintenance',
  title: 'Change Maintenance',
  kind: 'change',
  input: s.object({
    schedule: s.optional(s.oneOf(['hourly', '2h', '3h', '6h', '12h', 'daily'] as const)),
    paused: s.optional(s.boolean()),
    step: s.optional(s.string({ min: 1, max: 60 })),
    stepOn: s.optional(s.boolean()),
  }),
  run: (_ctx, i) =>
    said(setMaintenance({ schedule: i.schedule, paused: i.paused, step: i.step !== undefined ? { key: i.step, enabled: i.stepOn ?? true } : undefined })),
  did: () => 'Saved',
  target: () => ({ kind: 'routine', id: 'maintenance' }),
});

export const runMaintenanceAction = defineAction({
  id: 'routines.runMaintenance',
  title: 'Run Maintenance now',
  kind: 'change',
  input: s.object({}),
  run: () => said(runMaintenance()),
  did: () => 'Started Maintenance',
  target: () => ({ kind: 'routine-runs', id: 'maintenance' }),
});

export const setHeartbeatAction = defineAction({
  id: 'routines.setHeartbeat',
  title: 'Set how often Heartbeat checks',
  kind: 'change',
  input: s.object({ seconds: s.number({ int: true, min: 5, max: 60 }) }),
  run: (_ctx, i) => said(setHeartbeat(i.seconds)),
  did: (i) => `Heartbeat every ${i.seconds} s`,
  target: () => ({ kind: 'routine', id: 'heartbeat' }),
});

export const runJobAction = defineAction({
  id: 'routines.runJob',
  title: 'Run a system job now',
  kind: 'change',
  input: s.object({ job: s.string({ min: 1, max: 100 }) }),
  run: (_ctx, i) => said(runJob(i.job)),
  did: () => 'Started it',
  target: (i) => ({ kind: 'job', id: i.job }),
});

export const setJobAction = defineAction({
  id: 'routines.setJob',
  title: 'Turn a system job on or off',
  kind: 'change',
  input: s.object({ job: s.string({ min: 1, max: 100 }), on: s.boolean() }),
  run: (_ctx, i) => said(setJob(i.job, i.on)),
  did: (i) => (i.on ? 'Turned on' : 'Turned off'),
  target: (i) => ({ kind: 'job', id: i.job }),
});

export const JOB_PRESETS = ['15m', 'hourly', '4h', 'daily', 'business'] as const;

export const setJobScheduleAction = defineAction({
  id: 'routines.setJobSchedule',
  title: 'Pick when a system job runs: a preset, or its daily time',
  kind: 'change',
  input: s.object({
    job: s.string({ min: 1, max: 100 }),
    preset: s.optional(s.oneOf(JOB_PRESETS)),
    at: s.optional(s.string({ min: 5, max: 5, pattern: /^([01]\d|2[0-3]):[0-5]\d$/, what: 'a time, HH:MM' })),
  }),
  run: (_ctx, i) => said(setJobSchedule(i.job, { preset: i.preset, at: i.at })),
  did: (i) => (i.at && !i.preset ? `Runs at ${i.at}` : 'Schedule changed'),
  target: (i) => ({ kind: 'job', id: i.job }),
});

/** Search and filter the routines list; both live in the address. */
export const ROUTINE_STATES = ['on', 'failed', 'hold', 'off'] as const;
export type RoutineState = (typeof ROUTINE_STATES)[number];

export const listRoutines = defineAction({
  id: 'routines.list',
  title: 'Search or filter the routines',
  kind: 'view',
  input: s.object({ q: s.optional(s.string({ max: 200 })), state: s.optional(s.oneOf(ROUTINE_STATES)) }),
  run: ({ router }, i) => go(router, ROUTINES, { q: i.q?.trim() || undefined, state: i.state }),
  did: () => 'Listed the routines',
  target: () => ({ kind: 'tab', id: 'routines' }),
});

// ---------------------------------------------------------------- the board, inboxes and templates

const notLoaded = () => Object.assign(new Error('That routine is not loaded'), { code: 'not_found: That routine is not loaded' });
const notBoard = () => Object.assign(new Error("This routine isn't a board"), { code: "not_a_board: This routine isn't a board" });

const act = s.object({ do: s.oneOf(Object.keys(ACTS)), args: s.optional(s.record(s.string({ max: 20_000 }))) });
const cond = s.object({ field: s.string({ min: 1, max: 200 }), op: s.optional(s.oneOf(['is', 'is_not', 'contains', 'matches', '>=', '<='] as const)), value: s.string({ max: 2000 }) });

export const saveBoardItem = defineAction({
  id: 'routines.saveBoardItem',
  title: "Add or change a rule, question, destination or the LLM on a routine's board",
  kind: 'change',
  input: s.object({
    routine: id,
    part: s.oneOf(['rule', 'question', 'destination', 'llm'] as const),
    // which one, from 0; left out adds a new one (the LLM has just one)
    at: s.optional(s.number({ int: true, min: 0, max: 500 })),
    when: s.optional(s.array(cond, { max: 20 })),
    match: s.optional(s.oneOf(['all', 'any'] as const)),
    then: s.optional(s.array(act, { max: 20 })),
    // a rule's or question's last step: a destination id, or '' to end (or, a rule with moveOn, to move on)
    goTo: s.optional(s.string({ max: 100 })),
    moveOn: s.optional(s.boolean()),
    text: s.optional(s.string({ max: 8000 })),
    min: s.optional(s.nullable(s.number({ min: 0, max: 1 }))),
    name: s.optional(name),
    mode: s.optional(s.oneOf(['sort', 'draft', 'reply'] as const)),
    prompt: s.optional(s.string({ max: 20_000 })),
    dests: s.optional(s.array(s.string({ min: 1, max: 100 }), { max: 50 })),
    unsure: s.optional(s.string({ max: 100 })),
  }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (ctx, { routine, ...item }) => {
    const r = cached(ctx, routine);
    if (!r) throw notLoaded();
    const b = routineBoard(r);
    if (!b) throw notBoard();
    return said(setFlow(routine, flowOf(putItem(b, item, r)))).then(() => undefined);
  },
  did: (i) => (i.at === undefined && i.part !== 'llm' ? 'Added' : 'Saved'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const removeBoardItem = defineAction({
  id: 'routines.removeBoardItem',
  title: "Remove a rule, question or destination from a routine's board",
  kind: 'change',
  input: s.object({ routine: id, part: s.oneOf(['rule', 'question', 'destination'] as const), at: s.number({ int: true, min: 0, max: 500 }) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (ctx, i) => {
    const r = cached(ctx, i.routine);
    if (!r) throw notLoaded();
    const b = routineBoard(r);
    if (!b) throw notBoard();
    return said(setFlow(i.routine, flowOf(dropItem(b, i.part as Exclude<Part, 'llm'>, i.at)))).then(() => undefined);
  },
  did: () => 'Removed',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

/** `human:<id>` as the server's owner (`person:<id>`); an agent or a team as it is. */
const ownerRef = (o: string) => o.replace(/^human:/, 'person:');

export const newInbox = defineAction({
  id: 'routines.newInbox',
  title: 'Add an inbox routine from a template',
  kind: 'change',
  input: s.object({
    // an email address, or a channel a team hears
    inbox: s.string({ min: 1, max: 300 }),
    owner: s.string({ min: 1, max: 200, pattern: /^(human|person|bot|group):\S+$/, what: 'human:<id>, bot:<id> or group:<id>' }),
    template: s.string({ min: 1, max: 100 }),
    on: s.optional(s.boolean()),
  }),
  run: async ({ router }, i) => {
    const filled = await said(fillTemplate(i.template, ownerRef(i.owner), i.inbox.trim()));
    const made = await said(createInbox(filled, !!i.on));
    await go(router, ROUTINES, { panel: `routine:${made.id}` });
    return made.id;
  },
  did: (i) => `Added the ${i.inbox.trim()} inbox`,
  target: (_i, out) => (out ? { kind: 'routine', id: out } : null),
});

export const applyTemplate = defineAction({
  id: 'routines.applyTemplate',
  title: 'Move an inbox routine onto a template',
  kind: 'change',
  input: s.object({ routine: id, template: s.string({ min: 1, max: 100 }) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (ctx, i) => {
    const r = cached(ctx, i.routine);
    if (!r) throw notLoaded();
    const filled = await said(fillTemplate(i.template, ownerRef(r.owner_id ?? ''), String(r.trigger.inbox ?? '')));
    const flow = applyFlow(r, filled);
    if (!flow) throw Object.assign(new Error("This template doesn't fit this inbox"), { code: "bad_input: This template doesn't fit this inbox" });
    await said(setFlow(i.routine, flow, 'Applied a template'));
  },
  did: () => 'Applied the template',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

// ---------------------------------------------------------------- webhooks

export const openWebhook = defineAction({
  id: 'webhooks.open',
  title: 'Open a webhook',
  kind: 'view',
  input: s.object({ hook: id }),
  run: ({ router }, { hook }) => go(router, ROUTINES, { tab: 'webhooks', panel: `hook:${hook}` }),
  did: () => 'Opened the webhook',
  target: ({ hook }) => ({ kind: 'hook', id: hook }),
});

const PRESETS = ['hmac', 'github', 'standard', 'stripe', 'slack'] as const;
export const addWebhook = defineAction({
  id: 'webhooks.add',
  title: 'Add a webhook',
  kind: 'change',
  // Its address carries a secret shown once, and so may its signing secret: the person's own press.
  tico: false,
  input: s.object({ name, preset: s.optional(s.oneOf(PRESETS)), secret: s.optional(s.string({ max: 300 })) }),
  // The full address (secret included) and a made signing secret go back to the page that added it, held in its
  // state: never cached. Making one is two calls, as the old app made it: create, then signing.
  run: async ({ router }, i) => {
    const made = await said(createHook(i.name.trim()));
    await go(router, ROUTINES, { tab: 'webhooks', panel: `hook:${made.id}` });
    const signed = i.preset ? await said(signHook(made.id, i.preset, i.secret?.trim() || undefined)) : undefined;
    return { hook: made.id, url: made.url, ...(signed?.secret ? { signing: signed.secret } : {}) };
  },
  did: (i) => `Added ${i.name}`,
  target: (_i, made) => (made ? { kind: 'hook', id: made.hook } : null),
});

export const updateWebhook = defineAction({
  id: 'webhooks.update',
  title: "Rename a webhook",
  kind: 'change',
  input: s.object({ hook: id, name }),
  run: (_ctx, i) => said(updateHook(i.hook, { name: i.name.trim() })).then(() => undefined),
  did: () => 'Saved',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

export const rotateWebhook = defineAction({
  id: 'webhooks.rotate',
  title: 'Give a webhook a new secret',
  kind: 'change',
  tico: false,
  input: s.object({ hook: id }),
  run: async ({ router }, i) => {
    const url = await said(rotateHook(i.hook));
    await go(router, ROUTINES, { tab: 'webhooks', panel: `hook:${i.hook}` });
    return { url };
  },
  did: () => 'New secret made',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

/* A signing secret shows once, to the person who set it: the action hands it back as its output (never to Tico, never
   cached), and the panel holds it until it closes. */
export const signWebhook = defineAction({
  id: 'webhooks.sign',
  title: "Check a webhook's calls with a signing secret",
  kind: 'change',
  tico: false,
  input: s.object({
    hook: id,
    preset: s.oneOf(['hmac', 'github', 'standard', 'stripe', 'slack'] as const),
    secret: s.optional(s.string({ max: 300 })),
  }),
  run: (_ctx, i) => said(signHook(i.hook, i.preset, i.secret?.trim() || undefined)),
  did: () => 'Signing on',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

export const signWebhookOff = defineAction({
  id: 'webhooks.signOff',
  title: "Stop checking a webhook's signature",
  kind: 'change',
  tico: false,
  input: s.object({ hook: id }),
  run: (_ctx, i) => said(signHookOff(i.hook)).then(() => undefined),
  did: () => 'Signing off',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

export const removeWebhook = defineAction({
  id: 'webhooks.remove',
  title: 'Remove a webhook',
  kind: 'change',
  input: s.object({ hook: id }),
  // Its address and secret are gone for good, and whoever calls it is refused: the person's own press.
  tico: false,
  can: (ctx, i) => !ctx.queryClient.getQueryData<{ id: string; builtin?: boolean }[]>(hooksQuery.queryKey)?.find((h) => h.id === i.hook)?.builtin,
  run: async ({ router }, i) => {
    await said(removeHook(i.hook));
    await panelOff(router);
  },
  did: () => 'Removed the webhook',
});

// ---------------------------------------------------------------- rules: views

const ruleTab = (router: AnyRouter, t: RuleTab, panel?: string) => go(router, RULES, { tab: t || undefined, panel: panel || undefined });

export const openRule = defineAction({
  id: 'rules.open',
  title: 'Open a rule',
  kind: 'view',
  input: s.object({ rule: id, kind: s.oneOf(['org', 'stage'] as const), tab: s.optional(s.oneOf(RULE_TABS)) }),
  run: ({ router }, i) => ruleTab(router, i.tab ?? (i.kind === 'stage' ? 'stages' : ''), `${i.kind}:${i.rule}`),
  did: () => 'Opened the rule',
  target: (i) => ({ kind: 'rule', id: i.rule }),
});

export const newRule = defineAction({
  id: 'rules.new',
  title: 'Start a new rule',
  kind: 'view',
  input: s.object({ kind: s.oneOf(['org', 'stage', 'outside'] as const) }),
  run: ({ router }, i) => ruleTab(router, i.kind === 'stage' ? 'stages' : i.kind === 'outside' ? 'outside' : '', `${i.kind === 'stage' ? 'stage' : 'org'}:new`),
  did: () => 'Opened a new rule',
  target: () => ({ kind: 'panel', id: 'rule:new' }),
});

export const pickScope = defineAction({
  id: 'rules.pickScope',
  title: 'Show the rules for a team, an agent or a person',
  kind: 'view',
  input: s.object({ who: party }),
  run: ({ router }, i) => go(router, RULES, i.who === '*' ? {} : { who: i.who }),
  did: () => 'Opened their rules',
  target: (i) => ({ kind: 'scope', id: i.who }),
});

/** Due dates by status, as the Due tab's filter names them. */
export const listRules = defineAction({
  id: 'rules.list',
  title: 'Search the rules on a tab',
  kind: 'view',
  input: s.object({ tab: s.optional(s.oneOf(RULE_TABS)), q: s.optional(s.string({ max: 200 })) }),
  run: ({ router }, i) => {
    // whose rules (Company) stay as they are
    const prev = router.state.location.search as { tab?: string; who?: string };
    return go(router, RULES, { tab: i.tab === undefined ? prev.tab : i.tab || undefined, who: prev.who, q: i.q?.trim() || undefined });
  },
  did: () => 'Listed the rules',
  target: (i) => ({ kind: 'tab', id: i.tab || 'company' }),
});

export const closeRule = defineAction({
  id: 'rules.closePanel',
  title: 'Close the rule',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => panelOff(router),
  did: () => 'Closed the rule',
});

// ---------------------------------------------------------------- rules: changes

const orgRule = (ctx: Ctx, rid: string): OrgRule | undefined =>
  [...(ctx.queryClient.getQueryData<OrgRule[]>(orgRulesQuery('active').queryKey) ?? []), ...(ctx.queryClient.getQueryData<OrgRule[]>(orgRulesQuery('suggested').queryKey) ?? [])].find(
    (r) => r.id === rid,
  );
const stageRule = (ctx: Ctx, rid: string): StageRule | undefined =>
  [...(ctx.queryClient.getQueryData<StageRules>(stageRulesQuery('active').queryKey)?.items ?? []), ...(ctx.queryClient.getQueryData<StageRules>(stageRulesQuery('suggested').queryKey)?.items ?? [])].find(
    (r) => r.id === rid,
  );
const missing = () => Object.assign(new Error('That rule is not loaded'), { code: 'not_found: That rule is not loaded' });

const SOURCE_KINDS = ['vendor', 'customer', 'government', 'other'] as const;
const orgFields = {
  text: s.string({ min: 1, max: 2000 }),
  ifBroken: s.oneOf(['block', 'escalate', 'warn'] as const),
  // under Ask first: the person who OKs it; left out, the manager above whoever broke it
  reviewer: s.optional(party),
  // who follows it: ['*'] (Everyone, the default) or teams, agents and people
  who: s.optional(s.array(party, { max: 100 })),
  outside: s.optional(s.boolean()),
  // an outside rule: who it is from, an existing From's id, or a new one by name and kind
  source: s.optional(id),
  newSource: s.optional(s.object({ name: s.string({ min: 1, max: 200 }), kind: s.oneOf(SOURCE_KINDS) })),
};
type OrgIn = { text: string; ifBroken: 'block' | 'escalate' | 'warn'; reviewer?: string; who?: string[]; outside?: boolean; source?: string; newSource?: { name: string; kind: string } };

/** An outside rule's From: the one picked, or a new one made first. */
async function sourceOf(o: OrgIn, was?: string): Promise<string | undefined> {
  if (o.newSource) return (await said(addRuleSource(o.newSource.name.trim(), o.newSource.kind))).source.id;
  return o.source ?? was;
}
const orgOut = (o: OrgIn, kind: 'internal' | 'external', source?: string): OrgRuleFields => ({
  kind,
  text: o.text.trim(),
  if_broken: o.ifBroken,
  reviewer: o.reviewer?.replace(/^human:/, '') || null,
  who: o.who?.length ? o.who : ['*'],
  ...(source ? { source_id: source } : {}),
});
const noFrom = () => Object.assign(new Error('Pick who it is from'), { code: 'bad_input: Pick who it is from' });
const stageFields = {
  name: s.optional(s.string({ max: 200 })),
  pipeline: s.string({ max: 100 }),
  stages: s.array(s.string({ min: 1, max: 100 }), { max: 50 }),
  event: s.string({ min: 1, max: 100, pattern: /^(timer|(enters|owner_changes|bot_snoozes)(,(enters|owner_changes|bot_snoozes))*)$/, what: 'enters, owner_changes, bot_snoozes or timer' }),
  timerKind: s.optional(s.oneOf(['in_stage', 'quiet', 'waiting_person'] as const)),
  timerHours: s.optional(s.number({ min: 0.25, max: 2000 })),
  decisionModel: s.optional(s.string({ max: 2000 })),
  then: s.oneOf(['block', 'tell_manager', 'tell_owner', 'move_back'] as const),
  reason: s.optional(s.string({ max: 2000 })),
};

export const addRule = defineAction({
  id: 'rules.add',
  title: 'Add a rule',
  kind: 'change',
  // `stay`: keep the page where it is (the pipeline editor) instead of opening the new rule on the Rules page
  input: s.object({ kind: s.oneOf(['org', 'stage'] as const), org: s.optional(s.object(orgFields)), stage: s.optional(s.object(stageFields)), stay: s.optional(s.boolean()) }),
  run: async ({ router }, i) => {
    if (i.kind === 'org' && i.org) {
      const o = i.org;
      // the server refuses an outside rule with no From
      const source = o.outside ? await sourceOf(o) : undefined;
      if (o.outside && !source) throw noFrom();
      const made = await said(addOrgRule(orgOut(o, o.outside ? 'external' : 'internal', source)));
      await ruleTab(router, made.status === 'suggested' ? 'suggested' : o.outside ? 'outside' : '', made.status === 'suggested' ? '' : `org:${made.id}`);
      return made.id;
    }
    if (i.kind === 'stage' && i.stage) {
      const t = i.stage;
      const made = await said(
        addStageRule({
          name: t.name ?? '',
          pipeline: t.pipeline,
          stages: t.stages,
          event: t.event,
          timer: t.event === 'timer' ? { kind: t.timerKind ?? 'in_stage', hours: t.timerHours ?? 24 } : null,
          cond: {},
          jev: t.decisionModel ?? '',
          then: t.event === 'timer' ? t.then : 'block',
          reason: t.reason ?? '',
        }),
      );
      if (!i.stay) await ruleTab(router, 'stages', `stage:${made.id}`);
      return made.id;
    }
    throw Object.assign(new Error('Say what the rule is'), { code: 'bad_input: org or stage is required' });
  },
  did: () => 'Added the rule',
  target: (_i, out) => (out ? { kind: 'rule', id: out } : null),
});

export const updateRule = defineAction({
  id: 'rules.update',
  title: 'Change a rule',
  kind: 'change',
  input: s.object({ rule: id, kind: s.oneOf(['org', 'stage'] as const), org: s.optional(s.object(orgFields)), stage: s.optional(s.object(stageFields)) }),
  can: (ctx, i) => (i.kind === 'org' ? orgRule(ctx, i.rule)?.can_edit !== false : stageRule(ctx, i.rule)?.can_edit !== false),
  run: async (ctx, i) => {
    if (i.kind === 'org' && i.org) {
      const was = orgRule(ctx, i.rule);
      if (!was) throw missing();
      const o = i.org;
      const source = was.kind === 'external' ? await sourceOf(o, was.source?.id) : undefined;
      // a Who left out keeps the one it has, every team, agent and person in it
      await said(updateOrgRule(was, orgOut({ ...o, who: o.who ?? partiesOf(was.who) }, was.kind, source)));
      return;
    }
    if (i.kind === 'stage' && i.stage) {
      const was = stageRule(ctx, i.rule);
      if (!was) throw missing();
      const t = i.stage;
      await said(
        updateStageRule(was, {
          name: t.name ?? was.name,
          pipeline: t.pipeline,
          stages: t.stages,
          event: t.event,
          timer: t.event === 'timer' ? { kind: t.timerKind ?? 'in_stage', hours: t.timerHours ?? 24 } : null,
          cond: was.cond ?? {},
          jev: t.decisionModel ?? '',
          then: t.event === 'timer' ? t.then : 'block',
          reason: t.reason ?? was.reason ?? '',
        }),
      );
    }
  },
  did: () => 'Saved the rule',
  target: (i) => ({ kind: 'rule', id: i.rule }),
});

export const removeRule = defineAction({
  id: 'rules.remove',
  title: 'Remove a rule',
  kind: 'change',
  input: s.object({ rule: id, kind: s.oneOf(['org', 'stage'] as const), stay: s.optional(s.boolean()) }),
  can: (ctx, i) => (i.kind === 'org' ? orgRule(ctx, i.rule)?.can_edit !== false : stageRule(ctx, i.rule)?.can_edit !== false),
  run: async (ctx, i) => {
    if (i.kind === 'org') {
      const was = orgRule(ctx, i.rule);
      if (!was) throw missing();
      await said(decideOrgRule(was, 'retire'));
    } else {
      const was = stageRule(ctx, i.rule);
      if (!was) throw missing();
      await said(decideStageRule(was, 'retire'));
    }
    if (!i.stay) await panelOff(ctx.router);
  },
  did: () => 'Removed the rule',
});

export const acceptRule = defineAction({
  id: 'rules.accept',
  title: 'Add a suggested rule',
  kind: 'change',
  // `who`: who follows it, for a suggestion that names no one (the server takes none without)
  input: s.object({ rule: id, kind: s.oneOf(['org', 'stage'] as const), who: s.optional(s.array(party, { max: 100 })) }),
  run: async (ctx, i) => {
    if (i.kind === 'org') {
      const was = orgRule(ctx, i.rule);
      if (!was) throw missing();
      if (!i.who?.length && !hasWho(was)) throw Object.assign(new Error('Say who follows it'), { code: 'bad_input: Say who follows it' });
      await said(decideOrgRule(was, 'accept', i.who));
    } else {
      const was = stageRule(ctx, i.rule);
      if (!was) throw missing();
      await said(decideStageRule(was, 'accept'));
    }
  },
  did: () => 'Added the rule',
  target: (i) => ({ kind: 'rule', id: i.rule }),
});

/** A suggestion names who follows it (Everyone, a team, an agent or a person). */
export const hasWho = (r: Pick<OrgRule, 'who'>) => !!r.who && (!!r.who.everyone || !!r.who.all_bots || !!(r.who.groups?.length || r.who.bots?.length || r.who.people?.length));

export const skipRule = defineAction({
  id: 'rules.skip',
  title: 'Set a suggested rule aside',
  kind: 'change',
  input: s.object({ rule: id, kind: s.oneOf(['org', 'stage'] as const) }),
  run: async (ctx, i) => {
    if (i.kind === 'org') {
      const was = orgRule(ctx, i.rule);
      if (!was) throw missing();
      await said(decideOrgRule(was, 'skip'));
    } else {
      const was = stageRule(ctx, i.rule);
      if (!was) throw missing();
      await said(decideStageRule(was, 'skip'));
    }
  },
  did: () => 'Set aside',
});

export const setReachAction = defineAction({
  id: 'rules.setReach',
  title: 'Set who a teammate may reach, and how much a day',
  kind: 'change',
  input: s.object({
    who: party,
    mode: s.optional(s.nullable(s.oneOf(['open', 'nearby', 'closed'] as const))),
    book: s.optional(s.nullable(s.oneOf(['anyone', 'nearby', 'none'] as const))),
    chatsPerDay: s.optional(s.nullable(s.number({ int: true, min: 0, max: 10_000 }))),
    tasksPerDay: s.optional(s.nullable(s.number({ int: true, min: 0, max: 10_000 }))),
  }),
  run: (_ctx, i) =>
    said(
      setReach(i.who, {
        ...(i.mode !== undefined ? { mode: i.mode } : {}),
        ...(i.book !== undefined ? { book: i.book } : {}),
        ...(i.chatsPerDay !== undefined ? { chats_per_day: i.chatsPerDay } : {}),
        ...(i.tasksPerDay !== undefined ? { tasks_per_day: i.tasksPerDay } : {}),
      }),
    ),
  did: () => 'Saved',
  target: (i) => ({ kind: 'scope', id: i.who }),
});

// ---------------------------------------------------------------- rules: due dates

export const openDue = defineAction({
  id: 'rules.openDue',
  title: 'Open a due date',
  kind: 'view',
  input: s.object({ due: id }),
  run: ({ router }, i) => ruleTab(router, 'due', `due:${i.due}`),
  did: () => 'Opened the due date',
  target: (i) => ({ kind: 'due', id: i.due }),
});

export const newDue = defineAction({
  id: 'rules.newDue',
  title: 'Start a new due date',
  kind: 'view',
  // `rule`: the rule it is for, already picked
  input: s.object({ rule: s.optional(id) }),
  run: ({ router }, i) => ruleTab(router, 'due', i.rule ? `due:new:${i.rule}` : 'due:new'),
  did: () => 'Opened a new due date',
  target: () => ({ kind: 'panel', id: 'due:new' }),
});

export const addDueAction = defineAction({
  id: 'rules.addDue',
  title: 'Add a due date to a rule',
  kind: 'change',
  input: s.object({
    rule: id,
    due: day,
    repeats: s.oneOf(REPEATS),
    owner,
    // a task opens this many days before each due day
    taskDays: s.optional(s.number({ int: true, min: 0, max: 366 })),
  }),
  run: async ({ router }, i) => {
    const made = await said(addDue(i.rule, { first_due: i.due, repeats: i.repeats, owner: i.owner, ...(i.taskDays !== undefined ? { task_days: i.taskDays } : {}) }));
    await ruleTab(router, 'due', `due:${made.id}`);
    return made.id;
  },
  did: () => 'Added the due date',
  target: (_i, out) => (out ? { kind: 'due', id: out } : null),
});

export const updateDueAction = defineAction({
  id: 'rules.updateDue',
  title: 'Change a due date',
  kind: 'change',
  input: s.object({
    due: id,
    first: s.optional(day),
    repeats: s.optional(s.oneOf(REPEATS)),
    owner: s.optional(owner),
    // null: no task before it
    taskDays: s.optional(s.nullable(s.number({ int: true, min: 0, max: 366 }))),
  }),
  run: async ({ router }, i) => {
    const saved = await said(
      updateDue(i.due, {
        ...(i.first ? { first_due: i.first } : {}),
        ...(i.repeats ? { repeats: i.repeats } : {}),
        ...(i.owner ? { owner: i.owner } : {}),
        ...(i.taskDays !== undefined ? { task_days: i.taskDays } : {}),
      }),
    );
    // a new date or repeat ends this row and starts another: open the live one
    if (saved?.id && saved.id !== i.due) await ruleTab(router, 'due', `due:${saved.id}`);
    return saved?.id;
  },
  did: () => 'Saved',
  target: (i, out) => ({ kind: 'due', id: out ?? i.due }),
});

export const satisfyDueAction = defineAction({
  id: 'rules.satisfyDue',
  title: 'Mark a due date satisfied',
  kind: 'change',
  input: s.object({ due: id, on: s.optional(day) }),
  run: (_ctx, i) => said(satisfyDue(i.due, i.on)).then(() => undefined),
  did: () => 'Marked satisfied',
  target: (i) => ({ kind: 'due', id: i.due }),
});

export const removeDueAction = defineAction({
  id: 'rules.removeDue',
  title: 'Remove a due date',
  kind: 'change',
  input: s.object({ due: id }),
  run: async ({ router }, i) => {
    await said(removeDue(i.due));
    await panelOff(router);
  },
  did: () => 'Removed the due date',
});

// ---------------------------------------------------------------- rules: a document to read

export const readDocumentAction = defineAction({
  id: 'rules.readDocument',
  title: 'Have a document read for outside rules',
  kind: 'change',
  input: s.object({
    file: s.optional(id),
    url: s.optional(s.string({ min: 8, max: 2000, pattern: /^https?:\/\/\S+$/i, what: 'an http(s) link' })),
    note: s.optional(s.string({ max: 2000 })),
    // an existing From's id, or a new From's name with `kind`
    source: s.string({ min: 1, max: 200 }),
    kind: s.optional(s.oneOf(['vendor', 'customer', 'government', 'other'] as const)),
  }),
  run: (_ctx, i) => {
    if (!i.file === !i.url) throw Object.assign(new Error('Pick a file or paste a link'), { code: 'bad_input: Pick a file or paste a link' });
    return said(readDocument({ ...(i.file ? { file_id: i.file } : { url: i.url }), note: i.note?.trim() ?? '', source_id: i.source.trim(), ...(i.kind ? { kind: i.kind } : {}) }));
  },
  did: (_i, out) => (out?.screening === 'hold' ? 'Screening held it' : 'Reading it'),
  target: (_i, out) => (out ? { kind: 'task', id: out.task_id } : null),
});

// ---------------------------------------------------------------- listening

export const openWatch = defineAction({
  id: 'listening.open',
  title: 'Open a listening watch',
  kind: 'view',
  input: s.object({ watch: id, tab: s.optional(s.oneOf(['matches', 'sources'] as const)) }),
  run: ({ router }, i) => go(router, LISTENING, { watch: i.watch, tab: i.tab === 'sources' ? 'sources' : undefined }),
  did: () => 'Opened the watch',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const openMatch = defineAction({
  id: 'listening.openMatch',
  title: 'Open a match',
  kind: 'view',
  input: s.object({ watch: id, match: s.nullable(id) }),
  run: ({ router }, i) => go(router, LISTENING, { watch: i.watch, match: i.match ?? undefined }),
  did: () => 'Opened the match',
  target: (i) => (i.match ? { kind: 'match', id: i.match } : null),
});

export const listWatches = defineAction({
  id: 'listening.list',
  title: 'Search the listening watches',
  kind: 'view',
  input: s.object({ q: s.optional(s.string({ max: 200 })) }),
  run: ({ router }, i) => go(router, LISTENING, { q: i.q?.trim() || undefined }),
  did: () => 'Listed the watches',
  target: () => ({ kind: 'section', id: 'watches' }),
});

export const addWatch = defineAction({
  id: 'listening.add',
  title: 'Add a listening watch',
  kind: 'change',
  input: s.object({ name }),
  run: async ({ router, queryClient }, i) => {
    const me = await queryClient.ensureQueryData(meQuery);
    const watch = await said(createWatch(i.name.trim(), me.id));
    await go(router, LISTENING, { watch, tab: 'sources' });
    return watch;
  },
  did: (i) => `Added ${i.name}`,
  target: (_i, watch) => (watch ? { kind: 'watch', id: watch } : null),
});

const watchOf = (ctx: Ctx, wid: string): Routine | undefined =>
  ctx.queryClient.getQueryData<Routine>(watchQuery(wid).queryKey) ?? ctx.queryClient.getQueryData<Routine[]>(watchesQuery.queryKey)?.find((w) => w.id === wid);

export const pauseWatch = defineAction({
  id: 'listening.pause',
  title: 'Pause a listening watch',
  kind: 'change',
  input: s.object({ watch: id }),
  run: (_ctx, i) => said(setRoutineOn(i.watch, false, watchesQuery.queryKey, watchQuery(i.watch).queryKey).then(() => undefined)),
  did: () => 'Paused',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const resumeWatch = defineAction({
  id: 'listening.resume',
  title: 'Turn a listening watch back on',
  kind: 'change',
  input: s.object({ watch: id }),
  run: (_ctx, i) => said(setRoutineOn(i.watch, true, watchesQuery.queryKey, watchQuery(i.watch).queryKey).then(() => undefined)),
  did: () => 'Turned on',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const removeWatch = defineAction({
  id: 'listening.remove',
  title: 'Remove a listening watch',
  kind: 'change',
  input: s.object({ watch: id }),
  // The same delete as a routine's: nothing brings it back.
  tico: false,
  run: async ({ router }, i) => {
    await said(removeRoutine(i.watch, watchesQuery.queryKey));
    await go(router, LISTENING);
  },
  did: () => 'Removed the watch',
});

const REPO = /^[\w.-]+\/[\w.-]+$/;
const repoOf = (v: string) => v.trim().replace(/^https?:\/\/(www\.)?github\.com\//i, '').replace(/\.git$|\/+$/g, '');
const badSource = (why: string) => Object.assign(new Error(why), { code: `bad_input: ${why}` });

export const addSource = defineAction({
  id: 'listening.addSource',
  title: 'Add a source to a listening watch',
  kind: 'change',
  input: s.object({
    watch: id,
    // search: words on a site; page: an address; github: owner/repo; push: a name (posts are sent to its own address)
    kind: s.oneOf(['search', 'page', 'github', 'push'] as const),
    target: s.optional(s.string({ max: 500 })),
    site: s.optional(s.string({ max: 200 })),
    follow: s.optional(s.oneOf(['prs', 'releases'] as const)),
  }),
  // A push source's address carries a secret, shown once: the person's own press.
  can: (ctx, i) => !(i.kind === 'push' && ctx.via === 'tico'),
  run: async (ctx, i) => {
    const w = watchOf(ctx, i.watch);
    if (!w) throw Object.assign(new Error('That watch is not loaded'), { code: 'not_found: That watch is not loaded' });
    const sid = `src_${Math.random().toString(36).slice(2, 10)}`;
    const target = i.target?.trim() ?? '';
    const site = i.site?.trim() ?? '';
    let src: ListenSource;
    if (i.kind === 'push') {
      if (!site) throw badSource('Name the push source');
      src = { id: sid, kind: 'push', site };
    } else if (i.kind === 'github') {
      const repo = repoOf(target);
      if (!REPO.test(repo)) throw badSource('A GitHub source is owner/repo');
      src = { id: sid, kind: 'github', site: 'github', target: repo, follow: i.follow ?? 'prs' };
    } else {
      if (!target) throw badSource('Say what to read');
      if (i.kind === 'page' && !/^https?:\/\/\S+$/i.test(target)) throw badSource('A page is a web address');
      src = { id: sid, kind: i.kind, target, ...(site ? { site } : i.kind === 'page' ? { site: target.replace(/^https?:\/\/(www\.)?/i, '').split('/')[0] ?? '' } : {}) };
    }
    const saved = await said(setWatchTrigger(w, { ...w.trigger, sources: [...(w.trigger.sources ?? []), src] }));
    // the push address goes back to the press that made it, held in the page's state only
    return { address: saved.push_addresses?.[sid] };
  },
  did: () => 'Added the source',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

const TAG = /^[a-z0-9][a-z0-9-]{0,23}$/;

export const updateWatch = defineAction({
  id: 'listening.update',
  title: 'Rename a listening watch, or change what it keeps',
  kind: 'change',
  input: s.object({
    watch: id,
    name: s.optional(name),
    // keep (with a tag) or drop what a sentence describes
    rules: s.optional(s.array(s.object({ do: s.oneOf(['keep', 'drop'] as const), tag: s.optional(s.string({ max: 24 })), text: s.string({ min: 1, max: 300 }) }), { max: 20 })),
  }),
  run: (ctx, i) => {
    const w = watchOf(ctx, i.watch);
    if (!w) throw Object.assign(new Error('That watch is not loaded'), { code: 'not_found: That watch is not loaded' });
    const tags = (i.rules ?? []).filter((r) => r.do === 'keep').map((r) => r.tag?.trim() ?? '');
    if (tags.some((t) => !TAG.test(t))) throw badSource('A tag is lowercase letters, numbers and dashes');
    if (new Set(tags).size !== tags.length) throw badSource('Each tag is used once');
    const trigger: Trigger = {
      ...w.trigger,
      ...(i.rules ? { rules: i.rules.map((r) => (r.do === 'drop' ? { do: 'drop', text: r.text.trim() } : { do: 'keep', tag: r.tag?.trim() ?? '', text: r.text.trim() })) } : {}),
    };
    return said(setWatchTrigger(w, trigger, i.name?.trim())).then(() => undefined);
  },
  did: () => 'Saved',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const removeSource = defineAction({
  id: 'listening.removeSource',
  title: 'Remove a source from a listening watch',
  kind: 'change',
  input: s.object({ watch: id, source: id }),
  run: (ctx, i) => {
    const w = watchOf(ctx, i.watch);
    if (!w) throw Object.assign(new Error('That watch is not loaded'), { code: 'not_found: That watch is not loaded' });
    return said(setWatchTrigger(w, { ...w.trigger, sources: (w.trigger.sources ?? []).filter((x) => x.id !== i.source) }).then(() => undefined));
  },
  did: () => 'Removed the source',
});
