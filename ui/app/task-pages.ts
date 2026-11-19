/* ui/app/task-pages.ts — A task's Files tab: the pages and files linked to the task (its list), the links it takes
   ("Link a page or file", a search that links a page as related) and "New page", plus Make page (⋯ › Make a page).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/pages.ts, whose other code Home runs, into the task page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

// The Files tab's host: the task dialog, which keeps the tab's counter and its last read's order.
interface TftHost extends TaskPagesHost, TfDialog { tftSeq?: number; taskDocs?: TaskDoc[] | null; }
// A page or file in GET /api/v2/tasks/{id}/files?all=1: the task's files, and every page linked to the task in any role
// (a page carries its `links`, and usually `writers` and `title`).
interface TftLink { target: string; role: string; name?: string | null; }
interface TftWriter { actor: string; name?: string | null; }
interface TftItem {
  id: string; name?: string | null; title?: string | null; current_version?: number | null; version?: number | null; mime?: string;
  archived?: boolean | null; updated_at?: string | null; links?: TftLink[] | null; writers?: TftWriter[] | null; versions?: TfVersion[] | null;
}
// The Files tab's key on the task window's tab row (task-modal.ts), and its state, per task id, kept across the task's
// redraws; a page's title, for the Spec cards, by page id.
const TASK_FILES_TAB = 'files';
const TFT_COUNT = new Map<string, number>();   // the pages and files linked to the task: "Files N"
const TFT_ROWS = new Map<string, string>();     // the list as it was last drawn, so a redraw draws it at once
const TFT_TITLES = new Map<string, string>();
const TFT_ITEMS = new Map<string, TftItem[]>();  // the task's last read, before what shows elsewhere is left out
const TFT_PAGES = new Map<string, TaskDoc>();   // a page row as a document, so it opens in the task window, by page id
/** The Files tab's label on the tab row: "Files", or "Files N" once the task's count is read. */
const taskFilesLabel = (n: number | null | undefined): string => n ? `Files ${n}` : 'Files';

