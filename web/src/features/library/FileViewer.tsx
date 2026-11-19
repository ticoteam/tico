import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Markdown } from '@/kit/Markdown';
import { PictureViewer } from '@/kit/PictureViewer';
import { ChatWith, press } from './parts';
import { fileQuery, fileTextQuery, fileUrl, fileVersionsQuery } from './queries';
import { viewing } from './viewing';
import './library.css';

/* A file wherever it opens (the old app's viewer.ts and file-review.ts): Files, a task's Details, a link in a chat.
   A picture opens full size; anything else opens over the page with Download, its maker's chat and the way to its
   page in Files, where its versions are. */

const TEXT = /^(text\/|application\/(json|xml|csv|x-yaml|yaml|javascript))/;
const MARKDOWN = /^text\/(x-)?markdown/;

export interface PreviewProps {
  id: string;
  version: number;
  mime: string;
  kind: string;
  title: string;
  url: string;
  /** The file's own name, when it says more than the mime (`notes.md`). */
  name?: string | null;
}

/** One file's face: a picture, a video or a sound, a page or a PDF in a frame, Markdown as reading text, other text
    as it is. Anything else offers Download. */
export function Preview({ id, version, mime, kind, title, url, name }: PreviewProps) {
  if (mime.startsWith('image/') || kind === 'image')
    return (
      <div className="fv-desk">
        <img className="fv-img" src={url} alt={title} />
      </div>
    );
  if (mime.startsWith('video/'))
    return (
      <div className="fv-desk">
        <video className="fv-img" src={url} controls aria-label={title} />
      </div>
    );
  if (mime.startsWith('audio/'))
    return (
      <div className="fv-desk">
        <audio src={url} controls aria-label={title} />
      </div>
    );
  if (mime === 'application/pdf') return <iframe className="fv-frame" src={url} title={title} />;
  if (mime === 'text/html' || kind === 'page') return <iframe className="fv-frame" src={url} title={title} sandbox="" />;
  if (MARKDOWN.test(mime) || /\.(md|markdown)$/i.test(name ?? '')) return <TextPreview id={id} version={version} as="markdown" />;
  if (TEXT.test(mime)) return <TextPreview id={id} version={version} as={mime.includes('csv') ? 'csv' : 'text'} />;
  return (
    <div className="fv-none">
      <Empty title="No preview" icon="file">
        <a className="btn" href={url} download>
          <Icon name="download" />
          Download
        </a>
      </Empty>
    </div>
  );
}

function TextPreview({ id, version, as }: { id: string; version: number; as: 'text' | 'csv' | 'markdown' }) {
  const t = useQuery(fileTextQuery(id, version));
  if (t.isError) return <Empty title="This file did not load" icon="alert" />;
  if (t.data === undefined) return null;
  if (as === 'markdown')
    return (
      <div className="fv-main">
        <div className="read">
          <Markdown text={t.data} />
        </div>
      </div>
    );
  if (as === 'csv') {
    const lines = t.data.split(/\r?\n/).filter(Boolean).slice(0, 500);
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
      <pre className="fv-text t-code">{t.data}</pre>
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
    <div className="lib fv-embed">
      <Preview
        id={file}
        version={n ?? 1}
        mime={shown?.mime ?? f.data.mime ?? ''}
        kind={f.data.library_kind}
        title={f.data.title}
        url={fileUrl(file, n)}
        name={shown?.name ?? f.data.name}
      />
    </div>
  );
}

const chatOf = (bot: string | undefined) => (bot ? (bot.includes(':') ? bot : `bot:${bot}`) : null);

/** A file over the page. A picture uses the full-size viewer; anything else a wide dialog. */
export function FileViewer({ file, version, onClose }: { file: string; version?: number; onClose: () => void }) {
  const f = useQuery(fileQuery(file));
  const versions = useQuery(fileVersionsQuery(file));
  const shown = (version && versions.data?.find((v) => v.version === version)) || versions.data?.find((v) => v.current);
  const n = shown?.version ?? f.data?.version;
  const mime = shown?.mime ?? f.data?.mime ?? '';
  const url = fileUrl(file, n);
  if (f.isError)
    return (
      <Dialog open onOpenChange={(o) => !o && onClose()} title="File" actions={<Button onClick={onClose}>Close</Button>}>
        <Empty title="This file did not load" icon="alert" />
      </Dialog>
    );
  const d = f.data;
  if (!d) return null;
  if (mime.startsWith('image/') || d.library_kind === 'image')
    return <PictureViewer pictures={[{ src: url, name: d.title }]} at={0} onAtChange={(at) => at == null && onClose()} />;
  const page = () => {
    onClose();
    void press('files.open', { file, version: shown && !shown.current ? shown.version : undefined }, 'Not opened');
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={d.title}
      className="lib fv-dialog"
      actions={
        <>
          <ChatWith actor={chatOf(d.bot)} />
          <span className="grow" />
          <a className="btn" href={url} download>
            <Icon name="download" />
            Download
          </a>
          <Button variant="primary" onClick={page}>
            Open
          </Button>
        </>
      }
    >
      <div className="fv-view">
        <Preview id={file} version={n ?? 1} mime={mime} kind={d.library_kind} title={d.title} url={url} name={shown?.name ?? d.name} />
      </div>
    </Dialog>
  );
}

/** Mounted once at the app's root: shows the file `files.view` opened. */
export function FileViewerHost() {
  const v = useSyncExternalStore(viewing.subscribe, viewing.get, viewing.get);
  return v ? <FileViewer key={`${v.file}:${v.version ?? ''}`} file={v.file} version={v.version} onClose={() => viewing.set(null)} /> : null;
}
