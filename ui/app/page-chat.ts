/* ui/app/page-chat.ts — Chat with a page's owner, from the page (spec page-owner-chat). One pattern for every page.
   A page names only its owner (GET /api/v2/page-owners, backend/page_owners.py; set in Settings > General > Page
   owners). With an owner, the page's header gets Chat: the owner's face and, on a desktop, the word. A page with no
   owner gets no button.
   Desktop: Chat opens a column at the right of the page, which stays where it is and usable: close at its left, the
   owner's face and name, then the chat. Its left edge drags it 260–560 px wide; the width and whether it is open are
   kept in this browser (open per page), so a reload opens it as it was.
   Phone (≤760 px, the pages' own phone layout): the owner's face opens the chat full screen over the page and the tab
   bar: ‹ at left, the name in the middle, the composer at the bottom. ‹ or the phone's back returns to the page as it
   was (a history entry of its own, as the task window's).
   The chat is the viewer's ordinary direct chat with the bot (thread-view.ts threadMount, as the pipeline manager's
   rail, pipeline-rail.ts), the same one as on the bot's page, never a chat of its own. Each message sent from it
   carries refs.from_page: the page and what is open on it (`item`), which the bot's turn reads and the chat shows as
   "From Listening › <item>". The chat loads only when opened, so the page loads no slower.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PageChatItem { kind: string; id: string; name: string }
interface PageChatFrom { page: string; label: string; item?: PageChatItem }
interface PageChatOpts {
  page: string;                    // the page's key in /v2/page-owners
  label: string;                   // its name, as "From <label>" reads
  root: HTMLElement;               // the page's root: takes .pgc-open while the column shows
  button: HTMLElement;             // where the Chat button goes, in the page's header
  panel: HTMLElement;              // the column (an <aside>), placed by the page's own layout
  item?: () => PageChatItem | null; // what is open on the page now
}
interface PageChatState extends PageChatOpts { owner: string; chat: ThvView | null; full: boolean }

let PGC: PageChatState | null = null;
const PGC_MIN = 260, PGC_MAX = 560, PGC_WIDTH = 340;
const PGC_WIDTH_KEY = 'tico.page_chat.width';
const pgcOpenKey = (page: string): string => 'tico.page_chat.open.' + page;
const PGC_PHONE = '(max-width:760px)';
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

/** What a message sent now carries: the page, and what is open on it. */
function pgcFrom(state: PageChatState): PageChatFrom {
  const item = state.item?.() || null;
  return {page: state.page, label: state.label, ...(item?.id && item.name ? {item} : {})};
}

/** Puts Chat in the page's header once the page's owner is known; a page without one gets nothing. Call it each time
    the page draws its shell. */
async function pageChatMount(opts: PageChatOpts): Promise<void> {
  pageChatLeave();
  const owner = await pageChatOwner(opts.page);
  if (!owner || !opts.root.isConnected) return;
  pageChatLeave();
  const state: PageChatState = {...opts, owner, chat: null, full: false};
  PGC = state;
  const name = actorLabel(owner);
  opts.button.innerHTML = `<button type="button" class="ghost pgc-btn" data-pgc-open aria-pressed="false" aria-label="Chat with ${esc(name)}" title="Chat with ${esc(name)}">`
    + `<span class="pgc-face">${actorFace(owner, 22)}</span><span class="pgc-word">Chat</span></button>`;
  opts.button.querySelector('button')?.addEventListener('click', () => pageChatToggle());
  opts.panel.classList.add('pgc');
  opts.panel.setAttribute('aria-label', 'Chat with ' + name);
  opts.root.style.setProperty('--pgc-w', pageChatWidth() + 'px');
  opts.panel.addEventListener('click', pgcClick);
  opts.panel.addEventListener('keydown', pgcKey);
  opts.panel.addEventListener('pointerdown', pgcDragStart);
  pgcDraw(state, !pgcPhone() && pgcOpenKept(opts.page));
}

