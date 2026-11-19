import { useQuery } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import type { Call } from '@/api/schema';
import { ticoRoomQuery } from '@/conversation';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Popover } from '@/kit/Popover';
import { toast } from '@/kit/Toast';
import { CallBars, Dot, dot } from '@/tico/dot';
import { CallSide } from './CallSide';
import { running } from './model';
import { Face, inCall, press, useCards, useMe, useNameOf, useNow, type InCall } from './parts';
import { PeoplePick } from './PeoplePick';
import { callQuery, notesQuery } from './queries';
import { callState, forget, isActive, levelOf, useCall, type CallState, type Phase } from './session';
import './calls.css';

export interface CallPageProps {
  /** The call's id: `/call/<room>`. */
  room: string;
}

// Leaving this page keeps the call: the bar takes over on every other page (CallBar). Only Leave, closing the window
// or joining another call hangs up. A call that is over, or never joined, is let go when its page goes; that waits a tick
// so a page that unmounts and mounts again at once (Strict Mode, a route reload) keeps what it showed.
let letGo: ReturnType<typeof setTimeout> | undefined;
function useLetGoOnLeave(room: string) {
  useEffect(() => {
    clearTimeout(letGo);
    return () => {
      letGo = setTimeout(() => {
        const s = callState();
        if (s.callId === room && !isActive(s)) forget();
      }, 0);
    };
  }, [room]);
}

/** Back where the person came from, the call going on in the bar. */
function useBack(): () => void {
  const router = useRouter();
  return () => (router.history.canGoBack() ? router.history.back() : void router.navigate({ to: '/' }));
}

/**
 * The call window. On a desk (the `call` arrangement): the stage, what is said, the controls, with CallSide in the side
 * sheet. On a phone's call card (calls.js phoneCall): the faces in a row, what is said, chat, transcript and people,
 * the controls. Nothing joins until the person presses Join.
 */
export function CallPage({ room }: CallPageProps) {
  const s = useCall();
  const phase: Phase = s.callId === room ? s.phase : 'idle';
  const on = phase === 'live' || phase === 'reconnecting';
  const call = useQuery(callQuery(room));
  const ticoRoom = useQuery(ticoRoomQuery).data;
  const cards = useCards();
  const back = useBack();
  useLetGoOnLeave(room);
  const c = call.data;
  const ended = phase === 'ended' || (phase === 'idle' && c?.state === 'ended');
  const isTico = !!c && !!ticoRoom && ticoRoom === c.thread_id;

  if (call.isError && !c)
    return (
      <main className={cards ? 'pane pc cw' : 'pane cw is-full'}>
        <Empty title="Call did not load" icon="alert">
          <Button onClick={() => void call.refetch()}>Try again</Button>
        </Empty>
      </main>
    );

  const lead = cards ? <IconButton icon="chevdown" label="Back to the app" onClick={back} /> : null;
  return (
    <main className={cards ? 'pane pc cw' : 'pane cw is-full'} aria-label={c?.title || 'Call'} data-target={`page:/call/${room}`}>
      <Head c={c} live={on} lead={lead} />
      {ended ? <Ended room={room} /> : <Stage room={room} c={c} s={s} phase={phase} isTico={isTico} row={cards && !isTico} />}
      {cards && c && !ended && <CallSide room={room} />}
      {!ended && <Controls room={room} s={s} phase={phase} onBack={cards ? undefined : back} />}
    </main>
  );
}

function Head({ c, live, lead }: { c: Call | undefined; live: boolean; lead: ReactNode }) {
  const now = useNow(1000);
  const since = c ? (now.getTime() - new Date(c.started_at).getTime()) / 1000 : 0;
  return (
    <header className="cw-head">
      {lead}
      <div className="cw-t">
        <h1>{c?.title || 'Call'}</h1>
        {live && c && (
          <p className="cw-live">
            <i />
            Live
            <time>{running(since)}</time>
          </p>
        )}
      </div>
      <span className="grow" />
      <IconButton
        icon="link"
        label="Copy link"
        onClick={() =>
          void navigator.clipboard?.writeText(location.href).then(
            () => toast({ text: 'Link copied' }),
            () => toast({ text: "Couldn't copy", kind: 'failed' }),
          )
        }
      />
    </header>
  );
}

/** One way on from each thing that can go wrong. */
const TROUBLE: Partial<Record<Phase, { title: string; text?: string; icon: 'micoff' | 'offline'; act: string }>> = {
  'mic-refused': { title: 'Microphone blocked', text: 'Allow it for this site, then try again.', icon: 'micoff', act: 'Try again' },
  'no-mic': { title: 'No microphone found', icon: 'micoff', act: 'Try again' },
  reconnecting: { title: 'Reconnecting', icon: 'offline', act: 'Leave' },
  lost: { title: 'Call disconnected', icon: 'offline', act: 'Join again' },
};

