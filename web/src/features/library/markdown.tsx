import type { ReactNode } from 'react';

/* A doc's or a skill's markdown, drawn in the reading page's own parts (files.css `.read`). Only what docs use:
   headings, paragraphs, lists, quotes, code blocks, and bold, code and links inside a line. Raw HTML is shown as text.
   `@/conversation` keeps its renderer to itself, so the Library has this small one. */

export interface Heading {
  id: string;
  text: string;
}

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'part';

/** The h2s, for the outline beside a doc. */
export function headings(text: string): Heading[] {
  const out: Heading[] = [];
  const used = new Set<string>();
  let fenced = false;
  for (const line of text.split('\n')) {
    if (line.startsWith('```')) fenced = !fenced;
    const m = !fenced && /^(#{1,2})\s+(.+)$/.exec(line);
    if (m && m[2]) out.push({ id: unique(slug(m[2]), used), text: m[2].trim() });
  }
  return out;
}

function unique(id: string, used: Set<string>) {
  let n = id;
  for (let i = 2; used.has(n); i++) n = `${id}-${i}`;
  used.add(n);
  return n;
}

const safe = (href: string) => /^(https?:|mailto:|\/|#)/i.test(href);

/** Bold, code and links inside one line. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let i = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}.${i++}`;
    if (m[1]) out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else if (m[2]) out.push(<b key={k}>{t.slice(2, -2)}</b>);
    else {
      const [, words = t, href = ''] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t) ?? [];
      out.push(safe(href) ? <a key={k} href={href} target={href.startsWith('/') || href.startsWith('#') ? undefined : '_blank'} rel="noreferrer">{words}</a> : words);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The text as blocks. A first-level heading reads as the page's h2: the page head already holds the h1. */
export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const at = (n: number) => lines[n] ?? '';
  const blocks: ReactNode[] = [];
  const used = new Set<string>();
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = at(i);
    const key = `b${k++}`;
    if (line.startsWith('```')) {
      const code: string[] = [];
      for (i++; i < lines.length && !at(i).startsWith('```'); i++) code.push(at(i));
      i++;
      blocks.push(
        <pre key={key} className="read-code">
          <code>{code.join('\n')}</code>
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,6})\s+(.+)$/.exec(line);
    if (h) {
      const words = (h[2] ?? '').trim();
      blocks.push(
        (h[1] ?? '').length <= 2 ? (
          <h2 key={key} id={unique(slug(words), used)} className="t-head">
            {inline(words, key)}
          </h2>
        ) : (
          <h3 key={key} className="t-label">
            {inline(words, key)}
          </h3>
        ),
      );
      i++;
      continue;
    }
    const bullet = /^\s*([-*+]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      for (; i < lines.length && bullet.test(at(i)); i++) items.push(at(i).replace(bullet, ''));
      const lis = items.map((t, n) => (
        <li key={n} className="t-read">
          {inline(t, `${key}.${n}`)}
        </li>
      ));
      blocks.push(ordered ? <ol key={key}>{lis}</ol> : <ul key={key}>{lis}</ul>);
      continue;
    }
    if (line.startsWith('>')) {
      const quote: string[] = [];
      for (; i < lines.length && at(i).startsWith('>'); i++) quote.push(at(i).replace(/^>\s?/, ''));
      blocks.push(
        <blockquote key={key}>
          <p className="t-read">{inline(quote.join(' '), key)}</p>
        </blockquote>,
      );
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const para: string[] = [];
    for (; i < lines.length && at(i).trim() && !/^(#{1,6}\s|```|>)/.test(at(i)) && !bullet.test(at(i)); i++) para.push(at(i).trim());
    blocks.push(
      <p key={key} className="t-read">
        {inline(para.join(' '), key)}
      </p>,
    );
  }
  return <>{blocks}</>;
}
