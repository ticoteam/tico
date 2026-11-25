import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Markdown } from '@/kit/Markdown';
import { PictureViewer } from '@/kit/PictureViewer';
import { Viewer } from '@/kit/Viewer';
import { csvRows, fileKind, safeFrame, type FileKind } from '@/lib';
import { ReadFault } from '@/pagekinds/_parts/ReadFault';
import { size } from './parts';
import { PdfView } from './PdfView';
import { fileQuery, fileTextQuery, fileUrl, fileVersionsQuery, heldFileQuery } from './queries';
import { viewing, type Viewing } from './viewing';
import './library.css';

/* A file wherever it opens (the old app's viewer.ts and file-review.ts): Files, a task's tab or Details, an email's
   attachment, a link in a chat. Whatever it is, it shows here or says what it is and offers it: a picture, a video or
   a sound with the browser's own controls, a PDF as one scroll of its pages (PdfView), a web page in a sandbox,
   Markdown as reading text, other text as it is. Office files and the rest show their name and size, with Download. */

type View = 'image' | 'video' | 'audio' | 'pdf' | 'html' | 'markdown' | 'csv' | 'text' | 'office' | 'none';

/** How each kind shows here. A kind with no face of its own yet shows as text when it is words, else as its name with
    Download: nothing falls through to a frame of its own address. */
const VIEW: Record<FileKind, View> = {
  image: 'image',
  video: 'video',
  audio: 'audio',
  pdf: 'pdf',
  html: 'html',
  markdown: 'markdown',
  csv: 'csv',
  tsv: 'text',
  text: 'text',
  diff: 'text',
  ics: 'text',
  vcf: 'text',
  eml: 'none',
  zip: 'none',
  docx: 'office',
  xlsx: 'office',
  office: 'office',
  none: 'none',
};

const viewOf = (mime: string, kind: string | undefined, name: string): View => VIEW[fileKind(mime, name, kind)];

const AS_TEXT = new Set<View>(['html', 'markdown', 'csv', 'text']);

/** What a chat's own file address serves inline, so these load from it as they show (a video plays as it arrives)
    rather than being read whole first. */
const STREAMS = new Set<View>(['image', 'video', 'audio', 'pdf']);

/** The type a file is held as when its address says nothing (an attachment): its own, else what its name says. */
const HELD_TYPE: Partial<Record<View, string>> = { pdf: 'application/pdf', html: 'text/plain', markdown: 'text/plain', csv: 'text/plain', text: 'text/plain' };

/** An HTML file is shown, never run: no scripts, its own origin, links open a new tab (viewer.ts htmlPreviewFrame), and
    it loads nothing from anywhere (lib/safeFrame). */
const HTML_SANDBOX = 'allow-popups allow-popups-to-escape-sandbox';

export interface PreviewProps {
  /** The Tico file and version its words are read from (a text kind); `held` reads them from `url` instead. */
  id?: string;
  version?: number;
  mime: string;
  kind?: string;
  title: string;
  url: string;
  /** The file's own name, when it says more than the mime (`notes.md`). */
  name?: string | null;
  /** Bytes, said beside the name where there is no preview. */
  size?: number | null;
  /** A linked file's own address (a Google doc): shown by Google, or opened where it lives. */
  link?: string;
  /** `url` sends the bytes only as a download (an email's frozen attachment): they are read here and shown. */
  held?: boolean;
  /** With `held`: `url` serves a picture, video, sound or PDF inline (a chat's own file), so it is shown from there. */
  streams?: boolean;
  /** A task's PDF at its current version, shown to a person: it offers Sign (PdfSign.tsx). */
  signable?: boolean;
  /** A video's still, shown until it plays. */
  poster?: string | null;
}
/** A file's video still at its current version (its `poster_blob_id`), when it has one; none for an older version. */
export const posterOf = (f: { poster_blob_id?: string | null } | undefined, id: string, n: number, older = false): string | undefined =>
  !older && f?.poster_blob_id ? `/api/v2/files/${encodeURIComponent(id)}/poster?v=${n}` : undefined;

/** Where a linked file lives (file-review.ts frvLink): only a web address, so a link never runs anything here. */
export function linkOf(f: { locator?: string; open?: Record<string, unknown> | null } | undefined): string {
  const url = f?.locator === 'remote_link' && f.open?.type === 'external' ? f.open.url : undefined;
  return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : '';
}

