/* ui/app/page-chat.ts — Chat with a page's owner, from the page (specs page-owner-chat, chat-on-every-page; style guide
   "Where chat lives"). One pattern for every page, mounted by the router: pageChatRoute maps each address to its page's
   key (PGC_ROUTES), and the page needs no code of its own. A page names only its owner (GET /api/v2/page-owners,
   backend/page_owners.py; set in Settings > General > Page owners): the bot that does the page's work. Every page has
   one; a page whose owner a person set to "No owner" has no chat at all (no column, no tab, no mark).
   No page chat where the chat IS the page (a bot's or person's page, a thread, the Assistant), on setup, the style
   guide, or in a bot's read-only view (view-only.ts: it offers nothing that writes). Those addresses have no key. A
   task's window and every other dialog open over the page and its chat, so they have none of their own.
   A page may give more with pageChatUse(page, {...}): what is selected on it (`item`, "From Goals › <item>"), the chat's
   empty line, Tools' key drop, Tasks' board (`context`, refs.board) and Tasks' owner (the shown pipeline's manager:
   only while live, and never on a Tasks page a person set to "No owner").
   Desktop: one shared column (<aside class="pgc">, appended to the app shell) at the right of the window, from the top
   of #main down. Wherever the page still fits beside it (PGC_PAGE_MIN beside the column's narrowest, PGC_MIN: from
   1266 px with the sidebar's 326 px, so 1280 and up) it is docked (body.pgc-docked gives #main a right margin of its
   width), narrower than its kept or default width where the window is small, and open unless the person closed it on
   that page (kept in this browser per page). Narrower than that, and when closed, it folds to a slim tab
   on the right edge (.pgc-edge, the owner's face, in a gutter of its own that #main makes room for, so it covers no
   control or scrollbar of the page); the tab opens the column over the page, which keeps its width, with the cursor in
   its composer. There is no chat button in the page's header. Close at the column's left, the owner's face and name,
   then the chat. Its left edge drags it 260–560 px wide (kept; docked, never so wide that the page has less than
   PGC_PAGE_MIN). Esc inside it or its close closes it and gives the focus back to the tab. ⌘J (Ctrl+J) is this page's
   chat: the owner's column where the page has one, opened with the cursor in its composer or closed again
   (ask-panel.ts asks pageChatKey first), else Ask; the Ask row's title names ⌘J only where Ask has it
   (pageChatKeyTaken). Ask opens in the sidebar, so both may be open. A page's own panel may hold the column while it
   is open (pageChatHold: Listening's and Automations' editor, as it opens or another opens: opening the chat brings it
   back over the editor). Rules' rule detail swaps with it (PGC_HOLDS): a rule opened takes the chat's place, and
   opening the chat closes the rule (its address loses ?rule=), the chat in its place; the chat comes back when the
   rule closes. Two pages with one owner (Strategy and Goals) have one chat: moving between them keeps it as it is,
   open or closed, with what is typed in it.
   Calendar and Meetings (PGC_ASK) are the Assistant's: their tab and mark carry the Assistant's face and open Ask in the
   sidebar (ask-panel.ts), never a second Assistant chat, so they have no column and no owner in Settings.
   Phone (≤760 px): no column. The owner's face is the page's chat mark, 22 px in a 44 px button in the page's header
   row just before ⋯, or last where it has none (a .ph-chat slot: page-head.ts for the shared header, pageChatSlot in
   the few pages that draw their own: Calendar, a computer's screen and performance, Live meetings); it opens the chat
   full screen over the page and the tab bar (a modal dialog: the page, the sidebar and the tab bar are inert behind
   it): ‹ at left, the name in the middle, the composer at the bottom. ‹ or the phone's back returns to the page as it
   was (a history entry of its own, as the task window's; a page the chat leads to replaces that entry, so one back
   returns to the page). Tasks draws its own mark once its board is drawn (the board's bots, pipeline-ask.ts), so it
   asks for none then (`mark: false`).
   The chat is the viewer's ordinary direct chat with the bot (thread-view.ts threadMount), the same one as on the bot's
   page, never a chat of its own. Each message sent from it carries refs.from_page: the page and what is selected on it
   (`item`), which the bot's turn reads and the chat shows as "From Goals › <item>". The item shows above the composer
   as a chip, so the person sees what the next message carries. The chat loads only when shown.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PageChatItem { kind: string; id: string; name: string }
interface PageChatFrom { page: string; label: string; item?: PageChatItem }
/** What a page adds to its chat (pageChatUse); each field given replaces the one before. */
interface PageChatUse {
  item?: (() => PageChatItem | null) | undefined;   // what is selected on the page now
  empty?: (() => string) | undefined;               // the chat's empty state, written from the page when it opens
  keyDrop?: (() => string[]) | undefined;           // Tools: the composer catches keys (tools-key-drop.ts); the tools in Problem
  // Tasks: the board (`id`: its pipeline; another one shows its chip again), a chip and refs.board
  context?: {id: string; label: () => string; refs: () => ThvRefs | null} | undefined;
  owner?: string | null | undefined;                // Tasks: the shown pipeline's manager; left out, the page's own owner
  mark?: boolean | undefined;                       // false: the page draws its own chat mark on a phone (Tasks)
}
interface PageChatState {
  page: string; label: string;
  base: string | null;   // the page's owner as /v2/page-owners has it
  none: boolean;         // a person set the page to "No owner": no chat, whoever else might answer
  known: boolean;        // the owners have been read (until then a docked column holds its place, empty)
  use: PageChatUse; chat: ThvView | null; chatKey: string; ctxId: string; mounting: boolean;
  full: boolean; open: boolean; heldPage: boolean; heldRoute: boolean;
}

