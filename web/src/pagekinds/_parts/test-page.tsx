import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, it } from 'vitest';
import type { PageTab, StateProps } from '@/pagekinds/types';

/** Renders a page under a router at `at` and waits for its sheet. */
export async function showPage(node: ReactNode, at = '/p/one') {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(<RouterProvider router={router} />);
  return screen.findByRole('main');
}

export const TABS: readonly PageTab[] = [
  { label: 'One', to: '/p/one' },
  { label: 'Two', to: '/p/two', count: 3 },
];

/** The title is the h1, a flight target; the tabs are links and the address marks one. */
export function expectHeadAndTabs(main: HTMLElement, title: string, path: string) {
  expect(within(main).getByRole('heading', { level: 1, name: title }).getAttribute('data-target')).toBe(`page:${path}`);
  const tabs = within(main).getByRole('navigation', { name: `${title} pages` });
  expect(within(tabs).getByRole('link', { name: 'One' }).getAttribute('aria-current')).toBe('page');
  expect(within(tabs).getByRole('link', { name: /Two/ }).getAttribute('aria-current')).toBeNull();
}

/** One test per state: the head stays (except no access), and the body says what happened. */
export function stateTests(title: string, page: (s: StateProps) => ReactNode) {
  it('loading keeps the head and announces itself', async () => {
    const main = await showPage(page({ state: 'loading' }));
    expect(within(main).getByRole('heading', { level: 1, name: title })).toBeTruthy();
    expect(within(main).getByRole('status', { name: 'Loading' })).toBeTruthy();
  });
  it('empty says so, with its action', async () => {
    const main = await showPage(page({ state: 'empty', empty: { title: 'Nothing in Review', action: <button type="button">Add</button> } }));
    expect(within(main).getByRole('heading', { name: 'Nothing in Review' })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Add' })).toBeTruthy();
  });
  it('fault names the page and offers Try again', async () => {
    const main = await showPage(page({ state: 'fault', onRetry: () => {} }));
    expect(within(main).getByRole('heading', { name: `${title} did not load` })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
  it('no access shows nothing of the page', async () => {
    const main = await showPage(page({ state: 'no-access', noAccess: { text: 'An owner can share it with you.' } }));
    expect(within(main).queryByRole('heading', { level: 1 })).toBeNull();
    expect(within(main).getByRole('heading', { name: "You don't have access to this page" })).toBeTruthy();
    expect(within(main).getByText('An owner can share it with you.')).toBeTruthy();
  });
}
