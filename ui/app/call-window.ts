/* ui/app/call-window.ts — A Tico Meet call's meeting window (#/call/<call id>, docs/meet.md "The meeting window"): a
   view of the meeting alone. The desktop app shows it in a small window of its own, a browser in a tab, a phone on the
   whole screen. Small by default: the title, Live and the time, one face per person and bot in the call (a ring on
   whoever speaks, a crossed-out mic on whoever is muted, a breathing ring on a bot that is thinking), one caption line,
   then Chat, Transcript and People, and the mic, share and Leave. Share is a small menu: Screen, or Show a Tico file
   (one of the company's files, drawn as the file view draws it). While something shows (someone else's screen, or a
   file) it grows to the wide layout, the stage beside the tabs, and goes back when it stops; one thing at a time, the
   call view's `stage` (ui/app/thread-call-live.ts). The chat's + links a file (its card) or a task (a task card). Once the call has ended the same
   view is its notes: who was in it, how long, the summary, the action items (Create makes each a task linked to the
   call) and the transcript.
   The window joins only when it was opened to (`?join=1`, set by Start, Join and the call's card): a plain visit to its
   address shows Join.
   A page part (ui/parts.json) with the in-call part as its dep: the call itself (the room, mute, share, leave) is
   ui/app/thread-call-live.ts, which redraws this view on each change (callWindowDraw).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A bot in the call as this view shows it: its slug, and (voice calls) what it is doing and its words now.
interface CwBot { slug: string; state: string; caption: string }
// The call view, its `voice` (null without voice) each bot's state and words, and a passing note.
type CwCall = CallRecord;
// One face in the header.
interface CwFace { actor: string; bot: boolean; on: boolean; speaking: boolean; muted: boolean; thinking: boolean }
// A piece of the live transcript (GET /v2/live-meetings/{id} `chunks`, and each meeting.chunk event).
interface CwChunk { seq: number; revision?: number; speaker?: string; text: string; start_ms?: number }
interface CwLive { chunks?: CwChunk[]; event_id?: number }
interface CwItem { title: string; owner: string }
interface CwNotes { pending?: boolean; summary?: string[]; items?: CwItem[] }
interface CwState {
  id: string; el: HTMLElement; tab: string; threadId: string; title: string; join: boolean;
  chat: ThvView | null; source: EventSource | null; meeting: string; after: number; eventId: number;
  chunks: Map<number, CwChunk>; bots: string[];
  // Bots taken out of the call here: their faces stay, faint, to be tapped back in.
  gone: Set<string>; busy: Set<string>;
  joinTried: boolean; missing: boolean; drawn: number; wide: boolean; size: [number, number] | null;
  // The notes, once the call has ended (null until read), and what was done with each item here.
  ended: boolean; notes: CwNotes | null; made: Map<string, string>; tq: string; tsearch: boolean; notesTries: number;
  task: '' | 'new' | 'link'; note: string; noteAt: number;
  // The share menu: closed, its two choices, or the file picker.
  pop: '' | 'menu' | 'file';
  // The file on the stage as drawn (`id@version`, or `none` for No access), and the abort of its load.
  fileKey: string; fileLoad: AbortController | null;
  // The tasks the chat's cards name, as read (null: not readable), once each.
  tasks: Map<string, CwTask | null>;
  // Keeps the chat on its newest message while the layout around it changes size.
  pin: ResizeObserver | null;
}
// A task a chat card names: what the card shows of it (GET /v2/tasks/{id}).
interface CwTask { id?: string; title?: string; status?: string; owner_id?: string | null }

let CW: CwState | null = null;
const CW_LIVE = '/v2/live-meetings/';
// Named here so scripts/build-icon-font.py keeps them in the icon subset.
const CW_ICONS = {add: 'add', search: 'search', close: 'close', copy: 'content_copy', team: 'groups', check: 'check', link: 'link',
  down: 'expand_more', open: 'open_in_new', mic: 'mic', muted: 'mic_off', share: 'screen_share', stop: 'stop_screen_share', newTask: 'add_circle',
  file: 'description', lock: 'lock', done: 'check_circle', todo: 'radio_button_unchecked'};
// The sound the browser held back until a tap (autoplay): a speaker, crossed out.
const CW_SOUND_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="m22 9-6 6M16 9l6 6"/></svg>';
// The shared screen on the whole phone: four corners.
const CW_FULL_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
// How many faces the header shows before "+N": two rows, whatever the roster (the controls never leave the screen).
const CW_FACES = {small: 8, phone: 7, wide: 14};
// How long a voice note stays under the caption.
const CW_NOTE_MS = 8000;
// A phone, not a narrow window on a computer: the meeting is the whole screen and goes back to its thread with ⌄.
const cwPhone = (): boolean => matchMedia('(max-width: 760px) and (pointer: coarse)').matches;
const cwIcon = (name: string): string => `<span class="nav-icon" aria-hidden="true">${name}</span>`;
// A name as this window says it: the viewer's own name, not "You".
// One toast at a time in the window: a new one replaces the one before (Undo, a ring, an error).
function cwToast(msg: string, isErr?: boolean, action?: ToastAction | null): void {
  for (const t of document.querySelectorAll('body > .toast')) t.remove();
  toast(msg, isErr, action);
}
const cwName = (actor: string): string => actor === myActor() ? S.me?.name || actorName(actor) : actorName(actor);
// A face: a photo when the person has one, else initials on their own colour, bots too.
function cwAv(actor: string, size: number): string {
  const pid = actorPerson(actor), person = pid ? (S.people || []).find(p => p.id === pid) : null;
  return person && personPhotoUrl(person) ? personAvatar(person, size) : personCircle(cwName(actor), size);
}

// ----------------------------------------------------------------- the page
function pageCallWindow(id: string): void {
  const join = /[?&]join=1(?:&|$)/.test(location.hash);
  // The flag is used once: a reload or a copied address shows Join.
  if (join) history.replaceState(history.state, '', location.pathname + location.search + CALL_ROUTE + encodeURIComponent(id));
  if (CW?.id === id && CW.el.isConnected) {
    if (join && !CW.join) { CW.join = true; void cwJoin(CW); }
    callWindowDraw(); return;
  }
  callWindowStop();
  document.body.classList.add('call-page');
  pageTitle('Call');
  const main = $('#main'); if (main) main.innerHTML = '';
  const el = document.createElement('section');
  el.id = 'call-window'; el.className = 'cw'; el.setAttribute('aria-label', 'Call');
  el.innerHTML = `<header class="cw-head">
      <div class="cw-top"><div class="cw-titles"><h1 class="cw-title"></h1><div class="cw-meta tnum"></div></div><div class="cw-tools"></div></div>
      <div class="cw-faces" role="list" aria-label="In the call"></div>
      <div class="cw-caption cw-caption-head" aria-live="polite"></div>
    </header>
    <div class="cw-body">
      <div class="cw-stage"><div id="cw-screen" class="cw-screen" hidden></div><div id="cw-file" class="cw-file" hidden></div>
        <button type="button" class="cw-full" data-cw="full" aria-label="Full screen" title="Full screen"><span>${CW_FULL_SVG}</span></button>
        <div class="cw-caption cw-caption-stage" aria-hidden="true"></div></div>
      <div class="cw-side">
        <div class="cw-tabs" role="tablist" aria-label="Call"></div>
        <div class="cw-pane cw-chat" data-pane="chat" role="tabpanel"><div class="cw-task" hidden></div><div class="cw-thread"></div></div>
        <div class="cw-pane cw-transcript" data-pane="transcript" role="tabpanel" hidden><p class="cw-empty muted">Nothing said yet</p></div>
        <div class="cw-pane cw-people" data-pane="people" role="tabpanel" hidden>
          <label class="cw-find">${cwIcon(CW_ICONS.search)}<input type="search" data-cw-q aria-label="Add people or bots" placeholder="Add people or bots" autocomplete="off"></label>
          <div class="cw-people-list"></div></div>
      </div>
    </div>
    <div class="cw-after" hidden></div>
    <div class="cw-pop" role="dialog" aria-label="Share" hidden></div>
    <footer class="cw-bar"></footer>`;
  document.body.append(el);
  const st: CwState = {id, el, tab: 'chat', threadId: '', title: '', join, chat: null, source: null, meeting: '', after: 0, eventId: 0,
    chunks: new Map(), bots: [], gone: new Set(), busy: new Set(), joinTried: false, missing: false, drawn: 0, wide: false, size: null,
    ended: false, notes: null, made: new Map(), tq: '', tsearch: false, notesTries: 0, task: '', note: '', noteAt: 0,
    pop: '', fileKey: '', fileLoad: null, tasks: new Map(), pin: null};
  CW = st;
  el.addEventListener('click', ev => { void cwClick(st, ev); });
  el.addEventListener('input', ev => {
    const t = eventElement(ev);
    if (t?.matches('[data-cw-q]')) cwPeopleDraw(st);
    else if (t?.matches('[data-cw-tq]') && t instanceof HTMLInputElement) { st.tq = t.value; cwAfterTranscript(st); }
  });
  el.addEventListener('submit', ev => { ev.preventDefault(); const f = eventElement(ev); if (f instanceof HTMLFormElement) void cwTaskSubmit(st, f); });
  // A screen shared before this view drew (the call was joined in the thread first) moves into the stage.
  const screen = $('#cw-screen');
  for (const v of document.querySelectorAll('#call-bar .call-screen video')) screen?.append(v);
  callWindowDraw();
  void cwLoad(st);
}
// Leaving the page: the view and what it follows go. The call goes on (its bar shows) until it is left.
function callWindowStop(): void {
  const st = CW; if (!st) return;
  CW = null;
  st.source?.close();
  clearTimeout(st.after); clearTimeout(st.drawn);
  st.fileLoad?.abort();
  st.pin?.disconnect();
  threadUnmount(st.chat);
  // A shared screen goes back to the bar.
  const bar = $('#call-bar .call-screen');
  for (const v of st.el.querySelectorAll<HTMLVideoElement>('#cw-screen video')) { v.hidden = false; bar?.append(v); }
  st.el.remove();
  document.body.classList.remove('call-page', 'cw-wide-page', 'cw-composer');
  if (CALL) callScreenSync();
}
async function cwLoad(st: CwState): Promise<void> {
  const v = await callViewRead(st.id);
  if (CW !== st) return;
  if (!v) { st.missing = true; callWindowDraw(); return; }
  st.threadId = v.thread_id || '';
  st.title = threadTitle(threadsRow(st.threadId)) || v.title || 'Call';
  st.meeting = v.meeting_id || '';
  // A Google Meet is in Google's own tab: this address opens its thread.
  if (v.provider === 'google_meet') { location.replace(threadHref(st.threadId)); return; }
  void cwMembers(st);
  if (v.state === 'live') {
    cwChatMount(st);
    if (st.meeting) void cwLiveRead(st, true);
    await cwJoin(st);
  }
  callWindowDraw();
}
// Join, once, when the window was opened to.
async function cwJoin(st: CwState): Promise<void> {
  const v = cwView(st);
  if (!st.join || st.joinTried || !v || v.state !== 'live' || CALL?.id === v.id) return;
  st.joinTried = true;
  await callJoin(st.threadId, v, st.title).catch((e: unknown) => callNoted(st.threadId, e));
}
// The bots of the call's thread: faint faces to tap into the call.
async function cwMembers(st: CwState): Promise<void> {
  if (!st.threadId) return;
  try {
    const r = await get<{members?: (string | {actor?: string})[]}>(thvPath(st.threadId) + '/members?limit=50');
    if (CW !== st) return;
    st.bots = (r?.members || []).map(m => typeof m === 'string' ? m : m?.actor || '').filter(a => a.startsWith('bot:'));
    callWindowDraw();
  } catch { /* a thread whose members cannot be read: only the bots in the call show */ }
}
// The transcript: read once, then each event of the call's live meeting is applied as it comes (a new line, a
// corrected one), from where the read left off. `follow`: then follow it.
async function cwLiveRead(st: CwState, follow: boolean): Promise<void> {
  try {
    const d = await get<CwLive>(CW_LIVE + encodeURIComponent(st.meeting), {fresh: true});
    if (CW !== st) return;
    for (const c of d?.chunks || []) cwChunkPut(st, c);
    st.eventId = Number(d?.event_id) || 0;
    callWindowDraw();
  } catch { /* not readable yet (the join adds the reader): the stream reads it from the start */ }
  if (follow && !st.ended) cwFollow(st);
}
function cwFollow(st: CwState): void {
  if (st.source || !st.meeting || typeof EventSource === 'undefined') return;
  const source = new EventSource(API + CW_LIVE + encodeURIComponent(st.meeting) + '/events?after=' + st.eventId);
  st.source = source;
  const data = (ev: Event): Record<string, unknown> => {
    try { const d: unknown = JSON.parse(String((ev as MessageEvent).data || '{}')); return d && typeof d === 'object' ? d as Record<string, unknown> : {}; }
    catch { return {}; }
  };
  const chunk = (ev: Event): void => {
    const d = data(ev);
    if (CW !== st || typeof d.seq !== 'number') return;
    cwChunkPut(st, {seq: d.seq, revision: Number(d.revision) || 1, speaker: String(d.speaker || ''), text: String(d.text || ''),
      ...(typeof d.start_ms === 'number' ? {start_ms: d.start_ms} : {})});
    callWindowDraw();
  };
  source.addEventListener('meeting.chunk', chunk);
  source.addEventListener('meeting.chunk_corrected', chunk);
  // Who is in it and whether it goes on: the call's own view says.
  for (const type of ['meeting.bot_joined', 'meeting.bot_left', 'meeting.state', 'meeting.finalized'])
    source.addEventListener(type, () => { if (CW === st) liveSoon('call-view:' + st.id, () => { void callViewRead(st.id); }, 200); });
  source.addEventListener('expired', () => { source.close(); if (st.source === source) st.source = null; });
}
// One piece of the transcript, new or corrected (a later revision wins), drawn in place: no list is drawn again.
function cwChunkPut(st: CwState, c: CwChunk): void {
  const had = st.chunks.get(c.seq);
  if (had && Number(had.revision || 1) > Number(c.revision || 1)) return;
  st.chunks.set(c.seq, c);
  const pane = st.el.querySelector<HTMLElement>('.cw-transcript'); if (!pane) return;
  const near = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
  pane.querySelector('.cw-empty')?.remove();
  const row = document.createElement('div');
  row.innerHTML = cwTurnHTML(c);
  const turn = row.firstElementChild; if (!turn) return;
  const old = pane.querySelector(`.cw-turn[data-seq="${c.seq}"]`);
  if (old) old.replaceWith(turn);
  else {
    const after = [...pane.querySelectorAll<HTMLElement>('.cw-turn')].find(t => Number(t.dataset.seq) > c.seq);
    if (after) after.before(turn); else pane.append(turn);
  }
  if (near) pane.scrollTop = pane.scrollHeight;
}
const cwSaid = (st: CwState): CwChunk[] => [...st.chunks.values()].sort((a, b) => a.seq - b.seq).filter(c => c.text.trim());
function cwChatMount(st: CwState): void {
  if (st.chat || !st.threadId) return;
  const host = st.el.querySelector<HTMLElement>('.cw-thread'); if (!host) return;
  st.chat = threadMount(host, {id: st.threadId, header: false, reach: false, placeholder: () => 'Message',
    // The call's own posts (its card, transcript and notes) are not part of its chat: they are the tabs and the notes.
    hide: m => !!m.refs?.call && !(m.refs as {live_meeting?: {chat_id?: string}}).live_meeting?.chat_id,
    attachMore: {plus: true, items: [['link', 'Link a task', CW_ICONS.done], ['new', 'New task', CW_ICONS.newTask]], pick: k => { st.task = k === 'new' ? 'new' : 'link'; cwTaskDraw(st); }},
    // Link a file posts it at once: its link, which the chat draws as the file's card.
    libPick: f => { if (st.chat) void thvSend(st.chat, {text: `[${f.title.replace(/[[\]]/g, '')}](${API}/v2/files/${encodeURIComponent(f.id)})`}); },
    body: m => cwTaskBody(st, m)});
  // The newest message stays in view while the chat's box changes size (a stage opening above it, the phone's peek):
  // pinned to the end while it was at the end.
  const box = host.querySelector<HTMLElement>('.thv-scroll');
  if (!box || !window.ResizeObserver) return;
  let end = true;
  box.addEventListener('scroll', () => { end = box.scrollHeight - box.scrollTop - box.clientHeight < 8; }, {passive: true});
  st.pin = new ResizeObserver(() => { if (end) box.scrollTop = box.scrollHeight; });
  st.pin.observe(box);
}

