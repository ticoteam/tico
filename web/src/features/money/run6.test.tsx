import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { GrowthPage } from './Growth';
import { axis, count } from './model';
import type { GrowthFig, GrowthRead } from './queries';
import { validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Run 6 on Growth: the phone's Sources and Channels tables, and a count chart's axis. Made-up figures and names only. */

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function serve(reads: Record<string, unknown>) {
  vi.stubGlobal('fetch', async (url: string) => {
    const path = new URL(String(url), 'http://x').pathname.replace('/api/v2', '');
    return path in reads ? json(reads[path]) : json({}, 404);
  });
}

function show(at: string) {
  const Growth = () => <GrowthPage search={validateGrowthSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Growth }), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const f = (value: number | null, more: Partial<GrowthFig> = {}): GrowthFig => ({ value, ...more });
const READ: GrowthRead = {
  scope: 'all',
  period: '2026-10',
  as_of: '2026-11-02',
  headline: {
    signups: f(40),
    activated_rate: f(null, { status: 'nobody' }),
    paid_rate: f(null, { status: 'nobody' }),
    new_customers: f(12),
    cost: f(600),
    cac: f(50),
    kept: f(null, { status: 'nobody' }),
  },
  channels: [{ slug: 'search', name: 'Search ads', signups: 40, new_customers: 12, new_share: 1, cost: 600, cac: 50, cost_share: 1, budget: 700 }],
  health: { basis: 'customers', unknown_share: null, stale_sources: [], reconciled: 'not_reconciled' },
  metrics: [],
  more: [],
};
const SOURCES = {
  sources: [
    { id: 'src1', name: 'Ledger', health: 'fresh', last_run_at: '2026-11-01T10:00:00Z', fills: ['spend'], truth_for: ['spend'] },
    { id: 'src2', name: 'Signup sheet', health: 'stale', fills: ['signups'], truth_for: ['signups'] },
  ],
};

const heads = (table: HTMLElement) => within(table).getAllByRole('columnheader').map((h) => h.textContent);

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  delete document.documentElement.dataset.layout;
  vi.unstubAllGlobals();
});

describe('Growth on a phone (run 6)', () => {
  beforeEach(() => {
    document.documentElement.dataset.layout = 'cards';
  });

  it('Sources keeps the source and its last sync; the name is a link to its panel', async () => {
    serve({ '/growth/sources': SOURCES, '/growth/segments': { segments: [] } });
    show('/company/growth?tab=sources');
    const link = await screen.findByRole('link', { name: 'Ledger' });
    const table = screen.getByRole('table', { name: 'Sources' });
    expect(heads(table)).toEqual(['Source', 'Last sync']);
    // one column of fixed width beside the name, so it no longer starves (two of 184 did)
    expect([...table.querySelectorAll('col')].map((c) => c.className)).toEqual(['', 't-col']);
    expect(link.getAttribute('href')).toContain('panel=source');
    expect(within(table).getByRole('link', { name: 'Signup sheet' })).toBeTruthy();
  });

  it('Channels shows Channel, New and CAC', async () => {
    serve({ '/growth/read': READ, '/growth/segments': { segments: [] }, '/page-owners': { pages: {} } });
    show('/company/growth?month=2026-10');
    const table = await screen.findByRole('table', { name: 'Channels' });
    expect(heads(table)).toEqual(['Channel', 'New', 'CAC']);
  });
});

describe('Growth on a desk (run 6)', () => {
  it('Sources keeps Feeds and Holds', async () => {
    serve({ '/growth/sources': SOURCES, '/growth/segments': { segments: [] } });
    show('/company/growth?tab=sources');
    await screen.findByRole('link', { name: 'Ledger' });
    expect(heads(screen.getByRole('table', { name: 'Sources' }))).toEqual(['Source', 'Feeds', 'Holds', 'Last sync']);
  });
});

describe('a count chart’s axis (run 6)', () => {
  const labels = (values: number[]) => axis(values, true).ticks.map((t) => count(t));

  it('never prints the same label twice when every month is 0', () => {
    expect(labels([0, 0, 0])).toEqual(['0', '1']);
  });

  it('steps by whole numbers for small counts', () => {
    for (const values of [[0, 1], [0, 2], [1, 3], [0, 7], [2, 9, 4]]) {
      const ticks = axis(values, true).ticks;
      expect(ticks.every((t) => Number.isInteger(t))).toBe(true);
      expect(new Set(labels(values)).size).toBe(ticks.length);
    }
  });

  it('keeps fine steps for shares, which are not counts', () => {
    expect(axis([0, 0, 0]).ticks).toEqual([0, 0.5, 1]);
  });
});
