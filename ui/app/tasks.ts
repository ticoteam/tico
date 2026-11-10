/* ui/app/tasks.ts — Tasks: columns, the status icon, rows and cards, the board, and the goal pickers
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
  ask?: {body?: string | null; recipient_id?: string | null; older?: boolean} | null;
  reconnect?: {person?: string | null} | null;
  rank?: number | null;
  done_at?: string | null;
  closed_at?: string | null;
  building_started_at?: string | null;
  building_seconds?: number | null;
}
// One relation of a task (the server's `relations`, by kind): `direction` is the task's own side, `out` or `in`.
type TaskRelRow = {direction: string; id: string; owner_id: string; status: string; title: string};
// One row of the list, the sort and the columns (taskItem).
interface TaskItem {
  kind: string; key: string; id: string; title: string; slug: string; actor: string; team: string; needs: boolean;
  rank: number | null; lane: string; labels: string[]; updated_at: string; closed: string; task: TaskRow; col: string;
  // A scheduled item's next run (kind `schedule`).
  schedule?: {next?: string | null};
}
// A goal as the Serves picker needs it (goalOptions).
type GoalOption = Pick<import('../types/api').Goal, 'id' | 'title' | 'owner_id' | 'parent_id' | 'rank' | 'status'>;
const STATUS_WORD: Record<string, string> = {open: 'Open', doing: 'Doing', waiting: 'Waiting', review: 'In review', ready: 'Ready to ship',
                     done: 'Done', closed: 'Closed', declined: 'Declined'};
const teamOf = (id: string) => botRow(id)?.team || '';
// A run of a schedule (a routine before it became an automation): not work a person asked for, so Done lists leave it out.
const isRecurringTask = (t: TaskRow) => !!t.routine_id || /from schedule/.test(String(t.body || ''));
const canMove = () => !!S.me?.mover;
type PartialTaskRow = Partial<TaskRow> & {owner?: string};   // a person's page passes only the owner to needsWho
const taskFinished = (t: Partial<Pick<TaskRow, 'status'>> | null | undefined) => ['done', 'closed'].includes(String(t?.status || ''));
function taskAskToPerson(t: PartialTaskRow | null | undefined) {
  return !!(t?.ask?.body && actorPerson(t.ask.recipient_id));
}
// A task's relations (the server's `relations`, by kind; each {id, title, status, owner, direction}). `out` is the
// task's own side: its parent, what it blocks, what it duplicates or follows up; `in` the other; related is `both`.
const taskRels = (t: PartialTaskRow | null | undefined, kind: string, direction?: string) => ((t?.relations || {})[kind] || []).filter(r => !direction || r.direction === direction);
const taskParent = (t: TaskRow | null | undefined) => taskRels(t, 'parent', 'out')[0] || null;
const taskParentId = (t: TaskRow | null | undefined): string => { const parent = taskParent(t); return parent?.id ? String(parent.id) : ''; };
const taskBlockers = (t: PartialTaskRow | null | undefined) => taskRels(t, 'blocks', 'in');
// The related count a board card shows: everything but its parent (subtasks have their own ring).
// What holds a task, by kind (docs/tasks.md, Holds): `blocked` (cause manual, dependency, gate…) or `snoozed`.
const taskHold = (t: PartialTaskRow | null | undefined, kind: string) => (t?.holds || []).find(h => h.kind === kind);
const taskBlockedHold = (t: PartialTaskRow | null | undefined) => taskHold(t, 'blocked');
function taskBlockedReason(t: TaskRow, source = document.activeElement as HTMLElement | null): Promise<string | null> {
  return new Promise(resolve => {
    const opener = source, dialog = document.createElement('dialog');
    dialog.className = 'tmodal task-block-reason';
    dialog.innerHTML = `<form class="tmodal-body"><h2>Block task</h2>
      <label>What is it waiting for?<textarea name="reason" required maxlength="1000" rows="3">${esc(taskBlockedHold(t)?.reason || '')}</textarea></label>
      <div class="row"><button type="button" data-cancel>Cancel</button><button class="primary" type="submit">Block</button></div></form>`;
    let value: string | null = null;
    const form = dialog.querySelector('form'), area = dialog.querySelector('textarea'), cancel = dialog.querySelector<HTMLButtonElement>('[data-cancel]');
    if (!form || !area || !cancel) throw new Error('Block task: the dialog did not render its form');
    form.onsubmit = event => {
      event.preventDefault();
      const reason = area.value.trim();
      if (!reason) return;
      value = reason; dialog.close();
    };
    cancel.onclick = () => dialog.close();
    dialog.addEventListener('close', () => { dialog.remove(); if (opener?.isConnected) opener.focus(); resolve(value); }, {once: true});
    document.body.append(dialog); dialog.showModal(); area.focus();
  });
}
// A waiting task held by a bot doing prerequisite work: a bot owns its first open blocker, the one `--on bot:<slug>`
// must name (backend/hubdb.py _waiting_bot). Its reason is the owner's note, else what the blocker is. A task only
// blocked, not set waiting, still reads "Blocked by". A task that also waits on a person (`waiting_on` on a person's
// task, an open question to them, or any open blocker they own, which is how the ask card a bot's task files for them
// is served) keeps their ask, so its note is never shown as a bot's reason. A reader who cannot open the first
// blocker is served only the ones after it, so a second bot's task there is named instead. That wait is the task's
// Blocked hold, so it carries the hold's age; a review-gate or worktree hold is something else and says its own why.
function taskBotWait(t: TaskRow) {
  const open = (r: TaskRelRow) => !['done', 'closed', 'declined'].includes(String(r.status || ''));
  if (t?.status !== 'waiting' || ['gate', 'worktree'].includes(String(taskBlockedHold(t)?.cause)) || actorPerson(t.waiting_on)
      || taskAskToPerson(t) || taskBlockers(t).some(r => open(r) && actorPerson(r.owner_id))) return null;
  const b = taskBlockers(t).find(open);
  if (!b || !actorSlug(b.owner_id)) return null;
  const note = String(t.note || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  const hold = taskBlockedHold(t);
  return {name: actorLabel(b.owner_id), reason: note || b.title || '', blocker: b,
    since: hold ? ` · since ${fmt(hold.since)}` : ''};
}
function taskWaitLine(t: TaskRow) {
  const bot = taskBotWait(t);
  if (bot) return `Waiting on ${bot.name}${bot.reason ? `: ${bot.reason}` : ''}${bot.since}`;
  if (taskBlockedHold(t)) return taskBlockedLine(t);
  const blocker = taskBlockers(t)[0];
  if (blocker?.title) return `Blocked by: ${blocker.title}`;
  const ask = String(t?.ask?.body || '').split('\n').map(s => s.trim()).find(Boolean);
  if (ask) return ask;
  return String(t?.note || '').split('\n').map(s => s.trim()).find(Boolean) || '';
}
function clipLine(s: string | null | undefined, n: number) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
// The person an open task waits on: you (myActor()), another person ('human:sam'), someone who cannot be told
// ('' — an unknown id, or a declined task with no person to send it back to), or null when it waits on no person.
function taskWaitsOn(t: PartialTaskRow | null | undefined) {
  if (!t || taskFinished(t)) return null;
  if (taskNeedsViewer(t)) return myActor();
  if (hubColumn(t) !== 'needs') return null;
  const pid = actorPerson(t.ask?.recipient_id) || actorPerson(t.owner_id) || actorPerson(taskWaitingOn(t))
    || (t.status === 'declined' ? actorPerson(taskRequester(t)) : '');
  return pid && (S.people || []).some(p => p.id === pid) ? 'human:' + pid : '';
}
// "Needs you", "Needs Sam" or "Needs someone": the approved words for who a task waits on
function needsWords(who: string | null | undefined) {
  if (who && who === myActor()) return 'Needs you';
  const pid = actorPerson(who);
  return pid ? `Needs ${personShortName(pid)}` : 'Needs someone';
}
// A person's first name, unless someone else here has it too: then as much of the last name as tells them apart
// ("Sam O.", "Sam Le." and "Sam Lo.", or the whole last name), else the address's local part ("sam.lee").
function personShortName(pid: string) {
  const name = personDisplay(pid), first = firstName(name);
  const others = (S.people || []).filter(p => p.id !== pid && firstName(p.name || titleCase(p.id)) === first);
  if (!others.length) return first;
  const lastOf = (n: string | null | undefined) => { const parts = String(n || '').trim().split(/\s+/); return parts.length > 1 ? parts.at(-1)! : ''; };
  const last = lastOf(name), theirs = others.map(p => lastOf(p.name).toLowerCase());
  for (let n = 1; last && n <= last.length; n++) {
    const pre = last.slice(0, n).toLowerCase();
    if (!theirs.some(o => o.slice(0, n) === pre)) return n === last.length ? `${first} ${last}` : `${first} ${last.slice(0, n)}.`;
  }
  const person = (S.people || []).find(p => p.id === pid);
  return String(person?.email || pid).split('@')[0]!;
}
const needsWho = (t: PartialTaskRow | null | undefined) => {
  const who = taskWaitsOn(t);
  if (who != null) return needsWords(who);
  const pid = actorPerson(t?.ask?.recipient_id) || actorPerson(t?.owner_id);   // a partial row (a person's page passes only the owner)
  return needsWords(pid ? 'human:' + pid : '');
};
// One shape for the list, the sort and the columns.
const taskItem = (t: TaskRow): TaskItem => {
  const slug = actorSlug(t.owner_id);
  return {kind: 'hub', key: `t${t.id}`, id: t.id, title: t.title || '', slug, actor: t.owner_id,
          team: slug ? teamOf(slug) : 'people', needs: !!actorPerson(t.owner_id),
          rank: t.rank == null ? null : Number(t.rank), lane: t.lane || 'company', labels: t.labels || [],
          updated_at: t.updated_at || t.created_at, closed: t.done_at || t.closed_at || '', task: t, col: hubColumn(t)};
};
const searchWords = (value: unknown) => String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
const searchIncludes = (fields: unknown[], query: string) => {
  const haystack = searchWords(fields.filter(Boolean).join(' '));
  return searchWords(query).split(' ').every(word => haystack.includes(word));
};
function taskNeedsViewer(t: PartialTaskRow | null | undefined) {
  const me = myActor();
  if (!me || !t || taskFinished(t)) return false;
  if (t.status === 'declined') return taskDeclinedToWho(t, me);
  return t.owner_id === me || (t.ask?.recipient_id === me && !t.ask?.older) || taskWaitingOn(t) === me;
}
// The person a bot's waiting task names (`hub task update --status waiting --on <person>`), or ''.
// Who a waiting task waits on: the person it names, or the one its reconnect card is for (backend/reconnects.py).
function taskWaitingOn(t: PartialTaskRow | null | undefined) {
  if (t?.status !== 'waiting') return '';
  return actorPerson(t.waiting_on) ? t.waiting_on : actorPerson(t.reconnect?.person) ? t.reconnect?.person : '';
}
// The name of a task's status: its step when its type has steps, else the status (Open, Doing, Waiting, In review…).
function taskStatusLabel(t: (PartialTaskRow & Pick<TaskRow, 'status'>) | null | undefined) {
  if (!t) return '';
  if (taskBlockedHold(t)) return 'Blocked';
  if (t.step?.name && pipelineTypeId(t) !== 'general') return t.step.name;
  return STATUS_WORD[t.status] || String(t.status || '');
}
function taskStatusText(t: PartialTaskRow & Pick<TaskRow, 'status'>, label = taskStatusLabel(t)) {
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
function taskAgeTitle(t: TaskRow | null | undefined) {
  return [t?.created_at ? `Open ${fmt(t.created_at)}` : '',
    t?.updated_at || t?.created_at ? `Updated ${fmt(t.updated_at || t.created_at)}` : ''].filter(Boolean).join(' · ');
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
function taskSnoozed(t: TaskRow | null | undefined) {
  const until = taskHold(t, 'snoozed')?.until;
  return !!until && !taskFinished(t) && parseServerTime(until) > Date.now();
}
function snoozeDay(t: TaskRow | null | undefined) {
  return shortDay(new Date(parseServerTime(taskHold(t, 'snoozed')?.until)));
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
// A ticket says how long it has been at its step only once it is past the stage's limit (pipelineLateClock). Time spent
// waiting is not counted: the server restarts the clock when it comes back (backend/step_age.py).
// Status belongs to the bot, not the ticket: its live changes must also reach an unchanged card or open page.
function taskBuilderActors(t: TaskRow | null | undefined): string[] {
  if (!t || taskFinished(t) || t.status === 'declined' || pipelineType(t)?.role !== 'engineering') return [];
  return [...new Set([t.owner_id, ...(t.roles?.builder || [])].map(actorSlug).filter(Boolean))];
}
function taskBuilderNotices(actors: string[], names = false) {
  return actors.length ? `<div class="task-builder-notices" data-task-builders="${esc(JSON.stringify(actors))}"${names ? ' data-builder-names' : ''}>${taskBuilderNoticesHTML(actors, names)}</div>` : '';
}
function taskBuilderNoticesRefresh() {
  for (const slot of document.querySelectorAll<HTMLElement>('[data-task-builders]')) {
    const html = taskBuilderNoticesHTML(JSON.parse(String(slot.dataset.taskBuilders)), slot.hasAttribute('data-builder-names'));
    if (slot.innerHTML !== html) slot.innerHTML = html;
  }
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
