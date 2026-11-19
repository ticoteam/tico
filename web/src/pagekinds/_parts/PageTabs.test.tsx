import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PageTab } from '@/pagekinds/types';
import { PageTabs } from './PageTabs';

// One path, told apart by search (the style guide's row): the tab with no search of its own is Color.
const TABS: readonly PageTab[] = [
  { label: 'Color', to: '/guide' },
  { label: 'Type', to: '/guide', search: { at: 'type' } },
  { label: 'Space', to: '/guide', search: { at: 'space' } },
];

async function rowAt(at: string) {
  const root = createRootRoute({ component: () => <PageTabs tabs={TABS} label="Guide pages" /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(<RouterProvider router={router} />);
  return within(await screen.findByRole('navigation', { name: 'Guide pages' }));
}

const current = (row: Awaited<ReturnType<typeof rowAt>>) =>
  row
    .getAllByRole('link')
    .filter((a) => a.getAttribute('aria-current') === 'page')
    .map((a) => a.textContent);

describe('PageTabs', () => {
  it('marks only the open sibling current when tabs share a path', async () => {
    const row = await rowAt('/guide?at=type');
    expect(current(row)).toEqual(['Type']);
    expect(row.getByRole('link', { name: 'Type' }).className).toBe('doc-tab is-on');
    expect(row.getByRole('link', { name: 'Color' }).className).not.toContain('is-on');
  });

  it('marks the tab with no search current when no sibling is open, whatever else the address holds', async () => {
    const row = await rowAt('/guide?panel=x');
    expect(current(row)).toEqual(['Color']);
  });
});
