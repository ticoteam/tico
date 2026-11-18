/* ui/app/thread-library.ts — The Library picker: the files the viewer may read, filtered as they type, each with its kind's
   mark. The composer's Link a file puts the pick in the message (thread-view.ts attach menu); the meeting window's Show a
   Tico file and Link a file use the same picker (ui/app/call-window.ts, libPickerMount). Classic script: its globals are
   shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first Library pick, so a cold open of Home does not download it (P05b). */
'use strict';
// What a picker does with a pick and when it goes; `links`: files that are only a link (a Google file) are offered too.
// `bare`: no ✕ of its own, for a picker its host closes (Escape, a click outside, its button again).
interface LibPickerOpts { pick: (f: ThvFileRow) => void; close: () => void; links?: boolean; placeholder?: string; bare?: boolean; }
// The picker in `pane`: the newest files the viewer may read, filtered as they type. ✕ or Escape closes it.
async function libPickerMount(pane: HTMLElement, opts: LibPickerOpts): Promise<void> {
  const say = opts.placeholder || 'Find a file';
  pane.hidden = false;
  pane.innerHTML = `<div class="thv-lib-top"><input type="search" class="thv-lib-q" placeholder="${esc(say)}" aria-label="${esc(say)}">${opts.bare ? '' : '<button type="button" class="ghost thv-lib-x" data-lib-close aria-label="Close">✕</button>'}</div><div class="thv-lib-list"><div class="muted">Loading…</div></div>`;
  const q = pane.querySelector('.thv-lib-q') as HTMLInputElement, list = pane.querySelector('.thv-lib-list') as HTMLElement;
  const x = pane.querySelector<HTMLElement>('[data-lib-close]');
  if (x) x.onclick = opts.close;
  pane.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); opts.close(); } };
  q.focus();
  let files: ThvFileRow[] = [];
  try {
    files = ((await get<{files?: ThvFileRow[]}>('/v2/files?limit=50', {fresh: true})).files || [])
      .filter(f => !f.archived && (opts.links || f.locator !== 'remote_link' && !f.url));
  } catch (e) { list.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  const draw = () => {
    const words = q.value.trim().toLowerCase();
    const shown = files.filter(f => !words || String(f.title || f.name || '').toLowerCase().includes(words)).slice(0, 20);
    list.innerHTML = shown.length ? shown.map(f => `<button type="button" class="thv-lib-row" data-lib="${esc(f.id)}"><span class="nav-icon" aria-hidden="true">${
      f.locator === 'remote_link' || f.url ? 'link' : LF_ICON[f.library_kind || ''] || LF_ICON.other}</span><span class="thv-lib-t">${esc(f.title || f.name || f.id)}</span></button>`).join('')
      : '<div class="muted">No files</div>';
  };
  q.oninput = draw; draw();
  list.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-lib]'); if (!b) return;
    const f = files.find(x => x.id === b.dataset.lib);
    if (f) opts.pick(f);
  };
}
// The composer's Link a file: the pick goes in the message, as a chip, or to the view's own `libPick`.
async function thvLibOpen(view: ThvView) {
  const C = view.composer, pane = C.el.querySelector('.thv-lib') as HTMLElement;
  // ✕, Escape or a pick puts the picker away and hands the cursor back to the message box.
  const shut = () => { pane.hidden = true; C.el.querySelector<HTMLElement>('.p-text')?.focus(); };
  await libPickerMount(pane, {close: shut, pick: f => {
    const lib = {id: f.id, title: f.title || f.name || f.id};
    shut();
    if (view.opts.libPick) { view.opts.libPick(lib); return; }
    if (!C.lib.some(x => x.id === f.id)) C.lib.push(lib);
    thvChips(view);
  }});
}
