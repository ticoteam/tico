/* ui/app/viewer.js — The file viewer dialog, CSV view, and inline images and media
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// "i want to be able to open markdown files inline (popup or something), so
// instead of this link going to the downloads folder, it opens up for me to see with nice
// formatting." A link to a Tico file that turns out to be Markdown or plain text opens here,
// formatted, with Download beside it; any other file downloads exactly as before.
const DOC_VIEW_LIMIT = 2_000_000;
function fileLinkId(a) {
  if (!a || a.hasAttribute('download')) return '';
  let url; try { url = new URL(a.getAttribute('href') || '', location.href); } catch { return ''; }
  if (url.host !== location.host) return '';
  const m = url.pathname.match(/^\/api\/v2\/files\/([A-Za-z0-9-]{8,80})$/);
  return m ? m[1] : '';
}
// The version a file link pins with ?v=<n> (an approval links the version it approved), or null for the current one.
function fileLinkVersion(a) {
  let url; try { url = new URL(a.getAttribute('href') || '', location.href); } catch { return null; }
  const v = url.searchParams.get('v');
  return /^[1-9][0-9]{0,8}$/.test(v || '') ? Number(v) : null;
}
const fileNameOf = response => {
  const cd = response.headers.get('content-disposition') || '';
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) { try { return decodeURIComponent(star[1]); } catch {} }
  return (cd.match(/filename="?([^";]+)"?/i) || [])[1] || 'file';
};
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), {href: url, download: name});
  // inside an open modal dialog, or it is inert and the click does nothing
  (document.querySelector('dialog[open]') || document.body).append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
// "make these images easier to view inline or in a pop up ... Same with other
// video and content ... read most common content inline or in pop up easily in app and looking
// good." One viewer for all of it: a Tico file of any kind (fetched once, kept for the visit),
// a link to an image, video, audio, PDF, Markdown or text anywhere, a YouTube, Vimeo or Loom
// link (embedded), an attached chat image or a picture inside formatted text. Arrows, swipe and
// the arrow keys move through what sits together (a message, an update, a task). Image links
// also get a thumbnail in place; other media links a small type mark. Anything that cannot be
// shown downloads exactly as before.
const VIEW_KINDS = [[/\.(png|jpe?g|gif|webp|avif|heic|svg)$/i, 'image'], [/\.(mp4|webm|mov|m4v)$/i, 'video'],
  [/\.(mp3|m4a|wav|ogg|aac)$/i, 'audio'], [/\.pdf$/i, 'pdf'], [/\.(md|markdown)$/i, 'markdown'],
  [/\.csv$/i, 'csv'], [/\.(txt|log|json|ya?ml)$/i, 'text']];
const VIEW_TYPES = {image: '', video: 'video/mp4', audio: 'audio/mpeg', pdf: 'application/pdf'};
const MEDIA_MARK = {video: 'VIDEO', audio: 'AUDIO', pdf: 'PDF', markdown: 'DOC', text: 'TEXT', csv: 'CSV', embed: 'VIDEO'};
const viewKindOf = name => { for (const [re, kind] of VIEW_KINDS) if (re.test(String(name || '').split(/[?#]/)[0])) return kind; return null; };
function viewEmbedOf(url) {
  const h = url.hostname.replace(/^www\./, '');
  let id;
  if (h === 'youtu.be' && (id = url.pathname.slice(1))) return 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(id);
  if ((h === 'youtube.com' || h === 'm.youtube.com') && (id = url.searchParams.get('v') || (url.pathname.match(/^\/(?:shorts|embed)\/([\w-]+)/) || [])[1]))
    return 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(id);
  if (h === 'vimeo.com' && (id = (url.pathname.match(/^\/(\d+)/) || [])[1])) return 'https://player.vimeo.com/video/' + id;
  if (h === 'loom.com' && (id = (url.pathname.match(/^\/share\/([\w-]+)/) || [])[1])) return 'https://www.loom.com/embed/' + id;
  return '';
}
// What an element in the page points at, if the viewer can show it.
function viewItemOf(el) {
  if (!el) return null;
  if (el.matches('.chat-thumb')) return {fileId: el.dataset.chatImage, name: el.dataset.name || 'image', kind: 'image'};
  if (el.matches('img')) return el.src ? {src: el.src, name: el.alt || 'image', kind: 'image'} : null;
  if (!el.matches('a[href]') || el.hasAttribute('download') || el.closest('#side, #mobile-nav, .nav-link, .upd-meta')) return null;
  const text = (el.dataset.name || el.textContent || '').trim();      // a file card names its file in data-name
  const fileId = fileLinkId(el);
  if (fileId) return {fileId, version: fileLinkVersion(el), name: text || 'file', kind: viewKindOf(text)};
  let url; try { url = new URL(el.getAttribute('href') || '', location.href); } catch { return null; }
  if (!/^https?:$/.test(url.protocol)) return null;
  const embed = viewEmbedOf(url);
  if (embed) return {src: embed, href: url.href, name: text || url.hostname, kind: 'embed'};
  const kind = viewKindOf(url.pathname);
  return kind ? {src: url.href, href: url.href, name: text || url.pathname.split('/').pop(), kind} : null;
}
// A CSV as a readable table: quoted fields may hold commas, quotes ("") and line breaks. Every cell is
// set as text, never as HTML. The first CSV_ROWS rows show, the header row stays put while it scrolls.
const CSV_ROWS = 1000, CSV_LIMIT = 20_000_000;
function csvParse(text) {
  const rows = []; let row = [], cell = '', quoted = false, i = text.charCodeAt(0) === 0xFEFF ? 1 : 0;
  const end = () => { row.push(cell); cell = ''; if (row.length > 1 || row[0] !== '') rows.push(row); row = []; };
  for (; i < text.length; i++) {
    const c = text[i];
    if (quoted) { if (c !== '"') cell += c; else if (text[i + 1] === '"') { cell += '"'; i++; } else quoted = false; }
    else if (c === '"' && cell === '') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; end(); }
    else cell += c;
  }
  if (cell !== '' || row.length) end();
  return rows;
}
function csvView(text, max = CSV_ROWS) { return rowsView(csvParse(text), max); }
// Rows (the first one the header) as the same table: a CSV, or a JSON list of objects (a task's file viewer).
function rowsView(rows, max = CSV_ROWS) {
  const wrap = document.createElement('div');
  wrap.className = 'csv-view';
  if (!rows.length) { wrap.textContent = 'This file is empty.'; return wrap; }
  const scroll = document.createElement('div'), table = document.createElement('table'), head = table.createTHead().insertRow();
  scroll.className = 'csv-scroll'; scroll.tabIndex = 0; table.className = 'csv';
  for (const v of rows[0]) head.appendChild(document.createElement('th')).textContent = v;
  const body = table.createTBody();
  for (const r of rows.slice(1, max + 1)) { const tr = body.insertRow(); for (const v of r) tr.insertCell().textContent = v; }
  scroll.append(table); wrap.append(scroll);
  const total = rows.length - 1;
  if (total > max) {
    const note = document.createElement('p');
    note.className = 'muted csv-note';
    note.textContent = `Showing ${max.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} rows. Download the file for the rest.`;
    wrap.append(note);
  }
  return wrap;
}
const VIEW_FILES = new Map();                 // Tico file id -> Promise<{blob, url, name, kind}>
function viewFile(id, fallbackName, version = null) {
  const key = version ? `${id}?v=${version}` : id;
  if (!VIEW_FILES.has(key)) VIEW_FILES.set(key, (async () => {
    const response = await fetch(`${API}/v2/files/${encodeURIComponent(id)}${version ? '?v=' + version : ''}`, {credentials: 'same-origin'});
    if (!response.ok) throw new Error(response.status === 403 ? 'You cannot open this file.' : 'Could not open the file.');
    const name = fileNameOf(response) || fallbackName;
    const raw = await response.blob();
    const kind = viewKindOf(name) || (/^text\/csv\b/i.test(raw.type) ? 'csv' : null);
    const type = kind === 'image' ? (/\.png$/i.test(name) ? 'image/png' : /\.gif$/i.test(name) ? 'image/gif' : /\.webp$/i.test(name) ? 'image/webp'
      : /\.svg$/i.test(name) ? 'image/svg+xml' : 'image/jpeg') : (VIEW_TYPES[kind] || raw.type || 'application/octet-stream');
    const blob = new Blob([raw], {type});
    return {blob, url: URL.createObjectURL(blob), name, kind};
  })().catch(error => { VIEW_FILES.delete(key); throw error; }));
  return VIEW_FILES.get(key);
}
let VIEW = null;
function viewerDialog() {
  let d = $('#doc-viewer');
  if (!d) {
    d = document.createElement('dialog'); d.id = 'doc-viewer'; d.className = 'tmodal doc-viewer';
    d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
    d.addEventListener('close', () => { d.querySelector('video, audio')?.pause(); VIEW = null; });
    // On the document: a redraw drops focus out of the dialog, and the arrows must still work.
    document.addEventListener('keydown', ev => {
      if (!d.open || ev.target.closest?.('input, textarea, [contenteditable]')) return;
      if (ev.key === 'ArrowRight') { ev.preventDefault(); viewerStep(1); }
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); viewerStep(-1); }
    });
    let x0 = null;
    d.addEventListener('touchstart', ev => { x0 = ev.touches.length === 1 ? ev.touches[0].clientX : null; }, {passive: true});
    d.addEventListener('touchend', ev => {
      if (x0 === null || d.querySelector('.viewer-img.zoom')) return;
      const dx = ev.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 60) viewerStep(dx < 0 ? 1 : -1);
    }, {passive: true});
    document.body.append(d);
  }
  return d;
}
function viewerStep(delta) {
  if (!VIEW || VIEW.items.length < 2) return;
  VIEW.index = (VIEW.index + delta + VIEW.items.length) % VIEW.items.length;
  void viewerShow();
}
async function viewerShow() {
  const d = viewerDialog(), state = VIEW, item = state.items[state.index];
  const many = state.items.length > 1;
  const head = (name, extra = '') => `<div class="doc-viewer-head"><h2 title="${esc(name)}">${esc(name)}</h2>
      ${many ? `<span class="viewer-count">${state.index + 1} / ${state.items.length}</span>` : ''}${extra}
      <button class="ghost" type="button" data-doc-close aria-label="Close">✕</button></div>`;
  const arrows = many ? '<button type="button" class="viewer-nav prev" data-view-step="-1" aria-label="Previous">‹</button><button type="button" class="viewer-nav next" data-view-step="1" aria-label="Next">›</button>' : '';
  const paint = (name, kind, body, extra) => {
    if (VIEW !== state || state.items[state.index] !== item) return;
    d.className = 'tmodal doc-viewer' + (['image', 'video', 'embed'].includes(kind) ? ' viewer-media' : '');
    d.innerHTML = head(name, extra) + `<div class="doc-viewer-body viewer-${kind}">${body}</div>` + arrows;
    d.querySelector('[data-doc-close]').onclick = () => d.close();
    for (const b of d.querySelectorAll('[data-view-step]')) b.onclick = () => viewerStep(Number(b.dataset.viewStep));
    const img = d.querySelector('.viewer-img');
    if (img) {
      img.onclick = () => img.classList.toggle('zoom');
      // An icon (a 24 px SVG, say) is shown big enough to see; a vector stays sharp.
      const grow = () => {
        const w = img.naturalWidth, h = img.naturalHeight, big = Math.max(w, h);
        if (big && big < 320) img.style.width = Math.round(w * Math.min(/\.svg$/i.test(name) ? 20 : 4, 480 / big)) + 'px';
      };
      if (img.complete) grow(); else img.addEventListener('load', grow, {once: true});
    }
    if (!d.open) d.showModal();
  };
  const media = (kind, src, name) => kind === 'image' ? `<img class="viewer-img" src="${esc(src)}" alt="${esc(name)}">`
    : kind === 'video' ? `<video src="${esc(src)}" controls autoplay playsinline preload="metadata"></video>`
    : kind === 'audio' ? `<audio src="${esc(src)}" controls autoplay preload="metadata"></audio>`
    : kind === 'pdf' ? `<iframe src="${esc(src)}" title="${esc(name)}"></iframe>`
    : kind === 'embed' ? `<div class="viewer-embed"><iframe src="${esc(src)}" title="${esc(name)}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe></div>` : '';
  if (!item.fileId) {
    if (item.kind === 'markdown' || item.kind === 'text' || item.kind === 'csv') {
      paint(item.name, item.kind, '<p class="muted">Loading…</p>');
      try {
        const text = await (await fetch(item.src)).text();
        if (item.kind === 'csv') {
          paint(item.name, item.kind, '<div data-csv></div>', `<a class="ghost btnlike" href="${esc(item.href)}" target="_blank" rel="noopener">Open</a>`);
          d.querySelector('[data-csv]')?.replaceWith(csvView(text));
          return;
        }
        paint(item.name, item.kind, item.kind === 'markdown' ? `<div class="md">${safeMd(text)}</div>` : `<pre class="plain">${esc(text)}</pre>`,
              `<a class="ghost btnlike" href="${esc(item.href)}" target="_blank" rel="noopener">Open</a>`);
      } catch { window.open(item.href, '_blank', 'noopener'); d.close(); }
      return;
    }
    paint(item.name, item.kind, media(item.kind, item.src, item.name),
          `<a class="ghost btnlike" href="${esc(item.href || item.src)}" target="_blank" rel="noopener">Open</a>`);
    return;
  }
  paint(item.name, item.kind || 'loading', '<p class="muted">Loading…</p>');
  let file;
  try { file = await viewFile(item.fileId, item.name, item.version); }
  catch (e) { if (VIEW === state) { d.close(); toast(e.message || 'Could not open the file.', true); } return; }
  const extra = '<button class="ghost" type="button" data-doc-download>Download</button>';
  if (!file.kind || ((file.kind === 'markdown' || file.kind === 'text') && file.blob.size > DOC_VIEW_LIMIT) || (file.kind === 'csv' && file.blob.size > CSV_LIMIT)) {
    if (VIEW === state && state.items.length === 1) d.close();
    saveBlob(file.blob, file.name);                 // not something to show here: downloads as before
    return;
  }
  if (file.kind === 'csv') {
    const text = await file.blob.text();
    paint(file.name, 'csv', '<div data-csv></div>', extra);
    d.querySelector('[data-csv]')?.replaceWith(csvView(text));
  } else if (file.kind === 'markdown' || file.kind === 'text') {
    const text = await file.blob.text();
    paint(file.name, file.kind, file.kind === 'markdown' ? `<div class="md">${safeMd(text)}</div>` : `<pre class="plain">${esc(text)}</pre>`, extra);
  } else {
    paint(file.name, file.kind, media(file.kind, file.url, file.name),
          (file.kind === 'pdf' ? `<a class="ghost btnlike" href="${esc(file.url)}" target="_blank" rel="noopener">Open</a>` : '') + extra);
  }
  const dl = d.querySelector('[data-doc-download]');
  if (dl) dl.onclick = () => saveBlob(file.blob, file.name);
}
// Everything viewable that sits with `el` (a message, an update, a task), in page order.
function viewerOpenFrom(el) {
  const home = el.closest('.bubble, .conv-run, .upd-card, .upd-msg, .tmodal-body, .doc-viewer-body, .trow, .card') || el.parentElement || document.body;
  const els = [...home.querySelectorAll('a[href], .chat-thumb.ready, .md img')].filter(x => !x.closest('.inline-thumb'));
  const items = [], at = {i: 0};
  for (const x of els) {
    const item = viewItemOf(x);
    if (!item) continue;
    if (x === el) at.i = items.length;
    items.push(item);
  }
  if (!items.length) { const one = viewItemOf(el); if (!one) return false; items.push(one); }
  VIEW = {items, index: at.i};
  void viewerShow();
  return true;
}
async function openFileLink(id, fallbackName) {
  VIEW = {items: [{fileId: id, name: fallbackName || 'file', kind: viewKindOf(fallbackName)}], index: 0};
  await viewerShow();
}
document.addEventListener('click', ev => {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const thumb = ev.target.closest('.inline-thumb');
  const target = thumb ? thumb.previousElementSibling : ev.target.closest('a[href], .chat-thumb.ready, .md img');
  if (!target || (target.matches('img') && target.closest('a[href]'))) return;
  const item = viewItemOf(target);
  if (!item) return;
  ev.preventDefault();
  viewerOpenFrom(target);
});
// Thumbnails in place for image links, and a small type mark on other media links.
// A Tico file link whose words carry no extension ("storyboard") asks for the file's name and size
// (one small request, kept for the visit) rather than downloading it to find out what it is.
const FILE_META = new Map();
const fileMeta = id => {
  if (!FILE_META.has(id)) FILE_META.set(id, v2Get(`/v2/files/${encodeURIComponent(id)}/meta`));
  return FILE_META.get(id);
};
const INLINE_IMAGE_MAX = 15e6;
function mediaInline(root = document) {
  for (const a of root.querySelectorAll('.md a[href]:not([data-mi]), .upd-body a[href]:not([data-mi]), .chat-files a.pill:not([data-mi])')) {
    a.dataset.mi = '1';
    const item = viewItemOf(a);
    if (item?.fileId && !item.kind) {
      fileMeta(item.fileId).then(meta => {
        const kind = meta && viewKindOf(meta.name), mark = meta && MD_FILE_MARK[extOf(meta.name)];
        if (!(kind || mark) || !a.isConnected) return;
        if (kind === 'image' && meta.size <= INLINE_IMAGE_MAX && !a.closest('.chat-files')) mediaThumb(a, {...item, kind, name: meta.name});
        else if (mark && !a.closest('.chat-files')) fileCard(a, item.name, appName(), mark);
        else if (kind && kind !== 'image') { a.classList.add('media-link'); a.dataset.media = MEDIA_MARK[kind] || ''; }
      });
      continue;
    }
    if (!item?.kind || a.closest('.chat-files')) continue;
    if (item.kind !== 'image') { a.classList.add('media-link'); a.dataset.media = MEDIA_MARK[item.kind] || ''; continue; }
    mediaThumb(a, item);
  }
}
function mediaThumb(a, item) {
  {
    const holder = a.closest('.md, .upd-body');
    if (holder && holder.querySelectorAll('.inline-thumb').length >= 6) return;
    const b = Object.assign(document.createElement('button'), {type: 'button', className: 'inline-thumb'});
    b.setAttribute('aria-label', 'View ' + item.name);
    const img = document.createElement('img'); img.alt = item.name; img.decoding = 'async'; img.loading = 'lazy';
    if (!item.fileId && !item.src.startsWith(location.origin + '/')) img.referrerPolicy = 'no-referrer';
    b.append(img); a.after(b);
    const set = src => { img.src = src; img.onload = () => b.classList.add('ready'); img.onerror = () => b.remove(); };
    if (item.fileId) viewFile(item.fileId, item.name, item.version).then(f => f.kind === 'image' ? set(f.url) : b.remove()).catch(() => b.remove());
    else if (item.src.startsWith('https:') || item.src.startsWith(location.origin)) set(item.src);
    else b.remove();
  }
}
// A picture in formatted text shows a sized skeleton until it arrives (ui/styles/viewer.css); one that fails goes.
document.addEventListener('load', ev => { if (ev.target.matches?.('.md img')) ev.target.classList.add('ready'); }, true);
document.addEventListener('error', ev => { if (ev.target.matches?.('.md img')) ev.target.classList.add('broken'); }, true);
let MEDIA_INLINE_QUEUED = false;
new MutationObserver(() => {
  if (MEDIA_INLINE_QUEUED) return;
  MEDIA_INLINE_QUEUED = true;
  // A timer, not requestAnimationFrame: a tab opened in the background gets no frames, and its
  // thumbnails never came.
  setTimeout(() => { MEDIA_INLINE_QUEUED = false; try { mediaInline(); } catch {} }, 60);
}).observe(document.documentElement, {childList: true, subtree: true});
