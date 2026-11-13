/* ui/app/decide.ts — the ask screen (docs/pipelines-v3.md, S3): one item of Needs you at a time, over the page. A modal
   on a wide screen, the whole screen on a phone. The top bar is "2 of 7", then 👍 👎 ‹ › ✕ (‹ › on a wide screen only;
   Skip, in the answer row, steps on at every width). The thing asked about is on
   the left, shown in full (the page, the email, what an OK is for); the sentence asked, who asks, Notes and the buttons
   are on the right. A stage's typed answer asks its question: a choice is its buttons, a number a whole-number box
   with its range, text a box; a value the server refuses stays typed, under the server's line.
   An OK bound to what was shown: the content on screen is the preview GET /v2/oks/{id} (or the email's own preview)
   returned, held here and never read again behind the person's back. A letter shows its proof and a file its pinned
   version, read here before Approve is on; what does not load leaves Approve off. Approve sends the hash of exactly that preview. If
   the engine answers that it changed, the new content is read and shown with "It changed. Look again." and Approve
   needs a new click: it is never sent again by itself.
   A stage's review (the ▶ on a stage, #/decide?stage=<id>): the same screen, walking only what waits on the person in
   that stage (needsStageOf); the top bar names the stage ("Spec · 2 of 7"), and a clear stage offers the next stage of
   its pipeline that has items, then Close.
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
}
/** The 👎 sheet: what was wrong, then (Make a rule) the rule drafted from it. */
interface AskSheet {
  step: 'why' | 'rule';
  chip: '' | 'exist' | 'person' | 'written';
  note: string;
  feedbackId: string;
  rule: string;
  allBots: boolean;
}
/** The open screen: the walk's order, the item on screen, what each OK showed, what was answered here. */
interface DecState {
  order: string[];
  at: string | null;
  items: Map<string, NeedsEntry>;
  ok: Map<string, AskOkView>;
  gone: Set<string>;
  sheet: AskSheet | null;
  more: '' | 'snooze' | 'give' | 'back';
  busy: boolean;
  error: string;
  thumbed: Set<string>;
  barTimer: number;
  opener: HTMLElement | null;
  off: (() => void)[];
  /** A stage's review: the stage walked ('' walks everything; DEC_OTHER, what is in no stage). */
  stage: string;
  /** Answers given here in this stage's walk, for "Spec is clear · 7 answered". */
  answered: number;
}

let DEC: DecState | null = null;            // the open screen, null while it is shut
let DECIDE_WANT = '';                       // a deep link's item (#/decide?item=<id>), opened once Tasks has read
let DECIDE_STAGE = '';                      // a deep link's stage review (#/decide?stage=<id>), opened once Tasks has read
const DEC_OTHER = '-';                      // the walk of what is in no stage (Tasks › You's Other chip)
const DEC_UNDO_MS = 10_000;
const DEC_CHIPS: [AskSheet['chip'], string][] = [['exist', "Shouldn't exist"], ['person', 'Wrong person'], ['written', 'Badly written']];

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
function decideKind(it: NeedsEntry): AskKind {
  if (it.kind === 'ask') return it.ask?.kind === 'answer' ? 'question' : 'ok';
  if (it.kind === 'paused') return 'fix';
  if (it.kind === 'mention') return 'mention';
  return it.task?.turn?.state === 'paused' || it.task?.late?.level === 'person' ? 'stuck' : 'yours';
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
/** The items a walk takes: a stage's review only that stage's. */
const decideInWalk = (st: DecState, it: NeedsEntry) => !st.stage
  || (st.stage === DEC_OTHER ? !decideStageName(needsStageOf(it)) : needsStageOf(it) === st.stage);
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
}

