import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Toasts, toast } from './Toast';

describe('Toast', () => {
  it('shows one line and runs its Undo once', async () => {
    const undo = vi.fn();
    render(<Toasts />);
    act(() => {
      toast({ text: 'Approved', name: 'Spring launch', action: { label: 'Undo', run: undo } });
    });
    const text = await screen.findByText('Spring launch');
    expect(text.tagName).toBe('B');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(undo).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByText('Spring launch')).toBeNull());
  });

  it('is plain without an action', async () => {
    render(<Toasts />);
    act(() => {
      toast({ text: "Couldn't save", kind: 'failed' });
    });
    const line = await screen.findByText("Couldn't save");
    expect(line.closest('.toast')?.classList.contains('is-plain')).toBe(true);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
