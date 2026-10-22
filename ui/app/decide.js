/* ui/app/decide.js — Decide: everything on the person's plate as one queue of cards, answered one at a time with two
   big buttons. What a person asked for comes first, then what bots made (unless it is due or old); within each,
   questions bots asked (a pick between options, images inline), then approvals, then the rest of their tasks
   (waiting on them, then theirs). An answer, Done or a comment hands the task back to the bot; it is sent
   when its toast runs out or the next action lands, so Undo never needs the server.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let DEC = null;                 // the open Decide page's state, null elsewhere
const DEC_UNDO_MS = 4000;

// ---- which cards, in which order (GET /v2/needs-you items)
// A question answered in the card (question-card.js): one question with 2 to 6 options, a pick or several picks, put
// to this person by someone else. Several questions in one ask are a task card that links to the task.
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
function decideCards(items) {
  const me = myActor(), asks = [], approvals = [], waiting = [], mine = [], unexplained = [];
  for (const it of items || []) {
    if (it.kind === 'approval') { approvals.push({key: 'approval:' + it.id, type: 'approve', it, at: it.created}); continue; }
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
// A card's deadline: a person's task's due date (a bot's own task's due is only when it looks again).
const decideDue = it => { const d = it.kind === 'approval' ? null : taskDueInfo(it); return d?.kind === 'deadline' ? d : null; };
// "From Ana" beside the name when the card's work traces back to another person than the one shown;
// "You asked" when it is the viewer's own request a bot is bringing back.
function decideFromPerson(it, shown) {
  const p = it.from_person;
  if (!p || p === shown) return '';
  return p === myActor() ? 'You asked' : `From ${actorLabel(p)}`;
}
const decideCount = () => S.me?.role === 'bot' ? 0 : decideCards(S.v2?.needs).length;
// The Tasks page's way in: "Decide · N", only when there is something. A phone has it in the header, a wide
// screen in the filter bar, where the header's search keeps its room.
function decidePaintButton() {
  const links = document.querySelectorAll('.tl-decide');
  if (!links.length) return;
  const n = decideCount();
  for (const b of links) { b.hidden = !n; b.textContent = `Decide · ${n}`; }
}

// ---- the page
function pageDecide() {
  decideStop();
  const main = $('#main');
  main.classList.add('decide-layout');
  document.body.classList.add('decide-open');
  main.innerHTML = `<section class="decide" aria-label="Decide">
    <header class="dc-head">
      <button type="button" class="dc-back" data-dc="back" aria-label="Back to Tasks" title="Back to Tasks"><span class="nav-icon" aria-hidden="true">chevron_left</span></button>
      <h1 class="dc-count" aria-live="polite"></h1>
      <span class="dc-right"></span>
    </header>
    <div class="dc-stage"></div>
    <div class="dc-actions"></div>
    <p class="dc-keys" aria-hidden="true">1–6 pick · Y / N approve · R do what it recommends · D done · S skip · C comment</p>
    <div class="dc-toast" role="status" hidden><span></span><button type="button" data-dc="undo">Undo</button></div>
  </section>`;
  const st = DEC = {queue: [], cards: new Map(), handled: new Set(), pending: null, data: new Map(), ticks: new Map(), off: []};
  const root = $('.decide', main);
  root.addEventListener('click', ev => decideClick(st, ev));
  root.addEventListener('change', ev => { if (ev.target.matches('[data-qc-tick]')) decideTick(st, Number(ev.target.dataset.qcTick), ev.target.checked); });
  root.addEventListener('input', ev => {
    const box = ev.target.closest('.dc-note textarea');
    if (!box) return;
    box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 120) + 'px';
    const send = $('.dc-send', root); if (send) send.disabled = !box.value.trim();
  });
  root.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && ev.target.matches('.dc-note textarea') && $('.dc-send', root) && ev.target.value.trim()) {
      ev.preventDefault(); decideAct(st, 'comment');
    }
  });
  const keys = ev => decideKey(st, ev);
  document.addEventListener('keydown', keys);
  st.off.push(() => document.removeEventListener('keydown', keys));
  if (typeof liveOn === 'function') st.off.push(liveOn('needs-list', items => { if (DEC === st) decideRefresh(st, items); }));
  decideSync(st, S.v2?.needs || []);
  decideRender(st);
  void v2Refresh().then(() => { if (DEC === st) decideRefresh(st, S.v2.needs || []); });
}
// Leaving sends what is waiting on its toast.
function decideStop() {
  const st = DEC;
  if (!st) return;
  DEC = null;
  void decideCommit(st);
  st.off.forEach(fn => fn());
  document.body.classList.remove('decide-open');
}

// The server's list changed (live, or a read after a write): drop what left, append what is new, keep the order.
function decideSync(st, items) {
  const fresh = decideCards(items), keys = new Set(fresh.map(c => c.key));
  for (const c of fresh) st.cards.set(c.key, c);
  st.queue = st.queue.filter(k => keys.has(k));
  for (const k of [...st.handled]) if (!keys.has(k) && st.pending?.key !== k) st.handled.delete(k);
  const sending = new Set([...DEC_OUTBOX].map(p => p.key));     // on its way, perhaps from an earlier visit
  st.queue = st.queue.filter(k => !sending.has(k));
  for (const c of fresh) if (!st.queue.includes(c.key) && !st.handled.has(c.key) && st.pending?.key !== c.key && !sending.has(c.key)) st.queue.push(c.key);
}
function decideRefresh(st, items) {
  const before = st.queue[0], card = before && st.cards.get(before);
  decideSync(st, items);
  if (st.queue[0] !== before) decideRender(st, true);
  else if (card && JSON.stringify([st.cards.get(before).type, st.cards.get(before).it]) !== JSON.stringify([card.type, card.it])) {
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

// ---- drawing
// A bot's name opens the bot, as its side-rail entry does. A bot this person can't see, a person or Tico itself has
// no link.
const decideChatHref = actor => {
  const slug = actorSlug(actor);
  return slug && (S.emps || []).some(e => e.name === slug) ? `#/bot/${encodeURIComponent(slug)}` : '';
};
const decideCurrent = st => st.cards.get(st.queue[0]);
const decideDecision = c => c.type !== 'task' || !!c.yn;
function decidePaintHead(st) {
  const root = $('.decide');
  if (!root) return;
  const n = st.queue.length, d = st.queue.filter(k => decideDecision(st.cards.get(k))).length, c = decideCurrent(st);
  // How many cards the current group has left, so a sitting of decisions can be seen to end.
  const need = c?.it.need || 'do', inGroup = st.queue.filter(k => (st.cards.get(k)?.it.need || 'do') === need).length;
  $('.dc-count', root).innerHTML = n ? `<span class="tnum">${n} left</span>${c ? `<small class="tnum">${esc(needLabel(need))} · ${inGroup}</small>`
    : d ? `<small class="tnum">${d} decision${d === 1 ? '' : 's'}</small>` : ''}` : '';
  $('.dc-right', root).innerHTML = !c ? '' : decideDecision(c)
    ? '<button type="button" class="dc-text" data-dc="skip">Skip</button>'
    : `<a class="dc-text" href="#/task/${encodeURIComponent(c.it.id)}">Open task</a>`;
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
    stage.innerHTML = '<div class="dc-done"><h2>All caught up</h2><button type="button" class="ghost" data-dc="back">Back to tasks</button></div>';
    actions.innerHTML = '';
    return;
  }
  const from = c.type === 'approve' ? c.it.requester : c.type === 'pick' ? c.it.ask.from_actor
    : (c.it.waiting_on === myActor() || c.it.kind === 'waiting') && String(c.it.owner).startsWith('bot:') ? c.it.owner
    : c.it.requester || c.it.owner;
  const decide = decideDecision(c);
  // Who really asks: Tico for its own checks, never the assistant's name; a question card says no "You asked".
  const name = from === 'keeper' ? 'Tico' : actorLabel(from) || 'Tico', chat = decideChatHref(from);
  const origin = c.type === 'pick' ? '' : decideFromPerson(c.it, from);
  // A bot's face and name open the bot, as the side rail does; the face is a second way in for the pointer, the name the one for keys.
  const face = chat ? `<a class="dc-who-face" href="${esc(chat)}" tabindex="-1" aria-hidden="true">${actorFace(from, 32)}</a>` : actorFace(from, 32);
  stage.innerHTML = `<article class="dc-card${enter ? ' in' : ''}" data-key="${esc(c.key)}">
    <div class="dc-who">${face}
      <div class="dc-who-text"><div class="dc-who-name">${chat ? `<a class="dc-who-link" href="${esc(chat)}" title="${esc(`Open ${name}`)}">${esc(name)}</a>` : esc(name)}${origin ? `<span class="dc-origin"> · ${esc(origin)}</span>` : ''}</div>
        <div class="dc-who-sub">${esc(c.type === 'pick' ? qcardSub(c.it) : c.type !== 'task' ? plainActors(c.it.title || '') : decideMeta(c.it))}</div></div>
      ${decideDue(c.it) ? `<span class="tl-due${decideDue(c.it).late ? ' late' : ''}">${esc(decideDue(c.it).words)}</span>` : ''}
      <span class="pill ${decide ? 'needs' : ''}">${esc(needLabel(c.it.need || (decide ? 'decide' : 'do')))}</span></div>
    <div class="dc-body"></div>
    <div class="dc-note"><textarea rows="1" maxlength="4000" aria-label="${decide ? 'Note' : 'Comment'}" placeholder="${decide ? 'Add a note' : 'Add a comment'}"></textarea>
      ${decide ? '' : `<button type="button" class="dc-send" data-dc="comment" aria-label="Send comment" title="Send comment" disabled>${ICON_SEND}</button>`}</div>
  </article>`;
  decidePaintBody(st);
  actions.innerHTML = decideActionsHTML(c);
  const card = $('.dc-card', stage);
  if (enter) requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove('in')));
  decideSwipe(st, card);
  void decideLoad(st, c);
  const next = st.cards.get(st.queue[1]);
  if (next) void decideLoad(st, next);
}
function decidePaintBody(st) {
  const c = decideCurrent(st), body = $('.decide .dc-body');
  if (!c || !body) return;
  // Other bots' tasks waiting on the person for the same thing are named on the card, as in Needs you.
  const html = (c.type === 'pick' ? decidePickHTML(st, c) : c.type === 'approve' ? decideApprovalHTML(c) : decideTaskHTML(st, c))
    + (c.type === 'approve' ? '' : foldedHTML(c.it) + decideClarifyHTML(c));
  if (body.dataset.html !== html) body.innerHTML = body.dataset.html = html;
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
function decideActionsHTML(c) {
  const k = key => `<kbd>${key}</kbd>`;
  // The bot said what it would do: taking that is the main button, across the row, and the card's own answers stay
  // below it.
  // A question card's fallback is a small line at its foot, never a recommendation (question-card.js).
  const rec = c.type === 'task' && c.it.recommend?.ask
    ? `<button type="button" class="primary dc-rec" data-dc="recommend" title="${esc(c.it.recommend.do)}">Do what you recommend${k('R')}</button>` : '';
  if (rec) return rec + decideActionsPlain(c, k).replace(/ class="primary"/g, '');
  return decideActionsPlain(c, k);
}
function decideActionsPlain(c, k) {
  if (c.type === 'pick' && (c.q.multi || decideTwo(c))) return qcardActionsHTML(c.it, c.q, {attr: 'dc', ticked: decideTicked(DEC, c), keys: true});
  if (c.type === 'pick') return `<button type="button" data-dc="skip">Skip${k('S')}</button><button type="button" disabled>Pick one</button>`;
  if (c.type === 'approve' || c.yn) return `<button type="button" data-dc="no">No${k('N')}</button><button type="button" class="primary" data-dc="yes">Yes${k('Y')}</button>`;
  return `<button type="button" data-dc="skip">Skip${k('S')}</button><button type="button" class="primary" data-dc="done">Done${k('D')}</button>`;
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
  support: 'Send this to Tico support?'};
function decideApprovalHTML(c) {
  return `<p class="dc-q">${esc(DEC_APPROVAL_Q[c.it.what] || 'Approve this?')}</p>${approvalPayloadNode(c.it).outerHTML}`;
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
         <p class="dc-blank">${esc(`${bot} has been asked to say plainly what it needs from you.`)}</p>
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
  return `${head}<p class="dc-title">${esc(plainActors(it.title || ''))}</p>${details
    ? `<details class="dc-details"><summary>Details</summary>${details}</details>` : ''}`;
}
async function decideLoad(st, c, again = false) {
  if (c.type === 'approve' || st.data.has(c.it.id) && !again) return;
  const id = c.it.id, version = c.it.version;
  if (!st.data.has(id)) st.data.set(id, null);
  const [files, comments] = await Promise.all([v2Get(`/v2/tasks/${encodeURIComponent(id)}/files`),
    c.type === 'task' ? v2Get(`/v2/tasks/${encodeURIComponent(id)}/comments`) : null]);
  // A read that failed records nothing: a refreshed card keeps what it shows (and Done its version); a first one
  // shows the card's own text, at the version it was drawn with.
  if (!files || c.type === 'task' && !comments) { if (!st.data.get(id)) st.data.set(id, {version, files: [], comments: []}); return; }
  st.data.set(id, {version, files: Array.isArray(files?.files) ? files.files.map(tfNorm) : [], comments: comments?.comments || []});
  if (DEC === st && st.queue[0] === c.key) decidePaintBody(st);
}

// ---- acting
// Who a card's answer goes to, named in its toast: the asking bot, the approval's requester, or the task's bot.
const decideBot = c => actorLabel(c.type === 'pick' ? c.it.ask.from_actor : c.type === 'approve' ? c.it.requester
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
  if ((what === 'yes' || what === 'no') && c.type === 'task') {
    // A yes/no ask on a task: on the person's own task it finishes (yes) or declines (no) it with the answer as
    // its note, as Done does; on a bot's task waiting on them the answer goes back as a comment, which wakes the bot.
    const said = (what === 'yes' ? 'Yes.' : 'No.') + (note ? ' ' + note : '');
    const label = `Said ${what} · sent to ${to}`;
    if (c.it.owner === myActor())
      return {writes: [{path: `/v2/tasks/${id}`, body: {status: what === 'yes' ? 'done' : 'declined', version: shown || c.it.version || 1, note: said}}], label};
    return {writes: [{path: `/v2/tasks/${id}/comments`, body: {text: said}}], label};
  }
  if (what === 'yes' || what === 'no')
    return {writes: [{path: `/v2/approvals/${id}`, body: {decision: what === 'yes' ? 'approved' : 'declined', ...(note ? {note} : {})}}],
      label: `${what === 'yes' ? 'Approved' : 'Declined'} · sent to ${to}`};
  // Done goes with the version the card showed: a task that changed meanwhile is refused, not finished unseen.
  if (what === 'done') return {writes: [{path: `/v2/tasks/${id}`, body: {status: 'done', version: shown || c.it.version || 1, ...(note ? {note} : {})}}], label: 'Done'};
  return {writes: [{path: `/v2/tasks/${id}/comments`, body: {text: note}}], label: `Sent to ${to}`};
}
function decideAct(st, what, i, shown = st.shown) {
  const key = st.queue[0], c = st.cards.get(key), root = $('.decide');
  // A second tap while the card leaves, or a button from a card no longer current, does nothing.
  if (!c || !root || st.busy || key !== shown || performance.now() < (st.lockUntil || 0)) return;
  st.lockUntil = performance.now() + 350;
  const box = $('.dc-note textarea', root), note = box?.value.trim() || '';
  const card = $('.dc-card', root);
  if (what === 'skip') {
    st.queue.push(st.queue.shift());
    decideLeaveCard(st, card, 'left');
    decideToast(st, 'Skipped', () => { const k = st.queue.pop(); if (k) st.queue.unshift(k); decideRender(st, true); });
    return;
  }
  if (what === 'comment' && !note) { box?.focus(); return; }
  if (what === 'ticked' && !decideTicked(st, c).size) return;
  const write = decideWrite(c, what, i, note);
  void decideCommit(st);                       // the previous action goes now
  st.queue.shift();
  st.handled.add(key);
  const pending = st.pending = {key, write, note};
  pending.timer = setTimeout(() => { if (st.pending === pending) void decideCommit(st); }, DEC_UNDO_MS);
  if (what === 'pick') $(`.dc-opt[data-i="${i}"]`, card)?.classList.add('sel');
  decideLeaveCard(st, card, what === 'no' ? 'left' : 'right');
  decideToast(st, write.label, () => {
    if (st.pending !== pending) return;
    clearTimeout(pending.timer); st.pending = null;
    st.handled.delete(key); st.queue.unshift(key);
    decideRender(st, true);
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
// Writes on their way: in the outbox until the server confirms each request, whether or not Decide is still open.
const DEC_OUTBOX = new Set();
// The outbox is kept in this browser too, so a write the page could not confirm before it closed goes out on the
// next load, under the same keys.
const DEC_SAVED = 'tico.decide.outbox';
let DEC_SIGNED_OUT = false;
function decideSave() {
  if (DEC_SIGNED_OUT) return;
  try { localStorage.setItem(DEC_SAVED, JSON.stringify([...DEC_OUTBOX].map(p => ({me: p.me || S.me?.id, key: p.key, writes: p.write.writes.slice(p.sent || 0)})))); } catch {}
}
function decideResume() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(DEC_SAVED) || '[]'); } catch {}
  // Only the person who took them: another person signed in to this browser never sends them.
  if (!S.me?.id) return;
  for (const item of Array.isArray(saved) ? saved : []) {
    if (!item?.writes?.length || item.me !== S.me.id || [...DEC_OUTBOX].some(p => p.key === item.key)) continue;
    const p = {me: item.me, key: item.key, write: {writes: item.writes}, sent: 0};
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
// The requests not yet confirmed, in order, each under the key it got when the action was taken.
async function decideSend(st, p) {
  try {
    for (const w of p.write.writes.slice(p.sent)) {
      await post(w.path, w.body, w.key);
      p.sent++;
      decideSave();
    }
    DEC_OUTBOX.delete(p);
    decideSave();
    if (st && !liveConnected()) liveSoon('decide-needs', () => v2Refresh().then(() => { if (DEC === st) decideRefresh(st, S.v2.needs || []); }), 300);
  } catch (e) {
    if (e.unconfirmed) {
      // It may have landed: never act again under new keys. It stays out of the queue and is sent again as it was.
      if (!p.retries && st) toast('Not confirmed yet; trying again', true);
      p.retries = (p.retries || 0) + 1;
      setTimeout(() => { if (DEC_OUTBOX.has(p)) void decideSend(st, p); }, Math.min(60000, 5000 * 2 ** Math.min(p.retries - 1, 4)));
      return;
    }
    DEC_OUTBOX.delete(p);
    decideSave();
    if (!st) return;                            // sent again from an earlier page: nothing on screen to put back
    const changed = e.status === 409 || e.body?.error?.code === 'version_conflict';
    toast(changed ? 'That task changed. Look again.' : e.message, true);
    st.handled.delete(p.key);
    st.data.delete(String(p.key).replace(/^task:/, ''));
    if (DEC === st) {
      if (!st.queue.includes(p.key)) st.queue.unshift(p.key);
      else st.queue = [p.key, ...st.queue.filter(k => k !== p.key)];
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
  if (what === 'back') { location.hash = TASKS; return; }
  if (what === 'ask') { $('.decide .dc-note textarea')?.focus(); return; }
  if (what === 'undo') { const undo = st.undo; st.undo = null; $('.decide .dc-toast').hidden = true; undo?.(); return; }
  decideAct(st, what, Number(b.dataset.i));
}
function decideKey(st, ev) {
  if (DEC !== st || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || document.querySelector('dialog[open]')) return;
  if (ev.target.matches?.('textarea, input:not([type=checkbox]), select, [contenteditable]')) {   // a ticked pick keeps the keys
    if (ev.key === 'Escape') { ev.preventDefault(); ev.target.blur(); }
    return;
  }
  const c = decideCurrent(st), k = ev.key.toLowerCase();
  if (!c) return;
  let what = null, i;
  if (k === 's') what = 'skip';
  else if (k === 'c') { ev.preventDefault(); $('.decide .dc-note textarea')?.focus(); return; }   // every card, a multi too
  else if (c.type === 'pick' && c.q.multi) {
    // Several picks: a number ticks or unticks one; A says yes to all, T sends the ticked ones.
    if (/^[1-6]$/.test(k) && Number(k) <= c.q.options.length) {
      ev.preventDefault(); const n = Number(k) - 1; decideTick(st, n, !decideTicked(st, c).has(n)); return;
    }
    if (k === 'a') what = 'all';
    else if (k === 't') what = 'ticked';
  }
  else if (k === 'r' && c.type === 'task' && c.it.recommend?.ask) what = 'recommend';
  else if ((c.type === 'approve' || c.yn) && (k === 'y' || k === 'n')) what = k === 'y' ? 'yes' : 'no';
  else if (decideYesNo(c) && (k === 'y' || k === 'n')) { what = 'pick'; i = k === 'y' ? 0 : 1; }
  else if (c.type === 'task' && !c.yn && k === 'd') what = 'done';
  else if (c.type === 'pick' && /^[1-6]$/.test(k) && Number(k) <= c.q.options.length) { what = 'pick'; i = Number(k) - 1; }
  if (!what) return;
  ev.preventDefault();
  decideAct(st, what, i);
}
// Swipe left skips. A sideways drag that moves the card swallows the click it ends with.
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
      card.style.transition = 'none';
      card.style.transform = `translateX(${Math.min(dx, 40)}px) rotate(${Math.min(dx, 40) / 40}deg)`;
    }
  });
  const end = ev => {
    if (x0 === null || ev.pointerId !== id) return;
    x0 = null;
    card.style.transition = ''; card.style.transform = '';
    if (st.swiped && dx < -90) decideAct(st, 'skip');
    setTimeout(() => { st.swiped = false; }, 0);
  };
  card.addEventListener('pointerup', end);
  card.addEventListener('pointercancel', end);
}
