import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Markdown } from '@/kit/Markdown';
import { PictureViewer } from '@/kit/PictureViewer';
import { size } from './parts';
import { fileQuery, fileTextQuery, fileUrl, fileVersionsQuery, heldFileQuery } from './queries';
import { viewing } from './viewing';
import './library.css';

/* A file wherever it opens (the old app's viewer.ts and file-review.ts): Files, a task's tab or Details, an email's
   attachment, a link in a chat. Whatever it is, it shows here or says what it is and offers it: a picture, a video or
   a sound with the browser's own controls, a PDF in the browser's own viewer, a web page in a sandbox, Markdown as
   reading text, other text as it is. Office files and the rest show their name and size, with Download. */

const TEXT = /^(text\/|application\/(json|xml|csv|x-yaml|yaml|javascript))/;
const MARKDOWN = /^text\/(x-)?markdown/;
const OFFICE = /^application\/(msword|rtf|vnd\.(openxmlformats-officedocument|ms-excel|ms-powerpoint|ms-word|oasis\.opendocument))/;

type View = 'image' | 'video' | 'audio' | 'pdf' | 'html' | 'markdown' | 'csv' | 'text' | 'office' | 'none';

/** By its name, when the type says nothing more (`application/octet-stream`, or `text/plain` for `notes.md`). */
const BY_NAME: [RegExp, View][] = [
  [/\.(png|jpe?g|gif|webp|avif|svg)$/i, 'image'],
  [/\.(mp4|webm|mov|m4v)$/i, 'video'],
  [/\.(mp3|m4a|wav|ogg|aac)$/i, 'audio'],
  [/\.pdf$/i, 'pdf'],
  [/\.html?$/i, 'html'],
  [/\.(md|markdown)$/i, 'markdown'],
  [/\.csv$/i, 'csv'],
  [/\.(txt|log|json|ya?ml|xml)$/i, 'text'],
  [/\.(docx?|xlsx?|pptx?|od[tsp]|rtf)$/i, 'office'],
];

function viewOf(mime: string, kind: string | undefined, name: string): View {
  const m = mime.toLowerCase().split(';')[0]?.trim() ?? '';
  if (m.startsWith('image/') || kind === 'image') return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  if (m === 'application/pdf') return 'pdf';
  if (m === 'text/html' || kind === 'page') return 'html';
  if (MARKDOWN.test(m)) return 'markdown';
  if (m.includes('csv')) return 'csv';
  if (OFFICE.test(m)) return 'office';
  const named = BY_NAME.find(([re]) => re.test(name))?.[1];
  return named ?? (TEXT.test(m) ? 'text' : 'none');
}

const AS_TEXT = new Set<View>(['html', 'markdown', 'csv', 'text']);

/** The type a file is held as when its address says nothing (an attachment): its own, else what its name says. */
const HELD_TYPE: Partial<Record<View, string>> = { pdf: 'application/pdf', html: 'text/plain', markdown: 'text/plain', csv: 'text/plain', text: 'text/plain' };

/** An HTML file is shown, never run: no scripts, its own origin, links open a new tab (viewer.ts htmlPreviewFrame). */
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
}
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
    <a className="ib" href={link || url} {...(link ? { target: '_blank', rel: 'noreferrer' } : { download: true })} aria-label={label} title={label}>
      <Icon name={link ? 'popout' : 'download'} />
    </a>
  );
}

/** One file's face, by its type, else its name. */
export function Preview({ id = '', version = 0, mime, kind, title, url, name, size: bytes, link, held = false }: PreviewProps) {
  const view = viewOf(mime, kind, name ?? title);
  const asText = AS_TEXT.has(view);
  const shows = view !== 'office' && view !== 'none';
  const words = useQuery({ ...fileTextQuery(id, version), enabled: !link && !held && asText });
  const kept = useQuery({ ...heldFileQuery(url, HELD_TYPE[view] ?? mime, asText), enabled: !link && held && shows });
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
  if (!shows)
    return (
      <div className="fv-none">
        <Empty title={name || title} icon="file" text={size(bytes) || undefined}>
          <a className="btn" href={url} download={name || true}>
            <Icon name="download" />
            Download
          </a>
          {/* An Office file opens where the browser or the computer can show it; nothing here draws one. */}
          {view === 'office' && !held && (
            <a className="btn" href={url} target="_blank" rel="noreferrer">
              <Icon name="popout" />
              Open
            </a>
          )}
        </Empty>
      </div>
    );
  const read = held ? kept : words;
  if (read.isError) return <Empty title="This file did not load" icon="alert" />;
  const src = held ? kept.data?.src : url;
  if (asText) {
    const text = held ? kept.data?.text : words.data;
    return text === undefined ? null : <TextPreview text={text} view={view} title={title} />;
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
        <video className="fv-img" src={src} controls aria-label={title} />
      </div>
    );
  if (view === 'audio')
    return (
      <div className="fv-desk">
        <audio src={src} controls aria-label={title} />
      </div>
    );
  return <iframe className="fv-frame" src={src} title={title} />;
}

function TextPreview({ text, view, title }: { text: string; view: View; title: string }) {
  if (view === 'html') return <iframe className="fv-frame" srcDoc={text} title={title} sandbox={HTML_SANDBOX} referrerPolicy="no-referrer" />;
  if (view === 'markdown')
    return (
      <div className="fv-main">
        <div className="read">
          <Markdown text={text} />
        </div>
      </div>
    );
  if (view === 'csv') {
    const lines = text.split(/\r?\n/).filter(Boolean).slice(0, 500);
    const cells = lines.map((l) => l.split(','));
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
    <div className="fv-main">
      <pre className="fv-text t-code">{text}</pre>
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
  const shown = (version && versions.data?.find((v) => v.version === version)) || versions.data?.find((v) => v.current);
  const n = shown?.version ?? f.data?.version;
  if (f.isError) return <Empty title="This file did not load" icon="alert" />;
  if (!f.data) return null;
  return (
    <FileFrame
      id={file}
      version={n ?? 1}
      mime={shown?.mime ?? f.data.mime ?? ''}
      kind={f.data.library_kind}
      title={f.data.title}
      url={fileUrl(file, n)}
      name={shown?.name ?? f.data.name}
      size={shown?.size ?? f.data.size}
      link={linkOf(f.data)}
    />
  );
}

/** Mounted once at the app's root: the picture `files.view` opened, full size over the page. Any other file opens as
    its page in Files. */
export function FileViewerHost() {
  const v = useSyncExternalStore(viewing.subscribe, viewing.get, viewing.get);
  return v ? <PictureViewer pictures={[{ src: fileUrl(v.file, v.version), name: v.name }]} at={0} onAtChange={(at) => at == null && viewing.set(null)} /> : null;
}
