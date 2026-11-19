import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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
  it('rests with the page behind and a lip for Tico, the menu and an open chat', () => {
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
});
