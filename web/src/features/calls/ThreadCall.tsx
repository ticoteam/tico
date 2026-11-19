import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import type { Call } from '@/api/schema';
import { jumpTo } from '@/conversation';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Popover } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { callCount, callFaces, callLength, callWhen, running } from './model';
import { LiveMark, press, useMe, useNow, Who } from './parts';
import { PickRow } from './PeoplePick';
import { callQuery, threadCallsQuery } from './queries';
import { isActive, useCall } from './session';
import './calls.css';

/** A thread with more people than this rings nobody (backend/call_ring.py RING_MAX). */
const RING_MAX = 8;

export interface ThreadCallHeadProps {
  thread: string;
  /** Everyone in the thread but the viewer: `human:<id>` and `bot:<id>`. */
  members: readonly string[];
  /** The viewer may post here (the thread's `me.can_post`); without it there is no Call, only Join. */
  canPost: boolean;
}

/**
 * The call control in a thread's head (calls.js startPage): the phone, which opens who to call; Join while a call is
 * on; "In call" while the person is in it; a booked call's time. Nothing while the thread's calls cannot be read.
 */
export function ThreadCallHead({ thread, members, canPost }: ThreadCallHeadProps) {
  const { data } = useQuery(threadCallsQuery(thread));
  const now = useNow(30_000);
  if (!data) return null;
  const live = data.calls.find((c) => c.state === 'live');
  if (live) return <JoinHere id={live.id} />;
  const next = data.next_scheduled;
  if (next?.state === 'scheduled')
    return (
      <Button className="btn-sm" icon="call" title="Join" onClick={() => void press('calls.join', { id: next.id })}>
        {callWhen(next.scheduled_start, now)}
      </Button>
    );
  if (!data.available || !canPost) return null;
  return <StartHere thread={thread} members={members} providers={data.providers} />;
}

/** A call that is on here: Join (with how many are in it), or "In call" when this window is. */
function JoinHere({ id }: { id: string }) {
  const { data: c } = useQuery(callQuery(id));
  const s = useCall();
  if (s.callId === id && isActive(s))
    return (
      <Button className="btn-sm" icon="call" onClick={() => void press('calls.open', { id })}>
        In call
      </Button>
    );
  const n = c ? callCount(c) : 0;
  return (
    // Disabled until the call is read: a Google Meet opens its tab in the press, a voice call asks for the microphone.
    <Button className="btn-sm" aria-label="Join call" disabled={!c} onClick={() => void press('calls.join', { id })}>
      <LiveMark />
      Join
      {n > 0 && <span className="call-card-n">{n}</span>}
    </Button>
  );
}

const KINDS = [
  { value: 'voice', label: 'Voice call' },
  { value: 'meet', label: 'Google Meet' },
] as const;

/** Who to call (calls.js callPop): a voice call rings the people and brings in the agents picked; a Google Meet has no agents. */
function StartHere({ thread, members, providers }: { thread: string; members: readonly string[]; providers: readonly string[] }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<'voice' | 'meet'>(providers.includes('tico') ? 'voice' : 'meet');
  const [left, setLeft] = useState<string[]>([]);
  const meet = kind === 'meet';
  const people = members.filter((a) => !a.startsWith('bot:'));
  const agents = members.filter((a) => a.startsWith('bot:'));
  const rings = people.length + 1 <= RING_MAX;
  const go = () => {
    setOpen(false);
    void press('calls.startIn', { thread, meet, agents: meet ? [] : agents.filter((a) => !left.includes(a)) });
  };
  return (
    <Popover
      className="pop call-pop"
      label="Call"
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setLeft([]);
      }}
      align="end"
      trigger={<IconButton icon="call" label="Call" on={open || undefined} aria-haspopup="dialog" />}
    >
      {providers.includes('tico') && providers.includes('google_meet') && (
        <Segmented label="Kind of call" options={KINDS} value={kind} onValueChange={setKind} />
      )}
      {members.length > 0 && (
        <div className="pick" role="group" aria-label="Who">
          {people.map((a) => (
            <Member key={a} actor={a} on={rings} />
          ))}
          {agents.map((a) => (
            <Member
              key={a}
              actor={a}
              on={!meet && !left.includes(a)}
              off={meet}
              onPress={() => setLeft(left.includes(a) ? left.filter((x) => x !== a) : [...left, a])}
            />
          ))}
        </div>
      )}
      <Button variant="primary" className="pop-go" icon={meet ? 'link' : 'call'} onClick={go}>
        {meet ? 'Post link' : 'Start call'}
      </Button>
    </Popover>
  );
}

