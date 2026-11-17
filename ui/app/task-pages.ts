/* ui/app/task-pages.ts — A task's pages at the top of the task: adopting them and the one to open.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/pages.ts, whose other code Home runs, into the task page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

// The pages pinned to a task, as cards at its top. The section moves into each redraw of the task (as its files do,
// tfAdopt), so a loaded frame is not loaded again.
function taskPagesAdopt(d: TaskPagesHost, task: {id?: unknown}) {
  const slot = $('[data-task-pages]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.tpEl && d.tpEl.dataset.task === id) { slot.replaceWith(d.tpEl); return; }
  d.tpOff?.();
  d.tpEl = slot; slot.dataset.task = id;
  // "+" opens the page search: recent pages until something is typed, then what matches; under them, a file dropped
  // (or picked) uploads to the task (task-files.ts). Make page is ⋯ › Make a page (taskPageMakeOpen); its Describe box
  // shows once chosen, or while a draft is kept.
  slot.innerHTML = `<header class="tp-head tw-h"><span class="lbl">Pages</span><button type="button" class="prop-add" data-tp-add aria-expanded="false" aria-label="Add a page" title="Add a page" hidden>${TL_ICON.plus}</button></header>
    <div class="tp-picker" role="group" aria-label="Link a page" hidden><input type="search" placeholder="Find a page" aria-label="Find a page" autocomplete="off">
      <div class="tp-results" aria-live="polite"></div><button type="button" class="tp-drop" data-tp-drop>or drop a file to upload</button></div>
    <form class="tp-make" hidden>${ldotHTML({state: 'still', size: 20})}<input type="text" aria-label="Describe the page" placeholder="Describe the page" maxlength="8000" required><button type="submit" class="primary">Make page</button></form>
    <p class="tp-error err" role="alert" hidden></p><div class="tp-making" role="status" hidden></div><div class="tp-pages"></div>`;
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
          : (await get<{files?: {id: string; title?: string | null}[]}>('/v2/files?' + new URLSearchParams({kind: 'page', limit: '6'}), {fresh: true})).files || [];
        if (d.tpEl !== slot || seq !== search) return;
        // A page already on the task is not offered again.
        const have = new Set([...(slot as HTMLElement).querySelectorAll<HTMLElement>('.tp-row[data-page]')].map(a => a.dataset.page));
        results.innerHTML = rows.filter(p => !have.has(p.id)).map(row).join('') || `<p class="muted">${q ? 'No pages' : 'No other pages'}</p>`;
      } catch (e) { if (d.tpEl === slot && seq === search) results.innerHTML = `<p class="err">${esc(errorMessage(e, ''))}</p>`; }
    }, wait);
  };
  const pick = (open: boolean) => {
    const was = !picker.hidden;
    picker.hidden = !open; add.setAttribute('aria-expanded', String(open));
    if (!open) { if (was) picker.dispatchEvent(new Event('menuclosed')); return; }   // a held redraw goes on (task-props.ts)
    input.value = ''; results.replaceChildren(); find(0);
    // The drop line is for whoever may add files to the task (its Files input, task-files.ts).
    drop.hidden = !$('[data-tf-input]', d) || !!$('[data-tf-add][hidden]', d);
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
    const link = eventElement(ev)?.closest<HTMLButtonElement>('[data-tp-page]'), unlink = eventElement(ev)?.closest<HTMLButtonElement>('[data-tp-unlink]');
    if ((!link && !unlink) || slot.tpBusy) return;
    slot.tpBusy = true;
    const button = link || unlink;
    if (!button) return;
    button.disabled = true;
    try {
      if (link) await post('/v2/pins', {place: 'task:' + id, item: 'page:' + link.dataset.tpPage});
      else await writeRequest('DELETE', '/v2/pins/' + encodeURIComponent(String(unlink?.dataset.tpUnlink)));
      if (d.tpEl !== slot) return;
      if (link) { pick(false); ++search; add.focus(); }
      await taskPagesLoad(d);
    } catch (e) { toast(errorMessage(e, ''), true); }
    finally { slot.tpBusy = false; button.disabled = false; }
  };
  const refresh = () => { if (d.open && d.tpEl === slot) void taskPagesLoad(d); };
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
// ⋯ › Make a page (task-props.ts): the Describe box, focused.
function taskPageMakeOpen(d: TaskPagesHost) {
  const slot = d.tpEl;
  if (!slot) return;
  slot.tpMakeOpen = true; taskPageMakeShow(slot);
  $('.tp-make input', slot)?.focus();
}
