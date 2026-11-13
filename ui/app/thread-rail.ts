/* ui/app/thread-rail.ts — The Threads page: the thread rail (Threads, Find a thread, Catch up, Pinned, the rest, Unread,
   Browse threads) beside one thread's timeline (#/threads, #/thread/<id>). On a phone the rail and the thread each take the
   screen. The lists are THREADS (ui/app/threads.ts); the timeline is threadMount (ui/app/thread-view.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TR_SVG = {
  plus: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  catchUp: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="4" y="9" width="16" height="11" rx="2"/><path d="M6 6h12M8 3h8"/></svg>',
  browse: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
  lock: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  pin: (on: boolean) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="${on ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="${on ? 0 : 1.6}" stroke-linejoin="round" aria-hidden="true"><path d="M15 3l6 6-3 1-4 4 .5 5-1.5 1.5-4-4-5 5-1-1 5-5-4-4L5 10l5 .5 4-4z"/></svg>`,
};
// `#` before a thread's name, a lock for a private one.
const threadHashHTML = (t: ThreadRow | null | undefined) => `<span class="tr-mark" aria-hidden="true">${threadPrivate(t) ? TR_SVG.lock : '#'}</span>`;
const TR_PHONE = matchMedia('(max-width:760px)'), trPhone = () => TR_PHONE.matches;
// #/threads first drawn at phone width has an empty pane: grown past the phone's width it opens what the computer does.
TR_PHONE.addEventListener('change', () => { if (!TR_PHONE.matches && S.route === THREADS_ROUTE) void pageThreads(); });

// The thread last opened here, which #/threads opens again.
const THREADS_LAST_KEY = 'tico.threads.last';
function threadsLastGet() { try { return localStorage.getItem(THREADS_LAST_KEY) || ''; } catch { return ''; } }
function threadsLastSet(id: string) { try { localStorage.setItem(THREADS_LAST_KEY, id); } catch { /* not kept */ } }

