// Tags (the list, New tag, search) and a tag (its tasks, Edit, More, Filter and New task). Made-up rows only.
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, type AnyRouter } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { Task } from '@/api/schema';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { pipelinesQuery, tagQuery, tagsQuery, type Pipeline, type TaskItem } from './queries';
import { TagPage, TagsPage } from './TagsPages';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string, body: unknown) => Reply | undefined = () => undefined) {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    sent.push({ url: String(url), method, body });
    const r = reply(String(url), method, body) ?? { status: 200, body: {} };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const posted = (part: string) => sent.find((s) => s.method === 'POST' && s.url.includes(part));

let router: AnyRouter;
function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
}

const tag = (id: string, key: string, extra: Record<string, unknown> = {}) => ({
  id,
  key,
  label: key,
  updated_at: '2030-01-01T00:00:00Z',
  created_at: '2030-01-01T00:00:00Z',
  is_template: false,
  kind: '' as const,
  markdown: '',
  metadata: {},
  owner: null,
  template_id: null,
  bot_writers: [],
  version: 1,
  ...extra,
});

const ENG: Pipeline = {
  id: 'eng',
  name: 'Engineering',
  stages: [
    { id: 'build', name: 'Build' },
    { id: 'qa', name: 'QA' },
  ],
  owner: null,
  done: { id: 'done', name: 'Done' },
  closed: { id: 'closed', name: 'Closed' },
  mayChange: true,
  preset: 'engineering',
};
const BUGS: Pipeline = { ...ENG, id: 'bugs', name: 'Bugs', stages: [{ id: 'new', name: 'New' }], preset: 'bugs' };
const task = (id: string, number: number, title: string, extra: Partial<TaskItem> = {}): TaskItem => ({
  id,
  number,
  title,
  status: 'open',
  pipeline_id: 'eng',
  stage_id: 'build',
  owner_id: null,
  labels: ['bug'],
  late: null,
  turn: null,
  updated_at: '2030-01-01T00:00:00Z',
  version: 1,
  ...extra,
});
const seedTag = (t: ReturnType<typeof tag>, tasks: TaskItem[] = [], extra: Record<string, unknown> = {}) =>
  queryClient.setQueryData(tagQuery(t.id).queryKey, { tag: t, editable: true, next_offset: null, tasks: tasks as unknown as Task[], ...extra });

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const ana = { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' };
  queryClient.setQueryData(['directory'], { actors: [ana], byId: { [ana.id]: ana } });
  queryClient.setQueryData(pipelinesQuery.queryKey, [ENG]);
});
afterEach(() => vi.unstubAllGlobals());

describe('Tags @s3', () => {
  it('lists every tag, most recently used first; a tag opens its page', async () => {
    serve();
    queryClient.setQueryData(tagsQuery.queryKey, [tag('t1', 'checkout'), tag('t2', 'shipping', { updated_at: '2030-02-01T00:00:00Z', created_at: '2030-02-01T00:00:00Z' })]);
    show(<TagsPage />, '/tags');
    const table = await screen.findByRole('table', { name: 'Tags' });
    const links = within(table).getAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual(['shipping', 'checkout']);
    expect(links[0]!.getAttribute('href')).toBe('/tags/t2');
  });

  it('reads tags without templates, and says so when there are none', async () => {
    serve(() => ({ status: 200, body: { tags: [tag('tp', 'release', { is_template: true })] } }));
    show(<TagsPage />, '/tags');
    expect(await screen.findByText('No tags yet')).toBeTruthy();
    expect(sent.map((s) => s.url)).toEqual(['/api/v2/tags?is_template=false']);
  });

  it('makes a new tag from the head +, its key made from the name, and opens it @s3', async () => {
    serve((url, method) => (method === 'POST' && url === '/api/v2/tags' ? { status: 200, body: { tag: tag('t7', 'launch-week', { label: 'Launch week' }) } } : undefined));
    queryClient.setQueryData(tagsQuery.queryKey, [tag('t1', 'bug')]);
    show(<TagsPage />, '/tags');
    fireEvent.click(await screen.findByRole('button', { name: 'New tag' }));
    const dialog = await screen.findByRole('dialog', { name: 'New tag' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Launch week' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(posted('/api/v2/tags')?.body).toEqual({ key: 'launch-week', label: 'Launch week', markdown: '', is_template: false }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/tags/t7'));
  });

  it('narrows the list to the words in the address, and says when nothing matches @s3', async () => {
    serve();
    queryClient.setQueryData(tagsQuery.queryKey, [tag('t1', 'shipping'), tag('t2', 'checkout')]);
    show(<TagsPage />, '/tags?q=ship');
    const table = await screen.findByRole('table', { name: 'Tags' });
    expect(within(table).getAllByRole('link').map((a) => a.textContent)).toEqual(['shipping']);
    expect((screen.getByLabelText('Search tags') as HTMLInputElement).value).toBe('ship');
    cleanup();
    show(<TagsPage />, '/tags?q=nothing');
    expect(await screen.findByText('No tags match')).toBeTruthy();
  });
});

