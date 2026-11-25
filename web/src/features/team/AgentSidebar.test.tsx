// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import type * as Actions from '@/actions';
import { run } from '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { agentsQuery, groupsQuery, type AgentRow, type Group, type SidebarWidget } from './queries';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
// every action runs as it does in the app; the calls are kept to see which ran
vi.mock('@/actions', async (importOriginal) => {
  const real = await importOriginal<typeof Actions>();
  return { ...real, run: vi.fn(real.run) };
});
const previews = () => vi.mocked(run).mock.calls.filter(([id]) => id === 'files.preview').map(([, input]) => input);

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
    { id: 'p1', item: 'page:f1', position: 0, size: 'medium', page: { file_id: 'f1', title: 'Videos by stage', bot: '0190-ana', updated_at: '2026-11-20T10:00:00Z', archived: false, version: 2 } },
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

type Reply = { status?: number; body: unknown } | Promise<never>;
let reads: string[] = [];

/** Ana's page: her sidebar as `widgets` (none: the server's answer without them), an update, a file and the pins.
    `reply` answers a read first when it knows it (`undefined`: the usual answer). */
function open(widgets?: SidebarWidget[], reply: (url: string) => Reply | undefined = () => undefined) {
  reads = [];
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    reads.push(u);
    const own = reply(u);
    if (own instanceof Promise) return own;
    const body = own
      ? own.body
      : u.endsWith('/bots/ana/task-tabs')
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
    return new Response(JSON.stringify(body), { status: own?.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
  const root = createRootRoute({ component: () => <TeamWho who="ana" /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/team/ana'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** Ana's pins with one card of `template` showing `data`, pinned as card:c9. */
const cardPins = (template: string, data: unknown) => ({
  ...pins,
  pins: [{ id: 'p9', item: 'card:c9', position: 0, size: 'medium', card: { id: 'c9', template, heading: 'Card', settings: {}, hidden: false, data } }],
});
const pinsReply = (body: unknown) => (u: string) => (u.includes('/api/v2/places/bot%3Aana') ? { body } : undefined);
/** A read of what it holds that has come (the page drew from it). */
const settled = (key: unknown[]) => waitFor(() => expect(queryClient.getQueryState(key)?.status).toBe('success'));

const SECTIONS = ['Latest update', 'Dream', 'Tasks', 'Files', 'Videos by stage', 'Waiting on Pat', 'Spend'];
/** The sidebar's sections in the order they are drawn. */
const order = () => screen.getAllByRole('region').map((r) => r.getAttribute('aria-label') ?? '').filter((n) => SECTIONS.includes(n));

function seed() {
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors, byId });
  queryClient.setQueryData(groupsQuery.queryKey, groups);
  queryClient.setQueryData(agentsQuery.queryKey, roster);
}
beforeEach(() => {
  queryClient.clear();
  seed();
  vi.mocked(run).mockClear();
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

  it('draws none of its pins once a read of them is refused, not the last ones it was shown', async () => {
    let refused = false;
    open([w('page:f1'), w('card:c1'), w('tasks'), w('updates')], (u) => (refused && u.includes('/api/v2/places/') ? { status: 403, body: { error: { code: 'forbidden', detail: 'No' } } } : undefined));
    await screen.findByRole('region', { name: 'Waiting on Pat' });
    refused = true;
    await act(() => queryClient.invalidateQueries({ queryKey: ['team', 'pins'] }));
    await waitFor(() => expect(order()).toEqual(['Tasks', 'Latest update']));
  });

  it('keeps its dream with its latest update: hidden with it, and in its height', async () => {
    const dream = { dream: { auto: false, closed_at: null, doing: null, questions: null, reason: null, started_at: '2026-11-20T10:00:00Z', state: 'done', status: 'done', summary: 'Tidied its notes', task: '' } };
    const withDream = (u: string) => (u.includes('/bots/ana/dream') ? { body: dream } : undefined);
    open([w('updates', { height: 200 }), w('tasks'), w('files')], withDream);
    const box = (await screen.findByRole('region', { name: 'Dream' })).closest('.side-sized');
    expect(box).not.toBeNull();
    expect(within(box as HTMLElement).getByRole('region', { name: 'Latest update' })).toBeTruthy();
    cleanup();
    queryClient.removeQueries({ queryKey: ['team', 'tabs'] });
    open([w('updates', { hidden: true }), w('tasks'), w('files')], withDream);
    await screen.findByRole('region', { name: 'Files' });
    await settled(['team', 'dream', 'ana']);
    expect(order()).toEqual(['Tasks', 'Files']);
  });

  it('reads a pinned page by an address that changes with each version', async () => {
    open([w('page:f1'), w('tasks')]);
    await screen.findByRole('region', { name: 'Videos by stage' });
    await waitFor(() => expect(reads).toContain('/api/v2/files/f1?v=2'));
    expect(reads).not.toContain('/api/v2/files/f1');
  });

  it('draws its first three pinned pages live and any after them as their rows', async () => {
    const page = (n: number) => ({ id: `p${n}`, item: `page:f${n}`, position: n, size: 'medium', page: { file_id: `f${n}`, title: `Page ${n}`, bot: '0190-ana', updated_at: '2026-11-20T10:00:00Z', archived: false } });
    open([1, 2, 3, 4].map((n) => w(`page:f${n}`)), pinsReply({ ...pins, pins: [1, 2, 3, 4].map(page) }));
    const fourth = await screen.findByRole('region', { name: 'Page 4' });
    expect(within(fourth).getByRole('link').getAttribute('href')).toBe('/library/files?file=f4');
    await waitFor(() => expect(reads.filter((u) => u.startsWith('/api/v2/files/')).map((u) => u.split('?')[0])).toEqual(['/api/v2/files/f1', '/api/v2/files/f2', '/api/v2/files/f3']));
  });

  it('reads the automation runs card over its last 24 hours, with which runs failed', async () => {
    const runs = { total: 4, failed: 1, running: 0, runs: [{ id: 'r1', automation_id: 'a1', name: 'Nightly sync', status: 'failed', started_at: '2026-11-20T10:00:00Z', finished_at: '2026-11-20T10:01:00Z' }] };
    open([w('card:c9'), w('tasks')], pinsReply(cardPins('automation_runs', runs)));
    const card = await screen.findByRole('region', { name: 'Card' });
    expect(within(card).getByText('Last 24 hours')).toBeTruthy();
    expect(within(card).getByText('Nightly sync')).toBeTruthy();
  });

  it('opens a latest page from its row in Files in a new tab, never its download', async () => {
    open([w('card:c9'), w('tasks')], pinsReply(cardPins('latest_pages', { pages: [{ file_id: 'f7', title: 'Q3 plan', updated_at: '2026-11-20T10:00:00Z' }] })));
    const link = (await screen.findByText('Q3 plan')).closest('a') as HTMLAnchorElement;
    expect([link.getAttribute('href'), link.target, link.rel]).toEqual(['/library/files?file=f7', '_blank', 'noreferrer']);
    expect(fireEvent.click(link)).toBe(true); // a plain click is the browser's: the new tab, not a preview over the page
    expect(previews()).toEqual([]);
  });

  it('opens a pinned page full screen at the version it shows', async () => {
    open([w('page:f1'), w('tasks')]);
    const card = await screen.findByRole('region', { name: 'Videos by stage' });
    fireEvent.click(within(card).getByRole('button', { name: 'Videos by stage' }));
    expect(previews()).toEqual([{ file: 'f1', version: 2 }]);
  });

  it('draws the default sidebar and none of its lists once a read of its layout is refused', async () => {
    let refused = false;
    const launch = { name: 'Launch', show_count: true, conditions: [{ field: 'stage', value: ['general-todo'] }] };
    const layout = { bot: '0190-ana', tabs: [launch], widgets: [w('page:f1'), w('tasks'), w('updates')], saved: true, can_edit: true, personal: false, updated_at: null, updated_by: null };
    open(undefined, (u) => (u.endsWith('/bots/ana/task-tabs') ? (refused ? { status: 403, body: { error: { code: 'forbidden', detail: 'No' } } } : { body: layout }) : undefined));
    await screen.findByRole('region', { name: 'Launch' });
    await screen.findByRole('region', { name: 'Videos by stage' });
    refused = true;
    await act(() => queryClient.invalidateQueries({ queryKey: ['team', 'tabs'] }));
    await waitFor(() => expect(order()).toEqual(['Latest update', 'Tasks', 'Files']));
    expect(screen.queryByRole('region', { name: 'Launch' })).toBeNull();
  });

  it('leaves the age of a row without a time empty', async () => {
    open([w('card:c9'), w('tasks')], pinsReply(cardPins('unread_threads', { count: 1, threads: [{ id: 'th1', title: 'Launch notes', updated_at: null }] })));
    const row = (await screen.findByText('Launch notes')).closest('a') as HTMLElement;
    expect(row.querySelector('.row-age')).toBeNull();
  });

  it('says Nothing yet only once its pins have come, and counts only what it shows', async () => {
    const empty = (u: string) => (u.includes('/api/v2/updates') ? { body: { updates: [] } } : u.includes('/bots/ana/files') ? { body: { files: [], total: 0 } } : undefined);
    open([w('page:f1')], (u) => (u.includes('/api/v2/places/') ? new Promise<never>(() => {}) : empty(u)));
    await settled(['team', 'tabs', 'ana']);
    await waitFor(() => expect(reads.some((u) => u.includes('/api/v2/places/'))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('Nothing yet')).toBeNull();
    cleanup();
    queryClient.clear();
    seed();
    // an update and a file, both hidden, and no pins it may see: nothing to show
    open([w('updates', { hidden: true }), w('tasks', { hidden: true }), w('files', { hidden: true })]);
    expect(await screen.findByText('Nothing yet')).toBeTruthy();
  });

  it('says its page did not load, with Try again, when its pins could not be read and it has nothing else', async () => {
    const empty = (u: string) => (u.includes('/api/v2/updates') ? { body: { updates: [] } } : u.includes('/bots/ana/files') ? { body: { files: [], total: 0 } } : undefined);
    let down = true;
    open([w('page:f1')], (u) => (u.includes('/api/v2/places/') && down ? { status: 500, body: { error: { code: 'internal', detail: 'down' } } } : empty(u)));
    const retry = await screen.findByRole('button', { name: 'Try again' });
    expect(screen.queryByText('Nothing yet')).toBeNull();
    down = false;
    fireEvent.click(retry);
    expect(await screen.findByRole('region', { name: 'Videos by stage' })).toBeTruthy();
  });

  it('draws no empty box for a sized widget with nothing in it', async () => {
    // an update, so the page has something to show; no files
    open([w('files', { height: 120 }), w('tasks'), w('updates')], (u) => (u.includes('/bots/ana/files') ? { body: { files: [], total: 0 } } : undefined));
    await screen.findByRole('region', { name: 'Latest update' });
    await settled(['team', 'files', 'ana']);
    expect(document.querySelector('.side-sized')).toBeNull();
  });
});
