import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SidePanel } from './SidePanel';

describe('SidePanel', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
    cleanup();
  });

  it('closes with its x on a computer, with no back arrow', () => {
    const onClose = vi.fn();
    render(<SidePanel title="Review" onClose={onClose}>Tasks</SidePanel>);
    const panel = screen.getByRole('complementary', { name: 'Review' });
    expect(within(panel).queryByRole('button', { name: 'Back' })).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('is a page pushed over the page on a phone: its back arrow closes it', () => {
    document.documentElement.dataset.layout = 'cards';
    const onClose = vi.fn();
    render(<SidePanel title="Review" onClose={onClose}>Tasks</SidePanel>);
    const panel = screen.getByRole('complementary', { name: 'Review' });
    const back = within(panel).getByRole('button', { name: 'Back' });
    // first in the head; the x is not drawn
    expect(panel.querySelector('.side-head')?.firstElementChild).toBe(back);
    expect(within(panel).queryByRole('button', { name: 'Close' })).toBeNull();
    fireEvent.click(back);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
