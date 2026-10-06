/* ui/app/updates.js — Updates page and the unread badge
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- updates
// "an updates section of the app, like a twitter feed with 2 toggles, daily
// and weekly. All bots report in daily, with Fridays being a more comprehensive week review."
// Tico asks the bots one at a time (backend/updates.py). This page is built to be snappy:
// the last feed paints at once from this tab's cache, reads are marked as cards are seen and sent
// in small batches, and a reply shows the moment it is sent. j / k move, r replies, u flips read,
// o opens the bot, ← / → turn a week's slides (desktop).
let UPD = null;
const UPD_CACHE = kind => 'tico.updates.' + kind;
function updCacheRead(kind) {
  try { return JSON.parse(sessionStorage.getItem(UPD_CACHE(kind)) || 'null'); } catch { return null; }
}
function updCacheWrite(kind, data) {
  try { sessionStorage.setItem(UPD_CACHE(kind), JSON.stringify(data)); } catch {}
}
function pageUpdates() {
  const kind = new URLSearchParams(S.route.split('?')[1] || '').get('kind') === 'weekly' ? 'weekly' : 'daily';
  const cached = updCacheRead(kind);
  // "a filter at the top, on by default, to only show my bots" (the bots I own).
  let mine = true;
  try { mine = localStorage.getItem('tico.updates.mine') !== '0'; } catch {}
  const state = UPD = {kind, mine, data: cached, open: new Set(), threads: {}, pending: new Set(), manualUnread: new Set(),
                       sel: -1, loading: !cached, fromCache: !!cached};
  if (cached) updOrder(state, true);
  $('#main').innerHTML = `<div class="upd-page">
    <div class="upd-head">
      <h1>Updates</h1>
      <div class="upd-seg" role="tablist" aria-label="Kind">
        <button type="button" role="tab" data-upd-kind="daily" aria-selected="${kind === 'daily'}" aria-label="Daily" title="Daily"><span class="nav-icon" aria-hidden="true">today</span><span class="upd-lbl">Daily</span></button>
        <button type="button" role="tab" data-upd-kind="weekly" aria-selected="${kind === 'weekly'}" aria-label="Weekly" title="Weekly"><span class="nav-icon" aria-hidden="true">date_range</span><span class="upd-lbl">Weekly</span></button>
      </div>
      <span class="spacer"></span>
      <button type="button" class="upd-chip" id="upd-mine" aria-pressed="${state.mine}" aria-label="My bots" title="My bots"><span class="nav-icon" aria-hidden="true">person</span><span class="upd-lbl">My bots</span></button>
      <button type="button" class="ghost upd-allread" id="upd-allread">Mark all read</button>
    </div>
    <div id="upd-feed" class="upd-feed" aria-live="polite"></div>
  </div>`;
  $('#main').querySelector('.upd-seg').onclick = ev => {
    const b = ev.target.closest('[data-upd-kind]'); if (!b || b.dataset.updKind === state.kind) return;
    location.hash = UPDATES + (b.dataset.updKind === 'weekly' ? '?kind=weekly' : '');
  };
  $('#upd-mine').onclick = () => {
    state.mine = !state.mine;
    $('#upd-mine').setAttribute('aria-pressed', String(state.mine));
    try { localStorage.setItem('tico.updates.mine', state.mine ? '1' : '0'); } catch {}
    state.ordered = false;
    updRender(state);
    void updLoad(state);                          // its own count and order
  };
  $('#upd-allread').onclick = async () => {
    for (const u of state.data?.updates || []) u.read = true;
    updRender(state); updBadge(0);
    try { await post('/v2/updates/read', {all: true}); } catch (e) { toast(e.message || 'Could not mark them read', true); }
  };
  updBind(state);
  updRender(state);
  void updLoad(state);
}
async function updLoad(state, more = false) {
  const q = new URLSearchParams({kind: state.kind, limit: '40'});
  if (state.mine) q.set('mine', 'true');           // the count and the feed agree with My bots
  if (more && state.data?.next_before) q.set('before', state.data.next_before);
  const r = await v2Get('/v2/updates?' + q);
  if (UPD !== state) return;
  state.loading = false;
  if (!r) { if (!state.data) $('#upd-feed').innerHTML = '<div class="empty">Could not load the updates yet; trying again…</div>'; setTimeout(() => UPD === state && updLoad(state, more), 4000); return; }
  if (more && state.data) state.data = {...r, updates: [...state.data.updates, ...r.updates], missed: state.data.missed};
  else { state.data = r; updCacheWrite(state.kind, r); state.fromCache = false; }
  updOrder(state, !more);
  updBadge(r.unread);
  updRender(state);
}
// The visit's order: unread first, then newest. Set on the first answer of a visit (fresh=true);
// later pages ("Load older") append below without moving anything already shown.
function updOrder(state, fresh) {
  const list = state.data?.updates || [];
  if (fresh || !state.order) {
    if (state.order && state.ordered) return;             // keep this visit's order once it is set
    const sorted = [...list].sort((a, b) => (a.read - b.read) || String(b.created).localeCompare(String(a.created)));
    state.order = new Map(sorted.map((u, i) => [u.id, i]));
    state.ordered = !!state.data && !state.fromCache;
    return;
  }
  let next = state.order.size;
  for (const u of list) if (!state.order.has(u.id)) state.order.set(u.id, next++);
}
function updBadge(n) {
  S.updUnread = n;
  for (const el of document.querySelectorAll('[data-upd-badge]')) { el.textContent = n > 99 ? '99+' : String(n || ''); el.hidden = !n; }
}
function updCard(u, i, state) {
  const open = state.open.has(u.id);
  const thread = state.threads[u.id];
  const body = String(u.body || '').trim();
  return `<article class="upd-card${u.read ? '' : ' unread'}${open ? ' open' : ''}${i === state.sel ? ' sel' : ''}" data-upd="${esc(u.id)}" data-i="${i}" tabindex="-1">
    <div class="upd-av">${avatar(u.bot, 36)}</div>
    <div class="upd-main">
      <div class="upd-meta"><a href="#/bot/${encodeURIComponent(u.bot)}" class="upd-name" data-upd-bot>${empName(u.bot)}</a>
        ${u.kind === 'weekly' ? '<span class="upd-pill">Week in review</span>' : ''}
        <span class="muted">· ${esc(ago(u.updated || u.created))}</span>${u.read ? '' : '<span class="upd-dot" aria-label="Unread"></span>'}</div>
      ${u.slides ? updDeck(u, state) : body ? `<div class="upd-body md">${safeMd(body, {shortLinks: true})}</div>` : ''}
      <div class="upd-actions">
        <button type="button" class="upd-act" data-upd-reply><span class="nav-icon" aria-hidden="true">chat_bubble</span>${u.replies ? u.replies : 'Reply'}</button>
        <button type="button" class="upd-act" data-upd-toggle>${u.read ? 'Mark unread' : 'Mark read'}</button>
      </div>
      ${open ? `<div class="upd-thread">${thread ? thread.map(m => `<div class="upd-msg${String(m.from_actor).startsWith('bot:') ? ' bot' : ''}${m.pending ? ' pending' : ''}">
          <b>${esc(actorLabel(m.from_actor))}</b> ${String(m.from_actor).startsWith('bot:') ? `<div class="md">${safeMd(m.body || '', {shortLinks: true})}</div>` : esc(String(m.body || '').replace(/^Re your update "[^"]*": /, ''))}
          <span class="muted">${m.pending ? 'sending…' : esc(ago(m.created))}</span></div>`).join('') : (u.replies ? '<div class="muted">Loading replies…</div>' : '')}
        <form class="upd-reply" data-upd-form><textarea rows="1" placeholder="Reply to ${esc(botDisplayName(u.bot))}…" aria-label="Reply"></textarea>
          <button class="primary" type="submit" aria-label="Send">Send</button></form>
        <div class="muted upd-hint">Goes to ${esc(botDisplayName(u.bot))}'s chat too.</div></div>` : ''}
    </div></article>`;
}
// A week in review is five slides, swiped left to right (scroll-snap), with arrows and dots for a mouse
// and ← / → on the selected card. Older weeks posted as bullets show their bullets.
const UPD_SLIDES = [['goal', 'Goal'], ['kpis', 'KPIs'], ['done', 'Done last week'], ['focus', 'Focus next week'], ['blockers', 'Biggest blockers']];
function updKpiTile(k, tracked) {
  const num = v => v !== '' && v != null && Number.isFinite(Number(v));
  const value = tracked
    ? (k.value != null && k.fresh !== false ? kpiNum(k.value, k.unit) : 'no data')
    : (num(k.value) ? kpiNum(Number(k.value), k.unit || '') : [k.value, k.unit].filter(Boolean).join(' '));
  const series = tracked ? k.spark : k.series;
  return `<li class="upd-kpi">
      <span class="upd-kpi-name">${tracked ? gdot(k.status) : ''}${esc(k.name)}</span>
      <span class="upd-kpi-val tnum">${esc(value)}</span>
      ${(series || []).length > 1 ? kpiSpark(series, tracked ? k.status : 'gray', 132, 28) : ''}
      ${k.target_label ? `<span class="upd-kpi-sub">${esc(k.target_label)}</span>` : ''}
      ${k.note ? `<span class="upd-kpi-sub">${esc(k.note)}</span>` : ''}</li>`;
}
function updSlide(key, s) {
  const list = (items, none) => items?.length ? `<ul>${items.map(b => `<li>${safeMd(b, {shortLinks: true})}</li>`).join('')}</ul>` : `<p class="muted">${none}</p>`;
  if (key === 'goal') return `<p class="upd-goal">${esc(s.goal || '')}</p>`;
  if (key === 'kpis') {
    const tiles = [...(s.tracked || []).map(k => updKpiTile(k, true)), ...(s.kpis || []).map(k => updKpiTile(k, false))];
    return tiles.length ? `<ul class="upd-kpis">${tiles.join('')}</ul>` : '<p class="muted">No KPIs yet.</p>';
  }
  return list(s[key], key === 'blockers' ? 'Nothing blocking.' : 'None.');
}
function updDeck(u, state) {
  const at = state.slide?.[u.id] || 0;
  return `<div class="upd-deck" data-upd-deck>
      <div class="upd-track" tabindex="-1" aria-roledescription="carousel" aria-label="Week in review">
        ${UPD_SLIDES.map(([key, label], i) => `<section class="upd-slide md" aria-roledescription="slide" aria-label="${i + 1} of ${UPD_SLIDES.length}: ${label}">
          <h3 class="upd-slide-h">${label}</h3>${updSlide(key, u.slides)}</section>`).join('')}
      </div>
      <div class="upd-deck-nav">
        <button type="button" class="upd-deck-btn" data-upd-step="-1" aria-label="Previous slide"${at ? '' : ' disabled'}><span class="nav-icon" aria-hidden="true">chevron_left</span></button>
        ${UPD_SLIDES.map(([, label], i) => `<button type="button" class="upd-deck-dot" data-upd-go="${i}" aria-label="${label}" title="${label}"${i === at ? ' aria-current="true"' : ''}></button>`).join('')}
        <button type="button" class="upd-deck-btn" data-upd-step="1" aria-label="Next slide"${at < UPD_SLIDES.length - 1 ? '' : ' disabled'}><span class="nav-icon" aria-hidden="true">chevron_right</span></button>
      </div></div>`;
}
function updDeckGo(deck, i, smooth = true) {
  const track = deck.querySelector('.upd-track');
  i = Math.max(0, Math.min(UPD_SLIDES.length - 1, i));
  track.scrollTo({left: i * track.clientWidth, behavior: smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'auto'});
}
function updDeckMark(deck, state) {
  const track = deck.querySelector('.upd-track');
  const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
  const id = deck.closest('[data-upd]')?.dataset.upd;
  if (id) (state.slide ||= {})[id] = i;
  deck.querySelectorAll('[data-upd-go]').forEach((d, n) => d.toggleAttribute('aria-current', n === i));
  deck.querySelector('[data-upd-step="-1"]').disabled = i === 0;
  deck.querySelector('[data-upd-step="1"]').disabled = i === UPD_SLIDES.length - 1;
}
function updDecksBind(state) {
  for (const deck of document.querySelectorAll('#upd-feed [data-upd-deck]')) {
    const track = deck.querySelector('.upd-track');
    const at = state.slide?.[deck.closest('[data-upd]').dataset.upd];
    if (at) track.scrollLeft = at * track.clientWidth;      // a re-render keeps the slide you were on
    let t = null;
    track.addEventListener('scroll', () => { clearTimeout(t); t = setTimeout(() => updDeckMark(deck, state), 60); }, {passive: true});
  }
}
function updRender(state) {
  const feed = $('#upd-feed'); if (!feed || UPD !== state) return;
  const data = state.data;
  if (!data) { feed.innerHTML = '<div class="upd-skel"></div><div class="upd-skel"></div><div class="upd-skel"></div>'; return; }
  const meId = S.me?.id;
  const isMine = slug => { const e = S.emps.find(x => x.name === slug); return !e || e.operator === meId || (!e.operator && S.me?.role === 'owner'); };
  // Unread first, then newest to oldest; the order is set when the page opens
  // (updOrder) and never moves while you read and scroll, only on the next visit.
  const rank = state.order || new Map();
  const items = (data.updates || []).filter(u => !state.mine || isMine(u.bot))
    .sort((a, b) => (rank.get(a.id) ?? -1) - (rank.get(b.id) ?? -1));
  state.list = items;
  // Just the updates in a feed; no greeting, no progress, no who did not report.
  let html = items.map((u, i) => updCard(u, i, state)).join('');
  if (!items.length) html += '<div class="upd-empty"><span class="nav-icon" aria-hidden="true">dynamic_feed</span><b>No updates yet.</b><span class="muted">The bots report in one at a time each morning.</span></div>';
  if (data.next_before) html += '<button type="button" class="ghost upd-more" data-upd-more>Load older</button>';
  feed.innerHTML = html;
  updDecksBind(state);
  const allRead = $('#upd-allread'); if (allRead) allRead.hidden = !items.some(u => !u.read);   // nothing to mark, no button
  updWatch(state);
}
// Seen for most of a second is read: queued here and sent in one small request.
function updWatch(state) {
  state.io?.disconnect();
  // Seen: 60% of the card on screen, or, for a card taller than that, half the screen is the card
  // (a long card on a phone could never be 60% visible and was never read).
  state.io = new IntersectionObserver(entries => {
    for (const e of entries) {
      const id = e.target.dataset.upd;
      const seen = e.isIntersecting && (e.intersectionRatio >= 0.6 || e.intersectionRect.height >= window.innerHeight * 0.5);
      if (seen && !e.target._t) e.target._t = setTimeout(() => updSeen(state, id), 700);
      else if (!seen) { clearTimeout(e.target._t); e.target._t = null; }
    }
  }, {threshold: [0, 0.25, 0.5, 0.6, 0.75, 1]});
  for (const el of document.querySelectorAll('#upd-feed .upd-card.unread')) state.io.observe(el);
}
function updSeen(state, id) {
  const u = state.data?.updates?.find(x => x.id === id);
  if (!u || u.read || state.manualUnread.has(id) || UPD !== state) return;
  u.read = true; state.pending.add(id);
  const el = document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"]`);
  el?.classList.remove('unread'); el?.querySelector('.upd-dot')?.remove();
  const t = el?.querySelector('[data-upd-toggle]'); if (t) t.textContent = 'Mark unread';
  updBadge(Math.max(0, (S.updUnread || 1) - 1));
  clearTimeout(state.flush);
  state.flush = setTimeout(() => updFlush(state), 900);
}
async function updFlush(state) {
  const ids = [...state.pending]; state.pending.clear();
  if (!ids.length) return;
  try { await post('/v2/updates/read', {ids}); updCacheWrite(state.kind, state.data); } catch { ids.forEach(id => state.pending.add(id)); }
}
async function updOpen(state, id, focusReply) {
  const u = state.data.updates.find(x => x.id === id); if (!u) return;
  if (state.open.has(id) && !focusReply) state.open.delete(id); else state.open.add(id);
  updRender(state);
  if (focusReply) document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"] textarea`)?.focus();
  if (state.open.has(id) && !state.threads[id] && u.replies) {
    const r = await v2Get('/v2/updates/' + encodeURIComponent(id));
    if (UPD !== state || !r) return;
    state.threads[id] = r.thread;
    const typing = document.activeElement?.closest?.('[data-upd-form]') ? document.activeElement.value : null;
    updRender(state);
    if (typing !== null) { const ta = document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"] textarea`); if (ta) { ta.value = typing; ta.focus(); } }
  }
}
async function updReply(state, id, text) {
  const u = state.data.updates.find(x => x.id === id); if (!u || !text.trim()) return;
  const mine = {from_actor: myActor() || 'human:me', body: text, pending: true, created: new Date().toISOString()};
  state.threads[id] = [...(state.threads[id] || []), mine];
  u.replies = (u.replies || 0) + 1; u.read = true;
  updRender(state);
  try {
    const r = await post('/v2/updates/' + encodeURIComponent(id) + '/reply', {text});
    if (UPD !== state) return;
    state.threads[id] = r.thread;
    updRender(state);
    toast(`Sent to ${botDisplayName(u.bot)}`);
  } catch (e) {
    state.threads[id] = (state.threads[id] || []).filter(m => m !== mine); u.replies--;
    updRender(state);
    toast(e.message || 'Could not send the reply', true);
  }
}
async function updToggle(state, id) {
  const u = state.data.updates.find(x => x.id === id); if (!u) return;
  u.read = !u.read;
  if (u.read) state.manualUnread.delete(id);
  else { state.manualUnread.add(id); state.pending.delete(id); }
  updCacheWrite(state.kind, state.data);
  updBadge(Math.max(0, (S.updUnread || 0) + (u.read ? -1 : 1)));
  updRender(state);
  try { await post('/v2/updates/read', {ids: [id], read: u.read}); } catch (e) { toast(e.message || 'Could not save', true); }
}
function updSelect(state, i) {
  const list = state.list || []; if (!list.length) return;
  state.sel = Math.max(0, Math.min(list.length - 1, i));
  for (const el of document.querySelectorAll('#upd-feed .upd-card.sel')) el.classList.remove('sel');
  const el = document.querySelector(`#upd-feed [data-i="${state.sel}"]`);
  if (el) { el.classList.add('sel'); el.scrollIntoView({block: 'nearest'}); el.focus({preventScroll: true}); }
  updSeen(state, list[state.sel].id);
}
function updBind(state) {
  const feed = $('#upd-feed');
  feed.addEventListener('click', ev => {
    const card = ev.target.closest('[data-upd]');
    if (ev.target.closest('[data-upd-more]')) { void updLoad(state, true); return; }
    if (!card || ev.target.closest('[data-upd-bot], a, textarea, form')) return;
    const deck = ev.target.closest('[data-upd-deck]');
    if (deck) {
      const go = ev.target.closest('[data-upd-go], [data-upd-step]');
      if (go) updDeckGo(deck, go.dataset.updGo != null ? Number(go.dataset.updGo) : (state.slide?.[card.dataset.upd] || 0) + Number(go.dataset.updStep));
      return;
    }
    const id = card.dataset.upd;
    if (ev.target.closest('[data-upd-reply]')) return void updOpen(state, id, true);
    if (ev.target.closest('[data-upd-toggle]')) return void updToggle(state, id);
    if (ev.target.closest('.upd-body') && !ev.target.closest('a')) return void updOpen(state, id);
  });
  feed.addEventListener('submit', ev => {
    const form = ev.target.closest('[data-upd-form]'); if (!form) return;
    ev.preventDefault();
    const ta = form.querySelector('textarea'), text = ta.value;
    ta.value = '';
    void updReply(state, form.closest('[data-upd]').dataset.upd, text);
  });
  feed.addEventListener('keydown', ev => {
    const ta = ev.target.closest('[data-upd-form] textarea'); if (!ta) return;
    if (ev.key === 'Enter' && !ev.shiftKey && !matchMedia('(pointer:coarse)').matches) { ev.preventDefault(); ta.form.requestSubmit(); }
    if (ev.key === 'Escape') ta.blur();
  });
  feed.addEventListener('input', ev => {
    const ta = ev.target.closest('[data-upd-form] textarea'); if (!ta) return;
    ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  });
  state.keys = ev => {
    if (UPD !== state || !$('#upd-feed')) return document.removeEventListener('keydown', state.keys);
    if (ev.target.closest('input, textarea, select, [contenteditable]') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const cur = (state.list || [])[state.sel];
    if (ev.key === 'j') { ev.preventDefault(); updSelect(state, state.sel + 1); }
    else if (ev.key === 'k') { ev.preventDefault(); updSelect(state, state.sel - 1); }
    else if (ev.key === 'r' && cur) { ev.preventDefault(); void updOpen(state, cur.id, true); }
    else if (ev.key === 'u' && cur) { ev.preventDefault(); void updToggle(state, cur.id); }
    else if (ev.key === 'o' && cur) { ev.preventDefault(); location.hash = '#/bot/' + encodeURIComponent(cur.bot); }
    else if (ev.key === 'Enter' && cur) { ev.preventDefault(); void updOpen(state, cur.id); }
    else if ((ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && cur?.slides) {
      const deck = document.querySelector(`#upd-feed [data-upd="${CSS.escape(cur.id)}"] [data-upd-deck]`);
      if (deck) { ev.preventDefault(); updDeckGo(deck, (state.slide?.[cur.id] || 0) + (ev.key === 'ArrowRight' ? 1 : -1)); }
    }
  };
  document.addEventListener('keydown', state.keys);
}
// The badge on the rail and the phone's bar, from the 30 s refresh (one small count).
async function updUnreadRefresh() {
  let mine = true;
  try { mine = localStorage.getItem('tico.updates.mine') !== '0'; } catch {}
  const r = await v2Get('/v2/updates/unread' + (mine ? '?mine=true' : ''));
  if (r) { updBadge(r.unread); meetPendingBadge(r.meetings_pending || 0); }
}
// The bot's latest update heads the Updates section of its right rail: its age, the text (long ones fold
// behind a small "More"), and an icon to all of its updates. Once the rail shows it, it counts as read.
async function botLatestUpdate(slug) {
  const host = $('#bot-latest'); if (!host) return;
  const r = await v2Get(`/v2/updates?bot=${encodeURIComponent(slug)}&limit=1`);
  const u = r?.updates?.[0];
  if (!$('#bot-latest') || BOT?.slug !== slug) return;
  if (!u) { host.hidden = true; host.innerHTML = ''; return; }
  const all = isKeeper(slug) ? `<a class="rail-ico" href="#/bot/${encodeURIComponent(slug)}/history" aria-label="All updates" title="All updates"><span class="nav-icon" aria-hidden="true">dynamic_feed</span></a>` : '';
  host.innerHTML = `<header class="rail-head"><h2 class="rail-h">Updates</h2>
      <span class="rail-age" title="${esc(fmt(u.updated || u.created))}">${esc(ago(u.updated || u.created))}</span>${u.read ? '' : '<span class="upd-dot" role="img" aria-label="Unread"></span>'}
      <span class="spacer"></span>${all}</header>
    <div class="upd-body md">${safeMd(u.body || '', {shortLinks: true})}</div>
    <button type="button" class="rail-more" data-latest-more hidden>More</button>`;
  host.hidden = false;
  const body = host.querySelector('.upd-body'), more = host.querySelector('[data-latest-more]');
  more.hidden = body.scrollHeight <= body.clientHeight + 2;
  more.onclick = () => { const open = body.classList.toggle('open'); more.textContent = open ? 'Less' : 'More'; };
  BOT.latest = u.read ? null : u;
  botLatestSeen();
}
// Called when the rail is drawn or comes into view: an unread latest update it shows is now read.
function botLatestSeen() {
  const u = BOT?.latest, host = $('#bot-latest');
  if (!u || !host || host.hidden || !host.offsetParent) return;
  BOT.latest = null;
  updBadge(Math.max(0, (S.updUnread || 1) - 1));
  void post('/v2/updates/read', {ids: [u.id]}).catch(() => {});
}
// Every update the bot posted, newest first; what is shown here is read.
async function botHistoryLoad(slug, before) {
  const host = $('#bot-history'); if (!host) return;
  const q = new URLSearchParams({bot: slug, limit: '30'}); if (before) q.set('before', before);
  const r = await v2Get('/v2/updates?' + q);
  if (!$('#bot-history') || BOT?.slug !== slug) return;
  if (!r) { if (!before) host.innerHTML = '<div class="empty">Could not load the updates.</div>'; return; }
  const items = r.updates || [];
  const html = items.map(u => `<article class="bot-history-item${u.read ? '' : ' unread'}">
      <div class="upd-meta"><b>${esc(new Date(u.day + 'T12:00:00').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</b>
        ${u.kind === 'weekly' ? '<span class="upd-pill">Week in review</span>' : ''}${u.read ? '' : '<span class="upd-dot" aria-label="Unread"></span>'}</div>
      <div class="upd-body md">${safeMd(u.body || '', {shortLinks: true})}</div></article>`).join('');
  const more = r.next_before ? `<button type="button" class="ghost upd-more" data-bot-history-more="${esc(r.next_before)}">Load older</button>` : '';
  if (before) { host.querySelector('[data-bot-history-more]')?.remove(); host.insertAdjacentHTML('beforeend', html + more); }
  else host.innerHTML = (html || '<div class="empty">No updates yet. It reports in each morning.</div>') + more;
  host.onclick = ev => { const b = ev.target.closest('[data-bot-history-more]'); if (b) void botHistoryLoad(slug, b.dataset.botHistoryMore); };
  const unread = items.filter(u => !u.read).map(u => u.id);
  if (unread.length) {
    try { await post('/v2/updates/read', {ids: unread}); updBadge(Math.max(0, (S.updUnread || 0) - unread.length)); } catch {}
  }
}
// A bot's daily and weekly switches, under More.
async function botUpdatesCardLoad(slug) {
  const host = $('#bot-updates'); if (!host) return;
  const r = await v2Get(`/v2/bots/${encodeURIComponent(slug)}/updates`);
  if (!$('#bot-updates') || BOT?.slug !== slug) return;
  if (!r) { host.innerHTML = '<div class="empty">Could not load.</div>'; return; }
  host.innerHTML = ['daily', 'weekly'].map(k => `<label class="upd-switch"><input type="checkbox" data-upd-set="${k}"${r[k] ? ' checked' : ''}>
      <span>${k === 'daily' ? 'Daily update' : 'Friday week in review'}</span></label>`).join('')
    + `<a class="linkish" href="${UPDATES}">See updates</a>`;
  host.onchange = async ev => {
    const box = ev.target.closest('[data-upd-set]'); if (!box) return;
    try { await post(`/v2/bots/${encodeURIComponent(slug)}/updates`, {[box.dataset.updSet]: box.checked}); toast('Saved'); }
    catch (e) { box.checked = !box.checked; toast(e.message || 'Could not save', true); }
  };
}
