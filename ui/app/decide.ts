/* ui/app/decide.ts — Flow, the ask screen (docs/pipelines-v3.md, S3): one item of Needs you at a time, over the page. A
   modal on a wide screen, the whole screen on a phone. The top bar is where you are (All mine / a pipeline / a stage, a
   menu), Flow's mark, the mic, Tico's line, the next ask Tico is still thinking about, then ‹ › ✕ (‹ › on a wide screen
   only; Skip, in the answer row, steps on at every width). The thing asked about is on the left, shown in full (the page, the email, what an OK is for); the
   sentence asked, who asks, Notes and the buttons are on the right. A stage's typed answer asks its question: a choice is its buttons, a number a whole-number box
   with its range, text a box; a value the server refuses stays typed, under the server's line.
   An OK bound to what was shown: the content on screen is the preview GET /v2/oks/{id} (or the email's own preview)
   returned, held here and never read again behind the person's back. A letter shows its proof and a file its pinned
   version, read here before Approve is on; what does not load leaves Approve off. Approve sends the hash of exactly that preview. If
   the engine answers that it changed, the new content is read and shown with "It changed. Look again." and Approve
   needs a new click: it is never sent again by itself.
   A pipeline's or a stage's walk (Flow's mark on a stage or the Tasks header, the menu, #/decide?stage=<id>): the same
   screen, walking only what waits on the person there (needsStageOf); the end is "All done" wherever it is.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** What an item is on this screen. */
type AskKind = 'ok' | 'question' | 'yours' | 'stuck' | 'fix' | 'mention';
/** What an OK shows, as it was read: `hash` is the hash of exactly this content, the one Approve sends. `none` is an OK
    with no bound grant (a stage's OK of a page): Approve is a plain answer. `missing`: a part of what it shows did not
    load, so Approve stays off. `urls`: the object URLs of bytes read for it (a proof, a file), let go with it. `page`: on
    a page requirement, the page this content is (`<file id>@<version>`), the one an answer is for. `shows`: the refs it
    was read from. Held per ask (decideOkKey): a stage's requirement is the same on every task in the stage. */
interface AskOkView {
  state: 'loading' | 'ready' | 'none' | 'failed';
  hash: string | null;
  html: string;
  pages: import('../types/api').Page[];
  changed: boolean;
  error: string;
  missing: boolean;
  urls: string[];
  page: string;
  shows: string;
  /** A page that comes back: its rounds (GET .../asks/{rid}/rounds), null when there are none or they did not load
      (the right side then keeps the stage's line). `since`: the version the viewer last answered, the switch over the
      page, and its diff. */
  rounds: DecRound[] | null;
  since: DecSince | null;
  /** What the ask says besides the send back and Jev (new commits since an OK, a bot's read of them, a merge conflict),
      kept on the face when the rounds replace the ask's line. */
  notices: string[];
}
/** One version of a page on a page requirement, as backend/page_rounds.py gives it. */
interface DecRound {
  file_id: string;
  version: number;
  at: string;
  by: string;
  replaced: boolean;
  note: string;
  changed: string[];
  answered: {point: string; done: boolean; why: string}[];
  jev: {verdict: 'met' | 'not_met' | 'unsure' | 'failed' | null; reason: string};
  answers: {by: string; answer: 'ok' | 'no'; note: string; at: string}[];
}
/** "Changes since vK": K, whether the switch is on (on by default when the viewer sent vK back), the line diff from vK
    (the page viewer's own, tfDiffHTML), and how many blocks the page marks with the switch off. */
interface DecSince {
  version: number;
  on: boolean;
  same: boolean;
  diff: string;
  marked: number;
}
/** The open screen: the walk's order, the item on screen, what each OK showed, what was answered here. */
interface DecState {
  order: string[];
  at: string | null;
  items: Map<string, NeedsEntry>;
  ok: Map<string, AskOkView>;
  gone: Set<string>;
  more: '' | 'snooze' | 'give' | 'back';
  busy: boolean;
  error: string;
  barTimer: number;
  opener: HTMLElement | null;
  off: (() => void)[];
  /** Where the walk is: a stage ('' for none; DEC_OTHER, what is in no stage), else a pipeline ('' with no stage walks
      all of it). */
  stage: string;
  pipeline: string;
  /** Snoozed here this visit, for "All done · 2 snoozed". */
  snoozed: number;
  /** The items shown this visit: they keep their place when Tico's scores reorder the rest (decideScoreOrder). */
  seen: Set<string>;
  /** Screenshots and files added to the note of the item on screen (pasted, dropped or picked with 📎). */
  files: DecFile[];
  /** What was opened on the right side, per item: `<item id>:jev` (Jev's reason), `<item id>:more:<part>` (a part
      cut to 2 lines, shown in full). */
  open: Set<string>;
}
/** A file added to a note: uploaded to the task's Files at once; its name and id end the note that is sent. */
interface DecFile {
  key: number;
  file: File;
  state: 'up' | 'done' | 'failed';
  id: string;
  error: string;
  run: Promise<void> | null;
}

let DEC: DecState | null = null;            // the open screen, null while it is shut
let DECIDE_WANT = '';                       // a deep link's item (#/decide?item=<id>), opened once Tasks has read
let DECIDE_STAGE = '';                      // a deep link's stage review (#/decide?stage=<id>), opened once Tasks has read
const DEC_OTHER = '-';                      // the walk of what is in no stage (Tasks › You's Other chip)
const DEC_UNDO_MS = 10_000;
const DEC_CALM_MS = 400;
let DEC_CALM_TIMER = 0;
let DEC_ASK_NOTE = '';                      // the item whose empty Send back asked for a note: Notes keeps saying so
// The two answers Tico adds to every bot's question to a person (backend/answers.py DONT_KNOW, GIVE_BACK).
const DEC_DONT_KNOW = "I don't know, explain", DEC_GIVE_BACK = 'Give it back';
const DEC_FILES_MAX = 10;                   // files on one note
let DEC_FILE_KEY = 0;
let DEC_VOICE = false;                      // the mic is on: voice is not built, so the bar says it is coming
const DEC_START_MS = 600;                   // "Getting ready to flow" over the first item, which is usable under it
/** Tico's line on an item and the answer it would press (decideLineSet), by item id. */
const DEC_LINES = new Map<string, {line: string; pick: string}>();
const DEC_SNOOZE: [string, string][] = [['hour', '1 hour'], ['day', 'Tomorrow'], ['week', 'Next week']];
const DEC_MIC = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

// ---- which items, in which order
/** Needs you as the Tasks page read it, else as the shared refresh holds it. */
function decideSource(): NeedsEntry[] {
  const page = typeof TASKS_ST !== 'undefined' && TASKS_ST && !TASKS_ST.pipeline && !TASKS_ST.archive ? TASKS_ST.needs : null;
  return page?.length ? page : needsHeld();
}
/** The walk goes in the list's order: Answer, Review, Do, oldest first within each. */
function decideOrdered(items: NeedsEntry[]): NeedsEntry[] {
  const rank = {answer: 0, review: 1, do: 2};
  return items.map((it, i) => ({it, i})).sort((a, b) => rank[needsGroup(a.it)] - rank[needsGroup(b.it)] || a.i - b.i).map(x => x.it);
}
// The server sorts Home's Needs you rows by the same rule (backend/cards.py needs_act): a new kind here changes both.
function decideKind(it: NeedsEntry): AskKind {
  if (it.kind === 'ask') return it.ask?.kind === 'answer' ? 'question' : 'ok';
  if (it.kind === 'paused') return 'fix';
  if (it.kind === 'mention') return 'mention';
  // Stuck: the ladder brought it to the person (stuck or late with them), a bot is Paused on it, or it waits on someone
  // being let in (turn.share). Its buttons are the stuck card's, never Done, Snooze or Give to (task #392).
  const t = it.task;
  return t?.turn?.state === 'paused' || t?.late?.level === 'person' || t?.stuck || t?.turn?.share?.length ? 'stuck' : 'yours';
}
const decideRid = (it: NeedsEntry) => it.requirement_id || it.id;
const decideTaskId = (it: NeedsEntry) => it.task_id || it.task?.id || '';
/** One ask: its task and its requirement. A stage's requirement ("OK Chris") is the same on every task in the stage, so
    the requirement alone would show one task's page on another's ask. */
const decideOkKey = (it: NeedsEntry) => `${decideTaskId(it)}\n${decideRid(it)}`;
const decideShows = (it: NeedsEntry) => (it.ask?.shows || []).join('\n');
/** What is held for an ask was read from other refs than it has now (a new page version, another page). */
const decideOkStale = (v: AskOkView, it: NeedsEntry) => v.page !== decidePage(it) || v.shows !== decideShows(it);
const decideLive = (st: DecState) => st.order.filter(k => !st.gone.has(k) && st.items.has(k));
const decideCurrent = (st: DecState) => st.at && !st.gone.has(st.at) ? st.items.get(st.at) || null : null;
/** The items a walk takes: a stage's only that stage's, a pipeline's those in its stages. */
const decideInWalk = (st: DecState, it: NeedsEntry) => st.stage
  ? (st.stage === DEC_OTHER ? !decideStageName(needsStageOf(it)) : needsStageOf(it) === st.stage)
  : !st.pipeline || decidePipelineOf(needsStageOf(it))?.id === st.pipeline;
const decidePipelines = () => (typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || [];
const decidePipelineOf = (stage: string) => stage ? decidePipelines().find(p => p.stages.some(s => s.id === stage)) : undefined;
function decideSync(st: DecState, items: NeedsEntry[]) {
  const fresh = decideOrdered(items.filter(it => decideInWalk(st, it))), keys = new Set(fresh.map(it => it.id));
  const before = st.order, was = st.at ? st.items.get(st.at) : undefined;
  st.items = new Map(fresh.map(it => [it.id, it]));
  const order = before.filter(k => keys.has(k));
  fresh.forEach((it, i) => {
    if (order.includes(it.id)) return;
    const prev = fresh.slice(0, i).reverse().find(x => order.includes(x.id));
    order.splice(prev ? order.indexOf(prev.id) + 1 : 0, 0, it.id);
  });
  for (const k of [...st.gone]) if (!keys.has(k)) st.gone.delete(k);
  for (const k of [...st.seen]) if (!keys.has(k)) st.seen.delete(k);
  // The item on screen left the list (answered elsewhere): the same ask asked again (a letter quoted again) takes its
  // place, else the next one.
  if (st.at && !keys.has(st.at)) {
    const i = before.indexOf(st.at), shows = (it?: NeedsEntry) => (it?.ask?.shows || []).join('\n');
    const again = was?.kind === 'ask' ? fresh.find(it => it.kind === 'ask' && !before.includes(it.id) && !st.gone.has(it.id)
      && (it.task_id === was.task_id || !!shows(was) && shows(it) === shows(was))) : undefined;
    st.at = again?.id || before.slice(i + 1).find(k => keys.has(k) && !st.gone.has(k))
      || before.slice(0, Math.max(i, 0)).reverse().find(k => keys.has(k) && !st.gone.has(k)) || null;
  }
  st.order = order;
  decideScoreOrder(st);
}

// ---- the count, and the lists that show Needs you
function decideCount(): number {
  if (S.me?.role === 'bot') return 0;
  return S.v2?.needsCount ?? (S.v2?.needs || []).length;
}
/** Flow's mark beside Tasks while something needs the person; no number (the Tasks header has the one count). */
function needsBadgePaint() {
  const n = decideCount();
  for (const badge of document.querySelectorAll<HTMLElement>('[data-needs-badge]')) {
    badge.hidden = !n;
    if (!badge.firstElementChild) { badge.innerHTML = flowMarkHTML(14); badge.title = 'Flow'; }
  }
}
/** Needs you changed: the badge, the Tasks page and a bot's rail draw again. */
function needsListsRedraw() {
  needsBadgePaint();
  if (typeof TASKS_ST !== 'undefined' && TASKS_ST) tasksNeedsLive();
  if (typeof BOT !== 'undefined' && BOT && $('#t-needs') && typeof botNeedsPaint === 'function') botNeedsPaint(BOT.slug);
}

// ---- open and close
function decideDialog(): HTMLDialogElement {
  let d: HTMLDialogElement | null = $('#need-card');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'need-card'; d.className = 'dc-dialog'; d.setAttribute('aria-label', 'Flow');
  d.innerHTML = `<section class="decide">
    <header class="dc-head">
      <span class="dc-where"><button type="button" class="dc-crumb" data-dc="where" aria-haspopup="menu" aria-expanded="false"></button>
        <span class="dc-menu" role="menu" hidden></span></span>
      <span class="dc-flow">${flowMarkHTML(24)}</span>
      <button type="button" class="dc-wave" data-dc="voice" aria-label="Voice" aria-pressed="true" title="Voice" hidden><i></i><i></i><i></i><i></i><i></i></button>
      <button type="button" class="dc-mic" data-dc="voice" aria-label="Voice" aria-pressed="false" title="Voice">${DEC_MIC}</button>
      <p class="dc-say" aria-live="polite"></p>
      <span class="dc-up"></span>
      <span class="dc-nav">
        <button type="button" class="dc-icon dc-wide" data-dc="prev" aria-label="Previous" title="Previous (←)">‹</button>
        <button type="button" class="dc-icon dc-wide" data-dc="next" aria-label="Next" title="Next (→)">›</button>
        <button type="button" class="dc-icon" data-dc="close" aria-label="Close" title="Close (Esc)">✕</button>
      </span>
    </header>
    <div class="dc-main"><div class="dc-thing"></div><div class="dc-side"></div></div>
    <div class="dc-bar" role="status" hidden><span></span><button type="button" data-dc="undo">Undo</button></div>
  </section>`;
  const root: HTMLElement = $('.decide', d)!;
  root.addEventListener('click', (ev: MouseEvent) => { if (DEC) void decideClick(DEC, ev); });
  root.addEventListener('input', (ev: Event) => {
    const box = eventElement(ev) as HTMLTextAreaElement | HTMLInputElement | null;
    if (!DEC || !box?.matches('textarea, input')) return;
    if (box instanceof HTMLTextAreaElement && box.matches('[data-dc-grow]')) decideGrow(box);
    // The person's own typing in Notes: Tico's note never fills it after that (decidePaintSide).
    if (ev.isTrusted && box.id === 'dc-notes' && DEC.at) DEC_OP.typed.add(DEC.at);
    if (box.id === 'dc-sug-say') DEC_SUG.draft = box.value;
    decideNeedsText(root);
  });
  // A screenshot pasted into the note, or a file dropped on the answer side, joins the note (copied text pastes as text).
  pasteImages(root, files => { if (DEC && eventInNote(root)) decideFilesAdd(DEC, files); });
  const side = root.querySelector<HTMLElement>('.dc-side');
  if (side) fileDropTarget(side, files => { if (DEC && root.querySelector('[data-dc-attach]')) decideFilesAdd(DEC, files); });
  root.addEventListener('change', (ev: Event) => {
    const picked = eventElement(ev) as HTMLInputElement | null;
    if (DEC && picked?.matches('input[data-dc-pick]')) { decideFilesAdd(DEC, [...picked.files || []]); picked.value = ''; return; }
    decideNeedsText(root);
  });
  d.addEventListener('keydown', (ev: KeyboardEvent) => {
    // Enter in an answer's one-line box sends it.
    if (DEC && ev.key === 'Enter' && eventElement(ev)?.matches('input#dc-other')) {
      ev.preventDefault(); (d.querySelector<HTMLButtonElement>('[data-dc="other"]'))?.click(); return;
    }
    // Enter in a suggested rule's ✎ box sends it.
    if (DEC && ev.key === 'Enter' && eventElement(ev)?.matches('input#dc-sug-say')) {
      ev.preventDefault(); (d.querySelector<HTMLButtonElement>('[data-dc="sug-send"]'))?.click(); return;
    }
    if (!DEC || ev.metaKey || ev.ctrlKey || ev.altKey || eventElement(ev)?.closest('input, textarea, select')) return;
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); decideStep(DEC, -1); }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); decideStep(DEC, 1); }
  });
  // The pinned row's height changes with the width (a row that wraps) and the safe area.
  window.addEventListener('resize', () => { if (d?.open) decideBarRoom(); });
  // Esc with the menu open closes the menu only.
  d.addEventListener('cancel', ev => { ev.preventDefault(); if (!decideMenu(false)) decideStop(); });
  d.addEventListener('close', () => { if (!d.open) decideStop(); });
  document.body.appendChild(d);
  return d;
}
/** A button that needs words (Send back, Something else, Add rule) is on once there are some. */
function decideNeedsText(root: HTMLElement) {
  for (const b of root.querySelectorAll<HTMLButtonElement>('[data-dc-needs]')) {
    const box = root.querySelector<HTMLTextAreaElement | HTMLInputElement>(b.dataset.dcNeeds!);
    b.disabled = !box?.value.trim() || !!DEC?.busy;
  }
}
/** Opens the item `id`, or the first of the list with none. `items` is the list to walk, Needs you as the page shows
    it by default; `stage` walks only that stage's items, `pipeline` only its stages' items, and an open screen walking
    another switches to it. False when there is no such item. */
