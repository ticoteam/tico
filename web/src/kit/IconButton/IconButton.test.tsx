import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { IconButton } from './IconButton';

describe('IconButton', () => {
  it('is named by its label and shows it on hover', () => {
    const onClick = vi.fn();
    render(<IconButton icon="more" label="More" size="sm" quiet onClick={onClick} />);
    const b = screen.getByRole('button', { name: 'More' });
    expect(b.getAttribute('title')).toBe('More');
    expect(b.className).toBe('ib ib-sm ib-quiet');
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('says when it is on', () => {
    render(<IconButton icon="filter" label="Filter" on />);
    const b = screen.getByRole('button', { name: 'Filter' });
    expect(b.getAttribute('aria-pressed')).toBe('true');
    expect(b.classList.contains('is-on')).toBe(true);
  });

  it('does nothing while disabled', () => {
    const onClick = vi.fn();
    render(<IconButton icon="close" label="Close" disabled onClick={onClick} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
