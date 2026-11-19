import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { directoryQuery, meQuery } from '@/data';
import { retryStart, screenFor, type StartScreenKind } from './queries';
import { setupStep } from './steps';

/** The longest the app waits for the person and the directory before it says it could not load (old app: boot.ts). */
export const BOOT_WAIT_MS = 10_000;

export interface StartGate {
  /** A card in place of the app (`StartScreen`), or null: open the app. */
  screen: StartScreenKind | null;
  /** What GET /api/v2/me refused with, for the card's words and the sign-in path. */
  error: unknown;
  /** Read again; the card stays while it reads. */
  retry: () => void;
  busy: boolean;
  /** The first run is open over the app (`FirstRun`). */
  firstRun: boolean;
}

/** For the root: whether the app can open, and whether the first run is open over it. */
export function useStartGate(): StartGate {
  const me = useQuery(meQuery);
  const dir = useQuery(directoryQuery);
  const step = useLocation({ select: (l) => setupStep((l.search as { setup?: unknown }).setup) });
  const [late, setLate] = useState(false);
  const [round, setRound] = useState(0);
  const settled = me.isError || (me.isSuccess && !dir.isPending);

  useEffect(() => {
    if (settled) return;
    const t = setTimeout(() => setLate(true), BOOT_WAIT_MS);
    return () => clearTimeout(t);
  }, [settled, round]);

  const retry = () => {
    setLate(false);
    setRound((n) => n + 1);
    retryStart();
  };
  return {
    screen: screenFor(me.error) ?? (late && !settled ? 'timed-out' : null),
    error: me.error,
    retry,
    busy: me.isFetching,
    firstRun: step !== null && me.isSuccess,
  };
}
