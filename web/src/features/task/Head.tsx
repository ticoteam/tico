import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type ReactElement, type ReactNode } from 'react';
import { run, type Result } from '@/actions';
import { meQuery, useActor, useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { LatePill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { Select } from '@/kit/Select';
import { PageMore } from '@/pagekinds';
import { pipelinesQuery } from '@/features/tasks/routing';
import { whoHref } from '@/features/team/routing';
import { DETAILS, earlierStages, failedToast, reasonOf, refusalOf, tabAddress, type Refusal } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { Face, Go, Mark } from './parts';
import { retiredStagesQuery, waitingQuery, type TaskData } from './queries';

/* The head's controls: the where line (pipeline › stage pick, owner pick, time in the stage, Late), Flow's previous
   and next, and "⋯" with the rest. */

const failed = (r: Result, action?: { label: string; run: () => void }) => toast({ ...failedToast(r), ...(action && { action }) });

/** The task's pipeline's stages, in order. */
function useStages(t: TaskData) {
  const id = t.pipeline_id ?? '';
  const stages = useQuery({ ...pipelinesQuery, select: (pipelines) => pipelines.find((p) => p.id === id)?.stages });
  const retired = useQuery({ ...retiredStagesQuery(id), enabled: !!id && stages.isSuccess && !stages.data });
  return stages.data ?? retired.data ?? [];
}

/** The where line (parts-kit.js where): the stage, who has it, how long, Late. Where the task sits is the trail. */
export function WhereLine({ t }: { t: TaskData }) {
  return (
    <>
      <StagePick t={t} />
      <OwnerPick t={t} />
      {t.stage_since && <Ago at={t.stage_since} className="row-age" />}
      {t.late && <LatePill />}
    </>
  );
}

/** An approval's one quiet line (plan/approvals.html): pipeline · stage · how long, and Late. The stage and owner
    picks are in Details, under ⋯. */
export function QuietLine({ t, children }: { t: TaskData; children?: ReactNode }) {
  return (
    <span className="tv-quiet">
      {[t.pipeline?.name, t.status === 'open' ? t.stage?.name : t.status === 'done' ? 'Done' : 'Closed'].filter(Boolean).join(' · ')}
      {t.stage_since && (
        <>
          {' · '}
          <Ago at={t.stage_since} />
        </>
      )}
      {children}
      {t.late && <LatePill />}
    </span>
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
  const [reopening, setReopening] = useState(false);
  const label = t.status === 'open' ? (t.stage?.name ?? 'No stage') : t.status === 'done' ? 'Done' : 'Closed';
  const move = async (to: string, name: string) => {
    const r = await run('task.move', { task: t.id, to }, { via: 'click' });
    if (r.ok) return;
    const refusal = refusalOf(r.error);
    if (refusal.code === 'task_not_met') setHeld({ to, name, refusal });
    else failed(r);
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
          <MenuItem label="Reopen" icon="refresh" onSelect={() => setReopening(true)} />
        )}
      </Menu>
      {held && <MoveAnyway t={t} held={held} onClose={() => setHeld(null)} />}
      {reopening && <ReopenAsk t={t} onClose={() => setReopening(false)} />}
    </>
  );
}

/** "Reopen it?": a finished task opens again only with a reason. */
function ReopenAsk({ t, onClose }: { t: TaskData; onClose: () => void }) {
  const [why, setWhy] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const press = async () => {
    setBusy(true);
    setError('');
    const r = await run('task.move', { task: t.id, to: 'reopen', why: why.trim() }, { via: 'click' });
    setBusy(false);
    if (r.ok) return onClose();
    setError(reasonOf(r.error));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Reopen it?"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!why.trim() || busy} onClick={() => press()}>
            Reopen
          </Button>
        </>
      }
    >
      <Field label="Why reopen it?" area autoFocus value={why} onValueChange={setWhy} error={error || undefined} />
    </Dialog>
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
          <Button variant="primary" busy={busy} disabled={!why.trim() || busy} onClick={() => press()}>
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
    failed(r, r.error?.startsWith('task_stage_rule') ? { label: 'Give anyway', run: () => void give(owner, true) } : undefined);
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
function useFlowOrder(t: TaskData, enabled = true) {
  const waiting = useQuery({ ...waitingQuery, enabled });
  const navigate = useNavigate();
  const rows = waiting.data ?? [];
  const i = rows.findIndex((w) => w.id === t.id);
  const next = () => {
    const to = rows[i + 1] ?? rows[i - 1];
    void navigate(to ? { to: tabAddress(to, undefined, true).to as never } : { to: '/flow' as never });
  };
  return { rows, i, next, read: waiting.isSuccess };
}

/** Flow's previous and next: the tasks that wait on the person, in Flow's order. */
function FlowSteps({ t }: { t: TaskData }) {
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

/** A phone's row over a task in Flow (more.js tv-top, 48 high): back to Flow, where this task is in Flow ("1 of 7",
    plan/approvals.html; how many wait when it is not in the list), previous and next, then the task's "⋯". */
export function FlowTop({ t, more }: { t: TaskData; more?: ReactNode }) {
  const { rows, i, read } = useFlowOrder(t);
  return (
    <div className="tv-top">
      <Go to="/flow" className="ib" aria-label="Back to Flow" title="Back to Flow">
        <Icon name="chevron" className="flip" />
      </Go>
      {read && (
        <span className="tv-n" title={`${rows.length} wait${rows.length === 1 ? 's' : ''} on you`}>
          <FlowMark size={18} play />
          {i >= 0 ? (
            <span>
              <b>{i + 1}</b> of {rows.length}
            </span>
          ) : (
            <b>{rows.length}</b>
          )}
        </span>
      )}
      <span className="grow" />
      <FlowSteps t={t} />
      {more}
    </div>
  );
}

type Asking = 'move' | 'reopen' | 'delete' | null;

/** "⋯": snooze, done (the due date and tags edit in Details); in Flow, Give to and Move back, after which Flow goes on to the next task. Then the rest:
    watch the agent's session, copy the link, move to another pipeline, reopen with a reason, delete. Reopen and Delete
    ask first. */
export function MoreMenu({ t, flow = false, details = false }: { t: TaskData; flow?: boolean; details?: boolean }) {
  const [asking, setAsking] = useState<Asking>(null);
  const [why, setWhy] = useState('');
  const [to, setTo] = useState<{ pipeline: string; name: string; stage: string | null } | null>(null);
  const navigate = useNavigate();
  const owner = useActor(t.owner_id);
  const me = useQuery(meQuery).data?.id;
  const pipelines = useQuery(pipelinesQuery).data ?? [];
  const others = pipelines.filter((p) => p.id !== t.pipeline_id);
  const stagesTo = pipelines.find((p) => p.id === to?.pipeline)?.stages.filter((s) => !s.side) ?? [];
  const mayDelete = t.mover || (!!me && t.requester_id === me);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const order = useFlowOrder(t, flow && t.status === 'open');
  const give = useGive(t, order.next);
  const back = earlierStages(useStages(t), t.stage_id);

  const open = (what: Exclude<Asking, null>) => {
    setError('');
    setWhy('');
    setAsking(what);
  };
  const press = async (id: string, input: object) => {
    setBusy(true);
    const r = await run(id, { task: t.id, ...input }, { via: 'click' });
    setBusy(false);
    if (r.ok) setAsking(null);
    else setError(reasonOf(r.error));
  };
  const snooze = async (until: string | null) => {
    const r = await run('task.snooze', { task: t.id, until }, { via: 'click' });
    if (!r.ok) failed(r);
  };
  const moveBack = async (to: string) => {
    const r = await run('task.moveBack', { task: t.id, to }, { via: 'click' });
    if (r.ok) order.next();
    else failed(r);
  };
  const close = () => setAsking(null);
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(`${location.origin}/task/${t.number ?? t.id}`);
      toast({ text: 'Link copied' });
    } catch {
      toast({ text: 'Couldn’t copy the link', kind: 'failed' });
    }
  };
  const moveTo = (pipeline: string, name: string) => {
    open('move');
    setTo({ pipeline, name, stage: null });
  };
  const cancel = <Button onClick={close}>Cancel</Button>;

  return (
    <>
      <PageMore>
        {details && (
          <>
            <MenuItem
              label="Details"
              icon="list"
              onSelect={() => {
                const a = tabAddress(t, DETAILS, flow);
                void navigate({ to: a.to as never, search: a.search as never });
              }}
            />
            <MenuRule />
          </>
        )}
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
        {t.status === 'open' && (
          <>
            {t.snoozed ? (
              <MenuItem label="Unsnooze" icon="clock" onSelect={() => void snooze(null)} />
            ) : (
              <MenuSub label="Snooze" icon="clock">
                {snoozeChoices().map(([label, at]) => (
                  <MenuItem key={label} label={label} onSelect={() => void snooze(at.toISOString())} />
                ))}
              </MenuSub>
            )}
            <MenuRule />
            <MenuItem
              label="Mark done"
              icon="check"
              onSelect={() => void run('task.done', { task: t.id }, { via: 'click' }).then((r) => (r.ok ? undefined : failed(r)))}
            />
          </>
        )}
        <MenuRule />
        {owner?.kind === 'agent' && t.status === 'open' && (
          <MenuItem label="Watch session" icon="computer" onSelect={() => void navigate({ to: whoHref(owner) as never, search: { tab: 'screen' } as never })} />
        )}
        <MenuItem label="Copy link" icon="link" onSelect={() => void copyLink()} />
        {others.length > 0 && (
          <MenuSub label="Move to" icon="tasks">
            {others.map((p) => (
              <MenuItem key={p.id} label={p.name} onSelect={() => moveTo(p.id, p.name)} />
            ))}
          </MenuSub>
        )}
        {t.status !== 'open' && <MenuItem label="Reopen" icon="refresh" onSelect={() => open('reopen')} />}
        {mayDelete && (
          <>
            <MenuRule />
            <MenuItem label="Delete" icon="close" onSelect={() => open('delete')} />
          </>
        )}
      </PageMore>

      <Dialog
        open={asking === 'move'}
        onOpenChange={(o) => !o && close()}
        title={`Move to ${to?.name ?? ''}`}
        actions={
          <>
            {cancel}
            <Button
              variant="primary"
              busy={busy}
              disabled={!to || busy}
              onClick={() => to && void press('task.moveTo', { pipeline: to.pipeline, ...(to.stage && { stage: to.stage }), ...(why.trim() && { why: why.trim() }) })}
            >
              Move
            </Button>
          </>
        }
      >
        {stagesTo.length > 0 && to && (
          <Select
            label="Stage"
            options={stagesTo.map((s) => ({ value: s.id, label: s.name }))}
            value={to.stage ?? stagesTo[0]!.id}
            onValueChange={(stage) => setTo({ ...to, stage })}
          />
        )}
        <Field label="Why" area value={why} onValueChange={setWhy} />
        {error && <p className="an-err">{error}</p>}
      </Dialog>

      {asking === 'reopen' && <ReopenAsk t={t} onClose={close} />}

      <Dialog
        open={asking === 'delete'}
        alert
        onOpenChange={(o) => !o && close()}
        title={`Delete “${t.title}”?`}
        actions={
          <>
            {cancel}
            <Button variant="primary" busy={busy} disabled={busy} onClick={() => press('task.delete', {})}>
              Delete
            </Button>
          </>
        }
      >
        {error && <p className="an-err">{error}</p>}
      </Dialog>

    </>
  );
}

/** Snooze's choices: the start of that day, local time (ui/app/task-props.ts snoozeItems). */
export function snoozeChoices(now = new Date()): [string, Date][] {
  const day = (n: number) => {
    const at = new Date(now);
    at.setHours(0, 0, 0, 0);
    at.setDate(at.getDate() + n);
    return at;
  };
  const monday = (8 - now.getDay()) % 7 || 7;
  return [
    ['Tomorrow', day(1)],
    ['Next week', day(monday)],
    ['In two weeks', day(14)],
    ['In a month', day(30)],
  ];
}

/** "bug, checkout" to its tags, once each. */
export const tagsOf = (text: string): string[] => [...new Set(text.split(',').map((x) => x.trim()).filter(Boolean))];