describe('A tag @s4', () => {
  it('groups its open tasks by pipeline, each with its stage, and draws Done shut', async () => {
    serve();
    queryClient.setQueryData(pipelinesQuery.queryKey, [ENG, BUGS]);
    seedTag(tag('t1', 'bug'), [
      task('a', 7, 'Checkout fails on one browser', { stage_id: 'qa' }),
      task('b', 8, 'Map tiles fail to load', { pipeline_id: 'bugs', stage_id: 'new' }),
      task('c', 3, 'Old label typo', { stage_id: 'done', status: 'done' }),
    ]);
    show(<TagPage tag="t1" />, '/tags/t1');
    expect(await screen.findByRole('heading', { level: 1, name: 'bug' })).toBeTruthy();
    const eng = screen.getByRole('region', { name: 'Engineering' });
    const row = within(eng).getByTitle('Checkout fails on one browser');
    expect(row.getAttribute('href')).toBe('/tasks/eng/7');
    expect(within(row).getByText('QA')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Bugs' })).getByTitle('Map tiles fail to load')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Done' })).queryByTitle('Old label typo')).toBeNull();
  });

  it('a tag on nothing says so', async () => {
    serve();
    seedTag(tag('t9', 'wholesale'));
    show(<TagPage tag="t9" />, '/tags/t9');
    expect(await screen.findByText('Nothing tagged wholesale')).toBeTruthy();
  });

  it('edits its name and notes from "⋯" Edit @s4', async () => {
    serve((url, method) => (method === 'POST' && url === '/api/v2/tags/t1' ? { status: 200, body: { tag: tag('t1', 'bug', { label: 'Bugs', version: 2 }) } } : undefined));
    seedTag(tag('t1', 'bug'), [task('a', 7, 'Fix the cart')]);
    show(<TagPage tag="t1" />, '/tags/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit tag' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Bugs' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Bugs' })).toBeTruthy();
    await waitFor(() => expect(posted('/api/v2/tags/t1')?.body).toEqual({ version: 1, label: 'Bugs', markdown: '', metadata: {} }));
  });

  it('reads the tasks past the first 500 on More @s4', async () => {
    serve((url) =>
      url === '/api/v2/tags/t1?offset=500'
        ? { status: 200, body: { tag: tag('t1', 'bug'), editable: true, next_offset: null, tasks: [task('z', 900, 'The one after five hundred')] } }
        : undefined,
    );
    seedTag(tag('t1', 'bug'), [task('a', 7, 'Fix the cart')], { next_offset: 500 });
    show(<TagPage tag="t1" />, '/tags/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'More tasks' }));
    expect(await screen.findByTitle('The one after five hundred')).toBeTruthy();
    expect(screen.getByTitle('Fix the cart')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More tasks' })).toBeNull();
  });

  it('files a new task with the tag on it from the head + @s4', async () => {
    serve((url, method) => (method === 'POST' && url === '/api/v2/tasks' ? { status: 200, body: { task: task('n', 12, 'Check the refund') } } : undefined));
    seedTag(tag('t1', 'bug'), [task('a', 7, 'Fix the cart')]);
    show(<TagPage tag="t1" />, '/tags/t1');
    fireEvent.click(await screen.findByRole('button', { name: 'New task' }));
    const dialog = await screen.findByRole('dialog', { name: 'New task' });
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Check the refund' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await screen.findByTitle('Check the refund')).toBeTruthy();
    await waitFor(() => expect(posted('/api/v2/tasks')?.body).toEqual({ title: 'Check the refund', pipeline: 'eng', labels: ['bug'] }));
  });

  it('narrows its tasks to an owner from the address, with a chip that clears it @s4', async () => {
    serve();
    seedTag(tag('t1', 'bug'), [task('a', 7, 'Fix the cart', { owner_id: 'human:ana' }), task('b', 8, 'Fix the map')]);
    show(<TagPage tag="t1" />, '/tags/t1?owner=human:ana');
    expect(await screen.findByTitle('Fix the cart')).toBeTruthy();
    expect(screen.queryByTitle('Fix the map')).toBeNull();
    expect(screen.getByRole('button', { name: 'Filter' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear Ana Ruiz Owner' }));
    expect(await screen.findByTitle('Fix the map')).toBeTruthy();
  });
});
