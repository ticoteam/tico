import { describe, expect, it } from 'vitest';
import { pickStop, rubber, velocity } from './stops';

// Tico's stops on a 787px card: full, half (410 shown), lip (34 safe area + 58 lip).
const TICO = [0, 377, 695] as const;
const FULL = 0;
const HALF = 1;
const LIP = 2;

describe('pickStop', () => {
  it('a flick goes one stop in its direction', () => {
    expect(pickStop({ stops: TICO, start: LIP, ty: 660, dy: -35, v: -0.8 })).toBe(HALF);
    expect(pickStop({ stops: TICO, start: HALF, ty: 330, dy: -47, v: -0.8 })).toBe(FULL);
    expect(pickStop({ stops: TICO, start: HALF, ty: 420, dy: 43, v: 0.8 })).toBe(LIP);
  });

  it('a hard flick up from the lip goes straight to full', () => {
    expect(pickStop({ stops: TICO, start: LIP, ty: 600, dy: -95, v: -2.2 })).toBe(FULL);
    expect(pickStop({ stops: TICO, start: HALF, ty: 300, dy: -77, v: -2.2 })).toBe(FULL);
  });

  it('a flick never runs past the end stops', () => {
    expect(pickStop({ stops: TICO, start: FULL, ty: -20, dy: -20, v: -1 })).toBe(FULL);
    expect(pickStop({ stops: TICO, start: LIP, ty: 720, dy: 25, v: 1 })).toBe(LIP);
  });

  it('a slow drag lands on the nearest stop', () => {
    expect(pickStop({ stops: TICO, start: LIP, ty: 400, dy: -295, v: -0.1 })).toBe(HALF);
    expect(pickStop({ stops: TICO, start: LIP, ty: 120, dy: -575, v: 0 })).toBe(FULL);
    expect(pickStop({ stops: [0, 650], start: 0, ty: 400, dy: 400, v: 0.2 })).toBe(1);
  });

  it('a short slow drag goes back to where it started', () => {
    expect(pickStop({ stops: TICO, start: HALF, ty: 357, dy: -20, v: -0.2 })).toBe(HALF);
  });
});

describe('rubber', () => {
  it('leaves positions between the stops alone', () => {
    expect(rubber(300, 0, 695, 787)).toBe(300);
  });

  it('pulls back past an end, more the further it goes, and never reaches the finger', () => {
    const a = rubber(-40, 0, 695, 787);
    const b = rubber(-200, 0, 695, 787);
    expect(a).toBeLessThan(0);
    expect(a).toBeGreaterThan(-40);
    expect(b).toBeLessThan(a);
    expect(b).toBeGreaterThan(-200);
    expect(rubber(800, 0, 695, 787)).toBeLessThan(800);
    expect(rubber(800, 0, 695, 787)).toBeGreaterThan(695);
  });
});

describe('velocity', () => {
  it('uses only the last 100ms', () => {
    const s = [
      { t: 0, y: 700 },
      { t: 200, y: 690 },
      { t: 250, y: 650 },
      { t: 300, y: 590 },
    ];
    expect(velocity(s)).toBeCloseTo(-1, 5);
  });

  it('is zero with one sample', () => {
    expect(velocity([{ t: 5, y: 10 }])).toBe(0);
    expect(velocity([])).toBe(0);
  });
});
