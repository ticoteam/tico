import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StyleGuidePage } from './exports';

async function show(at = '/settings/style-guide') {
  const search = new URLSearchParams(at.split('?')[1] ?? '');
  const root = createRootRoute({ component: () => <StyleGuidePage at={search.get('at') ?? undefined} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queries}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return screen.findByRole('main');
}

describe('Style guide', () => {
  it('is a list page whose tabs are links, Color first', async () => {
    const main = await show();
    expect(within(main).getByRole('heading', { level: 1, name: 'Style guide' })).toBeTruthy();
    const tabs = within(main).getByRole('navigation', { name: 'Style guide pages' });
    expect(within(tabs).getByRole('link', { name: 'Color' }).getAttribute('aria-current')).toBe('page');
    expect(within(tabs).getByRole('link', { name: 'Controls' }).getAttribute('href')).toBe('/settings/style-guide?at=controls');
  });

  it('the address picks the tab', async () => {
    const main = await show('/settings/style-guide?at=controls');
    const tabs = within(main).getByRole('navigation', { name: 'Style guide pages' });
    expect(within(tabs).getByRole('link', { name: 'Controls' }).getAttribute('aria-current')).toBe('page');
    // the row draws one tab on (TanStack's own active check also marks the search-less Color tab current; see report)
    expect([...tabs.querySelectorAll('.doc-tab.is-on')].map((el) => el.textContent)).toEqual(['Controls']);
  });

  it('draws every area, colors in both themes', async () => {
    const main = await show();
    for (const name of ['Color', 'Type', 'Marks', 'Rows', 'Controls', 'Chat', 'Sheets', 'Motion', 'Pages'])
      expect(within(main).getByRole('heading', { level: 2, name })).toBeTruthy();
    const themes = [...main.querySelectorAll('.sg-theme')].map((el) => el.getAttribute('data-theme'));
    expect(themes).toEqual(['dark', 'light']);
  });

  it('shows the head parts, a value, a summary, did lines with a mark or a face, and a reply being written', async () => {
    const main = await show();
    const owner = within(main).getAllByRole('button', { name: 'Chat with Engineering Manager' });
    expect(owner.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    fireEvent.click(owner[0]!);
    expect(owner[0]!.getAttribute('aria-pressed')).toBe('true');
    expect(within(main).getByRole('button', { name: 'Model' }).className).toBe('dd');
    expect(within(main).getByRole('button', { name: 'Engineering' }).className).toBe('scope-pick');
    expect(within(main).getByText('Haiku 5.5, $40 this month').className).toBe('sec-sum');
    expect(within(main).getByText('Asked QA Owner').closest('.did')?.querySelector('.av')).toBeTruthy();
    expect(main.querySelectorAll('.msg.is-live .typing').length).toBe(2);
  });

  it('a section folds from its name', async () => {
    const main = await show();
    const fold = within(main).getAllByRole('button', { name: /^Steps/ })[0]!;
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(fold);
    expect(fold.getAttribute('aria-expanded')).toBe('false');
  });

  it('says agents, never bots', async () => {
    const main = await show();
    expect(main.textContent).not.toMatch(/\bbots?\b/i);
  });
});
