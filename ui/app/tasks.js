/* ui/app/tasks.js — Tasks: columns, the status icon, rows and cards, the board, and the goal pickers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- tasks (#/tasks)
// One page for every piece of work the team owes: Tico's tasks, shown as **Needs you**, **Open** (a list),
// a **Board**, **Recurring** (the routines, one row each) and **Done**. `#/board`, `#/issues` and `#/recurring`
// open the matching view. No priority: every task has a rank in its owner's queue. Labels stand in for projects.
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
const BOARD_COLS = [...COMPANY_COLS, ['done', 'Done', 'Finished in the last 7 days']];   // legacy name, kept for the routine rows
// The view tabs: labelled, with counts. The keys are the routes' and the saved preference's.
const TASK_VIEWS = [['foryou', 'Needs you'], ['list', 'Open'], ['board', 'Board'], ['recurring', 'Recurring'], ['done', 'Done']];
const DONE_CAP = 20;                // The Done list shows 20 at a time
const ACTIVE_TASK_STATUSES = 'open,doing,waiting,review,ready,declined';
let TASKS_ST = null;
const teamOf = slug => S.emps.find(e => e.name === slug)?.team || '';
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
function taskWaitLine(t) {
  const blocker = taskBlockers(t)[0];
  if (blocker?.title) return `Blocked by: ${blocker.title}`;
  const ask = String(t?.ask?.body || '').split('\n').map(s => s.trim()).find(Boolean);
  if (ask) return ask;
  const note = String(t?.note || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  if (t?.status !== 'waiting' || !t.wait_escalated_to) return note;
  const why = t.waiting_on ? 'past its expected time' : 'names no one';
  return note ? `${note} · ${why}` : why;
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
  rank: null, updated: i.updatedAt, closed: i.state === 'CLOSED' ? i.closedAt : '', lane: 'company',
  issue: i});
const taskItem = t => {
  const slug = actorSlug(t.owner);
  return {kind: 'hub', key: `t${t.id}`, id: t.id, title: t.title || '', slug, actor: t.owner,
          team: slug ? teamOf(slug) : 'people', needs: !!actorPerson(t.owner),
          rank: t.rank == null ? null : Number(t.rank), lane: t.lane || 'company', labels: t.labels || [],
          updated: t.updated || t.created, closed: t.done_at || t.closed_at || '', task: t, col: hubColumn(t)};
};
// Rank is the order (no priority): ranked first, low to high, then the newest unranked.
const byRank = (a, b) => (a.rank == null ? 1 : 0) - (b.rank == null ? 1 : 0)
  || (a.rank ?? 0) - (b.rank ?? 0)
  || String(b.updated).localeCompare(String(a.updated));
const byNewest = (a, b) => String(b.closed || b.updated).localeCompare(String(a.closed || a.updated));
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
function taskNeedsViewer(t) {
  const me = myActor();
  return !!me && !!t && !taskFinished(t) && (t.owner === me || t.ask?.to_actor === me || taskWaitingOn(t) === me
    || (t.status === 'declined' && taskRequester(t) === me && !actorPerson(t.owner)));
}
// The person a bot's waiting task names (`hub task update --status waiting --on <person>`), or the
// person a silent or overdue wait was put in front of, or ''.
function taskWaitingOn(t) {
  if (t?.status !== 'waiting') return '';
  return actorPerson(t.waiting_on) ? t.waiting_on : actorPerson(t.wait_escalated_to) ? t.wait_escalated_to : '';
}
// The name of a task's status: its step when its type has steps, else the status (Open, Doing, Waiting, In review…).
function taskStatusLabel(t) {
  if (!t) return '';
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
  return `<span class="tl-sub${p.done === p.total ? ' all' : ''}" role="img" aria-label="${esc(words)}" title="${esc(words)}">${progressRing(p.done, p.total)}<span class="tnum">${p.done}/${p.total}</span></span>`;
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
// then: a parked task), shown as a muted "Wakes Oct 2". For a person's open task it is a deadline, red once passed.
function taskDueInfo(t) {
  if (!t?.due || taskFinished(t)) return null;
  const at = parseServerTime(t.due); if (!at) return null;
  const day = new Date(at).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  if (actorSlug(t.owner)) return {at, day, kind: 'wake', late: false, words: at > Date.now() ? `Wakes ${day}` : `Woke ${day}`};
  const late = at < Date.now();
  return {at, day, kind: 'deadline', late, words: `${late ? 'Overdue' : 'Due'} ${day}`};
}
function dueChip(t) {
  const due = taskDueInfo(t); if (!due) return '';
  return `<span class="tl-due${due.late ? ' late' : ''}${due.kind === 'wake' ? ' wake' : ''}" title="${esc(due.kind === 'wake' ? 'Looks again' : 'Due')} ${esc(fmt(new Date(due.at).toISOString()))}">${esc(due.words)}</span>`;
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
  return (opts.tags === false ? '' : rowTagChips(t, opts.maxTags)) + prStateBadge(taskPRState(t)) + subProgressChip(t) + dueChip(t);
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
// The note a row shows: what a waiting, blocked or asking task waits on. Amber only when it waits on you.
function taskRowNote(t) {
  if (!t || taskFinished(t) || (t.status !== 'waiting' && !taskBlockers(t).length && !taskAskToPerson(t) && t.status !== 'declined')) return null;
  const text = clipLine(plainMd(taskWaitLine(t)), 220);
  if (!text) return null;
  return {text, mine: taskNeedsViewer(t)};
}

// ---- the board: one column per state; an empty column folds to a thin strip with its name and count
// A small count of the card's related tasks, like an attachment count.
function taskRelChip(t) {
  const n = taskRelCount(t); if (!n) return '';
  const words = `${n} related task${n === 1 ? '' : 's'}`;
  return `<span class="bcard-rel tnum" role="img" aria-label="${esc(words)}" title="${esc(words)}"><svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M5 7l2-2M4.2 5.6L3 6.8a1.7 1.7 0 0 0 2.4 2.4L6.6 8M7.8 6.4L9 5.2A1.7 1.7 0 0 0 6.6 2.8L5.4 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>${n}</span>`;
}
// The same card the list's peek opens: a cover when the task has a picture, the title, its chips, a dot for an open
// question, the owner's face and the age.
function taskCard(it) {
  const t = it.task, st = TASKS_ST;
  const chips = t ? taskChipsHTML(t, {maxTags: 2}) : '';
  void st;   // selection, cursor and peek are painted on (task-list.js), so the card's signature only changes with its content
  return tasksSigned(`<div class="bcard" data-task-key="${esc(it.key)}">${t ? taskCoverHTML(t) : ''}
    <button class="bcard-open" type="button" data-open-task="${esc(it.key)}" title="${esc(t ? taskRowTip(t) : it.title)}" tabindex="-1">
      <span class="bcard-title">${esc(it.title)}</span></button>
    <div class="bcard-foot">${chips ? `<span class="bcard-chips">${chips}</span>` : ''}<span class="spacer"></span>${t ? taskRelChip(t) : ''}${t ? taskAskDot(t) : ''}
      <span class="tl-face" aria-hidden="true">${actorFace(it.actor, 16)}</span>
      <span class="age tnum" title="${esc(fmt(it.updated))}">${esc(ageShort(it.updated))}</span></div>
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
  return keys.map(key => ({key, items: (buckets.get(key) || []).sort(key === 'scheduled' ? (a, b) => String(a.updated).localeCompare(String(b.updated)) : byRank),
    label: key === 'needsme' ? 'Needs you' : key === 'needs-someone' ? 'Needs someone' : key.startsWith('needs:') ? needsWords(key.slice(6))
      : COMPANY_COLS.find(([k]) => k === key)?.[1] || key,
    kind: key === 'needsme' ? 'needsme' : key.startsWith('needs') ? 'needs' : key,
    owner: key === 'needsme' ? myActor() : key.startsWith('needs:') ? key.slice(6) : ''}));
}
// columns: [{id, name, hint, kind, items}] — readable columns share spare width; an empty one is a 34px strip.
function boardColumnsHTML(columns) {
  const anyWork = columns.some(c => c.items.length);
  const sizes = columns.map(c => anyWork && !c.items.length ? '34px' : 'minmax(260px,1fr)').join(' ');
  return `<div class="board work" style="--board-cols:${esc(sizes)}">${columns.map(column => {
    const empty = anyWork && !column.items.length;
    return `<section class="bcol${empty ? ' is-empty' : ''}" data-col="${esc(column.id)}" aria-label="${esc(column.name)}${column.hint ? `: ${esc(column.hint)}` : ''}">
      <header${column.hint ? ` title="${esc(column.hint)}"` : ''}><h2>${esc(column.name)}</h2><span class="cnt tnum">${column.items.length}</span></header>
      ${empty ? '' : `<div class="bcol-body">${column.items.map(taskCard).join('') || '<div class="empty">Nothing here</div>'}</div>`}
    </section>`;
  }).join('')}</div>`;
}
// Board: every open task in its column (the filters above still apply).
function tasksBoardHTML(items, state) {
  const pipeline = taskPipelineBoard(items, state);
  if (pipeline !== null) return pipeline;
  const hints = Object.fromEntries(COMPANY_COLS.map(([k, , hint]) => [k, hint]));
  return boardColumnsHTML(taskStatusGroups(items, {always: ['needsme', 'waiting', 'doing', 'scheduled']}).map(g =>
    ({id: g.key, name: g.label, hint: hints[g.key] || '', kind: g.kind, items: g.items})));
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
