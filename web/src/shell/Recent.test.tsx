import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { forgetRecentViews, noteView, setRecentHere } from '@/data';
import { openRecentViews } from './Recent';
import { Trail } from './Trail';

vi.mock('@/search', () => ({ openSearch: vi.fn() }));

const ME = 'Tico Team\u0000human:chris';
const PARTS = [{ label: 'Tico Team', to: '/' }, { label: 'Tasks', to: '/tasks' }, { label: 'Product' }];

function draw(at = '/tasks/product') {
  const history = createMemoryHistory({ initialEntries: [at] });
  const router = createRouter({ routeTree: createRootRoute({ component: () => <Trail parts={PARTS} /> }), history });
  render(<RouterProvider router={router} />);
  return router;
}
const button = () => screen.findByRole('button', { name: 'Recent views' });
const list = () => screen.findByRole('menu', { name: 'Recent views' });

beforeEach(() => {
  setRecentHere(ME, '');
  noteView({ key: 'page:/library/docs', to: '/library/docs', trail: ['Library', 'Docs'] });
  noteView({ key: 'task:12', to: '/tasks/product?who=me&task=12', trail: ['Tasks', 'Product', 'Fix the login bug'] });
  noteView({ key: 'page:/tasks/product', to: '/tasks/product', trail: ['Tasks', 'Product'] });
  setRecentHere(ME, 'page:/tasks/product');
});
afterEach(() => {
  forgetRecentViews();
  setRecentHere('', '');
});

describe('Recent views', () => {
  it('sits left of the first breadcrumb, the first stop in the top bar', async () => {
    draw();
    const nav = await screen.findByRole('navigation', { name: 'Breadcrumb' });
    const first = nav.querySelector('button, a');
    expect(first).toBe(await button());
    expect(first?.nextElementSibling?.textContent).toBe('Tico Team');
  });

  it('lists the views before this one, newest first, each with its whole trail as its tooltip; a row opens it and closes', async () => {
    const router = draw();
    fireEvent.click(await button());
    const rows = within(await list()).getAllByRole('menuitem');
    expect(rows.map((r) => r.textContent)).toEqual(['Tasks›Product›Fix the login bug', 'Library›Docs'].map((t) => t.replace(/›/g, '')));
    expect(rows.map((r) => r.getAttribute('title'))).toEqual(['Tasks › Product › Fix the login bug', 'Library › Docs']);
    fireEvent.click(rows[0]!);
    await waitFor(() => expect(router.state.location.href).toBe('/tasks/product?who=me&task=12'));
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'Recent views' })).toBeNull());
  });

  it('leaves a ⌘ or Ctrl click to the browser, for a new tab', async () => {
    const router = draw();
    fireEvent.click(await button());
    const row = within(await list()).getAllByRole('menuitem')[1]!;
    expect(row.getAttribute('href')).toBe('/library/docs');
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true });
    row.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
    expect(router.state.location.pathname).toBe('/tasks/product');
  });

  it('says what it will hold the first time, with no rows', async () => {
    forgetRecentViews();
    draw();
    fireEvent.click(await button());
    const menu = await list();
    expect(within(menu).queryAllByRole('menuitem')).toEqual([]);
    expect(menu.textContent).toContain('Pages you open show here.');
  });

  it('works from the keyboard: Enter opens it, arrows move, Escape closes it back to the button', async () => {
    draw();
    const b = await button();
    b.focus();
    fireEvent.keyDown(b, { key: 'Enter' });
    fireEvent.click(b);
    const rows = within(await list()).getAllByRole('menuitem');
    await waitFor(() => expect(document.activeElement).toBe(rows[0]));
    fireEvent.keyDown(rows[0]!, { key: 'ArrowDown' });
    await waitFor(() => expect(document.activeElement).toBe(rows[1]));
    fireEvent.keyDown(rows[1]!, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'Recent views' })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(b));
  });

  it('opens from g r with its top row ready', async () => {
    draw();
    await button();
    act(() => openRecentViews());
    const rows = within(await list()).getAllByRole('menuitem');
    await waitFor(() => expect(document.activeElement).toBe(rows[0]));
  });
});
