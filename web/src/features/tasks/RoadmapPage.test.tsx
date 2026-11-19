import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { listParams, pipelinesQuery, shelfParams, type Pipeline, type Requester, type TaskItem } from './queries';
import { RequestersDialog } from './Requesters';
import { RoadmapPage } from './RoadmapPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let posts: { url: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST') posts.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const ME = 'human:me';
const ENG: Pipeline = { id: 'eng', name: 'Engineering', stages: [{ id: 'e1', name: 'Build' }], owner: null, done: null, closed: null, mayChange: true, preset: 'engineering' };
const FEAT: Pipeline = {
  id: 'feat',
  name: 'Features',
  stages: ['Pitch', 'Spec', 'OK spec', 'Build', 'QA', 'Release'].map((n) => ({ id: n.toLowerCase().replace(' ', '-'), name: n })),
  owner: null,
  manager: null,
  done: { id: 'done', name: 'Done' },
  closed: { id: 'closed', name: 'Closed' },
  mayChange: true,
  preset: 'features',
};
const task = (id: string, number: number, title: string, stage: string, extra: Partial<TaskItem> = {}): TaskItem => ({
  id,
  number,
  title,
  status: 'open',
  pipeline_id: 'feat',
  stage_id: stage,
  owner_id: null,
  labels: [],
  late: null,
  turn: null,
  updated_at: '2030-01-01T00:00:00Z',
  version: 1,
  visibility: 'public',
  ...extra,
});
const seed = (open: TaskItem[], done: TaskItem[] = []) => {
  queryClient.setQueryData(pipelinesQuery.queryKey, [ENG, FEAT]);
  queryClient.setQueryData(keys.tasks.list(listParams('feat', {})), { tasks: open, cursor: 'c1' });
  queryClient.setQueryData(keys.tasks.list(shelfParams('feat', 'done', {})), { tasks: done, cursor: 'c1' });
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  posts = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['tags'], []);
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layout;
});

describe('Roadmap @s2', () => {
  it('can load the next page of shipped features', async () => {
    serve((url) =>
      url.includes('after=next')
        ? { status: 200, body: { tasks: [task('t2', 2, 'Export receipts', 'done', { status: 'done' })], cursor: 'c2' } }
        : { status: 200, body: {} },
    );
    seed([task('t3', 3, 'Review invoices', 'build')]);
    queryClient.setQueryData(keys.tasks.list(shelfParams('feat', 'done', {})), {
      tasks: [task('t1', 1, 'Import receipts', 'done', { status: 'done' })],
      cursor: 'c1',
      next_after: 'next',
    });
    show(<RoadmapPage search={{}} />, '/roadmap');
    const shipped = await screen.findByRole('region', { name: 'Shipped' });
    fireEvent.click(within(shipped).getByRole('button', { name: 'Shipped' }));
    fireEvent.click(within(shipped).getByRole('button', { name: 'More tasks' }));
    expect(await within(shipped).findByRole('link', { name: /Export receipts/ })).toBeTruthy();
  });

  it('shows the Features pipeline as Now, Next and Later, with Shipped drawn shut', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed(
      [
        task('a', 7, 'Share a list by link', 'qa', { late: { level: 'told', since: '2030-01-01T00:00:00Z' } }),
        task('b', 8, 'Bulk orders', 'spec', { turn: { state: 'person', who: ME } as TaskItem['turn'] }),
        task('c', 9, 'Seed box', 'pitch', { visibility: 'private' }),
      ],
      [task('d', 3, 'Gift cards', 'done', { status: 'done' })],
    );
    show(<RoadmapPage search={{}} />, '/roadmap');
    const now = await screen.findByRole('region', { name: 'Now' });
    const row = within(now).getByTitle('Share a list by link');
    expect(row.getAttribute('href')).toBe('/tasks/feat/7');
    expect(within(row).getByText('Late')).toBeTruthy();
    expect(within(row).getByRole('img', { name: 'QA' })).toBeTruthy();
    expect(within(row).queryByTitle('Not public')).toBeNull();
    const next = screen.getByRole('region', { name: 'Next' });
    expect(within(next).getByTitle('Bulk orders').querySelector('.row-mark .flow, .row-mark [class*="flow"]')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Later' })).getByTitle('Not public')).toBeTruthy();
    // Shipped is drawn shut: its count shows, its rows do not
    const shipped = screen.getByRole('region', { name: 'Shipped' });
    expect(within(shipped).queryByTitle('Gift cards')).toBeNull();
    expect(within(shipped).getByText('1')).toBeTruthy();
  });

  it('a company without a Features pipeline says so, with a way to Tasks', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [ENG]);
    show(<RoadmapPage search={{}} />, '/roadmap');
    expect(await screen.findByText('No Features pipeline')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Tasks' }).getAttribute('href')).toBe('/tasks');
  });

  it('with no features yet, the empty state offers New feature', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([]);
    show(<RoadmapPage search={{}} />, '/roadmap');
    expect(await screen.findByText('No features yet')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'New feature' })).toBeTruthy();
  });
});

