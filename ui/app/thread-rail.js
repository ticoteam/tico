/* ui/app/thread-rail.js — The Threads page: the thread rail (Threads, Find a thread, Catch up, Pinned, the rest, Browse
   threads) beside one thread's timeline (#/threads, #/thread/<id>). On a phone the rail and the thread each take the
   screen. The lists are THREADS (ui/app/threads.js); the timeline is threadMount (ui/app/thread-view.js).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TR_SVG = {
  pencil: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>',
  catchUp: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="4" y="9" width="16" height="11" rx="2"/><path d="M6 6h12M8 3h8"/></svg>',
  browse: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
  lock: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  pin: on => `<svg width="15" height="15" viewBox="0 0 24 24" fill="${on ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="${on ? 0 : 1.6}" stroke-linejoin="round" aria-hidden="true"><path d="M15 3l6 6-3 1-4 4 .5 5-1.5 1.5-4-4-5 5-1-1 5-5-4-4L5 10l5 .5 4-4z"/></svg>`,
};
// `#` before a thread's name, a lock for a private one.
const threadHashHTML = t => `<span class="tr-mark" aria-hidden="true">${threadPrivate(t) ? TR_SVG.lock : '#'}</span>`;
const trPhone = () => matchMedia('(max-width:760px)').matches;

// The thread last opened here, which #/threads opens again.
const THREADS_LAST_KEY = 'tico.threads.last';
function threadsLastGet() { try { return localStorage.getItem(THREADS_LAST_KEY) || ''; } catch { return ''; } }
function threadsLastSet(id) { try { localStorage.setItem(THREADS_LAST_KEY, id); } catch { /* not kept */ } }

