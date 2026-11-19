import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Select } from './Select';

const TEAMS = [
  { value: 'product', label: 'Product' },
  { value: 'engineering', label: 'Engineering' },
  { value: 'growth', label: 'Growth', disabled: true },
];

describe('Select', () => {
  it('shows the chosen value, opens and picks another', async () => {
    const onValueChange = vi.fn();
    render(<Select label="Team" options={TEAMS} value="engineering" onValueChange={onValueChange} />);
    const trigger = screen.getByRole('combobox', { name: /Team/ });
    expect(trigger.textContent).toBe('Engineering');
    fireEvent.click(trigger);
    const option = await screen.findByRole('option', { name: 'Product' });
    fireEvent.pointerDown(option, { pointerType: 'mouse' });
    fireEvent.click(option);
    expect(onValueChange).toHaveBeenCalledWith('product');
  });

  it('shows the placeholder, and closes with Escape', async () => {
    render(<Select label="Pipeline" options={TEAMS} value={null} placeholder="Pipeline" onValueChange={() => {}} />);
    const trigger = screen.getByRole('combobox', { name: /Pipeline/ });
    expect(trigger.querySelector('.ph')?.textContent).toBe('Pipeline');
    fireEvent.click(trigger);
    const list = await screen.findByRole('listbox');
    fireEvent.keyDown(list, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it('does not open while disabled', () => {
    render(<Select label="Team" look="quiet" labelHidden options={TEAMS} value="product" disabled onValueChange={() => {}} />);
    fireEvent.click(screen.getByRole('combobox', { name: 'Team' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