describe('Roadmap head and Shipped, computer and phone', () => {
  const OWNED: Pipeline = { ...FEAT, owner: 'bot:f00d-1', manager: 'bot:f00d-1' };
  // the page's "⋯"; each row has its own
  const pageMore = () => document.querySelector('[data-target="roadmap:more"]') as HTMLElement;
  const seedOwned = () => {
    seed([task('a', 7, 'Share a list by link', 'qa')], [task('d', 3, 'Gift cards', 'done', { status: 'done' })]);
    queryClient.setQueryData(pipelinesQuery.queryKey, [ENG, OWNED]);
    const fern = { id: 'bot:f00d-1', kind: 'agent', name: 'Fern Planner' };
    queryClient.setQueryData(['directory'], { actors: [fern], byId: { [fern.id]: fern } });
  };

  it("the owner's face opens an agent's chat as an agent, not a person", async () => {
    serve(() => ({ status: 200, body: {} }));
    seedOwned();
    show(<RoadmapPage search={{}} />, '/roadmap');
    const face = await screen.findByRole('link', { name: 'Chat with Fern Planner' });
    const chat = new URLSearchParams(face.getAttribute('href')?.split('?')[1]).get('chat');
    expect(chat).toBe('agent:f00d-1');
  });

  it('a computer draws Shipped once, as its section; the phone line is not there', async () => {
    serve(() => ({ status: 200, body: {} }));
    seedOwned();
    show(<RoadmapPage search={{}} />, '/roadmap');
    expect(await screen.findByRole('region', { name: 'Shipped' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '1 feature' })).toBeNull();
    // Filter is in the head, not the "⋯"
    expect(screen.getByRole('button', { name: 'Filter' })).toBeTruthy();
    fireEvent.click(pageMore());
    expect(await screen.findByRole('menuitem', { name: 'Open Features' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Filter' })).toBeNull();
  });

  it('a phone folds Shipped to one line, keeps New feature (not the face), and moves Filter into the "⋯"', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => ({ status: 200, body: {} }));
    seedOwned();
    show(<RoadmapPage search={{}} />, '/roadmap');
    const line = await screen.findByRole('link', { name: '1 feature' });
    expect(line.getAttribute('href')).toBe('/tasks/feat?view=archived');
    expect(screen.queryByRole('region', { name: 'Shipped' })).toBeNull();
    expect(screen.getByRole('button', { name: 'New feature' }).hasAttribute('data-phone')).toBe(true);
    expect(screen.getByRole('link', { name: 'Chat with Fern Planner' }).closest('[data-phone]')).toBeNull();
    fireEvent.click(pageMore());
    expect(await screen.findByRole('menuitem', { name: 'Filter' })).toBeTruthy();
  });

  it("a long press on a row offers the row's menu, Requesters first", async () => {
    document.documentElement.dataset.layout = 'cards';
    serve(() => ({ status: 200, body: {} }));
    seedOwned();
    const router = show(<RoadmapPage search={{}} />, '/roadmap');
    fireEvent.contextMenu(await screen.findByTitle('Share a list by link'));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Requesters' }));
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>).requesters).toBe('a'));
  });
});

