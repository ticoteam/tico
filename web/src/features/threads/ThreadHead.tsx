import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { Outline } from '@/conversation';
import { ThreadCallHead } from '@/features/calls/exports';
import { taskDetailQuery } from '@/features/task/exports';
import { Count } from '@/kit/Count';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { ItemHead, PageMore } from '@/pagekinds';
import { AddPeople, Members } from './Members';
import { isDm, othersOf } from './model';
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
  const manage = t?.me?.can_manage !== false;
  const other = t && isDm(t) ? people[0] : undefined;

  const more = (
    <PageMore>
      <MenuItem label="Rename" icon="edit" disabled={!manage} onSelect={() => setEditing(true)} />
      <MenuItem label="People" icon="people" onSelect={() => setOpen('people')} />
      <MenuItem label="Outline" icon="list" onSelect={() => setOpen('outline')} />
      <MenuItem label="Settings" icon="gear" onSelect={() => setOpen('settings')} />
    </PageMore>
  );

  return (
    <>
      <ItemHead
        title={title}
        target={`thread:${id}`}
        back={{ to: '/threads', label: 'Threads' }}
        lead={other ? <Face actor={other} /> : undefined}
        actions={
          t && (
            <>
              <ThreadCallHead thread={id} members={people} canPost={t.me?.can_post !== false} />
              {manage && <IconButton icon="archive" label="Archive" onClick={() => void press('threads.archive', { thread: id })} />}
            </>
          )
        }
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
            {t.task_id && <TaskChip task={String(t.task_id)} />}
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

/** The task the thread belongs to: its code and stage ("E604 Build"), from the task's own read. */
function TaskChip({ task }: { task: string }) {
  const { data } = useQuery(taskDetailQuery(task));
  const code = data?.label || (data?.number != null ? `#${data.number}` : 'Task');
  return (
    <Link className="ab-chip" to="/task/$task" params={{ task }} data-target={`task:${task}`}>
      <Icon name="tasks" />
      <span>{code}</span>
      {data?.status === 'open' && data.stage?.name && <em>{data.stage.name}</em>}
    </Link>
  );
}
