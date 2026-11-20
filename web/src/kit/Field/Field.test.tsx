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

  it('saves its words on Enter and when it is left', () => {
    const onCommit = vi.fn();
    render(<Field label="Limit" defaultValue="4" onCommit={onCommit} />);
    const box = screen.getByRole('textbox', { name: 'Limit' });
    fireEvent.change(box, { target: { value: '6' } });
    fireEvent.keyDown(box, { key: 'a' });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onCommit).toHaveBeenLastCalledWith('6');
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledTimes(2);
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

  it('marks the well while it holds words, so a page head keeps it open (its width only gives way when empty)', () => {
    render(<SearchHarness />);
    const box = screen.getByRole('textbox', { name: 'Search tasks' });
    const well = box.closest('.search');
    expect(well?.classList.contains('has-words')).toBe(false);
    fireEvent.change(box, { target: { value: 'launch' } });
    expect(well?.classList.contains('has-words')).toBe(true);
  });

  it('puts the cursor in the words when the magnifier is pressed (all a squeezed head search shows)', () => {
    render(<SearchHarness />);
    const box = screen.getByRole('textbox', { name: 'Search tasks' });
    const glass = box.closest('.search')?.querySelector('svg');
    if (!glass) throw new Error('no magnifier');
    fireEvent.pointerDown(glass);
    expect(document.activeElement).toBe(box);
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
