import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { Task } from '@/api/schema';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { pipelinesQuery, tagQuery, tagsQuery, type Pipeline, type Requester, type TaskItem } from './queries';
import { RequestersDialog } from './Requesters';
import { TagPage, TagsPage } from './TagsPages';

/* Tier 2 check of the Tasks track: Tags, a tag, and the requester editor. Made-up rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let posts: { url: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') posts.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method);
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

const tag = (id: string, key: string, updated_at: string) => ({
  id,
  key,
  label: key,
  updated_at,
  created_at: updated_at,
  is_template: false,
  kind: '' as const,
  markdown: '',
  metadata: {},
  owner: null,
  template_id: null,
  bot_writers: [],
  version: 1,
});

const ENG: Pipeline = {
  id: 'eng',
  name: 'Engineering',
  stages: [{ id: 'build', name: 'Build' }, { id: 'qa', name: 'QA' }],
  owner: null,
  done: { id: 'done', name: 'Done' },
  closed: { id: 'closed', name: 'Closed' },
  mayChange: true,
  preset: 'engineering',
};
const BUGS: Pipeline = { ...ENG, id: 'bugs', name: 'Bugs', stages: [{ id: 'new', name: 'New' }], preset: 'bugs' };

const task = (id: string, number: number, title: string, pipeline: string, stage: string, extra: Partial<TaskItem> = {}): TaskItem => ({
  id,
  number,
  title,
  status: 'open',
  pipeline_id: pipeline,
  stage_id: stage,
  owner_id: null,
  labels: ['bug'],
  late: null,
  turn: null,
  updated_at: '2030-01-01T00:00:00Z',
  version: 1,
  ...extra,
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  posts = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('Tags @s3', () => {
  it('lists every tag, most recently used first; a tag opens its page', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(tagsQuery.queryKey, [tag('t1', 'checkout', '2030-01-01T00:00:00Z'), tag('t2', 'shipping', '2030-02-01T00:00:00Z')]);
    show(<TagsPage />, '/tags');
    const table = await screen.findByRole('table', { name: 'Tags' });
    const links = within(table).getAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual(['shipping', 'checkout']);
    expect(links[0]!.getAttribute('href')).toBe('/tags/t2');
  });

  it('reads tags without templates, and says so when there are none', async () => {
    const asked: string[] = [];
    serve((url) => {
      asked.push(url);
      return { status: 200, body: { tags: [{ ...tag('tp', 'release', '2030-01-01T00:00:00Z'), is_template: true }] } };
    });
    show(<TagsPage />, '/tags');
    expect(await screen.findByText('No tags yet')).toBeTruthy();
    expect(asked).toEqual(['/api/v2/tags?is_template=false']);
  });
});

describe('A tag @s4', () => {
  it('groups its open tasks by pipeline, each with its stage, and draws Done shut', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [ENG, BUGS]);
    queryClient.setQueryData(tagQuery('t1').queryKey, {
      tag: tag('t1', 'bug', '2030-01-01T00:00:00Z'),
      editable: true,
      next_offset: null,
      tasks: [
        task('a', 7, 'Checkout fails on one browser', 'eng', 'qa'),
        task('b', 8, 'Map tiles fail to load', 'bugs', 'new'),
        task('c', 3, 'Old label typo', 'eng', 'done', { status: 'done' }),
      ] as unknown as Task[],
    });
    show(<TagPage tag="t1" />, '/tags/t1');
    expect(await screen.findByRole('heading', { level: 1, name: 'bug' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Tags' }).getAttribute('href')).toBe('/tags');
    const eng = screen.getByRole('region', { name: 'Engineering' });
    const row = within(eng).getByTitle('Checkout fails on one browser');
    expect(row.getAttribute('href')).toBe('/tasks/eng/7');
    expect(within(row).getByText('QA')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Bugs' })).getByTitle('Map tiles fail to load')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Done' })).queryByTitle('Old label typo')).toBeNull();
  });

  it('a tag on nothing says so', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [ENG]);
    queryClient.setQueryData(tagQuery('t9').queryKey, { tag: tag('t9', 'wholesale', '2030-01-01T00:00:00Z'), editable: true, next_offset: null, tasks: [] });
    show(<TagPage tag="t9" />, '/tags/t9');
    expect(await screen.findByText('Nothing tagged wholesale')).toBeTruthy();
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

describe('Requester editor @s36', () => {
  it("changes an outside requester's details in place; who they are is fixed once added", async () => {
    serve((_url, method) => (method === 'POST' ? { status: 200, body: {} } : { status: 200, body: { requesters: [person('r1', 'Ada Example')], next_offset: null, my_requester_id: null } }));
    seedRequesters([person('r1', 'Ada Example', { email: 'ada@example.com' })]);
    show(<RequestersDialog task="t1" title="Bulk orders" onClose={() => {}} />, '/roadmap?requesters=t1');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Ada Example' }));
    expect(screen.getByRole('dialog', { name: 'Edit requester' })).toBeTruthy();
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('ada@example.com');
    fireEvent.change(screen.getByLabelText('Organization'), { target: { value: 'Example Co' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Important' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    // back on the list at once, the change shown before the server answers
    expect(await screen.findByText('Important, Example Co')).toBeTruthy();
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ url: '/api/v2/tasks/t1/requesters/r1', body: { name: 'Ada Example', email: 'ada@example.com', organization: 'Example Co', important: true, source: 'manual' } });
  });

  it('a teammate requester has no contact fields or source to fill', async () => {
    serve(() => ({ status: 200, body: {} }));
    const ana = { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' };
    queryClient.setQueryData(['directory'], { actors: [ana], byId: { [ana.id]: ana } });
    seedRequesters([person('r2', '', { kind: 'teammate', actor: 'human:ana' })]);
    show(<RequestersDialog task="t1" title="Bulk orders" onClose={() => {}} />, '/roadmap?requesters=t1');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Ana Ruiz' }));
    expect(screen.queryByLabelText('Email')).toBeNull();
    expect(screen.queryByLabelText('Source')).toBeNull();
    expect(screen.getByLabelText('Organization')).toBeTruthy();
  });
});
