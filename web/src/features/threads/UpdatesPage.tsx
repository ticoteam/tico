import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { useActor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { OwnerButton } from '@/kit/OwnerButton';
import { Row } from '@/kit/Row';
import { SectionHead } from '@/kit/SectionHead';
import { VirtualList } from '@/kit/VirtualList';
import { chatAddress } from '@/lib';
import { Document, List, PageMore, type PageTab } from '@/pagekinds';
import { byDay, dayLabel, updateParagraphs, updateTitle, type Update } from './model';
import { Face, press, useMe } from './people';
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
  const list = useInfiniteQuery(updateListQuery(tab));
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const archive = tab === 'archive';
  // An update archived or moved back stays in the cache flagged until the next read; each list shows its own side.
  const all = (list.data?.pages.flatMap((p) => p.updates) ?? []).filter((u) => !!u.archived === archive);
  const unread = all.filter((u) => !u.read).length;
  const items: Item[] = byDay(all).flatMap((g) => [
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
  const state = list.isPending ? 'loading' : list.isError ? 'fault' : all.length === 0 ? 'empty' : 'ready';
  return (
    <List
      title={archive ? 'Archive' : 'Updates'}
      path={pathOf(tab)}
      crumb={archive ? ARCHIVE_CRUMB : undefined}
      tabs={archive ? undefined : TABS}
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
      state={state}
      empty={{ title: archive ? 'Nothing archived' : 'No updates yet' }}
      onRetry={() => void list.refetch()}
      sections={
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
  const order = (d = list.data) => byDay(d?.pages.flatMap((p) => p.updates) ?? []).flatMap((g) => g.rows);
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
  const { data: u, isPending, isError, refetch } = useQuery(updateQuery(id));
  const who = useActor(u ? actorOf(u) : undefined);
  const marked = useRef<string | null>(null);
  useEffect(() => {
    // Opening an update reads it, as the old app does; once per update shown.
    if (!u || u.read || marked.current === u.id) return;
    marked.current = u.id;
    void press('updates.markRead', { ids: [u.id] });
  }, [u]);
  const title = u ? updateTitle(u) : 'Update';
  const more = u && (
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
  const parts = u ? updateParagraphs(u) : [];
  const slides = u?.slides;
  return (
    <Document
      title={title}
      path={`${pathOf(tab)}?open=${id}`}
      crumb={tab === 'archive' ? [...ARCHIVE_CRUMB, { label: 'Archive', to: pathOf(tab) }] : [{ label: 'Updates', to: pathOf(tab) }]}
      actions={
        <>
          {u && <Writer actor={actorOf(u)} />}
          <Steps id={id} tab={tab} />
        </>
      }
      more={more}
      state={isPending ? 'loading' : isError ? 'fault' : 'ready'}
      onRetry={() => void refetch()}
      body={
        u && (
          <>
            <div className="read-by">
              <Face actor={actorOf(u)} />
              <b>{who?.name ?? ''}</b>
              <span className="grow" />
              <Ago at={u.created_at} className="row-age" />
            </div>
            {parts[0] && <p className="read-lead">{parts[0]}</p>}
            {parts.slice(1).map((p, i) => (
              <p key={i}>{p}</p>
            ))}
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
        )
      }
    />
  );
}

/** The writer's face in the head: it opens their chat beside the update (the mock's owner; the old reply box's place). */
function Writer({ actor }: { actor: string }) {
  const who = useActor(actor);
  const me = useMe();
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps `?open=`.
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      q.set('chat', chatAddress(actor, who?.kind));
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (actor === me) return null;
  const kind = who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person');
  return <OwnerButton name={who?.name ?? 'the writer'} kind={kind} hue={who?.hue} live={who?.working} href={href} />;
}