// ----------------------------------------------------------------- the page
// The thread the pane shows, and the element and view that hold it.
interface TrThreadPage {id: string; host: HTMLElement; view: ThvView}
let THREAD_PAGE: TrThreadPage | null = null;
// The rail and the pane beside it, drawn once and kept while one thread gives way to another.
function threadsShell() {
  const main = $('#main');
  main.classList.add('chat-layout', 'thread-layout', 'threads-layout');
  let shell = $('#threads-shell');
  if (shell && main.contains(shell)) return shell;
  threadUnmount(THREAD_PAGE?.view); THREAD_PAGE = null;
  main.innerHTML = `<div class="tr-shell" id="threads-shell" data-mode="list">
    <nav class="tr-rail" id="thread-rail" aria-label="Threads">
      <div class="tr-top"><h2 class="tr-h">Threads</h2>
        <a class="tr-ib" href="${THREADS_NEW}" data-tr-new aria-label="New message" title="New message">${TR_SVG.plus}</a></div>
      <div class="tr-find"><input type="search" id="tr-find" placeholder="Find a thread" aria-label="Find a thread" autocomplete="off"></div>
      <button type="button" class="tr-catch" data-tr-catch hidden>${TR_SVG.catchUp}<span class="tr-catch-word">Catch up</span><span class="tr-badge" hidden></span></button>
      <h3 class="tr-sec" data-sec="pinned" hidden>Pinned</h3><ul class="tr-list" data-list="pinned"></ul>
      <h3 class="tr-sec" data-sec="threads" hidden>Threads</h3><ul class="tr-list" data-list="threads"></ul>
      <button type="button" class="tr-more" data-tr-more hidden>More</button>
      <h3 class="tr-sec" data-sec="unread" hidden>Unread</h3><ul class="tr-list" data-list="unread"></ul>
      <button type="button" class="tr-more" data-tr-unread-more hidden></button>
      <p class="tr-none muted" hidden>No threads</p>
      <span class="tr-fill"></span>
      <button type="button" class="tr-browse" data-tr-browse hidden>${TR_SVG.browse}Browse threads</button>
    </nav>
    <section class="tr-pane" id="thread-pane"></section></div>`;
  shell = $('#threads-shell');
  RAIL_UNREAD = railUnreadFresh();
  const rail = shell.querySelector('.tr-rail');
  rail.querySelector('#tr-find').oninput = () => railRender();
  // While the pointer is on Unread its order holds: counts change in place and a new chat goes last (railUnreadRows).
  // On a phone a touch holds it for 2s.
  const unread = rail.querySelector('[data-list="unread"]');
  const hold = () => { RAIL_UNREAD.order = [...unread.querySelectorAll('[data-thread]')].map((a: HTMLElement) => a.dataset.thread || ''); };
  unread.addEventListener('pointerenter', (ev: PointerEvent) => { if (ev.pointerType !== 'touch') hold(); });
  unread.addEventListener('pointerleave', (ev: PointerEvent) => { if (ev.pointerType !== 'touch') { RAIL_UNREAD.order = null; railRender(); } });
  unread.addEventListener('touchstart', () => {
    hold(); clearTimeout(RAIL_UNREAD.touch);
    RAIL_UNREAD.touch = window.setTimeout(() => { RAIL_UNREAD.order = null; railRender(); }, RAIL_UNREAD_TOUCH_MS);
  }, {passive: true});
  rail.onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('button'); if (!b) return;
    if (b.matches('[data-tr-arch]')) { b.disabled = true; threadsArchive(b.dataset.trArch ?? '').catch(e => { b.disabled = false; toast(errorMessage(e, ''), true); }); }
    else if (b.matches('[data-tr-catch]')) void catchUpOpen();
    else if (b.matches('[data-tr-browse]')) void threadBrowseOpen();
    else if (b.matches('[data-tr-more]')) { b.disabled = true; threadsMore().catch(e => toast(e.message, true)).finally(() => { b.disabled = false; }); }
    else if (b.matches('[data-tr-unread-more]')) { RAIL_UNREAD.all = true; railRender(); }
    else if (b.matches('[data-tr-read]')) void railMarkRead(b.dataset.trRead ?? '');
  };
  return shell;
}
// The pane with no thread open: a picture of how a thread works, one heading and one sentence, then New thread (the
// New message pane). The picture's colours are classes in thread-rail.css.
const TR_EMPTY_HTML = `<div class="tr-empty"><div class="empty-state">
  <svg class="tr-diagram" viewBox="7 0 360 176" aria-hidden="true">
    <rect class="td-card" x="112" y="10" width="240" height="156" rx="14"/>
    <text class="td-title" x="130" y="35"># Launch plan</text>
    <path class="td-line" d="M112 46H352"/>
    <path class="td-line" d="M58 44H112M58 88H112M58 132H112"/>
    <circle class="td-face" cx="40" cy="44" r="18"/><circle class="td-ink" cx="40" cy="39" r="6"/><path class="td-ink" d="M29.5 57a10.5 9 0 0 1 21 0z"/>
    <rect class="td-face" x="22" y="70" width="36" height="36" rx="10"/><rect class="td-ink" x="31" y="84" width="5" height="6" rx="2.5"/><rect class="td-ink" x="44" y="84" width="5" height="6" rx="2.5"/><path class="td-stroke" d="M35 97h10"/>
    <rect class="td-face" x="22" y="114" width="36" height="36" rx="10"/><rect class="td-ink" x="31" y="128" width="5" height="6" rx="2.5"/><rect class="td-ink" x="44" y="128" width="5" height="6" rx="2.5"/><path class="td-stroke" d="M35 141h10"/>
    <circle class="td-face" cx="134" cy="73" r="8"/><circle class="td-ink" cx="134" cy="70.5" r="2.6"/><path class="td-ink" d="M129.8 79a4.2 4 0 0 1 8.4 0z"/>
    <rect class="td-bar" x="152" y="69" width="118" height="8" rx="4"/>
    <rect class="td-face" x="127" y="97" width="14" height="14" rx="4"/><circle class="td-ink" cx="131" cy="103.5" r="1.3"/><circle class="td-ink" cx="137" cy="103.5" r="1.3"/>
    <rect class="td-bar" x="152" y="100" width="150" height="8" rx="4"/>
    <rect class="td-face" x="127" y="128" width="14" height="14" rx="4"/><circle class="td-ink" cx="131" cy="134.5" r="1.3"/><circle class="td-ink" cx="137" cy="134.5" r="1.3"/>
    <rect class="td-bar" x="152" y="131" width="124" height="8" rx="4"/>
  </svg>
  <h2>Talk with your team here</h2>
  <p>Share conversations with your bots and anything else the team should see.</p>
  <a class="primary" href="${THREADS_NEW}" data-tr-new>New thread</a>
</div></div>`;
// #/threads: the rail; on a desktop the thread last opened (else the first pinned) beside it. `?view=inbox` (Inbox's
// old address) opens Catch up; `?view=dms` and `?with=` were the old list's filters.
async function pageThreads() {
  const q = new URLSearchParams(S.route.split('?')[1] || '');
  if (q.has('view') || q.has('with')) {
    location.replace(THREADS_ROUTE);
    if (q.get('view') === 'inbox') void threadsReady().then(() => catchUpOpen());
    return;
  }
  const shell = threadsShell(), pane = shell.querySelector('.tr-pane');
  shell.dataset.mode = 'list';
  railRender();
  if (trPhone()) { threadUnmount(THREAD_PAGE?.view); THREAD_PAGE = null; pane.innerHTML = ''; return; }
  await threadsReady();
  if (S.route !== THREADS_ROUTE) return;
  const listed = [...THREADS.pinned, ...THREADS.threads], last = threadsLastGet();
  const id = (last && (listed.some(t => t.id === last) || THREADS.next) ? last : '') || THREADS.pinned[0]?.id || '';
  if (id) { location.replace(threadHref(id)); return; }
  threadUnmount(THREAD_PAGE?.view); THREAD_PAGE = null;
  if (trUnreadPane(pane)) return;
  pane.innerHTML = TR_EMPTY_HTML;
}
// No thread to open but chats wait for you: say how many, and offer Catch up (never the empty picture).
const trUnreadWords = (n: number): string => `${threadsCountText(n)} chat${n === 1 ? '' : 's'} with new messages`;
function trUnreadPane(pane: Element): boolean {
  const n = THREADS.rail === true ? threadsCatchUp() : 0;
  if (!n) return false;
  pane.innerHTML = `<div class="tr-empty" data-tr-unread-pane><div class="empty-state">${EMPTY_ART.unread}<p><b data-tr-unread-n>${esc(trUnreadWords(n))}</b></p>
    <button type="button" class="primary" data-tr-catchup>Catch up</button></div></div>`;
  const go = pane.querySelector<HTMLButtonElement>('[data-tr-catchup]');
  if (go) go.onclick = () => void catchUpOpen();
  return true;
}
// #/thread/<id>: that thread beside the rail. `?m=<message id>` (a search hit, a quote; `?focus=` too) opens it at that message.
function pageThread(id: string) {
  const q = new URLSearchParams(S.route.split('?')[1] || ''), focus = q.get('m') || q.get('focus') || '';
  const shell = threadsShell(), pane = shell.querySelector('.tr-pane');
  shell.dataset.mode = 'thread';
  railRender();
  if (THREAD_PAGE?.id === id && THREAD_PAGE.host.isConnected) {
    if (focus && THREAD_PAGE.view.state === 'loading') THREAD_PAGE.view.opts.focus = focus;
    else if (focus) void thvFocus(THREAD_PAGE.view, focus);
    return;
  }
  threadUnmount(THREAD_PAGE?.view);
  pane.innerHTML = '<div class="thread-page" id="thread-page"></div>';
  const host = $('#thread-page');
  THREAD_PAGE = {id, host, view: threadMount(host, {id, ...(focus ? {focus} : {}), back: THREADS_ROUTE, members: true})};
  threadsLastSet(id);
}

