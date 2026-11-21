import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from '@tanstack/react-router';
import { useRef, useState } from 'react';
import { run } from '@/actions';
import type { TaskFileResult } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { fmtDate } from '@/lib';
import { DETAILS, failedToast, isPage, reasonOf, tabAddress } from './model';
import { pagePickQuery, pageShownQuery, taskDetailQuery, taskFilesQuery, type TaskData } from './queries';
import type { Versions } from './Versions';

/* What a person does to a task's pages and files beyond reading them (the old app's page view, ui/app/pages.ts, and
   its Files tab, ui/app/task-pages.ts and task-files.ts): a page's ⋯ (what changed, its versions, archive,
   download, delete), linking a page, and adding a file from disk. */

/** Saves a file the browser already may read (the same address the frame shows). */
export function download(url: string, name: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.rel = 'noreferrer';
  document.body.append(a);
  a.click();
  a.remove();
}

/** Where Details is from the address on screen: a task's tab, or Flow's `?tab=`. */
function useToDetails() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return () => {
    if (pathname.startsWith('/flow/')) return navigate({ to: pathname as never, search: { tab: DETAILS } as never });
    const m = /^(\/tasks\/[^/]+\/[^/]+)/.exec(pathname);
    return m ? navigate({ to: `${m[1]}/${DETAILS}` as never }) : undefined;
  };
}

const clock = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });
const when = (at: string) => `${fmtDate(at)}, ${clock.format(new Date(at))}`;

/** A page's ⋯ at the tab row's end. What it offers follows what the server says this reader may do. */
export function PageMenu({ task, vs }: { task: string; vs: Versions }) {
  const file = vs.doc.file_id;
  // What only a page offers (archive, delete) shows once the server has answered that it is one.
  const shown = useQuery({ ...pageShownQuery(task, file), enabled: isPage(vs.doc) });
  const toDetails = useToDetails();
  const [open, setOpen] = useState<'delete' | null>(null);
  const p = shown.data?.page;
  const manage = !!shown.data?.can_manage;
  const name = vs.doc.title || vs.doc.name;

  const archive = async (archived: boolean) => {
    const r = await run('task.archivePage', { task, page: file, archived }, { via: 'click' });
    if (!r.ok) return void toast(failedToast(r));
    if (!archived) return;
    toast({ text: 'Archived', name, action: { label: 'Undo', run: () => void run('task.archivePage', { task, page: file, archived: false }, { via: 'click' }).then((u) => u.ok || toast(failedToast(u))) } });
    await toDetails();
  };

  return (
    <>
      <Menu trigger={<IconButton icon="more" label="More" size="sm" data-target={`task-page-more:${file}`} />}>
        {vs.before && (
          <MenuItem label="What’s changed" icon="refresh" on={vs.on} onSelect={() => void run('task.compare', { task, against: vs.on ? null : (vs.before ?? null) }, { via: 'click' })} />
        )}
        {vs.list.length > 1 && (
          <MenuSub label="Versions" icon="clock">
            {vs.list.map((x) => (
              <MenuItem
                key={x.n}
                label={`v${x.n}`}
                meta={when(x.at)}
                on={x.n === vs.shown}
                onSelect={() => void run('task.showVersion', { task, version: x.n === vs.doc.version ? null : x.n }, { via: 'click' })}
              />
            ))}
          </MenuSub>
        )}
        {(manage || p?.may_write) && <MenuItem label={p?.archived ? 'Restore' : 'Archive'} icon="archive" onSelect={() => void archive(!p?.archived)} />}
        <MenuItem label="Download" icon="download" onSelect={() => download(`/api/v2/files/${encodeURIComponent(file)}?v=${vs.shown}`, vs.doc.name)} />
        {shown.data?.can_delete && (
          <>
            <MenuRule />
            <MenuItem label="Delete page" icon="close" onSelect={() => setOpen('delete')} />
          </>
        )}
      </Menu>
      {open === 'delete' && <DeletePage task={task} page={file} name={name} onClose={() => setOpen(null)} onDeleted={() => void toDetails()} />}
    </>
  );
}

