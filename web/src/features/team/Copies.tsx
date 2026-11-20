import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { meQuery, useActors, type Actor } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { ValueButton } from '@/kit/ValueButton';
import { Section } from '@/pagekinds';
import { bareId } from './model';
import { ConfirmDialog, MateRow, PickLine } from './parts';
import { agentsQuery, branchesQuery, computersQuery, profilesQuery, type Branch, type Branches } from './queries';

/* A copy of an agent follows its original: it keeps its own status, profile, computer and chat, and takes everything
   else from the original as it changes (mates.js copyPage; ui bot-branches.ts). */

function useCopies(slug: string) {
  const { data: me } = useQuery(meQuery);
  const actors = useActors();
  const roster = useQuery(agentsQuery).data ?? [];
  const b = useQuery(branchesQuery(slug)).data;
  const original = b ? roster.find((r) => r.id === b.original) : undefined;
  const mineId = me ? bareId(me.id) : undefined;
  const ownerName = (owner?: string | null) => actors.find((a) => a.kind === 'person' && bareId(a.id) === owner)?.name ?? 'Someone';
  const label = (x: Branch) => (x.owner_id && x.owner_id === mineId ? 'Your copy' : `${ownerName(x.owner_id)}'s copy`);
  const copies = b?.branches ?? [];
  const mine = copies.find((x) => x.owner_id === mineId);
  // A person makes a copy of an original that allows them, not their own agent, and only one.
  const canMake = !!b?.shared && !!mineId && original?.owner_id !== mineId && !mine;
  return { b, original, copies, mine, label, canMake };
}

/** Whether you may make your own copy of this agent: ⋯ offers it while the agent has no copies yet. */
export function useCanMakeCopy(slug: string): boolean {
  const { canMake, copies } = useCopies(slug);
  return canMake && copies.length === 0;
}

/** The pick beside the name: Original, then each person's copy, then Make my copy. Only once a copy exists (t-bot has
    none; t-copy-pick); before that, Make my copy is in ⋯. `making` opens the Make my copy dialog from there. */
export function CopyPick({ slug, botId, making, onMaking }: { slug: string; botId: string; making: boolean; onMaking: (open: boolean) => void }) {
  const open = useAction('team.open');
  const { b, original, copies, label, canMake } = useCopies(slug);
  if (!b) return null;
  const here = b.original === botId ? 'Original' : (copies.find((x) => x.id === botId) && label(copies.find((x) => x.id === botId)!)) || 'Original';
  return (
    <>
      {copies.length > 0 && (
        <Menu
          align="start"
          trigger={
            <ValueButton look="scope" className="copy-pick" label={`Copy: ${here}`}>
              {here}
            </ValueButton>
          }
        >
          <MenuItem label="Original" on={b.original === botId} onSelect={() => original && void open.run({ who: original.slug })} />
          {copies.map((x) => (
            // A copy opens on Settings: what is its own to change, then what follows the original (t-copy).
            <MenuItem key={x.id} label={label(x)} on={x.id === botId} disabled={x.status === 'archived'} onSelect={() => void open.run({ who: x.slug, tab: 'settings' })} />
          ))}
          {canMake && (
            <>
              <MenuRule />
              <MenuItem label="Make my copy" icon="plus" onSelect={() => onMaking(true)} />
            </>
          )}
        </Menu>
      )}
      {original && making && <MakeCopy open onOpenChange={onMaking} source={original.slug} b={b} />}
    </>
  );
}