// ---- the count, and the lists that show Needs you
function decideCount(): number {
  if (S.me?.role === 'bot') return 0;
  return S.v2?.needsCount ?? (S.v2?.needs || []).length;
}
function needsBadgePaint() {
  const n = decideCount();
  for (const badge of document.querySelectorAll<HTMLElement>('[data-needs-badge]')) {
    badge.hidden = !n; badge.textContent = String(n); badge.setAttribute('aria-label', `${n} need you`);
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
  d.id = 'need-card'; d.className = 'dc-dialog'; d.setAttribute('aria-label', 'Needs you');
  d.innerHTML = `<section class="decide">
    <header class="dc-head">
      <h2 class="dc-count tnum" aria-live="polite"></h2>
      <span class="dc-nav">
        <button type="button" class="dc-icon" data-dc="up" aria-label="Good ask" aria-pressed="false">👍</button>
        <button type="button" class="dc-icon" data-dc="down" aria-label="Bad ask">👎</button>
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
    if (DEC.sheet && box.matches('[data-dc-note]')) DEC.sheet.note = box.value;
    if (DEC.sheet && box.matches('[data-dc-rule]')) DEC.sheet.rule = box.value;
    decideNeedsText(root);
  });
  root.addEventListener('change', (ev: Event) => {
    const pick = eventElement(ev) as HTMLSelectElement | null;
    if (DEC?.sheet && pick?.matches('[data-dc-scope]')) DEC.sheet.allBots = pick.value === 'all';
    decideNeedsText(root);
  });
  d.addEventListener('keydown', (ev: KeyboardEvent) => {
    // Enter in an answer's one-line box sends it.
    if (DEC && ev.key === 'Enter' && eventElement(ev)?.matches('input#dc-other')) {
      ev.preventDefault(); (d.querySelector<HTMLButtonElement>('[data-dc="other"]'))?.click(); return;
    }
    if (!DEC || ev.metaKey || ev.ctrlKey || ev.altKey || eventElement(ev)?.closest('input, textarea, select')) return;
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); decideStep(DEC, -1); }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); decideStep(DEC, 1); }
  });
  d.addEventListener('cancel', ev => { ev.preventDefault(); decideStop(); });
  d.addEventListener('close', () => { if (!d.open) decideStop(); });
  document.body.appendChild(d);
  return d;
}
/** A button that needs words (Send back, Something else, Add rule) is on once there are some. */
function decideNeedsText(root: HTMLElement) {
  for (const b of root.querySelectorAll<HTMLButtonElement>('[data-dc-needs]')) {
    const box = root.querySelector<HTMLTextAreaElement | HTMLInputElement | HTMLSelectElement>(b.dataset.dcNeeds!);
    b.disabled = !box?.value.trim() || !!DEC?.busy;
  }
}
/** Opens the item `id`, or the first of the list with none. `items` is the list to walk, Needs you as the page shows
    it by default; `stage` walks only that stage's items (a stage's review), and an open screen walking another switches
    to it. False when there is no such item. */
function decideOpen(id: string = '', items: NeedsEntry[] = decideSource(), stage: string = ''): boolean {
  if (S.me?.role === 'bot') return false;
  const fresh = !DEC;
  const st: DecState = DEC || {order: [], at: null, items: new Map(), ok: new Map(), gone: new Set(), sheet: null, more: '',
    busy: false, error: '', thumbed: new Set(), barTimer: 0, opener: document.activeElement as HTMLElement | null, off: [], stage, answered: 0};
  // Opening one item from a list walks the list it is in; ▶ on a stage walks that stage, from its first item.
  if (st.stage !== stage && (stage || !id)) { st.stage = stage; st.order = []; st.gone.clear(); st.answered = 0; st.at = null; }
  decideSync(st, items);
  if (id && st.stage && !st.items.has(id)) { st.stage = ''; st.order = []; st.gone.clear(); st.answered = 0; decideSync(st, items); }
  const live = decideLive(st), to = id ? (live.includes(id) ? id : null) : live[0] || null;
  if (!to) return false;
  if (fresh) DEC = st;
  const d = decideDialog();
  decideGo(st, to);
  if (!d.open) d.showModal();
  return true;
}
const decideGoThrough = () => decideOpen('');
/** A task page's Answer: the Needs you item that is this task's ask for requirement `rid`. Items are keyed by their own
    id (`<ask id>:<requirement id>`) and a stage's requirement is the same on every task in the stage, so both the task
    and the requirement must match. Not in the list held: read it once more. Still not: say why, never nothing. The Tasks
    page's own copy is not read again behind an open task, so the shared list is looked in too. The list is read a page
    at a time and the newest ask sorts last in its group, so a miss reads on through the pages (up to DEC_ASK_PAGES). */
const DEC_ASK_PAGES = 20;
async function decideOpenAsk(taskId: string, rid: string): Promise<boolean> {
  const mine = (it: NeedsEntry) => it.kind === 'ask' && decideTaskId(it) === taskId && decideRid(it) === rid;
  const find = () => [decideSource(), needsHeld()].map(items => ({items, it: items.find(mine)})).find(x => x.it);
  let at = find();
  if (!at) { await v2Refresh(); at = find(); }
  for (let i = 0; !at && !needsAll() && i < DEC_ASK_PAGES; i++) {
    if (!await v2NeedsMore()) break;
    at = find();
  }
  if (at?.it && decideOpen(at.it.id, at.items)) return true;
  // Found but not opened: only a bot's decideOpen refuses, and a bot has no Answer; kept as a guard.
  toast(at ? 'This ask could not be opened here. Open it from Needs you.'
    : needsAll() ? 'This ask is no longer in your Needs you list: it may have been answered or withdrawn. Reload the task to see where it is.'
      : 'This ask was not found in the part of your Needs you list read so far. Open it from Needs you.', true);
  return false;
}
/** ▶ on a stage: every page of Needs you is read first, then the stage's items are walked. False when none wait. */
async function decideStageOpen(stage: string): Promise<boolean> {
  if (!stage || S.me?.role === 'bot') return false;
  if (!needsAll()) await needsReadAll();
  return decideOpen('', decideSource(), stage);
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
  DEC = null;
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
}

// ---- moving through
function decideGo(st: DecState, id: string | null) {
  st.at = id; st.sheet = null; st.more = ''; st.error = ''; st.busy = false;
  decideRender(st);
}
function decideStep(st: DecState, dir: number) {
  const live = decideLive(st), i = live.indexOf(st.at || ''), next = live[i + dir];
  if (next) decideGo(st, next);
}
/** Skip (every width; a phone has no ‹ ›): the next item, nothing answered; from the last, the first one still waiting,
    which is the first one skipped. */
function decideSkip(st: DecState) {
  const live = decideLive(st), i = live.indexOf(st.at || ''), next = live[i + 1] || live[0];
  if (next && next !== st.at) decideGo(st, next);
}
/** After an answer: the next item, else the one before, else All caught up. */
function decideAfter(st: DecState, id: string) {
  st.gone.add(id);
  st.answered++;
  const i = st.order.indexOf(id), live = (k: string) => !st.gone.has(k) && st.items.has(k);
  decideGo(st, st.order.slice(i + 1).find(live) || st.order.slice(0, Math.max(i, 0)).reverse().find(live) || null);
}

// ---- drawing
/** The walk's name in the top bar: the stage's, Other, or nothing for the whole list. */
const decideWalkName = (st: DecState) => !st.stage ? '' : st.stage === DEC_OTHER ? 'Other' : decideStageName(st.stage) || 'Stage';
/** The next stage of the stage's pipeline with items for the person, after it, else before it: [id, name, count]. */
function decideNextStage(stage: string): [string, string, number] | null {
  const p = ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).find(x => x.stages.some(s => s.id === stage));
  if (!p) return null;
  const at = p.stages.findIndex(s => s.id === stage), counts = needsStageCounts();
  const s = [...p.stages.slice(at + 1), ...p.stages.slice(0, at)].find(x => x.id && (counts.get(x.id) || 0) > 0);
  return s?.id ? [s.id, s.name, counts.get(s.id) || 0] : null;
}
/** Nothing left: All caught up; a stage's walk says the stage is clear and offers the next stage with items. */
function decideDoneHTML(st: DecState): string {
  const close = '<button type="button" class="ghost" data-dc="close">Close</button>';
  if (!st.stage) return `<div class="dc-done"><h2>All caught up</h2>${close}</div>`;
  const next = st.stage === DEC_OTHER ? null : decideNextStage(st.stage);
  const play = next ? `<button type="button" class="primary" data-dc-walk="${esc(next[0])}">▶ ${esc(next[1])} · <span class="tnum">${next[2]}</span></button>` : '';
  return `<div class="dc-done"><h2>${esc(decideWalkName(st))} is clear</h2>`
    + (st.answered ? `<p class="dc-sub">${st.answered} answered</p>` : '') + `<div class="dc-acts">${play}${close}</div></div>`;
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
  const stage = decideWalkName(st);
  $('.dc-count', root)!.textContent = it ? `${stage ? stage + ' · ' : ''}${at + 1} of ${live.length}` : stage;
  ($('[data-dc="prev"]', root) as HTMLButtonElement).disabled = !it || at <= 0;
  ($('[data-dc="next"]', root) as HTMLButtonElement).disabled = !it || at >= live.length - 1;
  const thumbs = !!it && it.kind === 'ask' && !!it.ask?.may.includes('thumbs');
  const up = $('[data-dc="up"]', root) as HTMLButtonElement, down = $('[data-dc="down"]', root) as HTMLButtonElement;
  up.hidden = down.hidden = !thumbs;
  up.setAttribute('aria-pressed', String(!!it && st.thumbed.has(it.id)));
  down.setAttribute('aria-pressed', String(!!st.sheet));
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
    return;
  }
  // Coming to an ask: what it shows is read once; held content from other refs (a new version came while it was off
  // screen) is read again and marked as changed. While it is on screen, a refresh never reads it again (decideShownPage).
  const held = decideKind(it) === 'ok' ? st.ok.get(decideOkKey(it)) : undefined;
  if (decideKind(it) === 'ok' && (!held || decideOkStale(held, it))) void decideOkLoad(st, it, !!held);
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
      : (v.changed ? '<p class="dc-changed" role="alert">It changed. Look again.</p>' : '') + `<div class="dc-doc">${v.html}${v.pages.map((_, i) => `<div data-dc-page="${i}"></div>`).join('')}</div>`;
  } else if (it.task) {
    html = `<div class="dc-doc"><h3 class="dc-title">${esc(it.task.title)}${lateTagHTML(it.task)}</h3>`
      + `<p class="dc-line">${taskMarkHTML(it.task, {space: false})} ${esc(taskLine(it.task))}</p>`
      + (kind === 'mention' ? `<blockquote class="dc-quote">${esc(it.line)}</blockquote>` : '') + '</div>';
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
  const stage = decideStageName(it.task?.stage_id);
  const typed = kind === 'question' ? it.ask?.typed : undefined;
  // A page's OK is about the task's page: its title heads it, with the version under it. The stage's own line ("OK Chris
  // (version 3)") names the person asked, so only what it says before that (Jev's reason, a send back) is kept.
  const page = it.ask?.page, title = page ? it.task?.title || '' : '';
  const said = title ? it.line.replace(/\s*OK [^()]* \(version \d+\)$/, '').trim() : '';
  const sub = [asker ? esc(actorLabel(asker)) : '', esc(stage), page ? `Version ${esc(String(page.version))}` : ''].filter(Boolean).join(' · ');
  const head = `<h3 class="dc-q">${esc(title || typed?.question || it.line)}</h3><p class="dc-sub">${sub}</p>`
    + (said && said !== title ? `<p class="dc-said">${esc(said)}</p>` : '');
  const err = st.error ? `<p class="err" role="alert">${esc(st.error)}</p>` : '';
  const busy = st.busy ? ' disabled' : '';
  let body = '';
  if (st.sheet) body = decideSheetHTML(st, it);
  else if (kind === 'ok') {
    const v = st.ok.get(decideOkKey(it)), may = it.ask?.may || [];
    const ready = !!v && !v.missing && (v.state === 'ready' && !!v.hash || v.state === 'none');
    // A bot's OK of Product's Validate page, its page first (backend/validate_gate.py): OK or Not validated with a note,
    // which sends the work back to In development. An OK bound to an action or a person's own keeps Approve and Send back.
    const checked = !page && it.task?.stage_id === 'product-validate' && decideAsker(it).startsWith('bot:')
      && /^page:/.test(it.ask?.shows?.[0] || '');
    body = `<label class="dc-notes"><span>Notes</span><textarea id="dc-notes" rows="3" maxlength="4000"></textarea></label>
      <div class="dc-acts">${decideBtn('approve', checked ? 'OK' : 'Approve', true, !ready || !may.includes('ok') || st.busy ? ' disabled' : '')}${decideBtn('sendback', checked ? 'Not validated' : 'Send back', false, ` data-dc-needs="#dc-notes" disabled`)}</div>`;
  } else if (typed) {
    body = decideTypedHTML(typed, it.ask?.choices || [], st.busy);
  } else if (kind === 'question') {
    const choices = it.ask?.choices || [];
    body = `<div class="dc-choices">${choices.map(c => `<button type="button" class="ghost" data-dc-choice="${esc(c)}"${busy}>${esc(c)}</button>`).join('')}</div>
      <label class="dc-notes"><span>Something else</span><textarea id="dc-other" rows="2" maxlength="4000"></textarea></label>
      <div class="dc-acts">${decideBtn('other', 'Send', !choices.length, ' data-dc-needs="#dc-other" disabled')}</div>`;
  } else if (kind === 'yours') {
    body = `<div class="dc-acts">${decideBtn('done', 'Done', true, busy)}${decideBtn('snooze', 'Snooze', false, busy)}${decideBtn('give', 'Give to…', false, busy)}</div>${decideMoreHTML(st, it)}`;
  } else if (kind === 'stuck') {
    body = `<div class="dc-acts">${decideBtn('again', 'Try again', true, busy)}${decideBtn('give', 'Reassign', false, busy)}${decideBtn('back', 'Move back', false, busy)}${decideBtn('close-task', 'Close', false, busy)}</div>${decideMoreHTML(st, it)}`;
  } else {
    // A cause a person fixes in the app (Connect GitHub) goes straight there; Open shows the first task it holds up.
    body = `<div class="dc-acts">${it.href ? decideBtn('fix', it.line, true) : ''}${decideBtn('open', 'Open', !it.href)}</div>`;
  }
  // What was typed stays through a redraw of the same item (busy, then a refusal in the server's words).
  const held = host.querySelector<HTMLElement>('[data-key]')?.dataset.key === it.id ? host.querySelector<HTMLInputElement>('#dc-other')?.value : undefined;
  host.innerHTML = `<div data-key="${esc(it.id)}">${head}${err}${body}</div>`;
  // Skip sits in the answer row, on every width; an item with no row of its own (a typed choice) gets one.
  if (!st.sheet && decideLive(st).length > 1) {
    const card = host.firstElementChild!, row = card.querySelector<HTMLElement>(':scope > .dc-acts');
    const skip = decideBtn('skip', 'Skip', false, busy);
    if (row) row.insertAdjacentHTML('beforeend', skip); else card.insertAdjacentHTML('beforeend', `<div class="dc-acts">${skip}</div>`);
  }
  const box = host.querySelector<HTMLInputElement>('#dc-other');
  if (box && held) box.value = held;
  decideNeedsText($('.decide')!);
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
/** Snooze, Give to… and Move back open their choices under the buttons. */
function decideMoreHTML(st: DecState, it: NeedsEntry): string {
  const busy = st.busy ? ' disabled' : '';
  if (st.more === 'snooze')
    return `<div class="dc-more">${decideBtn('snooze-1', 'Tomorrow', false, busy)}${decideBtn('snooze-7', 'Next week', false, busy)}</div>`;
  if (st.more === 'give')
    return `<div class="dc-more"><select id="dc-to" aria-label="Give to">${taskOwnerOptions('', 'Give to…')}</select>${decideBtn('give-to', 'Give', true, ' data-dc-needs="#dc-to" disabled')}</div>`;
  if (st.more === 'back') {
    const p = ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).find(x => x.id === it.task?.pipeline_id);
    const at = p ? p.stages.findIndex(s => s.id === it.task?.stage_id) : -1;
    const earlier = p && at > 0 ? p.stages.slice(0, at) : [];
    return `<div class="dc-more">${earlier.length ? earlier.map(s => `<button type="button" class="ghost" data-dc-stage="${esc(s.id || '')}"${busy}>${esc(s.name)}</button>`).join('') : '<span class="muted">No earlier stage</span>'}</div>`;
  }
  return '';
}
/** The 👎 sheet: what was wrong (a chip and a few words), then Send back or Make a rule; then the rule to add. */
function decideSheetHTML(st: DecState, it: NeedsEntry): string {
  const sh = st.sheet!, busy = st.busy ? ' disabled' : '';
  if (sh.step === 'rule') {
    const bot = it.ask?.asked_by || '';
    return `<h3 class="dc-q">New rule</h3><textarea id="dc-rule" data-dc-rule rows="3" maxlength="4000" aria-label="Rule">${esc(sh.rule)}</textarea>
      <select data-dc-scope aria-label="For">${bot ? `<option value="bot"${sh.allBots ? '' : ' selected'}>${esc(actorLabel(bot))}</option>` : ''}<option value="all"${sh.allBots || !bot ? ' selected' : ''}>All bots</option></select>
      <div class="dc-acts">${decideBtn('rule-add', 'Add rule', true, ' data-dc-needs="#dc-rule"')}</div>`;
  }
  const chips = DEC_CHIPS.map(([k, label]) => `<button type="button" class="dc-chip" data-dc-chip="${k}" aria-pressed="${sh.chip === k}"${busy}>${esc(label)}</button>`).join('');
  const give = sh.chip === 'person';
  return `<div class="dc-chips">${chips}</div><textarea id="dc-why" data-dc-note rows="3" maxlength="4000" aria-label="What was wrong">${esc(sh.note)}</textarea>
    ${give ? `<select id="dc-to" aria-label="Give to">${taskOwnerOptions('', 'Give to…')}</select>` : ''}
    <div class="dc-acts">${give ? decideBtn('why-give', 'Give to…', true, ' data-dc-needs="#dc-to" disabled')
      : decideBtn('why-back', 'Send back', true, busy)}${feedbackRuleOn(it) ? decideBtn('why-rule', 'Make a rule', false, busy) : ''}</div>`;
}
/** A rule is about a bot's asks. */
const feedbackRuleOn = (it: NeedsEntry) => String(it.ask?.asked_by || '').startsWith('bot:');
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
    page: decidePage(it), shows: decideShows(it)};
  const before = st.ok.get(key);
  if (before) decideOkLet(before);
  st.ok.set(key, v);
  if (DEC === st && st.at === it.id) { decidePaintBody(st); decidePaintSide(st); }
  try {
    // No grant (404): a stage's OK, which shows what its requirement names.
    const shown = await okShown(rid).catch((e: unknown) => { if (errorStatus(e) === 404) return null; throw e; });
    const [kind, ...rest] = (shown?.subject ?? '').split(':'), ref = rest.join(':');
    if (!shown) {
      await decideShowsRead(v, it.ask?.shows || []);
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
async function decidePageHTML(fid: string, version: number): Promise<string> {
  const file = await viewFile(fid, 'page.html', version);
  const text = await file.blob.text();
  if (!text.replace(/^[\uFEFF\s]+/, '').startsWith('<')) return `<div class="md dc-page-md">${md(text)}</div>`;
  const doc = htmlPreviewDoc(text);
  return `<iframe class="html-view dc-page" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="Version ${version}" srcdoc="${esc(doc)}"></iframe>`;
}
/** A stage's OK shows what the requirement names: pages in full (a page requirement's at its pinned version), an email
    as frozen, a file at its version, the rest by name. One that does not load leaves Approve off (`missing`). */
async function decideShowsRead(v: AskOkView, shows: string[]) {
  const parts: string[] = [];
  for (const ref of shows) {
    const [kind, ...rest] = ref.split(':'), id = rest.join(':'), pinned = /^([^@]+)@(\d+)$/.exec(id);
    try {
      if (kind === 'page' && pinned) { parts.push(await decidePageHTML(pinned[1]!, Number(pinned[2]))); continue; }
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
const decideTo = () => ($('#dc-to') as HTMLSelectElement | null)?.value || '';
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
  } catch (e) {
    if (DEC !== st) return;
    st.busy = false; st.error = taskRefusedOf(e)?.detail || errorMessage(e, 'Not saved.');
    decidePaintSide(st);
  }
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
    await decideDo(st, it, 'Approved', () => taskAnswer(taskId, rid, 'ok', it.task?.version ?? null, decideNote('#dc-notes') || undefined, page), decideUndoAnswer);
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
  const el = eventElement(ev), b = el?.closest<HTMLElement>('[data-dc], [data-dc-choice], [data-dc-stage], [data-dc-chip], [data-dc-walk]');
  if (!b || (b as HTMLButtonElement).disabled) return;
  if (b.dataset.dcWalk) { await decideStageOpen(b.dataset.dcWalk); return; }
  const what = b.dataset.dc || '';
  if (what === 'close') { decideStop(); return; }
  if (what === 'prev' || what === 'next') { decideStep(st, what === 'prev' ? -1 : 1); return; }
  if (what === 'skip') { decideSkip(st); return; }
  if (what === 'undo') return;            // the bar's own handler
  const it = decideCurrent(st);
  if (!it) return;
  // Every answer is for the item on screen, and only that one.
  const shown = b.closest<HTMLElement>('[data-key]')?.dataset.key;
  if (shown && shown !== it.id) return;
  const taskId = it.task_id || it.task?.id || '', rid = decideRid(it), ver = it.task?.version ?? null;
  if (b.dataset.dcChoice != null) { await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, b.dataset.dcChoice!, ver), decideUndoAnswer); return; }
  if (b.dataset.dcStage) { await decideDo(st, it, 'Moved back', () => taskMove(taskId, b.dataset.dcStage!, ver)); return; }
  if (b.dataset.dcChip != null && st.sheet) {
    const chip = b.dataset.dcChip as AskSheet['chip'];
    st.sheet.chip = st.sheet.chip === chip ? '' : chip;
    decidePaintSide(st); return;
  }
  switch (what) {
    case 'approve': await decideApprove(st, it); return;
    case 'sendback': {
      const note = decideNote('#dc-notes'), page = note ? await decideShownPage(st, it) : null;
      if (note && page !== null) await decideDo(st, it, 'Sent back', () => taskAnswer(taskId, rid, 'no', ver, note, page), decideUndoAnswer);
      return;
    }
    case 'other': { const text = decideNote('#dc-other'); if (text) await decideDo(st, it, 'Answered', () => taskAnswer(taskId, rid, text, ver), decideUndoAnswer); return; }
    case 'done': await decideDo(st, it, 'Done', () => taskDone(taskId, ver), decideUndoReopen(taskId)); return;
    case 'again': await decideDo(st, it, 'Tried again', () => taskTryAgain(taskId)); return;
    case 'close-task': await decideDo(st, it, 'Closed', () => taskMove(taskId, 'closed', ver), decideUndoReopen(taskId)); return;
    case 'snooze': case 'give': case 'back':
      st.more = st.more === what ? '' : what; decidePaintSide(st); return;
    case 'snooze-1': case 'snooze-7': {
      const until = new Date(Date.now() + (what === 'snooze-1' ? 1 : 7) * 86400_000).toISOString().slice(0, 10);
      await decideDo(st, it, 'Snoozed', () => taskSnooze(taskId, until, ver), () => () => taskUnsnooze(taskId)); return;
    }
    case 'give-to': { const to = decideTo(); if (to) await decideDo(st, it, `Given to ${actorLabel(to)}`, () => taskReassign(taskId, to, ver)); return; }
    case 'open': decideOpenWhere(it); return;
    case 'fix': if (it.href) { decideStop(); location.hash = it.href; } return;
    case 'up':
      if (st.thumbed.has(it.id)) return;
      try { await taskThumbs(taskId, rid, true); st.thumbed.add(it.id); decidePaintHead(st); }
      catch (e) { st.error = errorMessage(e, 'Not saved.'); decidePaintSide(st); }
      return;
    case 'down':
      st.sheet = st.sheet ? null : {step: 'why', chip: '', note: '', feedbackId: '', rule: '', allBots: false};
      decidePaintHead(st); decidePaintSide(st); return;
    case 'why-back': await decideDo(st, it, 'Sent back', () => taskThumbs(taskId, rid, false, decideWhy(st.sheet))); return;
    case 'why-give': { const to = decideTo(); if (to) await decideDo(st, it, `Given to ${actorLabel(to)}`, () => taskGiveAsk(taskId, rid, to, ver)); return; }
    case 'why-rule': await decideRuleDraft(st, it); return;
    case 'rule-add': await decideRuleAdd(st, it); return;
  }
}
/** The words a 👎 sends: the chip, then what was typed. */
function decideWhy(sh: AskSheet | null): string {
  const chip = DEC_CHIPS.find(([k]) => k === sh?.chip)?.[1];
  return [chip, sh?.note.trim()].filter(Boolean).join(': ');
}
/** Make a rule: the 👎 is saved (it sends the ask back), then Tico drafts one sentence from it to edit. */
async function decideRuleDraft(st: DecState, it: NeedsEntry) {
  const sh = st.sheet;
  if (!sh || st.busy) return;
  st.busy = true; st.error = ''; decidePaintSide(st);
  try {
    if (!sh.feedbackId) {
      const r = await taskThumbs(it.task_id || '', decideRid(it), false, decideWhy(sh));
      sh.feedbackId = String((r as {feedback_id?: string} | null)?.feedback_id || '');
      if (!sh.feedbackId) throw new Error('The 👎 was not saved.');
    }
    const draft: {text?: string} = await post(`/v2/feedback/${encodeURIComponent(sh.feedbackId)}/rule-draft`, {});
    sh.rule = draft.text || sh.note; sh.step = 'rule';
  } catch (e) { st.error = errorMessage(e, 'No rule drafted.'); }
  if (DEC !== st) return;
  st.busy = false; decidePaintSide(st);
}
async function decideRuleAdd(st: DecState, it: NeedsEntry) {
  const sh = st.sheet, text = decideNote('#dc-rule');
  if (!sh?.feedbackId || !text) return;
  await decideDo(st, it, 'Rule added', () => post(`/v2/feedback/${encodeURIComponent(sh.feedbackId)}/rule`, {text, all_bots: sh.allBots || !String(it.ask?.asked_by || '').startsWith('bot:')}));
}
/** Open: a mention's thread (or its task), a cause's first task. */
function decideOpenWhere(it: NeedsEntry) {
  decideStop();
  if (it.kind === 'mention' && !it.task_id && it.conversation_id && typeof threadHref === 'function') { location.hash = threadHref(it.conversation_id); return; }
  const id = it.task_id || it.task_ids?.[0] || it.task?.id;
  if (id) taskModalOpen(id);
}
