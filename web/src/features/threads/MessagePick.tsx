import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors, type Actor } from '@/data';
import { groupsQuery } from '@/features/team/routing';
import { Avatar } from '@/kit/Avatar';
import { Dialog } from '@/kit/Dialog';
import { Icon } from '@/kit/Icon';
import { press, useMe } from './people';
import './threads.css';

const SHOWN = 8;

export interface MessagePickProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Hit = { kind: 'team'; id: string; name: string } | { kind: 'mate'; a: Actor } | { kind: 'set'; name: string };

/**
 * Message (plan/threads-team.html "Message and Call"): a picker that only goes somewhere. A team goes to its thread; a
 * name becomes a chip, and the chips go to your DM with that one teammate, or to the one group DM for that set.
 * Nothing is named and nothing is sent here.
 */
export function MessagePick({ open, onOpenChange }: MessagePickProps) {
  const me = useMe();
  const actors = useActors();
  const { data: groups = [] } = useQuery(groupsQuery);
  const [to, setTo] = useState<Actor[]>([]);
  const [q, setQ] = useState('');
  const [on, setOn] = useState(0);
  const words = q.trim().toLowerCase();
  const chosen = new Set(to.map((a) => a.id));
  const has = (s: string | undefined) => !!s && s.toLowerCase().includes(words);
  const hits: Hit[] =
    to.length && !words
      ? [{ kind: 'set', name: to.map((a) => (to.length > 1 ? a.name.split(/\s+/)[0] : a.name)).join(', ') }]
      : [
          ...(to.length || !words ? [] : groups.filter((g) => has(g.name)).map((g): Hit => ({ kind: 'team', id: g.id, name: g.name }))),
          ...actors.filter((a) => a.id !== me && !chosen.has(a.id) && (!words || has(a.name) || has(a.team))).map((a): Hit => ({ kind: 'mate', a })),
        ].slice(0, SHOWN);
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) {
      setTo([]);
      setQ('');
      setOn(0);
    }
  };
  const go = (ids: string[]) => {
    close(false);
    void press('threads.message', { with: ids });
  };
  const pick = (h: Hit | undefined) => {
    if (!h) return;
    if (h.kind === 'team') go([`group:${h.id}`]);
    else if (h.kind === 'set') go(to.map((a) => a.id));
    else {
      setTo([...to, h.a]);
      setQ('');
      setOn(0);
    }
  };
  return (
    <Dialog open={open} onOpenChange={close} title="Message" className="msg-pick">
      <div className="to">
        <Icon name="message" />
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
          role="combobox"
          aria-expanded={hits.length > 0}
          aria-controls="msg-hits"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOn(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              pick(hits[on]);
            } else if (e.key === 'ArrowDown') {
              e.preventDefault();
              setOn(Math.min(on + 1, hits.length - 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setOn(Math.max(on - 1, 0));
            } else if (e.key === 'Backspace' && !q && to.length) setTo(to.slice(0, -1));
          }}
          autoFocus
        />
      </div>
      {hits.length ? (
        <div className="to-list" role="listbox" id="msg-hits" aria-label="Who">
          {hits.map((h, i) => (
            <button
              key={h.kind === 'team' ? `team:${h.id}` : h.kind === 'mate' ? h.a.id : 'set'}
              type="button"
              role="option"
              aria-selected={i === on}
              className={i === on ? 'sr is-on' : 'sr'}
              onClick={() => pick(h)}
            >
              {h.kind === 'mate' ? (
                <Avatar kind={h.a.kind} name={h.a.name} hue={h.a.hue} size={22} decorative />
              ) : h.kind === 'team' ? (
                <span className="tl-kind">
                  <Icon name="people" />
                </span>
              ) : to.length === 1 && to[0] ? (
                <Avatar kind={to[0].kind} name={to[0].name} hue={to[0].hue} size={22} decorative />
              ) : (
                <span className="tl-kind">
                  <Icon name="message" />
                </span>
              )}
              <span>{h.kind === 'mate' ? h.a.name : h.name}</span>
            </button>
          ))}
        </div>
      ) : null}
    </Dialog>
  );
}
