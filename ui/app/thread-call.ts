/* ui/app/thread-call.ts — Calls in a thread (docs/meet.md, docs/dev/meet-calls-contract.md): the phone button in the
   thread's header (a green pill with who is in it while a call is live), the call card a call posts in its thread, the
   rail's green phone, and the two kinds of call. A Google Meet call opens Meet in a new tab; a Tico Meet call opens its
   meeting window (#/call/<id>, ui/app/call-window.ts): a small window in the desktop app, a tab on the web, the whole
   screen on a phone. When the window cannot open, the call is joined here, with one bar while in it (mute, share
   screen, add bot, open the window, leave).
   Tico Meet is audio and screen share only: the camera is never asked for. Its media client is LiveKit's
   (ui/vendor/livekit-client.umd.min.js, Apache-2.0), loaded on the first call. Ringing is ui/app/call-ring.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Someone in a call, as the call view lists them; `in_call` is what the status source (Google) says now.
interface CallPerson { actor: string; joined_at?: string | null; left_at?: string | null; removed_at?: string | null; in_call?: boolean | number; }
// A bot's voice in a Tico Meet call (docs/meet.md, Voice): what it is doing, and the sentence it is saying.
interface CallVoiceBot { slug: string; state: 'listening' | 'waiting' | 'thinking' | 'making_voice' | 'speaking' | string; caption?: string | null; }
interface CallVoiceView { bots: CallVoiceBot[]; note?: string | null; }
// The server's call view (GET /v2/calls/{id}, list items, the start and join answers; a Start answer may wrap it in {call}).
interface CallRecord {
  id: string; title?: string; state?: string; connector?: string; call?: CallRecord;
  thread_id?: string; provider?: string; meet_uri?: string | null; join_url?: string | null;
  live_count?: number; status_source?: string | null; seen_at?: string | null; started_at?: string | null; ended_at?: string | null;
  duration_s?: number | null; ring?: boolean; card_message_id?: string | null; transcript_message_id?: string | null;
  participants?: CallPerson[]; meeting_id?: string | null; end_reason?: string | null;
  // A scheduled call (state 'scheduled'): when it is booked for. `people`: who it is with (GET /v2/calls/upcoming).
  scheduled_start?: string | null; scheduled_end?: string | null; people?: string[];
  // The bots in the call: slugs, or (an older voice view) each bot with what it is doing and its words now; what each is
  // doing now comes in `voice` (null without voice).
  bots?: (string | CallBotRaw)[]; voice?: CallVoiceView | null;
  // What shows on the stage, as this viewer may see it (null for nothing; absent from an older server), and how many
  // times it changed: a read with a lower `stage_n` is older (docs/meet.md, Sharing).
  stage?: CallStageView | null; stage_n?: number;
}
// A screen, or a Tico file: its id, version and title, or only `no_access` when the viewer may not read it.
interface CallStageView { kind: 'screen' | 'file'; by: string; at?: string | null; file_id?: string; version?: number; title?: string; no_access?: boolean; }
// A bot in a call as the server may send it: `state` is listening, waiting, thinking, making_voice or speaking.
interface CallBotRaw { slug?: string; state?: string | null; caption?: string | null; }
interface CallListReply { calls?: CallRecord[]; available?: boolean; providers?: string[]; }
interface CallJoinReply { call?: CallRecord; token: string; url: string; }
// Per thread: {call} (the live call, or null), {off} when the server has calls switched off, {removed} after a removal,
// {none} when the server has no calls at all. `providers`: what the viewer could start here, in order. `next`: the
// soonest scheduled call.
interface CallThreadInfo { call?: CallRecord | null; next?: CallRecord | null; none?: boolean; off?: boolean; removed?: boolean; providers?: string[]; }
// The parts of LiveKit's room, participants and tracks this file reads.
interface CallPublication { source?: unknown; track?: { stop?: () => void; mediaStreamTrack?: { stop?: () => void } } | null; }
interface CallParticipant { identity: string; isSpeaking?: boolean; isMicrophoneEnabled?: boolean; }
interface CallTrack { kind: string; source: unknown; attach(): HTMLMediaElement; detach(): HTMLMediaElement[]; }
interface CallLocal {
  identity?: string; isSpeaking?: boolean;
  setMicrophoneEnabled(on: boolean): Promise<unknown>;
  setScreenShareEnabled(on: boolean, options: {audio: boolean}): Promise<unknown>;
  trackPublications?: Map<string, CallPublication>;
}
interface CallRoom {
  localParticipant: CallLocal;
  remoteParticipants?: Map<string, CallParticipant>;
  canPlaybackAudio?: boolean;
  startAudio?: () => Promise<unknown>;
  on(event: string, listener: (...args: never[]) => void): unknown;
  connect(url: string, token: string, options: {autoSubscribe: boolean}): Promise<unknown>;
  disconnect(): void;
}
interface LivekitNamespace {
  Room: new (options: {adaptiveStream: boolean; dynacast: boolean}) => CallRoom;
  RoomEvent: {
    TrackSubscribed: string; TrackUnsubscribed: string; ParticipantConnected: string;
    ParticipantDisconnected: string; LocalTrackUnpublished: string; Disconnected: string;
    // Read by the meeting window (who speaks, who is muted, sound blocked); an older client may not have them.
    ActiveSpeakersChanged?: string; TrackMuted?: string; TrackUnmuted?: string; AudioPlaybackStatusChanged?: string;
    LocalTrackPublished?: string; Reconnecting?: string; Reconnected?: string;
  };
  Track: {Source: {ScreenShare: unknown}};
  DisconnectReason?: {PARTICIPANT_REMOVED: unknown};
}
// The call this tab is in.
interface CallState {
  id: string; threadId: string; title: string; connector: string | undefined; room: CallRoom;
  token: string; url: string; muted: boolean; sharing: boolean; leaving: boolean; timer: number; gen: number;
  // The stage (thread-call-live.ts): `stageN` is the call view's `stage_n` this tab's own last write left, `staging` the
  // writes still on their way, and `reconnecting` while LiveKit reconnects (its unpublishing then is not a stop).
  stageN: number; staging: number; reconnecting: boolean;
}

const CALL_LIB = '/vendor/livekit-client.umd.min.js';
// Tokens last five minutes; a fresh one a minute early keeps a slow answer from dropping the call.
const CALL_REFRESH_MS = 4 * 60 * 1000;
// A stalled leave must give up so a later Join can take its turn.
const CALL_LEAVE_TIMEOUT_MS = 5000;
const CALL_THREADS = new Map<string, CallThreadInfo>();
let CALL: CallState | null = null;
let CALL_LIB_LOAD: Promise<LivekitNamespace> | null = null;
// Each join takes the next number; a join that a newer one overtook stops before it turns the microphone on.
let CALL_GEN = 0;
// Joins on their way (asked for, not yet in the call or given up).
let CALL_JOINS = 0;
// Call id -> joins of it still on their way, so a cancelled Start call never gives back a seat a Join has taken.
const CALL_JOINING = new Map<string, number>();
// Call id -> all leaves still on their way, so a rejoin cannot get ahead of cleanup of its previous seat.
const CALL_LEAVING = new Map<string, Promise<void>>();
// Start's seat waits for the last Join to settle: a cancelled or failed Join still owes it back.
// Any leave settles it too, so the last Join cannot give it back twice.
const CALL_OWED = new Set<string>();
// A leave stays settled until the last Join finishes, including for a Start response still on its way.
const CALL_SETTLED = new Set<string>();
// Named here so scripts/build-icon-font.py keeps them in the icon subset.
const CALL_ICONS = {mic: 'mic', muted: 'mic_off', share: 'screen_share', stop: 'stop_screen_share', bot: 'smart_toy', leave: 'call_end',
  window: 'open_in_new', more: 'expand_more'};
// The call's Tico voice in the LiveKit room (backend/call_media.py RECORDER_PREFIX): never shown as a person.
const CALL_VOICE_PREFIX = 'tico-recorder-';

// body.error of a failed call request: its code when it is an object, or the message string itself. '' when there is none.
function callCode(e: unknown): string {
  if (!isErrorRecord(e) || !isErrorRecord(e.body)) return '';
  const detail = e.body.error;
  if (isErrorRecord(detail)) return typeof detail.code === 'string' ? detail.code : '';
  return typeof detail === 'string' ? detail : '';
}
const callPath = (id: string) => '/v2/calls/' + encodeURIComponent(id);
const callShareable = () => !!navigator.mediaDevices?.getDisplayMedia;
function callSeatSettled(id: string): void {
  CALL_OWED.delete(id);
  if (CALL_JOINING.has(id)) CALL_SETTLED.add(id);
}
function callPostLeave(id: string, opts: {keepalive?: boolean} = {}): Promise<void> {
  const leave: Promise<unknown> = post(callPath(id) + '/leave', {}, undefined,
    {...opts, timeout: window.__callLeaveTimeoutMs ?? CALL_LEAVE_TIMEOUT_MS}).catch((e: unknown) => {
    // Every leave is a new one: a kept request id would make the next leave (after a rejoin) replay this one.
    const signature = isErrorRecord(e) ? e.signature : undefined;
    if (typeof signature === 'string') pendingWrites.delete(signature);
  });
  const pending: Promise<void> = Promise.all([CALL_LEAVING.get(id), leave]).then(() => {
    if (CALL_LEAVING.get(id) === pending) CALL_LEAVING.delete(id);
  });
  CALL_LEAVING.set(id, pending);
  return pending;
}
function callSeatLeave(id: string): void {
  callSeatSettled(id);
  void callPostLeave(id, {keepalive: true});
}
function callViewsDraw(threadId: string): void { for (const v of THV_VIEWS) if (v.id === threadId && !v.gone) thvDraw(v); }
function callNoted(threadId: string, e: unknown): void {
  if (errorStatus(e) === 503) CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), off: true});
  else if (errorStatus(e) === 403 || callCode(e) === 'call_access_removed') CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), removed: true});
  else { toast(errorMessage(e, 'Could not join the call')); return; }
  callViewsDraw(threadId);
}

// ----------------------------------------------------------------- the header
async function callLoad(view: ThvView): Promise<void> {
  const t = view.thread;
  if (!t || view.opts.header === false || !view.id) return;
  try {
    // The newest few: a live call and a booked one are among them (a booking is listed from when it was made).
    const r = await get<CallListReply>(`/v2/threads/${encodeURIComponent(view.id)}/calls?limit=5`, {fresh: true});
    const live = (r.calls || []).find(c => c.state === 'live') || null;
    const next = (r.calls || []).filter(c => c.state === 'scheduled')
      .sort((a, b) => String(a.scheduled_start).localeCompare(String(b.scheduled_start)))[0] || null;
    CALL_THREADS.set(view.id, {...CALL_THREADS.get(view.id), call: live, next, none: false, off: r.available === false,
                               ...(r.providers ? {providers: r.providers} : {})});
    if (next) CALL_VIEWS.set(next.id, CALL_VIEWS.get(next.id) || next);
    if (live?.provider) callViewKeep(live);
  } catch (e) {
    // 404: a call the viewer cannot read.
    CALL_THREADS.set(view.id, errorStatus(e) === 503 ? {off: true} : {none: true});
  }
  callViewsDraw(view.id);
}
function callHeadHTML(view: ThvView): string {
  const t = view.thread, info = CALL_THREADS.get(view.id);
  if (!t || !info || info.none) return '';
  if (CALL?.threadId === view.id) return '<button type="button" class="thv-call-note thv-call-on" data-call="window" title="Open the call">In call</button>';
  if (info.removed) return '<span class="thv-call-note muted">Removed from call</span>';
  if (info.off) return '';     // calls are not set up on this server: no control that cannot work
  // A live call is a green pill with who is in it; tapping it joins.
  if (info.call) {
    const n = callCount(CALL_VIEWS.get(info.call.id) || info.call);
    return `<button type="button" class="thv-call on" data-call="join" aria-label="Join call" title="Join">${CALL_SVG}${n ? `<span class="tnum">${n}</span>` : ''}</button>`;
  }
  // A booked call: when, which opens its window (Join is there).
  const next = info.next ? CALL_VIEWS.get(info.next.id) || info.next : null;
  const booked = next?.state === 'scheduled' ? `<button type="button" class="thv-call-note thv-call-next" data-call="next" title="Open the call">Call · ${esc(callWhen(next.scheduled_start))}</button>` : '';
  // A DM with one bot calls that bot, which only Tico Meet can do: no phone where the server has no Tico Meet.
  if (!callTicoMeet(info.providers) && callDmBot(t)) return booked;
  if (!t.me?.can_post) return booked;
  // Tico Meet is every call's default; a Google Meet is asked for by name, from the phone's menu.
  const meet = info.providers?.includes('google_meet') && info.providers[0] !== 'google_meet';
  return `${booked}<button type="button" class="thv-call" data-call="start" aria-label="Start call" title="Call">${CALL_SVG}</button>${
    meet ? `<button type="button" class="thv-call thv-call-more" data-call="more" aria-label="More ways to call" title="More" aria-haspopup="menu"><span class="nav-icon" aria-hidden="true">${CALL_ICONS.more}</span></button>` : ''}`;
}
// Whether a bot can be called here: only Tico Meet calls take bots. `providers` is a thread's list answer (what the viewer
// could start); without one, the server's config says whether it runs Tico Meet.
const callTicoMeet = (providers?: string[]): boolean => providers ? providers.includes('tico') : S.config.features?.tico_meet === true;
// A phone: the meeting takes this tab's whole screen instead of a window of its own.
const callPhone = (): boolean => matchMedia('(max-width: 760px)').matches;
// Opens a Tico Meet call's meeting window, in the click: on a phone (and on the call page itself) this tab goes there;
// the desktop app makes a small window of its own (app/src/windows.rs open_call_window); a browser opens a tab, `tab`
// when one was opened already in the click. One window per call: the browser's is named tico-call-<id>, so a second
// Start, Join or card brings the same one forward (the desktop app's has its own label). `join=1` says the window was
// opened to join: a plain visit to its address shows Join instead. This tab's own seat in that call, if it has one,
// moves to the window: its media stops here without a leave, and the window joins. False when no window could open (a
// blocked pop-up): the caller joins here instead.
function callWindowOpen(call: CallRecord, tab: Window | null = null, join = true): boolean {
  const hash = CALL_ROUTE + encodeURIComponent(call.id) + (join ? '?join=1' : ''), name = 'tico-call-' + call.id;
  const close = (): void => { try { tab?.close(); } catch {} };
  if (callPhone() || location.hash.startsWith(CALL_ROUTE)) { close(); location.hash = hash; return true; }
  const native = (window.ticoNativeFeatures || []).includes('callWindow') && nativeHandler('callWindow');
  let opened = false;
  if (native) { close(); native.postMessage({call: call.id, join}); opened = true; }
  else {
    const url = location.pathname + location.search + hash;
    if (tab && !tab.closed) { try { tab.name = name; tab.location.href = url; opened = true; } catch { close(); } }
    if (!opened) { try { const w = window.open(url, name); w?.focus(); opened = !!w; } catch { opened = false; } }
  }
  if (opened && CALL?.id === call.id) callHandOff(CALL);
  return opened;
}
async function callAction(view: ThvView, kind: string | undefined, from?: HTMLElement): Promise<void> {
  const id = view.id, info: CallThreadInfo = CALL_THREADS.get(id) || {};
  // The phone's menu: a Google Meet instead of Tico Meet, for this call.
  if (kind === 'more') {
    if (from) thvMenuShow(from, [['meet', 'Use Google Meet instead']], () => { void callAction(view, 'meet'); });
    return;
  }
  // In the call here: its window opens and takes the call over.
  if (kind === 'window') { const v = CALL ? CALL_VIEWS.get(CALL.id) || {id: CALL.id, thread_id: CALL.threadId} : null; if (v) callWindowOpen(v); return; }
  // A booked call: its window shows when it starts, with Join.
  if (kind === 'next') { if (info.next) callWindowOpen(info.next, null, false); return; }
  const live = info.call ? CALL_VIEWS.get(info.call.id) || info.call : null;
  // Google Meet goes to a tab of its own, opened now while the click still allows it.
  if (kind === 'join' && live?.provider === 'google_meet') { await callMeetJoin(live); return; }
  // A Tico Meet call opens its window, which joins it.
  if (kind === 'join' && live && callWindowOpen(live)) return;
  // Start: a blank tab opens now, in the click and before anything is awaited, so a pop-up blocker lets it through. It
  // goes to the call's window (or the Meet) once the start answers, and closes when the start fails. A phone opens a tab
  // only for a Google Meet (its own call page is this tab). A call in a DM with one bot is a voice call with that bot,
  // always Tico Meet.
  const starting = kind === 'start' || kind === 'meet';
  const bot = kind === 'start' ? callDmBot(view.thread) : '';
  const meet = kind === 'meet' || (!bot && info.providers?.[0] === 'google_meet');
  let tab = starting && (meet || !callPhone()) ? callTabOpen() : null;
  const tabClose = (): void => { try { tab?.close(); } catch {} tab = null; };
  // Counted as a join on its way from the click, so a page hidden while the call is being made says it is in one,
  // and never joins the call once it is made.
  const gen = ++CALL_GEN;
  CALL_JOINS++;
  try {
    let call: CallRecord | null | undefined = info.call;
    if (starting) {
      const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(id)}/calls`,
        bot ? {provider: 'tico', bots: [bot]} : {provider: kind === 'meet' ? 'google_meet' : 'auto'});
      call = made?.call || made;
      if (call?.provider === 'google_meet') {
        CALL_THREADS.set(id, {...CALL_THREADS.get(id), call});
        const opened = tab; tab = null;
        callMeetLanded(call, opened, call.join_url || '');
        return;
      }
      if (call?.id && gen === CALL_GEN) {
        const opened = tab; tab = null;
        // The window joins it; the header here is the live call's pill until the window's join shows.
        if (callWindowOpen(call, opened)) { CALL_THREADS.set(id, {...CALL_THREADS.get(id), call}); callViewKeep(call); return; }
      }
      tabClose();
    }
    if (gen !== CALL_GEN) {
      // Hidden or overtaken while the call was being made: the server already counts this person in it, so the seat goes
      // back, unless a Join of the same call (from the header the call's start redrew) is in it or on its way.
      if (starting && call?.id) {
        if (CALL_JOINING.get(call.id)) {
          if (!CALL_SETTLED.has(call.id)) CALL_OWED.add(call.id);
        } else if (CALL?.id !== call.id) callSeatLeave(call.id);
      }
      return;
    }
    if (call?.id) await callJoin(id, call, threadTitle(view.thread), gen);
  } catch (e) {
    tabClose();
    if (gen !== CALL_GEN) return;
    if (callCode(e) === 'call_in_progress') { void callLoad(view); toast('A call already started here'); return; }
    callNoted(id, e);
  } finally {
    tabClose();
    CALL_JOINS--;
  }
}

// The bot a DM is with, when it is a DM with exactly one bot ('' otherwise).
function callDmBot(t: ThreadRow | null | undefined): string {
  if (!threadIsDm(t) || t.parent_id || Number(t.dm_with_count || 1) !== 1) return '';
  const others = threadOthers(t);
  return others.length === 1 && others[0]?.startsWith('bot:') ? others[0].slice(4) : '';
}
// Call a bot from anywhere (the org chart): the viewer's DM with it (made when there is none), a Tico Meet call there
// with the bot in it, joined here. A live call in that DM is joined instead.
async function callBot(slug: string): Promise<void> {
  const gen = ++CALL_GEN;
  CALL_JOINS++;
  try {
    const r = await get<ThvThread & {thread?: ThvThread}>('/v2/threads/dm?with=' + encodeURIComponent('bot:' + slug), {fresh: true});
    const t = (r?.thread !== undefined ? r.thread : r) || null;
    if (!t?.id || gen !== CALL_GEN) return;
    let call: CallRecord | null | undefined, started = false;
    try {
      const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(t.id)}/calls`, {provider: 'tico', bots: [slug]});
      call = made?.call || made; started = true;
    } catch (e) {
      if (callCode(e) !== 'call_in_progress') throw e;
      const list = await get<CallListReply>(`/v2/threads/${encodeURIComponent(t.id)}/calls?limit=1`, {fresh: true});
      call = (list.calls || []).find(c => c.state === 'live');
    }
    if (!call?.id) return;
    if (call.provider === 'google_meet') { await callMeetJoin(call); return; }
    // Hidden or overtaken while the call was being made: the seat the start took goes back.
    if (gen !== CALL_GEN) { if (started && CALL?.id !== call.id) callSeatLeave(call.id); return; }
    await callJoin(t.id, call, threadTitle(t), gen);
  } catch (e) {
    if (gen === CALL_GEN) toast(errorMessage(e, 'Could not call'), true);
  } finally {
    CALL_JOINS--;
  }
}
// Call teammates from anywhere (the Meetings page's Call now): one teammate is the viewer's DM with them (made when there
// is none), more are a new thread with them all, named for the call's title, else for who is in it (a group needs a
// name). A Tico Meet call starts there with the bots among them, and its meeting window opens and joins it, as the
// header's Start does: the blank tab opens now, in the click, before anything is awaited. A live call already in that
// DM is joined instead. Resolves to whether a call opened; a refusal is thrown for the caller to show.
async function callTeammates(actors: string[], title = ''): Promise<boolean> {
  const bots = actors.filter(a => a.startsWith('bot:')).map(a => a.slice(4));
  let tab = callPhone() ? null : callTabOpen();
  const tabClose = (): void => { try { tab?.close(); } catch {} tab = null; };
  const gen = ++CALL_GEN;
  CALL_JOINS++;
  try {
    const t = await callThreadWith(actors, title);
    if (!t?.id || gen !== CALL_GEN) return false;
    let call: CallRecord | null | undefined, started = false;
    try {
      const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(t.id)}/calls`,
        {provider: 'tico', ...(bots.length ? {bots} : {}), ...(title ? {title} : {})});
      call = made?.call || made; started = true;
    } catch (e) {
      if (callCode(e) !== 'call_in_progress') throw e;
      const list = await get<CallListReply>(`/v2/threads/${encodeURIComponent(t.id)}/calls?limit=1`, {fresh: true});
      call = (list.calls || []).find(c => c.state === 'live');
    }
    if (!call?.id) return false;
    if (call.provider === 'google_meet') { tabClose(); await callMeetJoin(call); return true; }
    // Hidden or overtaken while the call was being made: the seat the start took goes back.
    if (gen !== CALL_GEN) { if (started && CALL?.id !== call.id) callSeatLeave(call.id); return false; }
    const opened = tab; tab = null;
    if (callWindowOpen(call, opened)) { callViewKeep(call); return true; }
    await callJoin(t.id, call, threadTitle(t), gen);
    return true;
  } finally {
    tabClose();
    CALL_JOINS--;
  }
}

// The thread a call with these teammates goes in: one teammate is the viewer's DM with them, more are a new thread.
async function callThreadWith(actors: string[], title: string): Promise<ThreadRow | null> {
  const r: (ThreadRow & {thread?: ThreadRow}) | null = actors.length === 1
    ? await get('/v2/threads/dm?with=' + encodeURIComponent(actors[0]!), {fresh: true})
    : await post('/v2/threads', {title: title || actors.map(a => actorName(a)).join(', ').slice(0, 200), audience: 'members', members: actors});
  const t = r?.thread || r;
  if (t?.id) threadsPut(t);
  return t?.id ? t : null;
}
// Book a call with teammates for later (the Meetings page's Schedule): the call and its calendar event, nothing opens.
// Its bots are given their prep 30 minutes before (docs/meet.md, Scheduled calls). A refusal is thrown for the caller.
async function callSchedule(actors: string[], title: string, startsAt: Date, minutes: number): Promise<CallRecord | null> {
  const t = await callThreadWith(actors, title);
  if (!t) return null;
  const bots = actors.filter(a => a.startsWith('bot:')).map(a => a.slice(4));
  const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(t.id)}/calls`, {
    provider: 'tico', ...(bots.length ? {bots} : {}), ...(title ? {title} : {}),
    starts_at: startsAt.toISOString(), ends_at: new Date(startsAt.getTime() + minutes * 60000).toISOString()});
  const call = made?.call || made;
  if (call?.id) { callViewKeep(call); CALL_THREADS.set(t.id, {...CALL_THREADS.get(t.id), next: call}); }
  return call || null;
}
// When a booked call starts, short: "Today 3:00 PM", "Thu 3:00 PM", "Nov 30, 3:00 PM".
function callWhen(at: string | null | undefined): string {
  const d = at ? new Date(at) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  const time = d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86400000);
  if (days === 0) return 'Today ' + time;
  if (days > 0 && days < 7) return d.toLocaleDateString(undefined, {weekday: 'short'}) + ' ' + time;
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) + ', ' + time;
}

// ----------------------------------------------------------------- in a call: ui/app/thread-call-live.ts, the thread-call-live page part

// The page goes out of sight: the call ends for this tab at once (its microphone and screen stop, its seat is given
// back), and a join still on its way stops before it turns anything on. Answers whether a call was on or on its way.
function callHidden(): {inCall: boolean} {
  const inCall = !!CALL || CALL_JOINS > 0;
  CALL_GEN++;
  // CALL is only ever set by the in-call part, so when it is set that part has loaded and callHangUp is its own function,
  // run now: the microphone stops before the page is gone.
  if (CALL) callHangUp(CALL);
  return {inCall};
}
// Leaving the page leaves the call, so nobody stays in a call they can no longer see. The desktop app runs the same
// when it hides an org's page to show another org's (no pagehide comes then): window.ticoPageHidden.
addEventListener('pagehide', () => { callHidden(); });
window.ticoPageHidden = () => callHidden();

// ----------------------------------------------------------------- the call view
// The phone, as the header, the card, the rail and the ring draw it.
const CALL_SVG = '<svg class="call-ic" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>';
// A missed call: the phone hung up, with the missed arrow.
const CALL_MISSED_SVG = '<svg class="call-ic" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6.5 5.5 12 11l7-7-1-1-6 6-4.5-4.5H11V3H5v6h1.5V5.5zm17.21 11.17C20.66 13.78 16.54 12 12 12S3.34 13.78.29 16.67c-.18.18-.29.43-.29.71s.11.53.29.71l2.48 2.48c.18.18.43.29.71.29.27 0 .52-.11.7-.28.79-.74 1.69-1.36 2.66-1.85.33-.16.56-.5.56-.9v-3.1c1.45-.48 3-.73 4.6-.73s3.15.25 4.6.72v3.1c0 .39.23.74.56.9.98.49 1.87 1.12 2.67 1.85.18.18.43.28.7.28.28 0 .53-.11.71-.29l2.48-2.48c.18-.18.29-.43.29-.71s-.12-.52-.3-.7z"/></svg>';
// Call id -> its view as last read, for the cards, the header's count, the rail's mark and the ring.
const CALL_VIEWS = new Map<string, CallRecord>();
// Calls whose view could not be read (gone, or not the viewer's to read): their cards stay plain rather than asking again.
const CALL_VIEW_MISSING = new Set<string>();
const CALL_VIEW_READS = new Map<string, Promise<CallRecord | null>>();
// A Google Meet call that nothing reports on (no Google status): Tico does not know who is in it, so it
// shows no count and no faces, only the call. Opening it from Tico is not being in it.
const callBlind = (c: CallRecord): boolean => c.provider === 'google_meet' && !c.status_source;
// Who is in it now: the status source's count while there is one; a Tico Meet call has none, so it is who joined and its
// bots.
function callCount(c: CallRecord | null | undefined): number {
  if (!c || callBlind(c)) return 0;
  if (c.status_source) return Number(c.live_count) || 0;
  // A Tico Meet call counts its bots too, as its meeting window does.
  return (c.participants || []).filter(p => p.joined_at && !p.left_at && !p.removed_at).length + (c.state === 'live' ? (c.bots || []).length : 0);
}
// The faces on a live card: who the status source sees, else (Tico Meet) who joined.
const callFaces = (c: CallRecord): string[] => callBlind(c) ? [] : (c.participants || [])
  .filter(p => c.status_source ? !!p.in_call : p.joined_at && !p.left_at && !p.removed_at).map(p => p.actor);
async function callViewRead(id: string): Promise<CallRecord | null> {
  let read = CALL_VIEW_READS.get(id);
  if (!read) {
    read = get<CallRecord>(callPath(id), {fresh: true}).then(v => { if (v?.id) { callViewKeep(v); return v; } return null; }, (e: unknown) => {
      const status = errorStatus(e);
      if (status === 404 || status === 403) { CALL_VIEW_MISSING.add(id); callViewGone(id); }
      return null;
    }).finally(() => CALL_VIEW_READS.delete(id));
    CALL_VIEW_READS.set(id, read);
  }
  return read;
}
// A call's view, newly read: the thread's header, its card, the rail's mark and the ring follow it.
function callViewKeep(v: CallRecord): void {
  if (!v?.id) return;
  const was = CALL_VIEWS.get(v.id);
  CALL_VIEWS.set(v.id, v);
  CALL_VIEW_MISSING.delete(v.id);
  const tid = v.thread_id || was?.thread_id;
  if (tid) {
    const info = CALL_THREADS.get(tid);
    if (info?.call?.id === v.id && v.state !== 'live') CALL_THREADS.set(tid, {...info, call: null});
    else if (info && !info.call && v.state === 'live') CALL_THREADS.set(tid, {...info, call: v, ...(info.next?.id === v.id ? {next: null} : {})});
    else if (info?.next?.id === v.id && v.state !== 'scheduled') CALL_THREADS.set(tid, {...info, next: null});
    callRailMark(tid, v);
    if (JSON.stringify(was) !== JSON.stringify(v)) callViewsDraw(tid);
  }
  // The bar shows the bots' voice (thread-call-live.ts; CALL is set only once that part has loaded).
  if (CALL?.id === v.id) callBarDraw();
  if (typeof ringConsider === 'function') ringConsider(v);
  // An ended call is over here too, even when its room was not closed on the server: the room, the token's timer and
  // the microphone stop.
  if (v.state !== 'live' && CALL?.id === v.id) callDone(CALL);
  callWindowDraw();
}
// A call the viewer can no longer read: its mark and ring go.
function callViewGone(id: string): void {
  const v = CALL_VIEWS.get(id);
  CALL_VIEWS.delete(id);
  threadsEach(t => t.live_call?.id === id ? {...t, live_call: null} : null);
  if (typeof railRender === 'function') railRender();
  if (v?.thread_id) callViewsDraw(v.thread_id);
  if (typeof ringGone === 'function') ringGone(id);
}
// The rail row's green phone follows the call (the row's `live_call`, which the rail read brings too).
function callRailMark(threadId: string, v: CallRecord): void {
  let moved = false;
  threadsEach(t => {
    if (t.id !== threadId) return null;
    const had = t.live_call;
    if (v.state === 'live') {
      const next = {id: v.id, live_count: callCount(v)};
      if (had?.id === next.id && had.live_count === next.live_count) return null;
      moved = true;
      return {...t, live_call: next};
    }
    if (!had || had.id !== v.id) return null;
    moved = true;
    return {...t, live_call: null};
  });
  if (moved && typeof railRender === 'function') railRender();
}
// Every call.* change on the `threads` topic: that call is read again (once per burst).
liveOn('threads', (d: ThreadsEvent & {call_id?: string}) => {
  if (!d?.call_id || !String(d.kind || '').startsWith('call.')) return;
  const id = d.call_id;
  liveSoon('call-view:' + id, () => { void callViewRead(id); }, 200);
});

// ----------------------------------------------------------------- Google Meet
// A new tab, opened in the click itself so a pop-up blocker lets it through; it goes to the Meet once the server
// answers. The desktop app opens no blank windows (it sends links to the browser), so it opens the Meet's link then.
function callTabOpen(): Window | null {
  if (window.ticoNative) return null;
  try {
    const tab = window.open('', '_blank');
    if (tab) tab.opener = null;
    return tab;
  } catch { return null; }
}
function callTabGo(tab: Window | null, url: string): void {
  if (!url) { try { tab?.close(); } catch {} return; }
  if (tab && !tab.closed) { try { tab.location.href = url; return; } catch {} }
  window.open(url, '_blank', 'noopener');
}
// The Meet is made (or joined): its tab goes there.
function callMeetLanded(call: CallRecord, tab: Window | null, url: string): void {
  callViewKeep(call);
  callTabGo(tab, url || call.join_url || call.meet_uri || '');
}
// Join a Google Meet call: the server notes the open and answers its link (with the viewer's Google account).
async function callMeetJoin(call: CallRecord): Promise<void> {
  const tab = callTabOpen();
  try {
    const j: {call?: CallRecord; url?: string} | null = await post(callPath(call.id) + '/join', {});
    callMeetLanded({...call, ...(j?.call || {})}, tab, j?.url || call.join_url || '');
  } catch (e) {
    try { tab?.close(); } catch {}
    if (errorStatus(e) === 403 || callCode(e) === 'call_access_removed') { toast('Removed from call'); return; }
    toast(errorMessage(e, 'Could not join the call'));
    if (call.thread_id) void callViewRead(call.id);
  }
}
// Join from anywhere (the card, the ring): Google Meet in a tab, Tico Meet in its window (here when that cannot open).
// Runs its first step in the click.
function callJoinAny(call: CallRecord): Promise<void> {
  if (call.provider === 'google_meet') return callMeetJoin(call);
  if (callWindowOpen(call)) return Promise.resolve();
  const tid = call.thread_id || '';
  return callJoin(tid, call, threadTitle(threadsRow(tid)) || call.title || 'Call').catch(e => callNoted(tid, e));
}

// ----------------------------------------------------------------- the card
// A call's card in its thread (a message with refs.call.card): live, the phone, who is in it, how long, Join, and End for
// whoever started a Google Meet; ended, how long it was and its transcript, or the missed mark for a Google Meet that
// Google watched and saw nobody in. A Google Meet nothing reports on shows only the call.
function callCardHTML(m: ThvMessage): string {
  const id = String(m.refs?.call?.id || '');
  const v = id ? CALL_VIEWS.get(id) : undefined;
  if (id && !v && !CALL_VIEW_MISSING.has(id)) void callViewRead(id);
  const head = (cls: string, svg = CALL_SVG, label = '') => `<span class="call-card-ic${cls}"${label ? ` title="${label}" aria-label="${label}" role="img"` : ''}>${svg}</span><b class="call-card-t">Call</b>`;
  if (!v) return `<div class="call-card" data-call-card="${esc(id)}">${head('')}</div>`;
  if (v.state === 'scheduled') {
    return `<div class="call-card" data-call-card="${esc(id)}">${head('')}<span class="call-card-n muted">${esc(callWhen(v.scheduled_start))}</span>
      <span class="spacer"></span><button type="button" class="ghost small call-card-join" data-call-join="${esc(id)}">Join</button></div>`;
  }
  if (v.state === 'live') {
    const blind = callBlind(v), faces = callFaces(v), since = blind ? '' : v.seen_at || v.started_at || '';
    const end = v.provider === 'google_meet' && v.connector && v.connector === myActor()
      ? `<button type="button" class="ghost small danger call-card-end" data-call-end="${esc(id)}">End</button>` : '';
    if (since) callTick();
    return `<div class="call-card on" data-call-card="${esc(id)}">${head(' on')}${
      faces.length ? `<span class="call-card-ppl">${faces.slice(0, 4).map(a => `<span title="${esc(actorName(a))}">${actorAvatar(a, 20)}</span>`).join('')}</span>` : ''}${
      since ? `<span class="call-card-n muted tnum" data-call-since="${esc(since)}">${esc(callClock(since))}</span>` : ''}
      <span class="spacer"></span>${end}<button type="button" class="primary small call-card-join" data-call-join="${esc(id)}">Join</button></div>`;
  }
  // Missed only when something watched the call (Google) and saw nobody; a call nothing watched just ended.
  const missed = v.provider === 'google_meet' && v.duration_s == null && !!v.status_source;
  const len = callLength(v.duration_s);
  const tid = v.thread_id || m.thread_id || '';
  return `<div class="call-card${missed ? ' missed' : ''}" data-call-card="${esc(id)}">${missed ? head(' missed', CALL_MISSED_SVG, 'Missed') : head('')}${
    len ? `<span class="call-card-n muted tnum">${esc(len)}</span>` : ''}${
    v.transcript_message_id ? `<span class="spacer"></span><a class="call-card-link" href="${esc(threadHref(tid) + '?m=' + encodeURIComponent(v.transcript_message_id))}">Transcript</a>` : ''}</div>`;
}
// End on a card: whoever started the Google Meet ends it for everyone (the server asks Google to end it too).
async function callCardEnd(id: string, button: HTMLButtonElement): Promise<void> {
  if (!(await askConfirm('End the call for everyone?', button))) return;
  button.disabled = true;
  try { await post(callPath(id) + '/end', {}); void callViewRead(id); }
  catch (e) { button.disabled = false; toast(errorMessage(e, 'Could not end the call'), true); }
}
// How long it was: "45 s", "23 min", "1 h 5 min".
function callLength(s: number | null | undefined): string {
  const n = Math.max(0, Math.round(Number(s) || 0));
  if (s == null || !Number.isFinite(Number(s))) return '';
  if (n < 60) return `${n} s`;
  const min = Math.round(n / 60);
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ''}`;
}
// How long it has been going: 4:12, or 1:04:12.
function callClock(since: string): string {
  const s = Math.max(0, Math.floor((Date.now() - Date.parse(since)) / 1000));
  if (!Number.isFinite(s)) return '';
  const hh = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, '0');
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${ss}` : `${mm}:${ss}`;
}
// One tick a second moves every live card's clock; it stops when no card shows one.
let CALL_TICK = 0;
function callTick(): void {
  if (CALL_TICK) return;
  CALL_TICK = window.setInterval(() => {
    const clocks = document.querySelectorAll<HTMLElement>('[data-call-since]');
    if (!clocks.length) { clearInterval(CALL_TICK); CALL_TICK = 0; return; }
    for (const el of clocks) { const t = callClock(el.dataset.callSince || ''); if (el.textContent !== t) el.textContent = t; }
  }, 1000);
}
// Join on a card or a ring, wherever it is drawn (its tab opens in this click), and End on a card.
document.addEventListener('click', ev => {
  const end = eventElement(ev)?.closest<HTMLButtonElement>('[data-call-end]');
  if (end) { void callCardEnd(end.dataset.callEnd || '', end); return; }
  const b = eventElement(ev)?.closest<HTMLElement>('[data-call-join]');
  if (!b) return;
  const id = b.dataset.callJoin || '', v = CALL_VIEWS.get(id);
  if (!v) return;
  if (typeof ringStop === 'function') ringStop(id);
  void callJoinAny(v);
});
