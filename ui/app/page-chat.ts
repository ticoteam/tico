/* ui/app/page-chat.ts — Chat with a page's owner, from the page (specs page-owner-chat, chat-on-every-page). One pattern
   for every page. A page names only its owner (GET /api/v2/page-owners, backend/page_owners.py; set in Settings >
   General > Page owners): the bot that does the page's work. With an owner, the page's header gets Chat: the owner's
   face and, on a desktop, the word. A page with no owner gets no button.
   Desktop: one shared column (<aside class="pgc">, drawn here and appended to the app shell; pages pass no panel) at the
   right of the window, from the top of #main to the bottom. Below 1600 px it floats over the page, which keeps its
   width; from 1600 px body.pgc-docked gives #main a right margin of its width, so the page sits beside it. Close at its
   left, the owner's face and name, then the chat. Its left edge drags it 260–560 px wide; the width and whether it is
   open are kept in this browser (open per page). Esc inside it closes it. ⌘J (Ctrl+J) opens it with the cursor in its
   composer and closes it again (ask-panel.ts asks pageChatKey first); Ask and a page chat are never open together.
   A page may hold the column (pageChatHold): Listening's and Automations' editor takes its place while open. A page's
   own panel may instead swap with it (`panel`, Rules' detail): opening Chat closes the panel, and the page closes the
   chat when the panel opens (pageChatYield).
   Calendar and Meetings, whose owner is the Assistant, get the same Chat with the Assistant's face, which opens Ask
   (pageChatAsk): no second Assistant chat.
   Phone (≤760 px, the pages' own phone layout): the owner's face opens the chat full screen over the page and the tab
   bar: ‹ at left, the name in the middle, the composer at the bottom. ‹ or the phone's back returns to the page as it
   was (a history entry of its own, as the task window's).
   The chat is the viewer's ordinary direct chat with the bot (thread-view.ts threadMount, as the pipeline manager's
   rail, pipeline-rail.ts), the same one as on the bot's page, never a chat of its own. Each message sent from it
   carries refs.from_page: the page and what is selected on it (`item`), which the bot's turn reads and the chat shows as
   "From Goals › <item>". The item shows above the composer as a chip, so the person sees what the next message carries.
   The chat loads only when opened, so the page loads no slower.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PageChatItem { kind: string; id: string; name: string }
interface PageChatFrom { page: string; label: string; item?: PageChatItem }
interface PageChatOpts {
  page: string;                    // the page's key in /v2/page-owners
  label: string;                   // its name, as "From <label>" reads
  button: HTMLElement;             // where the Chat button goes, in the page's header
  stays: (route: string) => boolean; // the addresses that are still this page: leaving them closes its chat
  item?: () => PageChatItem | null; // what is selected on the page now
  empty?: () => string;            // the chat's empty state, written from the page when it opens
  keyDrop?: () => string[];        // Tools: the composer catches keys (tools-key-drop.ts); the tools in Problem, by name
  panel?: {open: () => boolean; close: () => void}; // Rules: the page's detail panel, which the chat takes the place of
}
interface PageChatState extends PageChatOpts { owner: string; chat: ThvView | null; full: boolean; open: boolean; held: boolean }

let PGC: PageChatState | null = null;
let PGC_SEQ = 0;                   // a mount still waiting on the owner draws nothing once the page has moved on
const PGC_MIN = 260, PGC_MAX = 560, PGC_WIDTH = 340;
const PGC_WIDTH_KEY = 'tico.page_chat.width';
const pgcOpenKey = (page: string): string => 'tico.page_chat.open.' + page;
const PGC_PHONE = '(max-width:760px)', PGC_DOCK = '(min-width:1600px)';
const pgcPhone = (): boolean => window.matchMedia(PGC_PHONE).matches;
const PGC_CLOSE = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const PGC_BACK = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';

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
const pgcOpenKept = (page: string): boolean => pgcKept(pgcOpenKey(page)) === '1';

/** Each page's owner as the server has it now: a changed owner shows at the page's next load. */
async function pageChatOwner(page: string): Promise<string | null> {
  try {
    const r = await get<{pages?: Record<string, {bot?: string | null}>}>('/v2/page-owners', {fresh: true});
    return r?.pages?.[page]?.bot || null;
  } catch { return null; }
}

