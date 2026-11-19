import { defineAction, s, type Schema } from '@/actions';
import { ApiError } from '@/api/client';
import { toast } from '@/kit/Toast';
import { writePref } from '@/prefs';
import {
  addRequester,
  assignMany,
  createTask,
  listsOf,
  makeSubtask,
  mergeTask,
  moveMany,
  moveTask,
  pipelinesQuery,
  removeRequester,
  savePipeline,
  snoozeMany,
  updateRequester,
  type Pipeline,
  type Skipped,
} from './queries';
import { VIEW_PREF } from './search';

const pipeline = s.string({ min: 1, max: 100, what: 'a pipeline id' });
const task = s.string({ min: 1, max: 100, what: 'a task id' });
const tasks = s.array(task, { max: 500 });
const actor = s.string({ min: 1, max: 120, what: 'an actor id: human:<id> or bot:<slug>' });
const where = (p: string) => `/tasks/${encodeURIComponent(p)}`;

/** A pipeline's page (`/tasks/<pipeline>`) or the Roadmap is on screen, not a task or another page. */
const onPipeline = (pathname: string) => /\/tasks\/[^/]+\/?$/.test(pathname) || /\/roadmap\/?$/.test(pathname);

export const openTasks = defineAction({
  id: 'tasks.open',
  title: 'Open Tasks',
  kind: 'view',
  input: s.object({ pipeline }),
  run: ({ router }, { pipeline }) => router.navigate({ to: where(pipeline) }),
  show: ({ router }, { pipeline }) => router.navigate({ to: where(pipeline) }),
  did: ({ pipeline }) => `Opened Tasks, ${pipeline}`,
  target: ({ pipeline }) => ({ kind: 'page', id: where(pipeline) }),
  offer: ({ queryClient }) =>
    (queryClient.getQueryData<Pipeline[]>(pipelinesQuery.queryKey) ?? []).map((p) => ({
      input: { pipeline: p.id },
      title: `Open ${p.name}`,
    })),
});

/** Board or list is remembered per pipeline; Archived is a place to look, so going back finds the view it left. */
export const setView = defineAction({
  id: 'tasks.setView',
  title: 'Show tasks as a board, a list, or the archived ones',
  kind: 'view',
  input: s.object({ pipeline, view: s.oneOf(['board', 'list', 'archived'] as const) }),
  run: async ({ router }, { pipeline, view }) => {
    if (view !== 'archived') writePref(`${VIEW_PREF}:${pipeline}`, view);
    await router.navigate({ to: where(pipeline), search: (prev: Record<string, unknown>) => ({ ...prev, view }) });
  },
  did: ({ view }) => (view === 'archived' ? 'Opened Archived' : view === 'board' ? 'Switched Tasks to a board' : 'Switched Tasks to a list'),
  target: ({ view }) => ({ kind: 'tasks', id: view === 'archived' ? 'archived' : 'view' }),
});

const clearable = <T>(sch: Schema<T>) => s.optional(s.nullable(sch));

/** Sets or clears filters on the pipeline on screen; null clears one. Filters replace the address, they do not push. */
export const filterTasks = defineAction({
  id: 'tasks.filter',
  title: 'Filter tasks',
  kind: 'view',
  input: s.object({
    owner: clearable(actor),
    waiting: clearable(actor),
    asked: clearable(actor),
    tag: clearable(s.string({ max: 120, what: 'a tag key' })),
    due: clearable(s.oneOf(['overdue', 'week', 'any'] as const)),
    late: clearable(s.boolean()),
    q: clearable(s.string({ max: 200 })),
  }),
  can: ({ router }) => onPipeline(router.state.location.pathname),
  run: async ({ router }, change) => {
    await router.navigate({
      to: '.',
      replace: true,
      search: (prev: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...prev };
        for (const [k, v] of Object.entries(change)) {
          if (v === undefined) continue;
          if (v === null || v === '' || v === false) delete next[k];
          else next[k] = v;
        }
        return next;
      },
    });
  },
  did: () => 'Filtered Tasks',
  target: () => ({ kind: 'tasks', id: 'filter' }),
});

/** The server's words for a refusal, else a plain line. */
const reason = (e: unknown, fallback: string) => (e instanceof ApiError && e.status !== 0 && e.message ? e.message : fallback);

async function said<T>(run: () => Promise<T>, fallback: string): Promise<T> {
  try {
    return await run();
  } catch (e) {
    toast({ text: reason(e, fallback), kind: 'failed' });
    throw e;
  }
}

