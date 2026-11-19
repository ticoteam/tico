import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient } from '@/data';
import { useStartGate } from './gate';

/* Tier 2 gap of the Start track: `?setup=` opens the first run for the owner only. Made-up data only. */

function Probe() {
  const gate = useStartGate();
  return <span>{gate.firstRun ? 'First run' : 'The app'}</span>;
}
function show(at: string) {
  const root = createRootRoute({ component: Probe });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [], byId: {} });
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Setup, first run @s1154', () => {
  it('opens over the app for the owner @s1154', async () => {
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    const router = show('/?setup=path');
    expect(await screen.findByText('First run')).toBeTruthy();
    expect(router.state.location.search).toEqual({ setup: 'path' });
  });

  it('sends anyone else to Tasks, with no first run @s1154', async () => {
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
    const router = show('/?setup=path');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(screen.getByText('The app')).toBeTruthy();
    expect(router.state.location.search).toEqual({});
  });
});
