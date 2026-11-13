/* ui/app/task-row.ts — a task row in every look (docs/pipelines-v3.md, S1), the mark, the Late tag, the one line, and
   the search and filter every task list shares.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A row is its title: mark, title, Late, anything after it, avatar, age. The words are on the task (taskLine), shown when
   you point at a row. */
'use strict';

/** working: a bot is at it (spinner). person: it needs a person (amber "!"). cant: it can't run (red dot). pick: it
    waits for a free bot of a stage's "One of several" (hollow ring; its label is the line, "Waiting for a free
    Reviewer"). '' is no mark: queued, checks running, waiting on a task or a full stage, snoozed, done or closed. */
type TaskMarkKind = 'working' | 'person' | 'cant' | 'pick' | '';
function taskMark(t: TaskAny | null | undefined): TaskMarkKind {
  if (!t || t.status !== 'open') return '';
  // A string: `pick` (a stage's "One of several" has nobody free) is newer than some readers of the wait's type.
  const state: string | undefined = t.turn?.state;
  if (state === 'person') return 'person';
  if (state === 'paused') return 'cant';
  if (state === 'pick') return 'pick';
  // The spinner only while a run is going (`turn.running`); a bot's turn between runs has no mark.
  if (state === 'bot' && (t.turn as {running?: boolean} | null)?.running) return 'working';
  return '';
}
const TASK_MARK_TIP: Record<string, string> = {working: 'Working', person: 'Needs a person', cant: "Can't run"};
/** The mark at the left of a row. `space` keeps an empty one the same width, so titles line up. */
function taskMarkHTML(t: TaskAny | null | undefined, {space = true}: {space?: boolean} = {}): string {
  const kind = taskMark(t);
  if (!kind) return space ? '<span class="tk-mark" aria-hidden="true"></span>' : '';
  const label = kind === 'pick' ? t?.turn?.line || 'Waiting for a free bot' : TASK_MARK_TIP[kind];
  return `<span class="tk-mark tk-mark-${kind}" role="img" aria-label="${esc(label)}">${kind === 'person' ? '!' : ''}</span>`;
}
const taskLate = (t: TaskAny | null | undefined) => !!t && t.status === 'open' && !!t.late;
const lateTagHTML = (t: TaskAny | null | undefined) => taskLate(t) ? '<span class="tk-late">Late</span>' : '';
/** One end of a dependency on a list row (backend/task_reads._dependencies): a task the reader may not open has no number. */
interface TaskDepRef { id?: string | null; number?: number | null }
/** The open tasks a list row depends on ("Blocked by #304"); [] for a full task or one that is not open. */
const taskBlockedBy = (t: TaskAny | null | undefined): TaskDepRef[] =>
  t && t.status === 'open' && 'blocked_by' in t ? t.blocked_by || [] : [];
/** The open tasks that depend on a list row ("Blocks #307"). */
const taskBlocks = (t: TaskAny | null | undefined): TaskDepRef[] =>
  t && t.status === 'open' && 'blocks' in t ? t.blocks || [] : [];
const taskDepRef = (r: TaskDepRef): string => r.number != null ? `#${r.number}` : '';
/** "Blocks #307", "Blocks #307, #308", and past two, or when one has no number, "Blocks 4 tasks". */
function taskBlocksText(rows: TaskDepRef[]): string {
  const refs = rows.map(taskDepRef);
  if (!rows.length) return '';
  return rows.length <= 2 && refs.every(Boolean) ? `Blocks ${refs.join(', ')}` : `Blocks ${rows.length} task${rows.length === 1 ? '' : 's'}`;
}
/** On a task others depend on, "Blocks #307" beside Late. */
function blocksTagHTML(t: TaskAny | null | undefined): string {
  const text = taskBlocksText(taskBlocks(t));
  return text ? `<span class="tk-blocks">${esc(text)}</span>` : '';
}
/** How long, short: since whose turn it is began, else since the task last changed. */
const taskAge = (t: {turn?: TaskWaitV3 | null; updated_at?: string | null; created_at?: string | null} | null | undefined) =>
  ageShort(t?.turn?.since || t?.updated_at || t?.created_at);
