import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useEffect, useState, useSyncExternalStore, type CSSProperties, type MouseEvent } from 'react';
import type { CalendarOccurrence, Call } from '@/api/schema';
import { run } from '@/actions';
import { directoryQuery, meQuery, useActor } from '@/data';
import { isCallWindow, useCallWindow } from '@/desktop';
import { Avatar, Faces, type AvatarSize } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { toast } from '@/kit/Toast';
import { joinOf } from './model';
import { isActive, levelOf, useCall, type CallState } from './session';

/** The desktop app's control that moves the call out (the shell's `shell.openCallWindow`). */
export const SMALL_WINDOW = 'Open in a small window';

/** This page is the desktop app's small call window (`/call/<id>?window=1`). */
export const useSmallWindow = (): boolean => useLocation({ select: (l) => isCallWindow(l.pathname, l.searchStr) });

/** The desktop app's small window holds this call now: the main window shows "In the small window" (platforms.md 4.6). */
export function useOut(id: string): boolean {
  const w = useCallWindow();
  return w.open && w.id === id;
}

/** In this call here, or out in the small window: either way there is nothing to join. */
export function useInCall(id: string): boolean {
  const s = useCall();
  const out = useOut(id);
  return out || (s.callId === id && isActive(s));
}

/** Press an action as the person's click; say so when the server refuses it. */
export async function press(id: string, input: unknown): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast({ text: "Couldn't save", name: r.why, kind: 'failed' });
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

const subscribeLayout = (f: () => void) => {
  const o = new MutationObserver(f);
  o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-layout'] });
  return () => o.disconnect();
};
/** A phone's cards (the shell sets html[data-layout]): the call is one card, the bar is small. */
export const useCards = () =>
  useSyncExternalStore(
    subscribeLayout,
    () => document.documentElement.dataset.layout === 'cards',
    () => false,
  );

/** Someone on a call as the faces draw them. `level` is their voice now, while they talk. */
export interface InCall {
  id: string;
  talks: boolean;
  muted: boolean;
  level: (() => number) | null;
}

/**
 * Who is on the call: in the room, the people LiveKit names, and the call's agents, who speak through its one voice
 * (session.ts `voice`); before the room, the people the call says are in it.
 */
export function inCall(s: CallState, c: Call | undefined, me: string | undefined): InCall[] {
  const live = s.phase === 'live' && s.callId === c?.id;
  const people = live ? [s.me ?? me ?? '', ...s.others] : (c?.participants ?? []).filter((p) => p.in_call).map((p) => p.actor);
  const agents = (c?.bots ?? []).map((b) => (b.startsWith('bot:') ? b : `bot:${b}`));
  const voiceTalks = live && !!s.voice && s.speaking.includes(s.voice);
  // The voice is the agents': the one the server says is speaking, else the only one.
  const said = c?.voice?.bots.find((b) => b.state === 'speaking')?.slug;
  const talker = voiceTalks ? (said ? `bot:${said}` : agents.length === 1 ? agents[0] : null) : null;
  const out: InCall[] = [];
  for (const id of [...people, ...agents]) {
    if (!id || out.some((x) => x.id === id)) continue;
    const agent = id.startsWith('bot:');
    const talks = agent ? id === talker : live && s.speaking.includes(id) && !(id === s.me && s.muted);
    const muted = live && !agent && (id === s.me ? s.muted : s.mutedIds.includes(id));
    out.push({ id, talks, muted, level: talks ? levelOf(agent ? (s.voice as string) : id) : null });
  }
  return out;
}
