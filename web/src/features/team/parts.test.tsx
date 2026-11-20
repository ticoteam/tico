import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LONG_LIST, OwnRows } from './parts';
import type { OwnTask } from './queries';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The virtual list draws rows by measuring them, which jsdom cannot; its own tests cover that. Here: which one is used.
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: ({ items, label, onReachEnd }: { items: readonly unknown[]; label: string; onReachEnd?: () => void }) => (
    <div role="list" aria-label={label} data-rows={items.length}>
      <button type="button" onClick={onReachEnd}>
        End
      </button>
    </div>
  ),
}));

const tasks = (n: number): OwnTask[] =>
  Array.from({ length: n }, (_, i) => ({ id: `t${i}`, number: i + 1, title: `Sample task ${i}`, status: 'open', pipeline_id: 'p1', updated_at: '2026-11-01T10:00:00Z' }) as OwnTask);

describe("a teammate's task list", () => {
  it('draws every row up to 100', () => {
    render(<OwnRows tasks={tasks(LONG_LIST)} me={undefined} label="Open tasks" />);
    expect(screen.getByText('Sample task 0')).toBeTruthy();
    expect(screen.getByText(`Sample task ${LONG_LIST - 1}`)).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Open tasks' })).toBeNull();
  });

  it('past 100 rows hands them all to the virtual list, which reads the next page at its end', () => {
    const onEnd = vi.fn();
    render(<OwnRows tasks={tasks(400)} me={undefined} label="Open tasks" onEnd={onEnd} />);
    expect(screen.getByRole('list', { name: 'Open tasks' }).getAttribute('data-rows')).toBe('400');
    expect(screen.queryByText('Sample task 0')).toBeNull();
    screen.getByRole('button', { name: 'End' }).click();
    expect(onEnd).toHaveBeenCalledTimes(1);
  });
});
