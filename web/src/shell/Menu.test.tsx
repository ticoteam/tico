import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Menu, type MenuProps } from './Menu';

const mate = (id: string, name: string, kind: 'person' | 'agent' = 'agent') => ({ id, name, short: name.slice(0, 2), hue: 200, kind, to: `/team/${id}` });
const PROPS: MenuProps = {
  company: { mark: 'F', name: 'Fernhill' },
  me: mate('me', 'Maya', 'person'),
  teams: [{ id: 'engineering', name: 'Engineering', to: '/tasks/engineering', members: [mate('qa', 'QA Owner'), mate('rel', 'Release Owner')] }],
  counts: { flow: 8, threads: 12, updates: 7 },
};

async function show(props: Partial<MenuProps> = {}, at = '/tasks') {
  const root = createRootRoute({ component: () => <Menu {...PROPS} {...props} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(<RouterProvider router={router} />);
  return screen.findByRole('navigation', { name: 'Main' });
}

describe('Menu', () => {
  it('draws its rows as links, with no Meetings row, and the person row with Settings', async () => {
    const nav = await show();
    for (const name of ['Tasks', 'Calendar', 'Threads', 'Updates', 'Company', 'Library']) expect(within(nav).getByRole('link', { name: new RegExp(`^${name}`) })).toBeTruthy();
    expect(within(nav).queryByRole('link', { name: /Meetings/ })).toBeNull();
    expect(within(nav).getByRole('link', { name: /^Tasks/ }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Settings' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Fernhill' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Search' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: /Engineering/ })).toBeTruthy();
  });

  it('leaves out pages the person cannot open', async () => {
    const nav = await show({ can: page => page !== 'company' && page !== 'settings' });
    expect(within(nav).queryByRole('link', { name: /^Company/ })).toBeNull();
    expect(within(nav).queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(within(nav).getByRole('link', { name: /^Library/ })).toBeTruthy();
  });

  it('folded, keeps every row as an icon with its name, and the company, search and settings reachable', async () => {
    const nav = await show({ folded: true }, '/team/qa');
    for (const name of ['Tasks', 'Calendar', 'Threads', 'Updates', 'Company', 'Library', 'QA Owner', 'Settings']) expect(within(nav).getByRole('link', { name })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Fernhill' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Search' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Tasks' }).textContent).toBe('8');
  });
});