// ----------------------------------------------------------------- reading the call
const cwView = (st: CwState): CwCall | null => CALL_VIEWS.get(st.id) || null;
const cwHere = (st: CwState): CallState | null => CALL?.id === st.id ? CALL : null;
// The bots in the call (`bots`: slugs, or bots with their state from an older voice view), each with what it is doing
// now (`voice.bots`).
function cwBots(v: CwCall | null): CwBot[] {
  const said = new Map<string, CallBotRaw>();
  for (const b of [...(v?.bots || []), ...(v?.voice?.bots || [])]) if (b && typeof b === 'object' && b.slug) said.set(b.slug, {...said.get(b.slug), ...b});
  return (v?.bots || []).map(b => typeof b === 'string' ? b : String(b?.slug || '')).filter(Boolean).map((slug): CwBot => {
    const b = said.get(slug);
    return {slug, state: String(b?.state || 'listening'), caption: String(b?.caption || '')};
  });
}
// Who said a piece of the transcript: its speaker is a person's id, or a bot's slug.
const cwSpeaker = (speaker: string | undefined): string => {
  const s = String(speaker || '');
  if (!s) return '';
  if (s.includes(':')) return s;
  return (S.emps || []).some(e => e.id === s || e.slug === s) ? 'bot:' + s : 'human:' + s;
};
// The faces: the people the call's view lists (only those the viewer may see) and its bots. The room only says who of
// them speaks and who is muted; it never adds a face.
function cwFaces(st: CwState, v: CwCall | null): CwFace[] {
  const faces = cwFacesHeard(st, v), who = v?.state === 'live' ? cwCaptionWho(st, v) : null;
  // One state per face, and the ring is the caption's: the face whose words show (while they speak) has it, nobody else.
  return faces.map(f => ({...f, speaking: !!who?.talking && f.actor === who.actor && !f.muted, thinking: f.thinking && !f.muted}));
}
function cwFacesHeard(st: CwState, v: CwCall | null): CwFace[] {
  const here = cwHere(st), room = here?.room, me = myActor(), faces = new Map<string, CwFace>(), live = v?.state === 'live';
  const remote = new Map<string, CallParticipant>();
  for (const p of room?.remoteParticipants?.values() ?? []) remote.set(p.identity, p);
  const person = (actor: string): void => {
    if (!actor || actor.startsWith('bot:') || faces.has(actor)) return;
    const p = remote.get(actor), mine = actor === me && !!here;
    const muted = mine ? here.muted : p?.isMicrophoneEnabled === false;
    // Muted wins: a muted mic shows no ring, whatever the room hears.
    faces.set(actor, {actor, bot: false, on: true, muted, thinking: false,
      speaking: !muted && (mine ? !!room?.localParticipant.isSpeaking : !!p?.isSpeaking)});
  };
  if (here) person(me);
  // While live, who is in it now; after, everyone who was.
  for (const p of v?.participants || []) if (p.joined_at && (!live || (!p.left_at && !p.removed_at))) person(p.actor);
  const bots = cwBots(v), inCall = new Set(bots.map(b => b.slug));
  for (const b of bots) faces.set('bot:' + b.slug, {actor: 'bot:' + b.slug, bot: true, on: true, speaking: b.state === 'speaking',
    muted: false, thinking: b.state === 'thinking' || b.state === 'making_voice'});
  if (live) for (const a of [...[...st.gone].map(s => 'bot:' + s), ...st.bots]) {
    const slug = actorSlug(a);
    if (slug && !inCall.has(slug) && !faces.has(a) && cwMayAdd(slug)) faces.set(a, {actor: a, bot: true, on: false, speaking: false, muted: false, thinking: false});
  }
  // After the call: the bots that spoke or took an item on were in it, too.
  if (!live) for (const a of [...cwSaid(st).map(c => cwSpeaker(c.speaker)), ...(st.notes?.items || []).map(i => i.owner)])
    if (a.startsWith('bot:') && !faces.has(a)) faces.set(a, {actor: a, bot: true, on: true, speaking: false, muted: false, thinking: false});
  return [...faces.values()];
}
// Who is in the call, as the tab, the People list and the header count it: people and bots in it, not the bots tapped out.
const cwCount = (faces: CwFace[]): number => faces.filter(f => f.on).length;
// Bots the viewer may put in a call (the server checks again): active, chattable, writable, not the Assistant's helpers.
const cwAddable = () => shownEmps().filter(e => e.status === 'active' && e.can_chat !== false && (!e.my_access || e.my_access.write) && !isAssistantHelper(e.name));
const cwMayAdd = (slug: string): boolean => cwAddable().some(e => e.id === slug || e.slug === slug);
// What the stage shows: the call view's `stage`. Someone else's screen once it arrives here, or a Tico file, anyone's;
// one at a time. A screen with no stage record (an older server, or a share the stage has not heard of yet) still
// shows. Who shows it, '' for nothing.
function cwStageDraw(st: CwState, live: boolean): string {
  const v = cwView(st), stage = live ? v?.stage || null : null, me = myActor();
  const screen = st.el.querySelector<HTMLElement>('#cw-screen'), file = st.el.querySelector<HTMLElement>('#cw-file');
  const videos = [...screen?.querySelectorAll<HTMLVideoElement>('video') ?? []];
  const who = !live || stage?.kind === 'file' ? '' : stage ? (stage.by === me ? '' : stage.by) : videos.at(-1)?.dataset.who || '';
  let video = false;
  for (const el of videos) { el.hidden = !who || el.dataset.who !== who || video; video ||= !el.hidden; }
  if (screen) screen.hidden = !video;
  const shown = stage?.kind === 'file' ? stage : null;
  if (file) {
    file.hidden = !shown;
    if (shown) cwFileDraw(st, file, shown);
    else if (st.fileKey) { st.fileLoad?.abort(); st.fileLoad = null; st.fileKey = ''; file.replaceChildren(); }
  }
  return video ? who : shown ? shown.by : '';
}
// A Tico file on the stage: its title over it, then that version as the file view draws it (fileShowInto). Drawn again
// only when the stage names another file or version; a load the next one replaced is aborted. "No access" for someone
// who may not read it: the view names nothing of it, so nothing is fetched.
function cwFileDraw(st: CwState, box: HTMLElement, stage: CallStageView): void {
  const by = `${cwAv(stage.by, 18)}<span>${esc(cwName(stage.by))}</span>`;
  const id = stage.no_access ? '' : stage.file_id || '';
  const key = id ? `${id}@${stage.version || 0}` : 'none';
  if (st.fileKey === key) { const b = box.querySelector<HTMLElement>('.cw-file-by'); if (b) patchHTML(b, by); return; }
  st.fileLoad?.abort();
  st.fileKey = key;
  const head = (title: string) => `<div class="cw-file-head"><span class="cw-file-t">${esc(title)}</span><span class="cw-file-by">${by}</span></div>`;
  if (!id) {
    st.fileLoad = null;
    box.innerHTML = `${head('')}<div class="cw-file-body"><div class="cw-noaccess muted">${cwIcon(CW_ICONS.lock)}<span>No access</span></div></div>`;
    return;
  }
  const load = new AbortController();
  st.fileLoad = load;
  box.innerHTML = `${head(stage.title || '')}<div class="cw-file-body"><div class="frv-preview" aria-busy="true"><div class="frv-skel"></div></div></div>`;
  const body = box.querySelector<HTMLElement>('.cw-file-body > div'), title = box.querySelector<HTMLElement>('.cw-file-t');
  if (!body || !title) return;
  const current = (): boolean => CW === st && st.fileLoad === load && !load.signal.aborted;
  void Promise.resolve(fileShowInto(body, id, stage.version || null, load.signal)).then(info => {
    if (!current()) return;
    if (!info) { body.className = 'cw-noaccess muted'; body.removeAttribute('aria-busy'); body.innerHTML = `${cwIcon(CW_ICONS.lock)}<span>No access</span>`; return; }
    if (!title.textContent) title.textContent = info.title || info.name || 'File';
  }, () => { if (current()) { body.className = 'cw-noaccess muted'; body.removeAttribute('aria-busy'); body.innerHTML = '<span>Could not open the file</span>'; } });
}