function Stage({ room, c, s, phase, isTico, row }: { room: string; c: Call | undefined; s: CallState; phase: Phase; isTico: boolean; row: boolean }) {
  const live = phase === 'live';
  const nameOf = useNameOf();
  const me = useMe();
  const who = inCall(s, c, me);
  // Tico speaks through the call's one voice (session.ts `voice`).
  const ticoTalks = isTico && live && !!s.voice && s.speaking.includes(s.voice);
  const meTalks = live && !!s.me && !s.muted && s.speaking.includes(s.me);
  const voice = c?.voice?.bots[0]?.state;
  const add = live && !isTico && c ? <AddPeople room={room} row={row} /> : null;

  useEffect(() => {
    if (!isTico || !live) return undefined;
    if (ticoTalks && s.voice) return dot.set('speak', levelOf(s.voice));
    if (voice === 'thinking' || voice === 'making_voice') return dot.set('think');
    return undefined;
  }, [isTico, live, ticoTalks, s.voice, voice]);

  const trouble = TROUBLE[phase];
  if (trouble)
    return (
      <div className="win-stage">
        <Empty title={trouble.title} text={trouble.text} icon={trouble.icon}>
          <Button onClick={() => void (phase === 'reconnecting' ? press('calls.leave', {}) : press('calls.join', { id: room }))}>{trouble.act}</Button>
        </Empty>
      </div>
    );

  const said = s.caption ?? captionOf(c);
  const saidBy = said && said.who === s.voice ? (who.find((x) => x.talks && x.id.startsWith('bot:'))?.id ?? '') : (said?.who ?? '');
  const say = (
    <div className="say" aria-live="polite">
      {live && said && (
        <p>
          {saidBy && <b>{nameOf(saidBy)}</b>}
          {said.text}
        </p>
      )}
    </div>
  );
  const isMe = (id: string) => id === s.me || (!live && id === me);

  if (isTico)
    return (
      <>
        <div className="win-stage is-tico">
          <div className="tc-big">
            <span className="tc-orb is-big">
              <Dot size={148} />
            </span>
            <span className="spk-lvl">
              <CallBars signal={ticoTalks && s.voice ? levelOf(s.voice) : null} gone={meTalks} />
            </span>
          </div>
        </div>
        {say}
      </>
    );

  if (row)
    return (
      <>
        <div className="cw-faces" role="list" aria-label="In the call">
          {who.map((x) => (
            <span key={x.id} className={x.talks ? 'pf is-speaking' : 'pf'} role="listitem">
              <Face actor={x.id} size={44} speaking={x.talks} muted={x.muted} />
              <span className="pf-lvl">{x.talks && <CallBars signal={x.level} />}</span>
              <span className="pf-name">{isMe(x.id) ? 'You' : nameOf(x.id)}</span>
            </span>
          ))}
          {add}
        </div>
        {say}
      </>
    );

  return (
    <>
      <div className="win-stage">
        <div className="tiles">
          {who.map((x: InCall) => (
            <div key={x.id} className={x.talks ? 'tile is-speaking' : 'tile'}>
              <Face actor={x.id} size={96} speaking={x.talks} />
              <span className="tile-foot">
                <span className="tile-name">{nameOf(x.id)}</span>
                {x.talks && <CallBars signal={x.level} />}
                {x.muted && <Icon name="micoff" className="tile-off" label="Muted" />}
                <span className="grow" />
                {isMe(x.id) && <span className="tile-word">You</span>}
              </span>
            </div>
          ))}
          {add}
        </div>
      </div>
      {say}
    </>
  );
}

/** The dashed face at the end (calls.js pfAdd, addTile): ring a person in, or bring an agent. */
function AddPeople({ room, row }: { room: string; row: boolean }) {
  const [open, setOpen] = useState(false);
  const [who, setWho] = useState<string[]>([]);
  const trigger = row ? (
    <button type="button" className="pf pf-add" role="listitem" aria-label="Add people or agents" title="Add people or agents">
      <span className="face-add" style={{ '--f': '44px' } as CSSProperties}>
        <Icon name="plus" />
      </span>
    </button>
  ) : (
    <button type="button" className="tile tile-add" aria-label="Add people or agents" title="Add people or agents">
      <span className="face-add" style={{ '--f': '64px' } as CSSProperties}>
        <Icon name="plus" />
      </span>
    </button>
  );
  return (
    <Popover
      className="pop call-pick"
      label="Add people or agents"
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setWho([]);
      }}
      trigger={trigger}
    >
      <PeoplePick value={who} onChange={setWho} autoFocus />
      <Button
        variant="primary"
        className="pop-go"
        disabled={!who.length}
        onClick={() => {
          setOpen(false);
          void press('calls.add', { id: room, with: who });
        }}
      >
        Add
      </Button>
    </Popover>
  );
}

