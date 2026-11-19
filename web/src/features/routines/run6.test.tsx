import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { validateRoutinesSearch } from './load';
import { RoutinesPage } from './RoutinesPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: 'ada@company.example', role: 'person', slug: 'ada' });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  // No runs in any range; every other read is empty.
  vi.stubGlobal('fetch', async (url: string) =>
    String(url).includes('/automations/runs?')
      ? new Response(JSON.stringify({ next: null, since: '2026-11-19T07:00:00Z', runs: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify({ error: { code: 'not_found', detail: 'Not found' } }), { status: 404, headers: { 'Content-Type': 'application/json' } }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Live() {
  return <RoutinesPage search={useSearch({ strict: false })} />;
}

function show(at: string) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root,
    path: '/company/routines',
    validateSearch: validateRoutinesSearch,
    component: () => (
      <>
        <Live />
        <ActionsRoot dot={() => null} fold={() => {}} />
      </>
    ),
  });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('Runs, empty', () => {
  it('names the default range it looked at', async () => {
    show('/company/routines?tab=runs');
    expect(await screen.findByText('No runs in the last hour')).toBeTruthy();
  });

  it('names the range and the status from the address', async () => {
    show('/company/routines?tab=runs&status=waiting&range=7d');
    expect(await screen.findByText('No waiting runs in the last 7 days')).toBeTruthy();
  });
});
