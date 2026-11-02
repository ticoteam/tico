/* ui/app/live.js — One live-event stream for the whole page (GET /api/v2/events)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Tasks, bot status lines and Needs you arrive here as they change, for every page; the messages and runs of the
// conversations on screen too (each view says which with liveFollow). One EventSource carries them all. It resumes
// from the last change number it was given, so a reconnect misses nothing; `reset` means it was away longer than
// the server keeps (a day) and each view reads in full. A browser with no EventSource keeps the old polls
// (liveFallback); everywhere else nothing polls for what this stream carries.
const LIVE_TOPICS = ['tasks', 'messages', 'runs', 'bots', 'needs'];
const LIVE = {es: null, after: null, started: false, connected: false, opened: 0, tries: 0, timer: 0, restart: 0,
              handlers: new Map(), conversations: new Map(), taskKey: ''};
const liveAvailable = () => typeof EventSource !== 'undefined';
const liveConnected = () => LIVE.connected;
// `fn(data)` for each event of `topic` (one of LIVE_TOPICS, or `reset`, `status` and `needs-list`); returns the unsubscribe.
function liveOn(topic, fn) {
  if (!LIVE.handlers.has(topic)) LIVE.handlers.set(topic, new Set());
  LIVE.handlers.get(topic).add(fn);
  return () => LIVE.handlers.get(topic)?.delete(fn);
}
function liveEmit(topic, data) {
  for (const fn of [...(LIVE.handlers.get(topic) || [])]) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}
// The messages and runs of this conversation while it is on screen; returns the release.
function liveFollow(id) {
  if (!id) return () => {};
  id = String(id);
  const had = LIVE.conversations.has(id);
  LIVE.conversations.set(id, (LIVE.conversations.get(id) || 0) + 1);
  if (!had) liveRestart();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const n = (LIVE.conversations.get(id) || 1) - 1;
    if (n > 0) LIVE.conversations.set(id, n);
    else { LIVE.conversations.delete(id); liveRestart(); }
  };
}
// Only where the stream cannot run: the poll it replaced. Returns the timer (0 when the stream carries it).
// A hidden tab does not poll: goals / task modal / Updates fell back every few seconds across background tabs
// whenever live was down.
const liveFallback = (fn, ms) => liveAvailable() ? 0 : setInterval(() => { if (!document.hidden) fn(); }, ms);
// Run `fn` once after a burst: every call within `ms` of the first folds into it.
const LIVE_SOON = new Map();
function liveSoon(key, fn, ms = 250) {
  if (LIVE_SOON.has(key)) return;
  // Something changed: the reads this sets off start fresh rather than sharing one sent before the change (api.js get).
  LIVE_SOON.set(key, setTimeout(() => { LIVE_SOON.delete(key); getForget(); fn(); }, ms));
}
// Run `fn` at most once per `ms`, the last call winning, and never while the tab is hidden: a hidden tab runs it once
// when it is shown again. For the reads a change sets off that fetch whole lists.
const LIVE_THROTTLE = new Map();
function liveThrottle(key, fn, ms = 15000) {
  const t = LIVE_THROTTLE.get(key) || {at: 0, timer: 0, hidden: false};
  t.fn = fn; t.ms = ms; LIVE_THROTTLE.set(key, t);
  if (document.hidden) { t.hidden = true; return; }
  if (t.timer) return;
  t.timer = setTimeout(() => {
    t.timer = 0;
    if (document.hidden) { t.hidden = true; return; }
    t.at = Date.now(); getForget(); t.fn();
  }, Math.max(0, t.at + ms - Date.now()));
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  for (const [key, t] of LIVE_THROTTLE) if (t.hidden) { t.hidden = false; liveThrottle(key, t.fn, t.ms); }   // the caller's own pace
});
function liveStart() {
  if (LIVE.started || !liveAvailable()) return;
  LIVE.started = true;
  liveEventsConnect();
}
function liveRestart() {
  if (!LIVE.started) return;
  clearTimeout(LIVE.restart);
  LIVE.restart = setTimeout(liveEventsConnect, 30);       // several views following at once reconnect once
}
// The task changes this page shows (refresh.js liveTasksApply): every task's on a task list, a bot's or a person's
// own on their page, the open task's anywhere, and none elsewhere. Every open tab was sent every task's change.
function liveTaskScope() {
  const r = typeof S !== 'undefined' && S.route || '';
  if (isTasksRoute(r) || r.startsWith('#/task/')) return {all: true};
  const open = typeof TASK_CHAT !== 'undefined' && TASK_CHAT?.id ? [String(TASK_CHAT.id)] : [];
  // A bot's route may name it by slug (a link made from a label); its actor is its id.
  const botRef = r.startsWith('#/bot/') ? decodeURIComponent(r.slice(6).split(/[/?]/)[0]) : '';
  const party = botRef ? 'bot:' + (botByRef(botRef)?.id || botRef)
    : r.startsWith('#/person/') ? 'human:' + decodeURIComponent(r.slice(9).split(/[/?]/)[0]) : '';
  return {task: open, party: party ? [party] : []};
}
const liveTaskKey = scope => scope.all ? 'all' : scope.task.join(',') + '|' + scope.party.join(',');
// A new page, or a task opened or closed: the stream reconnects (from where it was) only when that changes what it
// should send.
function liveTasksSync() {
  if (LIVE.started && LIVE.taskKey !== liveTaskKey(liveTaskScope())) liveRestart();
}
function liveUrl() {
  const conversations = [...LIVE.conversations.keys()];
  const scope = liveTaskScope();
  LIVE.taskKey = liveTaskKey(scope);
  // Without a conversation on screen, no messages or runs: those are every room's, and only one is shown.
  const topics = (conversations.length ? LIVE_TOPICS : LIVE_TOPICS.filter(t => t !== 'messages' && t !== 'runs'))
    .filter(t => t !== 'tasks' || scope.all || scope.task.length || scope.party.length);
  // Needs you as changed ids against the list the page holds (v2NeedsChanged), not the whole list each time.
  const q = new URLSearchParams({topics: topics.join(','), needs: 'changes'});
  if (LIVE.after != null) q.set('after', String(LIVE.after));
  if (conversations.length) q.set('conversation', conversations.join(','));
  if (!scope.all && scope.task.length) q.set('task', scope.task.join(','));
  if (!scope.all && scope.party.length) q.set('party', scope.party.join(','));
  return `${API}/v2/events?${q}`;
}
const liveData = ev => { try { return JSON.parse(ev.data); } catch { return null; } };
function liveAdvance(seq) {
  seq = Number(seq);
  if (Number.isFinite(seq) && seq > 0 && (LIVE.after == null || seq > LIVE.after)) LIVE.after = seq;
}
function liveEventsConnect() {
  clearTimeout(LIVE.timer); clearTimeout(LIVE.restart);
  if (SESSION.out) return;                                // signed out: nothing reconnects until the person signs in
  try { LIVE.es?.close(); } catch {}
  const es = LIVE.es = new EventSource(liveUrl());
  LIVE.opened = Date.now();
  const current = () => LIVE.es === es;
  es.addEventListener('ready', ev => {
    if (!current()) return;
    const d = liveData(ev) || {};
    if (LIVE.after == null) LIVE.after = Number(d.seq) || 0;
    // The list as this stream counts from: a held copy of another version (missed while away) is read again.
    if (d.needs_version) liveEmit('needs', {version: d.needs_version});
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
function liveAfterProbe(wait) {
  clearTimeout(LIVE.timer);
  LIVE.timer = setTimeout(liveEventsConnect, wait);
  void sessionProbe();
}
// Signed out (session.js signedOut): stop, and say the stream is down.
function liveSignedOut() {
  clearTimeout(LIVE.timer); clearTimeout(LIVE.restart);
  if (LIVE.es) liveDown(LIVE.es);
}
function liveDown(es) {
  try { es.close(); } catch {}
  if (LIVE.es === es) LIVE.es = null;
  if (LIVE.connected) { LIVE.connected = false; liveEmit('status', {connected: false}); }
}
// Back after a long time away: a phone may have dropped the stream while the app was suspended without an error
// reaching the page, so it still counts as connected. Reconnect from the last change number; the server sends what
// was missed, or `reset`.
function liveResume() {
  if (LIVE.started && !SESSION.out) liveEventsConnect();
}
// A tab coming back from sleep or a dropped network reconnects now rather than at the end of its backoff.
window.addEventListener('online', () => { if (LIVE.started && !LIVE.connected && !SESSION.out) liveEventsConnect(); });
window.liveEvents = {on: liveOn, follow: liveFollow, available: liveAvailable, connected: liveConnected};
