import type { Filters } from './queries';

export type View = 'board' | 'list';

/** `tico-next:tasks.view.v1:<pipeline>`: the view each pipeline was last shown in. */
export const VIEW_PREF = 'tasks.view.v1';
/** `tico-next:tasks.pipeline.v1`: the pipeline Tasks opened last. */
export const LAST_PREF = 'tasks.pipeline.v1';

/** What a pipeline's address holds: the view (when chosen) and the filters. */
export interface TasksSearch extends Filters {
  view?: View;
}

const text = (v: unknown, max: number) => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);

/** `?view=board|list&owner=bot:qa&tag=ui&late=true&q=checkout`; anything else is dropped. */
export function validateTasksSearch(raw: Record<string, unknown>): TasksSearch {
  const out: TasksSearch = {};
  if (raw.view === 'board' || raw.view === 'list') out.view = raw.view;
  const owner = text(raw.owner, 120);
  if (owner && /^(human|bot):./.test(owner)) out.owner = owner;
  const tag = text(raw.tag, 120);
  if (tag) out.tag = tag;
  if (raw.late === true || raw.late === 'true' || raw.late === 1 || raw.late === '1') out.late = true;
  const q = text(raw.q, 200);
  if (q) out.q = q;
  return out;
}

export const filtersOf = ({ owner, tag, late, q }: TasksSearch): Filters => ({ owner, tag, late, q });
