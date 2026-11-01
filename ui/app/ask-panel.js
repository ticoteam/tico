/* ui/app/ask-panel.js — Ask: the person's own chat bot, a tap away on every page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Ask (docs/assistant.md). The one way in to the Assistant ("Tico Assistant" here), to the helpers behind it (BotOps,
// the Strategy Bot, the Librarian), and to Tico support. On a computer the "Ask Tico" row at the bottom of the sidebar
// (or ⌘J) opens a panel beside the rail, rising from the bottom left; on a phone the Liquid Dot in the middle of the tab bar opens it as a sheet over
// the page, and closes it again. It is the same conversation as the Assistant page (#/assistant): GET /v2/assistant
// reads it and POST /v2/assistant/messages sends with the page the person is on (`page`), so the turn knows what "this"
// means. Like the Updates chat it keeps its own small state, never the bot pages' (chat.js V2C), and drops the
// Assistant page's cached copy when it sends.
// "Loop in Support", a link at the top right, brings Tico support into the chat over its existing requests
// (ui/support.js, ask-rules.js askSupportState): while one of the person's open requests is in the chat its messages
// flow in with the Assistant's, the box writes to it, and its chip in the header takes it out again (the link hides
// meanwhile). Bringing it in shares the chat (ask-rules.js askSupportSwitch): its last 20 turns, scrubbed of keys, go
// to the request (a new one, sent at once, when none is open). Out, nothing more from this chat goes; the request stays
// open. Support itself (every request, a new one) opens from any #/support link.
// "Your requests", beside it, opens Support's list over the page (Ask stays where it was); it reads the count of unread
// replies. A request Loop in Support files gets a line that diagnostics weren't sent, with "Attach diagnostics" (once
// per request; ui/support.js supportAttach opens Review and edit, and nothing is sent until Send).
// What waits on the person from anyone else sits in the thread by when it was asked, like every other line: a Confirm
// card a bot such as BotOps left in its own chat (GET /v2/assistant `pending`), and bots' questions (Needs you),
// answered here or by telling the Assistant. A peek beside the closed dot says, in Tico's words, that something waits.
const ASK_HIDDEN = 'tico.ask.hidden';
const ASK_PEEKS = 'tico.ask.peek';
const ASK_MANAGE = "We'd like Tico to fully manage our company.";
const ASK_SUP_IN = 'You added Tico Support. They can now see chats.';
const ASK_SUP_OUT = 'Tico Support can no longer see chats.';
const ASK_NO_DIAG = "Diagnostics weren't sent.";
const ASK_LOOP_NOTE = 'tico.ask.loopNote';
let ASK = null;
const ASK_CARDS_MAX = 3;
// The panel's name: "Tico Assistant", or the name a team gave its assistant.
const askName = () => myChatName() === CONFIG_DEFAULTS.assistant_name ? 'Tico Assistant' : myChatName();
const ASK_LOADED = Date.now();
const askPhone = () => matchMedia('(max-width:760px)').matches;
const askHiddenKey = () => ASK_HIDDEN + '.' + (S.me?.id || 'me');
function askHiddenTickets() {
  try { const v = JSON.parse(localStorage.getItem(askHiddenKey()) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; }
}
function askSetHidden(ids) {
  try { localStorage.setItem(askHiddenKey(), JSON.stringify([...new Set(ids)].slice(-50))); } catch { /* this page only */ }
}
// The page the person is looking at: its heading, else the address.
function askPage() {
  const path = S.route || location.hash || '';
  const heading = $('#main h1, #main h2')?.textContent.trim().replace(/\s+/g, ' ') || '';
  return {path: path.slice(0, 300), title: heading.slice(0, 200)};
}
// The Assistant page is this chat already, and setup has no room for it.
const askAway = () => !S.me || S.route === ASSISTANT || document.body.classList.contains('in-setup');
// Open or closed is a class, never `hidden`: the app's `:has(… :not([hidden]))` rules make any change of a `hidden`
// attribute restyle the whole page (6-30 ms on a real hub), and a panel shown from display:none lays itself out cold
// (11-45 ms). Closed, the panel keeps its box but draws nothing, is inert and is out of the accessibility tree
// (ask.css). Nothing else on the way in or out writes a `hidden` (or text) it does not change (askSet); what shows
// and goes with it (the peek, the page it sees, Loop in Support) does so by a class, `ask-on` (askOn).
const askShown = () => !!$('#ask-panel')?.classList.contains('ask-shown');
const askSet = (el, key, value) => { if (el && el[key] !== value) el[key] = value; };
const askOn = (el, on) => { on = !!on; if (el && el.classList.contains('ask-on') !== on) el.classList.toggle('ask-on', on); };
function askSync() {
  const row = $('#ask-row'), tab = $('#mobile-ask'), panel = $('#ask-panel');
  if (!row || !panel) return;
  const name = askName(), away = askAway(), open = !!ASK?.open && !away;
  askSet(row, 'hidden', away);
  if (askShown() !== open) { panel.classList.toggle('ask-shown', open); panel.inert = !open; }
  document.body.classList.toggle('ask-open', open);
  askSet(row, 'title', name + ' (⌘J)');
  // While the panel is open a close mark holds the row's (and the phone tab's) dot place (ask.css).
  for (const el of [row, tab].filter(Boolean)) {
    el.setAttribute('aria-expanded', String(open));
    if (open) el.setAttribute('aria-label', 'Close Ask Tico'); else el.removeAttribute('aria-label');
  }
  for (const el of document.querySelectorAll('[data-ask-dot]')) ldotWatch(el);
  askDot();
  askPeek();
  if (panel.getAttribute('aria-label') !== name) panel.setAttribute('aria-label', name);
  askSet(panel.querySelector('#ask-title'), 'textContent', name);
  if (!open) return;
  const page = askPage(), sees = panel.querySelector('[data-ask-sees]');
  askOn(sees, page.title);
  askSet(sees.querySelector('[data-ask-sees-name]'), 'textContent', page.title);
  askSet(sees, 'title', 'Sees ' + page.title);
  askPeople();
}
// The Liquid Dot (app/liquid-dot.js) is Ask's mark. One live dot per screen: the row's (or the phone tab's) while the
// panel is closed, the panel's own beside its name while it is open (Tico's turns carry none); the rest hold still.
// With reduced motion the dot is still and the closed row says the state in a word (shown while it has one, ask.css);
// the open panel says it in the thread already ("Thinking…", the reply itself).
function askDot() {
  const state = ASK?.dotState || 'idle', open = askShown();
  document.querySelectorAll('#ask-row [data-ask-dot], #mobile-ask [data-ask-dot]').forEach(el => ldotSet(el, open ? 'still' : state));
  ldotSet($('#ask-panel [data-ask-dot]'), open ? state : 'still');
  askSet($('#ask-row [data-ask-word]'), 'textContent', open ? '' : ldotWord(state));
}
// Where the dot is now: the open panel's, else the button's (the row on a computer, the tab on a phone).
const askHomeDot = () => [...document.querySelectorAll('#ask-row [data-ask-dot], #mobile-ask [data-ask-dot]')].find(el => el.getClientRects().length) || null;
const askLiveDot = () => askShown() ? $('#ask-panel [data-ask-dot]') : askHomeDot();
// Where `el` rests once the panel has finished sliding in: its box less the panel's translate right now (read in the
// same style pass as the box, so a slide just begun, or one reversed mid-close, cancels out).
function askSettled(el) {
  const t = getComputedStyle($('#ask-panel')).translate, r = el.getBoundingClientRect();
  const [, y = '0'] = t && t !== 'none' ? t.split(' ') : [];
  const dy = parseFloat(y) || 0;
  return dy ? {left: r.left, top: r.top - dy, width: r.width, height: r.height} : r;
}
// Open: the row's dot flies up to its place beside the panel's name; close: back down to the row. Both ends are
// measured once, before anything moves. On a computer the panel stands on the row (ask.css --ask-foot), measured
// with them. The flight is quick (a third of a free dot's): the panel is a click away and waits on nothing.
function askToggle(open = !ASK?.open, focus = true) {
  ASK ||= {open: false, mounted: false};
  document.querySelectorAll('[data-ask-dot]').forEach(d => d._ldotFly?.cancel());   // a flight the other way lands at once
  const from = askLiveDot()?.getBoundingClientRect(), back = open ? null : askHomeDot()?.getBoundingClientRect();
  const foot = open ? askFootNow() : null;
  const inside = !!$('#ask-panel')?.contains(document.activeElement);   // read before the closed panel goes inert
  if (!open && $('#ask-panel [data-ask-loop-note]')?.classList.contains('ask-on')) askLoopNoteDone();
  askFoot(foot);
  ASK.open = open;
  askSync();
  const to = askLiveDot();
  ldotFly(to, from, {to: open ? to && askSettled(to) : back, pace: 3});
  if (open && !ASK.mounted) { ASK.mounted = true; void askLoad(ASK); }
  else if (open) void ASK.refresh?.();       // cards answered or confirmed elsewhere meanwhile
  // A phone keeps its keyboard down until the person taps the box.
  if (open && focus && !askPhone()) setTimeout(() => $('#ask-panel .ask-form .p-text')?.focus(), 0);
  else if (!open && focus && inside) (askPhone() ? $('#mobile-ask') : $('#ask-row'))?.focus();
}
// Where the panel's foot goes on a computer: just over the Ask row (ask.css --ask-foot). Read with the other boxes before
// anything moves, then written; a phone's sheet has its own place.
const askFootNow = () => { const r = askPhone() ? null : $('#ask-row')?.getBoundingClientRect(); return r?.height ? Math.round(innerHeight - r.top + 6) : null; };
const askFoot = px => { if (px != null) $('#ask-panel')?.style.setProperty('--ask-foot', px + 'px'); };
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

