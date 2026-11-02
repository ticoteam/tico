/* ui/app/thread-call.js — Calls in a thread (Tico Meet, docs/meet.md): Start call or Join in the thread's header, and
   one bar while in a call (mute, share screen, add bot, leave). Audio and screen share only: the camera is never
   asked for. The media client is LiveKit's (ui/vendor/livekit-client.umd.min.js, Apache-2.0), loaded on the first call.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const CALL_LIB = '/vendor/livekit-client.umd.min.js';
// Tokens last five minutes; a fresh one a minute early keeps a slow answer from dropping the call.
const CALL_REFRESH_MS = 4 * 60 * 1000;
// Per thread: {call} (the live call, or null), {off} when the server has calls switched off, {removed} after a removal,
// {none} when the server has no calls at all.
const CALL_THREADS = new Map();
let CALL = null;           // the call this tab is in: {id, threadId, title, connector, room, token, url, timer, muted, sharing}
let CALL_LIB_LOAD = null;
// Each join takes the next number; a join that a newer one overtook stops before it turns the microphone on.
let CALL_GEN = 0;
// Named here so scripts/build-icon-font.py keeps them in the icon subset.
const CALL_ICONS = {mic: 'mic', muted: 'mic_off', share: 'screen_share', stop: 'stop_screen_share', bot: 'smart_toy', leave: 'call_end'};

const callCode = e => (typeof e?.body?.error === 'object' ? e.body.error?.code : e?.body?.error) || '';
const callPath = id => '/v2/calls/' + encodeURIComponent(id);
const callShareable = () => !!navigator.mediaDevices?.getDisplayMedia;
function callViewsDraw(threadId) { for (const v of THV_VIEWS) if (v.id === threadId && !v.gone) thvDraw(v); }
function callNoted(threadId, e) {
  if (e?.status === 503) CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), off: true});
  else if (e?.status === 403 || callCode(e) === 'call_access_removed') CALL_THREADS.set(threadId, {...CALL_THREADS.get(threadId), removed: true});
  else { toast(e?.message || 'Could not join the call'); return; }
  callViewsDraw(threadId);
}

// ----------------------------------------------------------------- the header
async function callLoad(view) {
  const t = view.thread;
  if (!t || view.opts.header === false || !view.id) return;
  try {
    const r = await get(`/v2/threads/${encodeURIComponent(view.id)}/calls?limit=1`, {fresh: true});
    const live = (r.calls || []).find(c => c.state === 'live') || null;
    CALL_THREADS.set(view.id, {...CALL_THREADS.get(view.id), call: live, none: false});
  } catch (e) {
    // 404 from a server without calls; a call the viewer cannot read is the same answer.
    CALL_THREADS.set(view.id, e?.status === 503 ? {off: true} : {none: true});
  }
  callViewsDraw(view.id);
}
function callHeadHTML(view) {
  const t = view.thread, info = CALL_THREADS.get(view.id);
  if (!t || !info || info.none) return '';
  if (CALL?.threadId === view.id) return '<span class="thv-call-note thv-call-on" role="status">In call</span>';
  if (info.removed) return '<span class="thv-call-note muted">Removed from call</span>';
  if (info.off) return '<span class="thv-call-note muted">Calls are off</span>';
  const icon = '<span class="nav-icon" aria-hidden="true">call</span>';
  if (info.call) return `<button type="button" class="ghost small thv-call" data-call="join">${icon}Join</button>`;
  return t.me?.can_post ? `<button type="button" class="ghost small thv-call" data-call="start">${icon}Start call</button>` : '';
}
async function callAction(view, kind) {
  const id = view.id, info = CALL_THREADS.get(id) || {};
  try {
    let call = info.call;
    if (kind === 'start') {
      call = await post(`/v2/threads/${encodeURIComponent(id)}/calls`, {});
      call = call?.call || call;
    }
    if (call?.id) await callJoin(id, call, threadTitle(view.thread));
  } catch (e) {
    if (callCode(e) === 'call_in_progress') { void callLoad(view); toast('A call already started here'); return; }
    callNoted(id, e);
  }
}

// ----------------------------------------------------------------- in a call
function callLib() {
  if (window.LivekitClient) return Promise.resolve(window.LivekitClient);
  CALL_LIB_LOAD ||= new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = CALL_LIB; s.onload = () => ok(window.LivekitClient); s.onerror = () => { CALL_LIB_LOAD = null; fail(new Error('Could not load calls')); };
    document.head.append(s);
  });
  return CALL_LIB_LOAD;
}
async function callJoin(threadId, call, title) {
  const gen = ++CALL_GEN;
  if (CALL) await callLeave();
  if (gen !== CALL_GEN) return;
  const j = await post(callPath(call.id) + '/join', {});
  // Overtaken while joining: this seat is given back and the newer join goes on.
  const stale = () => { if (gen === CALL_GEN) return false; post(callPath(call.id) + '/leave', {}).catch(() => {}); return true; };
  if (stale()) return;
  const LK = await callLib();
  if (stale()) return;
  const room = new LK.Room({adaptiveStream: false, dynacast: false});
  CALL = {id: call.id, threadId, title: title || call.title || 'Call', connector: (j.call || call).connector, room,
          token: j.token, url: j.url, muted: false, sharing: false, leaving: false, timer: 0, gen};
  const here = CALL;
  room.on(LK.RoomEvent.TrackSubscribed, (track, pub, who) => { if (CALL === here) callTrackOn(track, who); });
  room.on(LK.RoomEvent.TrackUnsubscribed, track => { track.detach().forEach(el => el.remove()); callScreenSync(); });
  room.on(LK.RoomEvent.ParticipantConnected, callBarDraw);
  room.on(LK.RoomEvent.ParticipantDisconnected, callBarDraw);
  room.on(LK.RoomEvent.LocalTrackUnpublished, pub => { if (pub.source === LK.Track.Source.ScreenShare && CALL === here) { here.sharing = false; callBarDraw(); } });
  room.on(LK.RoomEvent.Disconnected, reason => {
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
async function callRefresh(here) {
  if (CALL !== here) return;
  try {
    const r = await post(callPath(here.id) + '/token', {});
    if (r?.token) here.token = r.token;
  } catch (e) {
    if (CALL !== here) return;
    if (e?.status === 403 || callCode(e) === 'call_access_removed') {
      CALL_THREADS.set(here.threadId, {...CALL_THREADS.get(here.threadId), removed: true});
      callDone(here);
    } else if (['call_ended', 'not_joined'].includes(callCode(e))) callDone(here);
  }
}
function callTrackOn(track, who) {
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
function callScreenSync() { const box = $('#call-bar .call-screen'); if (box) box.hidden = !box.querySelector('video'); }
// Leave always leaves. End must reach the server: when it does not, the call goes on, End stays and the error shows.
async function callLeave({end = false} = {}) {
  const here = CALL; if (!here) return;
  here.leaving = true;
  if (end) {
    try { await post(callPath(here.id) + '/end', {}); }
    catch (e) {
      if (CALL !== here) return;
      here.leaving = false;
      toast(e?.message || 'Could not end the call', true);
      callBarDraw();
      return;
    }
  } else {
    try { await post(callPath(here.id) + '/leave', {}); } catch {}
  }
  callDone(here);
}
function callDone(here) {
  clearInterval(here.timer);
  try { here.room?.disconnect(); } catch {}
  if (CALL === here) CALL = null;
  const info = CALL_THREADS.get(here.threadId);
  if (info && !info.removed) CALL_THREADS.set(here.threadId, {...info, call: null});
  callBarDraw();
  for (const v of THV_VIEWS) if (v.id === here.threadId && !v.gone) void callLoad(v);
}

// ----------------------------------------------------------------- the bar
function callBarDraw() {
  let bar = $('#call-bar');
  document.body.classList.toggle('in-call', !!CALL);
  if (!CALL) { bar?.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'call-bar'; bar.className = 'call-bar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Call');
    bar.innerHTML = '<div class="call-screen" hidden></div><div class="call-row"></div><div class="call-audio" hidden></div>';
    bar.addEventListener('click', ev => { const b = ev.target.closest('[data-cb]'); if (b) void callBarAct(b.dataset.cb, b); });
    document.body.append(bar);
  }
  const n = (CALL.room?.remoteParticipants?.size || 0) + 1, mine = CALL.connector === myActor();
  const btn = (k, icon, label, extra = '') => `<button type="button" class="ghost small call-btn${extra}" data-cb="${k}" aria-label="${esc(label)}" title="${esc(label)}"><span class="nav-icon" aria-hidden="true">${icon}</span><span class="call-lbl">${esc(label)}</span></button>`;
  patchHTML(bar.querySelector('.call-row'), `<a class="call-title" href="${threadHref(CALL.threadId)}">${esc(CALL.title)}</a>
    <span class="call-n muted tnum" title="In the call">${n}</span><span class="spacer"></span>
    ${btn('mute', CALL.muted ? CALL_ICONS.muted : CALL_ICONS.mic, CALL.muted ? 'Unmute' : 'Mute', CALL.muted ? ' on' : '')}
    ${callShareable() ? btn('share', CALL.sharing ? CALL_ICONS.stop : CALL_ICONS.share, CALL.sharing ? 'Stop sharing' : 'Share screen', CALL.sharing ? ' on' : '') : ''}
    ${btn('bot', CALL_ICONS.bot, 'Add bot')}
    ${btn('leave', CALL_ICONS.leave, 'Leave', ' danger')}${mine ? '<button type="button" class="ghost small call-btn danger" data-cb="end">End</button>' : ''}`);
}
async function callBarAct(k, button) {
  const here = CALL; if (!here) return;
  const lp = here.room?.localParticipant;
  try {
    if (k === 'mute') { here.muted = !here.muted; await lp?.setMicrophoneEnabled(!here.muted); }
    else if (k === 'share') { here.sharing = !here.sharing; await lp?.setScreenShareEnabled(here.sharing, {audio: false}); }
    else if (k === 'leave') { await callLeave(); return; }
    else if (k === 'end') { if (confirm('End the call for everyone?')) await callLeave({end: true}); return; }
    else if (k === 'bot') { callBotPick(button); return; }
  } catch (e) {
    if (k === 'share') here.sharing = false;
    if (k === 'mute') here.muted = !here.muted;
    if (e?.name !== 'NotAllowedError') toast(e?.message || 'Could not change that');
  }
  callBarDraw();
}
// Bots the viewer can write to; the server checks again.
function callBotPick(button) {
  const bots = shownEmps().filter(e => e.status === 'active' && e.can_chat !== false && (!e.my_access || e.my_access.write) && !isAssistantHelper(e.name))
    .map(e => [e.name, e.display_name || e.name]);
  if (!bots.length) { toast('No bots to add'); return; }
  thvMenuShow(button, bots, async slug => {
    try { await post(callPath(CALL.id) + '/bots', {bots: [slug]}); toast(`${actorName('bot:' + slug)} joined`); }
    catch (e) { toast(e?.message || 'Could not add the bot'); }
  });
}
// Leaving the page leaves the call, so nobody stays in a call they can no longer see.
addEventListener('pagehide', () => { if (CALL) { CALL.leaving = true; try { CALL.room?.disconnect(); } catch {} } });
