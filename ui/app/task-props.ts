/* ui/app/task-props.ts — A task's properties, Linear style: one click-to-edit row each (Stage, Owner, Asked by, Due,
   Tags, Part of), their menus and pickers, and the task's "…" menu (docs/pipelines-v3.md S4, S5).
   Used by the task's window (#task-modal); each change saves at once with the task's version.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The task as this file reads it: the server's Task, plus the fields a task's page adds on top of it.
interface PropTask extends TaskV3 {
  links?: {kind: string; detail?: string | null}[];
  followers?: PropFollower[] | null;
}
interface PropFollower { actor: string; notify: string; }
// A request body: the server's JSON for one change (a saved row sends a field, a reader or follower sends its own shape).
type PropPatch = Record<string, unknown>;
// A save takes the body to send, or a function of the newest copy of the task that builds it (null: nothing to send).
type PropBody = PropPatch | ((cur: PropTask) => PropPatch | null);
type TaskSave = (key: string, body: PropBody) => unknown;
type PropChange = (body: PropBody) => unknown;
// Which row's refused save says why (one reason per row, under the properties).
interface PropsErrors { id: string; map: Map<string, string>; }
// The task dialog (#task-modal) with the state the page keeps on it.
interface TaskDialog extends HTMLDialogElement {
  focusProp?: string;
  propsErrs?: PropsErrors | null;
  // An owner a stage rule refused (409 task_stage_rule): a person may give it all the same with Set anyway.
  propsAnyway?: {id: string; owner: string} | null;
  liveTask?: PropTask | null;
  taskLive?: TaskLive | null;
  saveQueue?: Promise<unknown>;
}
// The rights a signed-in person has on one task (taskPropRights).
interface PropRights { mover: boolean; party: boolean; live: boolean; edit: boolean; links: boolean; reopen: boolean; snooze: boolean; }
// The one menu every row opens: its own element, with the anchor and the Enter handler it was given.
interface PropPop extends HTMLDivElement { anchor?: HTMLElement | null; onEntry?: ((v: string) => void) | null; }
interface PropMenuItem {
  value?: string;
  text: string;
  html?: string;
  checked?: boolean;
  danger?: boolean;
  actor?: string;
}
interface PropMenuOptions<T extends PropMenuItem> {
  label: string;
  items?: T[];
  find?: string;
  entry?: string;
  none?: string;
  onPick?: (it: T) => unknown;
  onEntry?: (v: string) => unknown;
  /** The items for what is typed in the find box, from the server, in place of filtering the ones shown. */
  search?: (q: string) => Promise<T[]>;
}
// A "…" menu entry: a label and what it does.
interface PropMenuAction { text: string; danger?: boolean; run: () => unknown; }
interface PropRowOptions { edit?: boolean; empty?: boolean; popup?: string; words?: string; title?: string; extra?: string; }

