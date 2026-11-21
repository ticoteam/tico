import { Link, useRouter } from '@tanstack/react-router';
import type { HTMLAttributes, ReactElement, ReactNode, Ref } from 'react';
import { useAction } from '@/actions';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, Menu, MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import type { Stage, TaskItem } from './queries';
import { leaveFlow } from '@/lib';

/** The head's Flow button: the play mark and how many tasks wait on you (parts-kit.js playBtn). On Flow it says so
    ("Flow · 3"), and × leaves it, back to the page Flow was entered from. It stays in a phone's head. */
export function FlowButton({ on, count }: { on: boolean; count: number }) {
  const router = useRouter();
  if (on)
    return (
      <span className="flow-on" data-target="tasks:flow" data-phone>
        <span className="li-play is-on">
          <FlowMark play live />
          <b>{count > 0 ? `Flow · ${count}` : 'Flow'}</b>
        </span>
        <IconButton icon="close" label="Exit Flow" quiet onClick={() => leaveFlow(router)} />
      </span>
    );
  return (
    <Link to="/flow" className="li-play" aria-pressed={false} aria-label="Flow" title="Flow" data-target="tasks:flow" data-phone>
      <FlowMark play />
      {count > 0 && <b>{count}</b>}
    </Link>
  );
}

/** A teammate's face from the directory; nothing for nobody. */
export function Face({ actor, size = 22 }: { actor: string | null | undefined; size?: 22 | 28 }) {
  const who = useActor(actor);
  if (!actor) return null;
  return <Avatar kind={who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? (actor.startsWith('bot:') ? 'Agent' : 'Person')} hue={who?.hue} live={who?.working} size={size} />;
}

/** What hangs in a row's margin: the Flow mark when it waits on you, else the stopped mark. */
export function markOf(t: Pick<TaskItem, 'turn' | 'stuck'>, me: string | undefined): ReactNode {
  if (me && t.turn?.state === 'person' && t.turn.who === me) return <FlowMark />;
  return t.stuck ? <StoppedMark /> : null;
}

/** The ladder brought the task to a person: stopped. */
export const StoppedMark = () => (
  <span className="stuck" title="Stopped">
    <Icon name="pause" />
  </span>
);

export const waitsOnMe = (t: Pick<TaskItem, 'turn'>, me: string | undefined) => !!me && t.turn?.state === 'person' && t.turn.who === me;

/** The address a task row opens: the task inside its pipeline. */
export const taskHref = (pipeline: string, t: Pick<TaskItem, 'id' | 'number'>) =>
  `/tasks/${encodeURIComponent(pipeline)}/${t.number ?? encodeURIComponent(t.id)}`;

/** A pending row (filed, not yet answered) is not a link and has no menu. */
export const isPending = (t: Pick<TaskItem, 'id'>) => t.id.startsWith('tmp:');

/**
 * A row or a card with its "Move to" menu: on "⋯" (a pointer or the keyboard) and on a right click or a long press.
 * The move is an action, so Tico moves a task the same way.
 */
export function Movable({
  task,
  stages,
  wrap,
  after,
  children,
}: {
  task: TaskItem;
  stages: readonly Stage[];
  /** The wrapper's own props: a card's drag. */
  wrap?: HTMLAttributes<HTMLDivElement> & { 'data-task'?: string; ref?: Ref<HTMLDivElement> };
  /** Beside the card, after its menu: a link of its own (Paused's Fix). */
  after?: ReactNode;
  children: ReactElement;
}) {
  const move = useAction('tasks.move');
  if (isPending(task)) return <div className="tk-item">{children}</div>;
  const items = stages.map((s) => (
    <MenuItem key={s.id} label={s.name} on={s.id === task.stage_id} onSelect={() => s.id !== task.stage_id && void move.run({ task: task.id, to: s.id })} />
  ));
  return (
    <div className="tk-item" {...wrap}>
      <ContextMenu items={items}>{children}</ContextMenu>
      <Menu trigger={<IconButton icon="more" label="Move to" size="sm" className="tk-more" />}>{items}</Menu>
      {after}
    </div>
  );
}

/** "Blocks E613, E633": the open tasks this one holds up, by their label (or number). */
export function Blocks({ t }: { t: { blocks?: readonly { label?: string | null; number: number | null }[] } }) {
  const names = (t.blocks ?? []).map((b) => b.label ?? (b.number != null ? `#${b.number}` : null)).filter(Boolean);
  return names.length ? <Pill>Blocks {names.join(', ')}</Pill> : null;
}
