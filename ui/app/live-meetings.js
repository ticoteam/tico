/* ui/app/live-meetings.js — Live transcript, chat, org-chart picker and persisted router trace. */
'use strict';

let LIVE_MEETINGS = null;
const LIVE_API = '/v2/live-meetings';

function liveMeetingsStop() {
  if (!LIVE_MEETINGS) return;
  clearInterval(LIVE_MEETINGS.poll);
  clearTimeout(LIVE_MEETINGS.refreshTimer);
  for (const source of LIVE_MEETINGS.sources.values()) source.close();
  LIVE_MEETINGS.sources.clear();
  LIVE_MEETINGS = null;
}
window.liveMeetingsStop = liveMeetingsStop;

async function pageLiveMeetings() {
  meetStop();
  const state = LIVE_MEETINGS = {list: [], selected: '', detail: null, sources: new Map(), cursor: {},
    poll: 0, refreshTimer: 0, candidates: [], busy: false, drafts: {}, paintedId: '',
    listVersion: 0, detailVersions: {}, chatSending: new Set()};
  $('#main').innerHTML = `<section class="live-page">
    <header class="live-head"><div><h1>Live now</h1><p class="muted">Transcript text stays in Tico. Audio is never sent.</p></div>
      <button type="button" class="ghost" id="live-back">Meetings</button></header>
    <form id="live-connect" class="live-connect"><label>Meeting title<input name="title" required maxlength="200" placeholder="Weekly team sync"></label>
      <label>Bot reply cap<input name="reply_cap" type="number" min="1" max="20" value="3" required></label>
      <label>Reply cooldown (seconds)<input name="cooldown_seconds" type="number" min="0" max="3600" value="60" required></label>
      <button class="primary" type="submit">Connect live meeting</button></form>
    <div class="live-layout"><nav class="live-list" aria-label="Live meetings"><div class="empty">Loading live meetings…</div></nav>
      <section class="live-detail"><div class="empty">Connect or choose a live meeting.</div></section></div>
    <div class="live-status" role="status" aria-live="polite"></div>
  </section>`;
  $('#live-back').onclick = () => pageNotes();
  $('#live-connect').onsubmit = event => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    void liveConnect(state, {title: form.get('title'), reply_cap: Number(form.get('reply_cap')),
      cooldown_seconds: Number(form.get('cooldown_seconds'))});
  };
  await liveCandidates(state);
  await liveLoad(state);
  if (LIVE_MEETINGS !== state) return;
  state.poll = setInterval(() => liveLoad(state), 5000);
}

async function liveCandidates(state) {
  try { const response = await get(LIVE_API + '/bot-candidates'); state.candidates = response.bots || []; }
  catch (error) { liveStatus(state, 'Could not load the visible bot list: ' + error.message); }
}

function liveStatus(state, text) {
  const el = $('.live-status');
  if (el && LIVE_MEETINGS === state) el.textContent = text || '';
}

async function liveConnect(state, options) {
  if (state.busy) return;
  state.busy = true;
  try {
    const clientId = newRequestId();
    const meeting = await post(LIVE_API, {...options, title: String(options.title || '').trim(), client_id: clientId});
    state.selected = meeting.id;
    liveStatus(state, 'Connected. Transcript sharing with the team is on.');
    state.busy = false;
    await liveLoad(state);
  } catch (error) { liveStatus(state, error.message); }
  finally { state.busy = false; }
}

async function liveLoad(state) {
  if (LIVE_MEETINGS !== state || state.busy) return;
  const version = ++state.listVersion;
  try {
    const response = await get(LIVE_API);
    if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion) return;
    state.list = response.meetings || [];
    if (state.selected && !state.list.some(meeting => meeting.id === state.selected)) {
      // Ended meetings leave Live now; keep their transcript open until the user returns to Meetings.
      const selected = state.selected;
      try { state.detail = await get(LIVE_API + '/' + encodeURIComponent(selected)); }
      catch { if (state.selected === selected) { state.selected = ''; state.detail = null; } }
      if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion || state.selected !== selected) return;
    }
    if (!state.selected && state.list.length) state.selected = state.list[0].id;
    if (LIVE_MEETINGS !== state || state.busy || version !== state.listVersion) return;
    livePaintList(state);
    if (state.selected) await liveLoadDetail(state, state.selected);
    else if (!state.detail) $('.live-detail').innerHTML = '<div class="empty">No live meetings. Connect when everyone is ready to share transcript text.</div>';
  } catch (error) {
    if (LIVE_MEETINGS === state) $('.live-list').innerHTML = `<div class="err">${esc(error.message)}</div>`;
  }
}

