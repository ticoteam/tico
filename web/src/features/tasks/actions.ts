import { defineAction, refusalFor, s, toldOf, type Schema } from '@/actions';
import { toast } from '@/kit/Toast';
import { writePref } from '@/prefs';
import {
  addRequester,
  assignMany,
  createPipeline,
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
import { createTag, createTaggedTask, moreTagTasks, saveTag, tagKeyOf } from './queries';

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

/** A refusal's toast: the short words, then the server's reason when it gave a safe one. */
const reason = (e: unknown, fallback: string) => refusalFor(e, fallback, fallback);

async function said<T>(run: () => Promise<T>, fallback: string): Promise<T> {
  try {
    return await run();
  } catch (e) {
    toast(reason(e, fallback));
    throw toldOf(e);
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

/** Merge a task into another: `into` stays and gets its details, the task is closed as a duplicate. It cannot be undone
    (backend/task_merge.py has no way back), so it is the person's own press. */
export const merge = defineAction({
  id: 'tasks.merge',
  title: 'Merge a task into another',
  kind: 'change',
  tico: false,
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
    if (notMoved) toast(reason(notMoved, 'Filed in the first stage'));
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
    const { moved, movedUnseen, skipped } = await said(() => moveMany(tasks, to), "Couldn't move the tasks");
    const left = tell(skipped, tasks.length);
    // Closing is the one a slip makes: it says so and takes it back.
    const closed = moved.length + movedUnseen;
    if (to === 'closed' && closed > 0)
      toast({
        text: `Closed ${plural(closed)}`,
        action: moved.length ? { label: 'Undo', run: () => void moveMany(moved, 'reopen') } : undefined,
      });
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
  // Its own page (`/tasks/<pipeline>/edit`); closing it goes back to the pipeline.
  run: ({ router }, { pipeline, open = true }) => router.navigate({ to: open ? `${where(pipeline)}/edit` : where(pipeline) }),
  did: ({ open = true }) => (open ? 'Opened Edit pipeline' : 'Closed Edit pipeline'),
  target: ({ pipeline }) => ({ kind: 'page', id: `${where(pipeline)}/edit` }),
});

const stageDraft = s.object({
  id: s.optional(s.string({ min: 1, max: 100, what: 'a saved stage id; leave out for a new stage' })),
  name: s.string({ min: 1, max: 80 }),
  side: s.boolean(),
  purpose: s.optional(s.string({ max: 300 })),
  owner: clearable(s.string({ min: 1, max: 120, what: "an actor id, assignee (the task's), or pick (one of several, as set)" })),
  task_limit: clearable(s.number({ int: true, min: 1, max: 1000 })),
});

const pipelineFacts = {
  goal: s.optional(s.string({ max: 300 })),
  manager_id: clearable(actor),
  factory_id: clearable(s.string({ min: 1, max: 100, what: 'a factory id' })),
  prefix: clearable(s.string({ pattern: /^[A-Za-z]{1,2}$/, what: 'one or two letters' })),
};

/** Saves a pipeline: its Goal, Manager, Factory and Prefix, and its stages in order with each one's goal, owner and limit.
    `version` is the settings_version the person read: someone else's save in between is refused (task_changed). A
    removed stage that holds tasks needs `move_to`, where they go: that moves them and deletes the stage, which cannot be
    undone, so it is the person's own press, never Tico's. */
export const savePipelineStages = defineAction({
  id: 'tasks.savePipeline',
  title: 'Save a pipeline',
  kind: 'change',
  input: s.object({
    pipeline,
    ...pipelineFacts,
    stages: s.array(stageDraft, { max: 40 }),
    version: s.optional(s.number({ int: true, min: 0 })),
    move_to: s.optional(s.object({ pipeline, stage: s.string({ min: 1, max: 100 }) })),
  }),
  // Removing a stage that holds tasks cannot be undone: Tico may save the rest, not that.
  can: ({ via }, { move_to }) => !(via === 'tico' && move_to),
  run: (_ctx, { pipeline, stages, version, move_to, ...facts }) => savePipeline(pipeline, { stages, facts, version, moveTo: move_to }),
  did: () => 'Saved the pipeline',
  target: ({ pipeline }) => ({ kind: 'page', id: where(pipeline) }),
});

/** New pipeline: the editor's page, empty (`/tasks/<pipeline>/edit?new=1`; Back goes to the pipeline on screen). */
export const newPipeline = defineAction({
  id: 'tasks.newPipeline',
  title: 'New pipeline',
  kind: 'view',
  input: s.object({ pipeline, open: s.optional(s.boolean()) }),
  run: ({ router }, { pipeline, open = true }) =>
    router.navigate({ to: open ? `${where(pipeline)}/edit` : where(pipeline), search: open ? ({ new: true } as never) : undefined }),
  did: ({ open = true }) => (open ? 'Opened New pipeline' : 'Closed New pipeline'),
  target: ({ pipeline }) => ({ kind: 'page', id: `${where(pipeline)}/edit` }),
});

/** Makes a pipeline (the company's owner and Admins; the server says so to anyone else) and opens it. */
export const makePipeline = defineAction({
  id: 'tasks.createPipeline',
  title: 'Make a pipeline',
  kind: 'change',
  input: s.object({
    name: s.string({ min: 1, max: 80 }),
    ...pipelineFacts,
    stages: s.array(stageDraft, { max: 40 }),
  }),
  run: async ({ router }, { stages, ...facts }) => {
    const made = await createPipeline(facts, stages);
    await router.navigate({ to: where(made.id) });
    return made;
  },
  did: ({ name }) => `Made ${name}`,
  target: (_input, out) => (out ? { kind: 'page', id: where(out.id) } : null),
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

/* ---------------------------------------------------------------- tags (ui/app/tags.ts) */

const tagId = s.string({ min: 1, max: 100, what: "a tag's id or key" });
const tagKey = s.string({ min: 1, max: 40, pattern: /^[a-z0-9][a-z0-9-]*$/, what: 'a tag key: lowercase words joined by hyphens' });
const metadata = s.optional(s.string({ max: 4000, what: 'a JSON object of fields, such as {"date": "2030-01-02"}' }));
/** The metadata as written, as an object; anything else is refused before it is sent. */
function fieldsOf(json: string | undefined): Record<string, unknown> | undefined {
  if (json === undefined || !json.trim()) return undefined;
  let v: unknown;
  try {
    v = JSON.parse(json);
  } catch {
    v = null;
  }
  if (!v || typeof v !== 'object' || Array.isArray(v)) {
    toast({ text: 'Metadata must be a JSON object', kind: 'failed' });
    throw toldOf(new Error('metadata'));
  }
  return v as Record<string, unknown>;
}
const onTag = (id: string) => ({ kind: 'page', id: `/tags/${id}` });
/** Tags (`/tags`) or a tag's page (`/tags/<id>`) is on screen. */
const onTags = (pathname: string) => /^\/tags(\/[^/]+)?\/?$/.test(pathname);

/** Narrows Tags or a tag's page in the address: words (`q`), and on a tag's page its owner and Late. Null clears one. */
export const filterTags = defineAction({
  id: 'tags.filter',
  title: 'Search or filter tags, or a tag\'s tasks',
  kind: 'view',
  input: s.object({ q: clearable(s.string({ max: 200 })), owner: clearable(actor), late: clearable(s.boolean()) }),
  can: ({ router }) => onTags(router.state.location.pathname),
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
  did: () => 'Filtered',
  target: () => ({ kind: 'tags', id: 'filter' }),
});

export const newTag = defineAction({
  id: 'tags.create',
  title: 'New tag',
  kind: 'change',
  input: s.object({
    name: s.string({ min: 1, max: 100 }),
    key: s.optional(tagKey),
    notes: s.optional(s.string({ max: 20000, what: 'markdown' })),
  }),
  run: async ({ router }, { name, key, notes }) => {
    const made = await said(
      () => createTag({ key: key || tagKeyOf(name), label: name, markdown: notes ?? '', is_template: false }),
      "Couldn't make the tag",
    );
    await router.navigate({ to: '/tags/$tag', params: { tag: made.id } });
    return made;
  },
  did: ({ name }) => `Made ${name}`,
  target: (_input, out) => (out ? onTag(out.id) : null),
});

export const editTag = defineAction({
  id: 'tags.edit',
  title: "Change a tag's name, notes or metadata",
  kind: 'change',
  input: s.object({ tag: tagId, name: s.optional(s.string({ min: 1, max: 100 })), notes: s.optional(s.string({ max: 20000 })), metadata }),
  run: (_ctx, { tag, name, notes, metadata }) => {
    const fields = fieldsOf(metadata);
    return said(() => saveTag(tag, { label: name, markdown: notes, metadata: fields }), "Couldn't save the tag");
  },
  did: () => 'Saved the tag',
  target: ({ tag }) => onTag(tag),
});

export const tagMore = defineAction({
  id: 'tags.moreTasks',
  title: "Read the next 500 of a tag's tasks",
  kind: 'view',
  input: s.object({ tag: tagId }),
  run: (_ctx, { tag }) => said(() => moreTagTasks(tag), "Couldn't read more"),
  did: () => 'Read more',
  target: ({ tag }) => onTag(tag),
});

export const newTaggedTask = defineAction({
  id: 'tags.newTask',
  title: 'New task with a tag on it',
  kind: 'change',
  input: s.object({ tag: tagId, key: s.string({ min: 1, max: 120, what: "the tag's key" }), title: s.string({ min: 1, max: 300 }), pipeline, owner: clearable(actor) }),
  run: (_ctx, { tag, key, ...input }) => said(() => createTaggedTask(tag, key, input), "Couldn't create the task"),
  did: (_input, out) => (out?.number ? `Filed #${out.number}` : 'Filed a task'),
  target: (_input, out) => (out ? { kind: 'task', id: out.number ?? out.id } : null),
});
