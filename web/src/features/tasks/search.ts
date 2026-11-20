import { directoryQuery, queryClient } from '@/data';
import { defineSearch } from '@/search/define';
import { faceOf, fetchFor, HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { pipelinesQuery, taskSearchQuery, type Due, type Filters } from './queries';

/** Board and list are a pipeline's open tasks; Archived is its snoozed, done and closed ones. */
export type View = 'board' | 'list' | 'archived';

/** `tico-next:tasks.view.v1:<pipeline>`: the view each pipeline was last shown in (board or list, never Archived). */
export const VIEW_PREF = 'tasks.view.v1';
/** `tico-next:tasks.pipeline.v1`: the pipeline Tasks opened last. */
export const LAST_PREF = 'tasks.pipeline.v1';

/** What a pipeline's address holds: the view (when chosen) and the filters. */
export interface TasksSearch extends Filters {
  view?: View;
  /** The New task box is up (search's "New task" opens it on the pipeline). */
  new?: 1;
}

const text = (v: unknown, max: number) => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);
const actor = (v: unknown) => {
  const a = text(v, 120);
  return a && /^(human|bot):./.test(a) ? a : undefined;
};
const DUE: readonly Due[] = ['overdue', 'week', 'any'];

/** `?view=board|list|archived&owner=bot:qa&waiting=human:1&asked=…&tag=ui&due=week&late=true&q=checkout&new=1`. */
export function validateTasksSearch(raw: Record<string, unknown>): TasksSearch {
  const out: TasksSearch = {};
  if (raw.view === 'board' || raw.view === 'list' || raw.view === 'archived') out.view = raw.view;
  const owner = actor(raw.owner);
  if (owner) out.owner = owner;
  const waiting = actor(raw.waiting);
  if (waiting) out.waiting = waiting;
  const asked = actor(raw.asked);
  if (asked) out.asked = asked;
  const tag = text(raw.tag, 120);
  if (tag) out.tag = tag;
  if (DUE.includes(raw.due as Due)) out.due = raw.due as Due;
  if (raw.late === true || raw.late === 'true' || raw.late === 1 || raw.late === '1') out.late = true;
  const q = text(raw.q, 200);
  if (q) out.q = q;
  if (raw.new === 1 || raw.new === '1' || raw.new === true || raw.new === 'true') out.new = 1;
  return out;
}

/** What the Roadmap's address holds: the filters, and the task whose requesters are open. */
export interface RoadmapSearch extends Filters {
  /** A task's id: its Requesters dialog is open. */
  requesters?: string;
}

/** `?owner=…&late=true&requesters=<task id>`: the pipeline's filters, without its view. */
export function validateRoadmapSearch(raw: Record<string, unknown>): RoadmapSearch {
  const { view: _view, new: _new, ...filters } = validateTasksSearch(raw);
  const task = text(raw.requesters, 100);
  return task ? { ...filters, requesters: task } : filters;
}

export const filtersOf = ({ owner, waiting, asked, tag, due, late, q }: Filters): Filters => ({ owner, waiting, asked, tag, due, late, q });

/* ---------------------------------------------------------------- Cmd+K */

/** The pipelines by name, as the menu lists them under Tasks: each opens its board. */
export const pipelinesSearch = defineSearch({
  group: 'Pipelines',
  order: 15,
  find: async (q) => {
    if (!q.trim()) return [];
    const pipelines = await queryClient.ensureQueryData(pipelinesQuery);
    return pipelines
      .filter((p) => matches(p.name, q))
      .slice(0, HITS)
      .map((p) => ({ key: `pipeline:${p.id}`, title: p.name, lead: { icon: 'tasks' as const }, run: { id: 'tasks.open', input: { pipeline: p.id } } }));
  },
});

/** A task's id as a board shows it, with its pipeline's letters ("G35001", "E-12"): the number in it. */
const ID = /^#?[a-z]{1,2}-?(\d{2,})$/i;

/** Tasks in any pipeline by title words, #N or the id a board shows (G35001), from the server's search; each shows
    its stage and owner's face. */
export const tasksSearch = defineSearch({
  group: 'Tasks',
  order: 10,
  remote: true,
  find: async (q, signal) => {
    const words = q.trim().slice(0, 200);
    if (!words) return [];
    // the server knows a task by its number, not by its pipeline's letters: ask for the number too
    const id = ID.exec(words)?.[1];
    const [found, byId, pipelines, directory] = await Promise.all([
      fetchFor(taskSearchQuery(words, HITS), signal),
      id ? fetchFor(taskSearchQuery(id, HITS), signal).catch(() => null) : null,
      queryClient.ensureQueryData(pipelinesQuery).catch(() => []),
      queryClient.ensureQueryData(directoryQuery).catch(() => undefined),
    ]);
    const exact = byId?.tasks.filter((t) => String(t.number) === id) ?? [];
    const page = { tasks: [...exact, ...found.tasks.filter((t) => !exact.some((e) => e.id === t.id))] };
    const stages = new Map<string, string>();
    const names = new Map<string, string>();
    for (const p of pipelines) {
      names.set(p.id, p.name);
      for (const s of [...p.stages, p.done, p.closed]) if (s) stages.set(s.id, s.name);
    }
    return page.tasks.slice(0, HITS).map((t) => {
      const owner = t.owner_id ? directory?.byId[t.owner_id] : undefined;
      return {
        key: `task:${t.number}`,
        title: t.title,
        // "412" or "#412" finds it by its number, "G412" by the id its board shows
        name: exact.includes(t) ? words : `#${t.number}`,
        meta: (t.stage_id && stages.get(t.stage_id)) || (t.pipeline_id && names.get(t.pipeline_id)) || undefined,
        lead: owner ? faceOf(owner) : { icon: 'tasks' as const },
        run: { id: 'task.open', input: t.pipeline_id ? { task: t.number, pipeline: t.pipeline_id } : { task: t.number } },
      };
    });
  },
});