// ----------------------------------------------------------------- the page
let THREAD_PAGE = null;
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
        <button type="button" class="tr-ib" data-tr-new aria-label="New thread" title="New thread">${TR_SVG.pencil}</button></div>
      <div class="tr-find"><input type="search" id="tr-find" placeholder="Find a thread" aria-label="Find a thread" autocomplete="off"></div>
      <button type="button" class="tr-catch" data-tr-catch hidden>${TR_SVG.catchUp}<span class="tr-catch-word">Catch up</span><span class="tr-badge" hidden></span></button>
      <h3 class="tr-sec" data-sec="pinned" hidden>Pinned</h3><ul class="tr-list" data-list="pinned"></ul>
      <h3 class="tr-sec" data-sec="threads" hidden>Threads</h3><ul class="tr-list" data-list="threads"></ul>
      <button type="button" class="tr-more" data-tr-more hidden>More</button>
      <p class="tr-none muted" hidden>No threads</p>
      <span class="tr-fill"></span>
      <button type="button" class="tr-browse" data-tr-browse hidden>${TR_SVG.browse}Browse threads</button>
    </nav>
    <section class="tr-pane" id="thread-pane"></section></div>`;
  shell = $('#threads-shell');
  const rail = shell.querySelector('.tr-rail');
  rail.querySelector('#tr-find').oninput = () => railRender();
  rail.onclick = ev => {
    const b = ev.target.closest('button'); if (!b) return;
    if (b.matches('[data-tr-new]')) threadNewOpen();
    else if (b.matches('[data-tr-catch]')) void catchUpOpen();
    else if (b.matches('[data-tr-browse]')) void threadBrowseOpen();
    else if (b.matches('[data-tr-more]')) { b.disabled = true; threadsMore().catch(e => toast(e.message, true)).finally(() => { b.disabled = false; }); }
  };
  shell.querySelector('.tr-pane').addEventListener('click', ev => { if (ev.target.closest('[data-tr-new]')) threadNewOpen(); });
  return shell;
}
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
  pane.innerHTML = '<div class="tr-empty"><button type="button" class="primary" data-tr-new>New thread</button></div>';
}
// #/thread/<id>: that thread beside the rail. `?m=<message id>` (a search hit) opens it at that message.
function pageThread(id) {
  const focus = new URLSearchParams(S.route.split('?')[1] || '').get('m') || '';
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

// ----------------------------------------------------------------- the rail
const railCurrent = () => S.route.startsWith(THREAD_ROUTE) ? decodeURIComponent(S.route.slice(THREAD_ROUTE.length).split(/[/?]/)[0]) : '';
function railRowHTML(t, cur) {
  const n = threadUnread(t), bold = n > 0 || !!t.me?.activity, here = t.id === cur;
  return `<li><a class="tr-row${bold ? ' unread' : ''}${here ? ' cur' : ''}" href="${esc(threadHref(t))}" data-thread="${esc(t.id)}"${here ? ' aria-current="page"' : ''}>
    ${threadHashHTML(t)}<span class="tr-title">${esc(threadTitle(t))}</span>${n ? `<span class="tr-count" aria-label="${n} unread">${esc(threadsCountText(n))}</span>` : ''}</a></li>`;
}
function railRender() {
  const rail = $('#thread-rail'); if (!rail?.isConnected) return;
  const words = (rail.querySelector('#tr-find')?.value || '').trim().toLowerCase(), cur = railCurrent();
  const shown = list => words ? list.filter(t => threadTitle(t).toLowerCase().includes(words)) : list;
  const lists = {pinned: shown(THREADS.pinned), threads: shown(THREADS.threads)};
  for (const [key, list] of Object.entries(lists)) {
    rail.querySelector(`[data-sec="${key}"]`).hidden = !list.length;
    patchHTML(rail.querySelector(`[data-list="${key}"]`), list.map(t => railRowHTML(t, cur)).join(''));
  }
  rail.querySelector('.tr-none').hidden = !(words && !lists.pinned.length && !lists.threads.length);
  rail.querySelector('[data-tr-more]').hidden = !THREADS.next || !!words;
  const can = THREADS.rail === true, n = threadsCatchUp();
  const catchUp = rail.querySelector('[data-tr-catch]'), badge = catchUp.querySelector('.tr-badge');
  catchUp.hidden = !can;
  catchUp.classList.toggle('cur', !!CU.open);
  badge.hidden = !n; badge.textContent = threadsCountText(n);
  catchUp.setAttribute('aria-label', n ? `Catch up, ${n}` : 'Catch up');
  rail.querySelector('[data-tr-browse]').hidden = !can;
}

// ----------------------------------------------------------------- browse
// Company and team threads the viewer reads but is not in (GET /v2/threads/browse); Join follows one.
function threadBrowseOpen() {
  const dlg = threadDialog('thread-browse', 'Browse threads');
  dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog tr-browse-dlg';
  dlg.innerHTML = `<form>${threadDialogHead('Browse threads')}<div class="dash-form">
      <input type="search" class="tr-browse-q" placeholder="Find a thread" aria-label="Find a thread" autocomplete="off">
      <ul class="tr-browse-list" aria-live="polite"><li class="muted">Loading…</li></ul>
      <button type="button" class="ghost small tr-browse-more" hidden>More</button></div></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form'), q = form.querySelector('.tr-browse-q'), list = form.querySelector('.tr-browse-list'), more = form.querySelector('.tr-browse-more');
  form.onsubmit = ev => ev.preventDefault();
  let next = null, seq = 0;
  const rowHTML = t => {
    const n = Number(t.member_count) || 0;
    return `<li data-thread="${esc(t.id)}"><a class="tr-browse-name" href="${esc(threadHref(t))}" data-close>${threadHashHTML(t)}<span>${esc(threadTitle(t))}</span></a>
      ${n ? `<span class="muted tr-browse-n">${n}</span>` : ''}<button type="button" class="ghost small" data-join>Join</button></li>`;
  };
  const load = async (add = false) => {
    const mine = ++seq, words = q.value.trim();
    try {
      const r = await get('/v2/threads/browse?limit=50&q=' + encodeURIComponent(words) + (add && next ? '&cursor=' + encodeURIComponent(next) : ''), {fresh: true});
      if (mine !== seq) return;
      const rows = (r?.threads || r?.items || []).map(rowHTML).join('');
      if (add) list.insertAdjacentHTML('beforeend', rows);
      else list.innerHTML = rows || '<li class="muted">No threads</li>';
      next = r?.next_cursor || null; more.hidden = !next;
    } catch (e) { if (mine === seq) list.innerHTML = `<li class="err">${esc(e.message)}</li>`; }
  };
  let timer = 0;
  q.oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(), 200); };
  more.onclick = () => load(true);
  list.onclick = async ev => {
    if (ev.target.closest('[data-close]')) { dlg.close(); return; }
    const b = ev.target.closest('[data-join]'); if (!b) return;
    const id = b.closest('[data-thread]').dataset.thread;
    b.disabled = true;
    try {
      const r = await put(thvPath(id) + '/follow', {notify: 'live'});
      b.textContent = 'Joined';
      const t = await get(thvPath(id), {fresh: true}).catch(() => null);
      if (t?.id) threadsPut({...t, me: {...t.me, following: true, notify: r?.notify || 'live'}});
    } catch (e) { b.disabled = false; toast(e.message, true); }
  };
  dlg.showModal();
  q.focus();
  void load();
}
