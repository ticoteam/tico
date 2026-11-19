import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flight, freeze, now, onBurst, seek, setMotionChoice, type BurstReport } from '@/motion';
import { resolveLevel } from './level';

const realAnimate = Element.prototype.animate;

// jsdom has no Web Animations: a stand-in that can be paused and cancelled.
beforeEach(() => {
  Element.prototype.animate = function (this: Element) {
    return {
      playState: 'running',
      currentTime: 0,
      effect: { target: this },
      pause() {},
      play() {},
      cancel(this: { playState: string }) {
        this.playState = 'idle';
      },
    } as unknown as Animation;
  };
  freeze(0);
  setMotionChoice('more');
  document.body.innerHTML = '';
});

afterEach(() => {
  seek(now() + 1e6);
  Element.prototype.animate = realAnimate;
});

function scene(n: number) {
  const from = document.createElement('span');
  from.className = 'dw';
  document.body.append(from);
  const rows = Array.from({ length: n }, (_, i) => {
    const r = document.createElement('div');
    r.setAttribute('aria-label', `row ${i}`);
    document.body.append(r);
    return r;
  });
  const launches: number[] = [];
  from.addEventListener('motion:launch', () => launches.push(now()));
  return { from, rows, launches };
}

describe('bursts', () => {
  it('flies three droplets 320ms apart, washes the rest after the last landing, and reports the rest', () => {
    const { from, rows, launches } = scene(5);
    const landed: number[] = [];
    const reports: BurstReport[] = [];
    const off = onBurst((r) => reports.push(r));
    rows.forEach((r, i) => flight(from, r, { label: `Row ${i}`, onLand: () => landed.push(now()) }));

    seek(2000);
    off();
    // jsdom lays nothing out, so every flight is the shortest: 300ms.
    expect(launches).toEqual([0, 320, 640]);
    expect(landed).toEqual([300, 620, 940, 940 + 120, 940 + 120 + 70]);
    expect(reports).toEqual([{ from, labels: ['Row 0', 'Row 1', 'Row 2'], more: 2, at: 940 + 380 }]);
    expect(document.querySelectorAll('.fly')).toHaveLength(0);
  });

  it('holds a later burst until 200ms after the last landing', () => {
    const { from, rows, launches } = scene(2);
    flight(from, rows[0]!);
    seek(500);
    flight(from, rows[1]!);
    seek(2000);
    // The first lands at 300; the next may start 200ms later.
    expect(launches).toEqual([0, 500]);
  });

  it('queues a burst that arrives while one is in the air', () => {
    const { from, rows, launches } = scene(5);
    rows.slice(0, 3).forEach((r) => flight(from, r));
    seek(450);
    flight(from, rows[3]!);
    seek(3000);
    // The first burst's last droplet lands at 940; the next starts at 940 + 200.
    expect(launches).toEqual([0, 320, 640, 1140]);
  });

  it('keeps bursts from different Dots apart', () => {
    const a = scene(1);
    const b = scene(1);
    flight(a.from, a.rows[0]!);
    flight(b.from, b.rows[0]!);
    seek(1000);
    expect(a.launches).toEqual([0]);
    expect(b.launches).toEqual([0]);
  });
});

describe('Motion off', () => {
  it('flies nothing: the change shows at once and the thing gets the wash', () => {
    setMotionChoice('off');
    const { from, rows, launches } = scene(1);
    let shown = -1;
    flight(from, rows[0]!, { onLand: () => (shown = now()) });
    expect(shown).toBe(0);
    seek(10);
    expect(launches).toEqual([]);
    expect(document.querySelectorAll('.fly')).toHaveLength(0);
    expect(rows[0]!.classList.contains('is-washing')).toBe(true);
    expect(rows[0]!.querySelector(':scope > .wash')).not.toBeNull();
    seek(1100);
    expect(rows[0]!.classList.contains('is-washing')).toBe(false);
  });

  it('washes several changes 70ms apart', async () => {
    setMotionChoice('off');
    const { from, rows } = scene(3);
    const landed: number[] = [];
    rows.forEach((r) => flight(from, r).then((x) => landed.push(x.landedAt)));
    seek(500);
    await Promise.resolve();
    expect(landed).toEqual([0, 70, 140]);
  });

  it('at Less: the first droplet only', () => {
    setMotionChoice('less');
    const { from, rows, launches } = scene(3);
    rows.forEach((r) => flight(from, r));
    seek(2000);
    expect(launches).toEqual([0]);
  });

  it('follows the system setting unless the person picked a level', () => {
    expect(resolveLevel('system', true)).toBe('off');
    expect(resolveLevel('system', false)).toBe('more');
    expect(resolveLevel('more', true)).toBe('more');
    expect(resolveLevel('junk', false)).toBe('more');
  });

  it('asks an agent only at More', () => {
    setMotionChoice('less');
    const { from, rows, launches } = scene(1);
    flight(from, rows[0]!, { kind: 'ask' });
    seek(1000);
    expect(launches).toEqual([]);
  });
});
