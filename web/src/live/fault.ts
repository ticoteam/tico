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
const passing = (err: unknown) => err instanceof ApiError && (err.status === 0 || err.status >= 500) && err.code !== 'stopped';

/**
 * The line for a read of `what` that failed (`error`; null when it has not, or its fault is not on screen). A passing
 * failure is read again (`retry`) each time the stream is live again, and every BACKSTOP_MS meanwhile. Each fault is
 * logged once, however many reads fail again before one succeeds.
 */
export function useReadFault(what: string, error: unknown, retry: () => unknown): string | null {
  const again = useRef(retry);
  const cause = useRef(error);
  useEffect(() => {
    again.current = retry;
    cause.current = error;
  });
  const auto = error != null && passing(error);
  useEffect(() => {
    if (!auto) return;
    const off = onLiveAgain(() => void again.current());
    const timer = setInterval(() => {
      if (liveStatus() === 'live' && document.visibilityState !== 'hidden') void again.current();
    }, BACKSTOP_MS);
    return () => {
      off();
      clearInterval(timer);
    };
  }, [auto]);
  const reason = error != null ? faultReason(error) : null;
  useEffect(() => {
    if (reason) console.warn(`${what} didn't load: ${reason}`, cause.current);
  }, [what, reason]);
  return reason && `${what} didn't load · ${reason}`;
}
