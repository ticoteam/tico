/* ui/app/pages.ts — Pages (docs/pages.md): the card every page shows as (pageCard), the full view at #/page/<id>, a
   task's pages at the top of the task, and Library › Files (#/library/files), every file the reader may see, with
   filters and search. The status line is drawn here, outside the page's frame, so a page cannot fake it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PAGE_ROUTE = '#/page/', RD_FILES = '#/library/files';
// A card shows the top of the page at the card's width and its size's fixed height; full follows the page's own height.
const PAGE_SIZES: Record<string, number> = {small: 180, medium: 320, large: 520};
const PAGE_FULL_MIN = 240;

// ----------------------------------------------------------------- the status line
const pageDay = (iso: string | null | undefined) => { const d = new Date(String(iso) + 'T12:00:00'); return isNaN(Number(d)) ? String(iso) : d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}); };
const pageHref = (id: string) => PAGE_ROUTE + encodeURIComponent(id);
// The bot's name as the rest of the app shows it (duplicates get their slug), else the server's.
// A page names its bot bot_id; a Library file and a search hit name it bot.
const pageBotName = (x: {bot_id?: string | null | undefined; bot?: string | null | undefined; bot_name?: string | null | undefined; [key: string]: unknown}) => {
  const bot = x.bot_id || x.bot;
  return bot && botRow(bot) ? botDisplayName(bot) : x.bot_name || bot || '';
};
// `link: false` inside a card that is itself a link: the replacing page's title without its own link.
const pageLockHTML = (p: {restricted?: boolean | null}) => p.restricted ? '<span class="nav-icon pg-lock" aria-label="Restricted">lock</span> ' : '';
function pageStatusHTML(p: import('../types/api').Page, {link = true}: {link?: boolean} = {}) {
  const lock = pageLockHTML(p);
  if (p.replaced_by_id) {
    const title = esc(p.replaced_by_title || 'a newer page');
    return `<div class="pg-status pg-replaced">${lock}Replaced by ${link ? `<a href="${esc(pageHref(p.replaced_by_id))}">${title}</a>` : title}</div>`;
  }
  const stale = Number.isInteger(p.stale_days);
  const by = String(p.updated_by || '');
  const who = by.startsWith('bot:') ? pageBotName({bot: by.slice(4), bot_name: p.updated_by_name}) : p.updated_by_name || pageBotName(p);
  const parts = [stale ? `Not updated in ${p.stale_days} day${p.stale_days === 1 ? '' : 's'}`
    : `Updated ${esc(ago(p.updated_at))}${who ? ' by ' + esc(who) : ''}`];
  if (Number.isInteger(p.version)) parts.push('v' + p.version);
  if (p.cadence && p.cadence !== 'none') parts.push(esc(p.cadence));
  if (p.as_of) parts.push('data from ' + esc(pageDay(p.as_of)));
  return `<div class="pg-status${stale ? ' pg-stale' : ''}" title="${esc(fmt(p.updated_at))}">${lock}${parts.join(' · ')}</div>`;
}

// ----------------------------------------------------------------- the card
// The approved address a page frames from, or null for an Open card. Tico file hosting's desktop check
// (visualPageSrc, ui/app/visual-cards.ts) applies where that build has it.
// visualPageSrc (ui/app/visual-cards.ts): null when this window can't frame it, e.g. an older desktop app.
const pageSrc = (p: import('../types/api').Page) => p.src ? visualPageSrc(p.src) : null;
// The frame's address says where the page is drawn: the server's height reporter (backend/visuals.py) reads
// `#tico-view=card|full&tico-size=<size>` into <html data-tico-view data-tico-size>, so a page's own CSS can draw a
// compact widget in a card. A fragment changes no signature, cache key or audience check, and follows a redirect.
// A desktop app that checks frame addresses (visualOrigin) refuses a fragment unless it says visualView
// (app/src/visual_origin.rs view_marker): older ones get the bare address, so the page draws, without its card layout.
const pageViewSrc = (src: string, size: string) => {
  const bare = src.split('#')[0] ?? src, features = window.ticoNativeFeatures || [];
  if (features.includes('visualOrigin') && !features.includes('visualView')) return bare;
  return `${bare}#tico-view=${size === 'full' ? 'full' : 'card'}&tico-size=${size}`;
};
// A page frame, with the height and cap a card keeps for it (set by pageCard and the height reporter below).
interface PageFrame extends HTMLIFrameElement { pageCap?: number | undefined; pageHeight?: number | undefined; }
// A card, with the page it draws and its size, kept for the desktop app's repaint (visual-cards.ts reads them).
interface PageCardElement extends HTMLElement { pageData?: import('../types/api').Page; pageSize?: string; }
function pageFrame(p: import('../types/api').Page, src: string, height: number, size: string): PageFrame {
  const f: PageFrame = document.createElement('iframe');
  f.className = 'page-frame';
  // Set before the frame joins the page, as a message's visual page is (VISUAL_PAGE_SANDBOX): the only script it runs
  // is the server's height reporter.
  f.setAttribute('sandbox', VISUAL_PAGE_SANDBOX);
  f.setAttribute('credentialless', '');
  f.referrerPolicy = 'no-referrer';
  // A card is a fixed window onto the page's top: nothing scrolls inside it.
  if (size !== 'full') f.setAttribute('scrolling', 'no');
  f.title = p.title || 'Page';
  f.style.height = height + 'px';
  f.src = pageViewSrc(src, size);
  return f;
}
// pageCard(page, {size}): one card holding the page's status line and the framed page (or an Open card, the status
// under its title, when there is no address). A card is a widget: the page at its own height up to the card's size,
// faded at the bottom when the page runs on, and the whole card one tap that opens the page full size, as its expand icon does.
// `full` is that full size, where the status line sits under the view's own title. `page` is GET
// /api/v2/pages/{id}'s page.
window.pageCard = function pageCard(page: import('../types/api').Page, {size = 'medium'}: {size?: string} = {}) {
  const full = size === 'full', named = full ? 'full' : PAGE_SIZES[size] ? size : 'medium', cap = PAGE_SIZES[named];
  const el: PageCardElement = document.createElement('article');
  el.className = `pg-card pg-${named}`;
  el.dataset.page = page.file_id;
  const src = pageSrc(page);
  if (!src) {
    const open = document.createElement(full ? 'div' : 'a');
    open.className = 'pg-open';
    if (!full && open instanceof HTMLAnchorElement) open.href = pageHref(page.file_id);
    open.innerHTML = `<span class="nav-icon" aria-hidden="true">article</span><div class="pg-open-text"><span class="pg-open-title">${esc(page.title || 'Page')}</span>${full ? '' : pageStatusHTML(page, {link: false})}</div>${full ? '' : '<span class="pg-open-btn">Open</span>'}`;
    if (full) el.innerHTML = pageStatusHTML(page);
    el.append(open);
    el.pageData = page; el.pageSize = size;     // drawn as its frame once the desktop app trusts it (nativeVisualRepaint)
    return el;
  }
  el.classList.add('pg-framed');
  el.innerHTML = pageStatusHTML(page);
  const box = document.createElement('div');
  box.className = 'pg-box';
  // A page measures at least its frame, so a card's frame starts at 1px and the page says its own height; the box
  // keeps the card's size until it does (pg-sized).
  const frame = pageFrame(page, src, full ? PAGE_FULL_MIN : 1, named);
  frame.pageCap = full ? 0 : cap;
  box.append(frame);
  el.append(box);
  if (!full) {
    const href = pageHref(page.file_id);
    const tap = document.createElement('a');
    tap.className = 'pg-tap'; tap.href = href;
    tap.setAttribute('aria-label', 'Open ' + (page.title || 'page'));
    const expand = document.createElement('a');
    expand.className = 'pg-expand'; expand.href = href;
    expand.setAttribute('aria-label', 'Open full screen'); expand.title = 'Open full screen';
    expand.innerHTML = '<span class="nav-icon" aria-hidden="true">open_in_full</span>';
    el.append(tap, expand);
  }
  return el;
};
// The height a page reports (backend/visuals.py REPORTER), from a page frame's own window only. A card takes the page's
// height up to its size and fades its bottom when the page is taller; the full view takes the height whole.
window.addEventListener('message', ev => {
  const n = ev.data && typeof ev.data === 'object' ? ev.data.ticoVisualHeight : undefined;
  if (typeof n !== 'number' || !Number.isFinite(n) || !ev.source) return;
  const frame = [...document.querySelectorAll<PageFrame>('iframe.page-frame')].find(x => x.contentWindow === ev.source);
  if (!frame) return;
  const h = frame.pageHeight = Math.max(24, Math.min(Math.ceil(n), VISUAL_PAGE_MAX));
  if (frame.pageCap) {
    const box = frame.parentElement;
    if (!box) return;
    // A page sized by the screen (100vh) says next to nothing from its 1px frame: it fills the card.
    const fills = !box.classList.contains('pg-sized') && Math.ceil(n) < 24;
    frame.style.height = (fills ? frame.pageCap : Math.min(h, frame.pageCap)) + 'px';
    box.classList.add('pg-sized');
    box.classList.toggle('pg-cut', !fills && h > frame.pageCap);
  }
  else frame.style.height = Math.max(h, PAGE_FULL_MIN) + 'px';
});

// ----------------------------------------------------------------- the full view, #/page/<id>
// One header line (back, title, status line, ⋯), then the page on the view's own surface with no box around it. The
// ⋯ menu holds who can see it, What's changed?, Version history, Archive, Download and Delete for good.
// A page's version as GET /api/v2/files/{id}/versions lists it (newest first).
interface PageVersion { version: number; created_at?: string | null; actor?: string | null; }
// The full view's state: the page as read, its versions, the version on screen, what the reader may do, and the
// version What's changed? compares against (null when the page is shown).
interface PageFullState {
  id: string;
  page: import('../types/api').Page | null;
  versions: PageVersion[];
  shown: number;
  can: {manage?: boolean; del?: boolean};
  pinned?: {label: string; place: string}[];
  diff?: number | null;
  menu?: 'history' | null;
}
let PG: PageFullState | null = null;          // {id, page, versions, shown}
async function pageFullView(id: string, at = 0) {
  const main = $('#main');
  main.innerHTML = `<div class="pg-view" id="pg-view">${skelRows(3, {card: true})}</div>`;
  const state: PageFullState = PG = {id, page: null, versions: [], shown: 0, can: {}, diff: null};
  let read: import('../types/api').GetPageResponse;
  try { read = await get<import('../types/api').GetPageResponse>('/v2/pages/' + encodeURIComponent(id), {fresh: true}); }
  catch (e) {
    if (PG !== state) return;
    $('#pg-view').innerHTML = `<p class="muted pg-pad">${esc(errorStatus(e) === 404 ? 'This page is gone, or not yours to see.' : errorMessage(e, ''))}</p>`;
    return;
  }
  if (PG !== state) return;
  state.page = read.page; state.can = {manage: !!read.can_manage, del: !!read.can_delete};
  state.pinned = read.pinned || [];
  // #/page/<id>?v=<n> (the decision view's review history) opens that version; past the newest, the newest.
  state.shown = at >= 1 && at <= read.page.version ? at : read.page.version;
  pageFullDraw(state);
  get<{versions?: PageVersion[]}>(`/v2/files/${encodeURIComponent(id)}/versions`).then(v => {
    if (PG !== state) return;
    state.versions = [...(v.versions || [])].sort((a, b) => b.version - a.version);
    pageFullHead(state);
  }).catch(() => {});
}
function pageFullDraw(state: PageFullState) {
  const root = $('#pg-view');
  if (!root || !state.page || !state.pinned) return;
  const pinned = state.pinned.map(x => `<a href="${esc(pagePlaceHref(x.place))}">${esc(x.label)}</a>`).join(' · ');
  root.innerHTML = `<header class="pg-head"></header><div class="pg-body"></div>${pinned ? `<p class="pg-pinned">Pinned to ${pinned}</p>` : ''}`;
  root.onkeydown = pageMenuKeys;
  pageFullHead(state);
  pageFullBody(state);
}
const pagesAudienceText = (audience: import('../types/api').Page['audience']) => !audience || audience.everyone ? "Everyone who can see where it's filed" : accessLevelText(audience);
async function pagesEditAudience(state: PageFullState) {
  if (!state.page) return;
  try {
    const result = await get('/v2/groups');
    const level = state.page.audience || accessEveryone();
    await accessEditor({name: state.page.title, singleAudience: true,
      initialData: {see: level, read: level, write: level, teams: Array.isArray(result) ? result : result.groups || []},
      save: (audience: import('../types/api').AccessAudience) => patch('/v2/pages/' + encodeURIComponent(state.id), {audience: audience.everyone ? null : audience}),
      after: (saved: SaSaved) => { if (PG === state) { state.page = saved.page as import('../types/api').Page; pageFullHead(state); } }});
  } catch (error) { toast(errorMessage(error, ''), true); }
}
function pagesAccessRefresh() {
  getForget();
  // Remove the held content immediately; each open surface reads again with current permissions.
  document.querySelectorAll('.pg-card, .vcard[data-kind="page"]').forEach(card => card.remove());
  for (const entry of VIEW_FILES.values()) {
    if (entry.url) URL.revokeObjectURL(entry.url);
    else entry.promise.then((item: {url: string}) => URL.revokeObjectURL(item.url)).catch(() => {});
  }
  VIEW_FILES.clear();
  if (VIEW?.items.some(item => item.fileId && item.kind === 'html')) $('#doc-viewer')?.close();
  if (PG) void pageFullView(PG.id);
  if (LF) void lfLoad();
  if (TASK_CHAT) {
    const dialog: TaskPagesHost = TASK_CHAT.dialog;
    if (dialog.tpEl) { dialog.tpEl.tpCards = new Map(); void taskPagesLoad(dialog); }
    taskChatLive(TASK_CHAT);
  }
  if (!PG && !LF && !TASK_CHAT) void route();
}
// A pin's place (backend/cards.py) as the app's address.
function pagePlaceHref(place: string) {
  const [kind, ...rest] = String(place).split(':'), id = encodeURIComponent(rest.join(':'));
  return kind === 'dashboard' ? OVERVIEW : kind === 'growth' ? '#/growth' : kind === 'person' ? HOME : kind === 'team' ? '#/team/' + id : kind === 'bot' ? '#/bot/' + id : '#/task/' + id;
}
// Who published a version, as the rest of the app names them.
const pageVersionBy = (v: PageVersion) => v.actor ? actorLabel(v.actor) : '';
// The status line, on the header line: the page's own, or the older version on screen with a way back to the latest.
function pageFullStatusHTML(state: PageFullState) {
  const p = state.page;
  if (!p) return '';
  if (state.shown === p.version) return pageStatusHTML(p);
  return `<div class="pg-status">${pageLockHTML(p)}v${state.shown} · an older version · <button type="button" class="linkish" data-pg-act="latest">Latest</button></div>`;
}
function pageFullMenuHTML(state: PageFullState) {
  const p = state.page;
  if (!p) return '';
  const item = (act: string, label: string, extra = '') => `<button type="button" role="menuitem" data-pg-act="${act}"${extra}>${label}</button>`;
  if (state.menu === 'history') return item('menu', '<span class="nav-icon" aria-hidden="true">chevron_left</span><span class="pg-mi-name">Version history</span>', ' class="pg-mi-back"')
    + state.versions.map(v => {
      const by = pageVersionBy(v), day = v.created_at ? pageDay(v.created_at.slice(0, 10)) : '';
      return `<button type="button" role="menuitemradio" aria-checked="${v.version === state.shown}" data-pg-act="version" data-pg-v="${v.version}">`
        + `<span class="pg-mi-name">v${v.version}${v.version === p.version ? ' · latest' : ''}</span><span class="pg-mi-sub">${esc([day, by].filter(Boolean).join(' · '))}</span></button>`;
    }).join('');
  const many = state.versions.length > 1;
  return [
    state.can.manage ? item('access', `<span class="pg-mi-name">Who can see it</span><span class="pg-mi-sub">${esc(pagesAudienceText(p.audience))}</span>`) : '',
    // The oldest version has nothing before it to compare with.
    pageDiffBases(state).length ? item('diff', "What's changed?") : '',
    many ? item('history', 'Version history') : '',
    state.can.manage ? item('archive', p.archived ? 'Restore' : 'Archive') : '',
    `<a role="menuitem" href="${API}/v2/files/${encodeURIComponent(p.file_id)}?v=${state.shown}" download>Download</a>`,
    state.can.del ? item('delete', 'Delete for good', ' class="danger"') : ''].join('');
}
function pageFullHead(state: PageFullState) {
  const p = state.page, head = $('#pg-view .pg-head');
  if (!head || !p) return;
  const open = head.querySelector('.pg-menu')?.hasAttribute('open');
  head.innerHTML = `<button type="button" class="ghost pg-back" data-pg-act="back" aria-label="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></button>
    <div class="pg-headline"><h1 class="pg-title">${esc(p.title)}</h1>${p.archived ? '<span class="pill pg-archived">Archived</span>' : ''}${pageFullStatusHTML(state)}</div>
    <details class="pg-menu"${open ? ' open' : ''}><summary aria-label="More" title="More" aria-haspopup="menu"><span class="nav-icon" aria-hidden="true">more_horiz</span></summary><div class="pg-menu-list" role="menu">${pageFullMenuHTML(state)}</div></details>`;
  head.onclick = (ev: MouseEvent) => { const b = eventElement(ev)?.closest<HTMLElement>('[data-pg-act]'); if (b) void pageFullAct(state, b.dataset.pgAct, b); };
  const menu = head.querySelector('.pg-menu') as HTMLDetailsElement | null;
  if (menu) menu.ontoggle = () => {
    if (menu.open) { pageMenuItems(menu)[0]?.focus(); return; }
    if (state.menu) { state.menu = null; pageFullHead(state); }
  };
}
const pageMenuItems = (menu: Element) => [...menu.querySelectorAll<HTMLElement>('.pg-menu-list [role^=menuitem]')];
function pageMenuClose(state: PageFullState, focus = false) {
  const menu = document.querySelector<HTMLDetailsElement>('#pg-view .pg-menu');
  if (!menu?.open) return false;
  state.menu = null;
  menu.removeAttribute('open');
  pageFullHead(state);
  if (focus) document.querySelector<HTMLElement>('#pg-view .pg-menu summary')?.focus();
  return true;
}
// The arrows, Home and End move through the ⋯ menu's items (Esc: the document handler below).
function pageMenuKeys(ev: KeyboardEvent) {
  const menu = eventElement(ev)?.closest('.pg-menu');
  if (!menu || !(menu as HTMLDetailsElement).open || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(ev.key)) return;
  ev.preventDefault();
  const items = pageMenuItems(menu), at = items.indexOf(document.activeElement as HTMLElement);
  const to = ev.key === 'Home' ? 0 : ev.key === 'End' ? items.length - 1 : ev.key === 'ArrowDown' ? at + 1 : at < 0 ? items.length - 1 : at - 1;
  items[Math.max(0, Math.min(items.length - 1, to))]?.focus();
}
// Esc wherever the focus is (a click on the diff's text leaves it on the body), unless a dialog over the view takes it.
document.addEventListener('keydown', ev => {
  const state = PG;
  if (ev.key !== 'Escape' || ev.defaultPrevented || !state || !document.getElementById('pg-view') || document.querySelector('dialog[open]')) return;
  if (pageMenuClose(state, true) || (state.diff != null && (pageDiffClose(state), true))) ev.preventDefault();
});
// The current version frames from its address; an older one, or any without an address, shows in the file viewer's
// sandboxed preview (ui/app/viewer.ts), which looks the same: pages run no script. The status line is the header's,
// and the page sits on the view's surface (pg-bare): no border, corner or white backdrop around it.
async function pageFullBody(state: PageFullState) {
  const p = state.page, body = $('#pg-view .pg-body');
  if (!body || !p) return;
  if (state.diff != null) { void pageDiffDraw(state); return; }
  const current = state.shown === p.version;
  const card = window.pageCard(p, {size: 'full'});
  card.classList.add('pg-bare');
  card.querySelector(':scope > .pg-status')?.remove();
  if (current && card.querySelector('iframe')) { body.replaceChildren(card); return; }
  card.querySelector('.pg-open')?.remove();
  delete card.pageData;                         // this view draws its own frame again (nativeVisualRepaint)
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
  } catch (e) { if (box.isConnected) box.innerHTML = `<p class="muted pg-pad">${esc(errorMessage(e, ''))}</p>`; }
}

// ----------------------------------------------------------------- What's changed?
// A page's visible text, one line per heading, paragraph, list item, table row or other block, for a line diff
// (tfDiff, ui/app/task-files.ts). DOMParser runs no script; scripts, styles and hidden elements say nothing.
const PAGE_TEXT_BLOCKS = new Set(['ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'BR', 'CAPTION', 'DD', 'DETAILS', 'DIV', 'DL', 'DT',
  'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI', 'MAIN', 'NAV', 'OL',
  'P', 'PRE', 'SECTION', 'SUMMARY', 'TABLE', 'TBODY', 'TFOOT', 'THEAD', 'TR', 'UL', 'svg', 'text']);
// An SVG's <title> is a tooltip, not text on the page.
const PAGE_TEXT_SKIP = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT', 'HEAD', 'IFRAME', 'OBJECT', 'title']);
function pageText(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return doc.body ? pageTextLines(doc.body).join('\n') : '';
}
/** The lines pageText reads from one node and what is in it. */
function pageTextLines(root: Node): string[] {
  const lines: string[] = [];
  let line = '';
  const flush = () => { const l = line.replace(/\s+/g, ' ').trim(); if (l) lines.push(l); line = ''; };
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) { line += node.textContent || ''; return; }
    if (!(node instanceof Element) || PAGE_TEXT_SKIP.has(node.tagName) || node.hasAttribute('hidden') || node.getAttribute('aria-hidden') === 'true') return;
    const block = PAGE_TEXT_BLOCKS.has(node.tagName), cell = node.tagName === 'TD' || node.tagName === 'TH';
    if (block) flush();
    if (cell && line.trim()) line += ' | ';
    for (const child of node.childNodes) walk(child);
    if (block) flush();
  };
  walk(root);
  flush();
  return lines;
}
// Each heading, paragraph, list item or table row of a page whose text is not in an earlier version's lines (pageText)
// gets a 3 px amber bar in its left margin: the decision view's "Changes since vK" switch, off. The innermost block is
// marked, so a changed list item marks that item, not its whole list. `marked`: how many blocks.
const PAGE_MARK_BLOCKS = 'h1,h2,h3,h4,h5,h6,p,li,tr,dt,dd,blockquote,pre,figcaption,caption,summary';
const PAGE_MARK_STYLE = '[data-tico-changed]{position:relative}[data-tico-changed]::before{content:"";position:absolute;'
  + 'left:-6px;top:0;bottom:0;width:3px;border-radius:2px;background:#d97706}';
