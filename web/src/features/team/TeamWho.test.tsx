import { QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentsQuery, changeAgent, groupsQuery, moveToTeam, type AgentRow, type Group } from './queries';
import { loadWho } from './routing';
import { TeamWho } from './TeamWho';
import type { GoalTree } from '@/api/schema';
import { goalsTreeQuery } from '@/features/company/exports';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const r = (await reply(u, init?.method ?? 'GET')) ?? fallback(u);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
// Every read a page makes, answered empty unless a test says otherwise.
const fallback = (url: string): Reply => {
  if (url.includes('/api/v2/groups')) return { status: 200, body: groups };
  if (url.includes('/api/employees')) return { status: 200, body: roster };
  if (url.includes('/api/runs')) return { status: 200, body: [] };
  if (url.includes('/api/v2/tasks')) return { status: 200, body: { tasks: [] } };
  return { status: 200, body: {} };
};

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

// Ana is a newer agent: her bot id is not her slug, as groups, computers and tasks name her.
const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const bo: Actor = { id: 'bot:bo', kind: 'agent', name: 'Bo', slug: 'bo' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const actors = [ana, bo, pat];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [
  { id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p1'] },
  { id: 'g2', name: 'Brand', parent: 'g1', lead: '', bots: ['bo'], people: [] },
  { id: 'g3', name: 'Sales', parent: '', lead: '', bots: [], people: [] },
];
const roster: AgentRow[] = [
  { id: '0190-ana', slug: 'ana', display_name: 'Ana', description: 'Writes copy', revision: 3 },
  { id: 'bo', slug: 'bo', display_name: 'Bo', revision: 1 },
];

const link = (title: string) => screen.queryAllByTitle(title).find((el) => el.tagName === 'A');

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
});
afterEach(() => vi.unstubAllGlobals());

