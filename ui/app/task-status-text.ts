/* ui/app/task-status-text.ts — A task's status, due date and snooze in words, for its window.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/tasks.ts, whose other code Home runs, into the task page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

function clipLine(s: string | null | undefined, n: number) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
function taskStatusText(t: {status?: string | null; stage?: unknown}, label = taskStatusLabel(t)) {
  return `<span class="task-status" data-status="${esc(t.status || 'open')}" aria-label="${esc(label)}" title="${esc(label)}">${esc(label)}</span>`;
}
function taskDueInfo(t: (Partial<Pick<TaskRow, 'due_at' | 'occurrence' | 'status'>> & {owner_id?: string | null}) | null | undefined) {
  if (!t?.due_at || taskFinished(t)) return null;
  const at = parseServerTime(t.due_at); if (!at) return null;
  const shown = occurrenceDay(t) || new Date(at);
  const day = shortDay(shown);
  if (actorSlug(t.owner_id) && !t.occurrence) return {at, day, kind: 'wake', late: false, words: at > Date.now() ? `Wakes ${day}` : `Woke ${day}`};
  const late = at < Date.now();
  // Within the coming week a deadline reads as its weekday ("Due Wed"), further out as its date.
  const start = (d: Date | number) => new Date(d).setHours(0, 0, 0, 0), days = Math.round((start(shown) - start(Date.now())) / 864e5);
  const when = days === 0 ? 'today' : days === 1 ? 'tomorrow' : days > 1 && days < 7
    ? shown.toLocaleDateString(undefined, {weekday: 'short'}) : day;
  return {at, day, kind: 'deadline', late, soon: !late && at - Date.now() < 2 * 864e5, words: late ? `Overdue ${day}` : `Due ${when}`};
}
// Set aside until a date (backend/task_snooze.py): only the Snoozed view shows it until then. A task that depends on an
// open task is blocked, never Snoozed, even under a snooze left open (backend/task_depends.py): no chip, no Unsnooze.
function taskSnoozed(t: Pick<TaskV3, 'status' | 'snoozed' | 'snooze'> & PartialTaskRow | null | undefined) {
  if (!t || taskFinished(t)) return false;
  if (taskRels(t, 'depends_on', 'out').some(r => r.status === 'open')) return false;
  return !!t.snoozed || !!t.snooze;
}
function snoozeDay(t: Pick<TaskV3, 'snooze'> | null | undefined) {
  const until = parseServerTime(t?.snooze?.until);
  return until ? shortDay(new Date(until)) : '';
}
