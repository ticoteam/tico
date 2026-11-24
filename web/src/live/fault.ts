import { useEffect, useRef } from 'react';
import { ApiError } from '@/api/client';
import { onLiveStatus } from './stream';

// A read that failed with nothing to show: one plain line saying why, read again by itself once the stream is back
// (a release's restart drops both, and the stream says when the server answers again), and the cause logged once.

/** Nothing pushes when a read failed while the stream stayed up (a 500, or a read that gave up just before the stream
    came back): read again this often meanwhile. */
const BACKSTOP_MS = 5 * 60_000;

/** Why a read failed, in two or three words. */
export function faultReason(err: unknown): string {
  if (navigator.onLine === false) return 'offline';
  if (!(err instanceof ApiError)) return 'app error';
  if (err.code === 'stopped') return 'company stopped';
  if (err.status === 0 || err.status === 502 || err.status === 503 || err.status === 504) return 'server restarting';
  if (err.status === 401) return 'signed out';
  if (err.status === 403) return 'no access';
  if (err.status === 404) return 'not found';
  return err.status >= 500 ? 'server error' : 'request refused';
}

/**
 * The line for a read of `what` that failed (`error`), or null while it has not. Meanwhile it is read again (`retry`)
 * when the stream is live again, and every BACKSTOP_MS; each failure is logged once.
 */
export function useReadFault(what: string, error: unknown, retry: () => unknown): string | null {
  const again = useRef(retry);
  useEffect(() => {
    again.current = retry;
  });
  const failed = error != null;
  useEffect(() => {
    if (!failed) return;
    const off = onLiveStatus((s) => void (s === 'live' && again.current()));
    const timer = setInterval(() => void again.current(), BACKSTOP_MS);
    return () => {
      off();
      clearInterval(timer);
    };
  }, [failed]);
  const reason = failed ? faultReason(error) : null;
  useEffect(() => {
    if (reason) console.warn(`${what} didn't load: ${reason}`, error);
  }, [what, reason, error]);
  return reason && `${what} didn't load · ${reason}`;
}
