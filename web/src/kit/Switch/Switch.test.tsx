import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Switch } from './Switch';

describe('Switch', () => {
  it('turns on when pressed', () => {
    const onCheckedChange = vi.fn();
    render(<Switch label="Email me" checked={false} onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Email me' }));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it('shows on', () => {
    render(<Switch label="Email me" checked onCheckedChange={() => {}} />);
    const s = screen.getByRole('switch', { name: 'Email me' });
    expect(s.getAttribute('aria-checked')).toBe('true');
    expect(s.classList.contains('is-on')).toBe(true);
  });

  it('does nothing while disabled', () => {
    const onCheckedChange = vi.fn();
    render(<Switch label="Email me" checked={false} disabled onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Email me' }));
    expect(onCheckedChange).not.toHaveBeenCalled();
  });
});
