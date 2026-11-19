import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

/** A Tico support sign-in that is on: when it ends (`/api/v2/support/access`, its `active`). */
export interface SupportSignIn {
  expires_at: string;
}

const left = (until: string, now: number): string => {
  const secs = Math.max(0, (new Date(until).getTime() - now) / 1000);
  if (secs >= 86400) return `${Math.round(secs / 86400)} days left`;
  if (secs >= 3600) return `${Math.round(secs / 3600)} hours left`;
  return `${Math.max(1, Math.round(secs / 60))} min left`;
};

/** One quiet line over every screen while Tico support is signed in to the company (Settings, Security, "Let Tico
    in"): how long is left, and Stop, which opens that setting. Nothing while no one is signed in. */
export function SupportLine({ session }: { session: SupportSignIn | null | undefined }) {
  const [now, setNow] = useState(Date.now);
  const on = !!session;
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, [on]);
  if (!session) return null;
  return (
    <p className="shell-note" role="status">
      <span>Tico support is signed in, {left(session.expires_at, now)}</span>
      <Link to="/settings/$section" params={{ section: 'security' }}>
        Stop
      </Link>
    </p>
  );
}
