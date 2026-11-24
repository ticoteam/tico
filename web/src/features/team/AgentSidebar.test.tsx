// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentsQuery, groupsQuery, type AgentRow, type Group, type SidebarWidget } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const actors = [ana, pat];
const byId = Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.slug ?? a.id.slice(6), a]]));
const groups: Group[] = [{ id: 'g1', name: 'Design', parent: '', lead: 'human:p1', bots: ['0190-ana'], people: ['p1'] }];
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3 } as AgentRow];
const w = (id: string, more: Partial<SidebarWidget> = {}): SidebarWidget => ({ id, hidden: false, height: null, ...more });

/** What Pat may see of Ana's pins: a page, a Needs you card with what waits on Pat, a card that shows Pat nothing. */
const pins = {
  place: 'bot:0190-ana',
  kind: 'bot',
  saved: true,
  can_edit: true,
  pins: [
    { id: 'p1', item: 'page:f1', position: 0, size: 'medium', page: { file_id: 'f1', title: 'Videos by stage', bot: '0190-ana', updated_at: '2026-11-20T10:00:00Z', archived: false } },
    {
      id: 'p2',
      item: 'card:c1',
      position: 1,
      size: 'medium',
      card: { id: 'c1', template: 'needs_you', heading: 'Waiting on Pat', settings: {}, hidden: false, data: { count: 1, items: [{ id: 'q1', kind: 'ask', title: 'Approve the storyboard', task_id: 't9', act: 'ok' }] } },
    },
    { id: 'p3', item: 'card:c2', position: 2, size: 'medium', card: { id: 'c2', template: 'spend', heading: 'Spend', settings: {}, hidden: true, data: null } },
  ],
};

/** Ana's page: her sidebar as `widgets` (none: the server's answer without them), an update, a file and the pins. */
function open(widgets?: SidebarWidget[]) {
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    const body = u.endsWith('/bots/ana/task-tabs')
      ? { bot: '0190-ana', tabs: [], saved: !!widgets, can_edit: true, personal: false, updated_at: null, updated_by: null, ...(widgets && { widgets }) }
      : u.includes('/api/v2/places/bot%3Aana')
        ? pins
        : u.includes('/api/v2/updates')
          ? { updates: [{ id: 'u1', body: '- Cut the intro', created_at: '2026-11-20T10:00:00Z' }] }
          : u.includes('/bots/ana/files')
            ? { files: [{ id: 'f2', name: 'cut.mp4', last_activity_at: '2026-11-20T10:00:00Z' }], total: 1 }
            : u.includes('/api/v2/groups')
              ? groups
              : u.includes('/api/employees')
                ? roster
                : u.includes('/api/runs')
                  ? []
                  : u.includes('/api/v2/tasks')
                    ? { tasks: [] }
                    : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  const root = createRootRoute({ component: () => <TeamWho who="ana" /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/team/ana'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const SECTIONS = ['Latest update', 'Tasks', 'Files', 'Videos by stage', 'Waiting on Pat', 'Spend'];
/** The sidebar's sections in the order they are drawn. */
const order = () => screen.getAllByRole('region').map((r) => r.getAttribute('aria-label') ?? '').filter((n) => SECTIONS.includes(n));

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
});
afterEach(() => vi.unstubAllGlobals());

describe("An agent's own sidebar", () => {
  it.each([
    ['leaves its widgets out', undefined],
    ['answers its default', [w('updates'), w('tasks'), w('files')]],
  ])('draws an agent that arranged nothing as the page always has, when the server %s', async (_, widgets) => {
    open(widgets);
    await screen.findByRole('region', { name: 'Files' });
    expect(order()).toEqual(['Latest update', 'Tasks', 'Files']);
  });

  it('draws its widgets in its stored order, leaving out the hidden ones, a set height its most', async () => {
    open([w('page:f1'), w('files', { height: 120 }), w('card:c1'), w('tasks'), w('updates', { hidden: true })]);
    await screen.findByRole('region', { name: 'Waiting on Pat' });
    expect(order()).toEqual(['Videos by stage', 'Files', 'Waiting on Pat', 'Tasks']);
    expect((screen.getByRole('region', { name: 'Files' }).parentElement as HTMLElement).style.maxHeight).toBe('120px');
    expect(screen.queryByText('Cut the intro')).toBeNull();
  });

  it('draws a card from what it shows this reader, and nothing for one that shows them nothing', async () => {
    open([w('card:c1'), w('card:c2'), w('tasks'), w('updates'), w('files')]);
    const card = await screen.findByRole('region', { name: 'Waiting on Pat' });
    expect(within(card).getByText('Approve the storyboard').closest('a')?.getAttribute('href')).toBe('/task/t9');
    await waitFor(() => expect(order()).toEqual(['Waiting on Pat', 'Tasks', 'Latest update', 'Files']));
    expect(screen.queryByRole('region', { name: 'Spend' })).toBeNull();
  });
});
