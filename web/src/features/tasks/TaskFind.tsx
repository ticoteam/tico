import { useAction } from '@/actions';
import { useActor } from '@/data';
import { SettledSearch } from '@/kit/Field';
import { useCards } from '@/kit/Popover';
import { FilterRow, type FilterChip } from '@/pagekinds';
import { DUE_NAMES } from './FilterMenu';
import type { Filters } from './queries';

/* The search field and the filter chips, shared by a pipeline's board, the Roadmap and the list beside an open task
   (ListTools), in a file of their own so that list loads them without the board. */

/** The head's search (the page kind's `search` slot): the field, as the kit draws it; on a phone it has its own line
    under the head, opened from "⋯"; beside an open task, from the list's tools (`focus`). Typing narrows the board after
    a short pause, not on every key. */
export function TaskFind({ search, focus }: { search: Filters; focus?: boolean }) {
  const filter = useAction('tasks.filter');
  const cards = useCards();
  return (
    <SettledSearch
      label="Search tasks"
      placeholder="Search"
      value={search.q ?? ''}
      onSettle={(q) => void filter.run({ q: q || null })}
      autoFocus={(focus ?? cards) && !search.q}
    />
  );
}

/** The filters that are set, one chip each, then Clear. */
export function Chips({ search }: { search: Filters }) {
  const filter = useAction('tasks.filter');
  const owner = useActor(search.owner)?.name;
  const waiting = useActor(search.waiting)?.name;
  const asked = useActor(search.asked)?.name;
  const chips: FilterChip[] = [];
  if (search.owner) chips.push({ label: owner ?? 'Owner', onClear: () => void filter.run({ owner: null }) });
  if (search.waiting) chips.push({ label: `Waiting on ${waiting ?? 'someone'}`, onClear: () => void filter.run({ waiting: null }) });
  if (search.asked) chips.push({ label: `Asked by ${asked ?? 'someone'}`, onClear: () => void filter.run({ asked: null }) });
  if (search.tag) chips.push({ label: search.tag, onClear: () => void filter.run({ tag: null }) });
  if (search.due) chips.push({ label: DUE_NAMES[search.due], onClear: () => void filter.run({ due: null }) });
  if (search.late) chips.push({ label: 'Late', onClear: () => void filter.run({ late: null }) });
  return (
    <FilterRow
      filters={chips}
      onClearAll={() => void filter.run({ owner: null, waiting: null, asked: null, tag: null, due: null, late: null, q: null })}
    />
  );
}
