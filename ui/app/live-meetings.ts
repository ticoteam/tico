/* ui/app/live-meetings.ts — Live transcript, chat, org-chart picker and persisted router trace. */
'use strict';

// The live meeting page's shapes as the server sends them (no generated type covers these routes yet).
// Meet* types from ui/app/meetings.ts are reused where the shape matches.
interface Window { liveMeetingsStop?: () => void }
interface LiveMember { actor: string; name?: string | undefined }
interface LiveBot { bot: string; name: string; joined_by?: string | undefined }
interface LiveCandidate { id?: string | undefined; slug?: string | undefined; name: string; team?: string | undefined }
interface LiveListRow { id: string; title: string; state: string; client?: string | undefined; seq?: number | undefined; bots?: unknown[] | undefined }
interface LiveChunk { seq: number; revision: number; speaker?: string | undefined; text: string }
interface LiveChat extends Omit<MeetChatMessage, 'actor'> { actor: string; created_at?: string | undefined; transcript_seq?: number | null | undefined }
interface LiveNotice { kind: string; reason?: string | undefined; bot: string; detail?: string | null | undefined; created_at?: string | undefined }
interface LiveTrace { ms?: number | undefined }
interface LiveRouter {
  windows?: {window_index: number; outcome: string; start_ms: number; end_ms: number; trace?: LiveTrace | undefined}[] | undefined;
  chat_routes?: {outcome?: string | undefined; status?: string | undefined; started_at?: string | undefined; trace?: LiveTrace | undefined}[] | undefined;
  bypasses?: unknown[] | undefined;
  turns?: {name: string; status: string; skip_reason?: string | null | undefined}[] | undefined;
}
interface LiveDetail {
  id: string; title: string; state: string; client?: string | undefined; event_id?: number | undefined; owner_actor?: string | undefined;
  visibility?: string | undefined; window_ms: number; cooldown_ms: number; reply_cap?: number | null | undefined;
  imported_meeting_id?: string | null | undefined; humans?: LiveMember[] | undefined; access?: LiveMember[] | undefined;
  bots?: LiveBot[] | undefined; chunks?: LiveChunk[] | undefined; chat?: LiveChat[] | undefined; notices?: LiveNotice[] | undefined;
  router?: LiveRouter | undefined;
}
interface LiveConnectForm { title: FormDataEntryValue | null; reply_cap: number; cooldown_seconds: number }
// A submit event on a form whose named controls are typed by C (the form's `elements` gains them).
interface LiveFormEvent<C = unknown> extends Event { readonly currentTarget: HTMLFormElement & {elements: C} }
interface LiveBotControls { bots: HTMLSelectElement }
interface LivePeopleControls { people: HTMLSelectElement }
interface LiveChatControls { text: HTMLInputElement }
type LiveControl = 'pause' | 'disconnect' | 'resume' | 'end';
interface LiveSelection { start: number; end?: number | null | undefined; direction?: string | null | undefined }
interface LiveDraft {
  chatText?: string | undefined; selectedBots?: string[] | undefined; selectedPeople?: string[] | undefined;
  focusKey?: string | undefined; selection?: LiveSelection | null | undefined;
}
// The focused element may be a text field (it has a selection) or not.
interface LiveFocusable extends HTMLElement {
  selectionStart?: number | null | undefined; selectionEnd?: number | null | undefined; selectionDirection?: string | null | undefined;
  setSelectionRange?: (start: number, end: number | null | undefined, direction?: string) => void;
}
interface LiveScreen {
  id: string; after: string; url: string; generation: string; abort: AbortController; running: boolean;
  request?: AbortController | undefined; statusRequest?: AbortController | undefined; timer?: number | undefined;
  statusTimer?: number | undefined; visibility?: (() => void) | undefined;
}
interface LiveMeetState {
  list: LiveListRow[]; selected: string; pinned: string; detail: LiveDetail | null; sources: Map<string, EventSource>;
  cursor: Record<string, number>; poll: number; refreshTimer: number; candidates: LiveCandidate[]; busy: boolean;
  drafts: Record<string, LiveDraft>; paintedId: string; listVersion: number; detailVersions: Record<string, number>;
  chatSending: Set<string>; screen?: LiveScreen | null | undefined;
}

let LIVE_MEETINGS: LiveMeetState | null = null;
const LIVE_API = '/v2/live-meetings';

function liveMeetingsStop(): void {
  if (!LIVE_MEETINGS) return;
  liveScreenStop(LIVE_MEETINGS);
  clearInterval(LIVE_MEETINGS.poll);
  clearTimeout(LIVE_MEETINGS.refreshTimer);
  for (const source of LIVE_MEETINGS.sources.values()) source.close();
  LIVE_MEETINGS.sources.clear();
  LIVE_MEETINGS = null;
}
window.liveMeetingsStop = liveMeetingsStop;