// ----------------------------------------------------------------- unread
// The rail's Unread section: each chat Catch up counts that the rail does not list (THREADS.unread), while Catch up still
// counts it. Eight rows, then "N more", which shows them all until the viewer leaves Threads; Find a thread lifts the cap.
// The chat opened from it stays in place, current, until another row is opened. Kept per drawn rail (threadsShell).
interface RailUnreadState {all: boolean; keep: ThreadUnread | null; keepAt: number; order: string[] | null; touch: number | undefined}
const RAIL_UNREAD_CAP = 8, RAIL_UNREAD_TOUCH_MS = 2000;
const railUnreadFresh = (): RailUnreadState => ({all: false, keep: null, keepAt: 0, order: null, touch: undefined});
let RAIL_UNREAD: RailUnreadState = railUnreadFresh();
// An Unread row as the thread helpers read a thread (its title, its mark).
const railUnreadThread = (u: ThreadUnread): ThreadRow => ({...u, home: u.home_actor ? {actor: u.home_actor} : null});
// The one other in a 1:1 chat, a bot or a person; else none.
const railUnreadOther = (u: ThreadUnread) => Number(u.dm_with_count) === 1 && u.dm_with?.[0]?.actor ? u.dm_with[0] : null;
function railUnreadName(u: ThreadUnread) {
  const other = railUnreadOther(u);
  if (other) return other.name || actorName(other.actor);
  if (u.audience === 'home' && u.home_actor) return actorName(u.home_actor);
  return threadTitle(railUnreadThread(u));
}
function railUnreadFace(u: ThreadUnread) {
  const other = railUnreadOther(u);
  if (other) return actorAvatar(other.actor, 20);
  if (u.audience === 'home' && u.home_actor) return actorAvatar(u.home_actor, 20);
  return threadMarkHTML(railUnreadThread(u), 20);
}
function railUnreadRowHTML(u: ThreadUnread, cur: string) {
  const here = u.id === cur, n = Number(u.new_count) || 0, name = railUnreadName(u);
  const count = here ? '' : u.mention ? '<span class="tr-count" aria-label="Mentions you">@</span>'
    : n ? `<span class="tr-count" aria-label="${n} new">${esc(threadsCountText(n))}</span>` : '';
  // No Mark read on a phone, on a mention (an open mention keeps the chat in Catch up, so the row would come back) or
  // where nothing hovers (the CSS hides it there: on a touch screen it would cover the count).
  const read = here || trPhone() || u.mention ? '' : `<button type="button" class="tr-arch" data-tr-read="${esc(u.id)}" aria-label="Mark read" title="Mark read"><span class="nav-icon" aria-hidden="true">check</span></button>`;
  return `<li class="tr-item tr-unread"><a class="tr-row${here ? ' cur' : ' unread'}" href="${esc(threadHref(u.id))}" data-thread="${esc(u.id)}"${here ? ' aria-current="page"' : ''}>
    <span class="tr-mark" aria-hidden="true">${railUnreadFace(u)}</span><span class="tr-title">${esc(name)}</span>${count}</a>${read}</li>`;
}
// The rows Unread holds now: the ones Catch up still counts, in the server's order (or the held order while the pointer is
// on the list, new ones last), and the chat opened from it in its place.
function railUnreadRows(cur: string): ThreadUnread[] {
  const cu = THREADS.cu;
  let rows = THREADS.unread.filter(u => !cu || cu.has(u.id) || u.id === cur);
  const keep = RAIL_UNREAD.keep;
  if (keep && keep.id !== cur) RAIL_UNREAD.keep = null;
  else if (keep && !rows.some(u => u.id === keep.id)) rows.splice(Math.min(RAIL_UNREAD.keepAt, rows.length), 0, keep);
  const opened = rows.findIndex(u => u.id === cur);
  if (opened >= 0) { RAIL_UNREAD.keep = rows[opened]!; RAIL_UNREAD.keepAt = opened; }
  const order = RAIL_UNREAD.order;
  if (order) {
    const at = (u: ThreadUnread) => { const i = order.indexOf(u.id); return i < 0 ? order.length : i; };
    rows = rows.map((u, i) => ({u, i})).sort((a, b) => (at(a.u) - at(b.u)) || (a.i - b.i)).map(x => x.u);
  }
  return rows;
}
// Mark read from an Unread row (desktop): the row and its count go at once, nothing opens; the server reads it all.
async function railMarkRead(id: string): Promise<void> {
  if (!id) return;
  threadsAllRead(id);
  try { await post(thvPath(id) + '/read-all', {}); }
  catch (e) { toast(errorMessage(e, ''), true); threadsRecount(); }
}

