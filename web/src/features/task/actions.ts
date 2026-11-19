import { defineAction, s } from '@/actions';
import { answerAsk, assignTask, markDone, moveTask, renameTask, setTaskDue, tagTask } from './queries';

/* Every change a person makes on an open task. `task` is the task's id (the detail's, not its number). A refusal
   carries the server's reason in the result ("task_requirement: Needs the QA report"), which the control shows. */

const task = s.string({ min: 1, max: 100 });

/** The run's error as "code: reason", so the control can say why. */
async function said<T>(p: Promise<T>): Promise<T> {
  try {
    return await p;
  } catch (e) {
    const err = e as { status?: number; code?: string; message?: string; extra?: { missing?: unknown } };
    if (err.status === 401 || err.status === 403 || !err.code) throw e;
    const missing = Array.isArray(err.extra?.missing) && err.extra.missing.length ? ` Missing: ${err.extra.missing.join(', ')}.` : '';
    throw Object.assign(new Error(err.message), { status: err.status, code: `${err.code}: ${err.message ?? ''}${missing}` });
  }
}

export const rename = defineAction({
  id: 'task.rename',
  title: 'Rename a task',
  kind: 'change',
  input: s.object({ task, title: s.string({ min: 1, max: 300 }) }),
  run: (_ctx, i) => said(renameTask(i.task, i.title.trim())),
  did: () => 'Renamed',
  target: (i) => ({ kind: 'task-title', id: i.task }),
});

export const move = defineAction({
  id: 'task.move',
  title: 'Move a task',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 100 }), why: s.optional(s.string({ max: 2000 })) }),
  run: (_ctx, i) => said(moveTask(i.task, i.to, i.why)),
  did: (i) => (i.to === 'closed' ? 'Closed' : i.to === 'reopen' ? 'Reopened' : 'Moved'),
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

export const assign = defineAction({
  id: 'task.assign',
  title: 'Give a task to someone',
  kind: 'change',
  input: s.object({ task, owner: s.string({ min: 1, max: 200 }), setAnyway: s.optional(s.boolean()) }),
  run: (_ctx, i) => said(assignTask(i.task, i.owner, i.setAnyway)),
  did: () => 'Given',
  target: (i) => ({ kind: 'task-owner', id: i.task }),
});

export const setDue = defineAction({
  id: 'task.setDue',
  title: 'Set when a task is due',
  kind: 'change',
  input: s.object({ task, due: s.nullable(s.string({ pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a day, like 2026-11-24' })) }),
  run: (_ctx, i) => said(setTaskDue(i.task, i.due)),
  did: (i) => (i.due ? 'Set the due date' : 'Cleared the due date'),
  target: (i) => ({ kind: 'task-due', id: i.task }),
});

export const tag = defineAction({
  id: 'task.tag',
  title: 'Tag a task',
  kind: 'change',
  input: s.object({ task, labels: s.array(s.string({ min: 1, max: 60 }), { max: 50 }) }),
  run: (_ctx, i) => said(tagTask(i.task, i.labels)),
  did: () => 'Tagged',
  target: (i) => ({ kind: 'task-tags', id: i.task }),
});

export const done = defineAction({
  id: 'task.done',
  title: 'Mark a task done',
  kind: 'change',
  input: s.object({ task }),
  run: (_ctx, i) => said(markDone(i.task)),
  did: () => 'Marked done',
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

/* An approval is the person's own: Tico never presses it for them. */
const answer = s.object({ task, requirement: s.string({ min: 1, max: 100 }) });

export const approve = defineAction({
  id: 'task.approve',
  title: 'Approve',
  kind: 'change',
  input: answer,
  tico: false,
  run: (_ctx, i) => said(answerAsk(i.task, i.requirement, 'ok')),
  did: () => 'Approved',
  target: (i) => ({ kind: 'task-answer', id: i.task }),
});

export const sendBack = defineAction({
  id: 'task.sendBack',
  title: 'Send back',
  kind: 'change',
  input: s.object({ task, requirement: s.string({ min: 1, max: 100 }), note: s.string({ min: 1, max: 4000 }) }),
  tico: false,
  run: (_ctx, i) => said(answerAsk(i.task, i.requirement, 'no', i.note.trim())),
  did: () => 'Sent back',
  target: (i) => ({ kind: 'task-answer', id: i.task }),
});
