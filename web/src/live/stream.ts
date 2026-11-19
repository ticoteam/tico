import { useSyncExternalStore } from 'react';
import { adaptMe, type SignedIn } from '@/api/adapt';
import { ApiError, api } from '@/api/client';
import type { GetMeResponse } from '@/api/schema';
import { clearHeld, connectionChanged } from '@/data/held';
import { keys } from '@/data/keys';
import { refetchQuiet } from '@/data/optimistic';
import { queryClient } from '@/data/query-client';
import { tabId } from '@/lib/tab';
import { dropFrame, push, setSink } from './frame';
import { installNotices } from './notices';
import { dispatch, installPatchers, resetPatchState } from './patch';

// One EventSource for the whole app on GET /api/v2/events (backend/events.py). Frames are `id: <seq>`,
// `event: <topic>`, `data: {seq, actor, at, ...}`; control frames are `ready` (no id), `cursor` and `reset` (with an
// id), `expired` (no id, `{}`), and a `: keepalive` comment. The server ends each stream after about 15 minutes;
// this reconnects from the newest change number it has seen, so nothing is missed, and backs off while it fails.

export const TOPICS = [
  'tasks',
  'messages',
  'runs',
  'bots',
  'needs',
  'threads',
  'meeting_items',
  'presence',
  'letters',
  'calendar',
  'setup',
  'screen',
] as const;
export type Topic = (typeof TOPICS)[number];
export type LiveStatus = 'live' | 'connecting' | 'offline' | 'signed-out';
export interface LiveEvent {
  seq?: number;
  actor?: string | null;
  at?: string;
  [field: string]: unknown;
}

/** What the stream asks for: its topics and the server's narrowing (20 values each at most). */
export interface StreamScope {
  topics: Topic[];
  conversation: string[];
  thread: string[];
  task: string[];
  party: string[];
  meeting: string[];
  place: string[];
}

const MAX_FILTER = 20; // backend/events.py MAX_FILTER
const FIRST_RETRY_MS = 250; // a stream the server ended on time reconnects at once
const BACKOFF_MS = [1000, 2000, 5000, 10_000]; // then 10 s steady (platforms.md 2.11), with jitter
const OFFLINE_AFTER_MS = 10_000; // under this a drop shows nothing (platforms.md 7)
const AWAY_MS = 30_000; // a tab hidden this long reconnects when shown: iOS drops streams silently
const RESTART_MS = 30; // several follows changing at once reconnect once

let scopeOf: () => StreamScope = () => ({
  topics: ['bots', 'needs', 'threads', 'calendar', 'setup', 'screen'],
  conversation: [],
  thread: [],
  task: [],
  party: [],
  meeting: [],
  place: [],
});

let started = false;
let es: EventSource | null = null;
let after: number | null = null; // the newest change number seen: where a reconnect resumes
let delivered = false; // a topic event reached the cache since start
let tries = 0;
let openedAt = 0;
let hiddenAt = 0;
let wasDown = false; // read quiet areas again once the stream is back
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let restartTimer: ReturnType<typeof setTimeout> | undefined;
let offlineTimer: ReturnType<typeof setTimeout> | undefined;

// ---------------------------------------------------------------- status

let status: LiveStatus = 'connecting';
const statusListeners = new Set<(s: LiveStatus) => void>();

function setStatus(next: LiveStatus): void {
  if (next === status) return;
  status = next;
  if (next === 'offline' || next === 'signed-out') wasDown = true;
  connectionChanged(next);
  for (const fn of [...statusListeners]) fn(next);
}

export const liveStatus = (): LiveStatus => status;

export function onLiveStatus(fn: (s: LiveStatus) => void): () => void {
  statusListeners.add(fn);
  return () => void statusListeners.delete(fn);
}

export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(onLiveStatus, liveStatus, liveStatus);
}

// ---------------------------------------------------------------- connecting

export function setScopeSource(fn: () => StreamScope): void {
  scopeOf = fn;
}

function url(): string {
  const s = scopeOf();
  // `client`: this tab's id (architecture.md 5).
  const q = new URLSearchParams({ topics: s.topics.join(','), needs: 'changes', output: 'compact', client: tabId() });
  if (after != null) q.set('after', String(after));
  // Past the server's cap a pair is left out: the stream then sends more (every readable row), never less.
  const pair = (a: 'conversation' | 'task', b: 'thread' | 'party') => {
    if (s[a].length > MAX_FILTER || s[b].length > MAX_FILTER) return;
    if (s[a].length) q.set(a, s[a].join(','));
    if (s[b].length) q.set(b, s[b].join(','));
  };
  pair('conversation', 'thread');
  pair('task', 'party');
  if (s.meeting.length && s.meeting.length <= MAX_FILTER) q.set('meeting', s.meeting.join(','));
  if (s.place.length && s.place.length <= MAX_FILTER) q.set('place', s.place.join(','));
  return `/api/v2/events?${q}`;
}

