import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { run } from '@/actions';
import type { TaskDocument, TaskFileView } from '@/api/schema';
import { meQuery } from '@/data';
import { FileFrame, FilePreview, signedLine } from '@/features/library/exports';
import { Ago } from '@/kit/Ago';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { PictureViewer, type Picture } from '@/kit/PictureViewer';
import { Skeleton, useDelayed } from '@/kit/Skeleton';
import { toast } from '@/kit/Toast';
import type { TaskSearch } from './exports';
import { download } from './Files';
import { DETAILS, failedToast, isPage, tabAddress } from './model';
import { Face, Go } from './parts';
import { frameDoc, parsePage } from './changes';
import { pageShownQuery, pageVersionQuery, taskFilesQuery, type TaskData } from './queries';
import { isWord, WordPages } from './Word';

/* What a task shows in its page sheet: one of its pages (a tab), or one of its files opened from Details. Both keep
   their own look in a frame (page-types: a file is a Document with its content in a frame). */

/** The sandbox a page drawn by an agent runs in: its own scripts, never this app's cookies (ui/app/visual-pictures.ts). */
export const PAGE_SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';

type FileVersion = TaskFileView['versions'][number];

/** Only a person signs (backend/file_signings.py); the server also checks they may write on the task. */
function useIsPerson(): boolean {
  const role = useQuery(meQuery).data?.role;
  return role === 'person' || role === 'owner';
}

const currentOf = (f: TaskFileView): FileVersion | undefined => f.versions.find((x) => x.n === f.current_version) ?? f.versions.at(-1);

/** A tab's content: a page from its page read (its own address, its live parts), or, when the server answers that it
    is no page, `file` (the same file from its file read, in the file viewer). */
function PageOrFile({ task, id, title, page, file }: { task: string; id: string; title: string; page: boolean; file: ReactNode }) {
  const shown = useQuery({ ...pageShownQuery(task, id), enabled: page });
  // No address (an install without page hosting: `src` null): the page's own text at its version, framed as an older
  // version is, so the tab never waits on an address that will not come.
  const p = shown.data?.page;
  const text = useQuery({ ...pageVersionQuery(id, p?.version ?? 0), enabled: !!p && !p.src && !!p.version });
  if (!page || shown.isError) return <div className="frame fv">{file}</div>;
  if (text.isError) return <Empty icon="alert" title={`${title} did not load`} />;
  const html = text.data != null ? frameDoc(parsePage(text.data)) : null;
  return (
    <div className="frame is-inset">
      {p?.src ? (
        <iframe src={p.src} title={title} sandbox={PAGE_SANDBOX} referrerPolicy="no-referrer" />
      ) : html ? (
        <iframe srcDoc={html} title={title} sandbox={PAGE_SANDBOX} referrerPolicy="no-referrer" />
      ) : (
        <PageBones />
      )}
    </div>
  );
}

/** A document's tab: a page, or a Markdown file. */
export function DocFrame({ task, doc }: { task: string; doc: TaskDocument }) {
  return <PageOrFile task={task} id={doc.file_id} title={doc.title || doc.name} page={isPage(doc)} file={<FilePreview file={doc.file_id} version={doc.version} />} />;
}

/** One of the task's other files as a tab, at its current version or the one an ask pinned (`version`): an HTML file
    may be a page linked to the task. */
export function FileTab({ task, f, version }: { task: string; f: TaskFileView; version?: number }) {
  const isPerson = useIsPerson();
  const now = currentOf(f);
  const v = (version !== undefined && f.versions.find((x) => x.n === version)) || now;
  if (!v) return null;
  const name = f.title || f.name;
  // A Word file is drawn as its words on paper, with Download beside it.
  if (isWord(f.name, v.mime))
    return (
      <>
        <div className="fv-bar">
          <b>{name}</b>
          <span className="grow" />
          <a className="ib ib-sm" href={v.url} download={f.name} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </div>
        <WordPages url={v.url} name={name} />
      </>
    );
  // At its current version a person may sign it here as in its file view; an older version pinned by an ask is read only.
  const file = <FileFrame id={f.id} version={v.n} mime={v.mime} title={name} url={v.url} name={v.name || f.name} size={v.size} signable={isPerson && v === now} />;
  return <PageOrFile task={task} id={f.id} title={name} page={v.mime.startsWith('text/html')} file={file} />;
}