/** The one line: who has it and what is missing ("Builder working", "Waiting on Sam"). Empty for a finished task. */
function taskLine(t: TaskAny | null | undefined): string {
  if (!t) return '';
  if (t.status === 'done') return 'Done';
  if (t.status === 'closed') return 'Closed';
  return t.turn?.line || '';
}
/** Who has it now: the actor whose turn it is when that is someone, else the owner. */
const taskHolder = (t: TaskAny | null | undefined): string | null => {
  const who = t?.turn?.who;
  return who && /^(bot|human):/.test(who) ? who : t?.owner_id || null;
};
const shortDate = (iso: string | null | undefined) => {
  const at = Date.parse(iso || '');
  return at ? new Date(at).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) : '';
};

type TaskRowLook = 'list' | 'you' | 'archive';
interface TaskRowOptions {
  look?: TaskRowLook;
  /** In Tasks › You the row is the sentence asked, not the title. */
  text?: string;
  /** Buttons after the title (S1.8, S1.9). */
  after?: string;
  /** The id the row opens; the task's own by default. */
  key?: string;
  /** Drawn where the age goes (a blocked task's stop mark and "#304"). */
  tail?: string;
}
/** One row, one line (S1). A row in your own list has no mark and no mark space; snoozed and finished rows are dimmed,
    with a date and no avatar. */
function taskRowHTML(t: TaskAny, {look = 'list', text = '', after = '', key = '', tail = ''}: TaskRowOptions = {}): string {
  const id = esc(key || t.id), title = esc(text || t.title || '');
  const line = taskLine(t);
  const tip = esc([taskHolder(t) ? actorLabel(taskHolder(t)) : '', line].filter(Boolean).join(' · '));
  if (look === 'archive' || t.status !== 'open' || ('snoozed' in t && t.snoozed && look !== 'you')) {
    const when = t.status === 'open' ? shortDate(('snooze' in t ? t.snooze?.until : null) || t.turn?.until) : shortDate('updated_at' in t ? t.updated_at : null);
    return `<div class="tk-row tk-row-dim" data-task="${id}" role="button" tabindex="0">`
      + `<span class="tk-title">${title}</span><span class="tk-age">${esc(when)}</span></div>`;
  }
  const holder = taskHolder(t);
  return `<div class="tk-row" data-task="${id}" role="button" tabindex="0"${tip ? ` data-tip="${tip}"` : ''}>`
    + (look === 'you' ? '' : taskMarkHTML(t))
    + `<span class="tk-title">${title}</span>${lateTagHTML(t)}${blocksTagHTML(t)}`
    + (after ? `<span class="tk-after">${after}</span>` : '')
    + `<span class="tk-who">${holder ? actorFace(holder, 18) : ''}</span>`
    + (tail || `<span class="tk-age">${esc(taskAge(t))}</span>`) + '</div>';
}
/** A task in a column (S7; task #220): the title on up to two lines, then a meta line with who asked (face and name),
    the marks (Late, the mark), how long it has been in this stage and, when someone else owns it, the owner's face at
    the right. Pointing at the card shows the whole title and its line; at the owner's face, "Owner: <name>". */
function taskCardHTML(t: TaskAny, tail = ''): string {
  const title = t.title || '', line = taskLine(t);
  // Who asked, as the task page's "Asked by" names it: who really filed it (a schedule's person, BotOps, a built-in such
  // as Health check, which has no face) over the requester whose rights it carries.
  const asker = taskAsker(t) || null, owner = t.owner_id || null, builtin = !!taskFiledByBuiltin(t);
  const since = t.stage_since || ('created_at' in t ? t.created_at : null);
  const who = asker
    ? `<span class="tk-asker">${builtin ? '' : actorFace(asker, 16)}<span class="tk-name">${esc(taskAskerLabel(t))}</span></span>` : '';
  const ownerTip = owner && owner !== asker ? esc(`Owner: ${actorLabel(owner)}`) : '';
  return `<div class="tk-card" data-task="${esc(t.id)}" role="button" tabindex="0" title="${esc([title, line].filter(Boolean).join('\n'))}">`
    + `<span class="tk-title">${esc(title)}</span>`
    + `<span class="tk-meta">${who}<span class="tk-sp"></span>${lateTagHTML(t)}${blocksTagHTML(t)}${taskMarkHTML(t, {space: false})}`
    + (tail || `<span class="tk-age">${esc(ageShort(since))}</span>`)
    + (ownerTip ? `<span class="tk-owner" role="img" aria-label="${ownerTip}" title="${ownerTip}">${actorFace(owner, 16)}</span>` : '')
    + '</span></div>';
}

