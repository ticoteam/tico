import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Call } from '@/api/schema';
import { queryClient } from '@/data';
import type { CompanyRef, TicoDesktop } from '@/desktop';
import { CallSide } from './CallSide';
import { callQuery, transcriptQuery } from './queries';

// The chat, faked: a few links of each kind, the in-app one moving through the router as real messages do.
vi.mock('@/conversation', async () => {
  const { Link } = await import('@tanstack/react-router');
  return {
    ticoRoomQuery: { queryKey: ['assistant', 'room'], queryFn: async () => 'th-tico' },
    readTicoInfo: async () => ({ bot: 'b1', room_id: 'th-tico' }),
    Conversation: ({ variant, title }: { variant: string; title?: string }) => (
      <div data-variant={variant} data-to={title}>
        <Link to="/tasks">A task</Link>
        <a href="https://example.com/a" target="_blank" rel="noreferrer">
          Outside
        </a>
        <a href="mailto:a@example.com">Mail</a>
      </div>
    ),
  };
});

const ROOM = 'r1';
const me: CompanyRef = { id: 'c1', origin: location.origin, name: 'Acme', email: '', active: true, self: true, signedOut: false, needs: 0 };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

let t: TicoDesktop;
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(callQuery(ROOM).queryKey, { id: ROOM, thread_id: 'th1', participants: [], bots: [], state: 'live' } as unknown as Call);
  t = { companies: { list: vi.fn(async () => [me]), switchTo: vi.fn(async () => {}) } } as unknown as TicoDesktop;
  (window as { tico?: TicoDesktop }).tico = t;
});
afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
  vi.restoreAllMocks();
});

function show(at: string) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const page = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <CallSide room={ROOM} /> });
  const tasks = createRoute({ getParentRoute: () => root, path: '/tasks', component: () => <h1>Tasks</h1> });
  const router = createRouter({ routeTree: root.addChildren([page, tasks]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('links in the small call window’s chat', () => {
  it('open the app’s pages in the main window and outside pages in the browser; the window stays on its call', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const router = show(`/call/${ROOM}?window=1`);
    fireEvent.click(await screen.findByRole('link', { name: 'A task' }));
    await vi.waitFor(() => expect(t.companies.switchTo).toHaveBeenCalledWith('c1', `${location.origin}/tasks`));
    expect(router.state.location.pathname).toBe(`/call/${ROOM}`);
    fireEvent.click(screen.getByRole('link', { name: 'Outside' }));
    expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
    const mail = new MouseEvent('click', { bubbles: true, cancelable: true });
    screen.getByRole('link', { name: 'Mail' }).dispatchEvent(mail);
    expect(mail.defaultPrevented).toBe(true);
    expect(open).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe(`/call/${ROOM}`);
  });

  it('move through the app as usual outside the small window', async () => {
    const router = show(`/call/${ROOM}`);
    fireEvent.click(await screen.findByRole('link', { name: 'A task' }));
    await screen.findByRole('heading', { name: 'Tasks' });
    expect(router.state.location.pathname).toBe('/tasks');
    expect(t.companies.switchTo).not.toHaveBeenCalled();
  });
});

describe('the call’s chat', () => {
  it('in Tico’s own room is Tico’s chat; with one agent it is to that agent, never “Teammate”', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, { id: ROOM, thread_id: 'th-tico', participants: [], bots: ['b1'], state: 'live' } as unknown as Call);
    show(`/call/${ROOM}`);
    await vi.waitFor(() => expect(document.querySelector('[data-variant]')?.getAttribute('data-variant')).toBe('tico'));
    cleanup();
    const gm = { id: 'bot:gm', name: 'Growth Manager', kind: 'agent' };
    queryClient.setQueryData(['directory'], { actors: [gm], byId: { 'bot:gm': gm } });
    queryClient.setQueryData(callQuery(ROOM).queryKey, { id: ROOM, thread_id: 'th1', participants: [], bots: ['gm'], state: 'live' } as unknown as Call);
    show(`/call/${ROOM}`);
    await vi.waitFor(() => expect(document.querySelector('[data-variant]')?.getAttribute('data-variant')).toBe('task'));
    expect(document.querySelector('[data-variant]')?.getAttribute('data-to')).toBe('Growth Manager');
    expect(document.body.textContent).not.toContain('Teammate');
  });
});

describe('Beside the call @s1046', () => {
  it('Chat is the call’s thread; Transcript what has been said; People who is in it and who left', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, {
      id: ROOM,
      thread_id: 'th1',
      meeting_id: 'mt9',
      participants: [
        { actor: 'human:ana', in_call: true },
        { actor: 'human:ben', in_call: false },
      ],
      bots: ['gm'],
      state: 'live',
    } as unknown as Call);
    queryClient.setQueryData(transcriptQuery('mt9').queryKey, { chunks: [{ seq: 1, speaker: 'Ana', start_ms: 5_000, text: 'Prices go up in May.' }], event_id: 4 });
    show(`/call/${ROOM}`);
    expect(await screen.findByRole('link', { name: 'A task' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Transcript' }));
    expect(await screen.findByText('Prices go up in May.')).toBeTruthy();
    expect(screen.getByText('0:05')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: /People/ }));
    expect(await screen.findAllByText('In the call')).toHaveLength(2);
    expect(screen.getByText('Left')).toBeTruthy();
  });
});
