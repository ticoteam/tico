import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MenuItem } from '@/kit/Menu';
import { Menu, type MenuNode, type MenuProps } from './Menu';
import { PhoneCards } from './phone';

const mate = (id: string, name: string, kind: 'person' | 'agent' = 'agent') => ({ id, name, short: name.slice(0, 2), hue: 200, kind, to: `/team/${id}` });
const row = (id: string, name: string, kids: MenuNode[] = [], more: Partial<Extract<MenuNode, { kind: 'mate' }>> = {}): MenuNode => ({
  kind: 'mate',
  key: `bot:${id}`,
  mate: mate(id, name),
  kids,
  size: kids.reduce((n, k) => n + 1 + k.size, 0),
  ...more,
});
const ENG = { id: 'engineering', name: 'Engineering' };
/* You: the Engineering Manager reports to you, with QA Owner (and E2E Runner under it) and Release Owner under it; Ops
   reports to you with no team; Platform has no head, so it is a row of its own holding Iris. */
const ORG: MenuNode[] = [
  row('em', 'Engineering Manager', [row('qa', 'QA Owner', [row('e2e', 'E2E Runner')]), row('rel', 'Release Owner')], { team: ENG }),
  row('ops', 'Ops'),
  { kind: 'team', key: 'team:platform', team: { id: 'platform', name: 'Platform' }, kids: [row('iris', 'Iris')], size: 1 },
];
const PROPS: MenuProps = {
  company: { mark: 'F', name: 'Fernhill' },
  me: mate('me', 'Maya', 'person'),
  org: ORG,
  counts: { flow: 8 },
};