// ---- search and filter (S7): the same on every list, General included
interface TaskFilters {
  owner?: string[];
  waiting_on?: string[];
  stage?: string[];
  asked_by?: string[];
  tag?: string[];
  due?: 'overdue' | 'week' | 'any';
  late?: boolean;
  pushed?: boolean;
}
const TASK_FILTER_NAMES: [keyof TaskFilters, string][] = [
  ['owner', 'Owner'], ['waiting_on', 'Waiting on'], ['stage', 'Stage'], ['asked_by', 'Asked by'], ['tag', 'Tag'],
  ['due', 'Due'], ['late', 'Late'], ['pushed', 'Pushed past'],
];
/** Whether a task shows under the search words and the filters. "What can Sam do?" is Waiting on: Sam. */
function taskFilterMatch(t: TaskAny, q: string, f: TaskFilters = {}): boolean {
  const words = String(q || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length) {
    const hay = `${t.title || ''} ${'body' in t ? t.body || '' : ''} ${t.number ?? ''} ${'labels' in t ? (t.labels || []).join(' ') : ''}`.toLowerCase();
    if (!words.every(w => hay.includes(w.replace(/^#/, '')))) return false;
  }
  const any = (list: string[] | undefined, v: string | null | undefined) => !list?.length || (!!v && list.includes(v));
  if (!any(f.owner, t.owner_id)) return false;
  if (f.waiting_on?.length && !(t.status === 'open' && f.waiting_on.includes(String(t.turn?.who || '')))) return false;
  if (!any(f.stage, t.stage_id)) return false;
  if (!any(f.asked_by, 'requester_id' in t ? t.requester_id : null)) return false;
  if (f.tag?.length && !('labels' in t && f.tag.some(tag => (t.labels || []).includes(tag)))) return false;
  if (f.late && !taskLate(t)) return false;
  if (f.due) {
    const due = Date.parse(t.due_at || '');
    if (!due) return false;
    if (f.due === 'overdue' && due >= Date.now()) return false;
    if (f.due === 'week' && due > Date.now() + 7 * 86400_000) return false;
  }
  // Pushed past: a list row says so (`pushed_past`); a full task by its requirements.
  if (f.pushed && !((t as {pushed_past?: boolean}).pushed_past || 'to_leave' in t && (t.to_leave || []).some(r => /pushed past/i.test(r.standing)))) return false;
  return true;
}
/** How a pipeline is drawn on the Tasks page (task-pipelines.ts sets it as it loads). The page (tasks-page.ts) owns the
    header, reading the pipeline and its open tasks, search, filter, the archive and opening a task (any [data-task]);
    it calls `html` again whenever it has read again. `bind` wires the root once drawn, `rail` draws the manager's rail
    into its host, `ask` the phone's Ask bar into its own (pipeline-ask.ts), `edit` opens the pipeline's settings. */
interface PipelineViewHooks {
  html(p: PipelineRec, tasks: TaskAny[], opts: {layout: 'list' | 'columns'; phone: boolean; q: string; filters: TaskFilters}): string;
  bind?(root: HTMLElement, p: PipelineRec): void;
  rail?(host: HTMLElement, p: PipelineRec): void;
  ask?(host: HTMLElement, p: PipelineRec, phone: boolean): void;
  edit?(p: PipelineRec): void;
}
const TASK_PAGE_HOOKS: {pipeline?: PipelineViewHooks} = {};
const taskFiltersOn = (f: TaskFilters | null | undefined) =>
  !!f && Object.values(f).some(v => Array.isArray(v) ? v.length > 0 : !!v);
