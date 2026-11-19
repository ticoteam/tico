import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type CSSProperties, type MouseEvent } from 'react';
import type { CalendarOccurrence } from '@/api/schema';
import { run } from '@/actions';
import { directoryQuery, meQuery, useActor } from '@/data';
import { Avatar, Faces, type AvatarSize } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { toast } from '@/kit/Toast';
import { joinOf } from './model';

/** Press an action as the person's click; say so when the server refuses it. */
export async function press(id: string, input: unknown): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast({ text: "Couldn't save", kind: 'failed' });
  return r.ok;
}

export const useMe = (): string | undefined => useQuery(meQuery).data?.id;

const bare = (actor: string) => actor.slice(actor.indexOf(':') + 1);
const kindOf = (actor: string) => (actor.startsWith('bot:') ? 'agent' : 'person');

/** A name for any actor id, from the directory. */
export function useNameOf(): (actor: string) => string {
  const { data } = useQuery(directoryQuery);
  return (actor) => data?.byId[actor]?.name ?? bare(actor);
}

/** On now: three ink bars that move (cal.css .live-mark). */
export const LiveMark = () => (
  <span className="live-mark" role="img" aria-label="Live now">
    <i />
    <i />
    <i />
  </span>
);

/** Someone in a call at any size: a face, a still ring while they speak, a mark when muted (calls.css .face). */
export function Face({ actor, size, speaking, muted }: { actor: string; size: number; speaking?: boolean; muted?: boolean }) {
  const a = useActor(actor);
  const style = { '--f': `${size}px`, '--x': `${size}px` } as CSSProperties;
  return (
    <span className={speaking ? 'face is-speaking' : 'face'} style={style}>
      <Avatar kind={a?.kind ?? kindOf(actor)} name={a?.name ?? bare(actor)} hue={a?.hue} size={36} className="av-x" />
      {muted && (
        <i className="face-muted" aria-label="Muted">
          <Icon name="micoff" />
        </i>
      )}
    </span>
  );
}

/** The faces of who is asked, from the directory; an outside guest is their email's initials. */
export function Who({ refs, size = 22, max = 4 }: { refs: readonly string[]; size?: AvatarSize; max?: number }) {
  const { data } = useQuery(directoryQuery);
  if (!refs.length) return null;
  const people = refs.map((r) => {
    const a = data?.byId[r];
    return { kind: a?.kind ?? kindOf(r), name: a?.name ?? (r.includes('@') ? r : bare(r)), hue: a?.hue };
  });
  return <Faces people={people} size={size} max={max} />;
}

/** Attendee refs as the calendar sends them: an actor id, or an outside email. */
export function refsOf(attendees: readonly unknown[]): string[] {
  return attendees
    .map((x) => {
      if (typeof x === 'string') return x;
      const o = (x ?? {}) as { ref?: unknown; email?: unknown };
      return typeof o.ref === 'string' ? o.ref : typeof o.email === 'string' ? o.email : '';
    })
    .filter(Boolean);
}

/** The time now, read again every `ms`. */
export function useNow(ms: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** Join from a press: a Tico call through its action, another app's link in a new tab. */
export function joinNow(o: CalendarOccurrence, e?: MouseEvent): boolean {
  const way = joinOf(o);
  if (!way) return false;
  e?.preventDefault();
  e?.stopPropagation();
  if ('call' in way) void press('calls.join', { id: way.call });
  else window.open(way.url, '_blank', 'noopener');
  return true;
}
