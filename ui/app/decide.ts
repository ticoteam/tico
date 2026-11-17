/* ui/app/decide.ts — Flow, the ask screen (docs/dev/pipelines-v3.md, S3): one item of Needs you at a time, over the page. A
   modal on a wide screen, the whole screen on a phone. The top bar is where you are (All mine / a pipeline / a stage, a
   menu), Flow's mark, the mic, the undo notice (what was just done), the next ask Tico is still thinking about, then ‹ › ✕ (‹ › on a wide screen
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
type AskKind = 'ok' | 'question' | 'stuck' | 'fix' | 'mention';
/** What an OK shows, as it was read: `hash` is the hash of exactly this content, the one Approve sends. `none` is an OK
    with no bound grant (a stage's OK of a page): Approve is a plain answer. `missing`: a part of what it shows did not
    load, so Approve stays off. `urls`: the object URLs of bytes read for it (a proof, a file), let go with it. `page`: on
    a page requirement, the page this content is (`<file id>@<version>`), the one an answer is for. `shows`: the refs it
    was read from. Held per ask (decideOkKey): a stage's requirement is the same on every task in the stage. */
interface AskOkView {
  state: 'loading' | 'ready' | 'none' | 'failed';
  /** What it OKs, as its grant's subject names it (finance, email_send, spec…); '' for a stage's OK with no grant. */
  kind?: string;
  /** The title its content gives, when it gives one: what its own tab on a phone is called. */
  title?: string;
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
/** The person's note on the version before, sent back: a chat message of theirs above the ask. */
interface DecNote { version: number; at: string; text: string }
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
/** "Changes since vK", in the version menu (decideRoundsHTML): K, whether it is on (off when the page opens: it opens
    clean), whether the text is the same, and what the pane shows when it is on: the page with each block not in vK
    marked in its margin (pageMarkChanged), and how many blocks that marks. */
interface DecSince {
  version: number;
  on: boolean;
  same: boolean;
  html: string;
  marked: number;
}
/** The open screen: the walk's order, the item on screen, what each OK showed, what was answered here. */
interface DecState {
  order: string[];
  at: string | null;
  items: Map<string, NeedsEntry>;
  ok: Map<string, AskOkView>;
  gone: Set<string>;
  more: '' | 'snooze' | 'give' | 'back' | 'ask';
  busy: boolean;
  error: string;
  barTimer: number;
  opener: HTMLElement | null;
  off: (() => void)[];
  /** Where the walk is: a stage ('' for none; DEC_OTHER, what is in no stage), else a pipeline ('' with no stage walks
      all of it). */
  stage: string;
  pipeline: string;
  /** The asks the person snoozed for themselves (GET /v2/needs-you/snoozed), read on the end screen; null until then.
      `snoozedOpen`: their list is open under "N snoozed". */
  snoozedAsks: NeedsEntry[] | null;
  snoozedOpen: boolean;
  /** Snooze's menu is on Other: the time box shows. */
  snzOther: boolean;
  /** The items shown this visit: they keep their place when Tico's scores reorder the rest (decideScoreOrder). */
  seen: Set<string>;
  /** Screenshots and files added to the note of the item on screen (pasted, dropped or picked with 📎). */
  files: DecFile[];
  /** What was opened on the right side, per item: `<item id>:vmenu` (the version menu), `<item id>:more:<part>` (a
      part cut to 2 lines, shown in full). */
  open: Set<string>;
  /** A bot's or a team's Flow mark (decideBotsFlow): only those bots' items (`bots`, actors; empty for all), the crumb's
      name for them, and who All done's ping asks. `asking`: the ping went out, so All done says "Asking your bots". */
  bots: string[];
  botsName: string;
  ping: DecPing;
  asking: boolean;
}
/** Who All done's "Ping your bots" asks (POST /v2/flow/ping): the person's reports, one bot, or a team's bots. */
interface DecPing {scope: 'reports' | 'bot' | 'group'; id?: string}
/** A file added to a note: uploaded to the task's Files at once; its name and id end the note that is sent. */
interface DecFile {
  key: number;
  file: File;
  state: 'up' | 'done' | 'failed';
  id: string;
  error: string;
  run: Promise<void> | null;
}
const DEC_UNDO_MS = 5000;
const DEC_BAR_FADE_MS = 400;     // the undo fades out over this long (decide.css .dc-bar-out)
const DEC_CALM_MS = 400;
let DEC_CALM_TIMER = 0;
let DEC_ASK_NOTE = '';                      // the item whose empty Send back asked for a note: Notes keeps saying so
// The two answers Tico adds to every bot's question to a person (backend/answers.py DONT_KNOW, GIVE_BACK).
const DEC_DONT_KNOW = "I don't know, explain", DEC_GIVE_BACK = 'Give it back';
const DEC_FILES_MAX = 10;                   // files on one note
let DEC_FILE_KEY = 0;
let DEC_VOICE = false;                      // the mic is on: voice is not built, so the bar says it is coming
const DEC_START_MS = 600;                   // "Getting ready to flow" over the first item, which is usable under it
/** Snooze's choices, one menu for an ask and for your own task: exact spans from now, then Other (a time of your own). */
const DEC_SNOOZE: [string, string][] = [['hour', '1 hour'], ['day', '1 day'], ['week', '1 week'], ['month', '1 month']];
/** Snooze, drawn as the mic and the clip are: an alarm clock. */
const DEC_SNOOZE_ICON = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2.5 1.5M4.5 4.5 2.8 6.2M19.5 4.5l1.7 1.7"/></svg>';
/** ↩ for the undo in the top bar, drawn as the mic and the clip are. */
const DEC_UNDO_ICON = '<svg class="dc-bar-i" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>';
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
const decideRid = (it: NeedsEntry) => it.requirement_id || it.id;
const decideTaskId = (it: NeedsEntry) => it.task_id || it.task?.id || '';
/** One ask: its task and its requirement. A stage's requirement ("OK Chris") is the same on every task in the stage, so
    the requirement alone would show one task's page on another's ask. */
const decideOkKey = (it: NeedsEntry) => `${decideTaskId(it)}\n${decideRid(it)}`;
/** A stage's OK that names nothing to show: the task's page from an earlier stage (the QA page at OK release, #3784),
    when the server gave it (only to a reader who may open it). */
const decideEarlier = (it: NeedsEntry) => {
  const e = it.ask?.earlier;
  return e?.ref && e.file_id && e.version && !e.removed && !it.ask?.page && !(it.ask?.shows || []).length
    ? {ref: e.ref, file_id: e.file_id, version: e.version, stage: e.stage || ''} : null;
};
/** What an OK shows: its own refs, else the earlier stage's page. */
const decideShowRefs = (it: NeedsEntry): string[] => { const e = decideEarlier(it); return e ? [e.ref] : it.ask?.shows || []; };
const decideShows = (it: NeedsEntry) => decideShowRefs(it).join('\n');
/** What is held for an ask was read from other refs than it has now (a new page version, another page). */
const decideOkStale = (v: AskOkView, it: NeedsEntry) => v.page !== decidePage(it) || v.shows !== decideShows(it);
const decideLive = (st: DecState) => st.order.filter(k => !st.gone.has(k) && st.items.has(k));
const decideCurrent = (st: DecState) => st.at && !st.gone.has(st.at) ? st.items.get(st.at) || null : null;
/** The items a walk takes: a stage's only that stage's, a pipeline's those in its stages. */
// A bot's or a team's walk (its ping not the reports') takes only those bots' items: none for a team with no bots.
const decideInWalk = (st: DecState, it: NeedsEntry) => (st.ping.scope === 'reports' || st.bots.includes(needsBotOf(it) || '')) && (st.stage
  ? (st.stage === DEC_OTHER ? !decideStageName(needsStageOf(it)) : needsStageOf(it) === st.stage)
  : !st.pipeline || decidePipelineOf(needsStageOf(it))?.id === st.pipeline);
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
      <div class="dc-sayw"><span class="dc-say"></span><span class="dc-vh" aria-live="polite"></span></div>
      <span class="dc-up"></span><span class="dc-bar" role="status" hidden></span>
      <span class="dc-nav">
        <button type="button" class="dc-icon dc-wide" data-dc="prev" aria-label="Previous" title="Previous (←)">‹</button>
        <button type="button" class="dc-icon dc-wide" data-dc="next" aria-label="Next" title="Next (→)">›</button>
        <button type="button" class="dc-icon" data-dc="close" aria-label="Close" title="Close (Esc)">✕</button>
      </span>
    </header>
    <div class="dc-main"><div class="dc-thing"></div><div class="dc-side"></div></div>
  </section>`;
  const root: HTMLElement = $('.decide', d)!;
  root.addEventListener('click', (ev: MouseEvent) => { if (DEC) void decideClick(DEC, ev); });
  root.addEventListener('input', (ev: Event) => {
    const box = eventElement(ev) as HTMLTextAreaElement | HTMLInputElement | null;
    if (!DEC || !box?.matches('textarea, input')) return;
    if (box instanceof HTMLTextAreaElement && box.matches('[data-dc-grow]')) decideGrow(box);
    if (box instanceof HTMLInputElement && box.id === 'dc-snz-at') box.setCustomValidity('');
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
    // Snooze's menu: Enter in Other's time box snoozes until it; ↑ ↓ move between its choices.
    const snz = DEC?.more === 'snooze' ? eventElement(ev)?.closest('.dc-snzw') : null;
    if (snz && ev.key === 'Enter' && eventElement(ev)?.matches('input#dc-snz-at')) {
      ev.preventDefault(); snz.querySelector<HTMLButtonElement>('[data-dc="snooze-at"]')?.click(); return;
    }
    // Snooze's and a question's ⋯ menu: ↑ ↓ move between their choices (from the button: the first, or the last).
    const menu = snz || (DEC?.more === 'ask' ? eventElement(ev)?.closest('.dc-askw') : null);
    if (menu && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') && eventElement(ev)?.matches('[role="menuitem"], [aria-haspopup="menu"]')) {
      ev.preventDefault();
      const all = [...menu.querySelectorAll<HTMLElement>('.dc-snz [role="menuitem"], #dc-snz-at')], down = ev.key === 'ArrowDown';
      const i = all.indexOf(document.activeElement as HTMLElement);
      all[i < 0 ? (down ? 0 : all.length - 1) : (i + (down ? 1 : all.length - 1)) % all.length]?.focus();
      return;
    }
    if (!DEC || ev.metaKey || ev.ctrlKey || ev.altKey || eventElement(ev)?.closest('input, textarea, select')) return;
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); decideStep(DEC, -1); }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); decideStep(DEC, 1); }
  });
  // The pinned row's height changes with the width (a row that wraps) and the safe area.
  window.addEventListener('resize', () => { if (d?.open) decideBarRoom(); });
  // Esc with the menu open closes the menu only.
  d.addEventListener('cancel', ev => { ev.preventDefault(); if (!decideMenu(false) && !(DEC && (decideSnoozeClose(DEC) || decideAskMoreClose(DEC)))) decideStop(); });
  d.addEventListener('close', () => { if (!d.open) decideStop(); });
  document.body.appendChild(d);
  return d;
}
/** A button that needs words (Send back, Something else, Add rule) is on once there are some. */
function decideNeedsText(root: HTMLElement) {
  for (const b of root.querySelectorAll<HTMLButtonElement>('[data-dc-needs]')) {
    const boxes = [...root.querySelectorAll<HTMLTextAreaElement | HTMLInputElement>(b.dataset.dcNeeds!)];
    b.disabled = !boxes.some(box => box.value.trim()) || !!DEC?.busy;
  }
}
/** Opens the item `id`, or the first of the list with none. `items` is the list to walk, Needs you as the page shows
    it by default; `stage` walks only that stage's items, `pipeline` only its stages' items, and an open screen walking
    another switches to it. False when there is no such item; with `end`, nothing to walk opens the screen on All done. */
function decideOpen(id: string = '', items: NeedsEntry[] = decideSource(), stage: string = '', pipeline: string = '', end = false): boolean {
  if (decideNoFlow()) return false;
  const fresh = !DEC;
  const st: DecState = DEC || {...decideBlank(document.activeElement as HTMLElement | null), stage, pipeline};
  const to0 = (s: string, p: string) => { st.stage = s; st.pipeline = p; st.order = []; st.gone.clear(); st.at = null; };
  // Opening one item from a list walks the list it is in; Flow on a stage or a pipeline walks it, from its first item.
  if ((st.stage !== stage || st.pipeline !== pipeline) && (stage || pipeline || !id)) to0(stage, pipeline);
  decideSync(st, items);
  if (id && (st.stage || st.pipeline) && !st.items.has(id)) { to0('', ''); decideSync(st, items); }
  const live = decideLive(st), to = id ? (live.includes(id) ? id : null) : live[0] || null;
  if (!to && !end) return false;
  if (fresh) DEC = st;
  const d = decideDialog();
  decideGo(st, to);
  if (!d.open) { d.showModal(); if (to) decideStart(d); }
  decideOpinions(st);
  return true;
}
/** A screen not open yet: nothing walked, nothing answered, its ping to the person's reports. */
const decideBlank = (opener: HTMLElement | null): DecState => ({order: [], at: null, items: new Map(), ok: new Map(), gone: new Set(),
  more: '', busy: false, error: '', barTimer: 0, opener, off: [], stage: '', pipeline: '', snoozedAsks: null,
  snoozedOpen: false, snzOther: false, files: [], open: new Set(), seen: new Set(), bots: [], botsName: '', ping: {scope: 'reports'},
  asking: false});
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
// Async so code outside this page part (Tasks' ▶ Review) may use its answer: the shell's stand-in loads the part first.
async function decideGoThrough(): Promise<boolean> { const at = DEC_RESUME; DEC_RESUME = ''; return (!!at && decideOpen(at)) || decideOpen(''); }
/** The phone's Flow tab and Flow's mark beside Tasks: ▶ over all of Needs you, as the Tasks header's Flow on You; with
    nothing waiting, the same screen on All done, never a tap that does nothing. Closing it
    gives focus back to `from` (a tapped button is not focused on every browser). The shell wires the taps (tasks.ts). */
async function decideFlow(from: HTMLElement): Promise<void> {
  if (!(await decideGoThrough())) decideOpen('', [], '', '', true);
  const st = DEC;
  if (!st) return;
  st.opener = from;
  // Away from Tasks the pipelines may not be read yet: the crumb's menu needs them.
  if (!decidePipelines().length) void tasksPipelinesRead().catch(() => {});
}
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
  if (decideNoFlow() || !me || ['done', 'closed'].includes(String(task.status || ''))) return false;
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
  if (!stage && !pipeline || decideNoFlow()) return false;
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
  decideChatStop(); DEC_APPROVED.clear(); DEC_BROWSED.clear();
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
  // On All done, what came in (a pinged bot's question) comes on screen.
  if (!st.at) { const first = decideLive(st)[0]; if (first) { decideGo(st, first); return void decideOpinions(st); } }
  if (st.at !== before) decideRender(st); else decidePaintHead(st);
  decideOpinions(st);   // asks that joined
}

// ---- moving through
function decideGo(st: DecState, id: string | null) {
  st.at = id; st.more = ''; st.snzOther = false; st.error = ''; st.busy = false; st.files = []; DEC_SENT = ''; DEC_ASK_NOTE = '';
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
  if (st.bots.length) return ['All mine', st.botsName || 'Bots'];
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
  Object.assign(DEC, {bots: [], botsName: '', ping: {scope: 'reports'}});
  if (!decideOpen('', decideSource(), stage, pipeline)) decideOpen('', decideSource());
}
/** Nothing left: All done, how many asks you snoozed for yourself are still snoozed, and Ping your bots. "N snoozed"
    opens their list: each by its task's title, with ↩ to bring it back now. */
function decideDoneHTML(st: DecState): string {
  const asks = st.snoozedAsks, n = asks?.length || 0;
  const list = st.snoozedOpen && asks?.length ? `<ul class="dc-snzl" aria-label="Snoozed">${asks.map(a => {
    const title = a.task?.title || a.line, back = `Unsnooze${a.snoozed_until ? ` · snoozed until ${timeSaid(a.snoozed_until)}` : ''}`;
    return `<li><span class="dc-snzl-t" title="${esc(a.line)}">${esc(title)}</span><button type="button" class="dc-icon" data-dc="unsnooze" data-dc-ask="${esc(a.id)}" aria-label="${esc(`Unsnooze: ${title}`)}" title="${esc(back)}">${DEC_UNDO_ICON}</button></li>`;
  }).join('')}</ul>` : '';
  const said = !n ? ''
    : `<button type="button" class="linkish dc-sub dc-snzd" data-dc="snoozed" aria-expanded="${st.snoozedOpen}">${n} snoozed</button>`;
  const ping = st.asking ? '<p class="dc-sub dc-asking" role="status">Asking your bots</p>'
    : '<button type="button" class="primary" data-dc="ping">Want more? Ping your bots</button>';
  return `<div class="dc-done">${flowMarkHTML(48, st.asking)}<h2>All done</h2>`
    + said + list + ping + '</div>';
}
/** "Want more? Ping your bots": each bot in the ping's scope is asked for its questions (backend/flow_ping.py); they
    come in as Needs you items, and decideRefresh brings the first on screen. One a moment ago is asked once. */
async function decidePing(st: DecState) {
  st.asking = true; decideRender(st);
  try {
    const got = await post('/v2/flow/ping', st.ping) as {pinged: {id: string}[]; recent: {id: string}[]};
    if (DEC === st && !got.pinged.length && !got.recent.length) { st.asking = false; decideRender(st); toast('No bots to ask'); }
  } catch (e) {
    if (DEC === st) { st.asking = false; decideRender(st); }
    toast(errorMessage(e, 'The bots were not asked'), true);
  }
}
/** A bot's or a team's Flow mark: Flow on those bots' items only, every page of Needs you read first; All done's ping
    asks them, whoever manages them. */
async function decideBotsFlow(from: HTMLElement, bots: string[], name: string, ping: DecPing) {
  if (decideNoFlow()) return;
  if (!needsAll()) await needsReadAll();
  if (DEC) decideStop();
  DEC = {...decideBlank(from), bots, botsName: name, ping};
  decideOpen('', decideSource(), '', '', true);
}
let DEC_SNZ_READ = false;                    // the snoozed asks are being read
/** The asks the person snoozed for themselves, read for the end screen (decideDoneHTML). */
async function decideSnoozedRead(st: DecState) {
  const r = await v2Get<{items?: NeedsEntry[]; next?: string | null}>('/v2/needs-you/snoozed', {fresh: true});
  if (DEC !== st || !r) return;
  st.snoozedAsks = r.items || [];
  needsWakeAt(r.next || null);
  if (!st.at) decideRender(st);
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
  // Voice on: the wave stands where the mark was and the mic goes; the slot says voice is coming.
  $('.dc-flow', root)!.hidden = DEC_VOICE;
  $('.dc-mic', root)!.hidden = DEC_VOICE;
  $('.dc-wave', root)!.hidden = !DEC_VOICE;
  // The slot holds the voice notice while the mic is on, and nothing else (the undo notice is beside it: decideBar).
  const say = DEC_VOICE ? 'Voice is coming soon' : '';
  const line = $('.dc-say', root)!, heard = line.nextElementSibling as HTMLElement;
  if (line.textContent !== say) line.textContent = say;
  if (heard.textContent !== say) heard.textContent = say;
  // On a phone the notice is a callout under the bar, its caret at the mark (decide.css).
  const wrap = line.parentElement, mark = $(DEC_VOICE ? '.dc-wave' : '.dc-flow', root);
  if (say && wrap && mark && decidePhone()) {
    const m = mark.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    wrap.style.setProperty('--dc-caret-x', `${Math.round(m.left + m.width / 2 - w.left)}px`);
  }
  // Tico thinking: the mark moves while the item on screen waits on its opinion, and the next ask still waiting is
  // named, faint, before ‹ › (none on a phone, decide.css).
  const thinking = (k: string) => DEC_OP.state.get(k) === 'pending';
  $('.dc-flow .flow-mark', root)?.classList.toggle('live', decideOpWaits(it));
  const next = live.find(k => k !== st.at && thinking(k)), upItem = next ? st.items.get(next) : undefined;
  const up = upItem ? upItem.task?.title || upItem.line : '', upEl = $('.dc-up', root)!;
  if (upEl.textContent !== up) upEl.textContent = up;
}
// ---- Tico's opinions (POST /v2/flow/opinions, backend/flow_opinions.py): a score on each ask that the walk is ordered
// by and never shown; its line and pick are not shown either. Tico only advises: nothing here answers anything.
/** One ask's opinion as the route answers it. Typed here by hand: the generated api types on this branch do not have
    the route yet. Only the state and the score are read: the route's pick, line and note are not shown. */
interface FlowOpinion {id: string; state: 'ready' | 'pending' | 'none'; score: number | null}
const DEC_OP_MAX = 30;            // ids in one request, the one request of a round
const DEC_OP_POLL_MS = 2000;      // the next round, while some are pending or not yet asked
const DEC_OP_FOR_MS = 60_000;     // pending ones are given up on this long after the last new ask was sent
const DEC_OP_ORDER_MIN = 6;       // a set this big has its unseen items ordered by score
/** This visit's opinions: each asked ask's state, the fingerprint of the ask it is for (decideOpPrint) and its score; the
    next round's timer, whether a round is
    out (one at a time) and its abort, when the last new ask was sent, `off` once the route failed or is missing
    (nothing is asked again this visit, and the screen is as it was without it), and the visit, which closing moves on
    so nothing from an earlier visit lands in this one. */
const DEC_OP = {state: new Map<string, FlowOpinion['state']>(), print: new Map<string, string>(), score: new Map<string, number>(),
  timer: 0, busy: false, abort: null as AbortController | null, since: 0, off: false, visit: 0};
function decideOpinionsEnd() {
  DEC_OP.visit++;
  DEC_OP.abort?.abort();
  clearTimeout(DEC_OP.timer);
  DEC_OP.timer = 0; DEC_OP.busy = false; DEC_OP.abort = null; DEC_OP.since = 0; DEC_OP.off = false;
  DEC_OP.state.clear(); DEC_OP.print.clear(); DEC_OP.score.clear();
}
/** What an opinion is about: the ask's sentence, choices, what it shows and its page's version. A bot that edits the
    ask or puts up a new page under the same id changes it. */
const decideOpPrint = (it: NeedsEntry): string =>
  JSON.stringify([it.line, it.ask?.line ?? '', it.ask?.choices ?? null, decideShowRefs(it), decidePage(it)]);
/** An ask still waiting on its opinion: asked (or about to be) with none back yet, so the mark moves. Not while the
    route is off. */
const decideOpWaits = (it: NeedsEntry | null | undefined): boolean => !!it && it.kind === 'ask' && !DEC_OP.off
  && !S.me?.read_only_view && DEC_OP.state.get(it.id) !== 'ready' && DEC_OP.state.get(it.id) !== 'none';
/** The asks of the walk from the item on screen on (it first), then round to those before it. */
function decideOpAsks(st: DecState): NeedsEntry[] {
  const live = decideLive(st), at = Math.max(0, st.at ? live.indexOf(st.at) : 0);
  return [...live.slice(at), ...live.slice(0, at)].map(k => st.items.get(k)).filter((it): it is NeedsEntry => it?.kind === 'ask');
}
/** On opening, on a new set and on each refresh: an ask that changed since its opinion was asked loses its state and
    score and is asked again; then a round starts unless one is out or due (it takes in what joined). */
function decideOpinions(st: DecState) {
  if (DEC !== st || DEC_OP.off || S.me?.read_only_view) return;
  let changed = false;
  for (const it of st.items.values()) {
    const was = DEC_OP.print.get(it.id);
    if (was === undefined || was === decideOpPrint(it)) continue;
    DEC_OP.state.delete(it.id); DEC_OP.print.delete(it.id); DEC_OP.score.delete(it.id);
    changed = true;
  }
  if (changed) decidePaintHead(st);
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
    DEC_OP.off = true;   // refused or failing: no score this visit, no more asking, the walk keeps its order
    decidePaintHead(st);
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
/** What came back for the asks `sent` (id → fingerprint when asked): a ready opinion's score orders the walk; an id the
    answer left out counts as none. An answer for an ask that changed since it was asked is dropped, and the ask is
    asked again. Nothing is shown: the buttons stay as drawn. */
function decideOpinionsTake(st: DecState, sent: Map<string, string>, got: FlowOpinion[]) {
  const byId = new Map(got.map(o => [o.id, o]));
  for (const [id, print] of sent) {
    const it = st.items.get(id);
    if (!it || decideOpPrint(it) !== print) continue;
    const o = byId.get(id), state = o?.state === 'ready' || o?.state === 'pending' ? o.state : 'none';
    DEC_OP.state.set(id, state); DEC_OP.print.set(id, print);
    if (typeof o?.score === 'number') DEC_OP.score.set(id, o.score);
  }
  decideScoreOrder(st);
  decidePaintHead(st);
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
    if (st.snoozedAsks === null && !DEC_SNZ_READ) { DEC_SNZ_READ = true; void decideSnoozedRead(st).finally(() => { DEC_SNZ_READ = false; }); }
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
    // An OK with nothing attached (on a company's own stage): the task's title and text, so there is something to OK.
    const bare = !!v && decideOkBare(v) && !!decideTaskId(it);
    if (bare) decideAboutLoad(decideTaskId(it));
    const title = decideAboutOf(it)?.task?.title || it.task?.title || '';
    const changed = v?.changed ? '<p class="dc-changed" role="alert">It changed. Look again.</p>' : '';
    // A document approval shows the task's document as a phone's tab does (decideOkDocMount): its one version line,
    // and the document in the app's colours. The served page stands in only when the task has no such document.
    if (it.ask?.approval) decideAboutLoad(decideTaskId(it));
    const about = it.ask?.approval ? decideAboutOf(it) : undefined;
    html = bare ? changed + decideAboutHTML(it, kind, title ? `<h3 class="dc-title">${esc(title)}</h3>` : '')
      : about?.state === 'loading' ? skelRows(4)
      : decideApprovalDoc(it) ? changed + '<div class="dc-docview"></div>'
      : !v || v.state === 'loading' ? skelRows(4)
      : v.state === 'failed' ? `<p class="err" role="alert">${esc(v.error)}</p>`
      // Changes since vK on (the version menu): the same page with its changed blocks marked.
      : changed + decideApprovalLineHTML(it) + `<div class="dc-doc${!v.html && !v.pages.length ? ' dc-bare' : ''}">${v.since?.on && v.since.marked ? v.since.html : v.html}`
        + `${v.pages.map((_, i) => `<div data-dc-page="${i}"></div>`).join('')}</div>`;
  } else if (kind === 'question' && decideTaskId(it)) {
    // A question's background: its task, one line that opens (open as it was left on this item, through a redraw).
    html = decideBgHTML(it);
  } else if (decideTaskId(it)) {
    // Who asked and when, then the task's own text, its links, files and pages; the heading is on the right only.
    html = decideAboutHTML(it, kind);
  } else if (kind === 'mention') {
    html = `<blockquote class="dc-quote">${esc(it.line)}</blockquote>`;
  }
  // A website's browser the ask shows (`browser:<tool>/<account>`): the live view; an OK's comes in its shows.
  // (On a phone it is in the Task tab; the bottom row's Open browser opens it: decidePaintSide.)
  if (kind !== 'ok') html += (it.ask?.shows || []).filter(r => r.startsWith('browser:')).map(r => decideBrowserSlot(r.slice(8))).join('');
  // A question: on a phone the question and its answer come first, the background under them (decide.css).
  host.parentElement?.classList.toggle('dc-qmain', kind === 'question');
  if (host.dataset.html === html && host.dataset.key === it.id) { decideOkDocMount(host, it); return; }
  host.dataset.html = html; host.dataset.key = it.id;
  browserView.stopIn(host);
  host.innerHTML = html;
  decideOkDocMount(host, it);
  host.querySelectorAll<HTMLElement>('[data-dc-browser]').forEach(slot => { decideBrowserMount(st, it, slot); });
  const v = kind === 'ok' ? st.ok.get(decideOkKey(it)) : null;
  if (v && typeof window.pageCard === 'function')
    v.pages.forEach((p, i) => host.querySelector(`[data-dc-page="${i}"]`)?.replaceWith(window.pageCard(p, {size: 'full'})));
}
/** The task document a document approval asks about, once the task is read; null for any other item. */
function decideApprovalDoc(it: NeedsEntry): TaskDoc | null {
  const fid = it.ask?.approval?.file_id;
  return fid ? decideAboutOf(it)?.task?.documents?.find(d => d.file_id === fid) || null : null;
}
/** The approval's document on the left (wide screen), mounted as the phone's tab mounts it (task-docs.ts docViewMount),
    so it follows the app's theme unless its own HTML sets its colours; drawn again it moves to a newer version. */
function decideOkDocMount(host: HTMLElement, it: NeedsEntry) {
  const slot = host.querySelector<HTMLElement>(':scope > .dc-docview'), doc = decideApprovalDoc(it);
  if (slot && doc) docViewMount(slot, doc, decideApprovalOn(it, doc));
}
/** A document approval's one muted version line over the document on the left ("v2 · submitted for approval ·
    changed: …"), as a phone shows it on the document's tab (task-docs.ts docVersionLine). '' for any other OK. */
function decideApprovalLineHTML(it: NeedsEntry): string {
  const a = it.ask?.approval;
  if (!a || a.version == null) return '';
  const asked: DocApproval = {version: a.version, unchecked: a.unchecked};
  const found = decideAboutOf(it)?.task?.documents?.find(d => d.file_id === a.file_id)?.versions.find(x => x.n === a.version);
  const text = found ? docVersionLine(found, asked) : [`v${a.version}`, 'submitted for approval', a.unchecked ? 'not checked' : ''].filter(Boolean).join(' · ');
  return `<p class="dc-docver">${esc(text)}</p>`;
}
/** An OK whose view, as read, shows nothing: no grant's content, no page, no diff. Its task's text stands in. */
const decideOkBare = (v: AskOkView): boolean => (v.state === 'ready' || v.state === 'none') && !v.html && !v.pages.length && !v.missing;
const decideBrowserSlot = (ref: string) => ref.includes('/') ? `<div class="dc-browser" data-dc-browser="${esc(ref)}"></div>` : '';
/** The live view of the browser an ask shows. Its Done answers the ask "Done" when that is one of its choices (a bot's
    "sign in for me"), else it only closes the view, which leaves an Open browser button. */
function decideBrowserMount(st: DecState, it: NeedsEntry, slot: HTMLElement) {
  const [toolId = '', accountId = ''] = (slot.dataset.dcBrowser || '').split('/');
  const answers = decideKind(it) === 'question' && (it.ask?.choices || []).includes('Done');
  const taskId = it.task_id || it.task?.id || '', rid = decideRid(it), ver = it.task?.version ?? null;
  const closed = () => {
    if (!slot.isConnected || DEC !== st) return;
    // Back from it: the bottom row's forward action is Done now (decidePaintSide).
    if (!DEC_BROWSED.has(it.id)) { DEC_BROWSED.add(it.id); if (st.at === it.id) decidePaintSide(st); }
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
/** Browser asks the person came back from: a phone's forward action is Done, not Open browser. */
const DEC_BROWSED = new Set<string>();
const decideBtn = (dc: string, label: string, main = false, extra = '') =>
  `<button type="button" class="${main ? 'primary' : 'ghost'}" data-dc="${dc}"${extra}>${esc(label)}</button>`;
/** The right side: the sentence, who asks, and the buttons for this kind. */
function decidePaintSide(st: DecState) {
  const it = decideCurrent(st), host: HTMLElement | null = $('.decide .dc-side');
  if (!it || !host) return;
  const kind = decideKind(it), asker = decideAsker(it), chat = decideChatOf(it);
  const typed = kind === 'question' ? it.ask?.typed : undefined;
  // A page's OK is about the task's page: its title heads it, with the version under it. Its row says only what it is
  // (the document's name): which version and what changed is the version line, never pasted into the row.
  const page = it.ask?.page, title = page ? it.task?.title || '' : '';
  // Who asks, alone, beside the question.
  const who = asker ? `<span class="dc-owner">${esc(actorLabel(asker))}</span>` : '';
  const q = (cls: string, text: string) => `<div class="dc-qrow"><h3 class="${cls}">${esc(text)}</h3>${who}</div>`;
  // Open task ↗: the task's own page, which stays open (taskModalOpen with `direct`); its Answer comes back here.
  const taskId = decideTaskId(it), num = it.task?.number;
  const open = taskId && kind !== 'mention' ? `<button type="button" class="linkish dc-open-task" data-dc="task">Open task${num ? ` #${num}` : ''} ↗</button>` : '';
  // A page with its rounds read: the right side in parts (decideRoundsHTML), and the stage's paragraph is gone.
  const okView = kind === 'ok' && page ? st.ok.get(decideOkKey(it)) : undefined;
  // There the task is "#N ↗" at the end of the one meta line, not a row of its own.
  const tag = taskId ? `<button type="button" class="linkish dc-task-link" data-dc="task">${num ? `#${num}` : 'Task'} ↗</button>` : '';
  // Who asked opens it, as beside an ask's heading: the asker, else the task's requester.
  const by = it.ask?.asked_by || it.task?.requester_id || asker;
  const parts = page && okView?.rounds ? decideRoundsHTML(st, it, okView.rounds, page.version, okView.notices, tag, okView.since, by) : null;
  // An OK that shows the page of an earlier stage (#3784): the ask heads it, then one meta line, who asked, that
  // stage, the page's version and the task, as a page's OK has.
  const earlier = kind === 'ok' && !parts ? decideEarlier(it) : null, dot = '<span class="dc-dot" aria-hidden="true">·</span>';
  const earlierMeta = earlier ? `<div class="dc-meta">${[by ? `<span class="dc-meta-who">${esc(actorLabel(by))}</span>` : '',
    earlier.stage ? `<span>${esc(earlier.stage)}</span>` : '', `<span>v${earlier.version}</span>`, tag].filter(Boolean).join(dot)}</div>` : '';
  // A question: itself, once, in full (#3786).
  const question = kind === 'question';
  const go = question ? decideGoOf(decideAboutText(decideAboutOf(it)?.task)) : null;
  const goBtn = go ? `<button type="button" class="ghost dc-go" data-dc="go" data-dc-href="${esc(go[1])}">${esc(go[0])}</button>` : '';
  // The task's title heads a page's OK on a wide screen; a phone drops it, the page has its own heading (decide.css).
  const head = chat ? '' : parts ? `<h3 class="dc-q dc-q-page">${esc(title)}</h3>${parts.sub}${parts.body}`
    : question ? decideQuestionHeadHTML(it, tag) + (goBtn ? `<div class="dc-links">${goBtn}</div>` : '')
    : earlier ? `<h3 class="dc-q">${esc(it.line)}</h3>${earlierMeta}`
    : q('dc-q', title || typed?.question || it.line) + open;
  const err = st.error ? `<p class="err" role="alert">${esc(st.error)}</p>` : '';
  const busy = st.busy ? ' disabled' : '';
  let body = '';
  if (chat) {
    // The task's chat answers it (decideChatMount): under the composer only Approve on an OK, then Snooze and ⋯.
    const v = st.ok.get(decideOkKey(it)), ready = !!v && !v.missing && (v.state === 'ready' && !!v.hash || v.state === 'none');
    // The forward button says what it does: Pay $1,240.00, Send email, OK (a Validate page), else Approve.
    const label = decideCheckedOk(it) ? 'OK' : decideAction(v)?.verb || 'Approve', words = DEC_CHAT?.item === it.id && DEC_CHAT.typed.trim();
    // An OK approved whose comment did not go has no Approve left: the arrow sends the comment.
    // A browser ask on a phone: Open browser is the forward action, Done once the person is back from it (when "Done"
    // can answer it: a question with that choice, or with none).
    const choices = it.ask?.choices || [], done = kind === 'question' && (!choices.length || choices.includes('Done'));
    const browse = decidePhone() && (it.ask?.shows || []).some(r => r.startsWith('browser:'))
      ? DEC_BROWSED.has(it.id) && done ? decideBtn('browse-done', 'Done', true, busy) : decideBtn('browse', 'Open browser', true, busy) : '';
    body = `<div class="dc-acts dc-acts-row">${kind === 'ok' && !DEC_APPROVED.has(it.id) ? decideBtn('approve', words ? `${label} with comments` : label, true,
      ` data-dc-label="${label}"` + (!ready || !(it.ask?.may || []).includes('ok') || st.busy ? ' disabled' : '')) : browse}</div>${decideMoreHTML(st, it)}`;
  } else if (kind === 'ok') {
    const v = st.ok.get(decideOkKey(it)), may = it.ask?.may || [];
    const ready = !!v && !v.missing && (v.state === 'ready' && !!v.hash || v.state === 'none');
    // A bot's OK of Product's Validate page, its page first (backend/validate_gate.py): OK or Not validated with a note,
    // which sends the work back (In development, or Build on Product). An OK bound to an action or a person's own keeps
    // Approve and Send back. `product-validate-2`: Product added beside the pair it replaced (validate_gate.STAGES).
    const checked = decideCheckedOk(it);
    // Notes and Attach are one row: the box says Notes, the clip sits inside it at the right; the files go under it.
    const hint = DEC_ASK_NOTE === it.id ? 'Say what to change' : 'Notes';
    body = `<div class="dc-note-row"><textarea id="dc-notes" data-dc-grow rows="${decideNoteRows()}" maxlength="4000" aria-label="Notes" placeholder="${hint}"></textarea>${decideAttachHTML(st)}</div>
      ${decideFilesHTML(st, true)}<div class="dc-acts">${decideBtn('sendback', checked ? 'Not validated' : 'Send back', false,
        // On a phone Send back is always on; with no note it asks for one (decideClick). A wide screen keeps it off until then.
        decidePhone() ? busy : ` data-dc-needs="#dc-notes" disabled`)}${decideBtn('approve', checked ? 'OK' : 'Approve', true, !ready || !may.includes('ok') || st.busy ? ' disabled' : '')}</div>`;
  } else if (question) {
    body = decideQuestionHTML(st, it, typed);
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
  const same = host.querySelector<HTMLElement>(':scope > [data-key]')?.dataset.key === it.id;
  const held = same ? host.querySelector<HTMLInputElement>('#dc-other')?.value : undefined;
  const heldSaid = same ? host.querySelector<HTMLTextAreaElement>('#dc-other-note')?.value : undefined;
  const heldNote = same ? host.querySelector<HTMLTextAreaElement>('#dc-notes')?.value : undefined;
  // So does the caret: a redraw while someone types (a refusal, the end of a send) leaves them typing in the same box.
  const typing = document.activeElement;
  const caret = same && typing instanceof HTMLTextAreaElement && host.contains(typing) && typing.id
    ? {id: typing.id, from: typing.selectionStart, to: typing.selectionEnd, dir: typing.selectionDirection ?? undefined} : null;
  const card = `<div data-key="${esc(it.id)}">${head}${err}${body}</div>`;
  if (chat) decideChatMount(st, it, host, card); else { decideChatStop(); host.innerHTML = card; }
  $('.decide')?.classList.toggle('dc-chatmode', chat);
  // An ask has Snooze (for you alone) at the end of the answer row, on every width; anything else has Skip there. An
  // item with no row of its own (a typed choice) gets one.
  const ask = it.kind === 'ask';
  if (ask || decideLive(st).length > 1) {
    const card = host.querySelector<HTMLElement>(':scope > [data-key]')!, row = card.querySelector<HTMLElement>(':scope > .dc-acts');
    const end = ask ? decideSnoozeHTML(st) : `<button type="button" class="linkish dc-skip" data-dc="skip"${busy}>Skip</button>`;
    if (row) row.insertAdjacentHTML('beforeend', end); else card.insertAdjacentHTML('beforeend', `<div class="dc-acts">${end}</div>`);
  }
  // ⋯ is last on every item's row, after Snooze: a question's other answers (I don't know, Give it back, Ask back),
  // and Close task.
  const more = decideAskMoreHTML(st, it);
  if (more) host.querySelector<HTMLElement>('[data-key] > .dc-acts')?.insertAdjacentHTML('beforeend', more);
  const box = host.querySelector<HTMLInputElement>('#dc-other');
  if (box && held) box.value = held;
  const saidBox = host.querySelector<HTMLTextAreaElement>('#dc-other-note');
  if (saidBox && heldSaid) saidBox.value = heldSaid;
  const note = host.querySelector<HTMLTextAreaElement>('#dc-notes');
  if (note && heldNote) note.value = heldNote;
  host.querySelectorAll<HTMLTextAreaElement>('textarea[data-dc-grow]').forEach(decideGrow);
  // "more" only under a part its 2 lines cut.
  host.querySelectorAll<HTMLElement>('.dc-clamp:not(.open)').forEach(p => {
    const more = p.nextElementSibling as HTMLElement | null;
    if (more?.matches('[data-dc-more]')) more.hidden = p.scrollHeight <= p.clientHeight + 1;
  });
  const back = caret ? host.querySelector<HTMLTextAreaElement>(`#${caret.id}`) : null;
  if (caret && back && !back.disabled) { back.focus({preventScroll: true}); back.setSelectionRange(caret.from, caret.to, caret.dir); }
  decideHeldWire(st, host);
  decideMenuPlace(host);
  decideNeedsText($('.decide')!);
  decideBarRoom();
}
/** On a phone the answer row is pinned to the bottom (decide.css), with a page's meta line pinned on top of it: the
    scroll keeps their height clear at the end (--dc-bar-h); the meta line sits on the
    row (--dc-acts-h). 0 where no row is pinned. */
