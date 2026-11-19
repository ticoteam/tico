import { useInfiniteQuery, useQuery, useSuspenseInfiniteQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useRef, useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { List, PageMore, Section, SidePanel, type PageState, type PageTab } from '@/pagekinds';
import { Foot, trimmed, useGo } from './AddTool';
import { Face, Find, KindGlyph, More, OwnerButton, press, Who } from './parts';
import { archivedDocsQuery, docRows, docsListQuery, docsSearchQuery, IMPORT_MAX, IMPORT_TYPES, sourceDocsQuery, sourcesQuery, type DocHit, type DocRow } from './queries';
import type { DocSource } from '@/api/schema';
import type { DocsSearch } from './search';
import './library.css';

/* Docs: the team's own docs in sections by their first folder, and External sources as the second tab (files.js
   docsPage, sourcesPage). A doc opens as its own page; a source opens beside the list. */

const docHref = (id: string) => `/library/${encodeURIComponent(id)}`;

function tabs(docs: number | undefined, sources: number | undefined): PageTab[] {
  return [
    { label: 'Docs', to: '/library/docs', count: docs, target: 'tab:docs' },
    { label: 'External sources', to: '/library/docs', search: { tab: 'sources' }, count: sources, target: 'tab:sources' },
  ];
}

/** A folder as a person reads it (docs-page.ts docsFolderWords): "_librarian" is "Librarian", "how-to" "How to". */
const folderWords = (part: string) => part.replace(/^_+/, '').replace(/[-_]+/g, ' ').trim().replace(/^./, (c) => c.toUpperCase()) || part;

/** A doc's folder: its first part names the section, the folders below it are what it is filed under ("How to"), and
    its own file name is neither. A doc at the top, or straight in its section's folder, is filed under nothing. */
function folder(path: string): [string, string] {
  const parts = path.split('/').filter(Boolean);
  if (parts.length < 2) return ['Docs', ''];
  return [folderWords(parts[0] ?? ''), parts.slice(1, -1).map(folderWords).join(' / ')];
}

const newDoc = () => void press('docs.new', {});

export function DocsPage({ search }: { search: DocsSearch }) {
  return search.tab === 'sources' ? <SourcesPage panel={search.panel} /> : <OwnDocs search={search} />;
}

/** The head's ⋯: archived docs, and a file to import as a new doc (the types the server reads, up to 20 MB). */
function DocsMore({ archived }: { archived: boolean }) {
  const pick = useRef<HTMLInputElement>(null);
  const chosen = (file: File | undefined) => {
    if (!file) return;
    if (file.size > IMPORT_MAX) toast({ text: 'Over 20 MB', kind: 'failed' });
    else void press('docs.import', { file }, `${file.name} was not imported`);
  };
  return (
    <>
      <PageMore>
        <MenuItem label="Import a file" onSelect={() => pick.current?.click()} />
        <MenuItem label="Archived" on={archived} onSelect={() => void press('docs.list', { archived: !archived })} />
      </PageMore>
      <input
        ref={pick}
        type="file"
        accept={IMPORT_TYPES}
        hidden
        aria-label="Import a file"
        onChange={(e) => {
          chosen(e.target.files?.[0]);
          e.target.value = '';
        }}
      />
    </>
  );
}

function OwnDocs({ search }: { search: DocsSearch }) {
  const archived = !!search.archived;
  const list = useSuspenseInfiniteQuery(archived ? archivedDocsQuery : docsListQuery);
  const live = useInfiniteQuery(docsListQuery);
  const sources = useQuery(sourcesQuery);
  const docs = docRows(list.data);
  const query = search.q?.trim() ?? '';
  const words = query.toLowerCase();
  // Words to search for go to the server's search of the docs' text (docs-search.ts); until it answers, and in
  // the archive, the loaded list is matched by title and path.
  const full = !archived && /[\p{L}\p{N}_]/u.test(query);
  const found = useQuery({ ...docsSearchQuery(query), enabled: full });
  const hits = full ? found.data : undefined;
  const rows = docs.filter((d) => !words || d.title.toLowerCase().includes(words) || d.path.toLowerCase().includes(words));

  const groups = new Map<string, DocRow[]>();
  for (const d of rows) {
    const [name] = archived ? ['Archived'] : folder(d.path);
    groups.set(name, [...(groups.get(name) ?? []), d]);
  }

  const state: PageState = !docs.length && !hits?.length && !list.hasNextPage ? 'empty' : 'ready';

  return (
    <List
      title="Docs"
      path="/library/docs"
      className="lib"
      tabs={tabs(live.data && docRows(live.data).length, sources.data?.sources.length)}
      search={<Find label="Search docs" value={search.q} onFind={(q) => void press('docs.list', { q, archived: archived || undefined })} />}
      actions={
        <>
          <OwnerButton page="docs" />
          <Button variant="primary" icon="plus" onClick={newDoc} data-target="docs:new">
            New doc
          </Button>
        </>
      }
      more={<DocsMore archived={archived} />}
      state={state}
      empty={{ title: archived ? 'Nothing archived' : 'No docs yet' }}
      sections={
        hits ? (
          hits.length ? (
            <Section name="Found" count={hits.length}>
              {hits.map((h) => (
                <HitLine key={`${h.type}:${h.id}`} hit={h} row={docs.find((d) => d.id === h.id)} />
              ))}
            </Section>
          ) : (
            <Empty title="Nothing matches" />
          )
        ) : (
          <>
            {rows.length > 0 ? (
              <div className={archived ? 'doc-cols is-gone' : 'doc-cols'} aria-hidden="true">
                <span />
                <span>Doc</span>
                <span>Filed under</span>
                <span>Edited by</span>
                <span>Changed</span>
                {archived && <span />}
              </div>
            ) : (
              (full || !list.hasNextPage) && <Empty title="Nothing matches" />
            )}
            {[...groups].map(([name, list]) => (
              <Section key={name} name={name} count={list.length}>
                {list.map((d) => (
                  <DocLine key={d.id} d={d} archived={archived} />
                ))}
              </Section>
            ))}
            {!full && list.hasNextPage && !list.isFetchingNextPage && (
              <More onClick={() => void list.fetchNextPage()}>{list.isFetchNextPageError ? 'Try again' : 'Load more'}</More>
            )}
          </>
        )
      }
    />
  );
}

function DocLine({ d, archived }: { d: DocRow; archived: boolean }) {
  const [, under] = folder(d.path);
  const pending = d.id.startsWith('tmp:');
  return (
    <Row className={archived ? 'lrow drow is-gone' : 'lrow drow'} title={d.title} href={pending || archived ? undefined : docHref(d.id)} target={`doc:${d.id}`}>
      <span className="lead" />
      <span className="doc-name">
        <span className="row-title">{d.title}</span>
      </span>
      <span className="tie">{under && <span>{under}</span>}</span>
      <Who actor={d.updated_by_id || null} name={d.updated_by_name} />
      <Ago at={d.updated_at} className="row-age" />
      {archived && (
        <Menu trigger={<IconButton icon="more" label="More" />}>
          <MenuItem label="Restore" onSelect={() => void press('docs.archive', { doc: d.id, version: d.version, archived: false }, 'Not restored')} />
        </Menu>
      )}
    </Row>
  );
}

/** A search hit: a team doc as its list row; an outside doc opens here, read as it was; a linked page where it lives. */
function HitLine({ hit, row }: { hit: DocHit; row: DocRow | undefined }) {
  if (hit.type === 'internal' && row) return <DocLine d={row} archived={false} />;
  const href = hit.type === 'internal' ? docHref(hit.id) : hit.type === 'external' ? `/library/ext:${encodeURIComponent(hit.id)}${hit.commit ? `?revision=${encodeURIComponent(hit.commit)}` : ''}` : hit.url;
  const under =
    hit.type === 'external' ? hit.source_title : hit.type === 'internal' ? folder(hit.path).filter(Boolean).join(' / ') : hostOf(hit.url);
  return (
    <Row className="lrow drow" title={hit.title} href={href} target={`doc:${hit.id}`}>
      <span className="lead" />
      <span className="doc-name">
        <span className="row-title">{hit.title}</span>
      </span>
      <span className="tie">{under && <span>{under}</span>}</span>
      <span />
      <span className="row-age" />
    </Row>
  );
}

const hostOf = (url: string) => {
  try {
    return new URL(url).host;
  } catch {
    return '';
  }
};

/* ---------------------------------------------------------------- external sources */

const BAD: Partial<Record<DocSource['status'], string>> = { failed: 'Failed', access_needed: 'Needs access', empty: 'Empty' };

function SourcesPage({ panel }: { panel?: string }) {
  const docs = useInfiniteQuery(docsListQuery);
  const { data: sources } = useSuspenseQuery(sourcesQuery);
  const list = sources.sources;
  const [adding, setAdding] = useState(false);

  return (
    <List
      title="Docs"
      path="/library/docs?tab=sources"
      className="lib"
      tabs={tabs(docs.data && docRows(docs.data).length, list.length)}
      actions={
        <>
          <OwnerButton page="docs" />
          <Button variant="primary" icon="plus" onClick={() => setAdding(true)} data-target="sources:new">
            Connect
          </Button>
          {adding && <SourceDialog source={null} onClose={() => setAdding(false)} />}
        </>
      }
      state={list.length ? 'ready' : 'empty'}
      empty={{ title: 'No outside sources yet' }}
      sections={
        <section className="sec">
          {list.map((s) => (
            <Row
              key={s.id}
              className="lrow srow"
              title={s.title}
              href={`/library/docs?tab=sources&panel=${encodeURIComponent(s.id)}`}
              selected={s.id === panel}
              target={`source:${s.id}`}
            >
              <span className="lead" title={s.kind}>
                <KindGlyph kind={s.kind} />
              </span>
              <span className="row-title">{s.title}</span>
              {BAD[s.status] ? (
                <span className="bad">
                  <Icon name="alert" />
                  {BAD[s.status]}
                </span>
              ) : (
                <span />
              )}
              <span className="row-fig">{s.doc_count.toLocaleString('en-US')} docs</span>
              <Face actor={s.added_by || null} />
              {s.last_checked_at ? <Ago at={s.last_checked_at} className="row-age" /> : <span className="row-age" />}
            </Row>
          ))}
        </section>
      }
      panel={panel ? <SourcePanel id={panel} /> : undefined}
    />
  );
}

/** One outside source beside the list: its documents, each opening as an outside doc. */
function SourcePanel({ id }: { id: string }) {
  const src = useQuery(sourceDocsQuery(id));
  const sources = useQuery(sourcesQuery);
  const [editing, setEditing] = useState(false);
  const close = () => void press('docs.list', { tab: 'sources' });
  const s = src.data?.source;
  const row = sources.data?.sources.find((x) => x.id === id);
  return (
    <SidePanel
      title={s?.title ?? 'Source'}
      target={`source:${id}`}
      onClose={close}
      actions={
        <>
          {s?.url && (
            <a className="ib" href={s.url} target="_blank" rel="noreferrer" aria-label="Open the source" title="Open the source">
              <Icon name="popout" />
            </a>
          )}
          {row && <IconButton icon="edit" label="Edit" onClick={() => setEditing(true)} />}
          <Button variant="quiet" icon="refresh" onClick={() => void press('docs.refreshSource', { source: id })}>
            Read again
          </Button>
          {row && editing && <SourceDialog source={row} onClose={() => setEditing(false)} />}
        </>
      }
    >
      {src.isError && <Empty title="This source did not load" icon="alert" />}
      {src.data && !src.data.documents.length && <Empty title="No documents yet" />}
      {src.data?.documents.map((d) => (
        <Link key={d.id} className="row lrow frow" to="/library/$doc" params={{ doc: `ext:${d.id}` }} search={d.commit ? { revision: d.commit } : {}} title={d.title}>
          <span className="lead">
            <Icon name="file" />
          </span>
          <span className="row-title">{d.title}</span>
          <span className="row-ctx">{d.path}</span>
          <span />
          <span />
        </Link>
      ))}
    </SidePanel>
  );
}

/** Link an outside source, or change one (docs-editor.ts linkDialog): its address, a name, what it holds, and for a
   repository the branch or tag it reads. Remove takes it off the list. */
function SourceDialog({ source, onClose }: { source: DocSource | null; onClose: () => void }) {
  const [url, setUrl] = useState(source?.url ?? '');
  const [title, setTitle] = useState(source?.title ?? '');
  const [about, setAbout] = useState(source?.description ?? '');
  const [ref, setRef] = useState(source?.reference_override ?? '');
  const [busy, go] = useGo(onClose);
  const ready = /^https?:\/\/\S+$/.test(url.trim());
  const save = () =>
    source
      ? go('docs.editSource', { source: source.id, url: url.trim(), title: title.trim() || source.title, description: about.trim(), reference: ref.trim() }, 'Not saved')
      : go('docs.addSource', { url: url.trim(), title: trimmed(title), description: trimmed(about), reference: trimmed(ref) }, 'Not linked');
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={source ? 'Edit source' : 'Connect a source'}
      actions={
        <>
          {source && (
            <>
              <Button variant="quiet" disabled={busy} onClick={() => void go('docs.editSource', { source: source.id, archived: true }, 'Not removed')}>
                Remove
              </Button>
              <span className="grow" />
            </>
          )}
          <Foot onClose={onClose} label={source ? 'Save' : 'Connect'} busy={busy} ready={ready} onGo={() => void save()} />
        </>
      }
    >
      <Field label="Address" value={url} onValueChange={setUrl} placeholder="https://github.com/acme/handbook" autoComplete="off" autoFocus />
      <Field label="Name" value={title} onValueChange={setTitle} placeholder="The address" autoComplete="off" />
      <Field label="What it holds" value={about} onValueChange={setAbout} autoComplete="off" />
      <Field label="Branch or tag" value={ref} onValueChange={setRef} placeholder="The default branch" autoComplete="off" />
    </Dialog>
  );
}
