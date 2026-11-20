import * as z from 'zod/mini';
import { defineAction, refusalFor, toldOf } from '@/actions';
import { moveTask as moveOne, moveTaskTo, said as taskSaid } from '@/features/task/routing';
import { toast } from '@/kit/Toast';
import { writePref } from '@/prefs';
import {
  addRequester,
  assignMany,
  createPipeline,
  createTask,
  listsOf,
  mergeTask,
  moveMany,
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

const pipeline = z.string().check(z.minLength(1), z.maxLength(100), z.describe('a pipeline id'));
const task = z.string().check(z.minLength(1), z.maxLength(100), z.describe('a task id'));
const tasks = z.array(task).check(z.maxLength(500));
const actor = z.string().check(z.minLength(1), z.maxLength(120), z.describe('an actor id: human:<id> or bot:<slug>'));
const where = (p: string) => `/tasks/${encodeURIComponent(p)}`;

/** A pipeline's page (`/tasks/<pipeline>`) or the Roadmap is on screen, not a task or another page. */
const onPipeline = (pathname: string) => /\/tasks\/[^/]+\/?$/.test(pathname) || /\/roadmap\/?$/.test(pathname);

export const openTasks = defineAction({
  id: 'tasks.open',
  title: 'Open Tasks',
  kind: 'view',
  input: z.strictObject({ pipeline }),
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
  input: z.strictObject({ pipeline, view: z.enum(['board', 'list', 'archived'] as const) }),
  run: async ({ router }, { pipeline, view }) => {
    if (view !== 'archived') writePref(`${VIEW_PREF}:${pipeline}`, view);
    await router.navigate({ to: where(pipeline), search: (prev: Record<string, unknown>) => ({ ...prev, view }) });
  },
  did: ({ view }) => (view === 'archived' ? 'Opened Archived' : view === 'board' ? 'Switched Tasks to a board' : 'Switched Tasks to a list'),
  target: ({ view }) => ({ kind: 'tasks', id: view === 'archived' ? 'archived' : 'view' }),
});

const clearable = <T extends z.core.SomeType>(sch: T) => z.optional(z.nullable(sch));

/** Sets or clears filters on the pipeline on screen; null clears one. Filters replace the address, they do not push. */
export const filterTasks = defineAction({
  id: 'tasks.filter',
  title: 'Filter tasks',
  kind: 'view',
  input: z.strictObject({
    owner: clearable(actor),
    waiting: clearable(actor),
    asked: clearable(actor),
    tag: clearable(z.string().check(z.maxLength(120), z.describe('a tag key'))),
    due: clearable(z.enum(['overdue', 'week', 'any'] as const)),
    late: clearable(z.boolean()),
    q: clearable(z.string().check(z.maxLength(200))),
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

/** Merge a task into another: `into` stays and gets its details, the task is closed as a duplicate. It cannot be undone
    (backend/task_merge.py has no way back), so it is the person's own press. */
export const merge = defineAction({
  id: 'tasks.merge',
  title: 'Merge a task into another',
  kind: 'change',
  tico: false,
  input: z.strictObject({ task, into: task, owner: clearable(actor) }),
  run: (_ctx, { task, into, owner }) => said(() => mergeTask(task, into, owner), "Couldn't merge the tasks"),
  did: (_input, out) => (out ? `Merged #${out.closed.number ?? out.closed.id} into #${out.task.number ?? out.task.id}` : 'Merged the tasks'),
  target: ({ into }, out) => ({ kind: 'task', id: out?.task.number ?? into }),
});

export const create = defineAction({
  id: 'tasks.create',
  title: 'New task',
  kind: 'change',
  input: z.strictObject({
    title: z.string().check(z.minLength(1), z.maxLength(300)),
    pipeline,
    owner: clearable(actor),
    stage: clearable(z.string().check(z.minLength(1), z.maxLength(100), z.describe('a stage id'))),
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

/** One task (`tasks` is its id) or many (a list), to `to`: a stage (back is a stage before the one it is in), next, done,
    closed or reopen. With `pipeline`, to that pipeline's stage `to` (its first when left out), and on the task's page the
    address follows it. `anyway` moves past what the stage still needs, with `why` on record. One task's refusal carries
    what is missing (task/model.ts said); many say what was left in a toast, and Close says so and takes it back. */
export const move = defineAction({
  id: 'tasks.move',
  title: 'Move tasks',
  kind: 'change',
  input: z
    .strictObject({
      tasks: z.union([task, tasks.check(z.minLength(1))]),
      to: z.optional(z.string().check(z.minLength(1), z.maxLength(100), z.describe('a stage id or name, or next, done, closed or reopen'))),
      pipeline: z.optional(z.string().check(z.minLength(1), z.maxLength(200), z.describe('another pipeline, by id or name'))),
      anyway: z.optional(z.boolean()),
      why: z.optional(z.string().check(z.maxLength(2000))),
    })
    .check(
      z.refine((i) => !!(i.to || i.pipeline), 'Say where: to, pipeline or both'),
      z.refine((i) => !i.anyway || !!i.why?.trim(), 'Moving anyway needs a why'),
    ),
  run: async ({ router }, { tasks: ids, to, pipeline, why }) => {
    const reason = why?.trim() || undefined;
    if (Array.isArray(ids)) {
      const { moved, movedUnseen, skipped } = await said(() => moveMany(ids, to, pipeline, reason), "Couldn't move the tasks");
      tell(skipped, ids.length);
      const closed = moved.length + movedUnseen;
      if (to === 'closed' && closed > 0)
        toast({ text: `Closed ${plural(closed)}`, action: moved.length ? { label: 'Undo', run: () => void moveMany(moved, 'reopen') } : undefined });
      return undefined;
    }
    if (!pipeline) return (await taskSaid(moveOne(ids, to ?? '', reason))).task;
    const { task: t } = await taskSaid(moveTaskTo(ids, pipeline, to, reason));
    if (/^\/tasks\/[^/]+\/(?!edit$)[^/]+/.test(router.state.location.pathname)) await router.navigate({ to: `${where(t.pipeline_id ?? pipeline)}/${t.number ?? t.id}` });
    return t;
  },
  did: ({ tasks: ids, to }, out) => {
    const verb = to === 'closed' ? 'Closed' : to === 'reopen' ? 'Reopened' : 'Moved';
    return Array.isArray(ids) ? `${verb} ${plural(ids.length)}` : out ? `${verb} #${out.number ?? out.id}` : verb;
  },
  target: ({ tasks: ids, to }, out) =>
    !Array.isArray(ids) ? { kind: 'task', id: out?.number ?? ids } : to && !['next', 'done', 'closed', 'reopen'].includes(to) ? { kind: 'stage', id: to } : null,
});

export const assignAll = defineAction({
  id: 'tasks.assignMany',
  title: 'Give tasks to an owner',
  kind: 'change',
  input: z.strictObject({ tasks, owner: actor }),
  run: async (_ctx, { tasks, owner }) => tell(await said(() => assignMany(tasks, owner), "Couldn't change the owner"), tasks.length),
  did: ({ tasks }) => `Assigned ${plural(tasks.length)}`,
});

export const snoozeAll = defineAction({
  id: 'tasks.snoozeMany',
  title: 'Snooze tasks',
  kind: 'change',
  input: z.strictObject({ tasks, until: z.string().check(z.minLength(10), z.maxLength(40), z.describe('an ISO date and time')) }),
  run: async (_ctx, { tasks, until }) => tell(await said(() => snoozeMany(tasks, until), "Couldn't snooze the tasks"), tasks.length),
  did: ({ tasks }) => `Snoozed ${plural(tasks.length)}`,
});

/* ---------------------------------------------------------------- Edit pipeline (its page is /tasks/<pipeline>/edit; ?new=1 makes one) */

const stageDraft = z.strictObject({
  id: z.optional(z.string().check(z.minLength(1), z.maxLength(100), z.describe('a saved stage id; leave out for a new stage'))),
  name: z.string().check(z.minLength(1), z.maxLength(80)),
  side: z.boolean(),
  purpose: z.optional(z.string().check(z.maxLength(300))),
  owner: clearable(z.string().check(z.minLength(1), z.maxLength(120), z.describe("an actor id, assignee (the task's), or pick (one of several, as set)"))),
  task_limit: clearable(z.int().check(z.minimum(1), z.maximum(1000))),
});

const pipelineFacts = {
  goal: z.optional(z.string().check(z.maxLength(300))),
  manager_id: clearable(actor),
  factory_id: clearable(z.string().check(z.minLength(1), z.maxLength(100), z.describe('a factory id'))),
  prefix: clearable(z.string().check(z.regex(/^[A-Za-z]{1,2}$/), z.describe('one or two letters'))),
};

/** Saves a pipeline: its Goal, Manager, Factory and Prefix, and its stages in order with each one's goal, owner and limit.
    `version` is the settings_version the person read: someone else's save in between is refused (task_changed). A
    removed stage that holds tasks needs `move_to`, where they go: that moves them and deletes the stage, which cannot be
    undone, so it is the person's own press, never Tico's. */
export const editPipeline = defineAction({
  id: 'tasks.editPipeline',
  title: 'Edit a pipeline',
  kind: 'change',
  input: z.strictObject({
    pipeline,
    ...pipelineFacts,
    stages: z.array(stageDraft).check(z.maxLength(40)),
    version: z.optional(z.int().check(z.minimum(0))),
    move_to: z.optional(z.strictObject({ pipeline, stage: z.string().check(z.minLength(1), z.maxLength(100)) })),
  }),
  // Removing a stage that holds tasks cannot be undone: Tico may save the rest, not that.
  can: ({ via }, { move_to }) => !(via === 'tico' && move_to),
  run: (_ctx, { pipeline, stages, version, move_to, ...facts }) => savePipeline(pipeline, { stages, facts, version, moveTo: move_to }),
  did: () => 'Saved the pipeline',
  target: ({ pipeline }) => ({ kind: 'page', id: where(pipeline) }),
});

/** Makes a pipeline (the company's owner and Admins; the server says so to anyone else) and opens it. */
export const makePipeline = defineAction({
  id: 'tasks.createPipeline',
  title: 'Make a pipeline',
  kind: 'change',
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(80)),
    ...pipelineFacts,
    stages: z.array(stageDraft).check(z.maxLength(40)),
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
  input: z.strictObject({}),
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
  input: z.strictObject({ task, open: z.optional(z.boolean()) }),
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

const text = (max: number) => z.optional(z.string().check(z.maxLength(max)));
const requesterFields = {
  name: text(200),
  email: text(320),
  phone: text(100),
  other: text(500),
  organization: text(200),
  note: text(4000),
  source: z.optional(z.enum(['manual', 'public_form', 'inbox', 'meeting'] as const)),
  important: z.optional(z.boolean()),
};

/** Record who asked for a task: a teammate (`actor`) or someone outside the team. */
export const addTaskRequester = defineAction({
  id: 'tasks.addRequester',
  title: 'Add who asked for a feature',
  kind: 'change',
  input: z.strictObject({ task, actor: z.optional(actor), ...requesterFields }),
  run: (_ctx, { task, ...input }) => said(() => addRequester(task, input), "Couldn't add the requester"),
  did: () => 'Added a requester',
});

export const updateTaskRequester = defineAction({
  id: 'tasks.updateRequester',
  title: 'Change a requester',
  kind: 'change',
  input: z.strictObject({ task, requester: z.string().check(z.minLength(1), z.maxLength(100), z.describe('a requester id')), ...requesterFields }),
  run: (_ctx, { task, requester, ...input }) => said(() => updateRequester(task, requester, input), "Couldn't save the requester"),
  did: () => 'Saved the requester',
  target: ({ requester }) => ({ kind: 'requester', id: requester }),
});

export const removeTaskRequester = defineAction({
  id: 'tasks.removeRequester',
  title: 'Remove a requester',
  kind: 'change',
  input: z.strictObject({ task, requester: z.string().check(z.minLength(1), z.maxLength(100), z.describe('a requester id')) }),
  run: (_ctx, { task, requester }) => said(() => removeRequester(task, requester), "Couldn't remove the requester"),
  did: () => 'Removed a requester',
});

/* ---------------------------------------------------------------- tags (ui/app/tags.ts) */

const tagId = z.string().check(z.minLength(1), z.maxLength(100), z.describe("a tag's id or key"));
const tagKey = z.string().check(z.minLength(1), z.maxLength(40), z.regex(/^[a-z0-9][a-z0-9-]*$/), z.describe('a tag key: lowercase words joined by hyphens'));
const metadata = z.optional(z.string().check(z.maxLength(4000), z.describe('a JSON object of fields, such as {"date": "2030-01-02"}')));
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
  input: z.strictObject({ q: clearable(z.string().check(z.maxLength(200))), owner: clearable(actor), late: clearable(z.boolean()) }),
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
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(100)),
    key: z.optional(tagKey),
    notes: z.optional(z.string().check(z.maxLength(20000), z.describe('markdown'))),
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
  input: z.strictObject({ tag: tagId, name: z.optional(z.string().check(z.minLength(1), z.maxLength(100))), notes: z.optional(z.string().check(z.maxLength(20000))), metadata }),
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
  input: z.strictObject({ tag: tagId }),
  run: (_ctx, { tag }) => said(() => moreTagTasks(tag), "Couldn't read more"),
  did: () => 'Read more',
  target: ({ tag }) => onTag(tag),
});

export const newTaggedTask = defineAction({
  id: 'tags.newTask',
  title: 'New task with a tag on it',
  kind: 'change',
  input: z.strictObject({ tag: tagId, key: z.string().check(z.minLength(1), z.maxLength(120), z.describe("the tag's key")), title: z.string().check(z.minLength(1), z.maxLength(300)), pipeline, owner: clearable(actor) }),
  run: (_ctx, { tag, key, ...input }) => said(() => createTaggedTask(tag, key, input), "Couldn't create the task"),
  did: (_input, out) => (out?.number ? `Filed #${out.number}` : 'Filed a task'),
  target: (_input, out) => (out ? { kind: 'task', id: out.number ?? out.id } : null),
});
