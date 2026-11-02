/* ui/app/threads.js — Inbox in the sidebar: the chats that wait on the viewer (Needs attention), live from the
   `threads` topic of GET /api/v2/events, and each bot's DM unread for the Team list. Shared helpers for thread titles, marks and actors live here too; the
   timeline is ui/app/thread-view.js and the Threads page, new thread and Make task are ui/app/threads-page.js.
   Shapes: docs/dev/threads-contract.md §9 and §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const THREADS_ROUTE = '#/threads', THREAD_ROUTE = '#/thread/', INBOX_ROUTE = '#/threads?view=inbox';
// About ten rows, then All: the sidebar is a way in, the Threads page is the list.
const THREADS_SIDE_MAX = 10;
// `on`: null until the server answered GET /v2/threads once; false when it has no threads (an older server).
// `botDms`: {bot actor: unread} for the viewer's DMs with one bot, which the Team list shows on that bot (sidebar.js).
const THREADS = {on: null, attention: [], threads: [], dms: [], botDms: {}, loading: null, wired: false};
// The list's order, the viewer's choice kept in this browser: Important (each thread's 1-10 level, then newest, noise
// left out; the default) or Recent (newest first). GET /v2/threads?sort=.
const THREADS_SORTS = [['important', 'Important'], ['recent', 'Recent']];
const THREADS_SORT_KEY = 'tico.threads.sort';
function threadsSort() {
  let v = '';
  try { v = localStorage.getItem(THREADS_SORT_KEY) || ''; } catch { /* storage blocked: the default */ }
  return THREADS_SORTS.some(([k]) => k === v) ? v : 'important';
}
function threadsSortSet(v) {
  try { localStorage.setItem(THREADS_SORT_KEY, v); } catch { /* storage blocked: this page only */ }
}
// Inbox / Important / Recent, for the Threads page header: `cur` is a sort, or 'inbox'.
const threadsSortHTML = cur => `<div class="tabs slim thp-sort" role="tablist" aria-label="View">${[['inbox', 'Inbox'], ...THREADS_SORTS].map(([k, label]) =>
  `<button type="button" role="tab" ${k === 'inbox' ? 'data-th-view' : 'data-th-sort'}="${k}" aria-selected="${k === cur}"${k === cur ? ' class="cur"' : ''}>${label}</button>`).join('')}</div>`;

const threadHref = t => THREAD_ROUTE + encodeURIComponent(typeof t === 'string' ? t : t?.id || '');
// Other pages link a thread by this (a top-level const is not a window property; the dashboard's cards look for it there).
window.threadHref = threadHref;
const threadIsDm = t => t?.audience === 'personal' || t?.audience === 'participants';
// A DM with one bot and no one else: its bot's page holds it, so lists leave it to the Team list's count.
const threadIsBotDm = t => threadIsDm(t) && !t.parent_id && Number(t.dm_with_count) === 1 && String(t.dm_with?.[0]?.actor || '').startsWith('bot:');
// Held messages, each with its review card (Inbox lists them; a held reply's thread is also an Inbox row, so only one
// that would start a thread adds to the count).
const inboxHolds = () => (S.v2?.needs || []).filter(it => it.kind === 'thread_held');
const inboxRows = () => THREADS.attention.filter(t => t && !t.archived_at);
const inboxCount = () => inboxRows().length + inboxHolds().filter(it => !it.thread_id).length;
// The count may go on past what was read: a page of rows that all wait on the viewer. (Held messages past the first
// page of Needs you are read when Inbox opens; the count does not wait for them.)
const inboxMore = () => !!THREADS.attentionMore;
const inboxCountText = n => n > 99 ? '99+' : n + (inboxMore() ? '+' : '');
const threadPrivate = t => ['members', 'personal', 'participants', 'meeting'].includes(t?.audience);
// The other people in a DM: `dm_with` on a thread view, or a members list read for it. The viewer is left out.
const threadOthers = t => (t?.dm_with || (Array.isArray(t?.members) ? t.members : t?.members?.members) || [])
  .map(m => typeof m === 'string' ? m : m?.actor).filter(a => a && a !== myActor());
