/* ui/app/directory-settings.ts — Settings › Directory (#/settings/directory): the Directory's types and their fields,
   and the shared places. ui/app/settings.ts owns the tab and calls directorySettingsShow when it opens.
   Reads: GET /api/v2/entities/types, /types/<key> and /counts. It writes nothing. */
'use strict';

let DIRS_RUN = 0;

async function directorySettingsShow(box: HTMLElement): Promise<void> {
  const run = ++DIRS_RUN;
  box.innerHTML = skelRows(4, { card: true, dot: false });
  let types: DirType[], counts: DirCount[];
  try {
    const [t, n] = await Promise.all([
      get<{ types: DirType[] }>('/v2/entities/types', { fresh: true }),
      get<{ counts: DirCount[] }>('/v2/entities/counts', { fresh: true }),
    ]);
    types = t.types.filter(x => x.mode !== 'archived'); counts = n.counts;
  } catch (e) {
    if (run === DIRS_RUN) box.innerHTML = dirErr(e);
    return;
  }
  if (run !== DIRS_RUN) return;
  const counted = new Map(counts.map(c => [c.type, c]));
  types.sort((a, b) => dirTabOrder(a.key) - dirTabOrder(b.key));
  box.innerHTML = `<section class="card" data-dirs-types><header><h2>Types</h2></header>
      <table class="tt-list dir-kv-table"><thead><tr><th>Type</th><th>Count</th><th>Added by</th></tr></thead><tbody>
      ${types.map(t => { const n = counted.get(t.key); return `<tr data-dirs-type="${esc(t.key)}">
        <td><a href="${dirListHref(t.key)}">${esc(t.name)}</a></td>
        <td class="tnum" data-label="Count">${n ? esc(dirN(n.count, n.approximate)) : '0'}</td>
        <td class="muted" data-label="Added by">${t.built_in ? 'Built in' : esc(dirWho(t.created_by_type && t.created_by_id ? `${t.created_by_type}:${t.created_by_id}` : ''))}</td></tr>`; }).join('')}
      </tbody></table></section>
    <section class="card" data-dirs-fields><header><h2>Fields</h2><span class="spacer"></span>
      <select data-dirs-field-type aria-label="Type">${types.map(t => `<option value="${esc(t.key)}"${t.key === 'organization' ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select></header>
      <div data-dirs-field-body>${skelRows(3, { dot: false })}</div></section>
    <section class="card" data-dirs-places><header><h2>Shared places</h2></header>
      <div class="dir-set-row"><span>Use shared places</span><span class="pill ok" data-dirs-places-on>On</span></div>
      <p class="muted">People, notes and mail stay here.</p></section>`;
  const select = box.querySelector<HTMLSelectElement>('[data-dirs-field-type]');
  const fields = box.querySelector<HTMLElement>('[data-dirs-field-body]');
  if (select && fields) {
    select.addEventListener('change', () => { void dirsFields(fields, select.value, run); });
    void dirsFields(fields, select.value, run);
  }
}
async function dirsFields(box: HTMLElement, key: string, run: number): Promise<void> {
  box.innerHTML = skelRows(3, { dot: false });
  try {
    const d = await get<DirDescribe>(`/v2/entities/types/${encodeURIComponent(key)}`, { fresh: true });
    if (run !== DIRS_RUN) return;
    box.innerHTML = d.attributes.length
      ? `<table class="tt-list dir-kv-table"><thead><tr><th>Field</th><th>Kind</th><th>Owner</th></tr></thead><tbody>
        ${d.attributes.map(a => `<tr data-dirs-field="${esc(a.key)}"><td><strong>${esc(a.name)}</strong></td>
          <td class="muted" data-label="Kind">${esc([a.value_type, a.multiple ? 'many' : '', a.sensitive ? 'sensitive' : ''].filter(Boolean).join(' · '))}</td>
          <td class="muted" data-label="Owner">${esc(a.owner.startsWith('connection:') ? dirCap(a.owner.slice(11)) : 'Tico')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted dir-none">None yet.</p>';
  } catch (e) {
    if (run === DIRS_RUN) box.innerHTML = dirErr(e);
  }
}
