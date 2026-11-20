import { Fragment, useState, type ReactNode } from 'react';
import { Lexer, Tokenizer, type MarkedToken, type Token, type Tokens } from 'marked';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { toast } from '@/kit/Toast';
import { Viewer } from '@/kit/Viewer';
import './Markdown.css';

/* The one Markdown: a chat message (compact) and a doc or a skill (the reading page's `.read` parts). marked's lexer
   reads the text (GFM) and this file draws its tokens as React elements, never as HTML, so raw HTML in the text shows
   as text. Links keep to safe addresses; anything else shows as its words. Images only from the app's own /api/: a
   picture from anywhere else would call that server the moment the text is drawn (a way to leak what a message
   holds), so it shows as a link to press. Never cut: a message shows in full. */

const SAFE_URL = /^(https?:|mailto:|\/|#)/i;
const SAFE_SRC = /^\/api\//i;
const OUTSIDE = /^(https?:)?\/\//i;
const away = { target: '_blank', rel: 'noreferrer' };

/** No HTML: a tag is not read as one, so it stays in the text and shows as written. */
class NoHtml extends Tokenizer {
  override html() {
    return undefined;
  }
  override tag() {
    return undefined;
  }
}
const DOC = { gfm: true, tokenizer: new NoHtml() };
const CHAT = { ...DOC, breaks: true };
const lex = (text: string, compact = false) => Lexer.lex(text, compact ? CHAT : DOC) as MarkedToken[];

// ---------------------------------------------------------------- headings

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'part';

/** Each first- or second-level heading of the text's top level, with an id kept unique. */
function ids(tokens: MarkedToken[]) {
  const used = new Set<string>();
  const out = new Map<Tokens.Heading, string>();
  for (const t of tokens) {
    if (t.type !== 'heading' || t.depth > 2) continue;
    let id = slug(t.text);
    for (let i = 2; used.has(id); i++) id = `${slug(t.text)}-${i}`;
    used.add(id);
    out.set(t, id);
  }
  return out;
}

export interface Heading {
  id: string;
  text: string;
}

/** The first- and second-level headings, for an outline beside the text; ids match what `Markdown` gives them. */
export function headings(text: string): Heading[] {
  return [...ids(lex(text))].map(([t, id]) => ({ id, text: t.text.trim() }));
}

// ---------------------------------------------------------------- drawing

/** Draws one safe link its own way (a chat's chips): its address, its words drawn, and whether the words are only the
    address written bare. Only addresses that pass SAFE_URL reach it. */
export type LinkRender = (href: string, words: ReactNode, bare: boolean) => ReactNode;

interface Ctx {
  compact: boolean;
  ids: Map<Tokens.Heading, string>;
  link?: LinkRender;
}

/** A bare address (`https://…`, `<https://…>`) is written as its own words. */
const bareLink = (t: Tokens.Link) => t.raw === t.href || t.raw === `<${t.href}>` || t.text === t.href;

function inline(tokens: Token[] | undefined, k: string, c: Ctx): ReactNode[] {
  return (tokens as MarkedToken[] | undefined ?? []).map((t, i) => {
    const key = `${k}.${i}`;
    const kids = () => inline('tokens' in t ? t.tokens : undefined, key, c);
    switch (t.type) {
      case 'strong':
        return <b key={key}>{kids()}</b>;
      case 'em':
        return <em key={key}>{kids()}</em>;
      case 'del':
        return <s key={key}>{kids()}</s>;
      case 'codespan':
        return <code key={key}>{t.text}</code>;
      case 'br':
        return <br key={key} />;
      case 'link':
        if (SAFE_URL.test(t.href) && c.link) return <Fragment key={key}>{c.link(t.href, kids(), bareLink(t))}</Fragment>;
        return SAFE_URL.test(t.href) ? (
          <Link key={key} className="lnk" href={t.href} {...(OUTSIDE.test(t.href) && away)}>
            {kids()}
          </Link>
        ) : (
          <Fragment key={key}>{kids()}</Fragment>
        );
      case 'image':
        if (SAFE_SRC.test(t.href)) return <img key={key} className="md-img" src={t.href} alt={t.text} loading="lazy" />;
        if (OUTSIDE.test(t.href))
          return (
            <Link key={key} className="lnk" href={t.href} {...away}>
              {t.text || 'Picture'}
            </Link>
          );
        return t.text;
      case 'text':
        if (t.tokens) return <Fragment key={key}>{kids()}</Fragment>;
        return c.compact ? t.text : t.text.replace(/\s*\n\s*/g, ' ');
      default:
        return 'text' in t ? t.text : t.raw;
    }
  });
}

function blocks(tokens: Token[], k: string, c: Ctx, tail?: ReactNode): ReactNode[] {
  const read = c.compact ? undefined : 't-read';
  return (tokens as MarkedToken[]).map((t, i) => {
    const key = `${k}.${i}`;
    switch (t.type) {
      case 'paragraph':
        return (
          <p key={key} className={read}>
            {inline(t.tokens, key, c)}
            {i === tokens.length - 1 && tail}
          </p>
        );
      case 'text':
        return <Fragment key={key}>{inline(t.tokens ?? [t], key, c)}</Fragment>;
      case 'heading': {
        const kids = inline(t.tokens, key, c);
        if (c.compact)
          return (
            <p key={key}>
              <b>{kids}</b>
            </p>
          );
        return t.depth <= 2 ? (
          <h2 key={key} id={c.ids.get(t)} className="t-head">
            {kids}
          </h2>
        ) : (
          <h3 key={key} className="t-label">
            {kids}
          </h3>
        );
      }
      case 'code':
        if (c.compact) return <ChatCode key={key} text={t.text} />;
        return (
          <pre key={key} className="read-code">
            <code>{t.text}</code>
          </pre>
        );
      case 'hr':
        return <hr key={key} className="md-rule" />;
      case 'blockquote':
        return <blockquote key={key}>{blocks(t.tokens, key, c)}</blockquote>;
      case 'list': {
        const L = t.ordered ? 'ol' : 'ul';
        return (
          <L key={key} start={t.ordered && t.start !== 1 ? Number(t.start) : undefined}>
            {t.items.map((it, n) => (
              <li key={n} className={read}>
                {blocks(it.tokens, `${key}.${n}`, c)}
              </li>
            ))}
          </L>
        );
      }
      case 'checkbox':
        return t.raw;
      case 'table': {
        const table = (
            <table className="table">
              <thead>
                <tr>
                  {t.header.map((h, n) => (
                    <th key={n}>{inline(h.tokens, `${key}.h${n}`, c)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r, n) => (
                  <tr key={n}>
                    {r.map((d, j) => (
                      <td key={j}>{inline(d.tokens, `${key}.${n}.${j}`, c)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
        );
        return c.compact ? (
          <ChatTable key={key} table={table} />
        ) : (
          <div key={key} className="md-table">
            {table}
          </div>
        );
      }
      default:
        return null;
    }
  });
}

/** Code in a chat: Copy at its corner, under the pointer (always there on a phone). */
function ChatCode({ text }: { text: string }) {
  const copy = () =>
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject(new Error('no clipboard'))).then(
      () => toast({ text: 'Copied' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  return (
    <div className="md-block">
      <pre>
        <code>{text}</code>
      </pre>
      <span className="md-tools">
        <IconButton icon="copy" label="Copy" size="sm" quiet onClick={() => void copy()} />
      </span>
    </div>
  );
}

/** A table in a chat scrolls sideways in the message; Expand shows it full screen, every column at its width. */
function ChatTable({ table }: { table: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="md-block">
      <div className="md-table">{table}</div>
      <span className="md-tools">
        <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setOpen(true)} />
      </span>
      {open && (
        <Viewer open onClose={() => setOpen(false)} title="Table">
          <div className="md-table is-full">{table}</div>
        </Viewer>
      )}
    </div>
  );
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
  /** Draws each safe link its own way (a chat's chips); a plain link when left out. */
  link?: LinkRender;
}

export function Markdown({ text, compact = false, tail, link }: MarkdownProps) {
  const tokens = lex(text, compact).filter((t) => t.type !== 'space' && t.type !== 'def');
  const tailOwn = tail != null && tokens[tokens.length - 1]?.type !== 'paragraph';
  return (
    <>
      {blocks(tokens, 'b', { compact, ids: ids(tokens), link }, tail)}
      {tailOwn && <p>{tail}</p>}
    </>
  );
}
