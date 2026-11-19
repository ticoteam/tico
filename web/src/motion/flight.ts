/* Flights (motion.md 5; the mock's fly(), tico/dot-states.js L162-173). A droplet leaves the Dot, arcs to the thing
   that changed and lands there: bloom, ripple, wash, and the change shows. Never on a read.

   Bursts: changes from one Dot that arrive within 400ms of each other are one burst. The first three launch 320ms apart; the rest
   get no droplet and wash after the last landing, 70ms apart; one report for the chat line comes 380ms after the last
   landing. Bursts never overlap: a new one waits for the last landing + 200ms, and if more than two wait, the older
   ones apply at once with washes only. Less: the first droplet of a burst only, no ripple. Off: no droplet at all,
   the change shows at once and the washes stay, 70ms apart.

   The Dot hears `motion:launch` (it kicks) and `motion:land` (the small return flight came home: it pulses). */
import { after, now, onFrame, play, stop } from './clock';
import { FLIGHT, type DropletSize } from './config';
import { clamp, eio, eioInv, eout, f } from './ease';
import { levelRank } from './level';
import { layer, pop, ripple, wash } from './wash';

export type FlightKind = 'open' | 'edit' | 'ask' | 'answer';

export type FlightOpts = {
  /** open: a page or a view; edit: a value, a row, a chart (default); ask: Tico asks an agent; answer: it answers. */
  kind?: FlightKind;
  /** Aim at the left (+14px) or the centre. Default: the target's `data-target-at`, else the centre. */
  at?: 'left' | 'center';
  /** The landing ring's size, or false. Default 1.2 for open, .8 on an edge chip, .7 otherwise; none for ask/answer. */
  ripple?: number | false;
  /** What gets the wash at landing. Default the target (a row's value lands, the row washes: pass the row). */
  wash?: Element | false;
  /** ask: how far the face pops (1.28 a face, 1.22 a team's stack). */
  pop?: number;
  /** The change's name, for the burst's chat line. */
  label?: string;
  /** Runs when the change should show: at landing, or at once when nothing flies. */
  onLand?: () => void;
};

export type FlightResult = { flew: boolean; landedAt: number };
export type BurstReport = { from: Element; labels: string[]; more: number; at: number };

type Change = { from: Element; to: Element; o: FlightOpts; kind: FlightKind; resolve: (r: FlightResult) => void };
type Burst = {
  key: Element;
  changes: Change[];
  start: number | null;
  off: boolean;
  open: boolean;
  closeCancel: () => void;
  pending: number;
  lastLanding: number;
  extrasWaiting: Array<[Change, number]>;
  finishing: boolean;
};

let bursts: Burst[] = [];
const listeners = new Set<(r: BurstReport) => void>();

