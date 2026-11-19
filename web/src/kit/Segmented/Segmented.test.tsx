import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Segmented } from './Segmented';

const RANGE = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
] as const;

describe('Segmented', () => {
  it('picks a choice and shows which is on', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" onValueChange={onValueChange} />);
    expect(screen.getByRole('group', { name: 'Range' }).className).toBe('seg is-words');
    expect(screen.getByRole('button', { name: 'Week' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    expect(onValueChange).toHaveBeenCalledWith('month');
  });

  it('never turns the chosen one off', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Week' }));
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it('names icon choices by their label', () => {
    render(
      <Segmented
        label="View"
        options={[{ value: 'list', label: 'List', icon: 'list' }, { value: 'columns', label: 'Columns', icon: 'columns' }]}
        value="list"
        onValueChange={() => {}}
      />,
    );
    expect(screen.getByRole('group', { name: 'View' }).className).toBe('seg');
    expect(screen.getByRole('button', { name: 'Columns' })).toBeTruthy();
  });

  it('does nothing while disabled', () => {
    const onValueChange = vi.fn();
    render(<Segmented label="Range" options={RANGE} value="week" disabled onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    expect(onValueChange).not.toHaveBeenCalled();
  });
});
