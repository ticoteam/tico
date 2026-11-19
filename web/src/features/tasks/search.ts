import type { Due, Filters } from './queries';

/** Board and list are a pipeline's open tasks; Archived is its snoozed, done and closed ones. */
export type View = 'board' | 'list' | 'archived';

/** `tico-next:tasks.view.v1:<pipeline>`: the view each pipeline was last shown in (board or list, never Archived). */
export const VIEW_PREF = 'tasks.view.v1';
/** `tico-next:tasks.pipeline.v1`: the pipeline Tasks opened last. */
export const LAST_PREF = 'tasks.pipeline.v1';

/** What a pipeline's address holds: the view (when chosen), the filters, and the side panel. */
export interface TasksSearch extends Filters {
  view?: View;
  /** Edit pipeline, beside the page. */
  panel?: 'pipeline';
}

const text = (v: unknown, max: number) => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);
const actor = (v: unknown) => {
  const a = text(v, 120);
  return a && /^(human|bot):./.test(a) ? a : undefined;
};
const DUE: readonly Due[] = ['overdue', 'week', 'any'];

/** `?view=board|list|archived&owner=bot:qa&waiting=human:1&asked=…&tag=ui&due=week&late=true&q=checkout&panel=pipeline`. */
export function validateTasksSearch(raw: Record<string, unknown>): TasksSearch {
  const out: TasksSearch = {};
  if (raw.view === 'board' || raw.view === 'list' || raw.view === 'archived') out.view = raw.view;
  if (raw.panel === 'pipeline') out.panel = 'pipeline';
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
  return out;
}

export const filtersOf = ({ owner, waiting, asked, tag, due, late, q }: TasksSearch): Filters => ({ owner, waiting, asked, tag, due, late, q });
