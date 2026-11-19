import { defineAction, s } from '@/actions';
import { ApiError } from '@/api/client';
import { toast } from '@/kit/Toast';
import { writePref } from '@/prefs';
import { createTask, listsOf, moveTask, pipelinesQuery, type Pipeline } from './queries';
import { VIEW_PREF, type View } from './search';

const pipeline = s.string({ min: 1, max: 100, what: 'a pipeline id' });
const task = s.string({ min: 1, max: 100, what: 'a task id' });
const where = (p: string) => `/tasks/${encodeURIComponent(p)}`;

/** A pipeline's page is on screen (`/tasks/<pipeline>`), not a task or another page. */
const onPipeline = (pathname: string) => /\/tasks\/[^/]+\/?$/.test(pathname);

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

export const setView = defineAction({
  id: 'tasks.setView',
  title: 'Show tasks as a board or a list',
  kind: 'view',
  input: s.object({ pipeline, view: s.oneOf(['board', 'list'] as const) }),
  run: async ({ router }, { pipeline, view }) => {
    writePref(`${VIEW_PREF}:${pipeline}`, view satisfies View);
    await router.navigate({ to: where(pipeline), search: (prev: Record<string, unknown>) => ({ ...prev, view }) });
  },
  did: ({ view }) => (view === 'board' ? 'Switched Tasks to a board' : 'Switched Tasks to a list'),
  target: () => ({ kind: 'tasks', id: 'view' }),
});

/** Sets or clears filters on the pipeline on screen; null clears one. Filters replace the address, they do not push. */
export const filterTasks = defineAction({
  id: 'tasks.filter',
  title: 'Filter tasks',
  kind: 'view',
  input: s.object({
    owner: s.optional(s.nullable(s.string({ max: 120, what: 'an actor id: human:<id> or bot:<slug>' }))),
    tag: s.optional(s.nullable(s.string({ max: 120, what: 'a tag key' }))),
    late: s.optional(s.nullable(s.boolean())),
    q: s.optional(s.nullable(s.string({ max: 200 }))),
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

export const move = defineAction({
  id: 'tasks.move',
  title: 'Move a task to a stage',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 100, what: 'a stage id or name, or next, done or closed' }) }),
  run: async (_ctx, { task, to }) => {
    try {
      return await moveTask(task, to);
    } catch (e) {
      toast({ text: reason(e, "Couldn't move the task"), kind: 'failed' });
      throw e;
    }
  },
  did: (_input, out) => (out ? `Moved #${out.number ?? out.id}` : 'Moved the task'),
  target: ({ task }, out) => ({ kind: 'task', id: out?.number ?? task }),
});

export const create = defineAction({
  id: 'tasks.create',
  title: 'New task',
  kind: 'change',
  input: s.object({
    title: s.string({ min: 1, max: 300 }),
    pipeline,
    owner: s.optional(s.nullable(s.string({ max: 120, what: 'an actor id: human:<id> or bot:<slug>' }))),
  }),
  run: async (_ctx, input) => {
    try {
      return await createTask(input, listsOf(input.pipeline));
    } catch (e) {
      toast({ text: reason(e, "Couldn't create the task"), kind: 'failed' });
      throw e;
    }
  },
  did: (_input, out) => (out?.number ? `Filed #${out.number}` : 'Filed a task'),
  target: (_input, out) => (out ? { kind: 'task', id: out.number ?? out.id } : null),
});
