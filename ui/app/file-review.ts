/* ui/app/file-review.ts — The file review view (docs/files.md, Review): a file full screen, its chat beside it.
   Left, the file as large as it reads (the viewer's kinds, a Word file drawn as Word lays it out, a Google file in
   Google's own preview); right, the chat with the bot about it, the same thread the person has elsewhere (a task's
   thread from a task, else the DM with the bot that owns the file); on top, the name, Download and the versions.
   Address: #/file/<id>?v=<n>&task=<id>|bot=<id>. Opened in the app it is a history entry over the page it came from, so
   Esc, ✕ and the browser's back all return there as it was; a cold link draws its context page under it first.
   Each message sent from it carries refs.from_page {page: 'file', label: title, item: {kind: 'file', id, name: 'v<n>'}},
   which the bot's turn reads and the chat shows as "From <title> › v<n>". A new version the bot publishes shows at once:
   the thread's live messages and runs re-read the versions, and a slow poll covers a quiet stream.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const FILE_ROUTE = '#/file/';
interface FrvCtx { task?: string | undefined; bot?: string | undefined }
interface FrvOpen extends FrvCtx { name?: string | undefined; version?: number | null | undefined }
interface FrvVersion { version: number; current?: boolean; name?: string | null; size?: number | null; mime?: string | null; actor?: string | null; created_at?: string | null }
interface FrvInfo { id: string; bot?: string | null; title?: string | null; name?: string | null; mime?: string | null; locator?: string | null;
  version?: number | null; open?: {type: string; url: string} | null; task_id?: string | null }
interface FrvState {
  id: string; name: string; v: number | null; cmp: boolean; ctx: FrvCtx; back: string;
  d: HTMLDialogElement; info: FrvInfo | null; versions: FrvVersion[]; chat: ThvView | null;
  offs: Array<() => void>; seq: number; poll: number; shown: string; gone: boolean;
}
let FRV: FrvState | null = null;
const FRV_PHONE = '(max-width:760px)';
const FRV_DOCX_LIMIT = 25_000_000;
const FRV_DIFF_KINDS = ['markdown', 'text', 'csv', 'docx'];

/** The review view's address for a file: a version pins it, a task or a bot names the chat beside it. */
function fileReviewHref(id: string, o: FrvOpen = {}): string {
  const q = new URLSearchParams();
  if (o.version) q.set('v', String(o.version));
  if (o.task) q.set('task', o.task); else if (o.bot) q.set('bot', o.bot);
  const s = q.toString();
  return FILE_ROUTE + encodeURIComponent(id) + (s ? '?' + s : '');
}
function frvParse(hash: string): (FrvOpen & {id: string}) | null {
  if (!hash.startsWith(FILE_ROUTE)) return null;
  const [path = '', query = ''] = hash.slice(FILE_ROUTE.length).split('?');
  let id = '';
  try { id = decodeURIComponent(path); } catch { return null; }
  if (!/^[A-Za-z0-9._:-]{1,200}$/.test(id)) return null;
  const q = new URLSearchParams(query), v = q.get('v') || '';
  return {id, version: /^[1-9][0-9]{0,8}$/.test(v) ? Number(v) : null, task: q.get('task') || undefined, bot: q.get('bot') || undefined};
}
// The page a cold link returns to: the task, the bot, else Library › Files.
const frvBase = (o: FrvCtx): string => o.task ? '#/task/' + encodeURIComponent(o.task) : o.bot ? '#/bot/' + encodeURIComponent(o.bot) : RD_FILES;
/** Whether an address belongs to the review view: its own, or the page under it that closing it returns to. */
const fileReviewOwns = (hash: string): boolean => hash.startsWith(FILE_ROUTE) || !!FRV && hash === FRV.back;

/** Opens a file in the review view, over the page that is showing (a history entry of its own). */
function fileReviewOpen(id: string, o: FrvOpen = {}): void {
  const href = fileReviewHref(id, o);
  if (FRV) { history.replaceState(history.state, '', href); frvShow({id, ...o}, FRV.back); return; }
  const back = location.hash || UPDATES;
  history.pushState({...(history.state || {}), fileReview: 1}, '', href);
  frvShow({id, ...o}, back);
}
/** Called first by route(): true when the address is the review view's (opened or updated) or the page under it
    (closed, the page left as it was), so the page is not drawn again. */
