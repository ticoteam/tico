/* ui/app/task-docs.ts — A task's documents (Spec, QA report, Release report, Invoice, Email; backend/task_documents.py):
   the tab row beside the task's title, and one document open with its one version line. The task window
   (ui/app/task-modal.ts) and Flow on a phone (ui/app/decide.ts) draw both from here, so they look and work the same.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A document as GET /v2/tasks/{id} lists it (`task.documents`), newest change first, its versions newest first.
interface TaskDocVersion { n: number; by: string; at: string; note?: string | null; }
interface TaskDoc { name: string; file_id: string; version: number; by: string; at: string; versions: TaskDocVersion[]; }
// One tab: Chat, a document, or (Flow) what an approval shows when it is no document of the task.
interface DocTab { key: string; label: string; dot?: boolean; }

const docKey = (name: string) => 'doc:' + name;
const docOf = (docs: TaskDoc[] | null | undefined, key: string | null | undefined): TaskDoc | null =>
  key?.startsWith('doc:') ? (docs || []).find(d => docKey(d.name) === key) || null : null;
/** The tabs: Chat first, then each document, most recently changed first (as the server lists them), then `extra`.
    None at all, not even Chat, when there is nothing beside the chat. */
function docTabs(docs: TaskDoc[] | null | undefined, extra: DocTab[] = [], chatDot = false): DocTab[] {
  const rest = [...(docs || []).map(d => ({key: docKey(d.name), label: d.name})), ...extra];
  return rest.length ? [{key: 'chat', label: 'Chat', dot: chatDot}, ...rest] : [];
}
/** The tab row, a segmented control that scrolls sideways when the tabs do not fit; '' with no tabs. `attrs` gives each
    tab's own attributes (the task window's data-task-tab, Flow's data-dc-tab). */
function docTabsHTML(tabs: DocTab[], on: string, attrs: (key: string) => string, label = 'Show'): string {
  if (!tabs.length) return '';
  const tab = (t: DocTab) => `<button type="button" role="tab" class="doc-tab" ${attrs(t.key)} aria-selected="${t.key === on}" tabindex="${t.key === on ? 0 : -1}">`
    + `${esc(t.label)}${t.dot ? '<span class="tw-dot" role="img" aria-label="Waiting on you"></span>' : ''}</button>`;
  return `<span class="doc-tabs" role="tablist" aria-label="${esc(label)}">${tabs.map(tab).join('')}</span>`;
}
/** ←/→, Home and End move along a tab row, as a tablist does; `show` opens the tab moved to. */
function docTabsKeys(row: HTMLElement, show: (tab: HTMLElement) => void) {
  row.onkeydown = (ev: KeyboardEvent) => {
    const tabs = [...row.querySelectorAll<HTMLElement>('[role="tab"]')], at = tabs.indexOf(ev.target as HTMLElement);
    if (at < 0) return;
    const to = ev.key === 'ArrowRight' ? at + 1 : ev.key === 'ArrowLeft' ? at - 1 : ev.key === 'Home' ? 0 : ev.key === 'End' ? tabs.length - 1 : null;
    if (to === null) return;
    ev.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length]!;
    show(next); next.focus();
  };
}
/** Marks the tab `on` selected in a row, and scrolls the row (only the row) so it shows: Chat, first, from the start. */
function docTabsPaint(row: ParentNode, on: string, keyOf: (tab: HTMLElement) => string | undefined) {
  for (const b of row.querySelectorAll<HTMLElement>('.doc-tabs [role="tab"]')) {
    const yes = keyOf(b) === on;
    b.setAttribute('aria-selected', String(yes)); b.tabIndex = yes ? 0 : -1;
    const strip = b.parentElement;
    if (!yes || !strip) continue;
    const left = b.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft, right = left + b.offsetWidth;
    if (b === strip.firstElementChild || left < strip.scrollLeft) strip.scrollLeft = b === strip.firstElementChild ? 0 : left - 2;
    else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = right - strip.clientWidth + 2;
  }
}

// ---- one document open: its one version line ("v3 · Ana · 2h ⌄"), which opens the version list and Compare, and the
// version shown, sandboxed as a page is. Kept per host across redraws of the same document.
interface DocViewState { doc: TaskDoc; shown: number; open: boolean; compare: [number, number] | null; seq: number; }
const DOC_VIEWS = new WeakMap<HTMLElement, DocViewState>();
/** A document's default look, the app's own theme: its background, text, lines and links, light or dark as the app is.
    Zero-specificity base styles, so a document that sets its own colours keeps them. */
