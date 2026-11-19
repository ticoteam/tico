import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { TaskById, TaskChat } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The conversation itself is tested in its own folder: here, only which one the task's chat opens.
vi.mock('@/conversation', () => ({
  Conversation: ({ id, title }: { id: string; title: string }) => <section aria-label={`Chat with ${title}`} data-conversation={id} />,
  MessageBones: () => <div role="status" aria-label="Loading messages" />,
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

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:a1', kind: 'agent', name: 'Birch Builder' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string) => {
    const body = String(url).includes('/api/v2/tasks/') ? { task, runs: [], children: [], comments: [], events: [], messages: [], has_more: false } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('a task’s chat', () => {
  it('names who has the task and opens their conversation on it @s53', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Birch Builder' });
    expect(chat.getAttribute('data-conversation')).toBe('c7');
    // The name goes to the owner's page.
    expect(screen.getByRole('link', { name: 'Birch Builder' }).getAttribute('href')).toBe('/team/a1');
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
