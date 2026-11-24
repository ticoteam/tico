import type { MouseEvent } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { NEEDS_YOU, pressListRow } from '@/lib';
import { NeedsYouRows } from './NeedsYouPage';
import { flowQuery, type FlowItem } from './queries';

// a task's row in the list, as TaskRow names it (`task:<number>`, else its id)
const ROW = 'a[data-target^="task:"]';
const numberOf = (it: FlowItem) => String(it.number ?? it.id);

/** Needs you as Tasks draws it on top: the list itself, drawn as it is. A plain press on one of its tasks marks that
    task opened from Needs you (lib/places), as a board's card does for its pipeline, so an answer that leaves nothing
    on it opens the next row of Needs you. `sorting`: Tico is sorting it (its rows past the top few wait). */
export function NeedsYouList({ sorting }: { sorting: boolean }) {
  const flow = useSuspenseQuery(flowQuery).data;
  const press = (e: MouseEvent) => {
    const a = e.target instanceof Element ? e.target.closest(ROW) : null;
    const n = a?.getAttribute('data-target')?.slice('task:'.length);
    const it = n ? flow.items.find((x) => numberOf(x) === n) : undefined;
    if (it) pressListRow(it.pipeline_id ?? 'all', n!, e, NEEDS_YOU);
  };
  return (
    <div className="ny-list" onClickCapture={press}>
      <NeedsYouRows sorting={sorting} bare />
    </div>
  );
}
