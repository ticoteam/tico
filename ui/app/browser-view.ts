/* ui/app/browser-view.ts — A person's live view of one website account's browser on the browser host (backend
   browser_commands.py): the picture about once a second, the page's address above it, Take control / Give back and
   Done. With control a tap or click goes to the page at the same spot (page pixels: the frame's X-Page-Width and
   X-Page-Height), a box types text, a keys row sends Enter, Tab and Backspace, and the wheel or a drag scrolls.
   Used by a website tool's page (Open browser, ui/app/tools-website.ts) and the ask screen (a `browser:<tool>/<account>`
   show ref, ui/app/decide.ts), where Done answers the ask "Done".
   browserView.mount(host, {toolId, accountId, full?, title?, onDone?, onClose?}) draws it into `host`; on a phone
   (≤ 760 px) an inline mount is one Open browser button that opens it full screen. browserView.open(...) opens it in a
   dialog over everything (full screen on a phone, 48 px controls). It stops by itself once its host leaves the page.
   Routes (people who can use the account): POST /api/v2/website-tools/<id>/accounts/<aid>/browser (opens or wakes the
   session; 409 browser_host_offline says which computer), GET …/browser/frame (image/jpeg, 204 before the first),
   POST …/browser/control {take} and POST …/browser/input {kind: click|type|key|scroll}. Styles: ui/styles/browser-view.css.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface BrowserViewOptions {
  toolId: string;
  accountId: string;
  full?: boolean;
  title?: string;
  // Done: on an ask it answers it. `false` (or a throw, which is toasted) keeps the view open.
  onDone?: () => unknown;
  onClose?: () => void;
}
interface BrowserViewHandle { stop(): void; root: HTMLElement; }
type BrowserViewInput = {kind: 'click'; x: number; y: number} | {kind: 'type'; text: string} | {kind: 'key'; key: string} | {kind: 'scroll'; dy: number};
interface BrowserViewState {
  opts: BrowserViewOptions;
  root: HTMLElement;
  img: HTMLImageElement;
  url: string;          // the newest frame's object URL, let go when the next one comes
  pageW: number;
  pageH: number;
  control: boolean;
  stopped: boolean;
  busy: boolean;
  timer: number;
  failed: string;
  scroll: number;       // wheel/drag distance not yet sent
  scrollTimer: number;
  onVis: () => void;
}

const BV_EVERY_MS = 1000;
const BV_SOON_MS = 250;
const BV_KEYS: [string, string, string][] = [['Enter', '⏎', 'Enter'], ['Tab', '⇥', 'Tab'], ['Backspace', '⌫', 'Backspace']];
const BV_MOUNTS = new Set<BrowserViewState>();
const bvPath = (o: BrowserViewOptions, rest = '') => `/v2/website-tools/${encodeURIComponent(o.toolId)}/accounts/${encodeURIComponent(o.accountId)}/browser${rest}`;
const bvPhone = () => matchMedia('(max-width:760px)').matches;
// What a failed frame answer says: its Problem's one line (api.ts), else its status.
async function bvProblem(r: Response): Promise<string> {
  try { return apiError(await r.json() as ApiFailureBody, r); } catch { return r.statusText || 'Did not load'; }
}
const bvShown = (el: HTMLElement) => el.isConnected && el.getClientRects().length > 0;

function bvHTML(o: BrowserViewOptions): string {
  const full = !!o.full;
  const you = '<span class="pill ok bv-you" data-bv-you hidden><span class="tt-dot" aria-hidden="true"></span>You</span>';
  return `${full ? `<header class="bv-head"><button type="button" class="bv-x" data-bv-close aria-label="Close">×</button>
      <strong class="bv-title">${esc(o.title || '')}</strong><span class="spacer"></span>${you}</header>` : ''}
    <div class="bv-url"><span class="nav-icon" aria-hidden="true">lock</span><span class="mono" data-bv-url></span>${full ? '' : you}</div>
    <div class="bv-stage" data-bv-stage><img data-bv-img alt="" draggable="false" hidden><p class="bv-msg" data-bv-msg role="status">Loading…</p></div>
    <form class="bv-type" data-bv-type hidden><input type="text" data-bv-text aria-label="Type" placeholder="Type" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="send">
      ${BV_KEYS.map(([key, mark, label]) => `<button type="button" class="ghost" data-bv-key="${key}" aria-label="${label}" title="${label}">${mark}</button>`).join('')}</form>
    <div class="bv-acts"><button type="button" class="ghost" data-bv-control>Take control</button>
      <button type="button" class="primary" data-bv-done>Done</button></div>`;
}

// ----------------------------------------------------------------- mount
function bvMount(host: HTMLElement, opts: BrowserViewOptions): BrowserViewHandle {
  bvStopIn(host);
  // A phone's inline view is a button: the picture is only useful full screen there.
  if (!opts.full && bvPhone()) {
    host.innerHTML = '<button type="button" class="primary bv-launch" data-bv-launch><span class="nav-icon" aria-hidden="true">open_in_full</span>Open browser</button>';
    const b = host.querySelector<HTMLButtonElement>('[data-bv-launch]');
    if (b) b.onclick = () => { bvOpen(opts); };
    return {stop: () => {}, root: host};
  }
  const root = document.createElement('div');
  root.className = 'bv' + (opts.full ? ' bv-full' : '');
  root.dataset.bv = `${opts.toolId}/${opts.accountId}`;
  root.innerHTML = bvHTML(opts);
  host.replaceChildren(root);
  const img = root.querySelector<HTMLImageElement>('[data-bv-img]')!;   // drawn just above
  const st: BrowserViewState = {opts, root, img, url: '', pageW: 0, pageH: 0, control: false, stopped: false, busy: false, timer: 0,
    failed: '', scroll: 0, scrollTimer: 0, onVis: () => {}};
  st.onVis = () => bvVisible(st);
  BV_MOUNTS.add(st);
  bvBind(st);
  document.addEventListener('visibilitychange', st.onVis);
  void bvStart(st);
  return {stop: () => bvStop(st), root};
}
// Opens (or wakes) the session on the browser host, then the picture.
async function bvStart(st: BrowserViewState): Promise<void> {
  try { await post(bvPath(st.opts), {}); }
  catch (e) { if (!st.stopped) bvFail(st, errorMessage(e, 'The browser did not open.')); return; }
  if (!st.stopped) bvTick(st, 0);
}
function bvFail(st: BrowserViewState, line: string): void {
  st.failed = line;
  st.root.dataset.state = 'failed';
  const msg = st.root.querySelector<HTMLElement>('[data-bv-msg]');
  if (msg) { msg.hidden = false; msg.textContent = line; }
  st.img.hidden = !st.url;
}
function bvTick(st: BrowserViewState, ms: number): void {
  clearTimeout(st.timer);
  if (!st.stopped) st.timer = window.setTimeout(() => void bvFrame(st), ms);
}
function bvVisible(st: BrowserViewState): void {
  if (!st.stopped && !document.hidden && !st.busy) bvTick(st, 0);
}
// One frame: drawn when it came, then the next in about a second. A hidden tab or a closed dialog reads nothing.
async function bvFrame(st: BrowserViewState): Promise<void> {
  if (st.stopped) return;
  if (!st.root.isConnected) { bvStop(st); return; }
  if (document.hidden) return;                                   // visibilitychange starts it again
  if (!bvShown(st.root)) { bvTick(st, BV_EVERY_MS); return; }
  st.busy = true;
  try {
    const r = await fetch(API + bvPath(st.opts, '/frame'), {credentials: 'same-origin', cache: 'no-store'});
    if (st.stopped) return;
    if (r.status === 200) {
      const blob = await r.blob();
      if (st.stopped) return;
      st.pageW = Number(r.headers.get('X-Page-Width')) || 0;
      st.pageH = Number(r.headers.get('X-Page-Height')) || 0;
      const old = st.url;
      st.url = URL.createObjectURL(blob);
      st.img.src = st.url;
      st.img.hidden = false;
      if (old) URL.revokeObjectURL(old);
      const page = r.headers.get('X-Page-Url') || '';
      const line = st.root.querySelector<HTMLElement>('[data-bv-url]');
      if (line) { line.textContent = bvShortUrl(page); line.title = page; }
      const msg = st.root.querySelector<HTMLElement>('[data-bv-msg]');
      if (msg) msg.hidden = true;
      st.failed = '';
      st.root.dataset.state = 'live';
    } else if (r.status !== 204) {
      bvFail(st, await bvProblem(r));
    }
  } catch { /* offline for a moment: the next tick tries again */ }
  finally { st.busy = false; }
  bvTick(st, BV_EVERY_MS);
}
// The address as it reads best: host and path, no query.
function bvShortUrl(url: string): string {
  try { const u = new URL(url); return u.host + (u.pathname === '/' ? '' : u.pathname); } catch { return url; }
}
function bvStop(st: BrowserViewState): void {
  if (st.stopped) return;
  st.stopped = true;
  BV_MOUNTS.delete(st);
  clearTimeout(st.timer); clearTimeout(st.scrollTimer);
  document.removeEventListener('visibilitychange', st.onVis);
  if (st.url) URL.revokeObjectURL(st.url);
  // Control held when the view goes is given back, so a bot is not kept waiting the five idle minutes.
  if (st.control) void post(bvPath(st.opts, '/control'), {take: false}).catch(() => {});
  st.control = false;
}
// Stops every view drawn inside `root` (a page or a screen that is redrawn or closed).
function bvStopIn(root: ParentNode): void {
  for (const st of [...BV_MOUNTS]) if (root.contains(st.root)) bvStop(st);
}

