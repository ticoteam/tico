import { fireEvent, render, screen } from '@testing-library/react';
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

  it('draws nothing while closed', () => {
    render(<Harness start={null} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
