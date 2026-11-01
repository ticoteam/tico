/* ui/app/pages.js — Pages (docs/pages.md): the card every page shows as (pageCard), the full view at #/page/<id>, a
   task's pages at the top of the task, and Library › Files (#/library/files), every file the reader may see, with
   filters and search. The status line is drawn here, outside the page's frame, so a page cannot fake it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PAGE_ROUTE = '#/page/', RD_FILES = '#/library/files';
// A card shows the top of the page at the card's width, cut at its size's height; full follows the page's own height.
const PAGE_SIZES = {small: 180, medium: 320, large: 520};
const PAGE_FULL_MIN = 240;

// ----------------------------------------------------------------- the status line
const pageDay = iso => { const d = new Date(String(iso) + 'T12:00:00'); return isNaN(d) ? String(iso) : d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}); };
const pageHref = id => PAGE_ROUTE + encodeURIComponent(id);
// The bot's name as the rest of the app shows it (duplicates get their slug), else the server's.
const pageBotName = x => x.bot && (S.emps || []).some(e => e.name === x.bot) ? botDisplayName(x.bot) : x.bot_name || x.bot || '';
// `link: false` inside a card that is itself a link: the replacing page's title without its own link.
function pageStatusHTML(p, {link = true} = {}) {
  if (p.replaced_by) {
    const title = esc(p.replaced_by_title || 'a newer page');
    return `<div class="pg-status pg-replaced">Replaced by ${link ? `<a href="${esc(pageHref(p.replaced_by))}">${title}</a>` : title}</div>`;
  }
  const stale = Number.isInteger(p.stale_days);
  const by = String(p.updated_by || '');
  const who = by.startsWith('bot:') ? pageBotName({bot: by.slice(4), bot_name: p.updated_by_name}) : p.updated_by_name || pageBotName(p);
  const parts = [stale ? `Not updated in ${p.stale_days} day${p.stale_days === 1 ? '' : 's'}`
    : `Updated ${esc(ago(p.updated_at))}${who ? ' by ' + esc(who) : ''}`];
  if (Number.isInteger(p.version)) parts.push('v' + p.version);
  if (p.cadence && p.cadence !== 'none') parts.push(esc(p.cadence));
  if (p.as_of) parts.push('data from ' + esc(pageDay(p.as_of)));
  return `<div class="pg-status${stale ? ' pg-stale' : ''}" title="${esc(fmt(p.updated_at))}">${parts.join(' · ')}</div>`;
}

// ----------------------------------------------------------------- the card
// The approved address a page frames from, or null for an Open card. Tico file hosting's desktop check
// (visualPageSrc, ui/app/visual-cards.js) applies where that build has it.
// visualPageSrc (ui/app/visual-cards.js): null when this window can't frame it, e.g. an older desktop app.
const pageSrc = p => p.src ? visualPageSrc(p.src) : null;
function pageFrame(p, src, height) {
  const f = document.createElement('iframe');
  f.className = 'page-frame';
  // Set before the frame joins the page, as a message's visual page is (VISUAL_PAGE_SANDBOX): the only script it runs
  // is the server's height reporter.
  f.setAttribute('sandbox', VISUAL_PAGE_SANDBOX);
  f.setAttribute('credentialless', '');
  f.referrerPolicy = 'no-referrer';
  f.title = p.title || 'Page';
  f.style.height = height + 'px';
  f.src = src;
  return f;
}
// pageCard(page, {size}): one card holding the page's status line and the framed page (or an Open card, the status
// under its title, when there is no address). Tapping a card opens the page full size; `full` is that full size, where
// the status line sits under the view's own title. `page` is GET /api/v2/pages/{id}'s page.
window.pageCard = function pageCard(page, {size = 'medium'} = {}) {
  const full = size === 'full', cap = PAGE_SIZES[size] || PAGE_SIZES.medium;
  const el = document.createElement('article');
  el.className = `pg-card pg-${full ? 'full' : PAGE_SIZES[size] ? size : 'medium'}`;
  el.dataset.page = page.file_id;
  const src = pageSrc(page);
  if (!src) {
    const open = document.createElement(full ? 'div' : 'a');
    open.className = 'pg-open';
    if (!full) open.href = pageHref(page.file_id);
    open.innerHTML = `<span class="nav-icon" aria-hidden="true">article</span><div class="pg-open-text"><span class="pg-open-title">${esc(page.title || 'Page')}</span>${full ? '' : pageStatusHTML(page, {link: false})}</div>${full ? '' : '<span class="pg-open-btn">Open</span>'}`;
    if (full) el.innerHTML = pageStatusHTML(page);
    el.append(open);
    return el;
  }
  el.classList.add('pg-framed');
  el.innerHTML = pageStatusHTML(page);
  const box = document.createElement('div');
  box.className = 'pg-box';
  const frame = pageFrame(page, src, full ? PAGE_FULL_MIN : cap);
  frame.pageCap = full ? 0 : cap;
  box.append(frame);
  if (!full) {
    const tap = document.createElement('a');
    tap.className = 'pg-tap'; tap.href = pageHref(page.file_id);
    tap.setAttribute('aria-label', 'Open ' + (page.title || 'page'));
    box.append(tap);
  }
  el.append(box);
  return el;
};
// The height a page reports (backend/visuals.py REPORTER), from a page frame's own window only: a card takes it up to
// its cut, so a short page leaves no blank; the full view takes it whole.
window.addEventListener('message', ev => {
  const n = ev.data && typeof ev.data === 'object' ? ev.data.ticoVisualHeight : undefined;
  if (typeof n !== 'number' || !Number.isFinite(n) || !ev.source) return;
  const frame = [...document.querySelectorAll('iframe.page-frame')].find(x => x.contentWindow === ev.source);
  if (!frame) return;
  const h = Math.max(24, Math.min(Math.ceil(n), VISUAL_PAGE_MAX));
  frame.style.height = (frame.pageCap ? Math.min(h, frame.pageCap) : Math.max(h, PAGE_FULL_MIN)) + 'px';
});

// ----------------------------------------------------------------- the full view, #/page/<id>
let PG = null;                // {id, page, versions, shown}
async function pageFullView(id) {
  const main = $('#main');
  main.innerHTML = `<div class="pg-view" id="pg-view">${skelRows(3, {card: true})}</div>`;
  const state = PG = {id, page: null, versions: [], shown: 0, can: {}};
  let read;
  try { read = await get('/v2/pages/' + encodeURIComponent(id), {fresh: true}); }
  catch (e) {
    if (PG !== state) return;
    $('#pg-view').innerHTML = `<p class="muted pg-pad">${esc(e.status === 404 ? 'This page is gone, or not yours to see.' : e.message)}</p>`;
    return;
  }
  if (PG !== state) return;
  state.page = read.page; state.can = {manage: !!read.can_manage, del: !!read.can_delete};
  state.pinned = read.pinned || [];
  state.shown = read.page.version;
  pageFullDraw(state);
  get(`/v2/files/${encodeURIComponent(id)}/versions`).then(v => {
    if (PG !== state) return;
    state.versions = v.versions || [];
    pageFullHead(state);
  }).catch(() => {});
}
function pageFullDraw(state) {
  const root = $('#pg-view');
  if (!root) return;
  const pinned = state.pinned.map(x => `<a href="${esc(pagePlaceHref(x.place))}">${esc(x.label)}</a>`).join(' · ');
  root.innerHTML = `<header class="pg-head"></header><div class="pg-body"></div>${pinned ? `<p class="pg-pinned">Pinned to ${pinned}</p>` : ''}`;
  pageFullHead(state);
  pageFullBody(state);
}
// A pin's place (backend/cards.py) as the app's address.
function pagePlaceHref(place) {
  const [kind, ...rest] = String(place).split(':'), id = encodeURIComponent(rest.join(':'));
  return kind === 'dashboard' ? OVERVIEW : kind === 'team' ? '#/team/' + id : kind === 'bot' ? '#/bot/' + id : '#/task/' + id;
}
function pageFullHead(state) {
  const p = state.page, head = $('#pg-view .pg-head');
  if (!head) return;
  const versions = state.versions.length > 1 ? `<select class="pg-ver" aria-label="Version">${state.versions.map(v =>
    `<option value="${v.version}"${v.version === state.shown ? ' selected' : ''}>v${v.version} · ${esc(pageDay((v.created_at || '').slice(0, 10)))}</option>`).join('')}</select>` : '';
  const items = [
    state.can.manage ? `<button type="button" role="menuitem" data-pg-act="archive">${p.archived ? 'Restore' : 'Archive'}</button>` : '',
    `<a role="menuitem" href="${API}/v2/files/${encodeURIComponent(p.file_id)}?v=${state.shown}" download>Download</a>`,
    state.can.del ? `<button type="button" role="menuitem" class="danger" data-pg-act="delete">Delete for good</button>` : ''].join('');
  head.innerHTML = `<button type="button" class="ghost pg-back" data-pg-act="back" aria-label="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></button>
    <h1 class="pg-title">${esc(p.title)}</h1>${p.archived ? '<span class="pill pg-archived">Archived</span>' : ''}<span class="spacer"></span>${versions}
    <details class="pg-menu"><summary aria-label="More" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></summary><div class="pg-menu-list" role="menu">${items}</div></details>`;
  head.onclick = ev => { const b = ev.target.closest('[data-pg-act]'); if (b) void pageFullAct(state, b.dataset.pgAct); };
  const ver = head.querySelector('.pg-ver');
  if (ver) ver.onchange = () => { state.shown = Number(ver.value); pageFullHead(state); pageFullBody(state); };
}
// The current version frames from its address; an older one, or any without an address, shows in the file viewer's
// sandboxed preview (ui/app/viewer.js), which looks the same: pages run no script.
async function pageFullBody(state) {
  const p = state.page, body = $('#pg-view .pg-body');
  if (!body) return;
  const current = state.shown === p.version;
  const card = window.pageCard(p, {size: 'full'});
  if (current && card.querySelector('iframe')) { body.replaceChildren(card); return; }
  card.querySelector('.pg-open')?.remove();
  if (!current) card.querySelector('.pg-status').textContent = `v${state.shown} · an older version`;
  const box = document.createElement('div');
  box.className = 'pg-box pg-preview';
  box.innerHTML = skelRows(4);
  card.append(box);
  body.replaceChildren(card);
  try {
    const item = await viewFile(p.file_id, p.title + '.html', state.shown);
    const text = await item.blob.text();
    if (PG !== state || !box.isConnected) return;
    box.replaceChildren(htmlPreviewFrame(text, p.title));
  } catch (e) { if (box.isConnected) box.innerHTML = `<p class="muted pg-pad">${esc(e.message)}</p>`; }
}
async function pageFullAct(state, act) {
  const p = state.page;
  document.querySelector('#pg-view .pg-menu')?.removeAttribute('open');
  if (act === 'back') { if (history.length > 1) history.back(); else location.hash = RD_FILES; return; }
  try {
    if (act === 'archive') {
      const r = await patch('/v2/pages/' + encodeURIComponent(p.file_id), {archived: !p.archived});
      if (PG !== state) return;
      state.page = r.page;
      pageFullHead(state);
      toast(r.page.archived ? 'Archived' : 'Restored');
    } else if (act === 'delete') {
      if (!confirm(`Delete “${p.title}” and every version for good? This cannot be undone.`)) return;
      await writeRequest('DELETE', '/v2/files/' + encodeURIComponent(p.file_id));
      toast('Deleted');
      location.hash = RD_FILES;
    }
  } catch (e) { toast(e.message, true); }
}

// ----------------------------------------------------------------- a task's pages
// The pages filed on a task, as cards at its top. The section moves into each redraw of the task (as its files do,
// tfAdopt), so a loaded frame is not loaded again.
function taskPagesAdopt(d, task) {
  const slot = $('[data-task-pages]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tpEl && d.tpEl.dataset.task === id) { slot.replaceWith(d.tpEl); return; }
  d.tpEl = slot; slot.dataset.task = id;
  // The pages pinned to the task (a page published with --task pins itself there), in their order.
  get('/v2/places/' + encodeURIComponent('task:' + id)).then(r => {
    if (d.tpEl !== slot) return;
    const pins = (r.pins || []).filter(x => x.page && !x.page.replaced_by && !x.page.archived);   // a replaced page links on
    slot.hidden = !pins.length;
    slot.replaceChildren(...pins.map(x => window.pageCard(x.page, {size: x.size || 'medium'})));
  }).catch(() => {});
}

// ----------------------------------------------------------------- Library › Files
const LF_KINDS = [['', 'All kinds'], ['page', 'Pages'], ['document', 'Documents'], ['image', 'Images'], ['data', 'Data'],
  ['media', 'Video and audio'], ['other', 'Other']];
const LF_ICON = {page: 'article', document: 'description', image: 'palette', data: 'analytics', media: 'play_arrow', other: 'inventory_2'};
const LF_DATES = [['', 'Any time'], ['7', 'Past week'], ['30', 'Past month'], ['365', 'Past year']];
const LF_FILTERS = ['q', 'kind', 'bot', 'team', 'task', 'days', 'pinned', 'archived'];
let LF = null;                // {filter, rows, cursor, loading, seq, groups}
function lfQuery(filter = LF.filter) {
  const q = new URLSearchParams();
  for (const k of LF_FILTERS) if (filter[k]) q.set(k, filter[k] === true ? '1' : filter[k]);
  const s = q.toString();
  return s ? '?' + s : '';
}
function lfApiQuery(cursor) {
  const f = LF.filter, q = new URLSearchParams({limit: '50'});
  for (const k of ['kind', 'bot', 'team', 'task']) if (f[k]) q.set(k, f[k]);
  if (f.days) q.set('since', new Date(Date.now() - Number(f.days) * 864e5).toISOString());
  if (f.pinned) q.set('pinned', 'true');
  if (f.archived) q.set('archived', 'true');
  if (cursor) q.set('cursor', cursor);
  return q.toString();
}
async function pageLibraryFiles() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const filter = Object.fromEntries(LF_FILTERS.map(k => [k, ['pinned', 'archived'].includes(k) ? params.get(k) === '1' : params.get(k) || '']));
  const same = LF && document.body.contains(LF.root);
  if (!same) {
    $('#main').innerHTML = `<div class="lf" id="lf-root">
      <header class="lf-head"><h1>Files</h1><span class="spacer"></span></header>
      <div class="lf-filters"><input class="lf-q" type="search" placeholder="Search" aria-label="Search files" autocomplete="off">
        <div class="lf-frow"><select class="lf-kind" aria-label="Kind">${LF_KINDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
          <select class="lf-bot" aria-label="Bot"><option value="">All bots</option></select>
          <select class="lf-team" aria-label="Team"><option value="">All teams</option></select>
          <select class="lf-days" aria-label="Updated">${LF_DATES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
          <button class="lf-toggle" type="button" data-lf-toggle="pinned" aria-pressed="false">Pinned</button>
          <button class="lf-toggle" type="button" data-lf-toggle="archived" aria-pressed="false">Archived</button>
          <span class="lf-task" hidden></span></div></div>
      <div class="lf-list" aria-live="polite">${skelRows(6)}</div></div>`;
    LF = {root: $('#lf-root'), filter, rows: [], cursor: null, seq: 0, groups: []};
    lfWire();
    get('/v2/groups').then(g => { if (!LF) return; LF.groups = Array.isArray(g) ? g : g?.groups || []; lfControls(); }).catch(() => {});
  } else LF.filter = filter;
  lfControls();
  await lfLoad();
}
function lfControls() {
  const root = LF.root, f = LF.filter;
  const bots = (S.emps || []).filter(e => e.status !== 'archived').map(e => [e.name, botDisplayName(e.name)])
    .sort((a, b) => a[1].localeCompare(b[1], undefined, {numeric: true}));
  const opt = (v, l) => `<option value="${esc(v)}">${esc(l)}</option>`;
  const bot = root.querySelector('.lf-bot'), team = root.querySelector('.lf-team');
  bot.innerHTML = opt('', 'All bots') + bots.map(([v, l]) => opt(v, l)).join('');
  team.innerHTML = opt('', 'All teams') + LF.groups.map(g => opt(g.id, g.name)).join('');
  team.hidden = !LF.groups.length;
  root.querySelector('.lf-q').value = f.q;
  root.querySelector('.lf-kind').value = f.kind;
  bot.value = f.bot; team.value = f.team;
  root.querySelector('.lf-days').value = f.days;
  for (const b of root.querySelectorAll('[data-lf-toggle]')) b.setAttribute('aria-pressed', String(!!f[b.dataset.lfToggle]));
  const task = root.querySelector('.lf-task');
  task.hidden = !f.task;
  task.innerHTML = f.task ? `Task <button type="button" class="link" data-lf-clear="task" aria-label="Show every task">✕</button>` : '';
  tasksBarEdge(root.querySelector('.lf-frow'));
}
function lfWire() {
  const root = LF.root;
  // On a phone the filters scroll sideways, the right edge fading while more are off it, as the Tasks bar does.
  const frow = root.querySelector('.lf-frow');
  frow.onscroll = () => tasksBarEdge(frow);
  if (window.ResizeObserver) new ResizeObserver(() => tasksBarEdge(frow)).observe(frow);
  const changed = () => {
    history.replaceState(null, '', RD_FILES + lfQuery());
    S.route = LAST_ROUTE = location.hash;
    void lfLoad();
  };
  let soon = 0;
  root.querySelector('.lf-q').oninput = ev => { LF.filter.q = ev.target.value.trim(); clearTimeout(soon); soon = setTimeout(changed, 250); };
  for (const [sel, key] of [['.lf-kind', 'kind'], ['.lf-bot', 'bot'], ['.lf-team', 'team'], ['.lf-days', 'days']])
    root.querySelector(sel).onchange = ev => { LF.filter[key] = ev.target.value; changed(); };
  root.onclick = ev => {
    const t = ev.target.closest('[data-lf-toggle]');
    if (t) { LF.filter[t.dataset.lfToggle] = !LF.filter[t.dataset.lfToggle]; t.setAttribute('aria-pressed', String(LF.filter[t.dataset.lfToggle])); changed(); return; }
    const c = ev.target.closest('[data-lf-clear]');
    if (c) { LF.filter[c.dataset.lfClear] = ''; lfControls(); changed(); return; }
    if (ev.target.closest('[data-lf-more]')) { void lfLoad(true); return; }
    const row = ev.target.closest('a.lf-row[data-lf-open="tico"]');
    if (row && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); void openFileLink(row.dataset.id, row.dataset.name); }
  };
}
// Search (the box) asks GET /api/v2/files/search and narrows its hits by kind and bot here; the list asks
// GET /api/v2/files with every filter, a page at a time.
async function lfLoad(more = false) {
  const state = LF, seq = ++state.seq, f = state.filter, list = state.root.querySelector('.lf-list');
  if (!more) list.innerHTML = skelRows(6);
  try {
    if (f.q) {
      const r = await get(`/v2/files/search?q=${encodeURIComponent(f.q)}&limit=50`, {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = (r.results || []).filter(h => (!f.kind || h.library_kind === f.kind) && (!f.bot || h.bot === f.bot))
        .map(h => ({...h, last_activity_at: h.updated_at, hit: true}));
      state.cursor = null;
    } else {
      const r = await get('/v2/files?' + lfApiQuery(more ? state.cursor : ''), {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = more ? state.rows.concat(r.files || []) : r.files || [];
      state.cursor = r.has_more ? r.next_cursor : null;
    }
  } catch (e) {
    if (LF === state && seq === state.seq) list.innerHTML = `<p class="err lf-pad">${esc(e.message)}</p>`;
    return;
  }
  lfDraw();
}
function lfRowHTML(f) {
  const kind = f.library_kind || 'other';
  const page = kind === 'page', link = f.locator === 'remote_link' || (f.hit && f.url);
  const url = page ? pageHref(f.id) : link ? (f.open?.url || f.url) : f.open?.url || `${API}/v2/files/${encodeURIComponent(f.id)}`;
  const label = LF_KINDS.find(([v]) => v === kind)?.[1] || 'Other';
  const pins = (f.pins || []).length ? `<span class="lf-pins" title="${esc(f.pins.join(', '))}">Pinned${f.pins.length > 1 ? ' ' + f.pins.length : ''}</span>` : '';
  const attrs = page ? '' : link ? ' target="_blank" rel="noopener noreferrer"' : ` data-lf-open="tico" data-id="${esc(f.id)}" data-name="${esc(f.name || f.title)}"`;
  return `<a class="lf-row" href="${esc(url)}"${attrs}>
    <span class="nav-icon" aria-hidden="true">${link ? 'link' : LF_ICON[kind] || 'inventory_2'}</span>
    <span class="lf-main"><span class="lf-title">${esc(f.title)}</span>
      <span class="lf-meta">${esc(label.replace(/s$/, ''))} · ${esc(pageBotName(f))} · ${esc(ago(f.last_activity_at))}${f.archived ? ' · archived' : ''}</span>
      ${f.excerpt ? `<span class="lf-excerpt">${esc(f.excerpt)}</span>` : ''}</span>${pins}</a>`;
}
function lfDraw() {
  const list = LF.root.querySelector('.lf-list'), rows = LF.rows;
  const any = LF_FILTERS.some(k => LF.filter[k]);
  list.innerHTML = rows.length ? rows.map(lfRowHTML).join('') + (LF.cursor ? '<button type="button" class="ghost lf-more" data-lf-more>More</button>' : '')
    : `<p class="muted lf-pad">${any ? 'Nothing matches.' : 'No files yet.'}</p>`;
}

// ----------------------------------------------------------------- routes and global search
// Called by redesignRoute (ui/app/redesign.js) before the router's own list.
function pagesRoute(r) {
  if (rdUnder(r, RD_FILES)) { pageLibraryFiles(); return true; }
  if (r.startsWith(PAGE_ROUTE)) { void pageFullView(decodeURIComponent(r.slice(PAGE_ROUTE.length).split('?')[0])); return true; }
  return false;
}
function pagesStop(r) {
  if (!r.startsWith(PAGE_ROUTE)) PG = null;
  if (!rdUnder(r, RD_FILES)) LF = null;
}
// File hits for the search box (ui/app/search.js): best first, only what the reader may open.
async function searchFileHits(q) {
  if (q.trim().length < 2) return [];
  const r = await get(`/v2/files/search?q=${encodeURIComponent(q.trim())}&limit=6`);
  return (r.results || []).map(h => ({kind: 'file', label: h.title, alias: pageBotName(h), icon: LF_ICON[h.library_kind] || 'inventory_2',
    href: h.library_kind === 'page' ? pageHref(h.id) : RD_FILES + '?q=' + encodeURIComponent(q.trim())}));
}
