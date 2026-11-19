import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s } from '@/actions';
import {
  addSubtask,
  answerAsk,
  askSomeone,
  giveTask,
  linkTask,
  markDone,
  moveTask,
  newTaskPage,
  relateTask,
  renameTask,
  setTaskDue,
  setTaskParent,
  snoozeTask,
  tagTask,
  dropLink,
} from './queries';

/* Every change a person makes on an open task. `task` is the task's id (the detail's, not its number). A refusal
   carries the server's reason in the result ("task_not_met: Not met: QA report", then a line for each thing missing,
   "- QA report: not approved"), which the control shows (model.ts refusalOf). */

const task = s.string({ min: 1, max: 100 });
/** Another task as a person names it: its number, or its id. */
const other = s.string({ min: 1, max: 200 });

/** The run's error as "code: reason" and a line for each thing missing, so the control can say why. */
async function said<T>(p: Promise<T>): Promise<T> {
  try {
    return await p;
  } catch (e) {
    const err = e as { status?: number; code?: string; message?: string; extra?: { missing?: unknown } };
    if (err.status === 401 || err.status === 403 || !err.code) throw e;
    const missing = Array.isArray(err.extra?.missing) ? err.extra.missing.map((m) => `\n- ${String(m)}`).join('') : '';
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
  input: s.object({ task, to: s.string({ min: 1, max: 100 }) }),
  run: (_ctx, i) => said(moveTask(i.task, i.to)),
  did: (i) => (i.to === 'closed' ? 'Closed' : i.to === 'reopen' ? 'Reopened' : 'Moved'),
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

/** Flow's Move back: to a stage before the one it is in. */
export const moveBack = defineAction({
  id: 'task.moveBack',
  title: 'Move a task back',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 100 }) }),
  run: (_ctx, i) => said(moveTask(i.task, i.to)),
  did: () => 'Moved back',
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

/* Past what is missing, with a reason on record. The server decides who may; Tico acts with the person's rights. */
export const moveAnyway = defineAction({
  id: 'task.moveAnyway',
  title: 'Move a task past what is missing',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 100 }), why: s.string({ min: 1, max: 2000 }) }),
  run: (_ctx, i) => said(moveTask(i.task, i.to, i.why.trim())),
  did: () => 'Moved',
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

