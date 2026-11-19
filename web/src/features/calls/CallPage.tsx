import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useSyncExternalStore } from 'react';
import type { Call } from '@/api/schema';
import { ticoRoomQuery } from '@/conversation';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { CallBars, Dot, dot } from '@/tico/dot';
import { CallSide } from './CallSide';
import { running } from './model';
import { Face, press, useMe, useNameOf, useNow } from './parts';
import { callQuery, notesQuery } from './queries';
import { callState, forget, isActive, leave, levelOf, useCall, type CallState, type Phase } from './session';
import './calls.css';

export interface CallPageProps {
  /** The call's id: `/call/<room>`. */
  room: string;
}

// React may unmount and mount a page again at once (Strict Mode, a route reload); the hang-up waits a tick and is
// called off when the page comes straight back.
let hangUp: ReturnType<typeof setTimeout> | undefined;
function useHangUpOnLeave(room: string) {
  useEffect(() => {
    clearTimeout(hangUp);
    return () => {
      hangUp = setTimeout(() => {
        const s = callState();
        if (s.callId !== room) return;
        if (isActive(s)) void leave();
        else forget();
      }, 0);
    };
  }, [room]);
}

const subscribeLayout = (f: () => void) => {
  const o = new MutationObserver(f);
  o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-layout'] });
  return () => o.disconnect();
};
/** A phone's cards: the call's chat and people sit under the stage, since there is no side sheet. */
const useCards = () =>
  useSyncExternalStore(
    subscribeLayout,
    () => document.documentElement.dataset.layout === 'cards',
    () => false,
  );

/** The call window: who is in it and who speaks, what is said, Mute and Leave. Nothing joins until the person presses. */
export function CallPage({ room }: CallPageProps) {
  const s = useCall();
  const phase: Phase = s.callId === room ? s.phase : 'idle';
  const on = phase === 'live' || phase === 'reconnecting';
  const call = useQuery({ ...callQuery(room), refetchInterval: on ? 5000 : false });
  const ticoRoom = useQuery(ticoRoomQuery).data;
  const cards = useCards();
  useHangUpOnLeave(room);
  const c = call.data;
  const ended = phase === 'ended' || (phase === 'idle' && c?.state === 'ended');
  const isTico = !!c && !!ticoRoom && ticoRoom === c.thread_id;

  if (call.isError && !c)
    return (
      <main className="pane cw is-full">
        <Empty title="Call did not load" icon="alert">
          <Button onClick={() => void call.refetch()}>Try again</Button>
        </Empty>
      </main>
    );

  return (
    <main className="pane cw is-full" aria-label={c?.title || 'Call'} data-target={`page:/call/${room}`}>
      <Head c={c} live={on} />
      {ended ? <Ended room={room} /> : <Stage room={room} c={c} s={s} phase={phase} isTico={isTico} />}
      {!ended && <Controls room={room} s={s} phase={phase} />}
      {cards && c && <CallSide room={room} />}
    </main>
  );
}

function Head({ c, live }: { c: Call | undefined; live: boolean }) {
  const now = useNow(1000);
  const since = c ? (now.getTime() - new Date(c.started_at).getTime()) / 1000 : 0;
  return (
    <header className="cw-head">
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

function Stage({ room, c, s, phase, isTico }: { room: string; c: Call | undefined; s: CallState; phase: Phase; isTico: boolean }) {
  const live = phase === 'live';
  const nameOf = useNameOf();
  const me = useMe();
  // Tico's voice in the room is its agent's; the call names which.
  const ticoId = isTico ? (s.others.find((i) => i.startsWith('bot:')) ?? null) : null;
  const ticoTalks = live && !!ticoId && s.speaking.includes(ticoId);
  const meTalks = live && !!s.me && !s.muted && s.speaking.includes(s.me);
  const voice = c?.voice?.bots[0]?.state;

  useEffect(() => {
    if (!isTico || !live) return undefined;
    if (ticoTalks && ticoId) return dot.set('speak', levelOf(ticoId));
    if (voice === 'thinking' || voice === 'making_voice') return dot.set('think');
    return undefined;
  }, [isTico, live, ticoTalks, ticoId, voice]);

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
  return (
    <>
      {isTico ? (
        <div className="win-stage is-tico">
          <div className="tc-big">
            <span className="tc-orb is-big">
              <Dot size={148} />
            </span>
            <span className="spk-lvl">
              <CallBars signal={ticoTalks && ticoId ? levelOf(ticoId) : null} gone={meTalks} />
            </span>
          </div>
        </div>
      ) : (
        <div className="win-stage">
          <div className="tiles">
            {(live ? [s.me ?? me ?? '', ...s.others] : (c?.participants ?? []).filter((p) => p.in_call).map((p) => p.actor)).filter(Boolean).map((id) => {
              const talks = live && s.speaking.includes(id) && !(id === s.me && s.muted);
              const muted = id === s.me ? s.muted : s.mutedIds.includes(id);
              return (
                <div key={id} className={talks ? 'tile is-speaking' : 'tile'}>
                  <Face actor={id} size={96} speaking={talks} />
                  <span className="tile-foot">
                    <span className="tile-name">{nameOf(id)}</span>
                    {talks && <CallBars signal={levelOf(id)} />}
                    {live && muted && <Icon name="micoff" className="tile-off" label="Muted" />}
                    <span className="grow" />
                    {(id === s.me || (!live && id === me)) && <span className="tile-word">You</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="say" aria-live="polite">
        {live && said && (
          <p>
            <b>{said.who ? nameOf(said.who) : ''}</b>
            {said.text}
          </p>
        )}
      </div>
    </>
  );
}

/** An agent's words as the server writes them, when the room sends no transcript of its own. */
function captionOf(c: Call | undefined): { who: string; text: string } | null {
  const b = c?.voice?.bots.find((x) => x.caption);
  return b?.caption ? { who: `bot:${b.slug}`, text: b.caption } : null;
}

function Controls({ room, s, phase }: { room: string; s: CallState; phase: Phase }) {
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
        onClick={() => void press('calls.mute', { on: !s.muted })}
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
      <Button variant="primary" className="cc-leave" onClick={() => void press('calls.leave', {})}>
        Leave
      </Button>
    </footer>
  );
}

/** After the call: what it was about and what came of it, once the notes are written. */
function Ended({ room }: { room: string }) {
  const notes = useQuery({ ...notesQuery(room), refetchInterval: (q) => (q.state.data?.pending ? 4000 : false) });
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
