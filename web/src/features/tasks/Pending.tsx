import { useLocation, useSearch } from '@tanstack/react-router';
import { Board, List } from '@/pagekinds';
import { usePref } from '@/prefs';
import { VIEW_PREF } from './search';

/** While a pipeline's tasks are read: the page in the view it opens in, titled Tasks, with its bars (list.js loading:
    the board's three columns of cards, or the list's rows). Tico and the menu are already there. */
export function TasksPending() {
  const path = useLocation({ select: (l) => l.pathname });
  const pipeline = decodeURIComponent(path.split('/')[2] ?? '');
  const { view } = useSearch({ strict: false }) as { view?: string };
  const [saved] = usePref<'board' | 'list'>(`${VIEW_PREF}:${pipeline}`, 'board');
  const board = (view ?? saved) === 'board';
  return board ? (
    <Board title="Tasks" path={path} state="loading" columns={null} className="tasks-page" />
  ) : (
    <List title="Tasks" path={path} state="loading" sections={null} className="tasks-page" />
  );
}

/** While Flow is read: Flow's own title, Tasks, over the list's bars. */
export function FlowPending() {
  return <List title="Tasks" path="/flow" state="loading" sections={null} className="tasks-page" />;
}
