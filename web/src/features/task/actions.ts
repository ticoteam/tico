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

/* ---------------------------------------------------------------- Details' facts and the head's ⋯ (s58) */

import { deleteTask, followTask, moveTaskTo, setTaskHuman, setTaskPublic, setTaskReaders, setTaskRoles, setTaskSize, setTaskVisible } from './queries';

const actor =s.string({ min: 1, max: 200 });

export const setHuman = defineAction({
  id: 'task.setHuman',
  title: 'Set the person accountable for a task',
  kind: 'change',
  input: s.object({ task, human: s.nullable(actor) }),
  run: (_ctx, i) => said(setTaskHuman(i.task, i.human)),
  did: (i) => (i.human ? 'Set the Human' : 'Took the Human off'),
  target: (i) => ({ kind: 'task-human', id: i.task }),
});

export const setSize = defineAction({
  id: 'task.setSize',
  title: 'Size a task',
  kind: 'change',
  input: s.object({ task, size: s.number({ min: 0, max: 1_000_000 }) }),
  run: (_ctx, i) => said(setTaskSize(i.task, i.size)),
  did: () => 'Sized it',
  target: (i) => ({ kind: 'task-size', id: i.task }),
});

/** Who is on the task in one role (reviewer, qa…): the whole list for that role; an empty one takes the role off. */
export const setRole = defineAction({
  id: 'task.setRole',
  title: 'Put people on a task in a role',
  kind: 'change',
  input: s.object({ task, role: s.string({ pattern: /^[a-z0-9][a-z0-9_-]{0,39}$/, what: 'a short name: letters, digits, - or _' }), people: s.array(actor, { max: 50 }) }),
  run: (_ctx, i) => said(setTaskRoles(i.task, i.role, i.people)),
  did: () => 'Changed who is on it',
  target: (i) => ({ kind: 'task-people', id: i.task }),
});

export const setPublic = defineAction({
  id: 'task.setPublic',
  title: "Write a task's public title or summary",
  kind: 'change',
  input: s.object({ task, field: s.oneOf(['public_title', 'public_summary'] as const), text: s.string({ max: 8000 }) }),
  run: (_ctx, i) => said(setTaskPublic(i.task, i.field, i.text.trim())),
  did: () => 'Saved the public text',
  target: (i) => ({ kind: 'task-public', id: i.task }),
});

/* Showing a task to the whole team or on the public roadmap is the person's own press; making it private is open. */
export const setVisible = defineAction({
  id: 'task.setVisible',
  title: 'Change who can see a task',
  kind: 'change',
  input: s.object({ task, to: s.oneOf(['team', 'public', 'private'] as const) }),
  can: ({ via }, { to }) => !(via === 'tico' && to !== 'private'),
  run: (_ctx, i) => said(setTaskVisible(i.task, i.to)),
  did: (i) => (i.to === 'private' ? 'Made it private' : i.to === 'public' ? 'Put it on the public roadmap' : 'Shared it with the team'),
  target: (i) => ({ kind: 'task-visible', id: i.task }),
});

/** Who else sees a private task: ordinary work among teammates, so Tico may do it for the person. */
export const setReaders = defineAction({
  id: 'task.setReaders',
  title: 'Change who else sees a private task',
  kind: 'change',
  input: s.object({ task, add: s.array(actor, { max: 50 }), remove: s.array(actor, { max: 50 }) }),
  run: (_ctx, i) => said(setTaskReaders(i.task, i.add, i.remove)),
  did: (i) => (i.add.length ? 'Let them see it' : 'Took them off'),
  target: (i) => ({ kind: 'task-visible', id: i.task }),
});

/** Follow or unfollow: the person, or `actor` (someone who already sees the task). */
export const follow = defineAction({
  id: 'task.follow',
  title: 'Follow a task',
  kind: 'change',
  input: s.object({ task, actor: s.optional(actor), following: s.boolean() }),
  run: (_ctx, i) => said(followTask(i.task, { ...(i.actor && { actor: i.actor }), following: i.following })),
  did: (i) => (i.following ? 'Following' : 'Unfollowed'),
  target: (i) => ({ kind: 'task-followers', id: i.task }),
});