const person = (id: string, name: string, extra: Partial<Requester> = {}): Requester => ({
  id,
  kind: 'external',
  actor: null,
  name,
  email: '',
  phone: '',
  other: '',
  organization: '',
  note: '',
  source: 'manual',
  important: 0,
  created_at: '2030-01-01T00:00:00Z',
  ...extra,
});
const seedRequesters = (rows: Requester[]) =>
  queryClient.setQueryData(['requesters', 't1'], { pages: [{ requesters: rows, next_offset: null, my_requester_id: null }], pageParams: [0] });

describe('Requesters @s36', () => {
  it('reads every page: the next one comes on More requesters, after the first', async () => {
    const asked: string[] = [];
    serve((url) => {
      asked.push(url);
      return url.includes('offset=100')
        ? { status: 200, body: { requesters: [person('r2', 'Sam Sample')], next_offset: null, my_requester_id: null } }
        : { status: 200, body: { requesters: [person('r1', 'Ada Example')], next_offset: 100, my_requester_id: null } };
    });
    show(<RequestersDialog task="t1" title="Bulk orders" onClose={() => {}} />, '/roadmap?requesters=t1');
    expect(await screen.findByTitle('Ada Example')).toBeTruthy();
    expect(screen.queryByTitle('Sam Sample')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More requesters' }));
    expect(await screen.findByTitle('Sam Sample')).toBeTruthy();
    expect(screen.getByTitle('Ada Example')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More requesters' })).toBeNull();
    expect(asked).toEqual(['/api/v2/tasks/t1/requesters?limit=100', '/api/v2/tasks/t1/requesters?limit=100&offset=100']);
  });

  it('adds someone outside the team; the answer\'s removal token is never kept or shown', async () => {
    const rows = [person('r1', 'Ada Example', { important: 1, organization: 'Example Co' })];
    serve((url, method) => {
      if (method === 'POST') return { status: 200, body: { id: 'r2', requester_token: 'tok-made-up' } };
      if (url.includes('/requesters')) return { status: 200, body: { requesters: [...rows, person('r2', 'Sam Sample')], next_offset: null, my_requester_id: null } };
      return { status: 200, body: {} };
    });
    seedRequesters(rows);
    show(<RequestersDialog task="t1" title="Bulk orders" onClose={() => {}} />, '/roadmap?requesters=t1');
    expect(await screen.findByText('Important, Example Co')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add requester' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Sam Sample' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByTitle('Sam Sample')).toBeTruthy();
    expect(posts[0]).toMatchObject({ url: '/api/v2/tasks/t1/requesters', body: { name: 'Sam Sample', important: false } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('tok-made-up');
    expect(screen.queryByText('tok-made-up')).toBeNull();
  });

  it('a removal shows at once and comes back with the reason when the server refuses it', async () => {
    let refuse!: () => void;
    serve((_url, method) => {
      if (method === 'POST') return new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { error: { code: 'forbidden', detail: 'Only the owner can do that' } } })));
      return { status: 200, body: { requesters: [person('r1', 'Ada Example')], next_offset: null, my_requester_id: null } };
    });
    seedRequesters([person('r1', 'Ada Example')]);
    show(<RequestersDialog task="t1" title="Bulk orders" onClose={() => {}} />, '/roadmap?requesters=t1');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Ada Example' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.getByText('No requesters yet')).toBeTruthy());
    refuse();
    expect(await screen.findByText('Only the owner can do that')).toBeTruthy();
    expect(await screen.findByTitle('Ada Example')).toBeTruthy();
  });
});
