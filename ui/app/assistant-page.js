/* ui/app/assistant-page.js — The Assistant page: your private chat with the assistant
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the Assistant page (docs/assistant.md)
// #/assistant is laid out like a bot page: the same top line (no goal), thread and composer (ui/app/chat.js,
// ui/app/pill.js), and on a wide window the same dense right rail (Active, Updates, Files, Recurring) when the
// assistant has any of them; with none, the chat alone. GET /v2/assistant gives your own Assistant room with its
// messages; a message goes to /v2/assistant/messages, which answers a lookup at once and hands the rest to a turn of the assistant bot, shown
// live like any bot's reply. Proposals are Confirm / Cancel cards (ui/assistant.js). The room takes text only, so
// the composer has no attach button.
const ASSISTANT_HINTS = ["What's waiting on me?", 'Find the launch plan', 'How do I add a bot?'];
let ASSISTANT_PILL = null, ASSISTANT_DRAFT = '', ASSISTANT_LOAD = 0;
// "Ask the Assistant…" in search: the words go into the box, now if the page is open, else when it opens.
function assistantPrefill(text) {
  const box = ASSISTANT_PILL?.el?.isConnected ? pq(ASSISTANT_PILL, '.p-text') : null;
  if (!box) { ASSISTANT_DRAFT = text; return; }
  box.value = text;
  box.dispatchEvent(new Event('input'));
  box.focus();
}
function assistantPill() {
  const slug = assistantBot();
  if (ASSISTANT_PILL?.slug === slug) return ASSISTANT_PILL;
  if (ASSISTANT_PILL) PILLS.delete(ASSISTANT_PILL);
  return ASSISTANT_PILL = pillRestore(makePill({mode: 'chat', slug, files: false, send: assistantSend,
    label: 'Message the Assistant', placeholder: 'Ask anything…'}));
}
const assistantEmptyHTML = () => `<div class="asst-empty"><div class="asst-hints" aria-label="Suggestions">${
  ASSISTANT_HINTS.map(t => `<button class="ghost" type="button" data-hint="${esc(t)}">${esc(t)}</button>`).join('')}</div></div>`;
async function pageAssistant() {
  const slug = assistantBot(), load = ++ASSISTANT_LOAD;
  $('#main').classList.add('chat-layout', 'bot-chat-layout');
  $('#main').innerHTML = `
  <div class="bot-top asst-top" id="bot-top"><div class="bot-ident">${avatar(slug, 36, stateOf(slug))}
    <div class="botid"><div class="bot-nameline"><h1>${esc(assistantName())}</h1></div></div></div></div>
  <div class="bot-work" id="bot-work"><div id="pane-chat">
    <section class="card conv" id="conv">
      <div class="conv-older" id="conv-older"></div>
      <div id="conv-thread" data-assistant><div class="empty">Loading…</div></div>
      <button type="button" class="stick-pill" id="conv-jump" hidden></button>
    </section>
    <div id="conv-paused" class="conv-paused" role="status" hidden></div>
    <div id="chat-composer"></div>
  </div>
  <aside id="pane-tasks" class="bot-rail" aria-label="${esc(assistantName())}" role="complementary" hidden>
    <section class="rail-sec bot-active" id="asst-active" aria-labelledby="asst-active-h" hidden><h2 class="rail-h" id="asst-active-h">Active</h2><div class="tpane"></div></section>
    <section class="rail-sec bot-latest" id="asst-latest" aria-label="Updates" hidden></section>
    <section class="rail-sec bot-files" id="asst-files" aria-label="Files" hidden></section>
    <section class="rail-sec bot-recurring" id="asst-recurring" aria-label="Recurring" hidden></section>
  </aside></div>`;
  void assistantRail(slug, load);
  let d;
  try { d = await get('/v2/assistant'); }
  catch (e) {
    if (load === ASSISTANT_LOAD && $('#conv-thread')) $('#conv-thread').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    return;
  }
  if (load !== ASSISTANT_LOAD || S.route !== ASSISTANT) return;
  if (!d.available) {
    window.assistantChat?.off($('#conv-thread'), d, {post, esc, after: () => { if (S.route === ASSISTANT) pageAssistant(); }});
    return;
  }
  const P = assistantPill(), thread = $('#conv-thread');
  $('#chat-composer').append(P.el);
  if (ASSISTANT_DRAFT) { assistantPrefill(ASSISTANT_DRAFT); ASSISTANT_DRAFT = ''; }
  bindChatSwipe(thread);
  thread.addEventListener('click', ev => { const b = ev.target.closest('[data-hint]'); if (b) assistantSend(P, b.dataset.hint); });
  void v2ChatLoad(slug, {conv: {id: d.room_id}, messages: d.messages, execution: d.execution, nextBefore: d.next_before,
                         empty: assistantEmptyHTML()});
}
async function assistantSend(P, text) {
  text = String(text || '').trim();
  if (!text || P.sending) return;
  P.sending = true;
  const btn = pq(P, '.p-send'), state = V2C;
  btn.disabled = true; pillBtnSay(btn, 'Sending…');
  try {
    const r = await post('/v2/assistant/messages', {text});
    pillAcknowledge(P, text, []);
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

// The rail: the assistant's active tasks, its latest update, its files and its routines, each only when it has
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
  const rail = $('#pane-tasks'), e = S.emps.find(x => x.name === slug);
  if (!ASSISTANT_RAIL_WATCH) { ASSISTANT_RAIL_WATCH = true; BOT_WIDE.addEventListener('change', assistantRailFit); }   // bot-page.js loads after this file
  new MutationObserver(assistantRailFit).observe(rail, {subtree: true, attributes: true, attributeFilter: ['hidden']});
  if (e) {
    const rec = $('#asst-recurring');
    rec.innerHTML = botRecurringHTML(e, slug);
    rec.hidden = !rec.innerHTML;
  }
  window.botFiles?.mount($('#asst-files'), {slug, get, esc, openFile: openFileLink});
  const [owned, latest] = await Promise.all([v2Get(`/v2/tasks?owner=${encodeURIComponent(slug)}&status=${V2_ACTIVE.join(',')}&brief=true`),
    v2Get(`/v2/updates?bot=${encodeURIComponent(slug)}&limit=1`)]);
  if (load !== ASSISTANT_LOAD || S.route !== ASSISTANT || !rail.isConnected) return;
  const active = (owned?.tasks || []).filter(t => V2_ACTIVE.includes(String(t.status)))
    .sort((a, b) => String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || '')));
  const act = $('#asst-active');
  act.querySelector('.tpane').innerHTML = `<div class="bot-task-list">${active.map(t => v2TaskRow(t, slug)).join('')}</div>`;
  act.hidden = !active.length;
  const u = latest?.updates?.[0], up = $('#asst-latest');
  if (u) {
    up.innerHTML = `<header class="rail-head"><h2 class="rail-h">Updates</h2>
        <span class="rail-age" title="${esc(fmt(u.updated || u.created))}">${esc(ago(u.updated || u.created))}</span>
        <span class="spacer"></span><a class="rail-ico" href="${UPDATES}" aria-label="All updates" title="All updates"><span class="nav-icon" aria-hidden="true">dynamic_feed</span></a></header>
      <div class="upd-body md">${safeMd(u.body || '', {shortLinks: true})}</div>`;
    up.hidden = false;
  }
  assistantRailFit();
}
