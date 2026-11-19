import { useInfiniteQuery, useSuspenseInfiniteQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { stamp } from '@/conversation';
import { useActor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Markdown } from '@/kit/Markdown';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { SectionHead } from '@/kit/SectionHead';
import { VirtualList } from '@/kit/VirtualList';
import { Document, List, PageMore, type PageTab } from '@/pagekinds';
import { byDay, dayLabel, updateRest, updateTitle, type Update } from './model';
import { ChatWith, Face, press } from './people';
import { updateListQuery, updateQuery, type UpdateTab } from './queries';
import './threads.css';

const TABS: PageTab[] = [
  { label: 'All', to: '/updates', target: 'tab:updates-all' },
  { label: 'Daily', to: '/updates/daily', target: 'tab:updates-daily' },
  { label: 'Weekly', to: '/updates/weekly', target: 'tab:updates-weekly' },
];

/** `bot` is an agent's slug or human:<id>. */
const actorOf = (u: Update) => (u.bot.includes(':') ? u.bot : `bot:${u.bot}`);

export interface UpdatesPageProps {
  tab?: UpdateTab;
  /** An update open as its own page. */
  open?: string;
}

/** Updates: what each teammate reports, a section a day, newest first. An update opens as a page to read. */
export function UpdatesPage({ tab = 'all', open }: UpdatesPageProps) {
  return open ? <UpdateOpen id={open} tab={tab} /> : <UpdatesList tab={tab} />;
}

const ARCHIVE_CRUMB = [{ label: 'Updates', to: '/updates' }] as const;

const pathOf = (tab: UpdateTab) => (tab === 'all' ? '/updates' : `/updates/${tab}`);
/** The tab an update opens from, for the action's input (All has none). */
const tabInput = (tab: UpdateTab) => (tab === 'all' ? {} : { tab });

type Item = { k: 'day'; day: string; n: number } | { k: 'up'; u: Update };

function UpdatesList({ tab }: { tab: UpdateTab }) {
  const list = useSuspenseInfiniteQuery(updateListQuery(tab));
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const [words, setWords] = useState('');
  const archive = tab === 'archive';
  const find = words.trim().toLowerCase();
  // An update archived or moved back stays in the cache flagged until the next read; each list shows its own side.
  const loaded = list.data.pages.flatMap((p) => p.updates).filter((u) => !!u.archived === archive);
  const all = find ? loaded.filter((u) => (u.body ?? '').toLowerCase().includes(find)) : loaded;
  const noMatch = !!find && all.length === 0;
  const nextBefore = list.data.pages.at(-1)?.next_before;
  const { fetchNextPage, isFetching, isFetchNextPageError } = list;
  useEffect(() => {
    if (noMatch && nextBefore && !isFetching && !isFetchNextPageError) void fetchNextPage({ cancelRefetch: false });
  }, [noMatch, nextBefore, isFetching, isFetchNextPageError, fetchNextPage]);
  const searchingOlder = noMatch && !!nextBefore;
  const searchState = isFetchNextPageError && !isFetching ? 'fault' : 'loading';
  const listState = loaded.length || find ? 'ready' : 'empty';
  const unread = loaded.filter((u) => !u.read).length;
  // Weekly reads a section a week; the rest a section a day.
  const items: Item[] = byDay(all, tab === 'weekly').flatMap((g) => [
    { k: 'day' as const, day: g.day, n: g.rows.length },
    ...(folded.has(g.day) ? [] : g.rows.map((u) => ({ k: 'up' as const, u }))),
  ]);
  const fold = (day: string) => {
    const next = new Set(folded);
    if (!next.delete(day)) next.add(day);
    setFolded(next);
  };
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  return (
    <List
      title={archive ? 'Archive' : 'Updates'}
      path={pathOf(tab)}
      crumb={archive ? ARCHIVE_CRUMB : undefined}
      tabs={archive ? undefined : TABS}
      search={<Search label="Find an update" placeholder="Search" value={words} onValueChange={setWords} />}
      actions={
        archive ? undefined : (
          <Button variant="primary" disabled={!unread} onClick={() => void press('updates.markRead', {})}>
            Mark read
          </Button>
        )
      }
      more={
        archive ? undefined : (
          <PageMore>
            <MenuItem label="Archive" icon="archive" onSelect={() => void press('updates.openArchive', {})} />
          </PageMore>
        )
      }
      state={searchingOlder ? searchState : listState}
      empty={{ title: archive ? 'Nothing archived' : 'No updates yet' }}
      onRetry={searchingOlder ? () => void fetchNextPage({ cancelRefetch: false }) : undefined}
      sections={
        noMatch ? (
          <Empty icon="search" title="No matches" />
        ) : (
        <VirtualList
          className="up-vl"
          label="Updates"
          role="feed"
          items={items}
          getKey={(it) => (it.k === 'day' ? `day:${it.day}` : it.u.id)}
          estimateSize={35}
          measure
          onReachEnd={more}
          renderItem={(it) =>
            it.k === 'day' ? (
              <SectionHead name={dayLabel(it.day)} count={it.n} open={!folded.has(it.day)} onToggle={() => fold(it.day)} />
            ) : (
              <UpdateRow u={it.u} tab={tab} />
            )
          }
        />
        )
      }
    />
  );
}

function UpdateRow({ u, tab }: { u: Update; tab: UpdateTab }) {
  const who = useActor(actorOf(u));
  const title = updateTitle(u);
  return (
    <Row className="up-row" title={title} href={`${pathOf(tab)}?open=${encodeURIComponent(u.id)}`} unread={!u.read} target={`update:${u.id}`}>
      <Face actor={actorOf(u)} />
      <span className="row-title">{title}</span>
      <span className="row-pills" />
      <span className="up-who">{who?.name ?? ''}</span>
      <Ago at={u.created_at} className="row-age" />
      <span className="tl-slot" />
    </Row>
  );
}

/** Newer and Older: the next update in the list it was opened from, reading the next page when the list ends. */
function Steps({ id, tab }: { id: string; tab: UpdateTab }) {
  const list = useInfiniteQuery(updateListQuery(tab));
  const order = (d = list.data) => byDay(d?.pages.flatMap((p) => p.updates) ?? [], tab === 'weekly').flatMap((g) => g.rows);
  const rows = order();
  const i = rows.findIndex((u) => u.id === id);
  const go = (u: Update | undefined) => u && void press('updates.open', { update: u.id, ...tabInput(tab) });
  const older = async () => {
    if (rows[i + 1]) return go(rows[i + 1]);
    const r = await list.fetchNextPage();
    go(order(r.data)[i + 1]);
  };
  return (
    <>
      <IconButton icon="chevdown" className="flip" label="Newer" disabled={i <= 0} onClick={() => go(rows[i - 1])} />
      <IconButton icon="chevdown" label="Older" disabled={i < 0 || (!rows[i + 1] && !list.hasNextPage)} onClick={() => void older()} />
    </>
  );
}

function UpdateOpen({ id, tab }: { id: string; tab: UpdateTab }) {
  const u = useSuspenseQuery(updateQuery(id)).data;
  const who = useActor(actorOf(u));
  const marked = useRef<string | null>(null);
  useEffect(() => {
    // Opening an update reads it, as the old app does; once per update shown.
    if (u.read || marked.current === u.id) return;
    marked.current = u.id;
    void press('updates.markRead', { ids: [u.id] });
  }, [u]);
  const title = updateTitle(u);
  const more = (
    <PageMore>
      <MenuItem label="Mark unread" icon="updates" onSelect={() => void press('updates.markRead', { ids: [u.id], read: false })} />
      <MenuRule />
      {u.archived ? (
        <MenuItem label="Move back to Updates" icon="updates" onSelect={() => void press('updates.archive', { ids: [u.id], archived: false })} />
      ) : (
        <MenuItem label="Archive update" icon="archive" onSelect={() => void press('updates.archive', { ids: [u.id] })} />
      )}
    </PageMore>
  );
  const rest = updateRest(u);
  const slides = u.slides;
  return (
    <Document
      title={title}
      path={`${pathOf(tab)}?open=${id}`}
      crumb={tab === 'archive' ? [...ARCHIVE_CRUMB, { label: 'Archive', to: pathOf(tab) }] : [{ label: 'Updates', to: pathOf(tab) }]}
      actions={
        <>
          <ChatWith actor={actorOf(u)} />
          <Steps id={id} tab={tab} />
        </>
      }
      more={more}
      body={
        <>
          <div className="read-by">
            <Face actor={actorOf(u)} />
            <b>{who?.name ?? ''}</b>
            <span className="grow" />
            <time className="row-age" dateTime={u.created_at}>
              {stamp(u.created_at)}
            </time>
          </div>
          {rest && <Markdown text={rest} />}
          {slides &&
            (['done', 'focus', 'blockers'] as const).map(
              (k) =>
                slides[k] && (
                  <section key={k}>
                    <h2>{k === 'done' ? 'Done' : k === 'focus' ? 'Next' : 'Blocked'}</h2>
                    <p>{slides[k]}</p>
                  </section>
                ),
            )}
        </>
      }
    />
  );
}