// ----------------------------------------------------------------- drawing
// Many changes come at once (speakers, mutes, the call's view): one draw for them all.
function callWindowDraw(): void {
  const st = CW; if (!st || st.drawn) return;
  st.drawn = window.setTimeout(() => { st.drawn = 0; if (CW === st) cwDraw(st); }, 0);
}
function cwDraw(st: CwState): void {
  const v = cwView(st), el = st.el, live = v?.state === 'live', ended = !!v && !live;
  // Live to ended while open: the chat and the stream stop and the notes are read.
  if (ended && !st.ended) { st.ended = true; void cwAfterLoad(st); }
  el.classList.toggle('cw-ended', ended);
  el.classList.toggle('cw-missing', st.missing);
  const sharer = cwStageDraw(st, live);
  cwWide(st, !!sharer);
  const title = el.querySelector<HTMLElement>('.cw-title');
  const name = st.title || v?.title || (st.missing ? 'Call not found' : 'Call');
  if (title && title.textContent !== name) { title.textContent = name; pageTitle(name); }
  const meta = el.querySelector<HTMLElement>('.cw-meta');
  if (meta) {
    const since = v?.started_at || '';
    if (live && since) callTick();
    const mine = sharer === myActor() || sharer === cwHere(st)?.room?.localParticipant?.identity;
    const sharing = sharer && !mine ? `${actorName(sharer)} sharing` : sharer || cwHere(st)?.sharing ? 'You are sharing' : '';
    patchHTML(meta, live ? `<span class="cw-live-dot" aria-hidden="true"></span>Live${since ? ` · <span data-call-since="${esc(since)}">${esc(callClock(since))}</span>` : ''}${sharing ? ` · ${esc(sharing)}` : ''}`
      : ended ? esc(cwWhen(v)) : '');
  }
  const tools = el.querySelector<HTMLElement>('.cw-tools');
  if (tools) patchHTML(tools, ended
    ? `${st.threadId ? `<a class="cw-icon" href="${esc(threadHref(st.threadId))}" data-cw="thread" aria-label="Open thread" title="Open thread">${cwIcon(CW_ICONS.open)}</a>` : ''}<button type="button" class="cw-icon" data-cw="close" aria-label="Close" title="Close">${cwIcon(CW_ICONS.close)}</button>`
    : `${v ? `<button type="button" class="cw-icon" data-cw="copy-link" aria-label="Copy link" title="Copy link">${cwIcon(CW_ICONS.link)}</button>` : ''}${
      cwPhone() && st.threadId ? `<button type="button" class="cw-icon" data-cw="down" aria-label="Back to the thread" title="Back to the thread">${cwIcon(CW_ICONS.down)}</button>` : ''}`);
  const faces = cwFaces(st, v), facesEl = el.querySelector<HTMLElement>('.cw-faces');
  if (facesEl) patchHTML(facesEl, cwFacesHTML(st, faces, live, ended));
  const caption = live ? cwCaptionHTML(st, v) : '';
  for (const c of el.querySelectorAll<HTMLElement>('.cw-caption')) { patchHTML(c, caption); c.hidden = !caption; }
  // A toast sits above the chat's field while it shows (ui/styles/call-window.css).
  document.body.classList.toggle('cw-composer', !ended && st.tab === 'chat' && !st.wide);
  if (ended) {
    for (const part of el.querySelectorAll<HTMLElement>('.cw-tabs, .cw-bar')) part.replaceChildren();
    cwAfterDraw(st); return;
  }
  const tabs = el.querySelector<HTMLElement>('.cw-tabs');
  const people = cwCount(faces);
  if (tabs) patchHTML(tabs, ([['chat', 'Chat'], ['transcript', 'Transcript'], ['people', 'People']] satisfies [string, string][]).map(([k, label]) =>
    `<button type="button" role="tab" class="cw-tab" data-cw-tab="${k}" aria-selected="${st.tab === k}">${label}${k === 'people' && people ? ` <span class="cw-n tnum">${people}</span>` : ''}</button>`).join(''));
  for (const pane of el.querySelectorAll<HTMLElement>('.cw-pane')) pane.hidden = pane.dataset.pane !== st.tab;
  if (st.tab === 'people') cwPeopleDraw(st);
  cwBarDraw(st, v);
}
// The header's faces, two rows at most: the rest are "+N", which opens People.
function cwFacesHTML(st: CwState, faces: CwFace[], live: boolean, ended: boolean): string {
  const small = !st.wide && !ended, size = small ? cwPhone() ? 52 : 44 : 32;
  const cap = cwPhone() && small ? CW_FACES.phone : small ? CW_FACES.small : CW_FACES.wide;
  const on = faces.filter(f => f.on), out = faces.filter(f => !f.on);
  const fit = faces.length <= cap + 1 ? faces : [...on.slice(0, cap), ...out.slice(0, Math.max(0, cap - on.length))];
  const more = on.length - fit.filter(f => f.on).length;
  return fit.map(f => cwFaceHTML(f, live, size)).join('')
    + (more ? `<button type="button" class="cw-face cw-more" data-cw="add" role="listitem" aria-label="${more} more in the call" title="Everyone in the call"><span class="cw-av tnum" style="--cw-av:${size}px">+${more}</span><span class="cw-name">More</span></button>` : '')
    + (live ? `<button type="button" class="cw-face cw-add" data-cw="add" role="listitem" aria-label="Add people or bots" title="Add"><span class="cw-av" style="--cw-av:${size}px">${cwIcon(CW_ICONS.add)}</span><span class="cw-name" aria-hidden="true"></span></button>` : '');
}
// One face: its avatar, the speaking ring, the mute mark, a thinking bot's breathing ring and the name under it. A bot's
// face is a button: tapping it puts the bot in the call, or takes it out.
function cwFaceHTML(f: CwFace, live: boolean, size: number): string {
  const name = cwName(f.actor), cls = `cw-face${f.on ? '' : ' cw-out'}${f.speaking ? ' cw-speaking' : ''}${f.thinking ? ' cw-thinking' : ''}`;
  const inner = `<span class="cw-av" style="--cw-av:${size}px">${cwAv(f.actor, size)}${f.muted ? `<span class="cw-mute" aria-label="Muted" role="img">${cwIcon(CW_ICONS.muted)}</span>` : ''}${
    f.thinking ? '<span class="cw-breath" aria-label="Thinking" role="img"></span>' : ''}</span><span class="cw-name">${esc(name)}</span>`;
  if (!f.bot || !live) return `<span class="${cls}" role="listitem" title="${esc(name)}">${inner}</span>`;
  const label = f.on ? `Take ${name} out of the call` : `Add ${name} to the call`;
  return `<button type="button" class="${cls}" role="listitem" data-cw-bot="${esc(actorSlug(f.actor))}" data-on="${f.on}" aria-label="${esc(label)}" title="${esc(name)}">${inner}</button>`;
}
// Whose words the caption shows: a bot speaking says its own; else the latest words of someone the room hears now; else
// the transcript's last words. `talking` while they are being said.
function cwCaptionWho(st: CwState, v: CwCall | null): {actor: string; text: string; talking: boolean} | null {
  const bot = cwBots(v).find(b => b.state === 'speaking' && b.caption);
  if (bot) return {actor: 'bot:' + bot.slug, text: bot.caption, talking: true};
  const here = cwHere(st), room = here?.room, me = myActor();
  const heard = new Set<string>();
  for (const p of room?.remoteParticipants?.values() ?? []) if (p.isSpeaking && p.isMicrophoneEnabled !== false) heard.add(p.identity);
  if (here && !here.muted && room?.localParticipant.isSpeaking) heard.add(me);
  const said = cwSaid(st), last = said[said.length - 1];
  const c = [...said].reverse().find(x => heard.has(cwSpeaker(x.speaker))) || last;
  return c ? {actor: cwSpeaker(c.speaker), text: c.text, talking: heard.has(cwSpeaker(c.speaker))} : null;
}
// The caption: whose words (cwCaptionWho) and the words, at most three lines. Under it, for a few seconds, the voice's
// note (why a bot is quiet, say).
function cwCaptionHTML(st: CwState, v: CwCall | null): string {
  const here = cwHere(st);
  const blocked = here?.room?.canPlaybackAudio === false
    ? `<button type="button" class="cw-icon cw-sound" data-cw="sound" aria-label="Turn on sound" title="Turn on sound">${CW_SOUND_SVG}</button>` : '';
  const said = cwCaptionWho(st, v), who = said?.actor || '', text = said?.text || '';
  const note = String(v?.voice?.note || '');
  if (note !== st.note) { st.note = note; st.noteAt = Date.now(); if (note) window.setTimeout(callWindowDraw, CW_NOTE_MS + 50); }
  const noteHTML = note && Date.now() - st.noteAt < CW_NOTE_MS ? `<span class="cw-note">${esc(note)}</span>` : '';
  if (!text) return blocked + noteHTML;
  const talking = !!said?.talking;
  return `${blocked}<span class="cw-wave${talking ? ' on' : ''}" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span class="cw-said">${
    who ? `<b>${esc(cwName(who))}</b> ` : ''}<span class="cw-said-t">${esc(text)}</span></span>${noteHTML}`;
}
const cwTurnHTML = (c: CwChunk, q = ''): string => {
  const at = typeof c.start_ms === 'number' ? mmss(c.start_ms) : '';
  const who = cwSpeaker(c.speaker);
  return `<div class="cw-turn" data-seq="${c.seq}"><div class="cw-turn-who">${who ? `<b>${esc(cwName(who))}</b>` : ''}${at ? `<span class="cw-at tnum">${esc(at)}</span>` : ''}</div><p>${cwMarked(c.text, q)}</p></div>`;
};
function cwMarked(text: string, q: string): string {
  const safe = esc(text);
  if (!q) return safe;
  const needle = esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return safe.replace(new RegExp(needle, 'gi'), m => `<mark>${m}</mark>`);
}
// People: who is in the call, each with their marks; typing finds a whole team, a bot or a person to add instead.
function cwPeopleDraw(st: CwState): void {
  const list = st.el.querySelector<HTMLElement>('.cw-people-list'); if (!list) return;
  const input = st.el.querySelector<HTMLInputElement>('[data-cw-q]');
  const q = (input?.value || '').trim().toLowerCase();
  const v = cwView(st), faces = cwFaces(st, v), inCall = new Set(cwBots(v).map(b => b.slug));
  const plus = (attr: string, label: string, solid = false): string =>
    `<button type="button" class="cw-plus${solid ? ' solid' : ''}" ${attr} aria-label="${esc(label)}" title="${esc(label)}">${cwIcon(CW_ICONS.add)}</button>`;
  const check = `<span class="cw-in" role="img" aria-label="In the call">${cwIcon(CW_ICONS.check)}</span>`;
  if (q) {
    const match = (s: string | undefined): boolean => String(s || '').toLowerCase().includes(q);
    const bots = cwAddable();
    const teams = (S.orgGroups || []).filter(g => match(g.name || g.id)).map(g => ({g, bots: bots.filter(b => (g.bots || []).includes(b.id))})).filter(t => t.bots.length).slice(0, 4);
    const people = (S.people || []).filter(p => p.id !== S.me?.id && (match(p.name) || match(p.email || ''))).slice(0, 6);
    const rows = [
      ...teams.map(({g, bots: tb}) => `<div class="cw-row cw-team"><span class="cw-team-ic">${cwIcon(CW_ICONS.team)}</span><span class="cw-row-name">${esc(g.name || g.id)}</span>
        <span class="cw-stack" aria-hidden="true">${tb.slice(0, 3).map(b => cwAv('bot:' + b.id, 24)).join('')}</span><span class="cw-n muted tnum">${tb.length}</span>
        ${plus(`data-cw-team="${esc(g.id)}"`, `Add ${g.name || g.id}`, true)}</div>`),
      ...bots.filter(b => match(b.display_name || b.name || b.slug || b.id)).slice(0, 8).map(b =>
        `<div class="cw-row">${cwAv('bot:' + b.id, 28)}<span class="cw-row-name">${esc(actorName('bot:' + b.id))}</span>${inCall.has(b.id)
          ? check : plus(`data-cw-bot="${esc(b.id)}" data-on="false"`, `Add ${actorName('bot:' + b.id)}`)}</div>`),
      ...people.map(p => {
        const actor = 'human:' + p.id;
        return `<div class="cw-row">${cwAv(actor, 28)}<span class="cw-row-name">${esc(actorName(actor))}</span>${faces.some(f => f.actor === actor && f.on)
          ? check : plus(`data-cw-person="${esc(actor)}"`, `Add ${actorName(actor)}`)}</div>`;
      })];
    patchHTML(list, rows.length ? rows.join('') : '<p class="cw-empty muted">No one found</p>');
    return;
  }
  const here = faces.filter(f => f.on);
  patchHTML(list, `<div class="cw-h muted">In call · <span class="tnum">${cwCount(faces)}</span></div>${here.map(f => {
    const marks = `${f.speaking ? '<span class="cw-mark cw-mark-on" role="img" aria-label="Speaking"></span>' : ''}${
      f.thinking ? '<span class="cw-mark cw-mark-think" role="img" aria-label="Thinking"></span>' : ''}${
      f.muted ? `<span class="cw-in muted" role="img" aria-label="Muted">${cwIcon(CW_ICONS.muted)}</span>` : ''}`;
    const inner = `${cwAv(f.actor, 28)}<span class="cw-row-name">${esc(cwName(f.actor))}${f.actor === myActor() ? ' <span class="muted">You</span>' : ''}</span>${marks}`;
    // A bot's row takes it out, as its face does, with Undo.
    return f.bot ? `<button type="button" class="cw-row cw-row-bot" data-cw-bot="${esc(actorSlug(f.actor))}" data-on="true" aria-label="${esc(`Take ${actorName(f.actor)} out of the call`)}">${inner}</button>`
      : `<div class="cw-row">${inner}</div>`;
  }).join('')}`);
}
function cwBarDraw(st: CwState, v: CallRecord | null): void {
  const bar = st.el.querySelector<HTMLElement>('.cw-bar'); if (!bar) return;
  const here = cwHere(st);
  if (v?.state !== 'live') { patchHTML(bar, ''); return; }
  if (!here) {
    patchHTML(bar, `<span class="spacer"></span><button type="button" class="primary cw-join" data-cw="join">Join</button>`);
    return;
  }
  const btn = (k: string, icon: string, label: string, on = false, more = ''): string =>
    `<button type="button" class="cw-ctl${on ? ' on' : ''}" data-cw-ctl="${k}" aria-label="${esc(label)}" title="${esc(label)}" aria-pressed="${on}"${more}>${cwIcon(icon)}</button>`;
  // Share: Stop while this person shows something, else the menu (Screen, Show a Tico file).
  const showing = here.sharing || callStageMine(here);
  patchHTML(bar, `${btn('mute', here.muted ? CW_ICONS.muted : CW_ICONS.mic, here.muted ? 'Unmute' : 'Mute', here.muted)}${
    showing ? btn('stop', CW_ICONS.stop, 'Stop sharing', true) : btn('share', CW_ICONS.share, 'Share', false, ` aria-haspopup="dialog" aria-expanded="${!!st.pop}"`)}
    <span class="spacer"></span><button type="button" class="cw-leave" data-cw-ctl="leave"${here.leaving ? ' disabled' : ''}>Leave</button>`);
}
// The wide layout while someone else's screen shows. The desktop app's window grows to fit it and goes back after
// (app/src/bridge.rs callWindowSize); so does a small pop-up a page opened. A tab keeps its size.
function cwWide(st: CwState, wide: boolean): void {
  if (st.wide === wide) return;
  st.wide = wide;
  // The chat keeps its newest message in view as the layout changes around it.
  const chat = st.el.querySelector<HTMLElement>('.cw-thread .thv-scroll');
  const atEnd = !!chat && chat.scrollHeight - chat.scrollTop - chat.clientHeight < 8;
  st.el.classList.toggle('cw-wide', wide);
  document.body.classList.toggle('cw-wide-page', wide);
  if (chat && atEnd) requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
  if (cwPhone()) return;
  const native = (window.ticoNativeFeatures || []).includes('callWindowSize') && nativeHandler('callWindowSize');
  if (native) { try { void Promise.resolve(native.postMessage({call: st.id, wide})).catch(() => {}); } catch {} return; }
  try {
    if (wide && window.outerWidth < 760) { st.size = [window.outerWidth, window.outerHeight]; window.resizeTo(1100, 720); }
    else if (!wide && st.size) { window.resizeTo(st.size[0], st.size[1]); st.size = null; }
  } catch { /* a tab or a window the page may not resize: the layout follows its size */ }
}
// When it was and how long: "Today, 2:00 PM · 24 min".
function cwWhen(v: CallRecord | null): string {
  const at = v?.started_at ? new Date(v.started_at) : null;
  if (!at || Number.isNaN(at.getTime())) return callLength(v?.duration_s);
  const today = new Date().toDateString() === at.toDateString();
  const day = today ? 'Today' : at.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  const len = callLength(v?.duration_s);
  return `${day}, ${at.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'})}${len ? ' · ' + len : ''}`;
}

