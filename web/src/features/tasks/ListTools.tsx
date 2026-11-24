import { useAction } from '@/actions';
import { MenuItem, MenuSub } from '@/kit/Menu';
import { FilterItems, SortItems } from './FilterMenu';
import { isFiltered, type Filters } from './queries';
import { Chips, TaskFind } from './TaskFind';

/* The list beside an open task: what its one tools button opens, and the line under its head. Loaded by itself
   (routing loadListTools) the first time the tools open or the list has a search or a filter, never with the board. */

/** The tools menu: Search (the field under the head; again, it goes, with its words), Filter › and Sort ›, the
    board's own rows. They change the list's search (`tasks.filter`, `tasks.sort` while the list shows). */
export function ListToolItems({ search, finding, onFind }: { search: Filters; finding: boolean; onFind: (on: boolean) => void }) {
  const filter = useAction('tasks.filter');
  const open = finding || !!search.q;
  return (
    <>
      <MenuItem
        icon="search"
        label="Search"
        on={open}
        onSelect={() => {
          onFind(!open);
          if (open && search.q) void filter.run({ q: null });
        }}
      />
      <MenuSub icon="filter" label="Filter">
        <FilterItems filters={search} />
      </MenuSub>
      <MenuSub icon="sort" label="Sort">
        <SortItems sort={search.sort} />
      </MenuSub>
    </>
  );
}

/** Under the head: the search field while it is open or has words, then a chip for each filter that is set. */
export function ListFind({ search, finding }: { search: Filters; finding: boolean }) {
  return (
    <>
      {finding || search.q ? (
        <div className="li-find">
          <TaskFind search={search} focus />
        </div>
      ) : null}
      {isFiltered({ ...search, q: undefined }) ? <Chips search={search} /> : null}
    </>
  );
}
