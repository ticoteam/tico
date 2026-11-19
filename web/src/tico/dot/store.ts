/* What the Dot is doing (motion.md 4). Four channels on one wrapper, as the mock draws them (dot-states.css L225-270):
   - shape, one at a time, highest first: off > cannot > speak > wait > think > write > rest;
   - droplets, one for each thing in progress, up to five (hidden in wait and off);
   - the message droplet (`fold`);
   - one-shots on the wrapper (shake > bump > kick > pulse > nudge), plus the lean while you type.
   A controller drives every wrapper drawn for it (Tico's head and the phone lip draw the same Dot) by classes, Web
   Animations and one shared frame loop; React only draws the markup once. `dot` is Tico's own. */
import {
  after,
  BUMP,
  clamp,
  eio,
  f,
  isFrozen,
  KICK,
  levelRank,
  MOTION,
  now,
  onFrame,
  play,
  pulseOf,
  sample,
  SHAKE,
  stop,
} from '@/motion';
import { flowPaths, voiceRadius } from './shapes';

export type DotShape = 'rest' | 'think' | 'write' | 'speak' | 'wait' | 'cannot' | 'off';

/** What the app tells the Dot. `done` is a one-shot; the rest hold until cleared (`cannot` lets go by itself). */
export type DotState = Exclude<DotShape, 'rest'> | 'work' | 'type' | 'fold' | 'done';

/** A level 0..1 at a moment on the motion clock: Tico's voice on a call. */
export type Level = (t: number) => number;

export type DotSnapshot = { shape: DotShape; droplets: number; message: boolean };

export type DotController = {
  /** Holds a state; returns its release. `work` takes a key per thing in progress; `speak` takes the voice level. */
  set(state: DotState, arg?: string | Level): () => void;
  clear(state: DotState, key?: string): void;
  /** The wrapper on screen, to fly from. */
  el(): HTMLElement | null;
  /** Shows exactly this shape and this many things in progress (for a Dot drawn from props). */
  show(shape: DotShape, work?: number, message?: boolean): void;
  kick(): void;
  pulse(top?: number, ms?: number): void;
  /** Lets go of everything at once, with no motion (the gallery's loops). */
  reset(): void;
  attach(w: HTMLElement): () => void;
  subscribe(fn: () => void): () => void;
  snapshot(): DotSnapshot;
};

const ORDER: Exclude<DotShape, 'rest'>[] = ['off', 'cannot', 'speak', 'wait', 'think', 'write'];
const CLASS: Record<DotShape, string> = { rest: '', think: 's-think', write: 's-busy', speak: 's-voice', wait: 's-flow', cannot: 's-grey', off: 's-off' };
const ALL_CLASSES = Object.values(CLASS).filter(Boolean);
/** The CSS animation on `.dot i` in each shape: a change between them gets the 400ms blend (motion.md 4). */
const ANIM: Partial<Record<DotShape, 'blob' | 'think'>> = { rest: 'blob', write: 'blob', cannot: 'blob', think: 'think' };
const FIRST = {
  blob: { borderRadius: '43% 57% 55% 45%/38% 44% 56% 62%', transform: 'none' },
  think: { borderRadius: '38% 62% 57% 43%/35% 45% 55% 65%', transform: 'scale(1.02)' },
};

const CANNOT_MS = 2400;
const BLEND_MS = 400;
const EASE = 'cubic-bezier(.2,0,0,1)'; // --ease
const FLOW_IN_MS = 1200;
const FLOW_OUT_MS = 800;
const NUDGE_FIRST_MS = 2600; // after the morph starts: the mock's 700 -> 3300
const NUDGE_EVERY_MS = 2400;
const BUD_AFTER_MS = 100;
const BUD_OFF_MS = 150;
const BUMP_AGAIN_MS = 2000;
const RAMP_VOICE_MS = 300;
const LEAN_IN_MS = 200;
const LEAN_OUT_MS = 300;
const WRITE_BREATH_MS = 1500;

