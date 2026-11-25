import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode, type SyntheticEvent } from 'react';
import { rangeTextQuery, zipListQuery, type Text } from '@/data';
import { Empty } from '@/kit/Empty';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Markdown, parseDiff } from '@/kit/Markdown';
import { Viewer } from '@/kit/Viewer';
import { csvRows, delimiterOf, firstLines, isFolder, parseEml, parseIcs, parseVcf, stripAnsi, type FileKind } from '@/lib';
import { ReadFault } from '@/pagekinds';
import './cards.css';

/* A chat's file that shows what it holds: the first rows of a sheet, the first lines of a text, a diff, an invite, a
   contact, an email's header, a zip's names. Each card reads only the start of its file (a zip, its end) through a
   ranged read, and draws it as text. A press opens it in the full-screen layer, read further but still bounded (2 MB,
   2,000 rows of 200 cells, 5,000 lines, 1,000 names), with a line and Download wherever a bound cuts it. Office files
   are the plain chip with Download. */

export type CardKind = Extract<FileKind, 'csv' | 'tsv' | 'text' | 'markdown' | 'diff' | 'ics' | 'vcf' | 'eml' | 'zip'>;
const CARDS = new Set<FileKind>(['csv', 'tsv', 'text', 'markdown', 'diff', 'ics', 'vcf', 'eml', 'zip']);
export const isCardKind = (k: FileKind): k is CardKind => CARDS.has(k);

/** How much of a file each card reads, and the most a full view reads. */
const READ: Record<CardKind, number> = {
  csv: 16 * 1024,
  tsv: 16 * 1024,
  text: 8 * 1024,
  markdown: 8 * 1024,
  diff: 16 * 1024,
  ics: 16 * 1024,
  vcf: 16 * 1024,
  eml: 16 * 1024,
  zip: 0,
};
const FULL = 2 * 1024 * 1024;
const CARD_ROWS = 8;
const CARD_COLUMNS = 12;
const CARD_LINES = 12;
const CARD_DIFF_LINES = 16;
const CARD_NAMES = 8;
const FULL_ROWS = 2_000;
const FULL_CELLS = 200;
const FULL_LINES = 5_000;

const ICON: Partial<Record<CardKind, IconName>> = { csv: 'columns', tsv: 'columns', ics: 'calendar', vcf: 'person', eml: 'mail', zip: 'archive' };

function bytesWord(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}
const count = (n: number) => n.toLocaleString();

const typeWord = (name: string) => /\.([a-z0-9]{1,5})$/i.exec(name)?.[1]?.toUpperCase() ?? '';

export interface FileCardProps {
  kind: CardKind;
  url: string;
  name: string;
  mime: string;
  size?: number;
}

/** The layer is a portal, but React still bubbles its presses up through the message it opened from: stopped here, a
    long press or right click inside it never opens that message's Copy and Reply sheet. */
const stop = (e: SyntheticEvent) => e.stopPropagation();

