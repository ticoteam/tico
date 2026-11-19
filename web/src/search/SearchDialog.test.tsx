import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { SearchDialog } from './SearchDialog';
import { closeSearch, openSearch } from './state';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const QA = { id: 'bot:qa', kind: 'agent', name: 'QA Owner', slug: 'qa', team: 'Engineering' };

beforeEach(() => {
  queryClient.setQueryData(['directory'], { actors: [QA], byId: { [QA.id]: QA } });
  vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')));
});
afterEach(() => {
  act(() => closeSearch());
  bind({ router: null });
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Search @s1209', () => {
  it('finds a teammate by name and opens their page; the last row hands the words to Tico', async () => {
    // a choice runs through the action layer (shell.goTo), which moves this router
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/tasks'] }) });
    bind({ router });
    render(
      <QueryClientProvider client={queryClient}>
        <SearchDialog />
      </QueryClientProvider>,
    );
    act(() => openSearch());
    const field = await screen.findByRole('combobox', { name: 'Search' });
    fireEvent.change(field, { target: { value: 'QA' } });
    const people = await screen.findByRole('group', { name: 'People' });
    expect(within(people).getByRole('option', { name: /QA Owner/ })).toBeTruthy();
    const options = screen.getAllByRole('option');
    expect(options.at(-1)?.textContent).toContain('Ask Tico about QA');
    fireEvent.click(within(people).getByRole('option', { name: /QA Owner/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/team/qa'));
    expect(screen.queryByRole('combobox', { name: 'Search' })).toBeNull();
  });
});