// The Files actions move into each redraw so an open picker or draft stays in place.
function taskPagesAdopt(d: TftHost, task: {id?: unknown}) {
  const slot = $('[data-task-pages]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tpEl && d.tpEl.dataset.task === id) { slot.replaceWith(d.tpEl); return; }
  d.tpOff?.();
  d.tpEl = slot; slot.dataset.task = id;
  d.tfFilesChanged = () => taskFilesTabLoad(d, id);
  // The section's head: "Link a page or file" opens the page search (recent pages until something is typed, then what
  // matches), and under it a file dropped (or picked) uploads to the task (task-files.ts). New page is a title and a
  // text (taskNewPageOpen). Make page is ⋯ › Make a page (taskPageMakeOpen); its Describe box shows once chosen, or
  // while a draft is kept.
  slot.innerHTML = `<header class="tp-head"><button type="button" class="tft-btn" data-tp-add aria-expanded="false" hidden>Link a page or file</button><button type="button" class="tft-btn" data-tft-new hidden>New page</button></header>
    <div class="tp-picker" role="group" aria-label="Link a page" hidden><input type="search" placeholder="Find a page" aria-label="Find a page" autocomplete="off">
      <div class="tp-results" aria-live="polite"></div><button type="button" class="tp-drop" data-tp-drop>or drop a file to upload</button></div>
    <form class="tp-make" hidden>${ldotHTML({state: 'still', size: 20})}<input type="text" aria-label="Describe the page" placeholder="Describe the page" maxlength="8000" required><button type="submit" class="primary">Make page</button></form>
    <p class="tp-error err" role="alert" hidden></p><div class="tp-making" role="status" hidden></div>`;
  taskPagesActions(d);
  $('[data-tft-new]', slot).onclick = () => taskNewPageOpen(d, id);
  const make = $('.tp-make', slot), prompt = $('input', make), error = $('.tp-error', slot);
  try { prompt.value = localStorage.getItem(taskMakeDraftKey(id)) || ''; } catch { /* storage may be unavailable */ }
  slot.tpMakeOpen = !!prompt.value;
  const add = $('[data-tp-add]', slot);
  prompt.oninput = () => { try { if (prompt.value) localStorage.setItem(taskMakeDraftKey(id), prompt.value); else localStorage.removeItem(taskMakeDraftKey(id)); } catch { /* storage may be unavailable */ } };
  make.onsubmit = async (ev: Event) => {
    ev.preventDefault();
    if (slot.tpSending || ['making', 'update_required'].includes(slot.tpMaking?.state) || !prompt.value.trim()) return;
    slot.tpSending = true; $('button', make).disabled = true; error.hidden = true;
    try {
      const r = await post('/v2/tasks/' + encodeURIComponent(id) + '/make-page', {text: prompt.value.trim()});
      if (d.tpEl !== slot) return;
      if (r.held) throw new Error('Not sent');
      make.reset(); slot.tpMakeOpen = false;      // sent: the box folds back to "+" once the page is made
      try { localStorage.removeItem(taskMakeDraftKey(id)); } catch { /* storage may be unavailable */ }
      taskPageMakeDraw(d, {state: 'making', conversation_id: r.message.conversation_id});
      await taskPageMakeLoad(d);
    } catch (e) { if (d.tpEl === slot) { error.textContent = errorMessage(e, ''); error.hidden = false; } }
    finally { slot.tpSending = false; $('button', make).disabled = ['making', 'update_required'].includes(slot.tpMaking?.state); }
  };
  const picker = $('.tp-picker', slot), input = $('input', picker), results = $('.tp-results', picker), drop = $('[data-tp-drop]', picker);
  let soon = 0, search = 0;
  const row = (p: {id: string; title?: string | null}) => `<button type="button" class="tp-result" data-tp-page="${esc(p.id)}"><span class="nav-icon" aria-hidden="true">description</span><span class="tp-name">${esc(p.title || 'Untitled')}</span></button>`;
  const find = (wait: number) => {
    clearTimeout(soon);
    const seq = ++search, q = input.value.trim();
    soon = setTimeout(async () => {
      try {
        // Nothing typed: the newest pages; else the pages that match.
        const rows = q ? (await get<import('../types/api').FileSearch>('/v2/files/search?' + new URLSearchParams({q, kind: 'page'}), {fresh: true})).results || []
          : (await get<{files?: {id: string; title?: string | null; may_write?: boolean}[]}>('/v2/files?' + new URLSearchParams({kind: 'page', limit: '6'}), {fresh: true})).files || [];
        if (d.tpEl !== slot || seq !== search) return;
        // A page already on the task (a row of its Files list) is not offered again.
        const have = new Set((TFT_ITEMS.get(id) || []).map(f => f.id));
        results.innerHTML = rows.filter(p => !have.has(p.id) && p.may_write !== false).map(row).join('') || `<p class="muted">${q ? 'No pages' : 'No other pages'}</p>`;
      } catch (e) { if (d.tpEl === slot && seq === search) results.innerHTML = `<p class="err">${esc(errorMessage(e, ''))}</p>`; }
    }, wait);
  };
  const pick = (open: boolean) => {
    const was = !picker.hidden;
    picker.hidden = !open; add.setAttribute('aria-expanded', String(open));
    if (!open) { if (was) picker.dispatchEvent(new Event('menuclosed')); return; }   // a held redraw goes on (task-props.ts)
    input.value = ''; results.replaceChildren(); find(0);
    // The drop line is for whoever may add files to the task (its Files input, task-files.ts).
    drop.hidden = d.canComment === false || !$('[data-tf-input]', d);
    input.focus();
  };
  add.onclick = () => pick(picker.hidden);
  input.oninput = () => find(250);
  // Esc closes the search, not the task; the focus goes back to +. ↓ from the box steps into the results.
  picker.addEventListener('keydown', (ev: KeyboardEvent) => {
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); pick(false); add.focus(); return; }
    const rows: HTMLElement[] = [...(picker as HTMLElement).querySelectorAll<HTMLElement>('.tp-result, .tp-drop:not([hidden])')], at = rows.findIndex(r => r === document.activeElement);
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const next = ev.key === 'ArrowDown' ? at + 1 : at - 1;
      (next < 0 ? input : rows[Math.min(next, rows.length - 1)])?.focus();
    }
  });
  // A file dropped on the search, or picked from its line, uploads to the task and shows under Files: the window's own
  // drop (task-files.ts, a drop anywhere on the task) uploads it; the search lights up while it hovers, then closes.
  drop.onclick = () => $('[data-tf-input]', d)?.click();
  fileDropTarget(picker, () => pick(false));
  slot.onclick = async (ev: MouseEvent) => {
    const link = eventElement(ev)?.closest<HTMLButtonElement>('[data-tp-page]');
    if (!link || slot.tpBusy) return;
    slot.tpBusy = true;
    link.disabled = true;
    try {
      await post('/v2/pages/' + encodeURIComponent(String(link.dataset.tpPage)) + '/links', {target: 'task:' + id, role: 'related'});
      if (d.tpEl !== slot) return;
      pick(false); ++search; add.focus();
      await taskFilesTabLoad(d, id);
    } catch (e) { toast(errorMessage(e, ''), true); }
    finally { slot.tpBusy = false; link.disabled = false; }
  };
  const refresh = () => { if (d.open && d.tpEl === slot) { void taskPagesLoad(d); void taskFilesTabLoad(d, id); } };
  const offs = [liveOn('tasks', (e: TaskPagesLiveEvent) => {
    if (d.open && d.tpEl === slot && (e.kinds || [e.kind]).includes('files') && String(e.id) === id)
      liveSoon('task-pages:' + d.id + ':' + id, refresh, 200);
  }), liveOn('runs', (e: TaskPagesLiveEvent) => {
    if (['making', 'update_required'].includes(slot.tpMaking?.state) && e.conversation_id === slot.tpMaking.conversation_id)
      liveSoon('task-pages:' + d.id + ':' + id, refresh, 200);
  }), liveOn('reset', refresh)];
  d.tpOff = () => { for (const off of offs) off(); slot.tpFollow?.(); };
  void taskPagesLoad(d);
}
function taskPagesActions(d: TftHost) {
  const slot = d.tpEl;
  if (!slot) return;
  const addPage = $('[data-tft-new]', slot);
  addPage.hidden = d.canComment === false;
  if (slot.tpCanLink != null) slot.hidden = !slot.tpCanLink && addPage.hidden;
  const drop = $('[data-tp-drop]', slot);
  if (drop) drop.hidden = d.canComment === false || !$('[data-tf-input]', d);
}
// ⋯ › Make a page (task-props.ts): the Describe box, focused, on the Files tab where the section is.
function taskPageMakeOpen(d: TaskPagesHost) {
  const slot = d.tpEl;
  if (!slot) return;
  taskTabShow(d as ModalDialog, TASK_FILES_TAB);
  slot.tpMakeOpen = true; taskPageMakeShow(slot);
  $('.tp-make input', slot)?.focus();
}

