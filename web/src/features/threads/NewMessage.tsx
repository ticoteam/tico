import { useInfiniteQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { refusal, refusalFor, run } from '@/actions';
import { Composer } from '@/conversation';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { toast } from '@/kit/Toast';
import { rowsOf } from './model';
import { useMe } from './people';
import { startWithFiles, threadListQuery } from './queries';

const SHOWN = 8;
const THREADS_SHOWN = 3;

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
  // A named thread that matches the words: going there may be what was meant (the old app offered it too).
  const list = useInfiniteQuery(threadListQuery);
  const threads = words
    ? rowsOf(list.data)
        .filter((t) => t.title?.toLowerCase().includes(words))
        .slice(0, THREADS_SHOWN)
    : [];
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
      {matches.length + threads.length > 0 && (
        <div className="to-list" role="listbox" aria-label="Suggestions">
          {matches.map((a) => (
            <button key={a.id} type="button" role="option" aria-selected={false} className="sr" onClick={() => add(a)}>
              <Avatar kind={a.kind} name={a.name} hue={a.hue} size={22} decorative />
              <span>{a.name}</span>
              <em>{a.title ?? a.team ?? (a.kind === 'agent' ? 'Agent' : '')}</em>
            </button>
          ))}
          {threads.map((t) => (
            <Link key={t.id} role="option" aria-selected={false} className="sr" to="/threads/$thread" params={{ thread: t.id }}>
              <span className="tl-kind">
                <Icon name="threads" />
              </span>
              <span>{t.title}</span>
              <em>Thread</em>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

/** The box: the first message. Sending makes the thread and lands in it. */
export function NewMessageFoot({ draft }: { draft: NewDraft }) {
  const me = useMe();
  const { to, name } = draft;
  const navigate = useNavigate();
  const send = async (text: string, files: File[]) => {
    if (!to.length || (to.length >= 2 && !name.trim())) {
      toast({ text: to.length ? 'Name the thread first' : 'Choose who it goes to', kind: 'failed' });
      return false;
    }
    const members = to.map((a) => a.id);
    const title = name.trim() || undefined;
    if (!files.length) {
      const r = await run('threads.new', { members, body: text, ...(title ? { title } : {}) }, { via: 'click' });
      if (!r.ok) toast(refusal(r, "Couldn't send"));
      // A rule can hold the first message: no thread is made, and the words stay in the box (new-message.ts).
      else if (!(r.out as { id?: string } | undefined)?.id) toast({ text: 'Held by a rule', kind: 'failed' });
      else return true;
      return false;
    }
    // Files are no action input: the DM is made, then the message goes up the conversation's own way.
    let refused: unknown;
    const t = await startWithFiles(members, text, files, me ?? '', title).catch((e: unknown) => ((refused = e), null));
    if (t?.id) {
      void navigate({ to: '/threads/$thread', params: { thread: t.id } });
      return true;
    }
    toast(refusalFor(refused, "Couldn't send"));
    return false;
  };
  if (!me) return null;
  return (
    <Composer
      key={me}
      id="new"
      me={me}
      to={to.length ? to.map((a) => a.name).join(', ') : 'a teammate'}
      onSend={send}
    />
  );
}