async function pageLiveMeetings(selected: string | {pinned?: string} = ''): Promise<void> {
  const pinned = typeof selected === 'string' ? selected : selected.pinned || '';
  liveMeetingsStop();
  meetStop();
  const state: LiveMeetState = LIVE_MEETINGS = {list: [], selected: pinned, pinned, detail: null, sources: new Map<string, EventSource>(), cursor: {},
    poll: 0, refreshTimer: 0, candidates: [], busy: false, drafts: {}, paintedId: '',
    listVersion: 0, detailVersions: {}, chatSending: new Set<string>()};
  $('#main').innerHTML = `<section class="live-page">
    <header class="live-head"><div><h1>Live now${betaPill('live_meetings')}</h1><p class="muted">Transcript text stays in Tico. Audio is never sent.</p></div>
      <button type="button" class="ghost" id="live-back">Meetings</button></header>
    <form id="live-connect" class="live-connect"><label>Meeting title<input name="title" required maxlength="200" placeholder="Weekly team sync"></label>
      <label>Bot reply cap<input name="reply_cap" type="number" min="1" max="20" value="5" required></label>
      <label>Reply cooldown (seconds)<input name="cooldown_seconds" type="number" min="0" max="3600" value="60" required></label>
      <button class="primary" type="submit">Connect live meeting</button></form>
    <div class="live-layout"><nav class="live-list" aria-label="Live meetings"><div class="empty">Loading live meetings…</div></nav>
      <section class="live-detail"><div class="empty">Connect or choose a live meeting.</div></section></div>
    <div class="live-status" role="status" aria-live="polite"></div>
  </section>`;
  $('#live-back').onclick = () => { location.hash = MEETINGS; if (S.route === MEETINGS) void pageNotes(); };
  $('#live-connect').onsubmit = (event: LiveFormEvent) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    void liveConnect(state, {title: form.get('title'), reply_cap: Number(form.get('reply_cap')),
      cooldown_seconds: Number(form.get('cooldown_seconds'))});
  };
  await liveCandidates(state);
  await liveLoad(state);
  if (LIVE_MEETINGS !== state) return;
  state.poll = setInterval(() => { if (!document.hidden) liveLoad(state); }, 5000);
}

async function liveCandidates(state: LiveMeetState): Promise<void> {
  try { const response = await get<{bots?: LiveCandidate[] | undefined}>(LIVE_API + '/bot-candidates'); state.candidates = response.bots || []; }
  catch (error) { liveStatus(state, 'Could not load the visible bot list: ' + errorMessage(error, 'Request failed.')); }
}

function liveStatus(state: LiveMeetState, text?: string): void {
  const el = $('.live-status');
  if (el && LIVE_MEETINGS === state) el.textContent = text || '';
}

async function liveConnect(state: LiveMeetState, options: LiveConnectForm): Promise<void> {
  if (state.busy) return;
  state.busy = true;
  try {
    const clientId = newRequestId();
    const meeting = await post(LIVE_API, {...options, title: String(options.title || '').trim(), client_id: clientId});
    state.selected = meeting.id;
    liveStatus(state, 'Connected. Transcript sharing with the team is on.');
    state.busy = false;
    await liveLoad(state);
  } catch (error) { liveStatus(state, errorMessage(error, 'Request failed.')); }
  finally { state.busy = false; }
}

async function liveLoad(state: LiveMeetState): Promise<void> {
  if (LIVE_MEETINGS !== state || state.busy) return;
  const version = ++state.listVersion;
  try {
    const response = await get<{meetings?: LiveListRow[] | undefined}>(LIVE_API);
    if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion) return;
    state.list = response.meetings || [];
    if (!state.pinned && state.selected && !state.list.some(meeting => meeting.id === state.selected)) {
      // Ended meetings leave Live now; keep their transcript open until the user returns to Meetings.
      const selected = state.selected;
      try { state.detail = await get<LiveDetail>(LIVE_API + '/' + encodeURIComponent(selected)); }
      catch { if (state.selected === selected) { state.selected = ''; state.detail = null; } }
      if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion || state.selected !== selected) return;
    }
    if (!state.pinned && !state.selected && state.list.length) state.selected = state.list[0]!.id;
    if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion) return;
    livePaintList(state);
    if (state.selected) await liveLoadDetail(state, state.selected);
    else if (!state.detail) $('.live-detail').innerHTML = '<div class="empty">No live meetings. Connect when everyone is ready to share transcript text.</div>';
  } catch (error) {
    if (LIVE_MEETINGS === state) $('.live-list').innerHTML = `<div class="err">${esc(errorMessage(error, 'Request failed.'))}</div>`;
  }
}

