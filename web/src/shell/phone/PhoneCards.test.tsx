import { createRootRoute, createRouter, RouterProvider, useLocation, useRouter } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Sheet } from '@/shell/Sheet';
import { PhoneCards } from './PhoneCards';

vi.mock('@/tico/dot', () => ({ Dot: () => null }));

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
    expect(document.querySelector('.ph')?.getAttribute('data-card')).toBe('tico-half');
    window.history.back();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('a chat that opens comes up as its own card', () => {
    const { rerender } = render(cards());
    rerender(cards({ chat: true }));
    expect(screen.getByRole('dialog', { name: 'Finance Owner' })).toBeTruthy();
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
