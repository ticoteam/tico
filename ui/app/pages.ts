/* ui/app/pages.ts — Pages (docs/pages.md): the card every page shows as (pageCard), the full view at #/page/<id>, a
   task's pages at the top of the task. Library › Files is ui/app/library-files.ts. The status line is drawn here, outside the page's frame, so a page cannot fake it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PAGE_ROUTE = '#/page/', RD_FILES = '#/library/files';
// A card shows the top of the page at the card's width and its size's fixed height; full follows the page's own height.
const PAGE_SIZES: Record<string, number> = {small: 180, medium: 320, large: 520};
const PAGE_FULL_MIN = 240;

// ----------------------------------------------------------------- the status line
// A day (YYYY-MM-DD) in the app's date words (ui/app/format.ts): "Nov 11", Today, Tomorrow. Read at noon, so a zone
// never moves it to the day before.
const pageDay = (iso: string | null | undefined) => dayWords(String(iso) + 'T12:00:00') || String(iso);
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
    : `Updated ${esc(agoSaid(p.updated_at))}${who ? ' by ' + esc(who) : ''}`];
  if (Number.isInteger(p.version)) parts.push('v' + p.version);
  if (p.cadence && p.cadence !== 'none') parts.push(esc(p.cadence));
  if (p.as_of) parts.push('data from ' + esc(lowerDay(pageDay(p.as_of))));
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
// pageCard(page, {size}): one card holding the page's status line and the framed page (or, when there is no address,
// one row: a page mark and the title, the whole row a link). A card is a widget: the page at its own height up to the card's size,
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
    const title = esc(page.title || 'Page');
    if (full) {
      const open = document.createElement('div');
      open.className = 'pg-open';
      open.innerHTML = `<span class="nav-icon" aria-hidden="true">article</span><div class="pg-open-text"><span class="pg-open-title">${title}</span></div>`;
      el.innerHTML = pageStatusHTML(page);
      el.append(open);
    } else {
      // One quiet row, as the rail's other rows: a mark (the page icon, or a lock when restricted) and the title, the
      // whole row the link. No status line, version or Open button: the page says those when it opens.
      el.classList.add('pg-row');
      const open = document.createElement('a');
      open.className = 'pg-open';
      open.href = pageHref(page.file_id);
      open.title = page.title || 'Page';
      const mark = page.restricted ? '<span class="nav-icon pg-lock" role="img" aria-label="Restricted">lock</span>' : '<span class="nav-icon" aria-hidden="true">article</span>';
      open.innerHTML = `<span class="pg-mark">${mark}</span><span class="pg-open-title">${title}</span>`;
      el.append(open);
    }
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
  return kind === 'dashboard' || kind === 'person' ? HOME : kind === 'growth' ? '#/growth' : kind === 'team' ? '#/team/' + id : kind === 'bot' ? '#/bot/' + id : '#/task/' + id;
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
// The diff's header: What's changed?, the version to compare from, → the one on screen, and ✕ (the one close icon).
function pageDiffShell(state: PageFullState, body: HTMLElement, from: number) {
  const options = pageDiffBases(state).map(v => `<option value="${v.version}"${v.version === from ? ' selected' : ''}>v${v.version}</option>`).join('');
  body.innerHTML = `<section class="pg-diff" data-to="${state.shown}" aria-label="What's changed?"><header class="pg-diff-head"><h2>What's changed?</h2>
    <select class="pg-diff-from" aria-label="Compare from">${options}</select><span aria-hidden="true">→</span><span class="pg-diff-to">v${state.shown}</span>
    <span class="spacer"></span><button type="button" class="ghost tmodal-x" data-pg-diff-close aria-label="Close" title="Close">✕</button></header>
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
  tpMakeOpen?: boolean;
  tpCanLink?: boolean;
}
// The task's dialog, which holds its section (tpEl) and the release of its live handlers (tpOff).
interface TaskPagesHost extends HTMLDialogElement { tpEl?: TaskPagesSlot | null; tpOff?: (() => void) | null; }
const taskMakeDraftKey = (id: string) => 'tico.task.make-page.' + encodeURIComponent(String(S.me?.id || '')) + '.' + id;
// A live event the task's pages react to: a files change on the task, or a run of the page being made.
interface TaskPagesLiveEvent { kind?: string; kinds?: string[]; id?: unknown; conversation_id?: string; }
// A pin on a task's place: its page is the page as GET /api/v2/places/{place} lists it, with the replaced and archived flags.
interface TaskPlacePin { id: string | null; size?: string; can_unpin?: boolean; page?: import('../types/api').Page | null; }
interface TaskPlaceRead { can_link?: boolean; pins?: TaskPlacePin[]; }
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
  taskPageMakeShow(slot);
}
// The Describe box shows once Make page is chosen, while a draft is kept, or while a request is on its way or failed.
function taskPageMakeShow(slot: TaskPagesSlot) {
  $('.tp-make', slot).hidden = !slot.tpCanLink || !(slot.tpMakeOpen || ['making', 'update_required', 'failed'].includes(slot.tpMaking?.state ?? ''));
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
    // Pages shows even with none, for its + (Details' one part that stays when empty); a reader who may not link any
    // and has none to open gets no section.
    slot.hidden = !r.can_link && !pins.length;
    $('[data-tp-add]', slot).hidden = !r.can_link;
    slot.tpCanLink = !!r.can_link;
    taskPageMakeShow(slot);
    const kept = slot.tpCards || new Map<string | null, TaskPagesRow>(), cards = new Map<string | null, TaskPagesRow>();
    for (const pin of pins) {
      const key = JSON.stringify([pin.page, pin.size, pin.can_unpin]);
      let row = kept.get(pin.id);
      if (!row || row.key !== key) {
        // One row: the page's icon and name, and the row opens the page. Its version is on the chat's chip, not here.
        const el = document.createElement('div'); el.className = 'tp-page';
        el.innerHTML = `<a class="tp-row" href="${esc(pageHref(pin.page.file_id))}" data-page="${esc(pin.page.file_id)}" title="${esc(pin.page.title)}"><span class="nav-icon" aria-hidden="true">description</span><span class="tp-name">${esc(pin.page.title)}</span></a>${pin.can_unpin
          ? `<button type="button" class="ghost tp-unlink" data-tp-unlink="${esc(pin.id)}" aria-label="${esc('Unlink ' + pin.page.title)}" title="Unlink">${TL_ICON.x}</button>` : ''}`;
        row = {key, el};
      }
      cards.set(pin.id, row);
    }
    slot.tpCards = cards;
    $('.tp-pages', slot).replaceChildren(...[...cards.values()].map(x => x.el));
    if (r.can_link) await taskPageMakeLoad(d);
  } catch (e) { if (d.tpEl === slot) toast(errorMessage(e, ''), true); }
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

// A text diff, drawn wherever two versions of a file are compared (a page, Needs you, a file review, a task's files).
// Kept in the shell: the task's files section (ui/app/task-files.ts) is a page part.

type TfDiffLine = [' ' | '+' | '-', string];
// An index the loops below bound themselves, so a miss is a bug: it throws rather than reading undefined.
function tfAt<T>(xs: ArrayLike<T>, i: number): T {
  const x = xs[i];
  if (x === undefined) throw new Error('The diff index is out of range');
  return x;
}
// A line diff: common head and tail trimmed, the middle by longest common subsequence (a very long middle shows as
// replaced). Unchanged runs longer than six lines fold to a count.
function tfDiff(a: string, b: string): TfDiffLine[] {
  const A = String(a).split('\n'), B = String(b).split('\n');
  let head = 0; while (head < A.length && head < B.length && A[head] === B[head]) head++;
  let tail = 0; while (tail < A.length - head && tail < B.length - head && A[A.length - 1 - tail] === B[B.length - 1 - tail]) tail++;
  const a1 = A.slice(head, A.length - tail), b1 = B.slice(head, B.length - tail), out: TfDiffLine[] = A.slice(0, head).map((l): TfDiffLine => [' ', l]);
  if (a1.length * b1.length > 4e6) { out.push(...a1.map((l): TfDiffLine => ['-', l]), ...b1.map((l): TfDiffLine => ['+', l])); }
  else {
    const n = a1.length, m = b1.length, L = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) {
      const row = tfAt(L, i), below = tfAt(L, i + 1), x = tfAt(a1, i);
      for (let j = m - 1; j >= 0; j--) row[j] = x === tfAt(b1, j) ? tfAt(below, j + 1) + 1 : Math.max(tfAt(below, j), tfAt(row, j + 1));
    }
    let i = 0, j = 0;
    while (i < n && j < m) {
      const x = tfAt(a1, i), y = tfAt(b1, j);
      if (x === y) { out.push([' ', x]); i++; j++; }
      else if (tfAt(tfAt(L, i + 1), j) >= tfAt(tfAt(L, i), j + 1)) out.push(['-', tfAt(a1, i++)]);
      else out.push(['+', tfAt(b1, j++)]);
    }
    while (i < n) out.push(['-', tfAt(a1, i++)]);
    while (j < m) out.push(['+', tfAt(b1, j++)]);
  }
  out.push(...A.slice(A.length - tail).map((l): TfDiffLine => [' ', l]));
  return out;
}
function tfDiffHTML(a: string, b: string): string {
  const lines = tfDiff(a, b), html = [];
  if (!lines.some(([op]) => op !== ' ')) return '<p class="muted">No changes.</p>';
  for (let i = 0; i < lines.length;) {
    if (tfAt(lines, i)[0] === ' ') {
      let j = i; while (j < lines.length && tfAt(lines, j)[0] === ' ') j++;
      const run = j - i, edge = i === 0 || j === lines.length;
      if (run > 6) {
        const keepA = i === 0 ? 0 : 3, keepB = j === lines.length ? 0 : 3;
        for (const [, l] of lines.slice(i, i + keepA)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
        html.push(`<span class="fold">⋯ ${run - keepA - keepB} lines</span>`);
        for (const [, l] of lines.slice(j - keepB, j)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
      } else for (const [, l] of lines.slice(i, j)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
      void edge; i = j; continue;
    }
    const [op, l] = tfAt(lines, i++);
    html.push(`<span class="${op === '+' ? 'add' : 'del'}">${op} ${esc(l)}</span>`);
  }
  return `<pre class="tf-diff-lines">${html.join('')}</pre>`;
}

// Files dropped or pasted onto a zone: a task, the new-task form, a Needs you note. In the shell, since Needs you wires
// them on Home; the task's files section (ui/app/task-files.ts) is a page part.

// Files dragged onto a task or the new-task form land there; the zone lights up while they hover.
function fileDropTarget(zone: HTMLElement, take: (files: File[]) => void): void {
  let depth = 0;
  const on = (v: boolean) => zone.classList.toggle('over', v);
  zone.addEventListener('dragenter', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); depth++; on(true); });
  zone.addEventListener('dragover', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); if (!ev.dataTransfer) throw new Error('The drop has no data transfer'); ev.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) on(false); });
  zone.addEventListener('drop', ev => {
    if (!draggingFiles(ev)) return;
    ev.preventDefault(); depth = 0; on(false);
    if (!ev.dataTransfer) throw new Error('The drop has no data transfer'); take([...ev.dataTransfer.files]);
  });
}
// A pasted screenshot arrives as "image.png": each gets its own name, so a second paste is a new file rather than
// a new version of the first. Text on the clipboard keeps its normal paste.
function pastedImages(ev: ClipboardEvent): File[] {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return Array.from(ev.clipboardData?.items || [])
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile()).filter((f): f is File => f !== null)
    .map((f, i) => new File([f], `screenshot-${stamp}${i ? '-' + (i + 1) : ''}.${(f.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`, {type: f.type}));
}
function pasteImages(zone: HTMLElement, take: (files: File[]) => void): void {
  zone.addEventListener('paste', ev => {
    const images = pastedImages(ev);
    if (!images.length) return;
    if (!ev.clipboardData) throw new Error('The paste has no clipboard data');
    if (!ev.clipboardData.getData('text/plain')) ev.preventDefault();
    take(images);
  });
}
