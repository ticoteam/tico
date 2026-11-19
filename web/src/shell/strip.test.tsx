import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Frame, foldSheet } from './Frame';
import { Sheet } from './Sheet';

/* Tico's folded strip: the whole column is one button that opens Tico, and a fold the person asks for runs fast. */
const level = vi.hoisted(() => ({ now: 'off' as 'off' | 'more' }));
vi.mock('@/motion', () => ({ motionLevel: () => level.now }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dw" aria-hidden="true" /> }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/shell/phone', () => ({ PhoneCards: () => <div>cards</div> }));

const resize = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });
const slots = {
  tico: <Sheet kind="tico" label="Tico" />,
  menu: <nav className="pane nav" aria-label="Main" />,
  page: <Sheet kind="page" label="Tasks" />,
};
const ticoWidth = () => screen.getByRole('separator', { name: 'Resize Tico' }).getAttribute('aria-valuenow');

beforeEach(() => {
  localStorage.clear();
  level.now = 'off';
  resize(1440);
});
afterEach(() => {
  delete (Element.prototype as { animate?: unknown }).animate;
});

describe('Tico folded', () => {
  it('is one button, Open Tico, with the Dot drawn inside it; pressing anywhere on it opens Tico', () => {
    render(<Frame {...slots} arrangement="page" />);
    act(() => foldSheet('tico', true));
    expect(ticoWidth()).toBe('56');

    const strip = screen.getByRole('button', { name: 'Open Tico' });
    expect(strip.tagName).toBe('BUTTON'); // a native button: Enter and Space press it
    expect(screen.getAllByRole('button', { name: 'Open Tico' })).toHaveLength(1);
    expect(within(strip).queryAllByRole('button')).toHaveLength(0); // nothing pressable of its own inside
    const dot = strip.querySelector('.dw');
    expect(dot?.getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByRole('complementary', { name: 'Tico' }).contains(strip)).toBe(true);

    fireEvent.click(dot as Element); // a press on the Dot, or anywhere in the column, opens it
    expect(ticoWidth()).toBe('300');
    expect(screen.queryByRole('button', { name: 'Open Tico' })).toBeNull();
  });

  it('folds and opens about three times faster than a change of arrangement (200ms, not 620)', () => {
    level.now = 'more';
    const calls: { keys: Keyframe[]; opts: KeyframeAnimationOptions }[] = [];
    Element.prototype.animate = function (keys: Keyframe[], opts: KeyframeAnimationOptions) {
      calls.push({ keys, opts });
      return { finished: Promise.resolve() } as unknown as Animation;
    } as typeof Element.prototype.animate;

    render(<Frame {...slots} arrangement="page" />);
    act(() => foldSheet('tico', true));
    const slides = () => calls.filter(c => String(c.keys[0]?.transform).startsWith('translateX'));
    expect(slides().length).toBeGreaterThan(0);
    expect(slides().every(c => c.opts.duration === 200)).toBe(true);

    calls.length = 0;
    fireEvent.click(screen.getByRole('button', { name: 'Open Tico' }));
    expect(slides().length).toBeGreaterThan(0);
    expect(slides().every(c => c.opts.duration === 200)).toBe(true);
    const fades = calls.filter(c => c.keys[0]?.opacity === 0);
    expect(fades.every(c => Number(c.opts.duration) <= 120)).toBe(true);
  });
});