const actorGroup = a => String(a || '').startsWith('group:') ? String(a).slice(6) : '';
const groupName = id => (S.orgGroups || []).find(g => g.id === id)?.name || titleCase(id);
// A teammate's name for any actor: bot, person or group.
const actorName = a => actorGroup(a) ? groupName(actorGroup(a)) : actorLabel(a);
function actorAvatar(a, size = 22) {
  const slug = actorSlug(a);
  if (slug) return avatar(slug, size);
  const pid = actorPerson(a);
  if (pid) return personAvatar((S.people || []).find(p => p.id === pid) || {id: pid, name: personDisplay(pid)}, size);
  return personCircle(actorName(a), size);
}
// A DM is named by who else is in it ("Chat with BotOps" and "Private … room" titles from before threads read as
// "BotOps"); any other thread by its title.
function threadTitle(t) {
  if (!t) return '';
  const others = threadOthers(t), total = Number(t.dm_with_count) || others.length;
  // The server's name, for someone in this DM the viewer's people list leaves out.
  const name = a => t.dm_with?.find(m => m.actor === a)?.name || actorName(a);
  const people = others.length ? others.slice(0, 3).map(name).join(', ') + (total > 3 ? ` +${total - 3}` : '') : '';
  if (threadIsDm(t) && !t.parent_id && people) return people;
  if (String(t.title || '').trim()) return String(t.title).trim();
  if (t.audience === 'home' && t.home?.actor) return actorName(t.home.actor);
  if (people) return people;
  return t.parent_id ? 'Sub-thread' : threadIsDm(t) ? 'Direct message' : 'Untitled';
}
// Where a row opens: its root, or the sub-thread holding what is new (a reply there may be pages up the root's timeline).
const threadRowHref = t => !t?.new_at?.thread_id ? threadHref(t)
  : threadHref(t.new_at.thread_id) + (t.new_at.message_id ? '?m=' + encodeURIComponent(t.new_at.message_id) : '');
window.threadRowHref = threadRowHref;
// The mark before a title: the owner's avatar for a Home, the other member's for a DM, else public or private.
function threadMarkHTML(t, size = 18) {
  if (t?.audience === 'home' && t.home?.actor) return actorAvatar(t.home.actor, size);
  const others = threadOthers(t);
  if (threadIsDm(t) && others.length) return actorAvatar(others[0], size);
  return `<span class="nav-icon th-mark" aria-hidden="true">${threadPrivate(t) ? 'lock' : 'forum'}</span>`;
}
const threadUnread = t => Number(t?.me?.unread) || 0;
const threadActivity = t => !threadUnread(t) && !!t?.me?.activity;
// A root row speaks for its sub-threads: the newest message anywhere under it.
const threadSeq = t => Number(t?.activity_seq ?? t?.last_seq) || 0;
// A row's level; one the server has not ranked (a thread that just arrived on the stream) reads as the middle.
const threadImportance = t => Number(t?.importance ?? 5);
// The server's order kept as rows change live: Important by level, then the most recent; Recent by the most recent. A
// thread already listed above is left out.
function threadsOrder(list, skip = new Set(), sort = threadsSort()) {
  return list.filter(t => t && !skip.has(t.id) && !t.archived_at)
    .sort((a, b) => (sort === 'important' ? threadImportance(b) - threadImportance(a) : 0) || threadSeq(b) - threadSeq(a));
}
function threadRowHTML(t, {attention = false} = {}) {
  const n = threadUnread(t), cur = S.route === threadHref(t) || S.route.startsWith(threadHref(t) + '?');
  const title = threadTitle(t);
  const tail = n ? `<span class="nav-badge th-count" aria-label="${n} unread">${n > 99 ? '99+' : n}</span>`
    : threadActivity(t) ? '<span class="th-activity" title="Activity" aria-label="Activity"></span>' : '';
  return `<li><a class="th-row${n ? ' unread' : ''}${attention ? ' attn' : ''}${cur ? ' cur' : ''}" href="${esc(threadRowHref(t))}" data-thread="${esc(t.id)}"${cur ? ' aria-current="page"' : ''}>
    ${threadMarkHTML(t)}<span class="th-row-title">${esc(title)}</span>${attention ? '<span class="th-attn" title="Needs attention" aria-label="Needs attention"></span>' : ''}${tail}</a></li>`;
}

