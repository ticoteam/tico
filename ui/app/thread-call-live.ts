/* ui/app/thread-call-live.ts — In a Tico Meet call: joining it, the LiveKit room, the call bar, leaving and ending.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first join, so a cold open of Home does not download it (P05b). The call
   state (CALL, CALL_GEN, CALL_JOINS), the header's phone button, Start and leaving the page stay in ui/app/thread-call.ts
   with Home. The meeting window (ui/app/call-window.ts) draws the same call: its screen box takes a shared screen, and
   each change here redraws it (callWindowDraw). What shows on the stage (a shared screen or a Tico file) is one record
   on the call, the call view's `stage` (docs/meet.md, Sharing): one thing at a time, the newest write. */
'use strict';

// `gen` is the join's number: Start call takes it at the click, so the one number runs through the whole join.
async function callJoin(threadId: string, call: CallRecord, title: string, gen = ++CALL_GEN, timing?: CallStartTiming): Promise<void> {
  CALL_JOINS++;
  CALL_JOINING.set(call.id, (CALL_JOINING.get(call.id) || 0) + 1);
  try { await callJoinNow(threadId, call, title, gen, timing); } finally {
    if (CALL?.startTiming !== timing) callStartReport(timing);
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
async function callJoinNow(threadId: string, call: CallRecord, title: string, gen: number, timing?: CallStartTiming): Promise<void> {
  if (CALL) await callLeave();
  if (gen !== CALL_GEN) return;
  while (CALL_LEAVING.has(call.id)) {
    await CALL_LEAVING.get(call.id);
    if (gen !== CALL_GEN) return;
  }
  const library = callLib();
  // A failed load is handled after the join settles, including giving back the seat it took.
  void library.catch(() => {});
  const j: CallJoinReply = await post(callPath(call.id) + '/join', {});
  if (j.token) callStartMark(timing, 'token');
  // Overtaken while joining: this seat is given back and the newer join goes on.
  const stale = (): boolean => { if (gen === CALL_GEN) return false; callSeatLeave(call.id); return true; };
  if (stale()) return;
  let LK: LivekitNamespace;
  try { LK = await library; }
  catch (e) { callSeatLeave(call.id); throw e; }
  if (stale()) return;
  if (j.call?.id) callViewKeep(j.call);
  const view = j.call || call;
  threadId ||= view.thread_id || '';
  const room = new LK.Room({adaptiveStream: false, dynacast: false});
  CALL = {id: call.id, threadId, title: title || view.title || 'Call', connector: view.connector, room,
          token: j.token, url: j.url, muted: true, sharing: false, leaving: false, timer: 0, gen,
          stageN: 0, staging: 0, reconnecting: false, connected: false, micPending: true, startTiming: timing};
  const here = CALL;
  room.on(LK.RoomEvent.TrackSubscribed, (track: CallTrack, _pub: unknown, who: CallParticipant | undefined) => { if (CALL === here) callTrackOn(track, who); });
  room.on(LK.RoomEvent.TrackUnsubscribed, (track: CallTrack) => { for (const el of track.detach()) el.remove(); callScreenSync(); });
  room.on(LK.RoomEvent.ParticipantConnected, () => {
    if (CALL !== here) return;
    callStartMark(timing, 'first-remote');
    callBarDraw();
  });
  room.on(LK.RoomEvent.ParticipantDisconnected, callBarDraw);
  // The browser's own Stop sharing ends the share, and this tab's screen leaves the stage. Not while LiveKit reconnects:
  // its unpublishing then is not a stop (callStageResync puts things right once it is back). A stop of this tab's own
  // (callShareOff) has already turned `sharing` off.
  room.on(LK.RoomEvent.LocalTrackUnpublished, (pub: CallPublication) => {
    if (pub.source !== LK.Track.Source.ScreenShare || CALL !== here || here.reconnecting || !here.sharing) return;
    here.sharing = false;
    if (callStageMine(here, 'screen')) void callStageClear(here);
    callBarDraw();
  });
  if (LK.RoomEvent.Reconnecting) room.on(LK.RoomEvent.Reconnecting, () => { if (CALL === here) here.reconnecting = true; });
  if (LK.RoomEvent.Reconnected) room.on(LK.RoomEvent.Reconnected, () => { if (CALL === here) void callStageResync(here); });
  // Who speaks, who is muted and whether the browser lets the call be heard: only the meeting window shows them.
  for (const event of [LK.RoomEvent.ActiveSpeakersChanged, LK.RoomEvent.TrackMuted, LK.RoomEvent.TrackUnmuted,
    LK.RoomEvent.AudioPlaybackStatusChanged, LK.RoomEvent.LocalTrackPublished]) if (event) room.on(event, () => { if (CALL === here) callWindowDraw(); });
  room.on(LK.RoomEvent.Disconnected, (reason: unknown) => {
    if (CALL !== here || here.leaving) return;
    if (reason === LK.DisconnectReason?.PARTICIPANT_REMOVED) CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), removed: true});
    if (!here.connected) callSeatLeave(here.id);
    callDone(here);
  });
  callBarDraw();
  const {localParticipant: local} = room;
  // Capture can wait on a permission prompt or the device. Start it with the connection and publish once connected.
  const mic = callMicAvailable() && LK.createLocalAudioTrack && local.publishTrack ? LK.createLocalAudioTrack().then(track => {
    if (CALL !== here || gen !== CALL_GEN || here.leaving) { track.stop(); return null; }
    here.capturedMic = track;
    return track;
  }) : null;
  void mic?.catch(() => {});
  try {
    await room.connect(j.url, j.token, {autoSubscribe: true});
  } catch (e) {
    if (CALL !== here) return;
    callSeatLeave(here.id);
    callDone(here);
    throw e;
  }
  // A newer join or a leave took over while this one connected: its room goes, and no microphone is turned on.
  if (CALL !== here || gen !== CALL_GEN || here.leaving) { try { room.disconnect(); } catch {} return; }
  here.connected = true;
  callStartMark(timing, 'connected');
  if (room.remoteParticipants?.size) callStartMark(timing, 'first-remote');
  here.timer = setInterval(() => void callRefresh(here), CALL_REFRESH_MS);
  callBarDraw();
  callViewsDraw(threadId);
  await callMicSet(here, true, mic);
  here.micPending = false;
  if (CALL === here && !here.leaving && !here.muted) callStartMark(timing, 'mic-published');
  if (CALL === here) callBarDraw();
  if (timing && !timing.reported) timing.timer = window.setTimeout(() => callStartReport(timing), 10000);
}
async function callMicSet(here: CallState, on: boolean, capture?: Promise<CallMic | null> | null): Promise<void> {
  if (on && !callMicAvailable()) { toast('Microphone not available in this window'); return; }
  try {
    const track = capture ? await capture : null;
    if (CALL !== here || here.leaving || (capture && here.gen !== CALL_GEN)) return;
    const local = here.room.localParticipant;
    if (track && local.publishTrack) {
      await local.publishTrack(track, {source: window.LivekitClient.Track.Source.Microphone});
      here.capturedMic = undefined;
    } else await local.setMicrophoneEnabled(on);
    here.muted = !on;
  } catch (e) {
    if (capture) {
      here.capturedMic?.stop();
      here.capturedMic = undefined;
    }
    if (CALL !== here || here.leaving) return;
    const name = isErrorRecord(e) ? e.name : undefined;
    if (name === 'NotAllowedError' || name === 'SecurityError') toast('Microphone blocked');
    else if (name === 'NotFoundError') toast('No microphone found');
    else toast(on ? 'Could not turn on the microphone' : 'Could not turn off the microphone');
  }
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
  const box = callScreenBox();
  if (!box) return;
  const el = track.attach();
  el.setAttribute('aria-label', `${actorName(who?.identity || '') || 'Someone'}'s screen`);
  el.dataset.who = who?.identity || '';
  box.append(el);
  callScreenSync();
}
// Where a shared screen shows: the meeting window's stage when this page is the window, else the bar's.
const callScreenBox = (): HTMLElement | null => $('#cw-screen') || $('#call-bar .call-screen');
function callScreenSync(): void {
  for (const box of document.querySelectorAll<HTMLElement>('#call-bar .call-screen, #cw-screen')) box.hidden = !box.querySelector('video');
  callWindowDraw();
}
// Leave always leaves. End must reach the server: when it does not, the call goes on, End stays and the error shows.
async function callLeave({end = false}: {end?: boolean} = {}): Promise<void> {
  const here = CALL; if (!here) return;
  here.leaving = true;
  if (end) {
    try { await post(callPath(here.id) + '/end', {}); }
    catch (e) {
      if (CALL !== here) return;
      here.leaving = false;
      toast(callErrorMessage(e, 'Could not end the call'), true);
      callBarDraw();
      return;
    }
  } else {
    here.capturedMic?.stop();
    here.capturedMic = undefined;
    await callPostLeave(here.id);
  }
  callSeatSettled(here.id);
  callDone(here);
}
function callDone(here: CallState): void {
  clearInterval(here.timer);
  here.capturedMic?.stop();
  here.capturedMic = undefined;
  callStartReport(here.startTiming);
  if (CALL === here) CALL = null;
  try { here.room?.disconnect(); } catch {}
  const info = CALL_THREADS.get(here.threadId);
  if (info && !info.removed) CALL_THREADS.set(here.threadId, {...info, call: null});
  callBarDraw();
  for (const v of THV_VIEWS) if (v.id === here.threadId && !v.gone) void callLoad(v);
}