function Member({ actor, on, off, onPress }: { actor: string; on: boolean; off?: boolean; onPress?: () => void }) {
  const a = useActor(actor);
  const name = a?.name ?? actor.slice(actor.indexOf(':') + 1);
  const kind = a?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person');
  return <PickRow on={on} off={off} label={name} onPress={onPress} lead={<Avatar kind={kind} name={name} hue={a?.hue} size={22} decorative />} />;
}

/** The call a message is the card of (the server posts one when a call starts: `refs.call.card`), or null. */
export function callCardOf(m: { refs?: Record<string, unknown> | null }): string | null {
  const c = m.refs?.call as { id?: unknown; card?: unknown } | undefined;
  return c && c.card && typeof c.id === 'string' && c.id ? c.id : null;
}

/** What a conversation draws for a message in place of its text: a call's card, or null for any other message. */
export function callCard(m: { refs?: Record<string, unknown> | null }): ReactNode {
  const id = callCardOf(m);
  return id ? <CallCard id={id} /> : null;
}

/**
 * A call's card in its thread (thread-call.ts callCardHTML): booked, when and Join; on, who is in it, how long, Join
 * (and End for whoever started a Google Meet); over, how long it was and its transcript, or missed.
 */
export function CallCard({ id }: { id: string }) {
  const { data: c } = useQuery(callQuery(id));
  const s = useCall();
  const me = useMe();
  const live = c?.state === 'live';
  const missed = !!c && c.state === 'ended' && c.provider === 'google_meet' && c.duration_s == null && !!c.status_source;
  const title =
    c?.provider === 'tico' && c.state !== 'scheduled' ? (
      <Link className="call-card-t" to="/call/$room" params={{ room: id }}>
        Call
      </Link>
    ) : (
      <b className="call-card-t">Call</b>
    );
  return (
    <div className={['call-card', live && 'is-live', missed && 'is-missed'].filter(Boolean).join(' ')} data-target={`call:${id}`}>
      <span className="call-card-ic">{live ? <LiveMark /> : <Icon name="call" />}</span>
      {title}
      {c && <CardBody c={c} me={me} inIt={s.callId === id && isActive(s)} missed={missed} />}
    </div>
  );
}

function CardBody({ c, me, inIt, missed }: { c: Call; me: string | undefined; inIt: boolean; missed: boolean }) {
  if (c.state === 'scheduled') return <Scheduled c={c} />;
  if (c.state === 'live') {
    const blind = c.provider === 'google_meet' && !c.status_source;
    return (
      <>
        <Who refs={callFaces(c)} />
        {!blind && <Since at={c.seen_at || c.started_at} />}
        <span className="call-card-go">
          {c.provider === 'google_meet' && c.connector === me && (
            <Menu trigger={<Button className="btn-sm">End</Button>}>
              <MenuItem label="End the call for everyone" onSelect={() => void press('calls.end', { id: c.id })} />
            </Menu>
          )}
          {inIt ? (
            <Button className="btn-sm" onClick={() => void press('calls.open', { id: c.id })}>
              Open
            </Button>
          ) : (
            <Button className="btn-sm" variant="primary" onClick={() => void press('calls.join', { id: c.id })}>
              Join
            </Button>
          )}
        </span>
      </>
    );
  }
  const transcript = c.transcript_message_id;
  return (
    <>
      <span className="call-card-n">{missed ? 'Missed' : callLength(c.duration_s)}</span>
      {transcript && (
        <span className="call-card-go">
          <Button className="btn-sm" variant="quiet" onClick={() => jumpTo(c.thread_id, transcript)}>
            Transcript
          </Button>
        </span>
      )}
    </>
  );
}

function Scheduled({ c }: { c: Call }) {
  const now = useNow(30_000);
  return (
    <>
      <span className="call-card-n">{callWhen(c.scheduled_start, now)}</span>
      <span className="call-card-go">
        <Button className="btn-sm" onClick={() => void press('calls.join', { id: c.id })}>
          Join
        </Button>
      </span>
    </>
  );
}

/** How long it has been on, moving each second. */
function Since({ at }: { at: string }) {
  const now = useNow(1000);
  return <span className="call-card-n">{running((now.getTime() - new Date(at).getTime()) / 1000)}</span>;
}