// ----------------------------------------------------------------- acting
async function cwClick(st: CwState, ev: MouseEvent): Promise<void> {
  const t = eventElement(ev); if (!t) return;
  // A click anywhere else puts the share menu away.
  if (st.pop && !t.closest('.cw-pop, [data-cw-ctl="share"]')) cwPop(st, '');
  const pop = t.closest<HTMLElement>('[data-cw-pop]');
  if (pop) { await cwPopPick(st, pop.dataset.cwPop || ''); return; }
  const card = t.closest<HTMLAnchorElement>('a[data-cw-tcard]');
  if (card) { ev.preventDefault(); cwOpenElsewhere(st, card.getAttribute('href') || ''); return; }
  const tab = t.closest<HTMLElement>('[data-cw-tab]');
  if (tab) { st.tab = tab.dataset.cwTab || 'chat'; cwDraw(st); if (st.tab === 'transcript') cwScrollEnd(st); return; }
  const bot = t.closest<HTMLElement>('[data-cw-bot]');
  if (bot) { await cwBotToggle(st, bot.dataset.cwBot || '', bot.dataset.on === 'true'); return; }
  const team = t.closest<HTMLElement>('[data-cw-team]');
  if (team) { await cwTeamAdd(st, team.dataset.cwTeam || ''); return; }
  const person = t.closest<HTMLElement>('[data-cw-person]');
  if (person) { await cwPersonAdd(st, person.dataset.cwPerson || '', person); return; }
  const item = t.closest<HTMLElement>('[data-cw-item]');
  if (item) { await cwItemAct(st, item.dataset.cwItem || '', Number(item.dataset.i), item); return; }
  const ctl = t.closest<HTMLElement>('[data-cw-ctl]');
  if (ctl) { await cwControl(st, ctl.dataset.cwCtl || '', ctl); return; }
  const act = t.closest<HTMLElement>('[data-cw]'); if (!act) return;
  const k = act.dataset.cw;
  if (k === 'add') { st.tab = 'people'; cwDraw(st); st.el.querySelector<HTMLInputElement>('[data-cw-q]')?.focus(); }
  else if (k === 'copy-link') { await copyText(location.origin + location.pathname + CALL_ROUTE + encodeURIComponent(st.id)).then(() => cwToast('Link copied'), () => cwToast('Could not copy', true)); }
  else if (k === 'down') location.hash = threadHref(st.threadId);
  else if (k === 'close') { ev.preventDefault(); cwClose(st); }
  else if (k === 'thread') { ev.preventDefault(); cwOpenThread(st); }
  else if (k === 'join') { st.join = true; st.joinTried = false; await cwJoin(st); }
  else if (k === 'sound') { await cwHere(st)?.room?.startAudio?.().catch(() => {}); callWindowDraw(); }
  else if (k === 'full') cwFull(st);
  else if (k === 'tsearch') { st.tsearch = !st.tsearch; if (!st.tsearch) st.tq = ''; cwAfterDraw(st); st.el.querySelector<HTMLInputElement>('[data-cw-tq]')?.focus(); }
  else if (k === 'tcopy') { await copyText(cwSaid(st).map(c => `${cwName(cwSpeaker(c.speaker))}: ${c.text}`).join('\n')).then(() => cwToast('Copied'), () => cwToast('Could not copy', true)); }
  else if (k === 'task-cancel') { st.task = ''; cwTaskDraw(st); }
}
function cwScrollEnd(st: CwState): void { const pane = st.el.querySelector<HTMLElement>('.cw-transcript'); if (pane) pane.scrollTop = pane.scrollHeight; }
// The shared screen on the whole screen (a phone's), and back.
function cwFull(st: CwState): void {
  const video = st.el.querySelector<HTMLVideoElement & {webkitEnterFullscreen?: () => void}>('#cw-screen video:not([hidden])');
  if (document.fullscreenElement) { void document.exitFullscreen().catch(() => {}); return; }
  const box = st.el.querySelector<HTMLElement>('#cw-file:not([hidden])') || st.el.querySelector<HTMLElement>('#cw-screen');
  if (box?.requestFullscreen) void box.requestFullscreen().catch(() => { video?.webkitEnterFullscreen?.(); });
  else video?.webkitEnterFullscreen?.();
}
async function cwControl(st: CwState, k: string, button: HTMLElement): Promise<void> {
  const here = cwHere(st); if (!here) return;
  if (k === 'leave') {
    // Whoever started the call may also end it for everyone.
    if (here.connector && here.connector === myActor()) {
      thvMenuShow(button, [['leave', 'Leave'], ['end', 'End for everyone']], pick => {
        void (async () => { await callBarAct(pick, button); if (pick === 'leave' && !CALL) cwClose(st); })();
      });
      return;
    }
    await callBarAct('leave', button);
    if (!CALL) cwClose(st);
    return;
  }
  // Where the browser cannot share a screen (a phone), share is the file picker at once.
  if (k === 'share') { cwPop(st, st.pop ? '' : callShareable() ? 'menu' : 'file'); return; }
  if (k === 'stop') { cwPop(st, ''); await callShowStop(); return; }
  await callBarAct(k, button);
}
// The share menu over the controls: Screen (where the browser can share one) and Show a Tico file, which turns into the
// file picker (ui/app/thread-library.ts); a pick shows it to everyone.
function cwPop(st: CwState, pop: CwState['pop']): void {
  st.pop = pop;
  const box = st.el.querySelector<HTMLElement>('.cw-pop'), bar = st.el.querySelector<HTMLElement>('.cw-bar');
  st.el.querySelector('[data-cw-ctl="share"]')?.setAttribute('aria-expanded', String(!!pop));
  if (!box) return;
  box.hidden = !pop;
  box.classList.toggle('cw-pop-file', pop === 'file');
  if (!pop) { box.replaceChildren(); return; }
  if (bar) box.style.bottom = `${Math.max(0, st.el.getBoundingClientRect().bottom - bar.getBoundingClientRect().top) + 6}px`;
  box.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); cwPop(st, ''); st.el.querySelector<HTMLElement>('[data-cw-ctl="share"]')?.focus(); } };
  if (pop === 'menu') {
    box.innerHTML = `${callShareable() ? `<button type="button" class="cw-pop-row" data-cw-pop="screen">${cwIcon(CW_ICONS.share)}<span>Screen</span></button>` : ''}<button type="button" class="cw-pop-row" data-cw-pop="file">${cwIcon(CW_ICONS.file)}<span>Show a Tico file</span></button>`;
    box.querySelector<HTMLElement>('button')?.focus();
    return;
  }
  box.innerHTML = '<div class="cw-pop-lib"></div>';
  const pane = box.querySelector<HTMLElement>('.cw-pop-lib');
  if (pane) void libPickerMount(pane, {links: true, bare: true, placeholder: 'Find a file or page', close: () => cwPop(st, ''),
    pick: f => { cwPop(st, ''); void callShowFile(f.id); }});
}
async function cwPopPick(st: CwState, k: string): Promise<void> {
  if (k === 'file') { cwPop(st, 'file'); return; }
  cwPop(st, '');
  if (k === 'screen' && CALL) { try { await callShareStart(CALL); } catch (e) {
    if ((isErrorRecord(e) ? e.name : undefined) !== 'NotAllowedError') cwToast(errorMessage(e, 'Could not share the screen'), true);
  } callBarDraw(); }
}
// A bot's face (or its People row): one tap puts it in the call; on one in the call it leaves at once, with Undo.
async function cwBotToggle(st: CwState, slug: string, on: boolean): Promise<void> {
  if (!slug || st.busy.has(slug)) return;
  const v = cwView(st); if (!v) return;
  st.busy.add(slug);
  const name = actorName('bot:' + slug);
  const without = (v.bots || []).filter(b => (typeof b === 'string' ? b : b?.slug) !== slug);
  try {
    if (on) {
      st.gone.add(slug);
      callViewKeep({...v, bots: without});
      const r: CallRecord | null = await writeRequest('DELETE', callPath(st.id) + '/bots/' + encodeURIComponent(slug), {});
      if (r?.id) callViewKeep(r);
      cwToast(`${name} left`, false, {label: 'Undo', run: () => cwBotAttach(st, [slug])});
    } else await cwBotAttach(st, [slug]);
  } catch (e) {
    if (on) callViewKeep(v);
    cwToast(errorMessage(e, on ? 'Could not take the bot out' : 'Could not add the bot'), true);
  } finally { st.busy.delete(slug); callWindowDraw(); }
}
async function cwBotAttach(st: CwState, slugs: string[]): Promise<void> {
  const r: CallRecord | null = await post(callPath(st.id) + '/bots', {bots: slugs});
  if (r?.id) callViewKeep(r);
  for (const s of slugs) st.gone.delete(s);
  callWindowDraw();
}
// A team in one pick: every bot of it the viewer may add.
async function cwTeamAdd(st: CwState, id: string): Promise<void> {
  const g = (S.orgGroups || []).find(x => x.id === id), inCall = new Set(cwBots(cwView(st)).map(b => b.slug));
  const slugs = cwAddable().filter(b => (g?.bots || []).includes(b.id) && !inCall.has(b.id)).map(b => b.id);
  if (!slugs.length) { cwToast('Already in the call'); return; }
  try { await cwBotAttach(st, slugs); cwToast(`${g?.name || id} joined`); }
  catch (e) { cwToast(errorMessage(e, 'Could not add the team'), true); }
}
// A person is rung into the call (backend/call_notes.py): someone who reads the thread keeps their place in it, anyone
// else is added to it first, as the thread's own Add people does.
async function cwPersonAdd(st: CwState, actor: string, button: HTMLElement): Promise<void> {
  if (!(button instanceof HTMLButtonElement)) return;
  button.disabled = true;
  try {
    const r: {added?: boolean} | null = await post(callPath(st.id) + '/ring', {actor});
    cwToast(r?.added ? `${actorName(actor)} added` : `Ringing ${actorName(actor)}`);
  } catch (e) { button.disabled = false; cwToast(errorMessage(e, 'Could not ring them'), true); }
}
// Leave closes the window: a window the page opened closes, else (a phone, a tab opened by hand) its thread shows.
function cwClose(st: CwState): void {
  try { window.close(); } catch {}
  window.setTimeout(() => { if (!window.closed && CW === st) location.hash = threadHref(st.threadId); }, 150);
}
function cwOpenThread(st: CwState): void {
  if (st.threadId) cwOpenElsewhere(st, threadHref(st.threadId));
}
// A place in the app opened beside the call (a new window or tab) so the call stays; a phone goes there.
function cwOpenElsewhere(_st: CwState, hash: string): void {
  if (!hash.startsWith('#/')) return;
  const url = location.pathname + location.search + hash;
  if (window.opener || !cwPhone()) { try { if (window.open(url, '_blank')) return; } catch {} }
  location.hash = hash;
}

