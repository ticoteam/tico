import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useLocation, useRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { useCards } from '@/kit/Popover';
import { arrangementFor } from '@/shell/arrangement';
import { Frame } from '@/shell/Frame';
import { Sheet } from '@/shell/Sheet';
import { PhoneCards, type PhoneCardsProps } from './PhoneCards';

vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));

const width = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });
afterEach(() => {
  queryClient.clear();
  width(1440);
  window.history.replaceState(null, '', '/');
});

/** The frame as the root draws it, on a phone, at an address. */
function onPhone(node: () => ReactNode, at: string) {
  width(390);
  const root = createRootRoute({ component: node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const cards = (more: Partial<PhoneCardsProps> = {}) => (
  <PhoneCards page={<h1>Finance</h1>} tico={<p>Tico's chat</p>} menu={<a href="/next/tasks">Tasks</a>} labels={{ chat: 'Finance Owner' }} {...more} />
);
const bar = () => screen.getByRole('navigation', { name: 'Bar' });
const icons = () => within(bar()).getAllByRole('button').map((b) => b.getAttribute('aria-label'));
const icon = (name: string) => within(bar()).getByRole('button', { name });
const current = () => within(bar()).getAllByRole('button').filter((b) => b.getAttribute('aria-current')).map((b) => b.getAttribute('aria-label'));
const up = () => document.querySelector('.phone')?.getAttribute('data-card');

describe('the phone’s bar', () => {
  it('on a page: Search, Tico and Company, icons only, the page being Company', () => {
    render(cards());
    expect(icons()).toEqual(['Search', 'Tico', 'Company']);
    expect(bar().textContent).toBe('');
    expect(current()).toEqual(['Company']);
    expect(screen.getByRole('heading', { name: 'Finance' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('with a teammate’s chat (a DM): the Agent icon shows its face, after Company', () => {
    render(cards({ chat: <p>Owner's chat</p>, tucked: true, faces: { chat: <i data-testid="face" /> } }));
    expect(icons()).toEqual(['Search', 'Tico', 'Company', 'Finance Owner']);
    expect(within(icon('Finance Owner')).getByTestId('face')).toBeTruthy();
    expect(current()).toEqual(['Company']);
  });

  it('on a task with its agent: all five, the task current; Agent raises the chat, Task brings the task back, Company leaves it', () => {
    const leave = vi.fn();
    render(cards({ page: <h1>Fix the export</h1>, chat: <p>Owner's chat</p>, tucked: true, labels: {}, onLeave: leave }));
    expect(icons()).toEqual(['Search', 'Tico', 'Company', 'Agent', 'Task']);
    expect(current()).toEqual(['Task']);
    fireEvent.click(icon('Agent'));
    expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy();
    expect(current()).toEqual(['Agent']);
    fireEvent.click(icon('Task'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(current()).toEqual(['Task']);
    fireEvent.click(icon('Tico'));
    fireEvent.click(icon('Company'));
    expect(leave).toHaveBeenCalledOnce();
    expect(up()).toBe('none');
  });

  it('Company brings the page back from Tico and from the chat; on the page it opens the menu, and again puts it away', () => {
    render(cards({ chat: <p>Owner's chat</p>, tucked: true }));
    fireEvent.click(icon('Tico'));
    expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy();
    expect(current()).toEqual(['Tico']);
    fireEvent.click(icon('Company'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(current()).toEqual(['Company']);
    fireEvent.click(icon('Finance Owner'));
    fireEvent.click(icon('Company'));
    expect(up()).toBe('none');
    fireEvent.click(icon('Company'));
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeTruthy();
    expect(current()).toEqual(['Company']);
    fireEvent.click(icon('Company'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a tap on an icon adds nothing to the history: Back is navigation only', () => {
    window.history.replaceState(null, '', '/tasks/eng');
    const before = window.history.length;
    render(cards({ chat: <p>Owner's chat</p>, tucked: true }));
    for (const name of ['Tico', 'Finance Owner', 'Company', 'Company', 'Tico', 'Company']) fireEvent.click(icon(name));
    expect(window.history.length).toBe(before);
    expect(window.history.state).toBeNull();
  });

  it('Search opens search; the card stays as it was', () => {
    const search = vi.fn();
    render(cards({ onSearch: search }));
    fireEvent.click(icon('Search'));
    expect(search).toHaveBeenCalledOnce();
    expect(up()).toBe('none');
  });

  it('Tico comes up at its latest message with the cursor already in its box (iOS raises the keyboard only then)', () => {
    const tico = (
      <>
        <div className="chat-body" data-testid="tico-chat" />
        <div className="composer">
          <textarea aria-label="Message Tico" />
        </div>
      </>
    );
    render(cards({ tico }));
    const chat = screen.getByTestId('tico-chat');
    Object.defineProperty(chat, 'scrollHeight', { value: 900, configurable: true });
    let during: Element | null = null;
    const look = () => (during = document.activeElement);
    window.addEventListener('click', look);
    fireEvent.click(icon('Tico'));
    window.removeEventListener('click', look);
    const field = screen.getByRole('textbox', { name: 'Message Tico' });
    expect(during).toBe(field);
    expect(document.activeElement).toBe(field);
    expect(chat.scrollTop).toBe(900);
    expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy();
  });

  it('Escape puts a card away and the focus goes back to the bar', () => {
    render(cards());
    fireEvent.click(icon('Company'));
    const menu = screen.getByRole('dialog', { name: 'Menu' });
    expect(menu.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(icon('Company'));
  });
});

describe('the phone’s chat card', () => {
  it('a chat that opens comes up; one that ends goes, with its icon', () => {
    const { rerender } = render(cards());
    rerender(cards({ chat: <p>Owner's chat</p> }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
    rerender(cards());
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(icons()).toEqual(['Search', 'Tico', 'Company']);
  });

  it('a page’s own chat comes up when it waits on the person there, and stays up once that is answered', () => {
    const { rerender } = render(cards({ chat: <p>Owner's chat</p>, tucked: true, onLeave: () => {} }));
    expect(up()).toBe('none');
    rerender(cards({ chat: <p>Owner's chat</p>, tucked: true, onLeave: () => {}, raiseChat: true }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
    rerender(cards({ chat: <p>Owner's chat</p>, tucked: true, onLeave: () => {} }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
  });

  it('a page opened from the chat (an approval card) puts it away; a link marked to stay changes the card in place', () => {
    const chat = (
      <>
        <a href="/next/tasks/p1/12?chat=agent%3Agm&via=thread" onClick={(e) => e.preventDefault()}>
          Approve
        </a>
        <a href="/next/tasks/p1/12" data-stay onClick={(e) => e.preventDefault()}>
          Task chat
        </a>
      </>
    );
    render(cards({ chat }));
    fireEvent.click(screen.getByRole('link', { name: 'Task chat' }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Approve' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a place picked in the menu puts it away', () => {
    render(cards({ menu: <a href="/next/tasks" onClick={(e) => e.preventDefault()}>Tasks</a> }));
    fireEvent.click(icon('Company'));
    fireEvent.click(screen.getByRole('link', { name: 'Tasks' }));
    expect(up()).toBe('none');
  });

  it('an address that names a chat opens on its card, with the conversation in the card-high sheet', () => {
    render(
      cards({
        chat: (
          <Sheet kind="chat" label="Chat with Finance Owner">
            <div className="chat-body" role="log" />
          </Sheet>
        ),
      }),
    );
    const card = screen.getByRole('dialog', { name: 'Finance Owner' });
    // phone.css draws this sheet again in the card at the card's height (`.ly-chat>.pane`), so .chat-body scrolls
    // inside it; jsdom has no layout, so the structure that rule needs is what is checked here
    expect(card.querySelector(':scope > .pane.side > .chat-body')).toBeTruthy();
  });

  it('a chat opened by a navigation in the same press keeps its address', async () => {
    window.history.replaceState(null, '', '/tasks/eng');
    function Shell() {
      const r = useRouter();
      const chat = useLocation({ select: (l) => (l.search as Record<string, unknown>).chat });
      return cards({
        page: (
          <button type="button" onClick={() => void r.navigate({ to: '/tasks/eng' as never, search: { chat: 'agent:builder' } as never })}>
            Ask
          </button>
        ),
        chat: chat ? <p>Builder's chat</p> : undefined,
        labels: { chat: 'Birch Builder' },
      });
    }
    const router = createRouter({ routeTree: createRootRoute({ component: Shell }) });
    render(<RouterProvider router={router} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ask' }));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(router.state.location.search).toEqual({ chat: 'agent:builder' });
    expect(location.search).toBe('?chat=agent%3Abuilder');
    expect(screen.getByRole('dialog', { name: 'Birch Builder' })).toBeTruthy();
  });

  it('a card out of view takes no focus: it is inert until it comes up', () => {
    render(cards({ chat: <p>Owner's chat</p>, tucked: true }));
    for (const s of ['tico', 'menu', 'chat']) expect(document.querySelector(`.phone > .ly-${s}`)?.hasAttribute('inert')).toBe(true);
    fireEvent.click(icon('Finance Owner'));
    expect(document.querySelector('.phone > .ly-chat')?.hasAttribute('inert')).toBe(false);
    expect(document.querySelector('.phone > .pg')?.hasAttribute('inert')).toBe(true);
  });

  it('clears the deferred keyboard update if the phone layout unmounts after blur', () => {
    vi.useFakeTimers();
    try {
      const { unmount } = render(<PhoneCards page={<input aria-label="Filter" />} tico={null} menu={null} />);
      const input = screen.getByRole('textbox', { name: 'Filter' });
      const before = vi.getTimerCount();
      fireEvent.focusOut(input);
      expect(vi.getTimerCount()).toBeGreaterThan(before);
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
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
          <h1>{arr === 'task' ? 'Fix the export' : arr === 'call' ? 'Pricing review' : 'Tasks'}</h1>
        </Sheet>
      }
      chat={arr === 'task' || arr === 'chat' ? <Sheet kind="chat" label="Chat" /> : undefined}
      trail={[{ label: 'Fernhill', to: '/' }, { label: 'Tasks' }]}
      onLeave={() => {}}
    />
  );
}

describe('phone: the frame', () => {
  // #4820: a page the frame draws knows it is on a phone from its first render, before the html attribute is written
  it("gives the page the window's mode from its first render", () => {
    width(390);
    delete document.documentElement.dataset.layout;
    const seen: boolean[] = [];
    const Page = () => {
      seen.push(useCards());
      return null;
    };
    render(<Frame tico={<p>Tico</p>} menu={<p>Menu</p>} page={<Page />} arrangement="page" />);
    expect(seen[0]).toBe(true);
  });

  it('a task opened by its own address shows its head, its chat behind the Agent icon; no trail over it', async () => {
    onPhone(PhoneShell, '/tasks/eng/t1');
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(icons()).toEqual(['Search', 'Tico', 'Company', 'Agent', 'Task']);
    expect(current()).toEqual(['Task']);
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
    fireEvent.click(icon('Agent'));
    expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy();
  });

  it('a board has no Task icon, and opening a task from it keeps the chat down', async () => {
    const router = onPhone(PhoneShell, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Tasks' })).toBeTruthy();
    expect(icons()).toEqual(['Search', 'Tico', 'Company']);
    await act(() => router.navigate({ to: '/tasks/eng/t1' as never }));
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(current()).toEqual(['Task']);
  });

  it('an address that names the chat opens with it up, a task’s and a page’s alike', async () => {
    onPhone(PhoneShell, '/tasks/eng/t1?chat=agent:builder');
    expect(await screen.findByRole('dialog', { name: 'Agent' })).toBeTruthy();
    expect(current()).toEqual(['Agent']);
  });

  it('a page with a second chat in its address still opens on the chat (A1)', async () => {
    onPhone(PhoneShell, '/roadmap?chat=agent:builder');
    expect(await screen.findByRole('dialog', { name: 'Agent' })).toBeTruthy();
  });

  it('a call is the page: no card of its own and no trail', async () => {
    onPhone(PhoneShell, '/call/abc');
    const head = await screen.findByRole('heading', { name: 'Pricing review' });
    expect(head.closest('.phone > .pg')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(icons()).toEqual(['Search', 'Tico', 'Company']);
    expect(current()).toEqual(['Company']);
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
  });

  it('an agent’s own page opens on the page, its chat behind the Agent icon', async () => {
    onPhone(
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
    expect(icons()).toEqual(['Search', 'Tico', 'Company', 'Agent']);
  });

  it('the company’s mark is the Company icon, as it is the menu’s tucked edge on a computer', async () => {
    onPhone(
      () => <Frame arrangement="page" tico={<p>Tico</p>} menu={<nav aria-label="Main" />} page={<h1>Finance</h1>} marks={{ menu: <span className="co-mark">F</span> }} />,
      '/company/finance',
    );
    expect(within(await screen.findByRole('button', { name: 'Company' })).getByText('F')).toBeTruthy();
  });
});
