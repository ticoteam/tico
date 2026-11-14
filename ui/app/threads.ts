/* ui/app/threads.ts — The viewer's threads as the rail lists them (GET /api/v2/threads/rail: Pinned, the rest, the Catch up
   count and each 1:1 DM's unread), live from the `threads` topic of GET /api/v2/events; the sidebar's Threads link and
   its count, and the Team list's DM counts. Shared helpers for thread titles, marks and actors live here too; the rail
   and the Threads page are ui/app/thread-rail.ts, the timeline ui/app/thread-view.ts, Catch up ui/app/catch-up.ts.
   Shapes: docs/dev/threads-contract.md §9 and §10.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A person in a DM or a members list: the actor id, and the name the server sent for them when it did.
interface ThreadMember {
  actor: string;
  name?: string | null | undefined;
}
// Someone in a thread's members list: an actor id, or a member object.
type ThreadPeople = (ThreadMember | string)[];
// The viewer's own marks on a thread row (the server's `me`).
interface ThreadMe {
  unread?: number | undefined;
  activity?: boolean | undefined;
  last_read_seq?: number | undefined;
  member?: boolean | undefined;
  following?: boolean | undefined;
  pinned?: boolean | undefined;
  notify?: string | undefined;
  [key: string]: unknown;
}
// A thread as the rail, the Threads page and a live event hold it: the server's thread view with the fields a list row reads.
interface ThreadRow {
  id: string;
  title?: string | null | undefined;
  name?: string | null | undefined;   // the server's name for an untitled thread (backend _thread_name)
  audience: string;
  parent_id?: string | null | undefined;
  archived_at?: string | null | undefined;
  home?: { actor?: string | undefined } | null | undefined;
  dm_with?: ThreadMember[] | null | undefined;
  dm_with_count?: number | string | null | undefined;
  members?: ThreadPeople | { members?: ThreadPeople } | null | undefined;
  me?: ThreadMe | null | undefined;
  new_at?: { thread_id?: string | undefined; message_id?: string | undefined } | null | undefined;
  last_seq?: number | undefined;
  last_unread_seq?: number | undefined;
  activity_seq?: number | undefined;
  last_message_at?: string | null | undefined;
  pinned_for_everyone?: boolean | undefined;
  // The thread's task, and on a single thread view whether the task hides the thread (finished: off the list and out of
  // Catch up, as the server counts it).
  task_id?: string | null | undefined;
  task_hidden?: boolean | undefined;
  // The thread's live call, for the rail's green phone (ui/app/thread-call.ts keeps it live).
  live_call?: { id: string; live_count?: number | undefined } | null | undefined;
  [key: string]: unknown;
}
// A chat Catch up counts that the rail does not list (a 1:1 DM, a Home, a task's thread): the rail's Unread row for it,
// in Catch up's order (mentions first, then newest). Contract §10.1, `unread`.
interface ThreadUnread {
  id: string;
  title?: string | null | undefined;
  name?: string | null | undefined;
  audience: string;
  dm_with?: ThreadMember[] | null | undefined;
  dm_with_count?: number | null | undefined;
  home_actor?: string | null | undefined;
  new_count: number;
  mention: boolean;
  task_id?: string | null | undefined;
  last_message_at?: string | null | undefined;
}
// The rail's reply: Pinned and the rest, the next page's cursor, the Catch up count, its chats off the rail and the unread
// of each DM.
interface ThreadRailReply {
  threads?: ThreadRow[];
  pinned?: ThreadRow[];
  next_cursor?: string | null;
  catch_up?: number | string;
  catch_up_ids?: string[];
  unread?: ThreadUnread[];
  dms?: Record<string, number>;
  /** The change number the first page is as of: a live stream resuming from it misses nothing since. */
  seq?: number;
}
// The plain list's reply, from a server without the rail.
interface ThreadListReply {
  threads?: ThreadRow[];
  dms?: ThreadRow[];
  person_dms?: Record<string, number>;
  bot_dms?: Record<string, number>;
}
// What a read of the rail (or the plain list) brings: the lists and the counts the sidebar draws.
interface ThreadsLoaded {
  rail: boolean;
  pinned: ThreadRow[];
  threads: ThreadRow[];
  next: string | null;
  catchUp: number;
  cu: Set<string> | null;
  unread: ThreadUnread[];
  dms: Record<string, number>;
  seq?: number | null;
}
// A root that is not on the rail, learned once for live counts: its 1:1 DM's teammate, and whether it is muted.
interface ThreadRoot {
  dm: string | null | undefined;
  muted: boolean;
  following: boolean;
  // Whether it belongs to a task: its task's state (a close, a post after it) decides whether it is listed.
  task?: boolean | undefined;
  // Another's Home: only a reply to the viewer or a mention there counts, which the server decides (the rail's read).
  home?: boolean | undefined;
}
// The threads topic's events (GET /api/v2/events) and the needs ones read here: one kind, with the fields that kind sends.
interface ThreadsEvent {
  kind?: string | undefined;
  thread_id?: string | undefined;
  root_id?: string | undefined;
  message_id?: string | undefined;
  actor?: string | undefined;
  unread?: boolean | undefined;
  unread_count?: number | undefined;
  /** The viewer's own read: the thread's read mark now. */
  read?: { last_read_seq?: number | undefined } | undefined;
  pinned?: boolean | undefined;
  pinned_for_everyone?: boolean | undefined;
  counters?: { last_seq?: number | undefined; last_unread_seq?: number | undefined } | undefined;
  thread?: ThreadRow | undefined;
  /** The viewer can no longer read the thread (taken off its members): its id alone, and its row leaves. */
  gone?: boolean | undefined;
}
// The needs topic's event: the Needs you version, and what the stream carried over a reconnect.
interface NeedsEvent {
  version?: string | null | undefined;
  connect?: boolean | undefined;
  carried?: string[] | undefined;
}
// `on`: null until the server answered once; false when it has no threads (an older server).
// `rail`: whether the server has GET /v2/threads/rail (pins, Catch up); false keeps the plain list (GET /v2/threads).
// `pinned`, `threads`: the rail's two lists, each by title. `catchUp`: the Catch up count from a server without
// `catch_up_ids`; `cu`: else the roots Catch up counts (a Set), patched live, and its size is the badge.
// `unread`: Catch up's chats the rail does not list (none from a server without them), each shown while `cu` holds it.
// `dms`: {actor: unread} for the viewer's 1:1 DMs, bots and people, which the Team list shows on that teammate.
// `roots`: {root id: {dm: the other actor or null, muted}} for roots not on the rail, learned once, for live counts.
// `counted`: the message ids an `activity` already counted (an edit sends one more).
interface ThreadsState {
  on: boolean | null;
  rail: boolean | null;
  pinned: ThreadRow[];
  threads: ThreadRow[];
  next: string | null;
  catchUp: number;
  cu: Set<string> | null;
  unread: ThreadUnread[];
  dms: Record<string, number>;
  roots: Record<string, ThreadRoot>;
  counted: Set<string>;
  loading: Promise<void> | null;
  again: boolean;
  wired: boolean;
  needsSeen: string | null;
  needsFirst: boolean;
  readAt: number;
  gen: number;
}

