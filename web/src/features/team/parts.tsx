import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { useActor, type Actor } from '@/data';
import { firstPipeline, pipelinesQuery } from '@/features/tasks/routing';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { LabelValue } from '@/kit/LabelValue';
import { Link as KitLink } from '@/kit/Link';
import { OwnerButton as KitOwnerButton } from '@/kit/OwnerButton';
import { Pill } from '@/kit/Pill';
import { Row, TaskRow } from '@/kit/Row';
import { Select, type SelectOption } from '@/kit/Select';
import { VirtualList } from '@/kit/VirtualList';
import { whoHref } from './model';
import type { OwnTask } from './queries';
import { chatAddress } from '@/lib';

export const waitsOn = (t: Pick<OwnTask, 'turn'>, me: string | undefined) => !!me && t.turn?.state === 'person' && t.turn.who === me;
export const taskHref = (t: Pick<OwnTask, 'pipeline_id' | 'number' | 'id'>) =>
  t.pipeline_id && t.number ? `/tasks/${encodeURIComponent(t.pipeline_id)}/${t.number}` : `/task/${encodeURIComponent(t.id)}`;

/** A task on its owner's page: no face, since every row here is theirs (mates.js ownRow). Only an open task is
    Late, as in the old app (ui/app/task-row.ts taskLate): a done one keeps its grey row. */
export function OwnRow({ task, me, sub }: { task: OwnTask; me: string | undefined; sub?: string }) {
  return (
    <TaskRow
      title={task.title ?? ''}
      href={taskHref(task)}
      target={`task:${task.number ?? task.id}`}
      mark={waitsOn(task, me) ? <FlowMark /> : undefined}
      late={task.status === 'open' && !!task.late}
      pills={sub ? <span className="row-stage">{sub}</span> : undefined}
      at={task.updated_at ?? new Date(0).toISOString()}
    />
  );
}

/** Past this many rows a task list draws only what is on screen (builder rules: lists past 100 rows are virtual). */
export const LONG_LIST = 100;

/** A teammate's tasks as OwnRows. Past LONG_LIST rows, only those on screen are drawn, in a scroller twelve rows high;
    `onEnd` reads the next page when its last row comes into view. */
export function OwnRows({ tasks, me, label, onEnd }: { tasks: readonly OwnTask[]; me: string | undefined; label: string; onEnd?: () => void }) {
  if (tasks.length <= LONG_LIST) return tasks.map((t) => <OwnRow key={t.id} task={t} me={me} />);
  return (
    <VirtualList
      className="tm-long"
      label={label}
      items={tasks}
      getKey={(t) => t.id}
      estimateSize={34}
      measure
      onReachEnd={onEnd}
      renderItem={(t) => <OwnRow task={t} me={me} />}
    />
  );
}

/** A teammate: what waits on you, who, what they are on now, a pill only when something is wrong, open tasks. */
export function MateRow({ actor, needs, pill, count }: { actor: Actor; needs?: boolean; pill?: string; count?: number }) {
  return (
    <Row className="mate" title={actor.name} href={whoHref(actor)} target={`teammate:${actor.slug ?? actor.id}`}>
      <span className="row-mark">{needs && <FlowMark />}</span>
      <Avatar kind={actor.kind} name={actor.name} hue={actor.hue} live={actor.working} size={22} />
      <span className="mate-name">{actor.name}</span>
      <span className="mate-now">{actor.status ?? ''}</span>
      <span className="row-pills">{pill && <Pill flag>{pill}</Pill>}</span>
      <span className="count">{count || ''}</span>
    </Row>
  );
}

/** The fold under a section: "Done", then how many. With `children`, how many opens them under it (the old Done recently). */
export function Fold({ label, text, children }: { label: string; text: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="stage-fold">
        <span className="fold-label">{label}</span>
        {children ? (
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {text}
          </button>
        ) : (
          <span>{text}</span>
        )}
      </div>
      {open && children}
    </>
  );
}

