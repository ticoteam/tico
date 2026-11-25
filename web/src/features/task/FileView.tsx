import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import type { TaskDocument, TaskFileView } from '@/api/schema';
import { APP_SANDBOX, meQuery, useAppBridge } from '@/data';
import { changeWords } from '@/lib';
import { FileFrame, FilePreview, signedLine } from '@/features/library/exports';
import { Ago } from '@/kit/Ago';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { PictureViewer, type Picture } from '@/kit/PictureViewer';
import { Skeleton, useDelayed } from '@/kit/Skeleton';
import { toast } from '@/kit/Toast';
import { ReadFault } from '@/pagekinds';
import { download } from './Files';
import { failedToast, isPage } from './model';
import { Face } from './parts';
import { frameDoc, parsePage } from './changes';
import { useTaskWords } from './routing';
import { fileMetaQuery, pageShownQuery, pageVersionQuery, taskFilesQuery, type TaskData } from './queries';
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
function PageOrFile({ task, id, title, page, file, pin }: { task: string; id: string; title: string; page: boolean; file: ReactNode; pin?: number }) {
  const shown = useQuery({ ...pageShownQuery(task, id), enabled: page });
  // No address (an install without page hosting: `src` null): the page's own text at its version, framed as an older
  // version is, so the tab never waits on an address that will not come. A version an ask pinned (`pin`) is shown live
  // only while the live page is that version; else that version's own text.
  const live = shown.data?.page;
  const p = live && pin && live.version !== pin ? { ...live, src: null, version: pin } : live;
  const text = useQuery({ ...pageVersionQuery(id, p?.version ?? 0), enabled: !!p && !p.src && !!p.version });
  // An app runs sealed, with its bridge, only from its live address (the current version, the one served with a key).
  const app = p?.src && p.app && p.version ? { page: id, version: p.version } : null;
  const frame = useAppBridge(app);
  // An app keeps its first address for its version: a renewed one would reload the frame and drop its bridge.
  const mount = app ? `${id}@${app.version}` : '';
  const [held, setHeld] = useState({ mount: '', src: '' });
  if (app && p?.src && held.mount !== mount) setHeld({ mount, src: p.src });
  // with no app drawn, nothing is held: the next one starts from the address read now (an old one may have expired)
  if (!app && held.mount) setHeld({ mount: '', src: '' });
  const src = app && held.mount === mount ? held.src : p?.src;
  if (!page || shown.isError) return <div className="frame fv">{file}</div>;
  if (text.isError) return <ReadFault what={title} reads={[text]} />;
  const html = text.data != null ? frameDoc(parsePage(text.data)) : null;
  return (
    <div className="frame is-inset">
      {p?.src ? (
        <iframe key={mount} ref={frame} src={src ?? p.src} title={title} sandbox={app ? APP_SANDBOX : PAGE_SANDBOX} referrerPolicy="no-referrer" />
      ) : html ? (
        <iframe srcDoc={html} title={title} sandbox={PAGE_SANDBOX} referrerPolicy="no-referrer" />
      ) : (
        <PageBones />
      )}
    </div>
  );
}

/** A document's tab: a page, or a Markdown file. */
export function DocFrame({ task, doc, pin }: { task: string; doc: TaskDocument; pin?: number }) {
  return <PageOrFile task={task} id={doc.file_id} title={doc.title || doc.name} page={isPage(doc)} pin={pin} file={<FilePreview file={doc.file_id} version={doc.version} />} />;
}

/** One of the task's other files as a tab, at its current version or the one an ask pinned (`version`): an HTML file
    may be a page linked to the task. */
export function FileTab({ task, f, version }: { task: string; f: TaskFileView; version?: number }) {
  const isPerson = useIsPerson();
  const now = currentOf(f);
  const listed = version !== undefined ? f.versions.find((x) => x.n === version) : now;
  // A version the listing no longer reaches (it keeps the newest): its own name and type, read by the file's id.
  const meta = useQuery({ ...fileMetaQuery(f.id, version ?? 0), enabled: !listed && version !== undefined });
  const v =
    listed ??
    (now && meta.data && { ...now, n: version as number, url: `/api/v2/files/${encodeURIComponent(f.id)}?v=${version}`, mime: meta.data.content_type, name: meta.data.name, size: meta.data.size ?? null });
  if (meta.isError) return <ReadFault what="This file" reads={[meta]} />;
  if (!v) return <PageBones />;
  const name = f.title || f.name;
  // Each version keeps the name it was stored under: its viewer and its download follow that version's own.
  const stored = v.name || f.name;
  // A Word file is drawn as its words on paper, with Download beside it.
  if (isWord(stored, v.mime))
    return (
      <>
        <div className="fv-bar">
          <b>{name}</b>
          <span className="grow" />
          <a className="ib ib-sm" href={v.url} download={stored} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </div>
        <WordPages url={v.url} name={name} />
      </>
    );
  // At its current version a person may sign it here as in its file view; an older version pinned by an ask is read only.
  const file = <FileFrame id={f.id} version={v.n} mime={v.mime} title={name} url={v.url} name={stored} size={v.size} poster={v.poster_url} signable={isPerson && v === now} />;
  return <PageOrFile task={task} id={f.id} title={name} page={v.mime.startsWith('text/html')} pin={version} file={file} />;
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
  return <FileFrame id={f.id} version={v.n} mime={v.mime} title={`${name}, v${v.n}`} url={v.url} name={stored} size={v.size} poster={v.poster_url} signable={signable} />;
}

