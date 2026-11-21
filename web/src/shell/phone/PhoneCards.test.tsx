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

  it("the bar: Tico's box and search side by side; the box raises Tico at its latest, the cursor already in its box; Back closes it", async () => {
    const search = vi.fn();
    const tico = (
      <>
        <div className="chat-body" data-testid="tico-chat" />
        <div className="composer">
          <textarea aria-label="Message Tico" />
        </div>
      </>
    );
    render(<PhoneCards page={<h1>Finance</h1>} tico={tico} menu={null} lipActs={{ tico: <button type="button" onClick={search}>Search</button> }} />);
    const box = screen.getByRole('button', { name: 'Open Tico' });
    expect(box.textContent).toBe('Tico');
    // search stands beside the box, not inside its tap target
    const find = screen.getAllByRole('button', { name: 'Search' }).find((b) => b.closest('.ly-tico'))!;
    expect(box.contains(find)).toBe(false);
    fireEvent.click(find);
    expect(search).toHaveBeenCalledOnce();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('none');

    // iOS raises the keyboard only for a focus inside the tap's own handler: the box has the cursor before the tap is over
    const chat = screen.getByTestId('tico-chat');
    Object.defineProperty(chat, 'scrollHeight', { value: 900, configurable: true });
    let during: Element | null = null;
    const look = () => (during = document.activeElement);
    window.addEventListener('click', look);
    fireEvent.click(box);
    window.removeEventListener('click', look);
    const field = screen.getByRole('textbox', { name: 'Message Tico' });
    expect(during).toBe(field);
    expect(document.activeElement).toBe(field);
    expect(chat.scrollTop).toBe(900);
    expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('tico');
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

  it("a page opened from Tico's card (a tracked task) tucks Tico, and Back brings it up again", async () => {
    render(
      <PhoneCards
        page={<h1>Finance</h1>}
        tico={<a href="/next/tasks/p1/12" onClick={(e) => e.preventDefault()}>Draft the strategy</a>}
        menu={null}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Open Tico' }));
    expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Draft the strategy' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    // Tico's entry is still there: Back (the page's own) lands on it, with Tico up
    window.history.pushState({}, '');
    window.history.back();
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Tico' })).toBeTruthy());
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

  it('an open chat’s lip can be focused, and still can with the menu up: it peeks above it', () => {
    render(cards({ chat: true }));
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Finance Owner' }), { key: 'Escape' });
    expect(chatLip()?.closest('[inert]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open Menu' }));
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeTruthy();
    expect(chatLip()?.closest('[inert]')).toBeNull();
    expect(chatLip()?.closest('.ly')?.classList.contains('is-behind')).toBe(true);
  });
});

describe('phone: the cards behind a raised one peek above it', () => {
  const phone = () => document.querySelector<HTMLElement>('.phone')!;
  const ly = (s: string) => document.querySelector<HTMLElement>(`.phone > .ly-${s}`)!;

  it('with Tico up, the menu and an open chat stay in the stack behind it, in order, and a tap on one brings it forward', () => {
    render(cards({ chat: true }));
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Finance Owner' }), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Open Tico' }));
    expect(phone().dataset.card).toBe('tico'); // the bar's box: Tico all the way up, ready to type
    for (const s of ['menu', 'chat']) {
      expect(ly(s).classList.contains('is-behind')).toBe(true);
      expect(ly(s).closest('[inert]')).toBeNull();
      // a peek is a tap target, not a way into the card: what is inside stays out of reach until it comes forward
      expect(ly(s).querySelector(':scope > .full')?.hasAttribute('inert')).toBe(true);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Open Menu' }));
    expect(phone().dataset.card).toBe('menu');
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeTruthy();
    expect(ly('tico').classList.contains('is-behind')).toBe(false); // Tico is in front of the menu: its lip, not a peek
    expect(ly('chat').classList.contains('is-behind')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Open Finance Owner' }));
    expect(phone().dataset.card).toBe('chat');
    expect(document.querySelectorAll('.phone > .ly.is-behind')).toHaveLength(0); // the chat is the back card
  });

  it('with no chat open, only the menu peeks behind Tico', () => {
    render(cards());
    fireEvent.click(screen.getByRole('button', { name: 'Open Tico' }));
    expect(ly('menu').classList.contains('is-behind')).toBe(true);
    expect(ly('chat').classList.contains('is-behind')).toBe(false);
    expect(ly('chat').hasAttribute('inert')).toBe(true);
  });

  it('a page that holds the bottom (a handle) still shows the peeks once Tico is raised from it', () => {
    render(<PhoneCards page={<h1>Fix the export</h1>} tico={<p>Tico's chat</p>} menu={<p>Menu</p>} handle />);
    expect(ly('menu').hasAttribute('inert')).toBe(true);
    fireEvent.click(document.querySelector<HTMLElement>('.phone > .nub > .nub-tico')!);
    expect(phone().dataset.card).toBe('tico');
    expect(ly('menu').classList.contains('is-behind')).toBe(true);
    expect(ly('menu').hasAttribute('inert')).toBe(false);
  });

  // jsdom lays nothing out, so the geometry is held by its rules
  it('a peek shows 20-50% of the company mark, and the lips a scroll draws down stop at the same peek', () => {
    const peek = Number(/--peek:(\d+)px/.exec(rule('.phone'))?.[1]);
    // the menu's lip is 40px with its 20px mark in the middle: the mark starts 10px down
    const shown = (peek - 10) / 20;
    expect(shown).toBeGreaterThanOrEqual(0.2);
    expect(shown).toBeLessThanOrEqual(0.5);
    expect(rule('.phone')).toContain('--drawM:calc((var(--lipM) - var(--peek)) * var(--c))');
    // and the mark stays while the words fade
    expect(phoneCss).toContain('.phone>.ly-menu>.lip>:not(.co-mark)');
  });

  it('a raised card stands below the peeks behind it, and its content still ends at the bottom of the screen', () => {
    expect(rule('.phone.has-chat')).toContain('--behind:calc(var(--peek) * 2)');
    expect(rule('.phone[data-card="tico"]')).toContain('--at:var(--behind)');
    expect(rule('.phone[data-card^="tico"]>.ly-tico')).toContain('--ty:var(--at)');
    expect(rule('.phone>.ly-menu.is-behind')).toContain('--ty:calc(var(--at) - var(--peek))');
    expect(rule('.phone[data-card^="tico"]>.ly-chat.is-behind')).toContain('--ty:calc(var(--at) - var(--peek) * 2)');
    expect(rule('.phone[data-card="tico"] .ly-tico>.full')).toContain('--vis:calc(100% - var(--at))');
    expect(rule('.phone[data-card="menu"] .ly-menu>.full')).toContain('height:calc(100% - var(--at))');
    // moves are transform only
    expect(rule('.phone>.ly')).toContain('transition:transform var(--card-dur) var(--card-ease)');
  });

  it('while typing, the cards in front go under the keyboard and the peeks behind stay, clear of the box', () => {
    expect(rule('.phone[data-typing]>.ly:not(.is-up):not(.is-behind)')).toContain('--ty:calc(100% + 48px)');
    expect(rule('.phone[data-typing="tico"][data-card="tico"]>.ly-tico>.full')).toContain('--vis:calc(100% - var(--at) - var(--kb))');
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

  it('a call that takes the screen ends above Tico’s edge: nothing of Tico sits on its controls', () => {
    render(
      <PhoneCards
        page={null}
        tico={<p>Tico's chat</p>}
        menu={<a href="/next/tasks">Tasks</a>}
        call={
          <footer className="win-ctl">
            <button type="button">Mute</button>
            <button type="button">Leave</button>
          </footer>
        }
      />,
    );
    const phone = document.querySelector<HTMLElement>('.phone')!;
    expect(phone.classList.contains('is-call') && phone.classList.contains('is-handle')).toBe(true);
    // the call is inside the card that ends at the edge; the handle is the stack's, outside it
    expect(screen.getByRole('button', { name: 'Leave' }).closest('.ly-chat > .full')).toBeTruthy();
    expect(document.querySelector('.phone > .nub')?.closest('.ly')).toBeNull();
    expect(rule('.phone.is-handle>.ly-chat>.full')).toContain('height:calc(100% - var(--edgeT))');
    // and no rule for a call gives its card the full height back or lifts the handle into it
    for (const m of phoneCss.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1]!.includes('is-call') && /(>\.full|\.nub)/.test(m[1]!)) expect(m[2]).not.toMatch(/height|bottom/);
  });

  it('with a chat up, the bar is the stack’s, outside the chat card: Tico’s box opens Tico, search is beside it', () => {
    const search = vi.fn();
    render(
      <PhoneCards
        page={<h1>Finance</h1>}
        tico={<textarea aria-label="Message Tico" />}
        menu={null}
        chat={<p>Owner's chat</p>}
        lipActs={{ tico: <button type="button" onClick={search}>Search</button> }}
      />,
    );
    const nub = document.querySelector<HTMLElement>('.phone > .nub');
    expect(nub?.closest('.ly')).toBeNull();
    expect(nub?.hasAttribute('inert')).toBe(false);
    expect(document.querySelector<HTMLElement>('.phone')?.style.getPropertyValue('--dock-h')).toBe('');
    fireEvent.click(within(nub!).getByRole('button', { name: 'Search' }));
    expect(search).toHaveBeenCalledOnce();
    fireEvent.click(within(nub!).getByRole('button', { name: 'Open Tico' }));
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('tico');
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message Tico' }));
  });
});