/** Google's own preview of a Google doc or Drive file (file-review-route.ts googlePreviewUrl); '' for anything else. */
export function googlePreview(href: string): string {
  let u: URL;
  try {
    u = new URL(href);
  } catch {
    return '';
  }
  if (u.protocol !== 'https:') return '';
  if (u.hostname === 'docs.google.com') {
    const m = /^\/(document|spreadsheets|presentation|drawings)\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/.exec(u.pathname);
    if (m) return `https://docs.google.com/${m[1]}/d/${m[2]}/preview`;
  }
  if (u.hostname === 'drive.google.com') {
    const m = /^\/file\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/.exec(u.pathname);
    if (m) return `https://drive.google.com/file/d/${m[1]}/preview`;
  }
  return '';
}

/** Download, or for a linked file the way to where it lives (it has nothing here to download). */
export function FileGet({ url, link }: { url: string; link: string }) {
  const label = link ? (/^https:\/\/(docs|drive)\.google\.com\//.test(link) ? 'Open in Google' : 'Open where it lives') : 'Download';
  return (
    <a className="ib" data-phone href={link || url} {...(link ? { target: '_blank', rel: 'noreferrer' } : { download: true })} aria-label={label} title={label}>
      <Icon name={link ? 'popout' : 'download'} />
    </a>
  );
}

/** One file's face, by its type, else its name. */
export function Preview({ id = '', version = 0, mime, kind, title, url, name, size: bytes, link, held = false, streams = false, signable = false, poster }: PreviewProps) {
  const view = viewOf(mime, kind, name ?? title);
  const asText = AS_TEXT.has(view);
  const shows = view !== 'office' && view !== 'none';
  const reads = held && !(streams && STREAMS.has(view));
  const words = useQuery({ ...fileTextQuery(id, version), enabled: !link && !held && asText });
  const kept = useQuery({ ...heldFileQuery(url, HELD_TYPE[view] ?? mime, asText), enabled: !link && reads && shows });
  if (link) {
    const g = googlePreview(link);
    // Google draws its own file, signed in as the viewer; Tico passes it nothing but the address.
    if (g) return <iframe className="fv-frame" src={g} title={title} referrerPolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms" />;
    return (
      <div className="fv-none">
        <Empty title="Linked file" icon="link">
          <a className="btn" href={link} target="_blank" rel="noreferrer">
            <Icon name="popout" />
            Open
          </a>
        </Empty>
      </div>
    );
  }
  if (!shows) return <Unshown name={name || title} bytes={bytes} url={url} office={view === 'office' && !held} />;
  const read = reads ? kept : words;
  if (read.isError) return <ReadFault what="This file" reads={[read]} />;
  const src = reads ? kept.data?.src : url;
  if (asText) {
    const text = held ? kept.data?.text : words.data;
    return text === undefined ? null : <TextPreview text={text} view={view} title={title} url={url} />;
  }
  if (!src) return null;
  if (view === 'image')
    return (
      <div className="fv-desk">
        <img className="fv-img" src={src} alt={title} />
      </div>
    );
  if (view === 'video')
    return (
      <div className="fv-desk">
        <video className="fv-img" src={src} poster={poster ?? undefined} controls aria-label={title} />
      </div>
    );
  if (view === 'audio')
    return (
      <div className="fv-desk">
        <audio src={src} controls aria-label={title} />
      </div>
    );
  if (view === 'pdf') {
    const sign = signable && id && version && !held ? { file: id, version, name: name || title } : undefined;
    return <PdfView src={src} title={title} url={url} name={name} sign={sign} />;
  }
  return <Unshown name={name || title} bytes={bytes} url={url} />;
}

function Unshown({ name, bytes, url, office = false }: { name: string; bytes?: number | null; url: string; office?: boolean }) {
  return (
    <div className="fv-none">
      <Empty title={name} icon="file" text={size(bytes) || undefined}>
        <a className="btn" href={url} download={name || true}>
          <Icon name="download" />
          Download
        </a>
        {/* An Office file opens where the browser or the computer can show it; nothing here draws one. */}
        {office && (
          <a className="btn" href={url} target="_blank" rel="noreferrer">
            <Icon name="popout" />
            Open
          </a>
        )}
      </Empty>
    </div>
  );
}

function TextPreview({ text, view, title, url }: { text: string; view: View; title: string; url: string }) {
  if (view === 'html') {
    const page = safeFrame(text);
    return page === null ? <Unshown name={title} url={url} /> : <iframe className="fv-frame" srcDoc={page} title={title} sandbox={HTML_SANDBOX} referrerPolicy="no-referrer" />;
  }
  // Text reads as a sheet of paper on the desk, as an image does (files.js textView).
  if (view === 'markdown')
    return (
      <div className="fv-desk is-paper">
        <article className="fv-paper read">
          <Markdown text={text} />
        </article>
      </div>
    );
  if (view === 'csv') {
    const cells = csvRows(text);
    return (
      <div className="fv-main">
        <table className="table">
          <thead>
            <tr>
              {cells[0]?.map((c, i) => (
                <th key={i}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cells.slice(1).map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div className="fv-desk is-paper">
      <article className="fv-paper">
        <pre className="fv-text t-code">{text}</pre>
      </article>
    </div>
  );
}

/** A file in a frame of its own, for a page that is not the library's (a task's tab, an email's attachment). */
export function FileFrame(p: PreviewProps) {
  return (
    <div className="lib fv-embed">
      <Preview {...p} />
    </div>
  );
}

/** A file by its id (and version, the current one when left out), read and drawn: for a task's Details or anywhere
    else that has only the id. */
export function FilePreview({ file, version }: { file: string; version?: number }) {
  const f = useQuery(fileQuery(file));
  const versions = useQuery(fileVersionsQuery(file));
  const shown = versions.data?.find((v) => (version === undefined ? v.current : v.version === version));
  if (f.isError) return <ReadFault what="This file" reads={[f]} />;
  if (!f.data) return null;
  const older = version !== undefined && version !== f.data.version;
  if (older && !shown)
    return versions.isPending ? null : <Empty title="This file did not load" icon="alert" />;
  const n = version ?? shown?.version ?? f.data.version;
  return (
    <FileFrame
      id={file}
      version={n ?? 1}
      mime={shown?.mime ?? (older ? '' : f.data.mime ?? '')}
      kind={older ? undefined : f.data.library_kind}
      title={f.data.title}
      url={fileUrl(file, n)}
      name={shown?.name ?? (older ? '' : f.data.name)}
      size={shown?.size ?? (older ? undefined : f.data.size)}
      link={linkOf(f.data)}
      poster={posterOf(f.data, file, n ?? 1, older)}
    />
  );
}

/** What a file is, in a word beside its name: "PDF", "Page", its ending, else nothing. */
function kindWord(mime: string, kind: string | undefined, name: string): string {
  const view = viewOf(mime, kind, name);
  if (view === 'html') return 'Page';
  if (view === 'pdf') return 'PDF';
  const end = /\.([a-z0-9]{1,5})$/i.exec(name)?.[1];
  return (end ?? mime.split('/')[1]?.split(/[;+]/)[0] ?? '').toUpperCase();
}

/** A file in the full-screen layer, whatever it is: a message's own by its address, else the library's by its id. */
function FullFile({ v }: { v: Viewing & { full: NonNullable<Viewing['full']> } }) {
  const { full } = v;
  const close = () => viewing.set(null);
  const url = full.url ?? fileUrl(v.file, v.version);
  const named = full.name || v.name;
  return (
    <Viewer
      open
      onClose={close}
      title={v.name}
      meta={kindWord(full.mime, full.kind, named) || undefined}
      actions={
        <>
          {!full.url && (
            <Link className="ib" href={`/library/files?file=${encodeURIComponent(v.file)}`} aria-label="Open in Files" title="Open in Files" onClick={close}>
              <Icon name="popout" />
            </Link>
          )}
          <a className="ib" href={url} download={named} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </>
      }
    >
      {full.url ? (
        <FileFrame id={v.file} mime={full.mime} title={v.name} url={full.url} name={named} size={full.size} held streams />
      ) : (
        <FilePreview file={v.file} version={v.version} />
      )}
    </Viewer>
  );
}

const pictureOf = (v: Viewing) => !v.full || viewOf(v.full.mime, v.full.kind, v.full.name || v.name) === 'image';

/** Mounted once at the app's root: the picture `files.view` opened, full size over the page (any other file opens as
    its page in Files), or any file `files.preview` opened, in the full-screen layer. */
export function FileViewerHost() {
  const v = useSyncExternalStore(viewing.subscribe, viewing.get, viewing.get);
  if (!v) return null;
  if (v.full && !pictureOf(v)) return <FullFile v={{ ...v, full: v.full }} />;
  const src = v.full?.url ?? fileUrl(v.file, v.version);
  return <PictureViewer pictures={[{ src, name: v.name }]} at={0} onAtChange={(at) => at == null && viewing.set(null)} />;
}
