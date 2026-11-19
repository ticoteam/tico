import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useRef, useState, type DragEvent } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Document, FilterRow, List, Section, type PageState } from '@/pagekinds';
import { SectionHead } from '@/kit/SectionHead';
import { VirtualList } from '@/kit/VirtualList';
import { FileGet, linkOf, Preview } from './FileViewer';
import { Face, Find, KindGlyph, OwnerButton, press, refused, size, when, Who } from './parts';
import { fileQuery, fileSearchQuery, filesQuery, fileUrl, fileVersionsQuery, type FileRow } from './queries';
import type { FilesSearch } from './search';
import './library.css';

/* Files (files.js filesPage, filePage): every file teammates made or added, newest first in Today, This week and
   Earlier. A file opens in the page as a framed preview with its facts and versions beside it. */

const KINDS = [
  ['page', 'Pages'],
  ['document', 'Documents'],
  ['image', 'Images'],
  ['data', 'Data'],
  ['media', 'Media'],
  ['other', 'Other'],
] as const;

const DAY = 86_400_000;

type Item = { k: 'head'; name: string; n: number } | { k: 'file'; f: FileRow };

function bucket(at: string, now: number): 'Today' | 'This week' | 'Earlier' {
  const age = now - new Date(at).getTime();
  return age < DAY ? 'Today' : age < 7 * DAY ? 'This week' : 'Earlier';
}

const fileHref = (search: FilesSearch, id: string, v?: number) => {
  const p = new URLSearchParams();
  if (search.kind) p.set('kind', search.kind);
  if (search.q) p.set('q', search.q);
  p.set('file', id);
  if (v) p.set('v', String(v));
  return `/library/files?${p.toString()}`;
};

export function FilesPage({ search }: { search: FilesSearch }) {
  if (search.file) return <FilePage id={search.file} version={search.v} />;
  return <FilesList search={search} />;
}

function FilesList({ search }: { search: FilesSearch }) {
  const kind = search.kind ?? '';
  const files = useInfiniteQuery(filesQuery(kind));
  const found = useQuery(fileSearchQuery(search.q ?? ''));
  const [chosen, setChosen] = useState<File[] | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const rows = files.data?.pages.flatMap((p) => p.files) ?? [];
  const searching = !!search.q && search.q.trim().length > 1;
  // Grouped against when the page opened, so the groups do not shift while it is read.
  const [now] = useState(() => Date.now());
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const items: Item[] = (['Today', 'This week', 'Earlier'] as const).flatMap((name) => {
    const list = rows.filter((f) => bucket(f.last_activity_at, now) === name);
    if (!list.length) return [];
    return [{ k: 'head' as const, name, n: list.length }, ...(folded.has(name) ? [] : list.map((f) => ({ k: 'file' as const, f })))];
  });
  const fold = (name: string) => {
    const next = new Set(folded);
    if (!next.delete(name)) next.add(name);
    setFolded(next);
  };

  let state: PageState = 'ready';
  if (files.isError) state = refused(files.error);
  else if (!files.data) state = 'loading';
  else if (!rows.length && !kind && !searching) state = 'empty';

  const take = (list: FileList | null | undefined) => {
    const picked = list ? Array.from(list) : [];
    if (!picked.length) return;
    if (search.task) void upload(search.task, picked);
    else setChosen(picked);
  };
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    take(e.dataTransfer.files);
  };
  const kindName = KINDS.find(([k]) => k === kind)?.[1];

  return (
    <div
      className={over ? 'lib-drop is-over' : 'lib-drop'}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <List
        title="Files"
        path="/library/files"
        className="lib"
        search={<Find label="Search files" value={search.q} onFind={(q) => void press('files.list', { kind: search.kind, q })} />}
        actions={
          <>
            <Menu trigger={<IconButton icon="filter" label="Filter by kind" on={!!kind} data-phone />}>
              <MenuItem label="All files" on={!kind} onSelect={() => void press('files.list', { q: search.q })} />
              {KINDS.map(([k, label]) => (
                <MenuItem key={k} label={label} on={k === kind} onSelect={() => void press('files.list', { kind: k, q: search.q })} />
              ))}
            </Menu>
            <OwnerButton page="docs" />
            <Button variant="primary" icon="plus" onClick={() => input.current?.click()} data-target="files:add">
              Add
            </Button>
            <input ref={input} type="file" multiple hidden aria-label="Choose files" onChange={(e) => take(e.target.files)} />
          </>
        }
        filter={kindName && <FilterRow filters={[{ label: kindName, onClear: () => void press('files.list', { q: search.q }) }]} />}
        state={state}
        empty={{ title: 'No files yet' }}
        onRetry={() => void files.refetch()}
        sections={
          searching ? (
            <Section name="Found" count={found.data?.results.length}>
              {found.data?.results.map((f) => (
                <Row key={f.id} className="lrow frow" title={f.title} href={fileHref(search, f.id)} target={`file:${f.id}`}>
                  <span className="lead">
                    <Icon name="file" />
                  </span>
                  <span className="row-title">{f.title}</span>
                  <span />
                  <span />
                  <span />
                </Row>
              ))}
              {found.data && !found.data.results.length && <Empty title="Nothing matches" />}
            </Section>
          ) : rows.length ? (
            <VirtualList
              className="lib-vl"
              label="Files"
              items={items}
              getKey={(it) => (it.k === 'head' ? `head:${it.name}` : it.f.id)}
              estimateSize={36}
              measure
              onReachEnd={() => files.hasNextPage && !files.isFetchingNextPage && void files.fetchNextPage()}
              renderItem={(it) =>
                it.k === 'head' ? (
                  <SectionHead name={it.name} count={it.n} open={!folded.has(it.name)} onToggle={() => fold(it.name)} />
                ) : (
                  <FileLine f={it.f} href={fileHref(search, it.f.id)} />
                )
              }
            />
          ) : (
            <Empty title={`No ${kindName?.toLowerCase() ?? 'files'}`} />
          )
        }
      />
      {chosen && <UploadDialog files={chosen} onClose={() => setChosen(null)} />}
    </div>
  );
}

