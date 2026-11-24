import { describe, expect, it } from 'vitest';
import { actualScale, canSwipe, clamp, FIT, MAX_SCALE, pan, swipeStep, toggle, zoomTo } from './zoom';

const box = { w: 400, h: 300, areaW: 400, areaH: 300 };

describe('picture zoom', () => {
  it('swipes walk the set only at fit; zoomed in, the same drag pans', () => {
    expect(swipeStep(FIT, -120, 10)).toBe(1);
    expect(swipeStep(FIT, 120, 10)).toBe(-1);
    const zoomed = zoomTo(FIT, 2, 0, 0, box);
    expect(canSwipe(zoomed)).toBe(false);
    expect(swipeStep(zoomed, -120, 10)).toBe(0);
    expect(pan(zoomed, -120, 10, box)).toMatchObject({ scale: 2, x: -120, y: 10 });
    // a short or mostly-downward drag is no swipe
    expect(swipeStep(FIT, -20, 0)).toBe(0);
    expect(swipeStep(FIT, -80, 120)).toBe(0);
  });

  it('never pans at fit, and never past the picture’s edge', () => {
    expect(pan(FIT, 50, 50, box)).toEqual(FIT);
    // twice as large: 200 px spare across, 150 down, so half of each either way
    expect(pan(zoomTo(FIT, 2, 0, 0, box), 999, -999, box)).toEqual({ scale: 2, x: 200, y: -150 });
  });

  it('keeps the point under the cursor or fingers in place, within fit and the most', () => {
    const z = zoomTo(FIT, 2, 100, 50);
    expect(z).toEqual({ scale: 2, x: -100, y: -50 });
    expect(zoomTo(z, 0.2)).toEqual(FIT);
    expect(zoomTo(FIT, 99).scale).toBe(MAX_SCALE);
    expect(clamp({ scale: 0.5, x: 30, y: 30 })).toEqual(FIT);
  });

  it('toggles between fit and actual size, or twice the fit for a small picture', () => {
    expect(actualScale(1600, 400)).toBe(4);
    expect(actualScale(300, 400)).toBe(2);
    expect(toggle(FIT, 4).scale).toBe(4);
    expect(toggle(zoomTo(FIT, 3), 4)).toEqual(FIT);
  });
});