function livePaintList(state) {
  const host = $('.live-list');
  if (!state.list.length) { host.innerHTML = '<div class="empty">No connected meetings.</div>'; return; }
  host.innerHTML = state.list.map(meeting => `<button type="button" class="live-list-item${meeting.id === state.selected ? ' selected' : ''}" data-live-id="${esc(meeting.id)}">
    <b>${esc(meeting.title)}</b><span class="live-state ${esc(meeting.state)}">${esc(meeting.state)}</span>
    <small>${meeting.seq || 0} transcript chunks · ${meeting.bots?.length || 0} bots</small></button>`).join('');
  host.querySelectorAll('[data-live-id]').forEach(button => button.onclick = () => {
    state.selected = button.dataset.liveId; state.detail = null; livePaintList(state); void liveLoadDetail(state, state.selected);
  });
}

function liveIsHumanJoined(detail) {
  const actor = `human:${S.me?.id || ''}`;
  return (detail.humans || []).some(member => member.actor === actor);
}

function liveCaptureDraft(state, id) {
  const host = $('.live-detail');
  if (!host || !id) return;
  const prior = state.drafts[id] || {};
  const chat = host.querySelector('#live-chat-form input[name="text"]');
  const picker = host.querySelector('#live-bot-picker select[name="bots"]');
  const active = host.contains(document.activeElement) ? document.activeElement : null;
  const draft = {...prior};
  if (chat) draft.chatText = chat.value;
  if (picker) draft.selectedBots = [...picker.selectedOptions].map(option => option.value);
  if (active?.dataset.liveFocusKey) {
    draft.focusKey = active.dataset.liveFocusKey;
    if (typeof active.selectionStart === 'number') {
      draft.selection = {start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection};
    } else draft.selection = null;
  } else { delete draft.focusKey; delete draft.selection; }
  state.drafts[id] = draft;
}

function liveRestoreDraft(host, draft) {
  const chat = host.querySelector('#live-chat-form input[name="text"]');
  const picker = host.querySelector('#live-bot-picker select[name="bots"]');
  if (chat && typeof draft.chatText === 'string') chat.value = draft.chatText;
  if (picker && Array.isArray(draft.selectedBots)) {
    const selected = new Set(draft.selectedBots);
    for (const option of picker.options) option.selected = selected.has(option.value);
  }
  if (draft.focusKey) {
    const target = host.querySelector(`[data-live-focus-key="${draft.focusKey}"]`);
    if (target) {
      target.focus({preventScroll: true});
      if (draft.selection && typeof target.setSelectionRange === 'function') {
        target.setSelectionRange(draft.selection.start, draft.selection.end, draft.selection.direction || 'none');
      }
    }
  }
}

