import { useQuery } from '@tanstack/react-query';
import { meQuery } from '@/data';
import { useFollow } from '@/live/follow';
import { List } from '@/pagekinds';
import { NeedsYouPage, NeedsYouShelf, NeedsYouSnoozedAsks } from './NeedsYouPage';
import './tasks.css';

/** The views Needs you's section on Tasks links to. */
export type NeedsYouView = 'all' | 'snoozed' | 'done';

const NAMES: Record<NeedsYouView, string> = { all: 'All', snoozed: 'Snoozed', done: 'Done' };

/** Needs you's other views, off its section on Tasks: All (every task anyone may claim, the old page's All), and the
    person's Snoozed (with the asks they snoozed, to Unsnooze) and Done shelves. */
export function NeedsYouViews({ view }: { view: NeedsYouView }) {
  if (view === 'all') return <NeedsYouPage />;
  return <Shelf shelf={view} />;
}

function Shelf({ shelf }: { shelf: 'snoozed' | 'done' }) {
  useFollow({ allTasks: true });
  const me = useQuery(meQuery).data?.id;
  return (
    <List
      title={NAMES[shelf]}
      path="/needs-you"
      className="tasks-page"
      state={me ? 'ready' : 'loading'}
      sections={
        me ? (
          <>
            {shelf === 'snoozed' && <NeedsYouSnoozedAsks />}
            <NeedsYouShelf owner={me} shelf={shelf} />
          </>
        ) : null
      }
    />
  );
}

/** The quiet links in Needs you's head on Tasks: All · Snoozed · Done. */
export const NEEDS_YOU_VIEWS: readonly { view: NeedsYouView; name: string }[] = (['all', 'snoozed', 'done'] as const).map((view) => ({ view, name: NAMES[view] }));