const BONES = [38, 92, 86, 64, 90, 72, 48];

/** A page's bones while it is read, after 150 ms: a heading and a few lines in the frame. */
export function PageBones() {
  const shown = useDelayed(150);
  return (
    <div className="tv-bones" role="status" aria-busy="true" aria-label="Loading the page">
      {shown && BONES.map((w, i) => <Skeleton key={i} width={w} className={i ? undefined : 'sk-title'} />)}
    </div>
  );
}


/** One version of a file as the frame draws it: a picture opens full size; anything else is the file viewer's. A PDF at
    its current version offers Sign when `signable`. */
function Body({ f, v, onPicture, signable = false }: { f: TaskFileView; v: FileVersion; onPicture?: () => void; signable?: boolean }) {
  const name = f.title || f.name;
  if (v.mime.startsWith('image/'))
    return onPicture ? (
      <button type="button" className="fv-pic" aria-label={`${name}, full size`} title="Full size" onClick={onPicture}>
        <img src={v.url} alt={name} />
      </button>
    ) : (
      <img src={v.url} alt={`${name}, v${v.n}`} />
    );
  // Each version keeps the name it was stored under: an older draft opens and saves as it was named then.
  const stored = v.name || f.name;
  if (isWord(stored, v.mime)) return <WordPages url={v.url} name={`${name}, v${v.n}`} />;
  return <FileFrame id={f.id} version={v.n} mime={v.mime} title={`${name}, v${v.n}`} url={v.url} name={stored} size={v.size} signable={signable} />;
}

/** A file open in the page sheet (more.js fileBar): back to Details, its name, which of the task's files it is, and its
    ⋯ (Download, its versions, Compare, Remove). The address keeps the version (`v`) and the one compared with (`vs`). */
