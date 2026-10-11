/* ui/app/decide.js — Decide: everything on the person's plate as one queue of cards, answered one at a time with two
   big buttons. Questions bots asked first (a pick between options, images inline), then approvals, then the rest of
   their tasks (waiting on them, then theirs). An answer, Done or a comment hands the task back to the bot; it is sent
   when its toast runs out or the next action lands, so Undo never needs the server.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let DEC = null;                 // the open Decide page's state, null elsewhere
const DEC_UNDO_MS = 4000;

// ---- which cards, in which order (GET /v2/needs-you items)
// A question answered in the card: one pick-one question with 2 to 6 options, put to this person by someone else.
// Several questions or a multi-select is a task card that links to the task.
function decideQuestion(it) {
  const ask = it.ask, qs = ask?.refs?.questions;
  if (!ask || !Array.isArray(qs) || qs.length !== 1) return null;
  const q = qs[0], n = (q.options || []).length;
  if (q.multi || n < 2 || n > 6 || ask.to_actor !== myActor() || ask.from_actor === myActor()) return null;
  return q;
}
function decideCards(items) {
  const me = myActor(), asks = [], approvals = [], waiting = [], mine = [];
  for (const it of items || []) {
    if (it.kind === 'approval') { approvals.push({key: 'approval:' + it.id, type: 'approve', it, at: it.created}); continue; }
    if (!['task', 'question', 'waiting'].includes(it.kind)) continue;
    const q = it.kind === 'question' ? decideQuestion(it) : null;
    if (q) asks.push({key: 'task:' + it.id, type: 'pick', it, q, at: it.ask.created || it.created});
    else (it.owner === me && it.kind === 'task' && it.waiting_on !== me ? mine : waiting)
      .push({key: 'task:' + it.id, type: 'task', it, at: it.created});
  }
  const old = (a, b) => String(a.at || '').localeCompare(String(b.at || ''));
  return [...asks.sort(old), ...approvals.sort(old), ...waiting.sort(old), ...mine.sort(old)];
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
    <p class="dc-keys" aria-hidden="true">1–6 pick · Y / N approve · D done · S skip · C comment</p>
    <div class="dc-toast" role="status" hidden><span></span><button type="button" data-dc="undo">Undo</button></div>
  </section>`;
  const st = DEC = {queue: [], cards: new Map(), handled: new Set(), pending: null, data: new Map(), off: []};
  const root = $('.decide', main);
  root.addEventListener('click', ev => decideClick(st, ev));
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
  if (typeof liveOn === 'function') st.off.push(liveOn('needs', d => { if (DEC === st) decideRefresh(st, d.items || []); }));
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
const decideCurrent = st => st.cards.get(st.queue[0]);
const decideDecision = c => c.type !== 'task';
function decidePaintHead(st) {
  const root = $('.decide');
  if (!root) return;
  const n = st.queue.length, d = st.queue.filter(k => decideDecision(st.cards.get(k))).length, c = decideCurrent(st);
  $('.dc-count', root).innerHTML = n ? `<span class="tnum">${n} left</span>${d ? `<small class="tnum">${d} decision${d === 1 ? '' : 's'}</small>` : ''}` : '';
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
  const decide = decideDecision(c), needsNote = decideNeedsNote(c);
  stage.innerHTML = `<article class="dc-card${enter ? ' in' : ''}" data-key="${esc(c.key)}">
    <div class="dc-who">${actorFace(from, 32)}
      <div class="dc-who-text"><div class="dc-who-name">${esc(actorLabel(from) || 'Tico')}</div>
        <div class="dc-who-sub">${esc(decide ? plainActors(c.it.title || '') : decideMeta(c.it))}</div></div>
      <span class="pill ${decide ? 'needs' : ''}">${decide ? 'Decide' : 'Task'}</span></div>
    <div class="dc-body"></div>
    <div class="dc-note"><textarea rows="1" maxlength="4000" aria-label="${decide ? 'Note' : 'Comment'}" placeholder="${needsNote ? 'What did you decide?' : decide ? 'Add a note' : 'Add a comment'}"></textarea>
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
  const html = c.type === 'pick' ? decidePickHTML(st, c) : c.type === 'approve' ? decideApprovalHTML(c) : decideTaskHTML(st, c);
  if (body.dataset.html !== html) body.innerHTML = body.dataset.html = html;
}
const decideTwo = c => c.type === 'pick' && c.q.options.length === 2;
// Yes / No reads No, Yes like an approval: the affirmative on the right.
const decideYesNo = c => decideTwo(c) && c.q.options.map(o => String(o.label).toLowerCase()).join() === 'yes,no';
function decideOrder(c) {
  return decideYesNo(c) ? [1, 0] : [0, 1];
}
function decideActionsHTML(c) {
  const k = key => `<kbd>${key}</kbd>`;
  if (decideTwo(c)) return decideOrder(c).map((i, n) => {
    const o = c.q.options[i];
    const yn = decideYesNo(c) ? String(o.label)[0].toUpperCase() : i + 1;
    return `<button type="button" class="${n && String(o.label).toLowerCase() === 'yes' ? 'primary' : ''}" data-dc="pick" data-i="${i}">${esc(o.label)}${k(yn)}</button>`;
  }).join('');
  if (c.type === 'pick') return `<button type="button" data-dc="skip">Skip${k('S')}</button><button type="button" disabled>Pick one</button>`;
  if (c.type === 'approve') return `<button type="button" data-dc="no">No${k('N')}</button><button type="button" class="primary" data-dc="yes">Yes${k('Y')}</button>`;
  return `<button type="button" data-dc="skip">Skip${k('S')}</button><button type="button" class="primary" data-dc="done">Done${k('D')}</button>`;
}
// An option's picture, large: the image itself when small enough, else its thumb.
function decidePicture(st, c, o) {
  const [fid, n] = String(o.file || '').split('@');
  const f = fid && (st.data.get(c.it.id)?.files || []).find(x => x.id === fid);
  if (!f) return fid ? '<span class="dc-pic tf-skel"></span>' : '';
  const v = tfVersion(f, n), kind = tfKind(f.name, v.mime || f.mime);
  const src = kind === 'image' && Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : tfPicture(v, kind);
  return src ? `<span class="dc-pic tf-skel"><img alt="" data-tf-src="${esc(src)}"></span>`
    : `<span class="dc-pic dc-glyph">${esc(tfGlyph(kind, f.name))}</span>`;
}
function decidePickHTML(st, c) {
  const it = c.it, q = c.q, line = (it.first_line || '').trim();
  const context = line && ![q.question, it.title].includes(line) ? `<p class="dc-ctx">${esc(plainActors(line))}</p>` : '';
  const pics = q.options.some(o => o.file);
  const desc = o => o.description ? `<span class="dc-desc">${esc(o.description)}</span>` : '';
  let opts = '';
  if (q.options.length === 2 && pics) opts = `<div class="dc-pair">${q.options.map((o, i) =>
    `<button type="button" class="dc-opt" data-dc="pick" data-i="${i}" aria-label="${esc(o.label)}">${decidePicture(st, c, o)}<span class="dc-l">${esc(o.label)}</span>${desc(o)}</button>`).join('')}</div>`;
  else if (q.options.length === 2) opts = q.options.some(o => o.description)
    ? `<ul class="dc-descs">${q.options.map(o => `<li><b>${esc(o.label)}</b>${desc(o)}</li>`).join('')}</ul>` : '';
  else opts = `<div class="dc-tiles">${q.options.map((o, i) =>
    `<button type="button" class="dc-tile${o.file ? ' pic' : ''}" data-dc="pick" data-i="${i}">${o.file ? decidePicture(st, c, o) : ''}<span class="dc-tl"><b class="tnum">${i + 1}</b><span class="dc-l">${esc(o.label)}</span></span>${desc(o)}</button>`).join('')}</div>`;
  return `<p class="dc-q">${esc(q.question)}</p>${context}${opts}`;
}
const DEC_APPROVAL_Q = {send: 'Send this email?', spend: 'Approve this spend?', publish: 'Publish this?', merge: 'Merge this?'};
function decideApprovalHTML(c) {
  return `<p class="dc-q">${esc(DEC_APPROVAL_Q[c.it.what] || 'Approve this?')}</p>${approvalPayloadNode(c.it).outerHTML}`;
}
function decideMeta(it) {
  const me = myActor();
  if (it.waiting_on === me || it.kind === 'waiting') return `Waiting on you · ${ageShort(it.updated || it.created)}`;
  if (it.kind === 'question') return `Question for you · ${ageShort(it.ask?.created || it.created)}`;
  return `Assigned to you · ${ageShort(it.created)}`;
}
function decideTaskHTML(st, c) {
  const it = c.it, data = st.data.get(it.id), me = myActor();
  const said = (data?.comments || []).filter(m => m.from_actor !== me && String(m.body || '').trim()).at(-1);
  // A body that opens with the title does not say it twice.
  const body = String(taskBody(it) || '').trim(), title = String(it.title || '').trim();
  const text = said?.body || it.note || (body.startsWith(title) ? body.slice(title.length).trim() : body);
  const files = (data?.files || []).filter(f => !f.archived && f.versions.length);
  const shown = files.map(f => {
    const v = tfVersion(f), kind = tfKind(f.name, v.mime || f.mime);
    const src = kind === 'image' ? (Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : tfPicture(v, kind)) : null;
    return src ? `<a class="dc-img tf-skel" href="${esc(v.url)}" target="_blank" rel="noopener" aria-label="${esc(f.name)}"><img alt="" data-tf-src="${esc(src)}"></a>`
      : `<a class="dc-file" href="${esc(v.url)}" target="_blank" rel="noopener"><span class="tc-kind">${esc(tfGlyph(kind, f.name))}</span><span class="dc-fname">${esc(f.name)}</span><span class="muted tnum">v${v.n}</span></a>`;
  }).join('');
  return `<p class="dc-q dc-title">${esc(plainActors(it.title || ''))}</p>
    ${text ? `<div class="dc-msg md">${safeMd(plainActors(text))}</div>` : ''}${shown ? `<div class="dc-files">${shown}</div>` : ''}`;
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
// Finishing a bot's request on a person's own task says what was decided (the server refuses Done without it).
const decideNeedsNote = c => c.type === 'task' && String(c.it.owner || '').startsWith('human:') && String(c.it.requester || '').startsWith('bot:');
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
  if (what === 'pick') {
    const ask = c.it.ask, q = c.q, o = q.options[i], t = ask.refs?.target;
    const target = t?.file ? {file: t.file, version: Number(t.version)} : {comment: ask.id};
    const other = note && q.other !== false;
    const writes = [{path: `/v2/tasks/${id}/answers`, body: {target, answers: {[q.id]: [o.label]}, ...(other ? {other: note} : {})}}];
    if (note && !other) writes.push({path: `/v2/tasks/${id}/comments`, body: {text: note}});
    return {writes, label: `Picked ${o.label} · sent to ${to}`};
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
  if (what === 'done' && !note && decideNeedsNote(c)) {
    box?.focus();
    toast(`Say what you decided for ${decideBot(c)}`, true);
    return;
  }
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
function decideSave() {
  try { localStorage.setItem(DEC_SAVED, JSON.stringify([...DEC_OUTBOX].map(p => ({key: p.key, writes: p.write.writes.slice(p.sent || 0)})))); } catch {}
}
function decideResume() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(DEC_SAVED) || '[]'); } catch {}
  for (const item of Array.isArray(saved) ? saved : []) {
    if (!item?.writes?.length || [...DEC_OUTBOX].some(p => p.key === item.key)) continue;
    const p = {key: item.key, write: {writes: item.writes}, sent: 0};
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
  if (what === 'undo') { const undo = st.undo; st.undo = null; $('.decide .dc-toast').hidden = true; undo?.(); return; }
  decideAct(st, what, Number(b.dataset.i));
}
function decideKey(st, ev) {
  if (DEC !== st || ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey || document.querySelector('dialog[open]')) return;
  if (ev.target.matches?.('textarea, input, select, [contenteditable]')) {
    if (ev.key === 'Escape') { ev.preventDefault(); ev.target.blur(); }
    return;
  }
  const c = decideCurrent(st), k = ev.key.toLowerCase();
  if (!c) return;
  let what = null, i;
  if (k === 's') what = 'skip';
  else if (k === 'c') { ev.preventDefault(); $('.decide .dc-note textarea')?.focus(); return; }
  else if (c.type === 'approve' && (k === 'y' || k === 'n')) what = k === 'y' ? 'yes' : 'no';
  else if (decideYesNo(c) && (k === 'y' || k === 'n')) { what = 'pick'; i = k === 'y' ? 0 : 1; }
  else if (c.type === 'task' && k === 'd') what = 'done';
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