function fileReviewRoute(hash: string): boolean {
  const want = frvParse(hash);
  if (want) {
    if (FRV) { frvShow(want, FRV.back); return true; }
    const cold = !LAST_ROUTE;
    if (cold) {
      // A cold link: the page it belongs to is drawn under it, one history entry back, so closing returns there.
      const base = frvBase(want);
      history.replaceState(null, '', base);
      route(base);
      history.pushState({fileReview: 1}, '', hash);
    }
    frvShow(want, LAST_ROUTE || frvBase(want));
    if (cold && want.task) frvAboveTask();
    return true;
  }
  if (!FRV) return false;
  const back = FRV.back;
  frvTeardown();
  return hash === back;
}
// A cold link from a task draws the task under the view, and the task opens (later, once loaded) as a modal of its own:
// the view goes back on top of it when it does.
function frvAboveTask(): void {
  const st = FRV;
  if (!st) return;
  const raise = () => { if (FRV === st && st.d.open) { st.d.close(); st.d.showModal(); } };
  if (document.querySelector('#task-modal[open]')) raise();
  // Each time it opens (it may open again as it loads), for the first seconds only.
  const watch = new MutationObserver(records => {
    if (records.some(r => r.target instanceof HTMLDialogElement && r.target.id === 'task-modal' && r.target.open
      || [...r.addedNodes].some(n => n instanceof HTMLDialogElement && n.id === 'task-modal' && n.open))) raise();
  });
  watch.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['open']});
  const stop = setTimeout(() => watch.disconnect(), 10_000);
  st.offs.push(() => { clearTimeout(stop); watch.disconnect(); });
}
/** Esc, ✕: back through the view's history entry to the page it came from. */
function fileReviewClose(): void {
  const st = FRV;
  if (!st) return;
  if (history.length > 1) { history.back(); return; }
  frvTeardown();
  location.replace(st.back);
}
function frvTeardown(): void {
  const st = FRV;
  if (!st) return;
  FRV = null; st.gone = true;
  threadUnmount(st.chat);
  for (const off of st.offs) off();
  clearInterval(st.poll);
  st.d.querySelector<HTMLMediaElement>('video, audio')?.pause();
  if (st.d.open) st.d.close();
  st.d.remove();
}

