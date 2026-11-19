import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SkeletonRows } from './Skeleton';

describe('SkeletonRows', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows nothing for a fast read, then task-shaped rows', () => {
    render(<SkeletonRows rows={3} label="Loading tasks" />);
    expect(screen.queryByRole('status')).toBeNull();

    act(() => vi.advanceTimersByTime(150));
    const status = screen.getByRole('status', { name: 'Loading tasks' });
    expect(status.getAttribute('aria-busy')).toBe('true');
    const rows = status.querySelectorAll('.row.task.is-skeleton');
    expect(rows).toHaveLength(3);
    expect((rows[0]?.children[1] as HTMLElement).style.getPropertyValue('--w')).toBe('62%');
  });
});
