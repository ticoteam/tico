import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type ReactElement } from 'react';
import { run } from '@/actions';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { LatePill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { earlierStages, reasonOf, refusalOf, tabAddress, type Refusal } from './model';
import { Face, FlowMark, Go, Mark } from './parts';
import { pipelineStagesQuery, waitingQuery, type TaskData } from './queries';

/* The head's controls: the where line (pipeline › stage pick, owner pick, time in the stage, Late), Flow's previous
   and next, and "⋯" with the rest. */

const failed = (error: string | undefined, action?: { label: string; run: () => void }) =>
  toast({ text: reasonOf(error), kind: 'failed', ...(action && { action }) });

/** The task's pipeline's stages, in order. */
function useStages(t: TaskData) {
  const stages = useQuery(pipelineStagesQuery);
  return stages.data?.find((p) => p.id === t.pipeline_id)?.stages ?? [];
}

/** The rest of the where line after the pipeline (parts-kit.js where): the stage, who has it, how long, Late. */
export function WhereLine({ t }: { t: TaskData }) {
  return (
    <>
      <Icon name="chevron" />
      <StagePick t={t} />
      <OwnerPick t={t} />
      {t.stage_since && <Ago at={t.stage_since} className="row-age" />}
      {t.late && <LatePill />}
    </>
  );
}

interface Held {
  to: string;
  name: string;
  refusal: Refusal;
}

export function StagePick({ t }: { t: TaskData }) {
  const mine = useStages(t);
  // A forward move the stage refused for what is missing: the list, and the person's way past it with a reason.
  const [held, setHeld] = useState<Held | null>(null);
  const label = t.status === 'open' ? (t.stage?.name ?? 'No stage') : t.status === 'done' ? 'Done' : 'Closed';
  const move = async (to: string, name: string) => {
    const r = await run('task.move', { task: t.id, to }, { via: 'click' });
    if (r.ok) return;
    const refusal = refusalOf(r.error);
    if (refusal.code === 'task_not_met') setHeld({ to, name, refusal });
    else failed(r.error);
  };
  return (
    <>
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
          <MenuItem key={s.id} label={s.name} on={s.id === t.stage_id && t.status === 'open'} onSelect={() => void move(s.id, s.name)} />
        ))}
        {mine.length > 0 && <MenuRule />}
        {t.status === 'open' ? (
          <>
            <MenuItem label="Done" icon="check" onSelect={() => void move('done', 'Done')} />
            <MenuItem label="Close" icon="archive" onSelect={() => void move('closed', 'Closed')} />
          </>
        ) : (
          <MenuItem label="Reopen" icon="refresh" onSelect={() => void move('reopen', 'Open')} />
        )}
      </Menu>
      {held && <MoveAnyway t={t} held={held} onClose={() => setHeld(null)} />}
    </>
  );
}

