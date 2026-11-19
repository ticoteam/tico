import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OfflineBar } from './OfflineBar';

describe('OfflineBar', () => {
  it('says the app is offline and counts what is held', () => {
    render(<OfflineBar held={2} />);
    const note = screen.getByRole('status');
    expect(note.className).toBe('nav-note');
    expect(note.textContent).toBe('Offline2 changes are held and will send.');
  });

  it('uses the singular and says nothing more when nothing is held', () => {
    const { rerender } = render(<OfflineBar held={1} />);
    expect(screen.getByText('1 change is held and will send.')).toBeTruthy();
    rerender(<OfflineBar held={0} />);
    expect(screen.getByRole('status').textContent).toBe('Offline');
  });
});