// ----------------------------------------------------------------- the rail
const railCurrent = () => S.route.startsWith(THREAD_ROUTE) ? decodeURIComponent(S.route.slice(THREAD_ROUTE.length).split(/[/?]/)[0] ?? '') : '';
function railRowHTML(t: ThreadRow, cur: string) {
  const n = threadUnread(t), bold = n > 0 || !!t.me?.activity, here = t.id === cur;
  const arch = t.me?.can_manage ? `<button type="button" class="tr-arch" data-tr-arch="${esc(t.id)}" aria-label="Archive" title="Archive"><span class="nav-icon" aria-hidden="true">archive</span></button>` : '';
  return `<li class="tr-item"><a class="tr-row${bold ? ' unread' : ''}${here ? ' cur' : ''}" href="${esc(threadHref(t))}" data-thread="${esc(t.id)}"${here ? ' aria-current="page"' : ''}>
    ${threadHashHTML(t)}<span class="tr-title">${esc(threadTitle(t))}</span>${t.live_call ? `<span class="tr-call" title="Call">${CALL_SVG}</span>` : ''}${n ? `<span class="tr-count" aria-label="${n} unread">${esc(threadsCountText(n))}</span>` : ''}</a>${arch}</li>`;
}
function railRender() {
  const rail = $('#thread-rail'); if (!rail?.isConnected) return;
  const words = (rail.querySelector('#tr-find')?.value || '').trim().toLowerCase(), cur = railCurrent();
  const shown = (list: ThreadRow[]) => words ? list.filter(t => threadTitle(t).toLowerCase().includes(words)) : list;
  const lists = {pinned: shown(THREADS.pinned), threads: shown(THREADS.threads)};
  for (const [key, list] of Object.entries(lists)) {
    rail.querySelector(`[data-sec="${key}"]`).hidden = !list.length;
    patchHTML(rail.querySelector(`[data-list="${key}"]`), list.map(t => railRowHTML(t, cur)).join(''));
  }
  const unread = railUnreadRows(cur).filter(u => !words || railUnreadName(u).toLowerCase().includes(words));
  const all = RAIL_UNREAD.all || !!words || unread.length <= RAIL_UNREAD_CAP;
  rail.querySelector('[data-sec="unread"]').hidden = !unread.length;
  patchHTML(rail.querySelector('[data-list="unread"]'), (all ? unread : unread.slice(0, RAIL_UNREAD_CAP)).map(u => railUnreadRowHTML(u, cur)).join(''));
  const more = rail.querySelector('[data-tr-unread-more]');
  more.hidden = all; more.textContent = all ? '' : `${unread.length - RAIL_UNREAD_CAP} more`;
  rail.querySelector('.tr-none').hidden = !(words && !lists.pinned.length && !lists.threads.length && !unread.length);
  rail.querySelector('[data-tr-more]').hidden = !THREADS.next || !!words;
  const can = THREADS.rail === true, n = threadsCatchUp();
  const catchUp = rail.querySelector('[data-tr-catch]'), badge = catchUp.querySelector('.tr-badge');
  catchUp.hidden = !can;
  catchUp.classList.toggle('cur', !!CU.open);
  badge.hidden = !n; badge.textContent = threadsCountText(n);
  catchUp.setAttribute('aria-label', n ? `Catch up, ${n}` : 'Catch up');
  rail.querySelector('[data-tr-browse]').hidden = !can;
  // The pane's "N chats with new messages" follows the count; at 0 (and Catch up closed: nothing moves behind it) the
  // pane is emptied first, so this cannot run again, and pageThreads() picks what shows instead.
  const un = document.querySelector('[data-tr-unread-n]'), unPane = un?.closest('.tr-pane');
  if (un && unPane) { if (n) un.textContent = trUnreadWords(n); else if (!CU.open && S.route === THREADS_ROUTE) { unPane.innerHTML = ''; void pageThreads(); } }
}

