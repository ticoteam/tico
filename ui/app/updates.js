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
// "Update cards nested by org chart": by default the page is one day's updates as the org chart, your card
// on top and an icon on each card opening the cards of its reports; Below me or Whole org; only direct
// reports' updates alert (backend/updates.py tree). List keeps the plain newest-first feed.
let UPD = null;
const UPD_DRAFTS = new Map();
const UPD_CACHE = state => updIsTree(state) ? `tico.updates.tree.${state.kind}.${state.scope || 'auto'}`
  : `tico.updates.${state.kind}.${state.view}.${state.mine ? 'mine' : 'all'}`;
const updIsTree = state => state.view !== 'archive' && state.layout !== 'list';
function updPrefs() {
  const out = {mine: true, layout: 'tree', scope: null};
  try {
    out.mine = localStorage.getItem('tico.updates.mine') !== '0';
    out.layout = localStorage.getItem('tico.updates.layout') === 'list' ? 'list' : 'tree';
    out.scope = ['below', 'all'].includes(localStorage.getItem('tico.updates.scope')) ? localStorage.getItem('tico.updates.scope') : null;
  } catch {}
  return out;
}
function updCacheRead(state) {
  try { return JSON.parse(sessionStorage.getItem(UPD_CACHE(state)) || 'null'); } catch { return null; }
}
function updCacheWrite(state, data) {
  try { sessionStorage.setItem(UPD_CACHE(state), JSON.stringify(data)); } catch {}
}
function updCacheClear() {
  try { for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const key = sessionStorage.key(i); if (key?.startsWith('tico.updates.')) sessionStorage.removeItem(key);
  } } catch {}
}
function updSaveDrafts() {
  for (const form of document.querySelectorAll('#upd-feed [data-upd-form]')) {
    const id = form.closest('[data-upd]')?.dataset.upd, text = form.querySelector('textarea')?.value;
    if (id) { if (text) UPD_DRAFTS.set(id, text); else UPD_DRAFTS.delete(id); }
  }
}
function pageUpdates() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const kind = params.get('kind') === 'weekly' ? 'weekly' : 'daily';
  const view = params.get('view') === 'archive' ? 'archive' : 'inbox';
  // "a filter at the top, on by default, to only show my bots" (the bots I own), in List and Archive.
  const {mine, layout, scope} = updPrefs();
  const state = UPD = {kind, view, mine, layout, scope, expanded: null, editing: false, data: null, open: new Set(), threads: {}, pending: new Set(), manualUnread: new Set(),
                       archivePending: new Set(), linked: new Set(), loadSeq: 0, mutationSeq: 0, sel: -1, loading: true, fromCache: false};
  const cached = state.data = updCacheRead(state);
  state.loading = !cached; state.fromCache = !!cached;
  if (cached) updOrder(state, true);
  $('#main').innerHTML = `<div class="upd-page">
    <div class="upd-head">
      <h1>${view === 'archive' ? 'Archive' : 'Updates'}</h1>
      <div class="upd-seg" role="tablist" aria-label="Kind">
        <button type="button" role="tab" data-upd-kind="daily" aria-selected="${kind === 'daily'}" aria-label="Daily" title="Daily"><span class="nav-icon" aria-hidden="true">today</span><span class="upd-lbl">Daily</span></button>
        <button type="button" role="tab" data-upd-kind="weekly" aria-selected="${kind === 'weekly'}" aria-label="Weekly" title="Weekly"><span class="nav-icon" aria-hidden="true">date_range</span><span class="upd-lbl">Weekly</span></button>
      </div>
      <span class="spacer"></span>
      <button type="button" class="upd-chip" id="upd-scope" aria-pressed="${state.scope === 'all'}" aria-label="Whole org" title="Whole org: off shows only the cards below you"${updIsTree(state) ? '' : ' hidden'}><span class="nav-icon" aria-hidden="true">groups</span><span class="upd-lbl">Whole org</span></button>
      <button type="button" class="upd-chip" id="upd-mine" aria-pressed="${state.mine}" aria-label="My bots" title="My bots"${updIsTree(state) ? ' hidden' : ''}><span class="nav-icon" aria-hidden="true">person</span><span class="upd-lbl">My bots</span></button>
      <button type="button" class="upd-chip" id="upd-layout" aria-pressed="${state.layout === 'list'}" aria-label="List" title="List: every update, newest first"${view === 'archive' ? ' hidden' : ''}><span class="nav-icon" aria-hidden="true">view_agenda</span><span class="upd-lbl">List</span></button>
      <button type="button" class="upd-chip upd-view" id="upd-view" aria-label="${view === 'archive' ? 'Open inbox' : 'Open archive'}" title="${view === 'archive' ? 'Inbox' : 'Archive'}"><span class="nav-icon" aria-hidden="true">${view === 'archive' ? 'inbox' : 'archive'}</span><span class="upd-lbl">${view === 'archive' ? 'Inbox' : 'Archive'}</span></button>
      <button type="button" class="ghost upd-allread" id="upd-allread"${view === 'archive' ? ' hidden' : ''}>Mark all read</button>
    </div>
    <div id="upd-feed" class="upd-feed" aria-live="polite"></div>
  </div>`;
  $('#main').querySelector('.upd-seg').onclick = ev => {
    const b = ev.target.closest('[data-upd-kind]'); if (!b || b.dataset.updKind === state.kind) return;
    const q = new URLSearchParams(); if (b.dataset.updKind === 'weekly') q.set('kind', 'weekly');
    if (state.view === 'archive') q.set('view', 'archive');
    location.hash = UPDATES + (q.toString() ? '?' + q : '');
  };
  $('#upd-view').onclick = () => {
    const q = new URLSearchParams(); if (state.kind === 'weekly') q.set('kind', 'weekly');
    if (state.view !== 'archive') q.set('view', 'archive');
    location.hash = UPDATES + (q.toString() ? '?' + q : '');
  };
  $('#upd-mine').onclick = () => {
    state.mine = !state.mine;
    $('#upd-mine').setAttribute('aria-pressed', String(state.mine));
    try { localStorage.setItem('tico.updates.mine', state.mine ? '1' : '0'); } catch {}
    // Same kind and view, so the cards on screen stay up (refiltered) until this scope's own load lands.
    state.data = updCacheRead(state) || state.data; state.fromCache = !!state.data; state.loading = !state.data;
    state.order = null; state.ordered = false;
    updRender(state);
    void updLoad(state);                          // its own count and order
  };
  $('#upd-layout').onclick = () => {
    try { localStorage.setItem('tico.updates.layout', state.layout === 'list' ? 'tree' : 'list'); } catch {}
    pageUpdates();
  };
  $('#upd-scope').onclick = () => {
    try { localStorage.setItem('tico.updates.scope', state.scope === 'all' ? 'below' : 'all'); } catch {}
    pageUpdates();
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
function updFeedPath(state, more = false) {
  if (updIsTree(state)) {
    const q = new URLSearchParams({kind: state.kind});
    if (state.scope) q.set('scope', state.scope);
    return '/v2/updates/tree?' + q;
  }
  const q = new URLSearchParams({kind: state.kind, limit: '40'});
  if (state.mine) q.set('mine', 'true');           // the count and the feed agree with My bots
  if (state.view === 'archive') q.set('archive', 'true');
  if (more && state.data?.next_before) q.set('before', state.data.next_before);
  return '/v2/updates?' + q;
}
// Startup asks for the feed beside the roster when Updates is the page it opens on, a round trip sooner (boot.js).
function updPrefetch() {
  if (!(location.hash === UPDATES || location.hash.startsWith(UPDATES + '?'))) return;
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  getPrefetch(updFeedPath({...updPrefs(), kind: params.get('kind') === 'weekly' ? 'weekly' : 'daily', view: params.get('view') === 'archive' ? 'archive' : 'inbox'}));
}
async function updLoad(state, more = false) {
  const request = ++state.loadSeq;
  const mutation = state.mutationSeq;
  const r = await v2Get(updFeedPath(state, more));
  if (UPD !== state || request !== state.loadSeq || mutation !== state.mutationSeq) return;
  state.loading = false;
  if (!r) { if (!state.data) $('#upd-feed').innerHTML = '<div class="empty">Could not load the updates yet; trying again…</div>'; setTimeout(() => UPD === state && updLoad(state, more), 4000); return; }
  if (r.roots) { state.data = updFromTree(r); updCacheWrite(state, state.data); state.fromCache = false; }
  else if (more && state.data) {
    const have = new Set(state.data.updates.map(u => u.id));   // a From: link may have loaded one already
    state.data = {...r, updates: [...state.data.updates, ...r.updates.filter(u => !have.has(u.id))], missed: state.data.missed};
  }
  else { state.data = r; updCacheWrite(state, r); state.fromCache = false; }
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
  // In the tree the badge also counts direct reports' updates from other days: the button follows it both ways.
  const allRead = $('#upd-allread');
  if (allRead && UPD && updIsTree(UPD)) allRead.hidden = !(n > 0) && !(UPD.list || []).some(u => !u.read && !u.quiet);
}
// An update's author is a bot slug or `human:<id>` for a person, who posts in the same shape.
function updAuthor(key) {
  const person = String(key || '').startsWith('human:');
  if (!person) return {person, name: empName(key), label: botDisplayName(key), avatar: avatar(key, 36), href: `#/bot/${encodeURIComponent(key)}`};
  const id = String(key).slice(6), p = (S.people || []).find(x => x.id === id);
  return {person, name: esc(p?.name || id), label: p?.name || id, avatar: personAvatar(p || {id, name: id}, 36), href: `#/person/${encodeURIComponent(id)}`};
}
// Today on the queue's clock (backend/updates.py ZONE). Friday's update is the week in review, so your own
// is written on the Daily tab any day and on the Weekly tab on Friday.
const updToday = () => new Date().toLocaleDateString('en-CA', {timeZone: 'America/Los_Angeles'});
const updCanWrite = state => state.kind === 'daily' || new Date(updToday() + 'T12:00:00Z').getUTCDay() === 5;
// A manager's card links the reports' updates it was written from (only those you may read come back).
function updFrom(u) {
  if (!u.inputs?.length) return '';
  return `<div class="upd-from muted">From: ${u.inputs.map(i => {
    const who = updAuthor(i.author);
    return `<a href="${who.href}" data-upd-input="${esc(i.id)}" data-upd-input-author="${esc(i.author)}">${esc(who.label)}</a>`;
  }).join(', ')}</div>`;
}
// A person's update the Assistant drafted says so until they replace it with their own words.
function updDraftPill(u) {
  if (!u.drafted_by) return '';
  return `<span class="upd-pill upd-draft">${u.edited_by ? 'Drafted by ' + esc(botDisplayName(u.drafted_by)) + ', edited' : 'Drafted by ' + esc(botDisplayName(u.drafted_by))}</span>`;
}
function updCard(u, i, state) {
  const who = updAuthor(u.bot);
  const open = state.open.has(u.id);
  const thread = state.threads[u.id];
  const body = String(u.body || '').trim();
  const alert = !u.read && !u.quiet;
  return `<article class="upd-card${alert ? ' unread' : ''}${!u.read && u.quiet ? ' upd-quiet' : ''}${!u.slides && state.view === 'inbox' ? ' swipeable' : ''}${open ? ' open' : ''}${i === state.sel ? ' sel' : ''}" data-upd="${esc(u.id)}" data-i="${i}" tabindex="-1">
    <div class="upd-av">${who.avatar}</div>
    <div class="upd-main">
      <div class="upd-meta"><a href="${who.href}" class="upd-name" data-upd-bot>${who.name}</a>
        ${u.kind === 'weekly' ? '<span class="upd-pill">Week in review</span>' : ''}${updDraftPill(u)}
        <span class="muted">· ${esc(ago(u.updated || u.created))}</span>${alert ? '<span class="upd-dot" aria-label="Unread"></span>' : ''}</div>
      ${u.slides ? updDeck(u, state) : body ? `<div class="upd-body md">${safeMd(body, {shortLinks: true})}</div>` : ''}
      ${updFrom(u)}
      <div class="upd-actions">
        ${who.person ? '' : `<button type="button" class="upd-act" data-upd-reply><span class="nav-icon" aria-hidden="true">chat_bubble</span>${u.replies ? u.replies : 'Reply'}</button>`}
        ${u.own && updCanWrite(state) ? `<button type="button" class="upd-act" data-upd-write><span class="nav-icon" aria-hidden="true">edit</span>${u.day === updToday() ? 'Edit' : 'Write today\'s'}</button>` : ''}
        <button type="button" class="upd-act" data-upd-toggle>${u.read ? 'Mark unread' : 'Mark read'}</button>
        <button type="button" class="upd-act" data-upd-archive aria-label="${state.view === 'archive' ? 'Restore update' : 'Archive update'}"${state.archivePending.has(u.id) ? ' disabled' : ''}>${state.view === 'archive' ? 'Restore' : 'Archive'}</button>
      </div>
      ${open ? `<div class="upd-thread">${thread ? thread.map(m => `<div class="upd-msg${String(m.from_actor).startsWith('bot:') ? ' bot' : ''}${m.pending ? ' pending' : ''}">
          <b>${esc(actorLabel(m.from_actor))}</b> ${String(m.from_actor).startsWith('bot:') ? `<div class="md">${safeMd(m.body || '', {shortLinks: true})}</div>` : esc(String(m.body || '').replace(/^Re your update "[^"]*": /, ''))}
          <span class="muted">${m.pending ? 'sending…' : esc(ago(m.created))}</span></div>`).join('') : (u.replies ? '<div class="muted">Loading replies…</div>' : '')}
        <form class="upd-reply" data-upd-form><textarea rows="1" placeholder="Reply to ${esc(who.label)}…" aria-label="Reply">${esc(UPD_DRAFTS.get(u.id) || '')}</textarea>
          <button class="primary" type="submit" aria-label="Send">Send</button></form>
        <div class="muted upd-hint">Goes to ${esc(who.label)}'s chat too.</div></div>` : ''}
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
// The tree's updates, flat, so reading, replies and keys work on them as on the feed. A report's report
// is `quiet`: it is read when seen but never alerts.
function updFromTree(tree) {
  const updates = [];
  const walk = (nodes, depth) => { for (const n of nodes) {
    if (n.update) { n.update.quiet = !n.direct; n.update.own = n.key === tree.me; updates.push(n.update); }
    walk(n.children, depth + 1);
  } };
  walk(tree.roots, 0);
  return {tree, updates, unread: tree.unread, next_before: null};
}
function updKids(n) {
  let count = 0, unread = false;
  for (const k of n.children) { count++; if (k.direct && k.update && !k.update.read) unread = true; }
  return {count, unread};
}
function updTreeHTML(state) {
  const {tree} = state.data, items = [];
  if (!state.expanded) {          // your card and the one level that alerts start open
    state.expanded = new Set(tree.roots.map(n => n.key));
    const mark = nodes => { for (const n of nodes) { if (n.children.some(k => k.direct)) state.expanded.add(n.key); mark(n.children); } };
    mark(tree.roots);
  }
  const node = (n, depth) => {
    const kids = updKids(n), open = state.expanded.has(n.key);
    const u = n.update ? state.data.updates.find(x => x.id === n.update.id) : null;
    const own = n.key === tree.me;
    let card;
    if (own && state.editing) card = updEditor(u?.day === updToday() ? u : null, n, state);
    else if (u) { card = updCard(u, items.length, state); items.push(u); }
    else {
      const who = updAuthor(n.author);
      card = `<div class="upd-none" data-upd-node="${esc(n.key)}"><span class="upd-av">${who.avatar}</span><a href="${who.href}" class="upd-name">${who.name}</a>
        <span class="muted">No update yet</span>${own && updCanWrite(state) ? '<button type="button" class="ghost upd-write" data-upd-write>Write your update</button>' : ''}</div>`;
    }
    const toggle = kids.count ? `<button type="button" class="upd-kids" data-upd-kids="${esc(n.key)}" aria-expanded="${open}" title="${open ? 'Hide' : 'Show'} the cards of ${esc(n.name)}'s reports">
        <span class="nav-icon" aria-hidden="true">${open ? 'expand_less' : 'expand_more'}</span>${kids.count} report${kids.count === 1 ? '' : 's'}${kids.unread ? '<span class="upd-dot" aria-label="Unread"></span>' : ''}</button>` : '';
    const below = kids.count && open ? `<div class="upd-sub">${n.children.map(k => node(k, depth + 1)).join('')}</div>` : '';
    return `<div class="upd-node" data-depth="${depth}">${card}${toggle}${below}</div>`;
  };
  const html = tree.roots.map(n => node(n, 0)).join('');
  state.list = items;
  return html || '<div class="upd-empty"><span class="nav-icon" aria-hidden="true">account_tree</span><b>No one on the org chart yet.</b></div>';
}
// Your own card: write your update, or edit the one you or the Assistant posted today. Words you write
// yourself drop the Assistant's label; an edit keeps it, marked edited. On the Weekly tab it is the week in
// review's slides (the same shape a bot posts), one bullet per line.
const UPD_WEEK = [['done', 'Done last week', 'one to five'], ['focus', 'Focus next week', 'one to three'], ['blockers', 'Biggest blockers', 'up to three, or none']];
const updKpiLine = k => `${k.name}: ${[k.value, k.unit].filter(Boolean).join(' ')}`;
function updEditor(u, n, state) {
  const week = state.kind === 'weekly', s = u?.slides || {};
  const fields = week
    ? `<label class="upd-field">Goal <span class="muted">one sentence: what you work toward and where it stands</span>
        <input type="text" data-upd-slide="goal" maxlength="300" value="${esc(s.goal || '')}"></label>
      ${UPD_WEEK.map(([key, label, hint]) => `<label class="upd-field">${label} <span class="muted">${hint}, one per line</span>
        <textarea rows="3" data-upd-slide="${key}">${esc((s[key] || []).join('\n'))}</textarea></label>`).join('')}
      <label class="upd-field">KPIs <span class="muted">optional, one per line as Name: value</span>
        <textarea rows="2" data-upd-slide="kpis">${esc((s.kpis || []).map(updKpiLine).join('\n'))}</textarea></label>`
    : `<textarea rows="4" aria-label="Your update" placeholder="- One to five bullets: what you did, do next, or wait on">${esc(u?.body || '')}</textarea>`;
  return `<form class="upd-card upd-edit" data-upd-editor data-upd-kind="${week ? 'weekly' : 'daily'}">
    <div class="upd-main"><div class="upd-meta"><b>${updAuthor(n.author).name}</b><span class="muted">· ${week ? 'this week in review' : "today's update"}</span></div>
      ${fields}
      ${u?.drafted_by ? '<label class="upd-own"><input type="checkbox" data-upd-own> These are my own words (drop the Drafted by Assistant label)</label>' : ''}
      <div class="upd-actions"><button class="primary" type="submit">Post</button><button type="button" class="ghost" data-upd-cancel>Cancel</button></div>
      <div class="err" role="status" data-upd-edit-err></div></div></form>`;
}
// The editor's slides: bullets one per line, and KPIs as "Name: value", keeping a KPI's chart and note when
// its line is unchanged.
function updEditorSlides(form, old) {
  const lines = key => form.querySelector(`[data-upd-slide="${key}"]`).value.split('\n').map(l => l.replace(/^\s*[-*•]\s*/, '').trim()).filter(Boolean);
  const kpis = lines('kpis').map(line => {
    const same = (old?.kpis || []).find(k => updKpiLine(k) === line);
    if (same) return same;
    const at = line.indexOf(':');
    return at > 0 ? {name: line.slice(0, at).trim(), value: line.slice(at + 1).trim()} : null;
  }).filter(k => k?.name && k.value);
  return {goal: form.querySelector('[data-upd-slide="goal"]').value.trim(), done: lines('done'), focus: lines('focus'), blockers: lines('blockers'), kpis};
}
async function updPostOwn(state, form) {
  const err = form.querySelector('[data-upd-edit-err]'), week = form.dataset.updKind === 'weekly';
  const replace_draft = !!form.querySelector('[data-upd-own]')?.checked;
  let payload;
  if (week) {
    const old = state.data?.updates?.find(x => x.own)?.slides;
    const slides = updEditorSlides(form, old);
    if (!slides.goal || !slides.done.length) { err.textContent = 'Write your goal and at least one thing done.'; return; }
    payload = {kind: 'weekly', slides, replace_draft};
  } else {
    const body = form.querySelector('textarea').value.trim();
    if (!body) { err.textContent = 'Write at least one bullet.'; return; }
    // Always a daily: on a Friday the server would otherwise take it for a week in review.
    payload = {body, kind: 'daily', replace_draft};
  }
  const button = form.querySelector('[type=submit]'); button.disabled = true; err.textContent = '';
  try {
    const r = await post('/v2/updates', payload);
    if (UPD !== state) return;
    if (r.update?.warning) toast(r.update.warning);
    state.editing = false;
    await updLoad(state);
  } catch (e) { err.textContent = e.message || 'Could not post it.'; button.disabled = false; }
}
// A "From:" link: in the tree, open that report's card (its managers' cards too) and go to it; in List, go to
// its card when it is on the page, else load that one update and show it under the card that links it (the
// author's page shows their latest, which on an older card is a newer update). If it can't load, its author.
async function updGoInput(state, link) {
  const id = link.dataset.updInput;
  if (state.data?.tree && updIsTree(state)) {
    const path = [], find = nodes => nodes.some(n => (n.update?.id === id || find(n.children)) && path.push(n.key));
    if (find(state.data.tree.roots)) {
      path.slice(1).forEach(key => state.expanded.add(key));
      updRender(state);
    }
  }
  let i = (state.list || []).findIndex(u => u.id === id);
  if (i < 0 && !updIsTree(state) && state.data) {
    const from = link.closest('[data-upd]')?.dataset.upd;
    const r = await v2Get('/v2/updates/' + encodeURIComponent(id));
    if (UPD !== state || !state.data) return;
    if (r?.update) {
      if (!state.data.updates.some(u => u.id === id)) state.data.updates.push(r.update);
      state.threads[id] ||= r.thread;
      state.linked.add(id);                              // shown even when My bots would hide its author
      state.order ||= new Map();
      state.order.set(id, (state.order.get(from) ?? -1) + 0.5);
      updRender(state);
      i = state.list.findIndex(u => u.id === id);
    }
  }
  if (i < 0) { location.hash = link.getAttribute('href'); return; }
  updSelect(state, i);
}
function updRender(state) {
  const feed = $('#upd-feed'); if (!feed || UPD !== state) return;
  updSaveDrafts();
  const data = state.data;
  if (!data) { feed.innerHTML = '<div class="upd-skel"></div><div class="upd-skel"></div><div class="upd-skel"></div>'; return; }
  if (data.tree && updIsTree(state)) {
    feed.innerHTML = updTreeHTML(state);
    updDecksBind(state);
    // The badge also counts direct reports' updates from other days, which this day's tree does not show.
    const allRead = $('#upd-allread'); if (allRead) allRead.hidden = !(S.updUnread > 0) && !state.list.some(u => !u.read && !u.quiet);
    updWatch(state);
    return;
  }
  const meId = S.me?.id;
  const isMine = slug => { const e = S.emps.find(x => x.name === slug); return !e || e.operator === meId || (!e.operator && S.me?.role === 'owner'); };
  // Unread first, then newest to oldest; the order is set when the page opens
  // (updOrder) and never moves while you read and scroll, only on the next visit.
  const rank = state.order || new Map();
  const items = (data.updates || []).filter(u => !state.mine || isMine(u.bot) || state.linked.has(u.id))
    .sort((a, b) => (rank.get(a.id) ?? -1) - (rank.get(b.id) ?? -1));
  state.list = items;
  // Just the updates in a feed; no greeting, no progress, no who did not report.
  let html = items.map((u, i) => updCard(u, i, state)).join('');
  if (!items.length) html += state.view === 'archive'
    ? '<div class="upd-empty"><span class="nav-icon" aria-hidden="true">archive</span><b>No archived updates.</b></div>'
    : '<div class="upd-empty"><span class="nav-icon" aria-hidden="true">dynamic_feed</span><b>No updates yet.</b><span class="muted">The bots report in one at a time each morning.</span>' + (window.talkLineHTML?.() || '') + '</div>';
  if (data.next_before) html += '<button type="button" class="ghost upd-more" data-upd-more>Load older</button>';
  feed.innerHTML = html;
  updDecksBind(state);
  const allRead = $('#upd-allread'); if (allRead) allRead.hidden = state.view === 'archive' || !items.some(u => !u.read);   // nothing to mark, no button
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
  for (const el of document.querySelectorAll('#upd-feed .upd-card.unread, #upd-feed .upd-card.upd-quiet')) state.io.observe(el);
}
function updSeen(state, id) {
  const u = state.data?.updates?.find(x => x.id === id);
  if (!u || u.read || state.manualUnread.has(id) || UPD !== state) return;
  u.read = true; state.pending.add(id);
  const el = document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"]`);
  el?.classList.remove('unread', 'upd-quiet'); el?.querySelector('.upd-dot')?.remove();
  const t = el?.querySelector('[data-upd-toggle]'); if (t) t.textContent = 'Mark unread';
  if (!u.quiet) updBadge(Math.max(0, (S.updUnread || 1) - 1));
  clearTimeout(state.flush);
  state.flush = setTimeout(() => updFlush(state), 900);
}
async function updFlush(state) {
  const ids = [...state.pending]; state.pending.clear();
  if (!ids.length) return;
  try { await post('/v2/updates/read', {ids}); if (UPD === state) updCacheWrite(state, state.data); } catch { ids.forEach(id => state.pending.add(id)); }
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
    UPD_DRAFTS.set(id, text);
    updRender(state);
    toast(e.message || 'Could not send the reply', true);
  }
}
async function updToggle(state, id) {
  const u = state.data.updates.find(x => x.id === id); if (!u) return;
  u.read = !u.read;
  if (u.read) state.manualUnread.delete(id);
  else { state.manualUnread.add(id); state.pending.delete(id); }
  updCacheWrite(state, state.data);
  if (!u.quiet) updBadge(Math.max(0, (S.updUnread || 0) + (u.read ? -1 : 1)));
  updRender(state);
  try { await post('/v2/updates/read', {ids: [id], read: u.read}); } catch (e) { toast(e.message || 'Could not save', true); }
}
async function updArchive(state, id) {
  const update = state.data?.updates?.find(u => u.id === id);
  if (!update || state.archivePending.has(id)) return;
  const archived = state.view !== 'archive';
  state.archivePending.add(id); state.loadSeq++; state.mutationSeq++;
  const button = document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"] [data-upd-archive]`);
  if (button) button.disabled = true;                  // no repaint: an open reply draft stays put
  try {
    await post('/v2/updates/archive', {ids: [id], archived});
    updCacheClear();                                    // another view/scope must not paint stale state
    const unreadRefresh = updUnreadRefresh();             // uses the signed-in person's current My Bots scope
    state.mutationSeq++;
    if (UPD !== state) return;
    updSaveDrafts();
    state.archivePending.delete(id);
    state.data.updates = state.data.updates.filter(u => u.id !== id);
    state.pending.delete(id);
    state.list = (state.list || []).filter(u => u.id !== id);
    updRender(state);
    updCacheWrite(state, state.data);
    void unreadRefresh.then(result => {
      if (result && UPD === state && state.data) {
        state.data.unread = result.unread;
        updCacheWrite(state, state.data);
      }
    });
    toast(archived ? 'Archived' : 'Restored to Inbox');
  } catch (e) {
    state.mutationSeq++;
    state.archivePending.delete(id);
    if (UPD === state) {
      const current = document.querySelector(`#upd-feed [data-upd="${CSS.escape(id)}"] [data-upd-archive]`);
      if (current) current.disabled = false;
    }
    toast(e.message || (archived ? 'Could not archive the update' : 'Could not restore the update'), true);
  }
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
    if (state.ignoreClick && Date.now() < state.ignoreClick.until && card?.dataset.upd === state.ignoreClick.id) {
      state.ignoreClick = null; return;                 // consume only this swipe's synthetic click
    }
    if (state.ignoreClick && Date.now() >= state.ignoreClick.until) state.ignoreClick = null;
    if (ev.target.closest('[data-upd-more]')) { void updLoad(state, true); return; }
    const kids = ev.target.closest('[data-upd-kids]');
    if (kids) {
      const key = kids.dataset.updKids;
      if (state.expanded.has(key)) state.expanded.delete(key); else state.expanded.add(key);
      updRender(state); return;
    }
    if (ev.target.closest('[data-upd-write]')) {
      state.editing = true; updRender(state);
      document.querySelector('#upd-feed [data-upd-editor] textarea, #upd-feed [data-upd-editor] input[type=text]')?.focus(); return;
    }
    if (ev.target.closest('[data-upd-cancel]')) { state.editing = false; updRender(state); return; }
    const input = ev.target.closest('[data-upd-input]');
    if (input) { ev.preventDefault(); void updGoInput(state, input); return; }
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
    if (ev.target.closest('[data-upd-archive]')) return void updArchive(state, id);
    if (ev.target.closest('.upd-body') && !ev.target.closest('a')) return void updOpen(state, id);
  });
  feed.addEventListener('submit', ev => {
    const editor = ev.target.closest('[data-upd-editor]');
    if (editor) { ev.preventDefault(); void updPostOwn(state, editor); return; }
    const form = ev.target.closest('[data-upd-form]'); if (!form) return;
    ev.preventDefault();
    const ta = form.querySelector('textarea'), text = ta.value;
    const id = form.closest('[data-upd]').dataset.upd;
    ta.value = ''; UPD_DRAFTS.delete(id);
    void updReply(state, id, text);
  });
  feed.addEventListener('keydown', ev => {
    const ta = ev.target.closest('[data-upd-form] textarea'); if (!ta) return;
    if (ev.key === 'Enter' && !ev.shiftKey && !matchMedia('(pointer:coarse)').matches) { ev.preventDefault(); ta.form.requestSubmit(); }
    if (ev.key === 'Escape') ta.blur();
  });
  feed.addEventListener('input', ev => {
    const ta = ev.target.closest('[data-upd-form] textarea'); if (!ta) return;
    const id = ta.closest('[data-upd]')?.dataset.upd;
    if (id) { if (ta.value) UPD_DRAFTS.set(id, ta.value); else UPD_DRAFTS.delete(id); }
    ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  });
  feed.addEventListener('pointerdown', ev => {
    if (ev.pointerType !== 'touch' && !matchMedia('(pointer:coarse)').matches) return;
    const card = ev.target.closest('[data-upd]');
    if (!card || state.view !== 'inbox' || ev.target.closest('a,button,input,textarea,select,form,[contenteditable],[data-upd-deck]')) {
      state.swipe = null; return;
    }
    state.swipe = {id: card.dataset.upd, x: ev.clientX, y: ev.clientY};
  }, {passive: true});
  feed.addEventListener('pointerup', ev => {
    const start = state.swipe; state.swipe = null;
    if (!start || (ev.pointerType !== 'touch' && !matchMedia('(pointer:coarse)').matches)) return;
    const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
    if (dx < -80 && Math.abs(dy) < 48) {
      state.ignoreClick = {id: start.id, until: Date.now() + 500};
      void updArchive(state, start.id);
    }
  }, {passive: true});
  feed.addEventListener('pointercancel', () => { state.swipe = null; }, {passive: true});
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
// Only your direct reports' updates alert, one level down the org chart.
async function updUnreadRefresh() {
  const r = await v2Get('/v2/updates/unread?scope=direct');
  if (r) { updBadge(r.unread); meetPendingBadge(r.meetings_pending || 0); }
  return r;
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
  const q = new URLSearchParams({bot: slug, limit: '30', include_archive: 'true'}); if (before) q.set('before', before);
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
      <span>${k === 'daily' ? 'Daily update' : 'Friday week in review'}</span></label>`).join('');   // "See updates" is in the card's header
  host.onchange = async ev => {
    const box = ev.target.closest('[data-upd-set]'); if (!box) return;
    try { await post(`/v2/bots/${encodeURIComponent(slug)}/updates`, {[box.dataset.updSet]: box.checked}); toast('Saved'); }
    catch (e) { box.checked = !box.checked; toast(e.message || 'Could not save', true); }
  };
}
