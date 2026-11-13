/* ui/app/live.ts — One live-event stream for the whole page (GET /api/v2/events)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Tasks, bot status lines and Needs you arrive here as they change, for every page; the messages and runs of the
// conversations on screen too (each view says which with liveFollow). One EventSource carries them all. It resumes
// from the last change number it was given, so a reconnect misses nothing; `reset` means it was away longer than
// the server keeps (a day) and each view reads in full. A browser with no EventSource keeps the old polls
// (liveFallback); everywhere else nothing polls for what this stream carries.
type LiveEventData = {seq?: number | string; needs_version?: unknown; [key: string]: unknown};
// Each view's handler takes the data its own event carries (a list, a row, an event), so the parameter is bivariant.
type LiveHandler = Function;
// The task changes this page shows: all of them (a task list), or the open tasks and the parties (bots, people) it names.
type TaskScope = {all: true; task?: undefined; party?: undefined} | {all?: false; task: string[]; party: string[]};
// `topics` is set by liveEventsConnect on each stream it opens.
interface EventSource { topics: string[]; }
interface LiveState {
  es: EventSource | null;
  after: number | null;
  started: boolean;
  connected: boolean;
  opened: number;
  tries: number;
  timer: number;
  restart: number;
  handlers: Map<string, Set<LiveHandler>>;
  conversations: Map<string, number>;
  threads: Map<string, number>;
  taskKey: string;
  urlTopics?: string[];
  readyTopics?: string[];
  waiting: boolean;
}
interface ThrottleEntry { at: number; timer: number; hidden: boolean; fn?: () => void; ms?: number; }
const LIVE_TOPICS = ['tasks', 'messages', 'runs', 'bots', 'needs', 'letters'];
const LIVE: LiveState = {es: null, after: null, started: false, connected: false, opened: 0, tries: 0, timer: 0, restart: 0,
              handlers: new Map(), conversations: new Map(), threads: new Map(), taskKey: '', waiting: false};
// The first reads that may add a topic (liveTopicAdd), still on their way: the first stream waits for them, up to
// LIVE_PROBE_WAIT_MS, so it opens once with every topic rather than once without and again with. A read that hangs
// holds the first stream (and every live update) for that long; if it is the rail, the stream then opens from now, not
// from the rail's seq, and the rail's late liveTopicAdd('threads') reconnects it once more, as before probes.
const LIVE_PROBES = new Set<Promise<unknown>>();
const LIVE_PROBE_WAIT_MS = 5000;
function liveProbe(read: Promise<unknown>): void {
  if (LIVE.started && !LIVE.waiting) return;
  LIVE_PROBES.add(read);
  void read.catch(() => {}).finally(() => LIVE_PROBES.delete(read));
}
const liveAvailable = () => typeof EventSource !== 'undefined';
const liveConnected = () => LIVE.connected;
// `fn(data)` for each event of `topic` (one of LIVE_TOPICS, or `reset`, `status` and `needs-list`); returns the unsubscribe.
function liveOn(topic: string, fn: LiveHandler): () => void {
  let set = LIVE.handlers.get(topic);
  if (!set) { set = new Set(); LIVE.handlers.set(topic, set); }
  const had = set.size;
  set.add(fn);
  if (topic === 'letters' && !had && LIVE.started) liveRestart();
  return () => {
    const removed = LIVE.handlers.get(topic)?.delete(fn);
    if (removed && topic === 'letters' && !LIVE.handlers.get(topic)?.size && LIVE.started) liveRestart();
  };
}
function liveEmit(topic: string, data: unknown): void {
  for (const fn of [...(LIVE.handlers.get(topic) || [])]) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}
// The messages and runs of this conversation while it is on screen; returns the release.
function liveFollow(id: string | number | null | undefined): () => void {
  if (!id) return () => {};
  id = String(id);
  const had = LIVE.conversations.has(id);
  LIVE.conversations.set(id, (LIVE.conversations.get(id) || 0) + 1);
  // A page that is not cloud never wires the stream (liveWire), so a chat on screen starts it here.
  if (!had) { if (LIVE.started) liveRestart(); else liveStart(); }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const n = (LIVE.conversations.get(id) || 1) - 1;
    if (n > 0) LIVE.conversations.set(id, n);
    else { LIVE.conversations.delete(id); liveRestart(); }
  };
}
// The messages of this thread and its sub-threads while it is on screen (`?thread=`, its whole subtree); returns the release.
function liveFollowThread(id: string | number | null | undefined): () => void {
  if (!id) return () => {};
  id = String(id);
  const had = LIVE.threads.has(id);
  LIVE.threads.set(id, (LIVE.threads.get(id) || 0) + 1);
  if (!had) { if (LIVE.started) liveRestart(); else liveStart(); }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const n = (LIVE.threads.get(id) || 1) - 1;
    if (n > 0) LIVE.threads.set(id, n);
    else { LIVE.threads.delete(id); liveRestart(); }
  };
}
// A topic only some servers send (`threads`, once the server answers GET /v2/threads): asking an older server for it
// would fail the whole stream (422), so it joins only when the server has shown it has it.
// `seq`: the change number the read that showed it is as of (GET /v2/threads/rail `seq`). Before the first stream opens,
// that stream resumes from it, so it carries the topic over from that read (`carried` on its `needs` connect) and replays
// whatever changed since, in every topic, rather than starting from now and leaving a gap after the read.
function liveTopicAdd(topic: string, seq?: number | null): void {
  if (LIVE_TOPICS.includes(topic)) return;
  LIVE_TOPICS.push(topic);
  if (seq != null && Number.isFinite(seq) && !LIVE.es && LIVE.after == null) { LIVE.after = seq; LIVE.readyTopics = [topic]; }
  // Threads keep the sidebar live on every install, cloud or not.
  if (LIVE.started) liveRestart(); else liveStart();
}
// Only where the stream cannot run: the poll it replaced. Returns the timer (0 when the stream carries it).
// A hidden tab does not poll: goals / task modal / Updates fell back every few seconds across background tabs
// whenever live was down.
const liveFallback = (fn: () => void, ms: number) => liveAvailable() ? 0 : setInterval(() => { if (!document.hidden) fn(); }, ms);
// Run `fn` once after a burst: every call within `ms` of the first folds into it.
const LIVE_SOON = new Map<string, number>();
function liveSoon(key: string, fn: () => void, ms = 250): void {
  if (LIVE_SOON.has(key)) return;
  // Something changed: the reads this sets off start fresh rather than sharing one sent before the change (api.js get).
  LIVE_SOON.set(key, setTimeout(() => { LIVE_SOON.delete(key); getForget(); fn(); }, ms));
}
// Run `fn` at most once per `ms`, the last call winning, and never while the tab is hidden: a hidden tab runs it once
// when it is shown again. For the reads a change sets off that fetch whole lists.
const LIVE_THROTTLE = new Map<string, ThrottleEntry>();
function liveThrottle(key: string, fn: () => void, ms = 15000): void {
  const t = LIVE_THROTTLE.get(key) || {at: 0, timer: 0, hidden: false};
  t.fn = fn; t.ms = ms; LIVE_THROTTLE.set(key, t);
  if (document.hidden) { t.hidden = true; return; }
  if (t.timer) return;
  t.timer = setTimeout(() => {
    t.timer = 0;
    if (document.hidden) { t.hidden = true; return; }
    t.at = Date.now();
    if (typeof t.fn === 'function') { getForget(); t.fn(); }
  }, Math.max(0, t.at + ms - Date.now()));
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  for (const [key, t] of LIVE_THROTTLE) if (t.hidden && t.fn) { t.hidden = false; liveThrottle(key, t.fn, t.ms); }   // the caller's own pace
});
function liveStart(): void {
  if (LIVE.started || !liveAvailable()) return;
  LIVE.started = true;
  if (!LIVE_PROBES.size) { liveEventsConnect(); return; }
  LIVE.waiting = true;
  const waited = new Promise(resolve => setTimeout(resolve, LIVE_PROBE_WAIT_MS));
  void Promise.race([Promise.allSettled([...LIVE_PROBES]), waited]).then(() => { LIVE.waiting = false; liveEventsConnect(); });
}
function liveRestart(): void {
  if (!LIVE.started || LIVE.waiting) return;              // the first stream, still waiting, opens with what is asked by then
  clearTimeout(LIVE.restart);
  LIVE.restart = setTimeout(liveEventsConnect, 30);       // several views following at once reconnect once
}
// The task changes this page shows (refresh.js liveTasksApply): every task's on a task list, a bot's or a person's
// own on their page, the open task's anywhere, and none elsewhere. Every open tab was sent every task's change.
function liveTaskScope(): TaskScope {
  const r = typeof S !== 'undefined' && S.route || '';
  if (isTasksRoute(r) || r.startsWith('#/meetings')) return {all: true};
  if (r.startsWith('#/task/')) return {task: [decodeURIComponent(String(r.slice(7).split(/[/?]/)[0]))], party: []};
  const open: string[] = typeof TASK_CHAT !== 'undefined' && TASK_CHAT?.id ? [String(TASK_CHAT.id)] : [];
  // A bot's route may name it by slug (a link made from a label); its actor is its id.
  const botRef = r.startsWith('#/bot/') ? decodeURIComponent(String(r.slice(6).split(/[/?]/)[0])) : '';
  const party = botRef ? 'bot:' + (botByRef(botRef)?.id || botRef)
    : r.startsWith('#/person/') ? 'human:' + decodeURIComponent(String(r.slice(9).split(/[/?]/)[0]))
    // My dashboard: the reader's own tasks, for its My tasks card (dashboard.ts dashTasksLive).
    : r === HOME ? myActor() : '';
  return {task: open, party: party ? [party] : []};
}
const liveTaskKey = (scope: TaskScope) => scope.all ? 'all' : scope.task.join(',') + '|' + scope.party.join(',');
// A new page, or a task opened or closed: the stream reconnects (from where it was) only when that changes what it
// should send.
function liveTasksSync(): void {
  if (LIVE.started && LIVE.taskKey !== liveTaskKey(liveTaskScope())) liveRestart();
}
function liveUrl(): string {
  const conversations = [...LIVE.conversations.keys()], threads = [...LIVE.threads.keys()];
  const scope = liveTaskScope();
  LIVE.taskKey = liveTaskKey(scope);
  // Without a conversation on screen, no messages or runs: those are every room's, and only one is shown.
  const topics = (conversations.length || threads.length ? LIVE_TOPICS : LIVE_TOPICS.filter(t => t !== 'messages' && t !== 'runs'))
    .filter(t => t !== 'tasks' || scope.all || scope.task.length || scope.party.length)
    .filter(t => t !== 'letters' || LIVE.handlers.get('letters')?.size);
  // Needs you as changed ids against the list the page holds (v2NeedsChanged), not the whole list each time.
  LIVE.urlTopics = topics;
  // Run steps as compact signals (which step moved, not its contents): no screen here reads a step's payload; each one
  // notes that the run moved and reads its own summary (backend/events.py _step_output).
  const q = new URLSearchParams({topics: topics.join(','), needs: 'changes', output: 'compact'});
  if (LIVE.after != null) q.set('after', String(LIVE.after));
  if (conversations.length) q.set('conversation', conversations.join(','));
  if (threads.length) q.set('thread', threads.join(','));
  if (!scope.all && scope.task.length) q.set('task', scope.task.join(','));
  if (!scope.all && scope.party.length) q.set('party', scope.party.join(','));
  return `${API}/v2/events?${q}`;
}
const liveData = (ev: MessageEvent): LiveEventData | null => { try { return JSON.parse(ev.data); } catch { return null; } };
function liveAdvance(seq: number | string | undefined): void {
  seq = Number(seq);
  if (Number.isFinite(seq) && seq > 0 && (LIVE.after == null || seq > LIVE.after)) LIVE.after = seq;
}
function liveEventsConnect(): void {
  if (LIVE.waiting) return;                               // the first stream opens once its first reads are in (liveStart)
  clearTimeout(LIVE.timer); clearTimeout(LIVE.restart);
  if (SESSION.out) return;                                // signed out: nothing reconnects until the person signs in
  try { LIVE.es?.close(); } catch {}
  const es = LIVE.es = new EventSource(liveUrl());
  es.topics = LIVE.urlTopics || [];
  LIVE.opened = Date.now();
  const current = () => LIVE.es === es;
  es.addEventListener('ready', ev => {
    if (!current()) return;
    const d = liveData(ev) || {};
    // `carried`: the topics this stream resumed without a gap, so it replays what changed in them meanwhile: those the
    // last stream also carried, when it resumes from the last change number (`after`). The first stream carries none,
    // and a topic newly added was not followed up to `after`.
    const resumed = LIVE.after != null, carried = resumed ? es.topics.filter(t => LIVE.readyTopics?.includes(t)) : [];
    LIVE.readyTopics = es.topics;
    if (!resumed) LIVE.after = Number(d.seq) || 0;
    // The list as this stream counts from: a held copy of another version (missed while away) is read again.
    if (d.needs_version) liveEmit('needs', {version: d.needs_version, connect: true, carried});
    const was = LIVE.connected;
    LIVE.connected = true;
    if (!was) liveEmit('status', {connected: true});
  });
  for (const topic of LIVE_TOPICS) es.addEventListener(topic, ev => {
    if (!current()) return;
    const d = liveData(ev); if (!d) return;
    liveAdvance(ev.lastEventId || d.seq);
    getForget();
    liveEmit(topic, d);
  });
  es.addEventListener('cursor', ev => { if (current()) liveAdvance(ev.lastEventId || liveData(ev)?.seq); });
  es.addEventListener('reset', ev => {
    if (!current()) return;
    LIVE.after = Number(liveData(ev)?.seq) || 0;
    liveEmit('reset', {});
  });
  // The server says the sign-in lapsed: GET /v2/me confirms it and shows the signed-out banner (session.js). If
  // another tab signed in meanwhile, look again in a while.
  es.addEventListener('expired', () => {
    if (!current()) return;
    liveDown(es);
    liveAfterProbe(30000);
  });
  // The server ends each stream after about fifteen minutes; the browser would reconnect on its own, but with the
  // first URL's `after`. This reconnects with the newest one: at once after a full stream, backing off while
  // the connection keeps failing.
  // A second failure in a row, while the browser says it is online, asks whether a proxy signed the person out: its
  // login redirect fails the stream exactly as a dropped network does.
  es.onerror = () => {
    if (!current()) return;
    liveDown(es);
    if (Date.now() - LIVE.opened > 20000) LIVE.tries = 0;
    const wait = LIVE.tries ? Math.min(30000, 1000 * 2 ** (LIVE.tries - 1)) : 250;
    if (LIVE.tries++) liveAfterProbe(wait);
    else LIVE.timer = setTimeout(liveEventsConnect, wait);
  };
}
// Reconnect after `wait` as usual, and meanwhile ask whether a proxy signed the person out: if it did, signedOut stops
// the reconnect (liveSignedOut). The reconnect never waits on the answer, so a question that stalls cannot stop it.
function liveAfterProbe(wait: number): void {
  clearTimeout(LIVE.timer);
  LIVE.timer = setTimeout(liveEventsConnect, wait);
  void sessionProbe();
}
// Signed out (session.js signedOut): stop, and say the stream is down.
function liveSignedOut(): void {
  clearTimeout(LIVE.timer); clearTimeout(LIVE.restart);
  if (LIVE.es) liveDown(LIVE.es);
}
function liveDown(es: EventSource): void {
  try { es.close(); } catch {}
  if (LIVE.es === es) LIVE.es = null;
  if (LIVE.connected) { LIVE.connected = false; liveEmit('status', {connected: false}); }
}
// Back after a long time away: a phone may have dropped the stream while the app was suspended without an error
// reaching the page, so it still counts as connected. Reconnect from the last change number; the server sends what
// was missed, or `reset`.
function liveResume(): void {
  if (LIVE.started && !SESSION.out) liveEventsConnect();
}
// A tab coming back from sleep or a dropped network reconnects now rather than at the end of its backoff.
window.addEventListener('online', () => { if (LIVE.started && !LIVE.connected && !SESSION.out) liveEventsConnect(); });
window.liveEvents = {on: liveOn, follow: liveFollow, available: liveAvailable, connected: liveConnected};
