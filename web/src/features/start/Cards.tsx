import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { CenteredCard } from '@/pagekinds';
import type { StartScreenKind } from './queries';
import './start.css';

/* Before the app can open: sign in, a refusal, signed out, and the two boot failures. The server's own pages do the
   signing in (/auth/...); these only point at them. They draw without the action layer, which is not up yet. */

const LOGOUT = '/api/v2/logout';
// The mock's down card tries again by itself after this long.
const AGAIN_S = 8;

// Tico support is a chat inside the app (ui/support.ts); with no app open there is nowhere to link, so the foot only names it.
const Support = () => <>Contact Tico support</>;

/** The server's sign-in path, coming back here after. */
export function signInHref(path: string, back = location.pathname + location.search + location.hash): string {
  return `${path}${path.includes('?') ? '&' : '?'}next=${encodeURIComponent(back)}`;
}

export interface StartScreenProps {
  screen: StartScreenKind;
  error: unknown;
  retry: () => void;
  busy?: boolean;
}

export function StartScreen({ screen, error, retry, busy = false }: StartScreenProps) {
  const api = error instanceof ApiError ? error : null;
  if (screen === 'signin')
    return (
      <CenteredCard
        title="Sign in to Tico"
        path="/signin"
        actions={
          <a className="btn btn-fill" href={signInHref(api?.signIn ?? '/auth/sign-in')}>
            Sign in
          </a>
        }
        foot={<Support />}
      />
    );
  if (screen === 'signed-out')
    return (
      <CenteredCard
        title="You're signed out"
        path="/signed-out"
        actions={
          <Button variant="primary" onClick={() => location.reload()}>
            Sign in
          </Button>
        }
        foot={<Support />}
      />
    );
  if (screen === 'refused')
    return (
      <CenteredCard
        title="You're not on the list"
        path="/refused"
        fault="alert"
        text={api?.message}
        actions={
          <a className="btn btn-fill" href={LOGOUT}>
            Use another account
          </a>
        }
        foot={<Support />}
      />
    );
  if (screen === 'timed-out')
    return (
      <CenteredCard
        title="Couldn't load Tico"
        path="/timed-out"
        fault="alert"
        text="Tico took too long to answer."
        actions={
          <Button variant="primary" busy={busy} onClick={retry}>
            Try again
          </Button>
        }
        foot={<Support />}
      />
    );
  return <Down retry={retry} busy={busy} />;
}

/** The server did not answer: it tries again by itself, and says when. */
function Down({ retry, busy }: { retry: () => void; busy: boolean }) {
  const [left, setLeft] = useState(AGAIN_S);
  useEffect(() => {
    if (busy) return;
    const t = setTimeout(() => {
      if (left > 1) return setLeft(left - 1);
      setLeft(AGAIN_S);
      retry();
    }, 1000);
    return () => clearTimeout(t);
  }, [left, busy, retry]);
  return (
    <CenteredCard
      title="Can't reach Tico"
      path="/down"
      fault="offline"
      text={busy ? 'Trying again.' : `The server did not answer. Trying again in ${left} seconds.`}
      body={
        <ul className="q-try">
          <li>Check your internet connection.</li>
          <li>If your company runs its own server, check that it is running.</li>
          <li>Still stuck? Write to Tico support.</li>
        </ul>
      }
      actions={
        <Button variant="primary" busy={busy} onClick={retry}>
          Try again
        </Button>
      }
      foot={<Support />}
    />
  );
}

/** One part of a page that did not load; the rest of the page, the menu and Tico still work. */
export function PartFault({ name, onRetry }: { name: string; onRetry: () => void }) {
  return (
    <Empty icon="alert" title={`${name} did not load`} text="Check your connection and try again. The menu and Tico still work.">
      <Button variant="primary" onClick={onRetry}>
        Try again
      </Button>
    </Empty>
  );
}
