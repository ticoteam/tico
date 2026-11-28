import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the router's error component, as the app wires it
import { PageFault } from '@/shell/PageFault';
import type { FactoryMap } from '@/api/schema';
import { factoryGroups, moveTargets } from './OrgPage';
import { accessQuery, factoriesQuery, groupsQuery, moveTeammate, type Team } from './queries';
import { StrategyPage } from './StrategyPage';

/* Strategy's Org tab: the chart, a teammate's sheet and the factories. Sample data only. */

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
function serve(reply: (url: string, method: string, body: unknown) => Reply): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body: unknown = init?.body ? JSON.parse(String(init.body)) : null;
    if (method !== 'GET') sent.push({ url: String(url), method, body });
    const r = reply(String(url), method, body);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });
const refused = (status: number, code: string): Reply => ({ status, body: { error: { code } } });

const ORG = '/company/strategy/org';
/** Org at its own address; with no `search` it reads the address's search, as the route does. */
function show(at = ORG, search?: Record<string, unknown>) {
  function Routed() {
    const live = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <StrategyPage tab="org" search={search ?? live} />;
  }
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: Routed });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [at] }), defaultErrorComponent: PageFault });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');
async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}
const actor = (id: string, name: string, kind: 'person' | 'agent' = 'person') => ({ id, kind, name });
function directory(...actors: ReturnType<typeof actor>[]) {
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
}
const g = (id: string, name: string, over: Partial<Team> = {}): Team => ({ id, name, parent: '', order: 0, people: [], bots: [], bot_slugs: [], ...over });