let PGC: PageChatState | null = null;
let PGC_SEQ = 0;                   // an owner read still out when the page moved on draws nothing
// The owners as last read: a page draws its chat at once from these, and each new page reads them again.
interface PageOwnerRow { bot?: string | null; name?: string; choice?: string; default?: string | null }
let PGC_OWNERS: Record<string, PageOwnerRow> | null = null;
const PGC_MIN = 260, PGC_MAX = 560, PGC_WIDTH = 340;
// Docked, the page keeps at least this much beside the column: the column docks only where the page has this beside
// its narrowest (Tasks' board, Finance's, Usage's and Automations' tables have no sideways scroll at 680 px), and a
// kept or default width that would leave less docks narrower, kept as it was for a wider window.
const PGC_PAGE_MIN = 680;
const PGC_ITEM_MAX = 200;   // backend/page_owners.py FROM_LIMITS: a longer name is cut here, and on the server too
const PGC_WIDTH_KEY = 'tico.page_chat.width';
const pgcOpenKey = (page: string): string => 'tico.page_chat.open.' + page;
const PGC_PHONE = '(max-width:760px)';
const pgcPhone = (): boolean => window.matchMedia(PGC_PHONE).matches;
/** The room right of the sidebar: where #main starts to the window's right edge. */
const pgcRoom = (): number => document.documentElement.clientWidth - Math.max(0, document.getElementById('main')?.getBoundingClientRect().left || 0);
/** The column docks where the page keeps PGC_PAGE_MIN beside its narrowest width. */
const pgcDocks = (): boolean => !pgcPhone() && pgcRoom() >= PGC_PAGE_MIN + PGC_MIN;
const PGC_CLOSE = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const PGC_BACK = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';

// Each address's page key (backend/page_owners.py PAGES): the first base the address is, or is under, wins, so a deeper
// base comes before its parent. An address with no base here has no page chat (the list above says which). The first
// base of a key is where "From <page>" leads (thread-view.ts).
const PGC_ROUTES: readonly (readonly [string, string])[] = [
  // A task's address is the Tasks page with the task's window over it: the window covers the page and its chat (both
  // stay beneath it, as they were), so the chat is never the task's.
  ['#/tasks', 'tasks'], ['#/task', 'tasks'], ['#/calendar', 'calendar'], ['#/updates', 'updates'],
  ['#/home', 'company'], ['#/overview', 'company'], ['#/team', 'company'], ['#/company-page', 'company'],
  ['#/strategy/goals', 'goals'], ['#/strategy', 'strategy'], ['#/products', 'products'],
  ['#/finance', 'finance'], ['#/growth', 'growth'], ['#/automations', 'automations'], ['#/listening', 'listening'],
  ['#/rules', 'rules'], ['#/docs', 'docs'], ['#/library/files', 'docs'], ['#/page', 'docs'],
  ['#/library/skills', 'skills'], ['#/library/contacts', 'contacts'], ['#/tools', 'tools'], ['#/meetings', 'meetings'],
  ['#/mail', 'mail'], ['#/messaging', 'messaging'], ['#/settings', 'settings'], ['#/learnings', 'learnings'],
  ['#/usage', 'usage'], ['#/changelog', 'changelog'], ['#/replay', 'replay'], ['#/roadmap', 'roadmap'],
  ['#/tags', 'tags'], ['#/tag', 'tags'], ['#/computers', 'computers'], ['#/sql', 'sql'], ['#/seat', 'seat'],
];
// A page's own panel the address opens (the query keys that open it), which swaps with the column: Rules' rule detail
// and its due date's.
const PGC_HOLDS: Record<string, readonly string[]> = {
  rules: ['rule', 'due'],
};
// The pages whose chat is Ask itself, the Assistant's one chat (ask-panel.ts).
const PGC_ASK: ReadonlySet<string> = new Set(['calendar', 'meetings']);

/** The page key of an address, or null where there is no page chat. */
function pageChatPageOf(route: string): string | null {
  if (S.me?.read_only_view) return null;   // a bot's read-only view: nothing that writes (view-only.ts)
  const path = (route.split('?')[0] ?? '').replace(/\/+$/, '');
  return PGC_ROUTES.find(([base]) => path === base || path.startsWith(base + '/'))?.[1] ?? null;
}
/** Where a page's "From <page>" line leads: the page's own address. */
const pageChatHome = (page: string): string => PGC_ROUTES.find(([, key]) => key === page)?.[0] ?? '';
const pgcHeldBy = (page: string, route: string): boolean => {
  const q = new URLSearchParams(route.split('?')[1] ?? '');
  return (PGC_HOLDS[page] || []).some(k => q.has(k));
};
/** The address without the page's panel: the chat takes its place. */
function pgcRelease(page: string, route: string): string {
  const [path = '', query = ''] = route.split('?'), q = new URLSearchParams(query);
  for (const k of PGC_HOLDS[page] || []) q.delete(k);
  const rest = q.toString();
  return path + (rest ? '?' + rest : '');
}
const pgcAsk = (state: PageChatState | null): boolean => !!state && PGC_ASK.has(state.page);