function decideOpen(id: string = '', items: NeedsEntry[] = decideSource(), stage: string = '', pipeline: string = ''): boolean {
  if (S.me?.role === 'bot') return false;
  const fresh = !DEC;
  const st: DecState = DEC || {order: [], at: null, items: new Map(), ok: new Map(), gone: new Set(), more: '', busy: false, error: '',
    barTimer: 0, opener: document.activeElement as HTMLElement | null, off: [], stage, pipeline, snoozed: 0, files: [], open: new Set(),
    seen: new Set()};
  const to0 = (s: string, p: string) => { st.stage = s; st.pipeline = p; st.order = []; st.gone.clear(); st.at = null; };
  // Opening one item from a list walks the list it is in; Flow on a stage or a pipeline walks it, from its first item.
  if ((st.stage !== stage || st.pipeline !== pipeline) && (stage || pipeline || !id)) to0(stage, pipeline);
  decideSync(st, items);
  if (id && (st.stage || st.pipeline) && !st.items.has(id)) { to0('', ''); decideSync(st, items); }
  const live = decideLive(st), to = id ? (live.includes(id) ? id : null) : live[0] || null;
  if (!to) return false;
  if (fresh) DEC = st;
  const d = decideDialog();
  decideGo(st, to);
  if (!d.open) { d.showModal(); decideStart(d); }
  decideOpinions(st);
  return true;
}
/** "Getting ready to flow" with the mark, for well under a second over the first item, which is drawn and takes input
    under it (decide.css .dc-start). None with reduced motion. */
function decideStart(d: HTMLDialogElement) {
  if (ldotCalm()) return;
  d.querySelector('.dc-start')?.remove();
  const root = $('.decide', d);
  root?.insertAdjacentHTML('beforeend', `<div class="dc-start" aria-hidden="true">${flowMarkHTML(56, true)}<p>Getting ready to flow</p></div>`);
  const cover = root?.lastElementChild;
  window.setTimeout(() => cover?.remove(), DEC_START_MS);
}
/** ▶: the walk from its first item, or, after Go to, from the item it left (decide-about.ts). */
const decideGoThrough = () => { const at = DEC_RESUME; DEC_RESUME = ''; return (!!at && decideOpen(at)) || decideOpen(''); };
/** A task page's Answer: the Needs you item that is this task's ask for requirement `rid`. Items are keyed by their own
    id (`<ask id>:<requirement id>`) and a stage's requirement is the same on every task in the stage, so both the task
    and the requirement must match. Not in the list held: read it once more. Still not: say why, never nothing. The Tasks
    page's own copy is not read again behind an open task, so the shared list is looked in too. The list is read a page
    at a time and the newest ask sorts last in its group, so a miss reads on through the pages (up to DEC_ASK_PAGES). */
const DEC_ASK_PAGES = 20;
async function decideOpenAsk(taskId: string, rid: string, quiet = false): Promise<boolean> {
  const mine = (it: NeedsEntry) => it.kind === 'ask' && decideTaskId(it) === taskId && decideRid(it) === rid;
  const find = () => [decideSource(), needsHeld()].map(items => ({items, it: items.find(mine)})).find(x => x.it);
  let at = find();
  if (!at) { await v2Refresh(); at = find(); }
  for (let i = 0; !at && !needsAll() && i < DEC_ASK_PAGES; i++) {
    if (!await v2NeedsMore()) break;
    at = find();
  }
  if (at?.it && decideOpen(at.it.id, at.items)) return true;
  if (quiet) return false;
  // Found but not opened: only a bot's decideOpen refuses, and a bot has no Answer; kept as a guard.
  toast(at ? 'This ask could not be opened here. Open it from Needs you.'
    : needsAll() ? 'This ask is no longer in your Needs you list: it may have been answered or withdrawn. Reload the task to see where it is.'
      : 'This ask was not found in the part of your Needs you list read so far. Open it from Needs you.', true);
  return false;
}
/** Opening a task from anywhere (the board, a list, a #/task/ link, a link in chat, a bot's page): a task waiting on the
    person's own answer opens on that ask in this screen, as a click in Needs you does. False, and nothing said, when it
    is not waiting on them or the ask is not in their Needs you; the task page opens then (taskModalShow). */
type DecWait = {ask?: boolean | null; requirement_id?: string | null; who?: string | null};
async function decideTaskOpen(task: {id: string | number; turn?: DecWait | null; waits?: DecWait[] | null; status?: string | null}): Promise<boolean> {
  const me = myActor(), id = String(task.id);
  if (S.me?.role === 'bot' || !me || ['done', 'closed'].includes(String(task.status || ''))) return false;
  // Any of the task's waits that is their own ask counts, not only the first (another person's OK may head the list).
  const waits = task.waits?.length ? task.waits : task.turn ? [task.turn] : [];
  const rid = waits.find(w => w.ask && w.requirement_id && w.who === me)?.requirement_id
    // A list row carries only its first wait: an ask of theirs behind it is in the Needs you already read, oldest first.
    || decideHeldAsk(id);
  return rid ? decideOpenAsk(id, rid, true) : false;
}
function decideHeldAsk(taskId: string): string {
  const it = [...decideSource(), ...needsHeld()].find(x => x.kind === 'ask' && decideTaskId(x) === taskId);
  return it ? decideRid(it) : '';
}
/** Flow on a stage, or with `pipeline` and no stage on a pipeline: every page of Needs you is read first, then its items
    are walked. False when none wait. */
async function decideStageOpen(stage: string, pipeline: string = ''): Promise<boolean> {
  if (!stage && !pipeline || S.me?.role === 'bot') return false;
  if (!needsAll()) await needsReadAll();
  return decideOpen('', decideSource(), stage, stage ? '' : pipeline);
}
/** A deep link to a stage's review (#/decide?stage=<id>): its walk, or, with nothing there, its pipeline on that stage
    saying so. */
async function decideStageWanted(stage: string) {
  if (await decideStageOpen(stage)) return;
  const p = ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).find(x => x.stages.some(s => s.id === stage));
  const name = decideStageName(stage) || 'this stage';
  if (!p || typeof TASKS_ST === 'undefined' || !TASKS_ST) { toast(`Nothing in ${name} waits on you`); return; }
  pvStageKeep(p.id, stage);
  const view = pvStateFor(p);
  view.stage = stage;
  view.bar = {taskId: '', to: '', push: false, text: `Nothing in ${name} waits on you`};
  if (TASKS_ST.pipeline !== p.id || TASKS_ST.archive) tasksShow(TASKS_ST, {pipeline: p.id, archive: ''});
  else tasksRender(TASKS_ST);
}
function decideStop() {
  const st = DEC;
  if (!st) return;
  DEC = null; DEC_VOICE = false;
  decideOpinionsEnd();
  decideSugEnd();
  DEC_ABOUT.clear(); DEC_SENT = ''; DEC_HELD.clear(); DEC_SENDING.clear();
  clearTimeout(st.barTimer);
  for (const v of st.ok.values()) decideOkLet(v);
  st.off.forEach(fn => fn());
  // A browser view on screen gives back control and stops reading pictures.
  const thing: HTMLElement | null = $('.decide .dc-thing');
  if (thing) { browserView.stopIn(thing); thing.innerHTML = ''; delete thing.dataset.html; }
  const d: HTMLDialogElement | null = $('#need-card');
  if (d?.open) d.close();
  needsListsRedraw();
  if (st.opener?.isConnected) st.opener.focus({preventScroll: true});
}
/** A deep link to one item (#/decide?item=<id>): its ask, or its task when it is not in the list. */
async function decideWanted(id: string) {
  if (decideOpen(id)) return;
  await v2Refresh();
  if (!decideOpen(id)) location.hash = '#/task/' + encodeURIComponent(id);
}
/** The list was read again (hub-v2.ts): drop what left, add what came. The item on screen is drawn again only when it
    went; an OK's content is never read again here. */
function decideRefresh(st: DecState, items: NeedsItem[] | NeedsEntry[] | undefined) {
  const before = st.at;
  decideSync(st, needsAsEntries(items));
  if (st.at !== before) decideRender(st); else decidePaintHead(st);
  decideOpinions(st);   // asks that joined
}

// ---- moving through
function decideGo(st: DecState, id: string | null) {
  st.at = id; st.more = ''; st.error = ''; st.busy = false; st.files = []; DEC_SENT = ''; DEC_ASK_NOTE = '';
  if (id) st.seen.add(id);
  decideRender(st);
}
function decideStep(st: DecState, dir: number) {
  const live = decideLive(st), i = live.indexOf(st.at || ''), next = live[i + dir];
  if (next) { decideFilesLeft(st); decideGo(st, next); }
}
/** Skip (every width): the next item, nothing answered; from the last, the first one still waiting, which is the first
    one skipped. */
function decideSkip(st: DecState) {
  const live = decideLive(st), i = live.indexOf(st.at || ''), next = live[i + 1] || live[0];
  if (next && next !== st.at) { decideFilesLeft(st); decideGo(st, next); decideCalm(); }
}
/** After an answer or Skip on a phone the next item comes in where the last one's buttons were: for DEC_CALM_MS its
    buttons take no taps (decide.css .dc-calm), so a second tap on the same spot answers nothing unread. */
function decideCalm() {
  const root = $('.decide');
  if (!root || !decidePhone()) return;
  clearTimeout(DEC_CALM_TIMER);
  root.classList.add('dc-calm');
  DEC_CALM_TIMER = window.setTimeout(() => root.classList.remove('dc-calm'), DEC_CALM_MS);
}
/** Moving on unsent: the note's files stay on the task's Files (each uploaded when added), and the toast says where. */
function decideFilesLeft(st: DecState) {
  const up = st.files.filter(f => f.state !== 'failed').length;
  if (up) toast(`${up === 1 ? '1 file stays' : `${up} files stay`} on the task's Files`);
}
/** After an answer: the next item, else the one before, else All done. */
function decideAfter(st: DecState, id: string) {
  st.gone.add(id);
  const i = st.order.indexOf(id), live = (k: string) => !st.gone.has(k) && st.items.has(k);
  decideGo(st, st.order.slice(i + 1).find(live) || st.order.slice(0, Math.max(i, 0)).reverse().find(live) || null);
  decideCalm();
}

// ---- drawing
/** Where the walk is, as the crumb says it: ['All mine'], then the pipeline, then the stage (or Other). */
function decideWhere(st: DecState): string[] {
  if (st.stage === DEC_OTHER) return ['All mine', 'Other'];
  const p = st.stage ? decidePipelineOf(st.stage) : decidePipelines().find(x => x.id === st.pipeline);
  return ['All mine', ...(p ? [p.name] : []), ...(st.stage ? [decideStageName(st.stage) || 'Stage'] : [])];
}
/** The crumb's menu: All mine, then each pipeline and its stages where something waits on the person, then Other when
    something waits in no stage. No numbers. */