describe('Org @s309', () => {
  it('draws each team under its lead, a group inside it folding on its own, each teammate opening their sheet', async () => {
    serve(nothing);
    directory(actor('human:lee', 'Lee Park'), actor('human:ana', 'Ana Ruiz'), actor('human:bo', 'Bo Chen'));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { order: 1, people: ['lee', 'ana'], lead: 'person:lee' }), g('east', 'East', { parent: 'sales', order: 2, people: ['bo'] })]);
    queryClient.setQueryData(accessQuery.queryKey, { you: 'owner', owner: 'lee' });
    show();
    const sales = await screen.findByRole('region', { name: 'Sales' });
    // the company's owner (Access names them) is the root, with Tico beside them; Add teammate is the filled button
    const root = document.querySelector('.oc-root')!;
    expect(root.querySelector('.oc-me')?.getAttribute('href')).toBe('/team/lee');
    expect(root.textContent).toContain('Lee Park');
    expect(within(root as HTMLElement).getByText('Tico')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add teammate' })).toBeTruthy();
    expect(href(within(sales).getByRole('link', { name: 'Sales lead' }))).toBe('/company/strategy/org?panel=mate:human:lee');
    // the lead heads the team and is not listed again among its teammates
    expect(within(sales).getAllByText('Lee Park')).toHaveLength(1);
    expect(href(within(sales).getByText('Ana Ruiz'))).toBe('/company/strategy/org?panel=mate:human:ana');
    expect(within(sales).getByText('Bo Chen')).toBeTruthy();
    fireEvent.click(within(sales).getByRole('button', { name: /^East/ }));
    expect(within(sales).queryByText('Bo Chen')).toBeNull();
    expect(within(sales).getByText('Ana Ruiz')).toBeTruthy();
    fireEvent.click(within(sales).getByRole('button', { name: /^Sales/ }));
    expect(within(sales).queryByText('Ana Ruiz')).toBeNull();
  });

  it('keeps teammates reachable in groups nested more than one level down', async () => {
    serve(nothing);
    directory(actor('human:lee', 'Lee Park'));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales'), g('east', 'East', { parent: 'sales' }), g('retail', 'Retail', { parent: 'east', people: ['lee'] })]);
    show();
    const sales = await screen.findByRole('region', { name: 'Sales' });
    const lee = within(sales).getByRole('link', { name: /Lee Park/ });
    expect(href(lee)).toBe('/company/strategy/org?panel=mate:human:lee');
    fireEvent.click(within(sales).getByRole('button', { name: /^East/ }));
    expect(within(sales).queryByRole('link', { name: /Lee Park/ })).toBeNull();
  });

  it('keeps the Built-in group folded until it is opened', async () => {
    serve(nothing);
    directory(actor('human:ana', 'Ana Ruiz'), actor('bot:assistant', 'Assistant', 'agent'));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { order: 1, people: ['ana'] }), g('built-in', 'Built-in', { order: 2, bots: ['assistant'], bot_slugs: ['assistant'] })]);
    show();
    const built = await screen.findByRole('region', { name: 'Built-in' });
    expect(within(screen.getByRole('region', { name: 'Sales' })).getByText('Ana Ruiz')).toBeTruthy();
    expect(within(built).queryByText('Assistant')).toBeNull();
    fireEvent.click(within(built).getByRole('button', { name: /^Built-in/ }));
    expect(within(built).getByText('Assistant')).toBeTruthy();
  });

  it('opens an agent lead in its own group when its slug differs from its ID', async () => {
    serve(nothing);
    const agent = { ...actor('bot:agent-id', 'Builder', 'agent'), slug: 'builder' };
    queryClient.setQueryData(['directory'], { actors: [agent], byId: { [agent.id]: agent, 'bot:builder': agent } });
    queryClient.setQueryData(groupsQuery.queryKey, [g('ops', 'Operations', { bots: ['agent-id'], bot_slugs: ['builder'], lead: agent.id })]);
    const router = show();
    const ops = await screen.findByRole('region', { name: 'Operations' });
    expect(within(ops).getAllByText('Builder')).toHaveLength(1);
    fireEvent.click(within(ops).getByRole('link', { name: 'Operations lead' }));
    const lead = within(await screen.findByRole('complementary', { name: 'Builder' }));
    expect(lead.getByText('Group').nextElementSibling?.textContent).toBe('Operations');
    expect(href(lead.getByRole('link', { name: 'Open' }))).toBe('/team/builder');
    await router.navigate({ to: '.', search: { panel: 'mate:bot:builder' } as never });
    expect(lead.getByText('Group').nextElementSibling?.textContent).toBe('Operations');
  });

  it('moves one agent without replacing its ID or slug in the group cache', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { status: 200, body: {} } : nothing()));
    const ops = g('ops', 'Operations', { bots: ['agent-id', 'peer-id'], bot_slugs: ['builder', 'peer'] });
    const support = g('support', 'Support');
    queryClient.setQueryData(groupsQuery.queryKey, [ops, support]);
    await moveTeammate('bot:builder', 'ops', 'support');
    expect(sent).toEqual([{ url: '/api/v2/groups/support', method: 'PATCH', body: { add: { bots: ['agent-id'] } } }]);
    expect(queryClient.getQueryData(groupsQuery.queryKey)).toEqual([
      { ...ops, bots: ['peer-id'], bot_slugs: ['peer'] },
      { ...support, bots: ['agent-id'], bot_slugs: ['builder'] },
    ]);
  });

  it('takes a person out of their group, and only that group, when moved to no group', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { status: 200, body: {} } : nothing()));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { people: ['ana', 'lee'] }), g('east', 'East', { people: ['bo'] })]);
    await moveTeammate('human:ana', 'sales', '');
    expect(sent).toEqual([{ url: '/api/v2/groups/sales', method: 'PATCH', body: { remove: { people: ['ana'] } } }]);
  });

  it('says so when there are no teams, and offers Try again when the groups are refused', async () => {
    serve(nothing);
    queryClient.setQueryData(groupsQuery.queryKey, []);
    show();
    expect(await screen.findByText('No teams yet')).toBeTruthy();
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(['me'], person);
    serve((url) => (url.includes('/api/v2/groups') ? { status: 409, body: { detail: 'boom' } } : nothing()));
    show();
    expect(await screen.findByText(/didn't load · request refused$/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('Org teammate sheet @s362', () => {
  const groups: Team[] = [
    g('sales', 'Sales', { order: 1, people: ['ana'], lead: 'person:me' }),
    g('east', 'East', { parent: 'sales', order: 2 }),
    g('ops', 'Ops', { order: 3, bots: ['builder'], bot_slugs: ['builder'] }),
    g('built-in', 'General', { order: 4, bots: ['librarian'], bot_slugs: ['librarian'] }),
  ];
  const ANA = `${ORG}?panel=mate:human:ana`;
  beforeEach(() => {
    directory(actor('human:ana', 'Ana Ruiz'));
    queryClient.setQueryData(groupsQuery.queryKey, groups);
  });

  it('moves a teammate for the owner, at once', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { status: 200, body: groups[2] } : nothing()));
    queryClient.setQueryData(['me'], owner);
    show(ANA);
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
    show(ANA);
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    await pick(within(panel).getByRole('combobox', { name: 'Move to' }), /No group/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Move' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<Team[]>(groupsQuery.queryKey)?.find((x) => x.id === 'sales')?.people).toEqual(['ana']);
  });

  it('starts from the newly opened teammate’s own group when switching sheets', async () => {
    serve(nothing);
    queryClient.setQueryData(['me'], owner);
    directory(actor('human:lee', 'Lee Park'), actor('human:ana', 'Ana Ruiz'));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { people: ['lee'] }), g('support', 'Support', { people: ['ana'] })]);
    const router = show(`${ORG}?panel=mate:human:lee`);
    expect(within(await screen.findByRole('complementary', { name: 'Lee Park' })).getByRole('combobox', { name: 'Move to' }).textContent).toBe('Sales');
    await router.navigate({ to: '.', search: { panel: 'mate:human:ana' } as never });
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    expect(within(panel).getByRole('combobox', { name: 'Move to' }).textContent).toBe('Support');
    expect((within(panel).getByRole('button', { name: 'Move' }) as HTMLButtonElement).disabled).toBe(true);
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
    directory();
    show(`${ORG}?panel=mate:bot:builder`);
    const panel = await screen.findByRole('complementary', { name: 'builder' });
    expect(within(panel).getByRole('button', { name: 'Call builder' })).toBeTruthy();
    expect(within(panel).queryByRole('button', { name: 'Move' })).toBeNull();
  });
});

