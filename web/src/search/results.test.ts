import type { AnyRouter } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as z from 'zod/mini';
import type { AnyAction, Ctx } from '@/actions';
// The pieces, not the index, so this test does not load the whole registry.
import { defineAction } from '@/actions/types';
import { defineSearch } from './providers';
import { actionHits, gather, highlight, readRecent, remember, REMOTE_WAIT_MS, type Group } from './results';

const ctx: Ctx = { router: {} as AnyRouter, queryClient: {} as Ctx['queryClient'], via: 'search' };
const goTo = defineAction({
  id: 'shell.goTo',
  title: 'Go to a page',
  kind: 'view',
  input: z.strictObject({ to: z.string() }),
  run: () => {},
  offer: () => [
    { input: { to: '/company/finance' }, title: 'Company, Finance' },
    { input: { to: '/settings' }, title: 'Settings' },
  ],
}) as AnyAction;
const close = defineAction({ id: 'shell.closeChat', title: 'Close the second chat', kind: 'view', input: z.strictObject({}), can: () => false, run: () => {} }) as AnyAction;
const tasks = defineSearch({
  group: 'Tasks',
  order: 1,
  find: (q) => (q.includes('frost') ? [{ key: 'task:7', title: 'Frost dates', run: { id: 'task.open', input: { task: 7 } } }] : []),
});
const broken = defineSearch({ group: 'Files', order: 2, find: () => Promise.reject(new Error('down')) });

describe('search', () => {
  it('marks every matched word, whatever its case', () => {
    expect(highlight('First frost guide', 'FROST')).toEqual([
      { text: 'First ', hit: false },
      { text: 'frost', hit: true },
      { text: ' guide', hit: false },
    ]);
  });

  it('offers actions as rows, skipping what the person cannot do', () => {
    expect(actionHits('fin', [goTo, close], ctx).map((h) => h.title)).toEqual(['Company, Finance']);
    expect(actionHits('close', [goTo, close], ctx)).toEqual([]);
  });

  it('groups providers then actions, and drops a failing provider', async () => {
    const groups = await gather('frost', [tasks, broken], [goTo], ctx, new AbortController().signal);
    expect(groups.map((g) => g.name)).toEqual(['Tasks']);
    const both = await gather('settings', [tasks], [goTo], ctx, new AbortController().signal);
    expect(both.map((g) => [g.name, g.hits.map((h) => h.run)])).toEqual([['Actions', [{ id: 'shell.goTo', input: { to: '/settings' } }]]]);
  });

  it('opens on what was used last, newest first, once each', async () => {
    remember({ key: 'task:7', title: 'Frost dates', run: { id: 'task.open', input: { task: 7 } } });
    remember({ key: 'page:/settings', title: 'Settings', run: { id: 'shell.goTo', input: { to: '/settings' } } });
    remember({ key: 'task:7', title: 'Frost dates', run: { id: 'task.open', input: { task: 7 } } });
    expect(readRecent().map((h) => h.key)).toEqual(['task:7', 'page:/settings']);
    const groups = await gather('', [tasks], [goTo], ctx, new AbortController().signal);
    expect(groups.map((g) => g.name)).toEqual(['Recent']);
  });

  describe('servers wait for a pause in the typing', () => {
    afterEach(() => vi.useRealTimers());
    const server = () => {
      const find = vi.fn(() => [{ key: 'task:9', title: 'Frost cloth', run: { id: 'task.open', input: { task: 9 } } }]);
      return { find, provider: defineSearch({ group: 'Server', order: 0, remote: true, find }) };
    };

    it('shows ready groups and starts the debounced search while a cold list is still loading', async () => {
      vi.useFakeTimers();
      const { find, provider } = server();
      let finishList!: () => void;
      const list = defineSearch({
        group: 'List',
        order: 2,
        find: () => new Promise((resolve) => { finishList = () => resolve([]); }),
      });
      const early: Group[][] = [];
      const done = gather('frost', [provider, tasks, list], [], ctx, new AbortController().signal, (g) => early.push(g));
      await vi.advanceTimersByTimeAsync(REMOTE_WAIT_MS - 1);
      expect(early.map((g) => g.map((x) => x.name))).toEqual([['Tasks']]);
      expect(find).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      expect(find).toHaveBeenCalledTimes(1);
      expect(early.at(-1)?.map((g) => g.name)).toEqual(['Server', 'Tasks']);
      finishList();
      expect((await done).map((g) => g.name)).toEqual(['Server', 'Tasks']);
    });

    it('never asks the server for words typed over before the pause', async () => {
      vi.useFakeTimers();
      const { find, provider } = server();
      const typed = new AbortController();
      const done = gather('fro', [provider], [], ctx, typed.signal);
      await vi.advanceTimersByTimeAsync(REMOTE_WAIT_MS / 2);
      typed.abort();
      await vi.advanceTimersByTimeAsync(REMOTE_WAIT_MS);
      expect(await done).toEqual([]);
      expect(find).not.toHaveBeenCalled();
    });
  });
});