/** An agent's words as the server writes them, when the room sends no transcript of its own. */
function captionOf(c: Call | undefined): { who: string; text: string } | null {
  const b = c?.voice?.bots.find((x) => x.caption);
  return b?.caption ? { who: `bot:${b.slug}`, text: b.caption } : null;
}

function Controls({ room, s, phase, onBack }: { room: string; s: CallState; phase: Phase; onBack?: () => void }) {
  const on = phase === 'live' || phase === 'reconnecting';
  const meTalks = phase === 'live' && !!s.me && !s.muted && s.speaking.includes(s.me);
  if (!on)
    return (
      <footer className="win-ctl">
        <span className="grow" />
        {(phase === 'idle' || phase === 'joining') && (
          <Button variant="primary" className="cc-leave" icon="call" busy={phase === 'joining'} onClick={() => void press('calls.join', { id: room })}>
            Join
          </Button>
        )}
      </footer>
    );
  const word = s.muted ? 'Unmute' : 'Mute';
  return (
    <footer className="win-ctl">
      <button
        type="button"
        className={meTalks ? 'cc is-talk' : 'cc'}
        aria-pressed={s.muted}
        aria-label={word}
        title={word}
        onClick={() => void press('calls.toggleMute', {})}
      >
        <Icon name={s.muted ? 'micoff' : 'mic'} />
        {meTalks && s.me && <CallBars signal={levelOf(s.me)} />}
        <span className="cc-l">{word}</span>
      </button>
      {s.devices.length > 1 && (
        <Menu
          align="start"
          side="top"
          trigger={
            <button type="button" className="cc" aria-label="Microphone" title="Microphone">
              <Icon name="chevdown" className="flip" />
              <span className="cc-l">Microphone</span>
            </button>
          }
        >
          {s.devices.map((d) => (
            <MenuItem key={d.id} label={d.label} on={d.id === s.deviceId} onSelect={() => void press('calls.device', { id: d.id })} />
          ))}
        </Menu>
      )}
      {s.audioBlocked && (
        <button type="button" className="cc" aria-label="Play audio" title="Play audio" onClick={() => void press('calls.hear', {})}>
          <Icon name="play" />
          <span className="cc-l">Play audio</span>
        </button>
      )}
      <span className="grow" />
      {onBack && (
        <Button className="cw-back" icon="tobar" onClick={onBack}>
          Minimize
        </Button>
      )}
      <Button variant="primary" className="cc-leave" onClick={() => void press('calls.leave', {})}>
        Leave
      </Button>
    </footer>
  );
}

/** After the call: what it was about and what came of it, once the notes are written. */
// The summary is written after the call; it is read again while pending, for a few minutes at most.
const NOTES_EVERY = 4000;
const NOTES_FOR = 3 * 60_000;

function Ended({ room }: { room: string }) {
  const [since] = useState(() => Date.now());
  const notes = useQuery({ ...notesQuery(room), refetchInterval: (q) => (q.state.data?.pending && Date.now() - since < NOTES_FOR ? NOTES_EVERY : false) });
  const nameOf = useNameOf();
  const n = notes.data;
  return (
    <div className="win-stage">
      <div className="mv-body">
        <div className="mv-col">
          <section className="mv-sec">
            <div className="mv-sec-head">
              <h2 className="sec-name">Call ended</h2>
            </div>
            {n?.pending && <p className="mv-notes">Writing the summary</p>}
            {n && n.summary.length > 0 && (
              <ul className="mv-list">
                {n.summary.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            )}
          </section>
          {n && n.items.length > 0 && (
            <section className="mv-sec">
              <div className="mv-sec-head">
                <h2 className="sec-name">Action items</h2>
              </div>
              <ul className="acts">
                {n.items.map((it, i) => (
                  <li key={i} className="act">
                    <Face actor={it.owner} size={22} />
                    <span className="act-text">{it.title}</span>
                    <span className="row-age">{nameOf(it.owner)}</span>
                    <span />
                  </li>
                ))}
              </ul>
            </section>
          )}
          {n?.follow_ups && n.follow_ups.length > 0 && (
            <section className="mv-sec">
              <div className="mv-sec-head">
                <h2 className="sec-name">Tasks made</h2>
              </div>
              {n.follow_ups.map((f) => (
                <Link key={f.task_id} to="/task/$task" params={{ task: f.task_id }} className="rail-row">
                  <Icon name="tasks" />
                  <span>{f.title}</span>
                </Link>
              ))}
            </section>
          )}
          <Link to="/calendar" search={{ view: 'list' }} className="btn">
            Calendar
          </Link>
        </div>
      </div>
    </div>
  );
}
