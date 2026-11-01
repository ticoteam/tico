/* ui/app/tasks.js — Tasks: columns, the status icon, rows and cards, the board, and the goal pickers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- tasks (#/tasks)
// One page for every piece of work the team owes: Tico's tasks, shown as **Needs you**, **Open** (a list),
// a **Board** and **Done**. `#/board` and `#/issues` open the matching view; scheduled work is Automations.
// No priority: every task has a rank in its owner's queue. Labels stand in for projects.
// A task's thread is comments, not chat: one flat list with the author on every line.
// The list (ui/app/task-list.js) draws one line per task; this file holds what the list, the board, the
// task's header and the subtask rows share, so a task reads the same wherever it shows.
const COMPANY_COLS = [
  ['needs',   'Needs a human', 'Waiting on a human, blocked, or declined back'],
  ['waiting', 'Waiting', 'Waiting on the dependency shown in the task'],
  ['doing',   'Doing',     'Starting or being worked on now'],
  ['scheduled', 'Scheduled', 'One-off tasks scheduled to start in the future'],
];
const STATUS_WORD = {open: 'Open', doing: 'Doing', waiting: 'Waiting', review: 'In review', ready: 'Ready to ship',
                     done: 'Done', closed: 'Closed', declined: 'Declined'};
// The view tabs: labelled, with counts. The keys are the routes' and the saved preference's.
const TASK_VIEWS = [['foryou', 'Needs you'], ['list', 'Open'], ['board', 'Board'], ['snoozed', 'Snoozed'], ['done', 'Done'], ['closed', 'Closed']];
// Done and Closed share one feed of finished tasks, newest first; each shows its own status.
const tasksFinishedView = view => view === 'done' || view === 'closed';
const DONE_CAP = 20;                // The Done list shows 20 at a time
const ACTIVE_TASK_STATUSES = 'open,doing,waiting,review,ready,declined';
let TASKS_ST = null;
const teamOf = slug => S.emps.find(e => e.name === slug)?.team || '';
// A run of a schedule (a routine before it became an automation): not work a person asked for, so Done lists leave it out.
const isRecurringTask = t => !!t.routine_id || /from schedule/.test(String(t.body || ''));
const isRecurringIssue = i => (i.labels || []).some(l => /^type:recurring$/i.test(l)) || /_Created by dispatcher from schedule/.test(i.body || '');
const hasFiles = i => /s3:\/\//.test(i.body || '');
const canMove = () => !!S.me?.mover;
const taskFinished = t => ['done', 'closed'].includes(String(t?.status || ''));
function taskAskToPerson(t) {
  return !!(t?.ask?.body && actorPerson(t.ask.to_actor));
}
// A task's relations (the server's `relations`, by kind; each {id, title, status, owner, direction}). `out` is the
// task's own side: its parent, what it blocks, what it duplicates or follows up; `in` the other; related is `both`.
const taskRels = (t, kind, direction) => ((t?.relations || {})[kind] || []).filter(r => !direction || r.direction === direction);
const taskParent = t => taskRels(t, 'parent', 'out')[0] || null;
const taskParentId = t => taskParent(t)?.id ? String(taskParent(t).id) : '';
const taskBlockers = t => taskRels(t, 'blocks', 'in');
// The related count a board card shows: everything but its parent (subtasks have their own ring).
const taskRelCount = t => Object.entries(t?.relations || {}).reduce((n, [kind, rows]) => n + (kind === 'parent' ? 0 : rows.length), 0);
function taskBlockedReason(t, source = document.activeElement) {
  return new Promise(resolve => {
    const opener = source, dialog = document.createElement('dialog');
    dialog.className = 'tmodal task-block-reason';
    dialog.innerHTML = `<form class="tmodal-body"><h2>Block task</h2>
      <label>What is it waiting for?<textarea name="reason" required maxlength="1000" rows="3">${esc(t.blocked_reason || '')}</textarea></label>
      <div class="row"><button type="button" data-cancel>Cancel</button><button class="primary" type="submit">Block</button></div></form>`;
    let value = null;
    dialog.querySelector('form').onsubmit = event => {
      event.preventDefault();
      const reason = dialog.querySelector('textarea').value.trim();
      if (!reason) return;
      value = reason; dialog.close();
    };
    dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { dialog.remove(); if (opener?.isConnected) opener.focus(); resolve(value); }, {once: true});
    document.body.append(dialog); dialog.showModal(); dialog.querySelector('textarea').focus();
  });
}
function taskBlockedLine(t) {
  const blocker = taskBlockers(t).find(b => !['done', 'closed', 'declined'].includes(b.status));
  const reason = t.blocked_reason === 'Waiting on a dependency' && blocker?.title
    ? `Waiting for ${blocker.title}` : t.blocked_reason || 'Waiting on a hold';
  return `${reason} · since ${fmt(t.blocked_since)}`;
}
// A waiting task held by a bot doing prerequisite work: a bot owns its first open blocker, the one `--on bot:<slug>`
// must name (backend/hubdb.py _waiting_bot). Its reason is the owner's note, else what the blocker is. A task only
// blocked, not set waiting, still reads "Blocked by". A task that also waits on a person (`waiting_on` on a person's
// task, an open question to them, or any open blocker they own, which is how the ask card a bot's task files for them
// is served) keeps their ask, so its note is never shown as a bot's reason. A reader who cannot open the first
// blocker is served only the ones after it, so a second bot's task there is named instead. That wait is the task's
// Blocked hold, so it carries the hold's age; a review-gate or worktree hold is something else and says its own why.
function taskBotWait(t) {
  const open = r => !['done', 'closed', 'declined'].includes(String(r.status || ''));
  if (t?.status !== 'waiting' || ['gate', 'worktree'].includes(t.blocked_kind) || actorPerson(t.waiting_on)
      || taskAskToPerson(t) || taskBlockers(t).some(r => open(r) && actorPerson(r.owner))) return null;
  const b = taskBlockers(t).find(open);
  if (!b || !actorSlug(b.owner)) return null;
  const note = String(t.note || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  return {name: actorLabel(b.owner), reason: note || b.title || '', blocker: b,
    since: t.blocked_since ? ` · since ${fmt(t.blocked_since)}` : ''};
}
function taskWaitLine(t) {
  const bot = taskBotWait(t);
  if (bot) return `Waiting on ${bot.name}${bot.reason ? `: ${bot.reason}` : ''}${bot.since}`;
  if (t?.blocked_since) return taskBlockedLine(t);
  const blocker = taskBlockers(t)[0];
  if (blocker?.title) return `Blocked by: ${blocker.title}`;
  const ask = String(t?.ask?.body || '').split('\n').map(s => s.trim()).find(Boolean);
  if (ask) return ask;
  return String(t?.note || '').split('\n').map(s => s.trim()).find(Boolean) || '';
}
function clipLine(s, n) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
// the column a task sits in
function hubColumn(t) {
  const status = String(t.status || 'open');
  if (status === 'done' || status === 'closed') return 'done';
  if (status === 'declined') return 'needs';
  if (actorPerson(t.owner) || taskAskToPerson(t) || taskWaitingOn(t)) return 'needs';
  if (status === 'waiting' || taskBlockers(t).length) return 'waiting';
  return 'doing';
}
// The person an open task waits on: you (myActor()), another person ('human:sam'), someone who cannot be told
// ('' — an unknown id, or a declined task with no person to send it back to), or null when it waits on no person.
function taskWaitsOn(t) {
  if (!t || taskFinished(t)) return null;
  if (taskNeedsViewer(t)) return myActor();
  if (hubColumn(t) !== 'needs') return null;
  const pid = actorPerson(t.ask?.to_actor) || actorPerson(t.owner) || actorPerson(taskWaitingOn(t))
    || (t.status === 'declined' ? actorPerson(taskRequester(t)) : '');
  return pid && (S.people || []).some(p => p.id === pid) ? 'human:' + pid : '';
}
// "Needs you", "Needs Sam" or "Needs someone": the approved words for who a task waits on
function needsWords(who) {
  if (who && who === myActor()) return 'Needs you';
  const pid = actorPerson(who);
  return pid ? `Needs ${personShortName(pid)}` : 'Needs someone';
}
// A person's first name, unless someone else here has it too: then as much of the last name as tells them apart
// ("Sam O.", "Sam Le." and "Sam Lo.", or the whole last name), else the address's local part ("sam.lee").
function personShortName(pid) {
  const name = personDisplay(pid), first = firstName(name);
  const others = (S.people || []).filter(p => p.id !== pid && firstName(p.name || titleCase(p.id)) === first);
  if (!others.length) return first;
  const lastOf = n => { const parts = String(n || '').trim().split(/\s+/); return parts.length > 1 ? parts.at(-1) : ''; };
  const last = lastOf(name), theirs = others.map(p => lastOf(p.name).toLowerCase());
  for (let n = 1; last && n <= last.length; n++) {
    const pre = last.slice(0, n).toLowerCase();
    if (!theirs.some(o => o.slice(0, n) === pre)) return n === last.length ? `${first} ${last}` : `${first} ${last.slice(0, n)}.`;
  }
  const person = (S.people || []).find(p => p.id === pid);
  return String(person?.email || pid).split('@')[0];
}
const needsWho = t => {
  const who = taskWaitsOn(t);
  if (who != null) return needsWords(who);
  const pid = actorPerson(t?.ask?.to_actor) || actorPerson(t?.owner);   // a partial row (a person's page passes only the owner)
  return needsWords(pid ? 'human:' + pid : '');
};
// One shape for the list, the sort and the columns.
const issueItem = i => ({kind: 'issue', key: `i${i.number}`, n: i.number, title: i.title || '',
  slug: i.owner, actor: `bot:${i.owner}`, team: teamOf(i.owner), needs: !!i.needs_human,
  rank: null, updated_at: i.updatedAt, closed: i.state === 'CLOSED' ? i.closedAt : '', lane: 'company',
  issue: i});
const taskItem = t => {
  const slug = actorSlug(t.owner);
  return {kind: 'hub', key: `t${t.id}`, id: t.id, title: t.title || '', slug, actor: t.owner,
          team: slug ? teamOf(slug) : 'people', needs: !!actorPerson(t.owner),
          rank: t.rank == null ? null : Number(t.rank), lane: t.lane || 'company', labels: t.labels || [],
          updated_at: t.updated_at || t.created_at, closed: t.done_at || t.closed_at || '', task: t, col: hubColumn(t)};
};
// Rank is the order (no priority): ranked first, low to high, then the newest unranked.
const byRank = (a, b) => (a.rank == null ? 1 : 0) - (b.rank == null ? 1 : 0)
  || (a.rank ?? 0) - (b.rank ?? 0)
  || String(b.updated_at).localeCompare(String(a.updated_at));
const byNewest = (a, b) => String(b.closed || b.updated_at).localeCompare(String(a.closed || a.updated_at));
const searchWords = value => String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
const searchIncludes = (fields, query) => {
  const haystack = searchWords(fields.filter(Boolean).join(' '));
  return searchWords(query).split(' ').every(word => haystack.includes(word));
};
function taskMatches(it, query) {
  const person = actorPerson(it.actor);
  return searchIncludes([it.title, ...(it.labels || []), actorLabel(it.actor), actorLabel(taskRequester(it.task)),
    person && personDisplay(person), it.slug && botDisplayName(it.slug), taskStatusLabel(it.task), taskWaitLine(it.task)], query);
}

// A task’s state is written in words; its owner and dependencies remain separate properties.
// A question the server marks `older` no longer counts: it waits under Older questions. Since questions unanswered for two
// business days go back to their bot instead (backend/still_needed.py), servers no longer mark any.
function taskNeedsViewer(t) {
  const me = myActor();
  return !!me && !!t && !taskFinished(t) && (t.owner === me || (t.ask?.to_actor === me && !t.ask?.older) || taskWaitingOn(t) === me
    || (t.status === 'declined' && taskRequester(t) === me && !actorPerson(t.owner)));
}
function taskOlderAsk(t) {
  const me = myActor();
  return !!me && !!t && !taskFinished(t) && t.ask?.to_actor === me && !!t.ask?.older && !taskNeedsViewer(t);
}
// The person a bot's waiting task names (`hub task update --status waiting --on <person>`), or ''.
// Who a waiting task waits on: the person it names, or the one its reconnect card is for (backend/reconnects.py).
function taskWaitingOn(t) {
  if (t?.status !== 'waiting') return '';
  return actorPerson(t.waiting_on) ? t.waiting_on : actorPerson(t.reconnect?.person) ? t.reconnect.person : '';
}
// The name of a task's status: its step when its type has steps, else the status (Open, Doing, Waiting, In review…).
function taskStatusLabel(t) {
  if (!t) return '';
  if (t.blocked_since) return 'Blocked';
  if (t.step?.name && pipelineTypeId(t) !== 'general') return t.step.name;
  return STATUS_WORD[t.status] || String(t.status || '');
}
function taskStatusText(t, label = taskStatusLabel(t)) {
  return `<span class="task-status" data-status="${esc(t.status || 'open')}" aria-label="${esc(label)}" title="${esc(label)}">${esc(label)}</span>`;
}

// ---- the small things a row and a card carry
// Ages read short on a row: 5m, 3h, 2d, then the date.
function ageShort(iso) {
  const t = Date.parse(iso || '');
  if (!t) return '';
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d`;
  return new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}
// A row reads one short age: how long since the task last moved ("2h"), so a stale one stands out without a second
// number on every row. The tooltip has when it was opened and last updated.
const taskAge = t => ageShort(t?.updated_at || t?.created_at);
function taskAgeTitle(t) {
  return [t?.created_at ? `Open ${fmt(t.created_at)}` : '',
    t?.updated_at || t?.created_at ? `Updated ${fmt(t.updated_at || t.created_at)}` : ''].filter(Boolean).join(' · ');
}
// Subtask progress: "1/3" beside a ring that fills as they finish (children_summary from the server, else parts).
function taskSubProgress(t) {
  const s = t?.children_summary;
  if (s?.total) return {done: Number(s.done) || 0, total: Number(s.total)};
  if (t?.parts?.total) return {done: Number(t.parts.done) || 0, total: Number(t.parts.total)};
  return null;
}
function progressRing(done, total) {
  const c = 2 * Math.PI * 4.6, f = total ? Math.min(1, done / total) : 0;
  return `<svg class="ring" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle class="ring-bg" cx="7" cy="7" r="4.6"/>${f ? `<circle class="ring-fg" cx="7" cy="7" r="4.6" stroke-dasharray="${(f * c).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 7 7)"/>` : ''}</svg>`;
}
function subProgressChip(t) {
  const p = taskSubProgress(t); if (!p) return '';
  const words = `${p.done} of ${p.total} subtask${p.total === 1 ? '' : 's'} done`;
  return `<span class="tl-sub${p.done === p.total ? ' all' : ''}${p.total <= 1 ? ' one' : ''}" role="img" aria-label="${esc(words)}" title="${esc(words)}">${progressRing(p.done, p.total)}<span class="tnum">${p.done}/${p.total}</span></span>`;
}
// A task's PR state: the server's pr_state (the worst across its PRs); an older server sends only the links.
function taskPRState(t) {
  if (t?.pr_state) return t.pr_state;
  const prs = (t?.links || []).filter(l => l.kind === 'pr');
  if (!prs.length) return '';
  return prs.every(l => ['merged', 'shipped'].includes(l.state)) ? 'merged' : prs.some(l => !['merged', 'shipped', 'closed'].includes(l.state)) ? 'open' : '';
}
const taskHasPR = t => !!taskPRState(t) || (t?.links || []).some(l => l.kind === 'pr');
// A time from the server. One without an offset is a legacy value the server reads as Pacific (backend/hubdb.py
// not_yet_due), so it is read the same way here: the day shown never slips by one.
function parseServerTime(value) {
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
function occurrenceDay(t) {
  const m = /:(\d{4}-\d{2}-\d{2})$/.exec(t?.occurrence || ''), due = String(t?.due || '');
  if (!m || !due.startsWith(m[1] + 'T23:59:59')) return null;
  const [y, mo, d] = m[1].split('-').map(Number);
  return new Date(y, mo - 1, d);
}
function taskDueInfo(t) {
  if (!t?.due || taskFinished(t)) return null;
  const at = parseServerTime(t.due); if (!at) return null;
  const shown = occurrenceDay(t) || new Date(at);
  const day = shown.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  if (actorSlug(t.owner) && !t.occurrence) return {at, day, kind: 'wake', late: false, words: at > Date.now() ? `Wakes ${day}` : `Woke ${day}`};
  const late = at < Date.now();
  // Within the coming week a deadline reads as its weekday ("Due Wed"), further out as its date.
  const start = d => new Date(d).setHours(0, 0, 0, 0), days = Math.round((start(shown) - start(Date.now())) / 864e5);
  const when = days === 0 ? 'today' : days === 1 ? 'tomorrow' : days > 1 && days < 7
    ? shown.toLocaleDateString(undefined, {weekday: 'short'}) : day;
  return {at, day, kind: 'deadline', late, soon: !late && at - Date.now() < 2 * 864e5, words: late ? `Overdue ${day}` : `Due ${when}`};
}
// Set aside until a date (backend/task_snooze.py): only the Snoozed view shows it until then.
function taskSnoozed(t) {
  return !!t?.snoozed_until && !taskFinished(t) && parseServerTime(t.snoozed_until) > Date.now();
}
function snoozeDay(t) {
  return new Date(parseServerTime(t.snoozed_until)).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}
function snoozeChip(t) {
  return taskSnoozed(t) ? `<span class="tl-due wake" title="Snoozed until ${esc(fmt(t.snoozed_until))}">Snoozed to ${esc(snoozeDay(t))}</span>` : '';
}
function dueChip(t) {
  const due = taskDueInfo(t); if (!due) return '';
  return `<span class="tl-due${due.late ? ' late' : due.soon ? ' soon' : ''}${due.kind === 'wake' ? ' wake' : ''}" title="${esc(due.kind === 'wake' ? 'Looks again' : 'Due')} ${esc(fmt(new Date(due.at).toISOString()))}">${esc(due.words)}</span>`;
}
// Tags on a row: two, then "+N" (the rest are in the tooltip).
function rowTagChips(t, max = 2) {
  const keys = t?.labels || [];
  if (!keys.length) return '';
  const shown = keys.slice(0, max).map(key => {
    const tag = (t.tags || []).find(x => x.key === key) || {key, label: key};
    return `<span class="tlabel tl-tag" style="--hue:${tagHue(key)}" title="${esc(key)}"><i aria-hidden="true"></i>${esc(tagSummary(tag))}</span>`;
  }).join('');
  const rest = keys.length - max;
  return shown + (rest > 0 ? `<span class="tlabel more" title="${esc(keys.slice(max).join(', '))}">+${rest}</span>` : '');
}
function taskChipsHTML(t, opts = {}) {
  return (opts.tags === false ? '' : rowTagChips(t, opts.maxTags)) + prStateBadge(taskPRState(t)) + taskVerdictHTML(t) + subProgressChip(t) + dueChip(t) + snoozeChip(t);
}
// A face for an actor: a bot's avatar, a person's photo or initials.
function actorFace(actor, size = 18) {
  const slug = actorSlug(actor), pid = actorPerson(actor);
  if (slug) return avatar(slug, size, stateOf(slug));
  if (pid) {
    const person = pid === S.me?.id ? (mePerson() || {id: pid, name: S.me?.name || 'You'}) : (S.people || []).find(p => p.id === pid) || {id: pid, name: actorLabel(actor)};
    return personAvatar(person, size);
  }
  return personCircle(actorLabel(actor) || '?', size);
}
// The row's tooltip: who added it, who has it, and the note in full.
function taskRowTip(t) {
  const lines = [t.title || ''];
  const meta = [taskStatusLabel(t), `Owner: ${actorLabel(t.owner)}`, taskSourceLine(t)];
  if (taskParent(t)?.title) meta.push(`Part of ${taskParent(t).title}`);
  lines.push(meta.filter(Boolean).join(' · '));
  const note = taskWaitLine(t);
  if (note) lines.push(plainMd(note));
  return lines.join('\n');
}
// Waiting is not a step: a waiting task stays in its step, and its card and row carry this tag. Amber, like every wait.
const taskWaitTag = t => t?.status === 'waiting' ? '<span class="tl-waiting" title="Waiting">Waiting</span>' : '';
// The note a row shows: what a waiting, blocked or asking task waits on. Amber only when it waits on you.
function taskRowNote(t) {
  if (!t || taskFinished(t) || (t.status !== 'waiting' && !taskBlockers(t).length && !taskAskToPerson(t) && t.status !== 'declined')) return null;
  const text = clipLine(plainMd(taskWaitLine(t)), 220);
  if (!text) return null;
  return {text, mine: taskNeedsViewer(t)};
}

// A refused move (a reviewer moving its ticket, a step the type refuses) says why on the row or card it was tried on,
// until that task changes or is tried again.
function taskRefusedSet(state, t, message) { (state.refused ||= new Map()).set(String(t.id), {message, version: Number(t.version)}); }
function taskRefused(state, t) {
  const r = t && state?.refused?.get(String(t.id));
  return r && r.version === Number(t.version) ? r.message : '';
}

// ---- the board: one column per state; an empty column folds to a thin strip with its name and count
// A small count of the card's related tasks, like an attachment count.
function taskRelChip(t) {
  const n = taskRelCount(t); if (!n) return '';
  const words = `${n} related task${n === 1 ? '' : 's'}`;
  return `<span class="bcard-rel tnum" role="img" aria-label="${esc(words)}" title="${esc(words)}"><svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M5 7l2-2M4.2 5.6L3 6.8a1.7 1.7 0 0 0 2.4 2.4L6.6 8M7.8 6.4L9 5.2A1.7 1.7 0 0 0 6.6 2.8L5.4 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>${n}</span>`;
}
// A Dev ticket at a working step says how long it has been there; past four hours it turns amber, when its manager is
// told (backend/step_age.py). Time spent waiting is not counted: the server restarts the clock when it comes back.
const STEP_LATE_MS = 4 * 3600e3;
// Status belongs to the bot, not the ticket: its live changes must also reach an unchanged card or open page.
function taskBuilderActors(t) {
  if (!t || taskFinished(t) || t.status === 'declined' || pipelineType(t)?.role !== 'engineering') return [];
  return [...new Set([t.owner, ...(t.roles?.builder || [])].map(actorSlug).filter(Boolean))];
}
function taskBuilderNoticesHTML(actors, names = false) {
  names ||= actors.length > 1;
  return actors.map(slug => {
    const status = v2StatusOf(slug);
    const text = status?.bot_state === 'quarantined' ? status.quarantine?.builder_notice : '';
    return text ? `<div class="task-builder-notice">${names ? `<strong>${esc(botDisplayName(slug))}</strong>: ` : ''}${esc(text)}</div>` : '';
  }).join('');
}
function taskBuilderNotices(actors, names = false) {
  return actors.length ? `<div class="task-builder-notices" data-task-builders="${esc(JSON.stringify(actors))}"${names ? ' data-builder-names' : ''}>${taskBuilderNoticesHTML(actors, names)}</div>` : '';
}
function taskBuilderNoticesRefresh() {
  for (const slot of document.querySelectorAll('[data-task-builders]')) {
    const html = taskBuilderNoticesHTML(JSON.parse(slot.dataset.taskBuilders), slot.hasAttribute('data-builder-names'));
    if (slot.innerHTML !== html) slot.innerHTML = html;
  }
}
function taskStepAge(t) {
  const since = Date.parse(t?.step_since || ''); if (!since) return '';
  const late = Date.now() - since >= STEP_LATE_MS, age = ageShort(t.step_since);
  const words = `At ${t.step?.name || 'this step'} for ${age === 'now' ? 'under a minute' : age}${late ? ': over 4 hours' : ''}`;
  return `<span class="bcard-step-age tnum${late ? ' late' : ''}" data-step-age title="${esc(words)}" aria-label="${esc(words)}">${esc(age)} in step</span>`;
}
// The same card the list's peek opens: a cover when the task has a picture, the title, its chips, a dot for an open
// question, the owner's face and the age.
function taskCard(it) {
  const t = it.task, st = TASKS_ST;
  const chips = t ? taskChipsHTML(t, {maxTags: 2}) : '', refused = taskRefused(st, t);
  void st;   // selection, cursor and peek are painted on (task-list.js), so the card's signature only changes with its content
  return tasksSigned(`<div class="bcard" data-task-key="${esc(it.key)}"${t?.status === 'done' ? ' data-done' : ''}>${t ? taskBlockedStrip(it) + taskCoverHTML(t) : ''}
    <button class="bcard-open" type="button" data-open-task="${esc(it.key)}" title="${esc(t ? taskRowTip(t) : it.title)}" tabindex="-1">
      <span class="bcard-title">${esc(it.title)}</span></button>
    ${taskBuilderNotices(taskBuilderActors(t))}
    ${t?.building_started_at ? `<div class="muted bcard-clock">Building ${esc(Math.floor(((t.building_seconds || 0) + Math.max(0, (Date.now() - Date.parse(t.building_started_at)) / 1000)) / 60))}m</div>` : ''}
    <div class="bcard-foot">${chips ? `<span class="bcard-chips">${chips}</span>` : ''}${t ? taskWaitTag(t) + taskStepAge(t) : ''}<span class="spacer"></span>${t ? taskRelChip(t) : ''}${t ? taskAskDot(t) : ''}
      ${t && taskPropRights(t).edit
        ? `<button type="button" class="tl-face bcard-owner" data-card-owner="${esc(it.key)}" aria-haspopup="true" aria-label="${esc(`Owner: ${actorLabel(it.actor)}. Change`)}" title="${esc(actorLabel(it.actor))}">${actorFace(it.actor, 16)}</button>`
        : `<span class="tl-face" aria-hidden="true">${actorFace(it.actor, 16)}</span>`}
      <span class="age tnum" title="${esc(fmt(it.updated_at))}">${esc(ageShort(it.updated_at))}</span></div>
    ${refused ? `<p class="bcard-err" role="alert" data-task-refused>${esc(refused)}</p>` : ''}
  </div>`);
}
// The status a task is grouped by: 'needsme', 'needs:human:sam', 'needs-someone', 'waiting', 'doing', 'scheduled'.
function taskStatusGroup(it) {
  const who = taskWaitsOn(it.task);
  if (who != null) return who === myActor() ? 'needsme' : who ? 'needs:' + who : 'needs-someone';
  return it.col === 'needs' ? 'needs-someone' : it.col;
}
// The status groups in order: Needs you, Needs <each person> (by name), Needs someone, Waiting, Doing, Scheduled.
function taskStatusGroups(items, {always = []} = {}) {
  const buckets = new Map();
  for (const it of items) {
    const key = taskStatusGroup(it);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(it);
  }
  const people = [...buckets.keys()].filter(k => k.startsWith('needs:'))
    .sort((a, b) => needsWords(a.slice(6)).localeCompare(needsWords(b.slice(6))));
  const keys = ['needsme', ...people, 'needs-someone', 'waiting', 'doing', 'scheduled'].filter(k => buckets.has(k) || always.includes(k));
  // Needs you follows the server's queue (what a person asked for first); the rest keep their rank.
  const needOrder = new Map((S.v2?.needs || []).map((item, index) => [String(item.id), index]));
  const byNeed = (a, b) => (needOrder.get(String(a.id)) ?? Infinity) - (needOrder.get(String(b.id)) ?? Infinity) || byRank(a, b);
  return keys.map(key => ({key, items: (buckets.get(key) || []).sort(key === 'scheduled' ? (a, b) => String(a.updated_at).localeCompare(String(b.updated_at))
    : key === 'needsme' ? byNeed : byRank),
    label: key === 'needsme' ? 'Needs you' : key === 'needs-someone' ? 'Needs someone' : key.startsWith('needs:') ? needsWords(key.slice(6))
      : COMPANY_COLS.find(([k]) => k === key)?.[1] || key,
    kind: key === 'needsme' ? 'needsme' : key.startsWith('needs') ? 'needs' : key,
    owner: key === 'needsme' ? myActor() : key.startsWith('needs:') ? key.slice(6) : ''}));
}
// columns: [{id, name, hint, kind, items, drop, fold, foldable}] — readable columns share spare width; an empty one is
// a 34px strip, and so is a folded run of backlog or finished steps (`fold`, ui/app/task-board.js). `drop` is the step
// a card dropped on the column moves to; a board with any is one cards are dragged on.
// The Dev tickets step a release ships from; its header gets Release now (settings-release.js fills it).
const RELEASE_COLUMN = /^pending release$/i;
function boardColumnsHTML(columns, {head = ''} = {}) {
  const anyWork = columns.some(c => c.items.length);
  const narrow = c => c.fold || anyWork && !c.items.length;
  const sizes = columns.map(c => narrow(c) ? '34px' : 'minmax(260px,1fr)').join(' ');
  const drag = columns.some(c => c.drop);
  return `${head}<div class="board work"${drag ? ' data-board-drag' : ''} style="--board-cols:${esc(sizes)}">${columns.map(column => {
    const empty = !column.fold && anyWork && !column.items.length;
    const count = column.fold ? column.count ?? '' : column.items.length;
    const drop = column.drop ? ` data-drop-step="${esc(column.drop)}"` : '';
    if (column.fold) return `<section class="bcol is-empty bfold" data-col="${esc(column.id)}"${drop} aria-label="${esc(column.name)}">
      <button type="button" class="bfold-open" data-board-unfold="${esc(column.fold)}" title="${esc(column.hint || 'Show')}" aria-label="${esc(`Show ${column.name}`)}"><h2>${esc(column.name)}</h2><span class="cnt tnum">${esc(String(count))}</span></button>
    </section>`;
    const fold = column.foldable ? `<button type="button" class="bfold-x" data-board-fold="${esc(column.foldable)}" aria-label="Fold" title="Fold">${TL_ICON.caret}</button>` : '';
    const scope = column.scope ? ` data-board-type="${esc(column.scope.type)}" data-board-step="${esc(column.scope.step)}"` : '';
    const all = column.items.length && tasksCanSelect()
      ? `<button type="button" class="bcol-all" data-board-select="${esc(column.id)}"${scope} aria-label="${esc(`Select all in ${column.name}`)}" title="Select all in this column">${TL_ICON.all}</button>` : '';
    return `<section class="bcol${empty ? ' is-empty' : ''}" data-col="${esc(column.id)}"${drop} aria-label="${esc(column.name)}${column.hint ? `: ${esc(column.hint)}` : ''}">
      <header${column.hint ? ` title="${esc(column.hint)}"` : ''}><h2>${esc(column.name)}</h2><span class="cnt tnum">${count}</span>${RELEASE_COLUMN.test(column.name) ? '<span class="board-release-slot" data-release-slot></span>' : ''}${all}${fold}</header>
      ${taskBuilderNotices([...new Set(column.items.flatMap(it => taskBuilderActors(it.task)))], true)}
      ${empty ? '' : `<div class="bcol-body">${column.items.map(taskCard).join('') || '<div class="empty">Nothing here</div>'}</div>`}
    </section>`;
  }).join('')}</div>`;
}
// Board: every open task in its column (the filters above still apply, and the blocked focus when one is on).
function tasksBoardHTML(items, state) {
  const head = taskBoardHead(items, state);
  items = taskBoardFocused(items, state);
  const pipeline = taskPipelineBoard(items, state, head);
  if (pipeline !== null) return pipeline;
  const hints = Object.fromEntries(COMPANY_COLS.map(([k, , hint]) => [k, hint]));
  return boardColumnsHTML(taskStatusGroups(items, {always: ['needsme', 'waiting', 'doing', 'scheduled']}).map(g =>
    ({id: g.key, name: g.label, hint: hints[g.key] || '', kind: g.kind, items: g.items})), {head});
}
// When a task was done, in the Done list's quiet right-hand column: "2h ago" today, then "Sep 26".
function doneWhen(when) {
  const t = Date.parse(when || '');
  if (!t) return '';
  return ageShort(when);
}
// The goal a task serves is a link with the goal's title; the row only carries the id.
const GOAL_TITLES = {};
async function taskGoalTitle(root) {
  const a = root.querySelector('a.task-goal[data-goal-title]'); if (!a) return;
  const id = a.dataset.goalTitle;
  if (!GOAL_TITLES[id]) { const r = await v2Get('/v2/goals/' + encodeURIComponent(id)); GOAL_TITLES[id] = r?.goal?.title || ''; }
  if (GOAL_TITLES[id] && a.isConnected) a.textContent = 'serves: ' + GOAL_TITLES[id];
}
// Live goals for the "Serves" pick on a new task; fetched once a page, never blocks the form.
let GOAL_OPTIONS = null;
async function goalOptions() {
  if (GOAL_OPTIONS) return GOAL_OPTIONS;
  const r = await v2Get('/v2/goals?all=1');
  GOAL_OPTIONS = (r?.goals || []).map(g => ({id: g.id, title: g.title, owner: g.owner, parent_id: g.parent_id, rank: g.rank, status: g.status}));
  return GOAL_OPTIONS;
}
