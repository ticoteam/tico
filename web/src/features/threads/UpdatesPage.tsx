import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useActor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { SectionHead } from '@/kit/SectionHead';
import { VirtualList } from '@/kit/VirtualList';
import { Document, List, type PageTab } from '@/pagekinds';
import { byDay, dayLabel, updateParagraphs, updateTitle, type Update } from './model';
import { Face, press } from './people';
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
  return open ? <UpdateOpen id={open} /> : <UpdatesList tab={tab} />;
}

type Item = { k: 'day'; day: string; n: number } | { k: 'up'; u: Update };

function UpdatesList({ tab }: { tab: UpdateTab }) {
  const list = useInfiniteQuery(updateListQuery(tab));
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const all = list.data?.pages.flatMap((p) => p.updates) ?? [];
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
      title="Updates"
      path="/updates"
      tabs={TABS}
      actions={
        <Button variant="primary" disabled={!unread} onClick={() => void press('updates.markRead', {})}>
          Mark read
        </Button>
      }
      state={state}
      empty={{ title: 'No updates yet' }}
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
              <UpdateRow u={it.u} />
            )
          }
        />
      }
    />
  );
}

function UpdateRow({ u }: { u: Update }) {
  const who = useActor(actorOf(u));
  const title = updateTitle(u);
  return (
    <Row className="up-row" title={title} href={`/updates?open=${encodeURIComponent(u.id)}`} unread={!u.read} target={`update:${u.id}`}>
      <Face actor={actorOf(u)} />
      <span className="row-title">{title}</span>
      <span className="row-pills" />
      <span className="up-who">{who?.name ?? ''}</span>
      <Ago at={u.created_at} className="row-age" />
      <span className="tl-slot" />
    </Row>
  );
}

const CRUMB = [{ label: 'Updates', to: '/updates' }];

function UpdateOpen({ id }: { id: string }) {
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
    <Menu trigger={<IconButton icon="more" label="More" />}>
      <MenuItem label="Mark unread" icon="updates" onSelect={() => void press('updates.markRead', { ids: [u.id], read: false })} />
      <MenuRule />
      <MenuItem label="Archive update" icon="archive" onSelect={() => void press('updates.archive', { ids: [u.id] })} />
    </Menu>
  );
  const parts = u ? updateParagraphs(u) : [];
  const slides = u?.slides;
  return (
    <Document
      title={title}
      path={`/updates?open=${id}`}
      crumb={CRUMB}
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
