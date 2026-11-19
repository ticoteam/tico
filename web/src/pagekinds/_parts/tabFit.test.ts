import { describe, expect, it } from 'vitest';
import { LEAD, rowMarks, scrollFor, stepTab } from './tabFit';

// A Finance-like row: eleven tabs, 1100 of content in an 840 sheet.
const row = (scrollLeft = 0) => ({ scrollLeft, clientWidth: 840, scrollWidth: 1100 });

describe('rowMarks', () => {
  it('marks nothing when the row fits', () => {
    expect(rowMarks({ scrollLeft: 0, clientWidth: 840, scrollWidth: 600 }, false)).toEqual({ long: false, scrolled: false, end: false });
  });

  it('marks a desk row wider than its sheet long, so its right edge fades', () => {
    expect(rowMarks(row(), false)).toEqual({ long: true, scrolled: false, end: false });
  });

  it('marks scrolled, then end, as the row is scrolled', () => {
    expect(rowMarks(row(100), false)).toEqual({ long: true, scrolled: true, end: false });
    expect(rowMarks(row(260), false)).toEqual({ long: true, scrolled: true, end: true });
  });

  it('never marks a phone row long (its fade is always on)', () => {
    expect(rowMarks(row(), true).long).toBe(false);
    expect(rowMarks(row(100), true).scrolled).toBe(true);
  });

  it('ignores a one-pixel rounding difference', () => {
    expect(rowMarks({ scrollLeft: 0, clientWidth: 840, scrollWidth: 841 }, false).long).toBe(false);
  });
});

describe('scrollFor', () => {
  it('leaves a desk row alone when the open tab is in view', () => {
    expect(scrollFor(row(), { left: 0, right: 90 }, false)).toBeNull();
  });

  it('scrolls a desk row to an open tab past the edge, keeping a lead before it', () => {
    expect(scrollFor(row(), { left: 1000, right: 1070 }, false)).toBe(260); // clamped to the row's end
    expect(scrollFor(row(), { left: 240, right: 820 }, false)).toBe(240 - LEAD); // its right end is under the fade
  });

  it('scrolls a desk row back to an open tab under the left fade', () => {
    expect(scrollFor(row(200), { left: 210, right: 280 }, false)).toBe(210 - LEAD);
  });

  it('does not scroll a row that fits', () => {
    expect(scrollFor({ scrollLeft: 0, clientWidth: 840, scrollWidth: 600 }, { left: 500, right: 580 }, false)).toBeNull();
  });

  it('puts a phone row’s open tab near the start', () => {
    expect(scrollFor(row(), { left: 300, right: 380 }, true)).toBe(300 - LEAD);
    expect(scrollFor(row(), { left: 10, right: 80 }, true)).toBeNull();
  });

  it('does nothing with no open tab', () => {
    expect(scrollFor(row(), null, false)).toBeNull();
  });
});

describe('stepTab', () => {
  it('walks every tab with the arrow keys and wraps', () => {
    expect(stepTab(0, 11, 'ArrowRight')).toBe(1);
    expect(stepTab(10, 11, 'ArrowRight')).toBe(0);
    expect(stepTab(0, 11, 'ArrowLeft')).toBe(10);
    expect(stepTab(4, 11, 'Home')).toBe(0);
    expect(stepTab(4, 11, 'End')).toBe(10);
    expect(stepTab(4, 11, 'Enter')).toBeNull();
  });
});