/** Chat: on a desktop opens or closes the column (kept for the page); on a phone opens the chat full screen. */
function pageChatToggle(open?: boolean): void {
  const state = PGC;
  if (!state) return;
  if (pgcPhone()) { if (open !== false) pgcFullOpen(state); else pgcFullClose(state); return; }
  const next = open ?? state.panel.hidden;
  pgcKeep(pgcOpenKey(state.page), next ? '1' : '0');
  pgcDraw(state, next);
}

function pgcDraw(state: PageChatState, open: boolean): void {
  if (PGC !== state) return;
  const {panel, root, owner} = state, name = actorLabel(owner);
  panel.hidden = !open;
  root.classList.toggle('pgc-open', open && !state.full);
  panel.classList.toggle('pgc-full', state.full);
  state.button.querySelector('[data-pgc-open]')?.setAttribute('aria-pressed', String(open));
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
      : `<button type="button" class="ghost pgc-x" data-pgc-close aria-label="Close chat" title="Close">${PGC_CLOSE}</button>`)
      + `<span class="pgc-who"><span class="pgc-face">${actorFace(owner, 28)}</span><b class="pgc-name">${esc(name)}</b></span>`;
  }
  panel.querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(pageChatWidth()));
  const chat = panel.querySelector<HTMLElement>('.pgc-chat');
  if (chat && !state.chat) {
    state.chat = threadMount(chat, {dm: owner, card: true, header: false, from: () => pgcFrom(state)});
  }
}

// ---- phone: full screen, its own history entry
function pgcFullOpen(state: PageChatState): void {
  if (state.full) return;
  state.full = true;
  document.body.classList.add('pgc-phone-open');
  history.pushState({...(history.state || {}), pageChat: 1}, '');
  pgcDraw(state, true);
  state.panel.querySelector<HTMLTextAreaElement>('.p-text')?.focus({preventScroll: true});
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
// A link followed from inside the chat leaves the page: the tab bar comes back with it.
window.addEventListener('hashchange', () => {
  if (PGC?.full) pgcFullClose(PGC, false);
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
function pgcSetWidth(state: PageChatState, px: number, keep: boolean): void {
  const w = Math.min(PGC_MAX, Math.max(PGC_MIN, Math.round(px)));
  state.root.style.setProperty('--pgc-w', w + 'px');
  state.panel.querySelector('.pgc-grip')?.setAttribute('aria-valuenow', String(w));
  if (keep) pgcKeep(PGC_WIDTH_KEY, String(w));
}
function pgcDragStart(ev: PointerEvent): void {
  const state = PGC, grip = eventElement(ev)?.closest<HTMLElement>('.pgc-grip');
  if (!state || !grip || state.full || ev.button !== 0) return;
  ev.preventDefault();
  const right = state.panel.getBoundingClientRect().right;
  let last = state.panel.getBoundingClientRect().width;
  state.root.classList.add('pgc-dragging');
  const move = (e: PointerEvent) => { last = right - e.clientX; pgcSetWidth(state, last, false); };
  const up = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
    state.root.classList.remove('pgc-dragging');
    pgcSetWidth(state, last, true);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
}
function pgcKey(ev: KeyboardEvent): void {
  const state = PGC;
  if (!state) return;
  if (ev.key === 'Escape' && state.full) { ev.preventDefault(); pgcFullClose(state); return; }
  if (!eventElement(ev)?.closest('.pgc-grip') || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return;
  ev.preventDefault();
  pgcSetWidth(state, pageChatWidth() + (ev.key === 'ArrowLeft' ? 20 : -20), true);
}

/** The page is gone, or draws its shell again: the chat goes with it. */
function pageChatLeave(): void {
  const state = PGC;
  if (!state) return;
  PGC = null;
  threadUnmount(state.chat);
  state.panel.removeEventListener('click', pgcClick);
  state.panel.removeEventListener('keydown', pgcKey);
  state.panel.removeEventListener('pointerdown', pgcDragStart);
  state.root.classList.remove('pgc-open');
  // Left while full screen (a link followed from the chat): the tab bar comes back; the history is the router's.
  if (state.full) document.body.classList.remove('pgc-phone-open');
}
