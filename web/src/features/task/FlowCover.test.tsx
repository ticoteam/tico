import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlowCover } from './FlowCover';

afterEach(() => vi.useRealTimers());

describe('Getting ready to flow @s135', () => {
  it('covers a walk’s first task for a moment, and not the next one', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-19T10:00:00Z'));
    const first = render(<FlowCover />);
    expect(screen.getByText('Getting ready to flow')).toBeTruthy();
    act(() => void vi.advanceTimersByTime(700));
    expect(screen.queryByText('Getting ready to flow')).toBeNull();
    // Stepping to the next task: one goes, the next comes at once.
    first.unmount();
    render(<FlowCover />);
    expect(screen.queryByText('Getting ready to flow')).toBeNull();
  });
});
