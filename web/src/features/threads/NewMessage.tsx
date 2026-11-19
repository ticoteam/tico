import { useState } from 'react';
import { Composer } from '@/conversation';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { toast } from '@/kit/Toast';
import { press, useMe } from './people';

const SHOWN = 8;

export interface NewDraft {
  to: Actor[];
  setTo: (to: Actor[]) => void;
  name: string;
  setName: (name: string) => void;
  q: string;
  setQ: (q: string) => void;
}

/** The draft lives on the page so the people (in the reading column) and the box (in its foot) share it. */
export function useNewDraft(): NewDraft {
  const [to, setTo] = useState<Actor[]>([]);
  const [name, setName] = useState('');
  const [q, setQ] = useState('');
  return { to, setTo, name, setName, q, setQ };
}

/** Who it goes to: chips, then what is being typed, then the people and agents that match. */
export function NewMessage({ draft }: { draft: NewDraft }) {
  const { to, setTo, name, setName, q, setQ } = draft;
  const me = useMe();
  const actors = useActors();
  const words = q.trim().toLowerCase();
  const chosen = new Set(to.map((a) => a.id));
  const matches = actors
    .filter((a) => a.id !== me && !chosen.has(a.id))
    .filter((a) => !words || a.name.toLowerCase().includes(words) || a.team?.toLowerCase().includes(words))
    .slice(0, SHOWN);
  const add = (a: Actor) => {
    setTo([...to, a]);
    setQ('');
  };
  return (
    <>
      <div className="to">
        <span className="to-label" id="to-label">
          To
        </span>
        {to.map((a) => (
          <span key={a.id} className="to-chip">
            <Avatar kind={a.kind} name={a.name} hue={a.hue} size={16} decorative />
            {a.name}
            <button type="button" aria-label={`Remove ${a.name}`} onClick={() => setTo(to.filter((x) => x.id !== a.id))}>
              <Icon name="close" />
            </button>
          </span>
        ))}
        <input
          className="to-text"
          aria-labelledby="to-label"
          placeholder={to.length ? '' : 'Name a teammate'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches[0]) {
              e.preventDefault();
              add(matches[0]);
            } else if (e.key === 'Backspace' && !q && to.length) setTo(to.slice(0, -1));
          }}
          autoFocus
        />
      </div>
      {to.length >= 2 && (
        <label className="to to-name">
          <span className="to-label">Name</span>
          <input className="to-text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name this thread" />
        </label>
      )}
      {matches.length > 0 && (
        <div className="to-list" role="listbox" aria-label="Teammates">
          {matches.map((a) => (
            <button key={a.id} type="button" role="option" aria-selected={false} className="sr" onClick={() => add(a)}>
              <Avatar kind={a.kind} name={a.name} hue={a.hue} size={22} decorative />
              <span>{a.name}</span>
              <em>{a.title ?? a.team ?? (a.kind === 'agent' ? 'Agent' : '')}</em>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

/** The box: the first message. Sending makes the thread and lands in it. */
export function NewMessageFoot({ draft }: { draft: NewDraft }) {
  const me = useMe();
  const [fill, setFill] = useState<{ text: string } | null>(null);
  const { to, name } = draft;
  const send = async (text: string) => {
    if (!to.length || (to.length >= 2 && !name.trim())) {
      toast({ text: to.length ? 'Name the thread first' : 'Choose who it goes to', kind: 'failed' });
      setFill({ text });
      return;
    }
    const ok = await press('threads.new', { members: to.map((a) => a.id), body: text, ...(name.trim() ? { title: name.trim() } : {}) });
    if (!ok) setFill({ text });
  };
  if (!me) return null;
  return (
    <Composer
      id="new"
      me={me}
      to={to.length ? to.map((a) => a.name).join(', ') : 'a teammate'}
      onSend={(text) => void send(text)}
      fill={fill}
    />
  );
}