function pgcKept(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function pgcKeep(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* storage blocked: not kept */ }
}
/** The column's width: what this browser kept, inside 260–560. */
function pageChatWidth(): number {
  const n = Number(pgcKept(PGC_WIDTH_KEY));
  return Number.isFinite(n) && n > 0 ? Math.min(PGC_MAX, Math.max(PGC_MIN, Math.round(n))) : PGC_WIDTH;
}
/** The width the column has now: docked, narrower where the kept width would leave the page less than PGC_PAGE_MIN. */
function pgcWidthNow(): number {
  const kept = pageChatWidth();
  if (!pgcDocks()) return kept;
  return Math.max(PGC_MIN, Math.min(kept, Math.floor(pgcRoom() - PGC_PAGE_MIN)));
}
/** Open where it docks, unless the person closed it on this page; folded to its tab where it does not dock. */
const pgcWantOpen = (page: string): boolean => !pgcPhone() && pgcDocks() && pgcKept(pgcOpenKey(page)) !== '0';

/** The owners as the server has them now: a changed owner shows at the page's next load. A failed read keeps what was
    read before (a page with nothing read has no chat until the next page reads again). */
async function pgcOwnersRead(): Promise<void> {
  try {
    const r = await get<{pages?: Record<string, PageOwnerRow>}>('/v2/page-owners', {fresh: true});
    PGC_OWNERS = r?.pages || {};
  } catch { PGC_OWNERS = PGC_OWNERS || {}; }
}

/** Who answers on the page now: the page's override (Tasks' pipeline manager) while that bot is live, else its owner;
    no one where a person set the page to "No owner". */
function pgcOwner(state: PageChatState): string | null {
  if (state.none) return null;
  const over = state.use.owner;
  return over !== undefined && over !== null && pgcLive(over) ? over : state.base;
}
/** A bot that is set up and not archived, as the roster has it (a roster not read yet keeps it, until it is). */
function pgcLive(actor: string): boolean {
  const id = actor.replace(/^bot:/, ''), emps = S.emps || [];
  if (!emps.length) return true;
  const row = emps.find(e => e.id === id || e.slug === id);
  return !!row && !['planned', 'archived'].includes(String(row.status || row.state || ''));
}

/** What a message sent now carries: the page, and what is selected on it. */
function pgcFrom(state: PageChatState): PageChatFrom {
  const item = pgcItem(state);
  return {page: state.page, label: state.label, ...(item ? {item} : {})};
}
function pgcItem(state: PageChatState): PageChatItem | null {
  const item = state.use.item?.() || null;
  if (!item?.id || !item.name) return null;
  const name = item.name.trim();
  return name.length > PGC_ITEM_MAX ? {...item, name: name.slice(0, PGC_ITEM_MAX - 1).trimEnd() + '…'} : item;
}

/** The one column every page's chat opens in, made the first time it is needed. */
function pgcAside(): HTMLElement {
  let aside = document.querySelector<HTMLElement>('body > aside.pgc');
  if (aside) return aside;
  aside = document.createElement('aside');
  aside.className = 'pgc';
  aside.id = 'pgc-col';
  aside.hidden = true;
  aside.addEventListener('click', pgcClick);
  aside.addEventListener('keydown', pgcKey);
  aside.addEventListener('pointerdown', pgcDragStart);
  // After the tab in the document, so Tab from the tab goes on into the column it opened.
  const edge = document.querySelector('body > .pgc-edge');
  if (edge) edge.after(aside); else document.body.appendChild(aside);
  return aside;
}
/** The folded column: a slim strip on the right edge holding the tab, the owner's face. */
function pgcEdge(): HTMLElement {
  let edge = document.querySelector<HTMLElement>('body > .pgc-edge');
  if (edge) return edge;
  edge = document.createElement('div');
  edge.className = 'pgc-edge';
  edge.hidden = true;
  edge.innerHTML = '<button type="button" class="ghost pgc-tab" data-pgc-open aria-controls="pgc-col" aria-expanded="false"></button>';
  const aside = document.querySelector('body > aside.pgc');
  if (aside) aside.before(edge); else document.body.appendChild(edge);
  return edge;
}
const pgcTab = (): HTMLElement | null => document.querySelector<HTMLElement>('body > .pgc-edge .pgc-tab');
/** The column and its tab start where #main starts: below the top bar and any banner. A banner that comes or goes
    resizes #main, so they follow it. */