type ShotKind = 'shake' | 'bump' | 'kick' | 'pulse' | 'nudge';
type Shot = { kind: ShotKind; ms: number; min: number; at: (k: number) => string };
const RANK: Record<ShotKind, number> = { shake: 5, bump: 4, kick: 3, pulse: 2, nudge: 1 };

const SHOTS = {
  kick: (): Shot => ({ kind: 'kick', ms: 340, min: 1, at: (k) => { const v = KICK(k); return `scale(${f(v[0]!)},${f(v[1]!)})`; } }),
  shake: (): Shot => ({ kind: 'shake', ms: 360, min: 2, at: (k) => `translateX(${f(SHAKE(k)[0]!)}%)` }),
  bump: (): Shot => ({ kind: 'bump', ms: 460, min: 1, at: (k) => { const v = BUMP(k); return `translateY(${f(v[0]!)}%) scale(${f(v[1]!)},${f(v[2]!)})`; } }),
  pulse: (top: number, ms: number): Shot => {
    const p = pulseOf(top);
    return { kind: 'pulse', ms, min: 1, at: (k) => `scale(${f(p(k)[0]!)})` };
  },
  nudge: (): Shot => ({ kind: 'nudge', ms: 700, min: 2, at: (k) => `translateX(${f(8 * Math.sin(Math.PI * eio(k)))}%)` }),
};

const parts = (w: HTMLElement) => ({
  i: w.querySelector<HTMLElement>(':scope > .dot > i'),
  b: w.querySelector<HTMLElement>(':scope > .dot > b'),
  fb: w.querySelector<SVGPathElement>(':scope > .fshape > .fb'),
  ff: w.querySelector<SVGPathElement>(':scope > .fshape > .ff'),
});

