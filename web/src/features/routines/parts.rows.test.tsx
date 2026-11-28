import { render } from '@testing-library/react';
import { forwardRef, useImperativeHandle } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Rows } from './parts';

const scrollToKey = vi.fn(() => true);
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: forwardRef(function VirtualList(_: unknown, ref) {
    useImperativeHandle(ref, () => ({ scrollToKey }));
    return <div data-virtual />;
  }),
}));

const items = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `m${i}` }));
const draw = (m: { id: string }) => <span key={m.id}>{m.id}</span>;

describe('Rows', () => {
  it('a long list is brought to the row it names', () => {
    render(<Rows label="Earlier" items={items(150)} getKey={(m) => m.id} draw={draw} at="m130" />);
    expect(scrollToKey).toHaveBeenCalledWith('m130');
  });

  it('a short list, or one without that row, is not scrolled', () => {
    scrollToKey.mockClear();
    render(<Rows label="Earlier" items={items(150)} getKey={(m) => m.id} draw={draw} at="m999" />);
    render(<Rows label="Today" items={items(20)} getKey={(m) => m.id} draw={draw} at="m5" />);
    expect(scrollToKey).not.toHaveBeenCalled();
  });
});