// ----------------------------------------------------------------- the bar
function callBarDraw(): void {
  let bar: HTMLElement | null = $('#call-bar');
  document.body.classList.toggle('in-call', !!CALL);
  if (CALL) callStageSync(CALL);
  callWindowDraw();
  if (!CALL) { bar?.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'call-bar'; bar.className = 'call-bar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Call');
    bar.innerHTML = '<div class="call-screen" hidden></div><div class="call-voice" aria-live="polite" hidden></div><div class="call-row"></div><div class="call-audio" hidden></div>';
    bar.addEventListener('click', ev => {
      const b = eventElement(ev)?.closest<HTMLElement>('[data-cb]');
      if (b) void callBarAct(b.dataset.cb ?? '', b);
    });
    document.body.append(bar);
  }
  // The call's Tico voice (the bots speaking) is a participant of the room, never a person: the count is the call view's.
  const n = callCount(CALL_VIEWS.get(CALL.id)) || 1, mine = CALL.connector === myActor();
  callVoiceDraw(bar.querySelector<HTMLElement>('.call-voice'), CALL_VIEWS.get(CALL.id)?.voice);
  const btn = (k: string, icon: string, label: string, extra = '') => `<button type="button" class="ghost small call-btn${extra}" data-cb="${k}" aria-label="${esc(label)}" title="${esc(label)}"${k === 'mute' && CALL?.micPending ? ' disabled' : ''}><span class="nav-icon" aria-hidden="true">${icon}</span><span class="call-lbl">${esc(label)}</span></button>`;
  const row = bar.querySelector<HTMLElement>('.call-row');
  if (row) patchHTML(row, `<a class="call-title" href="${threadHref(CALL.threadId)}">${esc(CALL.title)}</a>
    <span class="call-n muted tnum" title="In the call">${n}</span><span class="spacer"></span>
    ${btn('mute', CALL.muted ? CALL_ICONS.muted : CALL_ICONS.mic, CALL.muted ? 'Unmute' : 'Mute', CALL.muted ? ' on' : '')}
    ${callShareable() ? btn('share', CALL.sharing ? CALL_ICONS.stop : CALL_ICONS.share, CALL.sharing ? 'Stop sharing' : 'Share screen', CALL.sharing ? ' on' : '') : ''}
    ${btn('bot', CALL_ICONS.bot, 'Add bot')}
    ${btn('window', CALL_ICONS.window, 'Open window')}
    ${btn('leave', CALL_ICONS.leave, 'Leave', ' danger')}${mine ? '<button type="button" class="ghost small call-btn danger" data-cb="end">End</button>' : ''}`);
}
// The bots' voice above the buttons: the bot answering, with dots while it thinks and its sentence while it speaks; a
// short note (a question missed, a repeat asked for) when no bot is answering.
function callVoiceDraw(box: HTMLElement | null, voice: CallVoiceView | null | undefined): void {
  if (!box) return;
  const busy = (voice?.bots || []).find(b => b.state === 'speaking') || (voice?.bots || []).find(b => b.state === 'thinking' || b.state === 'making_voice');
  let html = '';
  if (busy) {
    const name = esc(actorName('bot:' + busy.slug));
    html = busy.state === 'speaking' && busy.caption
      ? `${actorAvatar('bot:' + busy.slug, 18)}<span class="call-caption"><b>${name}</b> ${esc(busy.caption)}</span>`
      : `${actorAvatar('bot:' + busy.slug, 18)}<span class="typing" role="img" aria-label="${name} is thinking" title="${name}"><i></i><i></i><i></i></span>`;
  } else if (voice?.note) html = `<span class="call-caption muted">${esc(voice.note)}</span>`;
  box.hidden = !html;
  patchHTML(box, html);
}
async function callBarAct(k: string, button: HTMLElement): Promise<void> {
  const here = CALL; if (!here) return;
  try {
    if (k === 'mute') await callMicSet(here, here.muted);
    else if (k === 'share') { if (here.sharing) await callShowStop(); else await callShareStart(here); }
    else if (k === 'leave') { await callLeave(); return; }
    else if (k === 'end') { if (await askConfirm('End the call for everyone?')) await callLeave({end: true}); return; }
    else if (k === 'bot') { callBotPick(button); return; }
    else if (k === 'window') { callWindowOpen(CALL_VIEWS.get(here.id) || {id: here.id, thread_id: here.threadId}); return; }
  } catch (e) {
    toast(callErrorMessage(e, 'Could not change that'));
  }
  callBarDraw();
}
// ----------------------------------------------------------------- the stage
// What shows on the stage is one record on the call (docs/meet.md, Sharing): `PUT .../stage` shows this tab's screen or
// a Tico file in place of whatever showed, `DELETE` stops it, and every window follows the call view's `stage`.
// Whether the stage shows this person's own `kind` (any kind when it is not given).
function callStageMine(here: CallState, kind = ''): boolean {
  const stage = CALL_VIEWS.get(here.id)?.stage;
  return !!stage && stage.by === myActor() && (!kind || stage.kind === kind);
}
// One stage write. Its answer is the call view, kept at once; `stageN` is where this tab's own write left the stage, so
// an older read never stops the share it just started (callStageSync).
async function callStagePut(here: CallState, body: {kind: 'screen' | 'file'; file_id?: string}): Promise<boolean> {
  here.staging++;
  try {
    const v: CallRecord | null = await put(callPath(here.id) + '/stage', body);
    here.stageN = v?.stage_n ?? Number.MAX_SAFE_INTEGER;
    if (v?.id) callViewKeep(v);
    return true;
  } catch (e) {
    // An older server has no stage: the screen still reaches the room, and nothing here stops it.
    here.stageN = Number.MAX_SAFE_INTEGER;
    if (body.kind === 'file') toast(callErrorMessage(e, 'Could not show the file'), true);
    return false;
  } finally { here.staging--; }
}
async function callStageClear(here: CallState): Promise<void> {
  try {
    const v: CallRecord | null = await writeRequest('DELETE', callPath(here.id) + '/stage', {});
    if (v?.id) callViewKeep(v);
  } catch { /* the presenter's leave or the next write clears it */ }
}
// Share the screen: it reaches the room first, then the stage says it shows.
async function callShareStart(here: CallState): Promise<void> {
  if (!callShareable()) return;
  here.sharing = true;
  try { await here.room?.localParticipant?.setScreenShareEnabled(true, {audio: false}); }
  catch (e) {
    here.sharing = false;
    if (CALL === here && (isErrorRecord(e) ? e.name : undefined) !== 'NotAllowedError') toast('Could not share the screen');
    return;
  }
  if (CALL === here && here.sharing) await callStagePut(here, {kind: 'screen'});
}
// Show a Tico file in place of whatever showed, this tab's own screen too: one thing at a time.
async function callShowFile(id: string): Promise<void> {
  const here = CALL; if (!here || !id) return;
  if (await callStagePut(here, {kind: 'file', file_id: id})) await callShareOff(here);
  callBarDraw();
}
// This tab's screen share stops now: capture ends and the track is unpublished. The stage is not told here.
async function callShareOff(here: CallState): Promise<void> {
  if (!here.sharing) return;
  here.sharing = false;
  try { await here.room?.localParticipant?.setScreenShareEnabled(false, {audio: false}); } catch {}
}
// Stop: the screen stops at once, then this person's screen or file leaves the stage.
async function callShowStop(): Promise<void> {
  const here = CALL; if (!here) return;
  await callShareOff(here);
  if (callStageMine(here)) await callStageClear(here);
  callBarDraw();
}
// The stage moved on from this tab's screen (someone showed something newer, or the connector stopped it): the share
// stops. Only a read newer than this tab's own last write counts.
function callStageSync(here: CallState): void {
  if (!here.sharing || here.staging || here.reconnecting) return;
  const v = CALL_VIEWS.get(here.id);
  if (!v || (v.stage_n ?? 0) <= here.stageN || callStageMine(here, 'screen')) return;
  void callShareOff(here).then(callBarDraw);
}
// LiveKit is back after a reconnect: what this tab publishes is set against the stage as the server has it now. A share
// the reconnect lost leaves the stage; one the stage moved on from stops.
async function callStageResync(here: CallState): Promise<void> {
  const LK = window.LivekitClient;
  await callViewRead(here.id);
  here.reconnecting = false;
  if (CALL !== here) return;
  const published = [...here.room?.localParticipant?.trackPublications?.values() ?? []].some(p => p.source === LK?.Track.Source.ScreenShare && p.track);
  if (here.sharing && !published) {
    here.sharing = false;
    if (callStageMine(here, 'screen')) await callStageClear(here);
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
    catch (e) { toast(callErrorMessage(e, 'Could not add the bot')); }
  });
}
// Every track this tab sends (microphone, screen; never a camera) stops now, before the room is told.
function callStopLocal(here: CallState): void {
  for (const pub of here.room?.localParticipant?.trackPublications?.values() ?? []) {
    try { pub.track?.stop?.(); } catch {}
    try { pub.track?.mediaStreamTrack?.stop?.(); } catch {}
  }
}
// The page went out of sight while in the call (callHidden in ui/app/thread-call.ts): this tab's microphone and screen
// stop now, its seat is given back and the bar goes.
function callHangUp(here: CallState): void {
  here.leaving = true;
  callStopLocal(here);
  callSeatLeave(here.id);
  callDone(here);
}
// The call moves to its meeting window (callWindowOpen): this tab's microphone and screen stop and its room goes, but its
// seat stays, for the window that joins next.
function callHandOff(here: CallState): void {
  here.leaving = true;
  if (here.sharing && callStageMine(here, 'screen')) void callStageClear(here);
  callStopLocal(here);
  callSeatSettled(here.id);
  callDone(here);
}
