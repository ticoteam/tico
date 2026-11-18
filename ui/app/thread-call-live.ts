/* ui/app/thread-call-live.ts — In a Tico Meet call: joining it, the LiveKit room, the call bar, leaving and ending.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first join, so a cold open of Home does not download it (P05b). The call
   state (CALL, CALL_GEN, CALL_JOINS), the header's phone button, Start and leaving the page stay in ui/app/thread-call.ts
   with Home. The meeting window (ui/app/call-window.ts) draws the same call: its screen box takes a shared screen, and
   each change here redraws it (callWindowDraw). What shows on the stage (a shared screen or a Tico file) is each
   person's own room attributes (CALL_SHOW): one thing at a time, the newest (callShown). */
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
  if (j.call?.id) callViewKeep(j.call);
  // Overtaken while joining: this seat is given back and the newer join goes on.
  const stale = (): boolean => { if (gen === CALL_GEN) return false; callSeatLeave(call.id); return true; };
  if (stale()) return;
  const LK = await callLib();
  if (stale()) return;
  const room = new LK.Room({adaptiveStream: false, dynacast: false});
  CALL = {id: call.id, threadId, title: title || call.title || 'Call', connector: (j.call || call).connector, room,
          token: j.token, url: j.url, muted: false, sharing: false, leaving: false, timer: 0, gen,
          showing: '', showingAt: 0};
  const here = CALL;
  room.on(LK.RoomEvent.TrackSubscribed, (track: CallTrack, _pub: unknown, who: CallParticipant | undefined) => { if (CALL === here) callTrackOn(track, who); });
  room.on(LK.RoomEvent.TrackUnsubscribed, (track: CallTrack) => { for (const el of track.detach()) el.remove(); callScreenSync(); });
  room.on(LK.RoomEvent.ParticipantConnected, callBarDraw);
  room.on(LK.RoomEvent.ParticipantDisconnected, callBarDraw);
  // The browser's own Stop sharing ends the share: the stage is cleared for everyone.
  room.on(LK.RoomEvent.LocalTrackUnpublished, (pub: CallPublication) => {
    if (pub.source !== LK.Track.Source.ScreenShare || CALL !== here) return;
    here.sharing = false;
    if (here.showing === 'screen') void callShow(here, '');
    callBarDraw();
  });
  // Someone showed something newer: this tab's own screen or file stops.
  if (LK.RoomEvent.ParticipantAttributesChanged) room.on(LK.RoomEvent.ParticipantAttributesChanged, () => { if (CALL === here) callShowSync(here); });
  // Who speaks, who is muted and whether the browser lets the call be heard: only the meeting window shows them.
  for (const event of [LK.RoomEvent.ActiveSpeakersChanged, LK.RoomEvent.TrackMuted, LK.RoomEvent.TrackUnmuted,
    LK.RoomEvent.AudioPlaybackStatusChanged, LK.RoomEvent.LocalTrackPublished]) if (event) room.on(event, () => { if (CALL === here) callWindowDraw(); });
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
  const box = callScreenBox();
  if (!box) return;
  const el = track.attach();
  el.setAttribute('aria-label', `${actorName(who?.identity || '') || 'Someone'}'s screen`);
  el.dataset.who = who?.identity || '';
  // When it arrived here: what orders a screen whose sharer does not say what they show (callShown).
  el.dataset.at = String(Date.now());
  box.append(el);
  if (CALL) callShowSync(CALL);
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
  const btn = (k: string, icon: string, label: string, extra = '') => `<button type="button" class="ghost small call-btn${extra}" data-cb="${k}" aria-label="${esc(label)}" title="${esc(label)}"><span class="nav-icon" aria-hidden="true">${icon}</span><span class="call-lbl">${esc(label)}</span></button>`;
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
  const lp = here.room?.localParticipant;
  try {
    if (k === 'mute') { here.muted = !here.muted; await lp?.setMicrophoneEnabled(!here.muted); }
    else if (k === 'share') {
      here.sharing = !here.sharing;
      await lp?.setScreenShareEnabled(here.sharing, {audio: false});
      if (here.sharing) await callShow(here, 'screen');
      else if (here.showing === 'screen') await callShow(here, '');
    }
    else if (k === 'leave') { await callLeave(); return; }
    else if (k === 'end') { if (await askConfirm('End the call for everyone?')) await callLeave({end: true}); return; }
    else if (k === 'bot') { callBotPick(button); return; }
    else if (k === 'window') { callWindowOpen(CALL_VIEWS.get(here.id) || {id: here.id, thread_id: here.threadId}); return; }
  } catch (e) {
    if (k === 'share') here.sharing = false;
    if (k === 'mute') here.muted = !here.muted;
    if ((isErrorRecord(e) ? e.name : undefined) !== 'NotAllowedError') toast(errorMessage(e, 'Could not change that'));
  }
  callBarDraw();
}
// ----------------------------------------------------------------- the stage
// What one person shows: `mine` for this tab. `at` orders them: the newest is on the stage for everyone.
interface CallShowing { who: string; what: string; at: number; mine: boolean; }
// Everything shown in the call now, newest first: each person's attributes, this tab's own, and any shared screen whose
// sharer's client does not say (at the time it arrived here).
function callShowings(here: CallState | null): CallShowing[] {
  if (!here) return [];
  const all: CallShowing[] = [];
  if (here.showing) all.push({who: here.room?.localParticipant?.identity || myActor(), what: here.showing, at: here.showingAt, mine: true});
  for (const p of here.room?.remoteParticipants?.values() ?? []) {
    const what = p.attributes?.[CALL_SHOW] || '';
    if (what) all.push({who: p.identity, what, at: Number(p.attributes?.[CALL_SHOW_AT]) || 0, mine: false});
  }
  for (const v of document.querySelectorAll<HTMLElement>('#cw-screen video, #call-bar .call-screen video')) {
    const who = v.dataset.who || '';
    if (who && !all.some(s => s.who === who)) all.push({who, what: 'screen', at: Number(v.dataset.at) || 0, mine: false});
  }
  return all.sort((a, b) => b.at - a.at);
}
const callShown = (here: CallState | null): CallShowing | null => callShowings(here)[0] || null;
// This tab shows `what` ('' stops): its attributes say so to the room. Its time is after anything already shown, so a
// clock that runs behind still puts the newest pick on top.
async function callShow(here: CallState, what: string): Promise<void> {
  const newest = callShowings(here).filter(s => !s.mine)[0]?.at || 0;
  here.showing = what;
  here.showingAt = what ? Math.max(Date.now(), newest + 1) : 0;
  callWindowDraw();
  try { await here.room?.localParticipant?.setAttributes?.({[CALL_SHOW]: what, [CALL_SHOW_AT]: what ? String(here.showingAt) : ''}); }
  catch { /* an older server's token cannot say it: only this tab shows it */ }
}
// Show a Tico file on the stage, in place of this tab's own screen: one thing at a time.
// The stage is told first, so the share's end (LocalTrackUnpublished) finds it already replaced.
async function callShowFile(id: string): Promise<void> {
  const here = CALL; if (!here || !id) return;
  await callShow(here, 'file:' + id);
  await callShareOff(here);
  callBarDraw();
}
async function callShareOff(here: CallState): Promise<void> {
  if (!here.sharing) return;
  here.sharing = false;
  try { await here.room?.localParticipant?.setScreenShareEnabled(false, {audio: false}); } catch {}
}
// Stop: this tab's screen or file leaves the stage.
async function callShowStop(): Promise<void> {
  const here = CALL; if (!here) return;
  await callShow(here, '');
  await callShareOff(here);
  callBarDraw();
}
// Someone else showed something after this tab: this tab's own screen or file stops.
function callShowSync(here: CallState): void {
  const top = callShown(here);
  if (here.showing && top && !top.mine) void callShowStop();
  callWindowDraw();
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
// The call moves to its meeting window (callWindowOpen): this tab's microphone and screen stop and its room goes, but its
// seat stays, for the window that joins next.
function callHandOff(here: CallState): void {
  here.leaving = true;
  callStopLocal(here);
  callSeatSettled(here.id);
  callDone(here);
}