// New message (ui/app/new-message.ts) is a page of its own beside the rail.
const THREADS_ROUTE = '#/threads', THREAD_ROUTE = '#/thread/', THREADS_NEW = '#/threads/new';
const THREADS: ThreadsState = {on: null, rail: null, pinned: [], threads: [], next: null, catchUp: 0, cu: null, unread: [], dms: {}, roots: {}, counted: new Set(),
                 loading: null, again: false, wired: false, needsSeen: null, needsFirst: false, readAt: 0, gen: 0};
// A stream connect within this long of the rail's last read leaves it be (the `needs` handler in threadsSync).
const THREADS_CONNECT_FRESH_MS = 300e3;
// The Catch up count: the sidebar link, the phone tab and the rail's Catch up button all show this.
const threadsCatchUp = () => THREADS.cu ? THREADS.cu.size : THREADS.catchUp;
const THREADS_PAGE = 100;

const threadHref = (t: string | ThreadRow | null | undefined) => THREAD_ROUTE + encodeURIComponent(typeof t === 'string' ? t : t?.id || '');
// Other pages link a thread by this (a top-level const is not a window property; the dashboard's cards look for it there).
window.threadHref = threadHref;
const threadIsDm = (t: ThreadRow | null | undefined): t is ThreadRow => t?.audience === 'personal' || t?.audience === 'participants';
// A DM with one bot and no one else: its bot's page holds it, so lists leave it to the Team list's count.
const threadIsBotDm = (t: ThreadRow) => threadIsDm(t) && !t.parent_id && Number(t.dm_with_count) === 1 && String(t.dm_with?.[0]?.actor || '').startsWith('bot:');
const threadIsGroupDm = (t: ThreadRow | null | undefined) => threadIsDm(t) && !t?.parent_id && (Number(t.dm_with_count) || threadOthers(t).length) >= 2;
const threadPrivate = (t: ThreadRow | null | undefined) => ['members', 'personal', 'participants', 'meeting'].includes(t?.audience ?? '');
// What the rail lists (and what may be pinned): a root that is a public, team or members thread, or a group DM.
const threadOnRail = (t: ThreadRow | null | undefined) => !!t && !t.parent_id && !t.archived_at && !t.task_hidden && (['company', 'team', 'members'].includes(t.audience) || threadIsGroupDm(t));
// The other people in a DM: `dm_with` on a thread view, or a members list read for it. The viewer is left out.
const threadOthers = (t: ThreadRow | null | undefined): (string | undefined)[] => (t?.dm_with || (Array.isArray(t?.members) ? t.members : t?.members?.members) || [])
  .map(m => typeof m === 'string' ? m : m?.actor).filter(a => a && a !== myActor());
