import { useQuery } from '@tanstack/react-query';
import { createContext, use, useState, type MouseEvent, type ReactNode } from 'react';
import { run } from '@/actions';
import { useActor } from '@/data';
import { Icon, type IconName } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import type { LinkRender } from '@/kit/Markdown';
import { Tooltip } from '@/kit/Tooltip';
import { besideThread } from '@/lib';
import { isRawWords, pointerOf, withFileRefs, type Pointer } from './links';
import { fileFactsQuery, pageTextQuery } from './queries';
import type { Attachment, ChatMessage } from './types';

/* What a message holds besides its words, drawn so it reads at a glance (thread-content.html): a link is a chip with
   what it points at, a page shows small and live, pictures show as pictures. A click goes where it points: a page of
   the app opens beside the thread, a place on the web opens there in a new tab, a file opens full screen over
   everything (`files.preview`). Nothing here loads from outside the app while it is drawn. */

/** The thread these messages are in, as the shell's `?chat=` (`thread:<id>`, `agent:<slug>`): what an app link opens
    comes beside it (plan/layers.html). Left out in Tico's own sheet, which is always there. */
export const ChatPlace = createContext<string | undefined>(undefined);

/** A plain left click, which the app takes; any other (a new tab, a copy) is the browser's. */
const plainClick = (e: MouseEvent) => e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;

/** An address shortened for a tip: its host and path, the middle cut when long. */
function shortHref(href: string): string {
  const s = href.replace(/^https?:\/\//, '').replace(/\/$/, '');
  return s.length > 72 ? `${s.slice(0, 44)}…${s.slice(-24)}` : s;
}

const titleCase = (s: string) => s.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

function WebChip({ p }: { p: Extract<Pointer, { kind: 'web' }> }) {
  return (
    <Tooltip label={shortHref(p.href)}>
      <a className="lchip" href={p.href} target="_blank" rel="noreferrer" data-site={p.site}>
        <Icon name={p.icon} />
        {p.label}
      </a>
    </Tooltip>
  );
}

/** The words an app link shows when it was written bare: the teammate's name, else what kind of page it is. */
function AppWords({ p }: { p: Extract<Pointer, { kind: 'app' }> }) {
  const path = new URL(p.to, 'http://app').pathname.split('/').filter(Boolean);
  const who = useActor(p.what === 'teammate' ? decodeURIComponent(path[1] ?? '') : undefined);
  if (p.what === 'teammate') return who?.name ?? titleCase(decodeURIComponent(path[1] ?? 'Teammate'));
  if (p.what === 'task') return 'Task';
  if (p.what === 'thread') return 'Thread';
  if (p.what === 'doc') return 'Doc';
  return titleCase(path[path.length - 1] ?? 'Home');
}

function AppChip({ p, words }: { p: Extract<Pointer, { kind: 'app' }>; words: ReactNode | null }) {
  const chat = use(ChatPlace);
  return (
    <Link className="lchip is-app" href={besideThread(p.to, chat)}>
      <Icon name={p.icon} />
      {words ?? <AppWords p={p} />}
    </Link>
  );
}

const fileHrefOf = (file: string, version?: number): `/api/${string}` => `/api/v2/files/${encodeURIComponent(file)}${version ? `/versions/${version}` : ''}`;
const isPage = (mime: string | null | undefined, kind?: string) => /^text\/html/.test(mime ?? '') || kind === 'page';
const fileIcon = (mime: string | null | undefined, kind?: string): IconName => (isPage(mime, kind) ? 'tobar' : 'file');

/** Opens one of the app's files full screen, by its id (the library's) or as a message's own file. */
const preview = (file: string, version?: number, held?: { url: string; mime: string; name: string; size?: number }) =>
  void run('files.preview', held ? { file, held } : version ? { file, version } : { file }, { via: 'click' });

function FileChip({ p, words }: { p: Extract<Pointer, { kind: 'file' }>; words: ReactNode | null }) {
  const facts = useQuery(fileFactsQuery(p.file)).data;
  const href = fileHrefOf(p.file, p.version);
  const open = (e: MouseEvent) => {
    if (!plainClick(e)) return;
    e.preventDefault();
    preview(p.file, p.version);
  };
  return (
    <a className="lchip" href={href} target="_blank" rel="noreferrer" onClick={open}>
      <Icon name={fileIcon(facts?.mime, facts?.library_kind)} />
      {words ?? (facts?.title || facts?.name || 'File')}
    </a>
  );
}

/** The one link drawer for a message's text (kit Markdown's `link`): a chip for what the app knows, else a link. */
export const linkRender: LinkRender = (href, words, bare) => <Pointed href={href} words={words} bare={bare} />;

function Pointed({ href, words, bare }: { href: string; words: ReactNode; bare: boolean }) {
  const p = pointerOf(href, location.origin);
  const raw = bare || (typeof words === 'string' && isRawWords(words, href)) || (Array.isArray(words) && words.length === 1 && typeof words[0] === 'string' && isRawWords(words[0], href));
  if (p?.kind === 'file') return <FileChip p={p} words={raw ? null : words} />;
  if (p?.kind === 'app') return <AppChip p={p} words={raw ? null : words} />;
  if (p?.kind === 'web' && raw) return <WebChip p={p} />;
  // words someone chose read as words: an underlined link, its address on hover
  const away = !href.startsWith('/') || href.startsWith('//');
  return (
    <Link className="lnk" href={href} {...(away && { target: '_blank', rel: 'noreferrer', title: shortHref(href) })}>
      {words}
    </Link>
  );
}

/** A message's text with the file refs agents write (`page:file-…@2`) made links its chips draw. */
export const richText = withFileRefs;

/* ---------------------------------------------------------------- under the words */

const PAGE_REFS = 3;

/** The app's files a message's text points at (its links and refs), each once, in the order they read. */
function filesIn(text: string): Extract<Pointer, { kind: 'file' }>[] {
  const out = new Map<string, Extract<Pointer, { kind: 'file' }>>();
  const hrefs = [...withFileRefs(text).matchAll(/\]\(([^)\s]+)\)|(https?:\/\/[^\s)\]>]+)/g)].map((m) => m[1] ?? m[2] ?? '');
  for (const h of hrefs) {
    const p = pointerOf(h, location.origin);
    if (p?.kind === 'file' && !out.has(p.file)) out.set(p.file, p);
  }
  return [...out.values()];
}

