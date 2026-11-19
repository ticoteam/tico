import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Field, Search, SettledSearch } from './Field';

describe('Field', () => {
  it('is named by its label and reports what is typed', () => {
    const onValueChange = vi.fn();
    render(<Field label="Name" defaultValue="" onValueChange={onValueChange} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Launch plan' } });
    expect(onValueChange).toHaveBeenCalledWith('Launch plan');
  });

  it('shows a short error and marks the field invalid', () => {
    render(<Field label="Email" defaultValue="x" error="Not an email" />);
    expect(screen.getByRole('textbox', { name: 'Email' }).getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Not an email')).toBeTruthy();
  });

  it('is several lines with area, and can be disabled', () => {
    render(<Field label="Note" area disabled defaultValue="" />);
    const box = screen.getByRole('textbox', { name: 'Note' });
    expect(box.tagName).toBe('TEXTAREA');
    expect((box as HTMLTextAreaElement).disabled).toBe(true);
  });
});

function SearchHarness() {
  const [q, setQ] = useState('');
  return <Search label="Search tasks" value={q} onValueChange={setQ} placeholder="Search" />;
}

describe('Search', () => {
  it('clears with its button and with Escape', () => {
    render(<SearchHarness />);
    const box = screen.getByRole('textbox', { name: 'Search tasks' }) as HTMLInputElement;
    fireEvent.change(box, { target: { value: 'launch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(box.value).toBe('');
    fireEvent.change(box, { target: { value: 'launch' } });
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(box.value).toBe('');
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull();
  });
});

describe('SettledSearch', () => {
  it('hands on the words once typing pauses, and follows the address when it changes elsewhere', () => {
    vi.useFakeTimers();
    const onSettle = vi.fn();
    const { rerender } = render(<SettledSearch label="Find a thread" value="" onSettle={onSettle} />);
    const box = screen.getByRole('textbox', { name: 'Find a thread' }) as HTMLInputElement;
    fireEvent.change(box, { target: { value: 'lau' } });
    fireEvent.change(box, { target: { value: 'launch ' } });
    act(() => vi.advanceTimersByTime(249));
    expect(onSettle).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onSettle).toHaveBeenCalledOnce();
    expect(onSettle).toHaveBeenCalledWith('launch');
    rerender(<SettledSearch label="Find a thread" value="budget" onSettle={onSettle} />);
    expect(box.value).toBe('budget');
    act(() => vi.advanceTimersByTime(500));
    expect(onSettle).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
});