describe('Org leads', () => {
  const groups: Team[] = [
    g('growth', 'Growth', { order: 1, people: ['ana', 'lee'], lead: 'person:lee' }),
    g('product', 'Product', { order: 2, people: ['bo'] }),
    g('competitors', 'Competitors', { parent: 'product', order: 3, people: ['cy'], lead: 'person:cy' }),
    g('features', 'Features', { parent: 'product', order: 4, people: ['di'], bots: ['agent-id'], bot_slugs: ['builder'] }),
    g('built-in', 'General', { order: 5, bots: ['librarian'], bot_slugs: ['librarian'] }),
  ];
  const sheet = (who: string) => `${ORG}?panel=mate:${who}`;
  /** Accepts each change and answers with the group as the server keeps it. */
  const accept = (url: string, method: string, body: unknown): Reply => {
    if (method !== 'PATCH') return nothing();
    const gid = url.split('/').pop() ?? '';
    return { status: 200, body: { ...groups.find((x) => x.id === gid), ...(body as object) } };
  };
  const leadIn = (gid: string) => queryClient.getQueryData<Team[]>(groupsQuery.queryKey)?.find((x) => x.id === gid)?.lead;
  beforeEach(() => {
    directory(
      actor('human:ana', 'Ana Ruiz'),
      actor('human:lee', 'Lee Park'),
      actor('human:bo', 'Bo Chen'),
      actor('human:cy', 'Cy Diaz'),
      actor('human:di', 'Di Ng'),
      actor('bot:agent-id', 'Builder', 'agent'),
      actor('bot:librarian', 'Librarian', 'agent'),
    );
    queryClient.setQueryData(groupsQuery.queryKey, groups);
    queryClient.setQueryData(['me'], owner);
  });

  it('makes a teammate the lead in place of the old one, shown on the chart at once', async () => {
    const sent = serve(accept);
    show(sheet('human:ana'));
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    const row = within(panel).getByRole('region', { name: 'Lead' });
    expect(row.textContent).toContain('Replaces Lee Park');
    fireEvent.click(within(row).getByRole('button', { name: 'Make lead of Growth' }));
    const growth = screen.getByRole('region', { name: 'Growth' });
    await waitFor(() => expect(href(within(growth).getByRole('link', { name: 'Growth lead' }))).toBe('/company/strategy/org?panel=mate:human:ana'));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/groups/growth', method: 'PATCH', body: { lead: 'person:ana' } }]));
    expect(within(growth).getAllByText('Ana Ruiz')).toHaveLength(1);
    expect(within(growth).getByText('Lee Park')).toBeTruthy();
  });

  it('stops a lead leading, and the team says it has no lead', async () => {
    const sent = serve(accept);
    show(sheet('human:lee'));
    const row = within(await screen.findByRole('complementary', { name: 'Lee Park' })).getByRole('region', { name: 'Lead' });
    expect(row.textContent).toContain('Leads Growth');
    fireEvent.click(within(row).getByRole('button', { name: 'Stop leading' }));
    const growth = screen.getByRole('region', { name: 'Growth' });
    expect(await within(growth).findByText('No lead')).toBeTruthy();
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/groups/growth', method: 'PATCH', body: { lead: '' } }]));
    expect(leadIn('growth')).toBe('');
  });

  it('sends an agent lead by its ID when its sheet is opened by slug', async () => {
    const sent = serve(accept);
    show(sheet('bot:agent-id'));
    const panel = await screen.findByRole('complementary', { name: 'Builder' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Make lead of Features' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/groups/features', method: 'PATCH', body: { lead: 'bot:agent-id' } }]));
  });

  it('shows a nested team its lead, and a new one at once', async () => {
    serve(accept);
    show(sheet('human:di'));
    const product = await screen.findByRole('region', { name: 'Product' });
    expect(href(within(product).getByRole('link', { name: 'Competitors lead' }))).toBe('/company/strategy/org?panel=mate:human:cy');
    expect(within(product).getAllByText('Cy Diaz')).toHaveLength(1);
    expect(within(product).getAllByText('No lead')).toHaveLength(2); // Product and Features
    const panel = await screen.findByRole('complementary', { name: 'Di Ng' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Make lead of Features' }));
    await waitFor(() => expect(href(within(product).getByRole('link', { name: 'Features lead' }))).toBe('/company/strategy/org?panel=mate:human:di'));
    expect(within(product).getAllByText('No lead')).toHaveLength(1);
  });

  it('keeps the chart and says the reason in the sheet when the server refuses', async () => {
    serve((_url, method) => (method === 'PATCH' ? { status: 422, body: { error: { code: 'validation', detail: 'The lead must be in the group' } } } : nothing()));
    show(sheet('human:ana'));
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Make lead of Growth' }));
    expect((await within(panel).findByRole('alert')).textContent).toBe('The lead must be in the group');
    expect(leadIn('growth')).toBe('person:lee');
  });

  it('offers an Admin the same, and nobody a lead for the built-in team or no team', async () => {
    serve(nothing);
    queryClient.setQueryData(['me'], person);
    queryClient.setQueryData(accessQuery.queryKey, { you: 'admin', owner: null });
    queryClient.setQueryData(groupsQuery.queryKey, [...groups, g('east', 'East')]);
    directory(actor('human:ana', 'Ana Ruiz'), actor('bot:librarian', 'Librarian', 'agent'), actor('human:zed', 'Zed Ko'));
    const router = show(sheet('human:ana'));
    expect(within(await screen.findByRole('complementary', { name: 'Ana Ruiz' })).getByRole('button', { name: 'Make lead of Growth' })).toBeTruthy();
    await router.navigate({ to: '.', search: { panel: 'mate:bot:librarian' } as never });
    expect(within(await screen.findByRole('complementary', { name: 'Librarian' })).queryByRole('region', { name: 'Lead' })).toBeNull();
    expect(within(screen.getByRole('region', { name: 'General' })).queryByText('No lead')).toBeNull();
    await router.navigate({ to: '.', search: { panel: 'mate:human:zed' } as never });
    expect(within(await screen.findByRole('complementary', { name: 'Zed Ko' })).queryByRole('region', { name: 'Lead' })).toBeNull();
  });

  it('shows a member no Lead button and no "No lead"', async () => {
    serve(nothing);
    queryClient.setQueryData(['me'], person);
    queryClient.setQueryData(accessQuery.queryKey, { you: 'member', owner: null });
    show(sheet('human:ana'));
    const panel = await screen.findByRole('complementary', { name: 'Ana Ruiz' });
    expect(within(panel).queryByRole('region', { name: 'Lead' })).toBeNull();
    expect(screen.queryByText('No lead')).toBeNull();
    expect(href(within(screen.getByRole('region', { name: 'Product' })).getByRole('link', { name: 'Competitors lead' }))).toBe('/company/strategy/org?panel=mate:human:cy');
  });
});

