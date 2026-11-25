import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useState } from 'react';
import type { Page } from '@/api/schema';
import { useDirectory } from '@/data';
import { tagsQuery } from '@/features/tasks/routing';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuSub } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { VirtualList } from '@/kit/VirtualList';
import { FilterRow, List } from '@/pagekinds';
import { Find, Foot } from './parts';
import { LibraryPage } from './LibraryPage';
import { pageHref, pageKinds, pagesListQuery, pagesSearchQuery, type PageFilters, type PageKind, type PagesSearch } from './queries';
import './library.css';
import './pages.css';

const KIND: Record<PageKind, string> = { static: 'Static', live: 'Live', app: 'App' };

export function PagesPage({ search }: { search: PagesSearch }) {
  return search.file ? <LibraryPage key={search.file} id={search.file} search={search} /> : <PagesList filters={search} />;
}

function PagesList({ filters }: { filters: PageFilters }) {
  const router = useRouter();
  const searching = !filters.archived && (filters.q?.trim().length ?? 0) > 1;
  const list = useInfiniteQuery({ ...pagesListQuery(filters), enabled: !searching });
  const found = useQuery(pagesSearchQuery(filters));
  const tags = useQuery(tagsQuery).data ?? [];
  const [linked, setLinked] = useState(false);
  const query = searching ? found : list;
  const rows = searching ? found.data ?? [] : list.data?.pages.flatMap((p) => p.pages) ?? [];
  const update = (change: PageFilters) => void router.navigate({ href: pageHref({ ...filters, ...change }), replace: true });
  const directory = useDirectory((d) => d.actors) ?? [];
  const writers = new Map(directory.map((actor) => [actor.id, actor.name]));
  const links = new Map<string, string>();
  for (const page of rows) {
    for (const writer of page.writers ?? []) writers.set(writer.actor, writer.name);
    for (const link of page.links ?? []) links.set(link.target, link.name);
  }
  const chips = [
    ...(filters.writer ? [{ label: writers.get(filters.writer) ?? filters.writer, onClear: () => update({ writer: undefined }) }] : []),
    ...(filters.linked ? [{ label: links.get(filters.linked) ?? filters.linked, onClear: () => update({ linked: undefined }) }] : []),
    ...(filters.kind ? [{ label: KIND[filters.kind], onClear: () => update({ kind: undefined }) }] : []),
    ...(filters.stale ? [{ label: 'Stale', onClear: () => update({ stale: undefined }) }] : []),
    ...(filters.tag ? [{ label: tags.find((t) => t.key === filters.tag)?.label ?? filters.tag, onClear: () => update({ tag: undefined }) }] : []),
    ...(filters.archived ? [{ label: 'Archived', onClear: () => update({ archived: undefined }) }] : []),
  ];
  return (
    <>
      <List
        title="Pages"
        path="/library/pages"
        className="lib"
        search={!filters.archived && <Find label="Search pages" value={filters.q} onFind={(q) => update({ q })} />}
        actions={
          <Menu trigger={<IconButton icon="filter" label="Filter pages" on={chips.length > 0} data-phone />}>
            {writers.size > 0 && (
              <MenuSub label="Writer">
                {[...writers].map(([writer, name]) => <MenuItem key={writer} label={name} on={writer === filters.writer} onSelect={() => update({ writer: writer === filters.writer ? undefined : writer })} />)}
              </MenuSub>
            )}
            <MenuSub label="Linked to">
              {[...links].map(([target, name]) => <MenuItem key={target} label={name} on={target === filters.linked} onSelect={() => update({ linked: target === filters.linked ? undefined : target })} />)}
              <MenuItem label="Enter a link target" onSelect={() => setLinked(true)} />
            </MenuSub>
            <MenuSub label="Kind">
              {pageKinds.map((kind) => <MenuItem key={kind} label={KIND[kind]} on={filters.kind === kind} onSelect={() => update({ kind: filters.kind === kind ? undefined : kind })} />)}
            </MenuSub>
            <MenuItem label="Stale" on={filters.stale} onSelect={() => update({ stale: !filters.stale || undefined })} />
            {tags.length > 0 && (
              <MenuSub label="Tag">
                {tags.map((tag) => <MenuItem key={tag.id} label={tag.label || tag.key} on={filters.tag === tag.key} onSelect={() => update({ tag: filters.tag === tag.key ? undefined : tag.key })} />)}
              </MenuSub>
            )}
          </Menu>
        }
        more={
          <Menu trigger={<IconButton icon="more" label="More" />}>
            <MenuItem label="Templates" on={filters.tag === 'template'} onSelect={() => update({ tag: filters.tag === 'template' ? undefined : 'template', archived: undefined })} />
            <MenuItem label="Show archived" on={filters.archived} onSelect={() => update({ archived: !filters.archived || undefined, q: undefined })} />
          </Menu>
        }
        filter={chips.length > 0 && <FilterRow filters={chips} />}
        state={query.isError ? 'fault' : query.isPending ? 'loading' : 'ready'}
        onRetry={() => void query.refetch()}
        sections={rows.length ? (
          <VirtualList
            className="lib-vl"
            label="Pages"
            items={rows}
            getKey={(row) => row.file_id}
            estimateSize={36}
            renderItem={(row) => <PageLine page={row} href={pageHref({ ...filters, file: row.file_id })} />}
            onReachEnd={() => !searching && list.hasNextPage && !list.isFetchingNextPage && void list.fetchNextPage()}
          />
        ) : !searching && list.hasNextPage ? (
          <Button onClick={() => list.fetchNextPage()}>Load more</Button>
        ) : <Empty title={searching || chips.length ? 'No matching pages' : 'No pages yet'} />}
      />
      {linked && <LinkedFilter value={filters.linked ?? ''} onClose={() => setLinked(false)} onPick={(target) => update({ linked: target })} />}
    </>
  );
}

type PageLineRow = Pick<Page, 'file_id' | 'title' | 'live' | 'has_app' | 'approved' | 'stale_days'>;

function PageLine({ page, href }: { page: PageLineRow; href: string }) {
  return (
    <Row className="lrow page-line" title={page.title} href={href} target={`page:${page.file_id}`}>
      <span className="row-title">{page.title}</span>
      <span className="page-marks">
        {page.has_app && <span className="tag">App</span>}
        {page.live && <span className="tag">Live</span>}
        {page.stale_days !== null && <span className="tag">Stale</span>}
        {page.approved && <span className="tag" title="Approved"><Icon name="check" /><span className="sr-only">Approved</span></span>}
      </span>
    </Row>
  );
}

function LinkedFilter({ value, onClose, onPick }: { value: string; onClose: () => void; onPick: (target: string) => void }) {
  const [target, setTarget] = useState(value);
  const ready = /^(task|bot|human|goal|page|entity):\S+$/.test(target.trim());
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title="Linked to" actions={
      <Foot onClose={onClose} label="Filter" busy={false} ready={ready} onGo={() => { onPick(target.trim()); onClose(); }} />
    }>
      <Field label="Link target" placeholder="task:123" value={target} onValueChange={setTarget} autoFocus />
    </Dialog>
  );
}
