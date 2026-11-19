import type { ReactNode } from 'react';
import { Link } from '@/kit/Link';
import './Markdown.css';

/* The one Markdown: a chat message (compact) and a doc or a skill (the reading page's `.read` parts). Built as React
   elements, never as HTML, so raw HTML in the text shows as text. Links keep to safe addresses; anything else shows
   as its words. Images only from the app's own /api/: a picture from anywhere else would call that server the moment
   the text is drawn (a way to leak what a message holds), so it shows as a link to press. Never cut: a message shows in full. */

const SAFE_URL = /^(https?:|mailto:|\/|#)/i;
const SAFE_SRC = /^\/api\//i;
const OUTSIDE = /^https?:\/\//i;
const INLINE =
  /!\[([^\]]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)|\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)|`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|(?<![\p{L}\p{N}])_([^_\s][^_]*)_(?![\p{L}\p{N}])/gu;

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index;
    const k = `${key}.${i}`;
    if (i > at) out.push(text.slice(at, i));
    if (m[2] != null) {
      if (SAFE_SRC.test(m[2])) out.push(<img key={k} className="md-img" src={m[2]} alt={m[1] ?? ''} loading="lazy" />);
      else if (OUTSIDE.test(m[2]))
        out.push(
          <Link key={k} className="lnk" href={m[2]} target="_blank" rel="noreferrer">
            {m[1] || 'Picture'}
          </Link>,
        );
      else if (m[1]) out.push(m[1]);
    } else if (m[3] != null && m[4] != null) {
      if (SAFE_URL.test(m[4])) {
        const external = /^https?:/i.test(m[4]);
        out.push(
          <Link key={k} className="lnk" href={m[4]} {...(external && { target: '_blank', rel: 'noreferrer' })}>
            {inline(m[3], k)}
          </Link>,
        );
      } else out.push(...inline(m[3], k));
    } else if (m[5] != null) out.push(<code key={k}>{m[5]}</code>);
    else if (m[6] != null || m[7] != null) out.push(<b key={k}>{inline(m[6] ?? m[7] ?? '', k)}</b>);
    else out.push(<em key={k}>{inline(m[8] ?? m[9] ?? '', k)}</em>);
    at = i + m[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

// ---------------------------------------------------------------- blocks

interface Item {
  text: string;
  sub?: List;
}
interface List {
  ordered: boolean;
  items: Item[];
}
export type Block =
  | { t: 'p'; lines: string[] }
  | { t: 'h'; level: number; text: string }
  | { t: 'list'; list: List }
  | { t: 'quote'; paras: string[] }
  | { t: 'pre'; code: string }
  | { t: 'table'; head: string[]; rows: string[][] }
  | { t: 'hr' };

const FENCE = /^\s*```/;
const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());

/** The text as blocks: headings, paragraphs, lists (one level inside another), quotes, code, tables and rules. */
export function blocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const at = (n: number) => lines[n] ?? '';
  const out: Block[] = [];
  const starts = (n: number) => {
    const l = at(n);
    return FENCE.test(l) || HEADING.test(l) || RULE.test(l) || ITEM.test(l) || l.startsWith('>') || (l.includes('|') && TABLE_SEP.test(at(n + 1)));
  };
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (!line.trim()) {
      i++;
    } else if (FENCE.test(line)) {
      const code: string[] = [];
      for (i++; i < lines.length && !FENCE.test(at(i)); i++) code.push(at(i));
      i++;
      out.push({ t: 'pre', code: code.join('\n') });
    } else if (HEADING.test(line)) {
      const h = HEADING.exec(line);
      out.push({ t: 'h', level: h?.[1]?.length ?? 2, text: h?.[2] ?? '' });
      i++;
    } else if (RULE.test(line)) {
      out.push({ t: 'hr' });
      i++;
    } else if (line.includes('|') && TABLE_SEP.test(at(i + 1))) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && at(i).includes('|') && at(i).trim(); i++) rows.push(cells(at(i)));
      out.push({ t: 'table', head, rows });
    } else if (line.startsWith('>')) {
      const paras: string[] = [];
      let para: string[] = [];
      for (; i < lines.length && at(i).startsWith('>'); i++) {
        const l = at(i).replace(/^>\s?/, '');
        if (l.trim()) para.push(l.trim());
        else if (para.length) {
          paras.push(para.join(' '));
          para = [];
        }
      }
      if (para.length) paras.push(para.join(' '));
      out.push({ t: 'quote', paras });
    } else if (ITEM.test(line)) {
      const top: List = { ordered: /\d/.test(ITEM.exec(line)?.[2] ?? ''), items: [] };
      for (; i < lines.length; i++) {
        const l = at(i);
        const m = ITEM.exec(l);
        if (m) {
          const indent = (m[1] ?? '').length;
          const last = top.items[top.items.length - 1];
          if (indent >= 2 && last) {
            last.sub ??= { ordered: /\d/.test(m[2] ?? ''), items: [] };
            last.sub.items.push({ text: m[3] ?? '' });
          } else top.items.push({ text: m[3] ?? '' });
        } else if (l.trim() && /^\s+/.test(l) && top.items.length) {
          // A wrapped item: its next line, indented, joins it.
          const last = top.items[top.items.length - 1] as Item;
          const target = last.sub?.items[last.sub.items.length - 1] ?? last;
          target.text += ` ${l.trim()}`;
        } else break;
      }
      out.push({ t: 'list', list: top });
    } else {
      const para: string[] = [];
      for (; i < lines.length && at(i).trim() && (!para.length || !starts(i)); i++) para.push(at(i).trim());
      out.push({ t: 'p', lines: para });
    }
  }
  return out;
}

