import { useInfiniteQuery, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { meQuery } from '@/data';
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
import { useCards } from '@/kit/Popover';
import { Row } from '@/kit/Row';
import { Document, FilterRow, List, Section } from '@/pagekinds';
import { SectionHead } from '@/kit/SectionHead';
import { VirtualList } from '@/kit/VirtualList';
import { FileGet, linkOf, Preview } from './FileViewer';
import { Face, Find, KindGlyph, press, size, when, Who } from './parts';
import { fileQuery, fileSearchQuery, filesQuery, fileUrl, fileVersionsQuery, type FileRow } from './queries';
import type { FilesSearch } from './search';
import { signedLine } from './signing';
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

type Item = { k: 'head'; name: string; n: number | undefined } | { k: 'file'; f: FileRow };

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
  if (search.file) return <FilePage id={search.file} search={search} />;
  return <FilesList search={search} />;
}

function FilesList({ search }: { search: FilesSearch }) {
  const kind = search.kind ?? '';
  const searching = (search.q?.trim().length ?? 0) > 1;
  const files = useInfiniteQuery({ ...filesQuery(kind), enabled: !searching });
  const found = useQuery(fileSearchQuery(search.q ?? '', kind));
  const [chosen, setChosen] = useState<File[] | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const rows = files.data?.pages.flatMap((p) => p.files) ?? [];
  // Grouped against when the page opened, so the groups do not shift while it is read.
  const [now] = useState(() => Date.now());
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const groups = (['Today', 'This week', 'Earlier'] as const)
    .map((name) => ({ name, list: rows.filter((f) => bucket(f.last_activity_at, now) === name) }))
    .filter((g) => g.list.length);
  // The last group goes on in pages not read yet: its count would be the page size, so it shows none until the end.
  const items: Item[] = groups.flatMap(({ name, list }, i) => [
    { k: 'head' as const, name, n: i === groups.length - 1 && files.hasNextPage ? undefined : list.length },
    ...(folded.has(name) ? [] : list.map((f) => ({ k: 'file' as const, f }))),
  ]);
  const fold = (name: string) => {
    const next = new Set(folded);
    if (!next.delete(name)) next.add(name);
    setFolded(next);
  };

  const take = (list: FileList | null | undefined) => {
    const picked = list ? Array.from(list) : [];
    if (!picked.length) return;
    if (search.task) void upload(search.task, picked);
    else setChosen(picked);
  };
  // The page sheet itself takes dropped files: the kinds take no DOM props, and a wrapper around the sheet would break
  // the shell's `.shell-place>.pane` layout (the page drew 0 wide).
  useEffect(() => {
    const pane = input.current?.closest('.pane');
    if (!pane) return;
    const over = (e: Event) => {
      if (!(e as DragEvent).dataTransfer?.types.includes('Files')) return;
      e.preventDefault();
      pane.classList.add('is-drop');
    };
    const leave = () => pane.classList.remove('is-drop');
    const drop = (e: Event) => {
      e.preventDefault();
      leave();
      take((e as DragEvent).dataTransfer?.files);
    };
    pane.addEventListener('dragover', over);
    pane.addEventListener('dragleave', leave);
    pane.addEventListener('drop', drop);
    return () => {
      pane.removeEventListener('dragover', over);
      pane.removeEventListener('dragleave', leave);
      pane.removeEventListener('drop', drop);
    };
  });
  const kindName = KINDS.find(([k]) => k === kind)?.[1];
  // With no files at all, the head keeps only the owner and ⋯: the empty state holds the one button (files.js empty).
  const none = !rows.length && !kind && !searching && files.isSuccess;
  const add = (
    <Button variant="primary" onClick={() => input.current?.click()} data-target="files:add">
      Add
    </Button>
  );

  return (
    <>
      <List
        title="Files"
        path="/library/files"
        className="lib"
        search={!none && <Find label="Search files" value={search.q} onFind={(q) => void press('files.list', { kind: search.kind, q })} />}
        actions={
          <>
            {!none && (
              <Menu trigger={<IconButton icon="filter" label="Filter by kind" on={!!kind} data-phone />}>
              <MenuItem label="All files" on={!kind} onSelect={() => void press('files.list', { q: search.q })} />
              {KINDS.map(([k, label]) => (
                <MenuItem key={k} label={label} on={k === kind} onSelect={() => void press('files.list', { kind: k, q: search.q })} />
              ))}
              </Menu>
            )}
            {!none && add}
            <input ref={input} type="file" multiple hidden aria-label="Choose files" onChange={(e) => take(e.target.files)} />
          </>
        }
        filter={kindName && <FilterRow filters={[{ label: kindName, onClear: () => void press('files.list', { q: search.q }) }]} />}
        state={searching || rows.length ? 'ready' : files.isPending ? 'loading' : files.isError ? 'fault' : kind ? 'ready' : 'empty'}
        onRetry={() => void files.refetch()}
        empty={{ title: 'No files yet', action: <Button variant="primary" onClick={() => input.current?.click()}>Add file</Button> }}
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
              {found.isLoadingError && (
                <Empty title="The search did not load" icon="alert">
                  <Button onClick={() => found.refetch()}>Try again</Button>
                </Empty>
              )}
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
    </>
  );
}

