import { QueryClientProvider } from '@tanstack/react-query';
import { Outlet, createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider, useLocation } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { router as appRouter } from '@/router';
import { arrangementFor } from './arrangement';
import { Frame } from './Frame';
import { PhoneCards } from './phone';
import { Sheet } from './Sheet';
import { SupportLine } from './SupportLine';
import { tabTitle } from './title';

/* Browser run 6, the shell's part: a page that breaks, the phone's page-owned chats, hidden cards, the tab's title
   and the Tico support line. */

// the router module only: the app's pages are not needed to know what it draws for a page that breaks
vi.mock('@/routeTree.gen', () => ({ routeTree: createRootRoute() }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));

const width = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });

function inRouter(node: () => ReactNode, at: string) {
  const root = createRootRoute({ component: node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => width(1024));
afterEach(() => {
  queryClient.clear();
  width(1024);
});

describe('a page that breaks while drawing', () => {
  it('shows its fault in the page’s place; the menu stays, and Try again draws it again', async () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    const quietWarn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    let broken = true;
    const root = createRootRoute({
      component: () => (
        <>
          <nav aria-label="Main">menu</nav>
          <Outlet />
        </>
      ),
    });
    const page = createRoute({
      getParentRoute: () => root,
      path: '/',
      component: () => {
        if (broken) throw new TypeError('d.toISOString is not a function');
        return <h1>Routines</h1>;
      },
    });
    const router = createRouter({
      routeTree: root.addChildren([page]),
      history: createMemoryHistory({ initialEntries: ['/'] }),
      defaultErrorComponent: appRouter.options.defaultErrorComponent,
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const main = await screen.findByRole('main', { name: 'This page' });
    expect(within(main).getByText('This page did not load')).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeTruthy();

    broken = false;
    fireEvent.click(within(main).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'Routines' })).toBeTruthy();
    expect(screen.queryByText('This page did not load')).toBeNull();
    quiet.mockRestore();
    quietWarn.mockRestore();
  });
});

/* The shell as the root draws it on a phone: the arrangement from the address, a task's chat with an open task. */
function PhoneShell() {
  const path = useLocation({ select: (l) => l.pathname });
  const search = useLocation({ select: (l) => l.search as Record<string, unknown> });
  const arr = arrangementFor(path, search);
  return (
    <Frame
      arrangement={arr}
      tico={<p>Tico</p>}
      menu={<nav aria-label="Main" />}
      page={
        <Sheet kind="page" label="Page">
          <h1>{arr === 'task' ? 'Fix the export' : 'Tasks'}</h1>
          <button type="button">Stage</button>
        </Sheet>
      }
      chat={arr === 'task' || arr === 'chat' ? <Sheet kind="chat" label="Chat" /> : undefined}
    />
  );
}

describe('phone: a task’s chat and an agent’s chat belong to the page', () => {
  it('a task opened by its own address shows its head; the chat waits as a lip until it is raised', async () => {
    width(390);
    inRouter(PhoneShell, '/tasks/eng/t1');
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.ph')?.getAttribute('data-card')).toBe('none');
    const lip = screen.getByRole('button', { name: 'Open Chat' });
    expect(lip.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(lip);
    expect(screen.getByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('opening a task from its list keeps the chat tucked', async () => {
    width(390);
    const router = inRouter(PhoneShell, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Tasks' })).toBeTruthy();
    await act(() => router.navigate({ to: '/tasks/eng/t1' as never }));
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('an address that names the chat opens with it raised', async () => {
    width(390);
    inRouter(PhoneShell, '/tasks/eng/t1?chat=task');
    expect(await screen.findByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('a page with a second chat in its address still opens on the chat (A1)', async () => {
    width(390);
    inRouter(PhoneShell, '/roadmap?chat=agent:builder');
    expect(await screen.findByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('an agent’s own page opens on the page, its chat a lip', async () => {
    width(390);
    inRouter(
      () => (
        <Frame
          arrangement="agent"
          tico={<p>Tico</p>}
          menu={<nav aria-label="Main" />}
          page={<Sheet kind="chat" label="Chat with Birch Builder" />}
          widgets={
            <Sheet kind="page" label="Birch Builder">
              <h1>Birch Builder</h1>
            </Sheet>
          }
        />
      ),
      '/team/builder',
    );
    expect(await screen.findByRole('heading', { name: 'Birch Builder' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Chat' }).closest('[inert]')).toBeNull();
  });
});

describe('phone: a card out of view takes no focus', () => {
  const cards = (chat?: ReactNode) => <PhoneCards page={<h1>Thread</h1>} tico={<p>Tico</p>} menu={<a href="/next/tasks">Tasks</a>} chat={chat} />;
  const chatLip = () => document.querySelector<HTMLElement>('.ly-chat > .lip');

  it('with no chat open, the chat card and its lip are inert', () => {
    render(cards());
    expect(chatLip()?.closest('[inert]')?.classList.contains('ly-chat')).toBe(true);
  });

  it('an open chat’s lip can be focused, until the menu comes up over it', () => {
    render(cards(<p>Owner’s chat</p>));
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Chat' }), { key: 'Escape' });
    expect(chatLip()?.closest('[inert]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open Menu' }));
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeTruthy();
    expect(chatLip()?.closest('[inert]')).not.toBeNull();
  });
});

describe('the browser tab’s title', () => {
  it('names the open page, and follows it', async () => {
    const shown = (name: string) => (
      <Frame
        arrangement="page"
        tico={<p>Tico</p>}
        menu={<nav aria-label="Main" />}
        page={
          <Sheet kind="page" label={name}>
            <h1>{name}</h1>
          </Sheet>
        }
      />
    );
    const { rerender } = render(shown('Finance'));
    await waitFor(() => expect(document.title).toBe('Finance - Tico'));
    rerender(shown('Strategy'));
    await waitFor(() => expect(document.title).toBe('Strategy - Tico'));
  });

  it('is plain Tico with no page name, and never uses a middle dot', () => {
    expect(tabTitle('')).toBe('Tico');
    expect(tabTitle('Tasks')).toBe('Tasks - Tico');
    expect(tabTitle('Tasks')).not.toContain('·');
  });
});

describe('the Tico support line', () => {
  it('says Tico support is signed in, with the time left and Stop to the setting', async () => {
    const until = new Date(Date.now() + 2 * 3600_000 + 60_000).toISOString();
    inRouter(() => <SupportLine session={{ expires_at: until }} />, '/tasks');
    const line = await screen.findByRole('status');
    expect(line.textContent).toContain('Tico support is signed in, 2 hours left');
    expect(within(line).getByRole('link', { name: 'Stop' }).getAttribute('href')).toBe('/settings/security');
  });

  it('shows nothing while no one is signed in', async () => {
    inRouter(
      () => (
        <>
          <h1>Tasks</h1>
          <SupportLine session={null} />
        </>
      ),
      '/tasks',
    );
    await screen.findByRole('heading', { name: 'Tasks' });
    expect(screen.queryByRole('status')).toBeNull();
  });
});