export function createDot(): DotController {
  const wrappers = new Set<HTMLElement>();
  const subs = new Set<() => void>();

  const held = { think: false, write: false, wait: false, cannot: false, off: false, fold: false };
  const work = new Set<string>();
  let shown: DotShape = 'rest';
  let bud = false;
  let snap: DotSnapshot = { shape: 'rest', droplets: 0, message: false };
  let seq = 0;

  // timers that belong to a state, cancelled when it changes
  let cannotCancel = () => {};
  let leaveWait = () => {};
  let nudgeCancel = () => {};
  let budCancel = () => {};
  let speakCancel = () => {};
  let leanCancel = () => {};
  let lastBump = -Infinity;

  // continuous motion, painted by one frame function
  let speak: { sig: Level; start: number; end: number } | null = null;
  let lean: { start: number; end: number } | null = null;
  let morph: { from: number; to: number; start: number; ms: number } | null = null;
  let morphE = 0;
  let flowLeaving = false;
  let painting: (() => void) | null = null;

  // one-shots
  let running: { shot: Shot; anims: (Animation | null)[]; end: () => void } | null = null;
  let queue: Shot[] = [];

  const lv = () => levelRank();

  /* ---------- shape and classes ---------- */

  function shape(): DotShape {
    for (const s of ORDER) {
      if (s === 'speak' ? speak !== null : s === 'wait' ? held.wait || flowLeaving : held[s as keyof typeof held]) return s;
    }
    return 'rest';
  }

  const droplet = () => MOTION.think === 'droplet';
  const count = () => Math.min(5, work.size + (droplet() && shown === 'think' ? 1 : 0));

  function render(): void {
    const s = shape();
    const prev = shown;
    const blend = s !== prev ? readBlend(prev, s) : null;
    shown = s;
    const n = count();
    const cls = droplet() && s === 'think' ? '' : CLASS[s];
    for (const w of wrappers) {
      for (const c of ALL_CLASSES) w.classList.toggle(c, c === cls);
      for (let i = 1; i <= 5; i++) w.classList.toggle(`n${i}`, i === n);
      w.classList.toggle('has-bud', bud);
    }
    if (s !== prev) changed(prev, s, blend);
    const want = s === 'wait' && held.wait ? 1 : 0;
    if (s !== 'wait') {
      if (morph || morphE) {
        morph = null;
        setMorph(0);
      }
    } else if ((morph ? morph.to : morphE) !== want) {
      morph = { from: morphE, to: want, start: now(), ms: lv() ? (want ? FLOW_IN_MS : FLOW_OUT_MS) : 1 };
      paint();
    }
    if (snap.shape !== s || snap.droplets !== n || snap.message !== bud) {
      snap = { shape: s, droplets: n, message: bud };
      for (const fn of [...subs]) fn();
    }
  }

  /** Before the classes change: the shape `.dot i` has now, if the change swaps its animation. */
  function readBlend(prev: DotShape, next: DotShape): Map<HTMLElement, Keyframe> | null {
    const a = ANIM[prev];
    const b = ANIM[droplet() && next === 'think' ? 'rest' : next];
    if (!a || !b || a === b || isFrozen() || lv() < 1) return null;
    const out = new Map<HTMLElement, Keyframe>();
    for (const w of wrappers) {
      const { i } = parts(w);
      if (!i?.isConnected) continue;
      const cs = getComputedStyle(i);
      out.set(w, { borderRadius: cs.borderRadius, transform: cs.transform });
    }
    return out;
  }

  /** After the classes change: the blend, the quicker breath, and the wait's nudges. */
  function changed(prev: DotShape, next: DotShape, blend: Map<HTMLElement, Keyframe> | null): void {
    const a = ANIM[prev];
    const b = ANIM[droplet() && next === 'think' ? 'rest' : next];
    for (const w of wrappers) {
      const { i } = parts(w);
      if (a !== b) w.classList.toggle('is-blend', !!blend?.has(w));
      const from = blend?.get(w);
      if (i && from && b) play(i, [from, FIRST[b]], { duration: BLEND_MS, easing: EASE });
    }
    if (next === 'write' || prev === 'write') breathRate(next === 'write');
    if (next === 'wait' && prev !== 'wait') nudges();
  }

  /** It writes back: the breath at 1.5s by playback rate, which keeps the phase (motion.md A8). Frozen pictures use the
   *  mock's class rule instead (dot.css). */
  function breathRate(quick: boolean): void {
    if (isFrozen()) return;
    for (const w of wrappers) {
      const { i, b } = parts(w);
      for (const el of [i, b]) {
        for (const anim of el?.getAnimations?.() ?? []) {
          const name = (anim as CSSAnimation).animationName;
          if (name !== 'blob' && name !== 'glint') continue;
          const ms = Number(anim.effect?.getTiming().duration) || 0;
          anim.updatePlaybackRate(quick && ms ? ms / WRITE_BREATH_MS : 1);
        }
      }
    }
  }

  function nudges(): void {
    nudgeCancel();
    if (!MOTION.flowNudge) return;
    const tick = () => {
      if (shown !== 'wait' || !held.wait) return;
      shot(SHOTS.nudge());
      nudgeCancel = after(NUDGE_EVERY_MS, tick);
    };
    nudgeCancel = after(NUDGE_FIRST_MS, tick);
  }

  /* ---------- the frame: voice, lean, the Flow morph ---------- */

  function setMorph(e: number): void {
    morphE = e;
    const [back, front] = flowPaths(e);
    for (const w of wrappers) {
      const { fb, ff } = parts(w);
      fb?.setAttribute('d', back);
      ff?.setAttribute('d', front);
    }
  }

  function frame(t: number): void {
    const level = lv();
    let tf = '';
    let br = '';
    if (speak && level > 0) {
      const g = level === 1 ? 0.5 : 1;
      const s = t / 1000;
      const L = speak.sig(t) * clamp((t - speak.start) / RAMP_VOICE_MS) * clamp((speak.end - t) / RAMP_VOICE_MS);
      const sc = 1 + 0.14 * g * L;
      const sq = 0.014 * g * L * Math.sin(s * 2.3);
      tf = `scale(${f(sc + sq)},${f(sc - sq)})`;
      br = voiceRadius(s, 0.55 * g * L);
    }
    if (lean && level >= 2) {
      const e = eio(clamp(Math.min((t - lean.start) / LEAN_IN_MS, (lean.end - t) / LEAN_OUT_MS)));
      tf += `${tf ? ' ' : ''}translate(${f(0.8 * e)}%,${f(3 * e)}%)`;
    }
    if (morph) {
      const k = clamp((t - morph.start) / morph.ms);
      setMorph(morph.from + (morph.to - morph.from) * eio(k));
      if (k >= 1) morph = null;
    }
    for (const w of wrappers) {
      w.style.transform = tf;
      const { i } = parts(w);
      if (i) i.style.borderRadius = br;
    }
    if (!speak && !lean && !morph && painting) {
      painting();
      painting = null;
    }
  }

  function paint(): void {
    if (!painting) painting = onFrame(frame);
  }

  /* ---------- one-shots ---------- */

  function shot(s: Shot): void {
    if (lv() < s.min || !wrappers.size) return;
    if (running?.shot.kind === s.kind) {
      running.end();
      start(s);
      return;
    }
    if (running) {
      queue = [...queue.filter((q) => q.kind !== s.kind), s].slice(-2);
      return;
    }
    start(s);
  }

  function start(s: Shot): void {
    const t0 = now();
    const anims = [...wrappers].map((w) =>
      play(w, sample((k) => ({ transform: s.at(k) })), { duration: s.ms, easing: 'linear', composite: 'add' }, t0),
    );
    const cancel = after(s.ms, () => {
      finish();
      next();
    });
    const finish = () => {
      cancel();
      anims.forEach(stop);
      running = null;
    };
    running = { shot: s, anims, end: finish };
  }

  function next(): void {
    if (!queue.length) return;
    queue.sort((a, b) => RANK[b.kind] - RANK[a.kind]);
    start(queue.shift()!);
  }

  /* ---------- the app's states ---------- */

  function set(state: DotState, arg?: string | Level): () => void {
    switch (state) {
      case 'work': {
        const key = typeof arg === 'string' ? arg : `w${++seq}`;
        work.add(key);
        render();
        return () => clear('work', key);
      }
      case 'done':
        shot(SHOTS.pulse(1.14, 520));
        return () => {};
      case 'speak': {
        speakCancel();
        const sig = typeof arg === 'function' ? arg : () => 0;
        speak = speak && speak.end !== Infinity ? { sig, start: speak.start, end: Infinity } : { sig, start: now(), end: Infinity };
        render();
        paint();
        break;
      }
      case 'type': {
        leanCancel();
        const t = now();
        const r = lean ? clamp((lean.end - t) / LEAN_OUT_MS) : 0;
        lean = { start: t - r * LEAN_IN_MS, end: Infinity };
        paint();
        break;
      }
      case 'fold':
        if (now() - lastBump >= BUMP_AGAIN_MS) {
          lastBump = now();
          shot(SHOTS.bump());
        }
        if (!held.fold) {
          held.fold = true;
          budCancel();
          budCancel = after(BUD_AFTER_MS, () => {
            bud = held.fold;
            render();
          });
        }
        break;
      case 'cannot':
        held.cannot = true;
        cannotCancel();
        cannotCancel = after(CANNOT_MS, () => clear('cannot'));
        render();
        shot(SHOTS.shake());
        break;
      case 'wait':
        held.wait = true;
        leaveWait();
        flowLeaving = false;
        render();
        if (shown === 'wait') nudges();
        break;
      default:
        held[state] = true;
        render();
    }
    return () => clear(state);
  }

  function clear(state: DotState, key?: string): void {
    switch (state) {
      case 'work':
        if (key) work.delete(key);
        else work.clear();
        render();
        return;
      case 'done':
        return;
      case 'speak': {
        if (!speak || speak.end !== Infinity) return;
        speak.end = now() + RAMP_VOICE_MS;
        speakCancel = after(RAMP_VOICE_MS, () => {
          speak = null;
          render();
        });
        return;
      }
      case 'type':
        if (!lean || lean.end !== Infinity) return;
        lean.end = now() + LEAN_OUT_MS;
        leanCancel = after(LEAN_OUT_MS, () => {
          lean = null;
        });
        return;
      case 'fold':
        if (!held.fold) return;
        held.fold = false;
        budCancel();
        budCancel = after(BUD_OFF_MS, () => {
          bud = false;
          render();
        });
        after(400, () => shot(SHOTS.pulse(1.08, 320)));
        return;
      case 'cannot':
        cannotCancel();
        held.cannot = false;
        render();
        return;
      case 'wait': {
        if (!held.wait) return;
        held.wait = false;
        nudgeCancel();
        if (shown !== 'wait') {
          render();
          return;
        }
        flowLeaving = true;
        const ms = lv() ? FLOW_OUT_MS : 1;
        render();
        leaveWait = after(ms, () => {
          flowLeaving = false;
          render();
          after(100, () => shot(SHOTS.pulse(1.1, 420)));
        });
        return;
      }
      default:
        held[state] = false;
        render();
    }
  }

  function show(target: DotShape, n = 0, message = false): void {
    for (const s of ORDER) if (s !== target) clear(s);
    const holding = target === 'speak' ? speak?.end === Infinity : target !== 'rest' && held[target];
    if (target !== 'rest' && !holding) set(target);
    const keys = [...work];
    for (let i = keys.length; i < n; i++) work.add(`p${i}`);
    for (const k of keys.slice(n)) work.delete(k);
    if (message) set('fold');
    else clear('fold');
    render();
  }

  function reset(): void {
    for (const c of [cannotCancel, leaveWait, nudgeCancel, budCancel, speakCancel, leanCancel]) c();
    running?.end();
    queue = [];
    Object.assign(held, { think: false, write: false, wait: false, cannot: false, off: false, fold: false });
    work.clear();
    bud = false;
    speak = null;
    lean = null;
    morph = null;
    flowLeaving = false;
    lastBump = -Infinity;
    painting?.();
    painting = null;
    setMorph(0);
    for (const w of wrappers) {
      w.style.transform = '';
      const { i } = parts(w);
      if (i) i.style.borderRadius = '';
      w.classList.remove('is-blend');
    }
    shown = 'rest';
    render();
  }

  function attach(w: HTMLElement): () => void {
    wrappers.add(w);
    const onLaunch = () => shot(SHOTS.kick());
    const onLand = () => shot(SHOTS.pulse(1.1, 320));
    w.addEventListener('motion:launch', onLaunch);
    w.addEventListener('motion:land', onLand);
    // A Dot off screen stands still (motion.md 7).
    const io =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver((es) => es.forEach((e) => w.classList.toggle('is-idle', !e.isIntersecting)));
    io?.observe(w);
    setMorph(morphE);
    render();
    if (shown === 'write') breathRate(true);
    return () => {
      wrappers.delete(w);
      io?.disconnect();
      w.removeEventListener('motion:launch', onLaunch);
      w.removeEventListener('motion:land', onLand);
    };
  }

  function el(): HTMLElement | null {
    let first: HTMLElement | null = null;
    for (const w of wrappers) {
      if (!w.isConnected) continue;
      if (typeof w.checkVisibility !== 'function' || w.checkVisibility()) return w;
      first ??= w;
    }
    return first;
  }

  return {
    set,
    clear,
    el,
    show,
    kick: () => shot(SHOTS.kick()),
    pulse: (top = 1.14, ms = 520) => shot(SHOTS.pulse(top, ms)),
    reset,
    attach,
    subscribe(fn) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
    snapshot: () => snap,
  };
}

/** Tico's Dot: `dot.set('think')`, `dot.clear('think')`, `dot.el()`. */
export const dot: DotController = createDot();