// ---- the dialog
function frvDialog(): HTMLDialogElement {
  const d = document.createElement('dialog');
  d.className = 'frv';
  d.setAttribute('aria-label', 'File');
  d.innerHTML = `<header class="frv-head">
      <h2 class="frv-title"></h2><button type="button" class="frv-ver" data-frv-latest hidden></button>
      <span class="frv-spacer"></span>
      <a class="ghost frv-icon" data-frv-ext target="_blank" rel="noopener noreferrer" hidden><span class="nav-icon" aria-hidden="true">open_in_new</span></a>
      <a class="ghost frv-icon" data-frv-dl download aria-label="Download" title="Download" hidden><span class="nav-icon" aria-hidden="true">download</span></a>
      <button type="button" class="ghost frv-icon" data-frv-hist aria-label="History" title="History" aria-expanded="false" hidden><span class="nav-icon" aria-hidden="true">history</span></button>
      <button type="button" class="ghost frv-icon" data-frv-close aria-label="Close" title="Close (Esc)"><span class="nav-icon" aria-hidden="true">close</span></button>
    </header>
    <div class="frv-hist" data-frv-hist-list role="menu" aria-label="Versions" hidden></div>
    <div class="frv-tabs" role="tablist"><button type="button" role="tab" data-frv-pane="preview" aria-selected="true" aria-label="File"><span class="nav-icon" aria-hidden="true">description</span></button><button type="button" role="tab" data-frv-pane="chat" aria-selected="false" aria-label="Chat"><span class="nav-icon" aria-hidden="true">chat</span></button></div>
    <div class="frv-main"><section class="frv-preview" data-frv-preview aria-busy="true"></section><aside class="frv-chat" data-frv-chat></aside></div>`;
  d.addEventListener('cancel', ev => { ev.preventDefault(); fileReviewClose(); });
  d.addEventListener('click', frvClick);
  document.body.append(d);
  return d;
}
function frvShow(want: FrvOpen & {id: string}, back: string): void {
  let st = FRV;
  const ctx: FrvCtx = {task: want.task, bot: want.bot};
  if (st && (st.id !== want.id || st.ctx.task !== ctx.task || st.ctx.bot !== ctx.bot)) { frvTeardown(); st = null; }
  if (!st) {
    st = FRV = {id: want.id, name: want.name || '', v: want.version ?? null, cmp: false, ctx, back, d: frvDialog(), info: null, versions: [],
      chat: null, offs: [], seq: 0, poll: 0, shown: '', gone: false};
    st.d.showModal();
    frvTitle(st);
    void frvStart(st);
    return;
  }
  if (st.v !== (want.version ?? null)) { st.v = want.version ?? null; st.cmp = false; frvPaint(st); }
}
async function frvStart(st: FrvState): Promise<void> {
  const [info, list] = await Promise.all([
    v2Get<{file?: FrvInfo}>(`/v2/files/${encodeURIComponent(st.id)}/info`, {fresh: true}),
    v2Get<{versions?: FrvVersion[]}>(`/v2/files/${encodeURIComponent(st.id)}/versions`, {fresh: true}),
  ]);
  if (st.gone) return;
  st.info = info?.file || null;
  st.versions = Array.isArray(list?.versions) ? list.versions : [];
  if (!st.name) st.name = st.info?.name || st.versions[0]?.name || '';
  frvPaint(st);
  void frvChat(st);
  // A new version shows as soon as the bot publishes it: its run and its message in this chat say so, and a slow poll
  // (visible tab only) covers a stream that was away.
  const touched = (d: {conversation_id?: string} | null) => { if (st.chat?.id && d?.conversation_id === st.chat.id) liveSoon('frv-' + st.id, () => void frvRefresh(st), 1500); };
  st.offs.push(liveOn('messages', touched), liveOn('runs', touched), liveOn('reset', () => void frvRefresh(st)));
  st.poll = setInterval(() => { if (!document.hidden) void frvRefresh(st); }, 60_000);
}
async function frvRefresh(st: FrvState): Promise<void> {
  const list = await v2Get<{versions?: FrvVersion[]}>(`/v2/files/${encodeURIComponent(st.id)}/versions`, {fresh: true});
  if (st.gone || !Array.isArray(list?.versions)) return;
  const before = st.versions[0]?.version;
  st.versions = list.versions;
  if (st.versions[0]?.version !== before) frvPaint(st);
}
const frvLatest = (st: FrvState): number | null => st.versions[0]?.version ?? st.info?.version ?? null;
const frvShownVersion = (st: FrvState): number | null => st.v ?? frvLatest(st);
// What the view calls the file: its title in the bot's Files, else its file name.
const frvLabel = (st: FrvState): string => st.info?.title || st.name || 'File';
const frvLink = (st: FrvState): string => st.info?.locator === 'remote_link' && st.info.open?.type === 'external' ? st.info.open.url : '';

