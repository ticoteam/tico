/* ui/app/finance-tax.js — Finance › Tax (sales tax and 1099 workpapers), and "Import history" (a spreadsheet's monthly
   totals posted as raw totals).
   Tax: a year's sales tax by jurisdiction and quarter (taxable, exempt, collected, remitted, due) and its 1099 vendors
   (cash paid, card payments left out, whether a 1099-NEC is due), each with its CSV. Only a viewer who sees everything
   reads them (the server refuses anyone else), so the tab shows to them alone.
   Import history: a CSV of month, line, amount (and optionally team) → each sheet line mapped to a line of the chart →
   a dry run that says what would post → Import (import_raw_totals, finance.book).
   Reads: GET /api/v2/finance/tax/sales-tax, /finance/tax/1099 (and their .csv), /finance/chart?for=journal.
   Writes: import_raw_totals through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- tax
// 1099s are filed early in the year for the year before, so until April the 1099 view opens on last year.
const FIN_TAX = {view: 'sales', year: 0, sel: ''};
const finTaxYear = view => { const d = new Date(); return view === '1099' && d.getMonth() < 3 ? d.getFullYear() - 1 : d.getFullYear(); };
function finTaxPage() {
  const t = FIN_TAX;
  if (!t.year) t.year = finTaxYear(t.view);
  FIN.tax = {data: null, load: 0};
  const now = new Date().getFullYear(), years = Array.from({length: 6}, (_, i) => now - i);
  $('#fin-body').innerHTML = `<div class="fin-bar">${finSeg('tax', 'Workpaper', [['sales', 'Sales tax'], ['1099', '1099']], t.view)}
      <select data-fin-year aria-label="Year">${years.map(y => `<option value="${y}"${y === t.year ? ' selected' : ''}>${y}</option>`).join('')}</select>
      <span class="spacer"></span><button type="button" class="ghost" data-fin-export>Export</button></div>`
    + finSplit(`<div class="fin-scroll" id="fin-tax">${skelRows(6, {dot: false})}</div>`, '', 'Selected row');
  const body = $('#fin-body');
  body.querySelector('[data-fin-year]').onchange = ev => { t.year = Number(ev.target.value); t.sel = ''; void finTaxLoad(); };
  body.onclick = ev => {
    const seg = ev.target.closest('[data-fin-seg] button');
    if (seg) { if (seg.dataset.v !== t.view) { t.view = seg.dataset.v; t.sel = ''; finSegSet(seg); void finTaxLoad(); } return; }
    if (ev.target.closest('[data-fin-export]')) { finTaxCsv(); return; }
    const tr = ev.target.closest('tr[data-fin-tax]');
    if (tr) { t.sel = tr.dataset.finTax; finTaxDraw(); finTaxRail(); }
  };
  void finTaxLoad();
}
const finTaxPath = view => view === '1099' ? '/v2/finance/tax/1099' : '/v2/finance/tax/sales-tax';
async function finTaxLoad() {
  const state = FIN.tax, t = FIN_TAX, load = ++state.load;
  let d;
  try { d = await get(`${finTaxPath(t.view)}?year=${t.year}${finAs()}`, {fresh: true}); }
  catch (e) { if (FIN?.tax === state && state.load === load && $('#fin-tax')) { $('#fin-tax').innerHTML = finFail(e); finRail(''); } return; }
  if (FIN?.tax !== state || state.load !== load || !$('#fin-tax')) return;
  state.data = d;
  const keys = (d.rows || []).map(finTaxKey);
  if (!keys.includes(t.sel)) t.sel = keys[0] || '';
  finTaxDraw(); finTaxRail();
}
// The server's own CSV (the same lines as on screen), as a download.
function finTaxCsv() {
  const t = FIN_TAX, a = document.createElement('a');
  a.href = `${API}${finTaxPath(t.view)}.csv?year=${t.year}${finAs()}`;
  a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}
const finTaxKey = r => FIN_TAX.view === '1099' ? String(r.vendor_id || r.vendor) : `${r.period}|${r.code || r.jurisdiction}`;
function finTaxDraw() {
  const state = FIN.tax, t = FIN_TAX, box = $('#fin-tax'); if (!box || !state.data) return;
  const rows = state.data.rows || [];
  if (!rows.length) { box.innerHTML = finNote(t.view === '1099' ? 'No 1099 vendors.' : 'No sales tax.'); return; }
  const on = r => t.sel === finTaxKey(r) ? ' class="fin-on"' : '';
  if (t.view === '1099') {
    box.innerHTML = `<table class="fin-t"><thead><tr><th>Vendor</th><th>TIN</th><th class="fin-n">Cash paid</th><th class="fin-n">Card paid</th><th>1099-NEC</th></tr></thead><tbody>${rows.map(r =>
      `<tr data-fin-tax="${esc(finTaxKey(r))}"${on(r)}><td><button type="button" class="fin-link">${esc(r.vendor || '—')}</button></td>
        <td>${r.tin_on_file ? 'On file' : '<span class="fin-bad">Missing</span>'}</td><td class="fin-n">${finNum(r.cash_paid)}</td>
        <td class="fin-n muted">${finNum(r.card_paid_excluded)}</td><td>${r.reportable ? '<span class="pill waiting">Due</span>' : '<span class="muted">—</span>'}</td></tr>`).join('')}</tbody></table>`;
    return;
  }
  box.innerHTML = `<table class="fin-t"><thead><tr><th>Quarter</th><th>Jurisdiction</th><th class="fin-n">Taxable</th><th class="fin-n">Exempt</th><th class="fin-n">Collected</th><th class="fin-n">Remitted</th><th class="fin-n">Due</th><th>Due on</th></tr></thead><tbody>${rows.map(r =>
    `<tr data-fin-tax="${esc(finTaxKey(r))}"${on(r)}><td class="fin-nowrap">${esc(finQuarter(r.period))}</td><td><button type="button" class="fin-link">${esc(r.jurisdiction || r.code || '—')}</button></td>
      <td class="fin-n">${finNum(r.taxable_sales)}</td><td class="fin-n">${finNum(r.exempt_sales)}</td><td class="fin-n">${finNum(r.tax_collected)}</td>
      <td class="fin-n">${finNum(r.tax_remitted)}</td><td class="fin-n${r.due_at > 0 ? ' fin-bad' : ''}">${finNum(r.due_at)}</td><td class="fin-nowrap">${esc(finDayLabel(r.due_on))}</td></tr>`).join('')}</tbody></table>`;
}
// "2026-Q3" → "Q3 2026".
const finQuarter = p => { const m = /^(\d{4})-Q([1-4])$/.exec(String(p || '')); return m ? `Q${m[2]} ${m[1]}` : String(p || ''); };
function finTaxRail() {
  const state = FIN.tax, t = FIN_TAX, d = state?.data; if (!d) return;
  const r = (d.rows || []).find(x => finTaxKey(x) === t.sel);
  if (!r) { finRail(''); return; }
  if (t.view === '1099') {
    finRail(`<div class="fin-rail-title"><h2>${esc(r.vendor || '—')}</h2>${r.reportable ? '<span class="pill waiting">1099-NEC due</span>' : ''}</div>
      ${finKv('Cash paid', finUsd(r.cash_paid))}${finKv('Card paid', finUsd(r.card_paid_excluded))}${finKv('Threshold', finUsd(r.threshold ?? d.threshold))}
      ${finKv('TIN', r.tin_on_file ? 'On file' : 'Missing', r.tin_on_file ? '' : 'fin-bad')}${d.due_on ? finKv('File by', esc(finDayLabel(d.due_on))) : ''}`);
    return;
  }
  finRail(`<div class="fin-rail-title"><h2>${esc(r.jurisdiction || r.code || '—')}</h2><span class="fin-big">${finUsd(r.due_at)}</span></div>
    ${finKv('Quarter', esc(finQuarter(r.period)))}${r.code ? finKv('Code', esc(r.code)) : ''}
    ${finKv('Taxable', finUsd(r.taxable_sales))}${finKv('Exempt', finUsd(r.exempt_sales))}${finKv('Collected', finUsd(r.tax_collected))}
    ${finKv('Remitted', finUsd(r.tax_remitted))}${r.due_on ? finKv('Due on', esc(finDayLabel(r.due_on))) : ''}`);
}

// ----------------------------------------------------------------- import history
// A CSV's rows, quoted fields and all: [[cell, …], …], blank lines dropped.
function finCsvParse(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); cell = '';
      if (row.some(c => c.trim())) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell);
  if (row.some(c => c.trim())) rows.push(row);
  return rows;
}
// The sheet the server reads (backend/finance/core/raw_totals.py): a header of month, line, amount (optionally team);
// amounts in whole cents. A sheet in dollars ("2,900.00", "$48") is turned into cents here.
function finSheetRead(text, dollars) {
  const rows = finCsvParse(text.replace(/^﻿/, ''));
  if (!rows.length) throw new Error('The file is empty');
  const head = rows[0].map(h => h.trim().toLowerCase()), at = k => head.indexOf(k);
  if (at('month') < 0 || at('line') < 0 || at('amount') < 0) throw new Error('Columns: month, line, amount (and team)');
  const out = [], names = [];
  for (const [n, r] of rows.slice(1).entries()) {
    const raw = String(r[at('amount')] ?? '').trim().replace(/[$,\s]/g, '').replace(/^\((.*)\)$/, '-$1');
    let cents;
    if (dollars) { if (!/^-?\d+(\.\d{1,2})?$/.test(raw)) throw new Error(`Row ${n + 2}: amount ${raw || 'is empty'}`); cents = Math.round(Number(raw) * 100); }
    else { if (!/^-?\d+$/.test(raw)) throw new Error(`Row ${n + 2}: amount is whole cents`); cents = Number(raw); }
    const line = String(r[at('line')] ?? '').trim();
    out.push({month: String(r[at('month')] ?? '').trim(), line, amount: cents, team: at('team') >= 0 ? String(r[at('team')] ?? '').trim() : ''});
    if (line && !names.some(x => x.toLowerCase() === line.toLowerCase())) names.push(line);
  }
  const cell = v => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const team = at('team') >= 0;
  const csv = [team ? 'month,line,amount,team' : 'month,line,amount',
    ...out.map(r => [r.month, r.line, String(r.amount), ...(team ? [r.team] : [])].map(cell).join(','))].join('\n') + '\n';
  return {csv, names, rows: out.length};
}
async function finImportOpen() {
  let chart = [];
  try { chart = (await finChart('journal')).filter(a => !(a.type === 'equity' && a.subtype === 'opening')); }
  catch (e) { toast(e.message, true); return; }
  let sheet = null, sig = '';
  const lineOpts = name => {
    const want = name.toLowerCase(), hit = chart.find(a => a.name.toLowerCase() === want);
    return `<option value="">—</option>${chart.map(a => `<option value="${esc(a.id)}"${a.id === hit?.id ? ' selected' : ''}>${esc(a.number ? a.number + ' ' + a.name : a.name)}</option>`).join('')}`;
  };
  const dlg = finDialog('Import history', `<label>File<input type="file" name="file" accept=".csv,text/csv" required></label>
    <label>Amounts<select name="units"><option value="dollars">Dollars</option><option value="cents">Cents</option></select></label>
    <label>Basis<select name="basis"><option value="accrual">Accrual</option><option value="cash">Cash</option></select></label>
    <label>Source<input name="source" maxlength="120" autocomplete="off" placeholder="Sheet name"></label>
    <div class="fin-map" data-fin-map-rows></div><div class="fin-preview" data-fin-preview hidden></div><p class="fin-ask" data-ask hidden></p>`, 'Preview', async form => {
    const el = form.elements;
    if (!sheet) throw new Error('Pick a CSV file');
    const mapping = {};
    for (const sel of form.querySelectorAll('select[data-fin-sheet-line]')) {
      if (!sel.value) throw new Error(`Map ${sel.dataset.finSheetLine}`);
      mapping[sel.dataset.finSheetLine] = sel.value;
    }
    const payload = {csv: sheet.csv, basis: el.basis.value, mapping, ...(el.source.value.trim() ? {source_name: el.source.value.trim()} : {})};
    const now = JSON.stringify(payload);
    if (sig !== now) {
      const dry = await post('/v2/finance/actions', {kind: 'import_raw_totals', payload, dry_run: true});
      if (dry?.status !== 'committed' && dry?.status !== 'needs_approval') throw new Error(dry?.reason || 'Not done');
      sig = now;
      finImportPreview(form, dry);
      form.querySelector('[type=submit]').textContent = dry.impact?.already_imported ? 'Done' : dry.status === 'needs_approval' ? 'Send' : 'Import';
      return false;
    }
    const prev = form.querySelector('[data-fin-preview]').dataset;
    if (prev.again) return true;
    const r = await finAct('import_raw_totals', payload);
    if (r.status === 'committed') toast(`Imported ${(r.result?.months || []).length} months`);
    if (FIN?.pnl) void finPnlLoad();
    if (FIN?.bs) void finBsLoad();
  });
  const form = dlg.querySelector('form');
  const reset = () => {
    sig = ''; form.querySelector('[type=submit]').textContent = 'Preview';
    const p = form.querySelector('[data-fin-preview]'); p.hidden = true; p.innerHTML = ''; delete p.dataset.again;
    form.querySelector('[data-ask]').hidden = true;
  };
  const read = async () => {
    const file = form.elements.file.files?.[0], box = form.querySelector('[data-fin-map-rows]'), err = form.querySelector('[data-error]');
    sheet = null; box.innerHTML = ''; reset(); err.hidden = true;
    if (!file) return;
    if (file.size > 512 * 1024) { err.textContent = 'The file is over 512 KB'; err.hidden = false; return; }
    try { sheet = finSheetRead(await file.text(), form.elements.units.value === 'dollars'); }
    catch (e) { err.textContent = e.message; err.hidden = false; return; }
    box.innerHTML = `<h3 class="fin-h">Lines</h3>${sheet.names.map(n => `<label class="fin-map-row"><span>${esc(n)}</span><select data-fin-sheet-line="${esc(n)}" aria-label="${esc(n)} line">${lineOpts(n)}</select></label>`).join('')}`;
  };
  form.elements.file.addEventListener('change', () => void read());
  form.elements.units.addEventListener('change', () => void read());
  form.addEventListener('input', ev => { if (ev.target.name !== 'file' && ev.target.name !== 'units') reset(); });
  form.addEventListener('change', ev => { if (ev.target.matches('select[data-fin-sheet-line], [name=basis]')) reset(); });
}
// What the dry run says would post: months, rows, and each line's total; a file already in the books posts nothing.
function finImportPreview(form, dry) {
  const box = form.querySelector('[data-fin-preview]'), x = dry.impact || {}, ask = form.querySelector('[data-ask]');
  if (x.already_imported) { box.dataset.again = '1'; box.innerHTML = finKv('Already imported', esc(x.already_imported)); box.hidden = false; return; }
  box.innerHTML = `<h3 class="fin-h">Posts</h3>${finKv('Months', String(x.months ?? ''))}${finKv('Rows', String(x.rows ?? ''))}${x.zero_rows ? finKv('Zero rows', String(x.zero_rows)) : ''}
    ${(x.accounts || []).map(a => finKv(`${a.code} ${a.name}`, finUsd(a.total))).join('')}`;
  box.hidden = false;
  if (dry.status === 'needs_approval') { ask.textContent = finAskText(dry.approver || ''); ask.hidden = false; }
}
