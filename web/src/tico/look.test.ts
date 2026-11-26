import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { whoOf } from '@/conversation/Message';
import { queryClient } from '@/data';
import { meQuery } from '@/data/session';
import { writePref } from '@/prefs';
import { lookAttrs, lookQuery, setLook, useLook } from './look';

afterEach(() => {
  queryClient.clear();
  vi.restoreAllMocks();
});

describe('your assistant', () => {
  it('keeps what was set and changes only what is given; a cleared name goes back to Tico', async () => {
    const post = vi.spyOn(api, 'post').mockImplementation(async (_path, body) => body as never);
    queryClient.setQueryData(meQuery.queryKey, { id: 'human:me', email: 'me@example.com', role: 'other', slug: null });
    queryClient.setQueryData(lookQuery('human:me').queryKey, { name: 'Penny', color: 'violet' });

    await setLook({ sprite: 'pixel' });
    expect(post).toHaveBeenLastCalledWith('/api/v2/preferences/ui.assistant', { value: { name: 'Penny', color: 'violet', sprite: 'pixel' } }, expect.anything());
    expect(queryClient.getQueryData(lookQuery('human:me').queryKey)).toEqual({ name: 'Penny', color: 'violet', sprite: 'pixel' });

    await setLook({ name: null, sprite: null });
    expect(post).toHaveBeenLastCalledWith('/api/v2/preferences/ui.assistant', { value: { color: 'violet' } }, expect.anything());
  });

  it('keeps each account its own look on a shared browser, and none before the server says who is signed in', () => {
    // Ann used this browser and named her assistant; Ben signs in next on the same one
    writePref('assistant-look.v2:human:ann', { name: 'Penny', color: 'violet' });
    const seed = (who: string | undefined) => {
      const q = lookQuery(who);
      return typeof q.placeholderData === 'function' ? (q.placeholderData as () => unknown)() : q.placeholderData;
    };
    expect(seed('human:ann')).toEqual({ name: 'Penny', color: 'violet' });
    expect(seed('human:ben')).toEqual({});
    expect(seed(undefined)).toBeUndefined();
    // each account reads its own query, so what was read for one never shows for the other
    expect(lookQuery('human:ann').queryKey).not.toEqual(lookQuery('human:ben').queryKey);
    expect(lookQuery(undefined).enabled).toBe(false);
  });

  it('switches with the account: the next one to sign in never wears the last one\'s look', () => {
    const me = (id: string) => ({ id, email: `${id}@example.com`, role: 'other' as const, slug: null });
    const { result } = renderHook(() => useLook());
    expect(result.current).toEqual({});
    act(() => {
      queryClient.setQueryData(meQuery.queryKey, me('human:cy'));
      queryClient.setQueryData(lookQuery('human:cy').queryKey, { name: 'Penny', color: 'violet' });
    });
    expect(result.current).toEqual({ name: 'Penny', color: 'violet' });
    act(() => queryClient.setQueryData(meQuery.queryKey, me('human:dee')));
    expect(result.current).toEqual({});
  });

  it('changes nothing while nobody is signed in', async () => {
    const post = vi.spyOn(api, 'post');
    await expect(setLook({ color: 'gold' })).rejects.toThrow();
    expect(post).not.toHaveBeenCalled();
  });

  it('wears its look through the attributes the Dot css reads', () => {
    expect(lookAttrs({ color: 'coral', sprite: 'pixel' })).toEqual({ 'data-dot': 'coral', 'data-sprite': 'pixel' });
    // no color: the page's (or the jade) shows through; no sprite: a plain Dot even inside a page that wears one
    expect(lookAttrs({})).toEqual({ 'data-sprite': 'none' });
  });

  it('speaks in your chat by the name you gave it', () => {
    expect(whoOf({ sender_id: 'bot:assistant' }, 'tico', 'human:me', undefined, 'Penny').name).toBe('Penny');
    expect(whoOf({ sender_id: 'bot:assistant' }, 'tico', 'human:me').name).toBe('Tico');
    // another agent keeps its own name
    expect(whoOf({ sender_id: 'bot:growth', sender_name: 'Growth Manager' }, 'page', 'human:me', undefined, 'Penny').name).toBe('Growth Manager');
  });
});