// ---------------------------------------------------------------- headings

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'part';

function unique(id: string, used: Set<string>) {
  let n = id;
  for (let i = 2; used.has(n); i++) n = `${id}-${i}`;
  used.add(n);
  return n;
}

export interface Heading {
  id: string;
  text: string;
}

/** The first- and second-level headings, for an outline beside the text; ids match what `Markdown` gives them. */
export function headings(text: string): Heading[] {
  const used = new Set<string>();
  return blocks(text)
    .filter((b): b is Extract<Block, { t: 'h' }> => b.t === 'h' && b.level <= 2)
    .map((b) => ({ id: unique(slug(b.text), used), text: b.text.trim() }));
}

// ---------------------------------------------------------------- drawing

function ListEl({ list, k, read }: { list: List; k: string; read: boolean }) {
  const lis = list.items.map((it, n) => (
    <li key={n} className={read ? 't-read' : undefined}>
      {inline(it.text, `${k}.${n}`)}
      {it.sub && <ListEl list={it.sub} k={`${k}.${n}s`} read={read} />}
    </li>
  ));
  return list.ordered ? <ol>{lis}</ol> : <ul>{lis}</ul>;
}

export interface MarkdownProps {
  text: string;
  /**
   * A chat message: each line break kept, headings as strong lines, no reading-page classes. Otherwise a doc: its
   * headings are the page's h2 and h3 (the page head holds the h1) with ids for the outline.
   */
  compact?: boolean;
  /** Ends the last paragraph (a chat's typing dots). */
  tail?: ReactNode;
}

export function Markdown({ text, compact = false, tail }: MarkdownProps) {
  const bs = blocks(text);
  const read = !compact;
  const used = new Set<string>();
  const tailOwn = tail != null && bs[bs.length - 1]?.t !== 'p';
  return (
    <>
      {bs.map((b, i) => {
        const k = `b${i}`;
        const last = i === bs.length - 1;
        switch (b.t) {
          case 'pre':
            return (
              <pre key={k} className={read ? 'read-code' : undefined}>
                <code>{b.code}</code>
              </pre>
            );
          case 'h':
            if (compact)
              return (
                <p key={k}>
                  <b>{inline(b.text, k)}</b>
                </p>
              );
            return b.level <= 2 ? (
              <h2 key={k} id={unique(slug(b.text), used)} className="t-head">
                {inline(b.text, k)}
              </h2>
            ) : (
              <h3 key={k} className="t-label">
                {inline(b.text, k)}
              </h3>
            );
          case 'hr':
            return <hr key={k} className="md-rule" />;
          case 'list':
            return <ListEl key={k} list={b.list} k={k} read={read} />;
          case 'quote':
            return (
              <blockquote key={k}>
                {b.paras.map((p, n) => (
                  <p key={n} className={read ? 't-read' : undefined}>
                    {inline(p, `${k}.${n}`)}
                  </p>
                ))}
              </blockquote>
            );
          case 'table':
            return (
              <div key={k} className="md-table">
                <table className="table">
                  <thead>
                    <tr>
                      {b.head.map((c, n) => (
                        <th key={n}>{inline(c, `${k}.h${n}`)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, n) => (
                      <tr key={n}>
                        {b.head.map((_, c) => (
                          <td key={c}>{inline(r[c] ?? '', `${k}.${n}.${c}`)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case 'p':
            return (
              <p key={k} className={read ? 't-read' : undefined}>
                {compact ? b.lines.map((l, j) => (j ? [<br key={`br${j}`} />, ...inline(l, `${k}.${j}`)] : inline(l, `${k}.${j}`))) : inline(b.lines.join(' '), k)}
                {last && tail}
              </p>
            );
        }
      })}
      {tailOwn && <p>{tail}</p>}
    </>
  );
}
