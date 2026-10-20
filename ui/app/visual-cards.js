/* ui/app/visual-cards.js — Visuals in replies: the card each picture, SVG, visual page, video or audio clip gets
   under a chat message or a task comment (Visual replies 2/4 frame; the renderers fill VISUAL_BODY by kind).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A message keeps its attachments in the order they were attached (refs.attachments, backend/visuals.py). The first
// four that can show inline are cards; the rest, and anything else, stay the usual file chips.
const VISUAL_INLINE_MAX = 4;
// Renderers by kind: VISUAL_BODY[kind](file, card) -> the HTML that fills the card's body, inside the space the
// frame already holds for it. VISUAL_WIRE[kind](card) runs once per card after it is in the page. VISUAL_EXPAND[kind]
// (card) opens it full screen; a kind without one gets no Expand item. VISUAL_SPACE[kind](file) -> CSS for the body
// before it loads, when width/height do not say. VISUAL_MOVED[kind](card) runs on a kept card just before a redraw
// puts it back without moveBefore (visualSwap): taken out of the page, it loads again once back.
const VISUAL_BODY = {}, VISUAL_WIRE = {}, VISUAL_EXPAND = {}, VISUAL_SPACE = {}, VISUAL_MOVED = {};

// The address a card shows from: 1/4's signed, sign-in-free `src` on /visuals/<token> (backend/visuals.py). Never
// the signed-in /api/v2/files address and never a blob: URL. No `src` (inline serving off, or a file this reader can
// no longer open): no card, the file is a chip.
function visualSrc(f, field = 'src') {
  const raw = f && typeof f[field] === 'string' ? f[field] : '';
  if (!raw) return null;
  let url; try { url = new URL(raw, location.href); } catch { return null; }
  // New shells wait for the authenticated config's native ACK, including while
  // live messages arrive during a config refresh. Until then these stay chips.
  if ((window.ticoNativeFeatures || []).includes('visualOrigin') && url.origin !== location.origin) {
    if (url.origin !== NATIVE_VISUAL_ORIGIN || url.protocol !== 'https:' || /[\x00-\x20\x7f\\%?#@]/.test(raw)
      || /\/\.{1,2}(?:\/|$)/.test(raw) || url.username || url.password
      || !/^\/visuals\/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(url.pathname)) return null;
  }
  return /^https?:$/.test(url.protocol) && /^\/visuals\/[A-Za-z0-9_.-]+$/.test(url.pathname) && !url.search ? url.href : null;
}
// An ACK can arrive after a live snapshot painted chips. Reuse only the open
// threads' cached authorized data; no new read, draft reset or whole-page refresh.
function nativeVisualRepaint() {
  if (typeof V2C !== 'undefined' && V2C) v2ChatRender(V2C);
  if (typeof TASK_CHAT !== 'undefined' && TASK_CHAT?.data && taskChatCurrent(TASK_CHAT))
    taskCommentsRender(TASK_CHAT, TASK_CHAT.data);
}
const visualRef = f => `${f.file_id}@${Number(f.version)}`;
// A card, or null for a chip.
function visualState(f) {
  if (!f || typeof f !== 'object' || !f.file_id || !Number.isInteger(Number(f.version)) || !VISUAL_BODY[f.kind]) return null;
  if (f.removed) return 'removed';
  return visualSrc(f) ? 'ready' : null;
}
function visualSpace(f) {
  const w = Number(f.width), h = Number(f.height);
  if (w > 0 && h > 0) return `aspect-ratio:${w}/${h};--vcard-w:${Math.round(w)}px`;
  return VISUAL_SPACE[f.kind]?.(f) || '';
}
const visualTitle = f => String(f.title || f.name || 'Visual').trim() || 'Visual';
function visualCardHTML(f, key, state) {
  const title = visualTitle(f), n = Number(f.version), fid = String(f.file_id);
  const current = Number(f.current_version);
  const newer = state !== 'removed' && current > n
    ? `<button type="button" class="link vcard-newer" data-vcard-act="newer">Newer version</button>` : '';
  const expand = state === 'ready' && VISUAL_EXPAND[f.kind] ? `<button type="button" role="menuitem" data-vcard-act="expand">Expand</button>` : '';
  const menu = state === 'removed' ? '' : `<details class="vcard-menu"><summary aria-label="More for ${esc(title)}" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></summary>
      <div class="vcard-menu-list" role="menu">${expand}<button type="button" role="menuitem" data-vcard-act="open">Open in file viewer</button><a role="menuitem" href="${API}/v2/files/${encodeURIComponent(fid)}?v=${n}" download="${esc(f.name || title)}">Download</a></div></details>`;
  const body = state === 'removed' ? '<div class="vcard-note">This visual was removed</div>' : VISUAL_BODY[f.kind](f);
  return `<figure class="vcard" data-visual-key="${esc(key)}" data-kind="${esc(f.kind)}" data-state="${state}" data-file="${esc(fid)}" data-version="${n}"${
    current > n ? ` data-current="${current}"` : ''} data-name="${esc(f.name || title)}"${state === 'ready' ? ` data-fresh-src="${esc(visualSrc(f))}"` : ''}>
    <div class="vcard-body" style="${esc(state === 'removed' ? '' : visualSpace(f))}">${body}</div>
    <figcaption class="vcard-cap"><span class="vcard-title">${esc(title)}</span>${newer}${menu}</figcaption></figure>`;
}
// The cards for `files` (a message's refs.attachments) and, after them, `chips(rest, shown)` for everything else:
// `rest` the attachments that are not cards, `shown` the set of "<file id>@<version>" that are. `key` is the
// message's id: a card is keyed "<message>:<file>@<version>", so a new version is a new card.
function visualCardsHTML(files, {key = '', chips = () => ''} = {}) {
  const cards = [], rest = [], shown = new Set(), keys = new Set();
  for (const f of Array.isArray(files) ? files : []) {
    const state = cards.length < VISUAL_INLINE_MAX ? visualState(f) : null;
    if (!state) { rest.push(f); continue; }
    let k = `${key}:${visualRef(f)}`;
    for (let i = 2; keys.has(k); i++) k = `${key}:${visualRef(f)}#${i}`;
    keys.add(k); shown.add(visualRef(f));
    cards.push(visualCardHTML(f, k, state));
  }
  return (cards.length ? `<div class="vcards">${cards.join('')}</div>` : '') + chips(rest, shown);
}

// Both threads redraw with a whole new tree. Before the swap, a card whose key is in both trees keeps its old node,
// so a loaded page or a playing clip is not torn down; it takes the new tree's fresh address (data-fresh-src) for
// a renderer whose address ran out. visualKept pairs them (old node, its place in the new tree); visualKeep puts
// each old node in its place.
function visualKept(oldRoot, newRoot) {
  const old = new Map(), pairs = [];
  for (const card of oldRoot.querySelectorAll('figure.vcard[data-visual-key]')) old.set(card.dataset.visualKey, card);
  if (!old.size) return pairs;
  for (const card of newRoot.querySelectorAll('figure.vcard[data-visual-key]')) {
    const was = old.get(card.dataset.visualKey);
    if (!was || was.dataset.state !== card.dataset.state || was.dataset.current !== card.dataset.current) continue;
    old.delete(card.dataset.visualKey);
    if (card.dataset.freshSrc) was.dataset.freshSrc = card.dataset.freshSrc;
    pairs.push([was, card]);
  }
  return pairs;
}
function visualKeep(oldRoot, newRoot) {
  for (const [was, card] of visualKept(oldRoot, newRoot)) card.replaceWith(was);
}
// root.innerHTML = html, keeping the cards both trees share. The new tree is brought into this document first: a
// node moved through the template's own document reloads (a playing clip back to 0:00). Where the browser can move
// a node without taking it out (moveBefore), the new tree goes in and each kept card moves into it, so a page's
// frame is not loaded again either. Elsewhere the kept card is taken out and put back in the same task: a clip
// plays on, a page's frame loads again (VISUAL_MOVED).
function visualSwap(root, html) {
  if (!root.querySelector('figure.vcard')) { root.innerHTML = html; return; }
  const t = document.createElement('template');
  t.innerHTML = html;
  const fresh = document.importNode(t.content, true);
  if (!root.moveBefore) {
    for (const [was, card] of visualKept(root, fresh)) { card.replaceWith(was); VISUAL_MOVED[was.dataset.kind]?.(was); }
    root.replaceChildren(fresh);
    return;
  }
  const pairs = visualKept(root, fresh), old = [...root.childNodes];
  root.append(fresh);
  for (const [was, card] of pairs) { card.parentNode.moveBefore(was, card); card.remove(); }
  const kept = new Set(pairs.map(([was]) => was));
  for (const node of old) if (!kept.has(node)) node.remove();
}
function visualWire(root = document) {
  for (const card of root.querySelectorAll('figure.vcard:not([data-wired])')) {
    card.dataset.wired = '1';
    if (card.dataset.state === 'ready') VISUAL_WIRE[card.dataset.kind]?.(card);
  }
}
new MutationObserver(() => { if (document.querySelector('figure.vcard:not([data-wired])')) visualWire(); })
  .observe(document.documentElement, {childList: true, subtree: true});
// A renderer that could not show its visual: the card says so and offers the file viewer (and Download, which the
// menu has too). `message` replaces the default words, e.g. "This device can't play MOV video".
function visualFail(card, message = "Couldn't show this visual") {
  if (!card) return;
  card.dataset.state = 'failed';
  const body = card.querySelector('.vcard-body');
  body.removeAttribute('style');
  body.innerHTML = `<div class="vcard-note">${esc(message)} <button type="button" class="link" data-vcard-act="open">Open in file viewer</button></div>`;
  card.querySelector('[data-vcard-act="expand"]')?.remove();
}
// Open in file viewer: inside a task, its own file panel at that version; anywhere else the file viewer.
function visualOpen(card, version = Number(card.dataset.version)) {
  const d = card.closest('dialog');
  if (d?.tfEl && tfOpen(d, card.dataset.file, version, {scroll: true})) return;
  VIEW = {items: [{fileId: card.dataset.file, version, name: card.dataset.name, kind: viewKindOf(card.dataset.name)}], index: 0};
  void viewerShow();
}
document.addEventListener('click', ev => {
  const act = ev.target.closest('[data-vcard-act]');
  const card = act?.closest('figure.vcard');
  if (!card) return;
  ev.preventDefault();
  act.closest('details')?.removeAttribute('open');
  const what = act.dataset.vcardAct;
  if (what === 'expand') VISUAL_EXPAND[card.dataset.kind]?.(card);
  else if (what === 'newer') visualOpen(card, Number(card.dataset.current));
  else if (what === 'open') visualOpen(card);
});
// One menu open at a time; a click elsewhere closes it.
document.addEventListener('click', ev => {
  for (const m of document.querySelectorAll('details.vcard-menu[open]')) if (!m.contains(ev.target)) m.removeAttribute('open');
});
