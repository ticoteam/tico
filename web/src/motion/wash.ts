/* What lands: the gray wash on the thing that changed (B5), the face pop (B8), the ripple (B4), and the one layer
   above the sheets that flights and ripples are drawn on. Numbers in FLIGHT. */
import { after, now, play, stop } from './clock';
import { FLIGHT } from './config';
import { eio, eout, f, sample } from './ease';

let fx: HTMLElement | null = null;

/** The one fixed layer for flights and ripples: above sheets and phone cards, below dialogs (`--z-fx`, motion.md 5). */
export function layer(): HTMLElement {
  if (fx?.isConnected) return fx;
  fx = document.createElement('div');
  fx.className = 'fx-layer';
  fx.setAttribute('aria-hidden', 'true');
  document.body.appendChild(fx);
  return fx;
}

const washing = new WeakMap<Element, { anim: Animation | null; made: Element | null; rel: boolean }>();

/** Washes the opacity of the wash layer: up to 1 in the first 8%, then an ease-in-out fall (DS.js L176). */
const washFrames = (): Keyframe[] => {
  const frames: Keyframe[] = [
    { offset: 0, opacity: 0 },
    { offset: FLIGHT.washRise, opacity: 1 },
  ];
  for (let s = 1; s <= FLIGHT.samples; s++) {
    const k = FLIGHT.washRise + ((1 - FLIGHT.washRise) * s) / FLIGHT.samples;
    frames.push({ offset: k, opacity: f(1 - eio((k - FLIGHT.washRise) / (1 - FLIGHT.washRise))) });
  }
  return frames;
};

/**
 * A short breath of gray on the thing that changed: 15% ink, peak at 88ms, gone at 1100ms. It shows at every motion
 * level ("all that is left when motion is off"). Again on the same thing restarts it. Uses the element's own
 * `<s class="wash">` child when it has one (the mock's markup); otherwise lays one in and takes it out after.
 */
export function wash(el: Element, startAt = now()): void {
  // An edge chip is not washed: it arrives (B18).
  if (el.matches('.fx-edge > .edge')) {
    el.classList.add('in');
    return;
  }
  const prev = washing.get(el);
  if (prev) stop(prev.anim);
  let w = el.querySelector(':scope > .wash');
  let made: Element | null = null;
  if (!w) {
    w = made = document.createElement('s');
    w.className = 'wash';
    w.setAttribute('aria-hidden', 'true');
    el.prepend(w);
  } else if (prev?.made === w) {
    made = w;
  }
  const rel = prev?.rel ?? getComputedStyle(el).position === 'static';
  el.classList.add('is-washing');
  if (rel) el.classList.add('is-washing-rel');
  const anim = play(w, washFrames(), { duration: FLIGHT.washMs, easing: 'linear', fill: 'none' }, startAt);
  const mine = { anim, made, rel };
  washing.set(el, mine);
  after(FLIGHT.washMs - (now() - startAt), () => {
    if (washing.get(el) !== mine) return;
    washing.delete(el);
    stop(anim);
    made?.remove();
    el.classList.remove('is-washing', 'is-washing-rel');
  });
}

/** The asked agent's face at landing: `scale(1 + (top-1)*sin(πk))`, 420ms (B8). Adds to any transform it has. */
export function pop(el: Element, top: number = FLIGHT.popFace, startAt = now()): void {
  play(
    el,
    sample((k) => ({ transform: `scale(${f(1 + (top - 1) * Math.sin(Math.PI * k))})` }), FLIGHT.samples),
    { duration: FLIGHT.popMs, easing: 'linear', composite: 'add' },
    startAt,
  );
}

/** The landing ring: 40px, `scale(size*(.3+.7*eout(k)))`, `opacity (1-k)*.9`, 460ms (B4). Off with FLIGHT.ripple. */
export function ripple(p: { x: number; y: number }, size: number, startAt = now()): void {
  if (!FLIGHT.ripple) return;
  const e = document.createElement('i');
  e.className = 'rip';
  layer().appendChild(e);
  play(
    e,
    sample(
      (k) => ({
        transform: `translate(${f(p.x)}px,${f(p.y)}px) scale(${f(size * (0.3 + 0.7 * eout(k)))})`,
        opacity: f((1 - k) * 0.9),
      }),
      FLIGHT.samples,
    ),
    { duration: FLIGHT.rippleMs, easing: 'linear' },
    startAt,
  );
  after(FLIGHT.rippleMs - (now() - startAt), () => e.remove());
}
