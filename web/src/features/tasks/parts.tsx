import type { HTMLAttributes, ReactElement, ReactNode, Ref } from 'react';
import { useAction } from '@/actions';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { snoozeChoices } from './many';
import { waitsOnMe } from './needs';
import type { Stage, TaskItem } from './queries';

/** A teammate's face from the directory; nothing for nobody. */
export function Face({ actor, size = 22 }: { actor: string | null | undefined; size?: 22 | 28 }) {
  const who = useActor(actor);
  if (!actor) return null;
  return <Avatar kind={who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? (actor.startsWith('bot:') ? 'Agent' : 'Person')} hue={who?.hue} live={who?.working} size={size} />;
}

/** What hangs in a row's margin: the Flow mark when it waits on you (Needs you names it), else the stopped mark. */
export function markOf(t: Pick<TaskItem, 'id' | 'stuck'>, needs: ReadonlySet<string> | undefined): ReactNode {
  if (waitsOnMe(t, needs)) return <FlowMark />;
  return t.stuck ? <StoppedMark /> : null;
}

/** The ladder brought the task to a person: stopped. */
export const StoppedMark = () => (
  <span className="stuck" title="Stopped">
    <Icon name="pause" />
  </span>
);

export { waitsOnMe } from './needs';

/** The address a task row opens: the task inside its pipeline. */
export const taskHref = (pipeline: string, t: Pick<TaskItem, 'id' | 'number'>) =>
  `/tasks/${encodeURIComponent(pipeline)}/${t.number ?? encodeURIComponent(t.id)}`;

/** A pending row (filed, not yet answered) is not a link and has no menu. */
export const isPending = (t: Pick<TaskItem, 'id'>) => t.id.startsWith('tmp:');

/**
 * A row or a card with its menu: Move to a stage, Snooze ›, Close. On "⋯" (a pointer or the keyboard) and on a right
 * click or a long press. Each is an action, so Tico does it the same way; Close says so and takes it back.
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
  const snooze = useAction('tasks.snoozeMany');
  const close = useAction('tasks.moveMany');
  if (isPending(task)) return <div className="tk-item">{children}</div>;
  const items = (
    <>
      {stages.map((s) => (
        <MenuItem key={s.id} label={s.name} on={s.id === task.stage_id} onSelect={() => s.id !== task.stage_id && void move.run({ task: task.id, to: s.id })} />
      ))}
      <MenuRule />
      <MenuSub label="Snooze" icon="clock">
        {snoozeChoices().map((c) => (
          <MenuItem key={c.label} label={c.label} onSelect={() => void snooze.run({ tasks: [task.id], until: c.until })} />
        ))}
      </MenuSub>
      <MenuItem label="Close" icon="archive" onSelect={() => void close.run({ tasks: [task.id], to: 'closed' })} />
    </>
  );
  return (
    <div className="tk-item" {...wrap}>
      <ContextMenu items={items}>{children}</ContextMenu>
      <Menu trigger={<IconButton icon="more" label="Change" size="sm" className="tk-more" />}>{items}</Menu>
      {after}
    </div>
  );
}

/** "Blocks E613, E633": the open tasks this one holds up, by their label (or number). */
export function Blocks({ t }: { t: { blocks?: readonly { label?: string | null; number: number | null }[] } }) {
  const names = (t.blocks ?? []).map((b) => b.label ?? (b.number != null ? `#${b.number}` : null)).filter(Boolean);
  return names.length ? <Pill>Blocks {names.join(', ')}</Pill> : null;
}