/** Hear each burst's report (first three labels and how many more), for its chat line. */
export function onBurst(fn: (r: BurstReport) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Flies a droplet from `from` (the Dot) to `to` (the changed thing). Resolves when the change shows. */
export function flight(from: Element, to: Element, opts: FlightOpts = {}): Promise<FlightResult> {
  const kind = opts.kind ?? 'edit';
  return new Promise((resolve) => {
    const c: Change = { from, to, o: opts, kind, resolve };
    if (kind === 'ask' || kind === 'answer') launch(c);
    else enqueue(c);
  });
}

/* ---------- bursts ---------- */

function enqueue(c: Change): void {
  const off = levelRank() === 0;
  const key = c.from;
  let b = bursts.filter((x) => x.key === key).pop();
  if (!b || !b.open || b.off !== off) {
    b = { key, changes: [], start: null, off, open: true, closeCancel: () => {}, pending: 0, lastLanding: 0, extrasWaiting: [], finishing: false };
    bursts.push(b);
    trim(key);
  }
  b.closeCancel();
  const burst = b;
  b.closeCancel = after(FLIGHT.burstWindowMs, () => {
    burst.open = false;
    finish(burst);
  });
  b.changes.push(c);
  if (off) {
    // Nothing is in the air at Off: the change shows now and its wash takes its turn.
    if (b.start === null) b.start = now();
    c.o.onLand?.();
    const j = b.changes.length - 1;
    const at = b.start + j * FLIGHT.offWashGapMs;
    b.lastLanding = Math.max(b.lastLanding, at);
    after(at - now(), () => {
      wash(c.o.wash || c.to);
      c.resolve({ flew: false, landedAt: at });
    });
    return;
  }
  if (b.start !== null) place(b, c);
  else if (bursts.find((x) => x.key === key && !x.off) === b) begin(b);
}

function begin(b: Burst): void {
  b.start = now();
  for (const c of b.changes) place(b, c);
}

function place(b: Burst, c: Change): void {
  const i = b.changes.indexOf(c);
  if (i < FLIGHT.maxPerBurst) {
    b.pending++;
    const slot = (b.start ?? now()) + i * FLIGHT.burstGapMs;
    after(slot - now(), () => {
      b.pending--;
      // Less: the first droplet of a burst only; the others show at their turn with a wash.
      const landing = i > 0 && levelRank() < FLIGHT.minLevel.burstExtra ? arrive(c, now(), null) : launch(c);
      b.lastLanding = Math.max(b.lastLanding, landing);
      if (!b.pending) {
        for (const [x, j] of b.extrasWaiting) extra(b, x, j);
        b.extrasWaiting = [];
        finish(b);
      }
    });
  } else if (b.pending) b.extrasWaiting.push([c, i - FLIGHT.maxPerBurst]);
  else extra(b, c, i - FLIGHT.maxPerBurst);
}

function extra(b: Burst, c: Change, j: number): void {
  const at = Math.max(now(), b.lastLanding + FLIGHT.extraWashAfterMs + j * FLIGHT.extraWashGapMs);
  after(at - now(), () => arrive(c, at, null));
}

function finish(b: Burst): void {
  // A queued burst that has not begun finishes after its own droplets, not when its window closes.
  if (b.open || b.pending || b.finishing || b.start === null) return;
  b.finishing = true;
  const n = b.changes.length;
  const report: BurstReport = {
    from: b.key,
    labels: b.changes.slice(0, FLIGHT.maxPerBurst).flatMap((c) => (c.o.label ? [c.o.label] : [])),
    more: Math.max(0, n - FLIGHT.maxPerBurst),
    at: Math.max(now(), b.lastLanding + FLIGHT.lineAfterMs),
  };
  after(report.at - now(), () => {
    for (const fn of [...listeners]) fn(report);
  });
  const done = b.off ? now() : Math.max(now(), b.lastLanding + FLIGHT.queueGapMs);
  after(done - now(), () => {
    bursts = bursts.filter((x) => x !== b);
    const next = bursts.find((x) => x.key === b.key && !x.off);
    if (next && next.start === null) begin(next);
  });
}

/** More than two bursts waiting from one Dot: the older ones apply at once, washes only. */
function trim(key: Element): void {
  const waiting = bursts.filter((b) => b.key === key && !b.off && b.start === null);
  while (waiting.length > FLIGHT.queueKeep) {
    const old = waiting.shift()!;
    old.closeCancel();
    bursts = bursts.filter((x) => x !== old);
    old.changes.forEach((c, j) => {
      c.o.onLand?.();
      after(j * FLIGHT.offWashGapMs, () => {
        wash(c.o.wash || c.to);
        c.resolve({ flew: false, landedAt: now() });
      });
    });
    const report: BurstReport = {
      from: key,
      labels: old.changes.slice(0, FLIGHT.maxPerBurst).flatMap((c) => (c.o.label ? [c.o.label] : [])),
      more: Math.max(0, old.changes.length - FLIGHT.maxPerBurst),
      at: now(),
    };
    for (const fn of [...listeners]) fn(report);
  }
}

/* ---------- one flight ---------- */

type Pt = { x: number; y: number };

/** Launches one droplet now. Returns the landing time. */
function launch(c: Change): number {
  const t0 = now();
  const size: DropletSize = FLIGHT.kinds[c.kind] ?? 'standard';
  const level = levelRank();
  if (!c.from.isConnected || !c.to.isConnected || level < FLIGHT.minLevel[size]) return arrive(c, t0, null);

  const a = centre(c.from.getBoundingClientRect());
  const b = aim(c.to, c.o.at ?? (c.to.getAttribute('data-target-at') === 'left' ? 'left' : 'center'));
  const H = window.innerHeight;
  const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const ms = clamp(dist * FLIGHT.msPerPx, FLIGHT.minMs, FLIGHT.maxMs) * (size === 'tiny' ? FLIGHT.tinyFactor : 1);
  const lift = Math.min(FLIGHT.liftMax, dist * FLIGHT.liftFactor);
  const my = (a.y + b.y) / 2;
  const cp = {
    x: (a.x + b.x) / 2,
    y: my - lift < FLIGHT.dipFloor ? Math.min(H - FLIGHT.edgePad, my + lift * FLIGHT.dipFactor) : my - lift,
  };
  const pos = (u: number): Pt => ({
    x: (1 - u) * (1 - u) * a.x + 2 * (1 - u) * u * cp.x + u * u * b.x,
    y: (1 - u) * (1 - u) * a.y + 2 * (1 - u) * u * cp.y + u * u * b.y,
  });

  if (c.kind !== 'answer') c.from.dispatchEvent(new CustomEvent('motion:launch', { bubbles: true, detail: { kind: c.kind } }));

  const fx = layer();
  const drops = FLIGHT.sizes[size].map((sz, i) => {
    const e = document.createElement('span');
    e.className = 'dot fly';
    e.style.setProperty('--d', `${sz}px`);
    e.innerHTML = '<i></i><b></b>';
    fx.appendChild(e);
    const total = i ? ms : ms + FLIGHT.bloomMs;
    return { e, anim: play(e, dropletFrames(i, pos, b, ms, total), { duration: total, easing: 'linear' }, t0) };
  });

  // A target that goes mid-air: the droplets stop where they are and fade (Proposed, 120ms).
  let lost = false;
  const watch = onFrame(() => {
    if (lost || c.to.isConnected) return;
    lost = true;
    for (const d of drops) {
      d.anim?.pause();
      play(d.e, [{ opacity: 1 }, { opacity: 0 }], { duration: FLIGHT.lostFadeMs, fill: 'forwards', composite: 'replace' });
    }
  });

  const landing = t0 + ms;
  after(ms, () => {
    watch();
    if (lost) {
      c.resolve({ flew: true, landedAt: landing });
      return;
    }
    arrive(c, landing, b);
  });
  after(ms + Math.max(FLIGHT.bloomMs, FLIGHT.lostFadeMs), () => {
    watch();
    for (const d of drops) {
      stop(d.anim);
      d.e.remove();
    }
  });
  return landing;
}

/** The droplet's frames: on the curve with `eio`, each trailing the one before; the head swells mid-flight and then
 *  opens and fades where it lands (bloom). A trail droplet shows only while it moves (DS.js L169-170). */
function dropletFrames(i: number, pos: (u: number) => Pt, b: Pt, ms: number, total: number): Keyframe[] {
  const lag = i * FLIGHT.trailLag;
  const at = (k: number, op?: number): Keyframe => {
    const u = clamp(eio(k) * (1 + lag) - lag);
    const p = pos(u);
    const s = i ? 1 : 1 + Math.sin(Math.PI * u) * FLIGHT.headSwell;
    return {
      offset: (k * ms) / total,
      transform: `translate(${f(p.x)}px,${f(p.y)}px) translate(-50%,-50%) scale(${f(s)})`,
      opacity: op ?? (u > 0 && (i === 0 || u < 1) ? 1 : 0),
    };
  };
  const kOn = i ? eioInv(lag / (1 + lag)) : 0;
  const frames: Keyframe[] = [];
  for (let s = 0; s <= FLIGHT.samples; s++) {
    const k = s / FLIGHT.samples;
    if (kOn >= (s - 1) / FLIGHT.samples && kOn < k) frames.push(at(kOn, 0), at(kOn, 1));
    if (k === 0) frames.push(at(0, 0), ...(i ? [] : [at(0, 1)]));
    else frames.push(at(k, k === 1 ? 1 : undefined));
  }
  if (!i) {
    for (let s = 1; s <= FLIGHT.samples; s++) {
      const k = s / FLIGHT.samples;
      const t = eout(k);
      frames.push({
        offset: (ms + k * FLIGHT.bloomMs) / total,
        transform: `translate(${f(b.x)}px,${f(b.y)}px) translate(-50%,-50%) scale(${f(1 + t * FLIGHT.bloomScale)})`,
        opacity: f(1 - t),
      });
    }
  }
  return frames;
}

/** The change shows: ripple, wash or pop, the Dot's pulse for a return, then the caller's own swap. */
function arrive(c: Change, at: number, p: Pt | null): number {
  const level = levelRank();
  if (c.kind === 'ask') {
    if (level >= 1) pop(c.to, c.o.pop ?? FLIGHT.popFace, at);
    // An agent not on screen: its team's row washes as the stack pops (DS.js L316).
    if (level >= 1 && c.o.wash) wash(c.o.wash, at);
  } else if (c.kind === 'answer') {
    c.to.dispatchEvent(new CustomEvent('motion:land', { bubbles: true, detail: { kind: c.kind } }));
  } else {
    const onChip = c.to.matches('.fx-edge > .edge');
    const size = c.o.ripple ?? (c.kind === 'open' ? FLIGHT.rippleSize.title : onChip ? FLIGHT.rippleSize.chip : FLIGHT.rippleSize.row);
    if (p && size && level >= 2) ripple(p, size, at);
    const w = c.o.wash === false ? null : c.o.wash || c.to;
    if (w) wash(w, at);
    else if (onChip) wash(c.to, at);
  }
  c.o.onLand?.();
  c.resolve({ flew: !!p, landedAt: at });
  return at;
}

/* ---------- where ---------- */

const centre = (r: DOMRect): Pt => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 });

/** A row or a title is aimed at left + 14; anything else at its centre. A thing still arriving (a transform moving
 *  it right now) reads at its final place, as the mock reads the layout box (DS.js L106-108). */
function aim(el: Element, at: 'left' | 'center'): Pt {
  const r = el.getBoundingClientRect();
  let dx = 0;
  let dy = 0;
  const all = typeof document.getAnimations === 'function' ? document.getAnimations() : [];
  for (const anim of all) {
    const t = (anim.effect as KeyframeEffect | null)?.target;
    if (!t || anim.playState !== 'running' || !(t === el || t.contains(el)) || t.closest('.fx-layer')) continue;
    const tf = getComputedStyle(t).transform;
    if (!tf || tf === 'none' || typeof DOMMatrixReadOnly === 'undefined') continue;
    const m = new DOMMatrixReadOnly(tf);
    if (m.a === 1 && m.b === 0 && m.c === 0 && m.d === 1) {
      dx += m.e;
      dy += m.f;
    }
  }
  return { x: (at === 'left' ? r.left + FLIGHT.aimLeft : r.left + r.width / 2) - dx, y: r.top + r.height / 2 - dy };
}
