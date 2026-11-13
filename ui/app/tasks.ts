/* ui/app/tasks.ts — what a task's window and its lists share: relations, due dates, snooze, faces and goals
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- the shapes this file owns: a task row as the page reads it, a list item, the page's state (TASKS_ST), and the status words.
// A task row: the server's Task (ui/types/api.d.ts) plus what the list, the board and the task's header add to it. PropTask
// is the task dialog's copy of the same row (task-props.ts), so a row here can be passed wherever a dialog reads one.
interface TaskRow extends PropTask {
  routine_id?: string | null;
  children_summary?: {done?: number; total?: number} | null;
  parts?: {done?: number; total?: number} | null;
  pr_state?: string | null;
  links?: {kind: string; state?: string | null; detail?: string | null}[];
  note?: string | null;
  rank?: number | null;
  done_at?: string | null;
  closed_at?: string | null;
  building_started_at?: string | null;
  building_seconds?: number | null;
}
// One relation of a task (the server's `relations`, by kind): `direction` is the task's own side, `out` or `in`.
// An `after` row also has the other task's number and stage (the order to merge in: backend/merge_order.py).
type TaskRelRow = {direction: string; id: string; owner_id: string; status: string; title: string; number?: number | null; stage?: string | null; since?: string};
// A goal as the Serves picker needs it (goalOptions).
type GoalOption = Pick<import('../types/api').Goal, 'id' | 'title' | 'owner_id' | 'parent_id' | 'rank' | 'status'>;
const teamOf = (id: string) => botRow(id)?.team || '';
const canMove = () => !!S.me?.mover;
type PartialTaskRow = Partial<TaskRow>;
const taskFinished = (t: Partial<Pick<TaskRow, 'status'>> | null | undefined) => ['done', 'closed'].includes(String(t?.status || ''));
// A task's relations (the server's `relations`, by kind; each {id, title, status, owner, direction}). `out` is the
// task's own side: its parent, what it blocks, what it duplicates or follows up; `in` the other; related is `both`.
const taskRels = (t: PartialTaskRow | null | undefined, kind: string, direction?: string) => ((t?.relations || {})[kind] || []).filter(r => !direction || r.direction === direction);
const taskParent = (t: TaskRow | null | undefined) => taskRels(t, 'parent', 'out')[0] || null;
function clipLine(s: string | null | undefined, n: number) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
const searchWords = (value: unknown) => String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
const searchIncludes = (fields: unknown[], query: string) => {
  const haystack = searchWords(fields.filter(Boolean).join(' '));
  return searchWords(query).split(' ').every(word => haystack.includes(word));
};
// The name of a task's state: its stage while it is open, else Done or Closed. A related row has only its status.
const TASK_STATE_WORDS: Record<string, string> = {open: 'Open', done: 'Done', closed: 'Closed'};
function taskStatusLabel(t: {status?: string | null; stage?: unknown} | null | undefined) {
  if (!t) return '';
  const stage = t.status === 'open' && t.stage && typeof t.stage === 'object' ? String((t.stage as {name?: unknown}).name || '') : '';
  return stage || TASK_STATE_WORDS[String(t.status)] || String(t.status || '');
}
function taskStatusText(t: {status?: string | null; stage?: unknown}, label = taskStatusLabel(t)) {
  return `<span class="task-status" data-status="${esc(t.status || 'open')}" aria-label="${esc(label)}" title="${esc(label)}">${esc(label)}</span>`;
}

// ---- the small things a row and a card carry
// Ages read short on a row: 5m, 3h, 2d, then the date.
function ageShort(iso: string | null | undefined) {
  const t = Date.parse(iso || '');
  if (!t) return '';
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d`;
  return new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}
// A time from the server. One without an offset is a legacy value the server reads as Pacific (backend/hubdb.py
// not_yet_due), so it is read the same way here: the day shown never slips by one.
function parseServerTime(value: string | null | undefined) {
  const s = String(value || '').trim();
  if (!s) return NaN;
  if (/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(s)) return Date.parse(s);
  const naive = Date.parse((s.length <= 10 ? s + 'T00:00:00' : s.replace(' ', 'T')) + 'Z');
  if (!naive) return NaN;
  let offset = -480;
  try {
    const name = new Intl.DateTimeFormat('en-US', {timeZone: 'America/Los_Angeles', timeZoneName: 'shortOffset'})
      .formatToParts(new Date(naive)).find(p => p.type === 'timeZoneName')?.value || '';
    const m = /GMT([+-]\d+)(?::(\d+))?/.exec(name);
    if (m) offset = Number(m[1]) * 60 + Math.sign(Number(m[1])) * Number(m[2] || 0);
  } catch {}
  return naive - offset * 60000;
}
// A task's due date is one of two things. For a bot's task it is when the bot looks again (the scheduler wakes it
// then: a parked task), shown as a muted "Wakes Oct 2". For a person's open task it is a deadline: amber within two
// days, red once passed. A task a due-date routine opened (`occurrence` set) is that day's deadline whoever owns it.
// An occurrence of a due routine (backend/automation_actions.py) is due at the last second of its due day in the
// trigger's zone, and that day is the routine's due_on: it reads as that calendar day in any viewer's zone. A step that
// set its own due keeps the instant, as any task does. Null for other tasks.
// "Oct 20", with the year only when it isn't this one ("Oct 20, 2028"), so a far-off date can't pass for a past one.
function shortDay(at: Date) {
  return at.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(at.getFullYear() === new Date().getFullYear() ? {} : {year: 'numeric'})});
}
function occurrenceDay(t: {occurrence?: string | null; due_at?: string | null} | null | undefined): Date | null {
  const m = /:(\d{4}-\d{2}-\d{2})$/.exec(t?.occurrence || ''), due = String(t?.due_at || '');
  if (!m || !due.startsWith(m[1] + 'T23:59:59')) return null;
  const [y, mo, d] = m[1]!.split('-').map(Number);
  return new Date(y!, mo! - 1, d!);
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
// Set aside until a date (backend/task_snooze.py): only the Snoozed view shows it until then.
function taskSnoozed(t: Pick<TaskV3, 'status' | 'snoozed' | 'snooze'> | null | undefined) {
  return !!t && !taskFinished(t) && (!!t.snoozed || !!t.snooze);
}
function snoozeDay(t: Pick<TaskV3, 'snooze'> | null | undefined) {
  const until = parseServerTime(t?.snooze?.until);
  return until ? shortDay(new Date(until)) : '';
}
// A face for an actor: a bot's avatar, a person's photo or initials.
function actorFace(actor: string | null | undefined, size = 18) {
  const slug = actorSlug(actor), pid = actorPerson(actor);
  if (slug) return avatar(slug, size, stateOf(slug));
  if (pid) {
    const person = pid === S.me?.id ? (mePerson() || {id: pid, name: S.me?.name || 'You'}) : (S.people || []).find(p => p.id === pid) || {id: pid, name: actorLabel(actor)};
    return personAvatar(person, size);
  }
  return personCircle(actorLabel(actor) || '?', size);
}
// The goal a task serves is a link with the goal's title; the row only carries the id.
const GOAL_TITLES: Record<string, string> = {};
async function taskGoalTitle(root: ParentNode) {
  const a = root.querySelector<HTMLAnchorElement>('a.task-goal[data-goal-title]'); if (!a) return;
  const id = String(a.dataset.goalTitle);
  if (!GOAL_TITLES[id]) { const r: {goal?: GoalOption | null} | null = await v2Get('/v2/goals/' + encodeURIComponent(id)); GOAL_TITLES[id] = r?.goal?.title || ''; }
  if (GOAL_TITLES[id] && a.isConnected) a.textContent = 'serves: ' + GOAL_TITLES[id];
}
// Live goals for the "Serves" pick on a new task; fetched once a page, never blocks the form.
let GOAL_OPTIONS: GoalOption[] | null = null;
async function goalOptions() {
  if (GOAL_OPTIONS) return GOAL_OPTIONS;
  const r: import('../types/api').GoalList | null = await v2Get('/v2/goals?all=1');
  GOAL_OPTIONS = (r?.goals || []).map(g => ({id: g.id, title: g.title, owner_id: g.owner_id, parent_id: g.parent_id, rank: g.rank, status: g.status}));
  return GOAL_OPTIONS;
}
