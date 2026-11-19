import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Checkbox } from './Checkbox';

describe('Checkbox', () => {
  it('checks when its row is pressed', () => {
    const onCheckedChange = vi.fn();
    render(<Checkbox label="Email me" checked={false} onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByText('Email me'));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it('shows checked', () => {
    render(<Checkbox label="Email me" checked onCheckedChange={() => {}} />);
    expect(screen.getByRole('checkbox', { name: 'Email me' }).getAttribute('aria-checked')).toBe('true');
  });

  it('does nothing while disabled', () => {
    const onCheckedChange = vi.fn();
    render(<Checkbox label="Email me" checked={false} disabled onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Email me' }));
    expect(onCheckedChange).not.toHaveBeenCalled();
  });
});
