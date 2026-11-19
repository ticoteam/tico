import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Combobox } from './Combobox';

/** A typed change: the combobox opens only on real typing (an input event with an inputType). */
function type(box: HTMLElement, text: string) {
  fireEvent.input(box, { target: { value: text }, inputType: 'insertText' });
}

const PEOPLE = [
  { value: 'a', label: 'Ana Ruiz' },
  { value: 'b', label: 'Ben Okafor', meta: 'Agent' },
  { value: 'c', label: 'Cal Moore', disabled: true },
];

describe('Combobox', () => {
  it('narrows as you type and picks one', async () => {
    const onValueChange = vi.fn();
    render(<Combobox label="Owner" items={PEOPLE} value={null} onValueChange={onValueChange} />);
    const box = screen.getByRole('combobox', { name: 'Owner' });
    box.focus();
    type(box, 'ben');
    const option = await screen.findByRole('option', { name: /Ben Okafor/ });
    expect(screen.queryByRole('option', { name: /Ana Ruiz/ })).toBeNull();
    fireEvent.click(option);
    expect(onValueChange).toHaveBeenCalledWith('b');
  });

  it('says when nothing matches and closes with Escape', async () => {
    render(<Combobox label="Owner" items={PEOPLE} value={null} onValueChange={() => {}} />);
    const box = screen.getByRole('combobox', { name: 'Owner' });
    box.focus();
    type(box, 'zz');
    expect(await screen.findByText('No matches')).toBeTruthy();
    fireEvent.keyDown(box, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('No matches')).toBeNull());
  });

  it('does not open while disabled', () => {
    render(<Combobox label="Owner" items={PEOPLE} value="a" disabled onValueChange={() => {}} />);
    const box = screen.getByRole('combobox', { name: 'Owner' }) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.value).toBe('Ana Ruiz');
  });
});