function pageMarkChanged(html: string, before: string[]): {html: string; marked: number} {
  const doc = new DOMParser().parseFromString(html, 'text/html'), had = new Set(before);
  const blocks = [...doc.querySelectorAll<HTMLElement>(PAGE_MARK_BLOCKS)].reverse();   // innermost first
  let marked = 0;
  for (const el of blocks) {
    if (el.querySelector('[data-tico-changed]') || !pageTextLines(el).some(l => !had.has(l))) continue;
    el.setAttribute('data-tico-changed', ''); marked++;
  }
  if (!marked) return {html, marked};
  const style = doc.createElement('style');
  style.textContent = PAGE_MARK_STYLE;
  doc.head.append(style);
  return {html: (doc.doctype ? '<!DOCTYPE html>' : '') + doc.documentElement.outerHTML, marked};
}
// The versions What's changed? may compare against: those before the one on screen.
const pageDiffBases = (state: PageFullState) => state.versions.filter(v => v.version < state.shown);
function pageDiffOpen(state: PageFullState) {
  const base = pageDiffBases(state)[0];
  if (!base) return;
  state.diff = base.version;
  pageFullBody(state);
}
function pageDiffClose(state: PageFullState) {
  state.diff = null;
  pageFullBody(state);
  document.querySelector<HTMLElement>('#pg-view .pg-menu summary')?.focus();
}
async function pageDiffDraw(state: PageFullState) {
  const p = state.page, body = $('#pg-view .pg-body'), from = state.diff;
  if (!body || !p || from == null) return;
  const shown = body.querySelector('.pg-diff') as HTMLElement | null;
  if (!shown || shown.dataset.to !== String(state.shown)) pageDiffShell(state, body, from);
  const out = body.querySelector('.pg-diff-lines') as HTMLElement | null;
  if (!out) return;
  out.setAttribute('aria-busy', 'true');
  try {
    const [a, b] = await Promise.all([from, state.shown].map(async v => pageText(await (await viewFile(p.file_id, p.title + '.html', v)).blob.text())));
    if (PG !== state || state.diff !== from || !out.isConnected) return;
    out.removeAttribute('aria-busy');
    out.innerHTML = a === b ? '<p class="muted pg-pad">No text changes</p>' : tfDiffHTML(a ?? '', b ?? '');   // strict index access types a destructured list item as possibly undefined
  } catch (e) { if (out.isConnected) { out.removeAttribute('aria-busy'); out.innerHTML = `<p class="muted pg-pad">${esc(errorMessage(e, ''))}</p>`; } }
}
// The diff's header: What's changed?, the version to compare from, → the one on screen, and Close.
function pageDiffShell(state: PageFullState, body: HTMLElement, from: number) {
  const options = pageDiffBases(state).map(v => `<option value="${v.version}"${v.version === from ? ' selected' : ''}>v${v.version}</option>`).join('');
  body.innerHTML = `<section class="pg-diff" data-to="${state.shown}" aria-label="What's changed?"><header class="pg-diff-head"><h2>What's changed?</h2>
    <select class="pg-diff-from" aria-label="Compare from">${options}</select><span aria-hidden="true">→</span><span class="pg-diff-to">v${state.shown}</span>
    <span class="spacer"></span><button type="button" class="ghost" data-pg-diff-close>Close</button></header>
    <div class="pg-diff-lines">${skelRows(4)}</div></section>`;
  const sel = body.querySelector('.pg-diff-from') as HTMLSelectElement | null;
  if (sel) sel.onchange = () => { state.diff = Number(sel.value); void pageDiffDraw(state); };
  const close = body.querySelector('[data-pg-diff-close]') as HTMLElement | null;
  if (close) close.onclick = () => pageDiffClose(state);
}
async function pageFullAct(state: PageFullState, act: string | undefined, el?: HTMLElement) {
  const p = state.page;
  if (act === 'menu' || act === 'history') {
    state.menu = act === 'history' ? 'history' : null;
    pageFullHead(state);
    const menu = document.querySelector('#pg-view .pg-menu');
    if (menu) pageMenuItems(menu)[0]?.focus();
    return;
  }
  pageMenuClose(state);
  if (act === 'back') { if (history.length > 1) history.back(); else location.hash = RD_FILES; return; }
  if (!p) return;
  if (act === 'access') { void pagesEditAudience(state); return; }
  if (act === 'version' || act === 'latest') {
    state.shown = act === 'latest' ? p.version : Number(el?.dataset.pgV);
    state.diff = null;
    pageFullHead(state); pageFullBody(state);
    return;
  }
  if (act === 'diff') { pageDiffOpen(state); return; }
  try {
    if (act === 'archive') {
      const r: import('../types/api').EditPageResponse = await patch('/v2/pages/' + encodeURIComponent(p.file_id), {archived: !p.archived});
      if (PG !== state) return;
      state.page = r.page;
      pageFullHead(state);
      toast(r.page.archived ? 'Archived' : 'Restored');
    } else if (act === 'delete') {
      if (!(await askConfirm(`Delete “${p.title}” and every version for good? This cannot be undone.`))) return;
      await writeRequest('DELETE', '/v2/files/' + encodeURIComponent(p.file_id));
      toast('Deleted');
      location.hash = RD_FILES;
    }
  } catch (e) { toast(errorMessage(e, ''), true); }
}

