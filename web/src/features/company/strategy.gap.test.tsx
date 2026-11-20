import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { marketSpendQuery, strategyQuery, strategyVersionsQuery, type MarketSpend, type Strategy } from './queries';
import { StrategyPage } from './StrategyPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const strategy: Strategy = {
  current: { n: 1, summary: 'Sell kits to new gardeners.', sections: {}, published_by: 'bot:strat', published_at: '2026-11-18T10:00:00Z' },
  bets: [],
};
const spend = {
  segments: [
    { id: 'sg1', name: 'Balcony growers', self: true, size: { spend_usd: 2_400_000 }, players: [{ entity_id: 'e1', name: 'Sprout Co' }, { entity_id: 'e2', name: 'Leafbox' }] },
    { id: 'sg2', name: 'Gift buyers', rolled_usd: { spend_usd: 310_000 }, players: [{ entity_id: 'e1', name: 'Sprout Co' }] },
    { id: 'sg3', name: 'Schools', players: [] },
  ],
} as unknown as MarketSpend;
let asked = 0;
function serve(status: number) {
  asked = 0;
  vi.stubGlobal('fetch', async (url: string) => {
    if (String(url).includes('/market/spend')) asked += 1;
    const ok = status === 200 && String(url).includes('/market/spend');
    return new Response(JSON.stringify(ok ? spend : { detail: status === 403 ? 'forbidden' : 'down' }), { status: ok ? 200 : status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show() {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: '/company/strategy', component: () => <StrategyPage tab="strategy" /> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ['/company/strategy'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(strategyQuery.queryKey, strategy);
  queryClient.setQueryData(strategyVersionsQuery.queryKey, [{ n: 1 }]);
});
afterEach(() => vi.unstubAllGlobals());

describe('Strategy: where we play @s304', () => {
  it('lists the market’s segments with their sizes, each opening its Market panel @s304', async () => {
    serve(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, spend);
    show();
    const balcony = await screen.findByText('Balcony growers');
    expect(href(balcony)).toBe('/company/strategy/market?panel=segment:sg1');
    expect(within(balcony.closest('a')!).getByText('$2.4M')).toBeTruthy();
    expect(within(screen.getByText('Gift buyers').closest('a')!).getByText('$310k')).toBeTruthy();
    expect(within(screen.getByText('Schools').closest('a')!).getByText('Not sized')).toBeTruthy();
    // The mock's share column has no server number behind it: no percent is shown.
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  it('leaves Where we play out when the market has no segments @s304', async () => {
    serve(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, { segments: [] } as unknown as MarketSpend);
    show();
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Where we play' })).toBeNull();
    expect(screen.queryByText('None yet')).toBeNull();
  });

  it('offers Try again when the market did not load, and reads it again @s304', async () => {
    serve(500);
    show();
    expect(await screen.findByText('Market did not load')).toBeTruthy();
    serve(200);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Balcony growers')).toBeTruthy();
    expect(asked).toBe(1);
  });

  it('leaves Where we play out, with no Try again, when the market is closed to this person @s304', async () => {
    serve(403);
    show();
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Where we play' })).toBeNull());
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});