// ---- the top bar
function frvTitle(st: FrvState): void {
  const d = st.d, n = frvShownVersion(st), latest = frvLatest(st), link = frvLink(st);
  const title = d.querySelector<HTMLElement>('.frv-title');
  if (title) { title.textContent = frvLabel(st); title.title = frvLabel(st); }
  const ver = d.querySelector<HTMLButtonElement>('[data-frv-latest]');
  if (ver) {
    const old = st.v != null && latest != null && st.v !== latest;
    ver.hidden = !old;
    ver.innerHTML = old ? `<span class="tnum">v${st.v}</span><span class="nav-icon" aria-hidden="true">close</span>` : '';
    ver.setAttribute('aria-label', `Showing v${st.v}; show the latest`);
    ver.title = 'Latest';
  }
  const ext = d.querySelector<HTMLAnchorElement>('[data-frv-ext]');
  if (ext) {
    ext.hidden = !link;
    if (link) { ext.href = link; const label = /^https:\/\/(docs|drive)\.google\.com\//.test(link) ? 'Open in Google' : 'Open'; ext.setAttribute('aria-label', label); ext.title = label; }
  }
  const dl = d.querySelector<HTMLAnchorElement>('[data-frv-dl]');
  if (dl) {
    dl.hidden = !!link;
    dl.href = n && st.versions.length ? `${API}/v2/files/${encodeURIComponent(st.id)}/versions/${n}` : `${API}/v2/files/${encodeURIComponent(st.id)}`;
  }
  const hist = d.querySelector<HTMLButtonElement>('[data-frv-hist]');
  if (hist) hist.hidden = !!link || !st.versions.length;
}
function frvHistHTML(st: FrvState): string {
  const n = frvShownVersion(st), kind = frvKindOf(st.name, st.versions[0]?.mime);
  const older = st.versions.some(x => n != null && x.version < n);
  const rows = st.versions.map(x => {
    const when = x.created_at ? ago(x.created_at) : '', who = x.actor ? actorLabel(x.actor) : '';
    return `<button type="button" role="menuitemradio" aria-checked="${x.version === n && !st.cmp}" data-frv-v="${x.version}">
      <b class="tnum">v${x.version}</b><span class="muted">${esc(when)}</span><span class="frv-who">${x.actor ? actorFace(x.actor, 16) : ''}<span>${esc(who)}</span></span></button>`;
  }).join('');
  const cmp = older && FRV_DIFF_KINDS.includes(String(kind))
    ? `<button type="button" role="menuitemcheckbox" class="frv-cmp" data-frv-cmp aria-checked="${st.cmp}">Compare with previous</button>` : '';
  return rows + cmp;
}
function frvHistToggle(st: FrvState, open?: boolean): void {
  const box = st.d.querySelector<HTMLElement>('[data-frv-hist-list]'), btn = st.d.querySelector<HTMLElement>('[data-frv-hist]');
  if (!box || !btn) return;
  const show = open ?? box.hidden;
  if (show) box.innerHTML = frvHistHTML(st);
  box.hidden = !show;
  btn.setAttribute('aria-expanded', String(show));
  if (show) box.querySelector<HTMLElement>('[aria-checked="true"]')?.focus({preventScroll: true});
}
function frvClick(ev: MouseEvent): void {
  const st = FRV, el = eventElement(ev);
  if (!st || !el) return;
  if (el.closest('[data-frv-close]')) { fileReviewClose(); return; }
  if (el.closest('[data-frv-hist]')) { frvHistToggle(st); return; }
  const pick = el.closest<HTMLElement>('[data-frv-v]');
  if (pick) { frvGo(st, Number(pick.dataset.frvV), false); frvHistToggle(st, false); return; }
  if (el.closest('[data-frv-cmp]')) { frvGo(st, frvShownVersion(st), !st.cmp); frvHistToggle(st, false); return; }
  if (el.closest('[data-frv-latest]')) { frvGo(st, null, false); return; }
  const pane = el.closest<HTMLElement>('[data-frv-pane]');
  if (pane) { frvPane(st, pane.dataset.frvPane === 'chat' ? 'chat' : 'preview'); return; }
  if (!el.closest('.frv-hist')) frvHistToggle(st, false);
}
// A version picked (or the latest, null) is part of the address, so a copied link opens what is on screen.
function frvGo(st: FrvState, n: number | null, cmp: boolean): void {
  const latest = frvLatest(st);
  st.v = n != null && n !== latest ? n : null;
  st.cmp = cmp;
  history.replaceState(history.state, '', fileReviewHref(st.id, {...st.ctx, version: st.v}));
  frvPaint(st);
}
// On a phone the file and the chat take turns: two marks at the top switch between them.
function frvPane(st: FrvState, pane: 'preview' | 'chat'): void {
  st.d.classList.toggle('frv-on-chat', pane === 'chat');
  for (const b of st.d.querySelectorAll<HTMLElement>('[data-frv-pane]')) b.setAttribute('aria-selected', String(b.dataset.frvPane === pane));
  if (pane === 'chat') st.d.querySelector<HTMLTextAreaElement>('.frv-chat textarea')?.focus({preventScroll: true});
}

// ---- the chat beside it
const frvFrom = (st: FrvState): PageChatFrom => {
  const n = frvShownVersion(st);
  return {page: 'file', label: frvLabel(st).slice(0, 80), ...(n ? {item: {kind: 'file', id: st.id.slice(0, 200), name: 'v' + n}} : {})};
};
async function frvChat(st: FrvState): Promise<void> {
  const host = st.d.querySelector<HTMLElement>('[data-frv-chat]');
  if (!host) return;
  let opts: ThvOpts | null = null;
  if (st.ctx.task) {
    const t = await v2Get<{task?: {conversation_id?: string | null}}>(`/v2/tasks/${encodeURIComponent(st.ctx.task)}`);
    if (t?.task?.conversation_id) opts = {id: t.task.conversation_id};
  }
  const bot = st.ctx.bot || st.info?.bot;
  if (!opts && bot) opts = {dm: 'bot:' + bot};
  if (st.gone) return;
  st.d.classList.toggle('frv-solo', !opts);
  if (!opts) return;
  st.chat = threadMount(host, {...opts, card: true, header: false, from: () => frvFrom(st)});
}

// ---- the file
function frvKindOf(name: string | null | undefined, mime: string | null | undefined): string | null {
  const m = String(mime || '').toLowerCase();
  if (/\.docx$/i.test(String(name || '')) || m === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx';
  const kind = viewKindOf(name);
  if (kind) return kind;
  if (m.startsWith('text/csv')) return 'csv';
  if (m === 'text/markdown') return 'markdown';
  if (m.startsWith('text/plain') || m === 'application/json') return 'text';
  if (m === 'application/pdf') return 'pdf';
  if (/^image\/(png|jpeg|gif|webp|avif)/.test(m)) return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  return null;
}
/** Google's own preview of a Google Doc, Sheet, Slides or Drive file, or '' for any other link. */
function googlePreviewUrl(href: string): string {
  let u: URL; try { u = new URL(href); } catch { return ''; }
  if (u.protocol !== 'https:') return '';
  if (u.hostname === 'docs.google.com') {
    const m = u.pathname.match(/^\/(document|spreadsheets|presentation|drawings)\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/);
    if (m) return `https://docs.google.com/${m[1]}/d/${m[2]}/preview`;
  }
  if (u.hostname === 'drive.google.com') {
    const m = u.pathname.match(/^\/file\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/);
    if (m) return `https://drive.google.com/file/d/${m[1]}/preview`;
  }
  return '';
}
function frvCard(name: string, size: number | null | undefined, href: string, open = ''): string {
  return `<div class="frv-card"><span class="frv-card-mark">${esc((extOf(name) || 'file').toUpperCase())}</span><span class="frv-card-t"><b>${esc(name)}</b>${size != null ? `<span class="muted tnum">${esc(bytes(Number(size)))}</span>` : ''}</span>
    ${open ? `<a class="ghost btnlike" href="${esc(open)}" target="_blank" rel="noopener noreferrer"><span class="nav-icon" aria-hidden="true">open_in_new</span>Open</a>` : ''}${href ? `<a class="primary btnlike" href="${esc(href)}" download><span class="nav-icon" aria-hidden="true">download</span>Download</a>` : ''}</div>`;
}
function frvPaint(st: FrvState): void {
  frvTitle(st);
  const box = st.d.querySelector<HTMLElement>('[data-frv-preview]');
  if (!box) return;
  const n = frvShownVersion(st), link = frvLink(st), key = JSON.stringify([n, st.cmp, link]);
  if (key === st.shown) return;
  st.shown = key;
  const seq = ++st.seq, current = () => !st.gone && st.seq === seq;
  box.querySelector<HTMLMediaElement>('video, audio')?.pause();
  box.className = 'frv-preview';
  box.setAttribute('aria-busy', 'true');
  box.innerHTML = '<div class="frv-skel"></div>';
  if (link) {
    const g = googlePreviewUrl(link);
    box.removeAttribute('aria-busy');
    if (!g) { box.innerHTML = frvCard(frvLabel(st), null, '', link); return; }
    // Google draws its own file, signed in as the viewer; Tico passes nothing to it but the address.
    const frame = document.createElement('iframe');
    frame.className = 'frv-google';
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms');
    frame.referrerPolicy = 'no-referrer';
    frame.title = frvLabel(st);
    frame.src = g;
    box.replaceChildren(frame);
    box.classList.add('frv-is-frame');
    return;
  }
  void (async () => {
    let file: ViewedFile;
    try { file = await viewFile(st.id, st.name || 'file', n); } catch (e) {
      if (current()) { box.removeAttribute('aria-busy'); box.innerHTML = `<p class="err frv-err">${esc(errorMessage(e, 'Could not open the file.'))}</p>`; }
      return;
    }
    if (!current()) return;
    if (!st.name || st.name === 'file') { st.name = file.name; frvTitle(st); }
    const kind = frvKindOf(file.name, file.blob.type);
    if (st.cmp && kind && FRV_DIFF_KINDS.includes(kind)) { await frvCompare(st, box, file, kind, current); return; }
    await frvBody(st, box, file, kind, current);
  })();
}
async function frvBody(st: FrvState, box: HTMLElement, file: ViewedFile, kind: string | null, current: () => boolean): Promise<void> {
  const size = file.blob.size, href = st.d.querySelector<HTMLAnchorElement>('[data-frv-dl]')?.href || '';
  const done = (cls: string) => { box.removeAttribute('aria-busy'); box.classList.add('frv-' + cls); };
  const tooBig = (kind === 'markdown' || kind === 'text') && size > DOC_VIEW_LIMIT || kind === 'csv' && size > CSV_LIMIT
    || kind === 'html' && size > HTML_VIEW_LIMIT || kind === 'docx' && size > FRV_DOCX_LIMIT;
  if (!kind || tooBig) { done('other'); box.innerHTML = frvCard(file.name, size, href); return; }
  if (kind === 'image') { done('image'); box.innerHTML = `<img class="frv-img" src="${esc(file.url)}" alt="${esc(file.name)}">`; return; }
  if (kind === 'video') { done('video'); box.innerHTML = `<video src="${esc(file.url)}" controls playsinline preload="metadata"></video>`; return; }
  if (kind === 'audio') { done('audio'); box.innerHTML = `<audio src="${esc(file.url)}" controls preload="metadata"></audio>`; return; }
  if (kind === 'pdf') { done('frame'); box.innerHTML = `<iframe class="frv-pdf" src="${esc(file.url)}" title="${esc(file.name)}"></iframe>`; return; }
  if (kind === 'docx') {
    let frame: HTMLIFrameElement;
    try { frame = await docxPreviewFrame(file.blob, file.name); } catch {
      if (current()) { done('other'); box.innerHTML = frvCard(file.name, size, href); }
      return;
    }
    if (!current()) return;
    done('frame'); box.replaceChildren(frame); return;
  }
  const text = await file.blob.text();
  if (!current()) return;
  if (kind === 'csv') { done('csv'); box.replaceChildren(csvView(text)); return; }
  if (kind === 'html') { done('frame'); box.replaceChildren(htmlPreviewFrame(text, file.name)); return; }
  done('doc');
  box.innerHTML = kind === 'markdown' ? `<div class="md">${safeMd(text)}</div>` : `<pre class="plain">${esc(text)}</pre>`;
}
// A version against the one before it, as a line diff (task-files.ts tfDiffHTML); a Word file by its text.
async function frvCompare(st: FrvState, box: HTMLElement, file: ViewedFile, kind: string, current: () => boolean): Promise<void> {
  const n = frvShownVersion(st) ?? 0, prev = st.versions.find(x => x.version < n);
  if (!prev) { st.cmp = false; await frvBody(st, box, file, kind, current); return; }
  try {
    const old = await viewFile(st.id, st.name || 'file', prev.version);
    const text = (f: ViewedFile) => kind === 'docx' ? docxText(f.blob) : f.blob.text();
    const [a, b] = await Promise.all([text(old), text(file)]);
    if (!current()) return;
    box.removeAttribute('aria-busy'); box.classList.add('frv-diff');
    box.innerHTML = `<div class="frv-diff-head tnum">v${prev.version} → v${n}</div>${tfDiffHTML(a, b)}`;
  } catch (e) {
    if (current()) { box.removeAttribute('aria-busy'); box.innerHTML = `<p class="err frv-err">${esc(errorMessage(e, 'Could not compare the versions.'))}</p>`; }
  }
}

// ---- Word files (.docx), drawn in the browser by the vendored docx-preview (ui/vendor, NOTICE): no server converter.
// It draws into this page's document, so what it draws is copied out, stripped (no scripts, frames, forms, handlers,
// only http(s), mailto and in-page links, only pictures from inside the file) and shown in the same sandboxed frame as
// an HTML file (htmlPreviewFrame): no scripts, its own origin, nothing fetched from the network.
let FRV_DOCX_LOAD: Promise<void> | null = null;
function frvScript(src: string): Promise<void> {
  return new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = src; s.onload = () => ok(); s.onerror = () => { s.remove(); fail(new Error('Could not load the Word viewer')); };
    document.head.append(s);
  });
}
function docxLib(): Promise<void> {
  if (window.docx && window.JSZip) return Promise.resolve();
  FRV_DOCX_LOAD ||= (window.JSZip ? Promise.resolve() : frvScript('/vendor/jszip.min.js'))
    .then(() => window.docx ? undefined : frvScript('/vendor/docx-preview.min.js'))
    .catch(e => { FRV_DOCX_LOAD = null; throw e; });
  return FRV_DOCX_LOAD;
}
const DOCX_DROP = 'script, noscript, iframe, frame, frameset, object, embed, applet, form, input, button, textarea, select, link, meta, base, portal, template, foreignObject';
const DOCX_URL_ATTRS = new Set(['srcset', 'action', 'formaction', 'poster', 'background', 'data', 'ping', 'lowsrc', 'longdesc', 'srcdoc', 'xlink:href']);
/** Strips what a drawn Word file must never carry into the preview (in place). */
function docxSafe(root: ParentNode): void {
  for (const el of root.querySelectorAll(DOCX_DROP)) el.remove();
  for (const el of root.querySelectorAll('*')) {
    for (const a of [...el.attributes]) {
      const name = a.name.toLowerCase(), v = a.value.trim();
      // A picture from inside the file: docx-preview gives it as a data: address (typed by the file's own name, or
      // application/octet-stream), shown only as an image.
      const picture = (el.localName === 'img' || el.localName === 'image')
        && /^data:(image\/(png|jpe?g|gif|webp|bmp|svg\+xml|x-emf|x-wmf|tiff)|application\/octet-stream)[;,]/i.test(v);
      if (name.startsWith('on') || DOCX_URL_ATTRS.has(name)) el.removeAttribute(a.name);
      else if (name === 'src' && !picture) el.removeAttribute(a.name);
      else if (name === 'href' && !(el.localName === 'image' ? picture : el.localName === 'a' && /^(https?:|mailto:|#)/i.test(v))) el.removeAttribute(a.name);
    }
  }
}
const DOCX_FRAME_CSS = 'html,body{margin:0}body{background:#f3f3f3}.docx-wrapper{min-height:100vh;box-sizing:border-box}'
  + '@media (max-width:760px){.docx-wrapper{padding:8px!important;align-items:stretch!important}'
  + '.docx-wrapper>section.docx{width:auto!important;min-width:0;min-height:0!important;padding:20px 16px!important;overflow-x:auto!important}}';
async function docxPreviewFrame(blob: Blob, name: string): Promise<HTMLIFrameElement> {
  await docxLib();
  const lib = window.docx;
  if (!lib) throw new Error('Could not load the Word viewer');
  // Drawn detached, never in the page: its styles never reach the app, and it is copied out before anything shows.
  const work = document.createElement('div'), body = document.createElement('div'), style = document.createElement('div');
  work.append(style, body);
  try {
    await lib.renderAsync(blob, body, style, {useBase64URL: true, renderAltChunks: false, renderComments: false, renderChanges: false,
      experimental: false, inWrapper: true, breakPages: true, ignoreLastRenderedPageBreak: true, renderHeaders: true, renderFooters: true,
      renderFootnotes: true, renderEndnotes: true});
    docxSafe(work);
    const css = [...style.querySelectorAll('style')].map(s => s.textContent || '').join('\n').replace(/<\/style/gi, '<\\/style');
    return htmlPreviewFrame(`<!DOCTYPE html><html><head><meta charset="utf-8"><style>${DOCX_FRAME_CSS}</style><style>${css}</style></head><body>${body.innerHTML}</body></html>`, name);
  } finally { work.replaceChildren(); }
}
/** A Word file's text, a line per paragraph (tabs and breaks kept, deletions left out), for Compare. */
async function docxText(blob: Blob): Promise<string> {
  await docxLib();
  const zip = window.JSZip;
  if (!zip) throw new Error('Could not load the Word viewer');
  const xml = await (await zip.loadAsync(blob)).file('word/document.xml')?.async('string');
  if (!xml) return '';
  const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
  const doc = new DOMParser().parseFromString(xml, 'application/xml'), lines: string[] = [];
  const walk = (el: Element, out: string[]): void => {
    for (const c of el.children) {
      if (c.namespaceURI !== W) { walk(c, out); continue; }
      if (c.localName === 't') out.push(c.textContent || '');
      else if (c.localName === 'tab') out.push('\t');
      else if (c.localName === 'br' || c.localName === 'cr') out.push('\n');
      else if (c.localName === 'p') { const inner: string[] = []; walk(c, inner); lines.push(inner.join('')); }
      else if (c.localName !== 'delText' && c.localName !== 'del') walk(c, out);
    }
  };
  const top = doc.getElementsByTagNameNS(W, 'body')[0];
  if (top) walk(top, lines);
  return lines.join('\n');
}
