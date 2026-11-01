/* ui/app/decide.js — the Needs you card: one item of Needs you at a time, answered with two big buttons; a modal on a
   wide screen, the whole screen on a phone. A row that can be answered on a card opens here: a bot's question with
   options, an approval, a yes/no ask, a bot's recommendation, a question asked outside any task, the Stale tickets
   card. Every other row opens the full task. ‹ and › (or Go through, from the first) walk the cards in the order the
   list shows them. An answer, Done or a comment hands the task back to the bot; it is sent when its toast runs out
   or the next action lands, so Undo never needs the server.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let DEC = null;                 // the open card's state, null while it is shut
const DEC_UNDO_MS = 4000;
const DEC_MORE_AT = 10;         // cards left when the next page of Needs you is read
// A card's comment Send is disabled until there is a comment; its hover title says so.
const DC_SEND_WHY = 'Type a comment to send';
// Cards answered here, out of the list until the server's list drops them (or this page closes).
const DEC_HANDLED = new Set();
// A deep link's item (#/decide?item=<id>), opened once Needs you is drawn.
let DECIDE_WANT = '';

// ---- which cards, in which order (GET /v2/needs-you items)
// A question answered in the card (question-card.js): one question with 2 to 6 options, a pick or several picks, put
// to this person by someone else. Several questions in one ask are a task: they open the task.
const decideQuestion = it => qcardQuestion(it);
// The plain ask a task card leads with, from the server's read alone (so a card's buttons never wait on its
// comments): an open question to me, else the ask the bot wrote in its waiting note or its task's first paragraph.
const decideAskText = it => String((it.ask?.to_actor === myActor() && it.ask.from_actor !== myActor() ? it.ask.body : '')
  || it.person_ask || '').trim();
// A yes/no ask: its first question opens with a word that asks for a yes or a no and offers no "or". Deterministic, so
// a card's buttons never guess.
const DEC_YES_NO = new Set(['approve', 'can', 'could', 'should', 'shall', 'may', 'do', 'does', 'did', 'is', 'are', 'was',
  'were', 'will', 'would', 'ok', 'okay', 'send', 'publish', 'merge', 'ship', 'go', 'sign', 'confirm', 'proceed', 'accept',
  'allow', 'agree', 'have', 'has', 'want', 'renew', 'book', 'buy', 'pay', 'launch', 'post', 'keep', 'cancel']);
function decideYesNoAsk(text) {
  const q = String(text || '').trim().split(/(?<=[.!?])\s+/).find(s => s.endsWith('?'));
  if (!q || /\bor\b/i.test(q)) return false;
  return DEC_YES_NO.has(q.split(/\s+/)[0].replace(/[^A-Za-z]/g, '').toLowerCase());
}
// Each Needs you item's key: its kind and id.
const decideItemKey = it => ({approval: 'approval:', ask: 'ask:', stale: 'stale:', memory_tidy: 'memory_tidy:'}[it?.kind] || 'task:') + it?.id;
function decideCards(items) {
  const me = myActor(), asks = [], approvals = [], waiting = [], mine = [], unexplained = [];
  for (const it of items || []) {
    if (it.kind === 'approval') { approvals.push({key: 'approval:' + it.id, type: 'approve', it, at: it.created}); continue; }
    if (it.kind === 'ask') { asks.push({key: 'ask:' + it.id, type: 'ask', it, at: it.created}); continue; }
    if (it.kind === 'memory_tidy') { approvals.push({key: 'memory_tidy:' + it.id, type: 'memory_tidy', it, at: it.created}); continue; }
    if (it.kind === 'stale') { approvals.push({key: 'stale:' + it.id, type: 'stale', it, at: it.created}); continue; }
    if (!['task', 'question', 'waiting'].includes(it.kind)) continue;
    const q = it.kind === 'question' ? decideQuestion(it) : null;
    if (q) asks.push({key: 'task:' + it.id, type: 'pick', it, q, at: it.ask.created || it.created});
    // A card still waiting for its bot to say what it needs goes last, as the server orders it.
    else (it.ask_missing ? unexplained : it.owner === me && it.kind === 'task' && it.waiting_on !== me ? mine : waiting)
      .push({key: 'task:' + it.id, type: 'task', it, at: (it.ask?.to_actor === me && it.ask.created) || it.created, yn: !it.ask_missing && decideYesNoAsk(decideAskText(it))});
  }
  const old = (a, b) => String(a.at || '').localeCompare(String(b.at || ''));
  // The server's tier (backend/views.py decide_tier): what holds up something due, or going ahead on its own, within
  // two working days; then what holds up a roadmap item at Approved or Building; then the rest. Newest first within
  // each: how old a card is never moves it up.
  const tier = c => c.it.tier ?? 2;
  const all = [...asks, ...approvals, ...waiting, ...mine].sort((a, b) => tier(a) - tier(b) || old(b, a));
  return [...all, ...unexplained.sort(old)];
}
// Answered on a card: everything but a task that asks for no yes or no and recommends nothing (that one opens whole).
const decideOnCard = c => c.type !== 'task' || !!c.yn || !!c.it.recommend?.ask;
const needsCards = items => decideCards(items).filter(decideOnCard);
// The card an item opens as, or '' when it opens as the full task.
const decideCardKey = it => { const c = it && decideCards([it])[0]; return c && decideOnCard(c) ? c.key : ''; };
// Answered here and on its way, or sent from an earlier page: out of the list and the walk.
const decideGone = key => DEC_HANDLED.has(key) || [...DEC_OUTBOX].some(p => p.key === key);
// A card from Needs you's own order: the list's own, or a row the server still has that the list does not show.
// A card from a row (needs-you.js needRowHTML, task-list.js) carries its key in `data-need-card`.
const decideCount = () => S.me?.role === 'bot' ? 0 : needsCards(S.v2?.needs).filter(c => !decideGone(c.key)).length;
// "From Ana" beside the name when the card's work traces back to another person than the one shown;
// "You asked" when it is the viewer's own request a bot is bringing back.
function decideFromPerson(it, shown) {
  const p = it.from_person;
  if (!p || p === shown) return '';
  return p === myActor() ? 'You asked' : `From ${actorLabel(p)}`;
}
// The Needs you list's way in: "Go through · N", only on Needs you and only when there is a card. A phone has it in
// the header, a wide screen in the filter bar, where the header's search keeps its room.
function decidePaintButton() {
  const links = document.querySelectorAll('[data-go-through]');
  if (!links.length) return;
  const n = TASKS_ST && tasksNeedsQueue(TASKS_ST) ? decideCount() : 0;
  for (const b of links) { b.hidden = !n; b.textContent = `Go through · ${n}`; }
}
// The lists that show Needs you draw again: an answered card leaves them at once, and comes back on Undo.
function needsListsRedraw() {
  if (TASKS_ST) tasksRender(TASKS_ST);
  if (typeof BOT !== 'undefined' && BOT && $('#t-needs')) botNeedsPaint(BOT.slug);
}

// ---- the dialog
// The cards the list shows, in its order; a card the list does not show (not drawn yet, a page not read) goes in the
// server's order.
function decideListKeys() {
  return [...new Set([...document.querySelectorAll('#task-body [data-need-card], #t-needs [data-need-card]')].map(el => el.dataset.needCard))];
}
function decideDialog() {
  let d = $('#need-card');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'need-card'; d.className = 'dc-dialog'; d.setAttribute('aria-label', 'Needs you');
  d.innerHTML = `<section class="decide">
    <header class="dc-head">
      <button type="button" class="dc-icon dc-x" data-dc="close" aria-label="Close" title="Close (Esc)">✕</button>
      <h2 class="dc-count" aria-live="polite"></h2>
      <span class="dc-nav">${helpBubble('← → move · 1–6 pick · Y / N · R recommended · D done · S skip · C comment')}<button type="button" class="dc-icon" data-dc="prev" aria-label="Previous" title="Previous (←)"><span class="nav-icon" aria-hidden="true">chevron_left</span></button><button type="button" class="dc-icon" data-dc="next" aria-label="Next" title="Next (→)"><span class="nav-icon" aria-hidden="true">chevron_right</span></button></span>
    </header>
    <div class="dc-stage"></div>
    <div class="dc-actions"></div>
    <div class="dc-toast" role="status" hidden><span></span><button type="button" data-dc="undo">Undo</button></div>
  </section>`;
  const root = $('.decide', d);
  root.addEventListener('click', ev => { if (DEC) decideClick(DEC, ev); });
  root.addEventListener('change', ev => { if (DEC && ev.target.matches('[data-qc-tick]')) decideTick(DEC, Number(ev.target.dataset.qcTick), ev.target.checked); });
  root.addEventListener('input', ev => {
    const box = ev.target.closest('.dc-note textarea');
    if (!box) return;
    box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 120) + 'px';
    for (const b of root.querySelectorAll('.dc-send, [data-dc-text]')) {
      b.disabled = !box.value.trim();
      if (b.matches('.dc-send')) b.title = b.disabled ? DC_SEND_WHY : 'Send comment';
    }
  });
  root.addEventListener('keydown', ev => {
    if (!DEC || ev.key !== 'Enter' || ev.shiftKey || ev.isComposing || !ev.target.matches('.dc-note textarea') || !ev.target.value.trim()) return;
    const send = $('.dc-send, [data-dc-text]', root);
    if (send) { ev.preventDefault(); decideAct(DEC, send.dataset.dc); }
  });
  // Esc in the note leaves the note; otherwise it closes the card.
  d.addEventListener('cancel', ev => { ev.preventDefault(); decideStop(); });
  // The event lands a moment after closing: a card opened again since keeps its place.
  d.addEventListener('close', () => { if (!d.open) decideStop(); });
  document.body.appendChild(d);
  return d;
}
// Opens the card for `key`, or the first card of the list with none. False when there is no such card.
function decideOpen(key = '') {
  if (S.me?.role === 'bot') return false;
  const fresh = !DEC;
  const st = DEC || {order: [], at: null, cards: new Map(), pending: null, data: new Map(), ticks: new Map(), off: [], fromList: false};
  decideSync(st, S.v2?.needs || []);
  // A card the list does not show (a filter hides it, a link names it) leads the walk.
  if (key && st.cards.has(key) && !st.order.includes(key) && !decideGone(key)) st.order.unshift(key);
  const live = decideLive(st), to = key ? (live.includes(key) ? key : null) : live[0] || null;
  if (!to) return false;
  if (fresh) {
    DEC = st;
    if (typeof liveOn === 'function') st.off.push(liveOn('needs-list', items => { if (DEC === st) decideRefresh(st, items); }));
    const keys = ev => decideKey(st, ev);
    document.addEventListener('keydown', keys);
    st.off.push(() => document.removeEventListener('keydown', keys));
    st.opener = document.activeElement;
  }
  st.at = to;
  if (TASKS_ST && $('#task-peek')?.open) taskPeekClose(TASKS_ST);
  const d = decideDialog();
  if (!d.open) {
    d.showModal();
    // A phone's Back closes the card, as it closes the task sheet.
    if (matchMedia(PHONE).matches && !history.state?.needCard) history.pushState({...(history.state || {}), needCard: 1}, '');
  }
  decideRender(st, !fresh);
  return true;
}
const decideGoThrough = () => decideOpen('');
// Closing sends what is waiting on its toast.
let DEC_BACK = false;
function decideStop() {
  const st = DEC;
  if (!st) return;
  DEC = null;
  void decideCommit(st);
  st.off.forEach(fn => fn());
  const d = $('#need-card');
  if (d?.open) d.close();
  if (history.state?.needCard && !DEC_BACK) { DEC_BACK = true; history.back(); }
  needsListsRedraw();
  // Back to where the card was opened from, or the row of the card last shown.
  const row = st.at && document.querySelector(`[data-need-card="${CSS.escape(st.at)}"]`);
  const to = st.opener?.isConnected ? st.opener : row?.querySelector('.tl-open') || row;
  to?.focus?.({preventScroll: true});
}
window.addEventListener('popstate', () => {
  // The card's own Back, from closing it, landed after a card opened again: that card gets its history entry back.
  if (DEC_BACK) {
    DEC_BACK = false;
    if (DEC && !history.state?.needCard && matchMedia(PHONE).matches) history.pushState({...(history.state || {}), needCard: 1}, '');
    return;
  }
  if (DEC && !history.state?.needCard) decideStop();
});
// A row answered on a card outside the Tasks list (a bot's rail): its card, not the task. Capturing, so the row's own
// open-the-task handler never runs; a link to anything else (a bot, a person) keeps its place.
document.addEventListener('click', ev => {
  const row = ev.target.closest?.('[data-need-card]');
  if (!row || row.closest('#task-body') || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const link = ev.target.closest('a[href]');
  if (link && !link.matches('.ttl')) return;
  if (!decideOpen(row.dataset.needCard)) return;
  ev.preventDefault(); ev.stopPropagation();
}, true);
// A deep link to one item (an old #/decide?item=<id>): its card, or its task when it is answered whole.
async function decideWanted(id) {
  if (!needsItem(id)) { await v2Refresh(); needsListsRedraw(); }
  const it = needsItem(id);
  if (!it || !TASKS_ST) return;
  const key = decideCardKey(it);
  if (!key || !decideOpen(key)) location.hash = '#/task/' + encodeURIComponent(id);
}

// The server's list changed (live, or a read after a write): drop what left, add what is new where the list has it.
const decideLive = st => st.order.filter(k => !decideGone(k));
function decideSync(st, items) {
  const fresh = needsCards(items), keys = new Set(fresh.map(c => c.key));
  st.cards = new Map([...st.cards].filter(([k]) => keys.has(k)));
  for (const c of fresh) st.cards.set(c.key, c);
  // Answered here and gone from the server: no longer held out of the list.
  for (const k of [...DEC_HANDLED]) if (!keys.has(k) && st.pending?.key !== k) DEC_HANDLED.delete(k);
  const listed = decideListKeys().filter(k => keys.has(k));
  const want = listed.length ? listed : fresh.map(c => c.key);
  const before = st.order;
  let order = before.filter(k => keys.has(k));
  if (!st.fromList && listed.length) { order = [...new Set([...listed, ...order])]; st.fromList = true; }
  // A new card goes after the card before it in the list, else first.
  want.forEach((k, i) => {
    if (order.includes(k)) return;
    const prev = want.slice(0, i).reverse().find(x => order.includes(x));
    order.splice(prev ? order.indexOf(prev) + 1 : 0, 0, k);
  });
  // A card that the server dropped meanwhile (answered elsewhere): the next one in the list takes its place.
  if (st.at && !order.includes(st.at)) {
    const i = before.indexOf(st.at);
    st.at = before.slice(i + 1).find(k => order.includes(k) && !decideGone(k))
      || before.slice(0, Math.max(i, 0)).reverse().find(k => order.includes(k) && !decideGone(k)) || null;
  }
  st.order = order;
}
function decideRefresh(st, items) {
  const before = st.at, card = before && st.cards.get(before);
  decideSync(st, items);
  if (st.at !== before) decideRender(st, true);
  else if (card && JSON.stringify([st.cards.get(before).type, st.cards.get(before).it]) !== JSON.stringify([card.type, card.it])) {
    // The Stale tickets card mends its rows where they stand: an answered row goes, a new one comes, focus stays.
    if (card.type === 'stale') { decideStalePaint(st); return; }
    decideRedraw(st);
    // A new version: read its comments and files again. Until they land, the card shows (and Done sends) the old one.
    if (st.cards.get(before).it.version !== card.it.version) void decideLoad(st, st.cards.get(before), true);
  } else decidePaintHead(st);
}
// The current card changed under it (a live refresh): draw it again, buttons included, keeping what was typed.
function decideRedraw(st) {
  const box = $('.decide .dc-note textarea'), draft = box?.value || '', typing = box && document.activeElement === box;
  decideRender(st);
  const again = $('.decide .dc-note textarea');
  if (again && draft) { again.value = draft; again.dispatchEvent(new Event('input', {bubbles: true})); }
  if (again && typing) again.focus({preventScroll: true});
}
// The card `dir` steps from the current one; Skip goes round to the first from the last.
function decideStepKey(st, dir, round = false) {
  const live = decideLive(st), i = live.indexOf(st.at);
  if (i < 0) return live[0] || null;
  const j = i + dir;
  return j >= 0 && j < live.length ? live[j] : round && live.length > 1 ? live[(j + live.length) % live.length] : null;
}
// After a card is answered: the next card of the list, else the one before it.
function decideAfter(st, key) {
  const i = st.order.indexOf(key), live = k => !decideGone(k);
  return st.order.slice(i + 1).find(live) || st.order.slice(0, Math.max(i, 0)).reverse().find(live) || null;
}

// ---- drawing
// A bot's name opens the bot, as its side-rail entry does. A bot this person can't see, a person or Tico itself has
// no link.
const decideChatHref = actor => {
  const slug = actorSlug(actor);
  return slug && (S.emps || []).some(e => e.name === slug) ? `#/bot/${encodeURIComponent(slug)}` : '';
};
const decideCurrent = st => st.at ? st.cards.get(st.at) : null;
function decidePaintHead(st) {
  const root = $('.decide');
  if (!root) return;
  const live = decideLive(st), at = live.indexOf(st.at), c = decideCurrent(st);
  if (live.length - at <= DEC_MORE_AT) void needsMoreShow();     // the next page of cards, before the last one is reached
  $('.dc-count', root).innerHTML = c ? `<span class="tnum">${at + 1} of ${live.length}</span>` : '';
  $('[data-dc="prev"]', root).disabled = !c || at <= 0;
  $('[data-dc="next"]', root).disabled = !c || at >= live.length - 1;
}
function decideRender(st, enter = false) {
  const root = $('.decide');
  if (!root) return;
  decidePaintHead(st);
  const c = decideCurrent(st), stage = $('.dc-stage', root), actions = $('.dc-actions', root);
  st.shown = c?.key || null;                // every button and key acts on this card, and only once
  st.busy = false;
  root.classList.toggle('dc-empty', !c);
  if (!c) {
    stage.innerHTML = '<div class="dc-done"><h2>All caught up</h2><button type="button" class="ghost" data-dc="close">Close</button></div>';
    actions.innerHTML = actions.dataset.html = '';
    return;
  }
  const from = c.type === 'approve' || c.type === 'ask' ? c.it.requester : c.type === 'stale' ? 'keeper' : c.type === 'pick' ? c.it.ask.from_actor
    : (c.it.waiting_on === myActor() || c.it.kind === 'waiting') && String(c.it.owner).startsWith('bot:') ? c.it.owner
    : c.it.requester || c.it.owner;
  const decide = c.type !== 'task' || c.yn;
  // Who really asks: Tico for its own checks, never the assistant's name; a question card says no "You asked".
  const name = from === 'keeper' ? 'Tico' : actorLabel(from) || 'Tico', chat = decideChatHref(from);
  const origin = c.type === 'pick' ? '' : decideFromPerson(c.it, from);
  // A bot's face and name open the bot, as the side rail does; the face is a second way in for the pointer, the name the one for keys.
  const face = chat ? `<a class="dc-who-face" href="${esc(chat)}" tabindex="-1" aria-hidden="true">${actorFace(from, 32)}</a>`
    : from === 'keeper' ? personCircle('Tico', 32) : actorFace(from, 32);
  const sub = c.type === 'pick' ? qcardSub(c.it) : c.type === 'approve' ? plainActors(approvalTitle(c.it) || c.it.title || '')
    : c.type === 'memory_tidy' ? `Expires ${fmt(c.it.expires)}` : c.type === 'ask' || c.type === 'stale' ? ageShort(c.it.created) : decideMeta(c.it);
  // The chip names what the card is; a task card outside the questions names what it needs (Pay, Review and send).
  const chip = c.type === 'approve' ? 'Approval' : c.type === 'stale' ? 'Stale check' : c.type === 'memory_tidy' ? 'Nightly memory tidy'
    : c.type === 'task' && c.it.need && c.it.need !== 'decide' ? needLabel(c.it.need) : 'Question';
  // A card the note goes with: a comment on a task, a note with a decision, the answer to a question.
  const note = ['stale', 'memory_tidy'].includes(c.type) ? '' : `<div class="dc-note"><textarea rows="1" maxlength="4000" aria-label="${c.type === 'ask' ? 'Your answer' : decide ? 'Note' : 'Comment'}" placeholder="${c.type === 'ask' ? 'Your answer' : decide ? 'Add a note' : 'Add a comment'}"></textarea>
      ${decide ? '' : `<button type="button" class="dc-send" data-dc="comment" aria-label="Send comment" title="${DC_SEND_WHY}" disabled>${ICON_SEND}</button>`}</div>`;
  stage.innerHTML = `<article class="dc-card${enter ? ' in' : ''}" data-key="${esc(c.key)}">
    <div class="dc-who">${face}
      <div class="dc-who-text"><div class="dc-who-name">${chat ? `<a class="dc-who-link" href="${esc(chat)}" title="${esc(`Open ${name}`)}">${esc(name)}</a>` : esc(name)}${origin ? `<span class="dc-origin"> · ${esc(origin)}</span>` : ''}</div>
        <div class="dc-who-sub">${esc(sub)}</div></div>
      ${decideDue(c.it) ? `<span class="tl-due${decideDue(c.it).late ? ' late' : ''}">${esc(decideDue(c.it).words)}</span>` : ''}
      <span class="pill needs">${esc(chip)}</span></div>
    <div class="dc-body"></div>${note}
  </article>`;
  decidePaintBody(st);
  actions.innerHTML = actions.dataset.html = decideActionsHTML(c);
  const card = $('.dc-card', stage);
  if (enter) requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove('in')));
  decideSwipe(st, card);
  void decideLoad(st, c);
  const next = st.cards.get(decideStepKey(st, 1));
  if (next) void decideLoad(st, next);
}
// A card's deadline: a person's task's due date (a bot's own task's due is only when it looks again).
const decideDue = it => { const d = ['approval', 'ask', 'stale', 'memory_tidy'].includes(it.kind) ? null : taskDueInfo(it); return d?.kind === 'deadline' ? d : null; };
function decidePaintBody(st) {
  const c = decideCurrent(st), body = $('.decide .dc-body');
  if (!c || !body) return;
  // Other bots' tasks waiting on the person for the same thing are named on the card.
  const html = c.type === 'memory_tidy' ? decideMemoryTidyHTML(st, c) : c.type === 'approve' ? decideApprovalHTML(c) : c.type === 'stale' ? decideStaleHTML(c)
    : (c.type === 'pick' ? decidePickHTML(st, c) : c.type === 'ask' ? decideAskHTML(c) : decideTaskHTML(st, c)) + foldedHTML(c.it) + decideClarifyHTML(c);
  if (body.dataset.html !== html) body.innerHTML = body.dataset.html = html;
}
function decideStalePaint(st) {
  const c = decideCurrent(st), body = $('.decide .dc-body'), actions = $('.decide .dc-actions');
  if (!c || !body) return;
  const html = decideStaleHTML(c), act = decideActionsHTML(c);
  if (body.dataset.html !== html) { tasksPatch(body, html); body.dataset.html = html; }
  if (actions.dataset.html !== act) actions.innerHTML = actions.dataset.html = act;
  decidePaintHead(st);
}
// A stale ask from a bot to me (the server marks it `stale` past three days unanswered): one tap asks the bot
// whether it still matters, in clearer words (backend/clarify_asks.py).
const decideStaleAsk = c => { const a = c?.it?.ask;
  return !!a && String(a.from_actor || '').startsWith('bot:') && a.to_actor === myActor() && !!a.stale; };
function decideClarifyHTML(c) {
  if (!decideStaleAsk(c)) return '';
  const bot = actorLabel(c.it.ask.from_actor) || 'the bot';
  return `<button type="button" class="ghost dc-clarify" data-dc="clarify" title="Ask ${esc(bot)} whether this still matters, in clearer words">Ask ${esc(bot)} to clarify</button>`;
}
const decideTwo = c => c.type === 'pick' && !c.q.multi && c.q.options.length === 2;
// Yes / No reads No, Yes like an approval: the affirmative on the right.
const decideYesNo = c => decideTwo(c) && c.q.options.map(o => String(o.label).toLowerCase()).join() === 'yes,no';
const decideRecommends = c => (c.type === 'task' || c.type === 'ask') && !!c.it.recommend?.ask;
function decideActionsHTML(c) {
  const k = key => `<kbd>${key}</kbd>`;
  // The bot said what it would do: taking that is the main button, across the row, and the card's own answers stay
  // below it.
  // A question card's fallback is a small line at its foot, never a recommendation (question-card.js).
  const rec = decideRecommends(c)
    ? `<button type="button" class="primary dc-rec" data-dc="recommend" title="${esc(c.it.recommend.do)}">Do what you recommend${k('R')}</button>` : '';
  if (rec) return rec + decideActionsPlain(c, k).replace(/ class="primary"/g, '');
  return decideActionsPlain(c, k);
}
// A Product roadmap item finishes or comes back only by a step move (the task window hides Done for it too): its card
// offers no Done, and a yes or no on the person's own one goes as a comment, not a status.
const decideStepsOnly = c => c.type === 'task' && (!!c.it.steps_only || pipelineStepsOnly(c.it));
function decideActionsPlain(c, k) {
  const skip = `<button type="button" data-dc="skip">Skip${k('S')}</button>`;
  if (c.type === 'pick' && (c.q.multi || decideTwo(c))) return qcardActionsHTML(c.it, c.q, {attr: 'dc', ticked: decideTicked(DEC, c), keys: true});
  // A pick between files has a button under each file: no Pick one row.
  if (c.type === 'pick' && qcardReview(c.it)?.pick) return skip;
  if (c.type === 'pick') return `${skip}<button type="button" disabled>Pick one</button>`;
  if (c.type === 'memory_tidy') return `<button type="button" data-dc="no">Discard</button><button type="button" class="primary" data-dc="yes"${c.it.keep_available ? '' : ' disabled title="Keep is not available yet"'}>Keep</button>`;
  if (c.type === 'approve') return `<button type="button" data-dc="no">Decline${k('N')}</button><button type="button" class="primary" data-dc="yes">Approve${k('Y')}</button>`;
  if (c.yn) return `<button type="button" data-dc="no">No${k('N')}</button><button type="button" class="primary" data-dc="yes">Yes${k('Y')}</button>`;
  if (c.type === 'ask') return `${skip}<button type="button" class="primary" data-dc="answer" data-dc-text disabled title="Type an answer">Answer</button>`;
  if (c.type === 'stale') return skip + ((c.it.rows || []).some(r => r.action) ? `<button type="button" class="primary" data-stale-all="${esc(c.it.id)}">Accept all</button>` : '');
  if (decideStepsOnly(c)) return `${skip}<a class="button primary" href="#/task/${encodeURIComponent(c.it.id)}" title="Move it to its next step in the task">Open to move it</a>`;
  return `${skip}<button type="button" class="primary" data-dc="done">Done${k('D')}</button>`;
}
// An option's picture, large: the image itself when small enough, else its thumb.
const decidePicture = (st, c, o) => qcardPicture(st.data.get(c.it.id)?.files, o);
function decidePickHTML(st, c) {
  return qcardBodyHTML(c.it, c.q, {attr: 'dc', ticked: decideTicked(st, c), pic: o => decidePicture(st, c, o)})
    + qcardFallbackHTML(c.it) + qcardDetailsHTML(c.it, c.q);
}
// Several picks: the ones ticked on this card, the recommended ones until the person changes them.
function decideTicked(st, c) {
  if (!st || c?.type !== 'pick' || !c.q.multi) return new Set();
  if (!st.ticks.has(c.key)) st.ticks.set(c.key, qcardRecommended(c.q));
  return st.ticks.get(c.key);
}
function decideTick(st, i, on) {
  const c = decideCurrent(st), ticked = decideTicked(st, c);
  if (!c?.q?.multi || !(i >= 0 && i < c.q.options.length)) return;
  if (on) ticked.add(i); else ticked.delete(i);
  const box = $(`.decide [data-qc-tick="${i}"]`); if (box) box.checked = on;
  qcardRetick($('.decide .dc-actions'), c.it, c.q, ticked, 'dc');
}
const DEC_APPROVAL_Q = {send: 'Send this email?', spend: 'Approve this spend?', publish: 'Publish this?', merge: 'Merge this?',
  support: 'Send this to Tico support?', task_cap: 'Set this task cap?', spend_limit: 'Set this spend limit?',
  finance: 'Approve this Finance change?'};
// The exact payload, as Needs you has always shown it (needs-you.js approvalPayloadNode): every value set as text,
// the raw JSON folded underneath.
function decideApprovalHTML(c) {
  return `<p class="dc-q">${esc(DEC_APPROVAL_Q[c.it.what] || 'Approve this?')}</p>${approvalPayloadNode(c.it).outerHTML}`;
}
// A question asked outside any task: the question, and what the bot would do; the answer is the note.
function decideAskHTML(c) {
  return `<div class="dc-msg md">${safeMd(plainActors(c.it.body || c.it.first_line || c.it.title || ''))}</div>${recommendLine(c.it.recommend, c.it.requester)}`;
}
// Plain text for the checked diff/diary: memory is untrusted, never HTML.
function decideMemoryTidyHTML(st, c) {
  const it = st.data.get(c.it.id)?.card || c.it;
  return `<h2>Nightly memory tidy: ${esc(actorLabel('bot:' + it.bot) || it.bot)}</h2>
    <p>${esc(it.model)} · Estimated cost $${Number(it.cost_usd).toFixed(4)}</p>
    <p>Memory commit <code>${esc(it.memory_commit)}</code></p>
    <pre>${esc(it.diary)}</pre><details open><summary>Proposed changes</summary><pre>${esc(it.diff ?? (st.data.get(c.it.id)?.error ? 'Could not load proposed changes. Reopen the card to try again.' : 'Loading proposed changes…'))}</pre></details>`;
}

// The Stale tickets card: a row per task with Accept and Skip (needs-you.js staleCardRow), Accept all below.
function decideStaleHTML(c) {
  const sig = (c.it.rows || []).map(r => [r.id, r.task, r.title, r.reason, r.suggest, r.action, r.version]);
  return `<p class="dc-q">${esc(c.it.title || 'Stale tickets')}</p><div class="dc-stale" data-stale-card="${esc(c.it.id)}" data-patch-key="stale:${esc(c.it.id)}" data-patch-sig="${staleSig(sig)}">
    <ul class="stale-rows" aria-label="Stale ticket suggestions">${(c.it.rows || []).map(staleCardRow).join('')}</ul></div>`;
}
function decideMeta(it) {
  const me = myActor();
  if (it.ask_missing) return `Waiting for ${actorLabel(it.owner) || 'its bot'} to explain · ${ageShort(it.updated || it.created)}`;
  if (it.waiting_on === me || it.kind === 'waiting') return `Waiting on you · ${ageShort(it.updated || it.created)}`;
  if (it.kind === 'question') return `Question for you · ${ageShort(it.ask?.created || it.created)}`;
  return `Assigned to you · ${ageShort(it.created)}`;
}
// What a task card leads with: the plain ask, in the words written to the person. In order, an open question to them;
// the ask the bot wrote in its waiting note or its task's first paragraph (`person_ask`, checked by the server); on a
// card not waiting on them, the last thing someone else said to them (not a copy of a status note: the server mirrors
// those into the chat), then the task's request. A bot's card with none of these is waiting for that bot to explain
// (`ask_missing`): the server has asked the bot to restate it. The owner's status note, the rest of the body and the
// files fold under Details.
function decideLead(st, c) {
  const it = c.it, data = st.data.get(it.id), me = myActor(), comments = data?.comments || [];
  const note = String(it.note || '').trim(), text = s => String(s || '').trim();
  const asked = [it.ask, ...comments.filter(m => m.kind === 'ask' && !m.answered_by).reverse()]
    .find(m => m && m.to_actor === me && m.from_actor !== me && text(m.body));
  const said = comments.filter(m => m.kind === 'say' && m.from_actor !== me && m.to_actor === me && !m.refs?.note
    && text(m.body) && text(m.body) !== note).at(-1);
  // A body that opens with the title does not say it twice; an ask taken from its first paragraph is not said twice.
  const title = text(it.title), whole = text(taskBody(it));
  let body = whole.startsWith(title) ? whole.slice(title.length).trim() : whole;
  const ask = text(asked?.body) || text(it.person_ask);
  if (ask && body.startsWith(ask)) body = body.slice(ask.length).trim();
  // A bot's task waiting on me asks only in its note or an open question: what else it wrote is its own record.
  const botWait = it.ask_missing || decideWaitingOnMe(it) && String(it.owner || '').startsWith('bot:');
  const lead = ask || (botWait ? '' : text(said?.body) || body);
  return {lead, rest: lead === body ? '' : body, said: said && text(said.body) !== lead ? text(said.body) : '',
    note: note && note !== lead ? note : ''};
}
const decideWaitingOnMe = it => it.waiting_on === myActor() || it.kind === 'waiting';
function decideTaskHTML(st, c) {
  const it = c.it, data = st.data.get(it.id);
  const {lead, rest, said, note} = decideLead(st, c);
  const bot = String(it.owner || '').startsWith('bot:') ? actorLabel(it.owner) : decideBot(c);
  // No plain ask yet: the card says who it waits on to explain, and the comment box is a way to ask them.
  const head = lead ? `<div class="dc-msg md">${safeMd(plainActors(lead))}</div>`
    : it.ask_missing || data && decideWaitingOnMe(it)
      ? `<p class="dc-wait">${esc(`Waiting for ${bot} to explain`)}</p>
         <button type="button" class="ghost dc-ask" data-dc="ask">Comment to ask</button>` : '';
  const files = (data?.files || []).filter(f => !f.archived && f.versions.length);
  const shown = files.map(f => {
    const v = tfVersion(f), kind = tfKind(f.name, v.mime || f.mime);
    const src = kind === 'image' ? (Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : tfPicture(v, kind)) : null;
    return src ? `<a class="dc-img tf-skel" href="${esc(v.url)}" target="_blank" rel="noopener" aria-label="${esc(f.name)}"><img alt="" data-tf-src="${esc(src)}"></a>`
      : `<a class="dc-file" href="${esc(v.url)}" target="_blank" rel="noopener"><span class="tc-kind">${esc(tfGlyph(kind, f.name))}</span><span class="dc-fname">${esc(f.name)}</span><span class="muted tnum">v${v.n}</span></a>`;
  }).join('');
  const details = [rest ? `<div class="dc-rest md">${safeMd(plainActors(rest))}</div>` : '',
    shown ? `<div class="dc-files">${shown}</div>` : '',
    said ? `<div class="dc-latest"><b>Latest message</b><p>${esc(plainActors(said))}</p></div>` : '',
    note ? `<div class="dc-latest"><b>Latest note</b><p>${esc(plainActors(note))}</p></div>` : ''].join('');
  return `${head}<p class="dc-title"><a href="#/task/${encodeURIComponent(it.id)}" title="Open the task">${esc(plainActors(it.title || ''))}</a></p>${details
    ? `<details class="dc-details"><summary>Details</summary>${details}</details>` : ''}`;
}
async function decideLoad(st, c, again = false) {
  if (c.type === 'memory_tidy') {
    if (st.data.has(c.it.id) && !st.data.get(c.it.id)?.error && !again) return;
    st.data.set(c.it.id, null);
    const data = await v2Get(`/v2/memory-tidy/cards/${encodeURIComponent(c.it.id)}`);
    st.data.set(c.it.id, data?.card ? data : {error: true});
    if (DEC === st && st.at === c.key) decidePaintBody(st);
    return;
  }
  if (!['pick', 'task'].includes(c.type) || st.data.has(c.it.id) && !again) return;
  const id = c.it.id, version = c.it.version;
  if (!st.data.has(id)) st.data.set(id, null);
  const [files, comments] = await Promise.all([v2Get(`/v2/tasks/${encodeURIComponent(id)}/files`),
    c.type === 'task' ? v2Get(`/v2/tasks/${encodeURIComponent(id)}/comments`) : null]);
  // A read that failed records nothing: a refreshed card keeps what it shows (and Done its version); a first one
  // shows the card's own text, at the version it was drawn with.
  if (!files || c.type === 'task' && !comments) { if (!st.data.get(id)) st.data.set(id, {version, files: [], comments: []}); return; }
  st.data.set(id, {version, files: Array.isArray(files?.files) ? files.files.map(tfNorm) : [], comments: comments?.comments || []});
  if (DEC === st && st.at === c.key) decidePaintBody(st);
}

// ---- acting
// Who a card's answer goes to, named in its toast: the asking bot, the approval's requester, or the task's bot.
const decideBot = c => actorLabel(c.type === 'pick' ? c.it.ask.from_actor : c.type === 'approve' || c.type === 'ask' ? c.it.requester
  : [c.it.owner, c.it.requester].find(a => String(a || '').startsWith('bot:')) || c.it.requester) || 'Tico';
// What an action sends, as requests run in order when it commits, and what the toast says. Each request has its own
// idempotency key from the start, so sending it again (pagehide while it is in flight) lands once.
function decideWrite(c, what, i, note) {
  const out = decideWriteBody(c, what, i, note);
  out.writes = out.writes.map(w => ({...w, key: newRequestId()}));
  return out;
}
function decideWriteBody(c, what, i, note) {
  const shown = DEC?.data.get(c.it.id)?.version;
  const id = encodeURIComponent(c.it.id), to = decideBot(c);
  if (c.type === 'memory_tidy') {
    const decision = what === 'yes' ? 'keep' : 'discard';
    return {writes: [{path: `/v2/memory-tidy/cards/${id}/decide`, body: {decision}}],
      label: decision === 'keep' ? 'Kept Nightly memory tidy' : 'Discarded Nightly memory tidy'};
  }
  if (['pick', 'all', 'ticked'].includes(what)) {
    // One pick, Yes to all, or the ticked picks (the rest go to the bot as "not this one"): question-card.js.
    const labels = qcardLabels(c.q, what, i, decideTicked(DEC, c));
    return {writes: qcardWrites(c.it, c.q, labels, note), label: qcardSaid(c.it, c.q, labels)};
  }
  if (what === 'clarify') {
    const who = c.it.ask?.from_actor ? actorLabel(c.it.ask.from_actor) : to;
    return {writes: [{path: '/v2/needs-you/clarify', body: {ask: c.it.ask.id}}], label: `Asked ${who} to clarify`};
  }
  if (what === 'recommend') {
    const rec = c.it.recommend;
    const version = c.it.kind === 'ask' ? null : shown || c.it.version;
    return {writes: [{path: '/v2/needs-you/recommended', body: {ask: rec.ask, ...(version ? {version} : {})}},
      ...(note ? [{path: c.it.kind === 'ask' ? `/v2/conversations/${encodeURIComponent(c.it.conversation_id)}/messages` : `/v2/tasks/${id}/comments`, body: {text: note}}] : [])],
      label: `Done what ${to} recommends · ${rec.do}`};
  }
  // A question outside any task: the answer goes back to the bot that asked.
  if (what === 'answer') return {writes: [{path: `/v2/messages/${id}/answer`, body: {text: note}}], label: `Answered · sent to ${to}`};
  if ((what === 'yes' || what === 'no') && c.type === 'task') {
    // A yes/no ask on a task: on the person's own task it finishes (yes) or declines (no) it with the answer as
    // its note, as Done does; on a bot's task waiting on them the answer goes back as a comment, which wakes the bot.
    const said = (what === 'yes' ? 'Yes.' : 'No.') + (note ? ' ' + note : '');
    const label = `Said ${what} · sent to ${to}`;
    if (c.it.owner === myActor() && !decideStepsOnly(c))
      return {writes: [{path: `/v2/tasks/${id}`, body: {status: what === 'yes' ? 'done' : 'declined', version: shown || c.it.version || 1, note: said}}], label};
    return {writes: [{path: `/v2/tasks/${id}/comments`, body: {text: said}}], label};
  }
  if (what === 'yes' || what === 'no')
    return {writes: [{path: `/v2/approvals/${id}`, body: {decision: what === 'yes' ? 'approved' : 'declined', ...(note ? {note} : {})}}],
      label: `${what === 'yes' ? 'Approved' : 'Declined'} · sent to ${to}`};
  // Done goes with the version the card showed: a task that changed meanwhile is refused, not finished unseen.
  if (what === 'done' && !decideStepsOnly(c)) return {writes: [{path: `/v2/tasks/${id}`, body: {status: 'done', version: shown || c.it.version || 1, ...(note ? {note} : {})}}], label: 'Done'};
  return {writes: [{path: `/v2/tasks/${id}/comments`, body: {text: note}}], label: `Sent to ${to}`};
}
// Text may be only a note on an unresolved choice. Keep its card until the server settles it,
// using the same durable outbox and request key as every other card action.
function decideTextAnswer(st, c, note) {
  st.busy = true;
  const root = $('.decide');
  root?.setAttribute('aria-busy', 'true');
  root?.querySelectorAll('button, textarea').forEach(el => el.disabled = true);
  const p = {key: c.key, write: decideWrite(c, 'answer', null, note), note, sent: 0, waitSettlement: true};
  DEC_OUTBOX.add(p);
  decideSave();
  void decideSend(st, p);
}
function decideTextResult(st, p, feedback, saved) {
  if (!st) return;
  st.busy = false;
  if (DEC !== st) return;
  $('.decide')?.removeAttribute('aria-busy');
  decideRender(st);
  if (st.at !== p.key) return;
  const box = $('.decide .dc-note textarea');
  if (box) { box.value = saved ? '' : p.note || ''; box.dispatchEvent(new Event('input', {bubbles: true})); }
  if (feedback) {
    const status = document.createElement('p');
    status.className = 'dc-answer-feedback'; status.setAttribute('role', 'status'); status.textContent = feedback;
    $('.decide .dc-body')?.append(status);
  }
}
function decideAct(st, what, i, shown = st.shown) {
  const key = st.at, c = decideCurrent(st), root = $('.decide');
  // A second tap while the card leaves, or a button from a card no longer current, does nothing.
  if (!c || !root || st.busy || key !== shown || performance.now() < (st.lockUntil || 0)) return;
  const card = $('.dc-card', root);
  if (['skip', 'next', 'prev'].includes(what)) {
    const to = decideStepKey(st, what === 'prev' ? -1 : 1, what === 'skip');
    if (!to) return;
    st.lockUntil = performance.now() + 350;
    st.at = to;
    decideLeaveCard(st, card, what === 'prev' ? 'right' : 'left');
    return;
  }
  const box = $('.dc-note textarea', root), note = box?.value.trim() || '';
  if ((what === 'comment' || what === 'answer') && !note) { box?.focus(); return; }
  if (what === 'answer') { void decideTextAnswer(st, c, note); return; }
  if (what === 'ticked' && !decideTicked(st, c).size) return;
  // Request changes on a review goes with the note that says what to change.
  if (what === 'pick' && !note && qcardNeedsNote(c.it, c.q, qcardLabels(c.q, 'pick', i))) {
    if (box) { box.placeholder = 'What should change?'; box.focus(); }
    return;
  }
  st.lockUntil = performance.now() + 350;
  const write = decideWrite(c, what, i, note);
  void decideCommit(st);                       // the previous action goes now
  DEC_HANDLED.add(key);
  st.at = decideAfter(st, key);
  const pending = st.pending = {key, write, note};
  pending.timer = setTimeout(() => { if (st.pending === pending) void decideCommit(st); }, DEC_UNDO_MS);
  if (what === 'pick') $(`.dc-opt[data-i="${i}"]`, card)?.classList.add('sel');
  decideLeaveCard(st, card, what === 'no' ? 'left' : 'right');
  needsListsRedraw();
  decideToast(st, write.label, () => {
    if (st.pending !== pending) return;
    clearTimeout(pending.timer); st.pending = null;
    DEC_HANDLED.delete(key); st.at = key;
    decideRender(st, true);
    needsListsRedraw();
    const again = $('.decide .dc-note textarea'); if (again) { again.value = note; again.dispatchEvent(new Event('input', {bubbles: true})); }
  });
}
function decideLeaveCard(st, card, side) {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!card || reduce) { decideRender(st, false); return; }
  st.busy = true;
  card.classList.add('out-' + side);
  card.style.pointerEvents = 'none';
  setTimeout(() => { if (DEC === st) decideRender(st, true); }, 170);
}
// Writes on their way: in the outbox until the server confirms each request, whether or not the card is still open.
const DEC_OUTBOX = new Set();
// The outbox is kept in this browser too, so a write the page could not confirm before it closed goes out on the
// next load, under the same keys.
const DEC_SAVED = 'tico.decide.outbox';
let DEC_SIGNED_OUT = false;
function decideSave() {
  if (DEC_SIGNED_OUT) return;
  try { localStorage.setItem(DEC_SAVED, JSON.stringify([...DEC_OUTBOX].map(p => ({me: p.me || S.me?.id, key: p.key, waitSettlement: p.waitSettlement, writes: p.write.writes.slice(p.sent || 0)})))); } catch {}
}
function decideResume() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(DEC_SAVED) || '[]'); } catch {}
  // Only the person who took them: another person signed in to this browser never sends them.
  if (!S.me?.id) return;
  for (const item of Array.isArray(saved) ? saved : []) {
    if (!item?.writes?.length || item.me !== S.me.id || [...DEC_OUTBOX].some(p => p.key === item.key)) continue;
    const p = {me: item.me, key: item.key, write: {writes: item.writes}, sent: 0, waitSettlement: item.waitSettlement};
    DEC_OUTBOX.add(p);
    void decideSend(null, p);
  }
}
setTimeout(decideResume, 3000);
// Sends the waiting action, its requests in order.
async function decideCommit(st) {
  const p = st.pending;
  if (!p) return;
  st.pending = null;
  clearTimeout(p.timer);
  decideToastHide(st, p);
  p.sent = 0;
  DEC_OUTBOX.add(p);
  decideSave();
  await decideSend(st, p);
}
// A line for the person: in the card while it is open (a page toast would sit under it), else the page's toast.
function needsSay(text, err = false) {
  const t = DEC && $('#need-card')?.open && $('.decide .dc-toast');
  if (!t) { toast(text, err); return; }
  clearTimeout(DEC.toastTimer);
  $('span', t).textContent = text;
  $('button', t).hidden = true;
  t.hidden = false;
  DEC.undo = null; DEC.toastFor = null;
  DEC.toastTimer = setTimeout(() => { t.hidden = true; }, DEC_UNDO_MS);
}
// The requests not yet confirmed, in order, each under the key it got when the action was taken.
async function decideSend(st, p) {
  try {
    for (const w of p.write.writes.slice(p.sent)) {
      const result = await post(w.path, w.body, w.key);
      if (p.waitSettlement) { p.decisionOpen = !!result.decision_open; p.feedback = result.feedback; }
      p.sent++;
      decideSave();
    }
    DEC_OUTBOX.delete(p);
    decideSave();
    if (p.waitSettlement) {
      if (!p.decisionOpen) {
        DEC_HANDLED.add(p.key);
        if (st?.at === p.key) st.at = decideAfter(st, p.key);
      }
      decideTextResult(st, p, p.decisionOpen ? p.feedback || 'Note saved. Decision still open; choose an option.' : '', true);
      needsListsRedraw();
    }
    if (st && !liveConnected()) liveSoon('decide-needs', () => v2Refresh().then(() => {
      if (DEC === st) decideRefresh(st, S.v2.needs || []);
      needsListsRedraw();
    }), 300);
  } catch (e) {
    if (e.unconfirmed) {
      // It may have landed: never act again under new keys. It stays out of the list and is sent again as it was.
      if (!p.retries && st) needsSay('Not confirmed yet; trying again', true);
      p.retries = (p.retries || 0) + 1;
      setTimeout(() => { if (DEC_OUTBOX.has(p)) void decideSend(st, p); }, Math.min(60000, 5000 * 2 ** Math.min(p.retries - 1, 4)));
      return;
    }
    DEC_OUTBOX.delete(p);
    DEC_HANDLED.delete(p.key);
    decideSave();
    needsListsRedraw();
    if (p.waitSettlement) { decideTextResult(st, p, e.message, false); return; }
    if (!st) return;                            // sent again from an earlier page: nothing on screen to put back
    const changed = e.status === 409 || e.body?.error?.code === 'version_conflict';
    needsSay(changed && !String(p.key).startsWith('memory_tidy:') ? 'That task changed. Look again.' : e.message, true);
    st.data.delete(String(p.key).replace(/^(?:task|memory_tidy):/, ''));
    if (DEC === st) {
      st.at = p.key;
      decideRender(st, true);
      if (changed) void v2Refresh().then(() => { if (DEC === st) decideRefresh(st, S.v2.needs || []); });
    }
  }
}
// Leaving the page: what waits on its toast and what is still on its way go out with keepalive, under the same keys.
function decideFlush() {
  const all = [...DEC_OUTBOX];
  if (DEC?.pending) {
    const p = DEC.pending; clearTimeout(p.timer); DEC.pending = null;
    p.sent = 0; all.push(p); DEC_OUTBOX.add(p); decideSave();
  }
  for (const p of all) for (const w of p.write.writes.slice(p.sent || 0)) {
    fetch(API + w.path, {method: 'POST', keepalive: true, body: JSON.stringify(w.body),
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': w.key}}).catch(() => {});
  }
}
window.addEventListener('pagehide', decideFlush);
// Signing out: what still waits goes out as on leaving the page, then none of it (a note's words included) stays in
// this browser for whoever uses it next. The same links observability.js resets on.
document.addEventListener('click', e => {
  const link = e.target.closest?.('a[href]');
  if (!link || !/\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href'))) return;
  decideFlush();
  DEC_SIGNED_OUT = true;
  DEC_OUTBOX.clear();
  try { localStorage.removeItem(DEC_SAVED); } catch {}
}, true);
function decideToast(st, text, undo) {
  const t = $('.decide .dc-toast');
  if (!t) return;
  clearTimeout(st.toastTimer);
  $('span', t).textContent = text;
  $('button', t).hidden = false;
  t.hidden = false;
  st.undo = undo;
  st.toastFor = st.pending;
  st.toastTimer = setTimeout(() => { t.hidden = true; st.undo = null; }, DEC_UNDO_MS);
}
function decideToastHide(st, p) {
  if (st.toastFor !== p) return;
  const t = $('.decide .dc-toast');
  if (t) t.hidden = true;
  st.undo = null;
}
function decideClick(st, ev) {
  const b = ev.target.closest('[data-dc]');
  if (!b || b.disabled || st.swiped) return;
  const what = b.dataset.dc;
  if (what === 'close') { decideStop(); return; }
  if (what === 'ask') { $('.decide .dc-note textarea')?.focus(); return; }
  if (what === 'undo') { const undo = st.undo; st.undo = null; $('.decide .dc-toast').hidden = true; undo?.(); return; }
  decideAct(st, what, Number(b.dataset.i));
}
function decideKey(st, ev) {
  if (DEC !== st || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || !$('#need-card')?.open
      || [...document.querySelectorAll('dialog[open]')].some(d => d.id !== 'need-card' && !d.contains($('#need-card')))) return;
  if (ev.target.matches?.('textarea, input:not([type=checkbox]), select, [contenteditable]')) {   // a ticked pick keeps the keys
    if (ev.key === 'Escape') { ev.preventDefault(); ev.target.blur(); }
    return;
  }
  const c = decideCurrent(st), k = ev.key.toLowerCase();
  if (!c) return;
  let what = null, i;
  if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') what = ev.key === 'ArrowLeft' ? 'prev' : 'next';
  else if (k === 's') what = 'skip';
  else if (k === 'c') { if (!$('.decide .dc-note textarea')) return; ev.preventDefault(); $('.decide .dc-note textarea').focus(); return; }   // every card, a multi too
  else if (c.type === 'pick' && c.q.multi) {
    // Several picks: a number ticks or unticks one; A says yes to all, T sends the ticked ones.
    if (/^[1-6]$/.test(k) && Number(k) <= c.q.options.length) {
      ev.preventDefault(); const n = Number(k) - 1; decideTick(st, n, !decideTicked(st, c).has(n)); return;
    }
    if (k === 'a') what = 'all';
    else if (k === 't') what = 'ticked';
  }
  else if (k === 'r' && decideRecommends(c)) what = 'recommend';
  else if ((c.type === 'approve' || c.yn) && (k === 'y' || k === 'n')) what = k === 'y' ? 'yes' : 'no';
  else if (decideYesNo(c) && (k === 'y' || k === 'n')) { what = 'pick'; i = k === 'y' ? 0 : 1; }
  else if (c.type === 'task' && !c.yn && k === 'd' && !decideStepsOnly(c)) what = 'done';
  else if (c.type === 'pick' && /^[1-6]$/.test(k) && Number(k) <= c.q.options.length) { what = 'pick'; i = Number(k) - 1; }
  if (!what) return;
  ev.preventDefault();
  decideAct(st, what, i);
}
// Swipe left for the next card, right for the one before. A sideways drag that moves the card swallows the click
// it ends with.
function decideSwipe(st, card) {
  let x0 = null, y0 = 0, dx = 0, id = null;
  card.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || ev.target.closest('textarea, a, .dc-note')) return;
    x0 = ev.clientX; y0 = ev.clientY; dx = 0; id = ev.pointerId; st.swiped = false;
  });
  card.addEventListener('pointermove', ev => {
    if (x0 === null || ev.pointerId !== id) return;
    dx = ev.clientX - x0;
    if (!st.swiped && Math.abs(ev.clientY - y0) > Math.abs(dx)) { x0 = null; return; }
    if (Math.abs(dx) > 8) {
      st.swiped = true;
      const m = Math.max(-40, Math.min(dx, 40));
      card.style.transition = 'none';
      card.style.transform = `translateX(${m}px) rotate(${m / 40}deg)`;
    }
  });
  const end = ev => {
    if (x0 === null || ev.pointerId !== id) return;
    x0 = null;
    card.style.transition = ''; card.style.transform = '';
    if (st.swiped && Math.abs(dx) > 90) decideAct(st, dx < 0 ? 'next' : 'prev');
    setTimeout(() => { st.swiped = false; }, 0);
  };
  card.addEventListener('pointerup', end);
  card.addEventListener('pointercancel', end);
}
