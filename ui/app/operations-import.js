/* ui/app/operations-import.js — Import CSV on the Operations page: pick a type and a file; preview with each row's errors and
   likely duplicates for a person to decide; assign a location or customer to all; import; the result per row.
   POST /api/v2/imports/preview and /commit; rows go through the same action path as hand entry.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OPS_IMPORT_TYPES = [['locations', 'Locations'], ['items', 'Products & services'], ['equipment', 'Equipment'], ['due_dates', 'Due dates']];
const OPS_IMPORT_MAX = 5 * 1024 * 1024;
// Which records take a bulk location or customer.
const OPS_IMPORT_BULK = {locations: ['customer_id'], equipment: ['location_id', 'customer_id']};
const opsRowNo = r => r.row ?? r.line ?? r.number ?? '';
const opsRowName = r => r.name || r.label || r.values?.name || r.values?.title || '';
const opsRowDup = r => r.duplicate_of || r.duplicate || r.likely_duplicate || null;
const opsRowErrors = r => (Array.isArray(r.errors) ? r.errors : r.error ? [r.error] : []).map(e => typeof e === 'string' ? e : e?.message || e?.detail || '');

function opsImportOpen(tab) {
  document.querySelectorAll('dialog.ops-import').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal fin-dlg ops-import';
  dlg.setAttribute('aria-label', 'Import CSV');
  document.body.appendChild(dlg);
  dlg.onclose = () => dlg.remove();
  const st = {type: OPS_IMPORT_TYPES.some(([t]) => t === tab) ? tab : 'locations', text: '', filename: '', preview: null, keep: new Set(), dlg};
  opsImportPick(st);
  dlg.showModal();
}
const opsImportFrame = (title, inner) => `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
  <div class="tmodal-body fin-form">${inner}<p class="err" data-error hidden></p></div>`;
function opsImportWire(st) {
  st.dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => st.dlg.close(); });
}
const opsImportError = (st, e) => { const el = st.dlg.querySelector('[data-error]'); el.textContent = e?.message || 'Did not work'; el.hidden = false; };

function opsImportPick(st) {
  st.dlg.innerHTML = opsImportFrame('Import CSV', `${opsField('Type', `<select name="type">${grOpts(OPS_IMPORT_TYPES, st.type, null)}</select>`)}
    ${opsField('File', '<input type="file" name="file" accept=".csv,text/csv">')}
    <div class="fin-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="button" class="primary" data-import-preview>Preview</button></div>`);
  opsImportWire(st);
  const go = st.dlg.querySelector('[data-import-preview]'), file = st.dlg.querySelector('[name=file]'), type = st.dlg.querySelector('[name=type]');
  go.onclick = async () => {
    const chosen = file.files[0];
    if (!chosen) { opsImportError(st, new Error('Choose a file')); return; }
    if (chosen.size > OPS_IMPORT_MAX) { opsImportError(st, new Error('Choose a file under 5 MB')); return; }
    go.disabled = true;
    try {
      st.type = type.value; st.text = await chosen.text(); st.filename = chosen.name; st.keep = new Set();
      st.preview = await post('/v2/imports/preview', {type: st.type, csv: st.text, filename: st.filename});
      opsImportReview(st);
    } catch (e) { opsImportError(st, e.status === 404 ? new Error('Import is not available yet') : e); go.disabled = false; }
  };
}
function opsImportReview(st) {
  const rows = st.preview?.rows || [], bulk = OPS_IMPORT_BULK[st.type] || [];
  const bad = rows.filter(r => opsRowErrors(r).length), dupes = rows.filter(r => !opsRowErrors(r).length && opsRowDup(r));
  const fine = rows.length - bad.length - dupes.length;
  const row = r => {
    const errs = opsRowErrors(r), dup = opsRowDup(r), no = opsRowNo(r);
    const status = errs.length ? `<span class="ops-import-err">${esc(errs.join('; '))}</span>`
      : dup ? `<label class="ops-inline"><input type="checkbox" data-import-keep="${esc(no)}"${st.keep.has(String(no)) ? ' checked' : ''}><span>Likely duplicate of ${esc(dup.name || dup.label || dup)}. Import anyway</span></label>`
      : '<span class="muted">Ready</span>';
    return `<li data-import-row="${esc(no)}"${errs.length ? ' class="has-err"' : ''}><span class="muted ops-import-no">${esc(no)}</span><span class="ops-import-name">${esc(opsRowName(r))}</span><span class="ops-import-st">${status}</span></li>`;
  };
  // Problems first: the rows that need a person, then the rest.
  const ordered = [...bad, ...dupes, ...rows.filter(r => !bad.includes(r) && !dupes.includes(r))];
  st.dlg.innerHTML = opsImportFrame('Import CSV', `<ul class="ops-import-rows">${ordered.map(row).join('')}</ul>
    ${bulk.includes('location_id') ? opsPick('Location for all', 'location_id', opsLocations(), '', '—') : ''}
    ${bulk.includes('customer_id') ? opsPick('Customer for all', 'customer_id', opsCustomers(), '', '—') : ''}
    <div class="fin-foot"><button type="button" class="ghost" data-import-back>Back</button><button type="button" class="primary" data-import-commit${fine + st.keep.size ? '' : ' disabled'}>Import ${fine + st.keep.size}</button></div>`);
  opsImportWire(st);
  st.dlg.querySelector('[data-import-back]').onclick = () => opsImportPick(st);
  const go = st.dlg.querySelector('[data-import-commit]');
  st.dlg.querySelectorAll('[data-import-keep]').forEach(box => {
    box.onchange = () => {
      if (box.checked) st.keep.add(box.dataset.importKeep); else st.keep.delete(box.dataset.importKeep);
      go.textContent = `Import ${fine + st.keep.size}`; go.disabled = !(fine + st.keep.size);
    };
  });
  go.onclick = async () => {
    go.disabled = true;
    const v = n => st.dlg.querySelector(`[name=${n}]`)?.value || '';
    try {
      const result = await post('/v2/imports/commit', {type: st.type, csv: st.text, filename: st.filename, location_id: v('location_id') || undefined,
        customer_id: v('customer_id') || undefined, include_duplicates: [...st.keep].map(Number)});
      opsImportResult(st, result);
      void opsAfterWrite();
    } catch (e) { opsImportError(st, e); go.disabled = false; }
  };
}
function opsImportResult(st, r) {
  const count = x => Array.isArray(x) ? x.length : Number(x || 0);
  const failed = Array.isArray(r?.failed) ? r.failed : [];
  const facts = finKv('Added', esc(String(count(r?.created)))) + finKv('Already there', esc(String(count(r?.already_there ?? r?.skipped))))
    + (count(r?.pending) ? finKv('Sent for approval', esc(String(count(r.pending)))) : '') + finKv('Failed', esc(String(failed.length || count(r?.failed))));
  st.dlg.innerHTML = opsImportFrame('Import CSV', `<div>${facts}</div>
    ${failed.length ? `<ul class="ops-import-rows">${failed.map(f => `<li class="has-err"><span class="muted ops-import-no">${esc(opsRowNo(f))}</span><span class="ops-import-name">${esc(opsRowName(f))}</span><span class="ops-import-st"><span class="ops-import-err">${esc(f.reason || opsRowErrors(f).join('; '))}</span></span></li>`).join('')}</ul>` : ''}
    <div class="fin-foot"><button type="button" class="primary" data-close>Done</button></div>`);
  opsImportWire(st);
}