function livePaintDetail(state, detail) {
  const host = $('.live-detail');
  if (state.paintedId) liveCaptureDraft(state, state.paintedId);
  const draft = state.drafts[detail.id] || {};
  const me = liveIsHumanJoined(detail);
  const active = detail.state !== 'ended';
  const controls = detail.owner_actor === `human:${S.me?.id || ''}`;
  const candidates = state.candidates.filter(bot => !(detail.bots || []).some(attached => attached.bot === bot.slug));
  const chunks = detail.chunks || [], chat = detail.chat || [], router = detail.router || {windows: [], turns: []};
  const teammates = (detail.bots || []).map(attached => `<span class="live-teammate">${esc(attached.bot)}${me && active ?
    ` <button class="ghost" type="button" data-live-bot-remove="${esc(attached.bot)}" aria-label="Remove ${esc(attached.bot)} from this meeting">Remove</button>` : ''}</span>`).join(' ');
  host.innerHTML = `<article class="live-card">
    <header class="live-card-head"><div><h2>${esc(detail.title)}</h2><span class="live-state ${esc(detail.state)}">${esc(detail.state)}</span></div>
      <div class="live-controls">${controls && detail.state === 'live' ? '<button class="ghost" data-live-control="pause">Pause</button><button class="ghost" data-live-control="disconnect">Disconnect</button>' : ''}
      ${controls && detail.state === 'paused' ? '<button class="ghost" data-live-control="resume">Resume</button>' : ''}
      ${controls && active ? '<button class="danger" data-live-control="end">End</button>' : ''}
      ${controls && detail.state === 'ended' && !detail.imported_meeting_id ? '<button class="primary" data-live-finalize>Finalize transcript</button>' : ''}</div></header>
    ${!me ? '<p class="live-join"><button class="primary" data-live-join>Join to chat</button></p>' : ''}
    ${me && active ? `<form class="live-bot-picker" id="live-bot-picker"><label>Attach teammates<select name="bots" data-live-focus-key="bot-picker" multiple size="${Math.min(5, Math.max(2, candidates.length))}" aria-label="Choose visible bots">
      ${candidates.map(bot => `<option value="${esc(bot.slug)}">${esc(bot.name)}${bot.team ? ' · ' + esc(bot.team) : ''}</option>`).join('')}
      </select></label><button class="ghost" type="submit"${candidates.length ? '' : ' disabled'}>Attach bots</button></form>` : ''}
    <div class="live-members"><span>People: ${(detail.humans || []).map(person => esc(person.name || person.actor)).join(', ') || '—'}</span>
      <span>Teammates: ${teammates || '—'}</span></div>
    <section class="live-section"><h3>Transcript</h3><div class="live-transcript" aria-live="polite">${chunks.length ? chunks.map(chunk => `<p id="live-chunk-${chunk.seq}" data-seq="${chunk.seq}"><small>#${chunk.seq}${chunk.revision > 1 ? ' · revision ' + chunk.revision : ''} · ${esc(chunk.speaker || 'Speaker')}</small><span>${esc(chunk.text)}</span></p>`).join('') : '<p class="muted">Waiting for transcript text from Recorder.</p>'}</div></section>
    <section class="live-section"><h3>Chat</h3><div class="live-chat" aria-live="polite">${chat.length ? chat.map(message => `<p><b>${esc(liveActor(message.actor))}</b><span>${esc(message.text)}</span>${message.transcript_seq ? `<small><button type="button" class="live-transcript-link" data-live-transcript-seq="${message.transcript_seq}">Transcript #${message.transcript_seq}</button></small>` : ''}</p>`).join('') : '<p class="muted">No chat messages yet.</p>'}</div>
      ${me && active ? '<form id="live-chat-form" class="live-chat-form"><input name="text" data-live-focus-key="chat-input" maxlength="20000" required aria-label="Meeting chat" placeholder="Write to the meeting"><button class="primary" type="submit">Send</button></form>' : ''}</section>
    <section class="live-section"><h3>Router</h3><p class="muted">${detail.window_ms / 1000}s window · ${detail.cooldown_ms / 1000}s cooldown · up to ${detail.reply_cap ?? 3} turns per window</p>
      <div class="live-router">${router.windows?.length ? router.windows.map(window => `<details><summary>Window ${window.window_index + 1}: ${esc(window.outcome)} <small>${window.start_ms}–${window.end_ms} ms · ${window.trace?.ms ?? '—'} ms</small></summary><pre>${esc(JSON.stringify(window.trace || {}, null, 2))}</pre></details>`).join('') : '<p class="muted">No routing windows yet.</p>'}
      ${router.chat_routes?.length ? `<div class="live-chat-routes"><b>Chat routing</b>${router.chat_routes.map(route => `<details><summary>${esc(route.outcome || route.status)} · ${esc(route.started || '')} · ${route.trace?.ms ?? '—'} ms</summary><pre>${esc(JSON.stringify(route.trace || {}, null, 2))}</pre></details>`).join('')}</div>` : ''}
      ${router.bypasses?.length ? `<div class="live-bypasses"><b>Named mentions</b>${router.bypasses.map(bypass => `<pre>${esc(JSON.stringify(bypass, null, 2))}</pre>`).join('')}</div>` : ''}</div>
      ${router.turns?.length ? `<div class="live-turns"><b>Bot turns</b>${router.turns.map(turn => `<span>${esc(turn.bot)} · ${esc(turn.status)}${turn.skip_reason ? ' · ' + esc(turn.skip_reason) : ''}</span>`).join('')}</div>` : ''}</section>
    ${detail.imported_meeting_id ? `<p class="live-finalized">Saved in Meetings: <a href="#/meetings?meeting=${encodeURIComponent(detail.imported_meeting_id)}">Open transcript</a></p>` : ''}
  </article>`;
  state.paintedId = detail.id;
  liveRestoreDraft(host, draft);
  host.querySelectorAll('[data-live-control]').forEach(button => button.onclick = () => void liveControl(state, detail.id, button.dataset.liveControl));
  host.querySelectorAll('[data-live-bot-remove]').forEach(button => button.onclick = () =>
    void liveRemoveBot(state, detail.id, button.dataset.liveBotRemove));
  host.querySelectorAll('[data-live-transcript-seq]').forEach(button => button.onclick = () => {
    const target = host.querySelector(`[data-seq="${Number(button.dataset.liveTranscriptSeq)}"]`);
    if (target) { target.tabIndex = -1; target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest'}); }
  });
  host.querySelector('[data-live-finalize]')?.addEventListener('click', () => void liveFinalize(state, detail.id));
  host.querySelector('[data-live-join]')?.addEventListener('click', () => void liveJoin(state, detail.id));
  host.querySelector('#live-bot-picker')?.addEventListener('submit', event => {
    event.preventDefault(); const bots = [...event.currentTarget.elements.bots.selectedOptions].map(option => option.value);
    void liveAttach(state, detail.id, bots);
  });
  host.querySelector('#live-chat-form')?.addEventListener('submit', event => {
    event.preventDefault(); const input = event.currentTarget.elements.text;
    void liveChat(state, detail.id, input.value, input);
  });
  liveSubscribe(state, detail.id, detail.event_id || 0);
  const transcript = host.querySelector('.live-transcript'); if (transcript) transcript.scrollTop = transcript.scrollHeight;
}

function liveActor(actor) {
  if (String(actor || '').startsWith('human:')) return personDisplay(actor.slice(6));
  if (String(actor || '').startsWith('bot:')) return botDisplayName(actor.slice(4));
  return String(actor || 'Unknown');
}

async function liveLoadDetail(state, id) {
  if (LIVE_MEETINGS !== state || state.busy) return;
  const version = (state.detailVersions[id] || 0) + 1;
  state.detailVersions[id] = version;
  try {
    const detail = await get(LIVE_API + '/' + encodeURIComponent(id));
    if (LIVE_MEETINGS !== state || state.busy || state.selected !== id || state.detailVersions[id] !== version) return;
    state.detail = detail;
    livePaintDetail(state, detail);
  } catch (error) { liveStatus(state, error.message); }
}

function liveSubscribe(state, id, after) {
  if (state.sources.has(id) || !window.EventSource) return;
  const source = new EventSource(API + LIVE_API + '/' + encodeURIComponent(id) + '/events?after=' + encodeURIComponent(state.cursor[id] ?? after));
  state.sources.set(id, source);
  const types = ['meeting.state', 'meeting.joined', 'meeting.chunk', 'meeting.chunk_corrected', 'meeting.chat',
    'meeting.bot_joined', 'meeting.bot_left', 'meeting.bot_turn', 'meeting.bot_turn_claimed', 'meeting.bot_turn_skipped',
    'meeting.bot_reply', 'meeting.router', 'meeting.finalized'];
  for (const type of types) source.addEventListener(type, event => {
    state.cursor[id] = Number(event.lastEventId) || state.cursor[id] || 0;
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => { if (LIVE_MEETINGS === state) void liveLoad(state); }, 150);
  });
}

async function liveAction(state, id, path, body, message) {
  if (state.busy) return;
  try { state.busy = true; await post(LIVE_API + '/' + encodeURIComponent(id) + path, body);
    liveStatus(state, message); state.busy = false; await liveLoad(state); }
  catch (error) { liveStatus(state, error.message); }
  finally { state.busy = false; }
}
const liveJoin = (state, id) => liveAction(state, id, '/join', {}, 'You joined the meeting.');
const liveControl = (state, id, action) => liveAction(state, id, '/control', {action},
  action === 'end' ? 'Meeting ended.' : ({pause: 'Meeting paused.', disconnect: 'Recorder disconnected; meeting paused.', resume: 'Meeting resumed.'}[action] || 'Meeting updated.'));
const liveAttach = (state, id, bots) => liveAction(state, id, '/bots', {bots}, 'Teammates attached to this meeting.');
const liveFinalize = (state, id) => liveAction(state, id, '/finalize', {}, 'Transcript saved to Meetings.');

async function liveRemoveBot(state, id, bot) {
  if (state.busy) return;
  try {
    state.busy = true;
    await writeRequest('DELETE', LIVE_API + '/' + encodeURIComponent(id) + '/bots/' + encodeURIComponent(bot), {});
    liveStatus(state, `${bot} no longer has access to this meeting.`);
    state.busy = false;
    await liveLoad(state);
  } catch (error) { liveStatus(state, error.message); }
  finally { state.busy = false; }
}

async function liveChat(state, id, text, input) {
  if (state.busy || state.chatSending.has(id)) return;
  const sentText = String(text || '').trim();
  if (!sentText) return;
  state.chatSending.add(id);
  try {
    state.busy = true;
    const sendButton = input.form?.querySelector('button[type="submit"]');
    if (sendButton) sendButton.disabled = true;
    await post(LIVE_API + '/' + encodeURIComponent(id) + '/chat', {text: sentText});
    if (input.isConnected && input.value === sentText) input.value = '';
    const current = $('.live-detail')?.querySelector('#live-chat-form input[name="text"]');
    const draft = state.drafts[id] || {};
    if (current) draft.chatText = current.value;
    else if (input.value === sentText) draft.chatText = '';
    state.drafts[id] = draft;
    liveStatus(state, 'Message sent.'); state.busy = false; await liveLoad(state);
  }
  catch (error) { liveStatus(state, error.message); }
  finally {
    state.busy = false; state.chatSending.delete(id);
    if (input.isConnected) {
      const sendButton = input.form?.querySelector('button[type="submit"]');
      if (sendButton) sendButton.disabled = false;
    }
  }
}
