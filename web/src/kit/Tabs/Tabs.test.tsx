import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Tabs } from './Tabs';

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'goals', label: 'Goals' },
  { value: 'finance', label: 'Finance', disabled: true },
] as const;

describe('Tabs', () => {
  it('shows the chosen tab and picks another', () => {
    const onValueChange = vi.fn();
    render(<Tabs label="Company" tabs={TABS} value="overview" onValueChange={onValueChange} strip />);
    const on = screen.getByRole('tab', { name: 'Overview' });
    expect(on.getAttribute('aria-selected')).toBe('true');
    expect(on.className).toContain('is-on');
    fireEvent.click(screen.getByRole('tab', { name: 'Goals' }));
    expect(onValueChange).toHaveBeenCalledWith('goals');
  });

  it('skips a disabled tab', () => {
    const onValueChange = vi.fn();
    render(<Tabs label="Company" tabs={TABS} value="overview" onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Finance' }));
    expect(onValueChange).not.toHaveBeenCalled();
  });
});
