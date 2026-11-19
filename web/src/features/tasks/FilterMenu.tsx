import { useAction } from '@/actions';
import type { Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { IconButton } from '@/kit/IconButton';
import { MenuItem, MenuRule, MenuSub, Menu } from '@/kit/Menu';
import { isFiltered, type Due, type Filters } from './queries';

/** Due's choices, as the menu and the chip say them (backend/task_match.py DUE). */
export const DUE_NAMES: Record<Due, string> = { overdue: 'Overdue', week: 'Due this week', any: 'Has a due date' };

type Who = 'owner' | 'waiting' | 'asked';

/**
 * Filter: Owner ›, Waiting on ›, Asked by ›, Tag ›, Due ›, then Late (list.js filterMenu). Each choice is `tasks.filter`,
 * so it lands in the address.
 */
export function FilterMenu({ phone, ...props }: FilterProps & { phone?: boolean }) {
  return (
    <Menu
      trigger={
        <IconButton icon="filter" label="Filter" on={isFiltered({ ...props.filters, q: undefined })} data-target="tasks:filter" data-phone={phone || undefined} />
      }
    >
      <FilterItems {...props} />
    </Menu>
  );
}

interface FilterProps {
  filters: Filters;
  actors: readonly Actor[];
  tags: readonly string[];
}

/** The filter's rows, for a menu of their own or, on a phone, a page "⋯"'s Filter ›. */
export function FilterItems({ filters, actors, tags }: FilterProps) {
  const filter = useAction('tasks.filter');
  const people = (key: Who, label: string) => (
    <MenuSub label={label}>
      {actors.map((a) => (
        <MenuItem
          key={a.id}
          label={a.name}
          lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} decorative />}
          on={filters[key] === a.id}
          onSelect={() => void filter.run({ [key]: filters[key] === a.id ? null : a.id })}
        />
      ))}
    </MenuSub>
  );
  return (
    <>
      {people('owner', 'Owner')}
      {people('waiting', 'Waiting on')}
      {people('asked', 'Asked by')}
      {tags.length > 0 && (
        <MenuSub label="Tag">
          {tags.map((t) => (
            <MenuItem key={t} label={t} on={filters.tag === t} onSelect={() => void filter.run({ tag: filters.tag === t ? null : t })} />
          ))}
        </MenuSub>
      )}
      <MenuSub label="Due">
        {(Object.keys(DUE_NAMES) as Due[]).map((d) => (
          <MenuItem key={d} label={DUE_NAMES[d]} on={filters.due === d} onSelect={() => void filter.run({ due: filters.due === d ? null : d })} />
        ))}
      </MenuSub>
      <MenuRule />
      <MenuItem label="Late" on={!!filters.late} onSelect={() => void filter.run({ late: !filters.late })} />
    </>
  );
}