function livePaintList(state: LiveMeetState): void {
  const host = $('.live-list');
  if (!state.list.length) { host.innerHTML = '<div class="empty">No connected meetings.</div>'; return; }
  host.innerHTML = state.list.map(meeting => `<button type="button" class="live-list-item${meeting.id === state.selected ? ' selected' : ''}" data-live-id="${esc(meeting.id)}">
    <b>${esc(meeting.title)}</b>${meeting.client === 'tico-recorder' ? '<small>From Tico Recorder</small>' : ''}<span class="live-state ${esc(meeting.state)}">${esc(meeting.state)}</span>
    <small>${meeting.seq || 0} transcript chunks · ${meeting.bots?.length || 0} bots</small></button>`).join('');
  host.querySelectorAll('[data-live-id]').forEach((button: HTMLElement) => button.onclick = () => {
    location.hash = '#/meetings/live/' + encodeURIComponent(button.dataset.liveId!);
  });
}

function liveIsHumanJoined(detail: LiveDetail): boolean {
  const actor = `human:${S.me?.id || ''}`;
  return (detail.humans || []).some(member => member.actor === actor);
}

function liveCaptureDraft(state: LiveMeetState, id: string): void {
  const host = $('.live-detail');
  if (!host || !id) return;
  const prior: LiveDraft = state.drafts[id] || {};
  const chat = host.querySelector('#live-chat-form input[name="text"]');
  const picker = host.querySelector('#live-bot-picker select[name="bots"]');
  const peoplePicker = host.querySelector('#live-people-picker select[name="people"]');
  const active = host.contains(document.activeElement) ? document.activeElement as LiveFocusable : null;
  const draft: LiveDraft = {...prior};
  if (chat) draft.chatText = chat.value;
  if (picker) draft.selectedBots = [...picker.selectedOptions].map(option => option.value);
  if (peoplePicker) draft.selectedPeople = [...peoplePicker.selectedOptions].map(option => option.value);
  if (active?.dataset.liveFocusKey) {
    draft.focusKey = active.dataset.liveFocusKey;
    if (typeof active.selectionStart === 'number') {
      draft.selection = {start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection};
    } else draft.selection = null;
  } else { delete draft.focusKey; delete draft.selection; }
  state.drafts[id] = draft;
}

function liveRestoreDraft(host: HTMLElement, draft: LiveDraft): void {
  const chat = host.querySelector<HTMLInputElement>('#live-chat-form input[name="text"]');
  const picker = host.querySelector<HTMLSelectElement>('#live-bot-picker select[name="bots"]');
  if (chat && typeof draft.chatText === 'string') chat.value = draft.chatText;
  if (picker && Array.isArray(draft.selectedBots)) {
    const selected = new Set(draft.selectedBots);
    for (const option of picker.options) option.selected = selected.has(option.value);
  }
  const peoplePicker = host.querySelector<HTMLSelectElement>('#live-people-picker select[name="people"]');
  if (peoplePicker && Array.isArray(draft.selectedPeople)) {
    for (const option of peoplePicker.options) option.selected = draft.selectedPeople.includes(option.value);
  }
  if (draft.focusKey) {
    const target = host.querySelector<LiveFocusable>(`[data-live-focus-key="${draft.focusKey}"]`);
    if (target) {
      target.focus({preventScroll: true});
      if (draft.selection && typeof target.setSelectionRange === 'function') {
        target.setSelectionRange(draft.selection.start, draft.selection.end, draft.selection.direction || 'none');
      }
    }
  }
}