const actorGroup = (a: string | null | undefined) => String(a || '').startsWith('group:') ? String(a).slice(6) : '';
const groupName = (id: string) => (S.orgGroups || []).find(g => g.id === id)?.name || titleCase(id);
// A teammate's name for any actor: bot, person or group.
const actorName = (a: string | null | undefined) => actorGroup(a) ? groupName(actorGroup(a)) : actorLabel(a);
function actorAvatar(a: string | null | undefined, size = 22) {
  const slug = actorSlug(a);
  if (slug) return avatar(slug, size);
  const pid = actorPerson(a);
  if (pid) return personAvatar((S.people || []).find(p => p.id === pid) || {id: pid, name: personDisplay(pid)}, size);
  return personCircle(actorName(a), size);
}
// The one name a thread shows. A DM is named by who else is in it ("Chat with BotOps" and "Private … room" titles from
// before threads read as "BotOps"). Any other thread is its title, else the `name` the server gives it (the people in
// it, viewer left out, "You" when alone: backend `_thread_name`), so every surface agrees. Only a server with no `name`
// gets the people from the thread's members here. Nothing is stored.
// `messages`: what is loaded of it, which names a DM the server sends with no title and no people (a notice thread).
function threadTitle(t: ThreadRow | null | undefined, messages?: { sender_id?: string | null | undefined }[] | null): string {
  if (!t) return '';
  const others = threadOthers(t), total = Number(t.dm_with_count) || others.length;
  // The server's name, for someone in this DM the viewer's people list leaves out.
  const name = (a: string | undefined) => t.dm_with?.find(m => m.actor === a)?.name || actorName(a);
  const people = others.length ? others.slice(0, 3).map(name).join(', ') + (total > 3 ? ` +${total - 3}` : '') : '';
  const own = String(t.title || '').trim(), named = String(t.name || '');
  if (threadIsDm(t) && !t.parent_id && people && !(total >= 2 && own && !/^(chat with|private\b)/i.test(own))) return people;
  if (own) return own;
  if (t.audience === 'home' && t.home?.actor) return actorName(t.home.actor);
  if (named && !threadIsDm(t)) return named;
  if (people) return people;
  if (threadIsDm(t) && !t.parent_id) {
    const authors = [...new Set((messages || []).map(m => m?.sender_id).filter(a => a && a !== myActor()))];
    if (authors.length) return authors.slice(0, 3).map(actorName).join(', ') + (authors.length > 3 ? ` +${authors.length - 3}` : '');
  }
  return named || 'Thread';
}
// Where a row opens: its root, or the sub-thread holding what is new (a reply there may be pages up the root's timeline).
const threadRowHref = (t: ThreadRow | null | undefined) => !t?.new_at?.thread_id ? threadHref(t)
  : threadHref(t.new_at.thread_id) + (t.new_at.message_id ? '?m=' + encodeURIComponent(t.new_at.message_id) : '');