/** Open again a finished task, with why (docs/dev/pipelines-v3.md C24). */
export const reopen = defineAction({
  id: 'task.reopen',
  title: 'Reopen a task',
  kind: 'change',
  input: s.object({ task, why: s.string({ min: 1, max: 2000 }) }),
  run: (_ctx, i) => said(moveTask(i.task, 'reopen', i.why.trim())),
  did: () => 'Reopened',
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

/** To another pipeline, at its `stage` (its first when left out). On the task's page, the address follows it there. */
export const moveTo = defineAction({
  id: 'task.moveTo',
  title: 'Move a task to another pipeline',
  kind: 'change',
  input: s.object({ task, pipeline: s.string({ min: 1, max: 200 }), stage: s.optional(s.string({ min: 1, max: 200 })), why: s.optional(s.string({ max: 2000 })) }),
  run: async ({ router }, i) => {
    const r = await said(moveTaskTo(i.task, i.pipeline, i.stage, i.why?.trim() || undefined));
    const path = router.state.location.pathname;
    const n = r.task.number ?? r.task.id;
    if (path.startsWith('/tasks/')) await router.navigate({ to: `/tasks/${encodeURIComponent(r.task.pipeline_id ?? i.pipeline)}/${n}` });
  },
  did: () => 'Moved it',
  target: (i) => ({ kind: 'task-stage', id: i.task }),
});

/* A task goes to the trash, and only a person deletes one (the server refuses anyone acting for them). */
export const remove = defineAction({
  id: 'task.delete',
  title: 'Delete a task',
  kind: 'change',
  input: s.object({ task }),
  tico: false,
  run: async ({ router, queryClient }, i) => {
    const held = queryClient.getQueryData<{ pipeline_id: string | null }>(['tasks', 'detail', i.task]);
    await said(deleteTask(i.task));
    if (router.state.location.pathname.startsWith('/flow/')) await router.navigate({ to: '/flow' });
    else await router.navigate({ to: `/tasks/${encodeURIComponent(held?.pipeline_id ?? 'all')}` });
  },
  did: () => 'Deleted',
});

/* ---------------------------------------------------------------- a task's files and pages */

import type { Schema } from '@/actions';
import { archivePage, archiveTaskFile, deletePage, linkTaskPage, uploadTaskFile } from './queries';

const fileId = s.string({ min: 1, max: 100 });

/** A file from the person's disk: only a click or a drop has one. */
const picked: Schema<File> = {
  parse: (v, p) => (typeof File !== 'undefined' && v instanceof File ? { ok: true, value: v } : { ok: false, error: `${p || 'input'}: expected a file` }),
  json: () => ({ type: 'object', description: 'A file the person chose' }),
  optional: false,
  '~standard': {
    version: 1,
    vendor: 'tico',
    validate: (v) => (typeof File !== 'undefined' && v instanceof File ? { value: v } : { issues: [{ message: 'expected a file' }] }),
  },
};

/* The file comes from the person's own disk, so only their press adds it. */
export const upload = defineAction({
  id: 'task.upload',
  title: 'Add a file to a task',
  kind: 'change',
  input: s.object({ task, file: picked }),
  tico: false,
  run: (_ctx, i) => said(uploadTaskFile(i.task, i.file)),
  did: (i) => `Added ${i.file.name}`,
  target: (i) => ({ kind: 'task-files', id: i.task }),
});

export const linkPage = defineAction({
  id: 'task.linkPage',
  title: 'Link a page to a task',
  kind: 'change',
  input: s.object({ task, page: fileId }),
  run: (_ctx, i) => said(linkTaskPage(i.task, i.page)),
  did: () => 'Linked the page',
  target: (i) => ({ kind: 'task-files', id: i.task }),
});

/** Takes a file off the task's list (it is archived, not deleted), or brings it back. */
export const archiveFile = defineAction({
  id: 'task.archiveFile',
  title: 'Remove a file from a task',
  kind: 'change',
  input: s.object({ task, file: fileId, archived: s.boolean() }),
  run: (_ctx, i) => said(archiveTaskFile(i.task, i.file, i.archived)),
  did: (i) => (i.archived ? 'Removed the file' : 'Brought the file back'),
  target: (i) => ({ kind: 'task-files', id: i.task }),
});

/** An archived page leaves the task's tabs; restoring brings it back. */
export const archiveTaskPage = defineAction({
  id: 'task.archivePage',
  title: 'Archive a page',
  kind: 'change',
  input: s.object({ task, page: fileId, archived: s.boolean() }),
  run: (_ctx, i) => said(archivePage(i.task, i.page, i.archived)),
  did: (i) => (i.archived ? 'Archived the page' : 'Restored the page'),
  target: (i) => ({ kind: 'task-files', id: i.task }),
});

/* Every version goes for good and cannot be brought back: the person's own press, after they confirm. */
export const removePage = defineAction({
  id: 'task.deletePage',
  title: 'Delete a page for good',
  kind: 'change',
  input: s.object({ task, page: fileId }),
  tico: false,
  run: (_ctx, i) => said(deletePage(i.task, i.page)),
  did: () => 'Deleted the page',
});
