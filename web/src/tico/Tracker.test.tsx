import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { useFollow } from '@/live/follow';
import { taskMatches } from '@/live/patch';
import { Tracker } from './Tracker';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

const ME = 'human:ana';
const hour = (n: number) => new Date(Date.now() - n * 3_600_000).toISOString();
const task = (id: string, title: string, owner: string | null, status = 'open', finished?: string) => ({
  id,
  number: null,
  title,
  status,
  pipeline_id: null,
  owner_id: owner,
  updated_at: hour(1),
  ...(finished && { done_at: finished }),
});

// What the server answers each read, page by page (`after` names the page); the test checks what the tracker asked.
// The person's own task and an unassigned one fill the first open page: the tracked ones come on the next.
const OPEN: Record<string, { tasks: ReturnType<typeof task>[]; next_after: string | null }> = {
  '': { tasks: [task('t3', 'My own errand', ME), task('t6', 'Nobody has it', null)], next_after: 'o2' },
  o2: { tasks: [task('t1', 'Research e-signature vendors', 'bot:research'), task('t2', 'Draft the strategy', 'bot:strategy')], next_after: null },
};
// Newest finished first: the second page reaches past 7 days, so the third is never read.
const BACK: Record<string, { tasks: ReturnType<typeof task>[]; next_after: string | null }> = {
  '': { tasks: [task('t4', 'Add Builder 3', 'bot:ops', 'done', hour(2))], next_after: 'b2' },
  b2: { tasks: [task('t5', 'Old result', 'bot:ops', 'done', hour(24 * 9))], next_after: 'b3' },
  b3: { tasks: [], next_after: null },
};
const FIRST_BACK = BACK['']!;
let gets: string[] = [];
let posts: { url: string; body: unknown }[] = [];
let seen: string | null = null;
const ASK = { id: 'v1:r1', kind: 'ask', task_id: 't1', task: { id: 't1', number: 1, title: 'Research e-signature vendors' } };
let needs: unknown[] = [ASK];

beforeEach(() => {
  gets = [];
  posts = [];
  seen = null;
  needs = [ASK];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === 'POST') {
      posts.push({ url: u, body: JSON.parse(String(init.body)) });
      seen = (JSON.parse(String(init.body)) as { value: string }).value;
    } else gets.push(u);
    const q = new URL(u, 'http://x').searchParams;
    const body = u.startsWith('/api/v2/tasks')
      ? (q.get('status') === 'open' ? OPEN : BACK)[q.get('after') ?? '']
      : u.startsWith('/api/v2/needs-you')
        ? { items: needs, next: null }
        : { key: 'ui.tracker_seen_at', value: seen };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("Tico's tracker", () => {
  it('lists only the open tasks the person asked for that someone else holds, read by actor id, exact, on one party filter', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Tracker />
      </QueryClientProvider>,
    );
    const row = await screen.findByRole('button', { name: /Tracking 2/ });
    await waitFor(() => expect(row.textContent).toContain('1 needs you'));
    expect(row.textContent).toContain('1 back');

    const reads = gets.filter((u) => u.startsWith('/api/v2/tasks')).map((u) => Object.fromEntries(new URL(u, 'http://x').searchParams));
    for (const r of reads) expect(r).toMatchObject({ requester: ME, exact: 'true' }); // never `me`, never without exact
    // The open list follows every page; the finished one stops once a page reaches past Back's 7 days.
    expect(reads.filter((r) => r.status === 'open').map((r) => r.after ?? '')).toEqual(['', 'o2']);
    expect(reads.filter((r) => r.status !== 'open').map((r) => r.after ?? '')).toEqual(['', 'b2']);
    expect(vi.mocked(useFollow)).toHaveBeenLastCalledWith({ party: ME });
    expect(posts).toEqual([]); // nothing is marked seen before the tracker is opened

    fireEvent.click(row);
    const groups = (name: string) => within(screen.getByRole('button', { name: new RegExp(`^${name}`) }).closest('section')!);
    expect(groups('Needs you').getByRole('link', { name: /Research e-signature vendors/ })).toBeTruthy();
    expect(groups('Open').getByRole('link', { name: /Draft the strategy/ })).toBeTruthy();
    expect(groups('Back').getByRole('link', { name: /Add Builder 3/ })).toBeTruthy();
    expect(screen.queryByText('My own errand')).toBeNull();
    expect(screen.queryByText('Nobody has it')).toBeNull(); // unassigned: no one else holds it yet
    expect(screen.queryByText('Old result')).toBeNull(); // past the 7 days Back looks at
    // Opening starts Back again from now: the count clears, the group stays while open.
    await waitFor(() => expect(posts.map((p) => p.url)).toEqual(['/api/v2/preferences/ui.tracker_seen_at']));
    await waitFor(() => expect(row.textContent).not.toContain('back'));
    expect(groups('Back').getByRole('link', { name: /Add Builder 3/ })).toBeTruthy();
    // Closed and opened again, nothing has come back since.
    fireEvent.click(row);
    expect(row.textContent).not.toContain('back');
    fireEvent.click(row);
    expect(screen.queryByRole('button', { name: /^Back/ })).toBeNull();
  });

  it('counts every task a grouped Needs you item names as needing the person', async () => {
    // A paused agent is one item naming several tasks (task_ids), its brief only the first.
    needs = [{ id: 'paused:abc', kind: 'paused', task_ids: ['t1', 't2'], task: { id: 't1', number: 1, title: 'Research e-signature vendors' } }];
    render(
      <QueryClientProvider client={queryClient}>
        <Tracker />
      </QueryClientProvider>,
    );
    const row = await screen.findByRole('button', { name: /Tracking 2/ });
    await waitFor(() => expect(row.textContent).toContain('2 needs you'));
  });

  it('drops a result from the Back count when it ages past 7 days, on one timer', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      BACK[''] = { tasks: [task('t4', 'Add Builder 3', 'bot:ops', 'done', hour(24 * 7 - 0.5))], next_after: null };
      render(
        <QueryClientProvider client={queryClient}>
          <Tracker />
        </QueryClientProvider>,
      );
      const row = await screen.findByRole('button', { name: /Tracking 2/ });
      await waitFor(() => expect(row.textContent).toContain('1 back'));
      await act(() => vi.advanceTimersByTimeAsync(31 * 60_000));
      expect(row.textContent).not.toContain('back');
    } finally {
      BACK[''] = FIRST_BACK;
      vi.useRealTimers();
    }
  });

  it('a tracked list takes the stream as its rows: exact narrows no row, so a change patches in place', () => {
    const params = { requester: ME, status: 'open', snoozed: 'all', exact: 'true', brief: 'true', limit: 500 };
    expect(taskMatches({ id: 't9', status: 'open', requester_id: ME }, params)).toBe(true);
    expect(taskMatches({ id: 't9', status: 'open', requester_id: 'human:ben' }, params)).toBe(false);
  });
});
