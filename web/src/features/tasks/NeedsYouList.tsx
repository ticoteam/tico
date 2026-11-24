import { NeedsYouRows } from './NeedsYouPage';

/** Needs you as Tasks draws it on top: the list itself, drawn as it is. A task opened from it opens with Needs you as
    its list (`data-list`, router.tsx), so an answer that leaves nothing on it opens the next row. `sorting`: Tico is
    sorting it (its rows past the top few wait). */
export function NeedsYouList({ sorting }: { sorting: boolean }) {
  return (
    <div className="ny-list" data-list="">
      <NeedsYouRows sorting={sorting} bare />
    </div>
  );
}
