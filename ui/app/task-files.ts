/* ui/app/task-files.ts — A task's files and questions: the strip of tiles (one per file, at its newest version), the
   viewer that opens over the task, near full screen (versions, Compare, who added each and its question), file chips in comments,
   the question block (an ask on a comment or on a file version) and the board's covers.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- the shapes this file reads: a task's files (GET /v2/tasks/{id}/files), their versions, and the questions on them
type TfKind = 'markdown' | 'csv' | 'json' | 'text' | 'html' | 'image' | 'video' | 'audio' | 'pdf' | 'other';
interface TfAttachment { id: string; name?: string; title?: string; mime?: string; content_type?: string; size?: number; created_at?: string; url?: string; version?: number | string | null; file_id?: string | null; }
interface TfAnswer { by?: string | null; text?: string; answers?: Record<string, string[]> | null; dismiss?: boolean; other?: string | null; at?: string | null; quoted_by?: string | null; }
interface TfOption { label: string; description?: string | null; file?: string | null; }
interface TfQuestion { id: string; header?: string | null; question?: string | null; options?: TfOption[]; multi?: boolean; other?: boolean; }
interface TfAsk { questions?: TfQuestion[]; who?: string | null; by?: string | null; }
interface TfVersion {
  n: number; size?: number | null | undefined; mime?: string; created_at?: string | null; by?: string | null; comment_id?: number | string | null;
  note?: string | null; ask?: TfAsk | null; answers: TfAnswer[]; url: string; poster_url?: string | null; thumb_url?: string | null;
  width?: number | null; height?: number | null;
}
interface TfFile { id: string; name: string; mime?: string; current_version: number; archived?: boolean; versions: TfVersion[]; }
// The task dialog's files section, as the page keeps it on the dialog (every field optional: a TaskDialog is one too).
interface TfDialog extends TaskDialog {
  tfEl?: HTMLElement | null;
  tfOpen?: {id: string; n: number | null; cmp: number | null} | null;
  tfLoadSeq?: number;
  taskOpening?: unknown;
  liveTask?: (PropTask & {attachments?: TfAttachment[] | null}) | null;
  taskFiles?: TfFile[] | null;
  canComment?: boolean;
  tfJustClosed?: boolean;
  // Opens one of the task's files in its files section (tfOpen), for code outside this page part: set with tfEl.
  tfOpenFile?: (id: string | undefined, n?: string | number | null, opts?: {scroll?: boolean; focus?: boolean}) => boolean;
}
type TfCommentFile = {f: TfFile; v: TfVersion; title?: string; loose?: undefined} | {loose: TfAttachment; f?: undefined; v?: undefined; title?: undefined};
// A comment or chat message as the thread shows it: its refs name the files and the questions it carries.
interface TfMessage { id: string; ask?: TfAsk | null; refs?: {attachments?: TfAttachment[] | null; questions?: TfQuestion[] | null; who?: string | null; target?: {file?: string | null; version?: number | string | null} | null} | null; sender_id?: string | null; answers?: TfAnswer[] | null; body?: string | null; }

// ---- thumbnails: lazy, sized, a skeleton until they arrive. A versioned address never changes, so the browser's cache
// serves a revisit; this keeps the decoded picture too, so the modal and the pipeline redraw it at once.
const TF_THUMBS = new Map<string, HTMLImageElement>();          // url -> decoded Image, newest use last
function tfThumbLoad(url: string): Promise<HTMLImageElement> {
  const hit = TF_THUMBS.get(url);
  if (hit) { TF_THUMBS.delete(url); TF_THUMBS.set(url, hit); return Promise.resolve(hit); }
  const img = new Image(); img.decoding = 'async'; img.src = url;
  return img.decode().then(() => {
    TF_THUMBS.set(url, img);
    if (TF_THUMBS.size > 240) { const oldest = TF_THUMBS.keys().next().value; if (oldest !== undefined) TF_THUMBS.delete(oldest); }
    return img;
  });
}
// <img data-tf-src="…" data-tf-alt="…"> inside a .tf-skel box: the box turns .ready when it shows, .failed when
// neither address loads (a cover then hides; a tile keeps its type mark).
function tfShow(img: HTMLImageElement, url: string = img.dataset.tfSrc ?? '') {
  const box = img.closest('.tf-skel') || img.parentElement!;
  tfThumbLoad(url).then(() => { img.src = url; box.classList.add('ready'); }, () => {
    const alt = img.dataset.tfAlt;
    if (alt && alt !== url) { delete img.dataset.tfAlt; tfShow(img, alt); } else box.classList.add('failed');
  });
}
const TF_IO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  for (const e of entries) if (e.isIntersecting && e.target instanceof HTMLImageElement) { TF_IO!.unobserve(e.target); tfShow(e.target); }
}, {rootMargin: '200px'}) : null;
function tfLazy(root: ParentNode = document): void {
  for (const img of root.querySelectorAll<HTMLImageElement>('img[data-tf-src]:not([data-tf-on])')) {
    img.dataset.tfOn = '1';
    if (TF_THUMBS.has(img.dataset.tfSrc ?? '') || !TF_IO) tfShow(img); else TF_IO.observe(img);
  }
}
let TF_LAZY_QUEUED = false;
new MutationObserver(() => {
  if (TF_LAZY_QUEUED) return;
  TF_LAZY_QUEUED = true;
  setTimeout(() => { TF_LAZY_QUEUED = false; tfLazy(); }, 60);
}).observe(document.documentElement, {childList: true, subtree: true});

// ---- what a file is and how it shows
function tfKind(name: string | null | undefined, mime: string | null | undefined): TfKind {
  const n = String(name || '').toLowerCase().split(/[?#]/)[0] ?? '', m = String(mime || '').toLowerCase();
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
const TF_GLYPH: Partial<Record<TfKind, string>> = {markdown: 'MD', csv: 'CSV', json: 'JSON', text: 'TXT', image: 'IMG', video: 'VIDEO', audio: 'AUDIO', pdf: 'PDF', html: 'HTML'};
const tfGlyph = (kind: TfKind, name: string | null | undefined): string => TF_GLYPH[kind] || (String(name || '').match(/\.([a-z0-9]{1,4})$/i)?.[1] || 'FILE').toUpperCase();
const TF_TEXT_MAX = 2_000_000, TF_INLINE_IMAGE = 5_000_000;
// A version's picture for a tile, a chip or a cover: its thumb, a video's poster, else a small image itself.
function tfPicture(v: TfVersion, kind: TfKind): string | null {
  return v.thumb_url || (kind === 'video' ? v.poster_url : null)
    || (kind === 'image' && Number(v.size || 0) <= TF_INLINE_IMAGE ? v.url : null) || null;
}
// GET /v2/tasks/{id}/files answers {files: [{id, name, mime, current_version, archived, versions: [newest first]}]}.
// An older server has no such route: the task's attachments are its files, each at version 1.
function tfFromAttachments(list: TfAttachment[] | null | undefined): TfFile[] {
  return (list || []).map(a => ({id: a.id, name: a.name || 'file', mime: a.content_type || a.mime || '', current_version: 1, archived: false,
    versions: [{n: 1, size: a.size, mime: a.content_type || a.mime || '', created_at: a.created_at || null, by: null, comment_id: null, note: null,
      ask: null, answers: [], url: a.url || `${API}/v2/files/${encodeURIComponent(a.id)}`, poster_url: null, thumb_url: null}]}));
}
function tfNorm(f: TfFile): TfFile {
  const versions = (f.versions || []).map(v => ({...v, n: Number(v.n), answers: v.answers || []})).sort((a, b) => b.n - a.n);
  for (const v of versions) if (!v.url) v.url = `${API}/v2/files/${encodeURIComponent(f.id)}?v=${v.n}`;
  return {...f, versions, current_version: Number(f.current_version) || versions[0]?.n || 1};
}
const tfVersion = (f: TfFile, n?: number | string | null): TfVersion => f.versions.find(v => v.n === Number(n)) || f.versions.find(v => v.n === f.current_version) || f.versions[0]!;
const tfAskOpen = (v: TfVersion | null | undefined): boolean => !!(v?.ask && !(v.answers || []).length);
const tfFiles = (d: TfDialog | null | undefined): TfFile[] => (d?.taskFiles || []).filter(f => !f.archived && f.versions.length);
const TF_CACHE = new Map<string, TfFile[]>();          // task id -> its files, so a task reopened draws its strip at once

// ---- the strip
function tfTileHTML(f: TfFile, open: boolean): string {
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
function tfAdopt(d: TfDialog, task: {id: string | number; attachments?: unknown}): void {
  const slot = $('[data-task-files]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tfEl && d.tfEl.dataset.task === id) { slot.replaceWith(d.tfEl); tfViewModal($('[data-tf-view]', d.tfEl)); return; }
  d.tfEl = slot; slot.dataset.task = id; d.tfOpen = null;
  d.tfOpenFile = (fid, n = null, opts = {}) => tfOpen(d, fid, n, opts);
  d.taskFiles = TF_CACHE.get(id) || tfFromAttachments(task.attachments as TfAttachment[] | null | undefined);
  tfPaint(d);
}
async function tfLoad(d: TfDialog, id: string | number): Promise<void> {
  const opening = d.taskOpening, seq = d.tfLoadSeq = (d.tfLoadSeq || 0) + 1;
  const data = await v2Get(`/v2/tasks/${encodeURIComponent(id)}/files`);
  if (!d.open || String(d.dataset.task) !== String(id) || d.taskOpening !== opening || d.tfLoadSeq !== seq) return;
  if (!data && TF_CACHE.has(String(id))) return;          // a failed poll keeps what the last one found
  const files = Array.isArray(data?.files) ? data.files.map(tfNorm) : tfFromAttachments(d.liveTask?.attachments);
  TF_CACHE.set(String(id), files);
  if (TF_CACHE.size > 60) { const oldest = TF_CACHE.keys().next().value; if (oldest !== undefined) TF_CACHE.delete(oldest); }
  if (JSON.stringify(files) === JSON.stringify(d.taskFiles)) { if (d.tfEl) tfViewPaint(d); return; }   // a viewer that waited (playing, answering) catches up
  d.taskFiles = files;
  tfPaint(d);
  if (TASK_CHAT?.dialog === d && TASK_CHAT.data) { TASK_CHAT.rendered = ''; taskCommentsRender(TASK_CHAT, TASK_CHAT.data); }
}
function tfPaint(d: TfDialog): void {
  const el = d.tfEl;
  if (!el) return;
  const files = tfFiles(d);
  const open = d.tfOpen; if (open && !files.some(f => f.id === open.id)) d.tfOpen = null;
  const can = d.canComment !== false;
  el.hidden = !files.length && !can;
  el.classList.toggle('tsec-empty', !files.length);       // folds into the row of small add buttons (task-list.css)
  if (!$('.tf-strip', el)) {
    el.innerHTML = `<h3 class="rail-h">Files<span class="muted tf-status" data-tf-status role="status"></span><button type="button" class="prop-add" data-tf-add aria-label="Add files" title="Add files, or drop them on this task, or paste a screenshot">${TL_ICON.plus}</button></h3>
    <input type="file" multiple hidden data-tf-input aria-label="Add files to this task"><div class="tf-strip"></div><dialog class="tf-view" data-tf-view aria-label="File"></dialog>`;
    // the viewer's own Esc, a backdrop press or any other close folds the file (the keydown below usually gets Esc first)
    $('[data-tf-view]', el).addEventListener('close', () => { if (d.tfOpen && d.tfEl === el && !$('[data-tf-view]', el).open) tfClose(d); });
  }
  $('[data-tf-add]', el).hidden = !can;
  const strip = $('.tf-strip', el), html = files.map(f => tfTileHTML(f, d.tfOpen?.id === f.id)).join('')
    || '<p class="muted tf-empty">Drop files here or paste a screenshot.</p>';
  if (strip.dataset.html !== html) { strip.innerHTML = strip.dataset.html = html; tfLazy(strip); }
  tfViewPaint(d);
}
function tfOpen(d: TfDialog, id: string | undefined, n: string | number | null = null, opts: {scroll?: boolean; focus?: boolean} = {}): boolean {
  const f = tfFiles(d).find(x => x.id === id);
  if (!f) return false;
  d.tfOpen = {id: f.id, n: n == null ? null : Number(n), cmp: null};
  tfPaint(d);
  if (opts.scroll) { if (!d.tfEl) throw new Error('The task has no files section'); d.tfEl.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
  if (opts.focus && d.tfEl) $(`.tf-tile[data-tf-file="${CSS.escape(f.id)}"]`, d.tfEl)?.focus({preventScroll: true});
  return true;
}
function tfClose(d: TfDialog): void {
  const id = d.tfOpen?.id;
  d.tfOpen = null; tfPaint(d);
  if (id && d.tfEl) $(`.tf-tile[data-tf-file="${CSS.escape(id)}"]`, d.tfEl)?.focus({preventScroll: true});
}

// ---- adding files: the + on Files, a drop anywhere on the task, or a pasted screenshot. One upload per file; a name
// the task already has becomes that file's next version.
async function tfAdd(d: TfDialog, files: File[]): Promise<void> {
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
    toast(added ? `Added ${plural(added, 'file')}; ${errorMessage(e, '')}` : errorMessage(e, ''), true);
  } finally { status.textContent = ''; }
  if (!added) return;
  if (added === files.length) toast(`Added ${plural(added, 'file')}`);
  if (d.open && d.taskOpening === opening && String(d.dataset.task) === id) await tfLoad(d, id);
  if (TASKS_ST) void tasksCatchUp(TASKS_ST);
}
// Taking a file off a task: its owner, its requester, or someone who may move tasks (the server's rule).
function tfCanRemove(d: TfDialog): boolean {
  const t = d.liveTask;
  return !!t && ([t.owner_id, t.requester_id].includes(myActor()) || canMove());
}
async function tfRemove(d: TfDialog, fid: string, button: HTMLButtonElement): Promise<void> {
  const id = String(d.dataset.task || ''), opening = d.taskOpening;
  const f = tfFiles(d).find(x => x.id === fid);
  button.disabled = true;
  try {
    await patch(`/v2/files/${encodeURIComponent(fid)}`, {archived: true});
  } catch (e) {
    toast(errorMessage(e, ''), true);
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
  if (TASKS_ST) void tasksCatchUp(TASKS_ST);
}

// ---- the viewer: its own modal over the task, near full screen, so a file reads at size
const tfPlaying = (box: Element): boolean => [...box.querySelectorAll<HTMLMediaElement>('video, audio')].some(m => !m.paused && !m.ended);
// Shows the viewer on top. A redraw that moved the files section (tfAdopt) took it out of the top layer; this puts it back.
function tfViewModal(box: HTMLDialogElement | null | undefined): void {
  if (!box?.isConnected || !box.hasAttribute('open') || box.matches(':modal')) return;
  box.close(); box.showModal();
}
function tfViewPaint(d: TfDialog, force = false): void {
  const el = d.tfEl;
  if (!el) throw new Error('The task has no files section');
  const box = $('[data-tf-view]', el);
  if (!box) return;
  const open = d.tfOpen, f = open && tfFiles(d).find(x => x.id === open.id);
  if (!f) { if (box.open || box.dataset.sig) { box.querySelector('video, audio')?.pause(); if (box.open) box.close(); box.innerHTML = ''; box.dataset.sig = ''; } return; }
  const v = tfVersion(f, open.n), sig = JSON.stringify([f, open, d.canComment !== false]);
  if (!force && box.dataset.sig === sig) { tfViewModal(box); return; }
  if (!force && tfPlaying(box)) return;   // never under someone watching
  const kind = tfKind(f.name, v.mime || f.mime);
  const asc = f.versions.slice().reverse();
  const cmp = open.cmp != null ? tfVersion(f, open.cmp) : null;
  const vers = f.versions.length > 1 ? `<span class="tf-vers" role="group" aria-label="Versions">${asc.map(x =>
    `<button type="button" data-tf-v="${x.n}" aria-pressed="${x.n === v.n}"${tfAskOpen(x) ? ' class="has-ask"' : ''}>v${x.n}</button>`).join('<span aria-hidden="true">·</span>')}</span>
    <button type="button" class="tf-cmp" data-tf-cmp aria-pressed="${!!cmp}">Compare</button>` : `<span class="tf-vers muted">v${v.n}</span>`;
  const others = cmp ? f.versions.filter(x => x.n !== v.n) : [];
  const active = document.activeElement;
  const refocus = active && box.contains(active) && !active.matches('[data-tf-v], [data-tf-cmp]')
    ? '[data-tf-close]' : active?.matches?.('[data-tf-v], [data-tf-cmp]') && box.contains(active)
      ? (active instanceof HTMLElement && active.dataset.tfV ? `[data-tf-v="${active.dataset.tfV}"]` : '[data-tf-cmp]') : '';
  box.dataset.sig = sig;
  box.querySelector('video, audio')?.pause();
  box.innerHTML = `<div class="tf-view-head"><strong class="tf-view-name" title="${esc(f.name)}">${esc(f.name)}</strong>${vers}
      ${cmp ? `<label class="tf-with">with <select data-tf-with aria-label="Compare with">${others.map(x => `<option value="${x.n}"${x.n === cmp.n ? ' selected' : ''}>v${x.n}</option>`).join('')}</select></label>` : ''}
      <span class="spacer"></span><span class="tf-break" aria-hidden="true"></span>${tfCanRemove(d) ? '<button type="button" class="ghost danger tf-rm" data-tf-remove>Remove</button>' : ''}<a class="tf-dl" href="${esc(v.url)}" download aria-label="Download ${esc(f.name)} v${v.n}">↓</a>
      <button type="button" class="ghost tf-x" data-tf-review aria-label="Open full screen" title="Open full screen"><span class="nav-icon" aria-hidden="true">open_in_full</span></button>
      <button type="button" class="ghost tf-x" data-tf-close aria-label="Close" title="Close (Esc)" autofocus>✕</button></div>
    ${cmp ? tfCompareHTML(f, cmp, v, kind) : `<div class="tf-body">${tfBodyHTML(f, v, kind, d)}</div>`}
    ${tfVersionMetaHTML(d, v)}`;
  tfBodyWire(box);
  if (!box.open && box.isConnected && d.open) box.showModal();
  if (refocus) $(refocus, el)?.focus({preventScroll: true});      // the redraw never drops the focus on the page
}
function tfBodyHTML(f: TfFile, v: TfVersion, kind: TfKind, d: TfDialog | null | undefined): string {
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
function tfCompareHTML(f: TfFile, a: TfVersion, b: TfVersion, kind: TfKind): string {
  if (['markdown', 'csv', 'json', 'text'].includes(kind)) return `<div class="tf-body"><div class="tf-diff" data-tf-diff="${esc(a.url)}" data-tf-diff-b="${esc(b.url)}" aria-busy="true"><div class="tf-doc-skel"></div></div></div>`;
  return `<div class="tf-body tf-pair">${[a, b].map(x => `<div class="tf-side"><span class="tf-side-v tnum">v${x.n}</span>${tfBodyHTML(f, x, kind, null)}</div>`).join('')}</div>`;
}
function tfVersionMetaHTML(d: TfDialog, v: TfVersion): string {
  const who = v.by ? actorLabel(v.by) : '';
  const line = [who, v.created_at ? ago(v.created_at) : '', v.size != null ? bytes(Number(v.size)) : ''].filter(Boolean).map(esc).join(' · ');
  return `<div class="tf-vmeta">${line ? `<div class="tf-vline muted">${v.by ? actorFace(v.by, 14) : ''}<span>${line}</span></div>` : ''}
    ${v.note ? `<div class="tf-note">${esc(v.note)}</div>` : ''}
    ${v.ask ? askHTML(v.ask, v.answers, v.ask.by || v.by, tfFiles(d)) : ''}</div>`;
}
const TF_TEXT = new Map<string, Promise<string>>();          // versioned url -> Promise<text>, for the visit
function tfText(url: string): Promise<string> {
  if (!TF_TEXT.has(url)) TF_TEXT.set(url, fetch(url, {credentials: 'same-origin'}).then(r => {
    if (!r.ok) throw new Error(r.status === 403 ? 'You do not have access to this file.' : `Preview unavailable (${r.status}).`);
    return r.text();
  }).catch(e => { TF_TEXT.delete(url); throw e; }));
  return TF_TEXT.get(url)!;
}
function tfJSONView(raw: string): HTMLElement | null {
  let data; try { data = JSON.parse(raw); } catch { return null; }
  const list = Array.isArray(data) ? data : null;
  if (list?.length && list.every(r => r && typeof r === 'object' && !Array.isArray(r))) {
    const keys = [...new Set(list.flatMap(r => Object.keys(r)))].slice(0, 40);
    const cell = (x: unknown): string => x == null ? '' : typeof x === 'object' ? JSON.stringify(x) : String(x);
    return rowsView([keys, ...list.map(r => keys.map(k => cell(r[k])))], 200);
  }
  const pre = document.createElement('pre'); pre.className = 'plain'; pre.textContent = JSON.stringify(data, null, 2);
  return pre;
}
function tfBodyWire(box: HTMLElement): void {
  for (const wrap of box.querySelectorAll<HTMLElement>('.tf-media')) {
    const el = wrap.querySelector<HTMLElement>('img, video, audio');
    if (!el) throw new Error('A media box has no picture, video or audio');
    const ready = () => wrap.classList.add('ready');
    el.addEventListener(el.tagName === 'IMG' ? 'load' : 'loadedmetadata', ready, {once: true});
    el.addEventListener('error', () => { ready(); wrap.insertAdjacentHTML('afterend', '<p class="err">Preview unavailable.</p>'); }, {once: true});
    if (el instanceof HTMLImageElement && el.complete && el.naturalWidth) ready();
  }
  for (const doc of box.querySelectorAll<HTMLElement>('[data-tf-text]')) {
    tfText(doc.dataset.tfText ?? '').then(raw => {
      if (!doc.isConnected) return;
      const kind = doc.dataset.kind;
      doc.removeAttribute('aria-busy');
      if (kind === 'csv') { doc.replaceChildren(csvView(raw, 200)); return; }
      if (kind === 'html') { doc.replaceChildren(htmlPreviewFrame(raw, doc.dataset.name)); return; }
      if (kind === 'json') { const view = tfJSONView(raw); if (view) { doc.replaceChildren(view); return; } }
      doc.innerHTML = kind === 'markdown' ? `<div class="md">${safeMd(raw)}</div>` : `<pre class="plain">${esc(raw)}</pre>`;
    }, e => { if (doc.isConnected) { doc.removeAttribute('aria-busy'); doc.innerHTML = `<p class="err">${esc(e.message)}</p>`; } });
  }
  for (const diff of box.querySelectorAll<HTMLElement>('[data-tf-diff]')) {
    Promise.all([tfText(diff.dataset.tfDiff ?? ''), tfText(diff.dataset.tfDiffB ?? '')]).then(([a, b]) => {
      if (!diff.isConnected) return;
      diff.removeAttribute('aria-busy');
      diff.innerHTML = tfDiffHTML(a, b);
    }, e => { if (diff.isConnected) diff.innerHTML = `<p class="err">${esc(e.message)}</p>`; });
  }
  tfLazy(box);
}
// Clicks and keys inside a task dialog's files section (wired once per dialog by taskDialogWire).
function tfWire(d: TfDialog): void {
  d.addEventListener('click', ev => {
    if (!d.tfEl?.contains(eventElement(ev))) return;
    const tile = eventElement(ev)?.closest<HTMLElement>('.tf-tile');
    if (tile) { const id = tile.dataset.tfFile; d.tfOpen?.id === id ? tfClose(d) : tfOpen(d, id); return; }
    if (eventElement(ev)?.closest('[data-tf-close]')) { tfClose(d); return; }
    // The file full screen with the task's thread beside it (file-review.ts); the task's viewer stays under it.
    if (eventElement(ev)?.closest('[data-tf-review]') && d.tfOpen) {
      const f = tfFiles(d).find(x => x.id === d.tfOpen?.id);
      if (f) fileReviewOpen(f.id, {name: f.name, version: d.tfOpen.n, task: String(d.dataset.task || '') || undefined});
      return;
    }
    if (eventElement(ev)?.closest('[data-tf-add]')) { $('[data-tf-input]', d.tfEl).click(); return; }
    const rm = eventElement(ev)?.closest<HTMLButtonElement>('[data-tf-remove]');
    if (rm && d.tfOpen) {
      // The first press asks; the second removes. Tico keeps no way back for a task's file.
      if (rm.dataset.sure !== '1') { rm.dataset.sure = '1'; rm.textContent = 'Really remove?'; return; }
      void tfRemove(d, d.tfOpen.id, rm); return;
    }
    const ver = eventElement(ev)?.closest<HTMLElement>('[data-tf-v]');
    if (ver && d.tfOpen) {
      const open = d.tfOpen;
      const n = Number(ver.dataset.tfV);
      if (open.cmp === n) open.cmp = tfVersion(tfFiles(d).find(f => f.id === open.id)!, open.n).n;
      open.n = n; tfViewPaint(d); return;
    }
    if (eventElement(ev)?.closest('[data-tf-cmp]') && d.tfOpen) {
      const open = d.tfOpen;
      const f = tfFiles(d).find(x => x.id === open.id)!, v = tfVersion(f, open.n);
      open.cmp = open.cmp != null ? null : (f.versions.find(x => x.n < v.n) || f.versions.find(x => x.n !== v.n))!.n;
      tfViewPaint(d); return;
    }
    const step = eventElement(ev)?.closest<HTMLElement>('[data-tf-step]');
    if (step) { tfStepImage(d, Number(step.dataset.tfStep)); return; }
    // a press on the backdrop (the dialog itself, outside its box) folds the file
    const target = eventElement(ev);
    if (target?.matches?.('[data-tf-view]')) {
      const r = target.getBoundingClientRect();
      if (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom) tfClose(d);
    }
  });
  d.addEventListener('change', ev => {
    if (ev.target instanceof HTMLSelectElement && ev.target.matches('[data-tf-with]') && d.tfOpen) { d.tfOpen.cmp = Number(ev.target.value); tfViewPaint(d); }
    if (ev.target instanceof HTMLInputElement && ev.target.matches('[data-tf-input]')) { const files = [...ev.target.files!]; ev.target.value = ''; void tfAdd(d, files); }
  });
  const take = (files: File[]) => { if (d.tfEl && d.canComment !== false) void tfAdd(d, files); };
  fileDropTarget(d, take);
  pasteImages(d, take);
  d.addEventListener('cancel', ev => { if (d.tfJustClosed) { ev.preventDefault(); ev.stopImmediatePropagation(); } });
  d.addEventListener('close', ev => { if (ev.target === d && d.tfOpen) { d.tfOpen = null; if (d.tfEl) tfPaint(d); } });
}
// Esc folds an open file before it closes the task; ←/→ step through the task's images. On the document, in capture:
// a redraw can leave the focus on the page, and the task must not see the key.
document.addEventListener('keydown', ev => {
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  const d = eventElement(ev)?.closest?.('dialog:not(.tf-view)') || $('dialog:modal:not(.tf-view)');
  if (!d?.tfOpen || !d.tfEl || eventElement(ev)?.closest?.('.prop-pop')) return;
  const typing = eventElement(ev)?.closest?.('input, textarea, [contenteditable]');
  if (ev.key === 'Escape' && !typing) {
    ev.preventDefault(); ev.stopPropagation(); tfClose(d);
    d.tfJustClosed = true; setTimeout(() => { d.tfJustClosed = false; }, 0);
    return;
  }
  if ((ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') && !typing && !eventElement(ev)?.closest?.('select, video, audio')
    && $('.tf-view .tf-media.image', d.tfEl) && !$('.tf-pair', d.tfEl)) {
    ev.preventDefault(); ev.stopPropagation(); tfStepImage(d, ev.key === 'ArrowRight' ? 1 : -1);
  }
}, true);
function tfStepImage(d: TfDialog, delta: number): void {
  const imgs = tfFiles(d).filter(x => tfKind(x.name, tfVersion(x).mime || x.mime) === 'image');
  const at = imgs.findIndex(x => x.id === d.tfOpen?.id);
  if (imgs.length < 2 || at < 0) return;
  tfOpen(d, imgs[(at + delta + imgs.length) % imgs.length]!.id);
}

// ---- comments that carried files: small thumbs (images) or a chip, each opening its tile at that version
// The title the sender gave an attachment, or '' when there is none: the server stores an untitled file's name as its
// title, so a title that is just the file's name counts as none (as in chat, chatChipsHTML).
function tfAttachTitle(a: TfAttachment | null | undefined, f?: TfFile | null): string {
  const title = String(a?.title || '').trim();
  return title && title !== a?.name && title !== f?.name ? title : '';
}
function tfCommentFiles(m: TfMessage, files: TfFile[]): TfCommentFile[] {
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
// The comment's first visuals are cards (ui/app/visual-cards.ts); its other files stay thumbs and chips.
function tfCommentFilesHTML(m: TfMessage, files: TfFile[]): string {
  return visualCardsHTML(m.refs?.attachments || [], {key: m.id || '', chips: (_rest, shown) => tfCommentChipsHTML(m, files, shown instanceof Set ? shown : undefined)});
}
function tfCommentChipsHTML(m: TfMessage, files: TfFile[], shown: Set<string> = new Set()): string {
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
  const jump = eventElement(ev)?.closest<HTMLElement>('[data-tf-jump]');
  if (!jump) return;
  const d = jump.closest('dialog');
  if (d) tfOpen(d, jump.dataset.tfJump, jump.dataset.tfN, {scroll: true});
});

// ---- questions. An ask: {questions: [{id, header, question, options: [{label, description, file}], multi, other}],
// who}. On a comment it is that comment's (an 'ask' message, its refs.questions); on a file version it is the
// version's. It shows the question, its choices and the answers given; a person answers in the thread, and a bot that
// needs an answer to go on asks on the task (docs/pipelines-v3.md: the ask screen), so there are no controls here.
function askOf(m: TfMessage | null | undefined): TfAsk | null {
  if (!m) throw new Error('The question has no message');
  if (m.ask?.questions) return m.ask;
  const refs = m.refs; return Array.isArray(refs?.questions) ? {questions: refs.questions, who: refs.who || null} : null;
}
function askAnswerWords(a: TfAnswer, ask: TfAsk | null | undefined): string {
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
function askOptionHTML(o: TfOption, files: TfFile[]): string {
  let pic = '';
  const [fid, fn] = String(o.file || '').split('@');
  const f = fid && files.find(x => x.id === fid);
  if (f) {
    const v = tfVersion(f, fn), kind = tfKind(f.name, v.mime || f.mime), src = tfPicture(v, kind);
    if (src) pic = `<span class="ask-pic tf-skel"><img alt="" data-tf-src="${esc(src)}"></span>`;
  }
  return `<span class="ask-opt ro"${o.description ? ` title="${esc(o.description)}"` : ''}>${pic}<span class="ask-l">${esc(o.label)}</span>${o.description ? `<span class="ask-d">${esc(o.description)}</span>` : ''}</span>`;
}
function askHTML(ask: TfAsk | null | undefined, answers: TfAnswer[] | null | undefined, asker: string | null | undefined, files: TfFile[] = []): string {
  const qs = (ask?.questions || []).filter(q => q && q.id);
  if (!qs.length) return '';
  answers = answers || [];
  const mine = !!asker && asker === myActor();
  const answered = answers.length > 0;
  const body = qs.map(q => `<div class="ask-q" data-qid="${esc(q.id)}">
      <div class="ask-line">${q.header ? `<span class="ask-chip">${esc(q.header)}</span>` : ''}<span class="ask-text">${esc(q.question || '')}</span></div>
      ${mine || !(q.options || []).length ? '' : `<div class="ask-opts">${(q.options ?? []).map(o => askOptionHTML(o, files)).join('')}</div>`}
    </div>`).join('');
  const list = answered ? `<ul class="ask-answers">${answers.map(a => `<li>${a.by ? actorFace(a.by, 14) : ''}<b>${esc(a.by ? actorLabel(a.by) : 'Someone')}</b>${a.quoted_by ? `<span class="muted">(quoted by ${esc(actorLabel(a.quoted_by))})</span>` : ''}
      <span class="${a.dismiss ? 'muted' : ''}">${esc(askAnswerWords(a, ask))}</span>${a.other ? `<span class="ask-o">${esc(a.other)}</span>` : ''}${a.at ? `<time class="muted tnum" title="${esc(fmt(a.at))}">${esc(ageShort(a.at))}</time>` : ''}</li>`).join('')}</ul>` : '';
  return `<div class="ask ro${answered ? ' answered' : ''}${mine ? ' mine' : ''}">${body}${list}</div>`;
}

