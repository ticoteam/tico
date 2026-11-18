/* ui/app/thread-library.ts — The composer's Library picker: the files the viewer may read, filtered as they type, and
   one of them put in the message (thread-view.ts attach menu). Classic script: its globals are shared with the other files
   under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first Library pick, so a cold open of Home does not download it (P05b). */
'use strict';
// Library files: the newest the viewer may read, filtered as they type.
async function thvLibOpen(view: ThvView) {
  const C = view.composer, pane = C.el.querySelector('.thv-lib') as HTMLElement;
  pane.hidden = false;
  pane.innerHTML = '<div class="thv-lib-top"><input type="search" class="thv-lib-q" placeholder="Find a file" aria-label="Find a file"><button type="button" class="ghost thv-lib-x" data-lib-close aria-label="Close">✕</button></div><div class="thv-lib-list"><div class="muted">Loading…</div></div>';
  const q = pane.querySelector('.thv-lib-q') as HTMLInputElement, list = pane.querySelector('.thv-lib-list') as HTMLElement;
  // ✕ or Escape puts the picker away and hands the cursor back to the message box.
  const shut = () => { pane.hidden = true; C.el.querySelector<HTMLElement>('.p-text')?.focus(); };
  (pane.querySelector('[data-lib-close]') as HTMLElement).onclick = shut;
  pane.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); shut(); } };
  let files: ThvFileRow[] = [];
  try { files = ((await get<{files?: ThvFileRow[]}>('/v2/files?limit=50', {fresh: true})).files || []).filter(f => f.locator !== 'remote_link' && !f.url && !f.archived); }
  catch (e) { list.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  const draw = () => {
    const words = q.value.trim().toLowerCase();
    const shown = files.filter(f => !words || String(f.title || f.name || '').toLowerCase().includes(words)).slice(0, 20);
    list.innerHTML = shown.length ? shown.map(f => `<button type="button" class="thv-lib-row" data-lib="${esc(f.id)}">${esc(f.title || f.name || f.id)}</button>`).join('')
      : '<div class="muted">No files</div>';
  };
  q.oninput = draw; draw(); q.focus();
  list.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-lib]'); if (!b) return;
    const f = files.find(x => x.id === b.dataset.lib);
    if (f && !C.lib.some(x => x.id === f.id)) C.lib.push({id: f.id, title: f.title || f.name || f.id});
    pane.hidden = true; thvChips(view);
    (C.el.querySelector('.p-text') as HTMLTextAreaElement).focus();
  };
}