// ----------------------------------------------------------------- the Files tab
// The task's Files tab: every page and file linked to the task (GET /api/v2/tasks/{id}/files?all=1). A page row shows
// its title, the roles it has on this task (chips), v<version> · writers · time, and opens the page; a file row shows
// today's meta (v<version> · time) and opens in the task's viewer (task-files.ts). Its count is the tab's "Files N".
async function taskFilesTabLoad(d: TftHost, id: string | number): Promise<void> {
  const key = String(id);
  d.tftSeq = (d.tftSeq || 0) + 1;
  const seq = d.tftSeq;
  const data = await v2Get<{files?: TftItem[] | null}>(`/v2/tasks/${encodeURIComponent(key)}/files?all=1`, {fresh: true});
  if (!d.open || String(d.dataset.task) !== key || d.tftSeq !== seq) return;
  if (!data) return;                       // a failed read keeps what the last one found
  const items = (data.files || []).filter(f => !f.archived);
  for (const f of items) {
    TFT_TITLES.set(f.id, f.title || f.name || '');
    if (Array.isArray(f.links)) TFT_PAGES.set(f.id, tftPageDoc(f));
  }
  TFT_ITEMS.set(key, items);
  taskFilesPaint(d, key);
  // the Spec cards' titles, now that the pages are read
  for (const card of d.querySelectorAll<HTMLElement>('[data-task-spec]')) {
    const title = TFT_TITLES.get(card.dataset.taskSpec ?? '');
    const text = card.querySelector<HTMLElement>('.task-spec-t');
    if (title && text) { text.textContent = title; card.title = title; }
  }
}
/** Draws the Files list from the last read. What the task shows elsewhere, a Spec card or a document's tab, is left
    out, so nothing shows twice; "Files N" counts what is listed. Drawn again when the task's documents change. */
