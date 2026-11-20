import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useLocation, useRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { arrangementFor } from '@/shell/arrangement';
import { Frame } from '@/shell/Frame';
import { Sheet } from '@/shell/Sheet';
import { MenuLip } from './MenuLip';
import { PhoneCards } from './PhoneCards';

vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));

// Read with node's fs (tests load no CSS, not even ?raw), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const phoneCss = fs.readFileSync(new URL('./phone.css', here), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
/** The declarations of the rule whose selector list includes `selector` exactly. */
const rule = (selector: string): string => {
  for (const m of phoneCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1]!.split(',').some((s) => s.trim() === selector)) return m[2]!.replace(/\s+/g, ' ');
  }
  return '';
};

const width = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });
afterEach(() => {
  queryClient.clear();
  width(1440);
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

const cards = (extra: { chat?: boolean } = {}) => (
  <PhoneCards
    page={<h1>Finance</h1>}
    tico={<p>Tico's chat</p>}
    menu={<a href="/next/tasks">Tasks</a>}
    chat={extra.chat ? <p>Owner's chat</p> : undefined}
    labels={{ chat: 'Finance Owner' }}
  />
);

describe('PhoneCards', () => {
  it('rests with the page behind and a lip for Tico, the menu and an open chat @s1191', () => {
    render(cards());
    expect(screen.getByRole('heading', { name: 'Finance' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open Tico' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByRole('button', { name: 'Open Menu' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a lip opens its card, Escape tucks it and focus goes back to the lip', () => {
    render(cards());
    const lip = screen.getByRole('button', { name: 'Open Menu' });
    lip.focus();
    fireEvent.click(lip);
    const menu = screen.getByRole('dialog', { name: 'Menu' });
    expect(menu.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(lip);
  });

  it("Tico's lip raises it to half; Back closes it", async () => {
    render(cards());
    fireEvent.click(screen.getByRole('button', { name: 'Open Tico' }));
    expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('tico-half');
    window.history.back();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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

  it('a chat that opens comes up as its own card', () => {
    const { rerender } = render(cards());
    rerender(cards({ chat: true }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
  });

  it('a page opened from the chat (an approval card) tucks it and comes forward (plan/layers.html)', () => {
    render(
      <PhoneCards
        page={<h1>Finance</h1>}
        tico={<p>Tico's chat</p>}
        menu={<p>Menu</p>}
        chat={<a href="/next/tasks/p1/12?chat=agent%3Agm&via=thread" onClick={(e) => e.preventDefault()}>Approve</a>}
        labels={{ chat: 'Finance Owner' }}
      />,
    );
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Approve' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('none');
  });

  it('an address that names a chat opens on its card, with the conversation in the card-high sheet', () => {
    render(
      <PhoneCards
        page={<h1>Finance</h1>}
        tico={<p>Tico's chat</p>}
        menu={<a href="/next/tasks">Tasks</a>}
        chat={
          <Sheet kind="chat" label="Chat with Finance Owner">
            <div className="chat-body" role="log" />
          </Sheet>
        }
        labels={{ chat: 'Finance Owner' }}
      />,
    );
    const card = screen.getByRole('dialog', { name: 'Finance Owner' });
    // phone.css draws this sheet again in the card at the card's height (`.ly-chat>.full>.pane`), so .chat-body
    // scrolls inside it; jsdom has no layout, so the structure that rule needs is what is checked here
    expect(card.querySelector(':scope > .full > .pane.side > .chat-body')).toBeTruthy();
    expect(window.history.state?.['tico-next:card']).toBe('chat');
  });

  it('a chat opened by a navigation in the same press keeps its address (the router writes it before the card does)', async () => {
    window.history.replaceState(null, '', '/tasks/eng');
    function Shell() {
      const r = useRouter();
      const chat = useLocation({ select: (l) => (l.search as Record<string, unknown>).chat });
      return (
        <PhoneCards
          page={
            <button type="button" onClick={() => void r.navigate({ to: '/tasks/eng' as never, search: { chat: 'agent:builder' } as never })}>
              Ask
            </button>
          }
          tico={<p>Tico's chat</p>}
          menu={<p>Menu</p>}
          chat={chat ? <p>Builder's chat</p> : undefined}
          labels={{ chat: 'Birch Builder' }}
        />
      );
    }
    const router = createRouter({ routeTree: createRootRoute({ component: Shell }) });
    render(<RouterProvider router={router} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ask' }));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(router.state.location.search).toEqual({ chat: 'agent:builder' });
    expect(location.search).toBe('?chat=agent%3Abuilder');
    expect(screen.getByRole('dialog', { name: 'Birch Builder' })).toBeTruthy();
  });
});

describe('the lips drawn down by a scroll', () => {
  const scroller = (height: number) => {
    const el = screen.getByTestId('scroller');
    Object.defineProperty(el, 'scrollHeight', { value: height, configurable: true });
    Object.defineProperty(el, 'clientHeight', { value: 700, configurable: true });
    return (y: number) => {
      el.scrollTop = y;
      fireEvent.scroll(el);
    };
  };
  const drawn = () => document.querySelector<HTMLElement>('.phone')!.style.getPropertyValue('--c');

  it("come back at the page's end, so a thread opened at its newest message keeps the menu's lip whole", async () => {
    render(<PhoneCards page={<div data-testid="scroller" />} tico={<p>Tico's chat</p>} menu={<p>Menu</p>} />);
    const to = scroller(2000);
    to(100);
    to(200);
    await waitFor(() => expect(drawn()).toBe('1'));
    to(1300);
    await waitFor(() => expect(drawn()).toBe('0'));
  });

  it('come back on another page, which may be too short to scroll them back', async () => {
    window.history.replaceState(null, '', '/threads');
    function Shell() {
      const r = useRouter();
      return (
        <PhoneCards
          page={
            <div data-testid="scroller">
              <button type="button" onClick={() => void r.navigate({ to: '/threads/t1' as never })}>
                Open thread
              </button>
            </div>
          }
          tico={<p>Tico's chat</p>}
          menu={<p>Menu</p>}
        />
      );
    }
    render(<RouterProvider router={createRouter({ routeTree: createRootRoute({ component: Shell }) })} />);
    await screen.findByRole('button', { name: 'Open thread' });
    const to = scroller(5000);
    to(100);
    to(200);
    await waitFor(() => expect(drawn()).toBe('1'));
    fireEvent.click(screen.getByRole('button', { name: 'Open thread' }));
    await waitFor(() => expect(drawn()).toBe('0'));
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
    onPhone(PhoneShell, '/tasks/eng/t1');
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('none');
    const lip = screen.getByRole('button', { name: 'Open Chat' });
    expect(lip.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(lip);
    expect(screen.getByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('opening a task from its list keeps the chat tucked', async () => {
    const router = onPhone(PhoneShell, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Tasks' })).toBeTruthy();
    await act(() => router.navigate({ to: '/tasks/eng/t1' as never }));
    expect(await screen.findByRole('heading', { name: 'Fix the export' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('an address that names the chat opens with it raised', async () => {
    onPhone(PhoneShell, '/tasks/eng/t1?chat=task');
    expect(await screen.findByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('a page with a second chat in its address still opens on the chat (A1)', async () => {
    onPhone(PhoneShell, '/roadmap?chat=agent:builder');
    expect(await screen.findByRole('dialog', { name: 'Chat' })).toBeTruthy();
  });

  it('an agent’s own page opens on the page, its chat a lip', async () => {
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
    expect(screen.getByRole('button', { name: 'Open Chat' }).closest('[inert]')).toBeNull();
  });
});

describe('phone: a card out of view takes no focus', () => {
  const chatLip = () => document.querySelector<HTMLElement>('.ly-chat > .lip');

  it('with no chat open, the chat card and its lip are inert', () => {
    render(cards());
    expect(chatLip()?.closest('[inert]')?.classList.contains('ly-chat')).toBe(true);
  });

  it('an open chat’s lip can be focused, until the menu comes up over it', () => {
    render(cards({ chat: true }));
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Finance Owner' }), { key: 'Escape' });
    expect(chatLip()?.closest('[inert]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open Menu' }));
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeTruthy();
    expect(chatLip()?.closest('[inert]')).not.toBeNull();
  });
});

describe("The phone's menu lip @s1191", () => {
  const withLip = (lip: ReactNode) =>
    onPhone(
      () => (
        <Frame
          arrangement="page"
          tico={<p>Tico</p>}
          menu={<nav aria-label="Main" />}
          page={
            <Sheet kind="page" label="Finance">
              <h1>Finance</h1>
            </Sheet>
          }
          lips={{ menu: lip }}
        />
      ),
      '/company/finance',
    );

  it('shows the company, the page behind and what waits on you in Flow, passed through the frame', async () => {
    withLip(<MenuLip company="Fernhill" flow={4} />);
    const lip = await screen.findByRole('button', { name: 'Open Menu' });
    expect(within(lip).getByText('Fernhill')).toBeTruthy();
    expect(within(lip).getByText('F')).toBeTruthy();
    expect(await within(lip).findByText('Finance')).toBeTruthy();
    expect(within(lip).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(within(lip).getByText('4')).toBeTruthy();
  });

  it('leaves the count out when nothing waits on you', async () => {
    withLip(<MenuLip company="Fernhill" />);
    const lip = await screen.findByRole('button', { name: 'Open Menu' });
    expect(within(lip).getByText('Fernhill')).toBeTruthy();
    expect(within(lip).queryByRole('img', { name: 'Waits on you' })).toBeNull();
  });
});

describe("phone: Tico's Dot never sits on a field", () => {
  // jsdom lays nothing out, so this holds the geometry by its rules: the handle and whatever holds the bottom share one
  // line (--edgeT), the handle below it and the box above it.
  it('the handle is Tico drawn in to its edge at the bottom of the stack, under the message box', () => {
    const nub = rule('.phone>.nub');
    expect(nub).toMatch(/bottom:0[;}]?/);
    expect(nub).toContain('height:var(--edgeT)');
    expect(phoneCss).not.toContain('--dock-h');
    // a raised chat and a page that holds the bottom both end where the edge starts
    expect(rule('.phone.is-handle>.ly-chat>.full')).toContain('height:calc(100% - var(--edgeT))');
    expect(rule('.phone.is-handle>.pg')).toContain('bottom:var(--edgeT)');
    // and while typing it goes, with the stack, under the keyboard
    expect(rule('.phone[data-typing]>.nub')).toContain('opacity:0');
  });

  it("a page's own message box (a thread) clears the lips instead of resting under Tico's", () => {
    expect(rule('.phone>.pg .chat-page>.chat-foot')).toContain('padding:6px 10px calc(var(--cards-h) + 10px)');
  });

  it('with a chat up, the handle is the stack’s, outside the chat card, and opens Tico', () => {
    render(cards({ chat: true }));
    const nub = document.querySelector<HTMLElement>('.phone > .nub');
    expect(nub?.closest('.ly')).toBeNull();
    expect(nub?.hasAttribute('inert')).toBe(false);
    expect(document.querySelector<HTMLElement>('.phone')?.style.getPropertyValue('--dock-h')).toBe('');
    fireEvent.click(nub!);
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('tico-half');
  });
});
