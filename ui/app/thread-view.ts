/* ui/app/thread-view.ts — One thread on screen: its timeline, flat and in time order (a reply quotes the message it
   answers in one line above its words), its composer, the bots' status at its end, and its live updates. Mounted by the
   Thread page (#/thread/<id>), a bot's Home and DM, a person's Home and a team's Home. Shapes: docs/dev/threads-contract.md §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type ThvBotRow = import('../types/api').ThreadBotStatusRow;
type ThvCrumb = import('../types/api').ThreadCrumb;
// ---- shapes this file owns (the thread row itself is ThreadRow, ui/app/threads.ts)
interface ThvThread extends ThreadRow {
  deletable?: boolean;
  root_id?: string;
  audience_group_id?: string;
  earlier?: unknown;
}
// What a message quotes: the server's `reply_to`, or one made here from the message it names.
interface ThvQuote {
  id: string;
  author?: string | undefined;
  author_name?: string | undefined;
  excerpt?: string | undefined;
  hidden?: boolean | undefined;
  deleted?: boolean | undefined;
}
interface ThvReaction {
  emoji: string;
  count: number;
  actors: string[];
}
interface ThvFileRef {
  id?: string;
  title?: string;
  name?: string;
}
interface ThvUpdateRef {
  id?: string;
  title?: string;
  kind?: string;
  day?: string;
}
interface ThvRefs {
  update?: ThvUpdateRef | (string & ThvUpdateRef) | null;
  update_request?: unknown;
  card?: string;
  follow_up?: unknown;
  chat_goal?: unknown;
  goal_status?: unknown;
  goal_action?: unknown;
  credential_request?: string;
  action?: string;
  read_back?: unknown;
  earlier_messages?: { thread_id?: string };
  files?: ThvFileRef[];
  attachments?: ChatFile[];
  /** The runner's notices (backend/execution.py split_runner_notices): plain lines. */
  warnings?: string[];
  turn_id?: string | null;
  /** The call a message belongs to; `card` marks the call's own card (ui/app/thread-call.ts callCardHTML). */
  call?: { id?: string; card?: boolean; meeting_id?: string };
  [key: string]: unknown;
}
// A message in the timeline as this view holds it: the server's row, with the fields the stream and this file add.
interface ThvMessage {
  id: string;
  seq?: number;
  thread_id?: string | undefined;
  root_id?: string;
  conversation_id?: string;
  sender_id?: string;
  recipient_id?: string | null;
  body?: string | null;
  kind?: string;
  created_at?: string;
  edited_at?: string | null;
  revision?: number | undefined;
  hidden?: boolean;
  deleted?: boolean;
  earlier?: boolean;
  collapsed?: boolean;
  unread?: boolean;
  count?: number | string;
  mentions?: string[];
  in_reply_to?: string | null;
  reply_to?: ThvQuote | null | undefined;
  routed?: { to?: ({ actor?: string; name?: string } | null)[] } | null | undefined;
  sub_thread?: { id?: string; last_read_seq?: number | string } | null | undefined;
  run?: ChatRun;
  refs?: ThvRefs;
  reactions?: ThvReaction[];
  [key: string]: unknown;
}
// A message the stream or a send brings, wrapped with where it belongs.
interface ThvMessageEvent {
  id?: string | undefined;
  thread_id?: string | undefined;
  root_id?: string | undefined;
  conversation_id?: string | undefined;
  revision?: number;
  deleted?: boolean;
  message?: ThvMessage | null;
}
// The live events of the `threads` topic as this file reads them, on top of ThreadsEvent.
interface ThvLiveEvent extends ThreadsEvent {
  bots?: ThvBotRow[];
  routed?: ThvMessage['routed'];
}
// A held message (rule holds): what the server says of it.
interface ThvHeld {
  id: string;
  text?: string;
  operation?: string;
  author?: string;
  state?: string;
  outcome?: string;
  can_send?: boolean;
  nonce?: string;
  rules?: { text: string }[];
}
interface ThvFileRow {
  id: string;
  title?: string;
  name?: string;
  locator?: string;
  url?: string;
  archived?: boolean;
}
interface ThvLibFile {
  id: string;
  title: string;
}
// A message on its way out: shown as "Checking…", then sent, held or failed.
interface ThvPending {
  key: string;
  text: string;
  files: File[];
  lib: ThvLibFile[];
  state: 'checking' | 'held' | 'failed';
  replyTo?: string;
  quote?: ThvQuote | null;
  error?: string;
  held?: ThvHeld;
  canSend?: boolean;
  nonce?: string;
  messageId?: string;
}
// What a send answers: the message (alone or wrapped), a held verdict, a DM made on the way, and the rules' warnings.
interface ThvSendReply extends Partial<ThvMessage> {
  held?: ThvHeld;
  message?: ThvMessage;
  thread?: ThvThread;
  root_id?: string;
  warnings?: {rule?: string; actor?: string}[];
}
interface ThvMention {
  key: string;
  label: string;
  handle: string;
  kind: 'bot' | 'person';
}
interface ThvMentionState {
  start: number;
  matches: ThvMention[];
  at: number;
}
interface ThvOpts {
  id?: string;
  home?: string;
  dm?: string;
  card?: boolean;
  header?: boolean | string;
  menu?: boolean;
  actsHost?: HTMLElement | null;
  compactEmpty?: boolean;
  hideGone?: boolean | HTMLElement | null;
  focus?: string;
  goals?: string | null;
  canPost?: boolean;
  members?: boolean;
  back?: string;
  empty?: string;
}
// The composer of a bot's Home or DM or a thread: what pill.js, slash-commands.js and chat-goal.js read off it.
interface ThvComposer {
  files: File[];
  lib: ThvLibFile[];
  mention: ThvMentionState | null;
  slug: string | null;
  thread: boolean;
  running: { run_id?: string | null } | null;
  stopping: boolean;
  ways: boolean;
  label: () => void;
  buttons: () => void;
  goalsOf: () => ThvView['goals'];
  clear: () => void;
  command: (text: string) => Promise<void>;
  task: (text: string) => Promise<void>;
  newChat: (() => Promise<void>) | null;
  el: HTMLElement;
  reach: ReturnType<typeof composeReach> & { update(): void };
  goalMode?: boolean;
}
// Everything that belongs to one conversation: a view that moves to another (a fresh DM) starts from this again.
interface ThvConv {
  id: string;
  thread: ThvThread | null;
  breadcrumb: ThvCrumb[];
  messages: ThvMessage[];
  next: string | null;
  state: 'loading' | 'empty' | 'ready' | 'gone' | 'error';
  execution: ChatExecution | null | undefined;
  readAt: number;
  openSeq: number;
  readSent: Map<string, number>;
  marks: Map<string, number>;
  pending: ThvPending[];
  replyTo: string | null;
  botStatus: ThvBotRow[] | null;
  openUpdates: Set<string>;
  openLines: Set<string>;
  flash: string;
  focus: string;
  error: string;
  memberList: { actor: string }[] | null;
  memberMore: boolean;
  run: { openSteps: Set<string>; steps: Record<string, string>; openFolds: Set<string> };
}
interface ThvView extends ThvConv {
  key: number;
  host: HTMLElement;
  opts: ThvOpts;
  follow: (() => void) | null;
  gone: boolean;
  el: HTMLElement;
  goals?: ReturnType<typeof chatGoalThread>;
  shown?: ResizeObserver;
  // Absent until the composer is first drawn; every reader below guards for that or runs after the draw.
  composer: ThvComposer;
  flashTimer?: ReturnType<typeof setTimeout>;
  pressQuiet: number;   // 0 until a long press
  olderRead?: boolean;
  // What a live event changed and the next frame draws (thvLater); null once drawn.
  later?: ThvLater | null;
  // The list's markup above its status rows, as last drawn: a status-only draw replaces only what follows it.
  listRows?: string;
}
// The parts of a view a change touches: the header, the message rows, the bots' status rows at the list's end, the composer.
type ThvPart = 'head' | 'list' | 'status' | 'composer';
interface ThvLater {
  parts: Set<ThvPart>;
  stick: boolean;   // any change in the frame asked to stay at the bottom
  read: boolean;    // send read marks once the messages are drawn
}
// One row of the drawn list: a message, a fold of hidden or earlier ones, or a collapsed sub-thread's link.
interface ThvRow {
  m?: ThvMessage;
  fold?: string;
  id?: string;
  ids?: string[];
  more?: ThvMessage;
}
// A page of the timeline (GET /v2/threads/<id>/timeline).
interface ThvTimeline {
  thread?: ThvThread;
  breadcrumb?: ThvCrumb[];
  messages?: ThvMessage[];
  next_cursor?: string | null;
  read_marks?: Record<string, number | string>;
  bot_status?: ThvBotRow[];
}

const THV_VIEWS = new Set<ThvView>();
// One author's messages within this long of each other share one header.
const THV_GROUP_MS = 5 * 60 * 1000;
// The reaction picker: these, then what the thread already uses.
const THV_EMOJI = ['👍', '❤️', '😂', '🎉', '👀', '🙏'];
const THV_SVG = {
  react: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/></svg>',
  reply: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 5 5v6"/></svg>',
  copy: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
  quote: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 5 5v6"/></svg>',
};
let THV_N = 0;
const thvPath = (id: string | undefined) => '/v2/threads/' + encodeURIComponent(String(id));
const thvHuman = (a: unknown) => String(a || '').startsWith('human:');
const thvMine = (m: ThvMessage | null | undefined) => !!m && m.sender_id === myActor();
// Addressed to the viewer: written by a person, sent to them, or naming them. Other bot posts are activity.
const thvAddressed = (m: ThvMessage | null | undefined) => thvHuman(m?.sender_id) || m?.recipient_id === myActor() || (m?.mentions || []).includes(myActor());

// Unsent words and files per thread, Home or DM, kept while the view is off screen (another tab, page or bot).
const THV_DRAFTS = new Map<string, {text: string; files: File[]; lib: ThvLibFile[]}>();
const thvDraftKey = (view: ThvView) => view.opts.id ? 't:' + view.opts.id : view.opts.home ? 'h:' + view.opts.home : 'd:' + (view.opts.dm || '');
function thvDraftSave(view: ThvView) {
  const C = view.composer, key = thvDraftKey(view);
  if (!C) return;
  const text = C.el.querySelector<HTMLTextAreaElement>('.p-text')?.value || '';
  THV_DRAFTS.delete(key);
  if (text.trim() || C.files.length || C.lib.length) THV_DRAFTS.set(key, {text, files: C.files.slice(), lib: C.lib.slice()});
  if (THV_DRAFTS.size > 50) THV_DRAFTS.delete(THV_DRAFTS.keys().next().value!);
}
// The words (not the files) are also kept in this browser, so a reload brings them back as the old bot chat's did
// (#138). They are the signed-in person's in this org (pill.ts chatOwnedRead), as the old chat's drafts were.
const THV_DRAFT_TEXTS = 'hub.thread.drafts';
const thvDraftTexts = (): Record<string, string> => {
  const saved = chatOwnedRead(localStorage, THV_DRAFT_TEXTS);
  return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved as Record<string, string> : {};
};
function thvDraftTextKeep(view: ThvView, text: string) {
  const all = thvDraftTexts(), key = thvDraftKey(view);
  if (text.trim()) all[key] = text; else delete all[key];
  const keys = Object.keys(all);
  for (const old of keys.slice(0, Math.max(0, keys.length - 50))) delete all[old];
  chatOwnedWrite(localStorage, THV_DRAFT_TEXTS, all, !Object.keys(all).length);
}
// Sent or cleared: the draft is gone from memory and from this browser.
function thvDraftDrop(view: ThvView) {
  THV_DRAFTS.delete(thvDraftKey(view));
  thvDraftTextKeep(view, '');
}

// `opts`: {id} a thread, {home: actor} that teammate's Home, or {dm: actor} the viewer's DM with them (made on the
// first message). {card: true} keeps the list to a box inside a page; {header: false} leaves out the title line, and
// {header: 'compact'} keeps only its actions (call, Members, the thread menu; {menu: false} leaves the menu out), drawn into `actsHost` when given (a bar
// the page owns). {compactEmpty: true} draws a Home with no messages as its composer alone, never a blank timeline, and
// hides the card (`hideGone`) from a viewer who can't post either. {focus: message id} shows that message. {goals: slug}
// (a bot's Home or DM) brings back the old chat's bar (pill.js): the ways to send (Set as goal among them) and the
// pinned goal's bar (chat-goal.js), the bot's avatar that switches bots on a phone, Stop while it runs, and the "/" menu.
function threadMount(host: HTMLElement, opts: ThvOpts = {}): ThvView {
  const view = {key: ++THV_N, host, opts, ...thvConvFresh(), follow: null, gone: false} as ThvView;
  THV_VIEWS.add(view);
  host.innerHTML = `<section class="thv${opts.card ? ' thv-card' : ''}" data-thv="${view.key}">
    ${opts.header === false ? '' : '<header class="thv-head"></header>'}
    ${opts.goals ? '<section class="chat-goal thv-goal" aria-label="Goal" hidden></section>' : ''}
    <div class="thv-scroll"><button type="button" class="ghost small thv-older" hidden>Earlier</button>
      <div class="thv-list" role="log" aria-live="polite"></div></div>
    <button type="button" class="stick-pill thv-unread" hidden>Unread</button>
    <div class="thv-composer"></div></section>`;
  view.el = host.querySelector('.thv') as HTMLElement;
  if (opts.goals) view.goals = chatGoalThread(view, opts.goals);
  thvWire(view);
  // A view that is not displayed (a hidden tab or pane) reads nothing; it marks read once it shows.
  if (window.ResizeObserver) { view.shown = new ResizeObserver(() => thvMarkRead(view)); view.shown.observe(view.el); }
  void thvLoad(view);
  return view;
}
// Everything that belongs to one conversation: a view that moves to another (a fresh DM) starts from this again.
const thvConvFresh = (): ThvConv => ({id: '', thread: null, breadcrumb: [], messages: [], next: null, state: 'loading', execution: null,
  readAt: 0, openSeq: 0, readSent: new Map(), marks: new Map(), pending: [], replyTo: null, botStatus: null, openUpdates: new Set(), openLines: new Set(),
  flash: '', focus: '', error: '', memberList: null, memberMore: false, run: {openSteps: new Set(), steps: {}, openFolds: new Set()}});
