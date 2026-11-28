import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDrag, type DragOptions } from './Drag';

function Row({ options }: { options: DragOptions }) {
  const ref = useDrag(options);
  return (
    <>
      <div ref={ref} data-testid="row">
        Row
      </div>
      <div className="zone" data-testid="zone" />
    </>
  );
}

const data = { types: [], setData() {}, getData: () => '', setDragImage() {} };

describe('useDrag', () => {
  afterEach(() => vi.useRealTimers());

  it('a row that goes away mid-drag ends its drag, with a mouse or a finger', () => {
    const end = vi.fn();
    const drop = vi.fn();
    const mouse = render(<Row options={{ zones: '.zone', start: () => {}, end, drop }} />);
    fireEvent.pointerDown(mouse.getByTestId('row'), { button: 0, pointerType: 'mouse' });
    fireEvent.dragStart(mouse.getByTestId('row'), { dataTransfer: data });
    expect(mouse.getByTestId('row').classList.contains('is-drag')).toBe(true);
    mouse.unmount();
    expect(end).toHaveBeenCalledTimes(1);
    expect(drop).not.toHaveBeenCalled();

    end.mockClear();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const finger = render(<Row options={{ zones: '.zone', hold: 500, end, drop }} />);
    const row = finger.getByTestId('row');
    const touch = (type: 'pointerDown' | 'pointerMove', x: number, y: number) =>
      fireEvent[type](row, { pointerType: 'touch', pointerId: 1, isPrimary: true, button: 0, clientX: x, clientY: y });
    touch('pointerDown', 10, 10);
    vi.advanceTimersByTime(500);
    document.elementFromPoint = () => null;
    touch('pointerMove', 60, 60);
    expect(row.classList.contains('is-drag')).toBe(true);
    finger.unmount();
    expect(end).toHaveBeenCalledTimes(1);
    expect(drop).not.toHaveBeenCalled();
    expect(document.querySelector('.drag-lift')).toBeNull();
  });

  it("after a finger's drag, only that finger's click is held back: a tap on another row right after still lands", () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { getByTestId } = render(<Row options={{ zones: '.zone', hold: 500, drop: () => {} }} />);
    const row = getByTestId('row');
    const other = getByTestId('zone');
    document.elementFromPoint = () => null;
    const touch = (type: 'pointerDown' | 'pointerMove' | 'pointerUp', el: Element, x: number, y: number) =>
      fireEvent[type](el, { pointerType: 'touch', pointerId: 1, isPrimary: true, button: 0, clientX: x, clientY: y });
    const drag = () => {
      touch('pointerDown', row, 10, 10);
      vi.advanceTimersByTime(500);
      touch('pointerMove', row, 60, 60);
      touch('pointerUp', row, 60, 60);
    };

    // the lifted finger's own click: held back
    drag();
    expect(fireEvent.click(row)).toBe(false);
    // the drag made no click; the next tap, on another row, is the person's own
    drag();
    touch('pointerDown', other, 5, 5);
    touch('pointerUp', other, 5, 5);
    expect(fireEvent.click(other)).toBe(true);
    // and once the moment passes, a click is not held back either
    drag();
    vi.advanceTimersByTime(400);
    expect(fireEvent.click(other)).toBe(true);
  });

  it('a row that was not dragging ends nothing when it goes away', () => {
    const end = vi.fn();
    render(<Row options={{ zones: '.zone', end, drop: () => {} }} />).unmount();
    expect(end).not.toHaveBeenCalled();
  });
});
