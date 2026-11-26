import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useLocation, useRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Dock } from '@/kit/Dock';
import { useCards } from '@/kit/Popover';
import { forgetPlaces } from '@/lib';
import { arrangementFor } from '@/shell/arrangement';
import { Frame, foldSheet } from '@/shell/Frame';
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
  forgetPlaces();
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

  it('what the page docks (a task’s decision) stands above the bar, outside the page: in reach with the chat up', () => {
    render(
      cards({
        page: (
          <>
            <h1>Fix the export</h1>
            <Dock>
              <button type="button">Approve</button>
            </Dock>
          </>
        ),
        chat: <p>Owner's chat</p>,
        tucked: true,
        labels: {},
      }),
    );
    fireEvent.click(icon('Agent'));
    expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy();
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve.closest('.phone > .ph-dock')).toBeTruthy();
    expect(approve.closest('[inert]')).toBeNull();
    expect(document.querySelector('.phone > .pg')?.hasAttribute('inert')).toBe(true);
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
    let shown = false;
    const seen = () => (shown = !!document.activeElement?.closest('.ly-tico.is-up'));
    window.addEventListener('click', seen);
    fireEvent.click(icon('Tico'));
    window.removeEventListener('click', look);
    window.removeEventListener('click', seen);
    const field = screen.getByRole('textbox', { name: 'Message Tico' });
    expect(during).toBe(field);
    // the card is seen before the focus (a box in a hidden card takes none)
    expect(shown).toBe(true);
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

  it('is set again when its key (the page owner’s address) changes; a raise holds until then', () => {
    const at = (key: string) => cards({ chat: <p>Owner's chat</p>, tucked: true, chatKey: key });
    const { rerender } = render(at('/company'));
    expect(up()).toBe('none');
    fireEvent.click(icon('Finance Owner'));
    rerender(at('/company'));
    expect(up()).toBe('chat');
    rerender(at('/company/finance'));
    expect(up()).toBe('none');
  });

  it('a task opened from the chat (an approval card) opens over it, the card left up for its Close; a page puts it away', () => {
    const chat = (
      <>
        <a href="/next/tasks/p1/12?chat=agent%3Agm&via=thread" onClick={(e) => e.preventDefault()}>
          Approve
        </a>
        <a href="/next/people?task=12&list=p1" onClick={(e) => e.preventDefault()}>
          Over
        </a>
        <a href="/next/tasks/p1" data-stay onClick={(e) => e.preventDefault()}>
          Board
        </a>
        <a href="/next/people/ana" onClick={(e) => e.preventDefault()}>
          Ana
        </a>
      </>
    );
    render(cards({ chat }));
    for (const name of ['Approve', 'Over', 'Board']) {
      fireEvent.click(screen.getByRole('link', { name }));
      expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole('link', { name: 'Ana' }));
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

/* Typing in a chat folds the bar away from the focus alone: jsdom has no visualViewport, as an iPhone whose keyboard
   was never measured (#3562). The bar's `display:none` is phone.css's, keyed on `data-typing`. */
describe('phone: the bar while typing', () => {
  const typing = () => document.querySelector('.phone')?.getAttribute('data-typing') ?? null;

  it("Tico's box: the bar goes with no keyboard measured", () => {
    render(cards({ tico: <textarea aria-label="Message Tico" /> }));
    fireEvent.click(icon('Tico'));
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message Tico' }));
    expect(typing()).toBe('tico');
  });

  it("a bot's chat box: the bar goes", () => {
    render(cards({ chat: <textarea aria-label="Message Finance Owner" /> }));
    fireEvent.click(icon('Finance Owner'));
    act(() => screen.getByRole('textbox', { name: 'Message Finance Owner' }).focus());
    expect(typing()).toBe('chat');
  });

  it("a page's own chat box: the bar goes, and is back once the box loses focus", () => {
    vi.useFakeTimers();
    try {
      render(cards({ page: <div className="chat-page"><textarea aria-label="Message the page" /></div> }));
      const box = screen.getByRole('textbox', { name: 'Message the page' });
      act(() => box.focus());
      expect(typing()).toBe('page');
      act(() => box.blur());
      act(() => vi.advanceTimersByTime(100));
      expect(typing()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('a keyboard that closes while the box keeps focus brings the bar back; it goes again when the keys come up', () => {
    vi.useFakeTimers();
    // a visualViewport the test drives: the keyboard is the height it hides under the keys
    const vv = Object.assign(new EventTarget(), { height: 800, scale: 1, offsetTop: 0 });
    Object.defineProperty(window, 'visualViewport', { value: vv, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    const keyboard = (kb: number) =>
      act(() => {
        vv.height = 800 - kb;
        vv.dispatchEvent(new Event('resize'));
        vi.advanceTimersByTime(100);
      });
    try {
      render(cards({ tico: <textarea aria-label="Message Tico" /> }));
      fireEvent.click(icon('Tico'));
      keyboard(300);
      expect(typing()).toBe('tico');
      keyboard(0);
      expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message Tico' }));
      expect(typing()).toBeNull();
      keyboard(300);
      expect(typing()).toBe('tico');
      // the keys go down with the box kept, then the box is left and tapped again at once: a new focus, the bar goes
      keyboard(0);
      const box = screen.getByRole('textbox', { name: 'Message Tico' });
      act(() => {
        box.blur();
        box.focus();
      });
      expect(typing()).toBe('tico');
    } finally {
      delete (window as { visualViewport?: unknown }).visualViewport;
      vi.useRealTimers();
    }
  });

  it('an input with an empty or unknown type is a text box: the bar goes', () => {
    render(cards({ page: <input type="" aria-label="Reply" /> }));
    act(() => screen.getByRole('textbox', { name: 'Reply' }).focus());
    expect(typing()).toBe('page');
  });

  it('a checkbox or a select on the page keeps the bar', () => {
    render(
      cards({
        page: (
          <>
            <input type="checkbox" aria-label="Done" />
            <select aria-label="Stage">
              <option>Build</option>
            </select>
          </>
        ),
      }),
    );
    act(() => screen.getByRole('checkbox', { name: 'Done' }).focus());
    expect(typing()).toBeNull();
    act(() => screen.getByRole('combobox', { name: 'Stage' }).focus());
    expect(typing()).toBeNull();
  });
});

/* The shell as the root draws it on a phone: the arrangement from the address, a second chat when it names one. */
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
          <h1>{arr === 'call' ? 'Pricing review' : 'Tasks'}</h1>
        </Sheet>
      }
      chat={arr === 'chat' ? <Sheet kind="chat" label="Chat" /> : undefined}
      trail={[{ label: 'Fernhill', to: '/' }, { label: 'Tasks' }]}
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

  it('a fold of Tico (the first run, `shell.fold`) brings its card up and puts it down', () => {
    width(390);
    render(<Frame tico={<p>Tico</p>} menu={<p>Menu</p>} page={<h1>Tasks</h1>} arrangement="page" />);
    expect(up()).not.toBe('tico');
    act(() => foldSheet('tico', false));
    expect(up()).toBe('tico');
    act(() => foldSheet('tico', true));
    expect(up()).not.toBe('tico');
    // grown past 760 but inside the hysteresis: still the cards, so the fold is still the card's
    width(780);
    expect(document.querySelector('.phone')).toBeTruthy();
    act(() => foldSheet('tico', false));
    expect(up()).toBe('tico');
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