function decideBarRoom() {
  const row = decideBarSet(), meta = row ? decideBarMeta() : null;
  // Measured again whenever its height changes without a redraw (the font arriving, a long label wrapping).
  DEC_BAR_WATCH?.disconnect();
  if (row) DEC_BAR_WATCH?.observe(row, {box: 'border-box'});   // padding too: the safe-area inset
  if (meta) DEC_BAR_WATCH?.observe(meta, {box: 'border-box'});
}
const decideBarRow = () => decidePhone() ? $('.decide .dc-side>div:not(.dc-done)>.dc-acts:last-child') as HTMLElement | null : null;
const decideBarMeta = () => $('.decide .dc-side>div:not(.dc-done)>.dc-meta') as HTMLElement | null;
function decideBarSet(): HTMLElement | null {
  const root = $('.decide') as HTMLElement | null, row = decideBarRow(), meta = row ? decideBarMeta() : null;
  const h = row ? row.offsetHeight : 0;
  root?.style.setProperty('--dc-acts-h', `${h}px`);
  root?.style.setProperty('--dc-bar-h', `${h + (meta ? meta.offsetHeight : 0)}px`);
  return row;
}
const DEC_BAR_WATCH = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { decideBarSet(); });
// ---- a page that comes back: the right side in parts
const DEC_JEV: Record<string, [string, string]> = {met: ['✓', 'Meets'], unsure: ['?', 'Unsure'], failed: ['–', 'Not checked'], not_met: ['✗', "Doesn't"]};
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
/** The right side of a page's OK, in parts. First one meta line, one control: who asked (`by`), the stage, the
    version, its date and Jev's word (a phone keeps the mark and leaves the date and the word to the menu), then the
    task (`tag`, "#N ↗"). Its menu says it all in full (who, stage, version, date; Jev's word and reason), lists each
    version on one line (date, Jev's mark, the answer it got), which opens that version in the page viewer, and
    "Changes since vK", which marks the page's changed blocks (a dot on the control while on). Then What changed in vN,
    Your note on vN−1, Your points; each part is left out when it has nothing to say. */
