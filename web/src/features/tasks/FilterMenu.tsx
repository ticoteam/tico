import { Menu as BaseMenu } from '@base-ui/react/menu';
import type { ReactNode } from 'react';
import { useAction } from '@/actions';
import type { Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { popLayer, popMotion } from '@/kit/Popover';
import type { Filters } from './queries';

/** A second level beside the first, on the side with room (list.css `.anchor>.menu.is-sub`). */
function Sub({ label, children }: { label: string; children: ReactNode }) {
  return (
    <BaseMenu.SubmenuRoot>
      <BaseMenu.SubmenuTrigger render={<a />}>
        <span>{label}</span>
        <Icon name="chevron" className="is-next" />
      </BaseMenu.SubmenuTrigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner className={popLayer} side="left" align="start" sideOffset={6}>
          <BaseMenu.Popup className={`menu is-select ${popMotion}`}>{children}</BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.SubmenuRoot>
  );
}

/** Filter: Owner ›, Tag ›, then Late (list.js filterMenu). Each choice is `tasks.filter`, so it lands in the address. */
export function FilterMenu({ filters, owners, tags }: { filters: Filters; owners: readonly Actor[]; tags: readonly string[] }) {
  const filter = useAction('tasks.filter');
  const on = !!(filters.owner || filters.tag || filters.late);
  return (
    <Menu trigger={<IconButton icon="filter" label="Filter" on={on} data-target="tasks:filter" />}>
      <Sub label="Owner">
        {owners.map((a) => (
          <MenuItem
            key={a.id}
            label={a.name}
            lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} decorative />}
            on={filters.owner === a.id}
            onSelect={() => void filter.run({ owner: filters.owner === a.id ? null : a.id })}
          />
        ))}
      </Sub>
      {tags.length > 0 && (
        <Sub label="Tag">
          {tags.map((t) => (
            <MenuItem key={t} label={t} on={filters.tag === t} onSelect={() => void filter.run({ tag: filters.tag === t ? null : t })} />
          ))}
        </Sub>
      )}
      <MenuRule />
      <MenuItem label="Late" on={!!filters.late} onSelect={() => void filter.run({ late: !filters.late })} />
    </Menu>
  );
}
