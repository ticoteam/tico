/* A CSV or TSV file's rows, for the chat's sheet card, its full table and the file viewer. */

export interface CsvOptions {
  /** The most rows read; the rest of the text is left unread. */
  max?: number;
  /** The text is only the start of a longer file: a last record it cuts off (no line end after it, or still inside
      quotes) is dropped rather than shown short. */
  partial?: boolean;
}

/** A quoted field can hold the delimiter, line breaks, and a literal quote written twice. */
export function csvRows(text: string, delimiter = ',', { max = 500, partial = false }: CsvOptions = {}): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  let start = text.charCodeAt(0) === 0xfeff ? 1 : 0;
  for (let i = start; i < text.length && rows.length < max; i++) {
    const c = text.charAt(i);
    if (quoted) {
      if (c !== '"') cell += c;
      else if (text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = false;
    } else if (c === '"' && !cell) quoted = true;
    else if (c === delimiter) {
      row.push(cell);
      cell = '';
    } else if (c === '\n' || c === '\r') {
      if (i > start) rows.push([...row, cell]);
      if (c === '\r' && text[i + 1] === '\n') i++;
      row = [];
      cell = '';
      start = i + 1;
    } else cell += c;
  }
  // what is left had no line end after it: the whole file's last record, or where a partial read stopped
  if (start < text.length && rows.length < max && !partial) rows.push([...row, cell]);
  return rows;
}

/** The delimiter a sheet's kind uses. */
export const delimiterOf = (kind: string) => (kind === 'tsv' ? '\t' : ',');