window.threadRowHref = threadRowHref;
// The mark before a title: the owner's avatar for a Home, the other member's for a DM, else public or private.
function threadMarkHTML(t: ThreadRow | null | undefined, size = 18) {
  if (t?.audience === 'home' && t.home?.actor) return actorAvatar(t.home.actor, size);
  const others = threadOthers(t);
  if (threadIsDm(t) && others.length) return actorAvatar(others[0], size);
  return `<span class="nav-icon th-mark" aria-hidden="true">${threadPrivate(t) ? 'lock' : 'forum'}</span>`;
}
const threadUnread = (t: ThreadRow | null | undefined) => Number(t?.me?.unread) || 0;
const threadActivity = (t: ThreadRow | null | undefined) => !threadUnread(t) && !!t?.me?.activity;
const threadsCountText = (n: number) => n > 99 ? '99+' : String(n);
// The server's order (contract §10.1): by title, case-insensitively, unnamed last, then id.
const threadsByTitle = (a: ThreadRow, b: ThreadRow) => (Number(!a.title) - Number(!b.title)) || String(a.title || '').localeCompare(String(b.title || ''), undefined, {sensitivity: 'base'})
  || String(a.id).localeCompare(String(b.id));

// ----------------------------------------------------------------- loading
// The rail when the server has it; else the plain list, its threads and group DMs by title, with no pins or Catch up.
async function threadsRead(): Promise<ThreadsLoaded | null> {
  const limit = Math.min(200, Math.max(THREADS_PAGE, THREADS.threads.length));
  if (THREADS.rail !== false) {
    try {
      const r = await get<ThreadRailReply>('/v2/threads/rail?limit=' + limit, {fresh: true});
      if (Array.isArray(r?.threads)) {
        return {rail: true, pinned: r.pinned || [], threads: r.threads, next: r.next_cursor || null,
                catchUp: Number(r.catch_up) || 0, cu: Array.isArray(r.catch_up_ids) ? new Set(r.catch_up_ids) : null,
                unread: Array.isArray(r.unread) ? r.unread : [], dms: r.dms || {},
                seq: typeof r.seq === 'number' ? r.seq : null};
      }
    } catch (e) { if (errorStatus(e) !== 404) throw e; }
  }
  const r = await get<ThreadListReply>('/v2/threads?limit=50', {fresh: true});
  if (!Array.isArray(r?.threads)) return null;
  const dms: Record<string, number> = {...(r.person_dms || {}), ...(r.bot_dms || {})};
  // An older server sends no person_dms: each 1:1 DM with a person in the list speaks for itself.
  for (const t of r.dms || []) {
    const [other] = threadOthers(t);
    if (!t.parent_id && Number(t.dm_with_count) === 1 && typeof other === 'string' && other.startsWith('human:') && !(other in dms)) dms[other] = threadUnread(t);
  }
  const threads = [...r.threads, ...(r.dms || [])].filter(threadOnRail).sort(threadsByTitle);
  return {rail: false, pinned: [], threads, next: null, catchUp: 0, cu: null, unread: [], dms};
}
async function threadsLoad(): Promise<void> {
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
      const {seq, ...lists} = r;
      const moved = JSON.stringify(r.dms) !== JSON.stringify(THREADS.dms);
      // The page's first read goes out before Needs you is read (seen is null): the Needs you read sent right after it
      // stands for its version (the `needs` handler below).
      const vouch = gen === THREADS.gen;
      Object.assign(THREADS, {on: true, ...lists, needsSeen: vouch ? seen : null, needsFirst: vouch && !seen, readAt: Date.now()});
      // An open Catch up follows what this read counts, an empty one too.
      if (typeof cuReconcile === 'function') cuReconcile();
      if (moved && typeof renderTree === 'function') renderTree();
      liveTopicAdd('threads', seq);     // the first stream resumes from this read, so it need not read the rail again
    } catch (e) {
      // 404: a server without threads. Anything else keeps what was drawn and tries again on the next change.
      if (errorStatus(e) === 404 && THREADS.on !== true) THREADS.on = false;
    } finally {
      THREADS.loading = null;
      // Every way out draws the sidebar again, the early return for a server without threads included.
      threadsSideRender();
    }
  })();
  const loading = THREADS.loading;
  await loading;
  if (THREADS.again) { THREADS.again = false; return threadsLoad(); }
}
// The rest of the Threads list, a page at a time (the rail's More).
async function threadsMore(): Promise<void> {
  if (!THREADS.next || !THREADS.rail) return;
  const r = await get<ThreadRailReply>('/v2/threads/rail?limit=' + THREADS_PAGE + '&cursor=' + encodeURIComponent(THREADS.next), {fresh: true});
  // A later page may hold pins too (past the first page's share): they go on Pinned.
  const have = new Set([...THREADS.pinned, ...THREADS.threads].map(t => t.id));
  THREADS.pinned = [...THREADS.pinned, ...(r?.pinned || []).filter(t => !have.has(t.id))];
  THREADS.threads = [...THREADS.threads, ...(r?.threads || []).filter(t => !have.has(t.id))];
  THREADS.next = r?.next_cursor || null;
  threadsSideRender();
}
// Once the first read is in (the Threads page waits for it to choose a thread).
async function threadsReady(): Promise<void> {
  if (THREADS.on === null) await (THREADS.loading || threadsLoad());
}