export const move = defineAction({
  id: 'tasks.move',
  title: 'Move a task to a stage',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 100, what: 'a stage id or name, or next, done or closed' }) }),
  run: (_ctx, { task, to }) => said(() => moveTask(task, to), "Couldn't move the task"),
  did: (_input, out) => (out ? `Moved #${out.number ?? out.id}` : 'Moved the task'),
  target: ({ task }, out) => ({ kind: 'task', id: out?.number ?? task }),
});

/** One task under another as its subtask (a card let go over another's card, Make it a subtask). */
export const subtask = defineAction({
  id: 'tasks.makeSubtask',
  title: 'Make a task a subtask of another',
  kind: 'change',
  input: s.object({ task, parent: task }),
  run: (_ctx, { task, parent }) => said(() => makeSubtask(task, parent), "Couldn't make it a subtask"),
  did: (_input, out) => (out ? `Made #${out.number ?? out.id} a subtask` : 'Made it a subtask'),
  target: ({ parent }) => ({ kind: 'task', id: parent }),
});

/** Merge a task into another: `into` stays and gets its details, the task is closed as a duplicate. */
export const merge = defineAction({
  id: 'tasks.merge',
  title: 'Merge a task into another',
  kind: 'change',
  input: s.object({ task, into: task, owner: clearable(actor) }),
  run: (_ctx, { task, into, owner }) => said(() => mergeTask(task, into, owner), "Couldn't merge the tasks"),
  did: (_input, out) => (out ? `Merged #${out.closed.number ?? out.closed.id} into #${out.task.number ?? out.task.id}` : 'Merged the tasks'),
  target: ({ into }, out) => ({ kind: 'task', id: out?.task.number ?? into }),
});

export const create = defineAction({
  id: 'tasks.create',
  title: 'New task',
  kind: 'change',
  input: s.object({
    title: s.string({ min: 1, max: 300 }),
    pipeline,
    owner: clearable(actor),
    stage: clearable(s.string({ min: 1, max: 100, what: 'a stage id' })),
  }),
  run: async (_ctx, input) => {
    const { task, notMoved } = await said(() => createTask(input, listsOf(input.pipeline)), "Couldn't create the task");
    if (notMoved) toast({ text: reason(notMoved, 'Filed in the first stage'), kind: 'failed' });
    return task;
  },
  did: (_input, out) => (out?.number ? `Filed #${out.number}` : 'Filed a task'),
  target: (_input, out) => (out ? { kind: 'task', id: out.number ?? out.id } : null),
});

/* ---------------------------------------------------------------- many at once */

const plural = (n: number) => `${n} task${n === 1 ? '' : 's'}`;

/** Anything the server left as it was: how many, and why (the first reason). */
function tell(left: Skipped, total: number) {
  if (left.n) toast({ text: left.n === total ? left.why || 'Nothing changed' : `${plural(left.n)} not changed: ${left.why}`, kind: 'failed' });
  return left;
}

export const moveAll = defineAction({
  id: 'tasks.moveMany',
  title: 'Move tasks to a stage',
  kind: 'change',
  input: s.object({ tasks, to: s.string({ min: 1, max: 100, what: 'a stage id, or done, closed or reopen' }) }),
  run: async (_ctx, { tasks, to }) => {
    const left = tell(await said(() => moveMany(tasks, to), "Couldn't move the tasks"), tasks.length);
    // Closing is the one a slip makes: it says so and takes it back.
    if (to === 'closed' && left.n < tasks.length)
      toast({ text: `Closed ${plural(tasks.length - left.n)}`, action: { label: 'Undo', run: () => void moveMany(tasks, 'reopen') } });
    return left;
  },
  did: ({ tasks, to }) => (to === 'closed' ? `Closed ${plural(tasks.length)}` : `Moved ${plural(tasks.length)}`),
  target: ({ to }) => (to === 'done' || to === 'closed' || to === 'reopen' ? null : { kind: 'stage', id: to }),
});

export const assignAll = defineAction({
  id: 'tasks.assignMany',
  title: 'Give tasks to an owner',
  kind: 'change',
  input: s.object({ tasks, owner: actor }),
  run: async (_ctx, { tasks, owner }) => tell(await said(() => assignMany(tasks, owner), "Couldn't change the owner"), tasks.length),
  did: ({ tasks }) => `Assigned ${plural(tasks.length)}`,
});

export const snoozeAll = defineAction({
  id: 'tasks.snoozeMany',
  title: 'Snooze tasks',
  kind: 'change',
  input: s.object({ tasks, until: s.string({ min: 10, max: 40, what: 'an ISO date and time' }) }),
  run: async (_ctx, { tasks, until }) => tell(await said(() => snoozeMany(tasks, until), "Couldn't snooze the tasks"), tasks.length),
  did: ({ tasks }) => `Snoozed ${plural(tasks.length)}`,
});

