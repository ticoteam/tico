import { QueryClientProvider } from '@tanstack/react-query';
import { render as draw, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { LONG_LIST, OwnRows } from './parts';
import type { OwnTask } from './queries';
import { queryClient } from '@/data';

// a row reads what Needs you names (its Dot)
const render = (node: ReactNode) => draw(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);

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
    render(<OwnRows tasks={tasks(LONG_LIST)} needs={undefined} label="Open tasks" />);
    expect(screen.getByText('Sample task 0')).toBeTruthy();
    expect(screen.getByText(`Sample task ${LONG_LIST - 1}`)).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Open tasks' })).toBeNull();
  });

  it('past 100 rows hands them all to the virtual list, which reads the next page at its end', () => {
    const onEnd = vi.fn();
    render(<OwnRows tasks={tasks(400)} needs={undefined} label="Open tasks" onEnd={onEnd} />);
    expect(screen.getByRole('list', { name: 'Open tasks' }).getAttribute('data-rows')).toBe('400');
    expect(screen.queryByText('Sample task 0')).toBeNull();
    screen.getByRole('button', { name: 'End' }).click();
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it('marks the rows Needs you names from the set the page read once, no row reading it again', () => {
    // no QueryClientProvider: a row that read Needs you itself would throw
    const { container } = draw(<OwnRows tasks={tasks(3)} needs={new Set(['t1'])} label="Open tasks" />);
    const rows = [...container.querySelectorAll('a')];
    expect(rows.map((r) => !!r.querySelector('.dot'))).toEqual([false, true, false]);
  });
});
