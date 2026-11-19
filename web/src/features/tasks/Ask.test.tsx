import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { keys, queryClient } from '@/data';
import { boardAgents } from './Ask';
import { listParams, pipelinesQuery, type Pipeline } from './queries';
import { TasksPage } from './TasksPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const PIPE: Pipeline = {
  id: 'eng',
  name: 'Engineering',
  stages: [
    { id: 's1', name: 'Build', owner: 'bot:builder' },
    { id: 's2', name: 'Review', owner: 'bot:mgr' },
    { id: 's3', name: 'Fixes', owner: 'bot:builder' },
  ],
  owner: 'bot:mgr',
  manager: 'bot:mgr',
  done: null,
  closed: null,
  mayChange: true,
};
const actors = [
  { id: 'bot:mgr', kind: 'agent', name: 'Maple Manager' },
  { id: 'bot:builder', kind: 'agent', name: 'Birch Builder' },
];

function show(p: Pipeline) {
  queryClient.setQueryData(pipelinesQuery.queryKey, [p]);
  queryClient.setQueryData(keys.tasks.list(listParams('eng', {})), { tasks: [], cursor: 'c1' });
  const root = createRootRoute({ component: () => <TasksPage pipeline="eng" search={{}} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks/eng'] }) });
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
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  queryClient.setQueryData(['tags'], []);
  queryClient.setQueryData(['flow'], { count: 0, items: [] });
  document.documentElement.dataset.layout = 'cards';
});
afterEach(() => {
  delete document.documentElement.dataset.layout;
  vi.unstubAllGlobals();
});

describe('Pipeline ask chat, on a phone @s146', () => {
  it("lists the pipeline's agents, manager first, each with the stages they hold", () => {
    expect(boardAgents(PIPE)).toEqual([
      { actor: 'bot:mgr', role: 'Manager' },
      { actor: 'bot:builder', role: 'Holds Build, Fixes' },
    ]);
  });

  it("keeps the head's face on a phone; picking an agent opens their chat over the board", async () => {
    const router = show(PIPE);
    const face = await screen.findByRole('button', { name: 'Chat with…' });
    expect(face.closest('[data-phone]')).toBeTruthy();
    fireEvent.click(face);
    fireEvent.click(await screen.findByRole('menuitem', { name: /Birch Builder/ }));
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>).chat).toBe('bot:builder'));
  });

  it('opens the one agent at once when only the manager is on the board', async () => {
    show({ ...PIPE, stages: PIPE.stages.map((s) => ({ ...s, owner: null })) });
    const link = await screen.findByRole('link', { name: 'Chat with Maple Manager' });
    expect(link.closest('[data-phone]')).toBeTruthy();
    expect(decodeURIComponent(link.getAttribute('href') ?? '')).toContain('chat=bot:mgr');
  });
});
