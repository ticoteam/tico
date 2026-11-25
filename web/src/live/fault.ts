import { useEffect, useRef } from 'react';
import { ApiError } from '@/api/client';
import { liveStatus, onLiveAgain } from './stream';

// A read that failed with nothing to show: one plain line saying why, the cause logged once, and, when trying again
// could help, read again by itself once the stream is back (a release's restart drops both; the stream says when the
// server answers again).

/** Nothing pushes when a read failed while the stream stayed up (a read that gave up just before the stream came
    back): read again this often meanwhile, while the stream is live and the page is in view. */
const BACKSTOP_MS = 5 * 60_000;

/** Why a read failed, in two or three words. */
export function faultReason(err: unknown): string {
  if (!(err instanceof ApiError)) return 'app error';
  if (err.status === 0) return navigator.onLine === false ? 'offline' : 'no connection';
  if (err.code === 'stopped') return 'company stopped';
  if (err.status === 503 && err.code === 'starting') return 'server restarting';
  if (err.status === 503 && err.code === 'busy') return 'server busy';
  if (err.status >= 500) return 'server error';
  if (err.status === 401) return 'signed out';
  if (err.status === 403) return 'no access';
  if (err.status === 404) return 'not found';
  return 'request refused';
}

/** Whether reading again may answer differently: no answer, or the server's own failure (not a stopped company). A
    refusal (4xx) or the app's own error would only fail again; Try again stays for those. */
export const passing = (err: unknown) => err instanceof ApiError && (err.status === 0 || err.status >= 500) && err.code !== 'stopped';

/** A read that failed: its error, and how to read it again (a query's own result fits). */
export interface FailedRead {
  error: unknown;
  refetch: (opts: { cancelRefetch: boolean }) => unknown;
}

/** The failed read that says why: the first that reading again would not fix (what Try again is there for), else the
    first. */
const firstFault = (failed: readonly FailedRead[]) => failed.find((r) => !passing(r.error)) ?? failed[0];

/**
 * The line for reads of `what` that failed (`failed`: those whose fault is on screen; firstFault's error says why),
 * or null while none has. Each failed read that may pass is read again each time the stream is live again, and
 * every BACKSTOP_MS meanwhile, joining a read already on its way rather than sending another. A fault is logged once,
 * through the attempts that fail again, until a read succeeds (`ok`).
 */
export function useReadFault(what: string, failed: readonly FailedRead[], ok: boolean): string | null {
  const reads = useRef(failed);
  useEffect(() => {
    reads.current = failed;
  });
  const auto = failed.some((r) => passing(r.error));
  useEffect(() => {
    if (!auto) return;
    const again = () => {
      for (const r of reads.current) if (passing(r.error)) void r.refetch({ cancelRefetch: false });
    };
    const off = onLiveAgain(again);
    const timer = setInterval(() => {
      if (liveStatus() === 'live' && document.visibilityState !== 'hidden') again();
    }, BACKSTOP_MS);
    return () => {
      off();
      clearInterval(timer);
    };
  }, [auto]);
  const first = firstFault(failed);
  const reason = first ? faultReason(first.error) : null;
  // the fault logged last: a read trying again (pending, its error cleared) is not a new one
  const logged = useRef<string | null>(null);
  useEffect(() => {
    if (ok) logged.current = null;
    else if (reason && reason !== logged.current) {
      logged.current = reason;
      console.warn(`${what} didn't load: ${reason}`, firstFault(reads.current)?.error);
    }
  }, [what, reason, ok]);
  return reason && `${what} didn't load · ${reason}`;
}
