/* ui/app/threads.js — Threads in the sidebar: Threads (Needs attention, then unread, then recent) and DMs, live from
   the `threads` topic of GET /api/v2/events. Shared helpers for thread titles, marks and actors live here too; the
   timeline is ui/app/thread-view.js and the Threads page, new thread and Make task are ui/app/threads-page.js.
   Shapes: docs/dev/threads-contract.md §9 and §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const THREADS_ROUTE = '#/threads', THREAD_ROUTE = '#/thread/';
// About ten rows each, then All: the sidebar is a way in, the Threads page is the list.
const THREADS_SIDE_MAX = 10;
// `on`: null until the server answered GET /v2/threads once; false when it has no threads (an older server).
const THREADS = {on: null, attention: [], threads: [], dms: [], loading: null, wired: false};

const threadHref = t => THREAD_ROUTE + encodeURIComponent(typeof t === 'string' ? t : t?.id || '');
// Other pages link a thread by this (a top-level const is not a window property; the dashboard's cards look for it there).
window.threadHref = threadHref;
const threadIsDm = t => t?.audience === 'personal' || t?.audience === 'participants';
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
function threadTitle(t) {
  if (!t) return '';
  if (String(t.title || '').trim()) return String(t.title).trim();
  if (t.audience === 'home' && t.home?.actor) return actorName(t.home.actor);
  const others = threadOthers(t), total = Number(t.dm_with_count) || others.length;
  // The server's name, for someone in this DM the viewer's people list leaves out.
  const name = a => t.dm_with?.find(m => m.actor === a)?.name || actorName(a);
  if (others.length) return others.slice(0, 3).map(name).join(', ') + (total > 3 ? ` +${total - 3}` : '');
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
// Unread first, then the most recent; a thread already listed above is left out.
function threadsOrder(list, skip = new Set()) {
  return list.filter(t => t && !skip.has(t.id) && !t.archived_at)
    .sort((a, b) => (threadUnread(b) > 0) - (threadUnread(a) > 0) || threadSeq(b) - threadSeq(a));
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
  if (THREADS.loading) return THREADS.loading;
  THREADS.loading = (async () => {
    try {
      const r = await get('/v2/threads?limit=50', {fresh: true});
      // Only a real list turns Threads on; any other answer is a server without them.
      if (!Array.isArray(r?.threads)) { if (THREADS.on !== true) THREADS.on = false; return; }
      Object.assign(THREADS, {on: true, attention: r.attention || [], threads: r.threads, dms: r.dms || []});
      liveTopicAdd('threads');
    } catch (e) {
      // 404: a server without threads. Anything else keeps what was drawn and tries again on the next change.
      if (e?.status === 404 && THREADS.on !== true) THREADS.on = false;
    } finally { THREADS.loading = null; }
    threadsSideRender();
  })();
  return THREADS.loading;
}
function threadsSideRender() {
  const on = THREADS.on === true;
  for (const id of ['#nav-threads-section', '#nav-dms-section']) { const el = $(id); if (el) el.hidden = !on; }
  // On a phone Threads takes Search's place in the bottom bar; global search is then in More (the drawer's footer).
  const tab = $('#mobile-threads'), search = $('#mobile-search');
  if (tab) tab.hidden = !on;
  if (search) search.hidden = on;
  if (!on) return;
  const attn = THREADS.attention.filter(t => t && !t.archived_at).slice(0, THREADS_SIDE_MAX);
  const seen = new Set(attn.map(t => t.id));
  const rest = threadsOrder(THREADS.threads.filter(t => !threadIsDm(t)), seen).slice(0, Math.max(0, THREADS_SIDE_MAX - attn.length));
  const total = THREADS.threads.filter(t => !threadIsDm(t)).length + attn.filter(t => !THREADS.threads.some(x => x.id === t.id)).length;
  const list = $('#th-list');
  if (list) patchLiveHTML(list, attn.map(t => threadRowHTML(t, {attention: true})).join('') + rest.map(t => threadRowHTML(t)).join('')
    + (total > attn.length + rest.length || !(attn.length + rest.length)
      ? `<li><a class="th-row th-all${S.route === THREADS_ROUTE ? ' cur' : ''}" href="${THREADS_ROUTE}"><span class="th-row-title">${attn.length + rest.length ? 'All' : 'No threads yet'}</span></a></li>` : ''));
  const dms = threadsOrder(THREADS.dms).slice(0, THREADS_SIDE_MAX);
  const dmList = $('#dm-list');
  if (dmList) patchLiveHTML(dmList, dms.map(t => threadRowHTML(t)).join('')
    + (THREADS.dms.length > dms.length ? `<li><a class="th-row th-all" href="${THREADS_ROUTE}?view=dms"><span class="th-row-title">All</span></a></li>` : ''));
  // The mobile tab carries the unread total, the same count the rows show.
  const unread = [...THREADS.attention, ...THREADS.threads, ...THREADS.dms].reduce((n, t, i, all) =>
    all.findIndex(x => x.id === t.id) === i ? n + threadUnread(t) : n, 0);
  const badge = $('#mobile-threads .nav-badge');
  if (badge) { badge.hidden = !unread; badge.textContent = unread > 99 ? '99+' : String(unread); }
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
  // again (once per burst), and each open view is told.
  liveOn('threads', d => {
    if (d.kind === 'activity') return threadsActivity(d);
    if (d.kind === 'read' && d.read) threadsMarkedRead(d.thread_id, Number(d.read.last_read_seq) || 0);
    else if (d.thread && !d.thread.parent_id) threadsPut(d.thread);
    liveSoon('threads-side', threadsLoad, 400);
    threadViewsLive('threads', d);
  });
  // A hold or an ask moved on the viewer's Needs you list: the rows it marks are ranked first.
  liveOn('needs', () => { if (THREADS.on) liveThrottle('threads-side-live', threadsLoad, 3000); });
  liveOn('reset', () => { liveSoon('threads-side', threadsLoad, 400); threadViewsLive('reset', {}); });
}
document.addEventListener('click', ev => {
  const add = ev.target.closest('#th-new, #dm-new');
  if (add) { ev.preventDefault(); threadNewOpen({dm: add.id === 'dm-new'}); }
});
