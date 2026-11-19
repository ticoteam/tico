import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type { KpiDetail } from '@/api/schema';
import { moveTargets } from './OrgPage';
import {
  groupsQuery,
  kpiQuery,
  goalsTreeQuery,
  marketBriefingQuery,
  marketCountQuery,
  marketSetupQuery,
  marketSpendQuery,
  placedKpisQuery,
  valueChainQuery,
  type MarketBriefing,
  type MarketSetupState,
  type MarketSpend,
  type PlacedKpi,
  type Team,
} from './queries';
import { StrategyPage } from './StrategyPage';

/* Market's rows and panels, a KPI's place in the KPI tree, and a teammate's sheet on Org. Sample data only. */

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
const refused = (status: number, code: string): Reply => ({ status, body: { error: { code, detail: code } } });

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
async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

// ---------------------------------------------------------------- Market

const spend: MarketSpend = {
  name: 'Dog grooming in Ohio',
  year: 2026,
  definition: { description: 'What Ohio dog owners spend on grooming', year: 2026, geography: 'Ohio', currency: 'USD', includes: ['Mobile grooming'], excludes: ['Pet food'] },
  total: { spend_usd: 48_000_000 },
  segments: [
    {
      id: 's1',
      name: 'Mobile grooming',
      summary: 'Vans that come to the house.',
      size: { id: 'z1', spend_usd: 12_000_000, low_usd: 9_000_000, high_usd: 15_000_000, growth_pct: 6.5, confidence: 'medium', accepted: false },
      estimates: [{ id: 'z1', method: 'bottom_up', status: 'proposed', spend_usd: 12_000_000, low_usd: 9_000_000, high_usd: 15_000_000, arithmetic: '4,000 vans x $3,000', params: { vans: 4000 } }],
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

describe('Market @s325 @s357 @s360 @s361', () => {
  it('lists segments, changes, the value chain in order and the organizations, each opening its panel', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" />, at());
    expect(href(await screen.findByRole('link', { name: 'Mobile grooming' }))).toBe('/company/strategy/market?panel=segment:s1');
    expect(within(screen.getByRole('table', { name: 'Segments' })).getByText('Not sized')).toBeTruthy();
    expect(href(screen.getByText('Groomly raised prices'))).toBe('/company/strategy/market?panel=briefing');
    const layers = within(screen.getByRole('region', { name: 'How it reaches buyers' })).getAllByRole('listitem');
    expect(layers.map((l) => l.getAttribute('aria-label'))).toEqual(['Suppliers', 'Us and rivals']);
    const groomly = within(layers[1]!).getByRole('link');
    expect(href(groomly)).toBe('/company/strategy/market?panel=player:e1');
    expect(groomly.textContent).toContain('$3M');
    expect(href(within(screen.getByRole('table', { name: 'Organizations' })).getByText('Groomly'))).toBe('/company/strategy/market?panel=player:e1');
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

  it('saves a parameter for the owner', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: {} } : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'vans' }), { target: { value: '5000' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/market/sizes/z1/params', method: 'POST', body: { params: { vans: 5000 } } }]));
  });

  it("takes back a parameter the server refuses, saying why", async () => {
    serve((_url, method) => (method === 'POST' ? refused(403, 'forbidden') : nothing()));
    queryClient.setQueryData(['me'], owner);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'segment:s1' }} />, at('segment:s1'));
    const panel = await screen.findByRole('complementary', { name: 'Mobile grooming' });
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'vans' }), { target: { value: '5000' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<MarketSpend>(marketSpendQuery.queryKey)?.segments?.[0]?.estimates?.[0]?.params).toEqual({ vans: 4000 });
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
  it('shows what a sized market counts', async () => {
    serve(nothing);
    market();
    show(<StrategyPage tab="market" search={{ panel: 'definition' }} />, at('definition'));
    const panel = await screen.findByRole('complementary', { name: 'Definition' });
    expect(within(panel).getByText('What Ohio dog owners spend on grooming')).toBeTruthy();
    expect(within(panel).getByText('Ohio')).toBeTruthy();
    expect(within(panel).getByRole('region', { name: 'Includes' }).textContent).toContain('Mobile grooming');
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

// ---------------------------------------------------------------- a KPI's place

describe('KPI place @s367', () => {
  const placed: PlacedKpi[] = [
    { id: 'k1', name: 'Weekly signups', parent_kpi_id: null, growth_role: null, may_edit: true },
    { id: 'k2', name: 'Paying customers', parent_kpi_id: null, growth_role: 'north_star', may_edit: true },
    { id: 'k3', name: 'Trial starts', parent_kpi_id: 'k1', growth_role: 'input', may_edit: true },
  ];
  const seed = () => {
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], other_kpis: [], owners: {}, proposals: [], unaligned: {} });
    queryClient.setQueryData(placedKpisQuery.queryKey, placed);
  };

  it('offers Place in the KPI’s menu', async () => {
    serve(nothing);
    seed();
    const detail = { kpi: { id: 'k1', name: 'Weekly signups', unit: '', latest: null, owner_id: 'bot:growth', status: 'none' }, readings: [], links: [], may_edit: true, may_log: false } as unknown as KpiDetail;
    queryClient.setQueryData(kpiQuery('k1').queryKey, detail);
    show(<StrategyPage tab="goals" search={{ panel: 'kpi:k1' }} />, '/company/strategy/goals?panel=kpi:k1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Place in tree' })).toBeTruthy();
  });

  it('saves what it feeds and its role, never offering itself or a KPI under it', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: {} } : nothing()));
    seed();
    show(<StrategyPage tab="goals" search={{ panel: 'kpi-place:k1' }} />, '/company/strategy/goals?panel=kpi-place:k1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    fireEvent.click(within(panel).getByRole('combobox', { name: 'Feeds' }));
    expect(await screen.findByRole('option', { name: /Paying customers/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /Trial starts/ })).toBeNull();
    expect(screen.queryByRole('option', { name: /Weekly signups/ })).toBeNull();
    const option = screen.getByRole('option', { name: /Paying customers/ });
    fireEvent.pointerDown(option, { pointerType: 'mouse' });
    fireEvent.click(option);
    await pick(within(panel).getByRole('combobox', { name: 'Role' }), /Input/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/kpis/k1', method: 'POST', body: { parent_kpi_id: 'k2', growth_role: 'input' } }]));
  });

  it('takes a refused place back, saying why', async () => {
    serve((_url, method) => (method === 'POST' ? refused(403, 'forbidden') : nothing()));
    seed();
    show(<StrategyPage tab="goals" search={{ panel: 'kpi-place:k1' }} />, '/company/strategy/goals?panel=kpi-place:k1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    await pick(within(panel).getByRole('combobox', { name: 'Role' }), /North Star/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<PlacedKpi[]>(placedKpisQuery.queryKey)?.find((k) => k.id === 'k2')?.growth_role).toBe('north_star');
  });

  it('says so when the KPI tree does not load', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], other_kpis: [], owners: {}, proposals: [], unaligned: {} });
    show(<StrategyPage tab="goals" search={{ panel: 'kpi-place:k1' }} />, '/company/strategy/goals?panel=kpi-place:k1');
    expect(await screen.findByText('The KPI tree did not load')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- a teammate's sheet on Org

describe('Org teammate sheet @s362', () => {
  const g = (id: string, name: string, over: Partial<Team> = {}): Team => ({ id, name, parent: '', order: 0, people: [], bots: [], bot_slugs: [], ...over });
  const groups: Team[] = [
    g('sales', 'Sales', { order: 1, people: ['ana'], lead: 'person:me' }),
    g('east', 'East', { parent: 'sales', order: 2 }),
    g('ops', 'Ops', { order: 3, bots: ['builder'], bot_slugs: ['builder'] }),
    g('built-in', 'General', { order: 4, bots: ['librarian'], bot_slugs: ['librarian'] }),
  ];
  const ana = { id: 'human:ana', kind: 'person', name: 'Ana Ruiz', slug: null };
  const seed = () => {
    queryClient.setQueryData(['directory'], { actors: [ana], byId: { 'human:ana': ana } });
    queryClient.setQueryData(groupsQuery.queryKey, groups);
  };

  it('opens a teammate from the chart', async () => {
    serve(nothing);
    seed();
    show(<StrategyPage tab="org" />, '/company/strategy/org');
    expect(href(await screen.findByText('Ana Ruiz'))).toBe('/company/strategy/org?panel=mate:human:ana');
  });

  it('moves a teammate for the owner, at once', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { status: 200, body: groups[2] } : nothing()));
    queryClient.setQueryData(['me'], owner);
    seed();
    show(<StrategyPage tab="org" search={{ panel: 'mate:human:ana' }} />, '/company/strategy/org?panel=mate:human:ana');
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    expect(panel.querySelector('.fact-v')?.textContent).toBe('Sales');
    expect(href(within(panel).getByText('Open'))).toBe('/team/ana');
    await pick(within(panel).getByRole('combobox', { name: 'Move to' }), /Ops/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Move' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/groups/ops', method: 'PATCH', body: { add: { people: ['ana'] } } }]));
    expect(queryClient.getQueryData<Team[]>(groupsQuery.queryKey)?.find((x) => x.id === 'ops')?.people).toEqual(['ana']);
  });

  it('takes a refused move back, saying why', async () => {
    serve((_url, method) => (method === 'PATCH' ? refused(403, 'forbidden') : nothing()));
    queryClient.setQueryData(['me'], owner);
    seed();
    show(<StrategyPage tab="org" search={{ panel: 'mate:human:ana' }} />, '/company/strategy/org?panel=mate:human:ana');
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    await pick(within(panel).getByRole('combobox', { name: 'Move to' }), /No group/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Move' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<Team[]>(groupsQuery.queryKey)?.find((x) => x.id === 'sales')?.people).toEqual(['ana']);
  });

  it('lets a lead move only within what it leads, and nobody move a built-in agent', () => {
    const lead = { id: 'human:me', role: 'person' };
    expect(moveTargets(lead, 'human:ana', 'sales', groups).map((t) => t.value)).toEqual(['sales', 'east']);
    expect(moveTargets(lead, 'bot:builder', 'ops', groups)).toEqual([]);
    expect(moveTargets({ id: 'human:me', role: 'owner' }, 'bot:librarian', 'built-in', groups)).toEqual([]);
    expect(moveTargets({ id: 'human:me', role: 'owner' }, 'bot:builder', 'ops', groups)[0]).toEqual({ value: '', label: 'No group' });
  });

  it('offers a call for an agent, and no Move to a teammate who may not move them', async () => {
    serve(nothing);
    seed();
    queryClient.setQueryData(['directory'], { actors: [], byId: {} });
    show(<StrategyPage tab="org" search={{ panel: 'mate:bot:builder' }} />, '/company/strategy/org?panel=mate:bot:builder');
    const panel = await screen.findByRole('complementary', { name: 'builder' });
    expect(within(panel).getByRole('button', { name: 'Call builder' })).toBeTruthy();
    expect(within(panel).queryByRole('button', { name: 'Move' })).toBeNull();
  });
});
