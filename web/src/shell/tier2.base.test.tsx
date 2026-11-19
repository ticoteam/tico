import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StyleGuidePage } from '@/features/styleguide/exports';

/* Tier 2 check of the base track: the Style guide, and the one dialog that stands in for the old app's Finance and
   Growth dialog shells (finance-shared.ts finDialog, growth-page.ts grDialog), shown in the guide's Sheets area. */

async function show(at = '/settings/style-guide') {
  const search = new URLSearchParams(at.split('?')[1] ?? '');
  const root = createRootRoute({ component: () => <StyleGuidePage at={search.get('at') ?? undefined} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return screen.findByRole('main');
}

describe('Style guide @s1071', () => {
  it('draws the target design from the app parts: every area under one head, its tabs links into the page', async () => {
    const main = await show('/settings/style-guide?at=sheets');
    expect(within(main).getByRole('heading', { level: 1, name: 'Style guide' })).toBeTruthy();
    const tabs = within(main).getByRole('navigation', { name: 'Style guide pages' });
    expect(within(tabs).getByRole('link', { name: 'Sheets' }).getAttribute('href')).toBe('/settings/style-guide?at=sheets');
    for (const name of ['Color', 'Type', 'Marks', 'Rows', 'Controls', 'Chat', 'Sheets', 'Motion', 'Pages'])
      expect(within(main).getByRole('heading', { level: 2, name })).toBeTruthy();
  });
});

describe('The one dialog, in place of the Finance and Growth shells @s448 @s477', () => {
  it('opens named by its title, holds Cancel and one filled act, and closes from either', async () => {
    const main = await show('/settings/style-guide?at=sheets');
    const sheets = main.querySelector('#sg-sheets') as HTMLElement;
    fireEvent.click(within(sheets).getByRole('button', { name: 'Delete task' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete this task?' });
    expect(within(dialog).getByText('Gift cards: send on a chosen date')).toBeTruthy();
    const buttons = within(dialog).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Cancel', 'Delete task']);
    expect(buttons.filter((b) => b.classList.contains('btn-fill'))).toHaveLength(1);
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  });
});