/** "Move to Review?": what is missing, as the server said it, and a reason to move past it. */
function MoveAnyway({ t, held, onClose }: { t: TaskData; held: Held; onClose: () => void }) {
  const [why, setWhy] = useState('');
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState(held.refusal);
  const [error, setError] = useState('');
  const press = async () => {
    setBusy(true);
    setError('');
    const r = await run('task.moveAnyway', { task: t.id, to: held.to, why }, { via: 'click' });
    setBusy(false);
    if (r.ok) return onClose();
    const again = refusalOf(r.error);
    if (again.missing.length) setRefusal(again);
    setError(again.reason);
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Move to ${held.name}?`}
      description={refusal.reason}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!why.trim() || busy} onClick={() => void press()}>
            Move anyway
          </Button>
        </>
      }
    >
      {refusal.missing.map((m) => (
        <div key={m} className="dt-line">
          <Mark met={false} />
          <span className="dt-text">{m}</span>
        </div>
      ))}
      <Field label="Why" area autoFocus value={why} onValueChange={setWhy} />
      {error && <p className="an-err">{error}</p>}
    </Dialog>
  );
}

/** Give to: someone the person may give work to. A stage rule that refuses them can be passed by a person. */
function useGive(t: TaskData, then?: () => void) {
  return async function give(owner: string, setAnyway?: boolean): Promise<void> {
    const r = await run('task.give', { task: t.id, owner, ...(setAnyway && { setAnyway }) }, { via: 'click' });
    if (r.ok) return then?.();
    // A stage rule refused this owner: a person may give it anyway (ui/app/task-api.ts taskReassign).
    failed(r.error, r.error?.startsWith('task_stage_rule') ? { label: 'Give anyway', run: () => void give(owner, true) } : undefined);
  };
}

/** Agents first, then people; the one who has it is marked. */
export function ActorItems({ current, onPick }: { current: string | null | undefined; onPick: (id: string) => void }) {
  const actors = useActors();
  const agents = actors.filter((a) => a.kind === 'agent');
  const people = actors.filter((a) => a.kind === 'person');
  const item = (a: (typeof actors)[number]) => (
    <MenuItem key={a.id} label={a.name} lead={<Avatar decorative kind={a.kind} name={a.name} hue={a.hue} size={16} />} on={a.id === current} onSelect={() => onPick(a.id)} />
  );
  return (
    <>
      {agents.map(item)}
      {agents.length > 0 && people.length > 0 && <MenuRule />}
      {people.map(item)}
    </>
  );
}

/** Who has it, as a menu on any trigger: the face in the head, the line in Details. */
export function OwnerMenu({ t, trigger }: { t: TaskData; trigger: ReactElement }) {
  const give = useGive(t);
  return (
    <Menu trigger={trigger}>
      <ActorItems current={t.owner_id} onPick={(id) => void give(id)} />
    </Menu>
  );
}

export function OwnerPick({ t }: { t: TaskData }) {
  return (
    <OwnerMenu
      t={t}
      trigger={
        <button className="tv-pick" type="button" aria-label="Who has it" data-target={`task-owner:${t.id}`}>
          <Face id={t.owner_id} decorative />
        </button>
      }
    />
  );
}

/** Flow's order: the tasks that wait on the person, and where Flow goes once this one is off their hands. */
function useFlowOrder(t: TaskData) {
  const waiting = useQuery(waitingQuery);
  const navigate = useNavigate();
  const rows = waiting.data ?? [];
  const i = rows.findIndex((w) => w.id === t.id);
  const next = () => {
    const to = rows[i + 1] ?? rows[i - 1];
    void navigate(to ? { to: tabAddress(to, undefined, true).to as never } : { to: '/flow' as never });
  };
  return { rows, i, next };
}

/** Flow's previous and next: the tasks that wait on the person, in Flow's order. */
export function FlowSteps({ t }: { t: TaskData }) {
  const { rows, i } = useFlowOrder(t);
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

/** A phone's row over a task in Flow (more.js tv-top, 48 high): back to Flow, how many wait, previous and next. */
export function FlowTop({ t }: { t: TaskData }) {
  const { rows } = useFlowOrder(t);
  return (
    <div className="tv-top">
      <Go to="/flow" className="ib" aria-label="Back to Flow" title="Back to Flow">
        <Icon name="chevron" className="flip" />
      </Go>
      <span className="tv-n" title={`${rows.length} wait${rows.length === 1 ? 's' : ''} on you`}>
        <FlowMark size={18} />
        <b>{rows.length}</b>
      </span>
      <span className="grow" />
      <FlowSteps t={t} />
    </div>
  );
}

type Asking = 'due' | 'tags' | null;

/** "⋯": due, tags, done; in Flow, Give to and Move back, after which Flow goes on to the next task. */
export function MoreMenu({ t, flow = false }: { t: TaskData; flow?: boolean }) {
  const [asking, setAsking] = useState<Asking>(null);
  const [due, setDue] = useState<Date | null>(null);
  const [tags, setTags] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const order = useFlowOrder(t);
  const give = useGive(t, order.next);
  const back = earlierStages(useStages(t), t.stage_id);

  const open = (what: Exclude<Asking, null>) => {
    setError('');
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
  const moveBack = async (to: string) => {
    const r = await run('task.moveBack', { task: t.id, to }, { via: 'click' });
    if (r.ok) order.next();
    else failed(r.error);
  };
  const day = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const close = () => setAsking(null);
  const cancel = <Button onClick={close}>Cancel</Button>;

  return (
    <>
      <Menu trigger={<IconButton icon="more" label="More" />}>
        {flow && t.status === 'open' && (
          <>
            <MenuSub label="Give to" icon="people">
              <ActorItems current={t.owner_id} onPick={(id) => void give(id)} />
            </MenuSub>
            <MenuSub label="Move back" icon="reply">
              {back.length ? (
                back.map((s) => <MenuItem key={s.id} label={s.name} onSelect={() => void moveBack(s.id)} />)
              ) : (
                <MenuItem label="No earlier stage" disabled onSelect={() => undefined} />
              )}
            </MenuSub>
            <MenuRule />
          </>
        )}
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
            <Button variant="primary" busy={busy} disabled={busy} onClick={() => void press('task.tag', { labels: tagsOf(tags) })}>
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

/** "bug, checkout" to its tags, once each. */
export const tagsOf = (text: string): string[] => [...new Set(text.split(',').map((x) => x.trim()).filter(Boolean))];