const PGC_MAIN_WATCH = window.ResizeObserver ? new ResizeObserver(() => { if (PGC) pgcPlace(); }) : null;
function pgcPlace(): void {
  const main = document.getElementById('main');
  if (main) document.body.style.setProperty('--pgc-top', Math.max(0, Math.round(main.getBoundingClientRect().top)) + 'px');
  if (main) PGC_MAIN_WATCH?.observe(main);
}

/** The router, on every address: the address's page gets its owner's chat; another address within the same page keeps
    it as it is (a rule opened on Rules holds it). Called before the page draws, so the page's header already finds its
    mark (pageChatMarkHTML) and the page's pageChatUse finds its state. */
function pageChatRoute(route: string): void {
  const page = pageChatPageOf(route);
  if (PGC && PGC.page === page) {
    const held = pgcHeldBy(page, route);
    if (held !== PGC.heldRoute) { PGC.heldRoute = held; pgcDraw(PGC); }
    return;
  }
  const row = page ? PGC_OWNERS?.[page] : undefined;
  if (page && PGC && row && pgcSameChat(PGC, page, row)) { pgcCarry(PGC, page, row, route); return; }
  pageChatLeave(!!page);
  if (!page) return;
  if (PGC_ASK.has(page)) {
    // The Assistant's: its face opens Ask. Nothing to read.
    PGC = {page, label: page, base: 'bot:' + assistantBot(), none: false, known: true, use: {}, chat: null, chatKey: '', ctxId: '',
      mounting: false, full: false, open: false, heldPage: false, heldRoute: false};
    pgcDraw(PGC);
    return;
  }
  // Its name is the server's; until that is read (Tasks' manager may answer first), the key, capitalised.
  const state: PageChatState = {page, label: row?.name || page.charAt(0).toUpperCase() + page.slice(1), base: row?.bot || null,
    none: row?.choice === 'none', known: !!PGC_OWNERS, use: {}, chat: null, chatKey: '', ctxId: '', mounting: false, full: false,
    open: pgcWantOpen(page), heldPage: false, heldRoute: pgcHeldBy(page, route)};
  PGC = state;
  pgcWidthSet();
  pgcDraw(state);
  pgcFollow(state);
}
/** The owners read again for the page now shown: a changed owner (or "No owner") draws at once. */
function pgcFollow(state: PageChatState): void {
  const seq = PGC_SEQ, page = state.page;
  void pgcOwnersRead().then(() => {
    if (PGC !== state || seq !== PGC_SEQ) return;
    const now = PGC_OWNERS?.[page];
    state.label = now?.name || state.label;
    const base = now?.bot || null, none = now?.choice === 'none';
    if (state.known && base === state.base && none === state.none) return;
    state.known = true;
    state.base = base;
    state.none = none;
    pgcDraw(state);
  });
}
/** Another page whose owner is the bot this chat is with (Strategy and Goals): the same chat. */
const pgcSameChat = (state: PageChatState, page: string, row: PageOwnerRow): boolean => !PGC_ASK.has(page) && !pgcAsk(state)
  && !!row.bot && row.choice !== 'none' && row.bot === pgcOwner(state) && state.use.owner === undefined;
/** The chat stays as it is, open or closed, mounted, with its draft; it now says it is from this page. */
function pgcCarry(state: PageChatState, page: string, row: PageOwnerRow, route: string): void {
  PGC_SEQ++;
  Object.assign(state, {page, label: row.name || state.label, base: row.bot || null, none: false, use: {}, heldPage: false,
    heldRoute: pgcHeldBy(page, route)});
  pgcDraw(state);
  pgcFollow(state);
}

/** A page gives its chat more (see PageChatUse). Ignored unless the address is that page's. What it gives draws at
    once (the column docks before the page lays itself out); what it takes back (an owner or a board) draws at the end
    of the turn: the router's leave (Tasks' pipelineRailLeave) and the page's draw (pipelineRailSync) come in one turn
    when the address moves within the page, and the chat must see only where they end (one manager, two boards: one
    chat, not a new one). */
let PGC_SOON: PageChatState | null = null;
function pageChatUse(page: string, use: PageChatUse): void {
  const state = PGC;
  if (!state || state.page !== page) return;
  state.use = {...state.use, ...use};
  const takes = ('owner' in use && use.owner === undefined) || ('context' in use && use.context === undefined);
  if (!takes) { pgcDraw(state); return; }
  if (PGC_SOON === state) return;
  PGC_SOON = state;
  queueMicrotask(() => { PGC_SOON = null; pgcDraw(state); });
}

/** The page's chat mark, for the shared header (page-head.ts) and pageChatSlot: the owner's face, which opens the chat
    full screen on a phone. The header shows it on a phone only. Empty with no owner, while the owner is read, or where
    the page draws its own (Tasks). */
