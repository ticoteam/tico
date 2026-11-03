/* ui/app/catch-up.js — Catch up: what is new for the viewer, one thread at a time (GET /api/v2/threads/catch-up), each
   with Later (to the end, for this visit), Mute (that thread's notify level) and Mark read (POST read-all). On a phone it
   takes the screen above the tab bar.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// `queue`: the cards still to see, in the server's order; `later`: the ones put off, after them. `done`: read or muted.
const CU = {open: false, dlg: null, queue: [], later: [], done: 0, total: 0, next: null, paging: null, state: 'idle', error: '', seen: new Set()};
const cuCurrent = () => CU.queue[0] || CU.later[0] || null;
const cuLeft = () => CU.queue.length + CU.later.length;
const CU_X = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const CU_ARROW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

function cuDialog() {
  if (CU.dlg?.isConnected) return CU.dlg;
  const dlg = document.createElement('dialog');
  dlg.id = 'catch-up'; dlg.className = 'cu-dialog'; dlg.setAttribute('aria-labelledby', 'cu-h');
  document.body.append(dlg);
  dlg.addEventListener('close', () => { CU.open = false; railRender(); threadsRecount(); });
  dlg.addEventListener('click', ev => void cuClick(ev));
  dlg.addEventListener('submit', ev => { ev.preventDefault(); void cuReply(ev.target); });
  dlg.addEventListener('keydown', ev => {
    if (ev.key !== 'ArrowRight' || ev.target.closest('input, textarea') || !cuCurrent()) return;
    ev.preventDefault(); void cuMarkRead();
  });
  CU.dlg = dlg;
  return dlg;
}
async function catchUpOpen() {
  if (THREADS.rail === false || CU.open) return;
  const dlg = cuDialog();
  Object.assign(CU, {open: true, queue: [], later: [], done: 0, total: 0, next: null, paging: null, state: 'loading', error: '', seen: new Set()});
  cuDraw();
  dlg.showModal();
  railRender();
  try { await cuPage(''); CU.state = 'ready'; }
  catch (e) { CU.state = 'error'; CU.error = e.message; }
  if (CU.open) cuDraw({focus: true});
}
// One page of cards; a thread already shown this visit is not shown again.
async function cuPage(cursor) {
  const r = await get('/v2/threads/catch-up?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
  const items = (r?.items || []).filter(it => it?.thread?.id && !CU.seen.has(it.thread.id));
  for (const it of items) CU.seen.add(it.thread.id);
  CU.queue.push(...items);
  CU.next = r?.next_cursor || null;
  const count = Number(r?.count) || 0;
  CU.total = Math.max(CU.total, CU.done + count, CU.done + cuLeft());
  if (!cursor) { THREADS.catchUp = count; threadsSideRender(); }
}
// Near the end, the next page comes in behind the cards.
function cuMore() {
  if (CU.paging || !CU.next || cuLeft() > 3) return;
  CU.paging = cuPage(CU.next).catch(() => { CU.next = null; }).finally(() => { CU.paging = null; if (CU.open) cuDraw(); });
}
const cuMsgHTML = m => {
  if (m.hidden || m.deleted) return '';
  const who = connectAuthor(m), bot = String(who || '').startsWith('bot:');
  return `<article class="cu-msg" style="--d:${Math.min(Number(m.depth) || 0, 3)}">${actorAvatar(who, 30)}<div class="cu-msg-main">
    <div class="cu-msg-head"><strong>${esc(actorName(who) || 'Someone')}</strong>${bot ? '<span class="cu-bot">BOT</span>' : ''}<time title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>
    <div class="cu-msg-body md">${safeMd(m.body || '', {shortLinks: true})}</div></div></article>`;
};
function cuCardHTML(it) {
  const t = it.thread, n = Number(it.new_count) || 0, more = Number(it.more) || 0, hash = threadOnRail(t);
  const title = threadTitle(t), name = hash ? '#' + title : title, left = cuLeft();
  return `<div class="cu-stack">${left > 2 ? '<span class="cu-back cu-back2"></span>' : ''}${left > 1 ? '<span class="cu-back cu-back1"></span>' : ''}
    <section class="cu-card" aria-labelledby="cu-t" data-thread="${esc(t.id)}">
      <div class="cu-card-head"><h3 id="cu-t">${hash ? threadHashHTML(t) : threadMarkHTML(t, 18)}<span>${esc(title)}</span></h3>
        ${n ? `<span class="cu-n">${n} new</span>` : ''}${it.mention ? '<span class="cu-you">@you</span>' : ''}<span class="spacer"></span>
        <a class="cu-open" href="${esc(threadHref(t))}" data-cu-open>Open</a></div>
      <div class="cu-msgs">${(it.messages || []).map(cuMsgHTML).join('')}${more ? `<a class="cu-more" href="${esc(threadHref(t))}" data-cu-open>${more} more</a>` : ''}</div>
      ${t.me?.can_post === false ? '' : `<form class="cu-reply"><input type="text" name="text" placeholder="Message ${esc(name)}" aria-label="Reply in ${esc(name)}" autocomplete="off" maxlength="20000"></form>`}
      <div class="cu-foot"><button type="button" class="cu-later" data-cu-later>Later</button><button type="button" class="cu-mute" data-cu-mute>Mute</button>
        <span class="spacer"></span><button type="button" class="primary cu-read" data-cu-read>Mark read${CU_ARROW}</button></div>
    </section></div>`;
}
function cuDraw({focus = false} = {}) {
  const dlg = CU.dlg; if (!dlg) return;
  const it = cuCurrent(), total = Math.max(CU.total, CU.done + cuLeft()), at = it ? CU.done + 1 : total;
  const typing = dlg.contains(document.activeElement) && document.activeElement.matches('.cu-reply input');
  let body;
  if (CU.state === 'loading') body = '<div class="cu-card cu-state muted">Loading…</div>';
  else if (CU.state === 'error') body = `<div class="cu-card cu-state err">${esc(CU.error || 'Could not load')}</div>`;
  else if (!it) body = '<div class="cu-card cu-state cu-empty"><p>All caught up</p><button type="button" class="ghost" data-cu-close>Close</button></div>';
  else body = cuCardHTML(it);
  dlg.innerHTML = `<div class="cu">
    <div class="cu-head"><h2 id="cu-h">Catch up</h2>${it ? `<span class="cu-pos">${at} of ${total}</span>` : ''}
      <span class="cu-bar" aria-hidden="true"><i style="width:${total ? Math.round(Math.min(at, total) / total * 100) : 100}%"></i></span>
      <button type="button" class="cu-x" data-cu-close aria-label="Close">${CU_X}</button></div>
    ${body}</div>`;
  if (focus || !typing) (dlg.querySelector('[data-cu-read]') || dlg.querySelector('.cu-empty [data-cu-close]') || dlg.querySelector('.cu-x'))?.focus({preventScroll: true});
}
// Read through (POST read-all) or muted: the card is done and the next one shows.
async function cuMarkRead({mute = false} = {}) {
  const it = cuCurrent(); if (!it) return;
  const id = it.thread.id;
  CU.queue = CU.queue.filter(x => x !== it); CU.later = CU.later.filter(x => x !== it);
  CU.done++;
  THREADS.catchUp = Math.max(0, THREADS.catchUp - 1);
  threadsAllRead(id);
  cuDraw();
  cuMore();
  try {
    if (mute) await put(thvPath(id) + '/follow', {notify: 'muted'});
    await post(thvPath(id) + '/read-all', {});
  } catch (e) { toast(e.message, true); }
}
function cuLater() {
  const it = cuCurrent(); if (!it || cuLeft() < 2) return;
  CU.queue = CU.queue.filter(x => x !== it); CU.later = CU.later.filter(x => x !== it);
  CU.later.push(it);
  cuDraw();
  cuMore();
}
async function cuReply(form) {
  const it = cuCurrent(), input = form.querySelector('input'), text = input?.value.trim();
  if (!it || !text) return;
  input.disabled = true;
  try {
    const r = await post(thvPath(it.thread.id) + '/messages', {text});
    if (r?.held) toast('Your message was held by a rule', true);
    const m = r?.message || (r?.id ? r : null);
    if (m && !r?.held) it.messages = [...(it.messages || []), {...m, depth: 0, sender_id: m.sender_id || myActor()}];
    input.value = '';
    cuDraw();
    CU.dlg.querySelector('.cu-reply input')?.focus();
  } catch (e) { toast(e.message, true); input.disabled = false; }
}
async function cuClick(ev) {
  const t = ev.target;
  if (t.closest('[data-cu-close]')) { CU.dlg.close(); return; }
  if (t.closest('[data-cu-open]')) { ev.preventDefault(); const href = t.closest('[data-cu-open]').getAttribute('href'); CU.dlg.close(); location.hash = href; return; }
  if (t.closest('[data-cu-later]')) { cuLater(); return; }
  if (t.closest('[data-cu-mute]')) { await cuMarkRead({mute: true}); return; }
  if (t.closest('[data-cu-read]')) { await cuMarkRead(); }
}
