import { useQuery } from '@tanstack/react-query';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { FilterRow } from '@/pagekinds';
import { press } from './parts';
import { filesTeamsQuery } from './queries';
import type { FilesSearch } from './search';

/* Files' one Filter (old library-files.ts filters): Kind ›, Role ›, Agent ›, Written by ›, Linked to ›, Team ›, Updated ›,
   then Pinned and Archived. Each choice is `files.filter`, so it lands in the address; what is on shows as a chip. */

export const KINDS = [
  ['page', 'Pages'],
  ['document', 'Documents'],
  ['image', 'Images'],
  ['data', 'Data'],
  ['media', 'Media'],
  ['other', 'Other'],
] as const;

const ROLES = ['Spec', 'QA report', 'Release report', 'Invoice'] as const;
const DAYS = [
  [7, 'Past week'],
  [30, 'Past month'],
  [365, 'Past year'],
] as const;

type Word = 'kind' | 'role' | 'agent' | 'writer' | 'linked' | 'team';
const WORDS = ['kind', 'role', 'agent', 'writer', 'linked', 'team', 'task', 'days', 'pinned', 'archived'] as const;

/** Whether any filter is on (the words searched for are not a filter). */
export const filtered = (s: FilesSearch) => WORDS.some((k) => s[k] !== undefined);

const filter = (change: Record<string, unknown>) => void press('files.filter', change, 'Not filtered');

export function FilesFilter({ search }: { search: FilesSearch }) {
  const actors = useActors();
  const teams = useQuery(filesTeamsQuery).data ?? [];
  // Choosing what is on takes it off again (the Tasks filter).
  const pick = (key: Word, value: string) => filter({ [key]: search[key] === value ? '' : value });
  const who = (key: Word, label: string, list: readonly Actor[]) =>
    list.length > 0 && (
      <MenuSub label={label}>
        {list.map((a) => (
          <MenuItem
            key={a.id}
            label={a.name}
            lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} decorative />}
            on={search[key] === a.id}
            onSelect={() => pick(key, a.id)}
          />
        ))}
      </MenuSub>
    );
  return (
    <Menu trigger={<IconButton icon="filter" label="Filter" on={filtered(search)} data-target="files:filter" data-phone />}>
      <MenuSub label="Kind">
        {KINDS.map(([k, label]) => (
          <MenuItem key={k} label={label} on={search.kind === k} onSelect={() => pick('kind', k)} />
        ))}
      </MenuSub>
      <MenuSub label="Role">
        {ROLES.map((r) => (
          <MenuItem key={r} label={r} on={search.role === r} onSelect={() => pick('role', r)} />
        ))}
      </MenuSub>
      {who(
        'agent',
        'Agent',
        actors.filter((a) => a.kind === 'agent'),
      )}
      {who('writer', 'Written by', actors)}
      {who('linked', 'Linked to', actors)}
      {teams.length > 0 && (
        <MenuSub label="Team">
          {teams.map((t) => (
            <MenuItem key={t.id} label={t.name} on={search.team === t.id} onSelect={() => pick('team', t.id)} />
          ))}
        </MenuSub>
      )}
      <MenuSub label="Updated">
        {DAYS.map(([d, label]) => (
          <MenuItem key={d} label={label} on={search.days === d} onSelect={() => filter({ days: search.days === d ? 0 : d })} />
        ))}
      </MenuSub>
      <MenuRule />
      <MenuItem label="Pinned" on={!!search.pinned} onSelect={() => filter({ pinned: !search.pinned })} />
      <MenuItem label="Archived" on={!!search.archived} onSelect={() => filter({ archived: !search.archived })} />
    </Menu>
  );
}

/** A chip for each filter that is on, then Clear. */
export function FilesChips({ search }: { search: FilesSearch }) {
  const actors = useActors();
  const teams = useQuery(filesTeamsQuery).data ?? [];
  const name = (id: string) => actors.find((a) => a.id === id)?.name ?? id;
  const off = (key: (typeof WORDS)[number]) => () => filter({ [key]: key === 'days' ? 0 : key === 'pinned' || key === 'archived' ? false : '' });
  const chips: { label: string; onClear: () => void }[] = [];
  if (search.kind) chips.push({ label: KINDS.find(([k]) => k === search.kind)?.[1] ?? search.kind, onClear: off('kind') });
  if (search.role) chips.push({ label: search.role, onClear: off('role') });
  if (search.agent) chips.push({ label: name(search.agent), onClear: off('agent') });
  if (search.writer) chips.push({ label: `Written by ${name(search.writer)}`, onClear: off('writer') });
  if (search.linked) chips.push({ label: `Linked to ${name(search.linked)}`, onClear: off('linked') });
  if (search.team) chips.push({ label: teams.find((t) => t.id === search.team)?.name ?? 'One team', onClear: off('team') });
  if (search.days) chips.push({ label: DAYS.find(([d]) => d === search.days)?.[1] ?? `Past ${search.days} days`, onClear: off('days') });
  if (search.task) chips.push({ label: 'One task', onClear: off('task') });
  if (search.pinned) chips.push({ label: 'Pinned', onClear: off('pinned') });
  if (search.archived) chips.push({ label: 'Archived', onClear: off('archived') });
  return <FilterRow filters={chips} onClearAll={chips.length > 1 ? () => filter({ clear: true }) : undefined} />;
}