function taskFilesPaint(d: TftHost, key: string) {
  const all = TFT_ITEMS.get(key);
  if (!all || String(d.dataset.task) !== key) return;
  const elsewhere = new Set((d.taskDocs || []).map(x => x.page_id || x.file_id));
  const items = all.filter(f => !elsewhere.has(f.id));
  const plain = new Map(all.flatMap<[string, TfFile]>(f => !Array.isArray(f.links) && f.versions?.length ? [[f.id, tfNorm({
    id: f.id, name: f.name || 'file', current_version: f.current_version ?? f.version ?? 0,
    ...(f.mime ? {mime: f.mime} : {}), versions: f.versions,
  })]] : []));
  d.tfListed = plain;
  const rows = taskFilesRowsHTML(key, items, plain);
  TFT_COUNT.set(key, items.length); TFT_ROWS.set(key, rows);
  const list = d.querySelector<HTMLElement>('[data-tft-list]');
  if (list && list.dataset.html !== rows) {
    list.innerHTML = list.dataset.html = rows;
    tfLazy(list);
  }
  if (d.tfEl) tfPaint(d);
  const label = taskFilesLabel(items.length), tab = d.querySelector<HTMLElement>(`.tw-tabs [data-task-tab="${TASK_FILES_TAB}"] .doc-tab-t`);
  if (tab) tab.textContent = label;
  const btn = d.querySelector<HTMLElement>(`.tw-tabs [data-task-tab="${TASK_FILES_TAB}"]`);
  if (btn) btn.title = label;
}
/** A page row as a document (task-docs.ts), so it opens in the task window with its version line, newest first. */
function tftPageDoc(f: TftItem): TaskDoc {
  const v = f.current_version ?? f.version ?? 1;
  const versions = (f.versions || []).map(x => ({n: x.n, by: x.by || '', at: x.created_at || ''})).sort((a, b) => b.n - a.n);
  const newest = versions[0] || {n: v, by: '', at: f.updated_at || ''};
  return {name: f.title || f.name || 'Page', title: f.title ?? null, file_id: f.id, page_id: f.id, version: v,
    by: newest.by, at: newest.at, versions: versions.length ? versions : [newest], kind: 'html'};
}
/** The list's rows: a page (a file carrying its `links`) or a file, in the order the task's files read gives them. */
function taskFilesRowsHTML(id: string, items: TftItem[], plain: Map<string, TfFile>): string {
  if (!items.length) return '<p class="muted tft-none">Nothing linked yet.</p>';
  return items.map(f => {
    const file = plain.get(f.id);
    return file ? tfFileRowHTML(file) : Array.isArray(f.links) ? tftPageRowHTML(id, f) : tftFileRowHTML(f);
  }).join('');
}
function tftPageRowHTML(id: string, f: TftItem): string {
  const chips = (f.links || []).filter(l => l.target === 'task:' + id).map(l => `<span class="tft-chip">${esc(l.role)}</span>`).join('');
  const writers = f.writers?.length ? f.writers.map(w => w.name || actorLabel(w.actor))
    : [...new Set((f.versions || []).map(v => v.by ? actorLabel(v.by) : ''))];
  const v = f.current_version ?? f.version ?? null, when = f.updated_at ?? f.versions?.[0]?.created_at ?? null;
  const meta = [v != null ? `v${v}` : '', [...new Set(writers.filter(Boolean))].join(', '), when ? ago(when) : ''].filter(Boolean).join(' · ');
  return `<a class="tft-row" href="${esc(pageHref(f.id))}" data-tft-page="${esc(f.id)}">`
    + '<span class="nav-icon tft-ic" aria-hidden="true">description</span>'
    + `<span class="tft-main"><span class="tft-t">${esc(f.title || f.name || 'Untitled')}</span><span class="tft-m muted">${esc(meta)}</span></span>${chips}</a>`;
}
function tftFileRowHTML(f: TftItem): string {
  const v = f.current_version ?? f.version ?? null, when = f.versions?.find(x => x.n === v)?.created_at ?? f.updated_at ?? null;
  const meta = [v != null ? `v${v}` : '', when ? ago(when) : ''].filter(Boolean).join(' · ');
  return `<button type="button" class="tft-row" data-tft-file="${esc(f.id)}">`
    + '<span class="nav-icon tft-ic" aria-hidden="true">draft</span>'
    + `<span class="tft-main"><span class="tft-t">${esc(f.name || 'file')}</span><span class="tft-m muted">${esc(meta)}</span></span></button>`;
}
// New page: a minimal dialog, a title and the page's text, saved (POST /api/v2/pages) as the task's related page.
function taskNewPageOpen(d: TftHost, id: string) {
  const dlg = document.createElement('dialog');
  dlg.className = 'tft-new';
  dlg.innerHTML = '<form class="tft-new-form">'
    + '<label class="tft-field"><span>Title</span><input name="title" required maxlength="200" autocomplete="off"></label>'
    + '<label class="tft-field"><span>Text</span><textarea name="text" required rows="12" spellcheck="false"></textarea></label>'
    + '<p class="err tft-new-err" role="alert" hidden></p>'
    + '<div class="tft-new-acts"><button type="button" class="ghost" data-tft-cancel>Cancel</button><button type="submit" class="primary">Save</button></div></form>';
  document.body.append(dlg);
  const form = $('form', dlg) as HTMLFormElement, title = $('[name="title"]', dlg) as HTMLInputElement, text = $('[name="text"]', dlg) as HTMLTextAreaElement;
  const error = $('.tft-new-err', dlg), save = $('button[type="submit"]', dlg) as HTMLButtonElement;
  dlg.addEventListener('close', () => dlg.remove());
  $('[data-tft-cancel]', dlg).onclick = () => dlg.close();
  form.onsubmit = async (ev: Event) => {
    ev.preventDefault();
    const name = title.value.trim();
    save.disabled = true; error.hidden = true;
    try {
      await post('/v2/pages', {title: name, text: text.value, task: id, as: 'related', name: taskPageSlug(name) + '.html'});
      dlg.close();
      toast('Saved');
      await taskFilesTabLoad(d, id);
    } catch (e) { error.textContent = errorMessage(e, ''); error.hidden = false; }
    finally { save.disabled = false; }
  };
  dlg.showModal();
  title.focus();
}
const taskPageSlug = (name: string): string => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'page';