/** Archived files and pages, off the tab row and folded at its end: each opens in the page sheet, where it can be restored. */
export function ArchiveMenu({ t, flow }: { t: TaskData; flow: boolean }) {
  const navigate = useNavigate();
  const files = useQuery(taskFilesQuery(t.id));
  const archived = (files.data ?? []).filter((f) => f.archived);
  if (!archived.length) return null;
  const details = tabAddress(t, DETAILS, flow);
  return (
    <Menu
      trigger={
        <button className="tv-ver tv-add" type="button" data-target={`task-archive:${t.id}`}>
          <Icon name="archive" />
          Archive ({archived.length})
        </button>
      }
    >
      {archived.map((f) => (
        <MenuItem key={f.id} label={f.title || f.name} onSelect={() => void navigate({ to: details.to as never, search: (flow ? { tab: DETAILS, file: f.id } : { file: f.id }) as never })} />
      ))}
    </Menu>
  );
}

/** Delete for good asks first: every version goes, and nothing brings it back. */
function DeletePage({ task, page, name, onClose, onDeleted }: { task: string; page: string; name: string; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    const r = await run('task.deletePage', { task, page }, { via: 'click' });
    setBusy(false);
    onClose();
    if (!r.ok) return void toast(failedToast(r));
    toast({ text: 'Deleted', name });
    onDeleted();
  };
  return (
    <Dialog
      open
      alert
      onOpenChange={(o) => !o && onClose()}
      title={`Delete ${name}?`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={busy} onClick={() => go()}>
            Delete
          </Button>
        </>
      }
    />
  );
}

/** Link a page: the newest pages, or what matches the words typed. A page already on the task is not offered. */
export function LinkPage({ task, onDone }: { task: string; onDone: () => void }) {
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const found = useQuery(pagePickQuery(q));
  const files = useQuery(taskFilesQuery(task));
  const t = useQuery(taskDetailQuery(task));
  const have = new Set([...(files.data ?? []).map((f) => f.id), ...(t.data?.documents ?? []).map((d) => d.file_id)]);
  const rows = (found.data ?? []).filter((p) => !have.has(p.id));

  const link = async (page: string, title: string) => {
    setBusy(page);
    setError('');
    const r = await run('task.linkPage', { task, page }, { via: 'click' });
    setBusy(null);
    if (!r.ok) return setError(reasonOf(r.error));
    toast({ text: 'Linked', name: title });
    onDone();
  };

  return (
    <div className="tv-new">
      <Search label="Find a page" placeholder="Find a page" value={q} onValueChange={setQ} wide autoFocus />
      {found.isError ? (
        <Empty icon="alert" title="Pages did not load" />
      ) : found.data && !rows.length ? (
        <Empty title={q.trim() ? 'No pages' : 'No other pages'} />
      ) : (
        <div className="tv-pages">
          {rows.map((p) => (
            <button key={p.id} type="button" className="row dt-file" title={p.title} disabled={!!busy} onClick={() => void link(p.id, p.title)}>
              <span className="row-mark">
                <Icon name="rules" />
              </span>
              <span className="row-title">{p.title}</span>
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="f-err" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** Add files from disk: chosen, or dropped on the box. The last one added opens. */
export function UploadFiles({ task, onDone }: { task: string; onDone: (file: string | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState('');

  const take = async (list: FileList | null | undefined) => {
    const files = list ? Array.from(list) : [];
    if (!files.length || busy) return;
    setBusy(true);
    setError('');
    let last: string | null = null;
    let added = 0;
    for (const file of files) {
      const r = await run('task.upload', { task, file }, { via: 'click' });
      if (!r.ok) {
        setError(`${file.name}: ${reasonOf(r.error)}`);
        break;
      }
      added++;
      last = (r.out as TaskFileResult | undefined)?.file_id ?? last;
    }
    setBusy(false);
    if (!added) return;
    toast({ text: added === 1 ? 'Added' : `Added ${added} files`, name: added === 1 ? files[0]?.name : undefined });
    if (added === files.length) onDone(last);
  };

  return (
    <div
      className={over ? 'tv-drop is-over' : 'tv-drop'}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        void take(e.dataTransfer.files);
      }}
    >
      <input ref={input} type="file" multiple hidden aria-label="Files to add" onChange={(e) => void take(e.target.files)} />
      <Button icon="attach" busy={busy} disabled={busy} onClick={() => input.current?.click()}>
        Choose files
      </Button>
      {error && (
        <p className="f-err" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