function pageChatMarkHTML(): string {
  const state = PGC, owner = state ? pgcOwner(state) : null;
  if (!state || !owner || state.use.mark === false) return '';
  const name = pgcName(state, owner), ask = pgcAsk(state);
  return `<button type="button" class="ghost ph-chat-mark" data-pgc-open${ask ? ` aria-controls="ask-panel" aria-expanded="${!!ASK?.open}"` : ' aria-haspopup="dialog"'} aria-label="Chat with ${esc(name)}" title="Chat with ${esc(name)}">${actorFace(owner, 22)}</button>`;
}
/** The mark's place in a header a page draws itself (not page-head.ts): before its ⋯, or last where it has none. */
const pageChatSlot = (): string => `<span class="ph-chat">${pageChatMarkHTML()}</span>`;
/** The name the tab and the mark say: the owner's, or Ask's own name for the Assistant (ask-panel.ts). */
const pgcName = (state: PageChatState, owner: string): string => pgcAsk(state) ? askName() : actorLabel(owner);
/** Ask opened or closed (ask-panel.ts askSync): the Assistant's tab and mark on Calendar and Meetings say so. */
function pageChatAskSync(open: boolean): void {
  if (!pgcAsk(PGC)) return;
  for (const el of document.querySelectorAll('body > .pgc-edge .pgc-tab, #main .ph-chat > [data-pgc-open]')) {
    if (el.getAttribute('aria-expanded') !== String(open)) el.setAttribute('aria-expanded', String(open));
  }
}
/** The marks already in the page's headers follow the owner (it is read after the header was drawn, or changes). */
function pgcMarks(): void {
  const html = pageChatMarkHTML();
  for (const slot of document.querySelectorAll<HTMLElement>('#main .ph-chat')) {
    if (slot.dataset['mark'] !== html) { slot.dataset['mark'] = html; slot.innerHTML = html; }
  }
}

/** ⌘J is the page's chat here (a desktop page with an owner's column), not Ask's: the Ask row names it only otherwise. */
/** A person set the page now shown to "No owner": it has no chat of any kind (the phone's board face too). */
const pageChatNone = (): boolean => !!PGC && PGC.none;
const pageChatKeyTaken = (): boolean => !!PGC && !!pgcOwner(PGC) && !pgcAsk(PGC) && !pgcPhone();

/** Whether the page's chat column shows now (a desktop's; a phone's full screen is not a column). */
const pageChatShown = (): boolean => !!PGC && !pgcAsk(PGC) && !PGC.full && !pgcPhone() && PGC.open && !PGC.heldPage && !PGC.heldRoute
  && (!!pgcOwner(PGC) || !PGC.known);

/** The tab or the mark: on a desktop opens or closes the column (kept for the page where it docks); on a phone opens
    the chat full screen. */
function pageChatToggle(open?: boolean, focus = false): void {
  const state = PGC;
  if (!state || !pgcOwner(state)) return;
  if (pgcAsk(state)) { if (!askAway()) askToggle(open); return; }   // the Assistant's page: Ask is its chat
  if (pgcPhone()) { if (open !== false) pgcFullOpen(state); else pgcFullClose(state); return; }
  const next = open ?? !pageChatShown();
  if (next && state.heldRoute) {
    // Rules: the chat takes the rule's place, so the rule closes (its address goes back to the list's).
    state.heldRoute = false;
    location.hash = pgcRelease(state.page, S.route || location.hash);
  }
  if (next) state.heldPage = false;   // asked for over a page's editor: it comes back over it
  if (pgcDocks()) pgcKeep(pgcOpenKey(state.page), next ? '1' : '0');
  const inside = pgcAside().contains(document.activeElement);
  state.open = next;
  pgcDraw(state);
  if (next && focus) pgcFocus(state);
  else if (!next && inside) pgcTab()?.focus({preventScroll: true});
}

/** ⌘J: on a desktop page with a chat, opens it with the cursor in its composer, or closes it. False: not ours (Ask's). */
function pageChatKey(): boolean {
  if (!PGC || !pgcOwner(PGC) || pgcAsk(PGC) || pgcPhone()) return false;
  // A window over the page (a goal's) keeps the keyboard: the key is taken, and nothing opens behind it.
  if (document.querySelector('dialog[open]')) return true;
  pageChatToggle(!pageChatShown(), true);
  return true;
}

/** A page's own panel takes the column's place while it is open (Listening's editor); the column comes back after. The
    page calls it as the panel opens, another opens or it closes, not on each redraw of the same one: the person may
    have opened the chat over it. */
function pageChatHold(on: boolean): void {
  if (!PGC || PGC.heldPage === on) return;
  PGC.heldPage = on;
  pgcDraw(PGC);
}