function docLook(): string {
  const css = getComputedStyle(document.documentElement), v = (name: string) => css.getPropertyValue(name).trim();
  const theme = document.documentElement.dataset.theme;
  const dark = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  return `:where(html){color-scheme:${dark ? 'dark' : 'light'};background:${v('--bg')};color:${v('--ink')}}`
    + `:where(a){color:${v('--link')}}:where(hr){border:0;border-top:1px solid ${v('--line')}}`
    + `:where(th,td){border:1px solid ${v('--line')};padding:4px 8px}:where(table){border-collapse:collapse}`
    + `:where(code,pre){background:${v('--surface2')};border-radius:4px}:where(code){padding:0 3px}:where(pre){padding:8px}`;
}
const docVersionSaid = (v: TaskDocVersion) => [`v${v.n}`, actorLabel(v.by), ago(v.at)].filter(Boolean).join(' · ');
/** Shows `doc` in `host`: its newest version, or the version shown there before when it is the same document. */
function docViewMount(host: HTMLElement, doc: TaskDoc) {
  const was = DOC_VIEWS.get(host);
  const same = was && was.doc.file_id === doc.file_id;
  const st: DocViewState = same ? {...was, doc, shown: doc.versions.some(v => v.n === was.shown) && was.shown !== was.doc.version ? was.shown : doc.version}
    : {doc, shown: doc.version, open: false, compare: null, seq: 0};
  DOC_VIEWS.set(host, st);
  if (!same || !host.querySelector('.doc-view')) {
    host.innerHTML = '<div class="doc-view"><div class="doc-ver"></div><div class="doc-vers" hidden></div><div class="doc-body"></div></div>';
    docViewBind(host);
  }
  docViewPaint(host, st, !same || was.doc.version !== doc.version);
}
function docViewPaint(host: HTMLElement, st: DocViewState, body = true) {
  const line = host.querySelector<HTMLElement>('.doc-ver'), list = host.querySelector<HTMLElement>('.doc-vers');
  if (!line || !list) return;
  const v = st.doc.versions.find(x => x.n === st.shown) || st.doc.versions[0];
  const many = st.doc.versions.length > 1;
  const icon = (name: string) => `<span class="nav-icon" aria-hidden="true">${name}</span>`;
  if (st.compare) {
    const pick = (attr: string, n: number) => `<select class="doc-pick" ${attr} aria-label="${attr === 'data-doc-from' ? 'From' : 'To'}">`
      + st.doc.versions.map(x => `<option value="${x.n}"${x.n === n ? ' selected' : ''}>v${x.n}</option>`).join('') + '</select>';
    line.innerHTML = `<span class="doc-cmp-line">${pick('data-doc-from', st.compare[0])}<span aria-hidden="true">→</span>${pick('data-doc-to', st.compare[1])}</span>`
      + `<button type="button" class="doc-ic" data-doc-compare-off aria-label="Close compare" title="Close compare">${icon('close')}</button>`;
  } else {
    // Open, the line is only its mark: the list under it says each version once, the one shown in bold.
    line.innerHTML = `<button type="button" class="doc-ver-line" data-doc-ver aria-expanded="${st.open}" title="Versions"${st.open ? ' aria-label="Versions"' : ''}>`
      + `${st.open ? '' : `<span class="doc-ver-t">${esc(v ? docVersionSaid(v) : '')}</span>`}${icon('expand_more')}</button>`
      + (st.open && many ? `<button type="button" class="doc-ic" data-doc-compare aria-label="Compare versions" title="Compare">${icon('swap_horiz')}</button>` : '');
  }
  list.hidden = !st.open || !!st.compare;
  list.innerHTML = st.open ? st.doc.versions.map(x => `<button type="button" class="doc-v" data-doc-v="${x.n}" aria-current="${x.n === st.shown}">`
    + `<span class="doc-v-said">${esc(docVersionSaid(x))}</span>${x.note ? `<span class="doc-v-note">${esc(x.note)}</span>` : ''}</button>`).join('') : '';
  if (body) void docViewBody(host, st);
}
function docViewBind(host: HTMLElement) {
  host.addEventListener('click', ev => {
    const st = DOC_VIEWS.get(host), el = eventElement(ev);
    if (!st || !el) return;
    if (el.closest('[data-doc-ver]')) { st.open = !st.open; docViewPaint(host, st, false); host.querySelector<HTMLElement>('[data-doc-ver]')?.focus(); return; }
    const pick = el.closest<HTMLElement>('[data-doc-v]');
    if (pick) { st.shown = Number(pick.dataset.docV); st.open = false; docViewPaint(host, st); host.querySelector<HTMLElement>('[data-doc-ver]')?.focus(); return; }
    if (el.closest('[data-doc-compare]')) {
      const to = st.shown, from = st.doc.versions.find(x => x.n < to)?.n ?? st.doc.versions[1]?.n ?? to;
      st.compare = [Math.min(from, to), Math.max(from, to)]; st.open = false; docViewPaint(host, st);
      host.querySelector<HTMLElement>('[data-doc-compare-off]')?.focus(); return;
    }
    if (el.closest('[data-doc-compare-off]')) { st.compare = null; docViewPaint(host, st); host.querySelector<HTMLElement>('[data-doc-ver]')?.focus(); }
  });
  host.addEventListener('change', ev => {
    const st = DOC_VIEWS.get(host), el = ev.target;
    if (!st?.compare || !(el instanceof HTMLSelectElement)) return;
    const n = Number(el.value);
    st.compare = el.matches('[data-doc-from]') ? [n, st.compare[1]] : [st.compare[0], n];
    void docViewBody(host, st);
  });
}
/** The version shown, or, comparing, the line diff of the two versions' text (pageText), in the reading font. */
async function docViewBody(host: HTMLElement, st: DocViewState) {
  const box = host.querySelector<HTMLElement>('.doc-body');
  if (!box) return;
  const seq = ++st.seq, fid = st.doc.file_id, stale = () => st.seq !== seq || DOC_VIEWS.get(host) !== st || !box.isConnected;
  try {
    if (st.compare) {
      const [a, b] = await Promise.all(st.compare.map(async n => pageText(await (await viewFile(fid, 'document.html', n)).blob.text())));
      if (stale()) return;
      box.innerHTML = `<div class="doc-diff">${tfDiffHTML(a ?? '', b ?? '')}</div>`;
      return;
    }
    const text = await (await viewFile(fid, 'document.html', st.shown)).blob.text();
    if (stale()) return;
    box.replaceChildren(htmlPreviewFrame(text, `${st.doc.name} v${st.shown}`, docLook()));
  } catch (e) { if (!stale()) box.innerHTML = `<p class="muted doc-err">${esc(errorMessage(e, ''))}</p>`; }
}
