/* One clock for every motion (motion.md 7, "Testable motion"). Live, it is the page's own time. Frozen, it stands
   still until `seek(ms)` moves it: timers fire in order with the clock set to their own moment, Web Animations are
   paused at `ms - start`, frame functions are called once at `ms`, and CSS animations are stopped at `ms` by the
   mock's rule (motion.css `.is-frozen`). So a picture at 1240ms is the same on every run.

   `?freeze=<ms>` freezes at load. Tests and the picture tool use `window.__motion.seek(ms)`. */

type Timer = { at: number; seq: number; fn: () => void };
type Frame = (t: number) => void;

let frozenAt: number | null = null;
let timers: Timer[] = [];
let seq = 0;
const tracked = new Map<Animation, number>();
const frames = new Set<Frame>();
let raf = 0;

export const now = (): number => frozenAt ?? performance.now();
export const isFrozen = (): boolean => frozenAt !== null;

/** `?freeze=<ms>` from the address, or null. */
export function freezeParam(): number | null {
  if (typeof location === 'undefined') return null;
  const q = new URLSearchParams(location.search);
  if (!q.has('freeze')) return null;
  return Math.max(0, Number(q.get('freeze')) || 0);
}

/** Runs `fn` after `ms` on this clock. Returns a cancel. */
export function after(ms: number, fn: () => void): () => void {
  if (frozenAt === null) {
    const id = setTimeout(fn, Math.max(0, ms));
    return () => clearTimeout(id);
  }
  const t: Timer = { at: frozenAt + Math.max(0, ms), seq: seq++, fn };
  timers.push(t);
  return () => {
    timers = timers.filter((x) => x !== t);
  };
}

/** Plays keyframes on an element, started at `startAt` on this clock (default now). Null where there is no Web Animations. */
export function play(el: Element, keyframes: Keyframe[], opts: KeyframeAnimationOptions, startAt = now()): Animation | null {
  if (typeof el.animate !== 'function') return null;
  const a = el.animate(keyframes, opts);
  if (frozenAt !== null) {
    tracked.set(a, startAt);
    a.pause();
    a.currentTime = frozenAt - startAt;
  } else if (startAt < now()) {
    a.currentTime = now() - startAt;
  }
  return a;
}

/** Stops an animation for good (it stops being seeked too). */
export function stop(a: Animation | null | undefined): void {
  if (!a) return;
  tracked.delete(a);
  a.cancel();
}

/** A function of the clock, called every frame while registered (and once at once). One shared loop. */
export function onFrame(fn: Frame): () => void {
  frames.add(fn);
  fn(now());
  wake();
  return () => {
    frames.delete(fn);
  };
}

function wake(): void {
  if (raf || frozenAt !== null || !frames.size || typeof requestAnimationFrame === 'undefined') return;
  raf = requestAnimationFrame(tick);
}

function tick(): void {
  raf = 0;
  if (frozenAt !== null) return;
  const t = now();
  for (const fn of [...frames]) fn(t);
  wake();
}

function root(): HTMLElement | null {
  return typeof document === 'undefined' ? null : document.documentElement;
}

/** Stops the clock at `at` (default 0). Everything that moves stands still until `seek`. */
export function freeze(at = 0): void {
  if (raf && typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(raf);
  raf = 0;
  frozenAt = at;
  root()?.classList.add('is-frozen');
  sync();
}

/** Moves a frozen clock to `t`, firing every timer that falls due on the way, in order, each at its own moment. */
export function seek(t: number): void {
  if (frozenAt === null) freeze(t);
  for (;;) {
    let next: Timer | undefined;
    for (const x of timers) if (x.at <= t && (!next || x.at < next.at || (x.at === next.at && x.seq < next.seq))) next = x;
    if (!next) break;
    const due = next;
    timers = timers.filter((x) => x !== due);
    frozenAt = Math.max(frozenAt ?? 0, due.at);
    due.fn();
  }
  frozenAt = t;
  sync();
}

/** Lets the clock run again from the page's own time. */
export function thaw(): void {
  if (frozenAt === null) return;
  const pending = timers;
  const at = frozenAt;
  frozenAt = null;
  timers = [];
  root()?.classList.remove('is-frozen');
  root()?.style.removeProperty('--ft');
  for (const [a] of tracked) a.play();
  tracked.clear();
  for (const x of pending) after(x.at - at, x.fn);
  wake();
}

function sync(): void {
  const t = frozenAt ?? 0;
  root()?.style.setProperty('--ft', String(t));
  for (const [a, start] of tracked) {
    const target = (a.effect as KeyframeEffect | null)?.target;
    if (a.playState === 'idle' || (target && !target.isConnected)) {
      tracked.delete(a);
      continue;
    }
    a.pause();
    a.currentTime = t - start;
  }
  for (const fn of [...frames]) fn(t);
}

const initial = freezeParam();
if (initial !== null) freeze(initial);

declare global {
  interface Window {
    __motion?: { seek: typeof seek; freeze: typeof freeze; thaw: typeof thaw; now: typeof now };
  }
}
if (typeof window !== 'undefined') window.__motion = { seek, freeze, thaw, now };