function threadUnmount(view: ThvView | null | undefined) {
  if (!view || view.gone) return;
  thvDraftSave(view);
  view.gone = true;
  view.follow?.();
  view.shown?.disconnect();
  THV_VIEWS.delete(view);
  // The page's bar the view drew its actions into is empty again, unless another view has taken it since.
  const bar = view.opts.actsHost;
  if (bar && bar.dataset.thv === String(view.key)) { bar.innerHTML = ''; bar.onclick = null; delete bar.dataset.thv; }
}
// Views whose host left the page stop following.
function thvSweep() { for (const v of THV_VIEWS) if (!v.host.isConnected) threadUnmount(v); }

// ----------------------------------------------------------------- loading
// Flat: the root's whole readable subtree in time order, each reply quoting what it answers (an older server sends its
// tree, drawn flat in the order it comes, its quotes made here).
const thvTimeline = (id: string, cursor?: string | null) => thvPath(id) + '/timeline?limit=50&flat=1' + (cursor != null ? '&cursor=' + encodeURIComponent(cursor) : '');
async function thvResolve(view: ThvView): Promise<string> {
  const o = view.opts;
  if (o.id) return o.id;
  if (o.home) {
    const t = await get<ThvThread>('/v2/threads/home/' + encodeURIComponent(o.home), {fresh: true});
    view.thread = t;
    return t?.id || '';
  }
  if (o.dm) {
    try {
      const r = await get<ThvThread & {thread?: ThvThread}>('/v2/threads/dm?create=false&with=' + encodeURIComponent(o.dm), {fresh: true});
      const t = r?.thread !== undefined ? r.thread : r;
      if (t?.id) { view.thread = t; return t.id; }
    } catch (e) { if (errorStatus(e) !== 404) throw e; }
  }
  return '';
}
function thvSetId(view: ThvView, id: string) {
  if (view.id === id) return;
  view.follow?.();
  view.id = id;
  view.follow = liveFollowThread(id);
  if (view.goals) { void chatGoalThreadId(view); void thvRunRead(view); }
}
async function thvLoad(view: ThvView): Promise<void> {
  try {
    const id = await thvResolve(view);
    if (view.gone) return;
    if (!id) { view.state = 'empty'; thvDraw(view); if (view.goals) void chatGoalThreadId(view); return; }
    thvSetId(view, id);
    const r = await get<ThvTimeline>(thvTimeline(id), {fresh: true});
    if (view.gone) return;
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || [];
    thvStatusTake(view, r);
    view.messages = thvMerge(view, r.messages || []);
    view.next = r.next_cursor || null;
    view.readAt = Number(r.read_marks?.[id] ?? view.thread?.me?.last_read_seq) || 0;
    view.openSeq = thvMaxSeq(view.messages);
    thvLearnMarks(view, r);
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: !view.opts.focus});
    thvMarkRead(view);
    void callLoad(view);
    void thvHoldsLoad(view);
    void thvMembersLoad(view);
    if (view.opts.focus) void thvFocus(view, view.opts.focus);
  } catch (e) {
    if (view.gone) return;
    view.state = errorStatus(e) === 404 ? 'gone' : 'error';
    view.error = errorMessage(e, '');
    // A Home the viewer may not read leaves no box behind on a person's or a team's page.
    if (view.state === 'gone' && view.opts.hideGone) { (view.opts.hideGone === true ? view.host : view.opts.hideGone).hidden = true; return; }
    thvDraw(view);
  }
}
// A message posted elsewhere (Start onboarding) into a DM on screen: that DM reads itself again, finding the thread
// if it was not there yet.
function threadsReloadDM(actor: string) {
  for (const v of THV_VIEWS) if (v.opts.dm === actor && !v.gone) { if (v.id) void thvRefresh(v); else void thvLoad(v); }
}
function threadsReloadHome(actor: string) {
  for (const v of THV_VIEWS) if (v.opts.home === actor && !v.gone) { if (v.id) void thvRefresh(v); else void thvLoad(v); }
}
// The viewer's own held messages in this thread come back after a reload, still with Edit and Discard. A held edit (made
// through the API) is not shown here: this view does not edit messages.
async function thvHoldsLoad(view: ThvView) {
  if (!view.id) return;
  try {
    const r = await get<{holds?: ThvHeld[]}>('/v2/rule-holds?thread=' + encodeURIComponent(view.id) + '&limit=20', {fresh: true});
    const mine = (r?.holds || []).filter(h => h.author === myActor() && h.state === 'held' && !view.pending.some(p => p.held?.id === h.id)).slice(0, 5);
    for (const s of mine) {
      const h = await get<ThvHeld>('/v2/rule-holds/' + encodeURIComponent(s.id), {fresh: true}).catch(() => null);
      if (!h || h.operation === 'threads_edit' || view.gone || view.pending.some(p => p.held?.id === h.id)) continue;
      view.pending.push({key: 'p' + (++THV_N), text: h.text || '', files: [], lib: [], state: 'held',
                         held: h, canSend: !!h.can_send, nonce: h.nonce || ''});
    }
    if (mine.length && !view.gone) thvDraw(view);
  } catch {}           // a server without rule holds has nothing to bring back
}
const thvMaxSeq = (list: ThvMessage[]) => list.reduce((n, m) => Math.max(n, Number(m.seq) || 0), 0);
// Where the viewer had read each thread on the page, as first read: a flat page's `read_marks` ({thread id: seq}), or an
// older server's inline sub-threads. What is past its own thread's mark shows as new.
function thvLearnMarks(view: ThvView, r: ThvTimeline | null | undefined) {
  for (const [tid, seq] of Object.entries(r?.read_marks || {})) if (tid !== view.id && !view.marks.has(tid)) view.marks.set(tid, Number(seq) || 0);
  for (const m of r?.messages || []) if (m.sub_thread?.id && !view.marks.has(m.sub_thread.id)) view.marks.set(m.sub_thread.id, Number(m.sub_thread.last_read_seq) || 0);
}
// A page read from the server merged with what the stream already put on screen: the higher revision of each message
// wins, and newer messages the page does not have yet stay at the bottom (`seq` orders the whole install).
function thvMerge(view: {messages: ThvMessage[]}, fresh: ThvMessage[]) {
  const had = new Map(view.messages.map(m => [m.id, m]));
  const out = fresh.map(f => {
    const e = had.get(f.id);
    // A row the server now hides or has deleted stays so; otherwise the later revision wins.
    if (!e || f.hidden || f.deleted || (Number(e.revision) || 0) <= (Number(f.revision) || 0) && !e.deleted) return f;
    return {...f, ...e, reply_to: f.reply_to ?? e.reply_to, routed: f.routed ?? e.routed};
  });
  const ids = new Set(out.map(m => m.id)), top = thvMaxSeq(out);
  for (const e of view.messages) if (!ids.has(e.id) && !e.hidden && e.seq && Number(e.seq) > top) { out.push(e); ids.add(e.id); }
  return out;
}
// The first page read again after a change the stream could not place; older pages already read stay above it.
async function thvRefresh(view: ThvView): Promise<void> {
  if (view.gone || !view.id) return thvLoad(view);
  try {
    const r = await get<ThvTimeline>(thvTimeline(view.id), {fresh: true});
    if (view.gone) return;
    const fresh = r.messages || [], ids = new Set(fresh.map(m => m.id));
    const first = fresh.find(m => !m.hidden && m.seq)?.seq ?? Infinity;
    // Everything above the page read again stays: whatever came before the page's first row.
    const start = view.messages.findIndex(m => !m.hidden && (Number(m.seq) || 0) >= first);
    const older = view.olderRead ? view.messages.slice(0, start < 0 ? view.messages.length : start).filter(m => !ids.has(m.id)) : [];
    view.thread = r.thread || view.thread;
    view.breadcrumb = r.breadcrumb || view.breadcrumb;
    thvStatusTake(view, r);
    thvLearnMarks(view, r);
    const olderIds = new Set(older.map(m => m.id));
    const merged = thvMerge({messages: view.messages.filter(m => !olderIds.has(m.id))}, fresh);
    view.messages = [...older, ...merged];
    if (!view.olderRead) view.next = r.next_cursor || null;
    view.state = view.messages.length ? 'ready' : 'empty';
    thvDraw(view, {stick: thvAtBottom(view)});
    thvMarkRead(view);
  } catch (e) {
    if (errorStatus(e) === 404) { view.state = 'gone'; thvDraw(view); }
  }
}
// One earlier page, above what is on screen.
async function thvPageOlder(view: ThvView) {
  const r = await get<ThvTimeline>(thvTimeline(view.id, view.next), {fresh: true});
  if (view.gone) return false;
  thvLearnMarks(view, r);
  const have = new Set(view.messages.map(m => m.id));
  view.messages = [...(r.messages || []).filter(m => !have.has(m.id)), ...view.messages];
  view.next = r.next_cursor || null;
  view.olderRead = true;
  return true;
}
// A message to show (from search, or a quote): earlier pages are read until it is there, then it is scrolled to and
// marked. False when it is not in the pages this view can read.
// With `toSeq` (the message's own seq) the pages are read down to it, however far.
async function thvFocus(view: ThvView, mid: string, {flash = false, toSeq = 0} = {}): Promise<boolean> {
  const above = () => !toSeq || !view.messages.length || Number(view.messages[0]!.seq) > toSeq;
  for (let pages = 0; !view.messages.some(m => m.id === mid) && view.next && pages < (toSeq ? 200 : 10) && above() && !view.gone; pages++) {
    const ok = await thvPageOlder(view).catch(() => false);
    if (!ok) return false;
    thvDraw(view);
  }
  if (!view.messages.some(m => m.id === mid)) return false;
  thvUpdateReveal(view, mid);
  if (flash) thvFlash(view, mid); else { view.focus = mid; thvDraw(view); }
  view.el.querySelector(thvRowSel(mid))?.scrollIntoView({block: 'center'});
  return true;
}
// A message's row, whether a message or a gray line (sourceLineHTML).
const thvRowSel = (mid: string) => `.th-msg[data-mid="${CSS.escape(mid)}"], .th-line[data-mid="${CSS.escape(mid)}"]`;
// A quoted message, brought into view and lit for a moment.
function thvFlash(view: ThvView, mid: string) {
  view.flash = mid; thvDraw(view);
  clearTimeout(view.flashTimer);
  view.flashTimer = setTimeout(() => { if (view.flash === mid) { view.flash = ''; thvDraw(view); } }, 1600);
}
async function thvGoTo(view: ThvView, mid: string) {
  const at = () => view.el.querySelector(thvRowSel(mid));
  // Found after the flash's redraw, which may put a new node in the old one's place.
  if (at()) { thvFlash(view, mid); at()?.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); return; }
  if (await thvFocus(view, mid, {flash: true}) || view.gone) return;
  // Not in this view's pages (a sub-thread's message, or past what it reads): its thread opens at it.
  const root = view.thread?.root_id || view.id;
  if (root && root !== view.id) { location.hash = threadHref(root) + '?focus=' + encodeURIComponent(mid); return; }
  // Further back than a few pages: read down to its seq. One the server will not give is "not shown".
  const seq = await get('/v2/messages/' + encodeURIComponent(mid), {fresh: true}).then(r => Number(r?.seq) || 0, () => 0);
  if (view.gone || seq && await thvFocus(view, mid, {flash: true, toSeq: seq}) || view.gone) return;
  if (thvQuotesFollow(view, {id: mid, hidden: true})) thvDraw(view);
}
async function thvOlder(view: ThvView, button: HTMLButtonElement) {
  if (!view.next) return;
  button.disabled = true;
  const box = view.el.querySelector('.thv-scroll') as HTMLElement, from = box.scrollHeight - box.scrollTop;
  try {
    if (await thvPageOlder(view)) { thvDraw(view); box.scrollTop = box.scrollHeight - from; }
  } catch (e) { toast(errorMessage(e, ''), true); }
  button.disabled = false;
}
// ----------------------------------------------------------------- drawing
const thvCanPost = (view: ThvView) => view.opts.canPost ?? (view.thread ? !!view.thread.me?.can_post : view.state === 'empty' && !!view.opts.dm);
// Archive on a root thread the viewer manages (not a Home): it leaves Threads and Catch up (threadsArchive). An archived
// one offers Unarchive.
const thvArchivable = (t: ThvThread | null | undefined) => !!t && !t.parent_id && t.audience !== 'home' && !!t.me?.can_manage;
const thvArchiveHTML = (t: ThvThread | null | undefined) => thvArchivable(t) && t ? `<button type="button" class="ghost thv-arch" data-thv-archive aria-label="${t.archived_at ? 'Unarchive' : 'Archive'}" title="${t.archived_at ? 'Unarchive' : 'Archive'}"><span class="nav-icon" aria-hidden="true">${t.archived_at ? 'unarchive' : 'archive'}</span></button>` : '';
// Delete, only when the server says the thread is deletable: every message deleted and nobody else in it.
const thvDeleteHTML = (t: ThvThread | null | undefined) => thvArchivable(t) && t?.deletable ? `<button type="button" class="ghost thv-del" data-thv-delete aria-label="Delete thread" title="Delete thread"><span class="nav-icon" aria-hidden="true">delete</span></button>` : '';
function thvActsHTML(view: ThvView) {
  const t = view.thread!, list = view.memberList;
  const members = !t.parent_id && ['members', 'personal', 'company', 'team'].includes(t.audience);
  // On the Threads page the members show as faces and a count, which open the same list.
  const n = list ? list.length : 0, more = view.memberMore ? '+' : '';
  const people = members && list ? `<button type="button" class="thv-people" data-thv-members aria-label="Members, ${n}${more}" title="Members">${
      list.slice(0, 3).map(m => actorAvatar(m.actor, 22)).join('')}<span class="thv-people-n">${n}${more}</span></button>`
    : members ? '<button type="button" class="ghost small" data-thv-members>Members</button>' : '';
  return `${people}${callHeadHTML(view)}${thvArchiveHTML(t)}${thvDeleteHTML(t)}
      ${view.opts.menu === false ? '' : `<button type="button" class="ghost thv-menu-btn" data-thv-menu aria-haspopup="dialog" aria-label="Thread settings" title="Thread settings"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`}`;
}
// The viewer's own pin, on a thread the rail lists (a server with pins says `me.pinned`).
const thvPinnable = (t: ThvThread | null | undefined): t is ThvThread & {me: ThreadMe} => !!t?.me && 'pinned' in t.me && threadOnRail(t);
const thvPinHTML = (t: ThvThread | null | undefined) => thvPinnable(t) ? `<button type="button" class="thv-pin${t.me.pinned ? ' on' : ''}" data-thv-pin aria-pressed="${!!t.me.pinned}" aria-label="Pin" title="${t.me.pinned ? 'Unpin' : 'Pin'}">${TR_SVG.pin(!!t.me.pinned)}</button>` : '';
function thvHeadHTML(view: ThvView) {
  const t = view.thread;
  if (!t) return '';
  if (view.opts.header === 'compact') return `<div class="thv-titleline thv-compact"><span class="spacer"></span>${thvActsHTML(view)}</div>`;
  const crumbs = (view.breadcrumb || []).filter(c => c.id !== t.id);
  // A thread the rail lists says what it is with its mark (# or a lock); a Home or a DM says it in words.
  const hash = threadOnRail(t);
  // A DM's face already says who it is with, so it carries no "Private"; the name keeps the room on a phone.
  const aud = hash || threadIsDm(t as ThreadRow) ? '' : t.audience === 'company' ? 'Company' : t.audience === 'team' ? groupName(t.audience_group_id!)
    : t.audience === 'home' ? 'Home' : threadPrivate(t) ? 'Private' : '';
  const back = crumbs.length ? `<a class="thv-back" href="${threadHref(crumbs[crumbs.length - 1]!.id)}" aria-label="Back" title="Back">‹</a>`
    : view.opts.back ? `<a class="thv-back thv-back-rail" href="${esc(view.opts.back)}" aria-label="Threads" title="Threads">‹</a>` : '';
  return `${crumbs.length ? `<nav class="thv-crumbs" aria-label="Thread path">${crumbs.map(c =>
      `<a href="${threadHref(c.id)}">${esc(c.title || 'Thread')}</a>`).join('<span aria-hidden="true">›</span>')}</nav>` : ''}
    <div class="thv-titleline">${back}
      ${hash ? '' : threadMarkHTML(t, 22)}<h1 class="thv-title">${hash ? threadHashHTML(t) : ''}${esc(threadTitle(t, view.messages))}</h1>${thvPinHTML(t)}
      ${aud ? `<span class="thv-aud muted">${esc(aud)}</span>` : ''}<span class="spacer"></span>${thvActsHTML(view)}</div>`;
}
// Who is in it, for the faces in the header: a thread the rail lists, on the Threads page (`opts.members`). A DM keeps
// its Members button, so a phone has room for the name.
async function thvMembersLoad(view: ThvView) {
  const t = view.thread;
  if (!view.opts.members || !threadOnRail(t)) return;
  try {
    const r = await get<{members?: {actor: string}[]; next_cursor?: string | null}>(thvPath(t!.id) + '/members?limit=100', {fresh: true});
    if (view.gone) return;
    view.memberList = r?.members || []; view.memberMore = !!r?.next_cursor;
    thvDraw(view);
  } catch { /* the Members button stays */ }
}
// Pin or unpin for the viewer; the rail moves the row at once.
async function thvPinToggle(view: ThvView, button: HTMLButtonElement) {
  const t = view.thread; if (!thvPinnable(t)) return;
  const on = !t.me.pinned;
  button.disabled = true;
  try {
    const r: ThvThread | null = on ? await put(thvPath(t.id) + '/pin', {}) : await writeRequest('DELETE', thvPath(t.id) + '/pin');
    view.thread = {...t, ...(r?.id ? r : {}), me: {...t.me, ...(r?.me || {}), pinned: r?.me ? !!r.me.pinned : on}};
    threadsPut(view.thread);
    thvDraw(view);
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
}
// The header's Archive takes the thread off the list and goes back to it (Undo is in the toast); on an archived thread
// Unarchive brings it back here.
async function thvArchive(view: ThvView, button: HTMLButtonElement) {
  const t = view.thread; if (!t) return;
  button.disabled = true;
  try {
    if (!t.archived_at) {
      await threadsArchive(t.id);
      if (threadsLastGet() === t.id) threadsLastSet('');
      location.hash = THREADS_ROUTE;
      return;
    }
    const r: ThvThread = await patch(thvPath(t.id), {archived: false});
    view.thread = {...t, ...r};
    threadsUnarchived(view.thread);
    thvDraw(view);
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
}
// Delete asks first. The thread leaves Threads and Catch up at once and reads as not found after that.
async function thvDelete(view: ThvView, button: HTMLButtonElement) {
  const t = view.thread; if (!t || !(await askConfirm("Delete this thread? It can't be undone."))) return;
  button.disabled = true;
  try {
    await writeRequest('DELETE', thvPath(t.id));
    const gone = {...t, archived_at: t.archived_at || new Date().toISOString()};
    threadsPut(gone);
    threadsArchivedGone(t.id, threadsRootLearn(gone)?.dm);
    threadsRecount();
    if (threadsLastGet() === t.id) threadsLastSet('');
    location.hash = THREADS_ROUTE;
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
}
// A message the viewer may not read is one quiet row, never its words. Earlier shared history folds into one "Earlier
// messages" row; two such rows meet where one page of it ends and the next begins.
function thvRows(view: ThvView) {
  const rows: ThvRow[] = [];
  for (const m of view.messages) {
    if (m.hidden) {
      const fold = m.earlier ? 'earlier' : 'hidden', last = rows[rows.length - 1];
      if (last?.fold === fold) { last.ids!.push(m.id); continue; }
      rows.push({fold, id: m.id, ids: [m.id]});
      continue;
    }
    // An older server's tree folds a long sub-thread's start: a quiet link to that sub-thread stands for it.
    if (m.collapsed) { rows.push({more: m, id: m.id}); continue; }
    if (chatGoalControl(m)) continue;          // a goal's /goal message: the goal bar shows its effect (chat-goal.js)
    rows.push({m});
  }
  return rows;
}
function thvIsNew(view: ThvView, m: ThvMessage) {
  if (!m.seq || thvMine(m)) return false;
  // Each message against its own thread's mark (an older sub-thread's flattened in, else the root's). Arriving at the
  // bottom of the view on screen is not news.
  const tid = m.thread_id || view.id, mark = tid !== view.id && view.marks.has(tid) ? view.marks.get(tid)! : view.readAt;
  return m.seq > mark && m.seq <= view.openSeq;
}
const thvBot = (a: unknown) => String(a || '').startsWith('bot:');
// Who a message is from, as its header and a quote of it name it: the Assistant relaying for a person reads as
// "Ana's Assistant"; a row the server wrote has no sender (rowSource).
const thvWhoName = (m: ThvMessage) => { const src = rowSource(m); return src.kind === 'assistant_for' || isSourceLine(m) ? src.name : actorName(m.sender_id); };
function thvWhoHTML(a: string | undefined) {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && S.emps.some(e => e.id === slug)) return `<a class="th-who" href="#/bot/${esc(slug)}">${esc(actorName(a))}</a>`;
  if (pid) return `<a class="th-who" href="#/person/${esc(encodeURIComponent(pid))}">${esc(actorName(a))}</a>`;
  return `<span class="th-who">${esc(actorName(a) || 'Someone')}</span>`;
}
const THV_BOT_TAG = '<span class="th-bot">BOT</span>';
// An update is its own record with its own page: the Home shows a card that opens it, never the update as chat.
function thvUpdateHTML(m: ThvMessage) {
  const u = m.refs?.update, id = typeof u === 'object' ? u?.id : u;
  const title = (typeof u === 'object' && u?.title) || v2Firstline(m.body) || 'Update';
  if (!id) return `<div class="th-body">${esc(title)}</div>`;
  const q = new URLSearchParams({...(u?.kind ? {kind: u.kind} : {}), ...(u?.day ? {day: u.day} : {}), update: id});
  return `<a class="th-update" href="${UPDATES}?${q}"><span class="th-update-k">Update</span>
    <strong>${esc(title)}</strong></a>`;
}
// Update cards are notices the server writes with refs.card 'update'; older rows may carry only refs.update.
const thvIsUpdateCard = (m: ThvMessage) => m.refs?.card === 'update' || m.kind === 'update' || (!!m.refs?.update && m.kind === 'notice');
function thvFilesHTML(m: ThvMessage) {
  const lib = (m.refs?.files || []).filter(f => f?.id);
  return (m.refs?.attachments?.length ? chatAttachmentsHTML(m.refs.attachments, m.id) : '')
    + (lib.length ? `<div class="chat-files">${lib.map(f => `<button type="button" class="pill th-libfile" data-th-file="${esc(f.id)}" data-name="${esc(f.title || f.name || 'file')}">${esc(f.title || f.name || 'file')}</button>`).join('')}</div>` : '');
}
// The server lists who reacted ({emoji, count, actors}); "mine" is whether the viewer is among them.
const thvReactedMine = (r: ThvReaction | null | undefined) => (r?.actors || []).includes(myActor());
function thvReactionsHTML(m: ThvMessage) {
  const rs = (m.reactions || []).filter(r => r.count > 0);
  if (!rs.length) return '';
  return `<div class="th-reacts">${rs.map(r => `<button type="button" class="th-react${thvReactedMine(r) ? ' me' : ''}" data-th-react="${esc(r.emoji)}" aria-pressed="${thvReactedMine(r) ? 'true' : 'false'}" title="${esc(r.emoji)}">${esc(r.emoji)} <span class="tnum">${r.count}</span></button>`).join('')}</div>`;
}

// ---- replies: one line quoting the message answered
// Plain words of a message's start, as a quote shows it.
const thvExcerpt = (text: unknown) => chatLine(String(text || '').replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#>~|]/g, ''), 120);
const thvQuoteFrom = (t: ThvMessage): ThvQuote => t.hidden ? {id: t.id, hidden: true} : {id: t.id, author: t.sender_id, author_name: thvWhoName(t), excerpt: thvExcerpt(t.deleted ? 'Deleted' : t.body)};
// What a message quotes: the server's `reply_to` (null: nothing), else, from a server without it or the stream, the
// message it names (`in_reply_to`) or, for one in a sub-thread, the message that sub-thread opened under.
function thvQuoteOf(view: ThvView, m: ThvMessage): ThvQuote | null {
  if (m.reply_to !== undefined) return m.reply_to || null;
  const target = m.in_reply_to ? view.messages.find(x => x.id === m.in_reply_to)
    : m.thread_id && m.thread_id !== view.id ? view.messages.find(x => x.sub_thread?.id === m.thread_id) : null;
  if (target) return thvQuoteFrom(target);
  return m.in_reply_to ? {id: m.in_reply_to, author: '', author_name: '', excerpt: ''} : null;
}
function thvQuoteHTML(q: ThvQuote | null | undefined) {
  if (!q?.id) return '';
  if (q.hidden) return `<span class="th-quote th-quote-hidden">${THV_SVG.quote}<span class="th-quote-text">${q.deleted ? 'Message deleted' : 'Message not shown'}</span></span>`;
  const who = q.author_name || (q.author ? actorName(q.author) : '');
  return `<button type="button" class="th-quote" data-th-quote="${esc(q.id)}" aria-label="${esc(who ? 'Reply to ' + who : 'Reply')}">${THV_SVG.quote}${
    who ? `<b class="th-quote-who">${esc(who)}</b>` : ''}<span class="th-quote-text">${esc(q.excerpt || '')}</span></button>`;
}
// Under a person's message that went through routing: which bots it asked, or that none was.
function thvRoutedHTML(m: ThvMessage) {
  const r = m.routed;
  if (!r || !Array.isArray(r.to) || !thvHuman(m.sender_id)) return '';
  const names = r.to.map(x => x?.name || actorName(x?.actor)).filter(Boolean);
  return `<div class="th-routed">${names.length ? esc('Asked ' + names.join(', ')) : 'No bot asked'}</div>`;
}

// ---- one message
const thvRunOf = (m: ThvMessage) => thvBot(m.sender_id) && !!(m.run?.steps || m.run?.did?.length || m.refs?.turn_id);
const thvClock = (iso: string | undefined) => { const d = new Date(iso || ''); return isNaN(Number(d)) ? '' : d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}); };
// Whether `m` sits under `prev`'s header: the same author within five minutes, and `m` neither a reply nor a bot's run.
function thvJoins(view: ThvView, prev: ThvMessage | null, m: ThvMessage) {
  if (!prev || prev.sender_id !== m.sender_id || prev.deleted || m.deleted || thvIsUpdateCard(m) || thvIsUpdateCard(prev)) return false;
  if (thvQuoteOf(view, m) || thvRunOf(m)) return false;
  const a = Date.parse(prev.created_at || ''), b = Date.parse(m.created_at || '');
  return a > 0 && b > 0 && Math.abs(b - a) <= THV_GROUP_MS;
}
function thvMsgActsHTML(view: ThvView, m: ThvMessage) {
  const can = thvCanPost(view);
  return `<div class="th-acts" role="toolbar" aria-label="Message">
    <button type="button" class="th-act" data-th-pick aria-label="React" title="React" aria-haspopup="menu" aria-expanded="false">${THV_SVG.react}</button>${
    can ? `<button type="button" class="th-act" data-th-reply aria-label="Reply" title="Reply">${THV_SVG.reply}</button>` : ''}${
    m.body ? `<button type="button" class="th-act" data-th-copy aria-label="Copy" title="Copy">${THV_SVG.copy}</button>` : ''}</div>`;
}
function thvMsgHTML(view: ThvView, m: ThvMessage, cont = false) {
  const relay = rowSource(m).kind === 'assistant_for';
  const bot = thvBot(m.sender_id);
  // In a bot's own Home or DM every bot line is that bot, so the BOT tag would only repeat the page.
  const ownBot = bot && !!view.opts?.goals && m.sender_id === `bot:${view.opts.goals}`;
  // A bot's own words take a row of Copy, 👍 and 👎 under them, apart from the emoji reactions (ui/app/feedback.ts).
  const fb = !m.deleted && m.id && bot ? fbKey('message', m.id) : '';
  const fresh = thvIsNew(view, m), quiet = fresh && !thvAddressed(m);
  const cls = ['th-msg', cont && 'th-cont', view.focus === m.id && 'th-focus', view.flash === m.id && 'th-flash', m.refs?.follow_up && 'th-followup',
    thvMine(m) && 'th-mine', fresh && 'th-new', quiet && 'th-new-quiet', m.deleted && 'th-deleted'].filter(Boolean).join(' ');
  const head = cont ? '' : `<div class="th-head">${relay ? `<span class="th-who">${esc(rowSource(m).name)}</span>` : thvWhoHTML(m.sender_id)}${(bot && !ownBot) || relay ? THV_BOT_TAG : ''}<time title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>${
    m.edited_at && !m.deleted ? '<span class="th-edited muted">edited</span>' : ''}</div>`;
  let body;
  if (m.deleted) body = '<div class="th-body muted">Deleted</div>';
  else if (thvIsUpdateCard(m)) body = thvUpdateHTML(m);
  else if (m.refs?.call?.card) body = callCardHTML(m);
  // A met or stopped goal: the one line the chat showed (chat-goal.js).
  else if (m.refs?.chat_goal && m.refs?.goal_status) body = chatGoalNoticeHTML(m, view.goals?.goal);
  // A card a bot left for you fills in from its action or request (ui/assistant.ts, ui/credential-card.ts).
  else if (m.refs?.credential_request) body = `<div data-credential-host="${esc(m.refs.credential_request)}"><div class="asst-state">Loading the card…</div></div>`;
  else if (m.refs?.action) body = `<div data-action-host="${esc(m.refs.action)}"><div class="asst-state">Loading the card…</div></div>`;
  // The Assistant's read-back of what it will approve is shown exactly as written, never formatted.
  else if (m.refs?.read_back) body = `<div class="th-body read-back" style="white-space:pre-line">${esc(m.body || '')}</div>`;
  else body = `<div class="th-body md">${safeMd(m.body || '', {shortLinks: true})}</div>${
    // The notice a bot's manager got about its Home's closed history (no longer sent; old ones keep their button).
    m.refs?.earlier_messages?.thread_id ? `<button type="button" class="ghost small th-earlier-open" data-th-earlier="${esc(m.refs.earlier_messages.thread_id)}">Open earlier messages to Read</button>` : ''}`;
  // A bot's reply reads as Claude's does: one folded line of what its run did first (ui/app/chat.ts), then the words,
  // the runner's warnings and the message's cards.
  const run = !m.deleted && bot ? v2RunHTML(m, view.run) : '';
  if (!m.deleted && bot)
    body += (m.refs?.warnings || []).map(w => `<div class="chat-warn">${esc(String(w).charAt(0).toUpperCase() + String(w).slice(1))}</div>`).join('') + v2MessageCards(m);
  const quote = m.deleted ? '' : thvQuoteHTML(thvQuoteOf(view, m));
  const av = cont ? `<time class="th-cont-time" title="${esc(fmt(m.created_at))}">${esc(thvClock(m.created_at))}</time>`
    : relay ? avatar(assistantBot(), 32) : actorAvatar(m.sender_id, 32);
  return `<div class="${cls}" data-mid="${esc(m.id)}" data-message="${esc(m.id)}" data-tid="${esc(m.thread_id || view.id)}" data-seq="${esc(m.seq ?? '')}">
    <div class="th-av">${av}</div>
    <div class="th-main">${head}${quote}${run}${body}${m.deleted ? '' : thvFilesHTML(m) + thvReactionsHTML(m) + fbRowHTML(fb, m.body)}${thvRoutedHTML(m)}</div>
    ${m.deleted ? '' : thvMsgActsHTML(view, m)}
  </div>`;
}
function thvFoldHTML(view: ThvView, row: ThvRow) {
  if (row.fold === 'hidden') return `<div class="th-fold th-notshown" data-mid="${esc(row.id)}"><span>Not shown</span></div>`;
  return `<div class="th-fold th-earlier" data-mid="${esc(row.id)}"><span>Earlier messages</span>${
    view.thread?.earlier ? `<button type="button" class="linkish" data-th-earlier="${esc(view.id)}">Open earlier messages to Read</button>` : ''}</div>`;
}
const thvMoreHTML = (m: ThvMessage) => `<a class="th-fold th-more" href="${threadHref(m.thread_id)}" data-mid="${esc(m.id)}"><span>${esc(m.count)} earlier ${Number(m.count) === 1 ? 'reply' : 'replies'}</span></a>`;
function thvPendingHTML(view: ThvView, p: ThvPending) {
  const text = `<div class="th-body">${esc(p.text)}${p.files?.length ? ` <span class="muted">· ${p.files.length} file${p.files.length === 1 ? '' : 's'}</span>` : ''}</div>`;
  const quote = thvQuoteHTML(p.quote);
  const av = `<div class="th-av">${actorAvatar(myActor(), 32)}</div>`;
  if (p.state === 'held') {
    const h: Partial<ThvHeld> = p.held || {}, rules = (h.rules || []).map(r => `<div class="th-held-rule">${ruTextHTML(r.text)}</div>`).join('');
    return `<div class="th-msg th-mine th-held" data-pending="${p.key}">${av}
      <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state">${h.outcome === 'escalated' ? 'Waiting for review' : 'Held'}</span></div>${quote}${text}${rules}
        <div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button>${
          p.canSend ? '<button type="button" class="ghost small" data-held-send>Send anyway</button>' : ''}<button type="button" class="ghost small" data-held-drop>Discard</button></div></div></div>`;
  }
  return `<div class="th-msg th-mine th-pending${p.state === 'failed' ? ' th-failed' : ''}" data-pending="${p.key}">${av}
    <div class="th-main"><div class="th-head"><span class="th-who">You</span><span class="th-state" role="status">${p.state === 'failed' ? esc(p.error || 'Not sent') : 'Checking…'}</span></div>${quote}${text}
    ${p.state === 'failed' ? '<div class="th-held-acts"><button type="button" class="ghost small" data-held-edit>Edit</button><button type="button" class="ghost small" data-held-drop>Discard</button></div>' : ''}</div></div>`;
}

// ---- the bots' status, at the end of the timeline
// One row per bot a person is waiting on (`bot_status`, docs/dev/threads-contract.md): working on what, up next after
// what, or why it can't reply and where that is fixed. A server without it sends none and shows none.
const THV_STATUS_WORD: Record<ThvBotRow['state'], string> = {working: 'working', up_next: 'up next', cant_reply: "can't reply"};
const thvMinSec = (since: string | undefined) => { const s = Math.max(0, Math.floor((Date.now() - (Date.parse(since || '') || Date.now())) / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
function thvStatusTake(view: ThvView, r: ThvTimeline | null | undefined) {
  if (r && Array.isArray(r.bot_status)) view.botStatus = r.bot_status.slice(0, 5);
}
function thvStatusHTML(view: ThvView, b: ThvBotRow | null | undefined) {
  if (!b?.actor || !THV_STATUS_WORD[b.state]) return '';
  const slug = actorSlug(b.actor), name = b.name || actorName(b.actor);
  const word = b.state === 'working' ? `working · <span class="tnum" data-th-since="${esc(b.since || '')}">${thvMinSec(b.since)}</span>` : THV_STATUS_WORD[b.state];
  const stop = b.state === 'working' && slug && settingsCanManageBot(botRow(slug))
    ? `<button type="button" class="ghost small th-status-act" data-th-stop="${esc(slug)}" data-run="${esc(b.run_id || '')}">Stop</button>` : '';
  const fix = b.state === 'cant_reply' && b.fix?.href ? `<a class="ghost small th-status-act" href="${esc(b.fix.href)}">${esc(b.fix.label || 'Fix')}</a>` : '';
  return `<div class="th-status th-status-${esc(b.state)}" data-th-status="${esc(b.actor)}" role="status">
    <div class="th-av">${actorAvatar(b.actor, 32)}</div>
    <div class="th-main"><div class="th-head"><span class="th-who">${esc(name)}</span>${THV_BOT_TAG}<span class="th-status-word">${word}</span></div>${
      b.detail ? `<div class="th-status-detail">${b.state === 'working' ? '<span class="th-status-dot" aria-hidden="true"></span>' : ''}${esc(b.detail)}</div>` : ''}</div>
    ${stop}${fix}</div>`;
}
// The working rows' clocks tick in place, once a second, while any is on screen.
let THV_TICK: ReturnType<typeof setInterval> | 0 = 0;
function thvTick() {
  let any = false;
  for (const v of THV_VIEWS) {
    if (v.gone || !v.host.isConnected || !v.el.getClientRects().length) continue;
    for (const el of v.el.querySelectorAll<HTMLElement>('[data-th-since]')) { any = true; const t = thvMinSec(el.dataset.thSince); if (el.textContent !== t) el.textContent = t; }
  }
  if (!any) { clearInterval(THV_TICK); THV_TICK = 0; }
}
// A bot's reply landed: its row goes, before the server's next status says so.
function thvStatusReplied(view: ThvView, m: ThvMessage) {
  if (!view.botStatus?.length || !thvBot(m?.sender_id)) return;
  view.botStatus = view.botStatus.filter(b => b.actor !== m.sender_id);
}

// ---- a daily update request and the bot's turn that writes the update
// The server sends a bot its update request as a notice from the keeper that carries `refs.update_request`
// (backend/updates.py send_request). The bot writes its update in the run that request starts, so the request and that
// run's messages in the same thread read as one "Update generated" line. The run's messages are the bot's first reply to
// the request (its `in_reply_to` names the request) and the bot's later messages from that same turn (`refs.turn_id`
// matches the reply's). A group ends at the first message that is not one of those: a person's, another bot's, the
// keeper's, another turn's message from the same bot (even one right after the reply), a hidden or collapsed row, or a
// deleted message. The next request is the keeper's, so it ends a group too. An update card is never in a group: the
// posted update keeps its own row, as it always did. A deleted request has no refs, so it is an ordinary message again.
const thvUpdateReq = (m: ThvMessage | null | undefined) => m?.sender_id === 'keeper' && !!m.refs?.update_request;
// A message the request's bot posted in this thread, that is not an update card or deleted.
const thvUpdateTurn = (m: ThvMessage, req: ThvMessage) => m?.sender_id === req.recipient_id && m.thread_id === req.thread_id && !m.deleted && !thvIsUpdateCard(m);
// Each message in a group, by id, mapped to the id of the request that starts the group (a request maps to itself).
function thvUpdateGroups(view: ThvView) {
  const of = new Map<string, string>(), msgs = view.messages;
  msgs.forEach((req, i) => {
    if (!thvUpdateReq(req) || !thvBot(req.recipient_id)) return;
    of.set(req.id, req.id);
    let turn;   // the turn that answers this request: set by the bot's first reply to it, undefined until then
    for (let j = i + 1; j < msgs.length && thvUpdateTurn(msgs[j]!, req); j++) {
      const m = msgs[j]!;
      if (turn === undefined) {
        if (m.in_reply_to !== req.id) break;
        turn = m.refs?.turn_id ?? null;
      } else if (m.in_reply_to !== req.id && !(turn && m.refs?.turn_id === turn)) break;
      of.set(m.id, req.id);
    }
  });
  return of;
}
// A message in a shut group opens the group, so a quote or a search hit has its row to scroll to.
function thvUpdateReveal(view: ThvView, id: string) {
  const req = thvUpdateGroups(view).get(id);
  if (req) view.openUpdates.add(req);
  const group = thvLineGroups(view).get(id);
  if (group) view.openLines.add(group);
}
// ---- gray lines: rows no person or bot wrote (rowSource, ui/app/hub-v2.ts). A task update, a built-in's or an automation's
// line, or a system line has no avatar or sender row; two or more task updates in a row are one "N task updates" row,
// shut until opened. A normal message after them starts its own group.
const thvTaskUpdate = (row: ThvRow | undefined) => !!row?.m && !row.m.deleted && isSourceLine(row.m) && rowSource(row.m).kind === 'task_update';
// Each task update in a run of two or more, by id, mapped to its run's key (the first one's id).
function thvLineGroups(view: ThvView) {
  const of = new Map<string, string>(), rows = thvRows(view).filter(r => !(view.opts.home && r.m && thvIsUpdateCard(r.m)));
  for (let i = 0; i < rows.length;) {
    if (!thvTaskUpdate(rows[i])) { i++; continue; }
    let end = i;
    while (end < rows.length && thvTaskUpdate(rows[end])) end++;
    if (end - i > 1) for (const r of rows.slice(i, end)) of.set(r.m!.id, rows[i]!.m!.id);
    i = end;
  }
  return of;
}
// The old notice about a Home's closed history keeps its Open button on the line.
const thvLineHTML = (view: ThvView, m: ThvMessage) => sourceLineHTML(m, {cls: 'th-line' + (view.flash === m.id ? ' th-flash' : ''),
  attrs: ` data-mid="${esc(m.id)}" data-tid="${esc(m.thread_id || view.id)}" data-seq="${esc(m.seq ?? '')}"`,
  tail: m.refs?.earlier_messages?.thread_id ? `<button type="button" class="linkish th-earlier-open" data-th-earlier="${esc(m.refs.earlier_messages.thread_id)}">Open earlier messages to Read</button>` : ''});
// The line stands for its group: a button that opens or shuts it. Its panel is always in the markup (empty while shut),
// so aria-controls names an element either way.
const thvUpdateLineHTML = (req: string, panel: string, open: boolean) => `<div class="th-upd"><button type="button" class="th-upd-btn" data-th-update="${esc(req)}" aria-expanded="${open}" aria-controls="${panel}">› Update generated</button></div>`;

// What the message rows read from the thread itself (thvMsgActsHTML, thvMoreHTML).
const thvRowsKey = (view: ThvView) => `${thvCanPost(view)}|${!!view.thread?.earlier}`;
// The list is its rows (thvRowsHTML), then the bots' status rows (thvTailHTML), so a status change redraws only those.
function thvTailHTML(view: ThvView, rows: string) {
  if (['loading', 'gone', 'error'].includes(view.state)) return '';
  const status = (view.botStatus || []).map(b => thvStatusHTML(view, b)).join('');
  return status || rows ? status : `<div class="th-empty muted">${esc(view.opts.empty || 'No messages yet')}</div>`;
}
function thvRowsHTML(view: ThvView) {
  if (view.state === 'loading') return '<div class="th-empty muted">Loading…</div>';
  if (view.state === 'gone') return '<div class="th-empty muted">Not found</div>';
  if (view.state === 'error') return `<div class="th-empty err">${esc(view.error || 'Could not load')}</div>`;
  const rows = thvRows(view), out = [], groups = thvUpdateGroups(view), lineGroups = thvLineGroups(view);
  let divided = false, prev: ThvMessage | null = null;
  // One row drawn where it sits. The "New" line goes above the first message the viewer had not read when the thread opened,
  // and only a row that is drawn can take it: a shut group's rows are not drawn, so they are not news on the screen.
  const draw = (row: ThvRow) => {
    if (!divided && row.m && thvIsNew(view, row.m)) { out.push('<div class="th-newline" role="separator" aria-label="New"><span>New</span></div>'); divided = true; prev = null; }
    if (row.m && !row.m.deleted && isSourceLine(row.m)) { out.push(thvLineHTML(view, row.m)); prev = null; return; }
    if (row.m) { out.push(thvMsgHTML(view, row.m, thvJoins(view, prev, row.m))); prev = row.m; return; }
    out.push(row.fold ? thvFoldHTML(view, row) : thvMoreHTML(row.more!));
    prev = null;
  };
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    // Updates have their own page (Updates); a Home does not repeat them as cards.
    if (view.opts.home && row.m && thvIsUpdateCard(row.m)) continue;
    const req = row.m && groups.get(row.m.id);
    const run = !req && row.m && lineGroups.get(row.m.id);
    if (run) {
      // A run of task updates: one row, its lines under it while it is open.
      const ms = [];
      let end = i;
      for (; end < rows.length; end++) {
        const r = rows[end]!;
        if (view.opts.home && r.m && thvIsUpdateCard(r.m)) continue;
        if (!r.m || lineGroups.get(r.m.id) !== run) break;
        ms.push(r.m);
      }
      out.push(sourceRunsHTML(ms, {line: (m: ThvMessage) => thvLineHTML(view, m), draw: () => '', open: (run: ThvMessage[]) => run.some(m => view.openLines.has(m.id))}));
      prev = null;
      i = end - 1;
      continue;
    }
    if (!req) { draw(row); continue; }
    // A request and its turn: the line, then their rows under it while it is open.
    let end = i;
    while (end < rows.length && rows[end]!.m && groups.get(rows[end]!.m!.id) === req) end++;
    const open = view.openUpdates.has(req), panel = 'th-upd-' + view.key + '-' + req.replace(/\W/g, '_');
    out.push(thvUpdateLineHTML(req, panel, open));
    out.push(`<div class="th-upd-body" id="${panel}"${open ? '' : ' hidden'}>`);
    prev = null;
    if (open) for (const r of rows.slice(i, end)) draw(r);
    out.push('</div>');
    prev = null;
    i = end - 1;
  }
  for (const p of view.pending) out.push(thvPendingHTML(view, p));
  return out.join('');
}
const thvAtBottom = (view: ThvView) => { const b = view.el?.querySelector('.thv-scroll'); return !b || b.scrollHeight - b.scrollTop - b.clientHeight < 60; };
const THV_ALL: ThvPart[] = ['head', 'list', 'status', 'composer'];
// Draw a view now: what someone just did shows at once. A draw a live event left for the next frame is folded in, so it
// does not run again. `parts` is what changed (all of it unless said); the message list is built only when 'list' is.
function thvDraw(view: ThvView, {stick = false, parts = THV_ALL}: {stick?: boolean; parts?: Iterable<ThvPart>} = {}): void {
  const later = view.later, want = new Set(parts);
  view.later = null;
  if (later) { for (const p of later.parts) want.add(p); stick ||= later.stick; }
  if (view.gone) return;
  if (!view.host.isConnected) { threadUnmount(view); return; }
  if (want.has('head')) {
    const head = view.opts.actsHost?.isConnected ? view.opts.actsHost : view.el.querySelector<HTMLElement>('.thv-head');
    if (head) { const h = thvHeadHTML(view); patchHTML(head, h); head.hidden = !h; }
    if (head === view.opts.actsHost) { head!.dataset.thv = String(view.key); view.el.querySelector('.thv-head')?.setAttribute('hidden', ''); }
  }
  const list = view.el.querySelector('.thv-list') as HTMLElement, box = view.el.querySelector('.thv-scroll') as HTMLElement;
  const rows = want.has('list') || want.has('status'), keep = rows && (stick || thvAtBottom(view));
  // Only the status rows changed: the rows above them stay as they are, unread and unbuilt.
  if (want.has('status') && !want.has('list') && view.listRows !== undefined && patchHTMLTail(list, view.listRows, thvTailHTML(view, view.listRows), visualSwap) !== null) {
    if (!THV_TICK && list.querySelector('[data-th-since]')) THV_TICK = setInterval(thvTick, 1000);
  } else if (rows) thvListDraw(view, list, box);
  if (want.has('composer')) thvComposerDraw(view);
  if (keep) box.scrollTop = box.scrollHeight;
  if (later?.read) thvMarkRead(view);
}
function thvListDraw(view: ThvView, list: HTMLElement, box: HTMLElement) {
  if (view.opts.compactEmpty) {
    const blank = view.state === 'empty' && !view.pending.length, card = view.opts.hideGone === true ? view.host : view.opts.hideGone;
    view.el.classList.toggle('thv-blank', blank);
    if (card && ['empty', 'ready'].includes(view.state)) card.hidden = blank && !thvCanPost(view);
  }
  // Cards both trees share keep their node, as in a chat (ui/app/visual-cards.ts).
  const rows = thvRowsHTML(view);
  view.listRows = rows;
  patchHTML(list, rows + thvTailHTML(view, rows), visualSwap);
  const reload = () => thvRefresh(view);
  if (list.querySelector('[data-action-host]')) void window.assistantChat?.cards(list, {get, post, esc, toast, reload});
  if (list.querySelector('[data-credential-host]')) void window.credentialCards?.mount(list, {get, post, esc, toast, reload});
  (view.el.querySelector('.thv-older') as HTMLElement).hidden = !view.next;
  chatThumbsLoad?.(list);
  const unread = list.querySelector('.th-new');
  (view.el.querySelector('.thv-unread') as HTMLElement).hidden = !unread || thvVisible(box, unread);
  fbSync();
  if (!THV_TICK && list.querySelector('[data-th-since]')) THV_TICK = setInterval(thvTick, 1000);
}
// A live event changed a view: its data is already updated, in the order events came; the drawing waits for the next
// frame, once per view however many events came, and only for the parts they touched. A view closed or off the page by
// then is not drawn. A tab in the background draws on its first frame back.
let THV_FRAME = 0;
function thvLater(view: ThvView, parts: ThvPart[], {stick = false, read = false} = {}) {
  if (view.gone) return;
  view.later ||= {parts: new Set(), stick: false, read: false};
  const later = view.later;
  for (const p of parts) later.parts.add(p);
  later.stick ||= stick;
  later.read ||= read;
  if (!THV_FRAME) THV_FRAME = requestAnimationFrame(thvFrame);
}
function thvFrame() {
  THV_FRAME = 0;
  for (const view of [...THV_VIEWS]) if (view.later) thvDraw(view, {parts: []});
}
const thvVisible = (box: Element, el: Element) => { const a = box.getBoundingClientRect(), b = el.getBoundingClientRect(); return b.bottom > a.top && b.top < a.bottom; };

// ----------------------------------------------------------------- read state
// Up to the last message drawn, per thread drawn: the thread on screen and every sub-thread inline in it. A read mark is one
// number, so a sub-thread whose collapsed "N more" still holds something unread stays where it is until that is opened.
function thvMarkRead(view: ThvView) {
  // Messages a frame has yet to draw are not read: that draw sends the marks.
  if (view.later) { view.later.read = true; return; }
  if (document.hidden || view.gone || !view.id || !['ready', 'empty'].includes(view.state) || !view.el.getClientRects().length) return;
  const max = new Map(), held = new Set(view.messages.filter(m => m.collapsed && m.unread).map(m => m.thread_id));
  for (const m of view.messages) {
    if (m.hidden || m.collapsed || !m.seq) continue;
    const tid = m.thread_id || view.id;
    if (held.has(tid)) continue;
    max.set(tid, Math.max(max.get(tid) || 0, Number(m.seq)));
  }
  for (const [tid, seq] of max) {
    const sent = view.readSent.get(tid) ?? (tid === view.id ? view.readAt : view.marks.get(tid) ?? 0);
    if (seq <= sent) continue;
    view.readSent.set(tid, seq);
    post(thvPath(tid) + '/read', {seq}).catch(() => {});
    threadsMarkedRead(tid, seq);
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) for (const v of THV_VIEWS) thvMarkRead(v); });

// ----------------------------------------------------------------- live
// A message edited or deleted: the chips that quote it say what it says now.
function thvQuotesFollow(view: ThvView, target: ThvMessage) {
  let changed = false;
  for (const [i, m] of view.messages.entries()) {
    if (m.reply_to?.id !== target.id || m.reply_to.hidden) continue;
    view.messages[i] = {...m, reply_to: target.deleted ? {id: target.id, hidden: true, deleted: true} : target.hidden ? {id: target.id, hidden: true}
      : {...m.reply_to, excerpt: thvQuoteFrom(target).excerpt}};
    changed = true;
  }
  return changed;
}
// A message changed in a thread on screen: an edit or a delete in place, a new one at the bottom in time order (a reply
// in an older sub-thread too, as the flat timeline reads it); anything the view cannot place reads the first page again.
function thvApplyMessage(view: ThvView, d: ThvMessageEvent) {
  const tid = d.thread_id || d.message?.thread_id || d.message?.conversation_id, inView = tid === view.id || view.messages.some(m => m.thread_id === tid || m.sub_thread?.id === tid);
  if (!inView && d.root_id !== (view.thread?.root_id || view.id)) return false;
  const at = view.messages.findIndex(m => m.id === d.id);
  if (d.deleted) {
    const quoted = thvQuotesFollow(view, {id: d.id!, deleted: true});
    if (at < 0) return quoted;
    view.messages[at] = {...view.messages[at]!, deleted: true, body: '', revision: d.revision};
    return true;
  }
  const m = d.message;
  if (!m) { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); return false; }
  if (at >= 0) {
    if ((Number(m.revision) || 0) < (Number(view.messages[at]!.revision) || 0)) return false;
    // The stream's view carries no `reply_to` or `routed`; the read's stay.
    const was = view.messages[at]!;
    view.messages[at] = {...was, ...m, reply_to: m.reply_to !== undefined ? m.reply_to : was.reply_to, routed: m.routed !== undefined ? m.routed : was.routed, sub_thread: was.sub_thread};
    thvQuotesFollow(view, view.messages[at]);
    return true;
  }
  view.pending = view.pending.filter(p => p.messageId !== m.id);
  const row = {...m, thread_id: tid};
  // Seq orders the whole install, so a late arrival still lands in its place.
  let i = view.messages.length;
  while (i > 0 && Number(view.messages[i - 1]!.seq) > Number(row.seq)) i--;
  view.messages.splice(i, 0, row);
  if (view.state === 'empty') view.state = 'ready';
  thvStatusReplied(view, row);
  return true;
}
function threadViewsLive(topic: string, d: ThvMessageEvent & ThvLiveEvent) {
  thvSweep();
  for (const view of THV_VIEWS) {
    if (topic === 'reset') { liveSoon('thv:' + view.key, () => thvRefresh(view), 150); continue; }
    if (topic === 'threads') {
      // The bots' status rows, patched as they are (a server with `bot_status`).
      if (d.thread_id === (view.thread?.root_id || view.id) && (d.kind === 'bot_status' || Array.isArray(d.bots))) {
        view.botStatus = (d.bots || []).slice(0, 5);
        thvLater(view, ['status']);
        continue;
      }
      // Who a message asked, known just after it lands.
      if (d.kind === 'routed' && d.message_id) {
        const m = view.messages.find(x => x.id === d.message_id);
        if (m) { m.routed = d.routed; thvLater(view, ['list']); }
        continue;
      }
      if (d.thread_id === view.id && d.thread) {
        // A rename redraws the header and the composer's line; the rows only when what they show changed (Reply, Earlier).
        const was = thvRowsKey(view);
        view.thread = {...view.thread, ...d.thread};
        thvLater(view, thvRowsKey(view) === was ? ['head', 'composer'] : THV_ALL);
      }
      // A pin from another tab, or a manager's for everyone: the header's pin follows (`pinned` is this viewer's state).
      else if (d.thread_id === view.id && d.kind === 'pinned' && view.thread?.me) {
        view.thread = {...view.thread, pinned_for_everyone: d.pinned_for_everyone ?? view.thread.pinned_for_everyone, me: {...view.thread.me, pinned: !!d.pinned}};
        thvLater(view, ['head']);
      }
      if (d.thread_id === view.id && String(d.kind || '').startsWith('call.')) liveSoon('call:' + view.key, () => callLoad(view), 200);
      // Who may read changed: read again.
      if (d.thread_id === view.id && d.kind === 'members') liveSoon('thv-members:' + view.key, () => thvMembersLoad(view), 300);
      if (d.thread_id === view.id && ['audience', 'members'].includes(d.kind!)) liveSoon('thv:' + view.key, () => thvRefresh(view), 150);
      continue;
    }
    // The title and the composer's line name the thread from its messages; the status rows can lose a bot that replied.
    if (topic === 'messages' && thvApplyMessage(view, d)) thvLater(view, THV_ALL, {read: true});
  }
}
liveOn('messages', (d: ThvMessageEvent & {goal_id?: string}) => {
  if (d?.thread_id || d?.message?.thread_id) threadViewsLive('messages', d);
  // A goal set, paused, met or stopped in a bot's Home or DM on screen: read it as the goal route shows it.
  if (d?.goal_id) for (const view of THV_VIEWS) if (view.goals && view.id && view.id === d.conversation_id && !view.gone) void chatGoalThreadId(view);
});
// A run in a bot's Home or DM on screen started, ended or was stopped: Stop follows it. Its output steps change nothing.
liveOn('runs', (d: {output?: unknown; conversation_id?: string}) => {
  if (d?.output) return;
  for (const view of THV_VIEWS) if (view.goals && view.id && view.id === d?.conversation_id && !view.gone)
    liveSoon('thv-run:' + view.key, () => thvRunRead(view), 120);
});
// A message waiting behind the bot's other run shows Stop while that run goes on.
liveOn('bots', (d: {bot?: string}) => { for (const view of THV_VIEWS) if (view.composer?.slug === d?.bot && !view.gone) thvStopSync(view); });
liveOn('reset', () => { for (const view of THV_VIEWS) if (view.goals && !view.gone) void thvRunRead(view); });

// ----------------------------------------------------------------- the composer
function thvComposerDraw(view: ThvView) {
  const host = view.el.querySelector('.thv-composer') as HTMLElement;
  const can = thvCanPost(view) && !['loading', 'gone', 'error'].includes(view.state);
  // Hidden, not removed, while posting is closed (archived, loading): unarchiving shows it again with its draft.
  if (!can) { host.hidden = true; return; }
  host.hidden = false;
  if (view.composer) { thvReplyingDraw(view); thvComposerLabel(view); return; }
  // One line, as the bot chat's pill was: (a bot's Home or DM: the bot, on a phone) the words, Attach, the ways to send
  // and Send, which Stop replaces while the bot runs. `pill-thread` keeps the task question card's frame (.ask,
  // ui/styles/task-files.css) off it. A bot's composer takes the old chat's "/" commands (slash-commands.js) through the
  // hooks below. `ways`: Send shows the person's kept way and every message names it; Set as goal is for one goal
  // (chat-goal.js reads it too).
  const bot = view.opts.goals || null;
  const C = view.composer = {files: [] as File[], lib: [] as ThvLibFile[], mention: null, slug: bot, thread: true, running: null, stopping: false, ways: !!bot,
    label: () => thvComposerLabel(view), buttons: () => thvComposerButtons(view), goalsOf: () => view.goals,
    clear: () => thvComposerClear(view), command: (text: string) => thvComposerCommand(view, text), task: (text: string) => thvComposerTask(view, text),
    newChat: view.opts.dm ? () => thvNewDm(view) : null} as ThvComposer;
  // Above the box: the message being answered.
  // Files waiting to be sent sit above the box, an image as its thumbnail (#138).
  host.innerHTML = `<div class="thv-replying" hidden></div><div class="ask pill-thread thv-ask"><div class="p-chips"></div><div class="p-pill">
      ${bot ? pillBotHTML(bot) : ''}
      ${bot ? '<span class="thv-goal-chip" hidden>Goal<button type="button" aria-label="Leave goal" title="Leave goal">×</button></span>' : ''}
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="Message" placeholder="Message"></textarea>
      <span class="thv-attach-wrap"><button class="p-attach p-icon" type="button" title="Attach" aria-label="Attach" aria-haspopup="menu" aria-expanded="false">${ICON_CLIP}</button>
        <div class="p-menu thv-attach-menu" role="menu" hidden><button type="button" role="menuitem" data-attach="upload">Upload</button><button type="button" role="menuitem" data-attach="library">Library file</button></div></span>
      <input type="file" multiple hidden class="p-file">
      ${bot ? `<button class="p-stop p-send-icon" type="button" aria-label="Stop" title="Stop" hidden>${ICON_STOP}</button>
      <span class="p-sendbox"><button class="p-way p-icon" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Ways to send" title="Ways to send"><span class="nav-icon" aria-hidden="true">expand_more</span></button>` : ''}
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button>${bot ? `
      <div class="p-menu p-ways" role="menu" aria-label="Ways to send" hidden></div></span>` : ''}</div>
    ${composeFootHTML()}
    ${bot ? '<div class="p-cmd" hidden></div>' : ''}
    <div class="p-menu thv-mentions" role="listbox" aria-label="Mention" hidden></div>
    <div class="thv-lib" hidden></div></div>`;
  C.el = host.querySelector('.thv-ask') as HTMLElement;
  // Follow and "Reaches N" under the box (pill.js composeReach): the way Send shows, the words' @mentions, the reply.
  C.reach = composeReach(C.el.querySelector('.p-foot'), {thread: () => thvDm(view) ? '' : view.id,
    mode: () => C.ways ? thvWayMode(view) : '', text: () => (C.el.querySelector('.p-text') as HTMLTextAreaElement).value, reply: () => view.replyTo});
  (host.querySelector('.thv-replying') as HTMLElement).onclick = ev => { if (eventElement(ev)?.closest('[data-reply-cancel]')) thvReplyStart(view, null); };
  const box = C.el.querySelector('.p-text') as HTMLTextAreaElement, file = C.el.querySelector('.p-file') as HTMLInputElement, menu = C.el.querySelector('.thv-attach-menu') as HTMLElement;
  const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 160) + 'px'; };
  box.addEventListener('input', () => { grow(); thvMentionFind(view); thvComposerButtons(view); thvDraftTextKeep(view, box.value); });
  // A pasted screenshot (or file) is a chip; pasted words go in at the cursor (pill.ts pillPasteWire).
  pillPasteWire(box, files => { C.files.push(...files); thvChips(view); });
  box.addEventListener('keydown', (ev: KeyboardEvent) => {
    if (C.mention && thvMentionKey(view, ev)) return;
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void thvComposerSend(view); }
  });
  box.addEventListener('blur', () => setTimeout(() => thvMentionClose(view), 150));
  (C.el.querySelector('.p-send') as HTMLElement).onclick = () => void thvComposerSend(view);
  if (bot) {
    pillBotWire(C.el.querySelector('.p-bot'), bot);
    (C.el.querySelector('.p-stop') as HTMLElement).onclick = () => void pillStop(C);
    slashAttach(C);
    thvWaysWire(view);
  }
  const attach = C.el.querySelector('.p-attach') as HTMLElement;
  attach.onclick = ev => {
    const open = menu.hidden;
    menu.hidden = !open; (ev.currentTarget as HTMLElement).setAttribute('aria-expanded', String(open));
  };
  menu.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-attach]'); if (!b) return;
    menu.hidden = true; attach.setAttribute('aria-expanded', 'false');
    if (b.dataset.attach === 'upload') file.click(); else void thvLibOpen(view);
  };
  file.onchange = () => { C.files.push(...file.files!); file.value = ''; thvChips(view); };
  (C.el.querySelector('.p-chips') as HTMLElement).onclick = ev => {
    // A thumbnail opens the image full size (chat.ts chatImageOpen); Esc or a click outside closes it.
    const thumb = eventElement(ev)?.closest<HTMLElement>('.p-thumb'); if (thumb) { ev.preventDefault(); chatImageOpen(thumb); return; }
    const rm = eventElement(ev)?.closest<HTMLElement>('[data-rm]'); if (!rm) return;
    ev.preventDefault();
    const [kind, i] = rm.dataset.rm!.split(':');
    if (kind === 'lib') C.lib.splice(Number(i), 1); else pillPreviewForget(C.files.splice(Number(i), 1)[0]);
    thvChips(view);
  };
  thvDropZone(view);
  const draft = THV_DRAFTS.get(thvDraftKey(view)), kept = draft ? '' : thvDraftTexts()[thvDraftKey(view)];
  if (draft) { box.value = draft.text; C.files.push(...draft.files); C.lib.push(...draft.lib); thvChips(view); requestAnimationFrame(grow); }
  else if (typeof kept === 'string' && kept) { box.value = kept; requestAnimationFrame(grow); }
  if (view.goals) chatGoalRender(view.goals);
  thvComposerLabel(view);
  thvReplyingDraw(view);
  thvStopSync(view);
}
// Send says what it will do: in a bot's Home or DM the way's icon (pill.js sendWayPaint), the target in goal mode,
// with the Goal chip and the empty box saying so too; elsewhere the arrow. An empty box only dims it.
function thvComposerLabel(view: ThvView) {
  const C = view.composer, btn = C?.el.querySelector<HTMLButtonElement>('.p-send'), box = C?.el.querySelector<HTMLTextAreaElement>('.p-text');
  if (!btn) return;
  const goal = chatGoalThreadMode(C);
  if (C.ways) sendWayPaint(btn, thvWayMode(view));
  else if (!btn.disabled) { btn.setAttribute('aria-label', 'Send'); btn.title = 'Send'; }
  const chip = C.el.querySelector<HTMLElement>('.thv-goal-chip');
  if (chip) chip.hidden = !goal;
  box!.placeholder = goal ? `Goal for ${botDisplayName(C.slug)}…` : thvPlaceholder(view);
  thvComposerButtons(view);
}
// Who the words go to: the bot in its Home or DM, the thread by its name, the people in a DM.
function thvPlaceholder(view: ThvView) {
  const t = view.thread, slug = view.opts.goals;
  // The bot's own name only: botDisplayName adds the slug when two bots share a name, and the page already says which.
  if (slug) return 'Message ' + (slug === assistantBot() ? assistantName() : botRow(slug)?.display_name || botRow(slug)?.slug || slug);
  if (t && threadOnRail(t)) return 'Message #' + threadTitle(t);
  const name = t ? threadTitle(t, view.messages) : view.opts.dm ? actorName(view.opts.dm) : '';
  return name ? 'Message ' + name : 'Message';
}
// The message being answered, above the box: who wrote it, its start, and × to answer nothing.
function thvReplyingDraw(view: ThvView) {
  const row = view.el.querySelector<HTMLElement>('.thv-replying'); if (!row) return;
  const m = view.replyTo && view.messages.find(x => x.id === view.replyTo);
  if (!m) { view.replyTo = null; row.hidden = true; row.textContent = ''; delete row.dataset.mid; return; }
  const q = thvQuoteFrom(m);
  const html = `<span class="thv-replying-q">${THV_SVG.quote}<b>${esc(q.author_name || 'Message')}</b><span class="th-quote-text">${esc(q.excerpt || '')}</span></span>
    <button type="button" class="ghost thv-replying-x" data-reply-cancel aria-label="Cancel reply" title="Cancel reply">×</button>`;
  if (row.dataset.mid !== m.id) { row.innerHTML = html; row.dataset.mid = m.id; }
  row.hidden = false;
}
function thvReplyStart(view: ThvView, m: ThvMessage | null | undefined) {
  view.replyTo = m?.id || null;
  thvReplyingDraw(view);
  thvFootSync(view);
  if (m) view.composer?.el.querySelector<HTMLTextAreaElement>('.p-text')?.focus();
}
function thvComposerButtons(view: ThvView) {
  const C = view.composer, btn = C?.el.querySelector<HTMLButtonElement>('.p-send'), box = C?.el.querySelector<HTMLTextAreaElement>('.p-text');
  if (!btn) return;
  const empty = !box!.value.trim() && !C.files.length && !C.lib.length;
  btn.classList.toggle('p-off', empty);
  thvFootSync(view);
  // While the bot runs, Stop takes Send's place (and the caret's) until something is typed: a message typed meanwhile
  // still goes to it.
  const stop = C.el.querySelector<HTMLButtonElement>('.p-stop');
  if (!stop) return;
  const show = !!C.running && empty;
  stop.hidden = !show; btn.hidden = show; stop.disabled = !!C.stopping;
  const caret = C.el.querySelector<HTMLElement>('.p-way');
  if (caret) { caret.hidden = show; if (show) thvWaysMenu(view, false); }
  const says = C.stopping ? 'Stopping…' : 'Stop';
  stop.title = says; stop.setAttribute('aria-label', says);
}
// ----------------------------------------------------------------- who it reaches
const thvDm = (view: ThvView) => !!view.opts.dm || threadIsDm(view.thread);
function thvFootSync(view: ThvView) {
  const C = view.composer, foot = C?.el.querySelector('.p-foot');
  if (!foot) return;
  C.reach?.update();
}
// ----------------------------------------------------------------- the ways to send (a bot's Home or DM)
// Steer now, Queue, Defer with its durations, then Set as goal (deliveryMenuHTML, pill.js). Steer, Queue and Defer are
// the person's kept way (sendWayPick); Set as goal enters goal mode (chat-goal.js) for one goal and is never kept.
// Someone who may not message the bot (Write) defers with no deadline, as on a task they are not on.
// Anyone in a DM with the bot may name a way to send; elsewhere it takes the right to direct the bot.
const thvDirects = (view: ThvView) => threadIsDm(view.thread) && !!view.thread?.me?.can_post || botByRef(view.composer?.slug)?.my_access?.write !== false;
// The way a message goes (never goal: a goal is not a message), and what Send shows (goal in goal mode).
const thvSendMode = (view: ThvView) => thvDirects(view) ? SEND_WAY : 'defer:never';
const thvWayMode = (view: ThvView) => chatGoalThreadMode(view.composer) ? 'goal' : thvSendMode(view);
const thvGoalOffered = (view: ThvView) => !!view.composer?.goalMode || !!view.goals?.goalSupported && !goalPinned(view.goals.goal);
function thvWaysMenu(view: ThvView, open: boolean) {
  const C = view.composer, menu = C?.el.querySelector<HTMLElement>('.p-ways'), caret = C?.el.querySelector<HTMLElement>('.p-way');
  if (!menu || !caret) return;
  if (!open && menu.hidden) return;
  const directs = thvDirects(view), current = thvWayMode(view);
  menu.innerHTML = open ? deliveryMenuHTML({steer: directs, queue: directs, goal: thvGoalOffered(view), deadlines: directs,
    current, deferFor: deliveryWay(current) === 'defer' ? deliveryFor(current) : 'never'}) : '';
  menu.hidden = !open;
  caret.setAttribute('aria-expanded', String(!!open));
  // On a phone the keyboard stays up: focus stays in the box.
  if (open && !touchKeyboard()) (menu.querySelector<HTMLElement>('[aria-checked="true"]') || menu.querySelector('button'))?.focus();
}
function thvWayPick(view: ThvView, mode: string) {
  const C = view.composer;
  thvWaysMenu(view, false);
  if (mode === 'goal') { if (!C.goalMode) chatGoalButton(view.goals); }
  else {
    if (C.goalMode) chatGoalButton(view.goals);      // a way to send leaves goal mode
    sendWayPick(mode);
  }
  thvComposerLabel(view);
  if (!touchKeyboard()) C.el.querySelector<HTMLTextAreaElement>('.p-text')?.focus();
}
function thvWaysWire(view: ThvView) {
  const C = view.composer, q = <T extends HTMLElement = HTMLElement>(sel: string) => C.el.querySelector(sel) as T, menu = q('.p-ways'), caret = q('.p-way');
  // The caret, a right-click or a long press on Send open the menu.
  caret.onclick = () => thvWaysMenu(view, menu.hidden);
  sendWayHold(q('.p-send'), () => thvWaysMenu(view, true), () => !q<HTMLButtonElement>('.p-send').disabled);
  menu.onclick = ev => { const b = eventElement(ev)?.closest<HTMLElement>('[data-deliver]'); if (b) thvWayPick(view, b.dataset.deliver!); };
  menu.addEventListener('keydown', ev => deliveryMenuKeys(menu, ev));
  // Escape closes the menu only, wherever focus is in the composer.
  C.el.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || menu.hidden) return;
    ev.preventDefault(); ev.stopPropagation();
    thvWaysMenu(view, false);
    if (!touchKeyboard()) caret.focus();
  }, true);
  q('.thv-goal-chip button').onclick = () => { if (C.goalMode) chatGoalButton(view.goals); q('.p-text').focus(); };
}
// A press outside an open menu closes it; a way picked anywhere repaints every bot composer on screen.
document.addEventListener('pointerdown', ev => {
  for (const v of THV_VIEWS) {
    const box = v.composer?.el.querySelector('.p-sendbox');
    if (box && !(box.querySelector('.p-ways') as HTMLElement).hidden && !box.contains(eventElement(ev))) thvWaysMenu(v, false);
  }
});
SEND_WAY_PAINT.add(() => { for (const v of THV_VIEWS) if (v.composer?.ways && !v.gone) thvComposerLabel(v); });
// ----------------------------------------------------------------- Stop
// The newest run in a bot's Home or DM, as the old chat read it (its conversation snapshot's execution: a thread id is a
// conversation id, docs/dev/threads-contract.md); Stop shows while it runs (chat.js chatRunning).
async function thvRunRead(view: ThvView) {
  if (!view.goals || !S.me?.cloud) return;
  const id = view.id;
  const d = id ? await v2Get(`/v2/conversations/${encodeURIComponent(id)}/snapshot?limit=1`, {fresh: true}) : null;
  if (view.gone || view.id !== id) return;
  view.execution = d?.execution || null;
  thvStopSync(view);
}
function thvStopSync(view: ThvView) {
  const C = view.composer;
  if (!C?.slug) return;
  const running = chatRunning(view.execution, C.slug);
  if (!running === !C.running && running?.run_id === C.running?.run_id) return;
  C.running = running; C.stopping = false;
  thvComposerButtons(view);
}
// ----------------------------------------------------------------- "/" commands (slash-commands.js slashRun)
function thvComposerClear(view: ThvView) {
  const box = view.composer.el.querySelector('.p-text') as HTMLTextAreaElement;
  box.value = ''; box.style.height = '';
  thvDraftDrop(view);
  thvComposerButtons(view);
}
// An empty DM is made first, as its first message would make it.
async function thvDmMake(view: ThvView) {
  if (view.id || !view.opts.dm) return !!view.id;
  const made: ThvThread & {thread?: ThvThread} = await post('/v2/threads/dm', {members: [view.opts.dm]});
  const t = made?.thread || made;
  if (t?.id && !view.gone) { view.thread = t; thvSetId(view, t.id); }
  return !!view.id;
}
// A harness's own command (/compact, a repository's skill) goes to the bot as the old chat sent it: a message marked
// as a command, which its harness runs as typed (backend/models.py MessageCreate.command).
async function thvComposerCommand(view: ThvView, text: string) {
  const C = view.composer;
  try {
    if (!await thvDmMake(view)) return;
    const r = await post('/v2/messages', {to: 'bot:' + C.slug, text, conversation_id: view.id, command: true});
    if ((C.el.querySelector('.p-text') as HTMLTextAreaElement).value.trim() === text) thvComposerClear(view);
    if (r?.held) void thvHoldsLoad(view); else void thvRefresh(view);
  } catch (e) { toast(`Not sent: ${errorMessage(e, '')}`, true); }
}
// /task: a task for this bot, with the files attached here (bot-tasks.js v2PillTask).
async function thvComposerTask(view: ThvView, text: string) {
  const C = view.composer, slug = C.slug, files = C.files.slice();
  try {
    await cloudCompose('/v2/tasks', {title: text.split('\n')[0]!.trim().slice(0, 80), body: text, owner: slug}, files);
    C.files = C.files.filter(f => !files.includes(f));
    thvComposerClear(view); thvChips(view);
    toast(`Task for ${empName(slug)} created`);
    await v2Refresh();
    if (slug !== null && BOT?.slug === slug) void botTasksCatchUp(slug);
  } catch (e) { toast(errorMessage(e, ''), true); }
}
// /new in a DM: the DM is archived and the next message starts a fresh one, as the old chat's New chat did.
async function thvNewDm(view: ThvView) {
  if (!view.id) return;
  try { await patch(thvPath(view.id), {archived: true}); }
  catch (e) { toast(errorMessage(e, ''), true); return; }
  if (view.gone) return;
  view.follow?.(); view.follow = null;
  clearTimeout(view.flashTimer);
  Object.assign(view, thvConvFresh());
  thvReplyingDraw(view);
  if (view.goals) { view.goals.conv = null; view.goals.goal = null; }
  thvStopSync(view);
  await thvLoad(view);
}
function thvDropZone(view: ThvView) {
  const zone = view.el;
  zone.addEventListener('dragover', ev => { if (draggingFiles(ev) && view.composer) { ev.preventDefault(); ev.dataTransfer!.dropEffect = 'copy'; } });
  zone.addEventListener('drop', ev => {
    if (!draggingFiles(ev) || !view.composer) return;
    ev.preventDefault();
    view.composer.files.push(...ev.dataTransfer!.files);
    thvChips(view);
  });
}
function thvChips(view: ThvView) {
  const C = view.composer; if (!C) return;
  (C.el.querySelector('.p-chips') as HTMLElement).innerHTML = [
    ...C.files.map((f, i) => pillChipHTML(f, 'file:' + i)),
    ...C.lib.map((f, i) => `<span>${esc(f.title)} <a href="#" data-rm="lib:${i}" aria-label="Remove ${esc(f.title)}">×</a></span>`)].join('');
  thvComposerLabel(view);   // a file sends as a message, so goal mode's "Set goal" waits until it is gone
}
// Library files: the newest the viewer may read, filtered as they type.
async function thvLibOpen(view: ThvView) {
  const C = view.composer, pane = C.el.querySelector('.thv-lib') as HTMLElement;
  pane.hidden = false;
  pane.innerHTML = '<input type="search" class="thv-lib-q" placeholder="Find a file" aria-label="Find a file"><div class="thv-lib-list"><div class="muted">Loading…</div></div><button type="button" class="ghost small" data-lib-close>Close</button>';
  const q = pane.querySelector('.thv-lib-q') as HTMLInputElement, list = pane.querySelector('.thv-lib-list') as HTMLElement;
  (pane.querySelector('[data-lib-close]') as HTMLElement).onclick = () => { pane.hidden = true; };
  let files: ThvFileRow[] = [];
  try { files = ((await get<{files?: ThvFileRow[]}>('/v2/files?limit=50', {fresh: true})).files || []).filter(f => f.locator !== 'remote_link' && !f.url && !f.archived); }
  catch (e) { list.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  const draw = () => {
    const words = q.value.trim().toLowerCase();
    const shown = files.filter(f => !words || String(f.title || f.name || '').toLowerCase().includes(words)).slice(0, 20);
    list.innerHTML = shown.length ? shown.map(f => `<button type="button" class="thv-lib-row" data-lib="${esc(f.id)}">${esc(f.title || f.name || f.id)}</button>`).join('')
      : '<div class="muted">No files</div>';
  };
  q.oninput = draw; draw(); q.focus();
  list.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-lib]'); if (!b) return;
    const f = files.find(x => x.id === b.dataset.lib);
    if (f && !C.lib.some(x => x.id === f.id)) C.lib.push({id: f.id, title: f.title || f.name || f.id});
    pane.hidden = true; thvChips(view);
    (C.el.querySelector('.p-text') as HTMLTextAreaElement).focus();
  };
}

// ----------------------------------------------------------------- mentions
// "@" then letters: the teammates whose name or handle starts so. A bot goes in as @slug, a person as their handle.
function thvMentionPeople(): ThvMention[] {
  const bots = shownEmps().filter(e => !e.hidden).map(e => ({key: 'bot:' + e.id, label: (e.display_name || e.name)!, handle: e.name!, kind: 'bot' as const}));
  const people = (S.people || []).filter(p => p.id && !p.hidden && p.id !== S.me?.id)
    .map(p => ({key: 'human:' + p.id, label: p.name || p.id, handle: personHandle(p.email || p.id), kind: 'person' as const}));
  return [...people, ...bots];
}
function thvMentionFind(view: ThvView) {
  const C = view.composer, box = C.el.querySelector('.p-text') as HTMLTextAreaElement;
  const before = box.value.slice(0, box.selectionStart || 0), hit = before.match(/(^|\s)@([\w.-]*)$/);
  if (!hit) { thvMentionClose(view); return; }
  const word = hit[2]!.toLowerCase();
  const matches = thvMentionPeople().filter(p => !word || p.handle.toLowerCase().startsWith(word)
    || p.label.toLowerCase().split(/\s+/).some(w => w.startsWith(word))).slice(0, 6);
  if (!matches.length) { thvMentionClose(view); return; }
  C.mention = {start: before.length - hit[2]!.length - 1, matches, at: 0};
  thvMentionDraw(view);
}
function thvMentionDraw(view: ThvView) {
  const C = view.composer, list = C.el.querySelector('.thv-mentions') as HTMLElement, M = C.mention!;
  list.hidden = false;
  list.innerHTML = M.matches.map((p, i) => `<button type="button" role="option" aria-selected="${i === M.at}" data-mention="${i}">${actorAvatar(p.key, 18)}<span>${esc(p.label)}</span><span class="muted">@${esc(p.handle)}</span></button>`).join('');
  list.onmousedown = ev => ev.preventDefault();
  list.onclick = ev => { const b = eventElement(ev)?.closest<HTMLElement>('[data-mention]'); if (b) thvMentionPick(view, Number(b.dataset.mention)); };
}
function thvMentionKey(view: ThvView, ev: KeyboardEvent) {
  const M = view.composer.mention!;
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    ev.preventDefault();
    M.at = (M.at + (ev.key === 'ArrowDown' ? 1 : -1) + M.matches.length) % M.matches.length;
    thvMentionDraw(view); return true;
  }
  if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); thvMentionPick(view, M.at); return true; }
  if (ev.key === 'Escape') { ev.preventDefault(); thvMentionClose(view); return true; }
  return false;
}
function thvMentionPick(view: ThvView, i: number) {
  const C = view.composer, box = C.el.querySelector('.p-text') as HTMLTextAreaElement, M = C.mention, p = M?.matches[i];
  if (!M || !p) return;
  const end = box.selectionStart || 0, text = `@${p.handle} `;
  box.value = box.value.slice(0, M.start) + text + box.value.slice(end);
  const caret = M.start + text.length;
  box.setSelectionRange(caret, caret);
  thvMentionClose(view);
  box.focus();
}
function thvMentionClose(view: ThvView) {
  const C = view.composer; if (!C) return;
  C.mention = null;
  const list = C.el.querySelector<HTMLElement>('.thv-mentions'); if (list) list.hidden = true;
}

// ----------------------------------------------------------------- sending
async function thvComposerSend(view: ThvView) {
  const C = view.composer, box = C.el.querySelector('.p-text') as HTMLTextAreaElement;
  const text = box.value.trim();
  // Goal mode: the words are the bot's goal for this thread (POST /v2/conversations/<thread>/goal), not a message.
  if (chatGoalThreadMode(C)) { if (await chatGoalSend(C, text)) thvDraftDrop(view); return; }
  if (!text && !C.files.length && !C.lib.length) { box.focus(); return; }
  // A bot's "/" commands, as the old chat ran them: Tico's own here, a harness's to the bot, other words as a message.
  if (C.slug && text) {
    const slash = slashRun(C, text);
    if (slash === true) return;
    if (slash !== 'plain' && runCommand(text, C.el.querySelector('.p-cmd'), C.slug)) { sessionAcknowledge(box); thvComposerClear(view); return; }
  }
  // In a bot's Home or DM every message names the kept way (thread posts take delivery, backend/thread_api.py); in
  // goal mode with a file attached it goes as a message, that way too.
  const way = C.ways ? deliveryBody(thvSendMode(view)) : null;
  const files = C.files.splice(0), lib = C.lib.splice(0);
  const replyTo = view.messages.find(x => x.id === view.replyTo) || null;
  box.value = ''; box.style.height = ''; thvChips(view);
  thvDraftDrop(view);
  thvReplyStart(view, null);
  await thvSend(view, {text, files, lib, way, replyTo});
}
// One message out: a "Checking…" row while the rules check runs, then the message, or a held row the author can
// edit, discard or (when the server allows) send anyway. A reply goes to this thread too, naming what it answers.
async function thvSend(view: ThvView, {text, files = [], lib = [], way = null, replyTo = null}: {text: string; files?: File[]; lib?: ThvLibFile[]; way?: object | null; replyTo?: ThvMessage | null}) {
  const p: ThvPending = {key: 'p' + (++THV_N), text, files, lib, replyTo: replyTo?.id || '', quote: replyTo ? thvQuoteFrom(replyTo) : null, state: 'checking'};
  view.pending.push(p);
  thvDraw(view, {stick: true});
  const refs = lib.length ? {files: lib.map(f => ({id: f.id, title: f.title}))} : null;
  const body = {text, ...(refs ? {refs} : {}), ...(replyTo ? {in_reply_to: replyTo.id} : {}), ...(way || {})};
  try {
    let r: (ThvSendReply & ThvThread) | undefined;
    // An upload goes to a thread, so a first message with files makes the DM before it sends.
    if (!view.id && view.opts.dm && files.length) {
      const made: ThvThread & {thread?: ThvThread} = await post('/v2/threads/dm', {members: [view.opts.dm]});
      const t = made?.thread || made;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    }
    if (!view.id && view.opts.dm) {
      r = await post('/v2/threads/dm', {members: [view.opts.dm], body: text, ...(refs ? {refs} : {}), ...(way || {})});
      const t = r?.thread || r;
      if (t?.id) { view.thread = t; thvSetId(view, t.id); }
    } else {
      r = files.length ? await thvUpload(thvPath(view.id).replace('/v2/', '/v2/uploads/') + '/messages', body, files)
        : await post(thvPath(view.id) + '/messages', body);
    }
    if (r?.held) thvHeld(view, p, r.held);
    else {
      view.pending = view.pending.filter(x => x !== p);
      const m = r?.message || (r?.id && r?.seq !== undefined ? r : null);
      if (m) thvApplyMessage(view, {id: m.id, thread_id: m.thread_id, root_id: m.root_id, message: {...(replyTo && !m.in_reply_to ? {in_reply_to: replyTo.id} : {}), ...m}});
      else void thvRefresh(view);
      for (const w of r?.warnings || []) if (w.rule === 'mention_outside') toast(`${actorName(w.actor)} is not in this thread`, true);
      // Without the live stream, the bots' status is read once now; with it, `bot_status` events keep it.
      if (Array.isArray(view.botStatus) && !liveConnected()) void thvStatusRead(view);
    }
  } catch (e) {
    p.state = 'failed'; p.error = errorMessage(e, '');
  }
  thvDraw(view, {stick: true});
}
function thvHeld(view: ThvView, p: ThvPending, held: ThvHeld) {
  p.state = 'held'; p.held = held;
  if (held.id) get<ThvHeld>('/v2/rule-holds/' + encodeURIComponent(held.id), {fresh: true})
    .then((h: ThvHeld | null) => { if (h?.can_send) { p.canSend = true; p.nonce = h.nonce || ''; thvDraw(view); } }).catch(() => {});
}
async function thvStatusRead(view: ThvView) {
  try {
    const r = await get<{bots?: ThvBotRow[]}>(thvPath(view.id) + '/bot-status', {fresh: true});
    if (view.gone || !Array.isArray(r?.bots)) return;
    view.botStatus = r.bots.slice(0, 5); thvDraw(view);
  } catch { /* the rows stay as they were */ }
}
// Files go in one multipart request with the message (the server answers with the message, or a held verdict).
async function thvUpload(path: string, body: Record<string, unknown>, files: File[]) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) fd.append(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
  files.forEach(f => fd.append('files', f, f.name));
  const response = await formFetch(API + path, fd, {});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) { const err = apiFailure(result, response); err.message ||= 'The server did not take the files.'; throw err; }
  return result;
}

// ----------------------------------------------------------------- clicks
function thvWire(view: ThvView) {
  const el = view.el;
  (el.querySelector('.thv-older') as HTMLButtonElement).onclick = ev => void thvOlder(view, ev.currentTarget as HTMLButtonElement);
  (el.querySelector('.thv-unread') as HTMLElement).onclick = ev => {
    el.querySelector('.th-new')?.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    (ev.currentTarget as HTMLElement).hidden = true;
  };
  el.querySelector('.thv-scroll')!.addEventListener('scroll', () => {
    const pill = el.querySelector('.thv-unread') as HTMLElement, first = el.querySelector('.th-new');
    if (!pill.hidden && first && thvVisible(el.querySelector('.thv-scroll') as HTMLElement, first)) pill.hidden = true;
  }, {passive: true});
  el.addEventListener('click', ev => {
    // The long press's own click, just after the lift, is not a tap on what it lifted from.
    if (view.pressQuiet && Date.now() < view.pressQuiet) { ev.preventDefault(); ev.stopPropagation(); view.pressQuiet = 0; return; }
    void thvClick(view, ev);
  }, true);
  thvPressWire(view);
  // The bar outside the view is shared by the views that take turns in it: only the one on screen answers.
  if (view.opts.actsHost) view.opts.actsHost.onclick = ev => { if (!view.gone) void thvClick(view, ev); };
  // A turn's steps load when their fold opens; open folds stay open across redraws (ui/app/chat.ts).
  el.addEventListener('toggle', ev => {
    const target = ev.target as HTMLDetailsElement;
    if (target.matches?.('details.run-steps')) void v2StepsOpen(view.run, target, el);
    const fold = target.dataset?.fold;
    if (fold) view.run.openFolds[target.open ? 'add' : 'delete'](fold);
    sourceGroupToggle(view.openLines, target);
  }, true);
}
const thvMsgOf = (view: ThvView, row: HTMLElement | null | undefined) => view.messages.find(m => m.id === row?.dataset.mid);
// A line opens or shuts its group. The choice is the view's: a redraw keeps it, and nothing is stored.
function thvUpdateToggle(view: ThvView, button: HTMLElement) {
  const req = button.dataset.thUpdate!, had = document.activeElement === button;
  if (view.openUpdates.has(req)) view.openUpdates.delete(req); else view.openUpdates.add(req);
  thvDraw(view);
  // The line is drawn again, so a keyboard user's focus goes back to it.
  if (had) view.el.querySelector<HTMLElement>(`[data-th-update="${CSS.escape(req)}"]`)?.focus();
}
async function thvClick(view: ThvView, ev: Event) {
  const t = eventElement(ev);
  if (!t) return;
  const row = t.closest<HTMLElement>('.th-msg');
  const m = thvMsgOf(view, row);
  const upd = t.closest<HTMLElement>('[data-th-update]');
  if (upd) { thvUpdateToggle(view, upd); return; }
  const file = t.closest<HTMLElement>('[data-th-file]');
  if (file) { void openFileLink(file.dataset.thFile, file.dataset.name); return; }
  if (t.closest('[data-thv-members]')) { threadMembersOpen(view); return; }
  const pin = t.closest<HTMLButtonElement>('[data-thv-pin]');
  if (pin) { void thvPinToggle(view, pin); return; }
  const arch = t.closest<HTMLButtonElement>('[data-thv-archive]');
  if (arch) { void thvArchive(view, arch); return; }
  const del = t.closest<HTMLButtonElement>('[data-thv-delete]');
  if (del) { void thvDelete(view, del); return; }
  const call = t.closest<HTMLElement>('[data-call]');
  if (call) { void callAction(view, call.dataset.call); return; }
  if (t.closest('[data-thv-menu]')) { threadSettingsOpen(view); return; }
  const earlier = t.closest<HTMLButtonElement>('[data-th-earlier]');
  if (earlier) {
    earlier.disabled = true;
    try { await threadEarlierOpen(view, earlier.dataset.thEarlier); }
    catch (e) { toast(errorMessage(e, ''), true); earlier.disabled = false; }
    return;
  }
  const quote = t.closest<HTMLElement>('[data-th-quote]');
  if (quote) { void thvGoTo(view, quote.dataset.thQuote!); return; }
  const stop = t.closest<HTMLButtonElement>('[data-th-stop]');
  if (stop) {
    stop.disabled = true;
    try { await post(`/v2/bots/${encodeURIComponent(stop.dataset.thStop!)}/stop`, stop.dataset.run ? {run_id: stop.dataset.run} : {}); }
    catch (e) { toast(`Not stopped: ${errorMessage(e, '')}`, true); stop.disabled = false; }
    return;
  }
  const react = t.closest<HTMLElement>('[data-th-react]');
  if (react && m) { await thvReact(view, m, react.dataset.thReact!); return; }
  const pick = t.closest<HTMLElement>('[data-th-pick]');
  if (pick && m) { thvEmojiPick(view, m, pick); return; }
  if (t.closest('[data-th-reply]') && m) { thvReplyStart(view, m); return; }
  if (t.closest('[data-th-copy]') && m) { thvCopy(m); return; }
  const pend = t.closest<HTMLElement>('[data-pending]');
  if (pend) {
    const p = view.pending.find(x => x.key === pend.dataset.pending); if (!p) return;
    // Edit and Discard both take a held write back: nothing is sent, and the review it waited on closes.
    if (p.held?.id && t.closest('[data-held-drop], [data-held-edit]'))
      post('/v2/rule-holds/' + encodeURIComponent(p.held.id) + '/withdraw', {}).catch(() => {});
    if (t.closest('[data-held-drop]')) { view.pending = view.pending.filter(x => x !== p); thvDraw(view); return; }
    if (t.closest('[data-held-edit]')) {
      view.pending = view.pending.filter(x => x !== p);
      thvDraw(view);
      const C = view.composer, box = C?.el.querySelector<HTMLTextAreaElement>('.p-text');
      if (p.replyTo && view.messages.some(x => x.id === p.replyTo)) thvReplyStart(view, view.messages.find(x => x.id === p.replyTo));
      if (box) { box.value = p.text; box.focus(); }
      if (C) { C.files.push(...(p.files || [])); C.lib.push(...(p.lib || [])); thvChips(view); }
      return;
    }
    const send = t.closest<HTMLButtonElement>('[data-held-send]');
    if (send) {
      send.disabled = true;
      try {
        await post('/v2/rule-holds/' + encodeURIComponent(p.held!.id) + '/send', {nonce: p.nonce || ''});
        view.pending = view.pending.filter(x => x !== p);
        await thvRefresh(view);
      } catch (e) { toast(errorMessage(e, ''), true); send.disabled = false; }
    }
  }
}
const thvCopy = (m: ThvMessage) => void copyText(m.body || '').then(() => toast('Message copied'), () => toast('Could not copy the message', true));
async function thvReact(view: ThvView, m: ThvMessage, emoji: string) {
  const before = (m.reactions || []).map(r => ({...r, actors: [...(r.actors || [])]}));
  const me = myActor(), r = before.find(x => x.emoji === emoji), on = !thvReactedMine(r);
  // Shown at once; the server's answer (the message's reactions, as a list) settles it.
  m.reactions = r ? before.map(x => x.emoji !== emoji ? x : {...x, count: Math.max(0, x.count + (on ? 1 : -1)),
                                                                 actors: on ? [...x.actors, me] : x.actors.filter(a => a !== me)})
    : [...before, {emoji, count: 1, actors: [me]}];
  thvDraw(view);
  try {
    const path = '/v2/messages/' + encodeURIComponent(m.id) + '/reactions/' + encodeURIComponent(emoji);
    const back = on ? await put(path, {}) : await writeRequest('DELETE', path);
    const fresh = Array.isArray(back) ? back : back?.reactions || back?.message?.reactions;
    if (Array.isArray(fresh)) { m.reactions = fresh; thvDraw(view); }
  } catch (e) {
    m.reactions = before;
    thvDraw(view); toast(errorMessage(e, ''), true);
  }
}
// The emoji to pick from: six common ones, then any already used in this thread.
function thvEmojiChoices(view: ThvView) {
  const used = new Set(THV_EMOJI);
  for (const x of view.messages) for (const r of x.reactions || []) if (r?.emoji && r.count > 0) used.add(r.emoji);
  return [...used].slice(0, 18);
}
function thvEmojiPick(view: ThvView, m: ThvMessage, button: HTMLElement) {
  thvMenuClose();
  thvMenuShow(button, thvEmojiChoices(view).map(e => [e, e]), emoji => thvReact(view, m, emoji), 'thv-emoji');
}

// ---- the long press, on touch: React, Reply and Copy as one sheet (and 👍 and 👎 on a bot's words)
const THV_PRESS_MS = 450;
function thvPressWire(view: ThvView) {
  const el = view.el.querySelector('.thv-list') as HTMLElement;
  let press: {x: number; y: number; timer: ReturnType<typeof setTimeout>} | null = null;
  const end = () => { if (press) { clearTimeout(press.timer); press = null; } };
  el.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'touch' || !ev.isPrimary) return;
    const target = eventElement(ev);
    const row = target?.closest<HTMLElement>('.th-msg[data-mid]');
    if (!row || target!.closest('a, button, input, textarea, select, summary, details, [contenteditable]')) return;
    end();
    press = {x: ev.clientX, y: ev.clientY, timer: setTimeout(() => {
      press = null;
      const m = thvMsgOf(view, row);
      if (!m || m.deleted) return;
      view.pressQuiet = Date.now() + 60000;   // until the finger lifts, however long it is held
      getSelection?.()?.removeAllRanges();
      navigator.vibrate?.(8);
      thvSheet(view, m);
    }, THV_PRESS_MS)};
  });
  el.addEventListener('pointermove', ev => { if (press && Math.hypot(ev.clientX - press.x, ev.clientY - press.y) > 10) end(); });
  for (const type of ['pointerup', 'pointercancel']) el.addEventListener(type, () => { end(); if (view.pressQuiet > Date.now() + 1000) view.pressQuiet = Date.now() + 400; });
  el.addEventListener('scroll', end, {passive: true});
  // The browser's own long-press menu would cancel the press before it is one.
  el.addEventListener('contextmenu', ev => { if ((press || view.pressQuiet > Date.now()) && eventElement(ev)?.closest('.th-msg')) ev.preventDefault(); });
}
function thvSheet(view: ThvView, m: ThvMessage) {
  const d = fbDialog('fb-menu thv-sheet', 'Message actions');
  const key = thvBot(m.sender_id) ? fbKey('message', m.id) : '', mine = key ? fbMine(key)?.rating || '' : '';
  d.innerHTML = `<div class="thv-sheet-emoji" role="group" aria-label="React">${thvEmojiChoices(view).slice(0, 6).map(e =>
      `<button type="button" data-pick-emoji="${esc(e)}" aria-label="${esc(e)}">${esc(e)}</button>`).join('')}</div>
    <div class="fb-menu-row">${thvCanPost(view) ? `<button type="button" data-pick="reply" aria-label="Reply">${THV_SVG.reply}</button>` : ''}${
      m.body ? `<button type="button" data-pick="copy" aria-label="Copy">${THV_SVG.copy}</button>` : ''}${key ? `
      <button type="button" data-pick="up" aria-pressed="${mine === 'up'}" aria-label="Good">${FB_ICON.up}</button>
      <button type="button" data-pick="down" aria-pressed="${mine === 'down'}" aria-label="Not good">${FB_ICON.down}</button>` : ''}</div>`;
  // The click the lift makes lands on the backdrop: it is not a tap to close.
  d.addEventListener('click', ev => { if (view.pressQuiet > Date.now()) { ev.stopImmediatePropagation(); view.pressQuiet = 0; } }, true);
  d.showModal();
  d.onclick = ev => {
    const target = eventElement(ev), e = target?.closest<HTMLElement>('[data-pick-emoji]'), b = target?.closest<HTMLElement>('[data-pick]');
    if (!e && !b) return;
    d.close();
    if (e) { void thvReact(view, m, e.dataset.pickEmoji!); return; }
    const k = b!.dataset.pick;
    if (k === 'reply') thvReplyStart(view, m);
    else if (k === 'copy') thvCopy(m);
    else if (k === 'up') void fbUp(key);
    else if (mine === 'down') fbShow(key); else void fbDown(key);
  };
  d.querySelector('button')?.focus();
}
// One small menu at a time, under the button that opened it; Escape or a click elsewhere closes it.
let THV_MENU: {menu: HTMLElement; button: HTMLElement} | null = null;
function thvMenuShow(button: HTMLElement, items: [string, string][], pick: (key: string) => unknown, cls = '') {
  const menu = document.createElement('div');
  menu.className = 'p-menu thv-pop' + (cls ? ' ' + cls : ''); menu.setAttribute('role', 'menu');
  menu.innerHTML = items.map(([k, label]) => `<button type="button" role="menuitem" data-k="${esc(k)}">${esc(label)}</button>`).join('');
  document.body.append(menu);
  const r = button.getBoundingClientRect();
  menu.style.top = Math.min(innerHeight - menu.offsetHeight - 8, r.bottom + 4) + 'px';
  menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, r.right - menu.offsetWidth)) + 'px';
  button.setAttribute('aria-expanded', 'true');
  THV_MENU = {menu, button};
  menu.onclick = ev => { const b = eventElement(ev)?.closest<HTMLElement>('[data-k]'); if (!b) return; thvMenuClose(); void pick(b.dataset.k!); };
  menu.querySelector('button')?.focus();
}
function thvMenuClose() {
  if (!THV_MENU) return;
  THV_MENU.menu.remove(); THV_MENU.button.setAttribute('aria-expanded', 'false');
  THV_MENU = null;
}
document.addEventListener('click', ev => { const t = eventElement(ev); if (THV_MENU && !THV_MENU.menu.contains(t) && !THV_MENU.button.contains(t)) thvMenuClose(); }, true);
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && THV_MENU) { THV_MENU.button.focus(); thvMenuClose(); } });