export function FileView({ t, file, full, flow }: { t: TaskData; file: string; full?: string; flow: boolean }) {
  const files = useQuery(taskFilesQuery(t.id));
  const me = useQuery(meQuery);
  const search = useSearch({ strict: false }) as TaskSearch;
  const navigate = useNavigate();
  // An archived file opens from the Archive menu too; it is not one of the task's files to step through.
  const list = (files.data ?? []).filter((f) => !f.archived);
  const i = list.findIndex((f) => f.id === file);
  const f = (files.data ?? []).find((x) => x.id === file);
  const details = tabAddress(t, DETAILS, flow);
  const at = (id: string) => (flow ? { tab: DETAILS, file: id } : { file: id });
  const back = flow ? { tab: DETAILS } : {};
  const current = f && currentOf(f);
  const v = (search.v && f?.versions.find((x) => x.n === search.v)) || current;
  const versions = [...(f?.versions ?? [])].sort((a, b) => b.n - a.n);
  const other = search.vs && search.vs !== v?.n ? f?.versions.find((x) => x.n === search.vs) : undefined;
  // Compare puts the one before the shown version beside it; the oldest is compared with the one after.
  const pair = versions.find((x) => v && x.n < v.n) ?? versions.find((x) => v && x.n > v.n);
  // The server's rule (ui/app/task-files.ts tfCanRemove): its owner, its requester, or someone who may move tasks.
  const mayRemove = t.mover || [t.owner_id, t.requester_id].includes(me.data?.id ?? '');
  const isPerson = useIsPerson();
  // The task's pictures, each at its current version: the full size view walks them.
  const pictures = list.flatMap((x) => {
    const cur = currentOf(x);
    return cur?.mime.startsWith('image/') && cur.url.startsWith('/api/') ? [{ id: x.id, src: cur.url, name: x.title || x.name } satisfies Picture & { id: string }] : [];
  });
  const fullAt = full ? pictures.findIndex((p) => p.id === full) : -1;
  const showPicture = (id: string | null) => void run('task.showPicture', { task: t.id, file: id }, { via: 'click' });
  const step = (d: number, label: string, flip: boolean) => {
    const to = list[i + d];
    return to ? (
      <Go to={details.to} search={at(to.id)} className="ib ib-sm" aria-label={label} title={label}>
        <Icon name="chevdown" className={flip ? 'flip' : undefined} />
      </Go>
    ) : (
      <button className="ib ib-sm" type="button" aria-label={label} title={label} disabled>
        <Icon name="chevdown" className={flip ? 'flip' : undefined} />
      </button>
    );
  };

  const remove = async (x: TaskFileView) => {
    const r = await run('task.archiveFile', { task: t.id, file: x.id, archived: true }, { via: 'click' });
    if (!r.ok) return void toast(failedToast(r));
    toast({ text: 'Removed', name: x.title || x.name, action: { label: 'Undo', run: () => void run('task.archiveFile', { task: t.id, file: x.id, archived: false }, { via: 'click' }).then((u) => u.ok || toast(failedToast(u))) } });
    void navigate({ to: details.to as never, search: back as never });
  };
  // Restore clears each flag that archived it, a page's own and its file's; the server says who may.
  const mayRestore = !!f?.can_restore;
  const restore = async (x: TaskFileView) => {
    const page = !!x.page_archived;
    const file = !page || !!x.file_archived;
    let r = page ? await run('task.archivePage', { task: t.id, page: x.id, archived: false }, { via: 'click' }) : null;
    if (file && (!r || r.ok)) r = await run('task.archiveFile', { task: t.id, file: x.id, archived: false }, { via: 'click' });
    if (!r) return;
    toast(r.ok ? { text: 'Restored', name: x.title || x.name } : failedToast(r));
  };

  const menu = f && v && (
    <Menu trigger={<IconButton icon="more" label="More" size="sm" data-target={`task-version:${t.id}`} />}>
      <MenuItem label="Download" icon="download" onSelect={() => download(v.url, v.name || f.name)} />
      {versions.length > 1 && (
        <MenuSub label="Versions" icon="clock">
          {versions.map((x) => (
            <MenuItem
              key={x.n}
              label={`v${x.n}`}
              on={x.n === v.n}
              onSelect={() => void run('task.showVersion', { task: t.id, version: x.n === current?.n ? null : x.n }, { via: 'click' })}
            />
          ))}
        </MenuSub>
      )}
      {pair && (
        <MenuItem label="Compare" icon="columns" on={!!other} onSelect={() => void run('task.compare', { task: t.id, against: other ? null : pair.n }, { via: 'click' })} />
      )}
      {(f.archived ? mayRestore : mayRemove) && (
        <>
          <MenuRule />
          {f.archived ? <MenuItem label="Restore" icon="archive" onSelect={() => void restore(f)} /> : <MenuItem label="Remove file" icon="close" onSelect={() => void remove(f)} />}
        </>
      )}
    </Menu>
  );

  return (
    <>
      <div className="fv-bar">
        <Go to={details.to} search={back} className="ib ib-sm" aria-label="Back to Details" title="Back to Details">
          <Icon name="chevron" className="flip" />
        </Go>
        <b>{f ? f.title || f.name : 'File'}</b>
        {versions.length > 1 && v && <small>v{v.n}</small>}
        {v?.width && v.height ? (
          <small>
            {v.width} by {v.height}
          </small>
        ) : null}
        {v && <Face id={v.by} decorative />}
        {v && <Ago at={v.created_at} />}
        {v?.signed && <small className="fv-signed">{signedLine(v.signed)}</small>}
        <span className="grow" />
        {list.length > 1 && i >= 0 && (
          <span className="fv-n">
            <b>{i + 1}</b> of {list.length}
          </span>
        )}
        {list.length > 1 && i >= 0 && step(-1, 'Previous', true)}
        {list.length > 1 && i >= 0 && step(1, 'Next', false)}
        {menu}
      </div>
      {files.isError ? (
        <Empty icon="alert" title="This file did not load" />
      ) : files.isSuccess && !f ? (
        <Empty title="This file is gone" />
      ) : f && v && other ? (
        <div className="frame fv fv-pair" data-target={`task-changes:${t.id}`}>
          {[other, v]
            .sort((a, b) => a.n - b.n)
            .map((x) => (
              <section key={x.n} className="fv-side" aria-label={`v${x.n}`}>
                <small>v{x.n}</small>
                <Body f={f} v={x} />
              </section>
            ))}
        </div>
      ) : (
        <div className="frame fv">
          {v && f && <Body f={f} v={v} onPicture={v === current && !f.archived ? () => showPicture(f.id) : undefined} signable={v === current && isPerson} />}
        </div>
      )}
      <PictureViewer pictures={pictures} at={fullAt >= 0 ? fullAt : null} onAtChange={(i) => showPicture(i == null ? null : (pictures[i]?.id ?? null))} />
    </>
  );
}
