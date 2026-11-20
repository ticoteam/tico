import { fireEvent } from '@testing-library/react';

/** A drag of `from` let go over `to` (or nowhere): a press (on `press`, inside `from`), then the browser's drag, which
    jsdom lacks, so its DataTransfer is a stand-in. */
export function dragTo(from: Element, to: Element | null, { press = from, pointerType = 'mouse' }: { press?: Element; pointerType?: string } = {}) {
  const dataTransfer = { types: [], setData() {}, getData: () => '', setDragImage() {} };
  fireEvent.pointerDown(press, { button: 0, pointerType });
  fireEvent.dragStart(from, { dataTransfer });
  if (!to) return void fireEvent.dragEnd(from, { dataTransfer });
  fireEvent.dragEnter(to, { dataTransfer });
  fireEvent.dragOver(to, { dataTransfer });
  fireEvent.drop(to, { dataTransfer });
}