const PROP_ICON = {
  due: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="2" y="3" width="10" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2 6h10M5 1.8v2.4M9 1.8v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  open: '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true" focusable="false"><path d="M5 2.5H2.5v7h7V7M7 2.5h2.5V5M9.5 2.5L5.5 6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  check: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 7.3l2.6 2.6L11 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  more: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3" cy="7" r="1.2" fill="currentColor"/><circle cx="7" cy="7" r="1.2" fill="currentColor"/><circle cx="11" cy="7" r="1.2" fill="currentColor"/></svg>',
};
// who may change what (the server holds the rules; this only hides controls that could never work)
function taskPropRights(t: PropTask): PropRights {
  const me = myActor(), mover = canMove();
  const party = !!me && [t.owner_id, t.requester_id, taskRequester(t)].includes(me);
  const live = !taskFinished(t);
  const onTask = !!me && Object.values<string[]>(t.roles || {}).some(actors => actors.includes(me));
  return {mover, party, live, edit: (mover || party) && live, links: mover && live, reopen: mover || party,
    snooze: live && (mover || party || onTask)};
}
// Snooze choices: the start of that day, local time (backend/task_snooze.py).
function snoozeItems(): {value: string; text: string; html: string}[] {
  const day = (n: number): Date => { const at = new Date(); at.setHours(0, 0, 0, 0); at.setDate(at.getDate() + n); return at; };
  const monday = (8 - new Date().getDay()) % 7 || 7;
  return ([['Tomorrow', day(1)], ['Next week', day(monday)], ['In two weeks', day(14)], ['In a month', day(30)]] satisfies [string, Date][]).map(([text, at]) =>
    ({value: at.toISOString(), text, html: `<span>${esc(text)}</span><span class="tl-mi-sub">${esc(at.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</span>`}));
}
function snoozeMenu(d: TaskDialog, anchor: HTMLElement, save: TaskSave): void {
  propMenu(d, anchor, {label: 'Snooze until', items: snoozeItems(), entry: 'A date, like 2026-11-02', none: 'Type a date, then Enter',
    onPick: it => void save('snooze', {snooze: it.value}),
    onEntry: v => {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim()), at = m && new Date(+m[1]!, +m[2]! - 1, +m[3]!);
      if (!at || at.getDate() !== +m![3]! || at <= new Date()) { toast('Pick a date in the future, like 2026-11-02', true); return; }
      void save('snooze', {snooze: at.toISOString()});
    }});
}
// The tasks whose Details the reader opened: kept across a redraw, which would shut it.
const TASK_DETAILS_OPEN = new Set<string>();
function taskDetailsOpen(d: HTMLElement): void { const el: HTMLDetailsElement | null = $('details.task-details', d); if (el) el.open = true; }
function taskPropsHTML(t: PropTask): string {
  const r = taskPropRights(t);
  const row = (key: string, label: string, value: string, o: PropRowOptions = {}): string => {
    const words = o.words || label;
    const v = o.edit
      ? `<button type="button" class="prop-v${o.empty ? ' empty' : ''}" data-prop="${key}" aria-haspopup="${o.popup || 'menu'}" aria-label="${esc(label)}: ${esc(words)}">${value}</button>`
      : `<span class="prop-v ro${o.empty ? ' empty' : ''}" aria-label="${esc(label)}: ${esc(words)}">${value}</span>`;
    return `<div class="prop" data-prop-row="${key}"><span class="prop-k" aria-hidden="true"${o.title ? ` title="${esc(o.title)}"` : ''}>${esc(label)}</span><span class="prop-vwrap">${v}${o.extra || ''}</span></div>`;
  };
  const txt = (s: string): string => `<span class="prop-txt">${esc(s)}</span>`;
  const rows: string[] = [];
  let stageRow = '';
  // Stage: the pipeline and the stage it is in (Done or Closed once finished); its menu moves it back or on.
  const stageName = taskStatusLabel(t), pipeName = t.pipeline?.name || '';
  stageRow = row('stage', 'Stage', txt(pipeName && t.status === 'open' ? `${pipeName} › ${stageName}` : stageName),
    {edit: r.edit && !!t.pipeline_id, words: stageName});
  // Owner and who asked. A private task's reassignment rule is the Owner label's tooltip, not a line on screen.
  rows.push(row('owner', 'Owner', actorFace(t.owner_id, 16) + txt(actorLabel(t.owner_id)), {edit: r.edit, words: actorLabel(t.owner_id),
    title: t.private ? 'Reassigning grants the new assignee access and removes the previous assignee’s access unless they requested the task.' : ''}));
  const asker = taskAsker(t), askerLabel = taskAskerLabel(t);
  rows.push(row('asker', 'Asked by', asker ? (taskFiledByBuiltin(t) ? '' : actorFace(asker, 16)) + txt(askerLabel) : txt('Unknown'), {words: askerLabel || 'Unknown', empty: !asker}));
  if (t.roles && Object.keys(t.roles).length) rows.push(taskPeopleRowHTML(t, r));
  // Visible to: Team, Public roadmap or Private · its people, one row (taskVisibleRowHTML). Size is not shown.
  rows.push(taskVisibleRowHTML(t, r));
  rows.push(taskFollowersRowHTML(t, r));
  // Due: a deadline (red once passed; taskDueInfo), or when a bot's parked task wakes (muted "Wakes Oct 2")
  const dueAt = parseServerTime(t.due_at);
  const info = taskDueInfo(t);
  const dueDayAt = occurrenceDay(t) || dueAt;   // an occurrence's calendar day is its routine's, not the viewer's zone
  const dueDay = dueAt ? new Date(dueDayAt).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '';
  const dueWords = !dueAt ? '' : info?.kind === 'wake' ? `${info.words.split(' ')[0]} ${dueDay}` : dueDay;
  const dueLate = !!info?.late;
  const dueISO = dueAt ? new Date(Number(dueDayAt) - new Date(dueDayAt).getTimezoneOffset() * 60000).toISOString().slice(0, 10) : '';
  rows.push(row('due', 'Due', `<span class="prop-ic${dueLate ? ' late' : ''}">${PROP_ICON.due}</span>` + (dueAt ? `<span class="prop-txt${dueLate ? ' late' : ''}${info?.kind === 'wake' ? ' wake' : ''}">${esc(dueWords)}</span>` : txt('None')),
    {edit: r.edit, empty: !dueAt, popup: 'dialog', words: dueWords || 'none',
      extra: (r.edit ? `<input type="date" class="prop-date" data-prop-date data-task-field="due" tabindex="-1" aria-hidden="true" value="${esc(dueISO)}">` : '')
        + (r.edit && dueAt ? `<button type="button" class="prop-x" data-prop-clear="due" aria-label="Clear the due date" title="Clear">${TL_ICON.x}</button>` : '')}));
  // Tags: chips (each opens its tag) and a small +
  const chips = tagChips(t.labels || [], t.tags || [], r.links, true);
  if (chips) rows.push(`<div class="prop" data-prop-row="tags"><span class="prop-k" aria-hidden="true">Tags</span><span class="prop-vwrap"><span class="prop-tags tlabels" aria-label="Tags">${chips
    || (r.links ? '' : '<span class="prop-v ro empty"><span class="prop-txt">None</span></span>')}${r.links
    ? (chips ? `<button type="button" class="prop-add" data-prop="tags" aria-haspopup="menu" aria-label="Add a tag" title="Add a tag">${TL_ICON.plus}</button>`
      : `<button type="button" class="prop-v empty" data-prop="tags" aria-haspopup="menu" aria-label="Tags: none, add a tag"><span class="prop-txt">Add tags</span></button>`) : ''}</span></span></div>`);
  // Part of: the parent's title is a link to it. A parent is fixed when the task is filed (backend/task_relations.py).
  // Other relations are in the Related section.
  const parent = taskParent(t);
  if (parent && !parent.id) {
    // A private parent this reader cannot open (a task filed from private work): named only as "a private task".
    rows.push(row('parent', 'Part of', txt(parent.title), {words: parent.title}));
  } else if (parent) {
    rows.push(`<div class="prop" data-prop-row="parent"><span class="prop-k" aria-hidden="true">Part of</span><span class="prop-vwrap">`
      + `<button type="button" class="prop-v prop-link" data-open-task="${esc(parent.id)}" aria-label="Part of: ${esc(parent.title)}. Open it"><span class="prop-txt">${esc(parent.title)}</span></button></span></div>`);
  }   // no parent: the row hides
  // Details: one line (owner · who sees it · tags) that opens in place to the rows. Stage stays above it. A phone's Info
  // tab is the whole window, so there the rows are open.
  const tagCount = (t.labels || []).length;
  const line = [actorLabel(t.owner_id), VISIBLE_WORDS[taskVisibleNow(t)], tagCount ? `${tagCount} tag${tagCount === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  const open = TASK_DETAILS_OPEN.has(String(t.id)) || matchMedia('(max-width:760px)').matches;
  return `<div class="props">${stageRow}</div><details class="task-details" data-task-details="${esc(String(t.id))}"${open ? ' open' : ''}><summary><span class="task-details-k">Details</span><span class="task-details-line">${esc(line)}</span><span class="task-details-chev" aria-hidden="true">›</span></summary><div class="props">${rows.join('')}</div></details><div class="props-msg" data-props-msg role="status" aria-live="polite"></div>`;
}
// A refused save's reason stays under the properties until that property saves (several: one line each, named).
const PROP_WORDS: Record<string, string> = {visible: 'Visible to', readers: 'Visible to', followers: 'Followers', stage: 'Stage', owner: 'Owner', due: 'Due', tags: 'Tags', parent: 'Part of', people: 'People', publication: 'Public', size: 'Size', snooze: 'Snooze'};

// Who is on a task by role (the server's `roles`): one chip per person and role, × takes one off, + adds one.
// Roles are names the team chooses; what they mean downstream is the client's.
const roleWord = (role: string): string => role === 'qa' ? 'QA' : String(role || '').replace(/[-_]/g, ' ').replace(/^\w/, c => c.toUpperCase());
function taskPeopleRowHTML(t: PropTask, r: PropRights): string {
  if (!t.roles) throw new Error('The task has no roles');
  const chips = Object.entries(t.roles).flatMap(([role, people]) => people.map(a => `<span class="prop-chip">${actorFace(a, 16)}<span class="prop-txt">${esc(actorLabel(a))} · ${esc(roleWord(role))}</span>${r.edit
    ? `<button type="button" class="prop-x" data-prop-clear-role="${esc(role)}" data-actor="${esc(a)}" aria-label="Take ${esc(actorLabel(a))} off as ${esc(roleWord(role))}" title="Take off">${TL_ICON.x}</button>` : ''}</span>`)).join('');
  const add = !r.edit ? (chips ? '' : '<span class="prop-v ro empty"><span class="prop-txt">None</span></span>')
    : chips ? `<button type="button" class="prop-add" data-prop="people" aria-haspopup="menu" aria-label="Add a person" title="Add">${TL_ICON.plus}</button>`
    : `<button type="button" class="prop-v empty" data-prop="people" aria-haspopup="menu" aria-label="People: none, add someone"><span class="prop-txt">Add</span></button>`;
  return `<div class="prop" data-prop-row="people"><span class="prop-k" aria-hidden="true">People</span><span class="prop-vwrap"><span class="prop-tags" aria-label="People">${chips}${add}</span></span></div>`;
}
// Visible to: one row with one menu (taskVisibleChoices) in place of the Private, Who can see and Public rows. It writes
// the fields they did: `private`, the readers (their own route) and `visibility`. A private task lists its requester and
// owner, then its readers: × takes a reader off and + adds one. "Public" only ever means the public roadmap.
type TaskVisible = 'team' | 'public' | 'private';
const VISIBLE_WORDS: Record<TaskVisible, string> = {team: 'Team', public: 'Public roadmap', private: 'Private'};
const taskVisibleNow = (t: PropTask): TaskVisible => t.private ? 'private' : t.visibility === 'public' && roadmapTask(t) ? 'public' : 'team';
// The states this reader may move the task to from where it is (the server holds the rules; hubdb.set_task_private).
function taskVisibleChoices(t: PropTask, r: PropRights): TaskVisible[] {
  const now = taskVisibleNow(t), unprivate = r.party && myActor() === t.requester_id;
  const may: Record<TaskVisible, boolean> = {
    team: now === 'private' ? unprivate : r.reopen,
    public: roadmapTask(t) && r.reopen && (now !== 'private' || unprivate),
    private: now !== 'private' && r.party,
  };
  return (Object.keys(may) as TaskVisible[]).filter(k => k !== now && may[k]);
}
function taskVisibleRowHTML(t: PropTask, r: PropRights): string {
  const now = taskVisibleNow(t), word = VISIBLE_WORDS[now], choices = taskVisibleChoices(t, r);
  const icon = `<span class="nav-icon prop-ic" aria-hidden="true">${now === 'private' ? 'lock' : now === 'public' ? 'travel_explore' : 'groups'}</span>`;
  const value = choices.length
    ? `<button type="button" class="prop-v" data-prop="visible" aria-haspopup="menu" aria-label="Visible to: ${esc(word)}">${icon}<span class="prop-txt">${esc(word)}</span></button>`
    : `<span class="prop-v ro" aria-label="Visible to: ${esc(word)}">${icon}<span class="prop-txt">${esc(word)}</span></span>`;
  let people = '';
  if (t.private) {
    const may = r.mover || r.party;
    const chip = (a: string, off: boolean): string => `<span class="prop-chip">${actorAvatar(a, 16)}<span class="prop-txt">${esc(actorName(a))}</span>${off
      ? `<button type="button" class="prop-x" data-prop-clear-reader="${esc(a)}" aria-label="Take ${esc(actorName(a))} off" title="Take off">${TL_ICON.x}</button>` : ''}</span>`;
    const parties = [...new Set([taskRequester(t) || t.requester_id, t.owner_id].filter((a): a is string => !!a))];
    const chips = parties.map(a => chip(a, false)).join('') + (t.readers || []).map(a => chip(a, may)).join('');
    const add = may ? `<button type="button" class="prop-add" data-prop="readers" aria-haspopup="menu" aria-label="Add who can see it" title="Add">${TL_ICON.plus}</button>` : '';
    people = `<span class="prop-sep" aria-hidden="true">·</span><span class="prop-tags" aria-label="Who can see it">${chips}${add}</span>`;
  }
  return `<div class="prop" data-prop-row="visible" data-visible="${now}"><span class="prop-k" aria-hidden="true">Visible to</span><span class="prop-vwrap">${value}${people}</span></div>`;
}
// Adds or takes off readers, then draws the task again (a reader is not a task field: it has its own route).
async function taskReaders(d: TaskDialog, task: PropTask, body: PropPatch): Promise<void> {
  d.focusProp = 'readers';
  try {
    await post(`/v2/tasks/${encodeURIComponent(task.id)}/readers`, body);
    if (d.propsErrs?.id === String(task.id)) d.propsErrs.map.delete('readers');
    const data = await get<{task?: PropTask}>(`/v2/tasks/${encodeURIComponent(task.id)}`);
    if (TASKS_ST) void tasksCatchUp(TASKS_ST);
    if (d.open && String(d.dataset.task) === String(task.id) && data.task) taskModalShow(data.task, d);
  } catch (e) {
    if (d.propsErrs?.id !== String(task.id)) d.propsErrs = {id: String(task.id), map: new Map()};
    d.propsErrs.map.set('readers', errorMessage(e, ''));
    taskPropsMsgPaint(d);
  }
}
// Followers: who follows the task, each at their level (backend/task_followers.py). Not a task field: the task's detail
// carries it (`followers`, null for a task in a shared chat, which has no row) and its own route changes it.
// Follow / Following (your level, Unfollow) for you; × and + for whoever may change the task.
const TASK_FOLLOWERS = new Map<string, PropFollower[] | null | undefined>();   // task id -> the followers last read
const FOLLOW_LEVELS: [string, string][] = [['live', 'Everything'], ['mentions', 'Mentions'], ['digest', 'Digest'], ['muted', 'Muted']];
const followLevelWord = (n: string): string => (FOLLOW_LEVELS.find(([k]) => k === n) || [])[1] || n;
function taskFollowersRowHTML(t: Pick<PropTask, 'id'> & {followers?: PropFollower[] | null}, r: PropRights): string {
  const id = String(t.id);
  if ('followers' in t) TASK_FOLLOWERS.set(id, t.followers);
  const list = TASK_FOLLOWERS.get(id);
  if (!Array.isArray(list)) return '';
  const may = r.mover || r.party, me = myActor(), mine = list.find(f => f.actor === me);
  const chip = (f: PropFollower): string => `<span class="prop-chip" data-follower="${esc(f.actor)}">${actorAvatar(f.actor, 16)}<span class="prop-txt">${esc(actorName(f.actor))}${f.notify === 'live' ? '' : ' · ' + esc(followLevelWord(f.notify))}</span>${may && f.actor !== me
    ? `<button type="button" class="prop-x" data-prop-unfollow="${esc(f.actor)}" aria-label="Take ${esc(actorName(f.actor))} off" title="Take off">${TL_ICON.x}</button>` : ''}</span>`;
  const own = mine
    ? `<button type="button" class="prop-v" data-prop="follow-me" aria-haspopup="menu" aria-label="Following: ${esc(followLevelWord(mine.notify))}"><span class="prop-txt">Following</span></button>`
    : '<button type="button" class="prop-v empty" data-prop-follow aria-label="Follow"><span class="prop-txt">Follow</span></button>';
  const add = may ? `<button type="button" class="prop-add" data-prop="followers" aria-haspopup="menu" aria-label="Add a follower" title="Add">${TL_ICON.plus}</button>` : '';
  return `<div class="prop" data-prop-row="followers"><span class="prop-k" aria-hidden="true">Followers</span><span class="prop-vwrap"><span class="prop-tags" aria-label="Followers">${list.map(chip).join('')}${own}${add}</span></span></div>`;
}
function taskFollowersPaint(d: TaskDialog, task: PropTask, list: PropFollower[] | null | undefined): void {
  TASK_FOLLOWERS.set(String(task.id), list);
  const row = $('[data-prop-row="followers"]', d) as HTMLElement | null;
  if (row && String(d.dataset.task) === String(task.id)) row.outerHTML = taskFollowersRowHTML({id: task.id}, taskPropRights(task)) || '<span hidden></span>';
  if (typeof taskFootRepaint === 'function') taskFootRepaint();
}
// One change ({actor?, following, notify?}), then the row from the server's answer.
async function taskFollow(d: TaskDialog, task: PropTask, body: PropPatch): Promise<void> {
  d.focusProp = 'followers';
  try {
    const data = await put(`/v2/tasks/${encodeURIComponent(task.id)}/followers`, body);
    if (d.propsErrs?.id === String(task.id)) d.propsErrs.map.delete('followers');
    taskFollowersPaint(d, task, data.followers);
    taskPropsMsgPaint(d);
  } catch (e) {
    if (d.propsErrs?.id !== String(task.id)) d.propsErrs = {id: String(task.id), map: new Map()};
    d.propsErrs.map.set('followers', errorMessage(e, ''));
    taskPropsMsgPaint(d);
  }
}
const propsMsgBox = (d: TaskDialog) => $('[data-props-msg]', d) as HTMLElement | null;
function taskPropsMsgPaint(d: TaskDialog): void {
  const box = propsMsgBox(d); if (!box) return;
  const errs: [string, string][] = d.propsErrs?.id === String(d.dataset.task) ? [...d.propsErrs.map] : [];
  box.textContent = errs.length === 1 ? errs[0]![1] : errs.map(([k, text]) => `${PROP_WORDS[k] || 'Task'}: ${text}`).join('\n');
  if (d.propsAnyway?.id !== String(d.dataset.task) || !errs.some(([k]) => k === 'owner')) return;
  const anyway = document.createElement('button');
  anyway.type = 'button'; anyway.className = 'linkish'; anyway.dataset.setAnyway = ''; anyway.textContent = 'Set anyway';
  box.append(' ', anyway);
}

// ---- one small menu for every row (and the "…" menu): arrow keys, Home/End, Enter, Esc; an optional find box
function propMenu<T extends PropMenuItem>(d: TaskDialog, anchor: HTMLElement, {label, items = [], find = '', entry = '', none = 'Nothing to pick', onPick, onEntry, search}: PropMenuOptions<T>): PropPop | undefined {
  let pop: PropPop | null = $('.prop-pop', d);
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (pop?.anchor && pop.anchor !== anchor) pop.anchor.setAttribute('aria-expanded', 'false');   // only one menu, only one expanded
  if (!pop) {
    const el = document.createElement('div') as PropPop;
    pop = el;
    el.className = 'tl-pop prop-pop'; el.popover = 'auto';
    el.addEventListener('toggle', ev => {
      if (ev.newState !== 'closed') return;
      const a = el.anchor;
      if (a?.isConnected && (el.contains(document.activeElement) || document.activeElement === document.body)) a.focus();
      el.dispatchEvent(new Event('menuclosed'));
    });
    el.addEventListener('keydown', ev => {
      const list = [...el.querySelectorAll<HTMLElement>('.tl-mi')].filter(b => !b.hidden);
      const input = $('.tl-menu-q', el) as HTMLInputElement | null;
      const at = list.indexOf(document.activeElement as HTMLElement);
      const go = (i: number): void => { ev.preventDefault(); ev.stopPropagation(); list[Math.max(0, Math.min(list.length - 1, i))]?.focus(); };
      if (ev.key === 'ArrowDown') return go(at < 0 ? 0 : at + 1);
      if (ev.key === 'ArrowUp') { if (at <= 0 && input) { ev.preventDefault(); ev.stopPropagation(); input.focus(); return; } return go(at - 1); }
      if (ev.key === 'Home' && at >= 0) return go(0);
      if (ev.key === 'End' && at >= 0) return go(list.length - 1);
      if (ev.key === 'Enter' && input && document.activeElement === input) {
        ev.preventDefault(); ev.stopPropagation();
        if (el.onEntry && input.value.trim()) el.onEntry(input.value.trim());
        else list[0]?.click();
      }
      if (['j', 'k', 'x', 'c', '/'].includes(ev.key)) ev.stopPropagation();   // the list's keys wait until the menu closes
    });
    d.append(el);
  }
  pop.anchor = anchor;
  pop.onEntry = onEntry ? v => { pop.hidePopover(); onEntry(v); } : null;
  // A menu with a find box is a group of plain buttons (a role=menu holds only menu items); either way ↑/↓ move.
  const menu = !(find || entry);
  const state = (it: PropMenuItem): string => it.checked == null ? '' : menu ? ` aria-checked="${it.checked ? 'true' : 'false'}"` : it.checked ? ' aria-current="true"' : '';
  const list = (rows: T[]) => `<div class="tl-menu-list">${rows.map((it, i) => `<button type="button"${menu ? ` role="${it.checked != null ? 'menuitemradio' : 'menuitem'}"` : ''}${state(it)} class="tl-mi${it.danger ? ' danger' : ''}" data-pick="${i}"${it.value != null ? ` data-prop-pick="${esc(it.value)}"` : ''} data-label="${esc(String(it.text || '').toLowerCase())}">${it.html || esc(it.text)}${it.checked ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>
    ${rows.length || !none ? '' : `<div class="tl-menu-none">${esc(none)}</div>`}`;
  pop.innerHTML = `<div class="tl-menu" role="${menu ? 'menu' : 'group'}" aria-label="${esc(label)}"><div class="tl-menu-h" aria-hidden="true">${esc(label)}</div>
    ${find || entry ? `<input type="${entry ? 'text' : 'search'}" class="tl-menu-q" placeholder="${esc(entry || find)}" aria-label="${esc(entry || find)}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-rows">${list(items)}</div></div>`;
  const input = $('.tl-menu-q', pop) as HTMLInputElement | null, first = items;
  let asked = 0, timer = 0;
  if (input) input.oninput = () => {
    const q = input.value.trim().toLowerCase();
    if (search) {
      // The server finds them (it holds more than the menu shows); the last thing typed wins.
      clearTimeout(timer);
      const n = ++asked, show = (rows: T[]) => { if (n !== asked || !pop.isConnected) return; items = rows; $('.tl-menu-rows', pop)!.innerHTML = list(rows); };
      if (!q) { show(first); return; }
      timer = window.setTimeout(() => void search(input.value.trim()).then(show, () => show([])), 200);
      return;
    }
    for (const b of pop.querySelectorAll<HTMLElement>('.tl-mi')) b.hidden = !!q && !b.dataset.label!.includes(q);
  };
  pop.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-pick]'); if (!b) return;
    const it = items[Number(b.dataset.pick)]!;
    pop.hidePopover();
    onPick?.(it);
  };
  pop.showPopover();
  if (!matchMedia('(max-width:760px)').matches) {
    const a = anchor.getBoundingClientRect();
    const below = a.bottom + 4 + pop.offsetHeight <= innerHeight - 8;
    pop.style.top = `${Math.max(8, below ? a.bottom + 4 : a.top - pop.offsetHeight - 4)}px`;
    pop.style.left = `${Math.max(8, Math.min(a.left, innerWidth - pop.offsetWidth - 8))}px`;
  } else pop.style.top = pop.style.left = '';
  (input || pop.querySelector<HTMLElement>('.tl-mi[aria-checked="true"], .tl-mi[aria-current="true"]') || pop.querySelector<HTMLElement>('.tl-mi'))?.focus();
  anchor.setAttribute('aria-expanded', 'true');
  pop.addEventListener('menuclosed', () => anchor.setAttribute('aria-expanded', 'false'), {once: true});
  return pop;
}
// A redraw replaces the dialog's content; one that lands while a menu is open waits for it to close.
function taskMenuSettled(d: TaskDialog) {
  const pop: PropPop | null = $('.prop-pop', d);
  if (!pop?.matches(':popover-open')) return Promise.resolve();
  return new Promise<Event>(resolve => pop.addEventListener('menuclosed', resolve, {once: true}));
}

// ---- the pickers
// Open tasks to pick from: the first page as a short-lived copy (a minute; a sent comment clears it). `more`: the server
// holds more open tasks than that page, so what is typed in the find box is searched there (GET /v2/tasks?q=).
type PropCandidate = Pick<TaskBriefV3, 'id' | 'title' | 'owner_id' | 'status'>;
let PROP_TASKS: {at: number; rows: PropCandidate[]; more: boolean} | null = null;
const PROP_TASKS_PAGE = 200;
async function taskPropCandidates(t: PropTask & {children?: unknown[]}, q = ''): Promise<{rows: PropCandidate[]; more: boolean}> {
  let read: {rows: PropCandidate[]; more: boolean};
  if (q) { const page = await taskBriefsPage({status: 'open', q, limit: PROP_TASKS_PAGE}); read = {rows: page.tasks, more: page.more}; }
  else {
    if (!PROP_TASKS || Date.now() - PROP_TASKS.at > 60000) {
      const page = await taskBriefsPage({status: 'open', limit: PROP_TASKS_PAGE});
      PROP_TASKS = {at: Date.now(), rows: page.tasks, more: page.more};
    }
    read = PROP_TASKS;
  }
  // Not the task itself, nor one of its own subtasks.
  const bar = new Set([String(t.id), ...(t.children || []).map(c => String((c as {id?: unknown}).id))]);
  return {rows: read.rows.filter(x => !bar.has(String(x.id))).sort((a, b) => String(a.title).localeCompare(String(b.title))), more: read.more};
}
// The stages a task may move back to: the earlier ones of its pipeline.
function taskEarlierStages(t: PropTask): {id: string; name: string}[] {
  const p = (TASK_PIPELINES || []).find(x => x.id === t.pipeline_id), here = t.stage_id;
  if (!p || !here) return [];
  const at = p.stages.findIndex(st => st.id === here);
  return p.stages.slice(0, Math.max(0, at)).map(st => ({id: String(st.id), name: st.name}));
}
// Wires the rows of a drawn task. `change(body)` saves (task-modal.ts taskModalBind) and redraws.
function taskPropsBind(d: TaskDialog, task: PropTask, change: PropChange): void {
  const box = $('[data-task-props]', d) as HTMLElement | null; if (!box) return;
  taskPropsMsgPaint(d);
  const details: HTMLDetailsElement | null = $('details.task-details', d);
  if (details) details.addEventListener('toggle', () => { TASK_DETAILS_OPEN[details.open ? 'add' : 'delete'](String(task.id)); });
  const msg = propsMsgBox(d);
  if (msg) msg.onclick = ev => {
    const anyway = d.propsAnyway;
    if (!anyway || !(ev.target as HTMLElement).closest('[data-set-anyway]')) return;
    d.propsAnyway = null; d.focusProp = 'owner';
    change({owner: anyway.owner, set_anyway: true});
  };
  const save: TaskSave = (key, body) => {
    d.focusProp = key;
    const done = change(body);
    // A new owner follows and the old one may not: read the list again once the owner is saved.
    if (key === 'owner') void Promise.resolve(done).then(() => get(`/v2/tasks/${encodeURIComponent(task.id)}/followers`))
      .then(data => { if (data) taskFollowersPaint(d, task, data.followers); }).catch(() => {});
    return done;
  };
  box.onclick = async ev => {
    if (eventElement(ev)?.closest('[data-tag-key],[data-drop-label],[data-open-task]')) return;   // a tag opens its page; × and ↗ are bound in taskModalBind
    const unread = eventElement(ev)?.closest<HTMLElement>('[data-prop-clear-reader]');
    if (unread) { void taskReaders(d, task, {remove: [unread.dataset.propClearReader]}); return; }
    const gone = eventElement(ev)?.closest<HTMLElement>('[data-prop-unfollow]');
    if (gone) { void taskFollow(d, task, {actor: gone.dataset.propUnfollow, following: false}); return; }
    if (eventElement(ev)?.closest('[data-prop-follow]')) { void taskFollow(d, task, {following: true}); return; }
    const off = eventElement(ev)?.closest<HTMLElement>('[data-prop-clear-role]');
    if (off) {
      const role = off.dataset.propClearRole ?? '', gone = off.dataset.actor;
      void save('people', cur => ({roles: {[role]: ((cur.roles || {})[role] || []).filter(a => a !== gone)}}));
      return;
    }
    const clear = eventElement(ev)?.closest<HTMLElement>('[data-prop-clear]');
    if (clear) {
      void save(clear.dataset.propClear ?? '', {due: ''});
      return;
    }
    const b = eventElement(ev)?.closest<HTMLElement>('[data-prop]'); if (!b) return;
    const key = b.dataset.prop;
    if (key === 'stage') {
      void taskStageMenu(d, b, task, save);
    } else if (key === 'people') {
      // First the role (one already on the task, or a new name), then the person or bot.
      const pick = (role: string): void => {
        const have = new Set((task.roles || {})[role] || []);
        const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll<HTMLOptionElement>('option[value]:not([value=""])');
        const items = [...opts].map(o => ({value: o.value, text: o.textContent ?? '', actor: o.value.includes(':') ? o.value : 'bot:' + o.value,
          html: `<span class="tl-opt-face">${actorFace(o.value.includes(':') ? o.value : 'bot:' + o.value, 16)}</span><span>${esc(o.textContent ?? '')}</span>`}))
          .filter(it => !have.has(it.actor));
        propMenu(d, b, {label: 'Add as ' + roleWord(role), find: 'Find a person or bot', items, none: 'Everyone is on it',
          onPick: it => void save('people', cur => ({roles: {[role]: [...((cur.roles || {})[role] || []), it.actor]}}))});
      };
      propMenu(d, b, {label: 'Role', entry: 'Role, like reviewer', none: 'Type a role, then Enter',
        items: Object.keys(task.roles || {}).map(k => ({value: k, text: k, html: `<span>${esc(roleWord(k))}</span>`})),
        onPick: it => setTimeout(() => pick(it.value)), onEntry: v => {
          const role = v.trim().toLowerCase().replace(/\s+/g, '-');
          if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(role)) { d.propsErrs = {id: String(task.id), map: new Map([['people', 'A role is a short name: letters, digits, - or _.']])}; taskPropsMsgPaint(d); return; }
          setTimeout(() => pick(role));
        }});
    } else if (key === 'visible') {
      const now = taskVisibleNow(task);
      const items = (['team', 'public', 'private'] as TaskVisible[]).filter(k => k === now || taskVisibleChoices(task, taskPropRights(task)).includes(k))
        .map(k => ({value: k, text: VISIBLE_WORDS[k], checked: k === now, html: `<span>${esc(VISIBLE_WORDS[k])}</span>`}));
      propMenu(d, b, {label: 'Visible to', items, onPick: it => {
        const to = it.value as TaskVisible;
        if (to === now) return;
        if (to === 'private') {
          // Private opens the people picker once it is saved.
          void Promise.resolve(save('visible', {private: true})).then(ok => {
            if (ok) setTimeout(() => ($('[data-prop="readers"]', d) as HTMLElement | null)?.click(), 0);
          });
        } else if (now === 'private') {
          void save('visible', {private: false});
          if (to === 'public') void save('visible', {visibility: 'public'});
        } else void save('visible', {visibility: to === 'public' ? 'public' : 'private'});
      }});
    } else if (key === 'readers') {
      const have = new Set([...(task.readers || []), task.owner_id, task.requester_id]);
      const items = threadAddable().filter(it => !have.has(it.actor)).map(it => ({value: it.actor, text: it.label ?? '',
        html: `<span class="tl-opt-face">${actorAvatar(it.actor, 16)}</span><span>${esc(it.label)}</span>`}));
      propMenu(d, b, {label: 'Who can see it', find: 'Find a person, bot or team', items, none: 'Nobody to add',
        onPick: it => void taskReaders(d, task, {add: [it.value]})});
    } else if (key === 'follow-me') {
      const now = (TASK_FOLLOWERS.get(String(task.id)) || []).find(f => f.actor === myActor())?.notify;
      propMenu(d, b, {label: 'Following', items: [...FOLLOW_LEVELS.map(([value, text]) => ({value, text, checked: now === value})),
        {value: '', text: 'Unfollow'}],
        onPick: it => void taskFollow(d, task, it.value ? {following: true, notify: it.value} : {following: false})});
    } else if (key === 'followers') {
      const have = new Set((TASK_FOLLOWERS.get(String(task.id)) || []).map(f => f.actor));
      // A private task: only who can see it (a group on its list may hold anyone, so then everyone is offered).
      const seen = task.private && !(task.readers || []).some(a => a.startsWith('group:'))
        ? new Set([...(task.readers || []), task.owner_id, task.requester_id, taskRequester(task)]) : null;
      const items = threadAddable({groups: false}).filter(it => !have.has(it.actor) && (!seen || seen.has(it.actor)))
        .map(it => ({value: it.actor, text: it.label ?? '', html: `<span class="tl-opt-face">${actorAvatar(it.actor, 16)}</span><span>${esc(it.label)}</span>`}));
      propMenu(d, b, {label: 'Add a follower', find: 'Find a person or bot', items, none: 'Nobody to add',
        onPick: it => void taskFollow(d, task, {actor: it.value, following: true})});
    } else if (key === 'owner') {
      const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll<HTMLOptionElement>('option[value]:not([value=""])');
      propMenu(d, b, {label: 'Owner', find: 'Find a person or bot', items: [...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return {value: o.value, text: o.textContent ?? '', html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent ?? '')}</span>`, checked: task.owner_id === actor, actor};
      }), onPick: it => { if (it.actor !== task.owner_id) void save('owner', {owner: it.actor}); }});
    } else if (key === 'due') {
      const input: HTMLInputElement | null = $('[data-prop-date]', d);
      d.focusProp = 'due';
      if (!input) throw new Error('The due date field is missing from the task dialog');
      try { input.showPicker(); } catch { input.removeAttribute('tabindex'); input.classList.add('shown'); input.focus(); }
    } else if (key === 'tags') {
      const have = new Set(task.labels || []);
      // built from the newest copy when it saves, so two quick tags both stay
      const add = (tag: string): void => { tag = String(tag).trim().toLowerCase(); if (tag && !have.has(tag)) void save('tags', cur => (cur.labels || []).includes(tag) ? null : {labels: [...(cur.labels || []), tag]}); };
      propMenu(d, b, {label: 'Add a tag', entry: 'Tag', none: 'Type a tag, then Enter', onEntry: add});
    }
  };
  // A date saves only when it is whole and sensible. With the native picker that is its change; typed by hand (the
  // fallback field), only on Enter or leaving the field, so a half-typed year like 0002 never saves.
  const date: HTMLInputElement | null = $('[data-prop-date]', d);
  if (date) date.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); date.dispatchEvent(new Event('change')); }
  };
  // the "…" menu in the header: Snooze, Move back, Move on, Close and the rest (S5)
  const more = $('[data-task-more]', d) as HTMLElement | null;
  if (more) more.onclick = () => {
    const r = taskPropRights(task), items: PropMenuAction[] = [];
    if (r.links && !$('[data-sub-add]', d)) items.push({text: 'Add subtask', run: () => taskRailAdd(d, task)});
    items.push({text: 'Copy link', run: async () => {
      try { await navigator.clipboard.writeText(`${location.origin}/#/task/${encodeURIComponent(task.id)}`); toast('Link copied'); } catch { toast('Could not copy the link', true); }
    }});
    // The owning bot's session id, for resuming or restoring that session by hand.
    const primary = ((d.liveTask || task).links || []).filter(l => l.kind === 'session').map(l => {
      try { return JSON.parse(l.detail || '{}'); } catch { return {}; }
    }).find(s => s.primary && s.thread_id);
    if (primary) items.push({text: 'Copy session ID', run: async () => {
      try { await navigator.clipboard.writeText(primary.thread_id); toast('Session ID copied'); } catch { toast('Could not copy the session ID', true); }
    }});
    // Links, Runs on, Sessions, Runs, Related, Meetings and Files (task-modal.ts taskActivityOpen).
    if ($('[data-task-activity]', d)) items.push({text: 'Activity and links', run: () => taskActivityOpen(d)});
    if (r.snooze) items.push(taskSnoozed(task) ? {text: 'Unsnooze', run: () => save('snooze', {unsnooze: true})}
      : {text: 'Snooze…', run: () => setTimeout(() => snoozeMenu(d, more, save))});
    if (r.edit && task.pipeline_id) {
      items.push({text: 'Move back…', run: () => setTimeout(() => void taskStageMenu(d, more, task, save, 'back'))});
      items.push({text: 'Move on', run: () => taskMoveOn(d, more, save)});
    }
    if (r.edit) items.push({text: 'Reassign…', run: () => setTimeout(() => { taskDetailsOpen(d); ($('[data-prop="owner"]', d) as HTMLElement | null)?.click(); })});
    // A reopen says why (docs/pipelines-v3.md C24): the menu takes the note, and Enter reopens it.
    if (!r.live && r.reopen) items.push({text: 'Reopen…', run: () => setTimeout(() => propMenu(d, more, {label: 'Reopen', entry: 'Why reopen it?',
      none: '', onEntry: v => void save('stage', {move: 'reopen', why: v})}))});
    if (task.status === 'open' && (r.mover || r.party)) items.push({text: 'Close', danger: true, run: () => save('stage', {close: true})});
    // Closing counts as finished work; a task made by mistake is deleted into the trash instead.
    if (r.mover || task.requester_id === myActor()) items.push({text: 'Delete task', danger: true, run: async () => {
      if (!(await askConfirm(`Delete "${task.title}"? It goes to the trash, from which it can be restored.`))) return;
      try { await post(`/v2/tasks/${encodeURIComponent(task.id)}/delete`, {}); } catch (e) { toast(errorCode(e) === 'has_work' ? "This task has work on it, such as a bot's turn, an answer, a bot's file or a subtask, so it can't be deleted. Close it instead." : errorMessage(e, ''), true); return; }
      d.close(); toast('Deleted');
      if (TASKS_ST) void tasksCatchUp(TASKS_ST);
    }});
    propMenu(d, more, {label: 'Task', items: items.map(it => ({...it, html: `<span>${esc(it.text)}</span>`})), onPick: it => it.run()});
  };
}
// The Stage row's menu: Move on, the earlier stages (Move back), Done; `only: 'back'` lists just the earlier ones.
async function taskStageMenu(d: TaskDialog, anchor: HTMLElement, task: PropTask, save: TaskSave, only = ''): Promise<void> {
  if (!TASK_PIPELINES) await tasksPipelinesRead().catch(() => []);
  if (!anchor.isConnected) return;
  const back = taskEarlierStages(task).map(st => ({value: st.id, text: st.name, html: `<span>${esc(st.name)}</span>`}));
  const items = only === 'back' ? back : [{value: 'next', text: 'Move on', html: '<span>Move on</span>'}, ...back,
    {value: 'done', text: 'Done', html: '<span>Done</span>'}];
  propMenu(d, anchor, {label: only === 'back' ? 'Move back to' : 'Move to', items, none: 'No earlier stage', onPick: it => {
    if (it.value === 'next') taskMoveOn(d, anchor, save);
    else void save('stage', {move: it.value});
  }});
}
// Move on: when the engine refuses for what is missing, the menu names it and takes a why to move on anyway.
function taskMoveOn(d: TaskDialog, anchor: HTMLElement, save: TaskSave): void {
  void Promise.resolve(save('stage', {move: 'next', quiet: true})).then(refused => {
    const why = refused && typeof refused === 'object' ? refused as TaskRefused : null;
    if (!why?.missing.length || !anchor.isConnected) return;
    setTimeout(() => propMenu(d, anchor, {label: `Missing: ${why.missing.join(', ')}`, entry: 'Why move on anyway', none: 'Say why, then Enter',
      onEntry: v => void save('stage', {move: 'next', why: v})}));
  });
}
