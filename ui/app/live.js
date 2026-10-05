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
              handlers: new Map(), conversations: new Map(), expiredToast: false};
const liveAvailable = () => typeof EventSource !== 'undefined';
const liveConnected = () => LIVE.connected;
// `fn(data)` for each event of `topic` (one of LIVE_TOPICS, or `reset` and `status`); returns the unsubscribe.
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
const liveFallback = (fn, ms) => liveAvailable() ? 0 : setInterval(fn, ms);
// Run `fn` once after a burst: every call within `ms` of the first folds into it.
const LIVE_SOON = new Map();
function liveSoon(key, fn, ms = 250) {
  if (LIVE_SOON.has(key)) return;
  LIVE_SOON.set(key, setTimeout(() => { LIVE_SOON.delete(key); fn(); }, ms));
}
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
function liveUrl() {
  const conversations = [...LIVE.conversations.keys()];
  // Without a conversation on screen, no messages or runs: those are every room's, and only one is shown.
  const topics = conversations.length ? LIVE_TOPICS : LIVE_TOPICS.filter(t => t !== 'messages' && t !== 'runs');
  const q = new URLSearchParams({topics: topics.join(',')});
  if (LIVE.after != null) q.set('after', String(LIVE.after));
  if (conversations.length) q.set('conversation', conversations.join(','));
  return `${API}/v2/events?${q}`;
}
const liveData = ev => { try { return JSON.parse(ev.data); } catch { return null; } };
function liveAdvance(seq) {
  seq = Number(seq);
  if (Number.isFinite(seq) && seq > 0 && (LIVE.after == null || seq > LIVE.after)) LIVE.after = seq;
}
function liveEventsConnect() {
  clearTimeout(LIVE.timer); clearTimeout(LIVE.restart);
  try { LIVE.es?.close(); } catch {}
  const es = LIVE.es = new EventSource(liveUrl());
  LIVE.opened = Date.now();
  const current = () => LIVE.es === es;
  es.addEventListener('ready', ev => {
    if (!current()) return;
    const d = liveData(ev) || {};
    if (LIVE.after == null) LIVE.after = Number(d.seq) || 0;
    const was = LIVE.connected;
    LIVE.connected = true; LIVE.expiredToast = false;
    if (!was) liveEmit('status', {connected: true});
  });
  for (const topic of LIVE_TOPICS) es.addEventListener(topic, ev => {
    if (!current()) return;
    const d = liveData(ev); if (!d) return;
    liveAdvance(ev.lastEventId || d.seq);
    liveEmit(topic, d);
  });
  es.addEventListener('cursor', ev => { if (current()) liveAdvance(ev.lastEventId || liveData(ev)?.seq); });
  es.addEventListener('reset', ev => {
    if (!current()) return;
    LIVE.after = Number(liveData(ev)?.seq) || 0;
    liveEmit('reset', {});
  });
  // Signed out (or the session lapsed): say so once, and look again in a while; another tab may sign in.
  es.addEventListener('expired', () => {
    if (!current()) return;
    liveDown(es);
    if (!LIVE.expiredToast) { LIVE.expiredToast = true; toast('Sign in again to continue receiving updates.', true); }
    LIVE.timer = setTimeout(liveEventsConnect, 30000);
  });
  // The server ends each stream after about five minutes; the browser would reconnect on its own, but with the
  // first URL's `after`. This reconnects with the newest one: at once after a full stream, backing off while
  // the connection keeps failing.
  es.onerror = () => {
    if (!current()) return;
    liveDown(es);
    if (Date.now() - LIVE.opened > 20000) LIVE.tries = 0;
    const wait = LIVE.tries ? Math.min(30000, 1000 * 2 ** (LIVE.tries - 1)) : 250;
    LIVE.tries++;
    LIVE.timer = setTimeout(liveEventsConnect, wait);
  };
}
function liveDown(es) {
  try { es.close(); } catch {}
  if (LIVE.es === es) LIVE.es = null;
  if (LIVE.connected) { LIVE.connected = false; liveEmit('status', {connected: false}); }
}
// A tab coming back from sleep or a dropped network reconnects now rather than at the end of its backoff.
window.addEventListener('online', () => { if (LIVE.started && !LIVE.connected) liveEventsConnect(); });
window.liveEvents = {on: liveOn, follow: liveFollow, available: liveAvailable, connected: liveConnected};
