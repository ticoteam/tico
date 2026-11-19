/* The level bars on a call (motion.md C2, C3; dot-states.js L147-151): four bars that read a voice while it has the
   floor, ramping in and out over 200ms, and otherwise rest as short still ticks. At Off they stand still at fixed
   heights while someone talks. Version A (MOTION.callBars) puts them with whoever talks; the call sheet places them. */
import { useEffect, useRef, useState } from 'react';
import { clamp, f, levelRank, now, onFrame } from '@/motion';
import type { Level } from './store';

const RAMP_MS = 200;
const LAG_MS = 55;

export type CallBarsProps = {
  /** The level of whoever talks now, or null when nobody does. Keep it stable between renders. */
  signal?: Level | null;
  /** Version B: the person talks, so the bars under the Dot turn gray. */
  dim?: boolean;
  /** Version A: the person talks at the microphone, so the bars under the Dot go. */
  gone?: boolean;
  /** Drive them from this controller rather than from `signal` (the gallery's scripts). */
  of?: BarsController;
};

export type BarsController = {
  start(sig: Level): void;
  stop(): void;
  attach(el: HTMLElement): () => void;
};

export function createBars(): BarsController {
  let el: HTMLElement | null = null;
  let live: { sig: Level; from: number; to: number } | null = null;
  let unframe: (() => void) | null = null;

  const bars = () => (el ? Array.from(el.querySelectorAll<HTMLElement>(':scope > i')) : []);
  const rest = () => bars().forEach((b) => (b.style.transform = ''));

  function frame(t: number): void {
    if (!live) {
      unframe?.();
      unframe = null;
      return;
    }
    const level = levelRank();
    el?.classList.toggle('is-on', level === 0 && t < live.to);
    if (level === 0 || t >= live.to) {
      rest();
      if (t >= live.to) {
        live = null;
        el?.classList.remove('is-on');
        unframe?.();
        unframe = null;
      }
      return;
    }
    const w = clamp((t - live.from) / RAMP_MS) * clamp((live.to - t) / RAMP_MS);
    const sig = live.sig;
    bars().forEach((b, j) => {
      const i = j % 4;
      b.style.transform = `scaleY(${f(0.22 + 0.78 * w * clamp(sig(t - i * LAG_MS) * (i % 2 ? 1.15 : 0.85)))})`;
    });
  }

  const run = () => {
    if (!unframe && live && el) unframe = onFrame(frame);
  };

  return {
    start(sig) {
      const t = now();
      live = live && live.to > t ? { sig, from: live.from, to: Infinity } : { sig, from: t, to: Infinity };
      run();
    },
    stop() {
      if (live?.to === Infinity) live.to = now() + RAMP_MS;
    },
    attach(next) {
      el = next;
      run();
      return () => {
        el = null;
        unframe?.();
        unframe = null;
      };
    },
  };
}

export function CallBars({ signal, dim, gone, of }: CallBarsProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [own] = useState(createBars);
  const ctl = of ?? own;
  useEffect(() => (ref.current ? ctl.attach(ref.current) : undefined), [ctl]);
  useEffect(() => {
    if (of) return;
    if (signal) ctl.start(signal);
    else ctl.stop();
  }, [of, ctl, signal]);
  return (
    <span ref={ref} className={`lvl${dim ? ' dim' : ''}${gone ? ' gone' : ''}`} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}
