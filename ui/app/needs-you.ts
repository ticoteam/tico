/* ui/app/needs-you.ts — Needs you outside its list: one item by id, Done's typed outcome, and the browser notification
   for a new ask while the page is in the background. The list is Tasks › You (tasks-page.ts), the ask screen decide.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** Needs you as the shared refresh holds it (S.v2.needs, read from GET /v2/needs-you: the new items). */
const needsAsEntries = (items: readonly NeedsItem[] | readonly NeedsEntry[] | null | undefined) => (items || []) as unknown as NeedsEntry[];
const needsHeld = () => needsAsEntries(S.v2?.needs);
/** The stage an item is in for a stage's review (the ▶ on a stage, decide.ts): its task's stage; a Paused cause is
    in none (backend/views.py needs_stage). */
const needsStageOf = (it: NeedsEntry): string => it.kind === 'paused' ? '' : it.task?.stage_id || '';
/** How many items wait on the person in each stage: the server's count over the whole list while only some pages are
    held (`stages`), else counted from the list. */
function needsStageCounts(): Map<string, number> {
  const whole = S.v2?.needsNext && S.v2.needsStages ? S.v2.needsStages : null;
  if (whole) return new Map(Object.entries(whole));
  const counts = new Map<string, number>();
  for (const it of needsHeld()) { const st = needsStageOf(it); if (st) counts.set(st, (counts.get(st) || 0) + 1); }
  return counts;
}
const needsStageCount = (stage: string): number => needsStageCounts().get(stage) || 0;
/** Every page of Needs you, read before a stage's review walks it (a stage's items can be past the first page). */
async function needsReadAll(): Promise<void> {
  // A page refused because a whole re-read started meanwhile (a live event) is read again from that re-read, twice at
  // most, so a walk does not start on part of the list.
  for (let i = 0, missed = 0; i < 40 && !needsAll(); i++) {
    if (await v2NeedsMore() || needsAll()) continue;
    if (++missed > 2 || !await v2NeedsRead()) break;
  }
}
/** One item of Needs you by id, or null. */
const needsItem = (id: unknown): NeedsEntry | null => needsHeld().find(it => String(it.id) === String(id)) || null;

// Finishing a bot's request is one tap: no prompt. What the person typed in the box they are looking at (the task
// window's comment box) goes with Done as the outcome; otherwise nothing extra is sent. Closing never takes one.
function taskOutcomeNote(task: {owner_id?: string | null | undefined; requester_id?: string | null | undefined} | null | undefined, action: {close?: boolean | undefined}, typed: string | null | undefined) {
  if (!task || action.close || !String(task.owner_id || '').startsWith('human:') ||
      !String(task.requester_id || '').startsWith('bot:')) return '';
  return String(typed || '').trim();
}
// The task window's typed outcome went with Done: it is not left behind to be sent again as a comment.
function taskOutcomeTaken(task: {id: string | number}) {
  TASK_DRAFTS.delete(String(task.id));
  const el = document.querySelector<HTMLTextAreaElement>(`dialog[data-task="${CSS.escape(String(task.id))}"] .task-comments + form textarea`);
  if (el) { el.value = ''; el.style.height = ''; }
}

// Browser notifications for a new ask, while the page is in the background, when the person turned them on (their
// Notifications card, person-page.ts). An @ is not notified (docs/pipelines-v3.md: no push). The ids already notified
// are kept per person in localStorage, so a reload does not repeat them; the first read after a fresh start records what
// is listed without notifying. Both reads of the list call this (hub-v2.ts, refresh.ts), one after another.
const THREAD_NEED_KINDS: Record<string, string> = {ask: 'Needs you'};
let notifyQueue = Promise.resolve();
function notifyNewThreadNeeds(items: NeedsItem[] | NeedsEntry[]) {
  notifyQueue = notifyQueue.then(() => notifyThreadNeedsOnce(needsAsEntries(items))).catch(() => {});
  return notifyQueue;
}
async function notifyThreadNeedsOnce(items: NeedsEntry[]) {
  const id = S.me?.id;
  if (!id) return;                  // the identity has not loaded: there is no person to keep a memory for yet
  if (S.notifyBrowser === undefined) {
    // The person's own row in the people list carries the setting (there is no one-person read).
    try { S.notifyBrowser = ((await v2Get('/humans'))?.people || []).find((p: {id?: string; notify_browser?: boolean}) => p.id === id)?.notify_browser === true; }
    catch { S.notifyBrowser = false; }
    pushKeep(S.notifyBrowser);         // this browser takes Web Push too (person-page.ts)
  }
  const key = 'tico.notified.' + id;
  const asks = items.filter(it => THREAD_NEED_KINDS[it.kind]);
  let seen: string[] | null = null;
  try { seen = JSON.parse(localStorage.getItem(key) || 'null'); } catch { seen = null; }
  const first = !Array.isArray(seen);
  const known = new Set(first ? [] : seen);
  const fresh = asks.filter(it => !known.has(String(it.id)));
  if (fresh.length) {
    const ids = [...known, ...fresh.map(it => String(it.id))].slice(-200);
    try { localStorage.setItem(key, JSON.stringify(ids)); } catch { /* private window: notify without memory */ }
  }
  if (first || !fresh.length || !S.notifyBrowser || !document.hidden || !('Notification' in window)
      || Notification.permission !== 'granted') return;
  for (const it of fresh) {
    try { new Notification(`${THREAD_NEED_KINDS[it.kind]} in Tico`, {body: it.line || '', tag: 'need-' + it.id}); }
    catch { /* a browser that refuses a notification shows nothing; the list still has it */ }
  }
}