/** A file open one layer over the task (more.js fileBar): back to the tab under it, its name, which of the task's files
    it is, and its ⋯ (Download, its versions, Compare, Remove). The address keeps its version (`fv`) and the one compared
    with (`fvs`), apart from the tab's own (`v`, `vs`): closing it leaves the tab as it was read. `back` names that tab;
    `onClose` takes the file away, back to it. */
export function FileView({ t, file, full, back, onClose }: { t: TaskData; file: string; full?: string; back: string; onClose: () => void }) {
  const files = useQuery(taskFilesQuery(t.id));
  const me = useQuery(meQuery);
  const search = useTaskWords();
  const navigate = useNavigate();
  // An archived file opens from the Archive menu too; it is not one of the task's files to step through.
  const list = (files.data ?? []).filter((f) => !f.archived);
  const i = list.findIndex((f) => f.id === file);
  const f = (files.data ?? []).find((x) => x.id === file);
  const current = f && currentOf(f);
  // The version the address names (`fv`), else its current one. The listing keeps only a file's newest versions: one
  // past them is read by the file's id, its own name and type, and says so when it cannot be read; never the current.
  const listed = search.fv ? f?.versions.find((x) => x.n === search.fv) : current;
  const meta = useQuery({ ...fileMetaQuery(file, search.fv ?? 0), enabled: !!f && !!search.fv && !listed });
  const unlisted: FileVersion | undefined =
    !listed && search.fv && current && meta.data
      ? {
          ...current,
          n: search.fv,
          url: `/api/v2/files/${encodeURIComponent(file)}?v=${search.fv}`,
          mime: meta.data.content_type,
          name: meta.data.name,
          size: meta.data.size ?? 0,
          width: null,
          height: null,
          poster_url: null,
          thumb_url: null,
          signed: null,
          answers: [],
          ask: null,
        }
      : undefined;
  const v = listed ?? unlisted;
  const versions = [...(f?.versions ?? [])].sort((a, b) => b.n - a.n);
  const other = search.fvs && search.fvs !== v?.n ? f?.versions.find((x) => x.n === search.fvs) : undefined;
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
    // the next file in its place, over the same tab: no step in the history, the tab under it untouched
    const go = to && (() => void navigate({ to: '.', search: changeWords(({ fv: _v, fvs: _vs, full: _p, ...rest }) => ({ ...rest, file: to.id })) as never, replace: true }));
    return (
      <button className="ib ib-sm" type="button" aria-label={label} title={label} disabled={!go} onClick={go || undefined}>
        <Icon name="chevdown" className={flip ? 'flip' : undefined} />
      </button>
    );
  };

  const remove = async (x: TaskFileView) => {
    const r = await run('task.archiveFile', { task: t.id, file: x.id, archived: true }, { via: 'click' });
    if (!r.ok) return void toast(failedToast(r));
    toast({ text: 'Removed', name: x.title || x.name, action: { label: 'Undo', run: () => void run('task.archiveFile', { task: t.id, file: x.id, archived: false }, { via: 'click' }).then((u) => u.ok || toast(failedToast(u))) } });
    onClose();
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
        <button className="ib ib-sm" type="button" aria-label={`Back to ${back}`} title={`Back to ${back}`} onClick={onClose}>
          <Icon name="chevron" className="flip" />
        </button>
        <b>{f ? f.title || f.name : 'File'}</b>
        {versions.length > 1 && v && <small>v{v.n}</small>}
        {v?.width && v.height ? (
          <small>
            {v.width} by {v.height}
          </small>
        ) : null}
        {listed && <Face id={listed.by} decorative />}
        {listed && <Ago at={listed.created_at} />}
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
      {files.isError || (f && !listed && meta.isError) ? (
        <ReadFault what="This file" reads={[files, meta]} />
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
