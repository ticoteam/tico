/* ui/app/task-row.ts — a task row in every look (docs/pipelines-v3.md, S1), the mark, the Late tag, the one line, and
   the search and filter every task list shares.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A row is its title: mark, title, Late, anything after it, avatar, age. The words are on the task (taskLine), shown when
   you point at a row. */
'use strict';

/** working: a bot is at it (spinner). person: it needs a person (amber "!"). cant: it can't run (red dot). '' is no
    mark: queued, checks running, waiting on a task or a full stage, snoozed, done or closed. */
type TaskMarkKind = 'working' | 'person' | 'cant' | '';
function taskMark(t: TaskAny | null | undefined): TaskMarkKind {
  if (!t || t.status !== 'open') return '';
  const state = t.turn?.state;
  if (state === 'person') return 'person';
  if (state === 'paused') return 'cant';
  // The spinner only while a run is going (`turn.running`); a bot's turn between runs has no mark.
  if (state === 'bot' && (t.turn as {running?: boolean} | null)?.running) return 'working';
  return '';
}
const TASK_MARK_TIP: Record<string, string> = {working: 'Working', person: 'Needs a person', cant: "Can't run"};
/** The mark at the left of a row. `space` keeps an empty one the same width, so titles line up. */
function taskMarkHTML(t: TaskAny | null | undefined, {space = true}: {space?: boolean} = {}): string {
  const kind = taskMark(t);
  if (!kind) return space ? '<span class="tk-mark" aria-hidden="true"></span>' : '';
  return `<span class="tk-mark tk-mark-${kind}" role="img" aria-label="${TASK_MARK_TIP[kind]}">${kind === 'person' ? '!' : ''}</span>`;
}
const taskLate = (t: TaskAny | null | undefined) => !!t && t.status === 'open' && !!t.late;
const lateTagHTML = (t: TaskAny | null | undefined) => taskLate(t) ? '<span class="tk-late">Late</span>' : '';
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
}
/** One row, one line (S1). A row in your own list has no mark and no mark space; snoozed and finished rows are dimmed,
    with a date and no avatar. */
function taskRowHTML(t: TaskAny, {look = 'list', text = '', after = '', key = ''}: TaskRowOptions = {}): string {
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
    + `<span class="tk-title">${title}</span>${lateTagHTML(t)}`
    + (after ? `<span class="tk-after">${after}</span>` : '')
    + `<span class="tk-who">${holder ? actorFace(holder, 18) : ''}</span>`
    + `<span class="tk-age">${esc(taskAge(t))}</span></div>`;
}
/** A task in a column (S7; task #220): the title on up to two lines, then a meta line with who asked (face and name),
    the marks (Late, the mark), how long it has been in this stage and, when someone else owns it, the owner's face at
    the right. Pointing at the card shows the whole title and its line; at the owner's face, "Owner: <name>". */
function taskCardHTML(t: TaskAny): string {
  const title = t.title || '', line = taskLine(t);
  const asker = t.requester_id || null, owner = t.owner_id || null;
  const since = t.stage_since || ('created_at' in t ? t.created_at : null);
  const who = asker
    ? `<span class="tk-asker">${actorFace(asker, 16)}<span class="tk-name">${esc(actorLabel(asker))}</span></span>` : '';
  const ownerTip = owner && owner !== asker ? esc(`Owner: ${actorLabel(owner)}`) : '';
  return `<div class="tk-card" data-task="${esc(t.id)}" role="button" tabindex="0" title="${esc([title, line].filter(Boolean).join('\n'))}"`
    + `${line ? ` data-tip="${esc(line)}"` : ''}>`
    + `<span class="tk-title">${esc(title)}</span>`
    + `<span class="tk-meta">${who}<span class="tk-sp"></span>${lateTagHTML(t)}${taskMarkHTML(t, {space: false})}`
    + `<span class="tk-age">${esc(ageShort(since))}</span>`
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
