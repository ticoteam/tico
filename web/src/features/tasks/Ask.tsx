import type { ComponentProps } from 'react';
import { useAction } from '@/actions';
import { useActor, useActors } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Menu, MenuItem } from '@/kit/Menu';
import { useCards } from '@/kit/Popover';
import { Face, OwnerButton } from './parts';
import type { Pipeline } from './queries';

export interface BoardAgent {
  actor: string;
  /** "Manager", or the stages they hold. */
  role: string;
}

/** Who a pipeline can be asked about: its manager first, then each other teammate holding a stage, the stages they hold
    (the old app's pipeline-ask.ts paBots). */
export function boardAgents(p: Pipeline): BoardAgent[] {
  const manager = p.manager ?? null;
  const out: BoardAgent[] = manager ? [{ actor: manager, role: 'Manager' }] : [];
  const held = new Map<string, string[]>();
  for (const st of p.stages) if (st.owner && st.owner !== manager) held.set(st.owner, [...(held.get(st.owner) ?? []), st.name]);
  for (const [actor, names] of held) out.push({ actor, role: names.length === p.stages.length ? 'Holds every stage' : `Holds ${names.join(', ')}` });
  return out;
}

/**
 * The head's face. On a computer it is the owner's, and their chat opens beside the page. On a phone, where the head
 * keeps only what is marked for it, the face stays: with one agent on the board it opens their chat card at once, with
 * more it lists them (manager first) and the one picked comes up.
 */
export function AskButton({ p }: { p: Pipeline }) {
  const cards = useCards();
  const chat = useAction('tasks.chatWith');
  const actors = useActors();
  if (!cards) return p.owner ? <OwnerButton actor={p.owner} /> : null;
  const agents = boardAgents(p);
  const first = agents[0]?.actor ?? p.owner;
  if (!first) return null;
  if (agents.length < 2)
    return (
      <span className="tk-ask" data-phone>
        <OwnerButton actor={first} />
      </span>
    );
  const nameOf = (id: string) => actors.find((a) => a.id === id)?.name ?? 'Teammate';
  return (
    <span className="tk-ask" data-phone>
      <Menu trigger={<AskFace actor={first} />}>
        {agents.map((a) => (
          <MenuItem
            key={a.actor}
            label={nameOf(a.actor)}
            lead={<Face actor={a.actor} />}
            meta={a.role}
            onSelect={() => void chat.run({ pipeline: p.id, who: a.actor })}
          />
        ))}
      </Menu>
    </span>
  );
}

/** The menu's button: the first agent's face (the kit's owner button, which opens one chat, takes no menu). */
function AskFace({ actor, ...rest }: { actor: string } & ComponentProps<'button'>) {
  const who = useActor(actor);
  return (
    <button type="button" className="pg-owner" aria-label="Chat with…" title="Chat with…" {...rest}>
      <Avatar kind={who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? 'Teammate'} hue={who?.hue} live={who?.working} size={28} decorative />
    </button>
  );
}
