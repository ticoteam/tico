/* ui/app/thread-call-live.ts — In a Tico Meet call: joining it, the LiveKit room, the call bar, leaving and ending.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first join, so a cold open of Home does not download it (P05b). The call
   state (CALL, CALL_GEN, CALL_JOINS), the header's phone button, Start and leaving the page stay in ui/app/thread-call.ts
   with Home. */
'use strict';

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
// The page went out of sight while in the call (callHidden in ui/app/thread-call.ts): this tab's microphone and screen
// stop now, its seat is given back and the bar goes.
function callHangUp(here: CallState): void {
  here.leaving = true;
  callStopLocal(here);
  callSeatLeave(here.id);
  callDone(here);
}
