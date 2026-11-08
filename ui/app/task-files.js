/* ui/app/task-files.js — A task's files and questions: the strip of tiles (one per file, at its newest version), the
   viewer that opens in place under it (versions, Compare, who added each and its question), file chips in comments,
   the question block (an ask on a comment or on a file version) and the board's covers.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- thumbnails: lazy, sized, a skeleton until they arrive. A versioned address never changes, so the browser's cache
// serves a revisit; this keeps the decoded picture too, so the peek, the modal and the board redraw it at once.
const TF_THUMBS = new Map();          // url -> decoded Image, newest use last
function tfThumbLoad(url) {
  const hit = TF_THUMBS.get(url);
  if (hit) { TF_THUMBS.delete(url); TF_THUMBS.set(url, hit); return Promise.resolve(hit); }
  const img = new Image(); img.decoding = 'async'; img.src = url;
  return img.decode().then(() => {
    TF_THUMBS.set(url, img);
    if (TF_THUMBS.size > 240) TF_THUMBS.delete(TF_THUMBS.keys().next().value);
    return img;
  });
}
// <img data-tf-src="…" data-tf-alt="…"> inside a .tf-skel box: the box turns .ready when it shows, .failed when
// neither address loads (a cover then hides; a tile keeps its type mark).
function tfShow(img, url = img.dataset.tfSrc) {
  const box = img.closest('.tf-skel') || img.parentElement;
  tfThumbLoad(url).then(() => { img.src = url; box.classList.add('ready'); }, () => {
    const alt = img.dataset.tfAlt;
    if (alt && alt !== url) { delete img.dataset.tfAlt; tfShow(img, alt); } else box.classList.add('failed');
  });
}
const TF_IO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  for (const e of entries) if (e.isIntersecting) { TF_IO.unobserve(e.target); tfShow(e.target); }
}, {rootMargin: '200px'}) : null;
function tfLazy(root = document) {
  for (const img of root.querySelectorAll('img[data-tf-src]:not([data-tf-on])')) {
    img.dataset.tfOn = '1';
    if (TF_THUMBS.has(img.dataset.tfSrc) || !TF_IO) tfShow(img); else TF_IO.observe(img);
  }
}
let TF_LAZY_QUEUED = false;
new MutationObserver(() => {
  if (TF_LAZY_QUEUED) return;
  TF_LAZY_QUEUED = true;
  setTimeout(() => { TF_LAZY_QUEUED = false; tfLazy(); }, 60);
}).observe(document.documentElement, {childList: true, subtree: true});

// ---- what a file is and how it shows
function tfKind(name, mime) {
  const n = String(name || '').toLowerCase().split(/[?#]/)[0], m = String(mime || '').toLowerCase();
  if (/\.(md|markdown)$/.test(n) || m === 'text/markdown') return 'markdown';
  if (/\.csv$/.test(n) || m.startsWith('text/csv')) return 'csv';
  if (/\.json$/.test(n) || m === 'application/json') return 'json';
  if (/\.(txt|text|log|ya?ml)$/.test(n) || m === 'text/plain') return 'text';
  if (/\.html?$/.test(n) || m === 'text/html') return 'html';                        // sandboxed preview (htmlPreviewFrame)
  if (/\.svg$/.test(n) || /svg/.test(m)) return 'other';                            // download only
  if (/\.(png|jpe?g|gif|webp)$/.test(n) || /^image\/(png|jpeg|gif|webp)/.test(m)) return 'image';
  if (/\.(mp4|webm|mov|m4v)$/.test(n) || m.startsWith('video/')) return 'video';
  if (/\.(mp3|m4a|wav|ogg|aac)$/.test(n) || m.startsWith('audio/')) return 'audio';
  if (/\.pdf$/.test(n) || m === 'application/pdf') return 'pdf';
  return 'other';
}
const TF_GLYPH = {markdown: 'MD', csv: 'CSV', json: 'JSON', text: 'TXT', image: 'IMG', video: 'VIDEO', audio: 'AUDIO', pdf: 'PDF', html: 'HTML'};
const tfGlyph = (kind, name) => TF_GLYPH[kind] || (String(name || '').match(/\.([a-z0-9]{1,4})$/i)?.[1] || 'FILE').toUpperCase();
const TF_TEXT_MAX = 2_000_000, TF_INLINE_IMAGE = 5_000_000;
// A version's picture for a tile, a chip or a cover: its thumb, a video's poster, else a small image itself.
function tfPicture(v, kind) {
  return v.thumb_url || (kind === 'video' ? v.poster_url : null)
    || (kind === 'image' && Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : null) || null;
}
// GET /v2/tasks/{id}/files answers {files: [{id, name, mime, current_version, archived, versions: [newest first]}]}.
// An older server has no such route: the task's attachments are its files, each at version 1.
function tfFromAttachments(list) {
  return (list || []).map(a => ({id: a.id, name: a.name || 'file', mime: a.content_type || a.mime || '', current_version: 1, archived: false,
    versions: [{n: 1, size: a.size, mime: a.content_type || a.mime || '', created_at: a.created_at || null, by: null, comment_id: null, note: null,
      ask: null, answers: [], url: a.url || `${API}/v2/files/${encodeURIComponent(a.id)}`, poster_url: null, thumb_url: null}]}));
}
function tfNorm(f) {
  const versions = (f.versions || []).map(v => ({...v, n: Number(v.n), answers: v.answers || []})).sort((a, b) => b.n - a.n);
  for (const v of versions) if (!v.url) v.url = `${API}/v2/files/${encodeURIComponent(f.id)}?v=${v.n}`;
  return {...f, versions, current_version: Number(f.current_version) || versions[0]?.n || 1};
}
const tfVersion = (f, n) => f.versions.find(v => v.n === Number(n)) || f.versions.find(v => v.n === f.current_version) || f.versions[0];
const tfAskOpen = v => !!(v?.ask && !(v.answers || []).length);
const tfFiles = d => (d?.taskFiles || []).filter(f => !f.archived && f.versions.length);
const TF_CACHE = new Map();          // task id -> its files, so a task reopened draws its strip at once

// ---- the strip
function tfTileHTML(f, open) {
  const v = tfVersion(f), kind = tfKind(f.name, v.mime || f.mime), pic = tfPicture(v, kind);
  const ratio = v.width && v.height ? v.width / v.height : kind === 'video' ? 16 / 9 : 4 / 3;
  const w = Math.round(Math.min(168, Math.max(88, 72 * ratio)));
  const age = ageShort(v.created_at);
  const dot = f.versions.some(tfAskOpen);
  return `<button type="button" class="tf-tile${open ? ' on' : ''}" data-tf-file="${esc(f.id)}" aria-expanded="${open}" style="width:${w}px" title="${esc(f.name)}">
    <span class="tf-thumb${pic ? ' tf-skel' : ''}"><span class="tf-glyph">${esc(tfGlyph(kind, f.name))}</span>${pic ? `<img alt="" data-tf-src="${esc(pic)}">` : ''}${kind === 'video' ? '<span class="tf-play" aria-hidden="true">▶</span>' : ''}</span>
    <span class="tf-name">${esc(f.name)}</span>
    <span class="tf-meta tnum">v${esc(v.n)}${age ? ` · ${esc(age)}` : ''}${dot ? '<span class="ask-dot" title="Open question"></span>' : ''}</span></button>`;
}
// The dialog's files section survives the dialog's redraws (a save, a poll): the same element moves into the new
// markup, so a playing video keeps playing and an open file stays open.
function tfAdopt(d, task) {
  const slot = $('[data-task-files]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tfEl && d.tfEl.dataset.task === id) { slot.replaceWith(d.tfEl); return; }
  d.tfEl = slot; slot.dataset.task = id; d.tfOpen = null;
  d.taskFiles = TF_CACHE.get(id) || tfFromAttachments(task.attachments);
  tfPaint(d);
}
async function tfLoad(d, id) {
  const opening = d.taskOpening, seq = d.tfLoadSeq = (d.tfLoadSeq || 0) + 1;
  const data = await v2Get(`/v2/tasks/${encodeURIComponent(id)}/files`);
  if (!d.open || String(d.dataset.task) !== String(id) || d.taskOpening !== opening || d.tfLoadSeq !== seq) return;
  if (!data && TF_CACHE.has(String(id))) return;          // a failed poll keeps what the last one found
  const files = Array.isArray(data?.files) ? data.files.map(tfNorm) : tfFromAttachments(d.liveTask?.attachments);
  TF_CACHE.set(String(id), files);
  if (TF_CACHE.size > 60) TF_CACHE.delete(TF_CACHE.keys().next().value);
  if (JSON.stringify(files) === JSON.stringify(d.taskFiles)) { if (d.tfEl) tfViewPaint(d); return; }   // a viewer that waited (playing, answering) catches up
  d.taskFiles = files;
  tfPaint(d);
  if (TASK_CHAT?.dialog === d && TASK_CHAT.data) { TASK_CHAT.rendered = ''; taskCommentsRender(TASK_CHAT, TASK_CHAT.data); }
}
function tfPaint(d) {
  qcardPaintPictures(d, tfFiles(d));       // the Needs you card's option pictures (question-card.js)
  const el = d.tfEl;
  if (!el) return;
  const files = tfFiles(d);
  if (d.tfOpen && !files.some(f => f.id === d.tfOpen.id)) d.tfOpen = null;
  const can = d.canComment !== false;
  el.hidden = !files.length && !can;
  el.classList.toggle('tsec-empty', !files.length);       // folds into the row of small add buttons (task-list.css)
  if (!$('.tf-strip', el)) el.innerHTML = `<h3 class="rail-h">Files<span class="muted tf-status" data-tf-status role="status"></span><button type="button" class="prop-add" data-tf-add aria-label="Add files" title="Add files, or drop them on this task, or paste a screenshot">${TL_ICON.plus}</button></h3>
    <input type="file" multiple hidden data-tf-input aria-label="Add files to this task"><div class="tf-strip"></div><div class="tf-view" data-tf-view hidden></div>`;
  $('[data-tf-add]', el).hidden = !can;
  const strip = $('.tf-strip', el), html = files.map(f => tfTileHTML(f, d.tfOpen?.id === f.id)).join('')
    || '<p class="muted tf-empty">Drop files here or paste a screenshot.</p>';
  if (strip.dataset.html !== html) { strip.innerHTML = strip.dataset.html = html; tfLazy(strip); }
  tfViewPaint(d);
}
function tfOpen(d, id, n = null, opts = {}) {
  const f = tfFiles(d).find(x => x.id === id);
  if (!f) return false;
  d.tfOpen = {id, n: n == null ? null : Number(n), cmp: null};
  tfPaint(d);
  if (opts.scroll) d.tfEl.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  if (opts.focus) $(`.tf-tile[data-tf-file="${CSS.escape(id)}"]`, d.tfEl)?.focus({preventScroll: true});
  return true;
}
function tfClose(d) {
  const id = d.tfOpen?.id;
  d.tfOpen = null; tfPaint(d);
  if (id) $(`.tf-tile[data-tf-file="${CSS.escape(id)}"]`, d.tfEl)?.focus({preventScroll: true});
}

// ---- adding files: the + on Files, a drop anywhere on the task, or a pasted screenshot. One upload per file; a name
// the task already has becomes that file's next version.
async function tfAdd(d, files) {
  const id = String(d.dataset.task || ''), el = d.tfEl, opening = d.taskOpening;
  if (!id || !el || d.canComment === false || !files.length) return;
  const status = $('[data-tf-status]', el);
  let added = 0;
  try {
    for (const file of files) {
      status.textContent = files.length > 1 ? `Uploading ${added + 1} of ${files.length}…` : 'Uploading…';
      const fd = new FormData();
      fd.append('file', file, file.name);
      const response = await formFetch(`${API}/v2/tasks/${encodeURIComponent(id)}/files`, fd);
      if (!response.ok) throw new Error(apiError(await response.json().catch(() => ({})), response) || `Upload failed (${response.status})`);
      added++;
    }
  } catch (e) {
    toast(added ? `Added ${plural(added, 'file')}; ${e.message}` : e.message, true);
  } finally { status.textContent = ''; }
  if (!added) return;
  if (added === files.length) toast(`Added ${plural(added, 'file')}`);
  if (d.open && d.taskOpening === opening && String(d.dataset.task) === id) await tfLoad(d, id);
  if (TASKS_ST) void tasksLoad(TASKS_ST);
}
// Taking a file off a task: its owner, its requester, or someone who may move tasks (the server's rule).
function tfCanRemove(d) {
  const t = d.liveTask;
  return !!t && ([t.owner_id, t.requester_id].includes(myActor()) || canMove());
}
async function tfRemove(d, fid, button) {
  const id = String(d.dataset.task || ''), opening = d.taskOpening;
  const f = tfFiles(d).find(x => x.id === fid);
  button.disabled = true;
  try {
    await patch(`/v2/files/${encodeURIComponent(fid)}`, {archived: true});
  } catch (e) {
    toast(e.message, true);
    if (button.isConnected) { button.disabled = false; button.dataset.sure = ''; button.textContent = 'Remove'; }
    return;
  }
  toast(`Removed ${f?.name || 'the file'}`);
  if (!d.open || d.taskOpening !== opening || String(d.dataset.task) !== id) return;
  d.taskFiles = (d.taskFiles || []).filter(x => x.id !== fid);
  TF_CACHE.delete(id);
  tfClose(d);
  await tfLoad(d, id);
  if (TASK_CHAT?.dialog === d) { TASK_CHAT.rendered = ''; void taskChatRead(TASK_CHAT); }
  if (TASKS_ST) void tasksLoad(TASKS_ST);
}
// Files dragged onto a task or the new-task form land there; the zone lights up while they hover.
function fileDropTarget(zone, take) {
  let depth = 0;
  const on = v => zone.classList.toggle('over', v);
  zone.addEventListener('dragenter', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); depth++; on(true); });
  zone.addEventListener('dragover', ev => { if (!draggingFiles(ev)) return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) on(false); });
  zone.addEventListener('drop', ev => {
    if (!draggingFiles(ev)) return;
    ev.preventDefault(); depth = 0; on(false);
    take([...ev.dataTransfer.files]);
  });
}
// A pasted screenshot arrives as "image.png": each gets its own name, so a second paste is a new file rather than
// a new version of the first. Text on the clipboard keeps its normal paste.
function pastedImages(ev) {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return Array.from(ev.clipboardData?.items || [])
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile()).filter(Boolean)
    .map((f, i) => new File([f], `screenshot-${stamp}${i ? '-' + (i + 1) : ''}.${(f.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`, {type: f.type}));
}
function pasteImages(zone, take) {
  zone.addEventListener('paste', ev => {
    const images = pastedImages(ev);
    if (!images.length) return;
    if (!ev.clipboardData.getData('text/plain')) ev.preventDefault();
    take(images);
  });
}

// ---- the viewer, in place under the strip
const tfPlaying = box => [...box.querySelectorAll('video, audio')].some(m => !m.paused && !m.ended);
function tfViewPaint(d, force = false) {
  const box = $('[data-tf-view]', d.tfEl);
  if (!box) return;
  const open = d.tfOpen, f = open && tfFiles(d).find(x => x.id === open.id);
  if (!f) { if (!box.hidden) { box.querySelector('video, audio')?.pause(); box.hidden = true; box.innerHTML = ''; box.dataset.sig = ''; } return; }
  const v = tfVersion(f, open.n), sig = JSON.stringify([f, open, d.canComment !== false]);
  if (!force && box.dataset.sig === sig) return;
  if (!force && d.canComment !== false && (askBusy(box) || tfPlaying(box))) return;   // never under someone answering or watching
  const kind = tfKind(f.name, v.mime || f.mime);
  const asc = f.versions.slice().reverse();
  const cmp = open.cmp != null ? tfVersion(f, open.cmp) : null;
  const vers = f.versions.length > 1 ? `<span class="tf-vers" role="group" aria-label="Versions">${asc.map(x =>
    `<button type="button" data-tf-v="${x.n}" aria-pressed="${x.n === v.n}"${tfAskOpen(x) ? ' class="has-ask"' : ''}>v${x.n}</button>`).join('<span aria-hidden="true">·</span>')}</span>
    <button type="button" class="tf-cmp" data-tf-cmp aria-pressed="${!!cmp}">Compare</button>` : `<span class="tf-vers muted">v${v.n}</span>`;
  const others = cmp ? f.versions.filter(x => x.n !== v.n) : [];
  const refocus = box.contains(document.activeElement) && !document.activeElement.matches('[data-tf-v], [data-tf-cmp]')
    ? '.tf-tile.on' : document.activeElement?.matches?.('[data-tf-v], [data-tf-cmp]') && box.contains(document.activeElement)
      ? (document.activeElement.dataset.tfV ? `[data-tf-v="${document.activeElement.dataset.tfV}"]` : '[data-tf-cmp]') : '';
  box.hidden = false;
  box.dataset.sig = sig;
  box.querySelector('video, audio')?.pause();
  box.innerHTML = `<div class="tf-view-head"><strong class="tf-view-name" title="${esc(f.name)}">${esc(f.name)}</strong>${vers}
      ${cmp ? `<label class="tf-with">with <select data-tf-with aria-label="Compare with">${others.map(x => `<option value="${x.n}"${x.n === cmp.n ? ' selected' : ''}>v${x.n}</option>`).join('')}</select></label>` : ''}
      <span class="spacer"></span><span class="tf-break" aria-hidden="true"></span>${tfCanRemove(d) ? '<button type="button" class="ghost danger tf-rm" data-tf-remove>Remove</button>' : ''}<a class="tf-dl" href="${esc(v.url)}" download aria-label="Download ${esc(f.name)} v${v.n}">↓</a>
      <button type="button" class="ghost tf-x" data-tf-close aria-label="Close" title="Close (Esc)">✕</button></div>
    ${cmp ? tfCompareHTML(f, cmp, v, kind) : `<div class="tf-body">${tfBodyHTML(f, v, kind, d)}</div>`}
    ${tfVersionMetaHTML(d, f, v)}`;
  tfBodyWire(box);
  if (refocus) $(refocus, d.tfEl)?.focus({preventScroll: true});      // the redraw never drops the focus on the page
}
function tfBodyHTML(f, v, kind, d) {
  const url = esc(v.url), name = esc(f.name);
  const ratio = v.width && v.height ? ` style="aspect-ratio:${Number(v.width)}/${Number(v.height)}"` : '';
  const big = Number(v.size || 0) > TF_TEXT_MAX;
  if (kind === 'image') {
    const imgs = d ? tfFiles(d).filter(x => tfKind(x.name, tfVersion(x).mime || x.mime) === 'image') : [];
    const nav = imgs.length > 1 ? '<button type="button" class="tf-nav prev" data-tf-step="-1" aria-label="Previous image">‹</button><button type="button" class="tf-nav next" data-tf-step="1" aria-label="Next image">›</button>' : '';
    return `<div class="tf-media image media-skel"${ratio}><img src="${url}" alt="${name}" decoding="async">${nav}</div>`;
  }
  if (kind === 'video') return `<div class="tf-media video media-skel${v.poster_url ? ' ready' : ''}"${ratio || ' style="aspect-ratio:16/9"'}><video src="${url}"${v.poster_url ? ` poster="${esc(v.poster_url)}"` : ''} controls playsinline preload="metadata"></video></div>`;
  if (kind === 'audio') return `<div class="tf-media audio media-skel"><audio src="${url}" controls preload="metadata"></audio></div>`;
  if (kind === 'pdf' && v.thumb_url) return `<a class="tf-pdf" href="${url}" target="_blank" rel="noopener" aria-label="Open ${name}"><span class="tf-skel"${ratio}><img alt="" data-tf-src="${esc(v.thumb_url)}"></span><span class="tf-pdf-open">Open PDF ↗</span></a>`;
  if (kind === 'html' && v.size != null && Number(v.size) <= HTML_VIEW_LIMIT) return `<div class="tf-doc tf-html" data-tf-text="${url}" data-kind="html" data-name="${name}" aria-busy="true"><div class="tf-doc-skel"></div></div>`;
  if (['markdown', 'csv', 'json', 'text'].includes(kind) && !big) return `<div class="tf-doc" data-tf-text="${url}" data-kind="${kind}" aria-busy="true"><div class="tf-doc-skel"></div></div>`;
  return `<div class="tf-card"><span class="tf-glyph">${esc(tfGlyph(kind, f.name))}</span><span class="tf-card-t"><span class="tf-name">${name}</span><span class="muted tnum">${v.size != null ? esc(bytes(Number(v.size))) : ''}</span></span>
    ${kind === 'pdf' ? `<a href="${url}" target="_blank" rel="noopener">Open ↗</a>` : ''}<a href="${url}" download>Download</a></div>`;
}
// Two versions side by side; text reads as a line diff.
function tfCompareHTML(f, a, b, kind) {
  if (['markdown', 'csv', 'json', 'text'].includes(kind)) return `<div class="tf-body"><div class="tf-diff" data-tf-diff="${esc(a.url)}" data-tf-diff-b="${esc(b.url)}" aria-busy="true"><div class="tf-doc-skel"></div></div></div>`;
  return `<div class="tf-body tf-pair">${[a, b].map(x => `<div class="tf-side"><span class="tf-side-v tnum">v${x.n}</span>${tfBodyHTML(f, x, kind, null)}</div>`).join('')}</div>`;
}
function tfVersionMetaHTML(d, f, v) {
  const who = v.by ? actorLabel(v.by) : '';
  const line = [who, v.created_at ? ago(v.created_at) : '', v.size != null ? bytes(Number(v.size)) : ''].filter(Boolean).map(esc).join(' · ');
  return `<div class="tf-vmeta">${line ? `<div class="tf-vline muted">${v.by ? actorFace(v.by, 14) : ''}<span>${line}</span></div>` : ''}
    ${v.note ? `<div class="tf-note">${esc(v.note)}</div>` : ''}
    ${v.ask ? askHTML(v.ask, v.answers, {file: f.id, version: v.n}, v.ask.by || v.by, d.dataset.task, tfFiles(d), d.canComment !== false) : ''}</div>`;
}
const TF_TEXT = new Map();          // versioned url -> Promise<text>, for the visit
function tfText(url) {
  if (!TF_TEXT.has(url)) TF_TEXT.set(url, fetch(url, {credentials: 'same-origin'}).then(r => {
    if (!r.ok) throw new Error(r.status === 403 ? 'You do not have access to this file.' : `Preview unavailable (${r.status}).`);
    return r.text();
  }).catch(e => { TF_TEXT.delete(url); throw e; }));
  return TF_TEXT.get(url);
}
function tfJSONView(raw) {
  let data; try { data = JSON.parse(raw); } catch { return null; }
  const list = Array.isArray(data) ? data : null;
  if (list?.length && list.every(r => r && typeof r === 'object' && !Array.isArray(r))) {
    const keys = [...new Set(list.flatMap(r => Object.keys(r)))].slice(0, 40);
    const cell = x => x == null ? '' : typeof x === 'object' ? JSON.stringify(x) : String(x);
    return rowsView([keys, ...list.map(r => keys.map(k => cell(r[k])))], 200);
  }
  const pre = document.createElement('pre'); pre.className = 'plain'; pre.textContent = JSON.stringify(data, null, 2);
  return pre;
}
const TF_CLAMP_LINES = 30;
function tfBodyWire(box) {
  for (const wrap of box.querySelectorAll('.tf-media')) {
    const el = wrap.querySelector('img, video, audio');
    const ready = () => wrap.classList.add('ready');
    el.addEventListener(el.tagName === 'IMG' ? 'load' : 'loadedmetadata', ready, {once: true});
    el.addEventListener('error', () => { ready(); wrap.insertAdjacentHTML('afterend', '<p class="err">Preview unavailable.</p>'); }, {once: true});
    if (el.tagName === 'IMG' && el.complete && el.naturalWidth) ready();
  }
  for (const doc of box.querySelectorAll('[data-tf-text]')) {
    tfText(doc.dataset.tfText).then(raw => {
      if (!doc.isConnected) return;
      const kind = doc.dataset.kind;
      doc.removeAttribute('aria-busy');
      if (kind === 'csv') { doc.replaceChildren(csvView(raw, 200)); return; }
      if (kind === 'html') { doc.replaceChildren(htmlPreviewFrame(raw, doc.dataset.name)); return; }
      if (kind === 'json') { const view = tfJSONView(raw); if (view) { doc.replaceChildren(view); return; } }
      doc.innerHTML = kind === 'markdown' ? `<div class="md">${safeMd(raw)}</div>` : `<pre class="plain">${esc(raw)}</pre>`;
      if (raw.split('\n').length > TF_CLAMP_LINES) {
        doc.classList.add('clamped');
        doc.insertAdjacentHTML('beforeend', '<button type="button" class="linkish tf-more" data-tf-more>Show all</button>');
      }
    }, e => { if (doc.isConnected) { doc.removeAttribute('aria-busy'); doc.innerHTML = `<p class="err">${esc(e.message)}</p>`; } });
  }
  for (const diff of box.querySelectorAll('[data-tf-diff]')) {
    Promise.all([tfText(diff.dataset.tfDiff), tfText(diff.dataset.tfDiffB)]).then(([a, b]) => {
      if (!diff.isConnected) return;
      diff.removeAttribute('aria-busy');
      diff.innerHTML = tfDiffHTML(a, b);
    }, e => { if (diff.isConnected) diff.innerHTML = `<p class="err">${esc(e.message)}</p>`; });
  }
  tfLazy(box);
}
// A line diff: common head and tail trimmed, the middle by longest common subsequence (a very long middle shows as
// replaced). Unchanged runs longer than six lines fold to a count.
function tfDiff(a, b) {
  const A = String(a).split('\n'), B = String(b).split('\n');
  let head = 0; while (head < A.length && head < B.length && A[head] === B[head]) head++;
  let tail = 0; while (tail < A.length - head && tail < B.length - head && A[A.length - 1 - tail] === B[B.length - 1 - tail]) tail++;
  const a1 = A.slice(head, A.length - tail), b1 = B.slice(head, B.length - tail), out = A.slice(0, head).map(l => [' ', l]);
  if (a1.length * b1.length > 4e6) { out.push(...a1.map(l => ['-', l]), ...b1.map(l => ['+', l])); }
  else {
    const n = a1.length, m = b1.length, L = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a1[i] === b1[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a1[i] === b1[j]) { out.push([' ', a1[i]]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push(['-', a1[i++]]);
      else out.push(['+', b1[j++]]);
    }
    while (i < n) out.push(['-', a1[i++]]);
    while (j < m) out.push(['+', b1[j++]]);
  }
  out.push(...A.slice(A.length - tail).map(l => [' ', l]));
  return out;
}
function tfDiffHTML(a, b) {
  const lines = tfDiff(a, b), html = [];
  if (!lines.some(([op]) => op !== ' ')) return '<p class="muted">No changes.</p>';
  for (let i = 0; i < lines.length;) {
    if (lines[i][0] === ' ') {
      let j = i; while (j < lines.length && lines[j][0] === ' ') j++;
      const run = j - i, edge = i === 0 || j === lines.length;
      if (run > 6) {
        const keepA = i === 0 ? 0 : 3, keepB = j === lines.length ? 0 : 3;
        for (const [, l] of lines.slice(i, i + keepA)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
        html.push(`<span class="fold">⋯ ${run - keepA - keepB} lines</span>`);
        for (const [, l] of lines.slice(j - keepB, j)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
      } else for (const [, l] of lines.slice(i, j)) html.push(`<span class="ctx">  ${esc(l)}</span>`);
      void edge; i = j; continue;
    }
    const [op, l] = lines[i++];
    html.push(`<span class="${op === '+' ? 'add' : 'del'}">${op} ${esc(l)}</span>`);
  }
  return `<pre class="tf-diff-lines">${html.join('')}</pre>`;
}
// Clicks and keys inside a task dialog's files section (wired once per dialog by taskDialogWire).
function tfWire(d) {
  d.addEventListener('click', ev => {
    if (!d.tfEl?.contains(ev.target)) return;
    const tile = ev.target.closest('.tf-tile');
    if (tile) { const id = tile.dataset.tfFile; d.tfOpen?.id === id ? tfClose(d) : tfOpen(d, id); return; }
    if (ev.target.closest('[data-tf-close]')) { tfClose(d); return; }
    if (ev.target.closest('[data-tf-add]')) { $('[data-tf-input]', d.tfEl).click(); return; }
    const rm = ev.target.closest('[data-tf-remove]');
    if (rm && d.tfOpen) {
      // The first press asks; the second removes. Tico keeps no way back for a task's file.
      if (rm.dataset.sure !== '1') { rm.dataset.sure = '1'; rm.textContent = 'Really remove?'; return; }
      void tfRemove(d, d.tfOpen.id, rm); return;
    }
    const ver = ev.target.closest('[data-tf-v]');
    if (ver && d.tfOpen) {
      const n = Number(ver.dataset.tfV);
      if (d.tfOpen.cmp === n) d.tfOpen.cmp = tfVersion(tfFiles(d).find(f => f.id === d.tfOpen.id), d.tfOpen.n).n;
      d.tfOpen.n = n; tfViewPaint(d); return;
    }
    if (ev.target.closest('[data-tf-cmp]') && d.tfOpen) {
      const f = tfFiles(d).find(x => x.id === d.tfOpen.id), v = tfVersion(f, d.tfOpen.n);
      d.tfOpen.cmp = d.tfOpen.cmp != null ? null : (f.versions.find(x => x.n < v.n) || f.versions.find(x => x.n !== v.n)).n;
      tfViewPaint(d); return;
    }
    const step = ev.target.closest('[data-tf-step]');
    if (step) { tfStepImage(d, Number(step.dataset.tfStep)); return; }
    if (ev.target.closest('[data-tf-more]')) { const doc = ev.target.closest('.tf-doc'); doc.classList.remove('clamped'); ev.target.remove(); }
  });
  d.addEventListener('change', ev => {
    if (ev.target.matches('[data-tf-with]') && d.tfOpen) { d.tfOpen.cmp = Number(ev.target.value); tfViewPaint(d); }
    if (ev.target.matches('[data-tf-input]')) { const files = [...ev.target.files]; ev.target.value = ''; void tfAdd(d, files); }
  });
  const take = files => { if (d.tfEl && d.canComment !== false) void tfAdd(d, files); };
  fileDropTarget(d, take);
  pasteImages(d, take);
  d.addEventListener('cancel', ev => { if (d.tfJustClosed) { ev.preventDefault(); ev.stopImmediatePropagation(); } });
}
// Esc folds an open file before it closes the task; ←/→ step through the task's images. On the document, in capture:
// a redraw can leave the focus on the page, and the task must not see the key.
document.addEventListener('keydown', ev => {
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  const d = ev.target.closest?.('dialog') || $('dialog:modal') || $('#task-peek[open]');
  if (!d?.tfOpen || !d.tfEl || ev.target.closest?.('.prop-pop')) return;
  const typing = ev.target.closest?.('input, textarea, [contenteditable]');
  if (ev.key === 'Escape' && !typing) {
    ev.preventDefault(); ev.stopPropagation(); tfClose(d);
    d.tfJustClosed = true; setTimeout(() => { d.tfJustClosed = false; }, 0);
    return;
  }
  if ((ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') && !typing && !ev.target.closest?.('select, video, audio')
    && $('.tf-view .tf-media.image', d.tfEl) && !$('.tf-pair', d.tfEl)) {
    ev.preventDefault(); ev.stopPropagation(); tfStepImage(d, ev.key === 'ArrowRight' ? 1 : -1);
  }
}, true);
function tfStepImage(d, delta) {
  const imgs = tfFiles(d).filter(x => tfKind(x.name, tfVersion(x).mime || x.mime) === 'image');
  const at = imgs.findIndex(x => x.id === d.tfOpen?.id);
  if (imgs.length < 2 || at < 0) return;
  tfOpen(d, imgs[(at + delta + imgs.length) % imgs.length].id, null, {focus: true});
}

// ---- comments that carried files: small thumbs (images) or a chip, each opening its tile at that version
// The title the sender gave an attachment, or '' when there is none: the server stores an untitled file's name as its
// title, so a title that is just the file's name counts as none (as in chat, chatChipsHTML).
function tfAttachTitle(a, f) {
  const title = String(a?.title || '').trim();
  return title && title !== a?.name && title !== f?.name ? title : '';
}
function tfCommentFiles(m, files) {
  const out = [];
  for (const f of files) for (const v of f.versions) if (v.comment_id != null && String(v.comment_id) === String(m.id)) out.push({f, v});
  for (const a of m.refs?.attachments || []) {
    const [aid, an] = String(a.id || '').split('@');
    const f = files.find(x => x.id === aid) || files.find(x => x.name === a.name);
    const n = a.version ?? an;
    const v = f && (n != null ? f.versions.find(x => x.n === Number(n)) : null) || f?.versions.find(x => x.comment_id == null) || f && tfVersion(f);
    const title = tfAttachTitle(a, f);
    if (f && v) {
      const had = out.find(x => x.f === f && x.v === v);
      if (had) had.title ||= title; else out.push({f, v, title});
    } else out.push({loose: a});
  }
  return out;
}
// The comment's first visuals are cards (ui/app/visual-cards.js); its other files stay thumbs and chips.
function tfCommentFilesHTML(m, files) {
  return visualCardsHTML(m.refs?.attachments || [], {key: m.id || '', chips: (rest, shown) => tfCommentChipsHTML(m, files, shown)});
}
function tfCommentChipsHTML(m, files, shown = new Set()) {
  const list = tfCommentFiles(m, files).filter(x => x.loose ? !shown.has(`${x.loose.file_id}@${x.loose.version}`) : !shown.has(`${x.f.id}@${x.v.n}`));
  if (!list.length) return '';
  // A file the comment attached with a title (refs.attachments[].title) shows that title; the name stays in the tooltip.
  return `<div class="tc-files">${list.map(({f, v, loose, title}) => {
    if (loose) { const t = tfAttachTitle(loose); return `<a class="tc-file" href="${API}/v2/files/${encodeURIComponent(loose.id)}"${t ? ` title="${esc(loose.name || '')}"` : ''}>${esc(t || loose.name || 'file')}</a>`; }
    const kind = tfKind(f.name, v.mime || f.mime), pic = kind === 'image' ? tfPicture(v, kind) : null;
    const name = title || f.name, label = title ? `${title} (${f.name} v${v.n})` : `${f.name} v${v.n}`;
    const thumb = pic && `<button type="button" class="tc-thumb tf-skel" data-tf-jump="${esc(f.id)}" data-tf-n="${v.n}" title="${esc(label)}" aria-label="Open ${esc(label)}"><img alt="" data-tf-src="${esc(pic)}"></button>`;
    return pic ? (title ? `<span class="tc-titled">${thumb}<span class="tc-file-title" aria-hidden="true">${esc(title)}</span></span>` : thumb)
      : `<button type="button" class="tc-file" data-tf-jump="${esc(f.id)}" data-tf-n="${v.n}" aria-label="Open ${esc(label)}"${title ? ` title="${esc(f.name)}"` : ''}><span class="tc-kind">${esc(tfGlyph(kind, f.name))}</span>${esc(name)} <span class="muted tnum">v${v.n}</span></button>`;
  }).join('')}</div>`;
}
document.addEventListener('click', ev => {
  const jump = ev.target.closest('[data-tf-jump]');
  if (!jump) return;
  const d = jump.closest('dialog');
  if (d) tfOpen(d, jump.dataset.tfJump, jump.dataset.tfN, {scroll: true});
});

// ---- questions. An ask: {questions: [{id, header, question, options: [{label, description, file}], multi, other}],
// who}. On a comment it is that comment's (an 'ask' message, its refs.questions); on a file version it is the
// version's. Anyone who may comment answers, except whoever asked; the answer is a comment the thread then shows.
// Readers (no comment rights: GET /v2/tasks/{id} `can_comment`) see the question, its choices and the answers, no controls.
// Someone is part-way through an answer: focus in a question, a choice picked or words typed but not sent. A redraw
// then waits; a question being sent (aria-busy) does not count, so the thread redraws once it lands.
// Drafts belong to one dialog opening and one question target; a task switch or close drops them.
const askDraftKey = el => (el.closest('.task-comments') ? 'comment:' : 'file:') + el.dataset.ask;
function askDraftCapture(d) {
  if (!d.askDrafts) d.askDrafts = new Map();
  for (const el of d.querySelectorAll('.ask:not([aria-busy]):not(.ro):not(.mine)')) {
    const choices = {};
    for (const q of el.querySelectorAll('.ask-q')) choices[q.dataset.qid] = [...q.querySelectorAll('.ask-opt[aria-pressed="true"]')].map(b => b.dataset.label);
    d.askDrafts.set(askDraftKey(el), {choices, other: $('.ask-other', el)?.value || '', again: el.classList.contains('again')});
  }
}
function askDraftRestore(root, d) {
  for (const el of root.querySelectorAll('.ask:not(.ro):not(.mine)')) {
    const draft = d.askDrafts?.get(askDraftKey(el));
    if (!draft || el.dataset.task !== String(d.dataset.task) || d.canComment === false) continue;
    for (const q of el.querySelectorAll('.ask-q')) for (const opt of q.querySelectorAll('.ask-opt'))
      opt.setAttribute('aria-pressed', String((draft.choices[q.dataset.qid] || []).includes(opt.dataset.label)));
    const other = $('.ask-other', el); if (other) other.value = draft.other;
    el.classList.toggle('again', draft.again);
  }
}
function askBusy(root) {
  return [...root.querySelectorAll('.ask:not([aria-busy])')].some(el => el.contains(document.activeElement) || el.classList.contains('again')
    || el.querySelector('.ask-opt[aria-pressed="true"]') || $('.ask-other', el)?.value.trim());
}
function askOf(m) {
  if (m.ask?.questions) return m.ask;
  return Array.isArray(m.refs?.questions) ? {questions: m.refs.questions, who: m.refs.who || null} : null;
}
function askTargetOf(m) {
  const t = m.refs?.target;
  return t?.file ? {file: t.file, version: Number(t.version)} : {comment: m.id};
}
function askAnswerWords(a, ask) {
  if (a.dismiss) return 'dismissed';
  // an answer from an older runner is a plain reply: {by, text, at}
  if (!a.answers && a.text) return String(a.text);
  const qs = ask?.questions || [];
  const parts = Object.entries(a.answers || {}).filter(([, l]) => (l || []).length).map(([qid, labels]) => {
    const q = qs.find(x => x.id === qid);
    return (qs.length > 1 && q?.header ? q.header + ': ' : '') + labels.join(', ');
  });
  return parts.join('; ');
}
function askOptionHTML(o, files, ro = false) {
  let pic = '';
  const [fid, fn] = String(o.file || '').split('@');
  const f = fid && files.find(x => x.id === fid);
  if (f) {
    const v = tfVersion(f, fn), kind = tfKind(f.name, v.mime || f.mime), src = tfPicture(v, kind);
    if (src) pic = `<span class="ask-pic tf-skel"><img alt="" data-tf-src="${esc(src)}"></span>`;
  }
  if (ro) return `<span class="ask-opt ro"${o.description ? ` title="${esc(o.description)}"` : ''}>${pic}<span class="ask-l">${esc(o.label)}</span>${o.description ? `<span class="ask-d">${esc(o.description)}</span>` : ''}</span>`;
  return `<button type="button" class="ask-opt" data-label="${esc(o.label)}" aria-pressed="false"${o.description ? ` title="${esc(o.description)}"` : ''}>${pic}<span class="ask-l">${esc(o.label)}</span>${o.description ? `<span class="ask-d">${esc(o.description)}</span>` : ''}</button>`;
}
function askHTML(ask, answers, target, asker, taskId, files = [], canAnswer = true) {
  const qs = (ask?.questions || []).filter(q => q && q.id);
  if (!qs.length) return '';
  answers = answers || [];
  const mine = !!asker && asker === myActor(), ro = !mine && !canAnswer;
  const answered = answers.length > 0;
  const other = qs.some(q => q.other !== false);
  const instant = qs.length === 1 && !qs[0].multi;
  const body = qs.map(q => `<div class="ask-q" data-qid="${esc(q.id)}" data-multi="${q.multi ? 1 : 0}">
      <div class="ask-line">${q.header ? `<span class="ask-chip">${esc(q.header)}</span>` : ''}<span class="ask-text">${esc(q.question || '')}</span></div>
      ${mine || !(q.options || []).length ? '' : `<div class="ask-opts"${ro ? '' : ` role="group" aria-label="${esc(q.header || q.question || 'Options')}"`}${q.multi ? ' data-multi' : ''}>${q.options.map(o => askOptionHTML(o, files, ro)).join('')}</div>`}
    </div>`).join('');
  const form = mine || ro ? '' : `<div class="ask-form">${other ? '<input class="ask-other" type="text" maxlength="2000" placeholder="Other…" aria-label="Other answer">' : ''}
      <div class="ask-foot">${!instant || other ? '<button type="button" class="primary ask-send" data-ask-send>Send</button>' : ''}<button type="button" class="linkish ask-dismiss" data-ask-dismiss>Dismiss</button><span class="muted ask-status" role="status"></span></div></div>`;
  const list = answered ? `<ul class="ask-answers">${answers.map(a => `<li>${a.by ? actorFace(a.by, 14) : ''}<b>${esc(a.by ? actorLabel(a.by) : 'Someone')}</b>${a.quoted_by ? `<span class="muted">(quoted by ${esc(actorLabel(a.quoted_by))})</span>` : ''}
      <span class="${a.dismiss ? 'muted' : ''}">${esc(askAnswerWords(a, ask))}</span>${a.other ? `<span class="ask-o">${esc(a.other)}</span>` : ''}${a.at ? `<time class="muted tnum" title="${esc(fmt(a.at))}">${esc(ageShort(a.at))}</time>` : ''}</li>`).join('')}</ul>` : '';
  return `<div class="ask${answered ? ' answered' : ''}${mine ? ' mine' : ''}${ro ? ' ro' : ''}" data-ask="${esc(JSON.stringify(target))}" data-task="${esc(taskId)}"${instant ? ' data-instant' : ''}>
    ${body}${list}${!mine && !ro && answered ? '<button type="button" class="linkish ask-again" data-ask-again>Answer</button>' : ''}${form}</div>`;
}
async function askSubmit(el, dismiss = false) {
  const status = $('.ask-status', el), dialog = el.closest('dialog'), opening = dialog?.taskOpening;
  const answers = {};
  for (const q of el.querySelectorAll('.ask-q')) {
    const picked = [...q.querySelectorAll('.ask-opt[aria-pressed="true"]')].map(b => b.dataset.label);
    if (picked.length) answers[q.dataset.qid] = picked;
  }
  const other = $('.ask-other', el)?.value.trim() || '';
  // An Other answer names the questions it answers, so it is bound to the wording shown (a reworded question has new ids).
  if (other) for (const q of el.querySelectorAll('.ask-q')) answers[q.dataset.qid] ||= [];
  if (!dismiss && !Object.keys(answers).length && !other) { if (status) status.textContent = 'Pick an answer'; return; }
  const target = JSON.parse(el.dataset.ask), task = el.dataset.task;
  const body = dismiss ? {target, answers: {}, dismiss: true} : {target, answers, ...(other ? {other} : {})};
  const buttons = [...el.querySelectorAll('button, input')];
  buttons.forEach(b => { b.disabled = true; });
  el.setAttribute('aria-busy', 'true');
  if (status) status.textContent = '';
  try {
    await post(`/v2/tasks/${encodeURIComponent(task)}/answers`, body);
    const d = dialog;
    if (d?.taskOpening !== opening || !d?.open || String(d.dataset.task) !== String(task)) return;
    for (const prefix of ['comment:', 'file:']) d.askDrafts?.delete(prefix + el.dataset.ask);
    for (const current of d.querySelectorAll('.ask')) if (current.dataset.ask === el.dataset.ask) {
      for (const opt of current.querySelectorAll('.ask-opt')) opt.setAttribute('aria-pressed', 'false');
      const other = $('.ask-other', current); if (other) other.value = '';
      current.classList.remove('again');
    }
    if (el.contains(document.activeElement)) document.activeElement.blur();
    if (TASK_CHAT && TASK_CHAT.dialog === d && taskChatCurrent(TASK_CHAT)) { TASK_CHAT.rendered = ''; await taskChatRead(TASK_CHAT); }
    if (d) await tfLoad(d, task);
    if (TASKS_ST) void tasksLoad(TASKS_ST);
  } catch (e) {
    buttons.forEach(b => { b.disabled = false; });
    el.removeAttribute('aria-busy');
    if (status?.isConnected) status.textContent = `Not sent: ${e.message}`;
  }
}
document.addEventListener('click', ev => {
  const el = ev.target.closest('.ask');
  if (!el || el.getAttribute('aria-busy') || el.classList.contains('ro')) return;
  const opt = ev.target.closest('.ask-opt');
  if (opt) {
    const group = opt.closest('.ask-opts');
    if (!group.hasAttribute('data-multi')) for (const b of group.querySelectorAll('.ask-opt')) if (b !== opt) b.setAttribute('aria-pressed', 'false');
    opt.setAttribute('aria-pressed', String(opt.getAttribute('aria-pressed') !== 'true'));
    if (el.hasAttribute('data-instant') && opt.getAttribute('aria-pressed') === 'true') void askSubmit(el);
    return;
  }
  if (ev.target.closest('[data-ask-send]')) { void askSubmit(el); return; }
  if (ev.target.closest('[data-ask-dismiss]')) { void askSubmit(el, true); return; }
  if (ev.target.closest('[data-ask-again]')) { el.classList.add('again'); $('.ask-opt, .ask-other', el)?.focus(); }
});
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Enter' || !ev.target.matches?.('.ask-other') || ev.isComposing) return;
  ev.preventDefault(); void askSubmit(ev.target.closest('.ask'));
});

// ---- the board: a cover from the newest picture (an image's thumb or a video's poster), and a dot for an open question.
// `cover` ({url, width, height}) when the server sends one; else the newest image or video among the attachments.
function taskCover(t) {
  if (t.cover?.url) return t.cover;
  const list = (t.attachments || []).filter(a => ['image', 'video'].includes(tfKind(a.name, a.content_type)));
  const a = list.at(-1);
  if (!a) return null;
  const base = String(a.url || `${API}/v2/files/${encodeURIComponent(a.id)}`).split('?')[0];
  const image = tfKind(a.name, a.content_type) === 'image';
  return {url: base + (image ? '/thumb' : '/poster'), alt: image && Number(a.size || 0) <= TF_INLINE_IMAGE ? base : ''};
}
function taskCoverHTML(t) {
  const c = taskCover(t);
  if (!c) return '';
  const ratio = c.width && c.height ? ` style="aspect-ratio:${Number(c.width)}/${Number(c.height)}"` : '';
  return `<div class="bcard-cover tf-skel"${ratio}><img alt="" data-tf-src="${esc(c.url)}"${c.alt ? ` data-tf-alt="${esc(c.alt)}"` : ''}></div>`;
}
const taskAskDot = t => Number(t?.open_asks) > 0 ? '<span class="ask-dot" title="Open question" aria-label="Open question"></span>' : '';