/** The page owner's face in the head: it opens their chat beside the page (`?chat=`, the shell's). */
export function OwnerButton({ actor }: { actor: string }) {
  const who = useActor(actor);
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      q.set('chat', chatAddress(who?.id ?? actor, who?.kind));
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (!who) return null;
  return <KitOwnerButton name={who.name} kind={who.kind} hue={who.hue} live={who.working} href={href} />;
}

/** A page's rare verbs as one quiet line, never red (mates.css `.rec .foot`). */
export function Foot({ children }: { children: ReactNode }) {
  return <div className="foot">{children}</div>;
}

/** One line of text, changed in a dialog and saved through an action. */
export function EditDialog({
  open,
  onOpenChange,
  title,
  label,
  value,
  area,
  action,
  input,
  verb = 'Save',
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  label: string;
  value: string;
  area?: boolean;
  action: string;
  input: (text: string) => unknown;
  /** The filled button's word: Save, or Send for a request. */
  verb?: string;
}) {
  const [text, setText] = useState(value);
  const save = useAction(action);
  const send = () => {
    onOpenChange(false);
    void save.run(input(text.trim()));
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setText(value);
        onOpenChange(o);
      }}
      title={title}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={text.trim() === value.trim()} onClick={send}>
            {verb}
          </Button>
        </>
      }
    >
      <Field label={label} labelHidden value={text} onValueChange={setText} area={area} autoFocus />
    </Dialog>
  );
}

/** A line whose value is picked where it stands: the pick saves at once, through an action. Without `action` (no right
    to change it), the words alone. */
export function PickLine({
  label,
  options,
  value,
  shown,
  action,
  input,
  onPick,
  trail,
  face,
}: {
  label: string;
  options: readonly SelectOption[];
  value: string;
  /** The words while nothing in `options` is chosen ("None", "Nobody"). */
  shown: string;
  action?: string;
  input?: (value: string) => unknown;
  /** In place of `action`: what a pick does, when it needs more than one action's run. */
  onPick?: (value: string) => void;
  /** At the line's end, outside the pick (LabelValue's `trail`). */
  trail?: ReactNode;
  /** Before the value: the face of who it names (Reports to, t-settings). */
  face?: ReactNode;
}) {
  const save = useAction(action ?? '');
  const words = options.find((o) => o.value === value)?.label ?? shown;
  const pick = onPick ?? (action && input ? (v: string) => void save.run(input(v)) : undefined);
  if (!pick)
    return (
      <LabelValue label={label} trail={trail}>
        {face ? (
          <span className="fact-face">
            {face}
            {words}
          </span>
        ) : (
          words
        )}
      </LabelValue>
    );
  const line = (
    <div className="fact">
      <span>{label}</span>
      <span className="fact-v">
        {face}
        <Select
          label={label}
          labelHidden
          look="quiet"
          options={options}
          value={value || null}
          placeholder={shown}
          onValueChange={(v) => v !== value && pick(v)}
        />
      </span>
    </div>
  );
  return trail ? (
    <div className="fact-more">
      {line}
      {trail}
    </div>
  ) : (
    line
  );
}

/** Asks before a change that is hard to take back: archive, remove, restart. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  text,
  verb,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  text?: string;
  verb: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={text}
      alert
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            {verb}
          </Button>
        </>
      }
    />
  );
}

/** The link at the right of a section's name: the whole list, somewhere else. */
export function SecLink({ href, children }: { href: string; children: string }) {
  return (
    <KitLink href={href} className="sec-link">
      {children}
      <Icon name="chevron" />
    </KitLink>
  );
}

/** "All tasks" on a teammate's page: the pipeline Tasks opens, filtered to them (`?owner=`). */
export function AllTasks({ owner }: { owner: string }) {
  const { data } = useQuery(pipelinesQuery);
  const p = data ? firstPipeline(data) : null;
  return <SecLink href={p ? `/tasks/${encodeURIComponent(p)}?owner=${encodeURIComponent(owner)}` : '/tasks'}>All tasks</SecLink>;
}