/** Draws everything the page's chat shows from its state: the column, the tab, the marks. */
function pgcDraw(state: PageChatState): void {
  if (PGC !== state) return;
  const owner = pgcOwner(state), phone = pgcPhone(), panel = pgcAside(), edge = pgcEdge();
  const column = pageChatShown(), shows = column || state.full;
  panel.hidden = !shows;
  panel.classList.toggle('pgc-full', state.full);
  document.body.classList.toggle('pgc-open', column);
  document.body.classList.toggle('pgc-docked', column && pgcDocks());
  // The tab: a computer's way in whenever the column is not docked beside the page (under an open column it waits).
  const tabbed = !phone && !!owner && !(column && pgcDocks());
  edge.hidden = !tabbed;
  document.body.classList.toggle('pgc-tabbed', tabbed);
  const tab = pgcTab();
  if (tab && owner) {
    const name = pgcName(state, owner), face = actorFace(owner, 22), ask = pgcAsk(state);
    if (tab.dataset['face'] !== face) { tab.dataset['face'] = face; tab.innerHTML = face; }
    tab.setAttribute('aria-label', 'Chat with ' + name);
    tab.title = `Chat with ${name} (⌘J)`;
    tab.setAttribute('aria-controls', ask ? 'ask-panel' : 'pgc-col');
    tab.setAttribute('aria-expanded', String(ask ? !!ASK?.open : column));
  }
  pgcMarks();
  pgcAskTitle();
  pgcModal(state.full);
  if (column) pgcWidthSet();
  if (shows || tabbed) pgcPlace();   // a phone's full screen starts under a banner too
  if (!shows) { pgcUnmount(state); panel.replaceChildren(); return; }
  if (!panel.querySelector(':scope > .pgc-shell')) {
    panel.innerHTML = `<div class="pgc-shell">
      <div class="pgc-grip" role="separator" aria-orientation="vertical" aria-label="Chat width" aria-valuemin="${PGC_MIN}" aria-valuemax="${PGC_MAX}" tabindex="0"></div>
      <header class="pgc-head"></header><div class="pgc-chat"></div></div>`;
  }
  const head = panel.querySelector<HTMLElement>('.pgc-head');
  const name = owner ? actorLabel(owner) : '';
  panel.setAttribute('aria-label', owner ? 'Chat with ' + name : 'Chat');
  // A phone's full screen is a modal dialog over the page (pgcModal makes the rest inert); the column is a region.
  if (state.full) { panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); }
  else { panel.removeAttribute('role'); panel.removeAttribute('aria-modal'); }
  if (head) {
    const html = (state.full
      ? `<button type="button" class="ghost pgc-x" data-pgc-close aria-label="Back" title="Back">${PGC_BACK}</button>`
      : `<button type="button" class="ghost pgc-x" data-pgc-close aria-label="Close chat" title="Close (Esc)">${PGC_CLOSE}</button>`)
      + (owner ? `<span class="pgc-who"><span class="pgc-face">${actorFace(owner, 28)}</span><b class="pgc-name">${esc(name)}</b></span>` : '');
    if (head.dataset['html'] !== html) { head.dataset['html'] = html; head.innerHTML = html; }
  }
  panel.querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(pgcWidthNow()));
  const chat = panel.querySelector<HTMLElement>('.pgc-chat');
  if (!chat) return;
  // The chat follows its owner and the page's options: another owner (another pipeline) or option is another chat.
  const key = owner ? [owner, !!state.use.empty, !!state.use.keyDrop, !!state.use.context].join('|') : '';
  if (state.chatKey !== key) { pgcUnmount(state); chat.replaceChildren(); state.chatKey = key; }
  // While the owner is read the column holds its place, its rows still loading.
  if (!owner) { if (!chat.firstChild) chat.innerHTML = skelRows(3, {dot: false}); return; }
  // Mounted once the page has drawn (pageChatRoute runs before it), so the page's pageChatUse comes first.
  if (!state.chat && !state.mounting) { state.mounting = true; queueMicrotask(() => pgcMount(state, chat)); }
  pgcContext(state);
  pageChatPicked();
}
/** Another board in the same chat (another pipeline, one manager): its chip shows again, even after ✕ on the last one's,
    and the next message carries this one's refs.board (the chat reads state.use.context when it sends). */
