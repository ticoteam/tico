import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Task } from '@/api/schema';
import { queryClient } from '@/data';
import { TaskStatus } from './StatusLine';

/* A task's own status line (its head, over its chat) marks Needs you as Needs you names the task, from the read the
   shell already holds: no read of its own, and no Dot until there is one. */

const ME = 'human:maya';
const turn = (who: string, state: 'person' | 'bot') => ({ state, who, line: 'Ship it?', requirement_id: null, since: null, until: null, ask: false }) as Task['turn'];
// the agent works on while an OK further down its waits is hers; and her own turn on a task she holds, nothing asked
const AGENTS = { id: 'a3', status_line: 'working', turn: turn('bot:qa', 'bot') } as const;
const HERS = { id: 'a2', status_line: 'needs_you', turn: turn(ME, 'person') } as const;

let reads: string[] = [];
beforeEach(() => {
  reads = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  vi.stubGlobal('fetch', async (url: string) => {
    reads.push(String(url));
    return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

const line = (t: typeof AGENTS | typeof HERS) => {
  const { container, unmount } = render(
    <QueryClientProvider client={queryClient}>
      <TaskStatus t={t} />
    </QueryClientProvider>,
  );
  const el = container.querySelector('.status-line');
  const shown = el ? { state: el.getAttribute('data-state'), dot: !!el.querySelector('.dot.flow') } : null;
  unmount();
  return shown;
};

it('says Needs you, with the Dot, where Needs you names the task, not by its turn', () => {
  queryClient.setQueryData(['flow'], { count: 1, items: [], task_ids: ['a3'] });
  expect(line(AGENTS)).toEqual({ state: 'needs_you', dot: true });
  expect(line(HERS)).toBeNull();
  expect(reads).toEqual([]);
});

it('marks no Dot, and reads nothing, until the app has read Needs you', () => {
  expect(line(AGENTS)).toEqual({ state: 'working', dot: false });
  expect(line(HERS)).toBeNull();
  expect(reads).toEqual([]);
});