/**
 * A page, small and live: its own drawing scaled down to the card, in a sandbox (no scripts, no app origin, nothing it
 * can press). The card opens it full screen.
 */
function PageCard({ src, title, onOpen }: { src: `/api/${string}`; title: string; onOpen: () => void }) {
  const read = useQuery(pageTextQuery(src));
  return (
    <div className="pcard">
      <span className="pcard-view" aria-hidden="true">
        {read.data !== undefined ? (
          <iframe srcDoc={read.data} sandbox="" title={title} tabIndex={-1} loading="lazy" referrerPolicy="no-referrer" />
        ) : (
          <span className={read.isError ? 'pcard-none' : 'pcard-none is-loading'}>
            <Icon name="tobar" />
          </span>
        )}
      </span>
      <span className="pcard-foot">
        <Icon name="tobar" />
        <span>{title}</span>
        <Icon name="expand" className="end" />
      </span>
      <button type="button" className="pcard-hit" aria-label={title} onClick={onOpen} />
    </div>
  );
}

/** A page or a picture the text points at, as a card under it; any other file is its chip in the text already. */
function RefCard({ p }: { p: Extract<Pointer, { kind: 'file' }> }) {
  const facts = useQuery(fileFactsQuery(p.file)).data;
  if (!facts) return null;
  const title = facts.title || facts.name || 'Page';
  const src = fileHrefOf(p.file, p.version);
  if (isPage(facts.mime, facts.library_kind)) return <PageCard src={src} title={title} onOpen={() => preview(p.file, p.version)} />;
  if (/^image\//.test(facts.mime ?? '')) return <Pictures list={[{ src, name: title }]} onOpen={() => preview(p.file, p.version)} />;
  return null;
}

interface Shown {
  src: string;
  name: string;
}

/** One to four pictures as pictures: one at its own shape, more as tiles, past four a count on the last. */
function Pictures({ list, onOpen }: { list: Shown[]; onOpen: (i: number) => void }) {
  // a picture that does not load (gone, or its bytes not kept) is its name as a chip, never a broken image
  const [lost, setLost] = useState<ReadonlySet<string>>(new Set());
  if (!list.length) return null;
  const shown = list.filter((p) => !lost.has(p.src));
  const more = shown.length - 4;
  return (
    <>
      {shown.length > 0 && (
        <div className={`pics is-${Math.min(shown.length, 4)}`}>
          {shown.slice(0, 4).map((p, i) => (
            <button key={p.src} type="button" className="pic" onClick={() => onOpen(list.indexOf(p))} aria-label={p.name}>
              <img src={p.src} alt={p.name} loading="lazy" onError={() => setLost((was) => new Set(was).add(p.src))} />
              {i === 3 && more > 0 && <span className="pic-more">+{more}</span>}
            </button>
          ))}
        </div>
      )}
      {list
        .filter((p) => lost.has(p.src))
        .map((p) => (
          <a key={p.src} className="file" href={p.src} target="_blank" rel="noreferrer">
            <Icon name="file" />
            <span>{p.name}</span>
          </a>
        ))}
    </>
  );
}

const attachmentHref = (f: Attachment): `/api/${string}` =>
  typeof f.url === 'string' && /^\/api\/v2\/files\//.test(f.url) ? (f.url as `/api/${string}`) : `/api/v2/files/${encodeURIComponent(f.id)}`;
const attachmentLabel = (f: Attachment) => f.title?.trim() || f.name || 'File';
const attachmentType = (f: Attachment) => {
  const end = /\.([a-z0-9]{1,5})$/i.exec(f.name ?? '')?.[1];
  return (end ?? f.content_type?.split('/')[1] ?? '').toUpperCase();
};
export const isPicture = (f: Attachment) => /^image\//.test(f.content_type ?? '');

/** A message's own file that is not a picture: a page small and live, anything else its chip. */
function AttachedFile({ f }: { f: Attachment }) {
  const url = attachmentHref(f);
  const name = attachmentLabel(f);
  const held = { url, mime: f.content_type ?? '', name, ...(typeof f.size === 'number' && { size: f.size }) };
  if (isPage(f.content_type)) return <PageCard src={url} title={name} onOpen={() => preview(f.id, undefined, held)} />;
  const open = (e: MouseEvent) => {
    if (!plainClick(e)) return;
    e.preventDefault();
    preview(f.id, undefined, held);
  };
  return (
    <a className="file" href={url} target="_blank" rel="noreferrer" onClick={open}>
      <Icon name="file" />
      <span>{name}</span>
      {attachmentType(f) && <em>{attachmentType(f)}</em>}
    </a>
  );
}

/**
 * Under a message's words: its own pictures, its own other files, then the pages and pictures its text points at.
 * `onPicture` opens one of the message's pictures by its address (the viewer walks them all).
 */
export function Shelf({ m, onPicture }: { m: ChatMessage; onPicture: (src: string) => void }) {
  const files = m.refs?.attachments ?? [];
  if (m.pending)
    return files.map((f) => (
      <span key={f.id} className="file">
        <Icon name="file" />
        <span>{attachmentLabel(f)}</span>
        {attachmentType(f) && <em>{attachmentType(f)}</em>}
      </span>
    ));
  const pictures = files.filter(isPicture).map((f) => ({ src: attachmentHref(f), name: attachmentLabel(f) }));
  const others = files.filter((f) => !isPicture(f));
  const refs = m.deleted ? [] : filesIn(m.body ?? '').slice(0, PAGE_REFS);
  if (!pictures.length && !others.length && !refs.length) return null;
  return (
    <div className="mshelf">
      <Pictures list={pictures} onOpen={(i) => onPicture(pictures[i]!.src)} />
      {others.map((f) => (
        <AttachedFile key={f.id} f={f} />
      ))}
      {refs.map((p) => (
        <RefCard key={p.file} p={p} />
      ))}
    </div>
  );
}
