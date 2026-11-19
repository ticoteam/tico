/* ui/app/task-docs.ts — A task's documents (Spec, QA report, Release report, Invoice, Email, and its Markdown files;
   backend/task_documents.py):
   the tab row beside the task's title, and one document open with its one version line. The task window
   (ui/app/task-modal.ts) and Flow on a phone (ui/app/decide.ts) draw both from here, so they look and work the same.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A document as GET /v2/tasks/{id} lists it (`task.documents`), newest change first, its versions newest first, with
// when it first appeared (`created`). A document is a linked page or a Markdown file. A linked page shows its links and whether the task OK'd an older version.
interface TaskDocVersion { n: number; by: string; at: string; note?: string | null; }
interface TaskDocLink { target: string; role: string; name?: string | null; by?: string | null; }
interface TaskDoc {
  name: string; file_id: string; page_id?: string | null; title?: string | null; version: number; by: string; at: string; created?: string;
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
/** The documents in the order they first appeared, oldest first: a new one joins at the end and a bot writing one
    moves none, so a tab (and a line of ⋯'s menu) stays where it was while a person taps through them. The server
    lists them newest change first; those it gives no `created` keep that order. */
const docsInPlace = (docs: TaskDoc[]): TaskDoc[] => [...docs].sort((a, b) => (a.created || '').localeCompare(b.created || ''));
/** The tabs: Chat first, then each document in the order it first appeared (docsInPlace), then `extra`. None at all,
    not even Chat, when there is nothing beside the chat. */
function docTabs(docs: TaskDoc[] | null | undefined, extra: DocTab[] = [], chatDot = false): DocTab[] {
  const rest = [...docsInPlace(docs || []).map((d, _, shown) => docTab(d, shown)), ...extra];
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
/** The documents that show as tabs; more than this and one shows (the one open, else the first) and +N lists them all. */
const DOC_TABS_MAX = 2;
const docTabIsDoc = (key: string) => key.startsWith('doc:');
/** Short names for a row of documents: a leading run of words that three or more of them share is left out ("Queue
    2026 11 18 pr4233 cce40e0" reads "pr4233 cce40e0"). A name nothing shares, or only one other, stays whole. */
function docTabShort(labels: string[]): Map<string, string> {
  const out = new Map<string, string>(), words = labels.map(l => l.split(' '));
  words.forEach((w, i) => {
    for (let k = w.length - 1; k >= 1; k--) {
      const head = w.slice(0, k).join(' ');
      if (words.filter((o, j) => j !== i && o.length > k && o.slice(0, k).join(' ') === head).length >= 2) { out.set(labels[i]!, w.slice(k).join(' ')); break; }
    }
  });
  return out;
}
/** The tab row, a segmented control that never scrolls: Chat (and Files, or Flow's approval) keep their size, a
    document's name gives way first. With more than DOC_TABS_MAX documents one shows and +N beside the tabs (how many
    more; not ⋯, which is the task's own menu beside it) lists every document. '' with no tabs. `attrs` gives each tab its own attributes (the task window's data-task-tab, Flow's
    data-dc-tab). `place`: the document the row being drawn again had in its place (docTabsPlace), kept there while
    `on` is no document, so a bot writing one leaves the row as it was. */
function docTabsHTML(tabs: DocTab[], on: string, attrs: (key: string) => string, label = 'Show', place = ''): string {
  if (!tabs.length) return '';
  const docs = tabs.filter(t => docTabIsDoc(t.key)), fold = docs.length > DOC_TABS_MAX;
  const short = docTabShort(docs.map(t => t.label));
  const slot = fold ? (docs.find(t => t.key === on) || docs.find(t => t.key === place) || docs[0]!).key : '';
  const tab = (t: DocTab) => {
    const doc = docTabIsDoc(t.key), text = doc ? short.get(t.label) ?? t.label : t.label;
    return `<button type="button" role="tab" class="doc-tab${doc ? ' doc-tab-d' : ' doc-tab-fix'}" ${attrs(t.key)} data-doc-short="${esc(text)}" aria-selected="${t.key === on}" tabindex="${t.key === on ? 0 : -1}" title="${esc(t.label)}"${fold && doc && t.key !== slot ? ' hidden' : ''}>`
      + `<span class="doc-tab-t">${esc(text)}</span>${t.dot ? '<span class="tw-dot" role="img" aria-label="Waiting on you"></span>' : ''}</button>`;
  };
  // +N sits beside the tabs, not among them: it opens a menu, it is no tab.
  const more = fold ? `<button type="button" class="doc-tab-more" data-doc-more aria-haspopup="menu" aria-expanded="false" aria-label="${docs.length} documents" title="${docs.length} documents"><span aria-hidden="true">+${docs.length - 1}</span></button>` : '';
  return `<span class="doc-tabs"><span class="doc-tabs-l" role="tablist" aria-label="${esc(label)}">${tabs.map(tab).join('')}</span>${more}</span>`;
}
/** The key of the document in a folded row's one place ('' unfolded or none): `keyOf` reads a tab's key. */
function docTabsPlace(row: ParentNode | null | undefined, keyOf: (tab: HTMLElement) => string | undefined): string {
  const tab = row?.querySelector('[data-doc-more]') ? row.querySelector<HTMLElement>('.doc-tab-d:not([hidden])') : null;
  return (tab && keyOf(tab)) || '';
}
/** ←/→, Home and End move along the tabs that show, as a tablist does; +N lists every document; `show` opens the tab
    moved to or picked. */
function docTabsKeys(row: HTMLElement, show: (tab: HTMLElement) => void) {
  row.onkeydown = (ev: KeyboardEvent) => {
    const tabs = [...row.querySelectorAll<HTMLElement>('[role="tab"]')].filter(b => !b.hidden), at = tabs.indexOf(ev.target as HTMLElement);
    if (at < 0) return;
    const to = ev.key === 'ArrowRight' ? at + 1 : ev.key === 'ArrowLeft' ? at - 1 : ev.key === 'Home' ? 0 : ev.key === 'End' ? tabs.length - 1 : null;
    if (to === null) return;
    ev.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length]!;
    show(next); next.focus();
  };
  const more = row.querySelector<HTMLElement>('[data-doc-more]');
  if (more) more.onclick = () => {
    const docs = [...row.querySelectorAll<HTMLElement>('.doc-tab-d')];
    // The menu goes inside the window: outside a modal dialog it could not be reached.
    const host = row.closest<HTMLElement>('dialog') || row.closest<HTMLElement>('.decide') || document.body;
    propMenu(host, more, {label: 'Documents', find: docs.length > 8 ? 'Find a document' : '',
      items: docs.map(b => ({text: b.title, html: `<span class="tl-mi-t" title="${esc(b.title)}">${esc(b.dataset.docShort || b.title)}</span>`,
        checked: !b.hidden && b.getAttribute('aria-selected') === 'true', tab: b})),
      onPick: it => { show(it.tab); if (it.tab.isConnected && !it.tab.hidden) it.tab.focus({preventScroll: true}); }});
  };
}
/** Marks the tab `on` selected in a row. A row with +N shows the document `on` in its one document place, and keeps the
    one there under Chat or Files. `shown` false: something else covers the tabs' pane (a task's Details), so none looks
    selected; `on` keeps the keyboard's stop. */
