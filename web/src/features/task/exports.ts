// One open task, everywhere it appears. The only file other code imports from this feature.
export { TaskPage, TaskChat, TaskById, type TaskPageProps, type TaskChatProps } from './TaskPage';
export { loadTask, taskRefQuery, taskDetailQuery } from './queries';
export { tabAddress as taskAddress } from './model';

/** What a task's address may carry: a file opened from Details, and in Flow the open tab. */
export interface TaskSearch {
  tab?: string;
  file?: string;
}

/** `validateSearch` for the task routes: keeps the two words it knows, drops anything else. */
export function taskSearch(raw: Record<string, unknown>): TaskSearch {
  const out: TaskSearch = {};
  if (typeof raw.tab === 'string' && raw.tab) out.tab = raw.tab;
  if (typeof raw.file === 'string' && raw.file) out.file = raw.file;
  return out;
}
