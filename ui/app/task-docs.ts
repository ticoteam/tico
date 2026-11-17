/* ui/app/task-docs.ts — A task's documents (Spec, QA report, Release report, Invoice, Email, and its Markdown files;
   backend/task_documents.py):
   the tab row beside the task's title, and one document open with its one version line. The task window
   (ui/app/task-modal.ts) and Flow on a phone (ui/app/decide.ts) draw both from here, so they look and work the same.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A document as GET /v2/tasks/{id} lists it (`task.documents`), newest change first, its versions newest first. A
// document is a linked page or a Markdown file. A linked page shows its links and whether the task OK'd an older version.
interface TaskDocVersion { n: number; by: string; at: string; note?: string | null; }
interface TaskDocLink { target: string; role: string; name?: string | null; by?: string | null; }
interface TaskDoc {
  name: string; file_id: string; page_id?: string | null; title?: string | null; version: number; by: string; at: string;
  versions: TaskDocVersion[]; links?: TaskDocLink[] | null; changed_since_ok?: boolean | null; kind?: 'html' | 'markdown';
}
// One tab: Chat, a document, or (Flow) what an approval shows when it is no document of the task.
interface DocTab { key: string; label: string; dot?: boolean; }

const docKey = (doc: TaskDoc, docs: TaskDoc[]) =>
  docs.filter(other => other.name === doc.name).length > 1
    ? 'doc:' + JSON.stringify([doc.name, doc.file_id])
    : 'doc:' + doc.name;
const docOf = (docs: TaskDoc[] | null | undefined, key: string | null | undefined): TaskDoc | null =>
  key?.startsWith('doc:') && docs ? docs.find(d => docKey(d, docs) === key) || null : null;
const docTab = (doc: TaskDoc, docs: TaskDoc[]): DocTab => ({
  key: docKey(doc, docs),
  label: docs.filter(other => other.name === doc.name).length > 1 ? doc.title || doc.name : doc.name,
});
/** The tabs: Chat first, then each document, most recently changed first (as the server lists them), then `extra`.
    None at all, not even Chat, when there is nothing beside the chat. */
function docTabs(docs: TaskDoc[] | null | undefined, extra: DocTab[] = [], chatDot = false): DocTab[] {
  const rest = [...(docs || []).map((d, _, shown) => docTab(d, shown)), ...extra];
  return rest.length ? [{key: 'chat', label: 'Chat', dot: chatDot}, ...rest] : [];
}
/** The Spec cards under a task's title: one card per page the task has as its Spec, each opening the page. A card shows
    the page's title (`titles`: the Files list's, once read; else the document's own), v<version>, its writers, "also
    the Spec of D-…" when another task has it as its Spec too, and a small dot when the task was OK'd on an older
    version. No Spec, no section. */
function taskSpecsHTML(docs: TaskDoc[] | null | undefined, taskId: string, titles: Map<string, string> = new Map()): string {
  const specs = (docs || []).filter(d => d.name === 'Spec' && d.kind !== 'markdown');
  if (!specs.length) return '';
  const cards = specs.map(d => {
    const id = d.page_id || d.file_id, links = d.links || [];
    const title = titles.get(id) || d.title || 'Spec';
    const writers = [...new Set([...links.filter(l => l.role === 'writer').map(l => l.name || actorLabel(l.target)),
      ...d.versions.map(v => actorLabel(v.by))].filter(Boolean))];
    const others = [...new Set(links.filter(l => l.role === 'Spec' && l.target.startsWith('task:') && l.target !== 'task:' + taskId)
      .map(l => /^D-\d+/.exec(l.name || '')?.[0] || l.name || l.target))];
    const meta = [`v${d.version}`, writers.join(', '), others.length ? `also the Spec of ${others.join(', ')}` : ''].filter(Boolean).join(' · ');
    return `<a class="task-spec" href="${esc(pageHref(id))}" data-task-spec="${esc(id)}" title="${esc(title)}">`
      + '<span class="nav-icon task-spec-ic" aria-hidden="true">description</span>'
      + `<span class="task-spec-main"><span class="task-spec-t">${esc(title)}</span><span class="task-spec-m muted">${esc(meta)}</span></span>`
      + (d.changed_since_ok ? '<span class="task-spec-dot" role="img" aria-label="Changed since OK" title="Changed since OK"></span>' : '')
      + '</a>';
  });
  return `<section class="task-specs" data-task-specs aria-label="Spec">${cards.join('')}</section>`;
}
/** The tab row, a segmented control that scrolls sideways when the tabs do not fit; '' with no tabs. `attrs` gives each
    tab's own attributes (the task window's data-task-tab, Flow's data-dc-tab). */
