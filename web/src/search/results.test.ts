import type { AnyRouter } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import type { AnyAction, Ctx } from '@/actions';
// The pieces, not the index, so this test does not load the whole registry.
import { s } from '@/actions/schema';
import { defineAction } from '@/actions/types';
import { defineSearch } from './providers';
import { actionHits, gather, highlight, readRecent, remember } from './results';

const ctx: Ctx = { router: {} as AnyRouter, queryClient: {} as Ctx['queryClient'], via: 'search' };
const goTo = defineAction({
  id: 'shell.goTo',
  title: 'Go to a page',
  kind: 'view',
  input: s.object({ to: s.string() }),
  run: () => {},
  offer: () => [
    { input: { to: '/company/finance' }, title: 'Company, Finance' },
    { input: { to: '/settings' }, title: 'Settings' },
  ],
}) as AnyAction;
const close = defineAction({ id: 'shell.closeChat', title: 'Close the second chat', kind: 'view', input: s.object({}), can: () => false, run: () => {} }) as AnyAction;
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
});