function decideRoundsHTML(st: DecState, it: NeedsEntry, rounds: DecRound[], version: number, notices: string[] = [], tag = '',
  since: DecSince | null = null, by = ''): {sub: string; body: string; line: string; points: string; note: DecNote | null} {
  let at = decideRoundAt(rounds, version);
  if (at < 0) at = rounds.length - 1;
  const now = rounds[at]!, me = myActor(), menu = st.open.has(`${it.id}:vmenu`);
  const stage = decideStageName(it.task?.stage_id), date = shortDate(now.at), who = by ? actorLabel(by) : '';
  const said = now.jev.verdict ? DEC_JEV[now.jev.verdict] : undefined;
  const dot = '<span class="dc-dot" aria-hidden="true">·</span>', wide = (h: string) => `<span class="dc-m-wide">${h}</span>`;
  // A version reaches a person once the decision model has looked (or could not): there is no "checking" state to show.
  const jev = said ? `<span class="dc-jev" data-v="${esc(now.jev.verdict || '')}">${said[0]}${wide(` ${esc(said[1])}`)}</span>` : '';
  // The line: who asked first, cut first when the line is too long (decide.css); the date and Jev's word only when wide.
  const line = [who ? `<span class="dc-meta-who">${esc(who)}</span>` : '', stage ? `<span>${esc(stage)}</span>` : '', `<span>v${now.version}</span>`,
    date ? wide(esc(date)) : '', jev].filter(Boolean).map((h, i) => (i ? h.startsWith('<span class="dc-m-wide"') ? wide(dot) : dot : '') + h).join('');
  const row = (r: DecRound, i: number) => {
    const j = r.jev.verdict ? DEC_JEV[r.jev.verdict] : undefined, answer = decideRoundAnswer(r, i === rounds.length - 1);
    const bits = [`<b>v${r.version}</b>`, esc(shortDate(r.at)), j ? `<span class="dc-vjev" data-v="${esc(r.jev.verdict || '')}" title="${esc(`Decision model: ${j[1]}${r.jev.reason ? ` · ${r.jev.reason}` : ''}`)}">${j[0]}</span>` : '',
      answer ? `<span class="dc-step-ans" data-a="${esc(answer)}">${esc(answer)}</span>` : ''].filter(Boolean);
    return `<button type="button" role="menuitem" class="dc-mi dc-vrow" data-dc-ver="${esc(r.file_id)}@${r.version}"${i === at ? ' aria-current="true"' : ''}>${bits.join(' · ')}</button>`;
  };
  const change = since ? `<button type="button" role="menuitemcheckbox" class="dc-mi dc-vrow" aria-checked="${since.on}" data-dc-since${since.same ? ' disabled' : ''}>`
    + `Changes since v${since.version}${since.same ? '<span class="dc-since-none"> · No text changes</span>' : ''}</button>` : '';
  const info = [[who, stage, `v${now.version}`, date].filter(Boolean).join(' · '),
    said ? `Decision model: ${said[1]}${now.jev.reason ? ` · ${now.jev.reason}` : ''}` : ''].filter(Boolean);
  const sheet = info.map(t => `<p class="dc-vinfo" role="presentation">${esc(t)}</p>`).join('')
    + (rounds.length > 1 ? rounds.map(row).join('') : '') + change;
  const sub = `<div class="dc-meta"><span class="dc-vwrap"><button type="button" class="dc-vbtn${since?.on ? ' on' : ''}" data-dc-vmenu aria-haspopup="menu" aria-expanded="${menu}">`
    + `${line}${TL_ICON.caret}</button><span class="dc-menu dc-vmenu" role="menu"${menu ? '' : ' hidden'}>${sheet}</span></span>${tag}</div>`
    // Why they are asked again, and a conflict their OK hands to the Builder: said before anything else is read.
    + notices.map(n => `<p class="dc-notice" role="note">${esc(n)}</p>`).join('');
  const parts: string[] = [];
  if (now.changed.length) {
    parts.push(`<section class="dc-part"><h4>What changed in v${now.version}</h4><ul class="dc-changed-list">${now.changed.slice(0, 5).map(l => `<li>${esc(l)}</li>`).join('')}</ul></section>`);
  } else if (now.note) {
    parts.push(`<section class="dc-part"><h4>What changed in v${now.version}</h4>${decideClampHTML(st, it, 'changed', now.note)}</section>`);
  }
  const before = at > 0 ? rounds[at - 1]! : null, back = before ? decideAnswerOf(before, me) : undefined;
  const note = before && back?.answer === 'no' && back.note ? {version: before.version, at: back.at, text: back.note} : null;
  if (before && back?.answer === 'no' && back.note) {
    parts.push(`<section class="dc-part"><h4>Your note on v${before.version} <span class="dc-when">${esc(shortDate(back.at))}</span></h4>${decideClampHTML(st, it, 'note', back.note)}</section>`);
  }
  const points = now.answered.length ? `<ul class="dc-points">${now.answered.map(p =>
    `<li data-done="${p.done}"><span class="dc-mark" aria-label="${p.done ? 'Done' : 'Not done'}">${p.done ? '✓' : '✗'}</span><span><b>${esc(p.point)}</b> ${esc(p.why)}</span></li>`).join('')}</ul>` : '';
  if (points) parts.push(`<section class="dc-part"><h4>Your points</h4>${points}</section>`);
  // The chat's ask (decideAskBubbleHTML): one line of what changed, else Jev's take; the points; the note goes above it.
  const one = now.changed.length ? now.changed.join('; ') : (now.note || '').split('\n')[0]!.trim() || now.jev.reason || '';
  return {sub, body: parts.join(''), line: one, points, note};
}
/** A question's head (#3786): the question once, in full, then one dense meta line: who asked, when, the task (#N ↗).
    In a box of its own, so a phone never pins this line as it pins a page's (decide.css). */
