import { render, screen, within } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { VirtualList } from './VirtualList';

// jsdom lays nothing out: give every element a 340 x 400 box so the list has a window to fill.
const box = { clientHeight: 340, clientWidth: 400 };
const saved = Object.keys(box).map((k) => [k, Object.getOwnPropertyDescriptor(Element.prototype, k)] as const);
beforeAll(() => {
  for (const [k, n] of Object.entries(box)) Object.defineProperty(Element.prototype, k, { configurable: true, get: () => n });
});
afterAll(() => {
  for (const [k, d] of saved) if (d) Object.defineProperty(Element.prototype, k, d);
});

const items = Array.from({ length: 1000 }, (_, i) => ({ id: `t${i}`, title: `Task ${i}` }));

describe('VirtualList', () => {
  it('draws only the rows in view plus the overscan, keyed by id', () => {
    const onReachStart = vi.fn();
    render(
      <VirtualList
        items={items}
        getKey={(t) => t.id}
        estimateSize={34}
        label="Tasks"
        onReachStart={onReachStart}
        renderItem={(t) => <span>{t.title}</span>}
      />,
    );
    const rows = within(screen.getByRole('list', { name: 'Tasks' })).getAllByRole('listitem');
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(30);
    expect(rows[0]?.textContent).toBe('Task 0');
    expect(screen.queryByText('Task 999')).toBeNull();
    expect(onReachStart).toHaveBeenCalled();
  });
});