export const give = defineAction({
  id: 'task.give',
  title: 'Give a task to someone',
  kind: 'change',
  input: s.object({ task, owner: s.string({ min: 1, max: 200 }), setAnyway: s.optional(s.boolean()) }),
  run: (_ctx, i) => said(giveTask(i.task, i.owner, i.setAnyway)),
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

/** Out of the open list until `until`; null brings it back now. */
export const snooze = defineAction({
  id: 'task.snooze',
  title: 'Snooze a task',
  kind: 'change',
  input: s.object({ task, until: s.nullable(s.string({ min: 10, max: 40, what: 'an ISO date and time' })) }),
  run: (_ctx, i) => said(snoozeTask(i.task, i.until)),
  did: (i) => (i.until ? 'Snoozed' : 'Unsnoozed'),
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

export const setParent = defineAction({
  id: 'task.setParent',
  title: 'Put a task under another',
  kind: 'change',
  input: s.object({ task, parent: other }),
  run: (_ctx, i) => said(setTaskParent(i.task, i.parent)),
  did: () => 'Moved it under the task',
  target: (i) => ({ kind: 'task-parent', id: i.task }),
});

/** related; depends_on: it is blocked by the other; blocks: the other is blocked by it. */
export const relate = defineAction({
  id: 'task.relate',
  title: 'Relate a task to another',
  kind: 'change',
  input: s.object({ task, other, kind: s.oneOf(['related', 'depends_on', 'blocks'] as const), remove: s.optional(s.boolean()) }),
  run: (_ctx, i) => said(relateTask(i.task, i.other, i.kind, i.remove)),
  did: (i) => (i.remove ? 'Took the relation off' : 'Related'),
  target: (i) => ({ kind: 'task-relations', id: i.task }),
});

export const subtask = defineAction({
  id: 'task.subtask',
  title: 'Add a subtask',
  kind: 'change',
  input: s.object({ task, title: s.string({ min: 1, max: 300 }), owner: s.optional(s.string({ min: 1, max: 200 })) }),
  run: (_ctx, i) => said(addSubtask(i.task, i.title.trim(), i.owner)),
  did: () => 'Added a subtask',
  target: (i) => ({ kind: 'task-subtasks', id: i.task }),
});

export const ask = defineAction({
  id: 'task.ask',
  title: 'Ask someone for an OK',
  kind: 'change',
  input: s.object({ task, to: s.string({ min: 1, max: 200 }), line: s.string({ min: 1, max: 300 }) }),
  run: (_ctx, i) => said(askSomeone(i.task, i.to, i.line.trim())),
  did: () => 'Asked',
  target: (i) => ({ kind: 'task-leave', id: i.task }),
});

export const link = defineAction({
  id: 'task.link',
  title: 'Add a link to a task',
  kind: 'change',
  input: s.object({ task, url: s.string({ min: 1, max: 2000, pattern: /^https?:\/\/\S+$/, what: 'a web address' }), title: s.optional(s.string({ max: 300 })) }),
  run: (_ctx, i) => said(linkTask(i.task, i.url.trim(), i.title?.trim() || undefined)),
  did: () => 'Added the link',
  target: (i) => ({ kind: 'task-links', id: i.task }),
});

export const removeLink = defineAction({
  id: 'task.removeLink',
  title: 'Remove a link from a task',
  kind: 'change',
  input: s.object({ task, link: s.string({ min: 1, max: 100 }) }),
  run: (_ctx, i) => said(dropLink(i.task, i.link)),
  did: () => 'Removed the link',
  target: (i) => ({ kind: 'task-links', id: i.task }),
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

/* ---------------------------------------------------------------- what the open page shows */

/** A task's page or Details is on screen: under its pipeline, or inside Flow. */
const onTask = (path: string) => /^\/(tasks\/[^/]+\/[^/]+|flow\/[^/]+)/.test(path);
const version = s.number({ int: true, min: 1 });

/** The address keeps it, so Back and a shared link come back to the same version. */
function show(router: AnyRouter, change: Record<string, unknown>) {
  // The same page by its path: the address changes only in its search.
  return router.navigate({
    to: router.state.location.pathname,
    search: (prev: Record<string, unknown>) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(change)) {
        if (v == null) delete next[k];
        else next[k] = v;
      }
      return next;
    },
  });
}

/** One version of the open page; null is its newest. Picking one ends a comparison. */
export const showVersion = defineAction({
  id: 'task.showVersion',
  title: 'Show a version of a page',
  kind: 'view',
  input: s.object({ task, version: s.nullable(version) }),
  can: ({ router }) => onTask(router.state.location.pathname),
  run: ({ router }, i) => show(router, { v: i.version, vs: null }),
  did: (i) => (i.version ? `Opened v${i.version}` : 'Opened the newest version'),
  target: (i) => ({ kind: 'task-version', id: i.task }),
});

/** Marks in the open version what changed since `against`; null takes the marks off. */
export const compare = defineAction({
  id: 'task.compare',
  title: 'Mark what changed in a page',
  kind: 'view',
  input: s.object({ task, against: s.nullable(version), version: s.optional(s.nullable(version)) }),
  can: ({ router }) => onTask(router.state.location.pathname),
  run: ({ router }, i) => show(router, { vs: i.against, ...(i.version !== undefined && { v: i.version }) }),
  did: (i) => (i.against ? `Marked the changes since v${i.against}` : 'Took the marks off'),
  target: (i) => ({ kind: 'task-changes', id: i.task }),
});

/** A picture among the task's files, full size over the page. */
export const showPicture = defineAction({
  id: 'task.showPicture',
  title: 'Show a picture full size',
  kind: 'view',
  input: s.object({ task, file: s.nullable(s.string({ min: 1, max: 100 })) }),
  can: ({ router }) => onTask(router.state.location.pathname),
  run: ({ router }, i) => show(router, { full: i.file }),
  did: (i) => (i.file ? 'Opened the picture' : 'Closed the picture'),
  target: (i) => ({ kind: 'task-files', id: i.task }),
});

export const newPage = defineAction({
  id: 'task.newPage',
  title: 'Write a new page on a task',
  kind: 'change',
  input: s.object({ task, title: s.string({ min: 1, max: 200 }), text: s.string({ min: 1, max: 1_000_000 }) }),
  run: (_ctx, i) => said(newTaskPage(i.task, i.title.trim(), i.text)),
  did: (i) => `Wrote the page ${i.title.trim()}`,
  target: (i) => ({ kind: 'task-files', id: i.task }),
});