// ----------------------------------------------------------------- loading and the sidebar
async function threadsLoad() {
  const sort = threadsSort();
  // A read still out for the other order is followed by one for this order, not taken for it.
  if (THREADS.loading) return THREADS.loadingSort === sort ? THREADS.loading : THREADS.loading.then(() => threadsLoad());
  THREADS.loadingSort = sort;
  THREADS.loading = (async () => {
    try {
      const r = await get('/v2/threads?limit=50&sort=' + sort, {fresh: true});
      if (sort !== threadsSort()) return;     // the order changed meanwhile: read again below, this answer is dropped
      // Only a real list turns Threads on; any other answer is a server without them.
      if (!Array.isArray(r?.threads)) { if (THREADS.on !== true) THREADS.on = false; return; }
      const botDms = r.bot_dms || {}, moved = JSON.stringify(botDms) !== JSON.stringify(THREADS.botDms);
      // A page of a list whose every row waits on the viewer may have more of them past it.
      const ids = new Set((r.attention || []).map(t => t.id)), full = list => list.length >= 50 && list.every(t => ids.has(t.id));
      const attentionMore = !!r.next_cursor && (full(r.threads) || full(r.dms || []));
      Object.assign(THREADS, {on: true, attention: r.attention || [], threads: r.threads, dms: r.dms || [], botDms, attentionMore});
      if (moved && typeof renderTree === 'function') renderTree();
      liveTopicAdd('threads');
    } catch (e) {
      // 404: a server without threads. Anything else keeps what was drawn and tries again on the next change.
      if (e?.status === 404 && THREADS.on !== true) THREADS.on = false;
    } finally { THREADS.loading = null; }
    threadsSideRender();
  })();
  const loading = THREADS.loading;
  return loading.then(() => sort !== threadsSort() ? threadsLoad() : undefined);
}
function threadsSideRender() {
  const on = THREADS.on === true;
  const sec = $('#nav-inbox-section'); if (sec) sec.hidden = !on;
  // On a phone Inbox takes Search's place in the bottom bar; global search is then in More (the drawer's footer).
  const tab = $('#mobile-threads'), search = $('#mobile-search');
  if (tab) tab.hidden = !on;
  if (search) search.hidden = on;
  if (!on) return;
  const rows = inboxRows(), n = inboxCount(), shown = rows.slice(0, THREADS_SIDE_MAX), holds = inboxHolds().length;
  const link = (href, label) => `<li><a class="th-row th-all${S.route === href ? ' cur' : ''}" href="${href}"><span class="th-row-title">${label}</span></a></li>`;
  const list = $('#th-list');
  // Every row waits on the viewer, so none carries the Needs attention dot.
  if (list) patchLiveHTML(list, shown.map(t => threadRowHTML(t)).join('')
    + (n > shown.length || inboxMore() ? link(INBOX_ROUTE, `All ${inboxCountText(n)}`) : '')
    // Held messages are reviewed on Inbox's page, each from its row there.
    + (holds ? link(INBOX_ROUTE, `${holds} held message${holds === 1 ? '' : 's'}`) : !n ? '<li class="th-done muted">All caught up</li>' : '')
    + link(THREADS_ROUTE, 'Threads'));
  // The header and the phone tab carry the same count: what is left to burn down.
  for (const badge of [$('#inbox-count'), $('#mobile-threads .nav-badge')]) {
    if (badge) { badge.hidden = !n; badge.textContent = inboxCountText(n); }
  }
}
// The row for one thread anywhere in the sidebar lists, updated in place from a thread view the server sent.
function threadsPut(view) {
  if (!view?.id) return;
  let found = false;
  for (const key of ['attention', 'threads', 'dms']) THREADS[key] = THREADS[key].map(t => {
    if (t.id !== view.id) return t;
    found = true;
    // A view is that thread's own numbers; what the row sums up from its sub-threads stays until the list is read again.
    return {...t, ...view, activity_seq: t.activity_seq, last_message_at: t.last_message_at || view.last_message_at,
            me: {...t.me, ...view.me, unread: t.me?.unread ?? view.me?.unread, activity: t.me?.activity ?? view.me?.activity}};
  });
  if (!found && !view.parent_id) (threadIsDm(view) ? THREADS.dms : THREADS.threads).unshift(view);
  threadsSideRender();
}
// Read up to `seq` here: the row's count clears at once, the server's answer follows on the stream.
function threadsMarkedRead(id, seq) {
  for (const key of ['attention', 'threads', 'dms']) THREADS[key] = THREADS[key].map(t => {
    if (t.id !== id || !t.me) return t;
    const me = {...t.me, last_read_seq: Math.max(Number(t.me.last_read_seq) || 0, seq)};
    // Something newer in a sub-thread keeps the row's marks: the list read after this says what is left.
    const below = Number(t.activity_seq) > (Number(t.last_seq) || 0);
    if (!below && me.last_read_seq >= (Number(t.last_unread_seq) || 0)) me.unread = 0;
    if (!below && me.last_read_seq >= (Number(t.last_seq) || 0)) me.activity = false;
    return {...t, me};
  });
  threadsSideRender();
}
// A message anywhere under a root: its row moves up now, and its counts come with the next read of the list.
function threadsActivity(d) {
  const seq = Number(d.counters?.last_seq) || 0, theirs = d.actor && d.actor !== myActor(), here = S.route === threadHref(d.root_id);
  for (const key of ['attention', 'threads', 'dms']) THREADS[key] = THREADS[key].map(t => t.id !== d.root_id ? t : {
    ...t, activity_seq: Math.max(threadSeq(t), seq), me: theirs && !here ? {...t.me, activity: true} : t.me});
  threadsSideRender();
  liveThrottle('threads-side-live', threadsLoad, 3000);
}
// Every route: the first load, the current row marked, and the stream wired once.
function threadsSync() {
  if (!S.me) return;
  if (THREADS.on === null && !THREADS.loading) void threadsLoad();
  else threadsSideRender();
  if (THREADS.wired) return;
  THREADS.wired = true;
  // A thread created, renamed, archived, read or with new messages, the viewer's Needs attention set moved
  // (`attention`) or a loop guard paused a root (`capped`): the counts are the viewer's own, so the list is read
  // again (once per burst), and each open view is told. A message another company's Tico has not taken (`unsent`) or a
  // paused root is a Needs you row with no `needs` event of its own, so that list is read whole, as if one had come.
  liveOn('threads', d => {
    if (d.kind === 'activity') return threadsActivity(d);
    if (d.kind === 'read' && d.read) threadsMarkedRead(d.thread_id, Number(d.read.last_read_seq) || 0);
    else if (d.thread && !d.thread.parent_id) threadsPut(d.thread);
    if (d.kind === 'unsent' || d.kind === 'capped') liveSoon('threads-needs', () => liveEmit('needs', {version: 'read'}), 400);
    // Not while the tab is hidden: a hidden tab reads the list once when it is shown again (liveThrottle).
    liveThrottle('threads-side', threadsLoad, 400);
    threadViewsLive('threads', d);
  });
  // A hold or an ask moved on the viewer's Needs you list: the rows it marks are ranked first.
  liveOn('needs', () => { if (THREADS.on) liveThrottle('threads-side-live', threadsLoad, 3000); });
  // Needs you read again (its holds are Inbox's): the count follows the list as it now stands.
  liveOn('needs-list', () => { if (THREADS.on) threadsSideRender(); });
  liveOn('reset', () => { liveSoon('threads-side', threadsLoad, 400); threadViewsLive('reset', {}); });
}
document.addEventListener('click', ev => {
  if (ev.target.closest('#th-new')) { ev.preventDefault(); threadNewOpen(); }
});
