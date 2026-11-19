import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { marketBriefingQuery, marketCountQuery, marketSpendQuery, valueChainQuery, type MarketSpend } from './queries';
import { StrategyPage } from './StrategyPage';

/* Browser run 6 on Market: a parameter's key is shown in plain words. Sample data only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: 'not_found' }), { status: 404, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => vi.unstubAllGlobals());

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const spend = (params: MarketSpend['segments']): MarketSpend => ({
  name: 'Dog grooming in Ohio',
  year: 2026,
  definition: { description: 'What Ohio dog owners spend on grooming', params: { vans_per_city: 12, avg_visit_price: { value: 80, judgement: true } } },
  segments: params,
});

function market(m: MarketSpend, role: 'person' | 'owner') {
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role, slug: null });
  queryClient.setQueryData(marketSpendQuery.queryKey, m);
  queryClient.setQueryData(marketBriefingQuery.queryKey, { briefing: null });
  queryClient.setQueryData(valueChainQuery.queryKey, { layers: [] });
  queryClient.setQueryData(marketCountQuery.queryKey, 3);
}

describe('Market parameters in plain words', () => {
  it('names the Definition’s shared parameters in words, never their keys', async () => {
    market(spend([{ id: 's1', name: 'Mobile grooming' }]), 'person');
    show(<StrategyPage tab="market" search={{ panel: 'definition' }} />, '/company/strategy/market?panel=definition');
    const panel = await screen.findByRole('complementary', { name: 'Definition' });
    const shared = within(panel).getByRole('region', { name: 'Shared parameters' });
    expect(within(shared).getByText('Vans per city')).toBeTruthy();
    expect(within(shared).getByText('Avg visit price')).toBeTruthy();
    expect(shared.textContent).not.toContain('_');
  });

  it('labels a segment estimate’s parameter field in words for the owner', async () => {
    const segs: MarketSpend['segments'] = [
      {
        id: 's1',
        name: 'Mobile grooming',
        size: { id: 'z1', spend_usd: 12_000_000 },
        estimates: [{ id: 'z1', method: 'bottom_up', status: 'proposed', spend_usd: 12_000_000, params: { vans_per_city: 4000 } }],
      },
    ];
    market(spend(segs), 'owner');
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, '/company/strategy/market?panel=segment:s1');
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    expect(within(panel).getByRole('spinbutton', { name: 'Vans per city' })).toBeTruthy();
    expect(within(panel).queryByText('vans_per_city')).toBeNull();
  });
});
