import { useQuery } from '@tanstack/react-query';
import { useRouterState } from '@tanstack/react-router';
import { Button } from '@/kit/Button';
import { Face, press, useNameOf } from './parts';
import { ringingQuery } from './queries';
import { useDeclined } from './ring';
import { isActive, useCall } from './session';
import './calls.css';

/**
 * A call ringing this person (calls.js ring): who calls and what about, Decline and Answer. The list is the server's
 * (GET /calls/ringing), read again on each `call.*` change in the threads topic (live/patch.ts), so a ring shows
 * and goes without a timer. Not the call they are in, nor one whose page is open.
 */
export function RingCard() {
  const rings = useQuery(ringingQuery).data?.calls ?? [];
  const declined = useDeclined();
  const s = useCall();
  const path = useRouterState({ select: (r) => r.location.pathname });
  const nameOf = useNameOf();
  const c = rings.find((x) => x.ring && x.state === 'live' && !declined.includes(x.id) && !(x.id === s.callId && isActive(s)) && path !== `/call/${x.id}`);
  if (!c) return null;
  return (
    <aside className="ring" role="alertdialog" aria-label="Incoming call">
      <div className="ring-who">
        <Face actor={c.connector} size={36} />
        <span className="ring-t">
          <b>{nameOf(c.connector).split(' ')[0]}</b>
          {c.title && <span>{c.title}</span>}
        </span>
      </div>
      <div className="ring-row">
        <Button onClick={() => press('calls.decline', { id: c.id })}>Decline</Button>
        <Button variant="primary" icon="call" onClick={() => press('calls.join', { id: c.id })}>
          Answer
        </Button>
      </div>
    </aside>
  );
}