// ----------------------------------------------------------------- tasks from the chat
// Link a task (by #number or link) or a new task, made at once and linked to the call's meeting: either way a message
// in the call's chat names it.
function cwTaskDraw(st: CwState): void {
  const slot = st.el.querySelector<HTMLElement>('.cw-task'); if (!slot) return;
  slot.hidden = !st.task;
  if (!st.task) { slot.innerHTML = ''; return; }
  const v = cwView(st);
  const owners = [myActor(), ...cwFaces(st, v).filter(f => f.on).map(f => f.actor)].filter((a, i, all) => a && all.indexOf(a) === i);
  slot.innerHTML = st.task === 'new'
    ? `<form class="cw-task-form" data-task="new"><input name="title" required maxlength="300" aria-label="New task" placeholder="New task">
        <select name="owner" aria-label="Owner">${owners.map(a => `<option value="${esc(a)}">${esc(a === myActor() ? 'Me' : actorName(a))}</option>`).join('')}</select>
        <button type="submit" class="primary small">Create</button><button type="button" class="cw-icon" data-cw="task-cancel" aria-label="Cancel" title="Cancel">${cwIcon(CW_ICONS.close)}</button></form>`
    : `<form class="cw-task-form" data-task="link"><input name="ref" required aria-label="Task to link" placeholder="#123 or task link">
        <button type="submit" class="primary small">Link</button><button type="button" class="cw-icon" data-cw="task-cancel" aria-label="Cancel" title="Cancel">${cwIcon(CW_ICONS.close)}</button></form>`;
  slot.querySelector<HTMLInputElement>('input')?.focus();
}
async function cwTaskSubmit(st: CwState, form: HTMLFormElement): Promise<void> {
  if (!form.matches('.cw-task-form')) return;
  const data = new FormData(form), buttons = form.querySelectorAll<HTMLButtonElement>('button');
  const view = st.chat; if (!view) return;
  buttons.forEach(b => { b.disabled = true; });
  try {
    let task: {id?: string; title?: string; number?: number} | null;
    if (form.dataset.task === 'new') {
      task = await cwTaskMake(st, String(data.get('title') || '').trim(), String(data.get('owner') || myActor()));
    } else {
      const raw = String(data.get('ref') || '').trim();
      const ref = (raw.match(/#\/task\/([^/?#]+)/) || raw.match(/\/tasks?\/([^/?#]+)/) || [])[1] || raw.replace(/^#/, '');
      const r: {task?: {id?: string; title?: string}; id?: string; title?: string} | null = await get(`/v2/tasks/${encodeURIComponent(decodeURIComponent(ref))}`);
      task = r?.task || r;
    }
    if (!task?.id) throw new Error('Task not found');
    await thvSend(view, {text: `[${(task.title || 'Task').replace(/[[\]]/g, '')}](#/task/${encodeURIComponent(task.id)})`});
    st.task = ''; cwTaskDraw(st);
  } catch (e) {
    buttons.forEach(b => { b.disabled = false; });
    cwToast(errorMessage(e, 'Could not do that'), true);
  }
}
// A message that is only a task's link (Link a task, New task) is the task's card: its state, its title and its owner.
const CW_TASK_LINK = /^\[([^\]\n]*)\]\(#\/task\/([^)\s/?#]+)\)$/;
function cwTaskBody(st: CwState, m: ThvMessage): string | null {
  const hit = CW_TASK_LINK.exec((m.body || '').trim()); if (!hit) return null;
  let id: string;
  try { id = decodeURIComponent(hit[2] || ''); } catch { return null; }
  if (!id) return null;
  if (!st.tasks.has(id)) void cwTaskRead(st, id);
  return cwTaskCardHTML(id, hit[1] || 'Task', st.tasks.get(id));
}
function cwTaskCardHTML(id: string, said: string, t: CwTask | null | undefined): string {
  const title = t?.title || said, done = !!t?.status && t.status !== 'open';
  const mark = `<span class="cw-tcard-mark${done ? ' done' : ''}" role="img" aria-label="${done ? 'Done' : 'To do'}">${cwIcon(done ? CW_ICONS.done : CW_ICONS.todo)}</span>`;
  return `<div class="th-body"><a class="cw-tcard" href="#/task/${esc(encodeURIComponent(id))}" data-cw-tcard>${mark}<span class="cw-tcard-t">${esc(title)}</span>${
    t?.owner_id ? `<span class="cw-tcard-who" title="${esc(cwName(t.owner_id))}">${cwAv(t.owner_id, 18)}</span>` : ''}</a></div>`;
}
// Read once per task: the chat draws again with what it says.
async function cwTaskRead(st: CwState, id: string): Promise<void> {
  st.tasks.set(id, null);
  const r = await v2Get<{task?: CwTask} & CwTask>(`/v2/tasks/${encodeURIComponent(id)}`);
  if (CW !== st) return;
  const t = r?.task || r;
  st.tasks.set(id, t?.id || t?.title ? t : null);
  if (st.chat && !st.chat.gone) thvDraw(st.chat, {parts: ['list']});
}
// A task linked to the call's meeting (its rules decide who reads it).
async function cwTaskMake(st: CwState, title: string, owner: string): Promise<{id?: string; title?: string}> {
  const r: {task?: {id?: string; title?: string}} | null = await post('/v2/tasks', {title, owner, ...(st.meeting ? {live_meeting_id: st.meeting} : {})}, newRequestId());
  return {...(r?.task || {}), title};
}

// ----------------------------------------------------------------- after the call: its notes
// The live part stops (the chat, the stream; the room is gone with the call: callViewKeep), then the notes: this call's
// own summary and action items (GET /v2/calls/{id}/notes), read again while the call still owes them, and its transcript
// (the meeting's, else the transcript message the call posted).
async function cwAfterLoad(st: CwState): Promise<void> {
  threadUnmount(st.chat); st.chat = null;
  st.el.querySelector('.cw-thread')?.replaceChildren();
  st.source?.close(); st.source = null;
  st.made = new Map(cwMade(st.id));
  if (st.meeting && !st.chunks.size) await cwLiveRead(st, false);
  if (!st.chunks.size) await cwSpokenRead(st);
  if (CW !== st) return;
  callWindowDraw();
  await cwNotesRead(st);
}
async function cwNotesRead(st: CwState): Promise<void> {
  try {
    const n = await get<CwNotes>(callPath(st.id) + '/notes', {fresh: true});
    if (CW !== st) return;
    // A call nobody spoke in owes none.
    st.notes = n?.pending && st.chunks.size ? null : {summary: n?.summary || [], items: n?.items || []};
  } catch { if (CW === st) st.notes = st.notes || null; }
  if (!st.notes && st.notesTries++ < 30) st.after = window.setTimeout(() => { if (CW === st) void cwNotesRead(st); }, 10000);
  callWindowDraw();
}
// The transcript the call posted in its thread ("Call transcript: <title>", a line per piece), for a meeting this
// viewer cannot read.
async function cwSpokenRead(st: CwState): Promise<void> {
  const mid = cwView(st)?.transcript_message_id; if (!mid) return;
  try {
    const m = await get<{body?: string}>('/v2/messages/' + encodeURIComponent(mid));
    (m?.body || '').split('\n').slice(1).forEach((l, i) => {
      const at = l.indexOf(': ');
      if (l.trim()) st.chunks.set(i, at > 0 ? {seq: i, speaker: l.slice(0, at), text: l.slice(at + 2)} : {seq: i, text: l});
    });
  } catch { /* nothing to show: "No transcript" */ }
}
// Items hidden here and tasks made from them, per call, in this browser only.
const cwKey = (id: string): string => 'tico.call-items.' + id;
function cwMade(id: string): [string, string][] {
  try { const v: unknown = JSON.parse(localStorage.getItem(cwKey(id)) || '[]'); return Array.isArray(v) ? v.filter((x): x is [string, string] => Array.isArray(x) && x.length === 2) : []; }
  catch { return []; }
}
function cwMadeKeep(st: CwState): void { try { localStorage.setItem(cwKey(st.id), JSON.stringify([...st.made])); } catch {} }
function cwAfterDraw(st: CwState): void {
  const box = st.el.querySelector<HTMLElement>('.cw-after'); if (!box) return;
  box.hidden = false;
  const notes = st.notes, items = notes?.items || [];
  const rows = !notes ? '<p class="cw-empty muted">Notes come a little after the call</p>'
    : !items.length ? '<p class="cw-empty muted">No action items</p>'
    : items.map((item, i) => {
      const made = st.made.get(item.title);
      if (made === '') return '';
      const owner = item.owner ? `<span class="cw-owner" title="${esc(cwName(item.owner))}">${cwAv(item.owner, 18)}<span class="cw-owner-n">${esc(cwName(item.owner))}</span></span>` : '';
      return `<div class="cw-item"><span class="cw-item-t">${esc(item.title)}</span>${owner}${made
        ? `<a class="cw-item-made" href="#/task/${esc(encodeURIComponent(made))}">${cwIcon(CW_ICONS.check)}<span>Task</span></a>`
        : `<button type="button" class="cw-create" data-cw-item="create" data-i="${i}">Create</button><button type="button" class="cw-icon" data-cw-item="hide" data-i="${i}" aria-label="Hide" title="Hide">${cwIcon(CW_ICONS.close)}</button>`}</div>`;
    }).join('');
  let side = box.querySelector<HTMLElement>('.cw-after-side');
  if (!side) {
    box.innerHTML = `<div class="cw-after-main"><div class="cw-summary" hidden><div class="cw-label">Summary</div><ul class="cw-bullets"></ul></div>
      <div class="cw-label">Action items</div><div class="cw-items"></div></div><aside class="cw-after-side" aria-label="Transcript"></aside>`;
    side = box.querySelector<HTMLElement>('.cw-after-side');
  }
  const summary = box.querySelector<HTMLElement>('.cw-summary'), bullets = notes?.summary || [];
  if (summary) {
    summary.hidden = !bullets.length;
    const ul = summary.querySelector('.cw-bullets');
    if (ul) patchHTML(ul, bullets.map(b => `<li>${esc(b)}</li>`).join(''));
  }
  const list = box.querySelector<HTMLElement>('.cw-items');
  if (list) patchHTML(list, rows);
  if (side && !side.querySelector('.cw-side-head')) {
    side.innerHTML = `<div class="cw-side-head"><b>Transcript</b><span class="spacer"></span><button type="button" class="cw-icon" data-cw="tsearch" aria-label="Search transcript" title="Search">${cwIcon(CW_ICONS.search)}</button><button type="button" class="cw-icon" data-cw="tcopy" aria-label="Copy transcript" title="Copy">${cwIcon(CW_ICONS.copy)}</button></div>
      <label class="cw-find" hidden>${cwIcon(CW_ICONS.search)}<input type="search" data-cw-tq aria-label="Find in transcript" placeholder="Find"></label><div class="cw-after-turns"></div>`;
  }
  const find = side?.querySelector<HTMLElement>('.cw-find');
  if (find) find.hidden = !st.tsearch;
  cwAfterTranscript(st);
}
function cwAfterTranscript(st: CwState): void {
  const turns = st.el.querySelector<HTMLElement>('.cw-after-turns'); if (!turns) return;
  const said = cwSaid(st), q = st.tsearch ? st.tq.trim() : '';
  const shown = q ? said.filter(c => c.text.toLowerCase().includes(q.toLowerCase())) : said;
  patchHTML(turns, shown.length ? shown.map(c => cwTurnHTML(c, q)).join('') : `<p class="cw-empty muted">${q ? 'Nothing found' : 'No transcript'}</p>`);
}
async function cwItemAct(st: CwState, k: string, i: number, button: HTMLElement): Promise<void> {
  const item = st.notes?.items?.[i]; if (!item) return;
  if (k === 'hide') { st.made.set(item.title, ''); cwMadeKeep(st); cwAfterDraw(st); return; }
  if (!(button instanceof HTMLButtonElement)) return;
  button.disabled = true;
  try {
    // The task goes to who took the item on, else to the viewer.
    const task = await cwTaskMake(st, item.title, item.owner || myActor());
    if (!task.id) throw new Error('The task was not made');
    st.made.set(item.title, task.id); cwMadeKeep(st);
    cwToast('Task created');
  } catch (e) { button.disabled = false; cwToast(errorMessage(e, 'Could not create the task'), true); }
  cwAfterDraw(st);
}