/** What a message sent now carries: the page, and what is selected on it. */
function pgcFrom(state: PageChatState): PageChatFrom {
  const item = pgcItem(state);
  return {page: state.page, label: state.label, ...(item ? {item} : {})};
}
function pgcItem(state: PageChatState): PageChatItem | null {
  const item = state.item?.() || null;
  return item?.id && item.name ? item : null;
}

/** The one column every page's chat opens in, made the first time it is needed. */
function pgcAside(): HTMLElement {
  let aside = document.querySelector<HTMLElement>('body > aside.pgc');
  if (aside) return aside;
  aside = document.createElement('aside');
  aside.className = 'pgc';
  aside.hidden = true;
  aside.addEventListener('click', pgcClick);
  aside.addEventListener('keydown', pgcKey);
  aside.addEventListener('pointerdown', pgcDragStart);
  document.body.appendChild(aside);
  return aside;
}
/** The column starts where #main starts: below the top bar and any banner. A banner that comes or goes resizes #main,
    so the column follows it while open. */
const PGC_MAIN_WATCH = window.ResizeObserver ? new ResizeObserver(() => { if (pageChatShown()) pgcPlace(); }) : null;
function pgcPlace(): void {
  const main = document.getElementById('main');
  if (main) document.body.style.setProperty('--pgc-top', Math.max(0, Math.round(main.getBoundingClientRect().top)) + 'px');
  if (main) PGC_MAIN_WATCH?.observe(main);
}

/** Puts Chat in the page's header once the page's owner is known; a page without one gets nothing. Call it each time
    the page draws its header: the same page drawn again keeps its chat as it is. */
async function pageChatMount(opts: PageChatOpts): Promise<void> {
  const seq = ++PGC_SEQ;
  const owner = await pageChatOwner(opts.page);
  if (seq !== PGC_SEQ || !opts.button.isConnected) return;
  if (PGC && (PGC.page !== opts.page || PGC.owner !== owner)) pageChatLeave();
  if (!owner) return;
  const state: PageChatState = PGC || {...opts, owner, chat: null, full: false, open: false, held: false};
  Object.assign(state, {button: opts.button, stays: opts.stays, item: opts.item, label: opts.label, panel: opts.panel});
  const name = actorLabel(owner);
  opts.button.innerHTML = `<button type="button" class="ghost pgc-btn" data-pgc-open aria-pressed="false" aria-label="Chat with ${esc(name)}" title="Chat with ${esc(name)} (⌘J)">`
    + `<span class="pgc-face">${actorFace(owner, 22)}</span><span class="pgc-word">Chat</span></button>`;
  opts.button.querySelector('button')?.addEventListener('click', () => pageChatToggle());
  if (PGC === state) { pgcDraw(state, state.open); return; }
  PGC = state;
  const aside = pgcAside();
  aside.setAttribute('aria-label', 'Chat with ' + name);
  document.body.style.setProperty('--pgc-w', pageChatWidth() + 'px');
  // A panel the address opened (a rule's detail) keeps its place: the chat kept open waits until it is asked for.
  pgcDraw(state, !pgcPhone() && pgcOpenKept(opts.page) && !opts.panel?.open());
}

/** Calendar and Meetings: Chat with the Assistant's face, which opens Ask (the Assistant's one chat, which already
    carries the page). Ask's own state says whether it is pressed (ask-panel.ts askSync). */
function pageChatAsk(button: HTMLElement): void {
  const owner = 'bot:' + assistantBot(), name = askName();
  button.innerHTML = `<button type="button" class="ghost pgc-btn" data-pgc-ask aria-pressed="${!!ASK?.open}" aria-label="Chat with ${esc(name)}" title="Chat with ${esc(name)} (⌘J)">`
    + `<span class="pgc-face">${actorFace(owner, 22)}</span><span class="pgc-word">Chat</span></button>`;
  button.querySelector('button')?.addEventListener('click', () => { if (!askAway()) askToggle(); });
}

