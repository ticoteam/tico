import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { configQuery, directoryQuery, meQuery } from '@/data';
import { needsSetup, retryStart, screenFor, type StartScreenKind } from './queries';

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
}

/** For the root: whether the app can open. */
export function useStartGate(): StartGate {
  const me = useQuery(meQuery);
  const dir = useQuery(directoryQuery);
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
  };
}

/** Whether the first run is owed: the owner of a company not yet live (the server's `onboarding_needed`, which is
    false for anyone else). Tico's sheet holds it in place of its chat (SetupChat) until the owner finishes. */
export function useSetupOwed(): boolean {
  const owner = useQuery({ ...meQuery, select: (m) => m.role === 'owner' }).data === true;
  const { data: config } = useQuery({ ...configQuery, enabled: owner });
  return owner && needsSetup(config);
}