/** One card for every kind: its face here, and the full-screen layer it opens. */
export function FileCard(p: FileCardProps) {
  const [open, setOpen] = useState(false);
  const type = typeWord(p.name);
  return (
    <>
      <div className={`pcard fcard is-${p.kind}`}>
        <div className="fcard-face" aria-hidden="true">
          <Face {...p} />
        </div>
        <span className="pcard-foot">
          <Icon name={ICON[p.kind] ?? 'file'} />
          <span>{p.name}</span>
          {type && <em className="fcard-type">{type}</em>}
          <Icon name="expand" className="end" />
        </span>
        <button type="button" className="pcard-hit" aria-label={p.name} onClick={() => setOpen(true)} />
      </div>
      {open && (
        <span className="fcard-layer" onClick={stop} onContextMenu={stop} onTouchStart={stop} onPointerDown={stop}>
          <Full {...p} onClose={() => setOpen(false)} />
        </span>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- the card's face */

function Face(p: FileCardProps) {
  const zip = p.kind === 'zip';
  const empty = p.size === 0; // a 0-byte file has nothing to read
  const read = useQuery({ ...rangeTextQuery(p.url, READ[p.kind] || 1), enabled: !zip && !empty });
  const names = useQuery({ ...zipListQuery(p.url), enabled: zip && !empty });
  if (empty) return <Lines text="" />;
  if (zip) {
    if (names.isPending) return <span className="fcard-wait" />;
    const z = names.data;
    if (!z) return <Lines text={p.size ? bytesWord(p.size) : ''} />;
    return (
      <div className="fcard-zip">
        <b>{z.count === null ? bytesWord(z.total) : `${count(z.count)} ${z.count === 1 ? 'file' : 'files'}`}</b>
        {z.entries.slice(0, CARD_NAMES).map((e, i) => (
          <span key={i} className={isFolder(e) ? 'is-folder' : undefined}>
            {e.name}
          </span>
        ))}
      </div>
    );
  }
  if (read.isPending) return <span className="fcard-wait" />;
  if (!read.data) return <span className="fcard-wait is-none" />;
  return <FaceOf {...p} read={read.data} />;
}

function FaceOf({ kind, read }: FileCardProps & { read: Text }) {
  const { text, truncated } = read;
  if (kind === 'csv' || kind === 'tsv') {
    const rows = csvRows(text, delimiterOf(kind), { max: CARD_ROWS, partial: truncated }).map((r) => r.slice(0, CARD_COLUMNS));
    return <Table rows={rows} className="fcard-table" />;
  }
  if (kind === 'diff') return <Diff text={firstLines(text, CARD_DIFF_LINES)} wrap={false} />;
  if (kind === 'ics') {
    const ev = parseIcs(text);
    if (ev) return <Facts rows={[['', ev.title], ['When', ev.when], ['Where', ev.where]]} />;
  }
  if (kind === 'vcf') {
    const c = parseVcf(text);
    if (c) return <Facts rows={[['', c.name], ['Email', c.email], ['Phone', c.phone]]} />;
  }
  if (kind === 'eml') {
    const m = parseEml(text);
    if (m) return <Facts rows={[['', m.subject], ['From', m.from], ['Date', m.date]]} />;
  }
  return <Lines text={firstLines(stripAnsi(text), CARD_LINES)} />;
}

const Lines = ({ text }: { text: string }) => <pre className="fcard-lines">{text}</pre>;

/** Label and value lines; the first, with no label, is the title. Empty values are left out. */
function Facts({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="fcard-facts">
      {rows
        .filter(([, v]) => v)
        .map(([k, v], i) =>
          k ? (
            <div key={i}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ) : (
            <div key={i} className="is-title">
              <dd>{v}</dd>
            </div>
          ),
        )}
    </dl>
  );
}

function Table({ rows, className }: { rows: string[][]; className: string }) {
  const [head, ...body] = rows;
  return (
    <table className={`table ${className}`}>
      {head && (
        <thead>
          <tr>
            {head.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {body.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => (
              <td key={j}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A unified diff in the app's greys, as a chat's ```diff block draws one (kit/Markdown/diff.ts). */
function Diff({ text, wrap }: { text: string; wrap: boolean }) {
  return (
    <div className="md-diff fcard-diff">
      <pre className={wrap ? 'is-wrap' : 'is-nowrap'}>
        <code>
          {parseDiff(text).map((l, i) => (
            <span key={i} className={`dl is-${l.kind}`}>
              <span className="dl-sign" aria-hidden={!l.sign || undefined}>
                {l.sign}
              </span>
              <span className="dl-text">{l.text || ' '}</span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

/* ---------------------------------------------------------------- the full-screen layer */

function Full(p: FileCardProps & { onClose: () => void }) {
  const [wrap, setWrap] = useState(false);
  const type = typeWord(p.name);
  return (
    <Viewer
      open
      onClose={p.onClose}
      title={p.name}
      meta={type || undefined}
      actions={
        <>
          {p.kind === 'diff' && <IconButton icon="wrap" label="Wrap" quiet on={wrap} onClick={() => setWrap((w) => !w)} />}
          <a className="ib" href={p.url} download={p.name} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </>
      }
    >
      {p.kind === 'zip' ? <ZipFull {...p} /> : <TextFull {...p} wrap={wrap} />}
    </Viewer>
  );
}

/** One line where a bound cut the view short: what is shown, and the whole file. */
function Cut({ shown, url, name }: { shown: string; url: string; name: string }) {
  return (
    <p className="fcard-cut">
      {shown}{' '}
      <a href={url} download={name}>
        Download
      </a>
    </p>
  );
}

/** The first `n` lines, and whether there were more. */
function upTo(text: string, n: number): [string, boolean] {
  let at = -1;
  for (let i = 0; i < n; i++) {
    at = text.indexOf('\n', at + 1);
    if (at < 0) return [text, false];
  }
  return at + 1 < text.length ? [text.slice(0, at + 1), true] : [text, false];
}

/** The card's read again when it held the whole file (the same query), else up to 2 MB. */
function TextFull(p: FileCardProps & { wrap: boolean }) {
  const empty = p.size === 0;
  const card = useQuery({ ...rangeTextQuery(p.url, READ[p.kind] || 1), enabled: !empty });
  const whole = card.data && !card.data.truncated;
  const full = useQuery({ ...rangeTextQuery(p.url, FULL), enabled: card.isSuccess && !whole && !empty });
  const read = whole ? card : full;
  if (empty) return <Empty title={p.name} icon="file" text="0 B" />;
  if (read.isError || card.isError) return <ReadFault what="This file" reads={[card, read]} />;
  if (!read.data) return null;
  const { text, truncated, total } = read.data;
  let body: ReactNode;
  let cut = truncated ? `First ${bytesWord(FULL)} of ${bytesWord(total)}` : '';
  if (p.kind === 'csv' || p.kind === 'tsv') {
    const all = csvRows(text, delimiterOf(p.kind), { max: FULL_ROWS + 2, partial: truncated });
    const rows = all.slice(0, FULL_ROWS + 1).map((r) => r.slice(0, FULL_CELLS));
    if (all.length > FULL_ROWS + 1) cut = `First ${count(FULL_ROWS)} rows`;
    else if (all.some((r) => r.length > FULL_CELLS)) cut = `First ${FULL_CELLS} columns`;
    body = <Table rows={rows} className="fcard-full-table" />;
  } else {
    const [lines, more] = upTo(text, FULL_LINES);
    if (more) cut = `First ${count(FULL_LINES)} lines`;
    if (p.kind === 'diff') body = <Diff text={lines} wrap={p.wrap} />;
    else if (p.kind === 'markdown')
      body = (
        <article className="fcard-paper read">
          <Markdown text={lines} />
        </article>
      );
    else body = <pre className="fcard-paper fcard-text">{stripAnsi(lines)}</pre>;
  }
  return (
    <div className={`vw-scroll fcard-full is-${p.kind}`}>
      {body}
      {cut && <Cut shown={cut} url={p.url} name={p.name} />}
    </div>
  );
}

function ZipFull(p: FileCardProps) {
  const z = useQuery({ ...zipListQuery(p.url), enabled: p.size !== 0 });
  if (z.isError) return <ReadFault what="This file" reads={[z]} />;
  if (z.isPending && p.size !== 0) return null;
  if (!z.data) return <Empty title={p.name} icon="archive" text={p.size ? bytesWord(p.size) : undefined} />;
  const { count: n, entries, total } = z.data;
  if (n === null) return <Empty title={p.name} icon="archive" text={bytesWord(total)} />;
  return (
    <div className="vw-scroll fcard-full">
      <ul className="fcard-names">
        {entries.map((e, i) => (
          <li key={i} className={isFolder(e) ? 'is-folder' : undefined}>
            <span>{e.name}</span>
            {!isFolder(e) && <em>{bytesWord(e.size)}</em>}
          </li>
        ))}
      </ul>
      {n > entries.length && <Cut shown={`First ${count(entries.length)} of ${count(n)}`} url={p.url} name={p.name} />}
    </div>
  );
}
