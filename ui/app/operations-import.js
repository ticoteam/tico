/* ui/app/operations-import.js — Import CSV on the Operations page: pick a type and a file; preview with each row's errors and
   likely duplicates for a person to decide; assign a location or customer to all; import; the result per row.
   POST /api/v2/imports/preview and /commit; rows go through the same action path as hand entry.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OPS_IMPORT_TYPES = [['locations', 'Locations'], ['items', 'Products & services'], ['equipment', 'Equipment'], ['due_dates', 'Due dates']];
const OPS_IMPORT_MAX = 5 * 1024 * 1024;
// Which records take a bulk location or customer, and what it's called there: a location's own file names its parent.
const OPS_IMPORT_BULK = {locations: [['location_id', 'Parent for all', opsLocations]], equipment: [['location_id', 'Location for all', opsLocations], ['customer_id', 'Customer for all', opsCustomers]]};
const OPS_IMPORT_DONE = {created: 'Added', already_there: 'Already there', needs_approval: 'Sent for approval', possible_duplicate: 'Skipped as duplicates', denied: 'Not allowed', error: 'Failed'};
const OPS_IMPORT_BAD = new Set(['error', 'denied']);

function opsImportOpen(tab) {
  document.querySelectorAll('dialog.ops-import').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal fin-dlg ops-import';
  dlg.setAttribute('aria-label', 'Import CSV');
  document.body.appendChild(dlg);
  dlg.onclose = () => dlg.remove();
  const st = {type: OPS_IMPORT_TYPES.some(([t]) => t === tab) ? tab : 'locations', text: '', preview: null, keep: new Set(), dlg};
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
      st.type = type.value; st.text = await chosen.text(); st.keep = new Set();
      st.preview = await post('/v2/imports/preview', {record_type: st.type, csv: st.text});
      opsImportReview(st);
    } catch (e) { opsImportError(st, e.status === 404 ? new Error('Import is not available yet') : e); go.disabled = false; }
  };
}
const opsImportMatch = m => m?.name || m?.label || 'a record already here';
function opsImportReview(st) {
  const lines = st.preview?.lines || [], bulk = OPS_IMPORT_BULK[st.type] || [];
  const fine = lines.filter(l => l.status === 'new').length;
  const row = l => {
    const status = OPS_IMPORT_BAD.has(l.status) ? `<span class="ops-import-err">${esc(l.reason)}</span>`
      : l.status === 'possible_duplicate' ? `<label class="ops-inline"><input type="checkbox" data-import-keep="${esc(l.line)}"${st.keep.has(l.line) ? ' checked' : ''}><span>Likely duplicate of ${esc(opsImportMatch(l.matches))}. Import anyway</span></label>`
      : `<span class="muted">${esc(l.status === 'new' ? 'Ready' : OPS_IMPORT_DONE[l.status] || l.status)}</span>`;
    return `<li data-import-row="${esc(l.line)}"${OPS_IMPORT_BAD.has(l.status) ? ' class="has-err"' : ''}><span class="muted ops-import-no">${esc(l.line)}</span><span class="ops-import-name">${esc(l.name)}</span><span class="ops-import-st">${status}</span></li>`;
  };
  // Problems first: the rows that need a person, then the rest.
  const rank = l => OPS_IMPORT_BAD.has(l.status) ? 0 : l.status === 'possible_duplicate' ? 1 : 2;
  const ordered = lines.map((l, n) => [l, n]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([l]) => l);
  st.dlg.innerHTML = opsImportFrame('Import CSV', `<ul class="ops-import-rows">${ordered.map(row).join('')}</ul>
    ${bulk.map(([name, label, pairs]) => opsPick(label, name, pairs(), '', '—')).join('')}
    <div class="fin-foot"><button type="button" class="ghost" data-import-back>Back</button><button type="button" class="primary" data-import-commit${fine + st.keep.size ? '' : ' disabled'}>Import ${fine + st.keep.size}</button></div>`);
  opsImportWire(st);
  st.dlg.querySelector('[data-import-back]').onclick = () => opsImportPick(st);
  const go = st.dlg.querySelector('[data-import-commit]');
  st.dlg.querySelectorAll('[data-import-keep]').forEach(box => {
    box.onchange = () => {
      const line = Number(box.dataset.importKeep);
      if (box.checked) st.keep.add(line); else st.keep.delete(line);
      go.textContent = `Import ${fine + st.keep.size}`; go.disabled = !(fine + st.keep.size);
    };
  });
  go.onclick = async () => {
    go.disabled = true;
    const assign = Object.fromEntries(bulk.map(([name]) => [name, st.dlg.querySelector(`[name=${name}]`)?.value || '']).filter(([, v]) => v));
    try {
      opsImportResult(st, await post('/v2/imports/commit', {record_type: st.type, csv: st.text, assign, include_lines: [...st.keep]}));
      void opsAfterWrite();
    } catch (e) { opsImportError(st, e); go.disabled = false; }
  };
}
function opsImportResult(st, r) {
  const facts = Object.entries(OPS_IMPORT_DONE).filter(([k]) => r?.summary?.[k]).map(([k, label]) => finKv(label, esc(String(r.summary[k])))).join('');
  const failed = (r?.lines || []).filter(l => OPS_IMPORT_BAD.has(l.status));
  st.dlg.innerHTML = opsImportFrame('Import CSV', `<div>${facts}</div>
    ${failed.length ? `<ul class="ops-import-rows">${failed.map(l => `<li class="has-err"><span class="muted ops-import-no">${esc(l.line)}</span><span class="ops-import-name">${esc(l.name)}</span><span class="ops-import-st"><span class="ops-import-err">${esc(l.reason)}</span></span></li>`).join('')}</ul>` : ''}
    <div class="fin-foot"><button type="button" class="primary" data-close>Done</button></div>`);
  opsImportWire(st);
}
