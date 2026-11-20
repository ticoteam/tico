import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import {
  listeningStatsQuery,
  marketBriefingQuery,
  marketCountQuery,
  marketSetupQuery,
  marketSpendQuery,
  valueChainQuery,
  type ListeningStats,
  type MarketBriefing,
  type MarketSetupState,
  type MarketSpend,
} from './queries';
import { StrategyPage } from './StrategyPage';

/* Strategy's Market tab: its figures, rows and panels, its definition and its set-up. Sample data only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
const toasts = vi.hoisted(() => vi.fn());
vi.mock('@/kit/Toast', async (real) => ({ ...(await real<object>()), toast: toasts }));

const person = { id: 'human:me', email: '', role: 'person', slug: null };
const owner = { ...person, role: 'owner' };
beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  toasts.mockClear();
  queryClient.setQueryData(['me'], person);
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
/** Answers every read with `reply`, and keeps each write. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });
const refused = (status: number, code: string, detail?: string): Reply => ({ status, body: { error: { code, ...(detail && { detail }) } } });

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
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');

const spend: MarketSpend = {
  name: 'Dog grooming in Ohio',
  year: 2026,
  definition: {
    description: 'What Ohio dog owners spend on grooming',
    year: 2026,
    geography: 'Ohio',
    currency: 'USD',
    includes: ['Mobile grooming'],
    excludes: ['Pet food'],
    params: { vans_per_city: 12, avg_visit_price: { value: 80, judgement: true } },
  },
  total: { spend_usd: 48_000_000 },
  segments: [
    {
      id: 's1',
      name: 'Mobile grooming',
      summary: 'Vans that come to the house.',
      size: { id: 'z1', spend_usd: 12_000_000, low_usd: 9_000_000, high_usd: 15_000_000, growth_pct: 6.5, confidence: 'medium', accepted: false },
      estimates: [{ id: 'z1', method: 'bottom_up', status: 'proposed', spend_usd: 12_000_000, low_usd: 9_000_000, high_usd: 15_000_000, arithmetic: '4,000 vans x $3,000', params: { vans_per_city: 4000 } }],
      players: [{ entity_id: 'e1', name: 'Groomly', figures: [{ metric: 'revenue', value: 3_000_000, kind: 'reported' }] }],
    },
    { id: 's2', name: 'Salons' },
  ],
};
const briefing: MarketBriefing = {
  briefing: { week: '2026-W46', title: 'Week of Nov 9', body: 'Groomly raised prices.', created_at: '2026-11-10T10:00:00Z', items: [{ text: 'Groomly raised prices' }], touched: [{ id: 'b1', n: 1, statement: 'Win mobile grooming' }] },
};
const chain = { layers: [{ key: 'suppliers', title: 'Suppliers', nodes: [{ id: 'e3', name: 'Van Co' }] }, { key: 'us', title: 'Us and rivals', nodes: [{ id: 'e1', name: 'Groomly' }] }, { key: 'buyers', title: 'Buyers', nodes: [] }] };
function market(over: { spend?: MarketSpend; count?: number } = {}) {
  queryClient.setQueryData(marketSpendQuery.queryKey, over.spend ?? spend);
  queryClient.setQueryData(marketBriefingQuery.queryKey, briefing);
  queryClient.setQueryData(valueChainQuery.queryKey, chain);
  queryClient.setQueryData(marketCountQuery.queryKey, over.count ?? 3);
}
const at = (panel?: string) => `/company/strategy/market${panel ? `?panel=${panel}` : ''}`;

describe('Market: what buyers say @s326', () => {
  it('counts zero buyer posts today when the latest posts were yesterday', async () => {
    serve(nothing);
    market();
    vi.setSystemTime(new Date('2026-11-19T05:00:00Z'));
    try {
      queryClient.setQueryData<ListeningStats>(listeningStatsQuery.queryKey, { daily: [{ day: '2026-11-18', sources: { Reddit: 7 } }] });
      show(<StrategyPage tab="market" />, at());
      const figures = await screen.findByRole('group', { name: 'Market figures' });
      expect(within(figures).getByText('Buyer posts').parentElement?.querySelector('b')?.textContent).toBe('0');
    } finally {
      vi.useRealTimers();
    }
  });

  it('lists the newest buyer posts with their site, each opening where it was written', async () => {
    serve(nothing);
    market();
    const stats: ListeningStats = {
      recent: [
        { item_id: 'i1', source: 'Reddit', text: 'Mobile groomers cost too much', url: 'https://example.com/post/1', at: '2026-11-18T10:00:00Z' },
        { item_id: 'i2', source: 'News', text: 'Salons add evening hours', url: null },
      ],
    };
    queryClient.setQueryData(listeningStatsQuery.queryKey, stats);
    show(<StrategyPage tab="market" />, at());
    const say = await screen.findByRole('region', { name: 'Buyers talk about' });
    const post = within(say).getByText('Mobile groomers cost too much').closest('a');
    expect(post?.getAttribute('href')).toBe('https://example.com/post/1');
    expect(post?.getAttribute('target')).toBe('_blank');
    expect(within(post!).getByText('Reddit')).toBeTruthy();
    expect(within(say).getByText('Salons add evening hours').closest('a')).toBeNull();
  });

  it('leaves the section out when Listening sent nothing', async () => {
    serve(nothing);
    market();
    queryClient.setQueryData(listeningStatsQuery.queryKey, { recent: [] } as ListeningStats);
    show(<StrategyPage tab="market" />, at());
    await screen.findByRole('link', { name: 'Mobile grooming' });
    expect(screen.queryByRole('region', { name: 'Buyers talk about' })).toBeNull();
  });
});

describe('Market @s302 @s319 @s320 @s324 @s325 @s357 @s360 @s361', () => {
  it('sizes the market on top, then lists segments, changes, the value chain in order and the organizations, each opening its panel', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" />, at());
    expect(href(await screen.findByRole('link', { name: 'Mobile grooming' }))).toBe('/company/strategy/market?panel=segment:s1');
    // where the money goes: the market's yearly spend, then each segment's
    const figures = screen.getByRole('group', { name: 'Market figures' });
    expect(figures.textContent).toContain('$48M');
    expect(figures.textContent).toContain('Dog grooming in Ohio');
    const segs = screen.getByRole('table', { name: 'Segments' });
    expect(within(segs).getByText('$12M')).toBeTruthy();
    expect(within(segs).getByText('6.5%')).toBeTruthy();
    expect(within(segs).getByText('Not sized')).toBeTruthy();
    expect(href(within(screen.getByRole('region', { name: 'Changes' })).getByText('Groomly raised prices'))).toBe('/company/strategy/market?panel=briefing');
    const layers = within(screen.getByRole('region', { name: 'How it reaches buyers' })).getAllByRole('listitem');
    expect(layers.map((l) => l.getAttribute('aria-label'))).toEqual(['Suppliers', 'Us and rivals']);
    const groomly = within(layers[1]!).getByRole('link');
    expect(href(groomly)).toBe('/company/strategy/market?panel=player:e1');
    expect(groomly.textContent).toContain('$3M');
    // who we are up against: each organization with what it sells and its figure, opening its note
    const orgs = screen.getByRole('table', { name: 'Organizations' });
    const row = within(orgs).getByText('Groomly').closest('tr')!;
    expect(row.textContent).toContain('Mobile grooming');
    expect(row.textContent).toContain('$3M');
    expect(href(within(orgs).getByText('Groomly'))).toBe('/company/strategy/market?panel=player:e1');
    expect(document.querySelector('a[href*="map"]')).toBeNull();
  });

  it('says so when nothing is there and the reader is not the owner', async () => {
    serve(nothing);
    market({ spend: { segments: [] }, count: 0 });
    queryClient.setQueryData(marketBriefingQuery.queryKey, { briefing: null });
    show(<StrategyPage tab="market" />, at());
    expect(await screen.findByText('Nothing here yet')).toBeTruthy();
  });

  it('shows the fault when Market does not load', async () => {
    serve((url) => (url.includes('/api/v2/market/') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(<StrategyPage tab="market" />, at());
    expect(await screen.findByText('Strategy did not load')).toBeTruthy();
  });

  it('opens a segment: its size, estimates and organizations; the owner accepts an estimate', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: { size: {} } } : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    expect(within(panel).getByText('Medium confidence')).toBeTruthy();
    expect(within(panel).getByText('Bottom up')).toBeTruthy();
    expect(within(panel).getByText('4,000 vans x $3,000')).toBeTruthy();
    expect(within(panel).getByText('Revenue $3M, Reported')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/market/sizes/z1/accept', method: 'POST', body: {} }]));
    await waitFor(() => expect(within(panel).queryByRole('button', { name: 'Accept' })).toBeNull());
  });

  it('takes back a refused accept, saying why', async () => {
    serve((_url, method) => (method === 'POST' ? refused(403, 'forbidden') : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(within(panel).getByRole('button', { name: 'Accept' })).toBeTruthy();
  });

  it('saves a parameter for the owner, its field named in words', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: {} } : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    expect(within(panel).queryByText('vans_per_city')).toBeNull();
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'Vans per city' }), { target: { value: '5000' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/market/sizes/z1/params', method: 'POST', body: { params: { vans_per_city: 5000 } } }]));
  });

  it("takes back a parameter the server refuses, saying why", async () => {
    serve((_url, method) => (method === 'POST' ? refused(403, 'forbidden') : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'Vans per city' }), { target: { value: '5000' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<MarketSpend>(marketSpendQuery.queryKey)?.segments?.[0]?.estimates?.[0]?.params).toEqual({ vans_per_city: 4000 });
  });

  it('reads the week’s briefing in full, with the bets it touches', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'briefing' }} />, at('briefing'));
    const panel = await screen.findByRole('complementary', { name: 'Week of Nov 9' });
    expect(within(panel).getByText('Bet 1')).toBeTruthy();
    expect(within(panel).getByText('Win mobile grooming')).toBeTruthy();
    expect(within(panel).getByText('Groomly raised prices.')).toBeTruthy();
  });

  it("opens an organization's note: its relations open other notes, and a note that fails says so", async () => {
    const note = {
      entity: { id: 'e1', name: 'Groomly', type: 'company', tier: 'core', summary: 'Mobile grooming vans.', aliases: ['Groomly', 'Groomly Inc'] },
      edges: { competes_with: { out: [{ src: 'e1', dst: 'e4' }], in: [] } },
      evidence: [{ our_read: 'They lead on price.', quote: 'From $49' }],
      neighbors: [{ id: 'e4', name: 'Wagz' }],
    };
    serve((url) => (url.includes('/market/entities/e1') ? { status: 200, body: note } : nothing()));
    market();
    show(<StrategyPage tab="market" search={{ panel: 'player:e1' }} />, at('player:e1'));
    const panel = await screen.findByRole('complementary', { name: 'Groomly' });
    expect(await within(panel).findByText('Mobile grooming vans.')).toBeTruthy();
    expect(within(panel).getByText('Groomly Inc')).toBeTruthy();
    expect(within(panel).getByText('competes with')).toBeTruthy();
    expect(href(within(panel).getByText('Wagz'))).toBe('/company/strategy/market?panel=player:e4');
    expect(within(panel).getByText('They lead on price.')).toBeTruthy();
  });

  it('says so when a note does not load', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'player:e9' }} />, at('player:e9'));
    expect(await screen.findByText('The note did not load')).toBeTruthy();
  });
});

describe('Market definition @s358 @s359', () => {
  it('shows what a sized market counts, its shared parameters in words, never their keys', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'definition' }} />, at('definition'));
    const panel = await screen.findByRole('complementary', { name: 'Definition' });
    expect(within(panel).getByText('What Ohio dog owners spend on grooming')).toBeTruthy();
    expect(within(panel).getByText('Ohio')).toBeTruthy();
    expect(within(panel).getByRole('region', { name: 'Includes' }).textContent).toContain('Mobile grooming');
    const shared = within(panel).getByRole('region', { name: 'Shared parameters' });
    expect(within(shared).getByText('Vans per city')).toBeTruthy();
    expect(within(shared).getByText('Avg visit price')).toBeTruthy();
    expect(shared.textContent).not.toContain('_');
    expect(within(panel).queryByRole('button', { name: 'Update' })).toBeNull();
  });

  it('lets the owner size a market not defined yet', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: { task_id: 't1', sizing: { id: 't1', turn: 'bot' } } } : nothing()));
    queryClient.setQueryData(['me'], owner);
    market({ spend: { segments: [] } });
    show(<StrategyPage tab="market" search={{ panel: 'definition' }} />, at('definition'));
    const panel = await screen.findByRole('complementary', { name: 'Definition' });
    expect(within(panel).getByText('Not defined yet')).toBeTruthy();
    fireEvent.change(within(panel).getByRole('textbox', { name: 'The market in a sentence' }), { target: { value: 'What Ohio dog owners spend on grooming' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Size it' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/market/spend/research', method: 'POST', body: { description: 'What Ohio dog owners spend on grooming' } }]));
    expect(await within(panel).findByText('Under way')).toBeTruthy();
  });

  it('says when the Librarian is not running to size it', async () => {
    serve((_url, method) => (method === 'POST' ? refused(409, 'librarian') : nothing()));
    queryClient.setQueryData(['me'], owner);
    market({ spend: { segments: [] } });
    show(<StrategyPage tab="market" search={{ panel: 'definition' }} />, at('definition'));
    const panel = await screen.findByRole('complementary', { name: 'Definition' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'The market in a sentence' }), { target: { value: 'Grooming' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Size it' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'The Librarian is not running yet', kind: 'failed' }));
  });
});

describe('Market set-up @s329', () => {
  const setup = (over: Partial<MarketSetupState> = {}): MarketSetupState => ({ answers: { sell: '', buyers: '', competitors: '', watch: '' }, can_answer: true, skipped: false, step: 0, task: null, ...over });
  const blank = (s: MarketSetupState) => {
    queryClient.setQueryData(['me'], owner);
    market({ spend: { segments: [] }, count: 0 });
    queryClient.setQueryData(marketBriefingQuery.queryKey, { briefing: null });
    queryClient.setQueryData(marketSetupQuery.queryKey, s);
  };

  it('asks the owner the first question, and keeps the answer on Next', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: setup({ step: 1, answers: { sell: 'https://example.com', buyers: '', competitors: '', watch: '' } }) } : nothing()));
    blank(setup());
    show(<StrategyPage tab="market" />, at());
    fireEvent.change(await screen.findByRole('textbox', { name: 'What do you sell?' }), { target: { value: 'https://example.com' } });
    expect(screen.getByText(/1 of 4/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(sent[0]?.body).toEqual({ answers: { sell: 'https://example.com', buyers: '', competitors: '', watch: '' }, step: 1, action: 'save' }));
    expect(await screen.findByRole('textbox', { name: 'Who buys it?' })).toBeTruthy();
  });

  it('asks for what they sell before starting the research', async () => {
    const sent = serve(nothing);
    blank(setup({ step: 3 }));
    show(<StrategyPage tab="market" />, at());
    fireEvent.click(await screen.findByRole('button', { name: 'Start research' }));
    expect(await screen.findByText('Say what you sell')).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it('says the research is under way once filed, and waits with Continue when set aside', async () => {
    serve(nothing);
    blank(setup({ task: { id: 't7', status: 'open', created_at: '2026-11-19T10:00:00Z' } }));
    show(<StrategyPage tab="market" />, at());
    expect(await screen.findByText('Researching your market')).toBeTruthy();
    expect(href(screen.getByText('View task'))).toBe('/task/t7');
  });

  it('shows how many were answered when set aside', async () => {
    serve(nothing);
    blank(setup({ skipped: true, answers: { sell: 'Grooming', buyers: '', competitors: '', watch: '' } }));
    show(<StrategyPage tab="market" />, at());
    expect(await screen.findByText('1 of 4 answered')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeTruthy();
  });

  it('says when the Librarian is not running to start the research', async () => {
    serve((_url, method) => (method === 'POST' ? refused(409, 'librarian') : nothing()));
    blank(setup({ step: 3, answers: { sell: 'Grooming', buyers: '', competitors: '', watch: '' } }));
    show(<StrategyPage tab="market" />, at());
    fireEvent.click(await screen.findByRole('button', { name: 'Start research' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'The Librarian is not running yet', kind: 'failed' }));
  });
});
