import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import type { TaskData } from './queries';
import { TaskPage } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* An open task's files and pages: add, link, remove, versions, and a page's ⋯. Made-up rows only. */

type Reply = { status: number; body: unknown };
const calls: string[] = [];
const sent: { url: string; method: string; body: Record<string, unknown> }[] = [];
function serve(reply: (url: string, method: string) => Reply | null) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push(`${method} ${String(url)}`);
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    if (method !== 'GET') sent.push({ url: String(url), method, body });
    const r = reply(String(url), method) ?? base(String(url));
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

const task = (): TaskData =>
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
    documents: [{ file_id: 'f2', name: 'QA report', version: 1 }],
    to_leave: [],
    links: [],
    runs: [],
    children: [],
    parent: null,
    can_comment: true,
    mover: true,
  }) as unknown as TaskData;
const detail = { task: task(), runs: [], children: [], comments: [], events: [], messages: [], has_more: false, can_comment: true, mover: true };
const page = { file_id: 'f2', title: 'QA report', src: 'about:blank', archived: false, may_write: true, version: 1 };
const shot = {
  id: 'f9',
  name: 'shot.png',
  title: 'Sign-in screen',
  mime: 'image/png',
  archived: false,
  current_version: 2,
  links: [],
  writers: [],
  versions: [
    { n: 1, by: 'bot:a1', created_at: '2026-11-01T10:00:00Z', mime: 'image/png', url: '/api/v2/files/f9?v=1', answers: [], ask: null },
    { n: 2, by: 'bot:a1', created_at: '2026-11-02T10:00:00Z', mime: 'image/png', url: '/api/v2/files/f9?v=2', answers: [], ask: null },
  ],
};

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { status: 200, body: detail };
  if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [shot] } };
  if (url.endsWith('/api/v2/pages/f2')) return { status: 200, body: { page, can_manage: true, can_delete: true } };
  if (url.endsWith('/api/v2/groups')) return { status: 200, body: [] };
  if (url.includes('/api/v2/pipelines')) return { status: 200, body: { pipelines: [{ id: 'p1', name: 'Product', stages: [{ id: 's2', name: 'Review', side: false }] }] } };
  if (url.includes('/needs-you')) return { status: 200, body: { items: [] } };
  return { status: 200, body: {} };
}

const lastMore = async () => fireEvent.click((await screen.findAllByRole('button', { name: 'More' })).at(-1) as HTMLElement);
const addDialog = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'File or page' }));
  return screen.findByRole('dialog', { name: 'New page' });
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  calls.length = 0;
  sent.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'human:h2', kind: 'person', name: 'Dana' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
});
afterEach(() => vi.unstubAllGlobals());

describe('a task’s files @s56', () => {
  it('uploads a file from Add and opens it @s56', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/tasks/t1/files') ? { status: 200, body: { file_id: 'f9', version: 1, link: null, file: shot } } : null,
    );
    const router = show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    const dialog = await addDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Upload' }));
    await screen.findByRole('dialog', { name: 'Upload a file' });
    const input = screen.getByLabelText('Files to add');
    fireEvent.change(input, { target: { files: [new File(['hi'], 'notes.txt', { type: 'text/plain' })] } });
    await waitFor(() => expect(calls).toContain('POST /api/v2/tasks/t1/files'));
    await waitFor(() => expect((router.state.location.search as { file?: string }).file).toBe('f9'));
  });

  it('links a page from elsewhere to the task @s56', async () => {
    serve((url, method) => {
      if (url.includes('/api/v2/files?kind=page')) return { status: 200, body: { files: [{ id: 'pg9', title: 'Pricing notes', may_write: true }] } };
      if (method === 'POST' && url.endsWith('/api/v2/pages/pg9/links')) return { status: 200, body: { page: { file_id: 'pg9' } } };
      return null;
    });
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    const dialog = await addDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Link a page' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Pricing notes' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/pg9/links', method: 'POST', body: { target: 'task:t1', role: 'related' } }));
  });
});

describe('a task’s file open @s76', () => {
  it('shows an older version, compares two, and removes the file @s76', async () => {
    serve(() => null);
    const router = show(<TaskPage task="12" pipeline="p1" tab="details" file="f9" />, '/tasks/p1/12/details?file=f9');
    expect(await screen.findByRole('button', { name: 'Sign-in screen, full size' })).toBeTruthy();
    await lastMore();
    expect(screen.getByRole('menuitem', { name: 'Download' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Versions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'v1' }));
    expect(await screen.findByAltText('Sign-in screen, v1')).toBeTruthy();

    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Compare' }));
    expect(await screen.findByRole('region', { name: 'v1' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'v2' })).toBeTruthy();

    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove file' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f9', method: 'PATCH', body: { archived: true } }));
    await waitFor(() => expect((router.state.location.search as { file?: string }).file).toBeUndefined());
  });

  it('adding a file from disk is closed to Tico @s76', async () => {
    serve(() => null);
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    const r = await run('task.upload', { task: 't1', file: new File(['hi'], 'a.txt') }, { via: 'tico' });
    expect(r.ok).toBe(false);
    expect(calls.some((c) => c.startsWith('POST'))).toBe(false);
  });
});

describe('a page’s ⋯ @s780', () => {
  it('archives the page and goes back to Details @s780', async () => {
    serve((url, method) => (method === 'PATCH' && url.endsWith('/api/v2/pages/f2') ? { status: 200, body: { page: { ...page, archived: true } } } : null));
    const router = show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/f2', method: 'PATCH', body: { archived: true } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
  });

  it('chooses who can see the page @s780', async () => {
    serve((url, method) => (method === 'PATCH' && url.endsWith('/api/v2/pages/f2') ? { status: 200, body: { page } } : null));
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Who can see it/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Who can see it' });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Everyone who can see the task' }));
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: 'Dana' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/f2', method: 'PATCH', body: { audience: { everyone: false, people: ['h2'], teams: [], bots: [] } } }),
    );
  });

  it('deletes the page for good only after asking @s780', async () => {
    serve(() => null);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete page' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Delete QA report?' });
    expect(calls.some((c) => c.startsWith('DELETE'))).toBe(false);
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls).toContain('DELETE /api/v2/files/f2'));
  });

  it('who can see and delete are closed to Tico @s780', async () => {
    serve(() => null);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await screen.findByTitle('QA report');
    const tico = { via: 'tico' } as const;
    expect((await run('task.deletePage', { task: 't1', page: 'f2' }, tico)).ok).toBe(false);
    expect((await run('task.pageAudience', { task: 't1', page: 'f2', everyone: true, people: [], teams: [], agents: [] }, tico)).ok).toBe(false);
    // Showing a task to the whole team is the person's own press too.
    expect((await run('task.setVisible', { task: 't1', to: 'team' }, tico)).ok).toBe(false);
    expect(sent).toEqual([]);
  });
});
