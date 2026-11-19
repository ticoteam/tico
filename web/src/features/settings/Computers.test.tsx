import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Computers } from './Agents';
import { computersQuery } from './queries';

vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => vi.unstubAllGlobals());

describe('s703 Settings, Computers: external agents', () => {
  it('is one dimmed Coming soon line under the computers, with nothing to press', async () => {
    queryClient.setQueryData(computersQuery.queryKey, [{ id: 'r1', label: 'Studio', online: true }]);
    const root = createRootRoute({ component: () => <Computers /> });
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/settings/computers'] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const section = await screen.findByRole('region', { name: 'External agents' });
    const line = within(section).getByText('Agents made and run outside Tico').closest('.row');
    expect(line?.getAttribute('aria-disabled')).toBe('true');
    expect(within(section).getByText('Coming soon')).toBeTruthy();
    expect(within(section).queryByRole('button', { name: /pair|add|connect/i })).toBeNull();
    expect(within(section).queryByRole('link')).toBeNull();
  });
});