/** Whether the page's chat column shows now (a desktop's; a phone's full screen is not a column). */
const pageChatShown = (): boolean => !!PGC && PGC.open && !PGC.full && !PGC.held;

/** Chat: on a desktop opens or closes the column (kept for the page); on a phone opens the chat full screen. */
function pageChatToggle(open?: boolean, focus = false): void {
  const state = PGC;
  if (!state) return;
  if (pgcPhone()) { if (open !== false) pgcFullOpen(state); else pgcFullClose(state); return; }
  const next = open ?? !(state.open && !state.held);
  if (next) state.held = false;   // Chat asked for while a page's panel holds the column: the chat comes back over it
  if (next && state.panel?.open()) state.panel.close();   // Rules: the chat takes the detail's place
  pgcKeep(pgcOpenKey(state.page), next ? '1' : '0');
  const inside = pgcAside().contains(document.activeElement);
  pgcDraw(state, next);
  if (next && focus) pgcFocus(state);
  else if (!next && inside) state.button.querySelector<HTMLElement>('[data-pgc-open]')?.focus({preventScroll: true});
}

/** ⌘J: on a desktop page with a chat, opens it with the cursor in its composer, or closes it. False: not ours (Ask's). */
function pageChatKey(): boolean {
  if (!PGC || pgcPhone()) return false;
  // A window over the page (a goal's) keeps the keyboard: the key is taken, and nothing opens behind it.
  if (document.querySelector('dialog[open]')) return true;
  pageChatToggle(!pageChatShown(), true);
  return true;
}

/** The page's own panel (`panel`, Rules' detail) has just opened: it takes the open chat's place, which closes. */
function pageChatYield(): void {
  if (pageChatShown()) pageChatToggle(false);
}

/** A page's own panel takes the column's place while it is open (Listening's editor); the column comes back after. */
function pageChatHold(on: boolean): void {
  if (!PGC || PGC.held === on) return;
  PGC.held = on;
  pgcDraw(PGC, PGC.open);
}

function pgcDraw(state: PageChatState, open: boolean): void {
  if (PGC !== state) return;
  const panel = pgcAside(), {owner} = state, name = actorLabel(owner);
  state.open = open;
  const shows = open && (state.full || !state.held);
  panel.hidden = !shows;
  panel.classList.toggle('pgc-full', state.full);
  document.body.classList.toggle('pgc-open', shows && !state.full);
  pgcDock();
  if (shows && !state.full) { pgcPlace(); if (ASK?.open) askToggle(false, false); }
  state.button.querySelector('[data-pgc-open]')?.setAttribute('aria-pressed', String(shows));
  if (!open) { threadUnmount(state.chat); state.chat = null; panel.replaceChildren(); return; }
  if (!panel.querySelector(':scope > .pgc-shell')) {
    panel.innerHTML = `<div class="pgc-shell">
      <div class="pgc-grip" role="separator" aria-orientation="vertical" aria-label="Chat width" aria-valuemin="${PGC_MIN}" aria-valuemax="${PGC_MAX}" tabindex="0"></div>
      <header class="pgc-head"></header><div class="pgc-chat"></div></div>`;
  }
  const head = panel.querySelector<HTMLElement>('.pgc-head');
  if (head) {
    head.innerHTML = (state.full
      ? `<button type="button" class="ghost pgc-x" data-pgc-close aria-label="Back" title="Back">${PGC_BACK}</button>`
      : `<button type="button" class="ghost pgc-x" data-pgc-close aria-label="Close chat" title="Close (Esc)">${PGC_CLOSE}</button>`)
      + `<span class="pgc-who"><span class="pgc-face">${actorFace(owner, 28)}</span><b class="pgc-name">${esc(name)}</b></span>`;
  }
  panel.querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(pageChatWidth()));
  const chat = panel.querySelector<HTMLElement>('.pgc-chat');
  if (chat && !state.chat) {
    state.chat = threadMount(chat, {dm: owner, card: true, header: false, from: () => pgcFrom(state),
      ...(state.empty ? {empty: state.empty()} : {}), ...(state.keyDrop ? {keyDrop: {tools: state.keyDrop}} : {})});
    chat.querySelector('.thv-composer')?.insertAdjacentHTML('beforebegin', '<div class="pgc-chip" hidden></div>');
  }
  pageChatPicked();
}