function decideMenuHTML(st: DecState): string {
  const counts = needsStageCounts(), row = (cls: string, on: boolean, data: string, name: string) =>
    `<button type="button" role="menuitemradio" aria-checked="${on}" class="dc-mi ${cls}" ${data}>${esc(name)}</button>`;
  let staged = 0;
  const tree = decidePipelines().map(p => {
    const stages = p.stages.filter(s => s.id && (counts.get(s.id) || 0) > 0);
    stages.forEach(s => { staged += counts.get(s.id || '') || 0; });
    return stages.length ? row('l1', !st.stage && st.pipeline === p.id, `data-dc-walk="" data-dc-pipe="${esc(p.id)}"`, p.name)
      + stages.map(s => row('l2', st.stage === s.id, `data-dc-walk="${esc(s.id)}"`, s.name)).join('') : '';
  }).join('');
  const other = decideCount() > staged ? row('l1', st.stage === DEC_OTHER, `data-dc-walk="${DEC_OTHER}"`, 'Other') : '';
  return row('', !st.stage && !st.pipeline, 'data-dc-walk=""', 'All mine') + tree + other;
}
/** Opens (true) or closes (false) the crumb's menu; true when it was open and now is not, or now is open. */
function decideMenu(open: boolean): boolean {
  const menu = document.querySelector<HTMLElement>('.decide .dc-menu'), crumb = document.querySelector<HTMLElement>('.decide .dc-crumb');
  if (!menu || !crumb || !DEC) return false;
  const was = !menu.hidden;
  if (open) menu.innerHTML = decideMenuHTML(DEC);
  menu.hidden = !open;
  crumb.setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  return open || was;
}
/** A row of the menu: that place's walk, every page of Needs you read first. */
async function decideWalkTo(stage: string, pipeline: string) {
  decideMenu(false);
  if (!needsAll()) await needsReadAll();
  if (!DEC) return;
  if (!decideOpen('', decideSource(), stage, pipeline)) decideOpen('', decideSource());
}
/** Nothing left: All done, how many were snoozed on this visit, and the suggested rules for the person's stages. */
function decideDoneHTML(st: DecState): string {
  const sug = decideSugHTML();
  return `<div class="dc-done${sug ? ' has' : ''}">${flowMarkHTML(48)}<h2>All done</h2>`
    + (st.snoozed ? `<p class="dc-sub">${st.snoozed} snoozed</p>` : '') + '<button type="button" class="ghost" data-dc="close">Close</button>'
    + sug + '</div>';
}

// ---- suggested rules (backend/flow_suggestions.py)
interface FlowSuggestion {id: string; text: string; state: 'open' | 'added' | 'dismissed'; pending: boolean; notes_used: number;
  notes_of: number; pipeline: string; stage: string}
const DEC_SUG_POLL_MS = 2000;     // the next read while one is being worked out or rewritten
const DEC_SUG_FOR_MS = 60_000;    // given up on this long after the last ask
/** This visit's suggested rules: the rows as last read, whether they were asked for, the next read's timer, when the
    last ask was sent, the row whose ✎ box is open and what is typed in it, and the visit, which closing moves on so a
    read from an earlier visit lands nowhere. */