function FileLine({ f, href }: { f: FileRow; href: string }) {
  return (
    <Row className="lrow frow" title={f.title} href={href} target={`file:${f.id}`}>
      <span className="lead" title={f.kind_label ?? f.library_kind}>
        <KindGlyph kind={f.library_kind} />
      </span>
      <span className="row-title">{f.title}</span>
      <span className="row-tags">{f.kind_label && <span className="tag">{f.kind_label}</span>}</span>
      <Face actor={f.bot} name={f.bot_name} />
      <Ago at={f.last_activity_at} className="row-age" />
    </Row>
  );
}

async function upload(task: string, files: File[]) {
  for (const file of files) await press('files.upload', { task, file }, `${file.name} was not added`);
}

/** A person's file lands on a task (the server takes a person's upload only there): which one. */
function UploadDialog({ files, onClose }: { files: File[]; onClose: () => void }) {
  const [task, setTask] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    await upload(task.trim(), files);
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={files.length === 1 ? `Add ${files[0]?.name ?? 'file'}` : `Add ${files.length} files`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!task.trim() || busy} onClick={() => void go()}>
            Add
          </Button>
        </>
      }
    >
      <Field label="Task number" value={task} onValueChange={setTask} autoFocus />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- one file */

function FilePage({ id, version }: { id: string; version?: number }) {
  const f = useQuery(fileQuery(id));
  const versions = useQuery(fileVersionsQuery(id));
  const file = f.data;
  const current = versions.data?.find((v) => v.current);
  const shown = (version && versions.data?.find((v) => v.version === version)) || current;
  const n = shown?.version ?? file?.version;
  const mime = shown?.mime ?? file?.mime ?? '';
  const url = fileUrl(id, n);

  let state: PageState = 'ready';
  if (f.isError) state = refused(f.error);
  else if (!file) state = 'loading';

  return (
    <Document
      title={file?.title ?? 'File'}
      path={`/library/files?file=${id}`}
      crumb={[{ label: 'Files', to: '/library/files' }]}
      className="lib"
      actions={
        <>
          <FileGet url={url} link={linkOf(file)} />
          <OwnerButton page="docs" />
        </>
      }
      state={state}
      onRetry={() => void f.refetch()}
      framed
      body={
        file && <Preview id={id} version={n ?? 1} mime={mime} kind={file.library_kind} title={file.title} url={url} name={shown?.name ?? file.name} link={linkOf(file)} />
      }
      side={
        file && (
          <div className="fv-side">
            <Facts label="About this file">
              <LabelValue label="Kind">{file.provider_label || file.kind}</LabelValue>
              {shown?.size != null && <LabelValue label="Size">{size(shown.size)}</LabelValue>}
              <LabelValue label="Made by">
                <Who actor={file.actor ?? file.bot} className="fact-who" />
              </LabelValue>
              {file.task_title && <LabelValue label="Task">{file.task_title}</LabelValue>}
              <LabelValue label="Changed">{when(file.last_activity_at)}</LabelValue>
            </Facts>
            {(versions.data?.length ?? 0) > 0 && (
              <>
                <div className="fv-h">
                  <h3 className="t-label">Versions</h3>
                </div>
                {versions.data?.map((v) => (
                  <Link
                    key={v.version}
                    className={v.version === n ? 'fv-ver is-on' : 'fv-ver'}
                    href={fileHref({}, id, v.current ? undefined : v.version)}
                  >
                    <b>v{v.version}</b>
                    <span>{when(v.created_at)}</span>
                    <Face actor={v.actor} />
                  </Link>
                ))}
              </>
            )}
          </div>
        )
      }
    />
  );
}