function docTabsHTML(tabs: DocTab[], on: string, attrs: (key: string) => string, label = 'Show'): string {
  if (!tabs.length) return '';
  const tab = (t: DocTab) => `<button type="button" role="tab" class="doc-tab" ${attrs(t.key)} aria-selected="${t.key === on}" tabindex="${t.key === on ? 0 : -1}" title="${esc(t.label)}">`
    + `<span class="doc-tab-t">${esc(t.label)}</span>${t.dot ? '<span class="tw-dot" role="img" aria-label="Waiting on you"></span>' : ''}</button>`;
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
/** Marks the tab `on` selected in a row, and scrolls the row (only the row) so it shows: Chat, first, from the start.
    `shown` false: something else covers the tabs' pane (a task's Details), so none looks selected; `on` keeps the
    keyboard's stop. */
function docTabsPaint(row: ParentNode, on: string, keyOf: (tab: HTMLElement) => string | undefined, shown = true) {
  for (const b of row.querySelectorAll<HTMLElement>('.doc-tabs [role="tab"]')) {
    const yes = keyOf(b) === on;
    b.setAttribute('aria-selected', String(yes && shown)); b.tabIndex = yes ? 0 : -1;
    const strip = b.parentElement;
    if (!yes || !strip) continue;
    const left = b.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft, right = left + b.offsetWidth;
    if (b === strip.firstElementChild || left < strip.scrollLeft) strip.scrollLeft = b === strip.firstElementChild ? 0 : left - 2;
    else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = right - strip.clientWidth + 2;
  }
}

// ---- one document open: its one version line ("v3 · Ana · 2h ⌄"), which opens the version list and Compare, and the
// version shown, sandboxed as a page is. Kept per host across redraws of the same document.
interface DocViewState { doc: TaskDoc; shown: number; open: boolean; compare: [number, number] | null; seq: number; approval: DocApproval | null; }
// The version of a document a stage asks a person to approve (Flow), and whether the decision model could check it.
interface DocApproval { version: number; unchecked: boolean; }
const DOC_VIEWS = new WeakMap<HTMLElement, DocViewState>();
const DOC_BOUND = new WeakSet<HTMLElement>();  // hosts whose delegated listeners are on, once each
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
/** Each document open draws again in the app's colours when its theme changes (its bytes are read once, then held). */
function docViewsRetheme() {
  for (const view of document.querySelectorAll<HTMLElement>('.doc-view')) {
    const host = view.parentElement, st = host ? DOC_VIEWS.get(host) : undefined;
    if (host && st && !st.compare && st.doc.kind !== 'markdown') void docViewBody(host, st);
  }
}
new MutationObserver(docViewsRetheme).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', docViewsRetheme);
const docVersionSaid = (v: TaskDocVersion) => [`v${v.n}`, actorLabel(v.by), ago(v.at)].filter(Boolean).join(' · ');
/** The one version line: the version submitted for approval says so, with what changed, and "not checked" when the
    decision model could not check it ("v2 · submitted for approval · changed: empty state"); any other, who and when. */
function docVersionLine(v: TaskDocVersion, approval: DocApproval | null): string {
  if (!approval || approval.version !== v.n) return docVersionSaid(v);
  const note = (v.note || '').trim();
  return [`v${v.n}`, 'submitted for approval', note ? `changed: ${note}` : '', approval.unchecked ? 'not checked' : ''].filter(Boolean).join(' · ');
}
/** Shows `doc` in `host`: its newest version, or the older version picked there before when it is the same document.
    With `approval`, the version submitted for it, which the person approves. The same document keeps its state object,
    so a read already on its way still lands. */
function docViewMount(host: HTMLElement, doc: TaskDoc, approval: DocApproval | null = null) {
  const was = DOC_VIEWS.get(host), same = !!was && was.doc.file_id === doc.file_id;
  const fresh = !same || !host.querySelector('.doc-view');
  if (fresh) host.innerHTML = '<div class="doc-view"><div class="doc-ver"></div><div class="doc-vers" hidden></div><div class="doc-body"></div></div>';
  if (!DOC_BOUND.has(host)) { DOC_BOUND.add(host); docViewBind(host); }
  const asked = approval && doc.versions.some(v => v.n === approval.version) ? approval : null;
  if (!was || !same) {
    const st: DocViewState = {doc, shown: asked?.version ?? doc.version, open: false, compare: null, seq: 0, approval: asked};
    DOC_VIEWS.set(host, st);
    docViewPaint(host, st);
    return;
  }
  // An approval that moved to a newer version shows that one; else a version picked here stays.
  const base = was.approval?.version ?? was.doc.version, now = asked?.version ?? doc.version;
  const pinned = was.shown !== base && now === base && doc.versions.some(v => v.n === was.shown);
  const shown = pinned ? was.shown : now, moved = shown !== was.shown;
  was.doc = doc; was.shown = shown; was.approval = asked;
  docViewPaint(host, was, fresh || moved);
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
    line.innerHTML = st.open ? '' : `<button type="button" class="doc-ver-line" data-doc-ver aria-expanded="false" title="Versions">`
      + `<span class="doc-ver-t">${esc(v ? docVersionLine(v, st.approval) : '')}</span>${icon('expand_more')}</button>`;
  }
  // Open, the list says each version once: the one shown, in bold, is the line itself (tapped again, the list shuts),
  // with Compare at its right end; each other version shows when picked.
  line.hidden = st.open && !st.compare;
  list.hidden = !st.open || !!st.compare;
  const said = (x: TaskDocVersion) => `<span class="doc-v-said">${esc(docVersionSaid(x))}</span>${x.note ? `<span class="doc-v-note">${esc(x.note)}</span>` : ''}`;
  list.innerHTML = st.open ? st.doc.versions.map(x => x === v
    ? `<div class="doc-v-cur"><button type="button" class="doc-v" data-doc-ver aria-expanded="true" aria-current="true" title="Versions">${said(x)}</button>`
      + (many ? `<button type="button" class="doc-ic" data-doc-compare aria-label="Compare versions" title="Compare">${icon('swap_horiz')}</button>` : '') + '</div>'
    : `<button type="button" class="doc-v" data-doc-v="${x.n}" aria-current="false">${said(x)}</button>`).join('') : '';
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
/** The version shown, or, comparing, the line diff of the two versions' text (pageText; a Markdown one's own lines),
    in the reading font. A Markdown document reads as Markdown, in the app's own type. */
async function docViewBody(host: HTMLElement, st: DocViewState) {
  const box = host.querySelector<HTMLElement>('.doc-body');
  if (!box) return;
  const seq = ++st.seq, fid = st.doc.file_id, stale = () => st.seq !== seq || DOC_VIEWS.get(host) !== st || !box.isConnected;
  const md = st.doc.kind === 'markdown', fallback = md ? 'document.md' : 'document.html';
  const read = async (n: number) => (await viewFile(fid, fallback, n)).blob.text();
  try {
    if (st.compare) {
      const [a, b] = await Promise.all(st.compare.map(async n => md ? read(n) : pageText(await read(n))));
      if (stale()) return;
      box.innerHTML = `<div class="doc-diff">${tfDiffHTML(a ?? '', b ?? '')}</div>`;
      return;
    }
    const text = await read(st.shown);
    if (stale()) return;
    if (md) box.innerHTML = `<div class="doc-md md">${safeMd(text)}</div>`;
    else box.replaceChildren(htmlPreviewFrame(text, `${st.doc.name} v${st.shown}`, docLook()));
  } catch (e) { if (!stale()) box.innerHTML = `<p class="muted doc-err">${esc(errorMessage(e, ''))}</p>`; }
}
