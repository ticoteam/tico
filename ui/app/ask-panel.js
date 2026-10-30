/* ui/app/ask-panel.js — Ask: the person's own chat bot, a tap away on every page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Ask (docs/assistant.md). The one way in to the Assistant, to the helpers behind it (BotOps, the Strategy Bot, the
// Librarian), and to Tico support. On a computer a round button at the bottom left of the page (or ⌘J) opens a panel
// above it; on a phone the Ask tab opens it as a sheet over the page, and the tab closes it again. It is the same conversation as the Assistant
// page (#/assistant, "Show chat"): GET /v2/assistant reads it and POST /v2/assistant/messages sends with the page the
// person is on (`page`), so the turn knows what "this" means. Like the Updates chat it keeps its own small state, never
// the bot pages' (chat.js V2C), and drops the Assistant page's cached copy when it sends.
// The + adds Tico support (free) or the Tico team (full management) to the conversation: both open Support
// (ui/support.js). While a request is open it is in this chat: its messages flow in with the Assistant's as green
// bubbles, its name at the top shows only them (and the box then writes to support), and its × takes it out.
// What waits on the person from anyone else comes at the end as cards: a Confirm card a bot such as BotOps left in its
// own chat (GET /v2/assistant `pending`), and bots' questions (Needs you), answered here or by telling the Assistant.
const ASK_HIDDEN = 'tico.ask.hidden';
const ASK_MANAGE = "We'd like Tico to fully manage our company.";
let ASK = null;
const ASK_CARDS_MAX = 3;
const askPhone = () => matchMedia('(max-width:760px)').matches;
const askHiddenKey = () => ASK_HIDDEN + '.' + (S.me?.id || 'me');
function askHiddenTickets() {
  try { const v = JSON.parse(localStorage.getItem(askHiddenKey()) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; }
}
// The page the person is looking at: its heading, else the address.
function askPage() {
  const path = S.route || location.hash || '';
  const heading = $('#main h1, #main h2')?.textContent.trim().replace(/\s+/g, ' ') || '';
  return {path: path.slice(0, 300), title: heading.slice(0, 200)};
}
// The Assistant page is this chat already, and setup has no room for it.
const askAway = () => !S.me || S.route === ASSISTANT || document.body.classList.contains('in-setup');
function askSync() {
  const fab = $('#ask-fab'), tab = $('#mobile-ask'), panel = $('#ask-panel');
  if (!fab || !panel) return;
  const name = myChatName(), away = askAway(), open = !!ASK?.open && !away;
  fab.hidden = away;
  panel.hidden = !open;
  document.body.classList.toggle('ask-open', open);
  fab.setAttribute('aria-expanded', String(open));
  fab.setAttribute('aria-label', open ? 'Close ' + name : name);
  fab.title = name + ' (⌘J)';
  // The phone's tab is a glyph with no word under it: Ask's mark, or a close mark while the sheet is open.
  if (tab) {
    const icon = open ? 'close' : 'auto_awesome';
    tab.setAttribute('aria-expanded', String(open));
    tab.setAttribute('aria-label', open ? 'Close' : 'Ask');
    tab.querySelector('.mobile-nav-icon').textContent = icon;
  }
  panel.setAttribute('aria-label', name);
  panel.querySelector('#ask-title').textContent = name;
  if (!open) return;
  const page = askPage(), sees = panel.querySelector('[data-ask-sees]');
  sees.hidden = !page.title;
  sees.querySelector('[data-ask-sees-name]').textContent = page.title;
  sees.title = 'Sees ' + page.title;
  askMembers();
}
function askToggle(open = !ASK?.open, focus = true) {
  ASK ||= {open: false, mounted: false};
  ASK.open = open;
  askMenu(false);
  askSync();
  if (open && !ASK.mounted) { ASK.mounted = true; void askLoad(ASK); }
  else if (open) void ASK.refresh?.();       // cards answered or confirmed elsewhere meanwhile
  // A phone keeps its keyboard down until the person taps the box.
  if (open && focus && !askPhone()) setTimeout(() => $('#ask-panel .ask-form .p-text')?.focus(), 0);
  else if (!open && focus && $('#ask-panel')?.contains(document.activeElement)) (askPhone() ? $('#mobile-ask') : $('#ask-fab'))?.focus();
}
// Opens the panel with words already in its box (Add bot's "connecting an agent"); words already typed stay.
function askDraft(text) {
  ASK ||= {open: false, mounted: false};
  ASK.draft = text;
  askToggle(true);
  askFillDraft(ASK);
}
function askFillDraft(chat) {
  const box = chat.draft && chat.composer ? pq(chat.composer, '.p-text') : null;
  if (!box) return;
  if (!box.value.trim()) { box.value = chat.draft; box.dispatchEvent(new Event('input', {bubbles: true})); }
  chat.draft = '';
}
function askMenu(open) {
  const menu = $('#ask-panel [data-ask-menu]'), add = $('#ask-add');
  if (!menu || !add) return;
  menu.hidden = !open;
  add.setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector('button')?.focus();
}
// Tico support while a request is open and not taken out of the panel.
function askMembers() {
  const strip = $('#ask-panel [data-ask-members]');
  if (!strip) return;
  const hidden = askHiddenTickets(), open = (window.supportState?.().open || []).filter(t => !hidden.includes(t.id));
  strip.hidden = !open.length;
  if (!open.length) { if (ASK?.only) askOnly(false); return; }
  const ticket = open[0], unread = open.filter(t => t.unread).length;
  strip.dataset.ticket = ticket.id;
  const chip = strip.querySelector('[data-ask-ticket]');
  chip.textContent = 'Tico support' + (unread ? ` · ${unread} new` : '');
  chip.setAttribute('aria-pressed', String(!!ASK?.only));
  chip.title = ASK?.only ? 'Show the whole chat' : 'Show only Tico support';
}
// The request in this chat, whole (its messages), or null.
function askTicket() {
  const id = $('#ask-panel [data-ask-members]:not([hidden])')?.dataset.ticket;
  return id ? window.supportTicket?.(id) || null : null;
}
// Only Tico support in the thread, and the box writes to it; or the whole chat again.
function askOnly(on) {
  if (!ASK) return;
  ASK.only = on && !!askTicket();
  $('#ask-panel')?.classList.toggle('ask-only', ASK.only);
  const box = ASK.composer && pq(ASK.composer, '.p-text');
  if (box) {
    box.placeholder = ASK.only ? 'Message Tico support' : 'Ask anything…';
    box.setAttribute('aria-label', ASK.only ? 'Message Tico support' : 'Message ' + myChatName());
  }
  $('#ask-panel [data-ask-ticket]')?.setAttribute('aria-pressed', String(ASK.only));
  if (ASK.only) void window.supportPoll?.();
  ASK.draw?.();
}
function askDrop(id) {
  const ids = [...new Set([...askHiddenTickets(), id])].slice(-50);
  try { localStorage.setItem(askHiddenKey(), JSON.stringify(ids)); } catch {}
  askMembers();
  askOnly(false);
}
// Support opening from the panel: on a computer the + keeps focus, so closing Support lands back there; a phone has
// room for one sheet, so Ask's gives way to Support's and focus waits on the Ask tab.
function askHandOff() {
  askMenu(false);
  if (askPhone()) { askToggle(false, false); $('#mobile-ask')?.focus(); }
  else $('#ask-add').focus();
}
function askClick(ev) {
  const t = ev.target;
  if (t.closest('[data-ask-close]')) { askToggle(false); return; }
  if (t.closest('#ask-add')) { askMenu($('#ask-panel [data-ask-menu]').hidden); return; }
  if (t.closest('#ask-support')) { askHandOff(); window.supportOpen?.(); return; }
  if (t.closest('#ask-manage')) { askHandOff(); window.supportStart?.(ASK_MANAGE); return; }
  if (t.closest('[data-ask-ticket]')) { askOnly(!ASK?.only); return; }
  const pick = t.closest('[data-ask-pick]');
  if (pick) { void askAnswer(pick.closest('[data-ask-q]'), {pick: Number(pick.dataset.askPick)}); return; }
  if (t.closest('[data-ask-drop]')) { askDrop($('#ask-panel [data-ask-members]').dataset.ticket); return; }
  const hint = t.closest('[data-ask-hint]');
  if (hint) { ev.preventDefault(); void ASK?.send?.(hint.dataset.askHint); return; }
  // A link followed from the panel: on a phone the sheet gets out of the way of the page it opens.
  const a = t.closest('a[href^="#/"]');
  if (a && askPhone() && !a.getAttribute('href').startsWith('#/support')) askToggle(false, false);
}
// Tico support's messages as the thread draws them: green bubbles, the person's on the right.
function askSupportRows(t) {
  const bubble = (body, staff, at) => ({at, html: `<div class="ask-sup ${staff ? 'them' : 'you'}"><span class="ask-sup-who">${
    staff ? 'Tico support' : 'You · to Tico support'}</span><div class="ask-sup-body">${esc(body)}</div></div>`});
  return [bubble(t.message, false, t.created), ...(t.messages || []).map(m => bubble(m.body, m.from === 'staff', m.created))];
}
// Bots' questions to this person (Needs you), the first few: one pick of two to six answered with a tap, any other
// question with a line of words; several picks at once, or several questions, open the task.
function askQuestions() {
  const me = myActor();
  return (S.v2?.needs || []).filter(it => ['question', 'ask'].includes(it.kind) && it.ask?.to_actor === me
    && it.ask.from_actor !== me).slice(0, ASK_CARDS_MAX);
}
function askQuestionHTML(it) {
  const q = it.kind === 'question' ? qcardQuestion(it) : null, who = qcardAsker(it.ask.from_actor);
  const body = `<div class="ask-card-who">${esc(who)} asks</div><div class="ask-card-q">${esc(plainActors(q ? q.question : it.ask.body))}</div>`;
  const structured = it.kind === 'question' && (it.ask.refs?.questions || []).length;
  const answer = q?.multi || (structured && !q) ? `<a href="#/task/${encodeURIComponent(it.id)}">Answer</a>`
    : q ? `<div class="ask-picks">${q.options.map((o, i) => `<button type="button" class="ghost" data-ask-pick="${i}">${esc(o.label)}</button>`).join('')}</div>`
    : `<form class="ask-reply" data-ask-reply><input name="text" maxlength="4000" autocomplete="off" placeholder="Answer ${esc(who)}" aria-label="Answer ${esc(who)}"><button type="submit" class="ghost">Send</button></form>`;
  return `<div class="ask-card" data-ask-q="${esc(it.id)}">${body}${answer}</div>`;
}
// The answer goes where Decide sends it (decide.js): a pick to the task's answers, words to a loose question's answer
// or as a comment on the task, which wakes its bot.
async function askAnswer(card, {pick, text}) {
  const it = (S.v2?.needs || []).find(x => String(x.id) === card?.dataset.askQ);
  if (!it || card.dataset.busy) return;
  const q = it.kind === 'question' ? qcardQuestion(it) : null, who = qcardAsker(it.ask.from_actor);
  const writes = pick !== undefined && q ? qcardWrites(it, q, [q.options[pick].label])
    : it.kind === 'ask' ? [{path: `/v2/messages/${encodeURIComponent(it.id)}/answer`, body: {text}}]
    : [{path: `/v2/tasks/${encodeURIComponent(it.id)}/comments`, body: {text}}];
  card.dataset.busy = '1';
  card.querySelectorAll('button, input').forEach(el => { el.disabled = true; });
  try {
    for (const w of writes) await post(w.path, w.body);
    card.innerHTML = `<div class="ask-card-who">${esc(who)}</div><div class="muted">Answered</div>`;
    toast('Sent to ' + who);
    await v2Refresh();                              // Needs you without it: the card leaves the chat
    ASK?.draw?.();
  } catch (e) {
    toast(e.message, true);
    delete card.dataset.busy;
    card.querySelectorAll('button, input').forEach(el => { el.disabled = false; });
  }
}
async function askLoad(chat) {
  const el = $('#ask-panel'), thread = el.querySelector('[data-ask-thread]'), error = el.querySelector('[data-ask-error]');
  const current = () => ASK === chat && chat.mounted;
  const fail = text => { if (!current()) return; error.textContent = text || ''; error.hidden = !text; };
  let info = null, messages = [], execution = null, rendered = '', es = null, poll = null;
  const stopStream = () => { try { es?.(); } catch { /* released */ } es = null; clearInterval(poll); poll = null; };
  chat.stop = stopStream;
  const isReply = m => m.from_actor === 'bot:' + info?.bot;
  let pending = [], readFor = '';
  const draw = () => {
    if (!current() || !info?.available) return;
    const ticket = askTicket(), only = !!chat.only && !!ticket, at = x => Date.parse(x || '') || 0;
    const rows = (only ? [] : messages.slice(-30).map(m => ({at: m.created, html: v2MessageHTML({...m, run: null})})))
      .concat(ticket ? askSupportRows(ticket) : [])
      .map((r, i) => ({...r, i})).sort((a, b) => at(a.at) - at(b.at) || a.i - b.i).map(r => r.html);
    if (!only) {
      const live = execution && execution.state !== 'completed' ? execution.text : '';
      if (live) rows.push(`<div data-ask-live>${v2MessageHTML({from_actor: 'bot:' + info.bot, body: live})}</div>`);
      else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status">${esc(execution?.label || 'Thinking…')}</p>`);
      // What waits on the person: Confirm cards left in other chats (BotOps'), then bots' questions.
      const here = new Set(messages.map(m => m.refs?.action).filter(Boolean));
      rows.push(...pending.filter(a => !here.has(a.id)).map(a =>
        `<div class="conv-run chat"><div data-action-host="${esc(a.id)}"><div class="asst-state">Loading the card…</div></div></div>`));
      rows.push(...askQuestions().map(askQuestionHTML));
    }
    const html = rows.join('') || (only ? '<p class="muted">No messages yet.</p>' : `<div class="ask-empty">${ASSISTANT_HINTS.map(h =>
      `<button class="ghost" type="button" data-ask-hint="${esc(h)}">${esc(h)}</button>`).join('')}</div>`);
    // A reply from support seen here is read, as in Support itself.
    const seen = ticket?.unread && chat.open && !$('#ask-panel').hidden && document.hasFocus() ? ticket.id + ':' + (ticket.messages || []).length : '';
    if (seen && seen !== readFor) { readFor = seen; void window.supportRead?.(ticket.id); }
    if (html === rendered) return;
    stickScroll(thread).update(() => {
      thread.innerHTML = html;
      if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload: snapshot});
    }, {fresh: !!rendered});
    rendered = html;
  };
  chat.draw = draw;
  // The whole chat again, with the Confirm cards waiting in other chats.
  const snapshot = async () => {
    if (!info?.room_id || !current()) return;
    try {
      const d = await get('/v2/assistant');
      if (!current()) return;
      messages = d.messages || []; execution = d.execution || null; pending = d.pending || []; draw(); fail('');
    } catch (e) { fail(e.message); }
  };
  chat.refresh = snapshot;
  const watch = () => {
    stopStream();
    if (!info?.room_id || !current()) return;
    const fallback = liveFallback(snapshot, 5000);
    if (fallback) { poll = fallback; return; }
    const cid = info.room_id, mine = d => d.conversation_id === cid && current();
    const soon = () => liveSoon('ask:' + cid, () => void snapshot(), 150);
    const offs = [liveFollow(cid), liveOn('messages', d => { if (mine(d)) soon(); }),
                  liveOn('runs', d => { if (mine(d)) soon(); }), liveOn('reset', () => { if (current()) soon(); })];
    es = () => offs.forEach(off => off());
  };
  const send = async text => {
    text = String(text || '').trim();
    const P = chat.composer;
    if (!text || !P || P.sending) return;
    P.sending = true; pillButtons(P); fail('');
    const ticket = chat.only && askTicket();
    if (ticket) {
      try { await window.supportReply(ticket.id, text); if (current()) { pillAcknowledge(P, text, []); stickScroll(thread).toBottom(); draw(); } }
      catch (e) { fail('Not sent. ' + e.message); }
      finally { P.sending = false; if (current()) pillButtons(P); }
      return;
    }
    try {
      const r = await post('/v2/assistant/messages', {text, page: askPage()});
      CHAT_CACHE.delete(info.bot);                    // the Assistant page reads the conversation again when it opens
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
  catch (e) { chat.mounted = false; if (ASK === chat) thread.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (!current()) return;
  info.name ||= myChatName();
  thread.setAttribute('aria-label', info.name + ' conversation');
  if (!info.available) {
    // Support stays a tap away in the + even then.
    if (!info.can_turn_on) { thread.innerHTML = `<p class="muted">${esc(info.name)} is off. Ask the team owner to turn it on.</p>`; return; }
    window.assistantChat?.off(thread, info, {post, esc, after: () => { if (current()) void askLoad(chat); }});
    return;
  }
  messages = info.messages || []; execution = info.execution || null; pending = info.pending || [];
  chat.composer = makePill({mode: 'chat', files: false, label: 'Message ' + info.name, placeholder: 'Ask anything…',
                            send: (P, text) => send(text)});
  pq(chat.composer, '.p-text').maxLength = 4000;
  el.querySelector('.ask-form').replaceChildren(chat.composer.el);
  askFillDraft(chat);
  draw();
  watch();
  if (chat.open && !askPhone()) pq(chat.composer, '.p-text').focus();
}
$('#ask-fab')?.addEventListener('click', () => askToggle());
$('#mobile-ask')?.addEventListener('click', () => askToggle());
$('#ask-panel')?.addEventListener('click', askClick);
$('#ask-panel')?.addEventListener('submit', ev => {
  const form = ev.target.closest('[data-ask-reply]');
  if (!form) return;
  ev.preventDefault();
  const text = form.elements.text.value.trim();
  if (text) void askAnswer(form.closest('[data-ask-q]'), {text});
});
document.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'j' && !askAway()) { ev.preventDefault(); askToggle(); return; }
  if (ev.key !== 'Escape' || !ASK?.open || document.querySelector('dialog[open]')) return;
  if (!$('#ask-panel [data-ask-menu]').hidden) { askMenu(false); $('#ask-add').focus(); return; }
  if ($('#ask-panel').contains(document.activeElement)) askToggle(false);
});
// "Ask to add or edit your bots" (getting started) and any other [data-ask-open] opens the panel.
document.addEventListener('click', ev => {
  if (!ev.target.closest?.('[data-ask-open]') || askAway()) return;
  ev.preventDefault(); askToggle(true);
});
// Any link to Support (a reply's "Loop in Tico support", "Talk to the Tico team") opens it over the page.
document.addEventListener('click', ev => {
  const a = ev.target.closest?.('a[href^="#/support"]');
  if (!a) return;
  ev.preventDefault();
  if (a.getAttribute('href').includes('manage')) window.supportStart?.(ASK_MANAGE); else window.supportOpen?.();
}, true);
document.addEventListener('tico:support', () => { askMembers(); ASK?.draw?.(); });
liveOn('needs-list', () => ASK?.draw?.());
addEventListener('hashchange', () => setTimeout(askSync, 0));
addEventListener('resize', () => askSync());