// ----------------------------------------------------------------- a task's pages
// A pinned page row of the task's section, kept across redraws while its key holds.
interface TaskPagesRow { key: string; el: HTMLElement; }
// POST and GET make-page's status (backend/tasks.py): a page being made in a chat, or the last try.
interface MakePageStatus { state: 'making' | 'made' | 'failed' | 'update_required'; conversation_id?: string; }
// The section's element, with the state this file keeps on it.
interface TaskPagesSlot extends HTMLDivElement {
  tpSending?: boolean;
  tpBusy?: boolean;
  tpMaking?: MakePageStatus | null;
  tpMakeSeq?: number;
  tpSeq?: number;
  tpCards?: Map<string | null, TaskPagesRow>;
  tpFollow?: (() => void) | null;
  tpFollowCid?: string | null | undefined;
}
// The task's dialog, which holds its section (tpEl) and the release of its live handlers (tpOff).
interface TaskPagesHost extends HTMLDialogElement { tpEl?: TaskPagesSlot | null; tpOff?: (() => void) | null; }
const taskMakeDraftKey = (id: string) => 'tico.task.make-page.' + encodeURIComponent(String(S.me?.id || '')) + '.' + id;
// A live event the task's pages react to: a files change on the task, or a run of the page being made.
interface TaskPagesLiveEvent { kind?: string; kinds?: string[]; id?: unknown; conversation_id?: string; }
// A pin on a task's place: its page is the page as GET /api/v2/places/{place} lists it, with the replaced and archived flags.
interface TaskPlacePin { id: string | null; size?: string; can_unpin?: boolean; page?: import('../types/api').Page | null; }
interface TaskPlaceRead { can_link?: boolean; pins?: TaskPlacePin[]; }
// The pages pinned to a task, as cards at its top. The section moves into each redraw of the task (as its files do,
// tfAdopt), so a loaded frame is not loaded again.
function taskPagesAdopt(d: TaskPagesHost, task: {id?: unknown}) {
  const slot = $('[data-task-pages]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tpEl && d.tpEl.dataset.task === id) { slot.replaceWith(d.tpEl); return; }
  d.tpOff?.();
  d.tpEl = slot; slot.dataset.task = id;
  slot.innerHTML = `<header class="tp-head"><span class="lbl">Pages</span><div class="tp-actions"><button type="button" class="ghost" data-tp-link hidden>Link page</button></div></header>
    <div class="tp-picker" hidden><div class="tp-search"><input type="search" placeholder="Search pages" aria-label="Search pages" autocomplete="off">
      <button type="button" class="ghost" data-tp-close aria-label="Close page picker">Close</button></div><div class="tp-results" aria-live="polite"></div></div>
    <form class="tp-make" hidden>${ldotHTML({state: 'still', size: 20})}<input type="text" aria-label="Describe the page" placeholder="Describe the page" maxlength="8000" required><button type="submit" class="primary">Make page</button></form>
    <p class="tp-error err" role="alert" hidden></p><div class="tp-making" role="status" hidden></div><div class="tp-pages"></div>`;
  const make = $('.tp-make', slot), prompt = $('input', make), error = $('.tp-error', slot);
  try { prompt.value = localStorage.getItem(taskMakeDraftKey(id)) || ''; } catch { /* storage may be unavailable */ }
  prompt.oninput = () => { try { if (prompt.value) localStorage.setItem(taskMakeDraftKey(id), prompt.value); else localStorage.removeItem(taskMakeDraftKey(id)); } catch { /* storage may be unavailable */ } };
  make.onsubmit = async (ev: Event) => {
    ev.preventDefault();
    if (slot.tpSending || ['making', 'update_required'].includes(slot.tpMaking?.state) || !prompt.value.trim()) return;
    slot.tpSending = true; $('button', make).disabled = true; error.hidden = true;
    try {
      const r = await post('/v2/tasks/' + encodeURIComponent(id) + '/make-page', {text: prompt.value.trim()});
      if (d.tpEl !== slot) return;
      if (r.held) throw new Error('Not sent');
      make.reset();
      try { localStorage.removeItem(taskMakeDraftKey(id)); } catch { /* storage may be unavailable */ }
      taskPageMakeDraw(d, {state: 'making', conversation_id: r.message.conversation_id});
      await taskPageMakeLoad(d);
    } catch (e) { if (d.tpEl === slot) { error.textContent = errorMessage(e, ''); error.hidden = false; } }
    finally { slot.tpSending = false; $('button', make).disabled = ['making', 'update_required'].includes(slot.tpMaking?.state); }
  };
  const picker = $('.tp-picker', slot), input = $('input', picker), results = $('.tp-results', picker);
  $('[data-tp-link]', slot).onclick = () => { picker.hidden = false; input.focus(); };
  $('[data-tp-close]', slot).onclick = () => { picker.hidden = true; $('[data-tp-link]', slot).focus(); };
  let soon = 0, search = 0;
  input.oninput = () => {
    clearTimeout(soon);
    const seq = ++search, q = input.value.trim();
    results.replaceChildren();
    if (!q) return;
    soon = setTimeout(async () => {
      try {
        const r = await get<import('../types/api').FileSearch>('/v2/files/search?' + new URLSearchParams({q, kind: 'page'}), {fresh: true});
        if (d.tpEl !== slot || seq !== search) return;
        results.innerHTML = (r.results || []).map(p => `<button type="button" class="tp-result" data-tp-page="${esc(p.id)}">${esc(p.title)}</button>`).join('') || '<p class="muted">No pages</p>';
      } catch (e) { if (d.tpEl === slot && seq === search) results.innerHTML = `<p class="err">${esc(errorMessage(e, ''))}</p>`; }
    }, 250);
  };
  slot.onclick = async (ev: MouseEvent) => {
    const link = eventElement(ev)?.closest<HTMLButtonElement>('[data-tp-page]'), unlink = eventElement(ev)?.closest<HTMLButtonElement>('[data-tp-unlink]');
    if ((!link && !unlink) || slot.tpBusy) return;
    slot.tpBusy = true;
    const button = link || unlink;
    if (!button) return;
    button.disabled = true;
    try {
      if (link) await post('/v2/pins', {place: 'task:' + id, item: 'page:' + link.dataset.tpPage});
      else await writeRequest('DELETE', '/v2/pins/' + encodeURIComponent(String(unlink?.dataset.tpUnlink)));
      if (d.tpEl !== slot) return;
      if (link) { picker.hidden = true; input.value = ''; results.replaceChildren(); ++search; }
      await taskPagesLoad(d);
    } catch (e) { toast(errorMessage(e, ''), true); }
    finally { slot.tpBusy = false; button.disabled = false; }
  };
  const refresh = () => { if (d.open && d.tpEl === slot) void taskPagesLoad(d); };
  const offs = [liveOn('tasks', (e: TaskPagesLiveEvent) => {
    if (d.open && d.tpEl === slot && (e.kinds || [e.kind]).includes('files') && String(e.id) === id)
      liveSoon('task-pages:' + d.id + ':' + id, refresh, 200);
  }), liveOn('runs', (e: TaskPagesLiveEvent) => {
    if (['making', 'update_required'].includes(slot.tpMaking?.state) && e.conversation_id === slot.tpMaking.conversation_id)
      liveSoon('task-pages:' + d.id + ':' + id, refresh, 200);
  }), liveOn('reset', refresh)];
  const poll = liveFallback(() => { if (['making', 'update_required'].includes(slot.tpMaking?.state)) refresh(); }, 5000);
  d.tpOff = () => { offs.forEach(off => off()); slot.tpFollow?.(); clearInterval(poll); };
  void taskPagesLoad(d);
}
function taskPageMakeDraw(d: TaskPagesHost, status: MakePageStatus | null) {
  const slot = d.tpEl;
  if (!slot) return;
  const pending = ['making', 'update_required'].includes(status?.state ?? '');
  const cid = pending ? status?.conversation_id : null;
  if (slot.tpFollowCid !== cid) {
    slot.tpFollow?.(); slot.tpFollow = cid ? liveFollow(cid) : null; slot.tpFollowCid = cid;
  }
  slot.tpMaking = status;
  const card = $('.tp-making', slot);
  card.hidden = !status || status.state === 'made';
  card.innerHTML = status?.state === 'making' ? 'Making page…' : status?.state === 'update_required'
    ? 'Update this computer to make pages · <button type="button" class="ghost" data-tp-retry>Retry</button>'
    : 'Not made · <a href="#/assistant">Open chat</a>';
  const retry = $('[data-tp-retry]', card);
  if (retry) retry.onclick = async () => {
    retry.disabled = true;
    try { await taskPageMakeLoad(d); }
    catch (e) { toast(errorMessage(e, ''), true); }
    finally { retry.disabled = false; }
  };
  $('button', $('.tp-make', slot)).disabled = slot.tpSending || pending;
}
async function taskPageMakeLoad(d: TaskPagesHost) {
  const slot = d.tpEl;
  if (!slot) return;
  const seq = slot.tpMakeSeq = (slot.tpMakeSeq || 0) + 1;
  const r = await get<import('../types/api').TaskMakePage>('/v2/tasks/' + encodeURIComponent(String(slot.dataset.task)) + '/make-page', {fresh: true});
  if (d.tpEl === slot && seq === slot.tpMakeSeq) taskPageMakeDraw(d, r.make_page);
}
async function taskPagesLoad(d: TaskPagesHost) {
  const slot = d.tpEl;
  if (!slot) return;
  const seq = slot.tpSeq = (slot.tpSeq || 0) + 1;
  try {
    const r = await get<TaskPlaceRead>('/v2/places/' + encodeURIComponent('task:' + slot.dataset.task), {fresh: true});
    if (d.tpEl !== slot || seq !== slot.tpSeq) return;
    const pins = (r.pins || []).filter((x): x is TaskPlacePin & {page: import('../types/api').Page} => !!x.page && !x.page.replaced_by_id && !x.page.archived);
    slot.hidden = !r.can_link && !pins.length;
    $('[data-tp-link]', slot).hidden = !r.can_link;
    $('.tp-make', slot).hidden = !r.can_link;
    const kept = slot.tpCards || new Map<string | null, TaskPagesRow>(), cards = new Map<string | null, TaskPagesRow>();
    for (const pin of pins) {
      const key = JSON.stringify([pin.page, pin.size, pin.can_unpin]);
      let row = kept.get(pin.id);
      if (!row || row.key !== key) {
        const el = document.createElement('div'); el.className = 'tp-page';
        el.append(window.pageCard(pin.page, {size: pin.size || 'medium'}));
        if (pin.can_unpin) {
          const b = document.createElement('button'); b.type = 'button'; b.className = 'ghost tp-unlink';
          b.dataset.tpUnlink = String(pin.id); b.textContent = 'Unlink'; b.setAttribute('aria-label', 'Unlink ' + pin.page.title);
          el.append(b);
        }
        row = {key, el};
      }
      cards.set(pin.id, row);
    }
    slot.tpCards = cards;
    $('.tp-pages', slot).replaceChildren(...[...cards.values()].map(x => x.el));
    if (r.can_link) await taskPageMakeLoad(d);
  } catch (e) { if (d.tpEl === slot) toast(errorMessage(e, ''), true); }
}

// ----------------------------------------------------------------- Library › Files
// A table across the page on a computer (name, kind, made by, team, updated) and two lines a row on a phone. The search
// and the filter button share the top row; the filters sit in a popover (the Tasks popover's classes) and show as chips
// under the row. Every filter rides in the address, so a view can be shared.
const LF_KINDS = [['', 'All kinds'], ['page', 'Pages'], ['document', 'Documents'], ['image', 'Images'], ['data', 'Data'],
  ['media', 'Video and audio'], ['other', 'Other']];
const LF_ICON: Record<string, string> = {page: 'article', document: 'description', image: 'palette', data: 'analytics', media: 'play_arrow', other: 'inventory_2'};
const LF_DATES = [['', 'Any time'], ['7', 'Past week'], ['30', 'Past month'], ['365', 'Past year']];
const LF_FILTERS = ['q', 'kind', 'bot', 'team', 'task', 'days', 'pinned', 'archived'];
const LF_COUNTED = ['kind', 'bot', 'team', 'task', 'days', 'pinned', 'archived'];   // the filter button's count and the chips (not the search)
interface LfGroup { id: string; name: string }
// Every filter is always present: '' (or false) when off. lfQuery writes them to the address and back.
interface LfFilter { q: string; kind: string; bot: string; team: string; task: string; days: string; pinned: boolean; archived: boolean; [k: string]: string | boolean | undefined }
// A file row of the Files list or a search hit, as the rows read it (`hit` marks a search hit).
type LfRow = { id: string; title: string; name?: string; library_kind?: string; bot?: string; bot_name?: string; locator?: string; hit?: boolean; url?: string | null; open?: {url?: string} | null; last_activity_at?: string | null; updated_at?: string; pins?: unknown[]; archived?: boolean; excerpt?: string; };
interface LfState { root: HTMLElement; pop: HTMLElement; filter: LfFilter; rows: LfRow[]; cursor: string | null; seq: number; groups: LfGroup[] }
// The open Files page's state; null until it opens, and again once another page is shown.
let LF: LfState | null = null;
const lfTeamName = (id: string | undefined) => id ? (LF?.groups || []).find((g: LfGroup) => g.id === id)?.name || teamLabel(id) : '';
function lfQuery(filter: LfFilter) {
  const q = new URLSearchParams();
  for (const k of LF_FILTERS) if (filter[k]) q.set(k, filter[k] === true ? '1' : filter[k]);
  const s = q.toString();
  return s ? '?' + s : '';
}
function lfApiQuery(f: LfFilter, cursor?: string | null) {
  const q = new URLSearchParams({limit: '50'});
  for (const k of ['kind', 'bot', 'team', 'task']) if (f[k]) q.set(k, String(f[k]));
  if (f.days) q.set('since', new Date(Date.now() - Number(f.days) * 864e5).toISOString());
  if (f.pinned) q.set('pinned', 'true');
  if (f.archived) q.set('archived', 'true');
  if (cursor) q.set('cursor', cursor);
  return q.toString();
}
async function pageLibraryFiles() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const filter = Object.fromEntries(LF_FILTERS.map(k => [k, ['pinned', 'archived'].includes(k) ? params.get(k) === '1' : params.get(k) || ''])) as LfFilter;
  if (filter.bot) filter.bot = botByRef(filter.bot)?.id || filter.bot;     // an old link names the bot by slug
  const same = LF && document.body.contains(LF.root);
  if (!same) {
    $('#main').innerHTML = `<div class="lf" id="lf-root">
      <header class="lf-head"><h1>Files</h1>
        <div class="lf-search">${TL_ICON.search}<input class="lf-q" type="search" placeholder="Search" aria-label="Search files" autocomplete="off" spellcheck="false"></div>
        <button type="button" class="lf-fbtn" popovertarget="lf-filter-pop" aria-haspopup="true" aria-expanded="false" aria-label="Filters" title="Filters">${filterIcon(16)}<span class="lf-fbn tnum" hidden></span></button>
        <div class="tl-pop lf-pop" id="lf-filter-pop" popover></div></header>
      <div class="lf-bar" hidden></div>
      <div class="lf-table"><div class="lf-cols" aria-hidden="true"><span class="lf-h"></span><span class="lf-h">Name</span><span class="lf-h lf-kind">Kind</span><span class="lf-h lf-by">Made by</span><span class="lf-h lf-team">Team</span><span class="lf-h lf-when">Updated</span></div>
        <div class="lf-list" aria-live="polite">${skelRows(6)}</div></div></div>`;
    LF = {root: $('#lf-root'), pop: $('#lf-filter-pop'), filter, rows: [], cursor: null, seq: 0, groups: []};
    lfWire();
    get('/v2/groups').then(g => { if (!LF) return; LF.groups = Array.isArray(g) ? g : g?.groups || []; lfControls(); }).catch(() => {});
  } else if (LF) LF.filter = filter;
  lfControls();
  await lfLoad();
}
// The count, the chips and the popover's ticks follow the filters; the search box keeps what is being typed.
function lfControls() {
  const state = LF;
  if (!state) return;
  const root = state.root, f = state.filter, q = root.querySelector<HTMLInputElement>('.lf-q'), btn = root.querySelector<HTMLElement>('.lf-fbtn'), badge = btn?.querySelector<HTMLElement>('.lf-fbn');
  if (!q || !btn || !badge) return;
  if (document.activeElement !== q) q.value = f.q;
  const n = LF_COUNTED.filter(k => f[k]).length;
  btn.classList.toggle('active', !!n);
  badge.hidden = !n; badge.textContent = n ? String(n) : '';
  btn.setAttribute('aria-label', n ? `Filters, ${n} active` : 'Filters');
  lfBarPaint();
  lfPopSync();
}
function lfBarPaint() {
  const state = LF;
  if (!state) return;
  const f = state.filter, bar = state.root.querySelector<HTMLElement>('.lf-bar');
  if (!bar) return;
  // A chip's name opens the filter popover (its own popovertarget); its ✕ takes the filter off.
  const chip = (key: string, label: string, value: string) => `<span class="tl-chip"><button type="button" class="tl-chip-main" popovertarget="lf-filter-pop" aria-haspopup="true"`
    + ` aria-label="${esc(`${label ? label + ': ' : ''}${value}. Change`)}" title="${esc(label ? `${label}: ${value}` : value)}">`
    + `${label ? `<span class="k">${esc(label)}</span>` : ''}<span class="v">${esc(value)}</span></button>`
    + `<button type="button" class="tl-chip-x" data-lf-clear="${key}" aria-label="${esc(`Remove ${label || value} filter`)}">${TL_ICON.x}</button></span>`;
  const chips = [
    f.kind && chip('kind', 'Kind', LF_KINDS.find(([v]) => v === f.kind)?.[1] || f.kind),
    f.bot && chip('bot', 'Bot', botDisplayName(f.bot)),
    f.team && chip('team', 'Team', lfTeamName(f.team)),
    f.days && chip('days', 'Updated', LF_DATES.find(([v]) => v === f.days)?.[1] || f.days),
    f.task && chip('task', 'Task', 'One task'),
    f.pinned && chip('pinned', '', 'Pinned'),
    f.archived && chip('archived', '', 'Archived'),
  ].filter(Boolean);
  bar.hidden = !chips.length;
  bar.innerHTML = chips.length ? chips.join('') + '<button type="button" class="linkish lf-clear" data-lf-clear-all>Clear</button>' : '';
}
function lfWire() {
  const state = LF;
  if (!state) return;
  const root = state.root, pop = state.pop;
  let soon = 0;
  const search = root.querySelector<HTMLInputElement>('.lf-q');
  if (!search) return;
  search.oninput = (ev: Event) => { state.filter.q = (ev.target as HTMLInputElement).value.trim(); clearTimeout(soon); soon = setTimeout(lfChanged, 250); };
  root.onclick = ev => {
    if (eventElement(ev)?.closest<HTMLElement>('.lf-pop')) return;                // the popover's own handler (below) takes its clicks
    const clear = eventElement(ev)?.closest<HTMLElement>('[data-lf-clear]');
    if (clear) { state.filter[String(clear.dataset.lfClear)] = ''; lfChanged(); return; }
    if (eventElement(ev)?.closest<HTMLElement>('[data-lf-clear-all]')) { for (const k of LF_COUNTED) state.filter[k] = ''; lfChanged(); return; }
    if (eventElement(ev)?.closest<HTMLElement>('[data-lf-more]')) { void lfLoad(true); return; }
    const row = eventElement(ev)?.closest<HTMLElement>('a.lf-row[data-lf-open="tico"]');
    if (row && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); void openFileLink(row.dataset.id, row.dataset.name); }
  };
  pop.onclick = (ev: MouseEvent) => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-lf-set], [data-lf-toggle]');
    if (!pick) return;
    if (pick.dataset.lfSet) state.filter[pick.dataset.lfSet] = pick.dataset.lfValue;
    else state.filter[String(pick.dataset.lfToggle)] = !state.filter[String(pick.dataset.lfToggle)];
    lfChanged();
  };
  // A long bot list has a find box: it hides the items that do not match, within its own section.
  pop.oninput = (ev: Event) => {
    const find = eventElement(ev)?.closest<HTMLInputElement>('.tl-menu-q'); if (!find) return;
    const words = find.value.trim().toLowerCase();
    const section = find.closest<HTMLElement>('.lf-sec');
    if (!section) return;
    for (const b of section.querySelectorAll<HTMLElement>('.tl-mi')) b.hidden = !!words && !String(b.dataset.label).includes(words);
  };
  pop.onkeydown = tasksMenuKeys;
  // The popover is built as it opens (its teams and bots as they are now), placed under the button, and focused.
  pop.addEventListener('beforetoggle', (ev: Event) => { if ('newState' in ev && ev.newState === 'open') pop.innerHTML = lfPopHTML(); });
  pop.addEventListener('toggle', (ev: Event) => {
    const open = 'newState' in ev && ev.newState === 'open', btn = root.querySelector<HTMLElement>('.lf-fbtn');
    if (!btn) return;
    btn.setAttribute('aria-expanded', String(open));
    if (!open) return;
    lfMenuPlace(pop, btn);
    // The first pick in force, not the bot find box: a phone would open the keyboard over the sheet, and a
    // focus that scrolls the popover moves Kind out of sight.
    (pop.querySelector<HTMLElement>('[aria-checked="true"]') || pop.querySelector<HTMLElement>('.tl-mi, .lf-pill'))?.focus({preventScroll: true});
  });
}
function lfChanged() {
  const state = LF;
  if (!state) return;
  history.replaceState(null, '', RD_FILES + lfQuery(state.filter));
  S.route = LAST_ROUTE = location.hash;
  lfControls();
  void lfLoad();
}
// Under the filter button and inside the window; a phone's popover is the bottom sheet the stylesheet makes it.
function lfMenuPlace(pop: HTMLElement, anchor: Element) {
  if (matchMedia(PHONE).matches) { pop.style.top = pop.style.left = ''; return; }
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8))}px`;
}
function lfPopHTML() {
  const state = LF;
  if (!state) return '';
  const f = state.filter;
  const pill = (key: string, value: string, label: string) => `<button type="button" class="tl-mi lf-pill" role="menuitemradio" aria-checked="${f[key] === value}"`
    + ` data-lf-set="${key}" data-lf-value="${esc(value)}">${esc(label)}</button>`;
  const item = (key: string, value: string, label: string, face: string, find: string) => `<button type="button" class="tl-mi" role="menuitemradio" aria-checked="${f[key] === value}"`
    + ` data-lf-set="${key}" data-lf-value="${esc(value)}" data-label="${esc(find.toLowerCase())}">${face ? `<span class="tl-opt-face">${face}</span>` : ''}`
    + `<span class="lf-mi-name">${esc(label)}</span><span class="tl-mi-on" aria-hidden="true">✓</span></button>`;
  const toggle = (key: string, label: string) => `<button type="button" class="tl-mi" role="menuitemcheckbox" aria-checked="${!!f[key]}" data-lf-toggle="${key}">`
    + `<span class="lf-mi-name">${label}</span><span class="tl-mi-on" aria-hidden="true">✓</span></button>`;
  const bots = (S.emps || []).filter(e => e.status !== 'archived').map(e => ({id: e.id, name: botDisplayName(e.id)}))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
  const groups = state.groups || [];
  return `<div class="tl-menu lf-menu" role="group" aria-label="Filters">
    <div class="lf-sec" role="group" aria-label="Kind"><div class="tl-menu-h" aria-hidden="true">Kind</div>
      <div class="lf-pills">${LF_KINDS.map(([v, l]) => pill('kind', String(v), String(l))).join('')}</div></div>
    <div class="lf-sec" role="group" aria-label="Updated"><div class="tl-menu-h" aria-hidden="true">Updated</div>
      <div class="lf-pills">${LF_DATES.map(([v, l]) => pill('days', String(v), String(l))).join('')}</div></div>
    <div class="lf-sec" role="group" aria-label="Bot"><div class="tl-menu-h" aria-hidden="true">Bot</div>
      ${bots.length > 7 ? '<input type="search" class="tl-menu-q" placeholder="Find a bot" aria-label="Find a bot" autocomplete="off" spellcheck="false">' : ''}
      <div class="tl-menu-list">${item('bot', '', 'All bots', '', 'all bots')}${bots.map(b => item('bot', b.id, b.name, avatar(b.id, 16), `${b.name} ${b.id}`)).join('')}</div></div>
    ${groups.length ? `<div class="lf-sec" role="group" aria-label="Team"><div class="tl-menu-h" aria-hidden="true">Team</div>
      <div class="tl-menu-list">${item('team', '', 'All teams', '', 'all teams')}${groups.map((g: LfGroup) => item('team', g.id, g.name, '', g.name)).join('')}</div></div>` : ''}
    <div class="tl-menu-sep" role="separator"></div>
    <div class="lf-sec" role="group" aria-label="Show">${toggle('pinned', 'Pinned')}${toggle('archived', 'Archived')}</div>
  </div>`;
}
// The ticks follow a change while the popover stays open (its find box keeps what was typed).
function lfPopSync() {
  const state = LF;
  if (!state?.pop?.matches(':popover-open')) return;
  for (const el of state.pop.querySelectorAll<HTMLElement>('[data-lf-set]')) el.setAttribute('aria-checked', String(state.filter[String(el.dataset.lfSet)] === el.dataset.lfValue));
  for (const el of state.pop.querySelectorAll<HTMLElement>('[data-lf-toggle]')) el.setAttribute('aria-checked', String(!!state.filter[String(el.dataset.lfToggle)]));
}
// Search (the box) asks GET /api/v2/files/search and narrows its hits by kind and bot here; the list asks
// GET /api/v2/files with every filter, a page at a time.
async function lfLoad(more = false) {
  const state = LF;
  if (!state) return;
  const seq = ++state.seq, f = state.filter, list = state.root.querySelector<HTMLElement>('.lf-list');
  if (!list) return;
  if (!more) list.innerHTML = skelRows(6);
  try {
    if (f.q) {
      const r = await get<import('../types/api').FileSearch>(`/v2/files/search?q=${encodeURIComponent(f.q)}&limit=50`, {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = (r.results || []).filter(h => (!f.kind || h.library_kind === f.kind) && (!f.bot || h.bot === f.bot))
        .map(h => ({...h, last_activity_at: h.updated_at, hit: true}));
      state.cursor = null;
    } else {
      const r = await get('/v2/files?' + lfApiQuery(f, more ? state.cursor : ''), {fresh: true});
      if (LF !== state || seq !== state.seq) return;
      state.rows = more ? state.rows.concat(r.files || []) : r.files || [];
      state.cursor = r.has_more ? r.next_cursor : null;
    }
  } catch (e) {
    if (LF === state && seq === state.seq) {
      const cols = state.root.querySelector<HTMLElement>('.lf-cols');
      if (!cols) return;
      cols.hidden = true;
      list.innerHTML = `<p class="err lf-pad">${esc(errorMessage(e, ''))}</p>`;
    }
    return;
  }
  lfDraw();
}
// One row is one link (the whole row opens the file). Its name cell holds the two-line form a phone shows: the name, then
// Kind · Bot · time, then the search excerpt; a computer shows the excerpt on the name's line and the columns beside it.
function lfRowHTML(f: LfRow) {
  const kind = f.library_kind || 'other';
  const page = kind === 'page', link = f.locator === 'remote_link' || (f.hit && f.url);
  const url = page ? pageHref(f.id) : link ? (f.open?.url || f.url) : f.open?.url || `${API}/v2/files/${encodeURIComponent(f.id)}`;
  // A Google file opens in the review view, in Google's own preview (file-review.ts); any other link at its site.
  const google = link && !f.hit && !!googlePreviewUrl(String(url || ''));
  const attrs = page ? '' : link ? ` target="_blank" rel="noopener noreferrer"${google ? ` data-lf-open="tico" data-id="${esc(f.id)}" data-name="${esc(f.title)}"` : ''}`
    : ` data-lf-open="tico" data-id="${esc(f.id)}" data-name="${esc(f.name || f.title)}"`;
  const kindName = (LF_KINDS.find(([v]) => v === kind)?.[1] || 'Other').replace(/s$/, '');
  const bot = pageBotName(f), when = ago(f.last_activity_at), team = lfTeamName(botRow(f.bot)?.team) || '—';
  const pinned = f.pins || [];
  const pins = pinned.length ? `<span class="lf-pins" title="${esc(pinned.join(', '))}">Pinned${pinned.length > 1 ? ' ' + pinned.length : ''}</span>` : '';
  const archived = f.archived ? '<span class="lf-pins">Archived</span>' : '';
  const excerpt = f.excerpt ? `<span class="lf-excerpt">${esc(f.excerpt)}</span>` : '';
  return `<a class="lf-row" href="${esc(url)}"${attrs}>
    <span class="nav-icon lf-ico" aria-hidden="true">${link ? 'link' : LF_ICON[kind] || 'inventory_2'}</span>
    <span class="lf-name"><span class="lf-line"><span class="lf-title" title="${esc(f.title)}">${esc(f.title)}</span>${pins}${archived}</span>
      <span class="lf-meta">${esc(kindName)} · ${esc(bot)} · ${esc(when)}</span>${excerpt}</span>
    <span class="lf-kind">${esc(kindName)}</span>
    <span class="lf-by">${f.bot ? avatar(f.bot, 18) : ''}<span class="lf-by-n">${esc(bot)}</span></span>
    <span class="lf-team">${esc(team)}</span>
    <span class="lf-when" title="${esc(fmt(f.last_activity_at))}">${esc(when)}</span></a>`;
}
function lfDraw() {
  const state = LF;
  if (!state) return;
  const root = state.root, list = root.querySelector<HTMLElement>('.lf-list'), cols = root.querySelector<HTMLElement>('.lf-cols'), rows = state.rows;
  if (!list || !cols) return;
  const any = LF_FILTERS.some(k => state.filter[k]);
  cols.hidden = !rows.length;
  list.innerHTML = rows.length ? rows.map(lfRowHTML).join('') + (state.cursor ? '<button type="button" class="ghost lf-more" data-lf-more>More</button>' : '')
    : `<p class="muted lf-pad">${any ? 'Nothing matches.' : 'No files yet.'}</p>`;
}

// ----------------------------------------------------------------- routes and global search
// Called by redesignRoute (ui/app/redesign.ts) before the router's own list.
function pagesRoute(r: string) {
  if (rdUnder(r, RD_FILES)) { pageLibraryFiles(); return true; }
  if (r.startsWith(PAGE_ROUTE)) {
    const [id = '', query = ''] = r.slice(PAGE_ROUTE.length).split('?');
    void pageFullView(decodeURIComponent(id), Number(new URLSearchParams(query).get('v')) || 0);
    return true;
  }
  return false;
}
function pagesStop(r: string) {
  if (!r.startsWith(PAGE_ROUTE)) PG = null;
  if (!rdUnder(r, RD_FILES)) LF = null;
}
// File hits for the search box (ui/app/search.ts): best first, only what the reader may open.
async function searchFileHits(q: string) {
  if (q.trim().length < 2) return [];
  const r = await get<import('../types/api').FileSearch>(`/v2/files/search?q=${encodeURIComponent(q.trim())}&limit=6`);
  return (r.results || []).map(h => ({kind: 'file', label: h.title, alias: pageBotName(h), icon: LF_ICON[h.library_kind] || 'inventory_2',
    href: h.library_kind === 'page' ? pageHref(h.id) : RD_FILES + '?q=' + encodeURIComponent(q.trim())}));
}
