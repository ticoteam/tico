import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { ruleLinksQuery, type RuleLink } from './queries';
import { RuleLinks } from './RuleLinks';

/* Rules › Links: each link's mode and its 30-day counts; a Not covered path shows no counts. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
});

it('shows each link with its mode, and counts only where it is measured', async () => {
  const links: RuleLink[] = [
    { id: 'email', name: 'Email', mode: 'enforced', counts: { passed: 4, refused: 0, held: 1 } },
    { id: 'tool:close', name: 'Close', mode: 'informed', counts: { passed: 2, refused: 3, held: 0 } },
    { id: 'computer', name: 'Calls straight from computers', mode: 'not_covered', counts: null },
  ];
  queryClient.setQueryData(ruleLinksQuery.queryKey, links);
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: '/company/rules/links', component: RuleLinks });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ['/company/rules/links'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  const row = async (name: string) => within((await screen.findByText(name)).closest('tr') as HTMLElement).getAllByRole('cell').map((c) => c.textContent);
  expect(await row('Email')).toEqual(['Email', 'Enforced', '5', '0', '1']);
  expect(await row('Close')).toEqual(['Close', 'Informed only', '5', '3', '0']);
  expect(await row('Calls straight from computers')).toEqual(['Calls straight from computers', 'Not covered', '', '', '']);
});
