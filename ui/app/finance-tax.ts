/* ui/app/finance-tax.ts — Finance › Tax (sales tax and 1099 workpapers), and "Import history" (a spreadsheet's monthly
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
// A lookup the page's own markup must have: when it is missing the error names it (a plain Error, as a TypeError would
// have stopped the same code a line later).
function finNeed<T>(el: T | null | undefined, what: string): T {
  if (!el) throw new Error(`Missing ${what}`);
  return el;
}
// The workpaper's two views: the sales-tax lines, or the 1099 vendors.
type FinTaxView = 'sales' | '1099';
// The tab's controls, kept for the visit.
interface FinTaxControls { view: FinTaxView; year: number; sel: string }
// One row of a workpaper: a sales-tax line (period, jurisdiction) or a 1099 vendor (vendor, cash paid). The view decides
// which fields a row has, so every field is optional here.
type FinTaxRow = Partial<import('../types/api').FinanceSalesTax['rows'][number] & import('../types/api').Finance1099['rows'][number]>;
// A workpaper as GET /v2/finance/tax/sales-tax or /tax/1099 sends it: the page-level fields of the two reads.
type FinTaxPage = {currency: string; year: number; rows: FinTaxRow[]; due_on?: string; threshold?: number};
// The tab's state on the page: the last read, and the load number that drops a read another one overtook.
interface FinTaxState { data: FinTaxPage | null; load: number }
const FIN_TAX: FinTaxControls = {view: 'sales', year: 0, sel: ''};
const finTaxYear = (view: FinTaxView): number => { const d = new Date(); return view === '1099' && d.getMonth() < 3 ? d.getFullYear() - 1 : d.getFullYear(); };
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
  body.querySelector('[data-fin-year]').onchange = (ev: Event) => { t.year = Number((eventElement(ev) as HTMLSelectElement).value); t.sel = ''; void finTaxLoad(); };
  body.onclick = (ev: Event) => {
    const hit = eventElement(ev);
    const seg = hit?.closest<HTMLElement>('[data-fin-seg] button');
    if (seg) { if (seg.dataset.v !== t.view) { t.view = seg.dataset.v as FinTaxView; t.sel = ''; finSegSet(seg); void finTaxLoad(); } return; }
    if (hit?.closest('[data-fin-export]')) { finTaxCsv(); return; }
    const tr = hit?.closest<HTMLElement>('tr[data-fin-tax]');
    if (tr) { t.sel = tr.dataset.finTax as string; finTaxDraw(); finTaxRail(); }
  };
  void finTaxLoad();
}
const finTaxPath = (view: FinTaxView): string => view === '1099' ? '/v2/finance/tax/1099' : '/v2/finance/tax/sales-tax';
async function finTaxLoad() {
  const state: FinTaxState = FIN.tax, t = FIN_TAX, load = ++state.load;
  let d: FinTaxPage;
  try { d = await get<FinTaxPage>(`${finTaxPath(t.view)}?year=${t.year}${finAs()}`, {fresh: true}); }
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
const finTaxKey = (r: FinTaxRow): string => FIN_TAX.view === '1099' ? String(r.vendor_id || r.vendor) : `${r.period}|${r.code || r.jurisdiction}`;
function finTaxDraw() {
  const state: FinTaxState = FIN.tax, t = FIN_TAX, box = $('#fin-tax'); if (!box || !state.data) return;
  const rows = state.data.rows || [];
  if (!rows.length) { box.innerHTML = finNote(t.view === '1099' ? 'No 1099 vendors.' : 'No sales tax.'); return; }
  const on = (r: FinTaxRow) => t.sel === finTaxKey(r) ? ' class="fin-on"' : '';
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
      <td class="fin-n">${finNum(r.tax_remitted)}</td><td class="fin-n${(r.due ?? 0) > 0 ? ' fin-bad' : ''}">${finNum(r.due)}</td><td class="fin-nowrap">${esc(finDayLabel(r.due_on))}</td></tr>`).join('')}</tbody></table>`;
}
// "2026-Q3" → "Q3 2026".
const finQuarter = (p: string | undefined): string => { const m = /^(\d{4})-Q([1-4])$/.exec(String(p || '')); return m ? `Q${m[2]} ${m[1]}` : String(p || ''); };
function finTaxRail() {
  const state: FinTaxState = FIN.tax, t = FIN_TAX, d = state?.data; if (!d) return;
  const r = (d.rows || []).find(x => finTaxKey(x) === t.sel);
  if (!r) { finRail(''); return; }
  if (t.view === '1099') {
    finRail(`<div class="fin-rail-title"><h2>${esc(r.vendor || '—')}</h2>${r.reportable ? '<span class="pill waiting">1099-NEC due</span>' : ''}</div>
      ${finKv('Cash paid', finUsd(r.cash_paid))}${finKv('Card paid', finUsd(r.card_paid_excluded))}${finKv('Threshold', finUsd(r.threshold ?? d.threshold))}
      ${finKv('TIN', r.tin_on_file ? 'On file' : 'Missing', r.tin_on_file ? '' : 'fin-bad')}${d.due_on ? finKv('File by', esc(finDayLabel(d.due_on))) : ''}`);
    return;
  }
  finRail(`<div class="fin-rail-title"><h2>${esc(r.jurisdiction || r.code || '—')}</h2><span class="fin-big">${finUsd(r.due)}</span></div>
    ${finKv('Quarter', esc(finQuarter(r.period)))}${r.code ? finKv('Code', esc(r.code)) : ''}
    ${finKv('Taxable', finUsd(r.taxable_sales))}${finKv('Exempt', finUsd(r.exempt_sales))}${finKv('Collected', finUsd(r.tax_collected))}
    ${finKv('Remitted', finUsd(r.tax_remitted))}${r.due_on ? finKv('Due on', esc(finDayLabel(r.due_on))) : ''}`);
}

// ----------------------------------------------------------------- import history
// A sheet's line as the server reads it (raw_totals): the month, the line's name, the amount in cents, and the team.
interface FinSheetLine { month: string; line: string; amount: number; team: string }
// A sheet read by finSheetRead: its CSV for the server, the line names to map, and the row count.
interface FinSheet { csv: string; names: string[]; rows: number }
// The import dialog's named controls (its markup is in finImportOpen).
interface FinImportControls { file: HTMLInputElement; units: HTMLSelectElement; basis: HTMLSelectElement; source: HTMLInputElement }
// The dry run's impact: what would post. The generated action reply leaves it out, so it is typed here.
interface FinDryImpact { already_imported?: string; months?: number; rows?: number; zero_rows?: number; accounts?: {code: string; name: string; total: number}[] }
// The import's reply: the action's status and reason, with the dry run's impact and the approver for a send.
type FinDryRun = import('../types/api').FinanceAction & {impact?: FinDryImpact; approver?: string};
// A CSV's rows, quoted fields and all: [[cell, …], …], blank lines dropped.
function finCsvParse(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
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
function finSheetRead(text: string, dollars: boolean): FinSheet {
  const rows = finCsvParse(text.replace(/^﻿/, ''));
  if (!rows.length) throw new Error('The file is empty');
  const head = rows[0]!.map(h => h.trim().toLowerCase()), at = (k: string): number => head.indexOf(k);
  if (at('month') < 0 || at('line') < 0 || at('amount') < 0) throw new Error('Columns: month, line, amount (and team)');
  const out: FinSheetLine[] = [], names: string[] = [];
  for (const [n, r] of rows.slice(1).entries()) {
    const raw = String(r[at('amount')] ?? '').trim().replace(/[$,\s]/g, '').replace(/^\((.*)\)$/, '-$1');
    let cents;
    if (dollars) { if (!/^-?\d+(\.\d{1,2})?$/.test(raw)) throw new Error(`Row ${n + 2}: amount ${raw || 'is empty'}`); cents = Math.round(Number(raw) * 100); }
    else { if (!/^-?\d+$/.test(raw)) throw new Error(`Row ${n + 2}: amount is whole cents`); cents = Number(raw); }
    const line = String(r[at('line')] ?? '').trim();
    out.push({month: String(r[at('month')] ?? '').trim(), line, amount: cents, team: at('team') >= 0 ? String(r[at('team')] ?? '').trim() : ''});
    if (line && !names.some(x => x.toLowerCase() === line.toLowerCase())) names.push(line);
  }
  const cell = (v: string): string => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const team = at('team') >= 0;
  const csv = [team ? 'month,line,amount,team' : 'month,line,amount',
    ...out.map(r => [r.month, r.line, String(r.amount), ...(team ? [r.team] : [])].map(cell).join(','))].join('\n') + '\n';
  return {csv, names, rows: out.length};
}
async function finImportOpen() {
  let chart: FinChartAccount[] = [];
  try { chart = (await finChart('journal')).filter(a => !(a.type === 'equity' && a.subtype === 'opening')); }
  catch (e) { toast(errorMessage(e, 'Did not load.'), true); return; }
  let sheet: FinSheet | null = null, sig = '';
  const lineOpts = (name: string): string => {
    const want = name.toLowerCase(), hit = chart.find(a => a.name.toLowerCase() === want);
    return `<option value="">—</option>${chart.map(a => `<option value="${esc(a.id)}"${a.id === hit?.id ? ' selected' : ''}>${esc(a.number ? a.number + ' ' + a.name : a.name)}</option>`).join('')}`;
  };
  const dlg = finDialog('Import history', `<label>File<input type="file" name="file" accept=".csv,text/csv" required></label>
    <label>Amounts<select name="units"><option value="dollars">Dollars</option><option value="cents">Cents</option></select></label>
    <label>Basis<select name="basis"><option value="accrual">Accrual</option><option value="cash">Cash</option></select></label>
    <label>Source<input name="source" maxlength="120" autocomplete="off" placeholder="Sheet name"></label>
    <div class="fin-map" data-fin-map-rows></div><div class="fin-preview" data-fin-preview hidden></div><p class="fin-ask" data-ask hidden></p>`, 'Preview', async (form: HTMLFormElement) => {
    if (!sheet) throw new Error('Pick a CSV file');
    const mapping: Record<string, string> = {};
    for (const sel of form.querySelectorAll<HTMLSelectElement>('select[data-fin-sheet-line]')) {
      if (!sel.value) throw new Error(`Map ${sel.dataset.finSheetLine}`);
      mapping[sel.dataset.finSheetLine as string] = sel.value;
    }
    const payload = {csv: sheet.csv, basis: el.basis.value, mapping, ...(el.source.value.trim() ? {source_name: el.source.value.trim()} : {})};
    const now = JSON.stringify(payload);
    if (sig !== now) {
      const dry: FinDryRun = await post('/v2/finance/actions', {kind: 'import_raw_totals', payload, dry_run: true});
      if (dry?.status !== 'committed' && dry?.status !== 'needs_approval') throw new Error(dry?.reason || 'Not done');
      sig = now;
      finImportPreview(form, dry);
      finNeed(form.querySelector('[type=submit]'), 'the submit button').textContent = dry.impact?.already_imported ? 'Done' : dry.status === 'needs_approval' ? 'Send' : 'Import';
      return false;
    }
    const prev = finNeed(form.querySelector<HTMLElement>('[data-fin-preview]'), 'the preview').dataset;
    if (prev.again) return true;
    const r = await finAct('import_raw_totals', payload);
    if (r.status === 'committed') toast(`Imported ${(r.result?.months || []).length} months`);
    if (FIN?.pnl) void finPnlLoad();
    if (FIN?.bs) void finBsLoad();
  });
  const form = finNeed(dlg.querySelector('form'), 'the import form');
  const el = form.elements as unknown as FinImportControls;
  const reset = () => {
    sig = ''; finNeed(form.querySelector('[type=submit]'), 'the submit button').textContent = 'Preview';
    const p = finNeed(form.querySelector<HTMLElement>('[data-fin-preview]'), 'the preview'); p.hidden = true; p.innerHTML = ''; delete p.dataset.again;
    finNeed(form.querySelector<HTMLElement>('[data-ask]'), 'the import note').hidden = true;
  };
  const read = async () => {
    const file = el.file.files?.[0], box = finNeed(form.querySelector<HTMLElement>('[data-fin-map-rows]'), 'the line mapping'), err = finNeed(form.querySelector<HTMLElement>('[data-error]'), 'the error line');
    sheet = null; box.innerHTML = ''; reset(); err.hidden = true;
    if (!file) return;
    if (file.size > 512 * 1024) { err.textContent = 'The file is over 512 KB'; err.hidden = false; return; }
    try { sheet = finSheetRead(await file.text(), el.units.value === 'dollars'); }
    catch (e) { err.textContent = errorMessage(e, ''); err.hidden = false; return; }
    box.innerHTML = `<h3 class="fin-h">Lines</h3>${sheet.names.map(n => `<label class="fin-map-row"><span>${esc(n)}</span><select data-fin-sheet-line="${esc(n)}" aria-label="${esc(n)} line">${lineOpts(n)}</select></label>`).join('')}`;
  };
  el.file.addEventListener('change', () => void read());
  el.units.addEventListener('change', () => void read());
  form.addEventListener('input', (ev: Event) => { const name = (eventElement(ev) as HTMLInputElement).name; if (name !== 'file' && name !== 'units') reset(); });
  form.addEventListener('change', (ev: Event) => { if (eventElement(ev)?.matches('select[data-fin-sheet-line], [name=basis]')) reset(); });
}
// What the dry run says would post: months, rows, and each line's total; a file already in the books posts nothing.
function finImportPreview(form: HTMLFormElement, dry: FinDryRun) {
  const box = finNeed(form.querySelector<HTMLElement>('[data-fin-preview]'), 'the preview'), x: FinDryImpact = dry.impact || {}, ask = finNeed(form.querySelector<HTMLElement>('[data-ask]'), 'the import note');
  if (x.already_imported) { box.dataset.again = '1'; box.innerHTML = finKv('Already imported', esc(x.already_imported)); box.hidden = false; return; }
  box.innerHTML = `<h3 class="fin-h">Posts</h3>${finKv('Months', String(x.months ?? ''))}${finKv('Rows', String(x.rows ?? ''))}${x.zero_rows ? finKv('Zero rows', String(x.zero_rows)) : ''}
    ${(x.accounts || []).map(a => finKv(`${a.code} ${a.name}`, finUsd(a.total))).join('')}`;
  box.hidden = false;
  if (dry.status === 'needs_approval') { ask.textContent = finAskText(dry.approver || ''); ask.hidden = false; }
}
