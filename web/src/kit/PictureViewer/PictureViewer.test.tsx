import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { PictureViewer, type Picture } from './PictureViewer';

const PICS: Picture[] = [
  { src: '/api/v2/files/a', name: 'Front.png' },
  { src: 'https://elsewhere.test/x.png', name: 'Outside.png' },
  { src: '/api/v2/files/b', name: 'Back.png', download: '/api/v2/files/b?download=1' },
];

function Harness({ start = 0 }: { start?: number | null }) {
  const [at, setAt] = useState<number | null>(start);
  return (
    <>
      <span data-testid="at">{String(at)}</span>
      <button type="button" onClick={() => setAt(0)}>
        Reopen
      </button>
      <PictureViewer pictures={PICS} at={at} onAtChange={setAt} />
    </>
  );
}

describe('PictureViewer', () => {
  it('shows only the app’s own pictures, walks them with the arrow keys and offers the download', async () => {
    render(<Harness />);
    const dialog = await screen.findByRole('dialog', { name: 'Front.png' });
    expect(screen.getByRole('img', { name: 'Front.png' }).getAttribute('src')).toBe('/api/v2/files/a');
    expect(screen.getByText('1 of 2')).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Outside.png' })).toBeNull();

    fireEvent.keyDown(dialog, { key: 'ArrowRight' });
    expect(await screen.findByRole('img', { name: 'Back.png' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/files/b?download=1');

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    expect(await screen.findByRole('img', { name: 'Front.png' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByRole('img', { name: 'Back.png' })).toBeTruthy();
  });

  it('closes on Escape', async () => {
    render(<Harness />);
    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.getByTestId('at').textContent).toBe('null');
  });

  it('zooms with its buttons, opens the next picture at fit, and swipes only at fit', async () => {
    render(<Harness />);
    const front = await screen.findByRole('img', { name: 'Front.png' });
    const stage = front.parentElement!;
    expect((screen.getByRole('button', { name: 'Zoom out' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(front.style.transform).toContain('scale(1.5)');

    // zoomed in, a swipe pans instead of walking the set
    const swipe = (el: Element, dx: number) => {
      fireEvent.pointerDown(el, { pointerId: 1, pointerType: 'touch', clientX: 200, clientY: 100 });
      fireEvent.pointerMove(el, { pointerId: 1, pointerType: 'touch', clientX: 200 + dx, clientY: 100 });
      fireEvent.pointerUp(el, { pointerId: 1, pointerType: 'touch', clientX: 200 + dx, clientY: 100 });
    };
    swipe(stage, -120);
    expect(screen.getByTestId('at').textContent).toBe('0');

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    const back = await screen.findByRole('img', { name: 'Back.png' });
    expect(back.style.transform).toBe('');

    swipe(back.parentElement!, -120);
    expect(screen.getByTestId('at').textContent).toBe('0');
    expect(await screen.findByRole('img', { name: 'Front.png' })).toBeTruthy();
  });

  it('opens a picture at fit again after another one, and after closing', async () => {
    render(<Harness />);
    const zoomIn = () => fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    zoomIn();
    expect((await screen.findByRole('img', { name: 'Front.png' })).style.transform).toContain('scale(1.5)');
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    await screen.findByRole('img', { name: 'Back.png' });
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowLeft' });
    expect((await screen.findByRole('img', { name: 'Front.png' })).style.transform).toBe('');
    zoomIn();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Reopen' }));
    expect((await screen.findByRole('img', { name: 'Front.png' })).style.transform).toBe('');
  });

  it('draws nothing while closed', () => {
    render(<Harness start={null} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