/* ---------------------------------------------------------------- Edit pipeline */

export const editPipeline = defineAction({
  id: 'tasks.editPipeline',
  title: 'Edit pipeline',
  kind: 'view',
  input: s.object({ pipeline, open: s.optional(s.boolean()) }),
  run: async ({ router }, { pipeline, open = true }) => {
    await router.navigate({
      to: where(pipeline),
      search: (prev: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...prev, panel: 'pipeline' };
        if (!open) delete next.panel;
        return next;
      },
    });
  },
  did: ({ open = true }) => (open ? 'Opened Edit pipeline' : 'Closed Edit pipeline'),
  target: () => ({ kind: 'panel', id: 'pipeline' }),
});

const stageDraft = s.object({
  id: s.optional(s.string({ min: 1, max: 100, what: 'a saved stage id; leave out for a new stage' })),
  name: s.string({ min: 1, max: 80 }),
  side: s.boolean(),
});

/** Saves a pipeline's stages in order. A removed stage that holds tasks needs `move_to`: where they go. */
export const savePipelineStages = defineAction({
  id: 'tasks.savePipeline',
  title: 'Save a pipeline’s stages',
  kind: 'change',
  input: s.object({
    pipeline,
    stages: s.array(stageDraft, { max: 40 }),
    move_to: s.optional(s.object({ pipeline, stage: s.string({ min: 1, max: 100 }) })),
  }),
  run: (_ctx, { pipeline, stages, move_to }) => savePipeline(pipeline, stages, move_to),
  did: () => 'Saved the pipeline',
  target: ({ pipeline }) => ({ kind: 'page', id: where(pipeline) }),
});

/* ---------------------------------------------------------------- Roadmap */

const ROADMAP = '/roadmap';

export const openRoadmap = defineAction({
  id: 'tasks.openRoadmap',
  title: 'Open Roadmap',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: ROADMAP }),
  show: ({ router }) => router.navigate({ to: ROADMAP }),
  did: () => 'Opened Roadmap',
  target: () => ({ kind: 'page', id: ROADMAP }),
  offer: () => [{ input: {} }],
});

/** Who asked for a roadmap task: a dialog over the Roadmap, held in the address (`?requesters=<task id>`). */
export const openRequesters = defineAction({
  id: 'tasks.openRequesters',
  title: 'Show who asked for a feature',
  kind: 'view',
  input: s.object({ task, open: s.optional(s.boolean()) }),
  run: async ({ router }, { task, open = true }) => {
    await router.navigate({
      to: ROADMAP,
      search: (prev: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...prev, requesters: task };
        if (!open) delete next.requesters;
        return next;
      },
    });
  },
  did: ({ open = true }) => (open ? 'Opened Requesters' : 'Closed Requesters'),
});

const text = (max: number) => s.optional(s.string({ max }));
const requesterFields = {
  name: text(200),
  email: text(320),
  phone: text(100),
  other: text(500),
  organization: text(200),
  note: text(4000),
  source: s.optional(s.oneOf(['manual', 'public_form', 'inbox', 'meeting'] as const)),
  important: s.optional(s.boolean()),
};

/** Record who asked for a task: a teammate (`actor`) or someone outside the team. */
export const addTaskRequester = defineAction({
  id: 'tasks.addRequester',
  title: 'Add who asked for a feature',
  kind: 'change',
  input: s.object({ task, actor: s.optional(actor), ...requesterFields }),
  run: (_ctx, { task, ...input }) => said(() => addRequester(task, input), "Couldn't add the requester"),
  did: () => 'Added a requester',
});

export const updateTaskRequester = defineAction({
  id: 'tasks.updateRequester',
  title: 'Change a requester',
  kind: 'change',
  input: s.object({ task, requester: s.string({ min: 1, max: 100, what: 'a requester id' }), ...requesterFields }),
  run: (_ctx, { task, requester, ...input }) => said(() => updateRequester(task, requester, input), "Couldn't save the requester"),
  did: () => 'Saved the requester',
  target: ({ requester }) => ({ kind: 'requester', id: requester }),
});

export const removeTaskRequester = defineAction({
  id: 'tasks.removeRequester',
  title: 'Remove a requester',
  kind: 'change',
  input: s.object({ task, requester: s.string({ min: 1, max: 100, what: 'a requester id' }) }),
  run: (_ctx, { task, requester }) => said(() => removeRequester(task, requester), "Couldn't remove the requester"),
  did: () => 'Removed a requester',
});
