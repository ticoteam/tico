import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the test hands the chat a stream event, as the shell's startLive does
import { dispatch, installPatchers } from '@/live/patch';
import { TaskById, TaskChat } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The conversation itself is tested in its own folder: here, only which one the task's chat opens.
vi.mock('@/conversation', async () => ({
  Conversation: ({ id, title, foot }: { id: string; title: string; foot?: ReactNode }) => (
    <section aria-label={`Chat with ${title}`} data-conversation={id}>
      {foot}
    </section>
  ),
  MessageBones: () => <div role="status" aria-label="Loading messages" />,
  SessionLine: (await import('@/conversation/Session')).SessionLine,
}));

/* Made-up rows only. */
const task = {
  id: 't1',
  number: 12,
  title: 'Fix the sign-in page',
  status: 'open',
  pipeline_id: 'p1',
  pipeline: { id: 'p1', name: 'Product' },
  stage_id: 's2',
  stage: { id: 's2', name: 'Review' },
  owner_id: 'bot:a1',
  conversation_id: 'c7',
  status_line: 'up_next',
  version: 1,
  labels: [],
  documents: [],
  to_leave: [],
  links: [],
};

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

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  installPatchers();
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:a1', kind: 'agent', name: 'Birch Builder' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  serveRoom({ id: 'c7', task_id: 't1', title: task.title });
});
afterEach(() => vi.unstubAllGlobals());

/** The task's own room (`task_id` is the task), or its owner's main chat (none). */
function serveRoom(room: Record<string, unknown>) {
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    const body = u.includes('/api/v2/tasks/')
      ? { task, runs: [], children: [], comments: [], events: [], messages: [], has_more: false }
      : u.includes('/api/v2/threads/c7')
        ? room
        : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('a task’s chat', () => {
  it('names who has the task and opens their conversation on it @s53', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(chat.getAttribute('data-conversation')).toBe('c7');
    // The name goes to the owner's page.
    expect(screen.getByRole('link', { name: 'Birch Builder' }).getAttribute('href')).toBe('/team/a1');
  });

  it('says under the name that this is the task’s own chat, or its owner’s main chat', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    expect((await screen.findByRole('link', { name: 'Fix the sign-in page' })).getAttribute('href')).toBe('/task/t1');
    expect(screen.queryByText('Main chat')).toBeNull();
  });

  it('says its one status line over the message box, and the next one when the stream sends the task again', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(await within(chat).findByText('Up next')).toBeTruthy();
    act(() => dispatch('tasks', [{ seq: 2, id: 't1', kind: 'run', kinds: ['run'], task: { ...task, version: 2, status_line: 'working' } }]));
    expect(await within(chat).findByText('Working')).toBeTruthy();
  });

  it('says "Main chat" when the task was filed in its owner’s main chat', async () => {
    serveRoom({ id: 'c7', task_id: null, title: 'Chat with Birch Builder' });
    show(<TaskChat task="12" />, '/tasks/p1/12');
    await screen.findByText('Main chat');
    expect(screen.queryByRole('link', { name: 'Fix the sign-in page' })).toBeNull();
  });
});

describe('a task by its number alone', () => {
  it('shows message bones while the task is read', () => {
    vi.stubGlobal('fetch', () => new Promise(() => undefined));
    show(<TaskChat task="12" />, '/tasks/p1/12');
    return screen.findByRole('status', { name: 'Loading messages' });
  });

  it('takes the address over to the task under its pipeline @s1', async () => {
    const router = show(<TaskById task="12" />, '/task/12');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12'));
  });
});