function pgcContext(state: PageChatState): void {
  const id = state.use.context?.id ?? '';
  if (!state.chat || state.ctxId === id) return;
  state.ctxId = id;
  state.chat.contextOff = false;
  thvContextDraw(state.chat);
}
function pgcMount(state: PageChatState, chat: HTMLElement): void {
  state.mounting = false;
  const owner = pgcOwner(state);
  if (PGC !== state || state.chat || !owner || !chat.isConnected || pgcAside().hidden) return;
  const use = state.use;
  chat.replaceChildren();
  // The board as it is when a message goes: the chat stays the same one from one pipeline to another with this manager.
  const context = {label: () => state.use.context?.label() ?? '', refs: () => state.use.context?.refs() ?? null};
  state.ctxId = use.context?.id ?? '';
  const opts: ThvOpts = {dm: owner, card: true, header: false, from: () => pgcFrom(state),
    ...(use.keyDrop ? {keyDrop: {tools: use.keyDrop}} : {}), ...(use.context ? {context} : {})};
  // The empty line is the page's as it is now: docked open at first, the chat mounts before the page has read its data.
  if (use.empty) Object.defineProperty(opts, 'empty', {enumerable: true, get: () => state.use.empty?.() ?? ''});
  state.chat = threadMount(chat, opts);
  chat.querySelector('.thv-composer')?.insertAdjacentHTML('beforebegin', '<div class="pgc-chip" hidden></div>');
  pageChatPicked();
}
/** The page has read what its chat's empty line names (Tools' problems): the line, if shown, says it now. */
function pageChatEmptySync(): void {
  const view = PGC?.chat, empty = PGC?.use.empty;
  if (!view || !empty || ['loading', 'gone', 'error'].includes(view.state)) return;
  const line = view.el.querySelector<HTMLElement>('.thv-scroll .th-empty');
  if (line) line.textContent = empty();
}
function pgcUnmount(state: PageChatState): void {
  threadUnmount(state.chat);
  state.chat = null;
  state.chatKey = '';
  state.ctxId = '';
}
/** The column's width now, on the page's margin and the column (pgcWidthNow). */
function pgcWidthSet(): void {
  const w = pgcWidthNow() + 'px';
  if (document.body.style.getPropertyValue('--pgc-w') !== w) document.body.style.setProperty('--pgc-w', w);
}
/** ⌘J names Ask on the Ask row only where Ask has it (ask-panel.ts askSync says the same). */
function pgcAskTitle(): void {
  const row = document.getElementById('ask-row');
  if (!row || typeof askName !== 'function') return;
  const title = askName() + (pageChatKeyTaken() ? '' : ' (⌘J)');
  if (row.title !== title) row.title = title;
}
// The page behind a phone's full screen chat: out of reach of touch, Tab and the screen reader while it is open.
const PGC_BEHIND = ['#main', '#side', '#topbar', '#mobile-nav'];
let PGC_INERT: HTMLElement[] = [];
function pgcModal(on: boolean): void {
  if (on === !!PGC_INERT.length) return;
  if (!on) { for (const el of PGC_INERT) el.inert = false; PGC_INERT = []; return; }
  PGC_INERT = PGC_BEHIND.map(sel => document.querySelector<HTMLElement>(sel)).filter((el): el is HTMLElement => !!el && !el.inert);
  for (const el of PGC_INERT) el.inert = true;
}
/** The roster came (or changed): the tab, the marks and the column's header say the owner's name and face. A pipeline
    manager the roster says is archived gives way to the page's owner. */
function pageChatFollowRoster(): void {
  if (PGC) pgcDraw(PGC);
}

/** The page's selection changed (or may have): the chip above the composer shows what the next message carries. */
function pageChatPicked(): void {
  const chip = PGC?.chat ? pgcAside().querySelector<HTMLElement>('.pgc-chip') : null;
  if (!PGC || !chip) return;
  const name = pgcItem(PGC)?.name || '';
  if (chip.textContent !== name) chip.textContent = name;
  chip.title = name;
  chip.hidden = !name;
}

/** The cursor in the composer, once the chat has drawn it. */
function pgcFocus(state: PageChatState): void {
  const panel = pgcAside();
  const box = () => panel.querySelector<HTMLTextAreaElement>('.thv-composer .p-text');
  const now = box();
  if (now) { now.focus({preventScroll: true}); return; }
  const watch = new MutationObserver(() => {
    const el = box();
    if (!el) return;
    watch.disconnect();
    if (PGC === state && (state.open || state.full)) el.focus({preventScroll: true});
  });
  watch.observe(panel, {childList: true, subtree: true});
  setTimeout(() => watch.disconnect(), 5000);
}

// ---- phone: full screen, its own history entry
// The chat's entry is in the history (pushed, not yet gone back over). A page reached from the chat replaces it, so
// the phone's back from there is one press to the page (PGC_REPLACE: that move's address).
let PGC_ENTRY = false, PGC_REPLACE = '';
function pgcFullOpen(state: PageChatState): void {
  if (state.full) return;
  state.full = true;
  document.body.classList.add('pgc-phone-open');
  history.pushState({...(history.state || {}), pageChat: 1}, '');
  PGC_ENTRY = true;
  pgcDraw(state);
  pgcFocus(state);
}
/** Back to the page where it was: ‹ goes back through the chat's history entry; the phone's back has already. */
function pgcFullClose(state: PageChatState, viaHistory = true): void {
  if (!state.full) return;
  state.full = false;
  // Its composer goes with it: the tab bar it hid while typing comes back (viewport.ts), even if no focusout said so.
  document.body.classList.remove('pgc-phone-open', 'mobile-composer-focus');
  pgcDraw(state);
  if (viaHistory && history.state?.pageChat) { PGC_ENTRY = false; history.back(); }
  document.querySelector<HTMLElement>('#main .ph-chat > [data-pgc-open]')?.focus({preventScroll: true});
}
window.addEventListener('popstate', () => {
  if (!history.state?.pageChat) PGC_ENTRY = false;
  if (PGC?.full && !history.state?.pageChat) pgcFullClose(PGC, false);
});
// A move from inside the full screen chat (a link, a button that sets the address) leaves the page: the tab bar comes
// back with it, and the chat's history entry under the new page goes. The move is held (the router, listening after
// this, does not draw it), the history steps back onto the chat's entry, which becomes the new address, and the router
// draws it once. What is selected may change with the address (a listening opened): the chip follows once it has drawn.
window.addEventListener('hashchange', ev => {
  if (PGC_REPLACE) {
    // Back on the chat's entry: it becomes the new page's.
    ev.stopImmediatePropagation();
    const to = PGC_REPLACE;
    PGC_REPLACE = '';
    history.replaceState(null, '', to);
    window.dispatchEvent(new HashChangeEvent('hashchange', {oldURL: ev.newURL, newURL: location.href}));
    return;
  }
  if (PGC?.full) {
    pgcFullClose(PGC, false);
    if (PGC_ENTRY && !history.state?.pageChat) {
      PGC_ENTRY = false;
      PGC_REPLACE = location.href;
      ev.stopImmediatePropagation();
      history.back();
      return;
    }
  }
  setTimeout(pageChatPicked, 0);
});
// Across the docking width the column docks or folds: it starts as the wider or narrower window has it. Across the phone's width
// the column gives way to the mark, and a full screen chat to the column.
let PGC_WIDE = pgcDocks(), PGC_NARROW = pgcPhone();
window.addEventListener('resize', () => {
  const wide = pgcDocks(), narrow = pgcPhone(), state = PGC;
  const crossed = wide !== PGC_WIDE || narrow !== PGC_NARROW;
  PGC_WIDE = wide; PGC_NARROW = narrow;
  if (!state) return;
  if (state.full && !narrow) pgcFullClose(state);
  if (crossed) state.open = pgcWantOpen(state.page);
  pgcWidthSet();
  pgcDraw(state);
});
// The tab and the mark, wherever they are drawn: the chat opens with the cursor in its composer.
document.addEventListener('click', ev => {
  const el = eventElement(ev)?.closest('[data-pgc-open]');
  if (!el || !PGC || ev.defaultPrevented) return;
  ev.preventDefault();
  pageChatToggle(undefined, true);
});

