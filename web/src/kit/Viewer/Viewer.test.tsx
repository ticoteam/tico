import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { toast, Toasts } from '@/kit/Toast';
import { Viewer } from './Viewer';

describe('Viewer', () => {
  it('names itself, shows its controls and closes on Escape and Close', async () => {
    const onClose = vi.fn();
    render(
      <Viewer open onClose={onClose} title="Spring catalog" meta="Page" actions={<button type="button">Get</button>}>
        <p>Inside</p>
      </Viewer>,
    );
    const dialog = await screen.findByRole('dialog', { name: 'Spring catalog' });
    expect(screen.getByText('Page')).toBeTruthy();
    expect(screen.getByText('Inside')).toBeTruthy();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('on a phone a pull down its bar lets it go; a short one springs back', async () => {
    const onClose = vi.fn();
    render(<Viewer open onClose={onClose} title="Shot" />);
    const bar = (await screen.findByRole('dialog')).querySelector('.vw-bar')!;
    const pull = (dy: number) => {
      fireEvent.pointerDown(bar, { pointerType: 'touch', clientY: 100, pointerId: 1 });
      fireEvent.pointerMove(bar, { pointerType: 'touch', clientY: 100 + dy, pointerId: 1 });
      fireEvent.pointerUp(bar, { pointerType: 'touch', clientY: 100 + dy, pointerId: 1 });
    };
    pull(40);
    expect(onClose).not.toHaveBeenCalled();
    pull(160);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('holds the toasts while open, so an Undo is in reach inside it; closed, they go back to the page', async () => {
    const undo = vi.fn();
    const { rerender } = render(
      <>
        <Toasts />
        <Viewer open onClose={() => {}} title="Task" />
      </>,
    );
    const dialog = await screen.findByRole('dialog', { name: 'Task' });
    act(() => void toast({ text: 'Approved', action: { label: 'Undo', run: undo } }));
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Undo' }));
    expect(undo).toHaveBeenCalledTimes(1);
    act(() => void toast({ text: 'Moved', action: { label: 'Undo', run: undo } }));
    rerender(
      <>
        <Toasts />
        <Viewer open={false} onClose={() => {}} title="Task" />
      </>,
    );
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Task' })).toBeNull());
    expect(screen.getByRole('button', { name: 'Undo' }).closest('.vw')).toBeNull();
  });

  it('passes the Escape that closes it, for what else it may be for', async () => {
    const onClose = vi.fn();
    render(<Viewer open onClose={onClose} title="Task" />);
    fireEvent.keyDown(await screen.findByRole('dialog'), { key: 'Escape' });
    expect(onClose.mock.calls[0]?.[0]).toBeInstanceOf(KeyboardEvent);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose.mock.calls[1]?.[0]).toBeUndefined();
  });

  it('draws nothing while closed', () => {
    render(<Viewer open={false} onClose={() => {}} title="Shot" />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
