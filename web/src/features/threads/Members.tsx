import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Combobox } from '@/kit/Combobox';
import { Dialog } from '@/kit/Dialog';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Pill } from '@/kit/Pill';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import type { Thread } from './model';
import { Face, press, useMe, useNameOf } from './people';
import { membersQuery, type MemberRow } from './queries';

const bare = (actor: string) => actor.slice(actor.indexOf(':') + 1);
/** Company and team threads take agents only: their people are everyone who can read them. */
const agentsOnly = (t: Thread) => t.audience === 'company' || t.audience === 'team';

/** Who is in a thread: each one's face and name, the owner marked, Remove for a manager, Leave for yourself. */
export function Members({ thread: t, onClose, onAdd }: { thread: Thread; onClose: () => void; onAdd: () => void }) {
  const q = useInfiniteQuery(membersQuery(t.id));
  const me = useMe();
  const nameOf = useNameOf();
  const rows = q.data?.pages.flatMap((p) => p.members) ?? [];
  const manage = t.me?.can_manage === true;
  const name = (m: MemberRow) => (m.actor === me ? 'You' : (nameOf(m.actor) ?? bare(m.actor)));
  const remove = (m: MemberRow) => void press('threads.removePerson', { thread: t.id, actor: m.actor });
  const leave = async (m: MemberRow) => {
    if (await press('threads.removePerson', { thread: t.id, actor: m.actor })) onClose();
  };

  const row = (m: MemberRow) => (
    <div className="row mb-row" title={name(m)}>
      <Face actor={m.actor} />
      <span className="row-title">{name(m)}</span>
      {m.role === 'owner' ? <Pill>Owner</Pill> : <span />}
      {m.role === 'owner' ? (
        <span />
      ) : m.actor === me ? (
        <Button onClick={() => void leave(m)}>Leave</Button>
      ) : manage ? (
        <IconButton icon="close" label={`Remove ${name(m)}`} onClick={() => remove(m)} />
      ) : (
        <span />
      )}
    </div>
  );

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="People"
      description={agentsOnly(t) ? 'Everyone who can read it is in it. Add agents here.' : undefined}
      actions={
        <>
          {t.me?.can_post !== false && (
            <Button icon="plus" onClick={onAdd}>
              {agentsOnly(t) ? 'Add agent' : 'Add people'}
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      {q.isPending ? (
        <SkeletonRows rows={3} />
      ) : q.isError ? (
        <p className="mb-note">
          Couldn't load who is in it.{' '}
          <Button variant="quiet" onClick={() => void q.refetch()}>
            Try again
          </Button>
        </p>
      ) : (
        <VirtualList
          className="mb-list"
          label="People in this thread"
          items={rows}
          getKey={(m) => m.actor}
          estimateSize={34}
          renderItem={row}
          onReachEnd={() => q.hasNextPage && !q.isFetchingNextPage && void q.fetchNextPage()}
        />
      )}
    </Dialog>
  );
}

/** Pick teammates (agents only on a company or team thread) and add them. */
export function AddPeople({ thread: t, have, onClose }: { thread: Thread; have: Set<string>; onClose: () => void }) {
  const actors = useActors();
  const [picked, setPicked] = useState<Actor[]>([]);
  const [busy, setBusy] = useState(false);
  const agents = agentsOnly(t);
  const left = actors.filter((a) => (!agents || a.kind === 'agent') && !have.has(a.id) && !picked.some((p) => p.id === a.id));
  const add = async () => {
    setBusy(true);
    const ok = await press('threads.addPeople', { thread: t.id, actors: picked.map((a) => a.id) });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={agents ? 'Add an agent' : 'Add people'}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!picked.length} busy={busy} onClick={() => void add()}>
            Add
          </Button>
        </>
      }
    >
      <Combobox
        label={agents ? 'Agent' : 'Teammate'}
        labelHidden
        placeholder={agents ? 'Name an agent' : 'Name a teammate'}
        items={left.map((a) => ({ value: a.id, label: a.name, lead: <Avatar kind={a.kind} name={a.name} hue={a.hue} size={16} decorative /> }))}
        value={null}
        onValueChange={(v) => {
          const a = actors.find((x) => x.id === v);
          if (a) setPicked([...picked, a]);
        }}
      />
      {picked.length > 0 && (
        <div className="to-list">
          {picked.map((a) => (
            <span key={a.id} className="to-chip">
              <Avatar kind={a.kind} name={a.name} hue={a.hue} size={16} decorative />
              {a.name}
              <button type="button" aria-label={`Remove ${a.name}`} onClick={() => setPicked(picked.filter((p) => p.id !== a.id))}>
                <Icon name="close" />
              </button>
            </span>
          ))}
        </div>
      )}
    </Dialog>
  );
}
