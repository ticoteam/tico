// Your own name and look for your assistant (preference `ui.assistant`, backend/preferences_routes.py check_look): a
// name, one of eight Dot colors and the one sprite. Only you see them, on your own screens. The look is drawn by CSS
// (styles/dot.css): `data-dot` and `data-sprite` on <html>, or on any wrapper, as a picker's swatches do.
import { queryOptions, useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api } from '@/api/client';
import { write } from '@/data';
import { readPref, writePref } from '@/prefs';

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
// This device's copy, so the look is right on the first paint of the next load, before the server answers.
const LOCAL = 'assistant-look.v1';

export const lookQuery = queryOptions({
  queryKey: ['tico', 'look'] as const,
  queryFn: async ({ signal }): Promise<Look> =>
    (await api.get<{ value: Look | null }>(`/api/v2/preferences/${KEY}`, { signal })).value ?? {},
  placeholderData: () => readPref<Look>(LOCAL, {}),
  staleTime: Infinity,
});

/** Keeps your look: what is given replaces what was there, and a null clears it. */
export function setLook(change: Partial<Look>): Promise<Look> {
  const next = (current: Look | undefined): Look => {
    const merged: Look = { ...(current ?? {}), ...change };
    // Only what is set is kept: the server takes no empty name.
    return Object.fromEntries(Object.entries(merged).filter(([, v]) => v != null && v !== '')) as Look;
  };
  let sent: Look = {};
  return write({
    key: lookQuery.queryKey,
    optimistic: (current: Look | undefined) => (sent = next(current)),
    send: async (idempotencyKey) => {
      const answer = await api.post<{ value: Look | null }>(`/api/v2/preferences/${KEY}`, { value: sent }, { idempotencyKey });
      return answer.value ?? {};
    },
  });
}

/** Your look, read once; the default (Tico, jade, no sprite) until it is read. */
export function useLook(): Look {
  return useQuery(lookQuery).data ?? {};
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
    this device's copy until then. */
export function useLookOnPage(signedIn: boolean): void {
  const look = useQuery({ ...lookQuery, enabled: signedIn });
  const { name, color, sprite } = look.data ?? {};
  const read = look.isSuccess && !look.isPlaceholderData;
  useEffect(() => {
    const html = document.documentElement;
    if (color) html.setAttribute('data-dot', color);
    else html.removeAttribute('data-dot');
    html.setAttribute('data-sprite', sprite ?? 'none');
    // only what the server said is this device's copy
    if (read) writePref(LOCAL, { ...(name ? { name } : {}), ...(color ? { color } : {}), ...(sprite ? { sprite } : {}) });
  }, [read, name, color, sprite]);
}
