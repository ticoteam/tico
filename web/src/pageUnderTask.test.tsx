import { QueryClient } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Route as Files } from './routes/library.files';
import { Route as Roadmap } from './routes/roadmap';

// the pages under the task modal: each counts its draws
const draws = { roadmap: 0, files: 0 };
vi.mock('@/features/tasks/exports', () => ({ RoadmapPage: () => (draws.roadmap++, null) }));
vi.mock('@/features/library/exports', () => ({ FilesPage: () => (draws.files++, null) }));

function app(at: string) {
  const root = createRootRoute({ component: Outlet });
  Roadmap.update({ id: '/roadmap', path: '/roadmap', getParentRoute: () => root, loader: () => undefined } as never);
  Files.update({ id: '/library/files', path: '/library/files', getParentRoute: () => root, loader: () => undefined } as never);
  const router = createRouter({
    routeTree: root.addChildren([Roadmap, Files] as never),
    history: createMemoryHistory({ initialEntries: [at] }),
    context: { queryClient: new QueryClient() },
  });
  render(<RouterProvider router={router} />);
  return router;
}

/** Opens a task over the page, moves it on (its tab, another task) and closes it; the page's own words stay. */
async function overThePage(router: ReturnType<typeof app>, own: Record<string, unknown>) {
  const go = (search: Record<string, unknown>) => act(() => router.navigate({ to: '.', search }));
  await go({ ...own, task: 12, list: 'eng' });
  await go({ ...own, task: 12, list: 'eng', 'task.tab': 'CHAT' });
  await go({ ...own, task: 13, list: 'eng' });
  await go(own);
}

describe('the page under the task modal', () => {
  it("doesn't re-draw as a task opens, moves or closes over it", async () => {
    for (const [page, at, own] of [
      ['roadmap', '/roadmap?owner=bot%3Aa', { owner: 'bot:a' }],
      ['files', '/library/files?kind=doc', { kind: 'doc' }],
    ] as const) {
      const router = app(at);
      await act(() => router.load());
      const first = draws[page];
      expect(first).toBeGreaterThan(0);
      await overThePage(router, own);
      expect(draws[page]).toBe(first);
      // its own words still draw it
      await act(() => router.navigate({ to: '.', search: { ...own, q: 'frost' } }));
      expect(draws[page]).toBe(first + 1);
    }
  });
});