function decideQuestionHeadHTML(it: NeedsEntry, tag: string): string {
  const by = it.ask?.asked_by || '', who = by ? actorLabel(by) : 'Tico', date = it.since ? shortDate(it.since) : '';
  const dot = '<span class="dc-dot" aria-hidden="true">·</span>';
  const meta = [`<span class="dc-meta-who">${esc(who)}</span>`, date ? `<span>${esc(date)}</span>` : '', tag].filter(Boolean).join(dot);
  return `<div class="dc-ask"><h3 class="dc-q dc-q-ask">${esc(decideQuestionOf(it))}</h3><p class="dc-meta dc-ask-meta">${meta}</p></div>`;
}
/** A question's choices: a bot's (or any with lines) as cards, each with its line and the "Recommended" tag on the one
    the bot recommends, every card alike; a person's or a stage's as buttons. Tapping one answers. */
function decideChoicesHTML(st: DecState, it: NeedsEntry): string {
  const busy = st.busy ? ' disabled' : '', brief = it.ask?.brief || null, bot = decideAsker(it).startsWith('bot:');
  const choices = it.ask?.choices || [], details = brief?.details || [];
  const card = (c: string, i: number) => {
    const rec = brief?.recommend === c, line = details[i] || '';
    // Every card looks the same: the Recommended tag alone marks the bot's pick, so none looks chosen already.
    return `<button type="button" class="dc-choice" data-dc-choice="${esc(c)}"${busy}>`
      + `<span class="dc-choice-head"><span class="dc-choice-name">${esc(c)}</span>${rec ? '<span class="dc-rec-tag">Recommended</span>' : ''}</span>`
      + (line ? `<span class="dc-choice-line">${esc(line)}</span>` : '') + '</button>';
  };
  return !choices.length ? '' : bot || details.length ? `<div class="dc-choice-list">${choices.map(card).join('')}</div>`
    : `<div class="dc-choices">${choices.map(c => `<button type="button" class="ghost" data-dc-choice="${esc(c)}"${busy}>${esc(c)}</button>`).join('')}</div>`;
}
/** A question's answer (#3786): a bot's why line and its choices (a card each, with its line and Recommended on the
    one it recommends), a person's or a stage's as buttons; then the answer box and Send, which answers it. A stage's
    typed choice is its buttons alone; its number a whole-number box with its range (Enter sends). */
function decideQuestionHTML(st: DecState, it: NeedsEntry, typed: TaskReq['typed'] | undefined): string {
  const brief = it.ask?.brief || null;
  const why = brief?.why ? `<p class="dc-why">Why it matters: ${esc(brief.why)}</p>` : '';
  const list = decideChoicesHTML(st, it);
  if (typed?.type === 'choice') return why + list;
  const label = esc(typed?.question || typed?.name || 'Answer');
  let box: string;
  if (typed?.type === 'number') {
    const {min, max} = typed;
    const range = min != null && max != null ? `${min}–${max}` : min != null ? `≥ ${min}` : max != null ? `≤ ${max}` : '';
    const bounds = (min != null ? ` min="${min}"` : '') + (max != null ? ` max="${max}"` : '');
    const input = `<input id="dc-other" type="number" step="1" inputmode="numeric"${bounds} aria-label="${label}" placeholder="Answer" autocomplete="off">`;
    // Words always have a box: a note with the number, or, alone, a question back to whoever asked.
    box = (range ? `<label class="dc-notes"><span>${esc(range)}</span>${input}</label>` : input)
      + `<div class="dc-note-row"><textarea id="dc-other-note" data-dc-grow rows="${decideNoteRows()}" maxlength="4000" aria-label="Notes" placeholder="Notes"></textarea></div>`;
  } else {
    box = `<div class="dc-note-row"><textarea id="dc-other" data-dc-grow rows="${decideNoteRows()}" maxlength="4000" aria-label="${label}" placeholder="Answer"></textarea>${typed ? '' : decideAttachHTML(st)}</div>`
      + (typed ? '' : decideFilesHTML(st, true));
  }
  const sent = DEC_SENT === it.id ? '<p class="dc-sent" role="status">Sent</p>' : '';
  return `${why}${list}${box}${sent}${decideHeldHTML(it, '#dc-other')}<div class="dc-acts dc-ask-acts">${decideBtn('other', 'Send', true, ' data-dc-needs="#dc-other, #dc-other-note" disabled')}</div>`;
}
/** ⋯ last on an item's row. A question's: a bot's "I don't know, explain" and "Give it back" (answers.DONT_KNOW and
    GIVE_BACK, which Tico adds to every bot's question), and Ask back, which sends the box's words to whoever asked as a
    comment and leaves the question open. On every item whose task the person may close (decideMayClose): Close task.
    '' when it would hold nothing. */
