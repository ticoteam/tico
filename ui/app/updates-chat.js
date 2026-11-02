/* ui/app/updates-chat.js — The Updates chat: the person's own chat bot beside the Updates page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The Updates chat (docs/assistant.md; spec: Strategy Bot v3, section 10.8). On a desktop it is a right rail beside the
// feed, named for the person's Assistant, the owner's too. The header's button shows or hides it, remembered per
// person (on this device and as preference ui.updates_chat, the newer copy winning, as ui/app/rails.js keeps widths); its edge resizes it like the other right rails (rails.js `chat`). On a
// phone the same button opens it full screen, and its close button or Back shuts it.
// It is the same conversation as the menu tab: GET /v2/assistant reads it and POST /v2/assistant/messages sends with
// `place: "updates"`, so that turn is handed the updates this person may read (backend/updates_chat.py). It keeps its own
// small state, like the Ask panel, never the shared chat state the bot pages use (chat.js V2C, CHAT_CACHE), so the two
// never clash; sending drops the menu tab's cached copy so it reads the new messages when it opens.
// A link to an update in a reply stays on the page: it scrolls to the card and highlights it (updates.js updGoTo).
const UPDC_KEY = 'tico.updates.chat', UPDC_PREF = 'ui.updates_chat';
const UPDC_HINT = "What's the top update today?";
let UPDC = null;
const updcPhone = () => matchMedia('(max-width:760px)').matches;
const updcKey = () => UPDC_KEY + '.' + (S.me?.id || 'me');
function updcSaved() {
  try { const v = JSON.parse(localStorage.getItem(updcKey()) || 'null'); return v && typeof v === 'object' ? v : null; } catch { return null; }
}
// Shown unless this person hid it.
const updcShown = () => updcSaved()?.shown !== false;
function updcKeep(shown) {
  const v = {shown, at: Date.now()};
  try { localStorage.setItem(updcKey(), JSON.stringify(v)); } catch {}
  if (S.me?.cloud) void post('/v2/preferences/' + UPDC_PREF, {value: v}).catch(() => {});
}
async function updcSync() {
  if (!S.me?.cloud) return;
  const v = (await v2Get('/v2/preferences/' + UPDC_PREF))?.value, local = updcSaved();
  const at = Number(v?.at) || 0, localAt = Number(local?.at) || 0;
  if (v && at > localAt) {
    try { localStorage.setItem(updcKey(), JSON.stringify({shown: v.shown !== false, at})); } catch {}
    updcLayout();
  } else if (local && at < localAt) void post('/v2/preferences/' + UPDC_PREF, {value: local}).catch(() => {});
}
// The header's button (pageUpdates).
function updcButtonHTML() {
  const name = myChatName();
  return `<button type="button" class="upd-chip upd-chat-btn" id="upd-chat-btn" aria-controls="upd-chat" aria-pressed="false" aria-label="${esc(name)}" title="Show or hide ${esc(name)}"><span class="nav-icon" aria-hidden="true">forum</span><span class="upd-lbl">${esc(name)}</span></button>`;
}
// Called by pageUpdates each time it draws: the rail (kept, with its conversation, when only the day or kind changed)
// and the layout for this window and this person's choice.
function updcMount() {
  const main = $('#main');
  if (!UPDC?.el?.isConnected) {
    updcStop();
    main.insertAdjacentHTML('beforeend', railEdgeHTML('chat') + `<aside class="upd-chat" id="upd-chat" role="complementary" hidden>
      <header class="updc-head"><h2></h2><a class="updc-open" href="${ASSISTANT}">Open chat</a>
        <button type="button" class="upd-act updc-close" data-updc-close><span class="updc-x" aria-hidden="true">×</span></button></header>
      <div class="updc-thread" data-updc-thread role="log" tabindex="0"><p class="muted">Loading conversation…</p></div>
      <div class="updc-form"></div>
      <p class="err" data-updc-error role="alert" hidden></p></aside>`);
    UPDC = {el: $('#upd-chat'), mounted: false, stop: null};
    UPDC.el.querySelector('[data-updc-close]').onclick = () => updcPhone() ? updcClose() : updcToggle(false);
    UPDC.el.addEventListener('click', updcClick);
    void updcSync();
  }
  const name = myChatName();
  UPDC.el.setAttribute('aria-label', name);
  UPDC.el.querySelector('.updc-head h2').textContent = name;
  UPDC.el.querySelector('[data-updc-close]').setAttribute('aria-label', 'Hide ' + name);
  $('#upd-chat-btn').onclick = () => updcPhone() ? updcOpen() : updcToggle(!updcShown());
  updcLayout();
}
function updcLayout() {
  const el = UPDC?.el; if (!el?.isConnected) return;
  const phone = updcPhone(), full = phone && document.body.classList.contains('upd-chat-full');
  // An Assistant that is off and that this person cannot turn on: no rail and no button.
  const shown = !UPDC.unusable && (phone ? full : updcShown());
  const button = $('#upd-chat-btn'); if (button) button.hidden = !!UPDC.unusable;
  $('#main').classList.toggle('upd-chat-layout', !phone && shown);
  el.hidden = !shown;
  $('#upd-chat-btn')?.setAttribute('aria-pressed', String(!phone && shown));
  if (!phone) document.body.classList.remove('upd-chat-full');
  if (shown && !UPDC.mounted) { UPDC.mounted = true; void updcLoad(UPDC); }
}
function updcToggle(shown) {
  updcKeep(shown);
  updcLayout();
  if (shown) UPDC?.el.querySelector('.updc-form .p-text')?.focus();
  else $('#upd-chat-btn')?.focus();
}
// A phone: full screen over the page, with its own history entry so Back closes it.
function updcOpen() {
  document.body.classList.add('upd-chat-full');
  if (!history.state?.updChat) history.pushState({...(history.state || {}), updChat: 1}, '');
  updcLayout();
  UPDC?.el.querySelector('.updc-form .p-text')?.focus();
}
// Settles once its history entry is gone, so a link followed after it is not undone by that step back.
function updcClose(fromBack = false) {
  if (!document.body.classList.contains('upd-chat-full')) return Promise.resolve();
  document.body.classList.remove('upd-chat-full');
  updcLayout();
  if (fromBack || !history.state?.updChat) return Promise.resolve();
  return new Promise(done => { addEventListener('popstate', () => done(), {once: true}); history.back(); });
}
addEventListener('popstate', () => { if (!history.state?.updChat) updcClose(true); });
addEventListener('resize', () => updcLayout());
// Leaving Updates (router.js): its live events stop and the phone's full screen goes.
function updcStop() {
  UPDC?.stop?.();
  if (UPDC?.composer) PILLS.delete(UPDC.composer);
  UPDC = null;
  document.body.classList.remove('upd-chat-full');
}
// A link to an update stays on the page (on a phone the chat closes first); any other link goes as usual.
function updcClick(ev) {
  const hint = ev.target.closest('[data-updc-hint]');
  if (hint) { ev.preventDefault(); void UPDC?.send?.(hint.dataset.updcHint); return; }
  const a = ev.target.closest('a[href]');
  const href = a?.getAttribute('href') || '';
  if (!href.startsWith(UPDATES + '?') || !new URLSearchParams(href.split('?')[1]).get('update')) return;
  ev.preventDefault();
  if (updcPhone()) void updcClose().then(() => updGoLink(href));
  else updGoLink(href);
}
async function updcLoad(chat) {
  const el = chat.el, thread = el.querySelector('[data-updc-thread]'), error = el.querySelector('[data-updc-error]');
  const current = () => UPDC === chat && el.isConnected;
  const fail = text => { if (!current()) return; error.textContent = text || ''; error.hidden = !text; };
  let info = null, messages = [], execution = null, rendered = '', es = null, poll = null;
  const stopStream = () => { try { es?.(); } catch { /* released */ } es = null; clearInterval(poll); poll = null; };
  chat.stop = stopStream;
  const isReply = m => m.sender_id === 'bot:' + info?.bot;
  const draw = () => {
    if (!current()) return;
    const rows = messages.map(m => v2MessageHTML({...m, run: null}));
    const live = execution && execution.state !== 'completed' ? execution.text : '';
    if (live) rows.push(`<div data-updc-live>${v2MessageHTML({from_actor: 'bot:' + info.bot, body: live})}</div>`);
    else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status">${esc(execution?.label || 'Waiting for reply…')}</p>`);
    const html = rows.join('') || `<div class="updc-empty"><p class="muted">Ask ${esc(info.name)} about today's updates.</p>
      <button class="ghost" type="button" data-updc-hint="${esc(UPDC_HINT)}">${esc(UPDC_HINT)}</button></div>`;
    if (html === rendered) return;
    stickScroll(thread).update(() => {
      thread.innerHTML = html;
      if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload: snapshot});
    }, {fresh: !!rendered});
    rendered = html;
  };
  const snapshot = async () => {
    if (!info?.room_id || !current()) return;
    try {
      const d = await get(`/v2/conversations/${encodeURIComponent(info.room_id)}/snapshot`);
      if (!current()) return;
      messages = d.messages || []; execution = d.execution || null; draw(); fail('');
    } catch (e) { fail(e.message); }
  };
  // A change to this conversation reads its snapshot again (ui/app/live.js); without EventSource, a poll.
  const watch = () => {
    stopStream();
    if (!info?.room_id || !current()) return;
    const fallback = liveFallback(snapshot, 5000);
    if (fallback) { poll = fallback; return; }
    const cid = info.room_id, mine = d => d.conversation_id === cid && current();
    const soon = () => liveSoon('updc:' + cid, () => void snapshot(), 150);
    const offs = [liveFollow(cid), liveOn('messages', d => { if (mine(d)) soon(); }),
                  liveOn('runs', d => { if (mine(d)) soon(); }), liveOn('reset', () => { if (current()) soon(); })];
    es = () => offs.forEach(off => off());
  };
  const send = async text => {
    text = String(text || '').trim();
    const P = chat.composer;
    if (!text || !P || P.sending) return;
    P.sending = true; pillButtons(P); fail('');
    try {
      const r = await post('/v2/assistant/messages', {text, place: 'updates'});
      CHAT_CACHE.delete(info.bot);                    // the menu tab reads the conversation again when it opens
      if (!current()) return;
      for (const m of [r.message, r.reply].filter(Boolean)) if (!messages.some(x => x.id === m.id)) messages.push(m);
      if (r.message?.conversation_id && r.message.conversation_id !== info.room_id) { info.room_id = r.message.conversation_id; watch(); }
      pillAcknowledge(P, text, []); stickScroll(thread).toBottom(); draw();
      if (!es && !poll) watch();
      await snapshot();
    } catch (e) { fail(e.message); }
    finally { P.sending = false; if (current()) pillButtons(P); }
  };
  chat.send = send;
  try { info = await get('/v2/assistant'); }
  catch (e) { if (current()) thread.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (!current()) return;
  info.name ||= myChatName();
  el.querySelector('.updc-head h2').textContent = info.name;
  el.setAttribute('aria-label', info.name);
  thread.setAttribute('aria-label', info.name + ' conversation');
  if (!info.available) {
    if (!info.can_turn_on) { chat.unusable = true; if (updcPhone()) void updcClose(); updcLayout(); return; }
    window.assistantChat?.off(thread, info, {post, esc, after: () => { if (current()) { chat.mounted = false; void updcLoad(chat); } }});
    return;
  }
  messages = info.messages || []; execution = info.execution || null;
  chat.composer = makePill({mode: 'chat', files: false, label: 'Message ' + info.name, placeholder: 'Ask about the updates…',
                            send: (P, text) => send(text)});
  pq(chat.composer, '.p-text').maxLength = 4000;
  el.querySelector('.updc-form').replaceChildren(chat.composer.el);
  draw();
  watch();
}
