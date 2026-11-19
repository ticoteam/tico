import type { ReactNode } from 'react';
import { Link } from '@/kit/Link';

/* The little of Markdown a chat message uses: paragraphs, lists, code blocks, **bold**, `code` and [links](url).
   Built as React elements, never as HTML, and never cut: a message shows in full. */

const INLINE = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
const SAFE_URL = /^(https?:|mailto:|\/|#)/i;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index;
    if (i > at) out.push(text.slice(at, i));
    if (m[1] != null) out.push(<b key={i}>{m[1]}</b>);
    else if (m[2] != null) out.push(<code key={i}>{m[2]}</code>);
    else if (m[3] != null && m[4] != null && SAFE_URL.test(m[4])) {
      const external = /^https?:/i.test(m[4]);
      out.push(
        <Link key={i} className="lnk" href={m[4]} {...(external && { target: '_blank', rel: 'noreferrer' })}>
          {m[3]}
        </Link>,
      );
    } else out.push(m[0]);
    at = i + m[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

const ITEM = /^\s*(?:[-*]|\d+[.)])\s+/;

type Block = { t: 'p'; text: string } | { t: 'ul'; items: string[] } | { t: 'pre'; code: string };

export function blocks(text: string): Block[] {
  const out: Block[] = [];
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  let para: string[] = [];
  let list: string[] = [];
  const flush = () => {
    if (para.length) out.push({ t: 'p', text: para.join('\n') });
    if (list.length) out.push({ t: 'ul', items: list });
    para = [];
    list = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;
    if (line.trimStart().startsWith('```')) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !(lines[i] as string).trimStart().startsWith('```'); i++) code.push(lines[i] as string);
      out.push({ t: 'pre', code: code.join('\n') });
    } else if (ITEM.test(line)) {
      if (para.length) flush();
      list.push(line.replace(ITEM, ''));
    } else if (!line.trim()) flush();
    else {
      if (list.length) flush();
      para.push(line);
    }
  }
  flush();
  return out;
}

/** A message's text as paragraphs, lists and code. `tail` ends the last paragraph (the typing dots). */
export function Markdown({ text, tail }: { text: string; tail?: ReactNode }) {
  const bs = blocks(text);
  const tailOwn = tail != null && bs[bs.length - 1]?.t !== 'p';
  return (
    <>
      {bs.map((b, i) => {
        if (b.t === 'pre') return <pre key={i}><code>{b.code}</code></pre>;
        if (b.t === 'ul') return <ul key={i}>{b.items.map((l, j) => <li key={j}>{inline(l)}</li>)}</ul>;
        return <p key={i}>{b.text.split('\n').map((l, j) => (j ? [<br key={j} />, ...inline(l)] : inline(l)))}{i === bs.length - 1 && tail}</p>;
      })}
      {tailOwn && <p>{tail}</p>}
    </>
  );
}
