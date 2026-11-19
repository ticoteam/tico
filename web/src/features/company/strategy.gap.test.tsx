import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { listeningStatsQuery, marketSpendQuery, strategyQuery, strategyVersionsQuery, type ListeningStats, type MarketSpend, type Strategy } from './queries';
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
const news: ListeningStats = {
  recent: [
    { item_id: 'p1', source: 'reddit', text: 'Best balcony kit for a first garden?', url: 'https://example.test/p1' },
    { item_id: 'p2', source: 'forum', text: 'A post with no link' },
  ],
};

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

describe('Strategy: where we play, competitors and news @s304', () => {
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

  it('lists each competitor once from the players, linked to its Market note @s304', async () => {
    serve(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, spend);
    show();
    expect(await screen.findByRole('button', { name: 'Competitors' })).toBeTruthy();
    const sprout = screen.getAllByText('Sprout Co');
    expect(sprout).toHaveLength(1);
    expect(href(sprout[0]!)).toBe('/company/strategy/market?panel=player:e1');
    expect(href(screen.getByText('Leafbox'))).toBe('/company/strategy/market?panel=player:e2');
  });

  it('shows Listening’s linked posts as Related news, opening where they were written @s304', async () => {
    serve(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, spend);
    queryClient.setQueryData(listeningStatsQuery.queryKey, news);
    show();
    const post = await screen.findByText('Best balcony kit for a first garden?');
    expect(post.closest('a')?.getAttribute('href')).toBe('https://example.test/p1');
    expect(screen.getByRole('button', { name: 'Related news' })).toBeTruthy();
    expect(screen.queryByText('A post with no link')).toBeNull();
  });

  it('says None yet when the market has no segments, and leaves Related news out when Listening has nothing @s304', async () => {
    serve(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, { segments: [] } as unknown as MarketSpend);
    queryClient.setQueryData(listeningStatsQuery.queryKey, null);
    show();
    expect(await screen.findByRole('button', { name: 'Where we play' })).toBeTruthy();
    expect(screen.getAllByText('None yet')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Related news' })).toBeNull();
  });

  it('offers Try again when the market did not load, and reads it again @s304', async () => {
    serve(500);
    show();
    expect(await screen.findByRole('img', { name: 'Market did not load' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Competitors' })).toBeNull();
    serve(200);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Balcony growers')).toBeTruthy();
    expect(asked).toBe(1);
  });

  it('says No access, with no Try again, when the market is closed to this person @s304', async () => {
    serve(403);
    show();
    expect(await screen.findByText('No access')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});