function pgcClick(ev: MouseEvent): void {
  const state = PGC, target = eventElement(ev);
  const link = target?.closest<HTMLAnchorElement>('a[href^="#"]');
  if (state?.full && link && history.state?.pageChat && !ev.defaultPrevented && ev.button === 0 && !ev.metaKey && !ev.ctrlKey) {
    // Leave through the chat's history entry first, so back from the new page returns to the page, not to a copy.
    ev.preventDefault();
    const href = link.getAttribute('href') || '';
    window.addEventListener('popstate', () => { location.hash = href; }, {once: true});
    pgcFullClose(state);
    return;
  }
  if (!state || !target?.closest('[data-pgc-close]')) return;
  if (state.full) pgcFullClose(state); else pageChatToggle(false);
}

// ---- width: drag the column's left edge, or arrow keys on it
function pgcSetWidth(px: number, keep: boolean): void {
  const w = Math.min(PGC_MAX, Math.max(PGC_MIN, Math.round(px)));
  if (keep) pgcKeep(PGC_WIDTH_KEY, String(w));
  const now = keep ? pgcWidthNow() : w;   // dragged, it follows the pointer; let go, docked, it keeps the page its room
  document.body.style.setProperty('--pgc-w', now + 'px');
  pgcAside().querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(now));
}
function pgcDragStart(ev: PointerEvent): void {
  const state = PGC, grip = eventElement(ev)?.closest<HTMLElement>('.pgc-grip');
  if (!state || !grip || state.full || ev.button !== 0) return;
  ev.preventDefault();
  const panel = pgcAside(), right = panel.getBoundingClientRect().right;
  let last = panel.getBoundingClientRect().width;
  document.body.classList.add('pgc-dragging');
  const move = (e: PointerEvent) => { last = right - e.clientX; pgcSetWidth(last, false); };
  const up = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
    document.body.classList.remove('pgc-dragging');
    pgcSetWidth(last, true);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
}
function pgcKey(ev: KeyboardEvent): void {
  const state = PGC;
  if (!state) return;
  if (ev.key === 'Escape' && !ev.defaultPrevented && !document.querySelector('dialog[open]')) {
    ev.preventDefault();
    ev.stopPropagation();
    if (state.full) pgcFullClose(state); else pageChatToggle(false);
    return;
  }
  if (!eventElement(ev)?.closest('.pgc-grip') || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return;
  ev.preventDefault();
  pgcSetWidth(pgcWidthNow() + (ev.key === 'ArrowLeft' ? 20 : -20), true);
}

/** The page is gone: the chat goes with it. `next`: another page's chat follows at once, so the column, its tab and
    the page's margin stay where they are until it draws, and the page does not jump. */
function pageChatLeave(next = false): void {
  PGC_SEQ++;
  const state = PGC;
  if (!state) return;
  PGC = null;
  pgcUnmount(state);
  // Left while full screen (a link followed from the chat): the tab bar comes back; the history is the router's.
  if (state.full) document.body.classList.remove('pgc-phone-open');
  pgcModal(false);
  const panel = document.querySelector<HTMLElement>('body > aside.pgc');
  if (panel) { panel.classList.remove('pgc-full'); panel.replaceChildren(); }
  if (next) return;
  if (panel) panel.hidden = true;
  const edge = document.querySelector<HTMLElement>('body > .pgc-edge');
  if (edge) edge.hidden = true;
  document.body.classList.remove('pgc-open', 'pgc-docked', 'pgc-tabbed', 'pgc-dragging');
}