function docTabsPaint(row: ParentNode, on: string, keyOf: (tab: HTMLElement) => string | undefined, shown = true) {
  const tabs = [...row.querySelectorAll<HTMLElement>('.doc-tabs [role="tab"]')];
  const swap = !!row.querySelector('[data-doc-more]') && tabs.some(b => b.classList.contains('doc-tab-d') && keyOf(b) === on);
  for (const b of tabs) {
    const yes = keyOf(b) === on;
    b.setAttribute('aria-selected', String(yes && shown)); b.tabIndex = yes ? 0 : -1;
    if (swap && b.classList.contains('doc-tab-d')) b.hidden = !yes;
  }
}

// ---- one document open: its one version line ("v3 · Ana · 2h ⌄"), which opens the version list and Compare, and the
// version shown, sandboxed as a page is. Kept per host across redraws of the same document.
interface DocViewState { doc: TaskDoc; shown: number; open: boolean; compare: [number, number] | null; seq: number; approval: DocApproval | null; }
// The version of a document a stage asks a person to approve (Flow), and whether the decision model could check it.
interface DocApproval { version: number; unchecked: boolean; }
const DOC_VIEWS = new WeakMap<HTMLElement, DocViewState>();
const DOC_BOUND = new WeakSet<HTMLElement>();  // hosts whose delegated listeners are on, once each
// The CSS a page carries itself, read from its raw HTML: the text of its <style> blocks and of its style= attributes.
const DOC_STYLE_BLOCK = /<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi;
const DOC_STYLE_ATTR = /\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;
// A colour or background declaration: its property, then its value up to the end of the declaration.
const DOC_COLOUR_DECL = /(?:^|[^\w-])(?:color|background(?:-color|-image)?|border(?:-(?:top|right|bottom|left))?(?:-color)?|outline-color)\s*:([^;{}]*)/gi;
// A literal colour in a value: #hex, rgb(), hsl(), or a plain colour name.
const DOC_LITERAL_COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(|(?:^|[^\w-])(?:white|black|gray|grey|silver|red|green|blue|yellow|orange|purple|pink|brown|navy|teal|maroon|olive|lime|aqua|fuchsia|cyan|magenta)\b/i;
/** A page that sets colours of its own and does not say it handles dark. Its CSS (style blocks and style= attributes)
    writes a literal colour in a colour or background declaration, and nothing there mentions prefers-color-scheme or
    color-scheme. Such a page is drawn light in either app theme: its text colours would sit on the app's dark background.
    A colour written through a token (var(--ink), fallback included) is the app's, so a page of tokens does not count,
    nor a page with no colours of its own. Pure: the raw HTML in, a boolean out. */
function pageIsLightOnly(raw: string): boolean {
  const blocks = [...raw.matchAll(DOC_STYLE_BLOCK)].map(m => m[1] ?? '');
  const attrs = [...raw.matchAll(DOC_STYLE_ATTR)].map(m => m[1] ?? m[2] ?? '');
  const css = [...blocks, ...attrs].join('\n');
  if (/prefers-color-scheme|color-scheme/i.test(css)) return false;
  // A picture's url() is data, not a colour of the page, and a var() is the app's token: both come out before the check.
  const own = css.replace(/url\((?:"[^"]*"|'[^']*'|[^)"'])*\)/gi, '').replace(/var\([^()]*\)/gi, '');
  return [...own.matchAll(DOC_COLOUR_DECL)].some(m => DOC_LITERAL_COLOUR.test(m[1] ?? ''));
}
/** The light palette (styles/tokens.css, :root[data-theme="light"]). The app's values can be read only for the theme it is
    in, so a light page drawn in the dark theme takes this set; keep it in step with tokens.css. */
const DOC_LIGHT: Record<string, string> = {'--bg': '#ffffff', '--ink': '#111111', '--link': '#111111', '--muted': '#5e5e5e',
  '--surface': '#f5f5f5', '--surface2': '#ededed', '--line': '#e0e0e0', '--fail': '#c53030', '--ok': '#2f7a4f', '--wait': '#b7791f'};
/** The tokens the spec templates (skills/build-the-page/templates) style through: the colours, which follow the look, and
    the type, the same in both themes. Every document defines them on :root (docLook), so a page that uses them follows the app. */
const DOC_COLOUR_TOKENS = ['--bg', '--ink', '--muted', '--surface', '--surface2', '--line', '--link', '--fail', '--ok', '--wait'];
const DOC_TYPE_TOKENS = ['--sans', '--mono', '--t-sm', '--t-base', '--t-md', '--t-lg'];
/** A document's look: base styles htmlPreviewDoc puts before the page's own (zero specificity, so the page's rules win).
    The app's own theme (its background, text, lines and links, light or dark as the app is), or the light palette for a
    page pageIsLightOnly names. And the template tokens, on :root, in the same colours, so a page styled through them
    (var(--ink), var(--surface)...) follows the app in both themes. */
function docLook(raw: string): string {
  const css = getComputedStyle(document.documentElement), read = (name: string) => css.getPropertyValue(name).trim();
  const theme = document.documentElement.dataset.theme;
  const appDark = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  const light = pageIsLightOnly(raw), dark = appDark && !light;
  const colour = (name: string) => light ? (DOC_LIGHT[name] ?? '') : read(name);
  const tokens = [...DOC_COLOUR_TOKENS.map(name => `${name}:${colour(name)}`), ...DOC_TYPE_TOKENS.map(name => `${name}:${read(name)}`)];
  return `:where(:root){${tokens.join(';')}}`
    + `:where(html){color-scheme:${dark ? 'dark' : 'light'};background:${colour('--bg')};color:${colour('--ink')}}`
    + `:where(a){color:${colour('--link')}}:where(hr){border:0;border-top:1px solid ${colour('--line')}}`
    + `:where(th,td){border:1px solid ${colour('--line')};padding:4px 8px}:where(table){border-collapse:collapse}`
    + `:where(code,pre){background:${colour('--surface2')};border-radius:4px}:where(code){padding:0 3px}:where(pre){padding:8px}`;
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
    else box.replaceChildren(htmlPreviewFrame(text, `${st.doc.name} v${st.shown}`, docLook(text)));
  } catch (e) { if (!stale()) box.innerHTML = `<p class="muted doc-err">${esc(errorMessage(e, ''))}</p>`; }
}
