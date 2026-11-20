import type { AnyRouter } from '@tanstack/react-router';
import * as z from 'zod/mini';
import { defineAction, type Ctx } from '@/actions';
import { meQuery } from '@/data';
import { ACTS, applyFlow, dropItem, flowOf, putItem, routineBoard, type Dest, type Part } from './board';
import { dropStep, findStep, newTrigger, putStep, stepOn, type FlowStep } from './flow';
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
  createBlank,
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
  renameRoutine,
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
  setTrigger,
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

export const ROUTINES = '/company/automations';
/** One automation's own page: its flow, `?view=runs` its runs, `?edit=` the step or part open in the panel. */
export const automationHref = (id: string) => `${ROUTINES}/${encodeURIComponent(id)}`;
export const RULES = '/company/rules';
export const LISTENING = '/company/listening';

/** The Rules tabs, in the mock's order: `?tab=` ('' is Company). */
export const RULE_TABS = ['', 'stages', 'built-in', 'outside', 'due', 'wakes', 'on-tasks', 'suggested'] as const;
export type RuleTab = (typeof RULE_TABS)[number];
export const BUILTINS = ['maintenance', 'heartbeat', 'system'] as const;
export type Builtin = (typeof BUILTINS)[number];

const id = z.string().check(z.minLength(1), z.maxLength(200));
const agent = z.string().check(z.minLength(1), z.maxLength(200), z.regex(/^bot:\S+$/), z.describe('bot:<id>'));
const party = z.string().check(z.minLength(1), z.maxLength(200), z.regex(/^(\*|(group|bot|human):\S+)$/), z.describe('*, group:<id>, bot:<id> or human:<id>'));
const cron = z.string().check(z.minLength(9), z.maxLength(100), z.regex(/^\S+( \S+){4}$/), z.describe('a five-part cron, like 0 9 * * 1-5'));
const name = z.string().check(z.minLength(1), z.maxLength(200));
const owner = z.string().check(z.minLength(1), z.maxLength(200), z.regex(/^(bot|human):\S+$/), z.describe('bot:<id> or human:<id>'));
const day = z.string().check(z.minLength(10), z.maxLength(10), z.regex(/^\d{4}-\d{2}-\d{2}$/), z.describe('a day, YYYY-MM-DD'));
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
  id: 'automations.open',
  title: 'Open an automation, on its flow or its runs',
  kind: 'view',
  input: z.strictObject({ routine: id, view: z.optional(z.enum(['flow', 'runs'] as const)) }),
  run: ({ router }, { routine, view }) => go(router, automationHref(routine), { view: view === 'runs' ? 'runs' : undefined }),
  did: () => 'Opened the automation',
  target: ({ routine }) => ({ kind: 'routine', id: routine }),
});

export const openBuiltin = defineAction({
  id: 'automations.openBuiltin',
  title: 'Open Maintenance, Heartbeat or System jobs',
  kind: 'view',
  input: z.strictObject({ which: z.enum(BUILTINS) }),
  run: ({ router }, { which }) => go(router, ROUTINES, { panel: `builtin:${which}` }),
  did: () => 'Opened it',
  target: ({ which }) => ({ kind: 'routine', id: which }),
});

export const newRoutine = defineAction({
  id: 'automations.new',
  title: 'Start a new routine',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => go(router, ROUTINES, { panel: 'routine:new' }),
  did: () => 'Opened a new routine',
  target: () => ({ kind: 'panel', id: 'routine:new' }),
});

export const closePanel = defineAction({
  id: 'automations.closePanel',
  title: 'Close the panel',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => panelOff(router),
  did: () => 'Closed the panel',
});

