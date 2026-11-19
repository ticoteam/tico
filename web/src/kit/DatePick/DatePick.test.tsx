import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DatePick } from './DatePick';

const NOV_24 = new Date(2026, 10, 24, 10, 15);

describe('DatePick', () => {
  it('opens on the chosen month, picks a day and a time, and sets them', async () => {
    const onValueChange = vi.fn();
    render(<DatePick label="Due" value={NOV_24} onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Due/ }));
    expect(await screen.findByText('November 2026')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Tuesday, November 24' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Friday, November 27' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Time' }), { target: { value: '2:30 pm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    expect(onValueChange).toHaveBeenCalledWith(new Date(2026, 10, 27, 14, 30));
  });

  it('marks a time it cannot read and keeps the card open', async () => {
    const onValueChange = vi.fn();
    render(<DatePick label="Due" value={NOV_24} onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Due/ }));
    const time = await screen.findByRole('textbox', { name: 'Time' });
    fireEvent.change(time, { target: { value: 'soon' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    expect(onValueChange).not.toHaveBeenCalled();
    expect(time.getAttribute('aria-invalid')).toBe('true');
  });

  it('moves between months and closes with Escape', async () => {
    render(<DatePick label="Due" value={NOV_24} onValueChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Due/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Next month' }));
    expect(screen.getByText('December 2026')).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Due' }), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('December 2026')).toBeNull());
  });

  it('does not open while disabled', () => {
    render(<DatePick label="Due" value={null} placeholder="No date" disabled onValueChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Due/ }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