function parse(e: Event): LiveEvent | null {
  try {
    const d: unknown = JSON.parse(String((e as MessageEvent).data));
    return d && typeof d === 'object' ? (d as LiveEvent) : null;
  } catch {
    return null;
  }
}

function seqOf(d: LiveEvent | null): number | null {
  const n = Number(d?.seq);
  return d?.seq != null && Number.isFinite(n) ? n : null;
}

function advance(seq: number | null): void {
  if (seq != null && (after == null || seq > after)) after = seq;
}

function connect(): void {
  clearTimeout(retryTimer);
  clearTimeout(restartTimer);
  if (!started || status === 'signed-out') return;
  es?.close();
  const source = new EventSource(url());
  es = source;
  openedAt = Date.now();
  const mine = () => es === source;

  source.addEventListener('ready', (e) => {
    if (!mine()) return;
    clearOffline();
    if (after == null) {
      // A first stream starts from now. What was read before it opened may have missed a change in between: read
      // what is on screen once more (not the person: the gate would take a failed read for a fault).
      after = seqOf(parse(e));
      // cancelRefetch false: a page's loader may be reading the same thing right now; cancelling it would fail the page.
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== keys.me()[0] }, { cancelRefetch: false });
      return isLive();
    }
    // A resumed stream keeps its own number, and the server replays from it. The cookie may be someone else's by
    // now (another tab signed in): ask who it is before held changes go out under it.
    void probe(false).then((same) => {
      if (same && mine()) isLive();
    });
  });
  source.addEventListener('cursor', (e) => {
    if (mine()) advance(seqOf(parse(e)));
  });
  source.addEventListener('reset', (e) => {
    if (!mine()) return;
    // Further behind than the server keeps: what is queued is older than what a read in full will bring.
    after = seqOf(parse(e)) ?? after;
    dropFrame();
    void queryClient.invalidateQueries(undefined, { cancelRefetch: false });
    dispatch('reset', [{ seq: after }]);
  });
  source.addEventListener('expired', () => {
    if (!mine()) return;
    down(source);
    void probe(true);
  });
  for (const topic of TOPICS)
    source.addEventListener(topic, (e) => {
      if (!mine()) return;
      const d = parse(e);
      if (!d) return;
      // Frames without a seq (a followed task's access loss) are never replays. A seq at or below the newest
      // seen is one already applied: a replay after a reconnect, so it is dropped.
      const seq = seqOf(d);
      if (seq != null) {
        if (after != null && seq <= after) return;
        after = seq;
      }
      delivered = true;
      push(topic, d);
    });
  source.onerror = () => {
    if (!mine()) return;
    const lived = Date.now() - openedAt;
    down(source);
    if (lived > 20_000) tries = 0;
    const wait = tries === 0 ? FIRST_RETRY_MS : jitter(BACKOFF_MS[Math.min(tries - 1, BACKOFF_MS.length - 1)] as number);
    // A second failure in a row with the network up may be a sign-out: EventSource cannot say 401.
    if (tries++ > 0 && navigator.onLine !== false) void probe(false);
    retryTimer = setTimeout(connect, wait);
  };
}

function isLive(): void {
  setStatus('live');
  if (wasDown) {
    wasDown = false;
    refetchQuiet();
  }
}

const jitter = (ms: number) => Math.round(ms * (0.8 + Math.random() * 0.4));

function down(source: EventSource): void {
  source.close();
  if (es === source) es = null;
  if (status === 'signed-out') return;
  if (navigator.onLine === false) return setStatus('offline');
  if (status === 'live') setStatus('connecting');
  armOffline();
}

/** Offline once not live for OFFLINE_AFTER_MS, counted from the first failure: failing retries do not push it back. */
function armOffline(): void {
  if (status !== 'connecting' || offlineTimer !== undefined) return;
  offlineTimer = setTimeout(() => {
    offlineTimer = undefined;
    if (status === 'connecting') setStatus('offline');
  }, OFFLINE_AFTER_MS);
}

