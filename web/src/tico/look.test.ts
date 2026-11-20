import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { whoOf } from '@/conversation/Message';
import { queryClient } from '@/data';
import { lookAttrs, lookQuery, setLook } from './look';

afterEach(() => {
  queryClient.clear();
  vi.restoreAllMocks();
});

describe('your assistant', () => {
  it('keeps what was set and changes only what is given; a cleared name goes back to Tico', async () => {
    const post = vi.spyOn(api, 'post').mockImplementation(async (_path, body) => body as never);
    queryClient.setQueryData(lookQuery.queryKey, { name: 'Penny', color: 'violet' });

    await setLook({ sprite: 'pixel' });
    expect(post).toHaveBeenLastCalledWith('/api/v2/preferences/ui.assistant', { value: { name: 'Penny', color: 'violet', sprite: 'pixel' } }, expect.anything());
    expect(queryClient.getQueryData(lookQuery.queryKey)).toEqual({ name: 'Penny', color: 'violet', sprite: 'pixel' });

    await setLook({ name: null, sprite: null });
    expect(post).toHaveBeenLastCalledWith('/api/v2/preferences/ui.assistant', { value: { color: 'violet' } }, expect.anything());
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
