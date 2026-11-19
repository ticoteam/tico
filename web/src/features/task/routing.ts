// What the routes use before a task's code loads: its first read, its address and the address check. No components
// here: the first load imports this file, and the task page comes through exports.ts with its route.
export { loadTask, taskRefQuery, taskDetailQuery } from './queries';
export { tabAddress as taskAddress } from './model';

/**
 * What a task's address may carry: a file opened from Details (`full` shows a picture full size), in Flow the open
 * tab, and the version of the open page (`v`) with the one its changes are marked against (`vs`).
 */
export interface TaskSearch {
  tab?: string;
  file?: string;
  full?: string;
  v?: number;
  vs?: number;
}

const n = (x: unknown): number | undefined => {
  const v = typeof x === 'number' ? x : typeof x === 'string' && /^\d+$/.test(x) ? Number(x) : NaN;
  return Number.isInteger(v) && v > 0 ? v : undefined;
};

/** `validateSearch` for the task routes: keeps the words it knows, drops anything else. */
export function taskSearch(raw: Record<string, unknown>): TaskSearch {
  const out: TaskSearch = {};
  if (typeof raw.tab === 'string' && raw.tab) out.tab = raw.tab;
  if (typeof raw.file === 'string' && raw.file) out.file = raw.file;
  if (typeof raw.full === 'string' && raw.full) out.full = raw.full;
  const v = n(raw.v);
  const vs = n(raw.vs);
  if (v) out.v = v;
  if (vs) out.vs = vs;
  return out;
}
