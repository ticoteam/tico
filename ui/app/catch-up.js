/* ui/app/catch-up.js — Catch up: what is new for the viewer, one thread at a time (GET /api/v2/threads/catch-up), each
   with Later (to the end, for this visit), Mute (that thread's notify level) and Mark read (POST read-all), a reply box
   whose Send shows once there is text, and Undo for the last Later, Mute or Mark read. On a phone it takes the screen
   above the tab bar.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// `queue`: the cards still to see, in the server's order; `later`: the ones put off, after them. `done`: read or muted.
// `undo`: the last Later, Mute or Mark read, while it can still be taken back. `drafts`: unsent replies, by thread;
// `sending`: the threads whose reply is on its way, kept across visits so a reopened card cannot send it twice.
const CU = {open: false, dlg: null, queue: [], later: [], done: 0, total: 0, next: null, paging: null, state: 'idle', error: '', seen: new Set(), undo: null, drafts: new Map(), sending: new Set()};
const CU_UNDO_MS = 5000;
const cuCurrent = () => CU.queue[0] || CU.later[0] || null;
const cuLeft = () => CU.queue.length + CU.later.length;
const CU_X = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const CU_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 12l16-8-6 16-2.5-6.5z"/></svg>';
const CU_ARROW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

function cuDialog() {
  if (CU.dlg?.isConnected) return CU.dlg;
  const dlg = document.createElement('dialog');
  dlg.id = 'catch-up'; dlg.className = 'cu-dialog'; dlg.setAttribute('aria-labelledby', 'cu-h');
  document.body.append(dlg);
  // Its reads went out one by one and the rail patched each, so closing reads nothing again.
  dlg.addEventListener('close', () => { CU.open = false; cuSettle(); railRender(); });
  dlg.addEventListener('click', cuClick);
  dlg.addEventListener('submit', ev => { ev.preventDefault(); void cuReply(ev.target); });
  dlg.addEventListener('input', ev => {
    const f = ev.target.closest('.cu-reply'), id = ev.target.closest('[data-thread]')?.dataset.thread; if (!f || !id) return;
    f.classList.toggle('has-text', !!ev.target.value.trim()); CU.drafts.set(id, ev.target.value);
  });
  dlg.addEventListener('keydown', ev => {
    if (ev.target.closest('input, textarea')) return;
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'z' && CU.undo) { ev.preventDefault(); cuUndo(); return; }
    if (ev.key !== 'ArrowRight' || !cuCurrent()) return;
    ev.preventDefault(); cuMarkRead();
  });
  CU.dlg = dlg;
  return dlg;
}
async function catchUpOpen() {
  if (THREADS.rail === false || CU.open) return;
  const dlg = cuDialog();
  Object.assign(CU, {open: true, queue: [], later: [], done: 0, total: 0, next: null, paging: null, state: 'loading', error: '', seen: new Set(), undo: null, drafts: new Map()});
  cuDraw();
  dlg.showModal();
  railRender();
  try { await cuPage(''); CU.state = 'ready'; }
  catch (e) { CU.state = 'error'; CU.error = e.message; }
  if (CU.open) cuDraw({focus: true});
}
// One page of cards; a thread already shown this visit is not shown again.
async function cuPage(cursor) {
  // A read or mute held for Undo when the page was asked for is done here but was still unread on the server, so
  // `count` has it too.
  const held = CU.undo && CU.undo.kind !== 'later' ? 1 : 0;
  const r = await get('/v2/threads/catch-up?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
  const items = (r?.items || []).filter(it => it?.thread?.id && !CU.seen.has(it.thread.id));
  for (const it of items) CU.seen.add(it.thread.id);
  CU.queue.push(...items);
  CU.next = r?.next_cursor || null;
  const count = Number(r?.count) || 0;
  CU.total = Math.max(CU.total, CU.done - held + count, CU.done + cuLeft());
  if (!cursor && !THREADS.cu) { THREADS.catchUp = count; threadsSideRender(); }
}
// Near the end, the next page comes in behind the cards.
function cuMore() {
  if (CU.paging || !CU.next || cuLeft() > 3) return;
  CU.paging = cuPage(CU.next).catch(() => { CU.next = null; }).finally(() => { CU.paging = null; if (CU.open) cuDraw(); });
}
const cuMsgHTML = m => {
  if (m.hidden || m.deleted) return '';
  const who = m.sender_id, bot = String(who || '').startsWith('bot:');
  return `<article class="cu-msg" style="--d:${Math.min(Number(m.depth) || 0, 3)}">${actorAvatar(who, 30)}<div class="cu-msg-main">
    <div class="cu-msg-head"><strong>${esc(actorName(who) || 'Someone')}</strong>${bot ? '<span class="cu-bot">BOT</span>' : ''}<time title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>
    <div class="cu-msg-body md">${safeMd(m.body || '', {shortLinks: true})}${m.clipped ? '<span class="muted">…</span>' : ''}</div></div></article>`;
};
function cuCardHTML(it) {
  const t = it.thread, n = Number(it.new_count) || 0, more = Number(it.more) || 0, hash = threadOnRail(t);
  const title = threadTitle(t, it.messages), name = hash ? '#' + title : title, left = cuLeft(), draft = CU.drafts.get(t.id) || '';
  const busy = CU.sending.has(t.id) ? ' disabled' : '';
  return `<div class="cu-stack">${left > 2 ? '<span class="cu-back cu-back2"></span>' : ''}${left > 1 ? '<span class="cu-back cu-back1"></span>' : ''}
    <section class="cu-card" aria-labelledby="cu-t" data-thread="${esc(t.id)}">
      <div class="cu-card-head"><h3 id="cu-t">${hash ? threadHashHTML(t) : threadMarkHTML(t, 18)}<span>${esc(title)}</span></h3>
        ${n ? `<span class="cu-n">${n} new</span>` : ''}${it.mention ? '<span class="cu-you">@you</span>' : ''}<span class="spacer"></span>
        <a class="cu-open" href="${esc(threadHref(t))}" data-cu-open>Open</a></div>
      <div class="cu-msgs">${(it.messages || []).map(cuMsgHTML).join('')}${more ? `<a class="cu-more" href="${esc(threadHref(t))}" data-cu-open>${more} more</a>` : ''}</div>
      ${t.me?.can_post === false ? '' : `<form class="cu-reply${draft.trim() ? ' has-text' : ''}"><input type="text" name="text" value="${esc(draft)}" placeholder="Message ${esc(name)}" aria-label="Reply in ${esc(name)}" autocomplete="off" maxlength="20000"${busy}><button type="submit" class="primary cu-send" aria-label="Send"${busy}>${CU_SEND}</button></form>`}
      <div class="cu-foot"><button type="button" class="cu-later" data-cu-later>Later</button><button type="button" class="cu-mute" data-cu-mute>Mute</button>
        <span class="spacer"></span><button type="button" class="primary cu-read" data-cu-read>Mark read${CU_ARROW}</button></div>
    </section></div>`;
}
const cuUndoHTML = () => {
  const u = CU.undo; if (!u) return '';
  return `<div class="cu-undo" role="status"><div class="cu-undo-pill"><span>${u.kind === 'later' ? 'Moved to later' : u.kind === 'mute' ? 'Muted' : 'Marked read'}</span><button type="button" data-cu-undo>Undo</button></div></div>`;
};
function cuDraw({focus = false} = {}) {
  const dlg = CU.dlg; if (!dlg) return;
  const it = cuCurrent(), total = Math.max(CU.total, CU.done + cuLeft()), at = it ? CU.done + 1 : total;
  const typing = dlg.contains(document.activeElement) && document.activeElement.matches('.cu-reply input');
  const sel = typing ? [document.activeElement.selectionStart, document.activeElement.selectionEnd, document.activeElement.selectionDirection] : null;
  let body;
  if (CU.state === 'loading') body = '<div class="cu-card cu-state muted">Loading…</div>';
  else if (CU.state === 'error') body = `<div class="cu-card cu-state err">${esc(CU.error || 'Could not load')}</div>`;
  else if (!it) body = `<div class="cu-card cu-state cu-empty"><p>All caught up</p><button type="button" class="ghost" data-cu-close>Close</button></div>`;
  else body = cuCardHTML(it);
  dlg.innerHTML = `<div class="cu">
    <div class="cu-head"><h2 id="cu-h">Catch up</h2>${it ? `<span class="cu-pos">${at} of ${total}</span>` : ''}${cuUndoHTML()}
      <span class="cu-bar" aria-hidden="true"><i style="width:${total ? Math.round(Math.min(at, total) / total * 100) : 100}%"></i></span>
      <button type="button" class="cu-x" data-cu-close aria-label="Close">${CU_X}</button></div>
    ${body}</div>`;
  // A redraw while typing (the next page coming in) keeps the caret in the reply box.
  const input = dlg.querySelector('.cu-reply input');
  if (typing && !focus && input && !input.disabled) { input.focus({preventScroll: true}); input.setSelectionRange(...sel); return; }
  if (focus || !typing) (dlg.querySelector('[data-cu-read]') || dlg.querySelector('.cu-empty [data-cu-close]') || dlg.querySelector('.cu-x'))?.focus({preventScroll: true});
}
// Read through or muted: the card is done and the next one shows. Read marks only move forward on the server, so the
// read (POST read-all) and the mute go out once Undo is gone: after CU_UNDO_MS, on the next card action, or on close.
function cuMarkRead({mute = false} = {}) {
  const it = cuCurrent(); if (!it) return;
  cuSettle();
  const from = CU.queue[0] === it ? 'queue' : 'later';
  CU.queue = CU.queue.filter(x => x !== it); CU.later = CU.later.filter(x => x !== it);
  CU.done++;
  cuHold(mute ? 'mute' : 'read', it, from);
  cuDraw();
  cuMore();
}
function cuLater() {
  const it = cuCurrent(); if (!it || cuLeft() < 2) return;
  cuSettle();
  const from = CU.queue[0] === it ? 'queue' : 'later';
  CU.queue = CU.queue.filter(x => x !== it); CU.later = CU.later.filter(x => x !== it);
  CU.later.push(it);
  cuHold('later', it, from);
  cuDraw();
  cuMore();
}
// Undo shows for CU_UNDO_MS in the head, in the progress bar's place; when the time is up it goes without a redraw, so a
// reply being typed stays.
function cuHold(kind, it, from) {
  CU.undo = {kind, it, from, at: performance.now(), timer: setTimeout(cuSettle, CU_UNDO_MS)};
}
// The held action can no longer be taken back: a read or mute goes to the server now. The read covers what was there
// at the click (`ago_ms`, measured as it is sent), so a message that came while Undo showed stays unread. Both go at
// once with keepalive, so leaving or reloading the page right after does not lose them.
function cuSettle({leaving = false} = {}) {
  const u = CU.undo; if (!u) return;
  CU.undo = null; clearTimeout(u.timer);
  CU.dlg?.querySelector('.cu-undo')?.remove();
  if (u.kind === 'later') return;
  const id = u.it.thread.id, opts = {keepalive: true};
  const writes = [writeRequest('POST', thvPath(id) + '/read-all', {ago_ms: Math.min(600000, Math.max(0, Math.round(performance.now() - u.at)))}, undefined, opts)];
  if (u.kind === 'mute') writes.push(writeRequest('PUT', thvPath(id) + '/follow', {notify: 'muted'}, undefined, opts));
  if (leaving) { for (const w of writes) w.catch(() => {}); return; }
  if (!THREADS.cu) THREADS.catchUp = Math.max(0, THREADS.catchUp - 1);
  threadsAllRead(id);
  Promise.all(writes).catch(e => toast(e.message, true));
}
window.addEventListener('pagehide', () => cuSettle({leaving: true}));
// The card comes back where it was, and nothing was sent.
function cuUndo() {
  const u = CU.undo; if (!u) return;
  CU.undo = null; clearTimeout(u.timer);
  CU.queue = CU.queue.filter(x => x !== u.it); CU.later = CU.later.filter(x => x !== u.it);
  if (u.kind !== 'later') CU.done--;
  (u.from === 'queue' ? CU.queue : CU.later).unshift(u.it);
  cuDraw({focus: true});
}
// A thread read somewhere else (the viewer's own `read` event): its card goes, counted as done. While open, Catch up
// reads nothing again on events; it pages near the end and reads afresh when opened again.
function catchUpGone(id) {
  if (!CU.open) return;
  if (CU.undo?.it.thread.id === id) cuSettle();
  const it = [...CU.queue, ...CU.later].find(x => x.thread?.id === id);
  if (!it) return;
  CU.queue = CU.queue.filter(x => x !== it); CU.later = CU.later.filter(x => x !== it);
  CU.done++;
  cuDraw();
  cuMore();
}
async function cuReply(form) {
  const it = cuCurrent(), input = form.querySelector('input'), raw = input?.value || '', text = raw.trim();
  if (!it || !text) return;
  const id = it.thread.id;
  if (CU.sending.has(id)) return;
  CU.sending.add(id);
  input.disabled = true; form.querySelector('.cu-send')?.setAttribute('disabled', '');
  let sent = false;
  try {
    const r = await post(thvPath(id) + '/messages', {text});
    if (r?.held) toast('Your message was held by a rule', true);
    const m = r?.message || (r?.id ? r : null);
    if (m && !r?.held) it.messages = [...(it.messages || []), {...m, depth: 0, sender_id: m.sender_id || myActor()}];
    // Only the words that went: a draft changed since (a redraw kept it) stays.
    if ((CU.drafts.get(id) ?? raw) === raw) CU.drafts.delete(id);
    sent = true;
  } catch (e) { toast(e.message, true); }
  CU.sending.delete(id);
  if (!CU.open) return;
  cuDraw();
  if (cuCurrent()?.thread.id !== id) return;
  if (sent) { const msgs = CU.dlg.querySelector('.cu-msgs'); if (msgs) msgs.scrollTop = msgs.scrollHeight; }
  CU.dlg.querySelector('.cu-reply input')?.focus();
}
function cuClick(ev) {
  const t = ev.target;
  if (t.closest('[data-cu-close]')) { CU.dlg.close(); return; }
  if (t.closest('[data-cu-open]')) { ev.preventDefault(); const href = t.closest('[data-cu-open]').getAttribute('href'); CU.dlg.close(); location.hash = href; return; }
  if (t.closest('[data-cu-undo]')) { cuUndo(); return; }
  if (t.closest('[data-cu-later]')) { cuLater(); return; }
  if (t.closest('[data-cu-mute]')) { cuMarkRead({mute: true}); return; }
  if (t.closest('[data-cu-read]')) cuMarkRead();
}
