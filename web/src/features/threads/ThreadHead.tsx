import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Combobox } from '@/kit/Combobox';
import { Count } from '@/kit/Count';
import { Dialog } from '@/kit/Dialog';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { ItemHead } from '@/pagekinds';
import { isDm, isMuted, othersOf } from './model';
import { Face, press, useMe, useTitle } from './people';
import { threadQuery } from './queries';

const FACES = 6;

/** The open thread's head: its name (renamed in place), who is in it, and "⋯". */
export function ThreadHead({ id }: { id: string }) {
  const { data: t } = useQuery(threadQuery(id));
  const me = useMe();
  const title = useTitle(t) || 'Thread';
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const people = t ? othersOf(t, me) : [];
  const muted = isMuted(t);
  const manage = t?.me?.can_manage !== false;
  const other = t && isDm(t) ? people[0] : undefined;

  const more = (
    <Menu trigger={<IconButton icon="more" label="More" />}>
      <MenuItem label="Rename" icon="edit" disabled={!manage} onSelect={() => setEditing(true)} />
      <MenuItem label="Add people" icon="people" onSelect={() => setAdding(true)} />
      <MenuItem label={muted ? 'Unmute' : 'Mute'} icon="asleep" onSelect={() => void press('threads.mute', { thread: id, muted: !muted })} />
      <MenuRule />
      <MenuItem label="Archive thread" icon="archive" disabled={!manage} onSelect={() => void press('threads.archive', { thread: id })} />
    </Menu>
  );

  return (
    <>
      <ItemHead
        title={title}
        target={`thread:${id}`}
        back={{ to: '/threads', label: 'Threads' }}
        lead={other ? <Face actor={other} /> : undefined}
        more={more}
        onRename={manage ? (next) => void press('threads.rename', { thread: id, title: next }) : undefined}
        renaming={editing}
        onRenamingChange={setEditing}
        renameLabel="Thread name"
      />
      {t && (
        <div className="conv-about">
          <div className="ab-row">
            <span className="faces ab-people">
              {people.slice(0, FACES).map((a) => (
                <Face key={a} actor={a} />
              ))}
            </span>
            {people.length > FACES && <Count n={people.length - FACES} className="ab-n" label={`${people.length - FACES} more`} />}
            <button type="button" className="ab-chip ab-add" onClick={() => setAdding(true)}>
              <Icon name="plus" />
              Add people
            </button>
            {t.task_id && (
              <Link className="ab-chip" to="/task/$task" params={{ task: String(t.task_id) }} data-target={`task:${t.task_id}`}>
                <Icon name="tasks" />
                Task
              </Link>
            )}
          </div>
        </div>
      )}
      {adding && <AddPeople id={id} have={new Set([...(me ? [me] : []), ...people])} onClose={() => setAdding(false)} />}
    </>
  );
}

function AddPeople({ id, have, onClose }: { id: string; have: Set<string>; onClose: () => void }) {
  const actors = useActors();
  const [picked, setPicked] = useState<Actor[]>([]);
  const [busy, setBusy] = useState(false);
  const left = actors.filter((a) => !have.has(a.id) && !picked.some((p) => p.id === a.id));
  const add = async () => {
    setBusy(true);
    const ok = await press('threads.addPeople', { thread: id, actors: picked.map((a) => a.id) });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Add people"
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
        label="Teammate"
        labelHidden
        placeholder="Name a teammate"
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