const DEC_SUG = {rows: [] as FlowSuggestion[], asked: false, timer: 0, since: 0, edit: '', draft: '', visit: 0};
function decideSugEnd() {
  DEC_SUG.visit++;
  clearTimeout(DEC_SUG.timer);
  Object.assign(DEC_SUG, {rows: [], asked: false, timer: 0, since: 0, edit: '', draft: ''});
}
/** All done, the first time this visit: the suggestions for the stages the person may change, if any. */
function decideSugStart(st: DecState) {
  if (DEC_SUG.asked || S.me?.read_only_view || !decidePipelines().some(p => p.may_change)) return;
  DEC_SUG.asked = true;
  decideSugPoll(st);
}
/** Read again every DEC_SUG_POLL_MS while something is pending, for DEC_SUG_FOR_MS at most; any failure ends it. */
function decideSugPoll(st: DecState) {
  DEC_SUG.since = Date.now();
  if (!DEC_SUG.timer) void decideSugRound(st);
}
async function decideSugRound(st: DecState) {
  DEC_SUG.timer = 0;
  const visit = DEC_SUG.visit, abort = new AbortController(), late = window.setTimeout(() => abort.abort(), 15_000);
  let got: {state: string; suggestions: FlowSuggestion[]} | null = null;
  try {
    // A read that changes nothing: no retries and no pressed-button state (not post()).
    const r = await fetch(`${API}/v2/flow/suggestions`, {method: 'POST', redirect: 'error', signal: abort.signal,
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': newRequestId()}, body: '{}'});
    if (r.ok) got = decideSugRead(await r.json());
  } catch { got = null; } finally { clearTimeout(late); }
  if (visit !== DEC_SUG.visit || DEC !== st || !got) return;
  DEC_SUG.rows = got.suggestions;
  decideSugPaint(st);
  if (got.state === 'pending' && Date.now() - DEC_SUG.since < DEC_SUG_FOR_MS) {
    DEC_SUG.timer = window.setTimeout(() => void decideSugRound(st), DEC_SUG_POLL_MS);
  }
}
/** The list in an answer; anything else (an empty answer) is none, ready. */
function decideSugRead(body: unknown): {state: string; suggestions: FlowSuggestion[]} {
  const o = body && typeof body === 'object' ? body as {state?: unknown; suggestions?: unknown} : {};
  const list = Array.isArray(o.suggestions) ? o.suggestions as unknown[] : [];
  return {state: o.state === 'pending' ? 'pending' : 'ready',
    suggestions: list.filter((x): x is FlowSuggestion => !!x && typeof x === 'object' && typeof (x as FlowSuggestion).id === 'string'
      && typeof (x as FlowSuggestion).text === 'string' && (x as FlowSuggestion).state !== 'dismissed')};
}
/** The list under All done: none at all with nothing to show. */
function decideSugHTML(): string {
  if (!DEC_SUG.rows.length) return '';
  const icon = (dc: string, label: string, mark: string, off = false) =>
    `<button type="button" class="dc-icon" data-dc="${dc}" title="${label}" aria-label="${label}"${off ? ' disabled' : ''}>${mark}</button>`;
  return '<section class="dc-sug" aria-label="Suggested rules"><h3>Suggested rules</h3>' + DEC_SUG.rows.map(r => {
    const on = r.state === 'added', id = esc(r.id);
    const why = on ? 'Added' : r.pending ? 'Tico is rewriting it'
      : `${esc(r.pipeline)} / ${esc(r.stage)} · ${r.notes_used} of ${r.notes_of} send backs`;
    const acts = on ? icon('sug-undo', 'Undo', '↩')
      : `<button type="button" class="ghost" data-dc="sug-add"${r.pending ? ' disabled' : ''}>Add</button>`
        + icon('sug-edit', 'Tell Tico what to change', '✎', r.pending) + icon('sug-dismiss', 'Dismiss', '✕');
    const box = DEC_SUG.edit === r.id && !on && !r.pending ? `<div class="dc-sf" data-sug="${id}">
      <input id="dc-sug-say" maxlength="500" autocomplete="off" aria-label="Tell Tico what to change" value="${esc(DEC_SUG.draft)}">
      <button type="button" class="ghost" data-dc="sug-send" data-dc-needs="#dc-sug-say"${DEC_SUG.draft.trim() ? '' : ' disabled'}>Send</button></div>` : '';
    return `<div class="dc-sr${on ? ' on' : ''}${r.pending ? ' busy' : ''}" data-sug="${id}"><div class="tx"><b title="${esc(r.text)}">${esc(r.text)}</b>`
      + `<span>${why}</span></div>${acts}</div>${box}`;
  }).join('') + '</section>';
}
/** Draws the list again when All done is on screen, the ✎ box keeping its focus. */
function decideSugPaint(st: DecState) {
  const side: HTMLElement | null = $('.decide .dc-side');
  if (!side || decideCurrent(st)) return;
  const typing = document.activeElement?.id === 'dc-sug-say';
  side.innerHTML = decideDoneHTML(st);
  if (typing || DEC_SUG.edit) document.querySelector<HTMLInputElement>('#dc-sug-say')?.focus({preventScroll: true});
}
/** Add, Undo, ✕, ✎ and Send on one row: each a write for everyone who may change the stage; the row as it came back. */
async function decideSugAct(st: DecState, what: string, id: string) {
  const at = DEC_SUG.rows.findIndex(r => r.id === id), row = DEC_SUG.rows[at];
  if (!row) return;
  if (what === 'edit') {
    DEC_SUG.edit = DEC_SUG.edit === id ? '' : id; DEC_SUG.draft = '';
    decideSugPaint(st);
    return;
  }
  const text = DEC_SUG.draft.trim();
  if (what === 'send' && !text) return;
  if (what === 'send') { DEC_SUG.edit = ''; DEC_SUG.draft = ''; row.pending = true; decideSugPaint(st); }
  const visit = DEC_SUG.visit, path = `/v2/flow/suggestions/${encodeURIComponent(id)}/${what === 'send' ? 'feedback' : what}`;
  try {
    const got = await post(path, what === 'send' ? {text} : {});
    if (visit !== DEC_SUG.visit) return;
    const back = decideSugRead({suggestions: [got.suggestion]}).suggestions[0];
    const now = DEC_SUG.rows.findIndex(r => r.id === id);
    if (now >= 0) {
      if (what === 'dismiss') DEC_SUG.rows.splice(now, 1);
      else if (back) DEC_SUG.rows[now] = back;
    }
    if (back?.pending) decideSugPoll(st);
  } catch (e) {
    if (visit !== DEC_SUG.visit) return;
    row.pending = false;
    toast(errorMessage(e, 'Not saved.'));
  }
  decideSugPaint(st);
}
const decideStageName = (id: string | null | undefined): string => {
  for (const p of (typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []) {
    const s = p.stages.find(x => x.id === id);
    if (s) return s.name;
  }
  return '';
};
/** Who asks: the asker of an ask, the bot that can't run, who @'d you, else the task's owner. */
function decideAsker(it: NeedsEntry): string {
  if (it.kind === 'mention') return it.from_id || '';
  if (it.kind === 'paused') return it.bots?.[0] ? 'bot:' + it.bots[0].replace(/^bot:/, '') : '';
  return it.ask?.asked_by || it.task?.owner_id || '';
}
function decidePaintHead(st: DecState) {
  const root = $('.decide');
  if (!root) return;
  const live = decideLive(st), at = live.indexOf(st.at || ''), it = decideCurrent(st);
  const where = decideWhere(st), crumb = $('.dc-crumb', root)!, key = where.join('/');
  if (crumb.dataset.where !== key) {
    crumb.dataset.where = key;
    crumb.innerHTML = where.map((w, i) => `<span${i < where.length - 1 ? ' class="dim"' : ''}>${esc(w)}</span>`).join('<span class="sl">/</span>') + TL_ICON.caret;
  }
  ($('[data-dc="prev"]', root) as HTMLButtonElement).disabled = !it || at <= 0;
  ($('[data-dc="next"]', root) as HTMLButtonElement).disabled = !it || at >= live.length - 1;
  // Voice on: the wave stands where the mark was and the mic goes; the line says voice is coming.
  $('.dc-flow', root)!.hidden = DEC_VOICE;
  $('.dc-mic', root)!.hidden = DEC_VOICE;
  $('.dc-wave', root)!.hidden = !DEC_VOICE;
  const say = DEC_VOICE ? 'Voice is coming soon' : it ? DEC_LINES.get(it.id)?.line || '' : '';
  const line = $('.dc-say', root)!;
  if (line.textContent !== say) line.textContent = say;
  // Tico thinking: the mark moves while the item on screen waits on its opinion, and the next ask still waiting is
  // named, faint, before ‹ › (none on a phone, decide.css).
  const thinking = (k: string) => DEC_OP.state.get(k) === 'pending';
  $('.dc-flow .flow-mark', root)?.classList.toggle('live', decideOpWaits(it));
  const next = live.find(k => k !== st.at && thinking(k)), upItem = next ? st.items.get(next) : undefined;
  const up = upItem ? upItem.task?.title || upItem.line : '', upEl = $('.dc-up', root)!;
  if (upEl.textContent !== up) upEl.textContent = up;
}
/** Tico's line for the item `itemId` (a Needs you id) and the answer it would press: the line fills the top bar and
    that answer's button becomes the main one while the item is on screen. `pick` is a button's `data-dc` (approve,
    sendback, done, snooze, again, ...) or a choice's label. Null takes it away, and the screen is as it was. Nothing is
    read or sent. */
function decideLineSet(itemId: string, o: {line: string; pick: string} | null): void {
  if (o) DEC_LINES.set(itemId, {line: o.line, pick: o.pick}); else DEC_LINES.delete(itemId);
  if (DEC && DEC.at === itemId) { decidePaintHead(DEC); decidePaintSide(DEC); }
}
/** The pick of Tico's line, as the main button (a bot's choice card, outlined as its recommendation is): the others go
    plain. While the ask waits on Tico (decideOpWaits) no answer is the main button; with no pick, they are as drawn. */
function decideLinePick(it: NeedsEntry, host: HTMLElement) {
  const pick = DEC_LINES.get(it.id)?.pick;
  const btns = [...host.querySelectorAll<HTMLButtonElement>('.dc-acts button[data-dc]:not(.linkish), button[data-dc-choice]')];
  if (!pick && decideOpWaits(it)) {
    for (const b of btns) if (b.classList.contains('primary')) { b.classList.remove('primary'); b.classList.add('ghost'); }
    return;
  }
  const to = pick ? btns.find(b => b.dataset.dc === pick || b.dataset.dcChoice === pick) : undefined;
  if (!to) return;
  for (const b of btns) {
    if (b.classList.contains('dc-choice')) b.classList.toggle('dc-rec', b === to);
    else { b.classList.toggle('primary', b === to); b.classList.toggle('ghost', b !== to); }
  }
}
// ---- Tico's opinions (POST /v2/flow/opinions, backend/flow_opinions.py): its line and pick on each ask, and a score
// the walk is ordered by and never shown. Tico only advises: nothing here answers anything.
/** One ask's opinion as the route answers it. Typed here by hand: the generated api types on this branch do not have
    the route yet. */
interface FlowOpinion {id: string; state: 'ready' | 'pending' | 'none'; pick: string; line: string; note: string; score: number | null}
const DEC_OP_MAX = 30;            // ids in one request, the one request of a round
const DEC_OP_POLL_MS = 2000;      // the next round, while some are pending or not yet asked
const DEC_OP_FOR_MS = 60_000;     // pending ones are given up on this long after the last new ask was sent
const DEC_OP_ORDER_MIN = 6;       // a set this big has its unseen items ordered by score
/** An OK's pick as the screen's buttons name it; a question's pick is its choice as written. */
const DEC_OP_PICK: Record<string, string> = {approve: 'approve', send_back: 'sendback', snooze: 'snooze'};
/** This visit's opinions: each asked ask's state, the fingerprint of the ask it is for (decideOpPrint), its score, the
    note a send back would carry, the items whose Notes the person typed in; the next round's timer, whether a round is
    out (one at a time) and its abort, when the last new ask was sent, `off` once the route failed or is missing
    (nothing is asked again this visit, and the screen is as it was without it), and the visit, which closing moves on
    so nothing from an earlier visit lands in this one. */
const DEC_OP = {state: new Map<string, FlowOpinion['state']>(), print: new Map<string, string>(), score: new Map<string, number>(),
  note: new Map<string, string>(), typed: new Set<string>(), timer: 0, busy: false, abort: null as AbortController | null,
  since: 0, off: false, visit: 0};
function decideOpinionsEnd() {
  DEC_OP.visit++;
  DEC_OP.abort?.abort();
  clearTimeout(DEC_OP.timer);
  DEC_OP.timer = 0; DEC_OP.busy = false; DEC_OP.abort = null; DEC_OP.since = 0; DEC_OP.off = false;
  DEC_OP.state.clear(); DEC_OP.print.clear(); DEC_OP.score.clear(); DEC_OP.note.clear(); DEC_OP.typed.clear(); DEC_LINES.clear();
}
/** What an opinion is about: the ask's sentence, choices, what it shows and its page's version. A bot that edits the
    ask or puts up a new page under the same id changes it. */
const decideOpPrint = (it: NeedsEntry): string =>
  JSON.stringify([it.line, it.ask?.line ?? '', it.ask?.choices ?? null, it.ask?.shows ?? [], decidePage(it)]);
/** An ask still waiting on its opinion: asked (or about to be) with none back yet. Its answers have no main button
    meanwhile, so the main one never changes under the person. Not while the route is off. */
const decideOpWaits = (it: NeedsEntry | null | undefined): boolean => !!it && it.kind === 'ask' && !DEC_OP.off
  && !S.me?.read_only_view && DEC_OP.state.get(it.id) !== 'ready' && DEC_OP.state.get(it.id) !== 'none';
/** The asks of the walk from the item on screen on (it first), then round to those before it. */
function decideOpAsks(st: DecState): NeedsEntry[] {
  const live = decideLive(st), at = Math.max(0, st.at ? live.indexOf(st.at) : 0);
  return [...live.slice(at), ...live.slice(0, at)].map(k => st.items.get(k)).filter((it): it is NeedsEntry => it?.kind === 'ask');
}
/** On opening, on a new set and on each refresh: an ask that changed since its opinion was asked loses its line, pick,
    note and score and is asked again; then a round starts unless one is out or due (it takes in what joined). */
function decideOpinions(st: DecState) {
  if (DEC !== st || DEC_OP.off || S.me?.read_only_view) return;
  let mine = false;
  for (const it of st.items.values()) {
    const was = DEC_OP.print.get(it.id);
    if (was === undefined || was === decideOpPrint(it)) continue;
    DEC_OP.state.delete(it.id); DEC_OP.print.delete(it.id); DEC_OP.score.delete(it.id); DEC_OP.note.delete(it.id);
    DEC_LINES.delete(it.id);
    mine ||= it.id === st.at;
  }
  if (mine) { decidePaintHead(st); decidePaintSide(st); }
  if (!DEC_OP.busy && !DEC_OP.timer) decideOpRound(st);
}
/** One round: ONE request of at most DEC_OP_MAX asks, the pending ones first, then those not yet asked nearest after
    the item on screen. The next comes DEC_OP_POLL_MS after it answers, while any is pending or not yet asked. */
function decideOpRound(st: DecState) {
  DEC_OP.timer = 0;
  if (DEC !== st || DEC_OP.off) return;
  const asks = decideOpAsks(st);
  const pending = asks.filter(it => DEC_OP.state.get(it.id) === 'pending'), fresh = asks.filter(it => !DEC_OP.state.has(it.id));
  if (!fresh.length && pending.length && Date.now() - DEC_OP.since > DEC_OP_FOR_MS) {
    for (const it of pending) DEC_OP.state.set(it.id, 'none');
    decidePaintHead(st);
    if (pending.some(it => it.id === st.at)) decidePaintSide(st);
    return;
  }
  const want = [...pending, ...fresh].slice(0, DEC_OP_MAX);
  if (!want.length) return;
  if (want.some(it => !DEC_OP.state.has(it.id))) DEC_OP.since = Date.now();
  const visit = DEC_OP.visit, sent = new Map(want.map(it => [it.id, decideOpPrint(it)])), abort = new AbortController();
  DEC_OP.busy = true; DEC_OP.abort = abort;
  const now = () => visit === DEC_OP.visit && DEC === st;
  void decideOpinionsRead([...sent.keys()], abort).then(got => {
    if (now()) decideOpinionsTake(st, sent, got);
  }, () => {
    if (!now()) return;
    DEC_OP.off = true;   // missing (404), refused or failing: no line this visit, no more asking, buttons as they were
    decidePaintHead(st);
    decidePaintSide(st);
  }).finally(() => {
    if (visit !== DEC_OP.visit) return;
    DEC_OP.busy = false; DEC_OP.abort = null;
    if (!now() || DEC_OP.off) return;
    if (decideOpAsks(st).some(it => !DEC_OP.state.has(it.id) || DEC_OP.state.get(it.id) === 'pending')) {
      DEC_OP.timer = window.setTimeout(() => decideOpRound(st), DEC_OP_POLL_MS);
    }
  });
}
/** One request, which `abort` (closing the screen, or 15 s) stops. */
async function decideOpinionsRead(ids: string[], abort: AbortController): Promise<FlowOpinion[]> {
  // A read that changes nothing: no retries and no pressed-button state (not post()); any failure ends the asking.
  const late = window.setTimeout(() => abort.abort(), 15_000);
  try {
    const r = await fetch(`${API}/v2/flow/opinions`, {method: 'POST', redirect: 'error', signal: abort.signal,
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': newRequestId()}, body: JSON.stringify({items: ids})});
    if (!r.ok) throw new Error(`Opinions: ${r.status}`);
    const body: unknown = await r.json();
    const list = body && typeof body === 'object' && 'opinions' in body && Array.isArray(body.opinions) ? body.opinions as unknown[] : [];
    return list.filter((o): o is FlowOpinion => !!o && typeof o === 'object' && typeof (o as FlowOpinion).id === 'string');
  } finally { clearTimeout(late); }
}
/** What came back for the asks `sent` (id → fingerprint when asked): a ready opinion's line and pick go on the screen,
    with its note when it would send back; an id the answer left out counts as none. An answer for an ask that changed
    since it was asked is dropped, and the ask is asked again. */
function decideOpinionsTake(st: DecState, sent: Map<string, string>, got: FlowOpinion[]) {
  const byId = new Map(got.map(o => [o.id, o]));
  let mine = false;
  for (const [id, print] of sent) {
    const it = st.items.get(id);
    if (!it || decideOpPrint(it) !== print) continue;
    const o = byId.get(id), state = o?.state === 'ready' || o?.state === 'pending' ? o.state : 'none';
    if (id === st.at && (DEC_OP.state.get(id) !== state || state === 'ready')) mine = true;
    DEC_OP.state.set(id, state); DEC_OP.print.set(id, print);
    if (typeof o?.score === 'number') DEC_OP.score.set(id, o.score);
    if (state !== 'ready' || !o?.line) continue;
    const pick = decideKind(it) === 'ok' ? DEC_OP_PICK[o.pick] || '' : String(o.pick || '');
    if (pick === 'sendback' && o.note) DEC_OP.note.set(id, String(o.note));
    DEC_LINES.set(id, {line: String(o.line), pick});
  }
  decideScoreOrder(st);
  decidePaintHead(st);
  if (mine) decidePaintSide(st);
}
/** A set of more than 5: the items not yet seen this visit go highest score first, those with none after them in the
    list's order; the item on screen and every one seen (or answered) keep their place, so ‹ goes back to what was just
    seen. 5 or fewer keep the list's order. */
function decideScoreOrder(st: DecState) {
  if (decideLive(st).length < DEC_OP_ORDER_MIN || !DEC_OP.score.size) return;
  const free = st.order.map((k, i) => ({k, i})).filter(x => x.k !== st.at && !st.seen.has(x.k) && !st.gone.has(x.k));
  const ranked = free.map((x, n) => ({k: x.k, n, s: DEC_OP.score.get(x.k) ?? -1})).sort((a, b) => b.s - a.s || a.n - b.n);
  free.forEach((x, n) => { st.order[x.i] = ranked[n]!.k; });
}
function decideRender(st: DecState) {
  const root = $('.decide');
  if (!root) return;
  decidePaintHead(st);
  const it = decideCurrent(st);
  root.classList.toggle('dc-empty', !it);
  if (!it) {
    $('.dc-thing', root)!.innerHTML = '';
    $('.dc-side', root)!.innerHTML = decideDoneHTML(st);
    decideBarRoom();
    decideSugStart(st);
    return;
  }
  // Coming to an ask: what it shows is read once; held content from other refs (a new version came while it was off
  // screen) is read again and marked as changed. While it is on screen, a refresh never reads it again (decideShownPage).
  const held = decideKind(it) === 'ok' ? st.ok.get(decideOkKey(it)) : undefined;
  if (decideKind(it) === 'ok' && (!held || decideOkStale(held, it))) void decideOkLoad(st, it, !!held);
  // Any other item on a task: its text is read once, and the next item's ahead (decide-about.ts).
  decideAboutAhead(st);
  decidePaintBody(st);
  decidePaintSide(st);
}
/** The left side: the thing asked about, as it was read. Drawn again from what is held, never read again. */
function decidePaintBody(st: DecState) {
  const it = decideCurrent(st), host: HTMLElement | null = $('.decide .dc-thing');
  if (!it || !host) return;
  const kind = decideKind(it);
  let html = '';
  if (kind === 'ok') {
    const v = st.ok.get(decideOkKey(it));
    html = !v || v.state === 'loading' ? skelRows(4)
      : v.state === 'failed' ? `<p class="err" role="alert">${esc(v.error)}</p>`
      : (v.changed ? '<p class="dc-changed" role="alert">It changed. Look again.</p>' : '') + decideSinceHTML(v)
        + (v.since?.on && !v.since.same ? `<div class="dc-doc dc-diff">${v.since.diff}</div>`
          // Nothing attached (no grant, no shows): marked bare, so a phone opens on the question (decide.css).
          : `<div class="dc-doc${!v.html && !v.pages.length ? ' dc-bare' : ''}">${v.html}${v.pages.map((_, i) => `<div data-dc-page="${i}"></div>`).join('')}</div>`);
  } else if (decideTaskId(it)) {
    // Who asked and when, then the task's own text, its links, files and pages; the heading is on the right only.
    html = decideAboutHTML(it, kind);
  } else if (kind === 'mention') {
    html = `<blockquote class="dc-quote">${esc(it.line)}</blockquote>`;
  }
  // A website's browser the ask shows (`browser:<tool>/<account>`): the live view; an OK's comes in its shows.
  if (kind !== 'ok') html += (it.ask?.shows || []).filter(r => r.startsWith('browser:')).map(r => decideBrowserSlot(r.slice(8))).join('');
  if (host.dataset.html === html && host.dataset.key === it.id) return;
  host.dataset.html = html; host.dataset.key = it.id;
  browserView.stopIn(host);
  host.innerHTML = html;
  host.querySelectorAll<HTMLElement>('[data-dc-browser]').forEach(slot => { decideBrowserMount(st, it, slot); });
  const v = kind === 'ok' ? st.ok.get(decideOkKey(it)) : null;
  if (v && typeof window.pageCard === 'function')
    v.pages.forEach((p, i) => host.querySelector(`[data-dc-page="${i}"]`)?.replaceWith(window.pageCard(p, {size: 'full'})));
}
/** "Changes since vK" over a page that comes back: on, the line diff from vK; off, the page with its changed blocks
    marked. "No text changes" when the text is the same. */
function decideSinceHTML(v: AskOkView): string {
  const s = v.since;
  if (!s) return '';
  return `<div class="dc-since"><label class="dc-switch"><input type="checkbox" role="switch" data-dc-since${s.on ? ' checked' : ''}>`
    + `<span>Changes since v${s.version}</span></label>${s.same ? '<span class="dc-since-none">No text changes</span>' : ''}</div>`;
}
const decideBrowserSlot = (ref: string) => ref.includes('/') ? `<div class="dc-browser" data-dc-browser="${esc(ref)}"></div>` : '';
/** The live view of the browser an ask shows. Its Done answers the ask "Done" when that is one of its choices (a bot's
    "sign in for me"), else it only closes the view, which leaves an Open browser button. */
function decideBrowserMount(st: DecState, it: NeedsEntry, slot: HTMLElement) {
  const [toolId = '', accountId = ''] = (slot.dataset.dcBrowser || '').split('/');
  const answers = decideKind(it) === 'question' && (it.ask?.choices || []).includes('Done');
  const taskId = it.task_id || it.task?.id || '', rid = decideRid(it), ver = it.task?.version ?? null;
  const closed = () => {
    if (!slot.isConnected || DEC !== st) return;
    slot.innerHTML = '<button type="button" class="ghost" data-dc-browser-open>Open browser</button>';
    const b = slot.querySelector<HTMLButtonElement>('[data-dc-browser-open]');
    if (b) b.onclick = () => decideBrowserMount(st, it, slot);
  };
  browserView.mount(slot, {toolId, accountId, title: it.line, onClose: closed,
    onDone: async () => {
      if (!answers) return true;
      await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, 'Done', ver), decideUndoAnswer);
      // Still on this item: the answer was refused and says why on the right; the view stays open.
      return !(DEC === st && st.at === it.id);
    }});
}
const decideBtn = (dc: string, label: string, main = false, extra = '') =>
  `<button type="button" class="${main ? 'primary' : 'ghost'}" data-dc="${dc}"${extra}>${esc(label)}</button>`;
/** The right side: the sentence, who asks, and the buttons for this kind. */
function decidePaintSide(st: DecState) {
  const it = decideCurrent(st), host: HTMLElement | null = $('.decide .dc-side');
  if (!it || !host) return;
  const kind = decideKind(it), asker = decideAsker(it);
  const typed = kind === 'question' ? it.ask?.typed : undefined;
  // A page's OK is about the task's page: its title heads it, with the version under it. Needs you's line opens with
  // what it is and the decision ("Spec for <title>, ready for your OK", views.page_ask_line), so only what the stage
  // said after that (Jev's reason, a send back) is kept.
  const page = it.ask?.page, title = page ? it.task?.title || '' : '';
  const said = title ? it.line.replace(/^.*?, ready for your OK\.?\s*/, '').replace(/\s*OK [^()]* \(version \d+\)$/, '').trim() : '';
  // Who asks, alone, beside the question.
  const who = asker ? `<span class="dc-owner">${esc(actorLabel(asker))}</span>` : '';
  const q = (cls: string, text: string) => `<div class="dc-qrow"><h3 class="${cls}">${esc(text)}</h3>${who}</div>`;
  // Open task ↗: the task's own page, which stays open (taskModalOpen with `direct`); its Answer comes back here.
  const taskId = decideTaskId(it), num = it.task?.number;
  const open = taskId && kind !== 'mention' ? `<button type="button" class="linkish dc-open-task" data-dc="task">Open task${num ? ` #${num}` : ''} ↗</button>` : '';
  // A page with its rounds read: the right side in parts (decideRoundsHTML), and the stage's paragraph is gone.
  const okView = kind === 'ok' && page ? st.ok.get(decideOkKey(it)) : undefined;
  const parts = page && okView?.rounds ? decideRoundsHTML(st, it, okView.rounds, page.version, okView.notices) : null;
  // A person's own task and a question: a short heading in the task's own words, the full question once under it.
  const short = parts ? null : decideHeadOf(it, kind), go = short ? decideGoOf(decideAboutText(decideAboutOf(it)?.task)) : null;
  const goBtn = go ? `<button type="button" class="ghost dc-go" data-dc="go" data-dc-href="${esc(go[1])}">${esc(go[0])}</button>` : '';
  const links = goBtn || open ? `<div class="dc-links">${goBtn}${open}</div>` : '';
  const head = parts ? `<h3 class="dc-q">${esc(title)}</h3>${parts.sub}${open}${parts.body}`
    : short ? q('dc-q dc-q-short', short.head) + links
      + (short.full ? `<p class="dc-full">${esc(short.full)}</p>` : '')
    : q('dc-q', title || typed?.question || it.line) + open
      + (said && said !== title ? `<p class="dc-said">${esc(said)}</p>` : '');
  const err = st.error ? `<p class="err" role="alert">${esc(st.error)}</p>` : '';
  const busy = st.busy ? ' disabled' : '';
  let body = '';
  if (kind === 'ok') {
    const v = st.ok.get(decideOkKey(it)), may = it.ask?.may || [];
    const ready = !!v && !v.missing && (v.state === 'ready' && !!v.hash || v.state === 'none');
    // A bot's OK of Product's Validate page, its page first (backend/validate_gate.py): OK or Not validated with a note,
    // which sends the work back (In development, or Build on Product). An OK bound to an action or a person's own keeps
    // Approve and Send back. `product-validate-2`: Product added beside the pair it replaced (validate_gate.STAGES).
    const checked = !page && /^product-validate(-\d+)?$/.test(it.task?.stage_id || '') && decideAsker(it).startsWith('bot:')
      && /^page:/.test(it.ask?.shows?.[0] || '');
    const ask = DEC_ASK_NOTE === it.id ? ' placeholder="Say what to change"' : '';
    body = `<label class="dc-notes"><span>Notes</span><textarea id="dc-notes" data-dc-grow rows="${decideNoteRows()}" maxlength="4000"${ask}></textarea></label>
      ${decideFilesHTML(st)}<div class="dc-acts">${decideBtn('sendback', checked ? 'Not validated' : 'Send back', false,
        // On a phone Send back is always on; with no note it asks for one (decideClick). A wide screen keeps it off until then.
        decidePhone() ? busy : ` data-dc-needs="#dc-notes" disabled`)}${decideBtn('approve', checked ? 'OK' : 'Approve', true, !ready || !may.includes('ok') || st.busy ? ' disabled' : '')}</div>`;
  } else if (typed) {
    body = decideTypedHTML(typed, it.ask?.choices || [], st.busy);
  } else if (kind === 'question' && asker.startsWith('bot:')) {
    body = decideBotAskHTML(st, it, asker);
  } else if (kind === 'question') {
    const choices = it.ask?.choices || [];
    body = `<div class="dc-choices">${choices.map(c => `<button type="button" class="ghost" data-dc-choice="${esc(c)}"${busy}>${esc(c)}</button>`).join('')}</div>
      <label class="dc-notes"><span>${choices.length ? 'Something else' : 'Your answer'}</span><textarea id="dc-other" data-dc-grow rows="${decideNoteRows()}" maxlength="4000"></textarea></label>
      ${decideFilesHTML(st)}<div class="dc-acts">${decideBtn('other', 'Send answer', !choices.length, ' data-dc-needs="#dc-other" disabled')}${
        // Ask back only to someone it reaches: a question no bot or person asked (a stage's) has no one to ask.
        decideReplyTo(it) ? decideBtn('askback', 'Ask back', false, ' data-dc-needs="#dc-other" disabled title="Ask what they mean; the question stays open"') : ''}</div>${decideHeldHTML(it, '#dc-other')}`;
  } else if (kind === 'yours') {
    body = `${decideReplyHTML(st, it)}<div class="dc-acts dc-acts-row">${decideBtn('done', 'Done', true, busy)}${decideBtn('snooze', 'Snooze', false, busy)}${decideBtn('give', 'Give to…', false, busy)}</div>${decideMoreHTML(st, it)}`;
  } else if (kind === 'stuck') {
    // Stuck because someone can't see the private task: one button lets exactly them see it and moves it on, and says
    // what they get before the press (task #392). Else Try again.
    // Everyone the next stage shuts out, named: one press lets exactly them in.
    const share = it.task?.turn?.share || [], name = decideNames(share);
    const fix = share.length ? decideBtn('let-see', `Let ${name} see it`, true, busy) : decideBtn('again', 'Try again', true, busy);
    const gives = share.length ? `<p class="muted dc-gives">${esc(name)} will see this task, its conversation and its files, until someone removes them.</p>` : '';
    body = `<div class="dc-acts">${fix}${decideBtn('give', 'Reassign', false, busy)}${decideBtn('back', 'Move back', false, busy)}${decideBtn('close-task', 'Close', false, busy)}</div>${gives}${decideMoreHTML(st, it)}`;
  } else {
    // A cause a person fixes in the app (Connect GitHub) goes straight there; Open shows the first task it holds up.
    body = `<div class="dc-acts">${it.href ? decideBtn('fix', it.line, true) : ''}${decideBtn('open', 'Open', !it.href)}</div>`;
  }
  // What was typed stays through a redraw of the same item (busy, uploads, then a refusal in the server's words).
  const same = host.querySelector<HTMLElement>('[data-key]')?.dataset.key === it.id;
  const held = same ? host.querySelector<HTMLInputElement>('#dc-other')?.value : undefined;
  const heldNote = same ? host.querySelector<HTMLTextAreaElement>('#dc-notes')?.value : undefined;
  const heldReply = same ? host.querySelector<HTMLTextAreaElement>('#dc-reply')?.value : undefined;
  // So does the caret: a redraw while someone types (a refusal, the end of a send) leaves them typing in the same box.
  const typing = document.activeElement;
  const caret = same && typing instanceof HTMLTextAreaElement && host.contains(typing) && typing.id
    ? {id: typing.id, from: typing.selectionStart, to: typing.selectionEnd, dir: typing.selectionDirection ?? undefined} : null;
  host.innerHTML = `<div data-key="${esc(it.id)}">${head}${err}${body}</div>`;
  // Skip sits in the answer row, on every width; an item with no row of its own (a typed choice) gets one.
  if (decideLive(st).length > 1) {
    const card = host.firstElementChild!, row = card.querySelector<HTMLElement>(':scope > .dc-acts');
    const skip = `<button type="button" class="linkish dc-skip" data-dc="skip"${busy}>Skip</button>`;
    if (row) row.insertAdjacentHTML('beforeend', skip); else card.insertAdjacentHTML('beforeend', `<div class="dc-acts">${skip}</div>`);
  }
  const box = host.querySelector<HTMLInputElement>('#dc-other');
  if (box && held) box.value = held;
  const note = host.querySelector<HTMLTextAreaElement>('#dc-notes');
  if (note && heldNote) note.value = heldNote;
  // Tico would send it back: its note fills an empty Notes the person has not typed in, never over their words.
  const ticoNote = DEC_OP.note.get(it.id);
  if (note && !note.value && ticoNote && DEC_LINES.get(it.id)?.pick === 'sendback' && !DEC_OP.typed.has(it.id)) note.value = ticoNote;
  const reply = host.querySelector<HTMLTextAreaElement>('#dc-reply');
  if (reply && heldReply) reply.value = heldReply;
  host.querySelectorAll<HTMLTextAreaElement>('textarea[data-dc-grow]').forEach(decideGrow);
  // "more" only under a part its 2 lines cut.
  host.querySelectorAll<HTMLElement>('.dc-clamp:not(.open)').forEach(p => {
    const more = p.nextElementSibling as HTMLElement | null;
    if (more?.matches('[data-dc-more]')) more.hidden = p.scrollHeight <= p.clientHeight + 1;
  });
  const back = caret ? host.querySelector<HTMLTextAreaElement>(`#${caret.id}`) : null;
  if (caret && back && !back.disabled) { back.focus({preventScroll: true}); back.setSelectionRange(caret.from, caret.to, caret.dir); }
  decideHeldWire(st, host);
  decideLinePick(it, host);
  decideNeedsText($('.decide')!);
  decideBarRoom();
}
/** On a phone the answer row is pinned to the bottom (decide.css): the scroll keeps its height clear at the end and the
    Undo bar sits above it. 0 where no row is pinned. */
function decideBarRoom() {
  const row = decideBarSet();
  // Measured again whenever its height changes without a redraw (the font arriving, a long label wrapping).
  DEC_BAR_WATCH?.disconnect();
  if (row) DEC_BAR_WATCH?.observe(row, {box: 'border-box'});   // padding too: the safe-area inset
}
const decideBarRow = () => decidePhone() ? $('.decide .dc-side>div:not(.dc-done)>.dc-acts:last-child') as HTMLElement | null : null;
function decideBarSet(): HTMLElement | null {
  const root = $('.decide') as HTMLElement | null, row = decideBarRow();
  root?.style.setProperty('--dc-bar-h', `${row ? row.offsetHeight : 0}px`);
  return row;
}
const DEC_BAR_WATCH = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { decideBarSet(); });
// ---- a page that comes back: the right side in parts
const DEC_JEV: Record<string, [string, string]> = {met: ['✓', 'Meets'], unsure: ['?', 'Unsure'], failed: ['?', 'Unsure'], not_met: ['✗', "Doesn't"]};
/** Jev's verdict as a chip: ✓ Meets, ? Unsure or ✗ Doesn't, never a p-value; "Jev checking", muted, while it has none.
    `open`: a button that shows the reason under it; otherwise the reason is the chip's title. */
function decideJevChip(jev: DecRound['jev'], open: boolean | null): string {
  const said = jev.verdict ? DEC_JEV[jev.verdict] : undefined;
  if (!said) return open === null ? '<span class="dc-jev dc-jev-wait dc-jev-mark" role="img" aria-label="Jev checking" title="Jev checking">…</span>'
    : '<span class="dc-jev dc-jev-wait">Jev checking</span>';
  const label = `<span aria-hidden="true">${said[0]}</span> ${esc(said[1])}`;
  // In a step of the history: the mark alone, its word and reason in its label and title.
  if (open === null) return `<span class="dc-jev dc-jev-mark" data-v="${esc(jev.verdict || '')}" role="img" aria-label="Jev: ${esc(said[1])}"`
    + ` title="${esc(`Jev: ${said[1]}${jev.reason ? ` · ${jev.reason}` : ''}`)}">${said[0]}</span>`;
  return `<button type="button" class="dc-jev" data-v="${esc(jev.verdict || '')}" data-dc-jev aria-expanded="${open}" aria-label="Jev: ${esc(said[1])}">${label}</button>`;
}
/** A person's own words, cut to 2 lines, with "more" (shown in full once opened). */
function decideClampHTML(st: DecState, it: NeedsEntry, part: string, text: string): string {
  const open = st.open.has(`${it.id}:more:${part}`);
  return `<p class="dc-clamp${open ? ' open' : ''}">${esc(text)}</p>`
    + (open ? '' : `<button type="button" class="linkish dc-more-btn" data-dc-more="${esc(part)}">more</button>`);
}
/** What a person did with a round: Sent back, OK'd, Replaced (a newer version came before anyone answered), or nothing
    on the version asked about now. */
function decideRoundAnswer(r: DecRound, last: boolean): string {
  const a = decideAnswerOf(r, myActor(), true);
  if (a) return a.answer === 'no' ? 'Sent back' : "OK'd";
  return last ? '' : 'Replaced';
}
/** The right side of a page's OK, in parts: "Version N · date" with Jev's chip, the review history, What changed in vN,
    Your note on vN−1, Your points. Each part is left out when it has nothing to say. */
function decideRoundsHTML(st: DecState, it: NeedsEntry, rounds: DecRound[], version: number, notices: string[] = []): {sub: string; body: string} {
  let at = decideRoundAt(rounds, version);
  if (at < 0) at = rounds.length - 1;
  const now = rounds[at]!, me = myActor(), jevOpen = st.open.has(`${it.id}:jev`);
  const stage = decideStageName(it.task?.stage_id), date = shortDate(now.at);
  const sub = `<div class="dc-ver"><p class="dc-sub">${[esc(stage), `Version ${now.version}${date ? ` · ${esc(date)}` : ''}`].filter(Boolean).join(' · ')}</p>`
    + `${decideJevChip(now.jev, jevOpen)}</div>`
    + (jevOpen && now.jev.reason ? `<p class="dc-jev-why">${esc(now.jev.reason)}</p>` : '')
    // Why they are asked again, and a conflict their OK hands to the Builder: said before anything else is read.
    + notices.map(n => `<p class="dc-notice" role="note">${esc(n)}</p>`).join('');
  const parts: string[] = [];
  if (rounds.length > 1) {
    // One step per version: its date, Jev's chip and the answer it got. A click opens that version in the page viewer.
    parts.push(`<nav class="dc-hist" aria-label="Review history"><ol>${rounds.map((r, i) => {
      const answer = decideRoundAnswer(r, i === rounds.length - 1);
      return `<li><button type="button" class="dc-step${i === at ? ' now' : ''}" data-dc-ver="${esc(r.file_id)}@${r.version}"${i === at ? ' aria-current="step"' : ''}>`
        + `<b>v${r.version}</b><span class="dc-step-date">${esc(shortDate(r.at))}</span>${decideJevChip(r.jev, null)}`
        + (answer ? `<span class="dc-step-ans" data-a="${esc(answer)}">${esc(answer)}</span>` : '') + '</button></li>';
    }).join('')}</ol></nav>`);
  }
  if (now.changed.length) {
    parts.push(`<section class="dc-part"><h4>What changed in v${now.version}</h4><ul class="dc-changed-list">${now.changed.slice(0, 5).map(l => `<li>${esc(l)}</li>`).join('')}</ul></section>`);
  } else if (now.note) {
    parts.push(`<section class="dc-part"><h4>What changed in v${now.version}</h4>${decideClampHTML(st, it, 'changed', now.note)}</section>`);
  }
  const before = at > 0 ? rounds[at - 1]! : null, back = before ? decideAnswerOf(before, me) : undefined;
  if (before && back?.answer === 'no' && back.note) {
    parts.push(`<section class="dc-part"><h4>Your note on v${before.version} <span class="dc-when">${esc(shortDate(back.at))}</span></h4>${decideClampHTML(st, it, 'note', back.note)}</section>`);
  }
  if (now.answered.length) {
    parts.push(`<section class="dc-part"><h4>Your points</h4><ul class="dc-points">${now.answered.map(p =>
      `<li data-done="${p.done}"><span class="dc-mark" aria-label="${p.done ? 'Done' : 'Not done'}">${p.done ? '✓' : '✗'}</span><span><b>${esc(p.point)}</b> ${esc(p.why)}</span></li>`).join('')}</ul></section>`);
  }
  return {sub, body: parts.join('')};
}
/** A bot's question (Tasks spec "Bots ask people a question with choices", picture 2): why it matters, each choice
    with its line and "Recommended" on the one the bot recommends, then "Write to <bot>" and Send, which reaches the bot
    on the task and leaves the question open (AC-13), then the two answers Tico adds to every bot's question, on the
    row Skip joins: "I don't know, explain" and "Give it back" (AC-4, AC-5; answers.DONT_KNOW and GIVE_BACK). */
function decideBotAskHTML(st: DecState, it: NeedsEntry, asker: string): string {
  const busy = st.busy ? ' disabled' : '', brief = it.ask?.brief || null;
  const choices = it.ask?.choices || [], details = brief?.details || [];
  const why = brief?.why ? `<p class="dc-why">Why it matters: ${esc(brief.why)}</p>` : '';
  const choice = (c: string, i: number) => {
    const rec = brief?.recommend === c, line = details[i] || '';
    return `<button type="button" class="dc-choice${rec ? ' dc-rec' : ''}" data-dc-choice="${esc(c)}"${busy}>`
      + `<span class="dc-choice-head"><span class="dc-choice-name">${esc(c)}</span>${rec ? '<span class="dc-rec-tag">Recommended</span>' : ''}</span>`
      + (line ? `<span class="dc-choice-line">${esc(line)}</span>` : '') + '</button>';
  };
  return `${why}${choices.length ? `<div class="dc-choice-list">${choices.map(choice).join('')}</div>` : ''}
    <label class="dc-notes"><span>Write to ${esc(actorLabel(asker))}</span><textarea id="dc-other" data-dc-grow rows="${decideNoteRows()}" maxlength="4000" placeholder="Something else, or tell it what's wrong"></textarea></label>
    ${decideFilesHTML(st)}<div class="dc-send">${DEC_SENT === it.id ? `<span class="dc-sent" role="status">Sent to ${esc(actorLabel(asker))}</span>` : ''}${
      decideBtn('write', 'Send', false, ' data-dc-needs="#dc-other" disabled')}</div>${decideHeldHTML(it, '#dc-other')}
    <div class="dc-acts dc-fixed">${decideBtn('dont-know', DEC_DONT_KNOW, false, busy)}${decideBtn('give-back', DEC_GIVE_BACK, false, busy)}</div>`;
}
/** A file added or finished redraws only the files row, so the note keeps its focus, caret and text. */
function decideFilesPaint(st: DecState) {
  const row = $('.decide .dc-side [data-dc-attach]');
  if (row) row.outerHTML = decideFilesHTML(st); else decidePaintSide(st);
}
// ---- the note: its size, and the screenshots and files that go with it
const decidePhone = () => matchMedia('(max-width:760px)').matches;
/** The note starts at 7 lines on a wide screen, 1 on a phone (it grows as it is typed in). */
const decideNoteRows = () => decidePhone() ? 1 : 7;
/** The note grows with its text, up to the answer column's height (half the screen on a phone), then scrolls. */
function decideGrow(box: HTMLTextAreaElement) {
  const side = box.closest<HTMLElement>('.dc-side');
  const most = decidePhone() ? Math.round(window.innerHeight / 2) : Math.max(160, (side?.clientHeight || 600) - 160);
  box.style.height = '';
  const start = box.offsetHeight, need = box.scrollHeight + box.offsetHeight - box.clientHeight;
  box.style.height = `${Math.min(Math.max(start, need), most)}px`;
}
/** A paste counts for the note only while a note box has the focus. */
const eventInNote = (root: HTMLElement) => !!root.querySelector('[data-dc-attach]') && !!document.activeElement?.matches('#dc-notes, #dc-other, #dc-reply');
/** The files row under the note: each file with its state and ✕ (Retry when it failed), then 📎 Attach. */
function decideFilesHTML(st: DecState): string {
  const rows = st.files.map(f => `<li class="dc-file" data-dc-file="${f.key}" data-state="${f.state}"><span class="dc-file-name">${esc(f.file.name)}</span>`
    + `<span class="muted dc-file-state">${f.state === 'up' ? 'Uploading…' : f.state === 'failed' ? esc(f.error || 'Did not upload') : ''}</span>`
    + (f.state === 'failed' ? `<button type="button" class="linkish" data-dc="file-retry" data-dc-key="${f.key}">Retry</button>` : '')
    + `<button type="button" class="dc-file-x" data-dc="file-drop" data-dc-key="${f.key}" aria-label="Remove ${esc(f.file.name)}" title="Remove">✕</button></li>`).join('');
  const full = st.files.length >= DEC_FILES_MAX, it = decideCurrent(st);
  // An OK of exactly what was shown (okGive) carries only that OK: the files go with Send back, and the row says so.
  const bound = rows && it && decideKind(it) === 'ok' && st.ok.get(decideOkKey(it))?.state === 'ready'
    ? '<p class="muted dc-file-note">Approve sends no note or files; Send back sends both.</p>' : '';
  return `<div class="dc-files" data-dc-attach>${rows ? `<ul>${rows}</ul>` : ''}${bound}`
    + `<label class="dc-attach${full ? ' disabled' : ''}" title="Paste a screenshot, drop a file, or pick one"><input type="file" multiple data-dc-pick${full ? ' disabled' : ''} hidden>📎 Attach</label></div>`;
}
/** Files join the note on screen: each one uploads to the task's Files at once (the same upload as the task page). */
function decideFilesAdd(st: DecState, files: File[]) {
  const it = decideCurrent(st), taskId = it ? decideTaskId(it) : '';
  if (!it || !taskId || !files.length) return;
  const room = DEC_FILES_MAX - st.files.length;
  if (files.length > room) toast(`Up to ${DEC_FILES_MAX} files on a note`, true);
  for (const file of files.slice(0, Math.max(room, 0))) {
    const f: DecFile = {key: ++DEC_FILE_KEY, file, state: 'up', id: '', error: '', run: null};
    st.files.push(f);
    decideFileUpload(st, it.id, taskId, f);
  }
  decideFilesPaint(st);
}
function decideFileUpload(st: DecState, item: string, taskId: string, f: DecFile) {
  f.state = 'up'; f.error = '';
  f.run = (async () => {
    try {
      const fd = new FormData();
      fd.append('file', f.file, f.file.name);
      const response = await formFetch(`${API}/v2/tasks/${encodeURIComponent(taskId)}/files`, fd);
      const body = await response.json().catch(() => ({})) as {file_id?: string};
      if (!response.ok) throw new Error(apiError(body, response) || `Upload failed (${response.status})`);
      if (!body.file_id) throw new Error('The upload gave no file id');
      f.id = body.file_id; f.state = 'done';
    } catch (e) { f.state = 'failed'; f.error = errorMessage(e, 'Did not upload'); }
    if (DEC === st && st.at === item && st.files.includes(f) && !st.busy) decideFilesPaint(st);
  })();
}
/** Before a note goes: every file is up. While some upload it says "Uploading 1 of 2…"; a failed one is named, with
    Retry on its row, and nothing is sent (null). Else the files, in the order they were added. */
async function decideFilesReady(st: DecState, item: string): Promise<DecFile[] | null> {
  for (;;) {
    const files = [...st.files], up = files.filter(f => f.state === 'up');
    const failed = files.find(f => f.state === 'failed');
    if (failed) { st.error = `${failed.file.name} did not upload. Retry it, or remove it with ✕.`; return null; }
    if (!up.length) return files;
    st.error = '';
    const status = $('.decide [data-dc-status]');
    const text = `Uploading ${files.length - up.length + 1} of ${files.length}…`;
    if (status) status.textContent = text;
    else $('.decide .dc-side [data-key]')?.insertAdjacentHTML('beforeend', `<p class="muted dc-status" data-dc-status role="status">${esc(text)}</p>`);
    await Promise.race(up.map(f => f.run));
    if (DEC !== st || st.at !== item) return null;
  }
}
/** The note as it is sent: the words, then each file's name and id, so the bot's wake and `tico task show` list them. */
function decideWithFiles(text: string, files: DecFile[]): string {
  if (!files.length) return text;
  return `${text}\n\nFiles:\n${files.map(f => `- ${f.file.name} (${f.id})`).join('\n')}`;
}
/** A stage's typed answer: a choice is its buttons; a number a whole-number box with its range; text a box. */
function decideTypedHTML(typed: NonNullable<TaskReq['typed']>, choices: string[], busy: boolean): string {
  const off = busy ? ' disabled' : '';
  if (typed.type === 'choice')
    return `<div class="dc-choices">${choices.map(c => `<button type="button" class="ghost" data-dc-choice="${esc(c)}"${off}>${esc(c)}</button>`).join('')}</div>`;
  const send = `<div class="dc-acts">${decideBtn('other', 'Send', true, ' data-dc-needs="#dc-other" disabled')}</div>`;
  if (typed.type === 'number') {
    const {min, max} = typed;
    const range = min != null && max != null ? `${min}–${max}` : min != null ? `≥ ${min}` : max != null ? `≤ ${max}` : '';
    const bounds = (min != null ? ` min="${min}"` : '') + (max != null ? ` max="${max}"` : '');
    return `<label class="dc-notes"><span>${esc(range)}</span><input id="dc-other" type="number" step="1" inputmode="numeric"${bounds} aria-label="${esc(typed.question || typed.name)}" autocomplete="off"></label>${send}`;
  }
  return `<textarea id="dc-other" rows="3" maxlength="4000" aria-label="${esc(typed.question || typed.name)}"></textarea>${send}`;
}
/** What Give to…'s suggestions read: the whole task the screen loads (its body and area tags), else the item's brief
    while that read is on its way or failed. */
const decideGiveTask = (it: NeedsEntry): GiveToTask | null => decideAboutOf(it)?.task || it.task || null;
/** Draws the right side again once the task's read arrives, without losing Give to…: while its picker is open the side
    stays (redrawing would take the picker's button away, and a pick would land nowhere) and the field's next opening
    reads the whole task; a pick not yet given is kept on the new field. */
const decideGiveBtn = () => $('.decide button[data-give-to]') as HTMLButtonElement | null;
function decidePaintSideKeepGive(st: DecState, it: NeedsEntry) {
  const btn = decideGiveBtn();
  if (btn?.getAttribute('aria-expanded') === 'true') { GIVE_TO_TASKS.set(btn.dataset.giveTo || '', decideGiveTask(it)); return; }
  const to = decideTo();
  decidePaintSide(st);
  const now = decideGiveBtn(), e = to ? giveToEntries(to).find(x => giveToIs(x, to)) : undefined;
  if (now && e) giveToFieldSet(now, e);
}
/** Snooze, Give to… and Move back open their choices under the buttons. */
function decideMoreHTML(st: DecState, it: NeedsEntry): string {
  const busy = st.busy ? ' disabled' : '';
  if (st.more === 'snooze')
    return `<div class="dc-more">${DEC_SNOOZE.map(([k, label]) => decideBtn(`snooze-${k}`, label, false, busy)).join('')}</div>`;
  if (st.more === 'give')
    return `<div class="dc-more">${giveToFieldHTML({id: 'dc-to', label: 'Give to', task: decideGiveTask(it), current: it.task?.owner_id})}${decideBtn('give-to', 'Give', true, ' data-dc-needs="#dc-to" disabled')}</div>`;
  if (st.more === 'back') {
    const p = ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).find(x => x.id === it.task?.pipeline_id);
    const at = p ? p.stages.findIndex(s => s.id === it.task?.stage_id) : -1;
    const earlier = p && at > 0 ? p.stages.slice(0, at) : [];
    return `<div class="dc-more">${earlier.length ? earlier.map(s => `<button type="button" class="ghost" data-dc-stage="${esc(s.id || '')}"${busy}>${esc(s.name)}</button>`).join('') : '<span class="muted">No earlier stage</span>'}</div>`;
  }
  return '';
}
function decideBar(label: string, undo: (() => Promise<unknown>) | null) {
  const st = DEC, bar: HTMLElement | null = $('.decide .dc-bar');
  if (!st || !bar) return;
  clearTimeout(st.barTimer);
  const words: HTMLElement = $('span', bar), b: HTMLButtonElement = $('button', bar);
  words.textContent = label;
  b.hidden = !undo; b.disabled = false;
  b.onclick = undo ? async () => {
    b.disabled = true;
    try { await undo(); bar.hidden = true; await v2Refresh(); if (DEC === st) { decideSync(st, decideSource()); decidePaintHead(st); } }
    catch (e) { words.textContent = errorMessage(e, 'Not undone.'); }
  } : null;
  bar.hidden = false;
  st.barTimer = window.setTimeout(() => { bar.hidden = true; }, DEC_UNDO_MS);
}

// ---- an OK: what it shows
/** Reads what an OK is for, once, and holds it. `changed`: read again after the engine said it changed. */
async function decideOkLoad(st: DecState, it: NeedsEntry, changed: boolean) {
  const rid = decideRid(it), key = decideOkKey(it);
  const v: AskOkView = {state: 'loading', hash: null, html: '', pages: [], changed, error: '', missing: false, urls: [],
    page: decidePage(it), shows: decideShows(it), rounds: null, since: null, notices: []};
  const before = st.ok.get(key);
  if (before) decideOkLet(before);
  st.ok.set(key, v);
  if (DEC === st && st.at === it.id) { decidePaintBody(st); decidePaintSide(st); }
  try {
    // No grant (404): a stage's OK, which shows what its requirement names.
    const shown = await okShown(rid).catch((e: unknown) => { if (errorStatus(e) === 404) return null; throw e; });
    const [kind, ...rest] = (shown?.subject ?? '').split(':'), ref = rest.join(':');
    if (!shown) {
      const before = it.ask?.page ? await decideRoundsRead(v, it) : null;
      await decideShowsRead(v, it.ask?.shows || [], before);
    } else if (kind === 'email_send') {
      // The email's own preview is the one to show; its hash is the hash of that email, as frozen.
      const mail = await get<EmailSendPreview & {shown_hash?: string | null}>(`/v2/email/sends/${encodeURIComponent(ref)}`, {fresh: true});
      v.html = emailSendPreviewHTML(mail);
      v.hash = mail.preview_complete ? mail.shown_hash || null : null;
    } else if (kind === 'letter') {
      await decideLetterRead(v, ref, rid);
    } else if (kind === 'file') {
      v.html = await decideFileHTML(v, shown.content);
      v.hash = shown.shown_hash;
    } else if (kind === 'support') {
      v.html = await decideSupportHTML(shown.content);
      v.hash = shown.shown_hash;
    } else {
      v.html = decideContentHTML(shown.content);
      v.hash = shown.shown_hash;
    }
    v.state = shown ? 'ready' : 'none';
  } catch (e) { v.state = 'failed'; v.error = errorMessage(e, 'It did not load.'); }
  if (DEC === st && st.ok.get(key) === v && st.at === it.id) { decidePaintBody(st); decidePaintSide(st); }
}
/** Lets go of the bytes an OK's view held. */
function decideOkLet(v: AskOkView) {
  for (const url of v.urls.splice(0)) URL.revokeObjectURL(url);
}
/** A letter waiting for an OK, as its own preview gives it (GET /api/v2/letters/{id}): the proof and the hash of it. */
interface DecideLetter {
  approval_id?: string | null; status?: string; proof?: string | null; shown_hash?: string | null;
  recipient?: Record<string, unknown>; mail_class?: string | null; page_count?: number | null; total_micros?: number | null;
}
/** A letter's OK: the letter's own preview (its proof and the hash it is bound to, from one read), and the proof's bytes,
    read and drawn before Approve is on. */
async function decideLetterRead(v: AskOkView, id: string, rid: string) {
  const l = await get<DecideLetter>(`/v2/letters/${encodeURIComponent(id)}`, {fresh: true});
  if (l.approval_id !== rid || !l.shown_hash || !/^\/api\/v2\/files\/[A-Za-z0-9_-]+$/.test(String(l.proof || ''))) throw new Error('The proof did not load.');
  const r = await fetch(String(l.proof), {credentials: 'same-origin', cache: 'no-store'});
  if (!r.ok) throw new Error('The proof did not load.');
  const url = URL.createObjectURL(new Blob([await r.arrayBuffer()], {type: 'application/pdf'}));
  v.urls.push(url);
  const to = Object.values(l.recipient || {}).filter(x => typeof x === 'string' && x.trim()).map(x => esc(String(x)));
  const terms = [l.mail_class ? String(l.mail_class).replace(/_/g, ' ') : '', l.page_count ? `${l.page_count} ${l.page_count === 1 ? 'page' : 'pages'}` : '',
    l.total_micros != null ? `$${(l.total_micros / 1e6).toFixed(2)}` : ''].filter(Boolean);
  v.html = `<p class="dc-addr">${to.join('<br>')}</p><p class="dc-line">${esc(terms.join(' · '))}</p>`
    + `<iframe class="dc-proof" src="${esc(url)}" title="Proof"></iframe><a href="${esc(url)}" target="_blank" rel="noopener">Proof PDF</a>`;
  v.hash = l.shown_hash;
}
// A file version shown in place up to this size; a bigger one, or one of another type, opens from its link.
const DEC_FILE_INLINE = 20 * 1024 * 1024;
/** A file at the version an OK pinned (`{file, version, name}`, backend/file_reviews.py freeze): its bytes read from that
    exact version (GET /api/v2/files/{id}/versions/{n}) and drawn, or a link that opens exactly that version. */
async function decideFileHTML(v: AskOkView, content: unknown): Promise<string> {
  const c = (content && typeof content === 'object' ? content : {}) as {file?: unknown; version?: unknown; name?: unknown};
  const fid = typeof c.file === 'string' ? c.file : '', version = Number(c.version);
  if (!fid || !Number.isInteger(version) || version < 1) throw new Error('The file did not load.');
  const src = `${API}/v2/files/${encodeURIComponent(fid)}/versions/${version}`, name = String(c.name || fid);
  const r = await fetch(src, {credentials: 'same-origin', cache: 'no-store'});
  if (!r.ok) { void r.body?.cancel(); throw new Error('The file did not load.'); }
  const type = (r.headers.get('content-type') || '').split(';')[0]!.trim().toLowerCase(), size = Number(r.headers.get('content-length') || 0);
  const text = /^text\/|\/(json|markdown|x-markdown|csv|xml|yaml)$/.test(type), image = type.startsWith('image/') && type !== 'image/svg+xml';
  const head = `<h3 class="dc-title">${esc(name)}</h3>`, link = `<a href="${esc(src)}" target="_blank" rel="noopener">${esc(name)}</a>`;
  if (size > DEC_FILE_INLINE || !(text || image || type === 'application/pdf')) {
    void r.body?.cancel();
    const media = type.startsWith('video/') ? `<video class="dc-media" controls preload="metadata" src="${esc(src)}"></video>`
      : type.startsWith('audio/') ? `<audio controls preload="metadata" src="${esc(src)}"></audio>` : '';
    return head + media + link;
  }
  if (text) {
    const body = await r.text();
    return head + (/markdown/.test(type) ? `<div class="md">${md(body)}</div>` : `<pre class="dc-raw">${esc(body)}</pre>`);
  }
  const url = URL.createObjectURL(new Blob([await r.arrayBuffer()], {type}));
  v.urls.push(url);
  return head + (image ? `<img class="dc-media" src="${esc(url)}" alt="${esc(name)}">` : `<iframe class="dc-proof" src="${esc(url)}" title="${esc(name)}"></iframe>`) + link;
}
/** What an OK is for, any shape: text as Markdown; an object with a subject and a body as a message, then every other
    field it has (the hash covers them all); else its fields. */
function decideContentHTML(content: unknown): string {
  if (content == null || content === '') return '';
  if (typeof content === 'string') return `<div class="md">${md(content)}</div>`;
  if (typeof content === 'object' && !Array.isArray(content)) {
    const o = content as Record<string, unknown>;
    const textKey = typeof o.body === 'string' ? 'body' : typeof o.text === 'string' ? 'text' : '';
    const titleKey = typeof o.subject === 'string' ? 'subject' : typeof o.title === 'string' ? 'title' : '';
    if (textKey) {
      const rest = Object.fromEntries(Object.entries(o).filter(([k]) => k !== textKey && k !== titleKey));
      return `${titleKey ? `<h3 class="dc-title">${esc(String(o[titleKey]))}</h3>` : ''}<div class="md">${md(String(o[textKey]))}</div>`
        + (Object.keys(rest).length ? `<pre class="dc-raw">${esc(JSON.stringify(rest, null, 2))}</pre>` : '');
    }
  }
  return `<pre class="dc-raw">${esc(JSON.stringify(content, null, 2))}</pre>`;
}
/** A report to Tico support (backend/support.py ask_report): the message exactly as it goes, and, unless the person
    left them out, the version and install ID that go with it (GET /api/v2/support/compose, what the send adds). The
    message's own hash (`content_sha256`) is checked against it by the server, so it is not shown. */
async function decideSupportHTML(content: unknown): Promise<string> {
  const c = (content && typeof content === 'object' ? content : {}) as {message?: unknown; include_ids?: unknown};
  if (typeof c.message !== 'string') throw new Error('The report did not load.');
  let ids = '';
  if (c.include_ids !== false) {
    const d = await get<{version?: string; install_id?: string; to?: string}>('/v2/support/compose', {fresh: true});
    ids = [d.version ? `Version ${d.version}` : '', d.install_id ? `Install ID ${d.install_id}` : ''].filter(Boolean).map(x => esc(x)).join(' · ');
    if (d.to) ids += `${ids ? ' · ' : ''}To ${esc(d.to)}`;
  }
  return `<pre class="dc-raw">${esc(c.message)}</pre>${ids ? `<p class="dc-line">${ids}</p>` : ''}`;
}
/** A page at the exact version an OK of it is for (`page:<id>@<n>`, a page requirement): its HTML read from that version
    and drawn sandboxed, as the page viewer draws it. A version that is not HTML (one attached before pages for an OK had
    to be, often Markdown) is drawn as Markdown, so it reads as a document rather than one run-on paragraph. */
async function decidePageHTML(fid: string, version: number, mark?: (html: string) => string): Promise<string> {
  const file = await viewFile(fid, 'page.html', version);
  const text = await file.blob.text();
  if (!text.replace(/^[\uFEFF\s]+/, '').startsWith('<')) return `<div class="md dc-page-md">${md(text)}</div>`;
  const doc = htmlPreviewDoc(mark ? mark(text) : text);
  return `<iframe class="html-view dc-page" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="Version ${version}" srcdoc="${esc(doc)}"></iframe>`;
}
/** A page that comes back: its rounds, and, when the viewer answered an earlier version, "Changes since vK" (K the
    last version they answered; on when they sent it back) with the page viewer's own line diff from vK. Returns vK's
    lines, for the margin marks, or null. Rounds that do not load leave the right side as it was (the stage's line). */
async function decideRoundsRead(v: AskOkView, it: NeedsEntry): Promise<string[] | null> {
  const taskId = decideTaskId(it), rid = decideRid(it), page = it.ask?.page;
  if (!taskId || !page) return null;
  try {
    const r = await get<{rounds?: DecRound[]; notices?: string[]}>(`/v2/tasks/${encodeURIComponent(taskId)}/asks/${encodeURIComponent(rid)}/rounds`, {fresh: true});
    v.rounds = Array.isArray(r.rounds) && r.rounds.length ? r.rounds : null;
    v.notices = Array.isArray(r.notices) ? r.notices.filter(n => typeof n === 'string' && n.trim()) : [];
  } catch { v.rounds = null; }
  const now = decideRoundAt(v.rounds, page.version);
  if (!v.rounds || now < 0) return null;
  const me = myActor();
  const base = v.rounds.slice(0, now).reverse().find(x => decideAnswerOf(x, me));
  if (!base) return null;
  try {
    const [a, b] = await Promise.all([[base.file_id, base.version], [page.file_id, page.version]].map(async ([f, n]) =>
      pageText(await (await viewFile(String(f), 'page.html', Number(n))).blob.text())));
    v.since = {version: base.version, on: decideAnswerOf(base, me)?.answer === 'no', same: a === b,
      diff: a === b ? '' : tfDiffHTML(a ?? '', b ?? ''), marked: 0};
    return (a ?? '').split('\n');
  } catch { return null; }
}
/** The index of the round a version is (the newest with that number), -1 when none is. */
const decideRoundAt = (rounds: DecRound[] | null, version: number) => (rounds || []).map(x => x.version).lastIndexOf(version);
/** A person's answer to a round: `who`'s own when given, else the newest by any person. */
function decideAnswerOf(r: DecRound, who: string, anyPerson = false) {
  const mine = [...r.answers].reverse().find(a => a.by === who);
  return mine || (anyPerson ? [...r.answers].reverse().find(a => a.by.startsWith('human:')) : undefined);
}
/** A stage's OK shows what the requirement names: pages in full (a page requirement's at its pinned version), an email
    as frozen, a file at its version, the rest by name. One that does not load leaves Approve off (`missing`). */
async function decideShowsRead(v: AskOkView, shows: string[], before: string[] | null = null) {
  const parts: string[] = [];
  for (const ref of shows) {
    const [kind, ...rest] = ref.split(':'), id = rest.join(':'), pinned = /^([^@]+)@(\d+)$/.exec(id);
    try {
      if (kind === 'page' && pinned) {
        // The page asked about, with the blocks not in the version the viewer last answered marked in its margin.
        const asked = `${pinned[1]}@${pinned[2]}` === v.page && before && v.since;
        parts.push(await decidePageHTML(pinned[1]!, Number(pinned[2]), asked ? (html => {
          const out = pageMarkChanged(html, before);
          v.since!.marked = out.marked;
          return out.html;
        }) : undefined));
        continue;
      }
      if (kind === 'page') { v.pages.push((await get<import('../types/api').GetPageResponse>('/v2/pages/' + encodeURIComponent(id), {fresh: true})).page); continue; }
      if (kind === 'email') { parts.push(emailSendPreviewHTML(await get<EmailSendPreview>('/v2/email/sends/' + encodeURIComponent(id), {fresh: true}))); continue; }
      if (kind === 'file' && pinned) { parts.push(await decideFileHTML(v, {file: pinned[1], version: Number(pinned[2])})); continue; }
      if (kind === 'browser' && id.includes('/')) { parts.push(decideBrowserSlot(id)); continue; }
    } catch (e) { v.missing = true; parts.push(`<p class="err">${esc(errorMessage(e, `${ref} did not load.`))}</p>`); continue; }
    parts.push(`<p class="dc-ref"><code>${esc(ref)}</code></p>`);
  }
  v.html = parts.join('');
}

// ---- acting
const decideNote = (sel: string) => ($(sel) as HTMLTextAreaElement | null)?.value.trim() || '';
const decideTo = () => ($('#dc-to') as HTMLInputElement | null)?.value || '';
/** Runs one answer for the item on screen: busy while it is on its way, then the bar and the next item. A failure stays
    on the item with its words. */
async function decideDo(st: DecState, it: NeedsEntry, label: string, run: () => Promise<unknown>, undo: ((r: unknown) => (() => Promise<unknown>) | null) | null = null) {
  if (st.busy) return;
  st.busy = true; st.error = '';
  decidePaintSide(st);
  try {
    const r = await run();
    if (DEC !== st) return;
    decideBar(label, undo ? undo(r) : null);
    decideAfter(st, it.id);
    void v2Refresh();
    const tid = decideTaskId(it); if (tid) taskAskSettled(tid);
  } catch (e) {
    if (DEC !== st) return;
    st.busy = false; st.error = taskRefusedOf(e)?.detail || errorMessage(e, 'Not saved.');
    decidePaintSide(st);
  }
}
/** 'Reviewer 1', 'Reviewer 1 and Other', 'A, B and C'. */
function decideNames(whom: string[]): string {
  const names = whom.map(actorLabel);
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0] || '';
}
/** The stuck card's fix (task #392): let exactly the person the card names see the private task, and move it on, in
    one write (a move with `let_see`). When the task now waits on someone else nobody is added: the refusal stays on
    screen and the list is read again, so the card names who it is now. */