function decideAskMoreHTML(st: DecState, it: NeedsEntry): string {
  const busy = st.busy ? ' disabled' : '', open = st.more === 'ask', bot = decideAsker(it).startsWith('bot:');
  const kind = decideKind(it), question = kind === 'question';
  // In the task's chat, a reply in the chat does what the other answers did: only Give to… and Close task.
  const chat = decideChatOf(it);
  const items: [string, string][] = chat ? []
    : [...(question && bot ? [['dont-know', DEC_DONT_KNOW], ['give-back', DEC_GIVE_BACK]] as [string, string][] : []),
      ...(question && decideReplyTo(it) ? [['askback', 'Ask back']] as [string, string][] : [])];
  // Close task, on every item with a task the person may close (a stuck one has its own Close).
  if (kind !== 'stuck' && decideMayClose(it)) items.push(['close-task', 'Close task']);
  // In the task's chat a reply does what the other answers did: only Close task and Give to… (the same right).
  if (chat && decideMayClose(it)) items.push(['give', 'Give to…']);
  if (!items.length) return '';
  const btn = `<button type="button" class="dc-icon dc-snz-b dc-more-b" data-dc="ask-more" aria-label="More" title="More" aria-haspopup="menu" aria-expanded="${open}"${busy}>⋯</button>`;
  const menu = open ? `<span class="dc-snz" role="menu" aria-label="More">${items.map(([k, l]) =>
    `<button type="button" role="menuitem" class="dc-mi" data-dc="${k}"${busy}>${esc(l)}</button>`).join('')}</span>` : '';
  return `<span class="dc-snzw dc-askw">${btn}${menu}</span>`;
}
/** Closes a question's ⋯ menu, focus back on ⋯; true when it was open. */
function decideAskMoreClose(st: DecState): boolean {
  if (st.more !== 'ask') return false;
  st.more = '';
  decidePaintSide(st);
  ($('.decide [data-dc="ask-more"]') as HTMLElement | null)?.focus({preventScroll: true});
  return true;
}
/** A file added or finished redraws only the files row, so the note keeps its focus, caret and text. */
function decideFilesPaint(st: DecState) {
  const row = $('.decide .dc-side [data-dc-attach]') as HTMLElement | null, list = row?.dataset.dcAttach === 'list';
  if (row) row.outerHTML = decideFilesHTML(st, list); else decidePaintSide(st);
  const clip = list ? $('.decide .dc-side .dc-attach-in') : null;
  if (clip) clip.outerHTML = decideAttachHTML(st);
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
const eventInNote = (root: HTMLElement) => !!root.querySelector('[data-dc-attach]') && !!document.activeElement?.matches('#dc-notes, #dc-other');
/** The files row under the note: each file with its state and ✕ (Retry when it failed), then 📎 Attach. */
function decideFilesHTML(st: DecState, list = false): string {
  const rows = st.files.map(f => `<li class="dc-file" data-dc-file="${f.key}" data-state="${f.state}"><span class="dc-file-name">${esc(f.file.name)}</span>`
    + `<span class="muted dc-file-state">${f.state === 'up' ? 'Uploading…' : f.state === 'failed' ? esc(f.error || 'Did not upload') : ''}</span>`
    + (f.state === 'failed' ? `<button type="button" class="linkish" data-dc="file-retry" data-dc-key="${f.key}">Retry</button>` : '')
    + `<button type="button" class="dc-file-x" data-dc="file-drop" data-dc-key="${f.key}" aria-label="Remove ${esc(f.file.name)}" title="Remove">✕</button></li>`).join('');
  const full = st.files.length >= DEC_FILES_MAX, it = decideCurrent(st);
  // An OK of exactly what was shown (okGive) carries only that OK: the files go with Send back, and the row says so.
  const bound = rows && it && decideKind(it) === 'ok' && st.ok.get(decideOkKey(it))?.state === 'ready'
    ? '<p class="muted dc-file-note">Approve sends no note or files; Send back sends both.</p>' : '';
  // `list`: the files alone, their Attach in the note's own row (decideAttachHTML).
  if (list) return `<div class="dc-files" data-dc-attach="list">${rows ? `<ul>${rows}</ul>` : ''}${bound}</div>`;
  return `<div class="dc-files" data-dc-attach>${rows ? `<ul>${rows}</ul>` : ''}${bound}`
    + `<label class="dc-attach${full ? ' disabled' : ''}" title="Paste a screenshot, drop a file, or pick one"><input type="file" multiple data-dc-pick${full ? ' disabled' : ''} hidden>📎 Attach</label></div>`;
}
const DEC_CLIP = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M21 11.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
/** Attach as the clip inside the note's row: its word for a screen reader and in its title. */
function decideAttachHTML(st: DecState): string {
  const full = st.files.length >= DEC_FILES_MAX;
  return `<label class="dc-attach dc-attach-in${full ? ' disabled' : ''}" title="Attach: paste a screenshot, drop a file, or pick one">`
    + `<input type="file" multiple data-dc-pick${full ? ' disabled' : ''} hidden>${DEC_CLIP}<span class="dc-vh">Attach</span></label>`;
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
/** Snooze and its menu, at the end of an ask's answer row (an icon, no word): 1 hour, 1 day, 1 week, 1 month, then
    Other, a time of your own in the same menu. It opens upward from a row at the bottom and downward from one near the
    top (decideMenuPlace), at the row's end, off the other buttons. */
function decideSnoozeHTML(st: DecState): string {
  const busy = st.busy ? ' disabled' : '', open = st.more === 'snooze';
  const btn = `<button type="button" class="dc-icon dc-snz-b" data-dc="snooze" aria-label="Snooze" title="Snooze" aria-haspopup="menu" aria-expanded="${open}"${busy}>${DEC_SNOOZE_ICON}</button>`;
  if (!open) return `<span class="dc-snzw">${btn}</span>`;
  const item = (k: string, label: string) => `<button type="button" role="menuitem" class="dc-mi" data-dc="snooze-${k}"${busy}>${esc(label)}</button>`;
  // The time box starts an hour on, to the minute, in the person's own clock (datetime-local has no zone).
  const local = (at: Date) => new Date(at.getTime() - at.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  const soon = new Date(Date.now() + 3600_000), now = new Date(Date.now() + 60_000), year = new Date(Date.now() + 365 * 86_400_000);
  const other = st.snzOther
    ? `<span class="dc-snz-at"><input type="datetime-local" id="dc-snz-at" aria-label="Snooze until" value="${local(soon)}" min="${local(now)}" max="${local(year)}"${busy}>`
      + `<button type="button" class="dc-icon" data-dc="snooze-at" aria-label="Snooze until this time" title="Snooze"${busy}>✓</button></span>`
    : item('other', 'Other');
  return `<span class="dc-snzw">${btn}<span class="dc-snz" role="menu" aria-label="Snooze">${DEC_SNOOZE.map(([k, l]) => item(k, l)).join('')}${other}</span></span>`;
}
/** An open Snooze or ⋯ menu goes up from its button, unless there is no room above it (a row near the top): then down. */
function decideMenuPlace(host: HTMLElement) {
  const menu = host.querySelector<HTMLElement>('.dc-snz'), btn = menu?.parentElement?.querySelector<HTMLElement>('[aria-haspopup="menu"]');
  if (!menu || !btn) return;
  const top = btn.getBoundingClientRect().top, room = (host.closest('.decide')?.querySelector('.dc-head')?.getBoundingClientRect().bottom || 0);
  menu.classList.toggle('dc-snz-down', top - room < menu.offsetHeight + 8);
}
/** Closes Snooze's menu, focus back on its button; true when it was open. */
function decideSnoozeClose(st: DecState): boolean {
  if (st.more !== 'snooze') return false;
  st.more = ''; st.snzOther = false;
  decidePaintSide(st);
  ($('.decide [data-dc="snooze"]') as HTMLElement | null)?.focus({preventScroll: true});
  return true;
}
/** Other's time box in Snooze's menu, when it shows. */
const decideSnzBox = () => document.querySelector<HTMLInputElement>('.decide #dc-snz-at');
/** When a choice of Snooze's menu ends: an exact span from now (a month: the same day and time next month), or the
    time typed in Other, which must be to come. Null when it is not. */
function decideSnoozeUntil(what: string): string | null {
  const now = new Date(), span = {hour: 3600_000, day: 86_400_000, week: 7 * 86_400_000}[what];
  if (span) return new Date(now.getTime() + span).toISOString();
  if (what === 'month') { const at = new Date(now); at.setMonth(at.getMonth() + 1); return at.toISOString(); }
  const typed = decideSnzBox()?.value || '', at = typed ? new Date(typed) : null;
  return at && !Number.isNaN(at.getTime()) && at.getTime() > now.getTime() ? at.toISOString() : null;
}
/** Snoozes the ask on screen until `until`, for you alone (POST /v2/needs-you/snooze: it stays in everyone else's
    list, and its bot is not told). Undo brings it back. Only an ask has Snooze. */
async function decideSnooze(st: DecState, it: NeedsEntry, until: string) {
  st.more = ''; st.snzOther = false;
  if (it.kind !== 'ask') return;
  // Counted on the end screen from the snoozed asks as the server has them (decideSnoozedRead), read again after this.
  await decideDo(st, it, 'Snoozed', async () => { const r = await askSnooze(it.id, until); st.snoozedAsks = null; needsWakeAt(until); return r; },
    () => async () => { await askUnsnooze(it.id); st.snoozedAsks = null; });
}
/** ↩ on a snoozed ask in the end screen's list: it is back in your Needs you now, and on screen. */
async function decideUnsnooze(st: DecState, b: HTMLElement, id: string) {
  b.toggleAttribute('disabled', true);
  try { await askUnsnooze(id); } catch (e) { b.toggleAttribute('disabled', false); toast(errorMessage(e, 'Not unsnoozed.'), true); return; }
  if (DEC !== st) return;
  st.snoozedAsks = (st.snoozedAsks || []).filter(a => a.id !== id);
  await v2Refresh();
  if (DEC !== st) return;
  decideSync(st, decideSource());
  if (st.items.has(id)) decideGo(st, id); else decideRender(st);
}
/** Give to… and Move back open their choices under the buttons (Snooze opens its menu at its button: decideSnoozeHTML). */
function decideMoreHTML(st: DecState, it: NeedsEntry): string {
  const busy = st.busy ? ' disabled' : '';
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
/** After an action, quiet in the top bar left of ‹ ›: "✓ <what was done>" and Undo, which takes it back.
    It stays DEC_UNDO_MS, longer while it has the focus, then fades. A failed undo says so there. */
function decideBar(label: string, undo: (() => Promise<unknown>) | null) {
  const st = DEC, bar: HTMLElement | null = $('.decide .dc-bar');
  if (!st || !bar) return;
  clearTimeout(st.barTimer);
  // For DEC_UNDO_MS: "✓ Approved with comments  Undo".
  bar.innerHTML = `<span class="dc-bar-w">✓ ${esc(label)}</span>` + (undo ? '<button type="button" data-dc="undo">Undo</button>' : '');
  bar.classList.remove('dc-bar-out', 'dc-bar-err');
  const b = $('button', bar) as HTMLButtonElement | null;
  if (b && undo) b.onclick = async () => {
    b.disabled = true;
    try { await undo(); bar.hidden = true; await v2Refresh(); if (DEC === st) { decideSync(st, decideSource()); decidePaintHead(st); } }
    catch (e) {
      const said = errorMessage(e, 'Not undone.');
      bar.classList.add('dc-bar-err'); bar.title = said;
      bar.innerHTML = `<span class="dc-bar-w">${esc(said)}</span>`;
    }
  };
  bar.title = '';
  bar.hidden = false;
  const out = () => { bar.classList.add('dc-bar-out'); st.barTimer = window.setTimeout(() => { bar.hidden = true; }, DEC_BAR_FADE_MS); };
  st.barTimer = window.setTimeout(out, DEC_UNDO_MS);
  // While ↶ has the keyboard's focus it stays; leaving it starts the wait again.
  if (b) {
    b.onfocus = () => { clearTimeout(st.barTimer); bar.classList.remove('dc-bar-out'); };
    b.onblur = () => { clearTimeout(st.barTimer); st.barTimer = window.setTimeout(out, DEC_UNDO_MS); };
  }
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
    v.kind = kind ?? '';
    const said = shown?.content && typeof shown.content === 'object' ? (shown.content as {title?: unknown}).title : null;
    if (typeof said === 'string') v.title = said;
    if (!shown) {
      const before = it.ask?.page ? await decideRoundsRead(v, it) : null;
      await decideShowsRead(v, decideShowRefs(it), before);
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
      // What will happen, in words, then the exact content under Details (decide-content.ts).
      v.html = await decideOkContentHTML(kind ?? '', shown.content);
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
    to be, often Markdown) is drawn as Markdown, so it reads as a document rather than one run-on paragraph. With
    `before` (an earlier version's lines), also the same page with its blocks not in them marked, and how many. */
async function decidePageHTML(fid: string, version: number, before: string[] | null = null): Promise<{html: string; marked: string; count: number}> {
  const file = await viewFile(fid, 'page.html', version);
  const text = await file.blob.text();
  if (!text.replace(/^[\uFEFF\s]+/, '').startsWith('<')) return {html: `<div class="md dc-page-md">${md(text)}</div>`, marked: '', count: 0};
  const frame = (raw: string) => `<iframe class="html-view dc-page" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="Version ${version}" srcdoc="${esc(htmlPreviewDoc(raw))}"></iframe>`;
  const out = before ? pageMarkChanged(text, before) : null;
  return {html: frame(text), marked: out?.marked ? frame(out.html) : '', count: out?.marked || 0};
}
/** A page that comes back: its rounds, and, when the viewer answered an earlier version, "Changes since vK" (K the
    last version they answered), off. Returns vK's lines, for the margin marks, or null. Rounds that do not load leave the right side as it was (the stage's line). */
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
    v.since = {version: base.version, on: false, same: a === b, html: '', marked: 0};
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
  const parts: string[] = [], marked: string[] = [];
  for (const ref of shows) {
    const at = parts.length;
    const [kind, ...rest] = ref.split(':'), id = rest.join(':'), pinned = /^([^@]+)@(\d+)$/.exec(id);
    try {
      if (kind === 'page' && pinned) {
        // The page asked about, clean; with Changes since vK on, the blocks not in vK marked in its margin.
        const asked = `${pinned[1]}@${pinned[2]}` === v.page && before && v.since;
        const page = await decidePageHTML(pinned[1]!, Number(pinned[2]), asked ? before : null);
        parts.push(page.html);
        if (asked && page.marked) { marked[at] = page.marked; v.since!.marked = page.count; }
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
  if (v.since) v.since.html = parts.map((h, i) => marked[i] ?? h).join('');
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
/** Whether a refused approval was a stale key (answers._page_version): the body names the version submitted now. */
function decideStaleKey(err: unknown): boolean {
  const body = (err as Partial<ApiFailure> | null)?.body;
  const found = body?.['approval'] as {document?: unknown} | undefined;
  return body?.['error'] === 'task_changed' && typeof found?.document === 'string';
}
/** A stale approval key (a newer version was submitted since it was shown): nothing was approved. The list is read
    again, so the item carries the version submitted now, and that version is shown with "It changed. Look again.". */
async function decideStaleApproval(st: DecState, it: NeedsEntry) {
  await v2Refresh();
  if (DEC !== st || st.at !== it.id) return;
  decideSync(st, decideSource());
  st.error = '';
  // The task's documents are read again too: they hold the newer version and its note.
  const tid = decideTaskId(it);
  decideAboutLoad(tid, true);
  await decideOkLoad(st, st.items.get(it.id) ?? it, true);
}
const decideUndoAnswer = (r: unknown) => { const id = (r as TaskMoved | null)?.answer_id; return id ? () => answerUndo(id) : null; };
// A reopen needs a why from everyone (task_engine.move): an undo says so, or the server refuses it.
const decideUndoReopen = (taskId: string) => () => () => taskMove(taskId, 'reopen', null, 'Undone');
/** Whether the person may close the item's task, as the screen knows it (task_calls.can_work; the server checks
    again): someone who moves tasks or may change its pipeline, a party to it, or the manager, at any level up, of the
    bot that owns or asked for it. */
function decideMayClose(it: NeedsEntry): boolean {
  const t = it.task, me = myActor();
  if (!t || !me || (t.status && t.status !== 'open')) return false;
  if (canMove() || decidePipelineOf(needsStageOf(it))?.may_change) return true;
  const parties = [t.owner_id, t.requester_id, t.assignee_id].filter((a): a is string => !!a);
  if (parties.includes(me)) return true;
  const up = (actor: string, seen: Set<string>): boolean => {
    if (!actor.startsWith('bot:') || seen.has(actor)) return false;
    seen.add(actor);
    const id = actor.slice(4), e = (S.emps || []).find(x => x.id === id || x.slug === id), boss = String(e?.reports_to || '');
    return boss === me || (!!boss && up(boss.startsWith('human:') || boss.startsWith('bot:') ? boss : 'bot:' + boss, seen));
  };
  return parties.some(a => up(a, new Set()));
}
// ---- the task's chat: an ask on a task is answered in the task's own thread, drawn by the chat every thread uses
// (thread-view.ts threadMount): its messages, the ask as the asker's last message (`tail`), and the composer, whose
// arrow sends a reply (decideChatSend). Under it only Approve on an OK, Snooze and ⋯. A stage's typed answer keeps its
// own boxes. Underneath an ask stays an ask: an OK's reply is its send back, a bot's question waits on the bot's reply.
let DEC_CHAT: {item: string; cid: string; view: ThvView | null; typed: string} | null = null;
const DEC_CHAT_SNOOZE_DAYS = 7;   // a replied question leaves Flow until the bot writes back (waiting_on), or this long
function decideChatOf(it: NeedsEntry): boolean {
  const kind = decideKind(it);
  return (kind === 'ok' || kind === 'question') && !it.ask?.typed && !!decideTaskId(it);
}
function decideChatStop() { if (DEC_CHAT?.view) threadUnmount(DEC_CHAT.view); DEC_CHAT = null; }
/** A bot's OK of Product's Validate page, its page first (backend/validate_gate.py): OK, or a reply that sends the work
    back. `product-validate-2`: Product added beside the pair it replaced (validate_gate.STAGES). */
const decideCheckedOk = (it: NeedsEntry): boolean => !it.ask?.page && /^product-validate(-\d+)?$/.test(it.task?.stage_id || '')
  && decideAsker(it).startsWith('bot:') && /^page:/.test(it.ask?.shows?.[0] || '');
/** The right side of an ask: the task's chat, kept through every redraw of the item (its thread is mounted once the
    task's read names it), then `card`, the row under it. On a phone the task folds to its title above the chat. */
function decideChatMount(st: DecState, it: NeedsEntry, host: HTMLElement, card: string) {
  const taskId = decideTaskId(it);
  decideAboutLoad(taskId);
  const cid = decideAboutOf(it)?.task?.conversation_id || '';
  if (!DEC_CHAT || DEC_CHAT.item !== it.id || (cid && !DEC_CHAT.view) || !host.querySelector(':scope > .dc-chat')) {
    const typed = DEC_CHAT?.item === it.id ? DEC_CHAT.typed : '';
    // Each item opens on what it needs from the person: an OK on what is to be approved (its Spec, Proof, Invoice...),
    // anything else on its chat. A redraw of the same item keeps the tab the person chose.
    if (DEC_CHAT?.item !== it.id) DEC_TAB = decideKind(it) === 'ok' ? 'task' : 'chat';
    decideChatStop();
    // A phone's title row and tabs (decide.css, decideTabsPaint): the task's chat, its documents, what an OK shows.
    const title = decideAboutOf(it)?.task?.title || it.task?.title || '';
    host.innerHTML = `<div class="dc-tabs"><h3 class="dc-tabs-t">${esc(title)}</h3></div><div class="dc-chat"></div><p class="dc-docnote" role="alert" hidden></p><div class="dc-docpane"></div>`;
    const el = host.querySelector<HTMLElement>('.dc-chat')!;
    DEC_CHAT = {item: it.id, cid, view: null, typed};
    if (cid) {
      DEC_CHAT.view = threadMount(el, {id: cid, card: true, header: false,
        tail: () => { const now = DEC && decideCurrent(DEC); return now && now.id === it.id ? decideAskBubbleHTML(DEC!, now) : ''; },
        send: (text, files) => decideChatSend(st, text, files), typed: decideChatTyped, reach: false});   // no "To <name>" line
    } else el.innerHTML = `<div class="thv-list">${decideAskBubbleHTML(st, it)}</div>`;   // until the task's read lands
  } else if (DEC_CHAT.view) thvDraw(DEC_CHAT.view, {parts: ['list']});
  else host.querySelector('.dc-chat .thv-list')!.innerHTML = decideAskBubbleHTML(st, it);
  host.querySelector(':scope > [data-key]')?.remove();
  host.insertAdjacentHTML('beforeend', card);
  // The tabs wait on the task's read (its documents) and on what an OK shows (an invoice, an email): drawn on each pass.
  decideTabsPaint(st, it, host);
}
// ---- a phone's tabs on an ask in the task's chat (ui/app/task-docs.ts, as the task window draws them): Chat, then the
// task's documents, newest change first. An OK whose page is one of those documents (the same file) opens on that
// document's tab; any other OK has its own tab after Chat, named for what it shows, and opens there. A question on a
// task with no document has no tabs.
let DEC_TAB = 'chat';   // the tab shown for the item on screen: 'chat', 'task' (what the OK shows) or a document's key
/** The task document an OK's page is, by its file: only then is what the OK shows that document. */
function decideOkDoc(it: NeedsEntry, docs: TaskDoc[]): TaskDoc | null {
  const fid = decideKind(it) === 'ok' ? it.ask?.page?.file_id : '';
  return fid ? docs.find(d => d.file_id === fid) || null : null;
}
/** An OK's own tab: the title of what it shows (its page, its content), else what it is (Proof, Spec, Invoice...);
    never the name of a document it is not. */
function decideOkLabel(st: DecState, it: NeedsEntry, docs: TaskDoc[]): string {
  const v = st.ok.get(decideOkKey(it)), title = (v?.pages[0]?.title || v?.title || '').trim();
  const free = (name: string) => !!name && !docs.some(d => d.name.toLowerCase() === name.toLowerCase());
  return [title, decideTabName(st, it)].find(free) || 'Approval';
}
function decideTabsOf(st: DecState, it: NeedsEntry): DocTab[] {
  const docs: TaskDoc[] = decideAboutOf(it)?.task?.documents || [];
  if (decideKind(it) !== 'ok') return docTabs(docs);
  const tabs = docTabs(docs, [], false);
  if (decideOkDoc(it, docs)) return tabs;
  return [{key: 'chat', label: 'Chat'}, {key: 'task', label: decideOkLabel(st, it, docs)}, ...tabs.slice(1)];
}
function decideTabsPaint(st: DecState, it: NeedsEntry, host: HTMLElement) {
  const bar = host.querySelector<HTMLElement>(':scope > .dc-tabs');
  if (!bar) return;
  const tabs = decideTabsOf(st, it), said = JSON.stringify(tabs);
  const own = DEC_TAB === 'task' ? decideOkDoc(it, decideAboutOf(it)?.task?.documents || []) : null;
  if (own) DEC_TAB = docKey(own.name);   // the OK's page is this document: its tab, with its version line
  if (!tabs.some(t => t.key === DEC_TAB)) DEC_TAB = 'chat';
  if (bar.dataset.tabs !== said) {
    bar.dataset.tabs = said;
    bar.querySelector('.doc-tabs')?.remove();
    bar.insertAdjacentHTML('beforeend', docTabsHTML(tabs, DEC_TAB, k => `data-dc="tab" data-dc-tab="${esc(k)}"`));
    const row = bar.querySelector<HTMLElement>('.doc-tabs');
    if (row) docTabsKeys(row, b => decideTabShow(it, b.dataset.dcTab || 'chat'));
  }
  decideTabShow(it, DEC_TAB);
}
/** Shows a tab: the chat, what the OK shows (the task open, with its page), or a document with its version line. */
function decideTabShow(it: NeedsEntry, key: string) {
  DEC_TAB = key;
  const root = $('.decide'), doc = docOf(decideAboutOf(it)?.task?.documents, key), pane = $('.decide .dc-side > .dc-docpane');
  root?.classList.toggle('dc-on-task', key === 'task');
  root?.classList.toggle('dc-on-doc', !!doc);
  const bar = $('.decide .dc-side > .dc-tabs');
  if (bar) docTabsPaint(bar, key, b => b.dataset.dcTab);
  const asked = doc ? decideApprovalOn(it, doc) : null;
  if (doc && pane) docViewMount(pane, doc, asked);
  // A stale key shows the newer version with the same note the wide screen has above it (decidePaintBody).
  const note = $('.decide .dc-side > .dc-docnote');
  // Its words are there only while it shows, so the wide screen's note is the one "It changed" on the page.
  const changed = !!asked && !!DEC?.ok.get(decideOkKey(it))?.changed;
  if (note) { note.hidden = !changed; note.textContent = changed ? 'It changed. Look again.' : ''; }
}
/** The approval an item asks of this document, as its version line says it ("v2 · submitted for approval · changed:
    …"): only when the item's page is this document. */
function decideApprovalOn(it: NeedsEntry, doc: TaskDoc): DocApproval | null {
  const a = it.ask?.approval;
  return a && a.version != null && a.file_id === doc.file_id ? {version: a.version, unchecked: a.unchecked} : null;
}
/** The second tab, named for what it holds: the Spec a page's OK is about, the Proof a Validate or QA OK shows, an
    action's Invoice or Email, else the Task. */
function decideTabName(st: DecState, it: NeedsEntry): string {
  if (decideKind(it) !== 'ok') return 'Task';
  if (decideCheckedOk(it) || decideEarlier(it)) return 'Proof';
  if (it.ask?.page) return 'Spec';
  const v = st.ok.get(decideOkKey(it));
  return decideAction(v) ? v?.kind === 'finance' ? 'Invoice' : v?.kind === 'email_send' ? 'Email' : 'Task' : v?.kind === 'spec' ? 'Spec' : 'Task';
}
/** The ask as the asker's last message: who and when, "needs you", the task; the question (or what the OK is for), its
    one why line, its choices as replies to tap; a page's rounds and version under it. */
function decideAskBubbleHTML(st: DecState, it: NeedsEntry): string {
  const asker = decideAsker(it), date = it.since ? shortDate(it.since) : '', num = it.task?.number;
  const tag = `<button type="button" class="linkish dc-task-link" data-dc="task">${num ? `#${num}` : 'Task'} ↗</button>`;
  const dot = '<span class="dc-dot" aria-hidden="true">·</span>';
  const page = it.ask?.page, okView = decideKind(it) === 'ok' && page ? st.ok.get(decideOkKey(it)) : undefined;
  // An OK that shows the page of an earlier stage (#3784) says which stage and version.
  const earlier = decideKind(it) === 'ok' && !page ? decideEarlier(it) : null;
  // Who asked: the asker, else (a stage's ask) the task's requester, as the old heading's meta line said.
  const who = it.ask?.asked_by || it.task?.requester_id || asker;
  // A paused asker is a mark, not a card.
  const paused = asker.startsWith('bot:') && stateOf(asker.slice(4)) === 'paused' ? '<span class="dc-mark" title="Paused" aria-label="Paused">⏸</span>' : '';
  const by = [`<span class="dc-meta-who">${esc(who ? actorLabel(who) : 'Tico')}</span>${paused}`, date ? `<span>${esc(date)}</span>` : '',
    earlier?.stage ? `<span>${esc(earlier.stage)}</span>` : '', earlier ? `<span>v${earlier.version}</span>` : ''].filter(Boolean).join(dot) + tag;
  const parts = page && okView?.rounds ? decideRoundsHTML(st, it, okView.rounds, page.version, okView.notices, tag, okView.since, who) : null;
  const why = it.ask?.brief?.why ? `<p class="dc-why">${esc(it.ask.brief.why)}</p>` : '';
  const one = (t: string) => t ? `<p class="dc-said dc-one" title="${esc(t)}">${esc(t)}</p>` : '';
  // A page's OK with its rounds read: its own meta line (who, stage, version, Jev, the task) as the byline, the
  // question, one line of what changed (else Jev's take) and your points; your note on the version before is your
  // message above it, not in it.
  if (parts) {
    return decideNoteMsgHTML(parts.note) + `<div class="dc-msg dc-msg-ask">${parts.sub}<div class="dc-msg-b"><h3 class="dc-q dc-q-ask">${esc(decidePageQuestion(it))}</h3>`
      + `${one(parts.line)}${parts.points}${why}</div></div>`;
  }
  // A Validate or QA OK: the question, then the verdict (up to 3 check rows, else one line) and Proof, which opens the
  // Proof tab on a phone. A long review is the asker's message above it.
  if (decideCheckedOk(it) || earlier) {
    const long = it.line.length > DEC_ASK_SHORT || /\n/.test(it.line), q = long ? decidePageQuestion(it) : it.line;
    // The rows are what Jev passed on the page the OK shows (backend/page_verdicts.py checks_of), never the ask's words.
    const checks = ((earlier ? it.ask?.earlier?.checks : it.ask?.checks) || []).map(d => String(d).trim()).filter(d => d && d.length <= DEC_ASK_SHORT).slice(0, 3);
    const verdict = checks.length ? `<ul class="dc-checks">${checks.map(c => `<li>${esc(c)}</li>`).join('')}</ul>` : one(it.ask?.brief?.why || '');
    const proof = '<button type="button" class="linkish dc-proof-link" data-dc="tab" data-dc-tab="task">Proof</button>';
    return (long ? decideSaidMsgHTML(who, it.since || '', it.line) : '')
      + `<div class="dc-msg dc-msg-ask"><div class="dc-meta dc-ask-meta dc-msg-by">${by}</div><div class="dc-msg-b"><h3 class="dc-q dc-q-ask">${esc(q)}</h3>`
      + `${verdict}${proof}</div></div>`;
  }
  // A page's OK before its rounds are read: the question, and what the stage said after "ready for your OK" (Jev's
  // reason, a send back) as its one line.
  // A document approval's row is its name alone: nothing more to say under the question.
  const title = page && !it.ask?.approval ? it.task?.title || '' : '';
  const said = title ? it.line.replace(/^.*?, ready for your OK\.?\s*/, '').replace(/\s*OK [^()]* \(version \d+\)$/, '').trim() : '';
  const q = decideKind(it) === 'question' ? decideQuestionOf(it) : page ? decidePageQuestion(it) : it.line;
  // An action's OK says what will happen in the ask itself, so Approve is never blind; a page stays in Task.
  const act = decideKind(it) === 'ok' ? decideReceiptHTML(st.ok.get(decideOkKey(it))) : '';
  return `<div class="dc-msg dc-msg-ask"><div class="dc-meta dc-ask-meta dc-msg-by">${by}</div><div class="dc-msg-b"><h3 class="dc-q dc-q-ask">${esc(q)}</h3>`
    + `${said && said !== title ? one(said) : ''}${act}${why}${decideChoicesHTML(st, it)}</div></div>`;
}
/** An ask's line longer than this is a review, not a question: it goes above the ask as the asker's message. */
const DEC_ASK_SHORT = 120;
/** A page's OK as a question: "OK the spec?", from the line ("Spec for X, ready for your OK") or the stage's name. */
function decidePageQuestion(it: NeedsEntry): string {
  // A document approval asks for its document ("Approve the Spec?"); any other page names its stage.
  const document = it.ask?.approval?.document;
  if (document) return `Approve the ${document}?`;
  const what = /^(.+?) for .+, ready for your OK/.exec(it.line)?.[1] || decideEarlier(it)?.stage || decideStageName(it.task?.stage_id) || 'page';
  return `OK the ${/^[A-Z]{2,}\b/.test(what) ? what : what.toLowerCase()}?`;
}
/** A message of someone's in the task's chat, drawn as the thread draws one, for words that belong there and not in the
    ask: none when the thread holds the same words already. */
function decideSaidMsgHTML(actor: string, at: string, text: string, extra = ''): string {
  if (!text.trim() || DEC_CHAT?.view?.messages.some(m => (m.body || '').trim() === text.trim())) return '';
  const mine = actor === myActor();
  return `<div class="th-msg dc-msg-said${mine ? ' th-mine' : ''}"><div class="th-av">${actorAvatar(actor, 28)}</div><div class="th-main">`
    + `<div class="th-head"><span class="th-who">${esc(mine ? 'You' : actorLabel(actor))}</span>${at ? `<time datetime="${esc(at)}">${esc(shortDate(at))}</time>` : ''}${extra}</div>`
    + `<div class="th-body md">${md(text)}</div></div></div>`;
}
/** Your note on the version before, sent back: your message above the page's ask. */
const decideNoteMsgHTML = (n: DecNote | null) => n ? decideSaidMsgHTML(myActor(), n.at, n.text, `<span class="dc-when">on v${n.version}</span>`) : '';
/** What an action's OK will do (decide-content.ts's heading; an email's subject and who it goes to): a payment, an
    email, a webhook. Null for a spec, a file or a letter's proof (pages, in the second tab) and a stage's OK. */
function decideAction(v: AskOkView | undefined): {line: string; amount: string; facts: string; verb: string} | null {
  if (!v || v.state !== 'ready' || !v.html || ['', 'spec', 'file', 'letter'].includes(v.kind || '')) return null;
  const t = document.createElement('template');
  t.innerHTML = v.html;
  const head = t.content.querySelector('.dc-title')?.textContent?.trim() || '';
  const facts = t.content.querySelector('.dc-facts')?.outerHTML || '';
  const mail = t.content.querySelector('.email-send-preview'), subject = mail?.querySelector('strong')?.textContent?.trim();
  const to = mail?.querySelector('p')?.textContent?.replace(/^To\s*/, '').trim();
  const line = head || (subject ? `Send "${subject}"${to ? ` to ${to}` : ''}` : '');
  if (!line) return null;
  const amount = v.kind === 'finance' ? (/[$€£][\d,]+(?:\.\d\d)?/.exec(line)?.[0] || '') : '';
  const verb = amount ? `Pay ${amount}` : v.kind === 'email_send' ? 'Send email' : '';
  return {line, amount, facts, verb};
}
/** An action's OK as a small receipt in the ask: the amount large, what it does, then its facts (to, from, when, the
    document), as the content shows them. */
function decideReceiptHTML(v: AskOkView | undefined): string {
  const a = decideAction(v);
  if (!a) return '';
  return `<div class="dc-receipt">${a.amount ? `<p class="dc-amt">${esc(a.amount)}</p>` : ''}<p class="dc-act-line">${esc(a.line)}</p>${a.facts}</div>`;
}
/** The words in the chat's box, as they change: Approve says "Approve with comments" while there are some. */
function decideChatTyped(text: string) {
  if (DEC_CHAT) DEC_CHAT.typed = text;
  // An OK with words typed: "Approve with comments" is the one filled control, the arrow (a comment alone) goes quiet.
  const it = DEC && decideCurrent(DEC);
  $('.decide')?.classList.toggle('dc-typed-ok', !!text.trim() && !!it && decideKind(it) === 'ok');
  const b = $('.decide [data-dc="approve"]') as HTMLButtonElement | null;
  if (b?.dataset.dcLabel) b.textContent = text.trim() ? `${b.dataset.dcLabel} with comments` : b.dataset.dcLabel;
}
/** What the chat's box holds now, read from the box itself (a draft put back counts): its view, words and files, for one
    submission that keeps them whatever is on screen when it ends. Null when the item has no chat. */
interface DecChatTake {view: ThvView; box: HTMLTextAreaElement; text: string; files: File[]}
function decideChatTake(): DecChatTake | null {
  const view = DEC_CHAT?.view, C = view?.composer, box = C?.el.querySelector<HTMLTextAreaElement>('.p-text');
  return view && C && box ? {view, box, text: box.value.trim(), files: C.files.slice()} : null;
}
/** The words and files of a submission went: that composer clears, the one it was taken from, not what is on screen. */
function decideChatGone(take: DecChatTake) {
  take.view.composer.files.splice(0); thvChips(take.view); thvComposerClear(take.view);
  if (DEC_CHAT?.view === take.view) decideChatTyped('');
}
/** The composer's files, up to the task's Files one by one; null (and the error on the item) when one fails. */
async function decideChatUpload(st: DecState, itemId: string, taskId: string, files: File[]): Promise<DecFile[] | null> {
  const ups: DecFile[] = [];
  for (const file of files) {
    const f: DecFile = {key: ++DEC_FILE_KEY, file, state: 'up', id: '', error: '', run: null};
    decideFileUpload(st, itemId, taskId, f);
    await f.run;
    if (f.state !== 'done') { st.error = `${file.name}: ${f.error}`; decidePaintSide(st); return null; }
    ups.push(f);
  }
  return ups;
}
/** OKs approved whose comment did not go (it failed or a rule held it): the item stays with the words, and the arrow
    sends them as a comment. */
const DEC_APPROVED = new Set<string>();
const decideHeldSaid = (held: RuleHold) => held.outcome === 'escalated' ? 'A rule holds this message until someone reviews it.' : 'A rule stopped this message.';
/** The composer's arrow on an ask: on an OK, its send back with the words as the note; on a bot's question, a reply on
    the task's thread named for the ask (it wakes the bot), and the ask leaves this person's Flow until the bot writes
    back; on a person's or a stage's question, the answer. Files go up to the task's Files first and are named in it.
    True once it went: the composer clears and the next item shows; a refusal stays on the item in the server's words. */
async function decideChatSend(st: DecState, text: string, files: File[]): Promise<boolean> {
  const it = DEC === st ? decideCurrent(st) : null;
  if (!it || st.busy) return false;
  const taskId = decideTaskId(it), rid = decideRid(it), ver = it.task?.version ?? null;
  const ups = await decideChatUpload(st, it.id, taskId, files);
  if (!ups) return false;
  const said = decideWithFiles(text, ups);
  const comment = async (body: {text: string; ask?: string}) => {
    const r = await post(`/v2/tasks/${encodeURIComponent(taskId)}/comments`, body) as {held?: RuleHold | null};
    if (r?.held) throw new Error(decideHeldSaid(r.held));
    return r;
  };
  if (DEC_APPROVED.has(it.id)) {
    // Approved already; its comment did not go: the arrow sends the words as that comment.
    await decideDo(st, it, 'Sent', () => comment({text: said}));
    if (!st.error) DEC_APPROVED.delete(it.id);
  } else if (decideKind(it) === 'ok') {
    const page = await decideShownPage(st, it);
    if (page === null) return false;
    await decideDo(st, it, 'Sent back', () => taskAnswer(taskId, rid, 'no', ver, said, page), decideUndoAnswer);
  } else if (decideAsker(it).startsWith('bot:')) {
    const until = new Date(Date.now() + DEC_CHAT_SNOOZE_DAYS * 86_400_000).toISOString();
    // The snooze first, then the reply: a bot that answers at once always writes after the snooze (waiting_on ends it),
    // and a retry only snoozes again. A reply that does not go takes the snooze back.
    await decideDo(st, it, 'Sent', async () => {
      const out = await askSnooze(it.id, until);
      try { await comment({text: said, ask: rid}); } catch (e) { await askUnsnooze(it.id).catch(() => {}); throw e; }
      needsWakeAt(until); return out;
    }, () => async () => { await askUnsnooze(it.id); });
  } else {
    await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, said, ver), decideUndoAnswer);
  }
  return !st.error;
}
/** Approve: the person's own OK of exactly what is on screen. A change since it was read is shown, never OK'd. */
async function decideApprove(st: DecState, it: NeedsEntry) {
  const rid = decideRid(it), v = st.ok.get(decideOkKey(it)), taskId = it.task_id;
  if (!v || !taskId || st.busy) return;
  // The chat's words and files go with this approval, taken now and kept still while it goes: what is on screen when
  // it ends (another item, its own words) is never sent with it.
  const take = decideChatTake();
  const hold = (on: boolean) => { if (take) take.box.readOnly = on; };
  if (v.state === 'none') {
    const page = await decideShownPage(st, it);
    if (page === null) return;
    const files = await decideFilesReady(st, it.id);
    if (!files) { decidePaintSide(st); return; }
    hold(true);
    const ups = await decideChatUpload(st, it.id, taskId, take?.files || []);
    if (!ups) { hold(false); return; }
    const said = decideWithFiles(take?.text || decideNote('#dc-notes'), [...files, ...ups]);
    // A document approval sends its whole key: the visit it was asked on and the version on screen.
    let stale = false;
    const approve = () => taskAnswer(taskId, rid, 'ok', it.task?.version ?? null, said || undefined, page, it.ask?.approval?.visit_id)
      .catch((e: unknown) => { stale = decideStaleKey(e); throw e; });
    await decideDo(st, it, take?.text ? 'Approved with comments' : 'Approved', approve, decideUndoAnswer);
    hold(false);
    if (stale) { await decideStaleApproval(st, it); return; }
    if (take && said && !st.error) decideChatGone(take);
    return;
  }
  const hash = v.state === 'ready' ? v.hash : null;
  if (!hash) return;
  st.busy = true; st.error = '';
  decidePaintSide(st);
  hold(true);
  try {
    const ups = await decideChatUpload(st, it.id, taskId, take?.files || []);
    if (!ups) { st.busy = false; decidePaintSide(st); return; }
    const r = await okGive(taskId, rid, hash);
    // "Approve with comments" on an OK bound to what it shows: the OK carries nothing else, so the words follow it on
    // the task's thread, where everyone sees them. One that does not go stays in the box, with why.
    const said = take ? decideWithFiles(take.text, ups) : '';
    let left = '';
    if (said) {
      try {
        const c = await post(`/v2/tasks/${encodeURIComponent(taskId)}/comments`, {text: said}) as {held?: RuleHold | null};
        if (c?.held) left = decideHeldSaid(c.held);
      } catch (e) { left = errorMessage(e, 'The comment was not sent.'); }
      if (!left && take) decideChatGone(take);
    }
    if (DEC !== st) return;
    // Undone, an approval whose comment did not go is not approved any more: Approve comes back, without the why.
    const undo = decideUndoAnswer(r);
    decideBar(said && !left ? 'Approved with comments' : 'Approved', undo && left ? async () => {
      await undo(); DEC_APPROVED.delete(it.id);
      if (DEC === st && st.at === it.id) { st.error = ''; decidePaintSide(st); }
    } : undo);
    if (left) {
      DEC_APPROVED.add(it.id);
      st.busy = false; st.error = `Approved. Your comment did not go: ${left} The arrow sends it again.`;
      decidePaintSide(st);
      return;
    }
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
  } finally { hold(false); }
}
async function decideClick(st: DecState, ev: MouseEvent) {
  const el = eventElement(ev), b = el?.closest<HTMLElement>('[data-dc], [data-dc-choice], [data-dc-stage], [data-dc-walk], [data-dc-more], [data-dc-ver], [data-dc-since], [data-dc-vmenu]');
  // A link in the task's text to a place in the app: there, as Go to goes (the item stays in Needs you).
  const link = el?.closest<HTMLAnchorElement>('.dc-thing a[href]'), there = link ? decideAppHref(link.getAttribute('href') || '') : '';
  if (there && !ev.defaultPrevented && ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) { ev.preventDefault(); decideGoThere(st, there); return; }
  if (!el?.closest('.dc-where')) decideMenu(false);
  // A click off Snooze's open menu only closes it: never an answer under it by a tap that meant to dismiss.
  if (st.more === 'snooze' && !el?.closest('.dc-snzw')) { decideSnoozeClose(st); return; }
  // Outside ⋯'s menu, or on a phone's dim backdrop (drawn by .dc-askw itself), closes it.
  if (st.more === 'ask' && (!el?.closest('.dc-askw') || el.classList.contains('dc-askw'))) { decideAskMoreClose(st); return; }
  const at = decideCurrent(st);
  // The version menu closes on any click but its control's own.
  const vmenu = at ? `${at.id}:vmenu` : '';
  if (vmenu && st.open.has(vmenu) && !el?.closest('[data-dc-vmenu]')) { st.open.delete(vmenu); decidePaintSide(st); }
  if (!b || (b as HTMLButtonElement).disabled) return;
  if (at && b.hasAttribute('data-dc-vmenu')) {
    if (st.open.has(vmenu)) st.open.delete(vmenu); else st.open.add(vmenu);
    decidePaintSide(st);
    ($(st.open.has(vmenu) ? '.decide .dc-vmenu .dc-mi' : '.decide [data-dc-vmenu]') as HTMLElement | null)?.focus({preventScroll: true});
    return;
  }
  if (at && b.dataset.dcMore != null) {
    // A part cut to 2 lines: shown in full on the right side of this item until it is left.
    st.open.add(`${at.id}:more:${b.dataset.dcMore}`);
    decidePaintSide(st);
    ($('.decide .dc-side .dc-clamp.open') as HTMLElement | null)?.focus?.({preventScroll: true});
    return;
  }
  if (b.dataset.dcVer) {
    // A version in the version menu opens in the page viewer.
    const [fid = '', n = ''] = b.dataset.dcVer.split('@');
    decideStop(); location.hash = `${pageHref(fid)}?v=${n}`; return;
  }
  if (at && b.hasAttribute('data-dc-since')) {
    const v = st.ok.get(decideOkKey(at));
    if (v?.since) { v.since.on = !v.since.on; decidePaintBody(st); decidePaintSide(st); }
    ($('.decide [data-dc-vmenu]') as HTMLElement | null)?.focus({preventScroll: true});
    return;
  }
  // A row of the crumb's menu: that place's walk.
  if (b.dataset.dcWalk != null) { await decideWalkTo(b.dataset.dcWalk, b.dataset.dcPipe || ''); return; }
  const what = b.dataset.dc || '';
  if (what === 'where') { decideMenu(!!$('.decide .dc-menu')?.hidden); return; }
  if (what === 'voice') { DEC_VOICE = !DEC_VOICE; decidePaintHead(st); $(DEC_VOICE ? '.decide .dc-wave' : '.decide .dc-mic')?.focus(); return; }
  if (what === 'close') { decideStop(); return; }
  if (what === 'ping') { await decidePing(st); return; }
  if (what === 'tab') {
    // A phone's tabs: the task's chat, what an OK shows, or one of the task's documents.
    const it = decideCurrent(st);
    if (it) decideTabShow(it, b.dataset.dcTab || 'chat');
    // The ask's Proof link hides with the chat: the focus goes to the tab it opened.
    if (b.getAttribute('role') !== 'tab') ($(`.decide .dc-tabs [role="tab"][data-dc-tab="${b.dataset.dcTab}"]`) as HTMLElement | null)?.focus();
    return;
  }
  if (what === 'prev' || what === 'next') { decideStep(st, what === 'prev' ? -1 : 1); return; }
  if (what === 'skip') { decideSkip(st); return; }
  if (what === 'go' && b.dataset.dcHref) { decideGoThere(st, b.dataset.dcHref); return; }
  if (what === 'undo') return;            // the bar's own handler
  if (what === 'snoozed') { st.snoozedOpen = !st.snoozedOpen; decideRender(st); ($('.decide [data-dc="snoozed"]') as HTMLElement | null)?.focus(); return; }
  if (what === 'unsnooze' && b.dataset.dcAsk) { await decideUnsnooze(st, b, b.dataset.dcAsk); return; }
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
        // Notes is under the page on a phone: brought into view over the pinned bar (scroll-padding-bottom).
        if (box) { box.placeholder = 'Say what to change'; box.focus({preventScroll: true}); box.scrollIntoView({block: 'nearest'}); }
        return;
      }
      const files = await decideFilesReady(st, it.id);
      if (!files) { if (DEC === st) decidePaintSide(st); return; }
      const page = await decideShownPage(st, it);
      if (page !== null) await decideDo(st, it, 'Sent back', () => taskAnswer(taskId, rid, 'no', ver, decideWithFiles(note, files), page), decideUndoAnswer);
      return;
    }
    case 'other': {
      // A number question's words: with a number, its note; alone, a question back to whoever asked (Ask back).
      const said = decideNote('#dc-other-note');
      if (said && !decideNote('#dc-other') && decideReplyTo(it)) {
        await decideReplySend(st, it, '#dc-other-note', decideAsker(it).startsWith('bot:') ? rid : '');
        return;
      }
      const text = decideNote('#dc-other') || said;
      if (!text) return;
      const files = await decideFilesReady(st, it.id);
      if (!files) { if (DEC === st) decidePaintSide(st); return; }
      const note = said && text !== said ? said : undefined;
      await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, decideWithFiles(text, files), ver, note), decideUndoAnswer);
      return;
    }
    case 'ask-more':
      if (decideAskMoreClose(st)) return;
      st.more = 'ask'; decidePaintSide(st);
      ($('.decide .dc-askw [role="menuitem"]') as HTMLElement | null)?.focus({preventScroll: true});
      return;
    case 'askback': {
      // Ask back sends what is in the box; with nothing there it asks for the words first, and sends nothing.
      st.more = '';
      if (!decideNote('#dc-other')) {
        decidePaintSide(st);
        const box = $('#dc-other') as HTMLTextAreaElement | null;
        if (box) { box.placeholder = 'Ask what they mean'; box.focus({preventScroll: true}); box.scrollIntoView({block: 'nearest'}); }
        return;
      }
      // A bot's question: the comment names it, and the server writes it to the bot (answers stay its own).
      await decideReplySend(st, it, '#dc-other', decideAsker(it).startsWith('bot:') ? decideRid(it) : '');
      return;
    }
    case 'dont-know': await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, DEC_DONT_KNOW, ver), decideUndoAnswer); return;
    case 'give-back': await decideDo(st, it, 'Given back', () => taskAnswer(taskId, rid, DEC_GIVE_BACK, ver), decideUndoAnswer); return;
    case 'task': decideStop(); void taskModalOpen(taskId, undefined, {direct: true}); return;
    case 'again': await decideDo(st, it, 'Tried again', () => taskTryAgain(taskId)); return;
    case 'let-see': { const whom = it.task?.turn?.share || []; if (whom.length) await decideLetSee(st, it, whom); return; }
    case 'browse': ($('.decide .dc-thing [data-bv-launch], .decide .dc-thing [data-dc-browser-open]') as HTMLElement | null)?.click(); return;
    case 'browse-done': await decideDo(st, it, 'Done', () => taskAnswer(taskId, rid, 'Done', ver), decideUndoAnswer); return;
    case 'close-task': {
      // Close (a stuck item's) and Close task (⋯): what is typed in the item's box is the reason, kept on the close (its
      // history, and the news a bot that asked for the task gets: task_effects._tell_requester). No confirm: ↶ reopens it.
      st.more = '';
      const why = [...document.querySelectorAll<HTMLTextAreaElement>('.decide .dc-side textarea')].map(b => b.value.trim()).find(Boolean);
      await decideDo(st, it, 'Closed', () => taskMove(taskId, 'closed', ver, why), decideUndoReopen(taskId));
      return;
    }
    case 'snooze':
      if (decideSnoozeClose(st)) return;
      st.more = 'snooze'; st.snzOther = false; decidePaintSide(st);
      ($('.decide .dc-snz [role="menuitem"]') as HTMLElement | null)?.focus({preventScroll: true});
      return;
    case 'give': case 'back': {
      // Give to… from your own task's ⋯: the menu closes and its picker takes the focus the menu item had.
      const menu = st.more === 'ask';
      st.more = st.more === what ? '' : what; decidePaintSide(st);
      if (menu) ($('.decide .dc-more .gv-field button, .decide .dc-more .gv-field input:not([type="hidden"])') as HTMLElement | null)?.focus({preventScroll: true});
      return;
    }
    case 'snooze-other':
      st.snzOther = true; decidePaintSide(st);
      decideSnzBox()?.focus({preventScroll: true});
      return;
    case 'snooze-hour': case 'snooze-day': case 'snooze-week': case 'snooze-month': case 'snooze-at': {
      // Every choice is a moment, never a bare date (which the server reads as midnight in the team's zone).
      const until = decideSnoozeUntil(what.slice('snooze-'.length));
      if (!until) { const box = decideSnzBox(); box?.setCustomValidity('A time to come'); box?.reportValidity(); box?.focus(); return; }
      await decideSnooze(st, it, until);
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
