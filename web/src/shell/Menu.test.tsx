import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MenuItem } from '@/kit/Menu';
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
    expect(within(nav).getByRole('link', { name: 'Tasks' }).textContent).toBe(''); // the folded Dot holds no count
  });

  it('opens the company pick and the account menu from the company and your name, and the plus adds a teammate', async () => {
    const pick = vi.fn();
    const add = vi.fn();
    const nav = await show({
      companyMenu: <MenuItem label="Okafor Garden Tours" onSelect={pick} />,
      accountMenu: <MenuItem label="Sign out" onSelect={() => undefined} />,
      onAddTeammate: add,
    });
    fireEvent.click(within(nav).getByRole('button', { name: 'Fernhill' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Okafor Garden Tours' }));
    expect(pick).toHaveBeenCalledOnce();
    // your name at the foot opens your menu (the Team section still links to your page)
    fireEvent.click(within(nav).getByRole('button', { name: 'Maya' }));
    expect(await screen.findByRole('menuitem', { name: 'Sign out' })).toBeTruthy();
    fireEvent.click(within(nav).getByRole('button', { name: 'Add a teammate' }));
    expect(add).toHaveBeenCalledOnce();
  });

  it('marks the "?" with Tico support\'s unread count, open and folded, and presses onHelp', async () => {
    const help = vi.fn();
    let nav = await show({ counts: { support: 2 }, onHelp: help });
    const q = within(nav).getByRole('button', { name: 'Tico support, 2 unread' });
    expect(q.parentElement?.querySelector('.slot-n')?.textContent).toBe('2');
    fireEvent.click(q);
    expect(help).toHaveBeenCalledOnce();
    document.body.innerHTML = '';
    nav = await show({ counts: { support: 3 }, folded: true });
    expect(within(nav).getByRole('button', { name: 'Tico support, 3 unread' }).parentElement?.querySelector('.slot-n')?.textContent).toBe('3');
    document.body.innerHTML = '';
    nav = await show();
    expect(within(nav).getByRole('button', { name: 'Tico support' }).parentElement?.querySelector('.slot-n')).toBeNull();
  });
});