async function decideLetSee(st: DecState, it: NeedsEntry, whom: string[]) {
  await decideDo(st, it, `${decideNames(whom)} can see it now`, () => taskLetSee(decideTaskId(it), whom, it.task?.version ?? null));
  if (DEC !== st || st.at !== it.id || !st.error) return;
  const said = st.error;
  await v2Refresh();
  if (DEC !== st || st.at !== it.id) return;
  decideSync(st, decideSource());
  st.error = said;
  decidePaintSide(st);
}
/** A page requirement's ask: the page attached now (`<file id>@<version>`), '' when there is none. */
const decidePage = (it: NeedsEntry): string => it.ask?.page ? `${it.ask.page.file_id}@${it.ask.page.version}` : '';
/** The page on screen, which an answer is for. A refresh that brought another page leaves the screen as it was read:
    that page is read and shown with "It changed. Look again." and nothing is sent (null). */
async function decideShownPage(st: DecState, it: NeedsEntry): Promise<string | undefined | null> {
  const v = st.ok.get(decideOkKey(it));
  if (!v || v.page === decidePage(it)) return v?.page || undefined;
  await decideOkLoad(st, it, true);
  return null;
}
const decideUndoAnswer = (r: unknown) => { const id = (r as TaskMoved | null)?.answer_id; return id ? () => answerUndo(id) : null; };
const decideUndoReopen = (taskId: string) => () => () => taskMove(taskId, 'reopen', null);
/** Approve: the person's own OK of exactly what is on screen. A change since it was read is shown, never OK'd. */
async function decideApprove(st: DecState, it: NeedsEntry) {
  const rid = decideRid(it), v = st.ok.get(decideOkKey(it)), taskId = it.task_id;
  if (!v || !taskId || st.busy) return;
  if (v.state === 'none') {
    const page = await decideShownPage(st, it);
    if (page === null) return;
    const files = await decideFilesReady(st, it.id);
    if (!files) { decidePaintSide(st); return; }
    await decideDo(st, it, 'Approved', () => taskAnswer(taskId, rid, 'ok', it.task?.version ?? null, decideWithFiles(decideNote('#dc-notes'), files) || undefined, page), decideUndoAnswer);
    return;
  }
  const hash = v.state === 'ready' ? v.hash : null;
  if (!hash) return;
  st.busy = true; st.error = '';
  decidePaintSide(st);
  try {
    const r = await okGive(taskId, rid, hash);
    if (DEC !== st) return;
    decideBar('Approved', decideUndoAnswer(r));
    decideAfter(st, it.id);
    void v2Refresh();
  } catch (e) {
    if (DEC !== st) return;
    st.busy = false;
    const code = (e as Partial<ApiFailure> | null)?.body?.error;
    if (code === 'task_changed') { await decideOkLoad(st, it, true); return; }     // the new content, and a new click
    st.error = taskRefusedOf(e)?.detail || errorMessage(e, 'Not approved.');
    decidePaintSide(st);
    if (code === 'task_requirement') void v2Refresh();     // not asked any more: an ask that took its place comes in

  }
}
async function decideClick(st: DecState, ev: MouseEvent) {
  const el = eventElement(ev), b = el?.closest<HTMLElement>('[data-dc], [data-dc-choice], [data-dc-stage], [data-dc-walk], [data-dc-jev], [data-dc-more], [data-dc-ver], [data-dc-since]');
  // A link in the task's text to a place in the app: there, as Go to goes (the item stays in Needs you).
  const link = el?.closest<HTMLAnchorElement>('.dc-thing a[href]'), there = link ? decideAppHref(link.getAttribute('href') || '') : '';
  if (there && !ev.defaultPrevented && ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); decideGoThere(st, there); return; }
  if (!el?.closest('.dc-where')) decideMenu(false);
  if (!b || (b as HTMLButtonElement).disabled) return;
  const at = decideCurrent(st);
  if (at && (b.dataset.dcMore != null || b.hasAttribute('data-dc-jev'))) {
    // Jev's reason, or a part cut to 2 lines: shown on the right side of this item until it is left.
    const key = b.dataset.dcMore != null ? `${at.id}:more:${b.dataset.dcMore}` : `${at.id}:jev`;
    if (st.open.has(key) && b.dataset.dcMore == null) st.open.delete(key); else st.open.add(key);
    decidePaintSide(st);
    ($(b.dataset.dcMore != null ? '.decide .dc-side .dc-clamp.open' : '.decide .dc-side [data-dc-jev]') as HTMLElement | null)?.focus?.({preventScroll: true});
    return;
  }
  if (b.dataset.dcVer) {
    // A step of the review history opens that version in the page viewer.
    const [fid = '', n = ''] = b.dataset.dcVer.split('@');
    decideStop(); location.hash = `${pageHref(fid)}?v=${n}`; return;
  }
  if (at && b.hasAttribute('data-dc-since')) {
    const v = st.ok.get(decideOkKey(at));
    if (v?.since) { v.since.on = (b as HTMLInputElement).checked; decidePaintBody(st); }
    return;
  }
  // A row of the crumb's menu: that place's walk.
  if (b.dataset.dcWalk != null) { await decideWalkTo(b.dataset.dcWalk, b.dataset.dcPipe || ''); return; }
  const what = b.dataset.dc || '';
  if (what.startsWith('sug-')) { await decideSugAct(st, what.slice(4), b.closest<HTMLElement>('[data-sug]')?.dataset.sug || ''); return; }
  if (what === 'where') { decideMenu(!!$('.decide .dc-menu')?.hidden); return; }
  if (what === 'voice') { DEC_VOICE = !DEC_VOICE; decidePaintHead(st); $(DEC_VOICE ? '.decide .dc-wave' : '.decide .dc-mic')?.focus(); return; }
  if (what === 'close') { decideStop(); return; }
  if (what === 'prev' || what === 'next') { decideStep(st, what === 'prev' ? -1 : 1); return; }
  if (what === 'skip') { decideSkip(st); return; }
  if (what === 'go' && b.dataset.dcHref) { decideGoThere(st, b.dataset.dcHref); return; }
  if (what === 'undo') return;            // the bar's own handler
  if (what === 'file-drop' || what === 'file-retry') {
    const f = st.files.find(x => String(x.key) === b.dataset.dcKey), at = decideCurrent(st);
    if (!f || !at) return;
    if (what === 'file-drop') st.files = st.files.filter(x => x !== f);
    else decideFileUpload(st, at.id, decideTaskId(at), f);
    if (st.error && !st.files.some(x => x.state === 'failed')) st.error = '';
    decidePaintSide(st); return;
  }
  const it = decideCurrent(st);
  if (!it) return;
  // Every answer is for the item on screen, and only that one.
  const shown = b.closest<HTMLElement>('[data-key]')?.dataset.key;
  if (shown && shown !== it.id) return;
  const taskId = it.task_id || it.task?.id || '', rid = decideRid(it), ver = it.task?.version ?? null;
  if (b.dataset.dcChoice != null) { await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, b.dataset.dcChoice!, ver), decideUndoAnswer); return; }
  if (b.dataset.dcStage) { await decideDo(st, it, 'Moved back', () => taskMove(taskId, b.dataset.dcStage!, ver)); return; }
  switch (what) {
    case 'approve': await decideApprove(st, it); return;
    case 'sendback': {
      // Send back still needs words: with none it sends nothing and asks for them in Notes. It waits for the files to be
      // up, and sends nothing while one failed.
      const note = decideNote('#dc-notes');
      if (!note) {
        const box = $('#dc-notes') as HTMLTextAreaElement | null;
        DEC_ASK_NOTE = it.id;
        if (box) { box.placeholder = 'Say what to change'; box.focus(); }
        return;
      }
      const files = await decideFilesReady(st, it.id);
      if (!files) { if (DEC === st) decidePaintSide(st); return; }
      const page = await decideShownPage(st, it);
      if (page !== null) await decideDo(st, it, 'Sent back', () => taskAnswer(taskId, rid, 'no', ver, decideWithFiles(note, files), page), decideUndoAnswer);
      return;
    }
    case 'other': {
      const text = decideNote('#dc-other');
      if (!text) return;
      const files = await decideFilesReady(st, it.id);
      if (!files) { if (DEC === st) decidePaintSide(st); return; }
      await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, decideWithFiles(text, files), ver), decideUndoAnswer);
      return;
    }
    case 'reply': await decideReplySend(st, it, '#dc-reply'); return;
    case 'askback': await decideReplySend(st, it, '#dc-other'); return;
    case 'write': await decideReplySend(st, it, '#dc-other', decideRid(it)); return;
    case 'dont-know': await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, DEC_DONT_KNOW, ver), decideUndoAnswer); return;
    case 'give-back': await decideDo(st, it, 'Given back', () => taskAnswer(taskId, rid, DEC_GIVE_BACK, ver), decideUndoAnswer); return;
    case 'task': decideStop(); void taskModalOpen(taskId, undefined, {direct: true}); return;
    case 'done': await decideDo(st, it, 'Done', () => taskDone(taskId, ver), decideUndoReopen(taskId)); return;
    case 'again': await decideDo(st, it, 'Tried again', () => taskTryAgain(taskId)); return;
    case 'let-see': { const whom = it.task?.turn?.share || []; if (whom.length) await decideLetSee(st, it, whom); return; }
    case 'close-task': await decideDo(st, it, 'Closed', () => taskMove(taskId, 'closed', ver), decideUndoReopen(taskId)); return;
    case 'snooze': case 'give': case 'back':
      st.more = st.more === what ? '' : what; decidePaintSide(st); return;
    case 'snooze-hour': case 'snooze-day': case 'snooze-week': {
      // Every choice is a moment (the server keeps a date-time to the minute): an hour on, or 8:00 tomorrow or seven
      // days on by this person's clock. Not a bare date, which the server reads as midnight in the team's zone: late in
      // the evening west of that zone, that midnight has already passed.
      const day = new Date();
      day.setDate(day.getDate() + (what === 'snooze-day' ? 1 : 7));
      day.setHours(8, 0, 0, 0);
      const until = what === 'snooze-hour' ? new Date(Date.now() + 3600_000).toISOString() : day.toISOString();
      await decideDo(st, it, 'Snoozed', async () => { const r = await taskSnooze(taskId, until, ver); st.snoozed++; return r; },
        () => async () => { await taskUnsnooze(taskId); st.snoozed = Math.max(0, st.snoozed - 1); });
      return;
    }
    case 'give-to': { const to = decideTo(); if (to) await decideDo(st, it, `Given to ${actorLabel(to)}`, () => taskReassign(taskId, to, ver)); return; }
    case 'open': decideOpenWhere(it); return;
    case 'fix': if (it.href) { decideStop(); location.hash = it.href; } return;
  }
}
/** Open: a mention's thread (or its task), a cause's first task. */
function decideOpenWhere(it: NeedsEntry) {
  decideStop();
  if (it.kind === 'mention' && !it.task_id && it.conversation_id && typeof threadHref === 'function') { location.hash = threadHref(it.conversation_id); return; }
  const id = it.task_id || it.task_ids?.[0] || it.task?.id;
  if (id) void taskModalOpen(id, undefined, {direct: true});
}
