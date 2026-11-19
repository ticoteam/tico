import { Link, useLocation } from '@tanstack/react-router';
import type { HTMLAttributes, ReactElement, ReactNode } from 'react';
import { useAction } from '@/actions';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link as KitLink } from '@/kit/Link';
import { ContextMenu, Menu, MenuItem } from '@/kit/Menu';
import type { Stage, TaskItem } from './queries';

const FLOW_PLAY = 'M16 5.5C8 4 4.5 12 5 24 4.5 36 8 44 16 42.5 27 40 45 32 45 24 45 16 27 8 16 5.5Z';

/** The head's Flow button: the play mark and how many tasks wait on you (parts-kit.js playBtn). On Flow it goes back. */
export function FlowButton({ on, count }: { on: boolean; count: number }) {
  return (
    <Link
      to={on ? '/tasks' : '/flow'}
      className={on ? 'li-play is-on' : 'li-play'}
      aria-pressed={on}
      aria-label="Flow"
      title="Flow"
      data-target="tasks:flow"
    >
      <span className={on ? 'flow-mark live' : 'flow-mark'} style={{ width: 22 }} aria-hidden="true">
        <svg viewBox="0 0 48 48" focusable="false">
          <g className="f1">
            <path d={FLOW_PLAY} transform="translate(-.5 7.2) scale(.7)" />
          </g>
          <g className="f2">
            <path d={FLOW_PLAY} transform="translate(10.5 7.2) scale(.7)" />
          </g>
        </svg>
      </span>
      {count > 0 && <b>{count}</b>}
    </Link>
  );
}

/** A teammate's face from the directory; nothing for nobody. */
export function Face({ actor, size = 22 }: { actor: string | null | undefined; size?: 22 | 28 }) {
  const who = useActor(actor);
  if (!actor) return null;
  return <Avatar kind={who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? 'Teammate'} hue={who?.hue} live={who?.working} size={size} />;
}

/** The page owner's face in the head: it opens their chat beside the page (`?chat=`, the shell's). */
export function OwnerButton({ actor }: { actor: string }) {
  const who = useActor(actor);
  const name = who?.name ?? 'the owner';
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps the page's own filters.
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      q.set('chat', actor);
      return `${l.pathname}?${q.toString()}`;
    },
  });
  return (
    <KitLink href={href} className="pg-owner" aria-label={`Chat with ${name}`} title={`Chat with ${name}`}>
      <Face actor={actor} size={28} />
    </KitLink>
  );
}

/** What hangs in a row's margin: the Flow mark when it waits on you, else the stopped mark. */
export function markOf(t: Pick<TaskItem, 'turn' | 'stuck'>, me: string | undefined): ReactNode {
  if (me && t.turn?.state === 'person' && t.turn.who === me) return <FlowMark />;
  if (t.stuck)
    return (
      <span className="stuck" title="Stopped">
        <Icon name="pause" />
      </span>
    );
  return null;
}

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
  children,
}: {
  task: TaskItem;
  stages: readonly Stage[];
  /** The wrapper's own handlers: a card's drag. */
  wrap?: HTMLAttributes<HTMLDivElement>;
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
    </div>
  );
}
