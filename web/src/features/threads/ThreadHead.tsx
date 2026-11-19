import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { Outline } from '@/conversation';
import { ThreadCallHead } from '@/features/calls/exports';
import { Count } from '@/kit/Count';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { ItemHead } from '@/pagekinds';
import { AddPeople, Members } from './Members';
import { isDm, isMuted, othersOf } from './model';
import { Face, press, useMe, useTitle } from './people';
import { threadQuery } from './queries';
import { Settings } from './Settings';

const FACES = 6;

/** The open thread's head: its name (renamed in place), who is in it, and "⋯". */
export function ThreadHead({ id }: { id: string }) {
  const { data: t } = useQuery(threadQuery(id));
  const me = useMe();
  const title = useTitle(t) || 'Thread';
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState<'people' | 'add' | 'settings' | 'outline' | null>(null);
  const close = () => setOpen(null);
  const people = t ? othersOf(t, me) : [];
  const muted = isMuted(t);
  const manage = t?.me?.can_manage !== false;
  const other = t && isDm(t) ? people[0] : undefined;

  const more = (
    <Menu trigger={<IconButton icon="more" label="More" />}>
      <MenuItem label="Rename" icon="edit" disabled={!manage} onSelect={() => setEditing(true)} />
      <MenuItem label="People" icon="people" onSelect={() => setOpen('people')} />
      <MenuItem label={muted ? 'Unmute' : 'Mute'} icon="asleep" onSelect={() => void press('threads.mute', { thread: id, muted: !muted })} />
      <MenuItem label="Outline" icon="list" onSelect={() => setOpen('outline')} />
      <MenuItem label="Settings" icon="gear" onSelect={() => setOpen('settings')} />
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
        actions={t ? <ThreadCallHead thread={id} members={people} canPost={t.me?.can_post !== false} /> : undefined}
        more={more}
        onRename={manage ? (next) => void press('threads.rename', { thread: id, title: next }) : undefined}
        renaming={editing}
        onRenamingChange={setEditing}
        renameLabel="Thread name"
      />
      {t && (
        <div className="conv-about">
          <div className="ab-row">
            <button type="button" className="faces ab-people" aria-label="People" title="People" onClick={() => setOpen('people')}>
              {people.slice(0, FACES).map((a) => (
                <Face key={a} actor={a} />
              ))}
              {people.length > FACES && <Count n={people.length - FACES} className="ab-n" label={`${people.length - FACES} more`} />}
            </button>
            <button type="button" className="ab-chip ab-add" onClick={() => setOpen('add')}>
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
      {t && open === 'people' && <Members thread={t} onClose={close} onAdd={() => setOpen('add')} />}
      {t && open === 'add' && <AddPeople thread={t} have={new Set([...(me ? [me] : []), ...people])} onClose={close} />}
      {t && open === 'settings' && <Settings thread={t} onClose={close} />}
      <Outline id={id} open={open === 'outline'} onOpenChange={(o) => setOpen(o ? 'outline' : null)} />
    </>
  );
}
