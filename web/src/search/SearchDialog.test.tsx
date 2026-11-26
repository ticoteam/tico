import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { bind } from '@/actions/bind';
import { forgetRecentViews, noteView, queryClient, setRecentHere } from '@/data';
import { holdToasts } from '@/kit/Toast';
import { loadProviders } from './providers';
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

// The features' providers load on the first open. Landing mid-test they search again and redraw the list, so a row the
// test already holds is gone. Load them first: every open then starts with all of them.
beforeAll(() => loadProviders());
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

  it('opened over a layer (a task open over the page), shows in that layer, not under its hiding of the rest', async () => {
    const layer = document.body.appendChild(document.createElement('div'));
    const letGo = holdToasts(layer);
    render(<SearchDialog />);
    act(() => openSearch());
    const popup = await screen.findByRole('dialog', { name: 'Search' });
    expect(layer.contains(popup)).toBe(true);
    act(() => closeSearch());
    letGo();
    layer.remove();
  });

  it('keeps what was typed when the layer under it closes (Back closes the task) and it moves out of it', async () => {
    const layer = document.body.appendChild(document.createElement('div'));
    const letGo = holdToasts(layer);
    render(<SearchDialog />);
    act(() => openSearch());
    fireEvent.change(await screen.findByRole('combobox', { name: 'Search' }), { target: { value: 'invoice' } });
    act(() => letGo());
    layer.remove();
    await waitFor(() => expect(layer.contains(document.querySelector('.pal-off'))).toBe(false));
    expect((screen.getByRole('combobox', { name: 'Search' }) as HTMLInputElement).value).toBe('invoice');
  });

  it('closes on a click off the palette, even with the shell drawn over it', async () => {
    render(
      <>
        <div className="shell-frame" style={{ position: 'relative', zIndex: 1 }} />
        <SearchDialog />
      </>,
    );
    act(() => openSearch());
    await screen.findByRole('combobox', { name: 'Search' });
    const off = document.querySelector('.pal-off');
    expect(off).toBeTruthy();
    fireEvent.pointerDown(off!, { button: 0 });
    fireEvent.mouseDown(off!, { button: 0 });
    fireEvent.pointerUp(off!, { button: 0 });
    fireEvent.mouseUp(off!, { button: 0 });
    fireEvent.click(off!, { button: 0 });
    await waitFor(() => expect(screen.queryByRole('combobox', { name: 'Search' })).toBeNull());
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

  it('with nothing typed lists Recent views; a row opens that view and closes, and typing gives way to results', async () => {
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/library/docs/d1'] }) });
    bind({ router });
    const me = 'Tico Team\u0000human:chris';
    setRecentHere(me, '');
    noteView({ key: 'page:/tasks/product', to: '/tasks/product?who=me', trail: ['Tasks', 'Product'] });
    noteView({ key: 'task:12', to: '/tasks/product?task=12', trail: ['Tasks', 'Product', 'Fix the login bug'] });
    noteView({ key: 'page:/library/docs/d1', to: '/library/docs/d1', trail: ['Library', 'Docs', 'Release notes'] });
    setRecentHere(me, 'page:/library/docs/d1');
    render(
      <QueryClientProvider client={queryClient}>
        <SearchDialog />
      </QueryClientProvider>,
    );
    act(() => openSearch());
    const views = await screen.findByRole('group', { name: 'Recent views' });
    expect(within(views).getAllByRole('option').map((o) => o.textContent)).toEqual(['Tasks › Product › Fix the login bug', 'Tasks › Product']);

    const field = screen.getByRole('combobox', { name: 'Search' });
    fireEvent.change(field, { target: { value: 'QA' } });
    await screen.findByRole('group', { name: 'People' });
    expect(screen.queryByRole('group', { name: 'Recent views' })).toBeNull();
    fireEvent.change(field, { target: { value: '' } });
    const again = await screen.findByRole('group', { name: 'Recent views' });

    fireEvent.click(within(again).getByRole('option', { name: /Tasks › Product$/ }));
    await waitFor(() => expect(router.state.location.href).toBe('/tasks/product?who=me'));
    expect(screen.queryByRole('combobox', { name: 'Search' })).toBeNull();
    // Search's own Recent keeps only what was opened from its results
    expect(results.readRecent()).toEqual([]);
    forgetRecentViews();
    setRecentHere('', '');
  });

  it('has the cursor in a field from the moment it opens, and keeps what was typed while it drew', async () => {
    render(<SearchDialog />);
    act(() => {
      openSearch();
      // the press that opened it is not over yet: a field already holds the cursor (iOS raises the keyboard only now)
      const holder = document.activeElement as HTMLInputElement;
      expect(holder.tagName).toBe('INPUT');
      holder.value = 'Q';
    });
    const field = (await screen.findByRole('combobox', { name: 'Search' })) as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    expect(field.value).toBe('Q');
    expect(field.selectionStart).toBe(1);
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
