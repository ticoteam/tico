/* ui/app/call-ring.ts — A call ringing the viewer (docs/dev/meet-calls-contract.md, Ringing): on any page a card at the
   top right with the caller, the thread, Join and × (on a phone a sheet at the bottom with Dismiss and Join), a ring made
   with WebAudio for at most 30 seconds, and a browser notification while Tico is hidden. Rung calls come from the call
   views (`ring: true`: the viewer was rung, has not joined, and the call is live), read on each call.* change
   (ui/app/thread-call.ts callViewKeep) and from GET /v2/calls/ringing on page load and each reconnect. × is remembered
   in this browser; the call stays joinable from its thread. `#/thread/<id>?call=<call id>` (a push notification's link)
   shows the same card, silent, while the call is live.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RING_KEY = 'tico.calls.dismissed';
// The ring sounds for at most this long after a call first rings here.
const RING_MS = 30000;
// Call id -> its card on screen.
const RING_CARDS = new Map<string, HTMLElement>();
// Call id -> its browser notification.
const RING_NOTES = new Map<string, Notification>();
// Call ids the ring sounded for (a second ring for the same call stays quiet).
const RING_HEARD = new Set<string>();
const RING_SOUND: {ctx: AudioContext | null; timer: number; stop: number} = {ctx: null, timer: 0, stop: 0};

function ringDismissed(): string[] {
  try { const v: unknown = JSON.parse(localStorage.getItem(RING_KEY) || '[]'); return Array.isArray(v) ? v.map(String) : []; }
  catch { return []; }
}
function ringDismiss(id: string): void {
  try { localStorage.setItem(RING_KEY, JSON.stringify([...ringDismissed().filter(x => x !== id), id].slice(-50))); } catch { /* not kept */ }
  ringStop(id);
}
// A call view, newly read: it rings here, or stops ringing.
function ringConsider(v: CallRecord): void {
  if (!v?.id) return;
  if (v.state !== 'live') { ringGone(v.id); return; }
  if (!v.ring || ringDismissed().includes(v.id) || CALL?.id === v.id) {
    // A card shown for the thread's `?call=` stays until it is used or the call ends; a ring that stopped goes.
    if (!RING_CARDS.get(v.id)?.dataset.quiet) ringStop(v.id);
    return;
  }
  ringShow(v, false);
}
// The call ended or cannot be read: its card, sound and notification go.
function ringGone(id: string): void { ringStop(id); }
function ringStop(id: string): void {
  RING_CARDS.get(id)?.remove();
  RING_CARDS.delete(id);
  try { RING_NOTES.get(id)?.close(); } catch {}
  RING_NOTES.delete(id);
  if (!RING_CARDS.size) ringSoundOff();
}
function ringHost(): HTMLElement {
  let host = document.getElementById('call-ring');
  if (!host) {
    host = document.createElement('div');
    host.id = 'call-ring'; host.className = 'call-ring';
    host.addEventListener('click', ev => {
      const t = eventElement(ev), card = t?.closest<HTMLElement>('[data-ring]');
      if (card && t?.closest('[data-ring-dismiss]')) ringDismiss(card.dataset.ring || '');
    });
    document.body.append(host);
  }
  return host;
}
// The caller's thread as the card names it: "# name" for a thread the rail lists, a group chat's name, nothing for a DM.
function ringPlace(v: CallRecord): string {
  const t = threadsRow(v.thread_id);
  if (t && threadOnRail(t)) return `${threadHashHTML(t)}${esc(threadTitle(t))}`;
  return v.title && v.title !== 'Call' ? esc(v.title) : '';
}
function ringShow(v: CallRecord, quiet: boolean): void {
  const who = v.connector || '', place = ringPlace(v);
  let card = RING_CARDS.get(v.id);
  const html = `<div class="call-ring-who">${actorAvatar(who, 36)}<span class="call-ring-name"><b>${esc(actorName(who) || 'Call')}</b>${
      place ? `<span class="muted">${place}</span>` : ''}</span></div>
    <div class="call-ring-acts"><button type="button" class="ghost call-ring-dismiss" data-ring-dismiss>Dismiss</button>
      <button type="button" class="primary call-ring-join" data-call-join="${esc(v.id)}">Join</button>
      <button type="button" class="ghost call-ring-x" data-ring-dismiss aria-label="Dismiss" title="Dismiss"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>`;
  if (!card) {
    card = document.createElement('div');
    card.className = 'call-ring-card'; card.dataset.ring = v.id;
    card.setAttribute('role', 'alertdialog'); card.setAttribute('aria-label', 'Call');
    if (quiet) card.dataset.quiet = '1';
    ringHost().prepend(card);
    RING_CARDS.set(v.id, card);
  } else if (!quiet) delete card.dataset.quiet;
  patchHTML(card, html);
  // The thread's name may not be known yet (a thread not on the rail): it is read once, and the card fills it in.
  if (!place && v.thread_id && !threadsRow(v.thread_id)) void ringThreadName(v);
  if (quiet || RING_HEARD.has(v.id)) return;
  RING_HEARD.add(v.id);
  ringSoundOn();
  ringNotify(v);
}
async function ringThreadName(v: CallRecord): Promise<void> {
  const t = await get<ThreadRow>(thvPath(v.thread_id || ''), {fresh: false}).catch(() => null);
  const card = RING_CARDS.get(v.id);
  if (!t?.id || !card || !threadOnRail(t)) return;
  const name = card.querySelector('.call-ring-name');
  if (name && !name.querySelector('.muted')) name.insertAdjacentHTML('beforeend', `<span class="muted">${threadHashHTML(t)}${esc(threadTitle(t))}</span>`);
}
// While Tico is hidden and the browser lets it: a notification, which opens the thread.
function ringNotify(v: CallRecord): void {
  if (!document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return;
  const t = threadsRow(v.thread_id);
  try {
    const n = new Notification(actorName(v.connector) || 'Call', {body: t ? '# ' + threadTitle(t) : '', tag: 'call-' + v.id});
    n.onclick = () => {
      try { window.focus(); } catch {}
      location.hash = threadHref(v.thread_id || '') + '?call=' + encodeURIComponent(v.id);
      n.close();
    };
    RING_NOTES.set(v.id, n);
  } catch { /* a browser that refuses shows the card only */ }
}

// ----------------------------------------------------------------- the sound
// Two soft tones every three seconds, from the first ring until 30 seconds have passed or nothing rings.
function ringSoundOn(): void {
  if (RING_SOUND.timer) return;
  const Ctx = window.AudioContext || (window as Window & {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
  if (!Ctx) return;
  try { RING_SOUND.ctx ||= new Ctx(); } catch { return; }
  const ctx = RING_SOUND.ctx;
  void ctx.resume().catch(() => {});
  const chirp = () => {
    if (!RING_CARDS.size) { ringSoundOff(); return; }
    const at = ctx.currentTime;
    for (const [i, hz] of [[0, 660], [1, 880]] as const) {
      const osc = ctx.createOscillator(), gain = ctx.createGain(), start = at + i * 0.22;
      osc.type = 'sine'; osc.frequency.value = hz;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.12, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.2);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start); osc.stop(start + 0.21);
    }
  };
  chirp();
  RING_SOUND.timer = window.setInterval(chirp, 3000);
  RING_SOUND.stop = window.setTimeout(ringSoundOff, RING_MS);
}
function ringSoundOff(): void {
  clearInterval(RING_SOUND.timer); clearTimeout(RING_SOUND.stop);
  RING_SOUND.timer = 0; RING_SOUND.stop = 0;
}

// ----------------------------------------------------------------- what rings now
// Page load and every reconnect: the calls ringing the viewer now. Any card shown for a call no longer in it goes,
// unless it was opened from the thread's `?call=`.
async function ringingLoad(): Promise<void> {
  let r: {calls?: CallRecord[]} | null = null;
  try { r = await get<{calls?: CallRecord[]}>('/v2/calls/ringing', {fresh: true}); } catch { return; }
  const now = new Set((r?.calls || []).map(c => c.id));
  for (const [id, card] of RING_CARDS) if (!now.has(id) && !card.dataset.quiet) ringStop(id);
  for (const v of r?.calls || []) callViewKeep(v);
}
// `#/thread/<id>?call=<call id>`: the call's card, silent, while it is live, so Join is one tap.
async function ringRoute(): Promise<void> {
  const h = location.hash;
  if (!h.startsWith(THREAD_ROUTE)) return;
  const id = new URLSearchParams(h.split('?')[1] || '').get('call');
  if (!id) return;
  const v = await callViewRead(id);
  if (v?.state === 'live' && location.hash === h) ringShow(v, true);
}
liveOn('status', (d: {connected?: boolean}) => {
  if (!d?.connected) return;
  void ringingLoad();
  void ringRoute();
});
addEventListener('hashchange', () => { void ringRoute(); });