describe('Org factories @s265', () => {
  const map: FactoryMap = {
    factories: [{ id: 'f1', name: 'Grooming', goal: '', manager_id: null, done: 0 }],
    pipelines: [
      { id: 'p2', name: 'Book', factory_id: 'f1', files_into: [], mark: null, open: 1 },
      { id: 'p1', name: 'Leads', factory_id: 'f1', files_into: ['p2'], mark: null, open: 2 },
      { id: 'p3', name: 'Hiring', factory_id: null, files_into: [], mark: null, open: 0 },
    ],
  };
  const org = (m: FactoryMap | null = map) => {
    queryClient.setQueryData(groupsQuery.queryKey, [g('ops', 'Ops')]);
    queryClient.setQueryData(factoriesQuery.queryKey, m);
  };

  it('lists each factory with its pipelines in the order work moves, each opening its tasks', async () => {
    serve(nothing);
    org();
    show();
    const sec = await screen.findByRole('region', { name: 'Factories' });
    const grooming = within(sec).getByRole('listitem', { name: 'Grooming' });
    expect(within(grooming).getAllByRole('link').map((a) => a.textContent)).toEqual(['Leads', 'Book']);
    expect(href(within(grooming).getByText('Leads'))).toBe('/tasks/p1');
    expect(href(within(sec).getByText('Hiring'))).toBe('/tasks/p3');
  });

  it('leaves the section out with no factory, and offers no New factory to a member', async () => {
    serve(nothing);
    org({ factories: [], pipelines: map.pipelines });
    show();
    await screen.findByRole('region', { name: 'Ops' });
    expect(screen.queryByRole('region', { name: 'Factories' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(factoryGroups(null)).toEqual([]);
  });

  it('lets an Admin make a factory from the ⋯, shown at once', async () => {
    const made = { ...map, factories: [...map.factories, { id: 'f2', name: 'Sales', goal: '', manager_id: null, done: 0 }] };
    const sent = serve((url, method) =>
      method === 'POST' ? { status: 200, body: { factory: { id: 'f2', name: 'Sales', goal: '' } } } : url.includes('/api/v2/factories') ? { status: 200, body: made } : nothing(),
    );
    org();
    queryClient.setQueryData(accessQuery.queryKey, { you: 'admin', owner: null });
    show(`${ORG}?panel=factory-new`, { panel: 'factory-new' });
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'New factory' })).toBeTruthy();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    const panel = await screen.findByRole('complementary', { name: 'New factory' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Sales' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/factories', method: 'POST', body: { name: 'Sales' } }]));
    expect(await screen.findByRole('listitem', { name: 'Sales' })).toBeTruthy();
  });

  it('says why a taken name is refused and takes the factory back', async () => {
    serve((url, method) => (method === 'POST' ? refused(409, 'factory_name') : url.includes('/api/v2/factories') ? { status: 200, body: map } : nothing()));
    queryClient.setQueryData(['me'], owner);
    org();
    show(`${ORG}?panel=factory-new`);
    const panel = await screen.findByRole('complementary', { name: 'New factory' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Grooming' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toasts).toHaveBeenCalled());
    expect(queryClient.getQueryData<FactoryMap>(factoriesQuery.queryKey)?.factories.map((f) => f.name)).toEqual(['Grooming']);
  });

  it('refuses Tico: a factory cannot be removed once made', async () => {
    const sent = serve(nothing);
    queryClient.setQueryData(['me'], owner);
    bind({ router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: [ORG] }) }) });
    expect(await run('org.newFactory', { name: 'Sales' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});