// ----------------------------------------------------------------- control and input
function bvPaintControl(st: BrowserViewState): void {
  const b = st.root.querySelector<HTMLButtonElement>('[data-bv-control]');
  if (b) b.textContent = st.control ? 'Give back' : 'Take control';
  st.root.querySelectorAll<HTMLElement>('[data-bv-you]').forEach(el => { el.hidden = !st.control; });
  const type = st.root.querySelector<HTMLFormElement>('[data-bv-type]');
  if (type) type.hidden = !st.control;
  st.root.classList.toggle('bv-control', st.control);
}
async function bvControl(st: BrowserViewState, take: boolean): Promise<void> {
  try {
    await post(bvPath(st.opts, '/control'), {take});
    if (st.stopped) return;
    st.control = take;
    bvPaintControl(st);
    if (take) st.root.querySelector<HTMLInputElement>('[data-bv-text]')?.focus({preventScroll: true});
  } catch (e) { toast(errorMessage(e, ''), true); }
}
async function bvInput(st: BrowserViewState, body: BrowserViewInput): Promise<void> {
  if (!st.control || st.stopped) return;
  try { await post(bvPath(st.opts, '/input'), body); }
  catch (e) {
    // Control ran out (five idle minutes) or someone else holds it: the buttons say so.
    if (errorStatus(e) === 409 || errorCode(e) === 'not_in_control') { st.control = false; bvPaintControl(st); }
    toast(errorMessage(e, ''), true);
    return;
  }
  if (!st.busy) bvTick(st, BV_SOON_MS);
}
// A point on the picture in the page's own pixels.
function bvPagePoint(st: BrowserViewState, clientX: number, clientY: number): {x: number; y: number} | null {
  const r = st.img.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  const w = st.pageW || st.img.naturalWidth, h = st.pageH || st.img.naturalHeight;
  const x = Math.round((clientX - r.left) / r.width * w), y = Math.round((clientY - r.top) / r.height * h);
  return x < 0 || y < 0 || x > w || y > h ? null : {x, y};
}
// Wheel and drag distances are summed and sent a few times a second.
function bvScroll(st: BrowserViewState, dy: number): void {
  st.scroll += dy;
  if (st.scrollTimer) return;
  st.scrollTimer = window.setTimeout(() => {
    st.scrollTimer = 0;
    const by = Math.round(st.scroll);
    st.scroll = 0;
    if (by) void bvInput(st, {kind: 'scroll', dy: by});
  }, 150);
}
function bvBind(st: BrowserViewState): void {
  const root = st.root, stage = root.querySelector<HTMLElement>('[data-bv-stage]')!;   // drawn by bvHTML
  root.addEventListener('click', ev => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('button');
    if (!b || b.disabled) return;
    if (b.hasAttribute('data-bv-control')) { void bvControl(st, !st.control); return; }
    if (b.dataset.bvKey) { void bvInput(st, {kind: 'key', key: b.dataset.bvKey}); return; }
    if (b.hasAttribute('data-bv-close')) { bvStop(st); st.opts.onClose?.(); return; }
    if (b.hasAttribute('data-bv-done')) void bvDone(st, b);
  });
  root.querySelector<HTMLFormElement>('[data-bv-type]')!.addEventListener('submit', ev => {
    ev.preventDefault();
    const box = root.querySelector<HTMLInputElement>('[data-bv-text]')!;
    const text = box.value;
    box.value = '';
    // An empty box sends Enter: the phone keyboard's Send is the page's Enter then.
    void bvInput(st, text ? {kind: 'type', text} : {kind: 'key', key: 'Enter'});
  });
  // A tap is a click; a drag scrolls. Pointer events cover mouse, pen and touch.
  let down: {x: number; y: number; last: number; moved: boolean} | null = null;
  stage.addEventListener('pointerdown', ev => {
    if (!st.control || ev.button !== 0) return;
    down = {x: ev.clientX, y: ev.clientY, last: ev.clientY, moved: false};
  });
  stage.addEventListener('pointermove', ev => {
    if (!down) return;
    if (Math.abs(ev.clientY - down.y) > 8 || Math.abs(ev.clientX - down.x) > 8) down.moved = true;
    if (down.moved) {
      const scale = st.img.getBoundingClientRect().height ? (st.pageH || st.img.naturalHeight) / st.img.getBoundingClientRect().height : 1;
      bvScroll(st, (down.last - ev.clientY) * scale);
      down.last = ev.clientY;
    }
  });
  stage.addEventListener('pointerup', ev => {
    const was = down;
    down = null;
    if (!was || was.moved || !st.control) return;
    const at = bvPagePoint(st, ev.clientX, ev.clientY);
    if (at) void bvInput(st, {kind: 'click', x: at.x, y: at.y});
  });
  stage.addEventListener('pointercancel', () => { down = null; });
  stage.addEventListener('wheel', ev => {
    if (!st.control) return;
    ev.preventDefault();
    bvScroll(st, ev.deltaMode === 1 ? ev.deltaY * 40 : ev.deltaY);
  }, {passive: false});
}
async function bvDone(st: BrowserViewState, b: HTMLButtonElement): Promise<void> {
  b.disabled = true;
  let kept = false;
  try { kept = await st.opts.onDone?.() === false; }
  catch (e) { toast(errorMessage(e, ''), true); kept = true; }
  if (kept && !st.stopped) { b.disabled = false; return; }
  bvStop(st);
  st.opts.onClose?.();
  if (b.isConnected) b.disabled = false;
}

// ----------------------------------------------------------------- full screen
// A dialog over everything, the ask screen's included (a modal dialog is in the top layer).
function bvOpen(opts: BrowserViewOptions): BrowserViewHandle {
  const dialog = document.createElement('dialog');
  dialog.className = 'bv-dialog';
  dialog.setAttribute('aria-label', opts.title || 'Browser');
  document.body.appendChild(dialog);
  let view: BrowserViewHandle | null = null;
  const close = () => { if (dialog.open) dialog.close(); };
  dialog.addEventListener('close', () => { view?.stop(); dialog.remove(); });
  view = bvMount(dialog, {...opts, full: true, onClose: () => { opts.onClose?.(); close(); }});
  dialog.showModal();
  return {stop: close, root: view.root};
}

const browserView = {mount: bvMount, open: bvOpen, stopIn: bvStopIn};
