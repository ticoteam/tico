/* ui/app/threads.js — The viewer's threads as the rail lists them (GET /api/v2/threads/rail: Pinned, the rest, the Catch up
   count and each 1:1 DM's unread), live from the `threads` topic of GET /api/v2/events; the sidebar's Threads link and
   its count, and the Team list's DM counts. Shared helpers for thread titles, marks and actors live here too; the rail
   and the Threads page are ui/app/thread-rail.js, the timeline ui/app/thread-view.js, Catch up ui/app/catch-up.js.
   Shapes: docs/dev/threads-contract.md §9 and §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const THREADS_ROUTE = '#/threads', THREAD_ROUTE = '#/thread/';
// `on`: null until the server answered once; false when it has no threads (an older server).
// `rail`: whether the server has GET /v2/threads/rail (pins, Catch up); false keeps the plain list (GET /v2/threads).
// `pinned`, `threads`: the rail's two lists, each by title. `catchUp`: the Catch up count from a server without
// `catch_up_ids`; `cu`: else the roots Catch up counts (a Set), patched live, and its size is the badge.
// `dms`: {actor: unread} for the viewer's 1:1 DMs, bots and people, which the Team list shows on that teammate.
// `roots`: {root id: {dm: the other actor or null, muted}} for roots not on the rail, learned once, for live counts.
// `counted`: the message ids an `activity` already counted (an edit sends one more).
const THREADS = {on: null, rail: null, pinned: [], threads: [], next: null, catchUp: 0, cu: null, dms: {}, roots: {}, counted: new Set(),
                 loading: null, again: false, wired: false, needsSeen: null, readAt: 0, gen: 0};
// A stream connect within this long of the rail's last read leaves it be (the `needs` handler in threadsSync).
const THREADS_CONNECT_FRESH_MS = 300e3;
// The Catch up count: the sidebar link, the phone tab and the rail's Catch up button all show this.
const threadsCatchUp = () => THREADS.cu ? THREADS.cu.size : THREADS.catchUp;
const THREADS_PAGE = 100;

const threadHref = t => THREAD_ROUTE + encodeURIComponent(typeof t === 'string' ? t : t?.id || '');
// Other pages link a thread by this (a top-level const is not a window property; the dashboard's cards look for it there).
window.threadHref = threadHref;
const threadIsDm = t => t?.audience === 'personal' || t?.audience === 'participants';
// A DM with one bot and no one else: its bot's page holds it, so lists leave it to the Team list's count.
const threadIsBotDm = t => threadIsDm(t) && !t.parent_id && Number(t.dm_with_count) === 1 && String(t.dm_with?.[0]?.actor || '').startsWith('bot:');
const threadIsGroupDm = t => threadIsDm(t) && !t?.parent_id && (Number(t.dm_with_count) || threadOthers(t).length) >= 2;
const threadPrivate = t => ['members', 'personal', 'participants', 'meeting'].includes(t?.audience);
// What the rail lists (and what may be pinned): a root that is a public, team or members thread, or a group DM.
const threadOnRail = t => !!t && !t.parent_id && !t.archived_at && (['company', 'team', 'members'].includes(t.audience) || threadIsGroupDm(t));
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
// "BotOps"); a group chat by the name it was given, else by its people; any other thread by its title.
function threadTitle(t) {
  if (!t) return '';
  const others = threadOthers(t), total = Number(t.dm_with_count) || others.length;
  // The server's name, for someone in this DM the viewer's people list leaves out.
  const name = a => t.dm_with?.find(m => m.actor === a)?.name || actorName(a);
  const people = others.length ? others.slice(0, 3).map(name).join(', ') + (total > 3 ? ` +${total - 3}` : '') : '';
  const own = String(t.title || '').trim();
  if (threadIsDm(t) && !t.parent_id && people && !(total >= 2 && own && !/^(chat with|private\b)/i.test(own))) return people;
  if (own) return own;
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
const threadsCountText = n => n > 99 ? '99+' : String(n);
// The server's order (contract §10.1): by title, case-insensitively, unnamed last, then id.
const threadsByTitle = (a, b) => (!a.title - !b.title) || String(a.title || '').localeCompare(String(b.title || ''), undefined, {sensitivity: 'base'})
  || String(a.id).localeCompare(String(b.id));

// ----------------------------------------------------------------- loading
// The rail when the server has it; else the plain list, its threads and group DMs by title, with no pins or Catch up.
async function threadsRead() {
  const limit = Math.min(200, Math.max(THREADS_PAGE, THREADS.threads.length));
  if (THREADS.rail !== false) {
    try {
      const r = await get('/v2/threads/rail?limit=' + limit, {fresh: true});
      if (Array.isArray(r?.threads)) {
        return {rail: true, pinned: r.pinned || [], threads: r.threads, next: r.next_cursor || null,
                catchUp: Number(r.catch_up) || 0, cu: Array.isArray(r.catch_up_ids) ? new Set(r.catch_up_ids) : null, dms: r.dms || {}};
      }
    } catch (e) { if (e?.status !== 404) throw e; }
  }
  const r = await get('/v2/threads?limit=50', {fresh: true});
  if (!Array.isArray(r?.threads)) return null;
  const dms = {...(r.person_dms || {}), ...(r.bot_dms || {})};
  // An older server sends no person_dms: each 1:1 DM with a person in the list speaks for itself.
  for (const t of r.dms || []) {
    const [other] = threadOthers(t);
    if (!t.parent_id && Number(t.dm_with_count) === 1 && String(other || '').startsWith('human:') && !(other in dms)) dms[other] = threadUnread(t);
  }
  const threads = [...r.threads, ...(r.dms || [])].filter(threadOnRail).sort(threadsByTitle);
  return {rail: false, pinned: [], threads, next: null, catchUp: 0, cu: null, dms};
}
async function threadsLoad() {
  // A change while a read is out reads once more after it: that read may have left before the change.
  if (THREADS.loading) { THREADS.again = true; return THREADS.loading; }
  // The Needs you version known before the read: the rail it answers is at least that new (the `needs` handler below).
  // Held only once the read lands, so a read that fails is tried again on the next connect.
  // A change that comes while it is on its way (`gen`) may not be in it, so that read vouches for nothing.
  const seen = S.v2.needsVersion || null, gen = THREADS.gen;
  THREADS.needsSeen = null;
  THREADS.loading = (async () => {
    try {
      const r = await threadsRead();
      // Only a real list turns Threads on; any other answer is a server without them.
      if (!r) { if (THREADS.on !== true) THREADS.on = false; return; }
      const moved = JSON.stringify(r.dms) !== JSON.stringify(THREADS.dms);
      Object.assign(THREADS, {on: true, ...r, needsSeen: gen === THREADS.gen ? seen : null, readAt: Date.now()});
      if (moved && typeof renderTree === 'function') renderTree();
      liveTopicAdd('threads');
    } catch (e) {
      // 404: a server without threads. Anything else keeps what was drawn and tries again on the next change.
      if (e?.status === 404 && THREADS.on !== true) THREADS.on = false;
    } finally { THREADS.loading = null; }
    threadsSideRender();
  })();
  const loading = THREADS.loading;
  await loading;
  if (THREADS.again) { THREADS.again = false; return threadsLoad(); }
}
// The rest of the Threads list, a page at a time (the rail's More).
async function threadsMore() {
  if (!THREADS.next || !THREADS.rail) return;
  const r = await get('/v2/threads/rail?limit=' + THREADS_PAGE + '&cursor=' + encodeURIComponent(THREADS.next), {fresh: true});
  // A later page may hold pins too (past the first page's share): they go on Pinned.
  const have = new Set([...THREADS.pinned, ...THREADS.threads].map(t => t.id));
  THREADS.pinned = [...THREADS.pinned, ...(r?.pinned || []).filter(t => !have.has(t.id))];
  THREADS.threads = [...THREADS.threads, ...(r?.threads || []).filter(t => !have.has(t.id))];
  THREADS.next = r?.next_cursor || null;
  threadsSideRender();
}
// Once the first read is in (the Threads page waits for it to choose a thread).
async function threadsReady() {
  if (THREADS.on === null) await (THREADS.loading || threadsLoad());
}

// ----------------------------------------------------------------- the sidebar
function threadsSideRender() {
  const on = THREADS.on === true;
  const link = $('#nav-threads'); if (link) link.hidden = !on;
  // On a phone Threads takes Search's place in the bottom bar; global search is then in More (the drawer's footer).
  const tab = $('#mobile-threads'), search = $('#mobile-search');
  if (tab) tab.hidden = !on;
  if (search) search.hidden = on;
  if (!on) return;
  // The link and the phone tab carry the same count: what Catch up has left.
  const n = threadsCatchUp();
  for (const badge of [$('#nav-threads .nav-badge'), $('#mobile-threads .nav-badge')]) {
    if (badge) { badge.hidden = !n; badge.textContent = threadsCountText(n); badge.setAttribute('aria-label', `${n} to catch up`); }
  }
  if (typeof railRender === 'function') railRender();
}
// The unread in the viewer's 1:1 DM with this teammate (THREADS.dms), for the Team list.
const threadsDmUnread = actor => Number(THREADS.dms?.[actor]) || 0;

// ----------------------------------------------------------------- live rows
const threadsEach = fn => { for (const key of ['pinned', 'threads']) THREADS[key] = THREADS[key].map(t => fn(t) || t); };
// One thread's row, updated in place from a thread view the server sent: archived leaves the rail, a pin moves it to
// Pinned (or back), a new one the viewer is in joins its list by title.
// `recheck` (a members event): whether the viewer is still in it decides, not whether it is listed.
function threadsPut(view, {recheck = false} = {}) {
  if (!view?.id || view.parent_id) return;
  let row = null;
  threadsEach(t => {
    if (t.id !== view.id) return;
    // A view is that thread's own numbers; what the row sums up from its sub-threads stays until the list is read again.
    row = {...t, ...view, last_message_at: t.last_message_at || view.last_message_at,
           me: {...t.me, ...view.me, unread: t.me?.unread ?? view.me?.unread, activity: t.me?.activity ?? view.me?.activity}};
    return row;
  });
  const me = (row || view).me || {};
  const listed = !!row, wanted = threadOnRail(view) && ((listed && !recheck) || me.member || me.following || me.pinned);
  if (!wanted) {
    if (listed) { THREADS.pinned = THREADS.pinned.filter(t => t.id !== view.id); THREADS.threads = THREADS.threads.filter(t => t.id !== view.id); }
  } else if (THREADS.rail && 'pinned' in me) {
    // Where it belongs now.
    const t = row || view, into = me.pinned ? 'pinned' : 'threads', out = me.pinned ? 'threads' : 'pinned';
    THREADS[out] = THREADS[out].filter(x => x.id !== t.id);
    if (!THREADS[into].some(x => x.id === t.id)) THREADS[into] = [...THREADS[into], t].sort(threadsByTitle);
  } else if (!listed) THREADS.threads = [...THREADS.threads, view].sort(threadsByTitle);
  threadsSideRender();
}
// Read up to `seq` here: the row's count clears at once, the server's answer follows on the stream.
function threadsMarkedRead(id, seq) {
  threadsEach(t => {
    if (t.id !== id || !t.me) return;
    const me = {...t.me, last_read_seq: Math.max(Number(t.me.last_read_seq) || 0, seq)};
    // Something newer in a sub-thread keeps the row's marks: the list read after this says what is left.
    const below = Number(t.activity_seq) > (Number(t.last_seq) || 0);
    if (!below && me.last_read_seq >= (Number(t.last_unread_seq) || 0)) me.unread = 0;
    if (!below && me.last_read_seq >= (Number(t.last_seq) || 0)) me.activity = false;
    return {...t, me};
  });
  threadsSideRender();
}
// Read through, the whole subtree (Mark read, Catch up, the viewer's own `read` event): the row is quiet, Catch up
// lets the root go, and a 1:1 DM's count on its teammate clears.
function threadsAllRead(id) {
  threadsEach(t => t.id === id ? {...t, me: {...t.me, unread: 0, activity: false}} : null);
  THREADS.cu?.delete(id);
  const dm = THREADS.roots[id]?.dm;
  if (dm && Number(THREADS.dms[dm])) { THREADS.dms = {...THREADS.dms, [dm]: 0}; if (typeof renderTree === 'function') renderTree(); }
  threadsSideRender();
}
const threadsRow = id => [...THREADS.pinned, ...THREADS.threads].find(t => t.id === id) || null;
// What a root not on the rail is, read alone once (GET /v2/threads/{id}): a 1:1 DM counts on its teammate.
function threadsRootLearn(view) {
  if (!view?.id || view.parent_id) return null;
  const others = threadOthers(view);
  const dm = threadIsDm(view) && !threadIsGroupDm(view) && others.length === 1 ? others[0] : null;
  // Another's Home is in Catch up only through a mention, an ask or a hold, which `needs` brings.
  const theirHome = view.audience === 'home' && view.home?.actor !== myActor();
  return (THREADS.roots[view.id] = {dm, muted: view.me?.notify === 'muted' || theirHome, following: !!(view.me?.following || view.me?.member)});
}
async function threadsRoot(id) {
  if (THREADS.roots[id]) return THREADS.roots[id];
  const t = await get(thvPath(id), {fresh: true}).catch(() => null);
  return threadsRootLearn(t) || (THREADS.roots[id] = {dm: null, muted: false, following: false});
}
// A message anywhere under a root (`activity`). Unread for this viewer and not in the open thread: its row counts one
// more and Catch up takes the root; the open thread reads it itself. Otherwise someone else's is activity on its row.
async function threadsActivity(d) {
  const root = d.root_id || d.thread_id, here = S.route === threadHref(root);
  if (!root || here) { threadsSideRender(); return; }
  const fresh = d.unread === true && !(d.message_id && THREADS.counted.has(d.message_id));
  // Several messages in one batch come as one event: `unread_count` of them are unread (1 from an older server).
  const more = fresh ? Math.max(1, Number(d.unread_count) || 1) : 0;
  if (d.message_id) { if (THREADS.counted.size > 2000) THREADS.counted.clear(); THREADS.counted.add(d.message_id); }
  const row = threadsRow(root);
  if (row) {
    const theirs = d.unread === true || (d.actor && d.actor !== myActor());
    threadsEach(t => {
      if (t.id !== root) return;
      const me = {...t.me, activity: theirs || !!t.me?.activity, unread: threadUnread(t) + more};
      const counters = d.thread_id === root && d.counters ? {last_seq: d.counters.last_seq, last_unread_seq: d.counters.last_unread_seq} : {};
      return {...t, ...counters, activity_seq: Math.max(Number(t.activity_seq) || 0, Number(d.counters?.last_seq) || 0), me};
    });
    if (fresh && row.me?.notify !== 'muted') THREADS.cu?.add(root);
  } else if (fresh) {
    // Not on the rail: a DM, a task's thread or a Home. Catch up counts it; a 1:1 DM counts on its teammate too.
    const info = await threadsRoot(root);
    if (S.route === threadHref(root)) return;
    if (!info.muted) THREADS.cu?.add(root);
    if (info.dm && info.dm in THREADS.dms) {
      THREADS.dms = {...THREADS.dms, [info.dm]: threadsDmUnread(info.dm) + more};
      if (typeof renderTree === 'function') renderTree();
    }
  }
  threadsSideRender();
}
// A pin moved (the viewer's own, or a manager's for everyone; `pinned` is this viewer's state): the row moves at once,
// and a thread not on the rail yet is read alone.
async function threadsPinned(d) {
  const t = threadsRow(d.thread_id);
  if (t) { threadsPut({...t, pinned_for_everyone: d.pinned_for_everyone ?? t.pinned_for_everyone, me: {...t.me, pinned: !!d.pinned}}); return; }
  if (!d.pinned) return;
  const view = await get(thvPath(d.thread_id), {fresh: true}).catch(() => null);
  if (view?.id) threadsPut({...view, me: {...view.me, pinned: true}});
}
// The whole rail is read only on the first load, a `reset`, a `needs` change and the safety poll; every other change is
// patched from its event (above). Once per burst.
const threadsRecount = () => liveThrottle('threads-rail', threadsLoad, 3000);
// Every route: the first load, the current row marked, and the stream wired once.
function threadsSync() {
  if (!S.me) return;
  if (THREADS.on === null && !THREADS.loading) void threadsLoad();
  else threadsSideRender();
  if (THREADS.wired) return;
  THREADS.wired = true;
  liveOn('threads', d => {
    THREADS.gen++;
    if (d.kind === 'activity') void threadsActivity(d);
    // The viewer's own read (it comes only to them): the root's row and its Catch up place clear; a card for it in an
    // open Catch up goes.
    else if (d.kind === 'read' && d.read) {
      const root = d.root_id || d.thread_id;
      threadsAllRead(root);
      if (typeof catchUpGone === 'function') catchUpGone(root);
    }
    else if (d.kind === 'pinned' && d.thread_id) void threadsPinned(d);
    else if (d.thread && !d.thread.parent_id) {
      // A 1:1 DM is not on the rail: it is learned for its teammate's count; a new one starts that count.
      if (threadIsDm(d.thread) && !threadIsGroupDm(d.thread)) {
        const info = threadsRootLearn(d.thread);
        if (d.kind === 'created' && info?.dm && info.following && !info.muted && !(info.dm in THREADS.dms)) {
          THREADS.dms = {...THREADS.dms, [info.dm]: threadUnread(d.thread)};
          if (typeof renderTree === 'function') renderTree();
        }
      } else threadsPut(d.thread, {recheck: d.kind === 'members'});
    }
    // A paused root is a Needs you row with no `needs` event of its own, so that list is read whole, as if one had come.
    if (d.kind === 'capped') liveSoon('threads-needs', () => liveEmit('needs', {version: 'read'}), 400);
    threadViewsLive('threads', d);
  });
  // An ask or a held message moved on the viewer's Needs you list: Catch up holds those too. A reconnect that carried
  // `threads` over (it replays the thread changes it missed) naming the Needs you version the rail was last read at,
  // within 5 minutes of that read, moved nothing, so the rail is not read again for it. Any other connect reads it: a
  // change no event names (a thread the viewer can no longer see) is caught by one at least that often.
  liveOn('needs', d => {
    const fresh = Date.now() - THREADS.readAt < THREADS_CONNECT_FRESH_MS;
    if (!THREADS.on || fresh && d?.carried?.includes('threads') && d.version === THREADS.needsSeen) return;
    if (!d?.connect) THREADS.gen++;
    threadsRecount();
  });
  liveOn('reset', () => { liveSoon('threads-rail', threadsLoad, 400); threadViewsLive('reset', {}); });
  // A safety read every few minutes, for a change the stream never carried.
  setInterval(() => { if (THREADS.on && !document.hidden) threadsRecount(); }, 180000);
}
