// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentsQuery, groupsQuery, peopleQuery, type AgentRow, type Group } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Reply = { status: number; body: unknown };
function serve(reply: (url: string) => Reply | undefined) {
  vi.stubGlobal('fetch', async (url: string) => {
    const r = reply(String(url)) ?? { status: 200, body: {} };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const kim: Actor = { id: 'human:p2', kind: 'person', name: 'Kim Roe' };
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: '', bots: ['0190-ana'], people: ['p2'] }];
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, users: [] } as unknown as AgentRow];
const today = new Date().toISOString();
const task = (n: number, status: string, late: boolean) => ({
  id: `t${n}`, number: n, title: `Task ${n}`, status, pipeline_id: 'eng', stage_id: null, owner_id: kim.id, late, turn: null, updated_at: today,
});

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, {
    actors: [ana, kim],
    byId: { [ana.id]: ana, ana, '0190-ana': ana, [kim.id]: kim, p2: kim },
  });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(peopleQuery.queryKey, [{ id: 'p2', name: 'Kim Roe' }]);
});
afterEach(() => vi.unstubAllGlobals());

describe('run 6 faults on the team pages', () => {
  it('marks only an open task Late on a person page; a done one stays grey', async () => {
    serve((url) => {
      if (url.includes('/api/v2/tasks') && url.includes('status=open')) return { status: 200, body: { tasks: [task(1, 'open', true)] } };
      if (url.includes('/api/v2/tasks') && url.includes('status=done')) return { status: 200, body: { tasks: [task(2, 'done', true)] } };
      if (url.includes('/api/v2/pipelines')) return { status: 200, body: { pipelines: [] } };
      return undefined;
    });
    show(<TeamWho who="p2" />, '/team/p2');
    fireEvent.click(await screen.findByRole('button', { name: '1 this month' }));
    expect(await screen.findByText('Task 2')).toBeTruthy();
    expect(screen.getAllByText('Late')).toHaveLength(1);
  });

  it('opens Last run from the head’s More too, where a narrow page hides the pill', async () => {
    serve((url) => {
      if (url.includes('/api/v2/status'))
        return { status: 200, body: { status: { state: 'idle', last_stop: { tone: 'soft', needs_person: false, reason: 'It ran out of time', text: 'Stopped early' } } } };
      if (url.includes('/api/v2/tasks')) return { status: 200, body: { tasks: [] } };
      if (url.includes('/api/v2/pipelines')) return { status: 200, body: { pipelines: [] } };
      if (url.includes('/api/runs')) return { status: 200, body: [] };
      return undefined;
    });
    show(<TeamWho who="ana" />, '/team/ana');
    expect(await screen.findByRole('button', { name: /Last run/ })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'More' })[0]!);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Last run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Last run' });
    expect(within(dialog).getByText('Is anything needed?')).toBeTruthy();
    expect(within(dialog).getByText('No. Nothing was lost.')).toBeTruthy();
  });
});
