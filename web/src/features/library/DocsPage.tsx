import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { List, Section, SidePanel, type PageState, type PageTab } from '@/pagekinds';
import { Face, Find, KindGlyph, OwnerButton, press, Who } from './parts';
import { docsListQuery, sourceDocsQuery, sourcesQuery, type DocRow } from './queries';
import type { DocSource } from '@/api/schema';
import type { DocsSearch } from './search';
import './library.css';

/* Docs: the team's own docs in sections by their first folder, and External sources as the second tab (files.js
   docsPage, sourcesPage). A doc opens as its own page; a source opens beside the list. */

const docHref = (id: string) => `/library/${encodeURIComponent(id)}`;

function tabs(docs: number | undefined, sources: number | undefined): PageTab[] {
  return [
    { label: 'Docs', to: '/library/docs', count: docs, target: 'tab:docs' },
    { label: 'External sources', to: '/library/sources', count: sources, target: 'tab:sources' },
  ];
}

/** A doc's folder: its first part names the section, the rest is what it is filed under. */
function folder(path: string): [string, string] {
  const parts = path.split('/').filter(Boolean);
  if (!parts.length) return ['Docs', ''];
  return [parts[0] ?? 'Docs', parts.slice(1).join(' / ')];
}

const newDoc = () => void press('docs.new', {});

export function DocsPage({ search }: { search: DocsSearch }) {
  const docs = useQuery(docsListQuery);
  const sources = useQuery(sourcesQuery);
  const words = search.q?.toLowerCase() ?? '';
  const rows = (docs.data?.docs ?? []).filter((d) => !words || d.title.toLowerCase().includes(words) || d.path.toLowerCase().includes(words));

  const groups = new Map<string, DocRow[]>();
  for (const d of rows) {
    const [name] = folder(d.path);
    groups.set(name, [...(groups.get(name) ?? []), d]);
  }

  let state: PageState = 'ready';
  if (docs.isError) state = 'fault';
  else if (!docs.data) state = 'loading';
  else if (!docs.data.docs.length) state = 'empty';

  return (
    <List
      title="Docs"
      path="/library/docs"
      className="lib"
      tabs={tabs(docs.data?.docs.length, sources.data?.sources.length)}
      actions={
        <>
          <Find label="Search docs" value={search.q} onFind={(q) => void press('docs.list', { q })} />
          <OwnerButton page="docs" />
          <Button variant="primary" icon="plus" onClick={newDoc} data-target="docs:new">
            New doc
          </Button>
        </>
      }
      state={state}
      empty={{ title: 'No docs yet' }}
      onRetry={() => void docs.refetch()}
      sections={
        rows.length ? (
          <>
            <div className="doc-cols" aria-hidden="true">
              <span />
              <span>Doc</span>
              <span>Filed under</span>
              <span>Edited by</span>
              <span>Changed</span>
            </div>
            {[...groups].map(([name, list]) => (
              <Section key={name} name={name} count={list.length}>
                {list.map((d) => (
                  <DocLine key={d.id} d={d} />
                ))}
              </Section>
            ))}
          </>
        ) : (
          <Empty title="Nothing matches" />
        )
      }
    />
  );
}

function DocLine({ d }: { d: DocRow }) {
  const [, under] = folder(d.path);
  const pending = d.id.startsWith('tmp:');
  return (
    <Row className="lrow drow" title={d.title} href={pending ? undefined : docHref(d.id)} target={`doc:${d.id}`}>
      <span className="lead" />
      <span className="doc-name">
        <span className="row-title">{d.title}</span>
        {d.locked && <Pill>Locked</Pill>}
      </span>
      <span className="tie">{under && <span>{under}</span>}</span>
      <Who actor={d.updated_by_id || null} name={d.updated_by_name} />
      <Ago at={d.updated_at} className="row-age" />
    </Row>
  );
}

/* ---------------------------------------------------------------- external sources */

const BAD: Partial<Record<DocSource['status'], string>> = { failed: 'Failed', access_needed: 'Needs access', empty: 'Empty' };

export function SourcesPage({ panel }: { panel?: string }) {
  const docs = useQuery(docsListQuery);
  const sources = useQuery(sourcesQuery);
  const list = sources.data?.sources ?? [];

  let state: PageState = 'ready';
  if (sources.isError) state = 'fault';
  else if (!sources.data) state = 'loading';
  else if (!list.length) state = 'empty';

  return (
    <List
      title="Docs"
      path="/library/sources"
      className="lib"
      tabs={tabs(docs.data?.docs.length, sources.data?.sources.length)}
      actions={<OwnerButton page="docs" />}
      state={state}
      empty={{ title: 'No outside sources yet' }}
      onRetry={() => void sources.refetch()}
      sections={
        <section className="sec">
          {list.map((s) => (
            <Row
              key={s.id}
              className="lrow srow"
              title={s.title}
              href={`/library/sources?panel=${encodeURIComponent(s.id)}`}
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
  const close = () => void press('docs.list', { tab: 'sources' });
  const s = src.data?.source;
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
          <Button variant="quiet" icon="refresh" onClick={() => void press('docs.refreshSource', { source: id })}>
            Read again
          </Button>
        </>
      }
    >
      {src.isError && <Empty title="This source did not load" icon="alert" />}
      {src.data && !src.data.documents.length && <Empty title="No documents yet" />}
      {src.data?.documents.map((d) => (
        <Link key={d.id} className="row lrow frow" to="/library/$doc" params={{ doc: `ext:${d.id}` }} title={d.title}>
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
