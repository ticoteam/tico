import { useInfiniteQuery } from '@tanstack/react-query';
import { useDeferredValue, useState, type MouseEvent } from 'react';
import { Button } from '@/kit/Button';
import { Count } from '@/kit/Count';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Segmented } from '@/kit/Segmented';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { press } from './people';
import { archivedQuery, browseQuery, type BrowseRow } from './queries';

type View = 'open' | 'archived';

/** Find a company or team thread you are not in and join it; or bring back one you archived. */
export function Browse({ onClose }: { onClose: () => void }) {
  const [view, setView] = useState<View>('open');
  const [words, setWords] = useState('');
  const q = useDeferredValue(words.trim());
  const found = useInfiniteQuery(browseQuery(q));
  const archived = useInfiniteQuery({ ...archivedQuery, enabled: view === 'archived' });
  const list = view === 'open' ? found : archived;
  const rows = list.data?.pages.flatMap((p) => p.threads) ?? [];
  const shown = view === 'archived' && q ? rows.filter((t) => t.title.toLowerCase().includes(q.toLowerCase())) : rows;

  const join = async (t: BrowseRow) => {
    if (!(await press('threads.join', { thread: t.id }))) return;
    onClose();
    await press('threads.open', { thread: t.id });
  };
  const back = (t: BrowseRow) => press('threads.unarchive', { thread: t.id });
  // Its name opens it to read before joining (ui thread-rail browse); the link stays real for a new tab.
  const read = (e: MouseEvent, t: BrowseRow) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onClose();
    void press('threads.open', { thread: t.id });
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title="Browse threads">
      <Segmented
        label="Which threads"
        options={[
          { value: 'open', label: 'Threads' },
          { value: 'archived', label: 'Archived' },
        ]}
        value={view}
        onValueChange={setView}
      />
      <Search label="Find a thread" placeholder="Find a thread" value={words} onValueChange={setWords} wide autoFocus />
      {list.isPending ? (
        <SkeletonRows rows={4} />
      ) : list.isError ? (
        <Empty icon="alert" title="Threads did not load">
          <Button onClick={() => list.refetch()}>Try again</Button>
        </Empty>
      ) : shown.length === 0 ? (
        <Empty icon="threads" title={view === 'open' ? 'No threads to join' : 'Nothing archived'} />
      ) : (
        <VirtualList
          className="br-list"
          label={view === 'open' ? 'Threads to join' : 'Archived threads'}
          items={shown}
          getKey={(t) => t.id}
          estimateSize={34}
          onReachEnd={() => list.hasNextPage && !list.isFetchingNextPage && void list.fetchNextPage()}
          renderItem={(t) => (
            <div className="row br-row" title={t.title}>
              <span className="tl-kind">
                <Icon name="threads" />
              </span>
              <a className="row-title" href={`/threads/${encodeURIComponent(t.id)}`} onClick={(e) => read(e, t)}>
                {t.title || 'Thread'}
              </a>
              {t.member_count ? <Count n={t.member_count} label={`${t.member_count} in it`} /> : <span />}
              {view === 'open' ? (
                <Button onClick={() => join(t)}>Join</Button>
              ) : (
                <Button onClick={() => back(t)}>Move back</Button>
              )}
            </div>
          )}
        />
      )}
    </Dialog>
  );
}
