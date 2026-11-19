import { createElement } from 'react';
import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { freeze, now, seek, setMotionChoice } from '@/motion';
import { createDot, Dot, type DotController } from '@/tico/dot';

type Call = { el: Element; frames: Keyframe[]; opts: KeyframeAnimationOptions };
let calls: Call[] = [];
const realAnimate = Element.prototype.animate;

// jsdom has no Web Animations: record what would play.
function fakeAnimate() {
  calls = [];
  Element.prototype.animate = function (this: Element, frames, opts) {
    calls.push({ el: this, frames: frames as Keyframe[], opts: opts as KeyframeAnimationOptions });
    return {
      playState: 'running',
      currentTime: 0,
      effect: { target: this, getTiming: () => ({ duration: 0 }) },
      pause() {},
      play() {},
      cancel(this: { playState: string }) {
        this.playState = 'idle';
      },
      updatePlaybackRate() {},
    } as unknown as Animation;
  };
}

let ctl: DotController;
let w: HTMLElement;

function mount() {
  ctl = createDot();
  const { container } = render(createElement(Dot, { of: ctl, size: 72 }));
  w = container.querySelector('.dw') as HTMLElement;
}

const shots = (kind: 'scale' | 'translateX' | 'translateY') =>
  calls.filter((c) => c.el === w && c.opts.composite === 'add' && String(c.frames[10]?.transform).startsWith(kind));

beforeEach(() => {
  fakeAnimate();
  freeze(0);
  setMotionChoice('more');
  mount();
});

afterEach(() => {
  ctl.reset();
  seek(now() + 1e6);
  Element.prototype.animate = realAnimate;
});

describe('shape priority', () => {
  it('shows the highest state that holds: off > cannot > speak > wait > think > write > rest', () => {
    ctl.set('write');
    expect(w.classList.contains('s-busy')).toBe(true);
    ctl.set('think');
    expect(ctl.snapshot().shape).toBe('think');
    ctl.set('wait');
    expect(w.classList.contains('s-flow')).toBe(true);
    ctl.set('speak', () => 0.5);
    expect(w.classList.contains('s-voice')).toBe(true);
    ctl.set('cannot');
    expect(w.classList.contains('s-grey')).toBe(true);
    ctl.set('off');
    expect(w.classList.contains('s-off')).toBe(true);
    expect(w.className.match(/\bs-\w+/g)).toEqual(['s-off']);

    ctl.clear('off');
    expect(ctl.snapshot().shape).toBe('cannot');
  });

  it('lets go of cannot by itself after 2.4s, with one shake', () => {
    ctl.set('think');
    ctl.set('cannot');
    expect(shots('translateX')).toHaveLength(1);
    seek(2399);
    expect(ctl.snapshot().shape).toBe('cannot');
    seek(2400);
    expect(ctl.snapshot().shape).toBe('think');
  });

  it('holds the Flow shape while leaving, then pulses once', () => {
    ctl.set('wait');
    seek(1200);
    const fb = w.querySelector('.fshape .fb')!.getAttribute('d');
    ctl.clear('wait');
    seek(1999);
    expect(ctl.snapshot().shape).toBe('wait');
    expect(w.querySelector('.fshape .fb')!.getAttribute('d')).not.toBe(fb);
    seek(2000);
    expect(ctl.snapshot().shape).toBe('rest');
    const before = shots('scale').length;
    seek(2100);
    expect(shots('scale').length).toBe(before + 1);
  });

  it('nudges a waiting Dot at 2.6s and every 2.4s after', () => {
    ctl.set('wait');
    seek(2599);
    expect(shots('translateX')).toHaveLength(0);
    seek(2600);
    expect(shots('translateX')).toHaveLength(1);
    seek(5000);
    expect(shots('translateX')).toHaveLength(2);
  });
});

describe('droplets and the message', () => {
  it('shows one droplet per thing in progress, five at most', () => {
    const done = Array.from({ length: 7 }, () => ctl.set('work'));
    expect(ctl.snapshot().droplets).toBe(5);
    expect(w.classList.contains('n5')).toBe(true);
    done.slice(0, 5).forEach((d) => d());
    expect(ctl.snapshot().droplets).toBe(2);
    expect(w.classList.contains('n2')).toBe(true);
  });

  it('bumps once, rests the droplet 100ms later, and takes it back 150ms after the sheet opens', () => {
    ctl.set('fold');
    expect(shots('translateY')).toHaveLength(1);
    seek(99);
    expect(w.classList.contains('has-bud')).toBe(false);
    seek(100);
    expect(w.classList.contains('has-bud')).toBe(true);
    ctl.set('fold');
    expect(shots('translateY')).toHaveLength(1);
    ctl.clear('fold');
    seek(249);
    expect(w.classList.contains('has-bud')).toBe(true);
    seek(250);
    expect(w.classList.contains('has-bud')).toBe(false);
  });
});

describe('one-shots and levels', () => {
  it('lets a different one-shot wait for the running one, and restarts the same one', () => {
    ctl.kick();
    ctl.set('done');
    expect(shots('scale')).toHaveLength(1);
    ctl.kick();
    expect(shots('scale')).toHaveLength(2);
    seek(340);
    expect(shots('scale')).toHaveLength(3);
  });

  it('at Off: gray without a shake, no bump, no pulse', () => {
    setMotionChoice('off');
    ctl.set('cannot');
    ctl.set('fold');
    ctl.set('done');
    expect(w.classList.contains('s-grey')).toBe(true);
    expect(calls.filter((c) => c.el === w)).toHaveLength(0);
  });

  it('at Less: no shake and no nudge', () => {
    setMotionChoice('less');
    ctl.set('cannot');
    ctl.clear('cannot');
    ctl.set('wait');
    seek(6000);
    expect(shots('translateX')).toHaveLength(0);
  });
});