// ----------------------------------------------------------------- the sidebar
function threadsSideRender() {
  // Shown from the first paint, before the first read lands: only a server without threads (THREADS.on false) hides it.
  const on = THREADS.on !== false;
  const link = $('#nav-threads'); if (link) link.hidden = !on;
  // On a phone Threads takes Search's place in the bottom bar; global search is then in More (the drawer's footer).
  const tab = $('#mobile-threads'), search = $('#mobile-search');
  if (tab) tab.hidden = !on;
  if (search) search.hidden = on;
  if (THREADS.on !== true) return;
  // The link and the phone tab carry the same count: what Catch up has left.
  const n = threadsCatchUp();
  for (const badge of [$('#nav-threads .nav-badge'), $('#mobile-threads .nav-badge')]) {
    if (badge) { badge.hidden = !n; badge.textContent = threadsCountText(n); badge.setAttribute('aria-label', `${n} to catch up`); }
  }
  if (typeof railRender === 'function') railRender();
}
// The unread in the viewer's 1:1 DM with this teammate (THREADS.dms), for the Team list.
const threadsDmUnread = (actor: string) => Number(THREADS.dms?.[actor]) || 0;

// ----------------------------------------------------------------- live rows
// Maps each row list (pinned, then the rest) through `fn`: it returns the row's new value, or null/undefined to keep it.
const threadsEach = (fn: (t: ThreadRow) => ThreadRow | null | undefined) => {
  for (const key of ['pinned', 'threads'] as const) THREADS[key] = THREADS[key].map(t => fn(t) || t);
};
// One thread's row, updated in place from a thread view the server sent: archived leaves the rail, a pin moves it to
// Pinned (or back), a new one the viewer is in joins its list by title, and on the plain list a renamed one moves.
// `recheck` (a members event): whether the viewer is still in it decides, not whether it is listed.
function threadsPut(view: ThreadRow, {recheck = false} = {}) {
  if (!view?.id || view.parent_id) return;
  if (view.archived_at || view.task_hidden) THREADS.cu?.delete(view.id);
  let row: ThreadRow | null = null;
  threadsEach(t => {
    if (t.id !== view.id) return;
    // A view is that thread's own numbers; what the row sums up from its sub-threads stays until the list is read again.
    row = {...t, ...view, last_message_at: t.last_message_at || view.last_message_at,
           me: {...t.me, ...view.me, unread: t.me?.unread ?? view.me?.unread, activity: t.me?.activity ?? view.me?.activity}};
    return row;
  });
  const me: ThreadMe = (row || view).me || {};
  const listed = !!row, wanted = threadOnRail(view) && ((listed && !recheck) || me.member || me.following || me.pinned);
  if (!wanted) {
    if (listed) { THREADS.pinned = THREADS.pinned.filter(t => t.id !== view.id); THREADS.threads = THREADS.threads.filter(t => t.id !== view.id); }
  } else if (THREADS.rail && 'pinned' in me) {
    // Where it belongs now.
    const t = row || view, into = me.pinned ? 'pinned' : 'threads', out = me.pinned ? 'threads' : 'pinned';
    THREADS[out] = THREADS[out].filter(x => x.id !== t.id);
    if (!THREADS[into].some(x => x.id === t.id)) THREADS[into] = [...THREADS[into], t].sort(threadsByTitle);
  } else if (!listed) THREADS.threads = [...THREADS.threads, view].sort(threadsByTitle);
  // The plain list is by title: a renamed thread goes where its new title puts it. (The rail's order is the server's
  // ranking, which a row alone cannot redo; its next read does.)
  else if (!THREADS.rail) THREADS.threads = [...THREADS.threads].sort(threadsByTitle);
  threadsSideRender();
}
// Read up to `seq` here: the row's count clears at once, the server's answer follows on the stream.
function threadsMarkedRead(id: string, seq: number) {
  threadsEach(t => {
    if (t.id !== id || !t.me) return;
    const readTo = Math.max(Number(t.me.last_read_seq) || 0, seq);
    const me: ThreadMe = {...t.me, last_read_seq: readTo};
    // Something newer in a sub-thread keeps the row's marks: the list read after this says what is left.
    const below = Number(t.activity_seq) > (Number(t.last_seq) || 0);
    if (!below && readTo >= (Number(t.last_unread_seq) || 0)) me.unread = 0;
    if (!below && readTo >= (Number(t.last_seq) || 0)) me.activity = false;
    return {...t, me};
  });
  threadsSideRender();
}
// Read through, the whole subtree (Mark read, Catch up, the viewer's own `read` event): the row is quiet, Catch up
// lets the root go, and a 1:1 DM's count on its teammate clears.
function threadsAllRead(id: string) {
  threadsEach(t => t.id === id ? {...t, me: {...t.me, unread: 0, activity: false}} : null);
  THREADS.cu?.delete(id);
  const dm = THREADS.roots[id]?.dm;
  if (dm && Number(THREADS.dms[dm])) { THREADS.dms = {...THREADS.dms, [dm]: 0}; if (typeof renderTree === 'function') renderTree(); }
  threadsSideRender();
}
// The viewer's own read (it comes only to them): that one thread's mark. The root's own read moves the row's mark
// (threadsMarkedRead: what is past it, or new in a sub-thread, stays); a sub-thread's read cannot settle a row that sums
// the whole subtree, which keeps its marks until the next read. A row left quiet leaves Catch up, and an open Catch up's
// card for it goes. A 1:1 DM is not on the rail: its own read clears its count on its teammate.
function threadsReadLive(d: ThreadsEvent) {
  const root = d.root_id || d.thread_id;
  if (!root) return;
  const own = !d.thread_id || d.thread_id === root;
  if (own) threadsMarkedRead(root, Number(d.read?.last_read_seq) || 0);
  const row = threadsRow(root), dm = THREADS.roots[root]?.dm;
  if (row ? threadUnread(row) || row.me?.activity : !(own && dm)) return;
  threadsAllRead(root);
  if (typeof catchUpGone === 'function') catchUpGone(root);
}
// Archive a thread from its row or its header: it leaves Threads and Catch up at once, and a short Undo puts it back.
// The server refuses anyone who does not manage the thread.
// An archived thread, here or in another window, leaves Catch up and its count at once; a 1:1 DM's count on its teammate
// goes too (a DM is not on the list, so only this clears it).
// A thread the viewer can no longer read: off the rail, Catch up and its count, with nothing read for it.
function threadsGone(id: string) {
  THREADS.pinned = THREADS.pinned.filter(t => t.id !== id);
  THREADS.threads = THREADS.threads.filter(t => t.id !== id);
  threadsArchivedGone(id, THREADS.roots[id]?.dm);
}
function threadsArchivedGone(id: string, dm?: string | null) {
  THREADS.cu?.delete(id);
  if (typeof catchUpGone === 'function') catchUpGone(id, true);
  if (dm && Number(THREADS.dms[dm])) { THREADS.dms = {...THREADS.dms, [dm]: 0}; if (typeof renderTree === 'function') renderTree(); }
  threadsSideRender();
}
// onUndo lets a caller that shows the thread's state (a bot's More row) redraw it after the toast's Undo.
async function threadsArchive(id: string, onUndo?: () => void): Promise<void> {
  const view: ThreadRow = await patch(thvPath(id), {archived: true});
  threadsPut(view);
  threadsArchivedGone(id, threadsRootLearn(view)?.dm);
  toast('Archived', false, {label: 'Undo', run: async () => {
    threadsUnarchived(await patch(thvPath(id), {archived: false}));
    onUndo?.();
  }});
  threadsRecount();
}
// A thread taken back out of the archive (Undo, or Unarchive in its header): it is back in its list, and Catch up counts
// it again at once when it has unread messages. The rail read after this says what the server counts.
function threadsUnarchived(back: ThreadRow) {
  threadsPut(back);
  if (THREADS.cu && threadUnread(back) > 0) THREADS.cu.add(back.id);
  threadsRecount();
}
const threadsRow = (id: string | undefined): ThreadRow | null => [...THREADS.pinned, ...THREADS.threads].find(t => t.id === id) || null;
// What a root not on the rail is, read alone once (GET /v2/threads/{id}): a 1:1 DM counts on its teammate.
function threadsRootLearn(view: ThreadRow | null | undefined): ThreadRoot | null {
  if (!view?.id || view.parent_id) return null;
  const others = threadOthers(view);
  const dm = threadIsDm(view) && !threadIsGroupDm(view) && others.length === 1 ? others[0] : null;
  // Another's Home is in Catch up only through a mention, an ask or a hold, which `needs` brings.
  const theirHome = view.audience === 'home' && view.home?.actor !== myActor();
  return (THREADS.roots[view.id] = {dm, muted: view.me?.notify === 'muted' || theirHome, following: !!(view.me?.following || view.me?.member), task: !!view.task_id, home: theirHome});
}
async function threadsRoot(id: string): Promise<ThreadRoot> {
  const known = THREADS.roots[id];
  if (known) return known;
  const t = await get<ThreadRow>(thvPath(id), {fresh: true}).catch(() => null);
  return threadsRootLearn(t) || (THREADS.roots[id] = {dm: null, muted: false, following: false});
}
// A task's thread is read again on its own once its events burst is in: the closing notice is no change to its row, and a
// post after the finish brings it back, so its view (which says whether the task hides it) decides whether it is listed.
const threadsViewLater = (id: string) => liveSoon('threads-view:' + id, () => { void threadsReadView(id); }, 400);
async function threadsReadView(id: string): Promise<void> {
  const view = await get<ThreadRow>(thvPath(id), {fresh: true}).catch(() => null);
  if (!view?.id) return;
  threadsRootLearn(view);
  threadsPut(view);
  if (view.task_hidden && typeof catchUpGone === 'function') catchUpGone(id, true);
}
// A message anywhere under a root (`activity`). Unread for this viewer and not in the open thread: its row counts one
// more and Catch up takes the root; the open thread reads it itself. Otherwise someone else's is activity on its row.
async function threadsActivity(d: ThreadsEvent) {
  const root = d.root_id || d.thread_id;
  if (!root) { threadsSideRender(); return; }
  const row = threadsRow(root), info = row ? null : await threadsRoot(root);
  if (row?.task_id || info?.task) threadsViewLater(root);
  if (S.route === threadHref(root)) { threadsSideRender(); return; }
  const fresh = d.unread === true && !(d.message_id && THREADS.counted.has(d.message_id));
  // Several messages in one batch come as one event: `unread_count` of them are unread (1 from an older server).
  const more = fresh ? Math.max(1, Number(d.unread_count) || 1) : 0;
  if (d.message_id) { if (THREADS.counted.size > 2000) THREADS.counted.clear(); THREADS.counted.add(d.message_id); }
  if (row) {
    const theirs = d.unread === true || (d.actor && d.actor !== myActor());
    threadsEach(t => {
      if (t.id !== root) return;
      const me: ThreadMe = {...t.me, activity: theirs || !!t.me?.activity, unread: threadUnread(t) + more};
      const counters = d.thread_id === root && d.counters ? {last_seq: d.counters.last_seq, last_unread_seq: d.counters.last_unread_seq} : {};
      return {...t, ...counters, activity_seq: Math.max(Number(t.activity_seq) || 0, Number(d.counters?.last_seq) || 0), me};
    });
    if (fresh && row.me?.notify !== 'muted') THREADS.cu?.add(root);
  } else if (fresh && info) {
    // Not on the rail: a DM, a task's thread or a Home. Catch up counts it; a 1:1 DM counts on its teammate too. Its
    // Unread row counts the new ones in place; a chat with no row yet is read with the rail, which names it.
    if (!info.muted) {
      THREADS.cu?.add(root);
      const listed = THREADS.unread.find(u => u.id === root);
      if (listed) THREADS.unread = THREADS.unread.map(u => u === listed ? {...u, new_count: (Number(u.new_count) || 0) + more} : u);
      else if (THREADS.cu && THREADS.rail) liveSoon('threads-rail', threadsLoad, 400);
    } else if (info.home && THREADS.cu && THREADS.rail) liveSoon('threads-rail', threadsLoad, 400);
    if (info.dm && info.dm in THREADS.dms) {
      THREADS.dms = {...THREADS.dms, [info.dm]: threadsDmUnread(info.dm) + more};
      if (typeof renderTree === 'function') renderTree();
    }
  }
  threadsSideRender();
}
// A pin moved (the viewer's own, or a manager's for everyone; `pinned` is this viewer's state): the row moves at once,
// and a thread not on the rail yet is read alone.
async function threadsPinned(d: ThreadsEvent) {
  const t = threadsRow(d.thread_id);
  if (t) { threadsPut({...t, pinned_for_everyone: d.pinned_for_everyone ?? t.pinned_for_everyone, me: {...t.me, pinned: !!d.pinned}}); return; }
  if (!d.pinned) return;
  const view = await get<ThreadRow>(thvPath(d.thread_id), {fresh: true}).catch(() => null);
  if (view?.id) threadsPut({...view, me: {...view.me, pinned: true}});
}
// The whole rail is read only on the first load, a `reset`, a reconnect and the safety poll; every other change is
// patched from its event (above). Once per burst.
const threadsRecount = () => liveThrottle('threads-rail', threadsLoad, 3000);
// Every route: the first load, the current row marked, and the stream wired once.
function threadsSync() {
  if (!S.me) return;
  if (THREADS.on === null && !THREADS.loading) {
    void threadsLoad();
    if (THREADS.loading) liveProbe(THREADS.loading);     // the first stream waits for it, to open with `threads` (live.ts)
  } else threadsSideRender();
  if (THREADS.wired) return;
  THREADS.wired = true;
  liveOn('threads', (d: ThreadsEvent) => {
    THREADS.gen++;
    if (d.kind === 'activity') void threadsActivity(d);
    else if (d.kind === 'read' && d.read) threadsReadLive(d);
    else if (d.kind === 'pinned' && d.thread_id) void threadsPinned(d);
    else if (d.gone && d.thread_id) threadsGone(d.thread_id);
    else if (d.thread && !d.thread.parent_id) {
      // A 1:1 DM is not on the rail: it is learned for its teammate's count; a new one starts that count.
      if (threadIsDm(d.thread) && !threadIsGroupDm(d.thread)) {
        const info = threadsRootLearn(d.thread);
        if (d.thread.archived_at) threadsArchivedGone(d.thread.id, info?.dm);
        else if (d.kind === 'created' && info?.dm && info.following && !info.muted && !(info.dm in THREADS.dms)) {
          THREADS.dms = {...THREADS.dms, [info.dm]: threadUnread(d.thread)};
          if (typeof renderTree === 'function') renderTree();
        }
      } else {
        threadsPut(d.thread, {recheck: d.kind === 'members'});
        // Archived, here or in another window, or finished with its task (the view says so): its Catch up card goes too.
        if ((d.thread.archived_at || d.thread.task_hidden) && typeof catchUpGone === 'function') catchUpGone(d.thread.id, true);
      }
    }
    // A paused root is a Needs you row with no `needs` event of its own, so that list is read whole, as if one had come.
    if (d.kind === 'capped') liveSoon('threads-needs', () => liveEmit('needs', {version: 'read'}), 400);
    threadViewsLive('threads', d);
  });
  // A Needs you change while connected reads nothing here (Nov 3): an unread mention already came as its thread's
  // `activity` (above), and what no event names (an ask answered, a held message let go) waits for the safety read. A
  // reconnect that carried `threads` over (it replays the thread changes it missed) naming the Needs you version the
  // rail was last read at, within 5 minutes of that read, moved nothing, so the rail is not read again for it. Any other
  // connect reads it: a change no event names (a thread the viewer can no longer see) is caught by one at least that often.
  // The first stream resumes from the first read's `seq` (liveTopicAdd), so it carries `threads` over too; that read went
  // out just before Needs you was first read, and the version that read holds stands for it (`needsFirst`).
  liveOn('needs', (d: NeedsEvent) => {
    if (!THREADS.on || !d?.connect) return;
    const fresh = Date.now() - THREADS.readAt < THREADS_CONNECT_FRESH_MS;
    const seen = THREADS.needsSeen ?? (THREADS.needsFirst ? S.v2.needsVersion || null : null);
    if (fresh && d.carried?.includes('threads') && d.version === seen) return;
    threadsRecount();
  });
  liveOn('reset', () => { liveSoon('threads-rail', threadsLoad, 400); threadViewsLive('reset', {}); });
  // A safety read every few minutes, for a change the stream never carried.
  setInterval(() => { if (THREADS.on && !document.hidden) threadsRecount(); }, 180000);
}
