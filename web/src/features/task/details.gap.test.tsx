import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import type { TaskData } from './queries';
import { TaskPage } from './TaskPage';

/* s58, the task window's Details: the facts the old window had, the meetings, and the head's ⋯. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
const sent: { method: string; url: string; body: Record<string, unknown> }[] = [];
function serve(reply: (url: string, method: string, body: Record<string, unknown>) => Reply | undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    if (method !== 'GET') sent.push({ method, url: String(url), body });
    const r = reply(String(url), method, body) ?? base(String(url));
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

/* Made-up rows only. */
let shown: Partial<TaskData> & { followers?: unknown } = {};
const task = (more: Partial<TaskData> & { followers?: unknown } = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    title: 'Fix the sign-in page',
    status: 'open',
    lane: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Product' },
    stage_id: 's2',
    stage: { id: 's2', name: 'Review' },
    owner_id: 'bot:a1',
    requester_id: 'human:me',
    labels: [],
    created_at: '2026-11-01T10:00:00Z',
    due_at: null,
    late: null,
    documents: [],
    to_leave: [],
    links: [],
    runs: [],
    children: [],
    parent: null,
    can_comment: true,
    mover: true,
    ...shown,
    ...more,
  }) as unknown as TaskData;

const detail = (t: TaskData) => ({ task: t, runs: [], children: [], comments: [], events: [], messages: [], has_more: false, can_comment: true, mover: true });

function base(url: string): Reply {
  if (url.includes('/meetings')) return { status: 200, body: { meetings: [], next_cursor: null } };
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { status: 200, body: detail(task()) };
  if (url.includes('/files')) return { status: 200, body: { files: [] } };
  if (url.includes('/api/v2/pipelines'))
    return {
      status: 200,
      body: {
        pipelines: [
          { id: 'p1', name: 'Product', stages: [{ id: 's1', name: 'Build', side: false }, { id: 's2', name: 'Review', side: false }] },
          { id: 'p2', name: 'Ops', stages: [{ id: 'o1', name: 'Triage', side: false }] },
        ],
      },
    };
  if (url.includes('/needs-you')) return { status: 200, body: { items: [] } };
  return { status: 200, body: {} };
}

const actors = [
  { id: 'human:me', kind: 'person', name: 'Ana Diaz', hue: 1 },
  { id: 'human:bo', kind: 'person', name: 'Bo Lee', hue: 2 },
  { id: 'bot:a1', kind: 'agent', name: 'Builder', slug: 'builder', hue: 3 },
];

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent.length = 0;
  shown = {};
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const details = () => show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
const facts = async () => fireEvent.click(await screen.findByRole('button', { name: 'Facts' }));
const more = async () => fireEvent.click((await screen.findAllByRole('button', { name: 'More' }))[0] as HTMLElement);

describe('Details: the facts', () => {
  it('sets the Human and the size in place @s58', async () => {
    serve(() => undefined);
    details();
    await facts();
    fireEvent.click(await screen.findByRole('button', { name: 'Human' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Bo Lee' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1', body: { human: 'human:bo' } }));

    fireEvent.click(screen.getByRole('button', { name: 'Size' }));
    const size = await screen.findByRole('textbox', { name: 'Size' });
    fireEvent.change(size, { target: { value: 'big' } });
    fireEvent.keyDown(size, { key: 'Enter' });
    expect(await screen.findByText('Use a number')).toBeTruthy();
    fireEvent.change(size, { target: { value: '3' } });
    fireEvent.keyDown(size, { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1', body: { size: 3 } }));
  });

  it('follows the task, and puts someone in a role @s58', async () => {
    shown = { followers: [] };
    serve((url, method) => {
      if (method === 'PUT' && url.endsWith('/followers')) return { status: 200, body: { task_id: 't1', following: true, notify: 'live', followers: [{ actor: 'human:me', notify: 'live' }] } };
      return undefined;
    });
    details();
    await facts();
    fireEvent.click(await screen.findByRole('button', { name: 'Follow' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'PUT', url: '/api/v2/tasks/t1/followers', body: { following: true } }));
    expect(await screen.findByRole('button', { name: 'Following' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Add someone' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add someone' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Role' }), { target: { value: 'QA Lead' } });
    expect((within(dialog).getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('lists the meetings the task was in, each opening in the calendar @s58', async () => {
    serve((url) =>
      url.includes('/meetings')
        ? { status: 200, body: { meetings: [{ kind: 'linked', meeting_id: 'm1', started_at: '2026-11-10T10:00:00Z', title: 'Sign-in review', url: '' }], next_cursor: null } }
        : undefined,
    );
    details();
    fireEvent.click(await screen.findByRole('button', { name: /Meetings/ }));
    const row = await screen.findByRole('link', { name: /Sign-in review/ });
    expect(row.getAttribute('href')).toMatch(/^\/calendar\?.*panel=meeting%3Am1/);
  });
});

describe('the head’s ⋯', () => {
  it('asks before deleting, then deletes and goes to the pipeline @s58', async () => {
    serve((url) => (url.endsWith('/delete') ? { status: 200, body: { deleted: true } } : undefined));
    const router = details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Delete “Fix the sign-in page”?' });
    expect(sent.some((s) => s.url.endsWith('/delete'))).toBe(false);
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/delete', body: {} }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1'));
  });

  it('reopens a finished task only with a reason @s58', async () => {
    shown = { status: 'done' } as Partial<TaskData>;
    serve(() => undefined);
    details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reopen' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reopen it?' });
    const go = within(dialog).getByRole('button', { name: 'Reopen' }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Why reopen it?' }), { target: { value: 'The fix broke on Safari' } });
    fireEvent.click(go);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to: 'reopen', why: 'The fix broke on Safari' } }));
  });

  it('moves the task to another pipeline and copies its link @s58', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    serve((url) => (url.endsWith('/move') ? { status: 200, body: { task: task({ pipeline_id: 'p2', stage_id: 'o1' }) } } : undefined));
    const router = details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${location.origin}/task/12`));

    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Ops' }));
    const dialog = await screen.findByRole('dialog', { name: 'Move to Ops' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to_pipeline: 'p2' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p2/12'));
  });
});
