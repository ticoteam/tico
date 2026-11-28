// Your own name and look for your assistant (preference `ui.assistant`, backend/preferences_routes.py check_look): a
// name, one of eight Dot colors and the one sprite. Only you see them, on your own screens. The look is drawn by CSS
// (styles/dot.css): `data-dot` and `data-sprite` on <html>, or on any wrapper, as a picker's swatches do.
import { queryOptions, useQuery } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';
import { api } from '@/api/client';
import { meQuery, queryClient, write } from '@/data';
import { readPref, removePref, writePref } from '@/prefs';

export const DOT_COLORS = ['jade', 'teal', 'sky', 'indigo', 'violet', 'rose', 'coral', 'gold'] as const;
export type DotColor = (typeof DOT_COLORS)[number];
export const DOT_SPRITES = ['pixel'] as const;
export type DotSprite = (typeof DOT_SPRITES)[number];

export const COLOR_NAME: Record<DotColor, string> = {
  jade: 'Jade',
  teal: 'Teal',
  sky: 'Sky',
  indigo: 'Indigo',
  violet: 'Violet',
  rose: 'Rose',
  coral: 'Coral',
  gold: 'Gold',
};

export type Look = { name?: string | null; color?: DotColor | null; sprite?: DotSprite | null };

/** The name everyone starts with. */
export const DEFAULT_NAME = 'Tico';
export const NAME_MAX = 40;

const KEY = 'ui.assistant';
// This device's copy, so the look is right on the first paint of the next load, before the server answers. Kept per
// account: on a browser people share, one person's name and colors never show for the next one who signs in.
const localKey = (who: string) => `assistant-look.v2:${who}`;
// the first copy was one for the whole browser, whoever was signed in: dropped, never read
const SHARED_COPY = 'assistant-look.v1';

/** The signed-in account, once the server has said who it is; none before. */
const whoNow = (): string | undefined => queryClient.getQueryData(meQuery.queryKey)?.id;

// Before the server answers for an account, its own copy on this device: read once per account, so a draw is cheap
// and the same copy each time.
const seeds = new Map<string, Look>();
const seedOf = (who: string): Look => {
  let seed = seeds.get(who);
  if (!seed) seeds.set(who, (seed = readPref<Look>(localKey(who), {})));
  return seed;
};
/** Your look, as the server keeps it for `who`: each account its own query, so a switch never shows the last one's. */
export const lookQuery = (who: string | undefined) =>
  queryOptions<Look, Error, Look, readonly ['tico', 'look', string | null]>({
    queryKey: ['tico', 'look', who ?? null] as const,
    queryFn: async ({ signal }): Promise<Look> =>
      (await api.get<{ value: Look | null }>(`/api/v2/preferences/${KEY}`, { signal })).value ?? {},
    // this device's copy for this account only, and none until the server has said who is signed in
    placeholderData: (): Look | undefined => (who ? seedOf(who) : undefined),
    enabled: !!who,
    staleTime: Infinity,
  });

/** Keeps your look: what is given replaces what was there, and a null clears it. */
export function setLook(change: Partial<Look>): Promise<Look> {
  const who = whoNow();
  if (!who) return Promise.reject(new Error('Sign in to change your assistant'));
  const next = (current: Look | undefined): Look => {
    const merged: Look = { ...(current ?? {}), ...change };
    // Only what is set is kept: the server takes no empty name.
    return Object.fromEntries(Object.entries(merged).filter(([, v]) => v != null && v !== '')) as Look;
  };
  let sent: Look = {};
  return write({
    key: lookQuery(who).queryKey,
    optimistic: (current: Look | undefined) => (sent = next(current)),
    send: async (idempotencyKey) => {
      const answer = await api.post<{ value: Look | null }>(`/api/v2/preferences/${KEY}`, { value: sent }, { idempotencyKey });
      return answer.value ?? {};
    },
  });
}

const NONE: Look = {};
/** Your look now: what the server said for the account signed in, else its copy on this device; nobody's before the
    server says who is signed in. */
const lookNow = (): Look => {
  const who = whoNow();
  return who ? (queryClient.getQueryData(lookQuery(who).queryKey) ?? seedOf(who)) : NONE;
};
const onCache = (change: () => void) => queryClient.getQueryCache().subscribe(change);

/** Your look; the default (Tico, jade, no sprite) until it is known. It only reads: the app's root reads it from the
    server (useLookOnPage), so the shell's pieces that show the name add no read and draw again only when it changes. */
export function useLook(): Look {
  return useSyncExternalStore(onCache, lookNow, lookNow);
}

/** What you call your assistant: your own name for it, or Tico. */
export function useTicoName(): string {
  return useLook().name || DEFAULT_NAME;
}

/** Puts a look on an element: the attributes styles/dot.css reads. */
export function lookAttrs(look: Look): { 'data-dot'?: DotColor; 'data-sprite': DotSprite | 'none' } {
  return { ...(look.color ? { 'data-dot': look.color } : {}), 'data-sprite': look.sprite ?? 'none' };
}

/** Mounted once at the app's root: your look on <html>, so every Dot on your screens wears it. Read once signed in;
    until the server answers, this device's copy for the account it said is signed in; before that, the default. */
export function useLookOnPage(signedIn: boolean): void {
  const who = useQuery(meQuery).data?.id;
  const mine = signedIn ? who : undefined;
  const look = useQuery(lookQuery(mine));
  const { name, color, sprite } = look.data ?? {};
  const read = look.isSuccess && !look.isPlaceholderData;
  useEffect(() => {
    const html = document.documentElement;
    if (color) html.setAttribute('data-dot', color);
    else html.removeAttribute('data-dot');
    html.setAttribute('data-sprite', sprite ?? 'none');
    // only what the server said is this device's copy
    if (read && mine) writePref(localKey(mine), { ...(name ? { name } : {}), ...(color ? { color } : {}), ...(sprite ? { sprite } : {}) });
  }, [read, mine, name, color, sprite]);
  useEffect(() => removePref(SHARED_COPY), []);
}

/** useLookOnPage as a piece of the tree that draws nothing. */
export function LookOnPage({ signedIn }: { signedIn: boolean }): null {
  useLookOnPage(signedIn);
  return null;
}
