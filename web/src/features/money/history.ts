/* Import history's sheet (finance-tax.ts): a CSV of month, line, amount and optionally team, as the server reads it
   (backend/finance/core/raw_totals.py): amounts in whole cents. A sheet in dollars ("2,900.00", "$48") becomes cents. */

export interface Sheet {
  /** The CSV the server reads. */
  csv: string;
  /** Each sheet line once, to map to a line of the chart. */
  names: string[];
  rows: number;
}

/** A CSV's rows, quoted fields and all; blank lines dropped. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      cell = '';
      if (row.some((c) => c.trim())) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell);
  if (row.some((c) => c.trim())) rows.push(row);
  return rows;
}

const quote = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

/** The sheet read and checked; a row the server would refuse throws, naming the row. */
export function readSheet(text: string, dollars: boolean): Sheet {
  const rows = parseCsv(text.replace(/^\uFEFF/, ''));
  if (!rows.length) throw new Error('The file is empty');
  const head = rows[0]!.map((h) => h.trim().toLowerCase());
  const at = (k: string) => head.indexOf(k);
  if (at('month') < 0 || at('line') < 0 || at('amount') < 0) throw new Error('Columns: month, line, amount (and team)');
  const team = at('team') >= 0;
  const names: string[] = [];
  const out = rows.slice(1).map((r, n) => {
    const raw = String(r[at('amount')] ?? '')
      .trim()
      .replace(/[$,\s]/g, '')
      .replace(/^\((.*)\)$/, '-$1');
    let cents: number;
    if (dollars) {
      if (!/^-?\d+(\.\d{1,2})?$/.test(raw)) throw new Error(`Row ${n + 2}: amount ${raw || 'is empty'}`);
      cents = Math.round(Number(raw) * 100);
    } else {
      if (!/^-?\d+$/.test(raw)) throw new Error(`Row ${n + 2}: amount is whole cents`);
      cents = Number(raw);
    }
    const line = String(r[at('line')] ?? '').trim();
    if (line && !names.some((x) => x.toLowerCase() === line.toLowerCase())) names.push(line);
    return [String(r[at('month')] ?? '').trim(), line, String(cents), ...(team ? [String(r[at('team')] ?? '').trim()] : [])];
  });
  const csv = [team ? 'month,line,amount,team' : 'month,line,amount', ...out.map((r) => r.map(quote).join(','))].join('\n') + '\n';
  return { csv, names, rows: out.length };
}