export const showRuns = defineAction({
  id: 'automations.showRuns',
  title: 'Show every run across routines',
  kind: 'view',
  input: z.strictObject({
    status: z.optional(z.enum(RUN_STATUSES)),
    range: z.optional(z.enum(RUN_RANGES)),
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
  id: 'automations.create',
  title: 'Add a routine',
  kind: 'change',
  input: z.strictObject({
    name,
    agent,
    instruction: z.string().check(z.minLength(1), z.maxLength(20_000)),
    cron: z.optional(cron),
    event: z.optional(z.string().check(z.minLength(1), z.maxLength(100))),
  }),
  run: async ({ router }, i) => {
    const made = await said(createRoutine({ name: i.name.trim(), agent: i.agent, instruction: i.instruction.trim(), trigger: triggerOf(i) }));
    await go(router, automationHref(made.id));
    return made;
  },
  did: (i) => `Added ${i.name}`,
  target: (_i, out) => (out ? { kind: 'routine', id: out.id } : null),
});

export const updateRoutineAction = defineAction({
  id: 'automations.update',
  title: 'Change a routine',
  kind: 'change',
  input: z.strictObject({
    routine: id,
    name: z.optional(name),
    agent: z.optional(agent),
    instruction: z.optional(z.string().check(z.minLength(1), z.maxLength(20_000))),
    cron: z.optional(cron),
    event: z.optional(z.string().check(z.minLength(1), z.maxLength(100), z.regex(/^[a-z][a-z0-9_]*(\.[a-z0-9_*-]+)+$/), z.describe('an event name, like task.created'))),
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
  id: 'automations.runNow',
  title: 'Run a routine now',
  kind: 'change',
  input: z.strictObject({ routine: id }),
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
  id: 'automations.testRun',
  title: 'Test a routine with sample input',
  kind: 'change',
  // `sample`: JSON standing in for what its trigger gives (a message, an event, a webhook body)
  input: z.strictObject({ routine: id, sample: z.optional(z.string().check(z.maxLength(50_000))) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (_ctx, i) => {
    const r = await said(runRoutine(i.routine, sampleOf(i.sample)));
    return { dropped: !!r.dropped };
  },
  did: (_i, out) => (out?.dropped ? "Dropped by the trigger's filters" : 'Started a test run'),
  target: (i) => ({ kind: 'routine-runs', id: i.routine }),
});

export const restoreVersionAction = defineAction({
  id: 'automations.restoreVersion',
  title: "Restore a routine's version, or turn on a proposed one",
  kind: 'change',
  input: z.strictObject({ routine: id, n: z.int().check(z.minimum(1), z.maximum(1_000_000)) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(restoreVersion(i.routine, i.n)).then(() => undefined),
  did: (i) => `Restored v${i.n}`,
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const discardVersionAction = defineAction({
  id: 'automations.discardVersion',
  title: "Discard an agent's proposed version",
  kind: 'change',
  input: z.strictObject({ routine: id, n: z.int().check(z.minimum(1), z.maximum(1_000_000)) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(discardVersion(i.routine, i.n)).then(() => undefined),
  did: (i) => `Discarded v${i.n}`,
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const pause = defineAction({
  id: 'automations.pause',
  title: 'Pause a routine',
  kind: 'change',
  input: z.strictObject({ routine: id }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_pause !== false,
  run: (_ctx, i) => said(setRoutineOn(i.routine, false)),
  did: () => 'Paused',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const setLimit = defineAction({
  id: 'automations.setLimit',
  title: "Set a routine's spend limit",
  kind: 'change',
  input: z.strictObject({ routine: id, per30m: z.nullable(z.number().check(z.minimum(0), z.maximum(100_000))) }),
  // A spending limit is budget: the person's own press.
  tico: false,
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(setRoutineLimit(i.routine, i.per30m)),
  did: (i) => (i.per30m === null ? "Uses the agent's limit" : 'Limit set'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const resume = defineAction({
  id: 'automations.resume',
  title: 'Turn a routine back on',
  kind: 'change',
  input: z.strictObject({ routine: id }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_pause !== false,
  run: (_ctx, i) => said(setRoutineOn(i.routine, true)),
  did: () => 'Turned on',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

/** The run guard held its runs (too many at once): release them in order, or drop them. */
export const endHoldAction = defineAction({
  id: 'automations.endHold',
  title: "Release or drop a routine's held runs",
  kind: 'change',
  input: z.strictObject({ routine: id, action: z.enum(['release', 'drop'] as const) }),
  // Dropped runs are skipped for good: the person's own press.
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false && !(i.action === 'drop' && ctx.via === 'tico'),
  run: (_ctx, i) => said(endHold(i.routine, i.action)).then(() => undefined),
  did: (i) => (i.action === 'release' ? 'Released' : 'Dropped'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const removeRoutineAction = defineAction({
  id: 'automations.remove',
  title: 'Remove a routine',
  kind: 'change',
  input: z.strictObject({ routine: id }),
  // Nothing brings a removed routine back, and what it held is dropped: the person's own press.
  tico: false,
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async ({ router }, i) => {
    await said(removeRoutine(i.routine));
    await go(router, ROUTINES);
  },
  did: () => 'Removed the routine',
});

export const setMaintenanceAction = defineAction({
  id: 'automations.setMaintenance',
  title: 'Change Maintenance',
  kind: 'change',
  input: z.strictObject({
    schedule: z.optional(z.enum(['hourly', '2h', '3h', '6h', '12h', 'daily'] as const)),
    paused: z.optional(z.boolean()),
    step: z.optional(z.string().check(z.minLength(1), z.maxLength(60))),
    stepOn: z.optional(z.boolean()),
  }),
  run: (_ctx, i) =>
    said(setMaintenance({ schedule: i.schedule, paused: i.paused, step: i.step !== undefined ? { key: i.step, enabled: i.stepOn ?? true } : undefined })),
  did: () => 'Saved',
  target: () => ({ kind: 'routine', id: 'maintenance' }),
});

export const runMaintenanceAction = defineAction({
  id: 'automations.runMaintenance',
  title: 'Run Maintenance now',
  kind: 'change',
  input: z.strictObject({}),
  run: () => said(runMaintenance()),
  did: () => 'Started Maintenance',
  target: () => ({ kind: 'routine-runs', id: 'maintenance' }),
});

export const setHeartbeatAction = defineAction({
  id: 'automations.setHeartbeat',
  title: 'Set how often Heartbeat checks',
  kind: 'change',
  input: z.strictObject({ seconds: z.int().check(z.minimum(5), z.maximum(60)) }),
  run: (_ctx, i) => said(setHeartbeat(i.seconds)),
  did: (i) => `Heartbeat every ${i.seconds} s`,
  target: () => ({ kind: 'routine', id: 'heartbeat' }),
});

export const runJobAction = defineAction({
  id: 'automations.runJob',
  title: 'Run a system job now',
  kind: 'change',
  input: z.strictObject({ job: z.string().check(z.minLength(1), z.maxLength(100)) }),
  run: (_ctx, i) => said(runJob(i.job)),
  did: () => 'Started it',
  target: (i) => ({ kind: 'job', id: i.job }),
});

export const setJobAction = defineAction({
  id: 'automations.setJob',
  title: 'Turn a system job on or off',
  kind: 'change',
  input: z.strictObject({ job: z.string().check(z.minLength(1), z.maxLength(100)), on: z.boolean() }),
  run: (_ctx, i) => said(setJob(i.job, i.on)),
  did: (i) => (i.on ? 'Turned on' : 'Turned off'),
  target: (i) => ({ kind: 'job', id: i.job }),
});

export const JOB_PRESETS = ['15m', 'hourly', '4h', 'daily', 'business'] as const;

export const setJobScheduleAction = defineAction({
  id: 'automations.setJobSchedule',
  title: 'Pick when a system job runs: a preset, or its daily time',
  kind: 'change',
  input: z.strictObject({
    job: z.string().check(z.minLength(1), z.maxLength(100)),
    preset: z.optional(z.enum(JOB_PRESETS)),
    at: z.optional(z.string().check(z.minLength(5), z.maxLength(5), z.regex(/^([01]\d|2[0-3]):[0-5]\d$/), z.describe('a time, HH:MM'))),
  }),
  run: (_ctx, i) => said(setJobSchedule(i.job, { preset: i.preset, at: i.at })),
  did: (i) => (i.at && !i.preset ? `Runs at ${i.at}` : 'Schedule changed'),
  target: (i) => ({ kind: 'job', id: i.job }),
});

/** Search and filter the routines list; both live in the address. */
export const ROUTINE_STATES = ['on', 'failed', 'hold', 'off'] as const;
export type RoutineState = (typeof ROUTINE_STATES)[number];

export const listRoutines = defineAction({
  id: 'automations.list',
  title: 'Search or filter the automations or their runs, or pick the time range',
  kind: 'view',
  input: z.strictObject({
    q: z.optional(z.string().check(z.maxLength(200))),
    state: z.optional(z.enum(ROUTINE_STATES)),
    range: z.optional(z.enum(RUN_RANGES)),
    // the list it narrows: All (left out) or every run, then by the runs' own status too
    tab: z.optional(z.enum(['runs'] as const)),
    status: z.optional(z.enum(RUN_STATUSES)),
  }),
  run: ({ router }, i) =>
    go(router, ROUTINES, {
      tab: i.tab,
      status: i.tab ? i.status : undefined,
      q: i.q?.trim() || undefined,
      state: i.state,
      range: i.range === RUNS_RANGE ? undefined : i.range,
    }),
  did: () => 'Listed the routines',
  target: () => ({ kind: 'tab', id: 'routines' }),
});

// ---------------------------------------------------------------- the flow: its trigger and its steps

const flowStep = z.record(z.string(), z.unknown());
const stepNotFound = () => Object.assign(new Error('That step is not in this automation'), { code: 'not_found: That step is not in this automation' });
/** The routine as loaded, read first when it is not (so a write keeps every step it does not touch). */
const loaded = async (ctx: Ctx, rid: string) => cached(ctx, rid) ?? (await ctx.queryClient.ensureQueryData(routineQuery(rid)));
const destsOf = (r: Routine) => (Array.isArray(r.destinations) ? (r.destinations as Dest[]) : []);

/** What `?edit=` may open on an automation's page: a step by its id, its trigger, a new step (`new|<list>|<at>|<type>`),
    a board part (`board|rule|0`, `board|destination|new`, `board|llm`), its versions or its spend limit. */
export const EDIT = /^(trigger|versions|limit|new\|[^|]{1,200}\|\d{1,3}\|[a-z_]{1,20}|board\|(rule|question|destination)\|(\d{1,3}|new)|board\|llm|[\w:.-]{1,200})$/;

export const editPart = defineAction({
  id: 'automations.edit',
  title: "Open one part of an automation's flow in the panel: a step, its trigger, a new step, a board part",
  kind: 'view',
  input: z.strictObject({ routine: id, edit: z.string().check(z.regex(EDIT), z.describe('a step id, trigger, versions, limit, new|<list>|<at>|<type>, board|<part>|<i or new>, board|llm')) }),
  run: ({ router }, i) => go(router, automationHref(i.routine), { edit: i.edit }),
  did: () => 'Opened it',
  target: (i) => ({ kind: 'panel', id: `edit:${i.edit}` }),
});

export const renameAutomation = defineAction({
  id: 'automations.rename',
  title: 'Rename an automation',
  kind: 'change',
  input: z.strictObject({ routine: id, name }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => said(renameRoutine(i.routine, i.name.trim())).then(() => undefined),
  did: (i) => `Renamed to ${i.name.trim()}`,
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const startAutomation = defineAction({
  id: 'automations.start',
  title: 'Start a blank automation on a trigger, off, and open it',
  kind: 'change',
  input: z.strictObject({ when: z.enum(['schedule', 'event', 'due'] as const) }),
  run: async (ctx, i) => {
    const me = await ctx.queryClient.ensureQueryData(meQuery);
    const made = await said(createBlank(ownerRef(me.id.includes(':') ? me.id : `human:${me.id}`), newTrigger(i.when)));
    await go(ctx.router, automationHref(made.id));
    return made.id;
  },
  did: () => 'Started an automation',
  target: (_i, out) => (out ? { kind: 'routine', id: out } : null),
});

export const saveStep = defineAction({
  id: 'automations.saveStep',
  title: "Add or change one step of an automation's flow",
  kind: 'change',
  input: z.strictObject({
    routine: id,
    // the step as the flow holds it: id, type and the fields its type carries
    step: flowStep,
    // a new step's place: the list (`root`, `<paths id>:<i>`, `<paths id>:other`) and where in it
    insert: z.optional(z.strictObject({ key: z.string().check(z.minLength(1), z.maxLength(200)), at: z.int().check(z.minimum(0), z.maximum(500)) })),
  }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (ctx, i) => {
    const r = await loaded(ctx, i.routine);
    const step = i.step as FlowStep;
    if (typeof step.id !== 'string' || typeof step.type !== 'string') throw Object.assign(new Error('A step needs an id and a type'), { code: 'bad_input: A step needs an id and a type' });
    if (!i.insert && !findStep(r.steps, step.id)) throw stepNotFound();
    return said(setFlow(i.routine, { steps: putStep(r.steps ?? [], step, i.insert), destinations: destsOf(r) })).then(() => undefined);
  },
  did: (i) => (i.insert ? 'Added the step' : 'Saved'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const removeStep = defineAction({
  id: 'automations.removeStep',
  title: "Remove one step from an automation's flow",
  kind: 'change',
  input: z.strictObject({ routine: id, step: id }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (ctx, i) => {
    const r = await loaded(ctx, i.routine);
    if (!findStep(r.steps, i.step)) throw stepNotFound();
    return said(setFlow(i.routine, { steps: dropStep(r.steps ?? [], i.step), destinations: destsOf(r) })).then(() => undefined);
  },
  did: () => 'Removed the step',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const setStepOnAction = defineAction({
  id: 'automations.setStepOn',
  title: "Turn one step of an automation's flow on or off",
  kind: 'change',
  input: z.strictObject({ routine: id, step: id, on: z.boolean() }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: async (ctx, i) => {
    const r = await loaded(ctx, i.routine);
    if (!findStep(r.steps, i.step)) throw stepNotFound();
    return said(setFlow(i.routine, { steps: stepOn(r.steps ?? [], i.step, i.on), destinations: destsOf(r) })).then(() => undefined);
  },
  did: (i) => (i.on ? 'Step on' : 'Step off'),
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

export const setTriggerAction = defineAction({
  id: 'automations.setTrigger',
  title: 'Change what starts an automation',
  kind: 'change',
  // the trigger as the flow holds it: `type` and the fields it carries (cron and tz, name, first_due…, filters)
  input: z.strictObject({ routine: id, trigger: z.record(z.string(), z.unknown()) }),
  can: (ctx, i) => cached(ctx, i.routine)?.can_edit !== false,
  run: (_ctx, i) => {
    const t = i.trigger as unknown as Trigger;
    if (typeof t.type !== 'string' || !t.type) throw Object.assign(new Error('Pick what starts it'), { code: 'bad_input: Pick what starts it' });
    return said(setTrigger(i.routine, t)).then(() => undefined);
  },
  did: () => 'Saved',
  target: (i) => ({ kind: 'routine', id: i.routine }),
});

// ---------------------------------------------------------------- the board, inboxes and templates

const notLoaded = () => Object.assign(new Error('That routine is not loaded'), { code: 'not_found: That routine is not loaded' });
const notBoard = () => Object.assign(new Error("This routine isn't a board"), { code: "not_a_board: This routine isn't a board" });

const act = z.strictObject({ do: z.enum(Object.keys(ACTS)), args: z.optional(z.record(z.string(), z.string().check(z.maxLength(20_000)))) });
const cond = z.strictObject({ field: z.string().check(z.minLength(1), z.maxLength(200)), op: z.optional(z.enum(['is', 'is_not', 'contains', 'matches', '>=', '<='] as const)), value: z.string().check(z.maxLength(2000)) });

export const saveBoardItem = defineAction({
  id: 'automations.saveBoardItem',
  title: "Add or change a rule, question, destination or the LLM on a routine's board",
  kind: 'change',
  input: z.strictObject({
    routine: id,
    part: z.enum(['rule', 'question', 'destination', 'llm'] as const),
    // which one, from 0; left out adds a new one (the LLM has just one)
    at: z.optional(z.int().check(z.minimum(0), z.maximum(500))),
    when: z.optional(z.array(cond).check(z.maxLength(20))),
    match: z.optional(z.enum(['all', 'any'] as const)),
    then: z.optional(z.array(act).check(z.maxLength(20))),
    // a rule's or question's last step: a destination id, or '' to end (or, a rule with moveOn, to move on)
    goTo: z.optional(z.string().check(z.maxLength(100))),
    moveOn: z.optional(z.boolean()),
    text: z.optional(z.string().check(z.maxLength(8000))),
    min: z.optional(z.nullable(z.number().check(z.minimum(0), z.maximum(1)))),
    name: z.optional(name),
    mode: z.optional(z.enum(['sort', 'draft', 'reply'] as const)),
    prompt: z.optional(z.string().check(z.maxLength(20_000))),
    dests: z.optional(z.array(z.string().check(z.minLength(1), z.maxLength(100))).check(z.maxLength(50))),
    unsure: z.optional(z.string().check(z.maxLength(100))),
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
  id: 'automations.removeBoardItem',
  title: "Remove a rule, question or destination from a routine's board",
  kind: 'change',
  input: z.strictObject({ routine: id, part: z.enum(['rule', 'question', 'destination'] as const), at: z.int().check(z.minimum(0), z.maximum(500)) }),
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
  id: 'automations.newInbox',
  title: 'Add an inbox routine from a template',
  kind: 'change',
  input: z.strictObject({
    // an email address, or a channel a team hears
    inbox: z.string().check(z.minLength(1), z.maxLength(300)),
    owner: z.string().check(z.minLength(1), z.maxLength(200), z.regex(/^(human|person|bot|group):\S+$/), z.describe('human:<id>, bot:<id> or group:<id>')),
    template: z.string().check(z.minLength(1), z.maxLength(100)),
    on: z.optional(z.boolean()),
  }),
  run: async ({ router }, i) => {
    const filled = await said(fillTemplate(i.template, ownerRef(i.owner), i.inbox.trim()));
    const made = await said(createInbox(filled, !!i.on));
    await go(router, automationHref(made.id));
    return made.id;
  },
  did: (i) => `Added the ${i.inbox.trim()} inbox`,
  target: (_i, out) => (out ? { kind: 'routine', id: out } : null),
});

export const applyTemplate = defineAction({
  id: 'automations.applyTemplate',
  title: 'Move an inbox routine onto a template',
  kind: 'change',
  input: z.strictObject({ routine: id, template: z.string().check(z.minLength(1), z.maxLength(100)) }),
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
  input: z.strictObject({ hook: id }),
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
  input: z.strictObject({ name, preset: z.optional(z.enum(PRESETS)), secret: z.optional(z.string().check(z.maxLength(300))) }),
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
  input: z.strictObject({ hook: id, name }),
  run: (_ctx, i) => said(updateHook(i.hook, { name: i.name.trim() })).then(() => undefined),
  did: () => 'Saved',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

export const rotateWebhook = defineAction({
  id: 'webhooks.rotate',
  title: 'Give a webhook a new secret',
  kind: 'change',
  tico: false,
  input: z.strictObject({ hook: id }),
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
  input: z.strictObject({
    hook: id,
    preset: z.enum(['hmac', 'github', 'standard', 'stripe', 'slack'] as const),
    secret: z.optional(z.string().check(z.maxLength(300))),
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
  input: z.strictObject({ hook: id }),
  run: (_ctx, i) => said(signHookOff(i.hook)).then(() => undefined),
  did: () => 'Signing off',
  target: (i) => ({ kind: 'hook', id: i.hook }),
});

export const removeWebhook = defineAction({
  id: 'webhooks.remove',
  title: 'Remove a webhook',
  kind: 'change',
  input: z.strictObject({ hook: id }),
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
  input: z.strictObject({ rule: id, kind: z.enum(['org', 'stage'] as const), tab: z.optional(z.enum(RULE_TABS)) }),
  run: ({ router }, i) => ruleTab(router, i.tab ?? (i.kind === 'stage' ? 'stages' : ''), `${i.kind}:${i.rule}`),
  did: () => 'Opened the rule',
  target: (i) => ({ kind: 'rule', id: i.rule }),
});

export const newRule = defineAction({
  id: 'rules.new',
  title: 'Start a new rule',
  kind: 'view',
  input: z.strictObject({ kind: z.enum(['org', 'stage', 'outside'] as const) }),
  run: ({ router }, i) => ruleTab(router, i.kind === 'stage' ? 'stages' : i.kind === 'outside' ? 'outside' : '', `${i.kind === 'stage' ? 'stage' : 'org'}:new`),
  did: () => 'Opened a new rule',
  target: () => ({ kind: 'panel', id: 'rule:new' }),
});

export const pickScope = defineAction({
  id: 'rules.pickScope',
  title: 'Show the rules for a team, an agent or a person',
  kind: 'view',
  input: z.strictObject({ who: party }),
  run: ({ router }, i) => go(router, RULES, i.who === '*' ? {} : { who: i.who }),
  did: () => 'Opened their rules',
  target: (i) => ({ kind: 'scope', id: i.who }),
});

/** Due dates by status, as the Due tab's filter names them. */
export const listRules = defineAction({
  id: 'rules.list',
  title: 'Search the rules on a tab',
  kind: 'view',
  input: z.strictObject({ tab: z.optional(z.enum(RULE_TABS)), q: z.optional(z.string().check(z.maxLength(200))) }),
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
  input: z.strictObject({}),
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
  text: z.string().check(z.minLength(1), z.maxLength(2000)),
  ifBroken: z.enum(['block', 'escalate', 'warn'] as const),
  // under Ask first: the person who OKs it; left out, the manager above whoever broke it
  reviewer: z.optional(party),
  // who follows it: ['*'] (Everyone, the default) or teams, agents and people
  who: z.optional(z.array(party).check(z.maxLength(100))),
  outside: z.optional(z.boolean()),
  // an outside rule: who it is from, an existing From's id, or a new one by name and kind
  source: z.optional(id),
  newSource: z.optional(z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(200)), kind: z.enum(SOURCE_KINDS) })),
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
  name: z.optional(z.string().check(z.maxLength(200))),
  pipeline: z.string().check(z.maxLength(100)),
  stages: z.array(z.string().check(z.minLength(1), z.maxLength(100))).check(z.maxLength(50)),
  event: z.string().check(z.minLength(1), z.maxLength(100), z.regex(/^(timer|(enters|owner_changes|bot_snoozes)(,(enters|owner_changes|bot_snoozes))*)$/), z.describe('enters, owner_changes, bot_snoozes or timer')),
  timerKind: z.optional(z.enum(['in_stage', 'quiet', 'waiting_person'] as const)),
  timerHours: z.optional(z.number().check(z.minimum(0.25), z.maximum(2000))),
  decisionModel: z.optional(z.string().check(z.maxLength(2000))),
  then: z.enum(['block', 'tell_manager', 'tell_owner', 'move_back'] as const),
  reason: z.optional(z.string().check(z.maxLength(2000))),
};

export const addRule = defineAction({
  id: 'rules.add',
  title: 'Add a rule',
  kind: 'change',
  // `stay`: keep the page where it is (the pipeline editor) instead of opening the new rule on the Rules page
  input: z.strictObject({ kind: z.enum(['org', 'stage'] as const), org: z.optional(z.strictObject(orgFields)), stage: z.optional(z.strictObject(stageFields)), stay: z.optional(z.boolean()) }),
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
  input: z.strictObject({ rule: id, kind: z.enum(['org', 'stage'] as const), org: z.optional(z.strictObject(orgFields)), stage: z.optional(z.strictObject(stageFields)) }),
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
  input: z.strictObject({ rule: id, kind: z.enum(['org', 'stage'] as const), stay: z.optional(z.boolean()) }),
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
  input: z.strictObject({ rule: id, kind: z.enum(['org', 'stage'] as const), who: z.optional(z.array(party).check(z.maxLength(100))) }),
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
  input: z.strictObject({ rule: id, kind: z.enum(['org', 'stage'] as const) }),
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
  input: z.strictObject({
    who: party,
    mode: z.optional(z.nullable(z.enum(['open', 'nearby', 'closed'] as const))),
    book: z.optional(z.nullable(z.enum(['anyone', 'nearby', 'none'] as const))),
    chatsPerDay: z.optional(z.nullable(z.int().check(z.minimum(0), z.maximum(10_000)))),
    tasksPerDay: z.optional(z.nullable(z.int().check(z.minimum(0), z.maximum(10_000)))),
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
  input: z.strictObject({ due: id }),
  run: ({ router }, i) => ruleTab(router, 'due', `due:${i.due}`),
  did: () => 'Opened the due date',
  target: (i) => ({ kind: 'due', id: i.due }),
});

export const newDue = defineAction({
  id: 'rules.newDue',
  title: 'Start a new due date',
  kind: 'view',
  // `rule`: the rule it is for, already picked
  input: z.strictObject({ rule: z.optional(id) }),
  run: ({ router }, i) => ruleTab(router, 'due', i.rule ? `due:new:${i.rule}` : 'due:new'),
  did: () => 'Opened a new due date',
  target: () => ({ kind: 'panel', id: 'due:new' }),
});

export const addDueAction = defineAction({
  id: 'rules.addDue',
  title: 'Add a due date to a rule',
  kind: 'change',
  input: z.strictObject({
    rule: id,
    due: day,
    repeats: z.enum(REPEATS),
    owner,
    // a task opens this many days before each due day
    taskDays: z.optional(z.int().check(z.minimum(0), z.maximum(366))),
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
  input: z.strictObject({
    due: id,
    first: z.optional(day),
    repeats: z.optional(z.enum(REPEATS)),
    owner: z.optional(owner),
    // null: no task before it
    taskDays: z.optional(z.nullable(z.int().check(z.minimum(0), z.maximum(366)))),
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
  input: z.strictObject({ due: id, on: z.optional(day) }),
  run: (_ctx, i) => said(satisfyDue(i.due, i.on)).then(() => undefined),
  did: () => 'Marked satisfied',
  target: (i) => ({ kind: 'due', id: i.due }),
});

export const removeDueAction = defineAction({
  id: 'rules.removeDue',
  title: 'Remove a due date',
  kind: 'change',
  input: z.strictObject({ due: id }),
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
  input: z.strictObject({
    file: z.optional(id),
    url: z.optional(z.string().check(z.minLength(8), z.maxLength(2000), z.regex(/^https?:\/\/\S+$/i), z.describe('an http(s) link'))),
    note: z.optional(z.string().check(z.maxLength(2000))),
    // an existing From's id, or a new From's name with `kind`
    source: z.string().check(z.minLength(1), z.maxLength(200)),
    kind: z.optional(z.enum(['vendor', 'customer', 'government', 'other'] as const)),
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
  input: z.strictObject({ watch: id, tab: z.optional(z.enum(['matches', 'sources'] as const)) }),
  run: ({ router }, i) => go(router, LISTENING, { watch: i.watch, tab: i.tab === 'sources' ? 'sources' : undefined }),
  did: () => 'Opened the watch',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const openMatch = defineAction({
  id: 'listening.openMatch',
  title: 'Open a match',
  kind: 'view',
  input: z.strictObject({ watch: id, match: z.nullable(id) }),
  run: ({ router }, i) => go(router, LISTENING, { watch: i.watch, match: i.match ?? undefined }),
  did: () => 'Opened the match',
  target: (i) => (i.match ? { kind: 'match', id: i.match } : null),
});

export const listWatches = defineAction({
  id: 'listening.list',
  title: 'Search the listening watches',
  kind: 'view',
  input: z.strictObject({ q: z.optional(z.string().check(z.maxLength(200))) }),
  run: ({ router }, i) => go(router, LISTENING, { q: i.q?.trim() || undefined }),
  did: () => 'Listed the watches',
  target: () => ({ kind: 'section', id: 'watches' }),
});

export const addWatch = defineAction({
  id: 'listening.add',
  title: 'Add a listening watch',
  kind: 'change',
  input: z.strictObject({ name }),
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
  input: z.strictObject({ watch: id }),
  run: (_ctx, i) => said(setRoutineOn(i.watch, false, watchesQuery.queryKey, watchQuery(i.watch).queryKey).then(() => undefined)),
  did: () => 'Paused',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const resumeWatch = defineAction({
  id: 'listening.resume',
  title: 'Turn a listening watch back on',
  kind: 'change',
  input: z.strictObject({ watch: id }),
  run: (_ctx, i) => said(setRoutineOn(i.watch, true, watchesQuery.queryKey, watchQuery(i.watch).queryKey).then(() => undefined)),
  did: () => 'Turned on',
  target: (i) => ({ kind: 'watch', id: i.watch }),
});

export const removeWatch = defineAction({
  id: 'listening.remove',
  title: 'Remove a listening watch',
  kind: 'change',
  input: z.strictObject({ watch: id }),
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
  input: z.strictObject({
    watch: id,
    // search: words on a site; page: an address; github: owner/repo; push: a name (posts are sent to its own address)
    kind: z.enum(['search', 'page', 'github', 'push'] as const),
    target: z.optional(z.string().check(z.maxLength(500))),
    site: z.optional(z.string().check(z.maxLength(200))),
    follow: z.optional(z.enum(['prs', 'releases'] as const)),
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
  input: z.strictObject({
    watch: id,
    name: z.optional(name),
    // keep (with a tag) or drop what a sentence describes
    rules: z.optional(z.array(z.strictObject({ do: z.enum(['keep', 'drop'] as const), tag: z.optional(z.string().check(z.maxLength(24))), text: z.string().check(z.minLength(1), z.maxLength(300)) })).check(z.maxLength(20))),
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
  input: z.strictObject({ watch: id, source: id }),
  run: (ctx, i) => {
    const w = watchOf(ctx, i.watch);
    if (!w) throw Object.assign(new Error('That watch is not loaded'), { code: 'not_found: That watch is not loaded' });
    return said(setWatchTrigger(w, { ...w.trigger, sources: (w.trigger.sources ?? []).filter((x) => x.id !== i.source) }).then(() => undefined));
  },
  did: () => 'Removed the source',
});