describe('Team pages', () => {
  it('lists a team, the teams inside it, and each row opens that teammate’s page @s249', async () => {
    serve(() => undefined);
    show(<TeamWho who="design" />, '/team/design');
    await screen.findByRole('heading', { name: 'Design' });
    await waitFor(() => expect(link('Ana')?.getAttribute('href')).toBe('/team/ana'));
    expect(link('Pat Doe')?.getAttribute('href')).toBe('/team/p1');
    // a team inside is one row that opens its own page; its people are on that page
    expect(link('Brand')?.getAttribute('href')).toBe('/team/team%3ABrand');
    expect(link('Bo')).toBeUndefined();
  });

  it('marks the lead, faces the team in its head, and runs every open task its teammates hold, the latest first, with no mark before a title', async () => {
    queryClient.setQueryData(groupsQuery.queryKey, groups.map((g) => (g.id === 'g1' ? { ...g, lead: 'person:p1' } : g)));
    serve((url) => {
      if (!url.startsWith('/api/v2/tasks?') || new URLSearchParams(url.split('?')[1]).get('status') !== 'open') return;
      const tasks = [
        { id: 't1', number: 1, pipeline_id: 'eng', title: 'Older copy pass', owner_id: ana.id, late: true, updated_at: '2026-11-10T10:00:00Z' },
        { id: 't2', number: 2, pipeline_id: 'eng', title: 'Brand refresh', owner_id: bo.id, turn: { state: 'person', who: pat.id }, updated_at: '2026-11-18T10:00:00Z' },
        { id: 't3', number: 3, pipeline_id: 'eng', title: 'Someone else', owner_id: 'bot:zed', updated_at: '2026-11-19T10:00:00Z' },
      ];
      return { status: 200, body: { tasks } };
    });
    show(<TeamWho who="design" />, '/team/design');
    const running = await screen.findByRole('region', { name: 'Running' });
    await waitFor(() => expect(within(running).getAllByRole('link').map((a) => a.getAttribute('title'))).toEqual(['Brand refresh', 'Older copy pass']));
    expect(within(running).getByRole('img', { name: 'Bo' })).toBeTruthy();
    expect(within(running).getByText('Late')).toBeTruthy();
    // the sketch draws nothing before a row's title: no Flow mark, even on a task that waits on you
    expect(within(running).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    const people = screen.getByRole('region', { name: 'People' });
    expect(within(people).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    expect(within(people).getAllByRole('link').map((a) => a.getAttribute('title'))).toEqual(['Pat Doe', 'Ana']);
    expect(within(link('Pat Doe')!).getByText('Lead')).toBeTruthy();
    expect(within(document.querySelector<HTMLElement>('.pk-head')!).getByRole('img', { name: 'Ana' })).toBeTruthy();
    expect(screen.queryByRole('tab')).toBeNull();
  });

  it('counts completed work across every page for the team and its subteams', async () => {
    const reads: URLSearchParams[] = [];
    serve((url) => {
      if (!url.startsWith('/api/v2/tasks?')) return;
      const params = new URLSearchParams(url.split('?')[1]);
      if (params.get('status') !== 'done') return;
      reads.push(params);
      return { status: 200, body: { tasks: [{ id: 't1', owner_id: ana.id }], counts: { total: 612 } } };
    });
    show(<TeamWho who="design" />, '/team/design');
    expect(await screen.findByText('612')).toBeTruthy();
    expect(reads[0]?.get('owners')?.split(',')).toEqual([ana.id, bo.id, pat.id]);
    expect(reads[0]?.get('counts')).toBe('stage');
    expect(reads[0]?.get('limit')).toBe('1');
    expect(reads[0]?.get('updated_since')).toBeTruthy();
  });

  it('starts the team spend read without waiting for the agents roster', async () => {
    queryClient.removeQueries({ queryKey: agentsQuery.queryKey });
    let finish!: (reply: Reply) => void;
    const agents = new Promise<Reply>((resolve) => { finish = resolve; });
    let spendQuery: URLSearchParams | undefined;
    serve((url) => {
      if (url === '/api/employees') return agents;
      if (url.startsWith('/api/v2/usage?')) {
        spendQuery = new URLSearchParams(url.split('?')[1]);
        return { status: 200, body: { totals: { est_cost_usd: 42 } } };
      }
    });
    show(<TeamWho who="design" />, '/team/design');
    expect(await screen.findByText('$42')).toBeTruthy();
    expect(spendQuery?.get('bots')).toBe('ana,bo');
    expect(spendQuery?.get('group')).toBe('day');
    await act(async () => finish({ status: 200, body: roster }));
  });

  it("counts every page of the members' open tasks, late ones included", async () => {
    const reads: URLSearchParams[] = [];
    const open = (id: string, late = false) => ({ id, number: null, title: id, status: 'open', owner_id: ana.id, late: late ? { level: 'told' } : null });
    serve((url) => {
      if (!url.startsWith('/api/v2/tasks?')) return;
      const params = new URLSearchParams(url.split('?')[1]);
      if (params.get('status') !== 'open') return;
      reads.push(params);
      return params.get('after') === 'k1'
        ? { status: 200, body: { tasks: [open('t2', true), open('t3', true)], next_after: null } }
        : { status: 200, body: { tasks: [open('t1')], next_after: 'k1' } };
    });
    show(<TeamWho who="design" />, '/team/design');
    expect(await screen.findByText('2 late')).toBeTruthy();
    expect(link('Ana')?.querySelector('.count')?.textContent).toBe('3');
    expect(reads.map((p) => p.get('after'))).toEqual([null, 'k1']);
    expect(reads[0]?.get('owners')?.split(',')).toEqual([ana.id, bo.id, pat.id]);
  });

  it('moves a teammate out at once and puts them back when the server refuses', async () => {
    let refuse!: () => void;
    serve((_url, method) => (method === 'PATCH' ? new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { detail: 'no' } }))) : undefined));
    show(<TeamWho who="design" />, '/team/design');
    await waitFor(() => expect(link('Ana')).toBeTruthy());
    let done!: Promise<unknown>;
    act(() => {
      done = moveToTeam('agent', '0190-ana', 'g3').catch(() => 'refused');
    });
    await waitFor(() => expect(link('Ana')).toBeUndefined());
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => {
      refuse();
      expect(await done).toBe('refused');
    });
    await waitFor(() => expect(link('Ana')?.getAttribute('href')).toBe('/team/ana'));
  });

  it('shows an agent’s new role at once and takes it back when the server refuses @s615', async () => {
    let refuse!: () => void;
    serve((url, method) =>
      method === 'POST' && url.includes('/definition') ? new Promise<Reply>((r) => (refuse = () => r({ status: 409, body: { detail: 'changed' } }))) : undefined,
    );
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    expect(await screen.findByText('Writes copy')).toBeTruthy();
    expect(link('Ana')).toBeUndefined();
    let done!: Promise<unknown>;
    act(() => {
      done = changeAgent('ana', { description: 'Writes the newsletter' }).catch(() => 'refused');
    });
    expect(await screen.findByText('Writes the newsletter')).toBeTruthy();
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => {
      refuse();
      expect(await done).toBe('refused');
    });
    expect(await screen.findByText('Writes copy')).toBeTruthy();
    expect(screen.queryByText('Writes the newsletter')).toBeNull();
  });

  it('links the agent’s computer and instructions as tabs of its own address', async () => {
    serve((url) => (url.includes('/instructions') ? { status: 200, body: { content: 'Write short.' } } : undefined));
    show(<TeamWho who="ana" tab="settings" />, '/team/ana?tab=settings');
    await screen.findByText('Write short.');
    expect(screen.getByTitle('Open the instructions').getAttribute('href')).toBe('/team/ana?tab=instructions');
    expect(screen.getByTitle('Open the computer').getAttribute('href')).toBe('/team/ana?tab=computer');
  });

  it('opens the model picker over Settings at ?tab=model', async () => {
    serve(() => undefined);
    show(<TeamWho who="ana" tab="model" />, '/team/ana?tab=model');
    expect(await screen.findByRole('dialog', { name: 'Model' })).toBeTruthy();
    expect(await screen.findByText('Writes copy')).toBeTruthy();
  });

  it('lists a person’s goals, each opening that goal’s sheet', async () => {
    serve(() => undefined);
    const kpi = { id: 'k1', name: 'Signups', unit: '', latest: { value: 12 }, link: { target: 20 } };
    queryClient.setQueryData(goalsTreeQuery.queryKey, {
      goals: [
        { id: 'g1', title: 'Grow signups', owner_id: pat.id, kpis: [kpi] },
        { id: 'g2', title: 'Ship kits', owner_id: ana.id, kpis: [] },
      ],
      other_kpis: [],
      owners: {},
      proposals: [],
      unaligned: {},
    } as unknown as GoalTree);
    show(<TeamWho who="p1" />, '/team/p1');
    await waitFor(() => expect(link('Grow signups')?.getAttribute('href')).toBe('/company/strategy/goals?panel=goal%3Ag1'));
    expect(screen.getByText('12 of 20')).toBeTruthy();
    expect(link('Ship kits')).toBeUndefined();
  });

  it('says so for a team with nobody in it', async () => {
    serve(() => undefined);
    show(<TeamWho who="sales" />, '/team/sales');
    expect(await screen.findByRole('heading', { name: 'No teammates yet' })).toBeTruthy();
  });

  it('shows open seats even when a team has no teammates yet', async () => {
    serve((url) =>
      url === '/api/humans' ? { status: 200, body: { seats: [{ id: 's1', title: 'Account manager', team: 'g3' }] } } : undefined,
    );
    show(<TeamWho who="sales" />, '/team/sales');
    expect(await screen.findByText('Account manager')).toBeTruthy();
    expect(screen.getByText('Open seat')).toBeTruthy();
  });

  it('says so for an address that names no one', async () => {
    serve(() => undefined);
    show(<TeamWho who="nobody" />, '/team/nobody');
    expect(await screen.findByRole('heading', { name: 'No such teammate' })).toBeTruthy();
  });
});

