import { useQuery } from '@tanstack/react-query';
import { createContext, use, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { run } from '@/actions';
import { useActor } from '@/data';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import type { LinkRender } from '@/kit/Markdown';
import { Tooltip } from '@/kit/Tooltip';
import { besideThread, fileKind } from '@/lib';
import { isRawWords, pointerOf, withFileRefs, type Pointer } from './links';
import { fileFactsQuery, pageTextQuery } from './queries';
import type { Attachment, ChatMessage } from './types';

/* What a message holds besides its words, drawn so it reads at a glance (thread-content.html): a link is a chip with
   what it points at, a page shows small and live, pictures show as pictures, a video plays where it is, a sound is a
   player bar, a PDF shows its first page. A click goes where it points: a page of
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
  if (p.what === 'teammate') return who?.name ?? titleCase(decodeURIComponent(path[1] ?? 'Person'));
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
const fileIcon = (mime: string | null | undefined, kind?: string, name?: string | null): IconName => (fileKind(mime, name, kind) === 'html' ? 'tobar' : 'file');
/** A page drawn small in the chat: only what its type or the library says is one, never its name alone. */
const isPage = (mime: string | null | undefined, kind?: string) => fileKind(mime, undefined, kind) === 'html';

/** Opens one of the app's files full screen, by its id (the library's) or as a message's own file. */
const preview = (file: string, version?: number, held?: { url: string; mime: string; name: string; filename?: string; size?: number }) =>
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
      <Icon name={fileIcon(facts?.mime, facts?.library_kind, facts?.name)} />
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
  if (fileKind(facts.mime, facts.name, facts.library_kind) === 'image') return <Pictures list={[{ src, name: title }]} onOpen={() => preview(p.file, p.version)} />;
  return null;
}

interface Shown {
  /** The picture itself: what the viewer opens. */
  src: string;
  name: string;
  /** Its small copy (the file's /thumb), drawn in its place; the picture itself when there is none yet. */
  thumb?: string;
}

