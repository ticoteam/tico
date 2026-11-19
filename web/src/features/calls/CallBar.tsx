import { useQuery } from '@tanstack/react-query';
import { Link, useRouterState } from '@tanstack/react-router';
import { desktop, useCallWindow } from '@/desktop';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { CallBars } from '@/tico/dot';
import { BarMore } from './CallMore';
import { running } from './model';
import { Face, inCall, press, SMALL_WINDOW, useCards, useMe, useNameOf, useNow } from './parts';
import { callQuery } from './queries';
import { canShare, isActive, useCall, type CallState } from './session';
import './calls.css';

/**
 * The call bar (calls.js callBar): on every page while a call is on, except the call's own page. Open the call, mute,
 * leave. Moving between pages keeps the call; only Leave, closing the window or joining another hangs up.
 */
export function CallBar() {
  const s = useCall();
  const out = useCallWindow();
  const path = useRouterState({ select: (r) => r.location.pathname });
  if (s.callId && isActive(s)) return path === `/call/${s.callId}` ? null : <Bar room={s.callId} s={s} />;
  // the desktop app's small window holds the call: it is drawn there, and here only says so
  if (out.open && out.id && path !== `/call/${out.id}`) return <OutBar room={out.id} />;
  return null;
}

function OutBar({ room }: { room: string }) {
  const c = useQuery(callQuery(room)).data;
  return (
    <header className="cb" aria-label="Call">
      <Link to="/call/$room" params={{ room }} className="cb-what" title="Open call">
        <Icon name="call" />
        <b>{c?.title || 'Call'}</b>
      </Link>
      <p className="cb-cap">In the small window</p>
      <span />
    </header>
  );
}

function Bar({ room, s }: { room: string; s: CallState }) {
  const c = useQuery(callQuery(room)).data;
  const me = useMe();
  const nameOf = useNameOf();
  const cards = useCards();
  const now = useNow(1000);
  const since = s.startedAt ?? (c ? new Date(c.started_at).getTime() : now.getTime());
  const time = <time>{running((now.getTime() - since) / 1000)}</time>;
  const title = c?.title || 'Call';
  const who = inCall(s, c, me);
  const talker = who.find((x) => x.talks && x.id !== s.me) ?? who.find((x) => x.talks);
  const first = (id: string) => nameOf(id).split(' ')[0];
  const word = s.muted ? 'Unmute' : 'Mute';
  const mic = <IconButton icon={s.muted ? 'micoff' : 'mic'} label={word} on={s.muted} onClick={() => void press('calls.toggleMute', {})} />;

  if (cards)
    return (
      <header className="cb" aria-label="Call">
        <Link to="/call/$room" params={{ room }} className="cb-what" title="Open call">
          <Face actor={talker?.id ?? s.me ?? me ?? ''} size={28} speaking={!!talker} />
          <span className="cb-t">
            <b>{title}</b>
            {time}
          </span>
        </Link>
        {mic}
        <IconButton icon="leave" label="Leave" className="cb-hang" onClick={() => void press('calls.leave', {})} />
      </header>
    );

  // Captions off: what is said stays out of the bar.
  const said = s.captions ? s.caption : null;
  // Words in the agents' voice are the agent's who talks.
  const saidBy = said && said.who === s.voice ? (talker?.id ?? who.find((x) => x.id.startsWith('bot:'))?.id ?? '') : (said?.who ?? '');
  return (
    <header className="cb" aria-label="Call">
      <Link to="/call/$room" params={{ room }} className="cb-what" title="Open call">
        <Icon name="call" />
        <b>{title}</b>
        {time}
      </Link>
      <div className="cb-who">
        <span className="cb-faces">
          {who.map((x) => (
            <Face key={x.id} actor={x.id} size={22} speaking={x.talks} muted={x.muted} />
          ))}
        </span>
        {said ? (
          <p className="cb-cap">
            {saidBy && <b>{first(saidBy)}</b>}
            {said.text}
          </p>
        ) : (
          talker && (
            <span className="cb-say">
              <CallBars signal={talker.level} />
              <b>{first(talker.id)}</b>
            </span>
          )
        )}
      </div>
      <div className="cb-ctl">
        {mic}
        {canShare() && (
          <IconButton
            icon="share"
            label={s.sharing ? 'Stop sharing' : 'Share screen'}
            on={s.sharing}
            onClick={() => void press('calls.share', { id: room, on: !s.sharing })}
          />
        )}
        <IconButton icon="cc" label="Captions" on={s.captions} onClick={() => void press('calls.captions', { on: !s.captions })} />
        <Link to="/call/$room" params={{ room }} className="ib" aria-label="Open call" title="Open call">
          <Icon name="popout" />
        </Link>
        {desktop() && <IconButton icon="shrink" label={SMALL_WINDOW} onClick={() => void press('shell.openCallWindow', { id: room })} />}
        <button type="button" className="btn btn-sm cb-leave" onClick={() => void press('calls.leave', {})}>
          <Icon name="leave" />
          Leave
        </button>
        <BarMore room={room} c={c} me={s.me ?? me} />
      </div>
    </header>
  );
}