describe('Loading a teammate', () => {
  const read = async (who: string, tab?: Parameters<typeof loadWho>[2]) => {
    const urls: string[] = [];
    serve((url) => (urls.push(url), undefined));
    await loadWho(queryClient, who, tab);
    return urls;
  };

  it('reads what the page it opens on draws first, before it draws', async () => {
    queryClient.removeQueries({ queryKey: agentsQuery.queryKey });
    expect(await read('ana')).toEqual([expect.stringContaining('/api/v2/tasks?owner=bot%3A0190-ana&status=open')]);
    expect(await read('ana', 'settings')).toEqual(['/api/employees']);
    expect(await read('p1', 'slack')).toEqual([expect.stringContaining('/humans/p1/slack')]);
    expect(await read('design')).toEqual([]);
  });

  it('throws a failed read to the router, which draws the fault', async () => {
    serve(() => ({ status: 500, body: { detail: 'down' } }));
    await expect(loadWho(queryClient, 'ana', 'instructions')).rejects.toThrow();
  });

  it('opens a team when the shell detaches from the in-flight directory read: the one read carries on', async () => {
    queryClient.removeQueries({ queryKey: directoryQuery.queryKey });
    const org = { status: 200, body: { org_groups: [], people: [], bots: [] } };
    let finish: (reply: Reply) => void = () => {};
    const first = new Promise<Reply>((resolve) => {
      finish = resolve;
    });
    let reads = 0;
    serve((url) => {
      if (url !== '/api/v2/org') return undefined;
      reads++;
      return reads === 1 ? first : org;
    });
    const observer = new QueryObserver(queryClient, directoryQuery);
    const unsubscribe = observer.subscribe(() => {});
    const page = loadWho(queryClient, 'design', undefined);
    unsubscribe();
    finish(org);
    await expect(page).resolves.toBeUndefined();
    expect(reads).toBe(1);
  });
});