/** Make my copy: on one of my computers that runs neither the original nor a copy of it; planned with none. */
function MakeCopy({ open, onOpenChange, source, b }: { open: boolean; onOpenChange: (o: boolean) => void; source: string; b: Branches }) {
  const { data: me } = useQuery(meQuery);
  const computers = useQuery(computersQuery).data ?? [];
  const make = useAction('agents.makeCopy');
  const family = new Set([b.original, ...(b.branches ?? []).map((x) => x.id)]);
  const mine = computers.filter((c) => !!me && c.owner_id === bareId(me.id));
  const free = mine.filter((c) => !(c.bots ?? []).some((bot) => family.has(bot)));
  // The menu opens this dialog, so the first free computer is the pick until the person chooses another.
  const [chosen, setPick] = useState('');
  const pick = free.some((c) => c.id === chosen) ? chosen : (free[0]?.id ?? '');
  const planned = !free.length;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Make my copy"
      description={
        planned
          ? `${mine.length ? 'Your computers already run it.' : 'You have no computer yet.'} It waits until you add one in Settings, Computers.`
          : undefined
      }
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            busy={make.pending}
            disabled={!planned && !pick}
            onClick={() => void make.run({ slug: source, ...(planned ? {} : { computer: pick }) }).then((r) => r.ok && onOpenChange(false))}
          >
            Make copy
          </Button>
        </>
      }
    >
      {!planned && <Select label="Computer" options={free.map((c) => ({ value: c.id, label: c.label }))} value={pick || null} onValueChange={setPick} />}
    </Dialog>
  );
}

const STATUS = [
  { value: 'planned', label: 'Setting up' },
  { value: 'active', label: 'Active' },
  { value: 'paused', label: 'Paused' },
] as const;

/** A copy's own lines on its Settings: what it follows, its status and its profile (mates.js copyDesk Info). */
export function CopyFacts({ slug, botId, manage }: { slug: string; botId: string; manage: boolean }) {
  const { b, original, copies } = useCopies(slug);
  const profiles = useQuery({ ...profilesQuery, enabled: !!b && b.original !== botId }).data ?? [];
  const setProfile = useAction('agents.setProfile');
  const [dropping, setDropping] = useState(false);
  const [wanted, setWanted] = useState('');
  const me = copies.find((x) => x.id === botId);
  if (!b || !me || b.original === botId) return null;
  const prof = me.profile?.name ?? '';
  const send = async (profile: string, dropRules?: boolean) => {
    const r = await setProfile.run({ slug, profile, ...(dropRules ? { dropRules } : {}) });
    if (!r.ok && r.error === 'conflict') {
      setWanted(profile);
      setDropping(true);
    }
  };
  return (
    <>
      <LabelValue label="Follows">
        {original ? (
          <Link className="fact-link" href={`/team/${encodeURIComponent(original.slug)}`}>
            {original.display_name ?? original.slug}
            <Icon name="arrow" />
          </Link>
        ) : (
          'The original'
        )}
      </LabelValue>
      <PickLine
        label="Status"
        options={STATUS}
        value={me.status ?? 'active'}
        shown="Active"
        action={manage ? 'agents.setStatus' : undefined}
        input={(v) => ({ slug, status: v })}
      />
      <PickLine
        label="Profile"
        options={profiles.map((p) => ({ value: p.name, label: p.label }))}
        value={prof}
        shown={me.profile?.summary || me.profile?.label || 'None'}
        onPick={manage ? (p) => void send(p) : undefined}
      />
      <ConfirmDialog
        open={dropping}
        onOpenChange={setDropping}
        title="Leave Custom?"
        text="Its own wake rules from the Custom profile are deleted."
        verb="Leave Custom"
        onConfirm={() => void send(wanted, true)}
      />
    </>
  );
}

/** On the original: each person's copy (mates.js copyDesk, Copies). */
export function CopiesSection({ slug, botId, actors }: { slug: string; botId: string; actors: Actor[] }) {
  const { b, copies, label } = useCopies(slug);
  if (!b || b.original !== botId || !copies.length) return null;
  const rows = copies.flatMap((x) => {
    const a = actors.find((y) => y.slug === x.slug);
    return a ? [{ ...a, name: label(x) }] : [];
  });
  if (!rows.length) return null;
  return (
    <Section name="Copies" count={rows.length}>
      {rows.map((a) => (
        <MateRow key={a.id} actor={a} />
      ))}
    </Section>
  );
}
