import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import * as results from './results';
import type { Group } from './results';
import { SearchDialog } from './SearchDialog';
import { closeSearch, openSearch } from './state';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('./results', async (importOriginal) => {
  const original = await importOriginal<typeof results>();
  return { ...original, gather: vi.fn(original.gather) };
});

const QA = { id: 'bot:qa', kind: 'agent', name: 'QA Owner', slug: 'qa', team: 'Engineering' };

beforeEach(() => {
  queryClient.setQueryData(['directory'], { actors: [QA], byId: { [QA.id]: QA } });
  // The server's own search fails (a 500: not a restart or a drop, which the client would send again for a minute).
  vi.stubGlobal('fetch', async () => new Response(null, { status: 500 }));
});
afterEach(() => {
  act(() => closeSearch());
  bind({ router: null });
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Search @s1209', () => {
  it('anchors to the menu inside the desktop frame', async () => {
    render(
      <>
        <div className="shell-frame">
          <div className="shell-place" data-sheet="menu">
            <nav className="nav" />
          </div>
        </div>
        <SearchDialog />
      </>,
    );
    vi.spyOn(screen.getByRole('navigation'), 'getBoundingClientRect').mockReturnValue(new DOMRect(320, 12, 240, 800));
    act(() => openSearch());
    const popup = await screen.findByRole('dialog', { name: 'Search' });
    expect(popup.style.left).toBe('320px');
    expect(popup.style.top).toBe('12px');
  });

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

  it('does not open a result from before the query was cleared', async () => {
    render(<SearchDialog />);
    act(() => openSearch('QA'));
    const field = await screen.findByRole('combobox', { name: 'Search' });
    expect(await screen.findByRole('option', { name: /QA Owner/ })).toBeTruthy();
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByRole('combobox', { name: 'Search' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /QA Owner/ })).toBeNull();
  });

  it('keeps the selected person when a higher-ranked result arrives', async () => {
    const people: Group = {
      name: 'People',
      hits: [
        { key: 'bot:qa', title: 'QA Owner', run: { id: 'shell.goTo', input: { to: '/team/qa' } } },
        { key: 'bot:qa-reviewer', title: 'QA Reviewer', run: { id: 'shell.goTo', input: { to: '/team/qa-reviewer' } } },
      ],
    };
    const tasks: Group = { name: 'Tasks', hits: [{ key: 'task:1', title: 'QA task', run: { id: 'shell.goTo', input: { to: '/task/1' } } }] };
    let respond!: (groups: Group[]) => void;
    const answer = new Promise<Group[]>((resolve) => (respond = resolve));
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/tasks'] }) });
    bind({ router });
    await vi.mocked(results.gather).withImplementation(
      (_q, _from, _actions, _ctx, _signal, early) => {
        early?.([people]);
        return answer;
      },
      async () => {
        render(<SearchDialog />);
        act(() => openSearch('QA'));
        const field = await screen.findByRole('combobox', { name: 'Search' });
        const reviewer = await screen.findByRole('option', { name: /QA Reviewer/ });
        const scroll = vi.fn();
        Object.defineProperty(reviewer, 'scrollIntoView', { value: scroll });
        fireEvent.keyDown(field, { key: 'ArrowDown' });
        expect(reviewer.getAttribute('aria-selected')).toBe('true');
        expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
        await act(async () => respond([tasks, people]));
        expect(await screen.findByRole('option', { name: /QA task/ })).toBeTruthy();
        expect(reviewer.getAttribute('aria-selected')).toBe('true');
        fireEvent.keyDown(field, { key: 'Enter' });
        await waitFor(() => expect(router.state.location.pathname).toBe('/team/qa-reviewer'));
      },
    );
  });
});