/** The page's selection changed (or may have): the chip above the composer shows what the next message carries. */
function pageChatPicked(): void {
  const chip = PGC?.open ? pgcAside().querySelector<HTMLElement>('.pgc-chip') : null;
  if (!PGC || !chip) return;
  const name = pgcItem(PGC)?.name || '';
  if (chip.textContent !== name) chip.textContent = name;
  chip.title = name;
  chip.hidden = !name;
}

/** Floats over the page below 1600 px; from 1600 px the page makes room for it. */
function pgcDock(): void {
  document.body.classList.toggle('pgc-docked', document.body.classList.contains('pgc-open') && window.matchMedia(PGC_DOCK).matches);
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
    if (PGC === state && state.open) el.focus({preventScroll: true});
  });
  watch.observe(panel, {childList: true, subtree: true});
  setTimeout(() => watch.disconnect(), 5000);
}

// ---- phone: full screen, its own history entry
function pgcFullOpen(state: PageChatState): void {
  if (state.full) return;
  state.full = true;
  document.body.classList.add('pgc-phone-open');
  history.pushState({...(history.state || {}), pageChat: 1}, '');
  pgcDraw(state, true);
  pgcAside().querySelector<HTMLTextAreaElement>('.p-text')?.focus({preventScroll: true});
}
/** Back to the page where it was: ‹ goes back through the chat's history entry; the phone's back has already. */
function pgcFullClose(state: PageChatState, viaHistory = true): void {
  if (!state.full) return;
  state.full = false;
  document.body.classList.remove('pgc-phone-open');
  pgcDraw(state, false);
  if (viaHistory && history.state?.pageChat) history.back();
  state.button.querySelector<HTMLElement>('[data-pgc-open]')?.focus({preventScroll: true});
}
window.addEventListener('popstate', () => {
  if (PGC?.full && !history.state?.pageChat) pgcFullClose(PGC, false);
});
// A link followed from inside the chat leaves the page: the tab bar comes back with it. What is selected may change
// with the address (a listening opened): the chip follows once the page has drawn.
window.addEventListener('hashchange', () => {
  if (PGC?.full) pgcFullClose(PGC, false);
  setTimeout(pageChatPicked, 0);
});
window.addEventListener('resize', () => { if (PGC?.open) { pgcDock(); pgcPlace(); } });

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
  document.body.style.setProperty('--pgc-w', w + 'px');
  pgcAside().querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(w));
  if (keep) pgcKeep(PGC_WIDTH_KEY, String(w));
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
  pgcSetWidth(pageChatWidth() + (ev.key === 'ArrowLeft' ? 20 : -20), true);
}

/** The router, on every address: a page's chat closes once the address is no longer that page. */
function pageChatRoute(route: string): void {
  if (PGC && !PGC.stays(route)) pageChatLeave();
}

/** The page is gone: the chat goes with it. */
function pageChatLeave(): void {
  PGC_SEQ++;
  const state = PGC;
  if (!state) return;
  PGC = null;
  threadUnmount(state.chat);
  const panel = document.querySelector<HTMLElement>('body > aside.pgc');
  if (panel) { panel.hidden = true; panel.classList.remove('pgc-full'); panel.replaceChildren(); }
  document.body.classList.remove('pgc-open', 'pgc-docked', 'pgc-dragging');
  // Left while full screen (a link followed from the chat): the tab bar comes back; the history is the router's.
  if (state.full) document.body.classList.remove('pgc-phone-open');
}
