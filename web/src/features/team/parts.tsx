import { useLocation } from '@tanstack/react-router';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { useActor, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Link as KitLink } from '@/kit/Link';
import { Pill } from '@/kit/Pill';
import { Row, TaskRow } from '@/kit/Row';
import { Select, type SelectOption } from '@/kit/Select';
import { whoHref } from './model';
import type { OwnTask } from './queries';

/** Flow's mark: two play-shaped dots (parts-kit.js flow()). Jade lives in dot.css. */
export function FlowMark({ size = 12 }: { size?: number }) {
  return (
    <span className="dot flow" style={{ '--d': `${size}px` } as CSSProperties} role="img" aria-label="Waits on you">
      <i />
      <b />
    </span>
  );
}

export const waitsOn = (t: Pick<OwnTask, 'turn'>, me: string | undefined) => !!me && t.turn?.state === 'person' && t.turn.who === me;
export const taskHref = (t: Pick<OwnTask, 'pipeline_id' | 'number' | 'id'>) =>
  t.pipeline_id && t.number ? `/tasks/${encodeURIComponent(t.pipeline_id)}/${t.number}` : `/task/${encodeURIComponent(t.id)}`;

/** A task on its owner's page: no face, since every row here is theirs (mates.js ownRow). */
export function OwnRow({ task, me, sub }: { task: OwnTask; me: string | undefined; sub?: string }) {
  return (
    <TaskRow
      title={task.title ?? ''}
      href={taskHref(task)}
      target={`task:${task.number ?? task.id}`}
      mark={waitsOn(task, me) ? <FlowMark /> : undefined}
      late={!!task.late}
      pills={sub ? <span className="row-stage">{sub}</span> : undefined}
      at={task.updated_at ?? new Date(0).toISOString()}
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

/** The fold under a section: "Done", then how many. */
export function Fold({ label, text }: { label: string; text: string }) {
  return (
    <div className="stage-fold">
      <span className="fold-label">{label}</span>
      <span>{text}</span>
    </div>
  );
}

/** The page owner's face in the head: it opens their chat beside the page (`?chat=`, the shell's). */
export function OwnerButton({ actor }: { actor: string }) {
  const who = useActor(actor);
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      q.set('chat', who?.id ?? actor);
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (!who) return null;
  return (
    <KitLink href={href} className="pg-owner" aria-label={`Chat with ${who.name}`} title={`Chat with ${who.name}`}>
      <Avatar kind={who.kind} name={who.name} hue={who.hue} live={who.working} size={28} />
    </KitLink>
  );
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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  label: string;
  value: string;
  area?: boolean;
  action: string;
  input: (text: string) => unknown;
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
            Save
          </Button>
        </>
      }
    >
      <Field label={label} labelHidden value={text} onValueChange={setText} area={area} autoFocus />
    </Dialog>
  );
}

/** One choice from a list, saved through an action. */
export function PickDialog({
  open,
  onOpenChange,
  title,
  options,
  value,
  action,
  input,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  options: readonly SelectOption[];
  value: string;
  action: string;
  input: (value: string) => unknown;
}) {
  const [pick, setPick] = useState(value);
  const save = useAction(action);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setPick(value);
        onOpenChange(o);
      }}
      title={title}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!pick || pick === value}
            onClick={() => {
              onOpenChange(false);
              void save.run(input(pick));
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <Select label={title} labelHidden options={options} value={pick || null} onValueChange={setPick} />
    </Dialog>
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

/** A choice of words that fills its row (mates.css `.seg.is-text`): the model picker's harness and effort. */
export function WordPick<V extends string>({
  label,
  options,
  value,
  onValueChange,
}: {
  label: string;
  options: readonly { value: V; label: string; disabled?: boolean }[];
  value: V;
  onValueChange: (v: V) => void;
}) {
  return (
    <div className="seg is-text" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          disabled={o.disabled}
          className={[o.value === value && 'is-on', o.disabled && 'is-off'].filter(Boolean).join(' ') || undefined}
          onClick={() => onValueChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
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
