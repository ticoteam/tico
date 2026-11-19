import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions';
import { useActors } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { reasonOf, tabAddress } from './model';
import { Face, Go } from './parts';
import { pipelineStagesQuery, waitingQuery, type TaskData } from './queries';

/* The head's controls: the stage pick (a move), the owner pick, Flow's previous and next, and "⋯" with the rest. */

const failed = (error: string | undefined, action?: { label: string; run: () => void }) =>
  toast({ text: reasonOf(error), kind: 'failed', ...(action && { action }) });

export function StagePick({ t }: { t: TaskData }) {
  const stages = useQuery(pipelineStagesQuery);
  const mine = stages.data?.find((p) => p.id === t.pipeline_id)?.stages ?? [];
  const label = t.status === 'open' ? (t.stage?.name ?? 'No stage') : t.status === 'done' ? 'Done' : 'Closed';
  const move = async (to: string) => {
    const r = await run('task.move', { task: t.id, to }, { via: 'click' });
    if (!r.ok) failed(r.error);
  };
  return (
    <Menu
      align="start"
      trigger={
        <button className="tv-pick" type="button" aria-label={`Stage: ${label}`} data-target={`task-stage:${t.id}`}>
          <span>{label}</span>
          <Icon name="chevdown" />
        </button>
      }
    >
      {mine.map((s) => (
        <MenuItem key={s.id} label={s.name} on={s.id === t.stage_id && t.status === 'open'} onSelect={() => void move(s.id)} />
      ))}
      {mine.length > 0 && <MenuRule />}
      {t.status === 'open' ? (
        <>
          <MenuItem label="Done" icon="check" onSelect={() => void move('done')} />
          <MenuItem label="Close" icon="archive" onSelect={() => void move('closed')} />
        </>
      ) : (
        <MenuItem label="Reopen" icon="refresh" onSelect={() => void move('reopen')} />
      )}
    </Menu>
  );
}

export function OwnerPick({ t }: { t: TaskData }) {
  const actors = useActors();
  const give = async (owner: string, setAnyway?: boolean) => {
    const r = await run('task.assign', { task: t.id, owner, ...(setAnyway && { setAnyway }) }, { via: 'click' });
    // A stage rule refused this owner: a person may give it anyway (ui/app/task-api.ts taskReassign).
    if (!r.ok) failed(r.error, r.error?.startsWith('task_stage_rule') ? { label: 'Give anyway', run: () => void give(owner, true) } : undefined);
  };
  const agents = actors.filter((a) => a.kind === 'agent');
  const people = actors.filter((a) => a.kind === 'person');
  const item = (a: (typeof actors)[number]) => (
    <MenuItem
      key={a.id}
      label={a.name}
      lead={<Avatar decorative kind={a.kind} name={a.name} hue={a.hue} size={16} />}
      on={a.id === t.owner_id}
      onSelect={() => void give(a.id)}
    />
  );
  return (
    <Menu
      trigger={
        <button className="tv-pick" type="button" aria-label="Who has it" data-target={`task-owner:${t.id}`}>
          <Face id={t.owner_id} decorative />
        </button>
      }
    >
      {agents.map(item)}
      {agents.length > 0 && people.length > 0 && <MenuRule />}
      {people.map(item)}
    </Menu>
  );
}

/** Flow's previous and next: the tasks that wait on the person, in Flow's order. */
export function FlowSteps({ t }: { t: TaskData }) {
  const waiting = useQuery(waitingQuery);
  const rows = waiting.data ?? [];
  const i = rows.findIndex((w) => w.id === t.id);
  const step = (d: number, label: string, flip: boolean) => {
    const to = i >= 0 ? rows[i + d] : undefined;
    const ico = <Icon name="chevdown" className={flip ? 'flip' : undefined} />;
    if (!to)
      return (
        <button className="ib" type="button" aria-label={label} title={label} disabled>
          {ico}
        </button>
      );
    const a = tabAddress(to, undefined, true);
    return (
      <Go to={a.to} search={a.search} className="ib" aria-label={label} title={label}>
        {ico}
      </Go>
    );
  };
  return (
    <>
      {step(-1, 'Previous', true)}
      {step(1, 'Next', false)}
    </>
  );
}

type Asking = 'rename' | 'due' | 'tags' | null;

/** "⋯": rename, due, tags, done. */
export function MoreMenu({ t }: { t: TaskData }) {
  const [asking, setAsking] = useState<Asking>(null);
  const [title, setTitle] = useState('');
  const [due, setDue] = useState<Date | null>(null);
  const [tags, setTags] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const open = (what: Exclude<Asking, null>) => {
    setError('');
    setTitle(t.title);
    setDue(t.due_at ? new Date(t.due_at) : null);
    setTags(t.labels.join(', '));
    setAsking(what);
  };
  const press = async (id: string, input: object) => {
    setBusy(true);
    const r = await run(id, { task: t.id, ...input }, { via: 'click' });
    setBusy(false);
    if (r.ok) setAsking(null);
    else setError(reasonOf(r.error));
  };
  const day = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const close = () => setAsking(null);
  const cancel = <Button onClick={close}>Cancel</Button>;

  return (
    <>
      <Menu trigger={<IconButton icon="more" label="More" />}>
        <MenuItem label="Rename" icon="edit" onSelect={() => open('rename')} />
        <MenuItem label="Due date" icon="calendar" onSelect={() => open('due')} />
        <MenuItem label="Tags" icon="rules" onSelect={() => open('tags')} />
        {t.status === 'open' && (
          <>
            <MenuRule />
            <MenuItem
              label="Mark done"
              icon="check"
              onSelect={() => void run('task.done', { task: t.id }, { via: 'click' }).then((r) => (r.ok ? undefined : failed(r.error)))}
            />
          </>
        )}
      </Menu>

      <Dialog
        open={asking === 'rename'}
        onOpenChange={(o) => !o && close()}
        title="Rename"
        actions={
          <>
            {cancel}
            <Button variant="primary" busy={busy} disabled={!title.trim() || busy} onClick={() => void press('task.rename', { title })}>
              Save
            </Button>
          </>
        }
      >
        <Field label="Title" labelHidden wide autoFocus value={title} onValueChange={setTitle} error={error || undefined} />
      </Dialog>

      <Dialog
        open={asking === 'due'}
        onOpenChange={(o) => !o && close()}
        title="Due date"
        actions={
          <>
            {t.due_at && <Button onClick={() => void press('task.setDue', { due: null })}>Clear</Button>}
            {cancel}
            <Button variant="primary" busy={busy} disabled={!due || busy} onClick={() => due && void press('task.setDue', { due: day(due) })}>
              Save
            </Button>
          </>
        }
      >
        <DatePick label="Due" labelHidden withTime={false} value={due} onValueChange={setDue} error={!!error} />
        {error && <p className="an-err">{error}</p>}
      </Dialog>

      <Dialog
        open={asking === 'tags'}
        onOpenChange={(o) => !o && close()}
        title="Tags"
        actions={
          <>
            {cancel}
            <Button
              variant="primary"
              busy={busy}
              disabled={busy}
              onClick={() => void press('task.tag', { labels: [...new Set(tags.split(',').map((x) => x.trim()).filter(Boolean))] })}
            >
              Save
            </Button>
          </>
        }
      >
        <Field label="Tags" labelHidden wide autoFocus placeholder="bug, checkout" value={tags} onValueChange={setTags} error={error || undefined} />
      </Dialog>
    </>
  );
}