async function show(props: Partial<MenuProps> = {}, at = '/tasks') {
  const root = createRootRoute({ component: () => <Menu {...PROPS} {...props} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(<RouterProvider router={router} />);
  return screen.findByRole('navigation', { name: 'Main' });
}

describe('Menu', () => {
  it('draws its rows as links, with no Meetings row, and the person row with Settings @s1188', async () => {
    const nav = await show();
    for (const name of ['Tasks', 'Calendar', 'Company', 'Library']) expect(within(nav).getByRole('link', { name: new RegExp(`^${name}`) })).toBeTruthy();
    expect(within(nav).queryByRole('link', { name: /Meetings/ })).toBeNull();
    // Threads and Updates are no rows: conversations are the area's tabs, updates cards in a team's thread
    expect(within(nav).queryByRole('link', { name: /^(Threads|Updates)/ })).toBeNull();
    expect(within(nav).getByRole('link', { name: /^Tasks/ }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Settings' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Fernhill' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Engineering Manager' })).toBeTruthy();
  });

  it('leaves out pages the person cannot open', async () => {
    const nav = await show({ can: page => page !== 'company' && page !== 'settings' });
    expect(within(nav).queryByRole('link', { name: /^Company/ })).toBeNull();
    expect(within(nav).queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(within(nav).getByRole('link', { name: /^Library/ })).toBeTruthy();
  });

  it('folded, keeps every row as an icon with its name, and the company and settings reachable', async () => {
    const nav = await show({ folded: true }, '/team/qa');
    // the faces are the rows showing: your direct reports, each folded
    for (const name of ['Tasks', 'Calendar', 'Company', 'Library', 'Engineering Manager', 'Ops', 'Settings']) expect(within(nav).getByRole('link', { name })).toBeTruthy();
    expect(within(nav).queryByRole('link', { name: 'QA Owner' })).toBeNull();
    expect(within(nav).getByRole('button', { name: 'Fernhill' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Tasks' }).textContent).toBe(''); // the folded Dot holds no count
  });

  it('opens the company pick and the account menu from the company and your name', async () => {
    const pick = vi.fn();
    const nav = await show({
      companyMenu: <MenuItem label="Okafor Garden Tours" onSelect={pick} />,
      accountMenu: <MenuItem label="Sign out" onSelect={() => undefined} />,
    });
    fireEvent.click(within(nav).getByRole('button', { name: 'Fernhill' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Okafor Garden Tours' }));
    expect(pick).toHaveBeenCalledOnce();
    // your name at the foot opens your menu
    fireEvent.click(within(nav).getByRole('button', { name: 'Maya' }));
    expect(await screen.findByRole('menuitem', { name: 'Sign out' })).toBeTruthy();
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

  it('has no search of its own, open or folded: search is beside Tico (each page keeps its own head) @s1190', async () => {
    let nav = await show();
    expect(within(nav).queryByRole('button', { name: 'Search' })).toBeNull();
    document.body.innerHTML = '';
    nav = await show({ folded: true });
    expect(within(nav).queryByRole('button', { name: 'Search' })).toBeNull();
  });

  it('opens the Company fold on a company page, with its pages, and keeps the Library shut @s1198', async () => {
    const nav = await show({}, '/company/finance');
    for (const name of ['Strategy', 'Finance', 'Growth', 'Products', 'Risk', 'Routines', 'Listening', 'Rules', 'Usage'])
      expect(within(nav).getByRole('link', { name }).getAttribute('href')).toBe(`/company/${name.toLowerCase()}`);
    expect(within(nav).getByRole('link', { name: 'Finance' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).queryByRole('link', { name: 'Docs' })).toBeNull();
  });

  it('opens the Library fold on a library page, leaving out what the person cannot open @s1199', async () => {
    const nav = await show({ can: page => page !== 'library/tools' }, '/library/docs');
    for (const name of ['Files', 'Docs', 'Skills', 'Contacts']) expect(within(nav).getByRole('link', { name }).getAttribute('href')).toBe(`/library/${name.toLowerCase()}`);
    expect(within(nav).queryByRole('link', { name: 'Tools' })).toBeNull();
    expect(within(nav).getByRole('link', { name: 'Docs' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).queryByRole('link', { name: 'Finance' })).toBeNull();
  });

  it.each([
    ['Company', '/company/finance', 'Finance'],
    ['Library', '/library/docs', 'Docs'],
  ])('folds and reopens %s without losing the current page', async (name, path, child) => {
    const nav = within(await show({}, path));
    const section = nav.getByRole('button', { name, expanded: true });
    fireEvent.click(section);
    expect(nav.getByRole('button', { name, expanded: false })).toBe(section);
    expect(nav.queryByRole('link', { name: child })).toBeNull();
    fireEvent.click(section);
    expect(nav.getByRole('link', { name: child }).getAttribute('aria-current')).toBe('page');
  });

  /* Going deeper never changes the menu (plan/layers.html): what is open is the person's, never the address's. */
  describe('going deeper', () => {
    async function at(path: string) {
      const root = createRootRoute({ component: () => <Menu {...PROPS} /> });
      const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [path] }) });
      const view = render(<RouterProvider router={router} />);
      const go = (to: string) => act(() => router.navigate({ to } as never));
      const nav = () => within(screen.getByRole('navigation', { name: 'Main' }));
      // what the person sees: every row, which are open, which is marked, and each team's fold
      const shape = () =>
        [...document.querySelectorAll('.nav-items > *, .tree-line > :is(a, button)')].map(
          (e) => `${e.className}|${e.getAttribute('aria-expanded')}|${e.getAttribute('aria-current')}|${e.textContent}`,
        );
      await screen.findByRole('navigation', { name: 'Main' });
      return { router, view, go, nav, shape };
    }

    it('opening a routine, a doc or a strategy page changes nothing in the menu', async () => {
      const m = await at('/company/routines');
      const before = m.shape();
      await m.go('/company/routines?panel=builtin%3Amaintenance');
      expect(m.shape()).toEqual(before);
      await m.go('/company/routines/r1');
      expect(m.shape().filter((r) => !r.includes('Routines'))).toEqual(before.filter((r) => !r.includes('Routines')));
      expect(m.nav().getByRole('button', { name: 'Company', expanded: true })).toBeTruthy();
    });

    it('a section the person shut stays shut on a deeper page, which only marks it', async () => {
      const m = await at('/company/strategy');
      fireEvent.click(await m.nav().findByRole('button', { name: 'Company', expanded: true }));
      const before = m.shape();
      expect(m.nav().queryByRole('link', { name: 'Strategy' })).toBeNull();
      await m.go('/company/strategy/market');
      expect(m.nav().getByRole('button', { name: 'Company', expanded: false }).getAttribute('aria-current')).toBe('page');
      expect(m.nav().queryByRole('link', { name: 'Strategy' })).toBeNull();
      expect(m.shape()).toEqual(before);
    });

    it('a page reached by a link opens no other section and no team; choosing a section in the menu does', async () => {
      const m = await at('/company/finance');
      await m.nav().findByRole('link', { name: 'Finance' });
      await m.go('/library/doc-1');
      expect(m.nav().queryByRole('link', { name: 'Docs' })).toBeNull();
      expect(m.nav().getByRole('link', { name: 'Finance' })).toBeTruthy();
      await m.go('/team/qa');
      expect(m.nav().getByRole('button', { name: '3 under Engineering Manager', expanded: false })).toBeTruthy();
      expect(m.nav().queryByRole('link', { name: 'QA Owner' })).toBeNull();
      fireEvent.click(m.nav().getByRole('link', { name: /^Library/ }));
      expect(await m.nav().findByRole('link', { name: 'Docs' })).toBeTruthy();
      expect(m.nav().queryByRole('link', { name: 'Finance' })).toBeNull();
    });

    it("keeps the person's choice when the menu is drawn again on another page", async () => {
      const m = await at('/library/docs');
      fireEvent.click(await m.nav().findByRole('button', { name: 'Library', expanded: true }));
      m.view.unmount();
      const n = await at('/library/doc-1');
      expect(n.nav().getByRole('button', { name: 'Library', expanded: false }).getAttribute('aria-current')).toBe('page');
      expect(n.nav().queryByRole('link', { name: 'Docs' })).toBeNull();
    });
  });

  it('the Team tab: your direct reports, each folded with how many are under it; a name opens your DM @s1200', async () => {
    localStorage.clear();
    const nav = within(await show({}, '/calendar'));
    expect(nav.getByRole('tab', { name: 'Team' }).getAttribute('aria-selected')).toBe('true');
    const em = nav.getByRole('link', { name: 'Engineering Manager' });
    expect(em.getAttribute('href')).toBe('/team/em');
    // folded: a person mark and the count of everyone underneath, on the chevron that opens it
    const fold = nav.getByRole('button', { name: '3 under Engineering Manager', expanded: false });
    expect(fold.textContent).toBe('3');
    expect(fold.querySelector('.tree-n svg')).toBeTruthy();
    expect(nav.queryByRole('link', { name: 'QA Owner' })).toBeNull();
    // a row with nobody under it has no chevron; a team with no head is a row of its own
    expect(nav.getByRole('link', { name: 'Ops' }).parentElement?.querySelector('.tree-fold')).toBeNull();
    expect(nav.getByRole('button', { name: /Platform/, expanded: false })).toBeTruthy();
    // the chevron opens the reports, each folded in turn, and folds them again
    fireEvent.click(fold);
    expect(nav.getByRole('link', { name: 'QA Owner' }).getAttribute('href')).toBe('/team/qa');
    expect(nav.getByRole('link', { name: 'Release Owner' })).toBeTruthy();
    expect(nav.queryByRole('link', { name: 'E2E Runner' })).toBeNull();
    fireEvent.click(nav.getByRole('button', { name: '1 under QA Owner' }));
    expect(nav.getByRole('link', { name: 'E2E Runner' })).toBeTruthy();
    fireEvent.click(nav.getByRole('button', { name: "Fold Engineering Manager's reports", expanded: true }));
    expect(nav.queryByRole('link', { name: 'QA Owner' })).toBeNull();
    localStorage.clear();
  });

  it('a team row opens and folds wherever it is pressed, and never leaves the page', async () => {
    localStorage.clear();
    const root = createRootRoute({ component: () => <Menu {...PROPS} /> });
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/calendar'] }) });
    render(<RouterProvider router={router} />);
    const nav = within(await screen.findByRole('navigation', { name: 'Main' }));
    const platform = nav.getByRole('button', { name: /Platform/ });
    fireEvent.click(platform.querySelector('.tree-name')!);
    expect(platform.getAttribute('aria-expanded')).toBe('true');
    expect(nav.getByRole('link', { name: 'Iris' })).toBeTruthy();
    fireEvent.click(platform);
    expect(platform.getAttribute('aria-expanded')).toBe('false');
    expect(router.state.location.pathname).toBe('/calendar');
    localStorage.clear();
  });

  it("keeps the person's folds when the menu is drawn again", async () => {
    localStorage.clear();
    let nav = within(await show({}, '/calendar'));
    fireEvent.click(nav.getByRole('button', { name: '3 under Engineering Manager' }));
    document.body.innerHTML = '';
    nav = within(await show({}, '/tasks'));
    expect(nav.getByRole('link', { name: 'QA Owner' })).toBeTruthy();
    localStorage.clear();
  });

  it("a row that heads a team carries the team's rows on its ⋯ and its right click, named; a team row its own", async () => {
    localStorage.clear();
    const teamMenu = vi.fn((t: { name: string }) => <MenuItem label={`Add to ${t.name}`} onSelect={() => undefined} />);
    const nav = within(await show({ teamMenu }, '/calendar'));
    // Ops heads no team: nothing to offer
    expect(nav.queryByRole('button', { name: 'More for Ops' })).toBeNull();
    fireEvent.click(nav.getByRole('button', { name: 'More for Engineering' }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByText('Engineering')).toBeTruthy();
    expect(within(menu).getByRole('menuitem', { name: 'Add to Engineering' })).toBeTruthy();
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    fireEvent.contextMenu(nav.getByRole('button', { name: /^Platform/ }));
    expect(await screen.findByRole('menuitem', { name: 'Add to Platform' })).toBeTruthy();
    localStorage.clear();
  });

  it('a team the address names (an old team link) opens down to the rows that head it, on the Team tab', async () => {
    localStorage.clear();
    localStorage.setItem('tico-next:menu.talk.v1', JSON.stringify('threads'));
    const deep: MenuNode[] = [row('gm', 'Growth Manager', [row('sm', 'Sales Manager', [row('rep', 'Sales Rep')], { team: { id: 'sales', name: 'Sales' } })]), ...ORG];
    const nav = within(await show({ org: deep, reveal: 'sales', threads: <p>threads</p> }, '/calendar'));
    await waitFor(() => expect(nav.getByRole('tab', { name: 'Team' }).getAttribute('aria-selected')).toBe('true'));
    expect(await nav.findByRole('link', { name: 'Sales Rep' })).toBeTruthy();
    expect(nav.getByRole('link', { name: 'Sales Manager' }).closest('.tree-line')?.className).toContain('is-hl');
    // the rest stays as the person left it
    expect(nav.queryByRole('link', { name: 'QA Owner' })).toBeNull();
    localStorage.clear();
  });

  it('Team | Threads: no counts; the last tab stays; the Dot only on a row, never a tab or a folded team; Message and Call', async () => {
    localStorage.clear();
    const message = vi.fn();
    const call = vi.fn();
    const automations = vi.fn();
    const asking: MenuNode[] = [row('em', 'Engineering Manager', [row('qa', 'QA Owner', [], { mate: { ...mate('qa', 'QA Owner'), needs: true } })], { team: ENG })];
    let nav = within(
      await show({ org: asking, threads: <a href="/threads/t1">Refund policy</a>, onMessage: message, onCall: call, onAutomations: automations }),
    );
    // folded, the row above shows no Dot; neither tab does; the tabs have no ⋯
    expect(nav.getByRole('link', { name: 'Engineering Manager' }).closest('.tree-line')?.querySelector('.dot')).toBeNull();
    expect(document.querySelector('.cat .dot')).toBeNull();
    expect(nav.queryByRole('button', { name: 'More' })).toBeNull();
    // open, the teammate who asks carries it on their row
    fireEvent.click(nav.getByRole('button', { name: '1 under Engineering Manager' }));
    expect(nav.getByRole('link', { name: /QA Owner/ }).querySelector('.dot')).toBeTruthy();
    fireEvent.click(nav.getByRole('tab', { name: /Threads/ }));
    expect(nav.getByRole('link', { name: 'Refund policy' })).toBeTruthy();
    expect(document.querySelector('.cat .dot')).toBeNull();
    fireEvent.click(nav.getByRole('button', { name: 'Message' }));
    fireEvent.click(nav.getByRole('button', { name: 'Call' }));
    expect(message).toHaveBeenCalledOnce();
    expect(call).toHaveBeenCalledOnce();
    // Threads' ⋯ has one item, Automations
    fireEvent.click(nav.getByRole('button', { name: 'More' }));
    const items = await screen.findAllByRole('menuitem');
    expect(items.map((i) => i.textContent)).toEqual(['Automations']);
    fireEvent.click(items[0]!);
    expect(automations).toHaveBeenCalledOnce();
    expect(document.querySelector('.ca .count, .ca .slot-n')).toBeNull();
    document.body.innerHTML = '';
    nav = within(await show({ threads: <a href="/threads/t1">Refund policy</a> }));
    expect(nav.getByRole('tab', { name: 'Threads' }).getAttribute('aria-selected')).toBe('true');
    localStorage.clear();
  });

  it('switches conversation tabs with arrow keys and leaves one tab in the keyboard order', async () => {
    localStorage.clear();
    const nav = within(await show({ threads: <a href="/threads/t1">Refund policy</a> }));
    const team = nav.getByRole('tab', { name: 'Team' });
    const threads = nav.getByRole('tab', { name: 'Threads' });
    expect(team.tabIndex).toBe(0);
    expect(threads.tabIndex).toBe(-1);
    act(() => team.focus());
    fireEvent.keyDown(team, { key: 'ArrowRight' });
    await waitFor(() => expect(document.activeElement).toBe(threads));
    expect(threads.getAttribute('aria-selected')).toBe('true');
    expect(nav.getByRole('tabpanel', { name: 'Threads' }).contains(nav.getByRole('link', { name: 'Refund policy' }))).toBe(true);
    expect(team.tabIndex).toBe(-1);
    localStorage.clear();
  });

  it('on a phone, the menu is a card from its lip: the rows and a way to tuck it away; search is on the bar @s1210', async () => {
    const root = createRootRoute({ component: () => <PhoneCards page={<h1>Finance</h1>} tico={<p>Tico's chat</p>} menu={<Menu {...PROPS} />} /> });
    render(<RouterProvider router={createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks'] }) })} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open Menu' }));
    const card = screen.getByRole('dialog', { name: 'Menu' });
    const nav = within(card).getByRole('navigation', { name: 'Main' });
    for (const name of ['Tasks', 'Calendar', 'Company', 'Library']) expect(within(nav).getByRole('link', { name: new RegExp(`^${name}`) })).toBeTruthy();
    expect(within(nav).getByRole('tab', { name: 'Threads' })).toBeTruthy();
    expect(within(card).queryByRole('button', { name: 'Search' })).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Tuck the menu away' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Finance' })).toBeTruthy();
  });
});