function clearOffline(): void {
  clearTimeout(offlineTimer);
  offlineTimer = undefined;
}

/**
 * Ask the server who is signed in. A 401 signs the app out; someone other than the person on screen (another tab
 * signed in as them) starts the app again as that person. True when it is still the same person, or the server
 * could not say.
 */
async function probe(reconnect: boolean): Promise<boolean> {
  let who: SignedIn | null = null;
  let refused: ApiError | null = null;
  try {
    who = adaptMe(await api.get<GetMeResponse>('/api/v2/me'));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) refused = err;
  }
  if (!started) return false;
  if (refused) {
    signedOut(refused);
    return false;
  }
  const shown = queryClient.getQueryData<SignedIn>(keys.me());
  if (who && shown && who.id !== shown.id) {
    switched();
    return false;
  }
  // Still signed in (another tab signed in again after `expired`): reconnect after a pause.
  if (reconnect && !es) retryTimer = setTimeout(connect, jitter(BACKOFF_MS[BACKOFF_MS.length - 1] as number));
  return true;
}

function halt(): void {
  clearTimeout(retryTimer);
  clearTimeout(restartTimer);
  clearOffline();
  es?.close();
  es = null;
}

/**
 * Signed out: held changes are dropped, everything read as the person goes, and the person's own read takes the
 * 401, which is how the root decides to show its signed-out card (features/start/gate.ts).
 */
function signedOut(error: ApiError): void {
  halt();
  setStatus('signed-out');
  clearHeld();
  queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.me()[0] });
  queryClient.getQueryCache().find({ queryKey: keys.me(), exact: true })?.setState({ status: 'error', error, data: undefined, fetchStatus: 'idle' });
}

/** Someone else is signed in now: nothing of the last person's goes out, and the app opens again as the new one. */
function switched(): void {
  halt();
  clearHeld();
  location.reload();
}

/** Reconnect now, from where the stream was (back from sleep, the network, the browser's page cache). */
function wake(): void {
  if (!started || status === 'signed-out') return;
  tries = 0;
  connect();
}

function onVisibility(): void {
  if (document.visibilityState === 'hidden') {
    hiddenAt = Date.now();
    return;
  }
  const away = hiddenAt ? Date.now() - hiddenAt : 0;
  hiddenAt = 0;
  if (away > AWAY_MS) wasDown = true;
  if (status !== 'live' || away > AWAY_MS) wake();
}
const onPageShow = (e: PageTransitionEvent) => {
  if (e.persisted) {
    wasDown = true;
    wake();
  }
};
const onOffline = () => {
  if (started && status !== 'signed-out') setStatus('offline');
};

// ---------------------------------------------------------------- the API

/** Open the stream (once; again after stopLive or a sign-out). */
export function startLive(): void {
  if (started) {
    if (status === 'signed-out') {
      setStatus('connecting');
      connect();
    }
    return;
  }
  started = true;
  setStatus('connecting');
  installPatchers();
  installNotices();
  setSink(dispatch);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('pageshow', onPageShow);
  window.addEventListener('online', wake);
  window.addEventListener('offline', onOffline);
  armOffline();
  connect();
}

/** Close the stream and forget where it was (sign-out, tests). */
export function stopLive(): void {
  started = false;
  clearTimeout(retryTimer);
  clearTimeout(restartTimer);
  clearOffline();
  es?.close();
  es = null;
  after = null;
  delivered = false;
  tries = 0;
  hiddenAt = 0;
  wasDown = false;
  dropFrame();
  resetPatchState();
  document.removeEventListener('visibilitychange', onVisibility);
  window.removeEventListener('pageshow', onPageShow);
  window.removeEventListener('online', wake);
  window.removeEventListener('offline', onOffline);
  setStatus('connecting');
  wasDown = false;
}

/** The follows changed: reconnect once, from where the stream was, with the new narrowing. */
export function restartLive(): void {
  if (!started || status === 'signed-out' || !es) return; // a stream waiting to retry opens with the new scope
  clearTimeout(restartTimer);
  restartTimer = setTimeout(connect, RESTART_MS);
}

/**
 * A first read answered as of change `seq` (a list's cursor, the rail's seq). Until the stream has delivered its
 * first event, it starts from the oldest such number, so nothing between the read and the stream is lost.
 */
export function liveFrom(seq: number): void {
  if (delivered || !Number.isFinite(seq) || (after != null && seq >= after)) return;
  after = seq;
  if (es) restartLive();
}