const LIVE_LOGS = ['.live-transcript', '.live-chat'];
function livePaintDetail(state: LiveMeetState, detail: LiveDetail): void {
  const host = $('.live-detail');
  if (state.paintedId) liveCaptureDraft(state, state.paintedId);
  const draft: LiveDraft = state.drafts[detail.id] || {};
  // The transcript and the chat are rebuilt with the card; each keeps its reader's place (ui/app/stick-scroll.ts).
  const logs = LIVE_LOGS.map(sel => {
    const el = state.paintedId === detail.id && host.querySelector(sel);
    if (!el) return null;
    const stick = stickScroll(el), was = {atBottom: stick.atBottom, top: el.scrollTop, count: el.children.length};
    stick.stop();
    return was;
  });
  const me = liveIsHumanJoined(detail);
  const active = detail.state !== 'ended';
  const controls = detail.owner_actor === `human:${S.me?.id || ''}`;
  const actor = `human:${S.me?.id || ''}`;
  const canAdd = controls || me || (detail.access || []).some(person => person.actor === actor);
  const people = [...new Map([...(detail.humans || []), ...(detail.access || [])].map((person): [string, LiveMember] => [person.actor, person])).values()];
  const peopleCandidates = (S.people || []).filter(person => !person.hidden
    && `human:${person.id}` !== detail.owner_actor
    && !people.some(member => member.actor === `human:${person.id}`));
  // A candidate's `id` is what the meeting's bots name; a server from before ids sent only `slug`, which held it.
  const candidates = state.candidates.map(bot => ({...bot, id: bot.id ?? bot.slug})).filter(bot => !(detail.bots || []).some(attached => attached.bot === bot.id));
  const chunks: LiveChunk[] = detail.chunks || [], chat: LiveChat[] = detail.chat || [], notices: LiveNotice[] = detail.notices || [], router: LiveRouter = detail.router || {windows: [], turns: []};
  // A teammate is named by the server (`name`); a bot the viewer's roster lacks would otherwise show its id.
  const teammates = (detail.bots || []).map(attached => `<span class="live-teammate">${esc(attached.name)}${active && (controls || attached.joined_by === actor) ?
    ` <button class="ghost" type="button" data-live-bot-remove="${esc(attached.bot)}" aria-label="Remove ${esc(attached.name)} from this meeting">Remove</button>` : ''}</span>`).join(' ');
  host.innerHTML = `<div class="live-screen" hidden><div class="muted">Screen · updates a few times a second</div><img alt="Shared screen" style="display:block;max-width:100%;max-height:65vh;margin:auto"></div><article class="live-card">
    <header class="live-card-head"><div><h2>${esc(detail.title)}</h2>${detail.client === 'tico-recorder' ? '<p class="muted">From Tico Recorder</p>' : ''}<span class="live-state ${esc(detail.state)}">${esc(detail.state)}</span></div>
      <div class="live-controls">${controls && detail.state === 'live' ? '<button class="ghost" data-live-control="pause">Pause</button><button class="ghost" data-live-control="disconnect">Disconnect</button>' : ''}
      ${controls && detail.state === 'paused' ? '<button class="ghost" data-live-control="resume">Resume</button>' : ''}
      ${controls && active ? '<button class="danger" data-live-control="end">End</button>' : ''}
      ${controls && detail.state === 'ended' && !detail.imported_meeting_id ? '<button class="primary" data-live-finalize>Finalize transcript</button>' : ''}</div></header>
    ${!me ? '<p class="live-join"><button class="primary" data-live-join>Join to chat</button></p>' : ''}
    ${canAdd && active ? `<form class="live-bot-picker" id="live-people-picker"><label>Add people<select name="people" data-live-focus-key="people-picker" multiple size="${Math.min(5, Math.max(2, peopleCandidates.length))}" aria-label="Choose people">
      ${peopleCandidates.map(person => `<option value="${esc(person.id)}">${esc(person.name || person.id)}</option>`).join('')}
      </select></label><button class="ghost" type="submit"${peopleCandidates.length ? '' : ' disabled'}>Add people</button></form>` : ''}
    ${canAdd && active ? `<form class="live-bot-picker" id="live-bot-picker"><label>Attach teammates<select name="bots" data-live-focus-key="bot-picker" multiple size="${Math.min(5, Math.max(2, candidates.length))}" aria-label="Choose visible bots">
      ${candidates.map(bot => `<option value="${esc(bot.id)}">${esc(bot.name)}${bot.team ? ' · ' + esc(bot.team) : ''}</option>`).join('')}
      </select></label><button class="ghost" type="submit"${candidates.length ? '' : ' disabled'}>Attach bots</button></form>` : ''}
    <div class="live-members"><span>People: ${people.map(person => `<span class="live-teammate">${esc(person.name || person.actor)}${active && controls && person.actor !== actor ?
      ` <button class="ghost" type="button" data-live-person-remove="${esc(person.actor.slice(6))}" aria-label="Remove ${esc(person.name || person.actor)} from this meeting">Remove</button>`
      : active && !controls && person.actor === actor ? ' <button class="ghost" type="button" data-live-leave>Leave</button>' : ''}</span>`).join(' ') || '—'}</span>
      <span>Teammates: ${teammates || '—'}</span></div>
    <section class="live-section"><h3>Transcript</h3><div class="live-transcript" aria-live="polite">${chunks.length ? chunks.map(chunk => `<p id="live-chunk-${chunk.seq}" data-seq="${chunk.seq}"><small>#${chunk.seq}${chunk.revision > 1 ? ' · revision ' + chunk.revision : ''} · ${esc(chunk.speaker || 'Speaker')}</small><span>${esc(chunk.text)}</span></p>`).join('') : '<p class="muted">Waiting for transcript text from Recorder.</p>'}</div></section>
    <section class="live-section"><h3>Chat</h3><div class="live-chat" aria-live="polite">${chat.length || notices.length ? liveChatRows(chat, notices) : '<p class="muted">No chat messages yet.</p>'}</div>
      ${me && active ? '<form id="live-chat-form" class="live-chat-form"><input name="text" data-live-focus-key="chat-input" maxlength="20000" required aria-label="Meeting chat" placeholder="Write to the meeting"><button class="primary" type="submit">Send</button></form>' : ''}</section>
    <section class="live-section"><h3>Router</h3><p class="muted">${detail.window_ms / 1000}s window · ${detail.cooldown_ms / 1000}s cooldown · up to ${detail.reply_cap ?? 5} turns per window</p>
      <div class="live-router">${router.windows?.length ? router.windows.map(window => `<details><summary>Window ${window.window_index + 1}: ${esc(window.outcome)} <small>${window.start_ms}–${window.end_ms} ms · ${window.trace?.ms ?? '—'} ms</small></summary><pre>${esc(JSON.stringify(window.trace || {}, null, 2))}</pre></details>`).join('') : '<p class="muted">No routing windows yet.</p>'}
      ${router.chat_routes?.length ? `<div class="live-chat-routes"><b>Chat routing</b>${router.chat_routes.map(route => `<details><summary>${esc(route.outcome || route.status)} · ${esc(route.started_at || '')} · ${route.trace?.ms ?? '—'} ms</summary><pre>${esc(JSON.stringify(route.trace || {}, null, 2))}</pre></details>`).join('')}</div>` : ''}
      ${router.bypasses?.length ? `<div class="live-bypasses"><b>Named mentions</b>${router.bypasses.map(bypass => `<pre>${esc(JSON.stringify(bypass, null, 2))}</pre>`).join('')}</div>` : ''}</div>
      ${router.turns?.length ? `<div class="live-turns"><b>Bot turns</b>${router.turns.map(turn => `<span>${esc(turn.name)} · ${esc(turn.status)}${turn.skip_reason ? ' · ' + esc(turn.skip_reason) : ''}</span>`).join('')}</div>` : ''}</section>
    ${detail.imported_meeting_id ? `<p class="live-finalized">Saved in Meetings: <a href="#/meetings?meeting=${encodeURIComponent(detail.imported_meeting_id)}">Open transcript</a></p>` : ''}
  </article>`;
  state.paintedId = detail.id;
  liveRestoreDraft(host, draft);
  host.querySelectorAll('[data-live-control]').forEach((button: HTMLElement) => button.onclick = () => void liveControl(state, detail.id, button.dataset.liveControl as LiveControl));
  host.querySelectorAll('[data-live-bot-remove]').forEach((button: HTMLElement) => button.onclick = () =>
    void liveRemoveBot(state, detail.id, button.dataset.liveBotRemove!));
  host.querySelectorAll('[data-live-transcript-seq]').forEach((button: HTMLElement) => button.onclick = () => {
    const target = host.querySelector(`[data-seq="${Number(button.dataset.liveTranscriptSeq)}"]`);
    if (target) { target.tabIndex = -1; target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest'}); }
  });
  host.querySelector('[data-live-finalize]')?.addEventListener('click', () => void liveFinalize(state, detail.id));
  host.querySelector('[data-live-join]')?.addEventListener('click', () => void liveJoin(state, detail.id));
  host.querySelector('#live-bot-picker')?.addEventListener('submit', (event: LiveFormEvent<LiveBotControls>) => {
    event.preventDefault(); const bots = [...event.currentTarget.elements.bots.selectedOptions].map(option => option.value);
    void liveAttach(state, detail.id, bots);
  });
  host.querySelectorAll('[data-live-person-remove]').forEach((button: HTMLElement) => button.onclick = () =>
    void liveAction(state, detail.id, '/access', {remove: [button.dataset.livePersonRemove!]}, 'Removed from this meeting.'));
  host.querySelector('[data-live-leave]')?.addEventListener('click', () => void liveLeave(state, detail));
  host.querySelector('#live-people-picker')?.addEventListener('submit', (event: LiveFormEvent<LivePeopleControls>) => {
    event.preventDefault(); const add = [...event.currentTarget.elements.people.selectedOptions].map(option => option.value);
    void liveAction(state, detail.id, '/access', {add}, 'People added.');
  });
  host.querySelector('#live-chat-form')?.addEventListener('submit', (event: LiveFormEvent<LiveChatControls>) => {
    event.preventDefault(); const input = event.currentTarget.elements.text;
    void liveChat(state, detail.id, input.value, input);
  });
  liveSubscribe(state, detail.id, detail.event_id || 0);
  LIVE_LOGS.forEach((sel, i) => {
    const el = host.querySelector(sel);
    if (el) stickScroll(el).restore(logs[i], {fresh: !!logs[i] && el.children.length > logs[i]!.count});
  });
}

// Chat messages and the bot notices (why a bot has not answered yet, or did not) in time order.
function liveChatRows(chat: LiveChat[], notices: LiveNotice[]): string {
  const rows: string[] = [];
  let next = 0;
  // The notice at the cursor; callers check the cursor is in range before taking one.
  const takeNotice = (): string => liveNotice(notices[next++]!);
  for (const message of chat) {
    while (next < notices.length && message.created_at && !(notices[next]!.created_at! > message.created_at)) rows.push(takeNotice());
    rows.push(`<p><b>${esc(liveActor(message.actor))}</b><span>${esc(message.text)}</span>${message.transcript_seq ? `<small><button type="button" class="live-transcript-link" data-live-transcript-seq="${message.transcript_seq}">Transcript #${message.transcript_seq}</button></small>` : ''}</p>`);
  }
  while (next < notices.length) rows.push(takeNotice());
  return rows.join('');
}

function liveNotice(notice: LiveNotice): string {
  const bot = botDisplayName(notice.bot);
  const text = notice.kind === 'deferred' ? (notice.reason === 'replying_in_meeting' ? 'Will answer this in the reply it is giving.' : 'Busy. Will answer after its current work.')
    : notice.kind === 'retried' ? 'Gave an empty reply. Asking again.'
    : liveNoticeDetail(notice.detail, bot, botSlug(notice.bot), notice.bot) || (notice.kind === 'waiting' ? 'Waiting for its computer.' : 'Could not answer.');
  return `<p class="live-notice ${notice.kind === 'failed' ? 'failed' : ''}" data-live-notice="${esc(notice.kind)}"><b>${esc(bot)}</b><span>${esc(text)}</span></p>`;
}

// The server's sentence can start with the bot's name (or slug), which the row already shows.
function liveNoticeDetail(detail: string | null | undefined, ...names: (string | undefined)[]): string {
  const text = String(detail || ''), lower = text.toLowerCase();
  const name = names.find(n => n && lower.startsWith(String(n).toLowerCase() + ' '));
  return name ? text.charAt(name.length + 1).toUpperCase() + text.slice(name.length + 2) : text;
}

function liveActor(actor: string): string {
  if (String(actor || '').startsWith('human:')) return personDisplay(actor.slice(6));
  if (String(actor || '').startsWith('bot:')) return botDisplayName(actor.slice(4));
  return String(actor || 'Unknown');
}

async function liveLoadDetail(state: LiveMeetState, id: string): Promise<void> {
  if (LIVE_MEETINGS !== state || state.busy) return;
  const version = (state.detailVersions[id] || 0) + 1;
  state.detailVersions[id] = version;
  try {
    const detail = await get<LiveDetail>(LIVE_API + '/' + encodeURIComponent(id));
    if (LIVE_MEETINGS !== state || state.busy || state.selected !== id || state.detailVersions[id] !== version) return;
    state.detail = detail;
    livePaintDetail(state, detail);
    if (detail.state === 'live') liveScreenStart(state, id);
    else liveScreenStop(state);
  } catch (error) {
    if (LIVE_MEETINGS !== state || state.selected !== id) return;
    const status = errorStatus(error);
    if (status !== undefined && [403, 404].includes(status)) {
      liveScreenStop(state);
      state.detail = null;
      $('.live-detail').innerHTML = '<div class="empty">This meeting is not available to you, or it does not exist.</div>';
    }
    liveStatus(state, errorMessage(error, 'Request failed.'));
  }
}

function liveSubscribe(state: LiveMeetState, id: string, after: number): void {
  if (state.sources.has(id) || !window.EventSource) return;
  const source = new EventSource(API + LIVE_API + '/' + encodeURIComponent(id) + '/events?after=' + encodeURIComponent(state.cursor[id] ?? after));
  state.sources.set(id, source);
  const types = ['meeting.state', 'meeting.access', 'meeting.joined', 'meeting.chunk', 'meeting.chunk_corrected', 'meeting.chat',
    'meeting.bot_joined', 'meeting.bot_left', 'meeting.bot_turn', 'meeting.bot_turn_claimed', 'meeting.bot_turn_skipped',
    'meeting.bot_turn_failed', 'meeting.bot_turn_waiting', 'meeting.bot_turn_deferred', 'meeting.bot_turn_retried',
    'meeting.bot_reply', 'meeting.router', 'meeting.finalized'];
  source.addEventListener('expired', () => { liveScreenStop(state); source.close(); void liveLoadDetail(state, id); });
  for (const type of types) source.addEventListener(type, (event: Event) => {
    if (type === 'meeting.state') liveScreenStop(state);
    // Access changes revalidate on the next frame request; they must not blank a static slide.
    state.cursor[id] = Number((event as MessageEvent).lastEventId) || state.cursor[id] || 0;
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => { if (LIVE_MEETINGS === state) void liveLoad(state); }, 150);
  });
}

async function liveAction(state: LiveMeetState, id: string, path: string, body: object, message: string): Promise<void> {
  if (state.busy) return;
  try { state.busy = true; await post(LIVE_API + '/' + encodeURIComponent(id) + path, body);
    liveStatus(state, message); state.busy = false; await liveLoad(state); }
  catch (error) { liveStatus(state, errorMessage(error, 'Request failed.')); }
  finally { state.busy = false; }
}
const liveJoin = (state: LiveMeetState, id: string) => liveAction(state, id, '/join', {}, 'You joined the meeting.');
const liveControl = (state: LiveMeetState, id: string, action: LiveControl) => liveAction(state, id, '/control', {action},
  action === 'end' ? 'Meeting ended.' : ({pause: 'Meeting paused.', disconnect: 'Recorder disconnected; meeting paused.', resume: 'Meeting resumed.'}[action] || 'Meeting updated.'));
const liveAttach = (state: LiveMeetState, id: string, bots: string[]) => liveAction(state, id, '/bots', {bots}, 'Teammates attached to this meeting.');
// Leaving answers {left: true}. A private meeting is then out of reach, so the list opens instead of a 403.
async function liveLeave(state: LiveMeetState, detail: LiveDetail): Promise<void> {
  if (state.busy) return;
  try {
    state.busy = true;
    await post(LIVE_API + '/' + encodeURIComponent(detail.id) + '/access', {remove: [S.me?.id || '']});
    state.busy = false;
    delete state.drafts[detail.id];
    if (detail.visibility !== 'team' && state.selected === detail.id) {
      liveScreenStop(state); state.sources.get(detail.id)?.close(); state.sources.delete(detail.id);
      state.pinned = state.selected = state.paintedId = ''; state.detail = null;
    }
    liveStatus(state, 'You left the meeting.');
    await liveLoad(state);
  } catch (error) { liveStatus(state, errorMessage(error, 'Request failed.')); }
  finally { state.busy = false; }
}
const liveFinalize = (state: LiveMeetState, id: string) => liveAction(state, id, '/finalize', {}, 'Transcript saved to Meetings.');

async function liveRemoveBot(state: LiveMeetState, id: string, bot: string): Promise<void> {
  if (state.busy) return;
  try {
    state.busy = true;
    await writeRequest('DELETE', LIVE_API + '/' + encodeURIComponent(id) + '/bots/' + encodeURIComponent(bot), {});
    liveStatus(state, `${botDisplayName(bot)} no longer has access to this meeting.`);
    state.busy = false;
    await liveLoad(state);
  } catch (error) { liveStatus(state, errorMessage(error, 'Request failed.')); }
  finally { state.busy = false; }
}

async function liveChat(state: LiveMeetState, id: string, text: string, input: HTMLInputElement): Promise<void> {
  if (state.busy || state.chatSending.has(id)) return;
  const submittedValue = String(text || '');
  const sentText = submittedValue.trim();
  if (!sentText) return;
  state.chatSending.add(id);
  try {
    state.busy = true;
    const sendButton = input.form?.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (sendButton) sendButton.disabled = true;
    await post(LIVE_API + '/' + encodeURIComponent(id) + '/chat', {text: sentText});
    if (input.isConnected && input.value === submittedValue) input.value = '';
    const current = $('.live-detail')?.querySelector('#live-chat-form input[name="text"]');
    const draft: LiveDraft = state.drafts[id] || {};
    if (current) draft.chatText = current.value;
    else if (input.value === submittedValue) draft.chatText = '';
    state.drafts[id] = draft;
    liveStatus(state, 'Message sent.'); state.busy = false; await liveLoad(state);
  }
  catch (error) { liveStatus(state, errorMessage(error, 'Request failed.')); }
  finally {
    state.busy = false; state.chatSending.delete(id);
    if (input.isConnected) {
      const sendButton = input.form?.querySelector<HTMLButtonElement>('button[type="submit"]');
      if (sendButton) sendButton.disabled = false;
    }
  }
}


// Frames have a separate bounded request loop; transcript refreshes never fetch media.
function liveScreenClear(state: LiveMeetState): void {
  if (state.screen?.url) URL.revokeObjectURL(state.screen.url);
  if (state.screen) { state.screen.url = ''; state.screen.after = ''; }
  const host = $('.live-screen');
  if (host) { host.hidden = true; host.querySelector('img')?.removeAttribute('src'); }
}
function liveScreenStop(state: LiveMeetState): void {
  state.screen?.abort.abort();
  state.screen?.request?.abort();
  state.screen?.statusRequest?.abort();
  clearTimeout(state.screen?.statusTimer);
  if (state.screen?.visibility) document.removeEventListener('visibilitychange', state.screen.visibility);
  clearTimeout(state.screen?.timer);
  liveScreenClear(state);
  state.screen = null;
}
function liveScreenStart(state: LiveMeetState, id: string): void {
  if (state.screen?.id === id) {
    const host = $('.live-screen');
    if (host && state.screen.url && !document.hidden) { host.hidden = false; host.querySelector('img').src = state.screen.url; }
    return;
  }
  liveScreenStop(state);
  const screen: LiveScreen = state.screen = {id, after: '', url: '', generation: '', abort: new AbortController(), running:false};
  const current = (): boolean => LIVE_MEETINGS === state && state.screen === screen && !screen.abort.signal.aborted;
  const next = async (): Promise<void> => {
    if (!current() || screen.running || !screen.generation) return;
    if (document.hidden) { liveScreenClear(state); screen.after = ''; return; }
    screen.running = true;
    const request = new AbortController();
    screen.request = request;
    let timeout = setTimeout(() => request.abort(), 4000);
    let delay = 2000;
    try {
      const response = await fetch(API + LIVE_API + '/' + encodeURIComponent(id) + '/screen/frame?wait=2&after=' + encodeURIComponent(screen.after),
        {credentials: 'same-origin', cache: 'no-store', signal: request.signal});
      if (!current()) return;
      if (response.status === 200) {
        clearTimeout(timeout);
        const size = Number(response.headers.get('Content-Length')) || 1_000_000;
        timeout = setTimeout(() => request.abort(), Math.min(15000, 4000 + size / 65536 * 1000));
        const version = response.headers.get('X-Screen-Version') || '';
        const blob = await response.blob();
        if (!current() || document.hidden || !screen.generation || !version.startsWith(screen.generation + ':')) { screen.after = ''; delay = 30; return; }
        liveScreenClear(state);
        screen.url = URL.createObjectURL(blob);
        screen.after = version;
        const host = $('.live-screen');
        if (host) { host.hidden = false; host.querySelector('img').src = screen.url; }
        delay = 30;
      } else if (response.status === 204) delay = 30;
      else { liveScreenClear(state); screen.after = ''; if ([401,403,404,410].includes(response.status)) screen.generation = ''; }
    } catch { liveScreenClear(state); screen.after = ''; }
    finally {
      clearTimeout(timeout); screen.running = false;
      if (current() && !document.hidden && screen.generation) screen.timer = setTimeout(next, delay);
    }
  };
  // Stop/revocation remains visible even while a large image downloads on a slow connection.
  const status = async (): Promise<void> => {
    if (!current() || document.hidden) return;
    const statusRequest = new AbortController();
    screen.statusRequest = statusRequest;
    const timeout = setTimeout(() => statusRequest.abort(), 2000);
    try {
      const response = await fetch(API + LIVE_API + '/' + encodeURIComponent(id) + '/screen/status',
        {credentials:'same-origin', cache:'no-store', signal: statusRequest.signal});
      if (!current()) return;
      if (response.ok) {
        const value = await response.json();
        if (screen.generation && screen.generation !== value.generation) { screen.request?.abort(); liveScreenClear(state); }
        screen.generation = value.generation || '';
        if (!screen.running && screen.generation) void next();
      } else { screen.generation = ''; screen.request?.abort(); liveScreenClear(state); }
    } catch { screen.generation = ''; screen.request?.abort(); liveScreenClear(state); }
    finally { clearTimeout(timeout); if (current() && !document.hidden) screen.statusTimer = setTimeout(status, screen.generation ? 1000 : 10000); }
  };
  screen.visibility = (): void => {
    clearTimeout(screen.timer); clearTimeout(screen.statusTimer);
    if (document.hidden) { screen.request?.abort(); screen.statusRequest?.abort(); liveScreenClear(state); screen.after = ''; }
    else { void status(); if (!screen.running) void next(); }
  };
  document.addEventListener('visibilitychange', screen.visibility);
  void status();
}
