import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { holdToasts, toast, Toasts, useTopLayer } from '@/kit/Toast';
import { Viewer } from './Viewer';

// Read with node's fs (tests load no CSS), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url;

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

  it('a layer opened from another holds the toasts and Cmd+K on top, as both open together and as the lower one draws again', async () => {
    const Top = () => <i data-testid="top" data-name={useTopLayer()?.closest('.vw')?.getAttribute('aria-label') ?? ''} />;
    // the file's layer as its own page draws it: the task's drawing again leaves it as it is
    const file = <Viewer open onClose={() => {}} title="Spec v2" />;
    const layers = (title: string) => (
      <>
        <Top />
        <Viewer open onClose={() => {}} title={title}>
          {file}
        </Viewer>
      </>
    );
    // a task's layer and a file opened from it, drawn in one go (a link to the file)
    const { rerender } = render(layers('Task'));
    await screen.findByRole('dialog', { name: 'Spec v2' });
    expect(screen.getByTestId('top').dataset.name).toBe('Spec v2');
    // the task's layer draws again (another version of the file picked), its host held anew: the file's stays on top
    rerender(layers('Task, renamed'));
    let again = () => {};
    act(() => void (again = holdToasts(screen.getByRole('dialog', { name: 'Task, renamed', hidden: true }).querySelector('.vw-toasts')!)));
    expect(screen.getByTestId('top').dataset.name).toBe('Spec v2');
    act(again);
  });

  it('a layer open over another hides the lower one’s bar, so one title shows; closed, the lower bar is back', async () => {
    const layers = (file: boolean) => (
      <Viewer open onClose={() => {}} title="Beach clean promo">
        <Viewer open={file} onClose={() => {}} title="beach-cut.mp4" />
      </Viewer>
    );
    const { rerender } = render(layers(true));
    await screen.findByRole('dialog', { name: 'beach-cut.mp4' });
    const task = () => screen.getByRole('dialog', { name: 'Beach clean promo', hidden: true });
    await waitFor(() => expect(task().hasAttribute('data-covered')).toBe(true));
    expect(screen.getByRole('dialog', { name: 'beach-cut.mp4' }).hasAttribute('data-covered')).toBe(false);
    // the style that hides it (tests load no CSS)
    const css = fs.readFileSync(new URL('./Viewer.css', here), 'utf8');
    expect(css).toContain('.vw[data-covered]>.vw-bar{visibility:hidden}');
    rerender(layers(false));
    await waitFor(() => expect(task().hasAttribute('data-covered')).toBe(false));
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