function FileLine({ f, href }: { f: FileRow; href: string }) {
  return (
    <Row className="lrow frow" title={f.title} href={href} target={`file:${f.id}`}>
      <span className="lead" title={f.kind_label ?? f.library_kind}>
        <KindGlyph kind={f.library_kind} />
      </span>
      <span className="row-title">{f.title}</span>
      {/* The kind is the lead's glyph; the tag with the task mark is the task that made the file (files.js). */}
      <span className="row-tags">
        {f.task_title && (
          <span className="tag" title={f.task_title}>
            <Icon name="tasks" />
            <span>{f.task_title}</span>
          </span>
        )}
      </span>
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
          <Button variant="primary" busy={busy} disabled={!task.trim() || busy} onClick={() => go()}>
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

function FilePage({ id, search }: { id: string; search: FilesSearch }) {
  const { data: file } = useSuspenseQuery(fileQuery(id));
  const versions = useQuery(fileVersionsQuery(id));
  const cards = useCards();
  const version = search.v;
  const older = version !== undefined && version !== file.version;
  const shown = versions.data?.find((v) => (version === undefined ? v.current : v.version === version));
  const unavailable = older && !shown;
  const n = version ?? shown?.version ?? file.version;
  const mime = shown?.mime ?? (older ? '' : file.mime ?? '');
  const url = fileUrl(id, n);
  const me = useQuery(meQuery);
  // A task's file at its current version, to a person: a PDF offers Sign (the server checks the rest).
  const signable = !!file.task_id && !older && (me.data?.role === 'person' || me.data?.role === 'owner');

  return (
    <Document
      title={file.title}
      path={`/library/files?file=${id}`}
      className="lib"
      state={unavailable ? (versions.isPending ? 'loading' : 'fault') : 'ready'}
      onRetry={versions.isError ? () => void versions.refetch() : undefined}
      after={
        cards && (versions.data?.length ?? 0) > 1 && (
          <Menu
            align="start"
            trigger={
              <button type="button" className="btn btn-quiet ver-pick" title="Versions" aria-label="Versions">
                v{n}
                <Icon name="chevdown" />
              </button>
            }
          >
            {versions.data?.map((v) => (
              <MenuItem
                key={v.version}
                label={`v${v.version}`}
                meta={when(v.created_at)}
                on={v.version === n}
                onSelect={() => void press('files.open', { file: id, version: v.current ? undefined : v.version })}
              />
            ))}
          </Menu>
        )
      }
      actions={
        <>
          {!unavailable && <FileGet url={url} link={linkOf(file)} />}
        </>
      }
      framed
      body={
        <Preview
          id={id}
          version={n ?? 1}
          mime={mime}
          kind={older ? undefined : file.library_kind}
          title={file.title}
          url={url}
          name={shown?.name ?? (older ? '' : file.name)}
          size={shown?.size ?? (older ? undefined : file.size)}
          link={linkOf(file)}
          signable={signable}
        />
      }
      sideFacts
      side={
        file && (
          <div className="fv-side">
            <Facts label="About this file">
              <LabelValue label="Kind">{file.provider_label || file.kind.replace(/^./, (c) => c.toUpperCase())}</LabelValue>
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
                    href={fileHref(search, id, v.current ? undefined : v.version)}
                  >
                    <b>v{v.version}</b>
                    {v.signed ? <span title={signedLine(v.signed)}>{signedLine(v.signed)}</span> : <span>{when(v.created_at)}</span>}
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