/** One to four pictures as pictures: one at its own shape, more as tiles, past four a count on the last. */
function Pictures({ list, onOpen }: { list: Shown[]; onOpen: (i: number) => void }) {
  // a thumbnail that does not load (not made yet, or never) gives way to the picture itself; a picture that does not
  // load (gone, or its bytes not kept) is its name as a chip, never a broken image
  const [lost, setLost] = useState<ReadonlySet<string>>(new Set());
  if (!list.length) return null;
  const shown = list.filter((p) => !lost.has(p.src));
  const more = shown.length - 4;
  return (
    <>
      {shown.length > 0 && (
        <div className={`pics is-${Math.min(shown.length, 4)}`}>
          {shown.slice(0, 4).map((p, i) => {
            const face = p.thumb && !lost.has(p.thumb) ? p.thumb : p.src;
            return (
              <button key={p.src} type="button" className="pic" onClick={() => onOpen(list.indexOf(p))} aria-label={p.name}>
                <img src={face} alt={p.name} loading="lazy" onError={() => setLost((was) => new Set(was).add(face))} />
                {i === 3 && more > 0 && <span className="pic-more">+{more}</span>}
              </button>
            );
          })}
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

const OWN_URL = /^\/api\/v2\/files\/([A-Za-z0-9_-]+)(\?v=\d+)?$/;
const OWN_ID = /^[A-Za-z0-9_-]+$/;
/** A message's file as one of the app's: its id and address. */
export interface OwnFile {
  id: string;
  href: `/api/${string}`;
}
/** The one place a message's file is addressed from: the plain address it came with (`/api/v2/files/<id>[?v=N]`, the
    id read from it), else its plain id; null for anything else (`..`, another route, no id), which is only its name
    and loads nothing. Every source, link and open below comes from this. */
export function ownFile(f: Attachment): OwnFile | null {
  const m = typeof f.url === 'string' ? OWN_URL.exec(f.url) : null;
  if (m?.[1]) return { id: m[1], href: m[0] as `/api/${string}` };
  if (typeof f.id === 'string' && OWN_ID.test(f.id)) return { id: f.id, href: `/api/v2/files/${f.id}` };
  return null;
}
const attachmentLabel = (f: Attachment) => f.title?.trim() || f.name || 'File';
const attachmentType = (f: Attachment) => {
  const end = /\.([a-z0-9]{1,5})$/i.exec(f.name ?? '')?.[1];
  return (end ?? f.content_type?.split('/')[1] ?? '').toUpperCase();
};
export const isPicture = (f: Attachment) => fileKind(f.content_type, f.name) === 'image';
/** A file's small copy on the server's own route (`/api/v2/files/<id>/thumb` for a picture or a PDF's first page,
    `/poster` for a video's frame, `?v=` kept), which checks its reader as the file does and answers 404 until the
    copy is made, or when it never is. */
const previewOf = (href: string, kind: 'thumb' | 'poster') => {
  const m = /^(\/api\/v2\/files\/[A-Za-z0-9_-]+)(\?v=\d+)?$/.exec(href);
  return m ? `${m[1]}/${kind}${m[2] ?? ''}` : undefined;
};
// the file's own name goes too: a caption says nothing of what it is, and `lease.pdf` sent as octet-stream is a PDF by its name
const heldOf = (f: Attachment, own: OwnFile) => ({
  url: own.href,
  mime: f.content_type ?? '',
  name: attachmentLabel(f),
  ...(f.name && { filename: f.name }),
  ...(typeof f.size === 'number' && { size: f.size }),
});

/** How a message's own file shows under its words, from its type alone: nothing about it is read to choose. */
type Face = 'picture' | 'video' | 'audio' | 'pdf' | 'page' | 'file';
function faceOf(f: Attachment): Face {
  const kind = fileKind(f.content_type, f.name);
  if (kind === 'image') return 'picture';
  if (kind === 'video' || kind === 'audio' || kind === 'pdf') return kind;
  return isPage(f.content_type) ? 'page' : 'file';
}

/** A file that will not play here, offered to save instead. */
function DownloadChip({ f, own }: { f: Attachment; own: OwnFile }) {
  const name = attachmentLabel(f);
  return (
    <a className="file" href={own.href} download={f.name || name}>
      <Icon name="download" />
      <span>{name}</span>
      {attachmentType(f) && <em>{attachmentType(f)}</em>}
    </a>
  );
}

/**
 * A video: its frame with a play mark (the poster, else a plain card), playing in place on a tap. The player is made
 * only then, and reads the video from its address as it plays; Expand pauses it and opens it full screen.
 */
function VideoCard({ f, own }: { f: Attachment; own: OwnFile }) {
  const url = own.href;
  const name = attachmentLabel(f);
  const poster = previewOf(url, 'poster');
  const [face, setFace] = useState<'poster' | 'card' | 'playing' | 'failed'>(poster ? 'poster' : 'card');
  const player = useRef<HTMLVideoElement>(null);
  if (face === 'failed') return <DownloadChip f={f} own={own} />;
  const expand = () => {
    player.current?.pause();
    preview(own.id, undefined, heldOf(f, own));
  };
  return (
    <div className="pcard is-video">
      <span className="pcard-view">
        {face === 'playing' ? (
          <video ref={player} src={url} controls autoPlay playsInline aria-label={name} onError={() => setFace('failed')} />
        ) : (
          <button type="button" className="pcard-play" aria-label={name} onClick={() => setFace('playing')}>
            {face === 'poster' && <img src={poster} alt="" loading="lazy" onError={() => setFace('card')} />}
            <span className="pcard-mark">
              <Icon name="play" />
            </span>
          </button>
        )}
      </span>
      <span className="pcard-foot">
        <Icon name="video" />
        <span>{name}</span>
        <IconButton icon="expand" label="Expand" size="sm" quiet onClick={expand} />
      </span>
    </div>
  );
}

/** A sound: the browser's own compact player under its name, reading the file from its address as it plays. */
function AudioBar({ f, own }: { f: Attachment; own: OwnFile }) {
  const [failed, setFailed] = useState(false);
  const name = attachmentLabel(f);
  if (failed) return <DownloadChip f={f} own={own} />;
  return (
    <div className="maudio">
      <span className="maudio-name">{name}</span>
      <audio src={own.href} controls preload="metadata" aria-label={name} onError={() => setFailed(true)} />
    </div>
  );
}

/** A PDF: its first page small (the file's /thumb, else a plain card) over its name; the card opens it full screen. */
function PdfCard({ f, own }: { f: Attachment; own: OwnFile }) {
  const url = own.href;
  const name = attachmentLabel(f);
  const thumb = previewOf(url, 'thumb');
  const [lost, setLost] = useState(!thumb);
  return (
    <div className="pcard is-pdf">
      <span className="pcard-view" aria-hidden="true">
        {lost ? (
          <span className="pcard-none">
            <Icon name="file" />
          </span>
        ) : (
          <img src={thumb} alt="" loading="lazy" onError={() => setLost(true)} />
        )}
      </span>
      <span className="pcard-foot">
        <Icon name="file" />
        <span>{name}</span>
        <em>PDF</em>
      </span>
      {/* a link, so a new-tab click still opens the file itself; a plain click opens it full screen */}
      <a
        className="pcard-hit"
        href={url}
        target="_blank"
        rel="noreferrer"
        aria-label={name}
        onClick={(e) => {
          if (!plainClick(e)) return;
          e.preventDefault();
          preview(own.id, undefined, heldOf(f, own));
        }}
      />
    </div>
  );
}

/** A message's own file that is not a picture: a video, a sound, a PDF or a page by its face, anything else its chip. */
function AttachedFile({ f, own }: { f: Attachment; own: OwnFile }) {
  const url = own.href;
  const name = attachmentLabel(f);
  const held = heldOf(f, own);
  const face = faceOf(f);
  if (face === 'video') return <VideoCard f={f} own={own} />;
  if (face === 'audio') return <AudioBar f={f} own={own} />;
  if (face === 'pdf') return <PdfCard f={f} own={own} />;
  if (face === 'page') return <PageCard src={url} title={name} onOpen={() => preview(own.id, undefined, held)} />;
  const open = (e: MouseEvent) => {
    if (!plainClick(e)) return;
    e.preventDefault();
    preview(own.id, undefined, held);
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
  const all = m.refs?.attachments ?? [];
  const named = (f: Attachment, i: number) => (
    <span key={i} className="file">
      <Icon name="file" />
      <span>{attachmentLabel(f)}</span>
      {attachmentType(f) && <em>{attachmentType(f)}</em>}
    </span>
  );
  if (m.pending) return all.map(named);
  const files = all.flatMap((f) => {
    const own = ownFile(f);
    return own ? [{ f, own }] : [];
  });
  const unknown = all.filter((f) => !ownFile(f));
  const pictures = files.filter(({ f }) => isPicture(f)).map(({ f, own }): Shown => {
    const src = own.href;
    // only a photo (JPEG) uses the thumbnail: thumbnails are JPEGs, so a PNG would lose its transparency and a GIF its motion
    const thumb = /^image\/jpe?g$/i.test(f.content_type ?? '') ? previewOf(src, 'thumb') : undefined;
    return { src, name: attachmentLabel(f), ...(thumb && { thumb }) };
  });
  const others = files.filter(({ f }) => !isPicture(f));
  const refs = m.deleted ? [] : filesIn(m.body ?? '').slice(0, PAGE_REFS);
  if (!pictures.length && !others.length && !unknown.length && !refs.length) return null;
  return (
    <div className="mshelf">
      <Pictures list={pictures} onOpen={(i) => onPicture(pictures[i]!.src)} />
      {others.map(({ f, own }, i) => (
        <AttachedFile key={`${i}:${own.id}`} f={f} own={own} />
      ))}
      {unknown.map(named)}
      {refs.map((p) => (
        <RefCard key={p.file} p={p} />
      ))}
    </div>
  );
}
