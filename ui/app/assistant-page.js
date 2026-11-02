/* ui/app/assistant-page.js — The Assistant page: your private chat with the assistant
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the Assistant page (docs/assistant.md)
// #/assistant is laid out like a bot page: the same top line (no goal), thread and composer (ui/app/chat.js,
// ui/app/pill.js), and on a wide window the same dense right rail (Active, Updates, Files, Recurring) when the
// assistant has any of them; with none, the chat alone. GET /v2/assistant gives your own Assistant room with its
// messages; a message goes to /v2/assistant/messages, which answers a lookup at once and hands the rest to a turn of
// the assistant bot, shown live like any bot's reply (with files, /v2/uploads/assistant/messages). Proposals are
// Confirm / Cancel cards (ui/assistant.js). The page is the same for everyone, the owner
// included: the owner's earlier chat with the Strategy Bot, from before it became a helper, stays readable under
// "Earlier chats" (GET /v2/assistant `earlier`), above the Assistant thread with its own older messages.
// "What it has learned" at the top opens ui/app/learned.js.
const ASSISTANT_HINTS = ["What's waiting on me?", 'Find the launch plan', 'How do I add a bot?'];
let ASSISTANT_PILL = null, ASSISTANT_LOAD = 0;
function assistantPill() {
  const slug = myChatBot();
  if (ASSISTANT_PILL?.slug === slug) return ASSISTANT_PILL;
  if (ASSISTANT_PILL) PILLS.delete(ASSISTANT_PILL);
  ASSISTANT_PILL = pillRestore(makePill({mode: 'chat', slug, send: assistantSend,
    label: 'Message the Assistant', placeholder: 'Ask anything…'}));
  return ASSISTANT_PILL;
}
const assistantEmptyHTML = () => `<div class="asst-empty"><div class="asst-hints" aria-label="Suggestions">${
  ASSISTANT_HINTS.map(t => `<button class="ghost" type="button" data-hint="${esc(t)}">${esc(t)}</button>`).join('')}</div></div>`;
async function pageAssistant() {
  const slug = myChatBot(), load = ++ASSISTANT_LOAD;
  $('#main').classList.add('chat-layout', 'bot-chat-layout');
  $('#main').innerHTML = `
  <div class="bot-top asst-top" id="bot-top"><div class="bot-ident">${avatar(slug, 36, stateOf(slug))}
    <div class="botid"><div class="bot-nameline"><h1>${esc(myChatName())}</h1></div></div></div>
    <a class="ghost asst-learned" href="${LEARNED}">What it has learned</a></div>
  <div class="bot-work" id="bot-work"><div id="pane-chat">
    <details class="asst-earlier" id="asst-earlier" hidden><summary>Earlier chats</summary><div class="asst-earlier-list"></div></details>
    <section class="card conv" id="conv">
      <div class="conv-older" id="conv-older"></div>
      <div id="conv-thread" data-assistant><div class="empty">Loading…</div></div>
      <button type="button" class="stick-pill" id="conv-jump" hidden></button>
    </section>
    <div id="conv-paused" class="conv-paused" role="status" hidden></div>
    <div id="chat-composer"></div>
  </div>
  <aside id="pane-tasks" class="bot-rail" aria-label="${esc(myChatName())}" role="complementary" hidden>
    <section class="rail-sec bot-active" id="asst-active" aria-labelledby="asst-active-h" hidden><h2 class="rail-h" id="asst-active-h">Active</h2><div class="tpane"></div></section>
    <section class="rail-sec bot-latest" id="asst-latest" aria-label="Updates" hidden></section>
    <section class="rail-sec bot-files" id="asst-files" aria-label="Files" hidden></section>
  </aside></div>`;
  void assistantRail(slug, load);
  let d;
  try { d = await get('/v2/assistant'); }
  catch (e) {
    if (load === ASSISTANT_LOAD && $('#conv-thread')) $('#conv-thread').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    return;
  }
  if (load !== ASSISTANT_LOAD || S.route !== ASSISTANT) return;
  // Earlier chats are stored history: readable while the Assistant is off too.
  assistantEarlier(d.earlier, load);
  if (!d.available) {
    window.assistantChat?.off($('#conv-thread'), d, {post, esc, after: () => { if (S.route === ASSISTANT) pageAssistant(); }});
    return;
  }
  const P = assistantPill(), thread = $('#conv-thread');
  $('#chat-composer').append(P.el);
  chatDropZone($('#pane-chat'), P);
  bindChatSwipe(thread);
  thread.addEventListener('click', ev => { const b = ev.target.closest('[data-hint]'); if (b) assistantSend(P, b.dataset.hint); });
  void v2ChatLoad(slug, {conv: {id: d.room_id}, messages: d.messages, execution: d.execution, nextBefore: d.next_before,
                         empty: assistantEmptyHTML()});
}
async function assistantSend(P, text) {
  text = String(text || '').trim();
  const files = P.files.slice();
  if ((!text && !files.length) || P.sending) return;
  P.sending = true;
  const btn = pq(P, '.p-send'), state = V2C, scope = sessionScope(pq(P, '.p-text'));
  btn.disabled = true; pillBtnSay(btn, 'Sending…');
  try {
    const r = await cloudCompose('/v2/assistant/messages', {text: text || 'Attached files.'}, files);
    pillAcknowledge(P, text, files, scope);
    if (state && V2C === state) {
      // Your words show at once; a lookup's answer comes with them, a bot turn streams in like any reply.
      for (const m of [r.message, r.reply].filter(Boolean)) { state.messages.push(m); state.mine.push(m); }
      state.stick?.toBottom();
      if (!r.fast) { state.live = {text: ''}; state.execution = null; state.sentAt = Date.now(); }
      v2ChatRender(state);
      if (!r.fast) v2ChatStream(state);
    }
  } catch (e) { toast(e.message, true); }
  finally { P.sending = false; pillBtnSay(btn, ''); pillLabel(P); pillButtons(P); }
}

// "Earlier chats": the owner's earlier chat with the Strategy Bot, by its name, read-only, newest page first and older
// pages on request. Loaded when opened; nothing is sent from here.
function assistantEarlier(earlier, load) {
  const box = $('#asst-earlier');
  if (!box) return;
  box.hidden = !earlier?.room_id;
  if (box.hidden) return;
  const list = box.querySelector('.asst-earlier-list');
  box.querySelector('summary').textContent = 'Earlier chats with ' + (earlier.name || 'Strategy Bot');
  let before = null, busy = false;
  const line = m => {
    const mine = m.from_actor === 'human:' + (S.me?.id || '');
    return `<div class="asst-earlier-msg"><b>${esc(mine ? 'You' : earlier.name || 'Assistant')}</b>
      <span class="muted" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</span><div class="md">${safeMd(m.body || '', {shortLinks: true})}</div></div>`;
  };
  const more = async () => {
    if (busy) return;
    busy = true;
    try {
      const q = before ? `?before=${encodeURIComponent(before)}` : '';
      const page = await get(`/v2/conversations/${encodeURIComponent(earlier.room_id)}/messages${q}`);
      if (load !== ASSISTANT_LOAD || !list.isConnected) return;
      list.querySelector('[data-earlier-more]')?.remove();
      list.insertAdjacentHTML('afterbegin', (page.messages || []).filter(m => !m.deleted_at).map(line).join('')
        || (before ? '' : '<div class="muted">No messages.</div>'));
      before = page.next_before;
      if (page.has_more && before) list.insertAdjacentHTML('afterbegin', '<button class="ghost" type="button" data-earlier-more>Show older</button>');
    } catch (e) { list.insertAdjacentHTML('afterbegin', `<div class="err">${esc(e.message)}</div>`); }
    finally { busy = false; }
  };
  box.ontoggle = () => { if (box.open && !list.childElementCount) void more(); };
  list.onclick = ev => { if (ev.target.closest('[data-earlier-more]')) void more(); };
}

// The rail: the assistant's active tasks, its latest update and its files, each only when it has
// some, as on a bot page (ui/app/bot-page.js); the whole rail only when one of them shows and the window is wide.
function assistantRailFit() {
  const rail = $('#pane-tasks');
  if (S.route !== ASSISTANT || !rail || !$('#asst-active')) return;
  const some = [...rail.querySelectorAll(':scope>.rail-sec')].some(el => !el.hidden);
  const split = some && BOT_WIDE.matches;
  if (rail.hidden === split) rail.hidden = !split;   // only on a change: the rail's own observer sees every write
  $('#main').classList.toggle('bot-split-layout', split);
}
let ASSISTANT_RAIL_WATCH = false;
async function assistantRail(slug, load) {
  const rail = $('#pane-tasks');
  if (!ASSISTANT_RAIL_WATCH) { ASSISTANT_RAIL_WATCH = true; BOT_WIDE.addEventListener('change', assistantRailFit); }   // bot-page.js loads after this file
  new MutationObserver(assistantRailFit).observe(rail, {subtree: true, attributes: true, attributeFilter: ['hidden']});
  window.botFiles?.mount($('#asst-files'), {slug, get, esc, openFile: openFileLink});
  const [owned, latest] = await Promise.all([v2Get(`/v2/tasks?owner=${encodeURIComponent(slug)}&status=${V2_ACTIVE.join(',')}&brief=true`),
    v2Get(`/v2/updates?bot=${encodeURIComponent(slug)}&limit=1`)]);
  if (load !== ASSISTANT_LOAD || S.route !== ASSISTANT || !rail.isConnected) return;
  const active = (owned?.tasks || []).filter(t => V2_ACTIVE.includes(String(t.status)))
    .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')));
  const act = $('#asst-active');
  act.querySelector('.tpane').innerHTML = `<div class="bot-task-list">${active.map(t => v2TaskRow(t, slug)).join('')}</div>`;
  act.hidden = !active.length;
  const u = latest?.updates?.[0], up = $('#asst-latest');
  if (u) {
    up.innerHTML = `<header class="rail-head"><h2 class="rail-h">Updates</h2>
        <span class="rail-age" title="${esc(fmt(u.updated_at || u.created_at))}">${esc(ago(u.updated_at || u.created_at))}</span>
        <span class="spacer"></span><a class="rail-ico" href="${UPDATES}" aria-label="All updates" title="All updates"><span class="nav-icon" aria-hidden="true">dynamic_feed</span></a></header>
      <div class="upd-body md">${safeMd(u.body || '', {shortLinks: true})}</div>`;
    up.hidden = false;
  }
  assistantRailFit();
}
