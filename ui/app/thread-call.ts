/* ui/app/thread-call.ts — Calls in a thread (docs/meet.md, docs/dev/meet-calls-contract.md): the phone button in the
   thread's header (a green pill with who is in it while a call is live), the call card a call posts in its thread, the
   rail's green phone, and the two kinds of call. A Google Meet call opens Meet in a new tab (and the Tico Recorder on a
   Mac that has it); a Tico Meet call is joined here, with one bar while in it (mute, share screen, add bot, leave).
   Tico Meet is audio and screen share only: the camera is never asked for. Its media client is LiveKit's
   (ui/vendor/livekit-client.umd.min.js, Apache-2.0), loaded on the first call. Ringing is ui/app/call-ring.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Someone in a call, as the call view lists them; `in_call` is what the status source (Google, a Recorder) says now.
interface CallPerson { actor: string; joined_at?: string | null; left_at?: string | null; removed_at?: string | null; in_call?: boolean | number; }
// The server's call view (GET /v2/calls/{id}, list items, the start and join answers; a Start answer may wrap it in {call}).
interface CallRecord {
  id: string; title?: string; state?: string; connector?: string; call?: CallRecord;
  thread_id?: string; provider?: string; meet_uri?: string | null; join_url?: string | null;
  live_count?: number; status_source?: string | null; seen_at?: string | null; started_at?: string | null; ended_at?: string | null;
  duration_s?: number | null; ring?: boolean; card_message_id?: string | null; transcript_message_id?: string | null;
  recorder?: {source?: string | null; installed?: boolean} | null;
  participants?: CallPerson[];
}
interface CallListReply { calls?: CallRecord[]; available?: boolean; providers?: string[]; }
interface CallJoinReply { call?: CallRecord; token: string; url: string; }
// Per thread: {call} (the live call, or null), {off} when the server has calls switched off, {removed} after a removal,
// {none} when the server has no calls at all. `providers`: what the viewer could start here, in order.
interface CallThreadInfo { call?: CallRecord | null; none?: boolean; off?: boolean; removed?: boolean; providers?: string[]; }
// The parts of LiveKit's room, participants and tracks this file reads.
interface CallPublication { source?: unknown; track?: { stop?: () => void; mediaStreamTrack?: { stop?: () => void } } | null; }
interface CallParticipant { identity: string; }
interface CallTrack { kind: string; source: unknown; attach(): HTMLMediaElement; detach(): HTMLMediaElement[]; }
interface CallLocal {
  setMicrophoneEnabled(on: boolean): Promise<unknown>;
  setScreenShareEnabled(on: boolean, options: {audio: boolean}): Promise<unknown>;
  trackPublications?: Map<string, CallPublication>;
}
interface CallRoom {
  localParticipant: CallLocal;
  remoteParticipants?: Map<string, CallParticipant>;
  on(event: string, listener: (...args: never[]) => void): unknown;
  connect(url: string, token: string, options: {autoSubscribe: boolean}): Promise<unknown>;
  disconnect(): void;
}
interface LivekitNamespace {
  Room: new (options: {adaptiveStream: boolean; dynacast: boolean}) => CallRoom;
  RoomEvent: {
    TrackSubscribed: string; TrackUnsubscribed: string; ParticipantConnected: string;
    ParticipantDisconnected: string; LocalTrackUnpublished: string; Disconnected: string;
  };
  Track: {Source: {ScreenShare: unknown}};
  DisconnectReason?: {PARTICIPANT_REMOVED: unknown};
}
// The call this tab is in.
interface CallState {
  id: string; threadId: string; title: string; connector: string | undefined; room: CallRoom;
  token: string; url: string; muted: boolean; sharing: boolean; leaving: boolean; timer: number; gen: number;
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
const CALL_ICONS = {mic: 'mic', muted: 'mic_off', share: 'screen_share', stop: 'stop_screen_share', bot: 'smart_toy', leave: 'call_end'};

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
    const r = await get<CallListReply>(`/v2/threads/${encodeURIComponent(view.id)}/calls?limit=1`, {fresh: true});
    const live = (r.calls || []).find(c => c.state === 'live') || null;
    CALL_THREADS.set(view.id, {...CALL_THREADS.get(view.id), call: live, none: false, off: r.available === false,
                               ...(r.providers ? {providers: r.providers} : {})});
    if (live?.provider) callViewKeep(live);
  } catch (e) {
    // 404 from a server without calls; a call the viewer cannot read is the same answer.
    CALL_THREADS.set(view.id, errorStatus(e) === 503 ? {off: true} : {none: true});
  }
  callViewsDraw(view.id);
}
function callHeadHTML(view: ThvView): string {
  const t = view.thread, info = CALL_THREADS.get(view.id);
  if (!t || !info || info.none) return '';
  if (CALL?.threadId === view.id) return '<span class="thv-call-note thv-call-on" role="status">In call</span>';
  if (info.removed) return '<span class="thv-call-note muted">Removed from call</span>';
  if (info.off) return '';     // calls are not set up on this server: no control that cannot work
  // A live call is a green pill with who is in it; tapping it joins.
  if (info.call) {
    const n = callCount(CALL_VIEWS.get(info.call.id) || info.call);
    return `<button type="button" class="thv-call on" data-call="join" aria-label="Join call" title="Join">${CALL_SVG}${n ? `<span class="tnum">${n}</span>` : ''}</button>`;
  }
  return t.me?.can_post ? `<button type="button" class="thv-call" data-call="start" aria-label="Start call" title="Call">${CALL_SVG}</button>` : '';
}
async function callAction(view: ThvView, kind: string | undefined): Promise<void> {
  const id = view.id, info: CallThreadInfo = CALL_THREADS.get(id) || {};
  // Google Meet goes to a tab of its own, opened now while the click still allows it.
  if (kind === 'join' && info.call?.provider === 'google_meet') { await callMeetJoin(CALL_VIEWS.get(info.call.id) || info.call); return; }
  // Start may make a Google Meet unless the server said it starts Tico Meet here: a blank tab opens now, in the click and
  // before anything is awaited, so a pop-up blocker lets it through. It goes to the Meet once the start answers, and
  // closes when the answer is a Tico Meet call (joined here) or the start fails.
  let tab = kind === 'start' && (!info.providers?.length || info.providers[0] === 'google_meet') ? callTabOpen() : null;
  const tabClose = (): void => { try { tab?.close(); } catch {} tab = null; };
  // Counted as a join on its way from the click, so a page hidden while the call is being made says it is in one,
  // and never joins the call once it is made.
  const gen = ++CALL_GEN;
  CALL_JOINS++;
  try {
    let call: CallRecord | null | undefined = info.call;
    if (kind === 'start') {
      const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(id)}/calls`, {provider: 'auto'});
      call = made?.call || made;
      if (call?.provider === 'google_meet') {
        CALL_THREADS.set(id, {...CALL_THREADS.get(id), call});
        const opened = tab; tab = null;
        callMeetLanded(call, opened, call.join_url || '');
        return;
      }
      tabClose();
    }
    if (gen !== CALL_GEN) {
      // Hidden or overtaken while the call was being made: the server already counts this person in it, so the seat goes
      // back, unless a Join of the same call (from the header the call's start redrew) is in it or on its way.
      if (kind === 'start' && call?.id) {
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

// ----------------------------------------------------------------- in a call
function callLib(): Promise<LivekitNamespace> {
  if (window.LivekitClient) return Promise.resolve(window.LivekitClient);
  CALL_LIB_LOAD ||= new Promise<LivekitNamespace>((ok, fail) => {
    const s = document.createElement('script');
    s.src = CALL_LIB; s.onload = () => ok(window.LivekitClient); s.onerror = () => { CALL_LIB_LOAD = null; fail(new Error('Could not load calls')); };
    document.head.append(s);
  });
  return CALL_LIB_LOAD;
}
// `gen` is the join's number: Start call takes it at the click, so the one number runs through the whole join.
async function callJoin(threadId: string, call: CallRecord, title: string, gen = ++CALL_GEN): Promise<void> {
  CALL_JOINS++;
  CALL_JOINING.set(call.id, (CALL_JOINING.get(call.id) || 0) + 1);
  try { await callJoinNow(threadId, call, title, gen); } finally {
    CALL_JOINS--;
    const left = (CALL_JOINING.get(call.id) ?? 1) - 1;
    if (left) CALL_JOINING.set(call.id, left);
    else {
      CALL_JOINING.delete(call.id);
      CALL_SETTLED.delete(call.id);
      if (CALL_OWED.delete(call.id) && CALL?.id !== call.id) callSeatLeave(call.id);
    }
  }
}
async function callJoinNow(threadId: string, call: CallRecord, title: string, gen: number): Promise<void> {
  if (CALL) await callLeave();
  if (gen !== CALL_GEN) return;
  while (CALL_LEAVING.has(call.id)) {
    await CALL_LEAVING.get(call.id);
    if (gen !== CALL_GEN) return;
  }
  const j: CallJoinReply = await post(callPath(call.id) + '/join', {});
  // Overtaken while joining: this seat is given back and the newer join goes on.
  const stale = (): boolean => { if (gen === CALL_GEN) return false; callSeatLeave(call.id); return true; };
  if (stale()) return;
  const LK = await callLib();
  if (stale()) return;
  const room = new LK.Room({adaptiveStream: false, dynacast: false});
  CALL = {id: call.id, threadId, title: title || call.title || 'Call', connector: (j.call || call).connector, room,
          token: j.token, url: j.url, muted: false, sharing: false, leaving: false, timer: 0, gen};
  const here = CALL;
  room.on(LK.RoomEvent.TrackSubscribed, (track: CallTrack, _pub: unknown, who: CallParticipant | undefined) => { if (CALL === here) callTrackOn(track, who); });
  room.on(LK.RoomEvent.TrackUnsubscribed, (track: CallTrack) => { track.detach().forEach(el => el.remove()); callScreenSync(); });
  room.on(LK.RoomEvent.ParticipantConnected, callBarDraw);
  room.on(LK.RoomEvent.ParticipantDisconnected, callBarDraw);
  room.on(LK.RoomEvent.LocalTrackUnpublished, (pub: CallPublication) => { if (pub.source === LK.Track.Source.ScreenShare && CALL === here) { here.sharing = false; callBarDraw(); } });
  room.on(LK.RoomEvent.Disconnected, (reason: unknown) => {
    if (CALL !== here || here.leaving) return;
    if (reason === LK.DisconnectReason?.PARTICIPANT_REMOVED) CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), removed: true});
    callDone(here);
  });
  callBarDraw();
  try {
    await room.connect(j.url, j.token, {autoSubscribe: true});
  } catch (e) {
    if (CALL !== here) return;
    callDone(here);
    throw e;
  }
  // A newer join or a leave took over while this one connected: its room goes, and no microphone is turned on.
  if (CALL !== here || gen !== CALL_GEN) { try { room.disconnect(); } catch {} return; }
  try { await room.localParticipant.setMicrophoneEnabled(true); }
  catch { here.muted = true; toast('Microphone blocked'); }
  if (CALL !== here) { try { room.disconnect(); } catch {} return; }
  here.timer = setInterval(() => void callRefresh(here), CALL_REFRESH_MS);
  callBarDraw();
  callViewsDraw(threadId);
}
async function callRefresh(here: CallState): Promise<void> {
  if (CALL !== here) return;
  try {
    const r: {token?: string} | undefined = await post(callPath(here.id) + '/token', {});
    if (r?.token) here.token = r.token;
  } catch (e) {
    if (CALL !== here) return;
    if (errorStatus(e) === 403 || callCode(e) === 'call_access_removed') {
      CALL_THREADS.set(here.threadId, {...CALL_THREADS.get(here.threadId), removed: true});
      callDone(here);
    } else if (['call_ended', 'not_joined'].includes(callCode(e))) callDone(here);
  }
}
function callTrackOn(track: CallTrack, who: CallParticipant | undefined): void {
  const LK = window.LivekitClient;
  if (track.kind === 'audio') { $('#call-bar .call-audio')?.append(track.attach()); return; }
  // Video arrives only as someone's shared screen: nobody publishes a camera.
  if (track.source !== LK.Track.Source.ScreenShare) return;
  const box = $('#call-bar .call-screen');
  if (!box) return;
  const el = track.attach();
  el.setAttribute('aria-label', `${actorName(who?.identity || '') || 'Someone'}'s screen`);
  box.append(el);
  callScreenSync();
}
function callScreenSync(): void { const box = $('#call-bar .call-screen'); if (box) box.hidden = !box.querySelector('video'); }
// Leave always leaves. End must reach the server: when it does not, the call goes on, End stays and the error shows.
async function callLeave({end = false}: {end?: boolean} = {}): Promise<void> {
  const here = CALL; if (!here) return;
  here.leaving = true;
  if (end) {
    try { await post(callPath(here.id) + '/end', {}); }
    catch (e) {
      if (CALL !== here) return;
      here.leaving = false;
      toast(errorMessage(e, 'Could not end the call'), true);
      callBarDraw();
      return;
    }
  } else {
    await callPostLeave(here.id);
  }
  callSeatSettled(here.id);
  callDone(here);
}
function callDone(here: CallState): void {
  clearInterval(here.timer);
  try { here.room?.disconnect(); } catch {}
  if (CALL === here) CALL = null;
  const info = CALL_THREADS.get(here.threadId);
  if (info && !info.removed) CALL_THREADS.set(here.threadId, {...info, call: null});
  callBarDraw();
  for (const v of THV_VIEWS) if (v.id === here.threadId && !v.gone) void callLoad(v);
}

// ----------------------------------------------------------------- the bar
function callBarDraw(): void {
  let bar: HTMLElement | null = $('#call-bar');
  document.body.classList.toggle('in-call', !!CALL);
  if (!CALL) { bar?.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'call-bar'; bar.className = 'call-bar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Call');
    bar.innerHTML = '<div class="call-screen" hidden></div><div class="call-row"></div><div class="call-audio" hidden></div>';
    bar.addEventListener('click', ev => {
      const b = eventElement(ev)?.closest<HTMLElement>('[data-cb]');
      if (b) void callBarAct(b.dataset.cb ?? '', b);
    });
    document.body.append(bar);
  }
  const n = (CALL.room?.remoteParticipants?.size || 0) + 1, mine = CALL.connector === myActor();
  const btn = (k: string, icon: string, label: string, extra = '') => `<button type="button" class="ghost small call-btn${extra}" data-cb="${k}" aria-label="${esc(label)}" title="${esc(label)}"><span class="nav-icon" aria-hidden="true">${icon}</span><span class="call-lbl">${esc(label)}</span></button>`;
  const row = bar.querySelector<HTMLElement>('.call-row');
  if (row) patchHTML(row, `<a class="call-title" href="${threadHref(CALL.threadId)}">${esc(CALL.title)}</a>
    <span class="call-n muted tnum" title="In the call">${n}</span><span class="spacer"></span>
    ${btn('mute', CALL.muted ? CALL_ICONS.muted : CALL_ICONS.mic, CALL.muted ? 'Unmute' : 'Mute', CALL.muted ? ' on' : '')}
    ${callShareable() ? btn('share', CALL.sharing ? CALL_ICONS.stop : CALL_ICONS.share, CALL.sharing ? 'Stop sharing' : 'Share screen', CALL.sharing ? ' on' : '') : ''}
    ${btn('bot', CALL_ICONS.bot, 'Add bot')}
    ${btn('leave', CALL_ICONS.leave, 'Leave', ' danger')}${mine ? '<button type="button" class="ghost small call-btn danger" data-cb="end">End</button>' : ''}`);
}
async function callBarAct(k: string, button: HTMLElement): Promise<void> {
  const here = CALL; if (!here) return;
  const lp = here.room?.localParticipant;
  try {
    if (k === 'mute') { here.muted = !here.muted; await lp?.setMicrophoneEnabled(!here.muted); }
    else if (k === 'share') { here.sharing = !here.sharing; await lp?.setScreenShareEnabled(here.sharing, {audio: false}); }
    else if (k === 'leave') { await callLeave(); return; }
    else if (k === 'end') { if (await askConfirm('End the call for everyone?')) await callLeave({end: true}); return; }
    else if (k === 'bot') { callBotPick(button); return; }
  } catch (e) {
    if (k === 'share') here.sharing = false;
    if (k === 'mute') here.muted = !here.muted;
    if ((isErrorRecord(e) ? e.name : undefined) !== 'NotAllowedError') toast(errorMessage(e, 'Could not change that'));
  }
  callBarDraw();
}
// Bots the viewer can write to; the server checks again.
function callBotPick(button: HTMLElement): void {
  const bots = shownEmps().filter(e => e.status === 'active' && e.can_chat !== false && (!e.my_access || e.my_access.write) && !isAssistantHelper(e.name))
    .map((e): [string, string] => [e.id, e.display_name || e.slug || '']);
  if (!bots.length) { toast('No bots to add'); return; }
  thvMenuShow(button, bots, async slug => {
    if (!CALL) return;
    try { await post(callPath(CALL.id) + '/bots', {bots: [slug]}); toast(`${actorName('bot:' + slug)} joined`); }
    catch (e) { toast(errorMessage(e, 'Could not add the bot')); }
  });
}
// Every track this tab sends (microphone, screen; never a camera) stops now, before the room is told.
function callStopLocal(here: CallState): void {
  for (const pub of here.room?.localParticipant?.trackPublications?.values() ?? []) {
    try { pub.track?.stop?.(); } catch {}
    try { pub.track?.mediaStreamTrack?.stop?.(); } catch {}
  }
}
// The page goes out of sight: the call ends for this tab at once (its microphone and screen stop, its seat is given
// back), and a join still on its way stops before it turns anything on. Answers whether a call was on or on its way.
function callHidden(): {inCall: boolean} {
  const inCall = !!CALL || CALL_JOINS > 0;
  CALL_GEN++;
  const here = CALL;
  if (here) {
    here.leaving = true;
    callStopLocal(here);
    callSeatLeave(here.id);
    callDone(here);
  }
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
// A Google Meet call that nothing reports on (no Google status, no Recorder): Tico does not know who is in it, so it
// shows no count and no faces, only the call. Opening it from Tico is not being in it.
const callBlind = (c: CallRecord): boolean => c.provider === 'google_meet' && !c.status_source;
// Who is in it now: the status source's count while there is one; a Tico Meet call has none, so it is who joined.
function callCount(c: CallRecord | null | undefined): number {
  if (!c || callBlind(c)) return 0;
  if (c.status_source) return Number(c.live_count) || 0;
  return (c.participants || []).filter(p => p.joined_at && !p.left_at && !p.removed_at).length;
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
    else if (info && !info.call && v.state === 'live') CALL_THREADS.set(tid, {...info, call: v});
    callRailMark(tid, v);
    if (JSON.stringify(was) !== JSON.stringify(v)) callViewsDraw(tid);
  }
  if (typeof ringConsider === 'function') ringConsider(v);
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
// The Meet is made (or joined): its tab goes there, and the Recorder opens to record it.
function callMeetLanded(call: CallRecord, tab: Window | null, url: string): void {
  callViewKeep(call);
  callTabGo(tab, url || call.join_url || call.meet_uri || '');
  callRecorderOpen(call);
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
// Join from anywhere (the card, the ring): Google Meet in a tab, Tico Meet here. Runs its first step in the click.
function callJoinAny(call: CallRecord): Promise<void> {
  if (call.provider === 'google_meet') return callMeetJoin(call);
  const tid = call.thread_id || '';
  return callJoin(tid, call, threadTitle(threadsRow(tid)) || call.title || 'Call').catch(e => callNoted(tid, e));
}

// ----------------------------------------------------------------- the Recorder
// A Mac, and not an iPad asking for the desktop site: where Tico Recorder runs. Also the calendar's Join (calendar-row.ts).
function recorderMac(): boolean {
  const ua = (navigator as Navigator & {userAgentData?: {platform?: string}}).userAgentData;
  if (ua?.platform) return ua.platform === 'macOS';
  return /Mac/.test(navigator.platform || '') && !(navigator.maxTouchPoints > 1);
}
// Starting or joining a Google Meet call on a Mac opens the Tico Recorder on it, to record it into the call. The desktop
// app opens it itself (`recordCall`, no browser prompt; it answers false when no Recorder is installed); a browser follows
// the Recorder's link without leaving the page. An app without `recordCall` opens nothing: its `openRecorder` only opens
// the Recorder on the org.
function callRecorderOpen(call: CallRecord): void {
  if (call.provider !== 'google_meet' || !call.id) return;
  if (window.ticoNative) {
    if (nativeHas('recordCall')) try { void Promise.resolve(nativeHandler('recordCall')?.postMessage({call: call.id})).catch(() => {}); } catch {}
    return;
  }
  if (!call.recorder?.installed || !recorderMac()) return;
  recorderLinkOpen(`tico-recorder://record?server=${encodeURIComponent(location.origin)}&call=${encodeURIComponent(call.id)}`, 'call-recorder-link');
}
// Follows a `tico-recorder://record` link without leaving the page (a hidden frame, gone after 3 s); with no Recorder
// installed nothing happens. The Recorder checks the link with the org before it records anything.
function recorderLinkOpen(link: string, cls: string): void {
  const frame = document.createElement('iframe');
  frame.hidden = true; frame.className = cls; frame.src = link;
  document.body.append(frame);
  setTimeout(() => frame.remove(), 3000);
}

// ----------------------------------------------------------------- the card
// A call's card in its thread (a message with refs.call.card): live, the phone, who is in it, how long, Join, and End for
// whoever started a Google Meet; ended, how long it was and its transcript, or the missed mark for a Google Meet that
// Google or a Recorder watched and saw nobody in. The red dot says a Recorder is recording it. A Google Meet nothing reports on shows only the call.
function callCardHTML(m: ThvMessage): string {
  const id = String(m.refs?.call?.id || '');
  const v = id ? CALL_VIEWS.get(id) : undefined;
  if (id && !v && !CALL_VIEW_MISSING.has(id)) void callViewRead(id);
  const head = (cls: string, svg = CALL_SVG, label = '') => `<span class="call-card-ic${cls}"${label ? ` title="${label}" aria-label="${label}" role="img"` : ''}>${svg}</span><b class="call-card-t">Call</b>`;
  if (!v) return `<div class="call-card" data-call-card="${esc(id)}">${head('')}</div>`;
  const rec = v.recorder?.source ? '<span class="call-rec" title="Recording" aria-label="Recording"></span>' : '';
  if (v.state === 'live') {
    const blind = callBlind(v), faces = callFaces(v), since = blind ? '' : v.seen_at || v.started_at || '';
    const end = v.provider === 'google_meet' && v.connector && v.connector === myActor()
      ? `<button type="button" class="ghost small danger call-card-end" data-call-end="${esc(id)}">End</button>` : '';
    if (since) callTick();
    return `<div class="call-card on" data-call-card="${esc(id)}">${head(' on')}${
      faces.length ? `<span class="call-card-ppl">${faces.slice(0, 4).map(a => `<span title="${esc(actorName(a))}">${actorAvatar(a, 20)}</span>`).join('')}</span>` : ''}${
      since ? `<span class="call-card-n muted tnum" data-call-since="${esc(since)}">${esc(callClock(since))}</span>` : ''}${rec}
      <span class="spacer"></span>${end}<button type="button" class="primary small call-card-join" data-call-join="${esc(id)}">Join</button></div>`;
  }
  // Missed only when something watched the call (Google or a Recorder) and saw nobody; a call nothing watched just ended.
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
