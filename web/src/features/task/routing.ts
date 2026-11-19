// What the routes use before a task's code loads: its first read, its address and the address check. No components
// here: the first load imports this file, and the task page comes through exports.ts with its route.
export { loadTask, taskRefQuery, taskDetailQuery } from './queries';
export { tabAddress as taskAddress } from './model';

/**
 * What a task's address may carry: a file opened from Details (`full` shows a picture full size), in Flow the open
 * tab, the version of the open page (`v`) with the one its changes are marked against (`vs`), and an email's file
 * opened from its Email tab (`part`).
 */
export interface TaskSearch {
  tab?: string;
  file?: string;
  full?: string;
  v?: number;
  vs?: number;
  part?: number;
}

const whole = (x: unknown): number => (typeof x === 'number' ? x : typeof x === 'string' && /^\d+$/.test(x) ? Number(x) : NaN);
const n = (x: unknown): number | undefined => {
  const v = whole(x);
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
  // An email's files count from 0.
  const part = whole(raw.part);
  if (Number.isInteger(part) && part >= 0) out.part = part;
  return out;
}