// ---- Tico Support in the chat
// Whether support is in the chat now, from the person's open requests (ui/support.js) and the ones taken out here.
function askSupport() {
  const s = window.supportState?.() || {};
  return askSupportState({enabled: !!window.supportOn?.(true), open: s.open || [], hidden: askHiddenTickets()});
}
const ASK_CHIP = '<span class="ask-sup-chip"><span class="ask-sav"><span class="nav-icon" aria-hidden="true">headset_mic</span></span>Tico Support'
  + '<button type="button" class="ask-supx" data-ask-sup-x aria-label="Remove Tico Support" title="Remove Tico Support">'
  + '<span class="nav-icon" aria-hidden="true">close</span></button></span>';
// The header chip, the link and the box follow support in or out; a change while the panel shows plays its motion.
function askPeople() {
  const panel = $('#ask-panel');
  if (!panel || !ASK) return;
  const sup = askSupport(), people = panel.querySelector('[data-ask-people]'), loop = panel.querySelector('[data-ask-loop]');
  const requests = panel.querySelector('[data-ask-requests]'), note = panel.querySelector('[data-ask-loop-note]');
  // Your requests is there while support is on; its count is the unread replies (Support's own count).
  askOn(requests, sup.available);
  const unread = window.supportState?.().unread || 0;
  askSet(requests, 'textContent', unread ? `Your requests (${unread})` : 'Your requests');
  const loopOn = sup.available && ASK.live && !sup.on;
  askOn(loop, loopOn);   // in: the chip's × is the way out
  // On a phone the words under the link, the first time only: gone once the panel has been closed with them shown.
  askOn(note, loopOn && askPhone() && !askLoopNoteSeen());
  if (!sup.available && ASK.supId === undefined) return;     // support's requests not read yet
  const id = sup.on ? sup.ticket.id : '', was = ASK.supId;
  const box = ASK.composer && pq(ASK.composer, '.p-text');
  if (box) {
    box.placeholder = 'Message ' + (sup.on ? 'Tico Support' : askName());
    box.setAttribute('aria-label', box.placeholder);
  }
  if (id === (people.dataset.ticket || '') && was !== undefined) return;
  const shows = askShown() && !!ASK.drawn && was !== undefined, chip = people.querySelector('.ask-sup-chip');
  if (!id && chip && shows) askChipOut(chip);
  people.dataset.ticket = id;
  people.innerHTML = id ? ASK_CHIP : '';
  ASK.supId = id;
  if (was === undefined || !!was === !!id) return;
  // In or out since the chat last looked: a line in the thread says so, at this moment.
  ASK.sys ||= [];
  if (id) ASK.sys.push({at: new Date().toISOString(), text: ASK_SUP_IN, id, key: 'sys:in:' + id + ':' + ASK.sys.length});
  else {
    const gone = window.supportTicket?.(was);
    (ASK.gone ||= []).push({id: was, at: new Date().toISOString(), ticket: gone ? {...gone, messages: [...(gone.messages || [])]} : null});
    ASK.sys.push({at: new Date().toISOString(), text: ASK_SUP_OUT, id: was, key: 'sys:out:' + was + ':' + ASK.sys.length});
  }
  if (shows && id) askChipIn(people.querySelector('.ask-sup-chip'));
  if (shows && !id) setTimeout(() => ldotMove(askLiveDot(), 'hop'), 300);
}
// Support comes in: the dot squashes and a bubble pops out of it, then arcs up into the header and lands as the chip.
function askChipIn(chip) {
  const sav = chip?.querySelector('.ask-sav'), dot = askLiveDot();
  if (!sav || !dot || ldotCalm() || typeof sav.animate !== 'function') return;
  ldotMove(dot, 'split');
  const from = dot.getBoundingClientRect(), to = sav.getBoundingClientRect();
  const b = sav.cloneNode(true);
  b.classList.add('ask-fly');
  Object.assign(b.style, {left: `${to.left}px`, top: `${to.top}px`});
  document.body.append(b);
  chip.style.visibility = 'hidden';
  const dx = from.left + from.width / 2 - (to.left + to.width / 2), dy = from.top + from.height / 2 - (to.top + to.height / 2);
  const arc = Math.min(90, Math.hypot(dx, dy) * .3) + 30;
  const run = b.animate([
    {transform: `translate(${dx}px,${dy}px) scale(.2)`},
    {transform: `translate(${dx + 14}px,${dy - 10}px) scale(1.1)`, offset: .22},
    {transform: `translate(${dx * .45}px,${dy * .45 - arc}px) scale(1)`, offset: .6},
    {transform: 'translate(0,0) scale(1.25,.8)', offset: .88},
    {transform: 'none'},
  ], {duration: 820, easing: 'cubic-bezier(.45,0,.4,1)'});
  ASK.rowDelay = 820;                         // the line in the thread follows the chip down
  const land = () => {
    b.remove();
    chip.style.visibility = '';
    chip.animate?.([{transform: 'scale(.6)', opacity: 0}, {transform: 'scale(1.08)', opacity: 1, offset: .6}, {transform: 'none', opacity: 1}],
      {duration: 280, easing: 'cubic-bezier(.3,0,.2,1)'});
  };
  run.onfinish = land; run.oncancel = land;
}
// Support goes out: its bubble jumps out of the panel (up and to the right, spinning, fading) and the chip folds away.
// Both are copies on top of the page; the chip itself is already gone.
function askChipOut(chip) {
  if (ldotCalm() || typeof chip.animate !== 'function') return;
  const r = chip.getBoundingClientRect(), sav = chip.querySelector('.ask-sav'), s = sav.getBoundingClientRect();
  const fold = chip.cloneNode(true), b = sav.cloneNode(true);
  fold.classList.add('ask-fly'); b.classList.add('ask-fly');
  fold.querySelector('.ask-sav').style.visibility = 'hidden';
  fold.querySelector('button')?.remove();
  Object.assign(fold.style, {left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`});
  Object.assign(b.style, {left: `${s.left}px`, top: `${s.top}px`});
  document.body.append(fold, b);
  fold.animate([{transform: 'scale(1)', opacity: 1}, {transform: 'scale(1.06)', opacity: 1, offset: .25}, {transform: 'scale(.4)', opacity: 0}],
    {duration: 300, fill: 'forwards', easing: 'ease-in'}).onfinish = () => fold.remove();
  const run = b.animate([
    {transform: 'none'},
    {transform: 'translate(0,4px) scale(1.25,.75)', offset: .15},
    {transform: 'translate(60px,-70px) scale(1) rotate(30deg)', offset: .55},
    {transform: 'translate(150px,-30px) scale(.4) rotate(120deg)', opacity: 0},
  ], {duration: 760, easing: 'cubic-bezier(.3,0,.5,1)'});
  run.onfinish = run.oncancel = () => b.remove();
}
// "Loop in Support" (or the chip's ×): bring support in, sharing the chat with it (a request taken out comes back;
// with none open, a new one is sent), or take the request out of the chat. The link waits while that is on its way.
async function askFlip() {
  const panel = $('#ask-panel'), sw = panel?.querySelector('[data-ask-loop]'), error = panel?.querySelector('[data-ask-error]');
  if (!ASK || ASK.flipping) return;
  ASK.flipping = true;
  askLoopNoteDone();   // the words were shown once; pressing the link is the same as having read them
  if (sw) sw.disabled = true;
  try {
    const act = await askSupportSwitch(askSupport(), {
      share: () => ASK?.share?.() || askShareText([], myChatName()),
      file: text => window.supportFile(text),
      reply: (id, text) => window.supportReply(id, text),
      hide: id => askSetHidden([...askHiddenTickets(), id]),
      show: id => askSetHidden(askHiddenTickets().filter(x => x !== id)),
    });
    if (act.do === 'file' && act.id) askFiled(act.id);
    if (act.do !== 'none') { askPeople(); ASK?.draw?.(); }
  } catch (e) {
    if (error) { error.textContent = 'Tico Support was not added. ' + (e?.message || 'Try again in a moment.'); error.hidden = false; }
  } finally {
    ASK.flipping = false;
    if (sw) sw.disabled = false;
  }
}
// A request Loop in Support filed (none was open before): the reply promise, then one line that diagnostics weren't
// sent, with Attach diagnostics. Both are keyed by the request, so looping in again never repeats them.
function askFiled(id) {
  ASK.sys ||= [];
  const at = new Date().toISOString();
  // The reply promise, once: the same words the pop-up shows under a sent request (ui/support.js supportDelivered).
  const promise = 'sys:promise:' + id;
  if (!ASK.sys.some(s => s.key === promise) && window.supportDelivered) ASK.sys.push({at, text: window.supportDelivered(), id, key: promise});
  const key = 'sys:diag:' + id;
  if (!ASK.sys.some(s => s.key === key)) ASK.sys.push({at, text: ASK_NO_DIAG, id, key, attach: true});
}
const askLoopNoteKey = () => ASK_LOOP_NOTE + '.' + (S.me?.id || 'me');
function askLoopNoteSeen() { try { return localStorage.getItem(askLoopNoteKey()) === '1'; } catch { return true; } }
function askLoopNoteDone() { try { localStorage.setItem(askLoopNoteKey(), '1'); } catch { /* this page only */ } }
// Tico support's messages as the thread draws them: support's blue-gray under its headset, the person's on the right.
// `until`: a request taken out of the chat keeps what it said before then.
function askSupportRows(t, until = '') {
  const end = Date.parse(until || '') || Infinity;
  const row = (body, staff, at, i) => ({at, staff, html: staff
    ? `<div class="ask-sup them" data-ask-row="sup:${esc(t.id)}:${i}"><span class="ask-sav"><span class="nav-icon" aria-hidden="true">headset_mic</span></span><div><small>Tico Support</small><div class="ask-sup-body">${esc(body)}</div></div></div>`
    : `<div class="ask-sup you" data-ask-row="sup:${esc(t.id)}:${i}"><div class="ask-sup-body">${esc(body)}</div></div>`});
  // What the switch shared is the chat already on screen: not drawn again.
  return [row(t.message, false, t.created, 0), ...(t.messages || []).map((m, i) => row(m.body, m.from === 'staff', m.created, i + 1))]
    .filter((r, i) => (Date.parse(r.at || '') || 0) <= end && !(!r.staff && askShared(i ? t.messages[i - 1].body : t.message)));
}
const askSysRow = s => ({at: s.at, sys: true, html: `<div class="ask-sys" data-ask-row="${esc(s.key)}" role="status">${esc(s.text)}${s.attach
  ? ` <button type="button" class="linkish" data-ask-attach="${esc(s.id)}">Attach diagnostics</button>` : ''}</div>`});
function askClick(ev) {
  const t = ev.target;
  if (t.closest('[data-ask-close]')) { askToggle(false); return; }
  if (t.closest('[data-ask-loop], [data-ask-sup-x]')) { askFlip(); return; }
  if (t.closest('[data-ask-requests]')) { window.supportOpen?.({list: true}); return; }
  const attach = t.closest('[data-ask-attach]');
  if (attach) { window.supportAttach?.(attach.dataset.askAttach); return; }
  const pick = t.closest('[data-ask-pick]');
  if (pick) { void askAnswer(pick.closest('[data-ask-q]'), {pick: Number(pick.dataset.askPick)}); return; }
  const more = t.closest('[data-ask-more]');
  if (more) { more.setAttribute('aria-expanded', String(more.getAttribute('aria-expanded') !== 'true')); return; }
  const hint = t.closest('[data-ask-hint]');
  if (hint) { ev.preventDefault(); void ASK?.send?.(hint.dataset.askHint); return; }
  // A link followed from the panel: on a phone the sheet gets out of the way of the page it opens.
  const a = t.closest('a[href^="#/"]');
  if (a && askPhone() && !a.getAttribute('href').startsWith('#/support')) askToggle(false, false);
}

// ---- Bots' questions
// Bots' questions to this person (Needs you), the first few: one pick of two to six answered with a tap, any other
// question with a line of words; several picks at once, or several questions, open the task.
function askQuestions() {
  const me = myActor();
  return (S.v2?.needs || []).filter(it => ['question', 'ask'].includes(it.kind) && it.ask?.to_actor === me
    && it.ask.from_actor !== me).slice(0, ASK_CARDS_MAX);
}
// Who asks: the panel's own bot by the panel's name, anyone else as the question card names them.
const askAsker = actor => actor === 'bot:' + assistantBot() ? askName() : qcardAsker(actor);
// A card is who asks, the question and at most two buttons. A long question shows three lines until it is tapped; three
// or more picks are one list and Send.
const ASK_Q_LONG = 160;
// A pick chosen in a list and not yet sent, by the question's wording ("task:question id", a reworded ask gets a new
// id) and kept as its label: the thread is drawn again whenever the chat changes. Questions no longer shown drop out.
const askPicks = new Map(), askPickKey = (it, q) => `${it.id}:${q.id}`;
function askQuestionHTML(it) {
  const q = it.kind === 'question' ? qcardQuestion(it) : null, who = askAsker(it.ask.from_actor);
  const text = plainActors(q ? q.question : it.ask.body);
  const body = `<div class="ask-card-who">${esc(who)} asks</div>${text.length > ASK_Q_LONG
    ? `<button type="button" class="ask-card-q" data-ask-more aria-expanded="false">${esc(text)}</button>` : `<div class="ask-card-q">${esc(text)}</div>`}`;
  const structured = it.kind === 'question' && (it.ask.refs?.questions || []).length;
  const answer = q?.multi || (structured && !q) ? `<a href="#/task/${encodeURIComponent(it.id)}">Answer</a>`
    : q && q.options.length > 2 ? `<form class="ask-reply" data-ask-choose="${esc(askPickKey(it, q))}"><select name="pick" aria-label="Answer ${esc(who)}">${q.options.map((o, i, all) => {
      const kept = askPicks.get(askPickKey(it, q)), on = all.some(x => x.label === kept) ? o.label === kept : o.recommended;
      return `<option value="${i}"${on ? ' selected' : ''}>${esc(o.label)}</option>`;
    }).join('')}</select><button type="submit" class="ghost">Send</button></form>`
    : q ? `<div class="ask-picks">${q.options.map((o, i) => `<button type="button" class="ghost" data-ask-pick="${i}">${esc(o.label)}</button>`).join('')}</div>`
    : `<form class="ask-reply" data-ask-reply><input name="text" maxlength="4000" autocomplete="off" placeholder="Answer ${esc(who)}" aria-label="Answer ${esc(who)}"><button type="submit" class="ghost">Send</button></form>`;
  return `<div class="ask-card" data-ask-q="${esc(it.id)}" data-ask-row="q:${esc(it.id)}">${body}${answer}</div>`;
}
// The answer goes where Decide sends it (decide.js): a pick to the task's answers, words to a loose question's answer
// or as a comment on the task, which wakes its bot.
async function askAnswer(card, {pick, text}) {
  const it = (S.v2?.needs || []).find(x => String(x.id) === card?.dataset.askQ);
  if (!it || card.dataset.busy) return;
  const q = it.kind === 'question' ? qcardQuestion(it) : null, who = askAsker(it.ask.from_actor);
  const writes = pick !== undefined && q ? qcardWrites(it, q, [q.options[pick].label])
    : it.kind === 'ask' ? [{path: `/v2/messages/${encodeURIComponent(it.id)}/answer`, body: {text}}]
    : [{path: `/v2/tasks/${encodeURIComponent(it.id)}/comments`, body: {text}}];
  card.dataset.busy = '1';
  card.querySelectorAll('button, input, select').forEach(el => { el.disabled = true; });
  try {
    for (const w of writes) await post(w.path, w.body);
    card.innerHTML = `<div class="ask-card-who">${esc(who)}</div><div class="muted">Answered</div>`;
    toast('Sent to ' + who);
    await v2Refresh();                              // Needs you without it: the card leaves the chat
    ASK?.draw?.();
  } catch (e) {
    toast(e.message, true);
    delete card.dataset.busy;
    card.querySelectorAll('button, input, select').forEach(el => { el.disabled = false; });
  }
}

// ---- Peeks (ask-rules.js askPeekFresh, askPeekLine): one short line beside the closed dot, Tico saying something waits
// on the person: bots' questions, or a Confirm card once the chat has read them. Tapping it opens the chat at the first.
const askPeekKey = () => ASK_PEEKS + '.' + (S.me?.id || 'me');
function askPeekStore() {
  try { const v = JSON.parse(localStorage.getItem(askPeekKey()) || '{}'); return askPeekTidy(v && typeof v === 'object' ? v : {}); }
  catch { return askPeekTidy({}); }
}
function askPeekSave(store) {
  try { localStorage.setItem(askPeekKey(), JSON.stringify(askPeekTidy(store))); } catch { /* this page only */ }
}
const askTypes = a => !!a && (a.isContentEditable || a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit'].includes(a.type)));
const askTyping = () => askTypes(document.activeElement);
let ASK_PEEK_TIMER = 0;
// On a computer the peek sits just past the rail, level with the Ask row (ask.css --ask-row-y). Measured only while a
// peek shows, never on the way in or out of the panel.
function askPeekPlace(el) {
  const r = !askPhone() && $('#ask-row')?.getBoundingClientRect();
  if (r?.height) el.style.setProperty('--ask-row-y', `${Math.round(innerHeight - r.bottom + r.height / 2)}px`);
}
function askPeek() {
  const el = $('#ask-peek');
  if (!el) return;
  clearTimeout(ASK_PEEK_TIMER);
  const now = Date.now(), open = !!ASK?.open, away = askAway() || !S.me;
  const items = away ? [] : askQuestions().map(it => ({id: String(it.id)}))
    .concat((ASK?.pending || []).map(a => ({id: 'confirm:' + a.id, confirm: true})));
  const store = askPeekStore(), cur = el.dataset.item;
  if (open || away) { askOn(el, false); delete el.dataset.item; return; }
  if (cur && el.classList.contains('ask-on') && items.some(it => it.id === cur) && !store.dismissed.includes(cur)) { askPeekPlace(el); return; }
  const {text, ids} = askPeekLine(askPeekFresh(items, {now, loadedAt: ASK_LOADED, typing: askTyping(), open, dismissed: store.dismissed, shown: store.shown}));
  if (!text) {
    askOn(el, false); delete el.dataset.item;
    if (now - ASK_LOADED < ASK_PEEK_WAIT_MS) ASK_PEEK_TIMER = setTimeout(askPeek, ASK_PEEK_WAIT_MS - (now - ASK_LOADED) + 50);
    return;
  }
  el.querySelector('[data-peek-text]').textContent = text;
  el.querySelector('[data-peek-go]').setAttribute('aria-label', text + '. Open ' + askName());
  el.dataset.item = ids[0];
  el.dataset.items = ids.join(' ');
  askPeekPlace(el);
  askOn(el, true);
  askPeekSave({...store, shown: {...store.shown, ...Object.fromEntries(ids.map(id => [id, now]))}});
  ldotMove(askLiveDot(), 'hop');
}
// Dismissed or tapped, the line is done with every item it spoke for.
function askPeekDone() {
  const el = $('#ask-peek'), store = askPeekStore();
  askPeekSave({...store, dismissed: [...store.dismissed, ...(el.dataset.items || '').split(' ').filter(Boolean)]});
  askOn(el, false); delete el.dataset.item;
}
$('#ask-peek')?.addEventListener('click', ev => {
  const el = $('#ask-peek'), id = el.dataset.item;
  if (!id) return;
  if (ev.target.closest('[data-peek-x]')) { askPeekDone(); return; }
  if (!ev.target.closest('[data-peek-go]')) return;
  askPeekDone();
  ASK ||= {open: false, mounted: false};
  ASK.focusQ = id;
  askToggle(true, false);
  ASK.draw?.();
});

async function askLoad(chat) {
  const el = $('#ask-panel'), thread = el.querySelector('[data-ask-thread]'), error = el.querySelector('[data-ask-error]');
  const current = () => ASK === chat && chat.mounted;
  const fail = text => { if (!current()) return; askSet(error, 'textContent', text || ''); askSet(error, 'hidden', !text); };
  let info = null, messages = [], execution = null, rendered = '', es = null, poll = null;
  const stopStream = () => { try { es?.(); } catch { /* released */ } es = null; clearInterval(poll); poll = null; };
  chat.stop = stopStream;
  const isReply = m => m.from_actor === 'bot:' + info?.bot;
  let pending = [], readFor = '';
  const seenRows = new Set();
  const draw = () => {
    if (!current() || !info?.available) return;
    const sup = askSupport(), at = x => Date.parse(x || '') || 0;
    const ticket = sup.on ? window.supportTicket?.(sup.ticket.id) || null : null;
    // Support's lines: the request in the chat now, and any taken out this visit up to when they left; then the lines
    // that say who came in or went (and, for a request already in the chat, when it came in).
    const shown = (ticket ? [{t: ticket, until: ''}] : []).concat((chat.gone || []).filter(g => g.id !== ticket?.id)
      .map(g => ({t: window.supportTicket?.(g.id) || g.ticket, until: g.at})).filter(x => x.t));
    const supRows = [].concat(...shown.map(x => askSupportRows(x.t, x.until)));
    const sys = [...(chat.sys || [])];
    for (const {t} of shown) if (!sys.some(s => s.id === t.id && s.text === ASK_SUP_IN))
      sys.push({at: new Date(at(t.created) - 1).toISOString(), text: ASK_SUP_IN, id: t.id, key: 'sys:in:' + t.id});
    // While the Assistant is replying, Ask's dot works, then talks as the words stream in, the panel open or not;
    // waiting on Tico Support's answer, it listens.
    const streaming = !!(execution && execution.state !== 'completed' && execution.text);
    const replying = streaming || (messages.length > 0 && !isReply(messages.at(-1)));
    const ownSup = ticket ? askSupportRows(ticket) : [], waiting = ownSup.length > 0 && !ownSup.at(-1).staff;
    chat.dotState = replying ? ldotChatState({replying, streaming}) : waiting ? 'listening' : 'idle';
    askDot();
    // What waits on the person sits where it was asked, among the rest: Confirm cards left in other chats (BotOps'),
    // and bots' questions. One with no time comes last.
    const here = new Set(messages.map(m => m.refs?.action).filter(Boolean)), late = new Date().toISOString(), asks = askQuestions();
    chat.pending = pending.filter(a => !here.has(a.id));   // the cards the thread shows, so a peek only points at those
    const cards = chat.pending.map(a => ({at: a.created || late,
      html: `<div class="conv-run chat"><div data-action-host="${esc(a.id)}"><div class="asst-state">Loading the card…</div></div></div>`}))
      .concat(asks.map(it => ({at: it.ask?.created || it.created || late, html: askQuestionHTML(it)})));
    const listed = new Set(asks.map(it => { const q = it.kind === 'question' && qcardQuestion(it); return q ? askPickKey(it, q) : ''; }));
    for (const k of askPicks.keys()) if (!listed.has(k)) askPicks.delete(k);
    const rows = messages.slice(-30).map(m => ({at: m.created, html: v2MessageHTML({...m, run: null})}))
      .concat(supRows, sys.map(askSysRow), cards)
      .map((r, i) => ({...r, i})).sort((a, b) => at(a.at) - at(b.at) || a.i - b.i).map(r => r.html);
    const live = execution && execution.state !== 'completed' ? execution.text : '';
    if (live) rows.push(`<div data-ask-live>${v2MessageHTML({from_actor: 'bot:' + info.bot, body: live})}</div>`);
    else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status">${esc(execution?.label || 'Thinking…')}</p>`);
    const html = rows.join('') || `<div class="ask-empty">${ASSISTANT_HINTS.map(h =>
      `<button class="ghost" type="button" data-ask-hint="${esc(h)}">${esc(h)}</button>`).join('')}</div>`;
    // A reply from support seen here is read, as in Support itself.
    const seen = ticket?.unread && chat.open && askShown() && document.hasFocus() ? ticket.id + ':' + (ticket.messages || []).length : '';
    if (seen && seen !== readFor) { readFor = seen; void window.supportRead?.(ticket.id); }
    if (html !== rendered) {
      stickScroll(thread).update(() => {
        thread.innerHTML = html;
        if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload: snapshot, assistantName: askName()});
      }, {fresh: !!rendered});
      askRowsIn(thread, seenRows, !!rendered, chat.rowDelay || 0);
      chat.rowDelay = 0;
      rendered = html;
    }
    chat.drawn = true;
    // Opened from a peek: the card it spoke of, in view.
    const fq = chat.focusQ || '', q = fq && thread.querySelector(fq.startsWith('confirm:')
      ? `[data-action-host="${CSS.escape(fq.slice(8))}"]` : `[data-ask-q="${CSS.escape(fq)}"]`);
    if (q) { chat.focusQ = ''; q.scrollIntoView({block: 'nearest'}); if (!askPhone()) q.querySelector('input, button, a')?.focus(); }
  };
  chat.draw = draw;
  // What turning Tico Support on shares: the chat's turns, Ben's and the Assistant's, scrubbed (ask-rules askShareText).
  chat.share = () => askShareText(messages.filter(m => m.body && (isReply(m) || m.from_actor === myActor()))
    .map(m => ({me: !isReply(m), text: plainActors(m.body)})), info?.name || 'Assistant');
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
    const P = chat.composer, files = P?.files.slice() || [];
    if ((!text && !files.length) || !P || P.sending) return;
    // Tico Support in the chat: the box writes to its request, which takes words only.
    const to = askSendTo(askSupport());
    if (to.to === 'support' && files.length) { fail('Tico Support gets words only. Remove the file to send.'); return; }
    P.sending = true; pillButtons(P); fail('');
    if (to.to === 'support') {
      try { await window.supportReply(to.id, text); if (current()) { pillAcknowledge(P, text, []); stickScroll(thread).toBottom(); draw(); } }
      catch (e) { fail('Not sent. ' + e.message); }
      finally { P.sending = false; if (current()) pillButtons(P); }
      return;
    }
    try {
      const r = await cloudCompose('/v2/assistant/messages', {text: text || 'Attached files.', page: askPage()}, files);
      CHAT_CACHE.delete(info.bot);                    // the Assistant page reads the conversation again when it opens
      if (!current()) return;
      for (const m of [r.message, r.reply].filter(Boolean)) if (!messages.some(x => x.id === m.id)) messages.push(m);
      if (r.message?.conversation_id && r.message.conversation_id !== info.room_id) { info.room_id = r.message.conversation_id; watch(); }
      pillAcknowledge(P, text, files); stickScroll(thread).toBottom(); draw();
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
  thread.setAttribute('aria-label', askName() + ' conversation');
  if (!info.available) {
    if (!info.can_turn_on) { thread.innerHTML = `<p class="muted">${esc(askName())} is off. Ask the team owner to turn it on.</p>`; return; }
    window.assistantChat?.off(thread, info, {post, esc, after: () => { if (current()) void askLoad(chat); }});
    return;
  }
  messages = info.messages || []; execution = info.execution || null; pending = info.pending || [];
  if (!el._dropZone) { el._dropZone = true; chatDropZone(el, () => ASK?.live ? ASK.composer : null); }   // a dropped file attaches
  chat.composer = makePill({mode: 'chat', label: 'Message ' + askName(), placeholder: 'Message ' + askName(),
                            send: (P, text) => send(text)});
  pq(chat.composer, '.p-text').maxLength = 4000;
  el.querySelector('.ask-form').replaceChildren(chat.composer.el);
  chat.live = true;
  askPeople();
  askFillDraft(chat);
  draw();
  watch();
  if (chat.open && !askPhone()) pq(chat.composer, '.p-text').focus();
}
// Lines new since the thread last drew come in: the person's rise, support's pop and a new card rise, a who-joined line
// rises (after the chip lands, `delay`). Tico's own turns arrive by the streaming words instead. Transform and opacity only.
function askRowsIn(thread, seen, animate, delay = 0) {
  const rows = thread.querySelectorAll(':scope > [data-ask-row], :scope > .conv-run.from-me[data-message]');
  for (const row of rows) {
    const key = row.dataset.askRow || 'm:' + row.dataset.message;
    if (seen.has(key)) continue;
    seen.add(key);
    if (!animate || ldotCalm() || typeof row.animate !== 'function') continue;
    const pop = row.matches('.ask-sup.them');
    row.animate(pop ? [{transform: 'translateY(6px) scale(.92)', opacity: 0}, {transform: 'scale(1.02)', opacity: 1, offset: .7}, {transform: 'none', opacity: 1}]
      : [{transform: 'translateY(8px)', opacity: 0}, {transform: 'none', opacity: 1}],
      {duration: pop ? 320 : 260, delay: row.matches('.ask-sys') ? delay : 0, easing: 'cubic-bezier(.2,0,0,1)', fill: 'backwards'});
  }
}
$('#ask-row')?.addEventListener('click', () => askToggle());
$('#mobile-ask')?.addEventListener('click', () => askToggle());
$('#ask-panel')?.addEventListener('click', askClick);
$('#ask-panel')?.addEventListener('change', ev => {
  const choose = ev.target.closest('[data-ask-choose]');
  if (choose) askPicks.set(choose.dataset.askChoose, choose.elements.pick.selectedOptions[0]?.textContent || '');
});
$('#ask-panel')?.addEventListener('submit', ev => {
  const choose = ev.target.closest('[data-ask-choose]');
  if (choose) { ev.preventDefault(); void askAnswer(choose.closest('[data-ask-q]'), {pick: Number(choose.elements.pick.value)}); return; }
  const form = ev.target.closest('[data-ask-reply]');
  if (!form) return;
  ev.preventDefault();
  const text = form.elements.text.value.trim();
  if (text) void askAnswer(form.closest('[data-ask-q]'), {text});
});
document.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'j' && !askAway()) { ev.preventDefault(); askToggle(); return; }
  if (ev.key !== 'Escape' || !ASK?.open || document.querySelector('dialog[open]')) return;
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
document.addEventListener('tico:support', () => { askPeople(); ASK?.draw?.(); });
// Typing anywhere holds a peek back; it may come once the box is left.
document.addEventListener('focusout', ev => { if (askTypes(ev.target)) setTimeout(askPeek, 0); });
liveOn('needs-list', () => { ASK?.draw?.(); askPeek(); });
addEventListener('hashchange', () => setTimeout(askSync, 0));
addEventListener('resize', () => { if (ASK?.open) askFoot(askFootNow()); askSync(); });
setTimeout(askPeek, ASK_PEEK_WAIT_MS + 100);