// ----------------------------------------------------------------- browse
// Company and team threads the viewer reads but is not in (GET /v2/threads/browse); Join follows one.
function threadBrowseOpen(): void {
  type BrowseRow = ThreadRow & {member_count?: number};
  type BrowsePage = {threads?: BrowseRow[]; items?: BrowseRow[]; next_cursor?: string | null};
  const dlg = threadDialog('thread-browse', 'Browse threads');
  dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog tr-browse-dlg';
  dlg.innerHTML = `<form>${threadDialogHead('Browse threads')}<div class="dash-form">
      <div class="tr-browse-tabs" role="group" aria-label="Which threads"><button type="button" class="ghost small on" data-tab="threads" aria-pressed="true">Threads</button><button type="button" class="ghost small" data-tab="archived" aria-pressed="false">Archived</button></div>
      <input type="search" class="tr-browse-q" placeholder="Find a thread" aria-label="Find a thread" autocomplete="off">
      <ul class="tr-browse-list" aria-live="polite"><li class="muted">Loading…</li></ul>
      <button type="button" class="ghost small tr-browse-more" hidden>More</button></div></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector<HTMLFormElement>('form')!;
  const q = form.querySelector<HTMLInputElement>('.tr-browse-q')!;
  const list = form.querySelector<HTMLUListElement>('.tr-browse-list')!;
  const more = form.querySelector<HTMLButtonElement>('.tr-browse-more')!;
  form.onsubmit = ev => ev.preventDefault();
  let next: string | null = null, seq = 0;
  let mode: 'threads' | 'archived' = 'threads';
  const rowHTML = (t: BrowseRow): string => {
    if (mode === 'archived') return `<li data-thread="${esc(t.id)}"><a class="tr-browse-name" href="${esc(threadHref(t))}" data-close>${threadHashHTML(t)}<span>${esc(threadTitle(t))}</span></a><button type="button" class="ghost small" data-unarchive>Unarchive</button></li>`;
    const n = Number(t.member_count) || 0;
    return `<li data-thread="${esc(t.id)}"><a class="tr-browse-name" href="${esc(threadHref(t))}" data-close>${threadHashHTML(t)}<span>${esc(threadTitle(t))}</span></a>
      ${n ? `<span class="muted tr-browse-n">${n}</span>` : ''}<button type="button" class="ghost small" data-join>Join</button></li>`;
  };
  const load = async (add = false): Promise<void> => {
    const mine = ++seq, words = q.value.trim();
    try {
      const cur = add && next ? '&cursor=' + encodeURIComponent(next) : '';
      const path = mode === 'archived' ? '/v2/threads/archived?limit=50' + cur : '/v2/threads/browse?limit=50&q=' + encodeURIComponent(words) + cur;
      const r = await get<BrowsePage>(path, {fresh: true});
      if (mine !== seq) return;
      const rows = (r.threads || r.items || []).map(rowHTML).join('');
      if (add) list.insertAdjacentHTML('beforeend', rows);
      else list.innerHTML = rows || (mode === 'archived' ? '<li class="muted">No archived threads</li>' : '<li class="muted">No threads</li>');
      next = r.next_cursor || null; more.hidden = !next;
    } catch (e) { if (mine === seq) list.innerHTML = `<li class="err">${esc(errorMessage(e, ''))}</li>`; }
  };
  let timer: number | undefined;
  q.oninput = () => { clearTimeout(timer); timer = window.setTimeout(() => load(), 200); };
  more.onclick = () => load(true);
  form.addEventListener('click', (ev: MouseEvent) => {
    const tab = eventElement(ev)?.closest<HTMLElement>('[data-tab]');
    if (!tab) return;
    mode = tab.dataset.tab === 'archived' ? 'archived' : 'threads';
    form.querySelectorAll<HTMLElement>('[data-tab]').forEach(b => { const on = b === tab; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); });
    q.hidden = mode === 'archived'; more.hidden = true; next = null;
    void load();
  });
  list.onclick = async (ev: MouseEvent): Promise<void> => {
    const target = eventElement(ev);
    if (target?.closest('[data-close]')) { dlg.close(); return; }
    const u = target?.closest<HTMLButtonElement>('[data-unarchive]');
    if (u) {
      const row = u.closest<HTMLElement>('[data-thread]');
      if (!row) return;
      u.disabled = true;
      try {
        threadsUnarchived(await patch(thvPath(row.dataset.thread), {archived: false}));
        row.remove();
        if (!list.querySelector('[data-thread]')) list.innerHTML = '<li class="muted">No archived threads</li>';
      } catch (e) { u.disabled = false; toast(errorMessage(e, ''), true); }
      return;
    }
    const b = target?.closest<HTMLButtonElement>('[data-join]');
    if (!b) return;
    const id = b.closest<HTMLElement>('[data-thread]')?.dataset.thread;
    if (!id) return;
    b.disabled = true;
    try {
      const r = await put(thvPath(id) + '/follow', {notify: 'live'});
      b.textContent = 'Joined';
      const t = await get(thvPath(id), {fresh: true}).catch(() => null);
      if (t?.id) threadsPut({...t, me: {...t.me, following: true, notify: r?.notify || 'live'}});
    } catch (e) { b.disabled = false; toast(errorMessage(e, ''), true); }
  };
  dlg.showModal();
  q.focus();
  void load();
}
