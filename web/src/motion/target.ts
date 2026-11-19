/* Where a flight lands (motion.md 5, "Marking a target").

   The convention: every element an action can change carries `data-target="<kind>:<id>"` (a field:
   `<kind>:<id>#<field>`), and may carry `data-target-at="left"` to be aimed at its left (rows, titles). A folded
   thing that stands for others it hides (a team row for its agents, a collapsed section for its rows) lists them:
   `data-target-holds="agent:fo agent:gm"`. A virtual list registers a resolver for rows it has not drawn.

   findTarget resolves, in order:
   1. the exact element, on screen: it;
   2. the exact element, scrolled out of its scroller: with `show`, it is jumped into view (no smooth scroll) and
      returned; otherwise the edge chip at the nearest edge of that scroller (B18), which shows when the droplet lands;
   3. a row a virtual list has not drawn: as 2, on the side its resolver gives (with `show`, the resolver reveals it
      and this returns null, so the caller waits for it);
   4. hidden in a folded sheet or section: the nearest visible ancestor that is itself a target, or a holder;
   5. and 6. on another page, or nowhere: null. No flight; the chat line stands alone. */
import { after } from './clock';
import { FLIGHT } from './config';
import { layer, wash } from './wash';

export type FindOpts = {
  /** Tico was told to show it ("go to", "open", "show"): jump it into view rather than land at the edge. */
  show?: boolean;
  /** The edge chip's words, when the change is off screen: "Software changed". */
  label?: string;
};

export type VirtualResolver = (
  kind: string,
  id: string,
) => { scroller: Element; side: 'above' | 'below'; reveal?: () => void } | null;

const resolvers = new Set<VirtualResolver>();

/** A virtual list tells where an undrawn row would be. Returns the unregister. */
export function registerTargetResolver(fn: VirtualResolver): () => void {
  resolvers.add(fn);
  return () => {
    resolvers.delete(fn);
  };
}

const quote = (s: string) => `"${s.replace(/["\\]/g, '\\$&')}"`;

export function findTarget(kind: string, id: string | number, opts: FindOpts = {}): Element | null {
  const key = `${kind}:${id}`;
  const el = document.querySelector(`[data-target=${quote(key)}]`);
  if (el) {
    if (folded(el)) return standIn(el);
    const side = offSide(el);
    if (!side) return el;
    if (opts.show) {
      el.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      return el;
    }
    return edgeChip(key, scrollerOf(el), side, opts.label, el, null);
  }
  for (const r of resolvers) {
    const v = r(kind, String(id));
    if (!v) continue;
    if (opts.show) {
      v.reveal?.();
      return null;
    }
    return edgeChip(key, v.scroller, v.side, opts.label, null, v.reveal ?? null);
  }
  const holder = document.querySelector(`[data-target-holds~=${quote(key)}]`);
  if (holder) return folded(holder) ? standIn(holder) : holder;
  return null;
}

/** Waits for a target that is not drawn yet (after Tico navigates), up to `ms` (motion.md 5, case 5). */
export function waitForTarget(kind: string, id: string | number, ms = 1500, opts: FindOpts = {}): Promise<Element | null> {
  const found = findTarget(kind, id, opts);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    const done = (el: Element | null) => {
      mo.disconnect();
      cancel();
      resolve(el);
    };
    const mo = new MutationObserver(() => {
      const el = findTarget(kind, id, opts);
      if (el) done(el);
    });
    mo.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-target', 'data-target-holds', 'hidden', 'inert'] });
    const cancel = after(ms, () => done(null));
  });
}

/* ---------- hidden, off screen ---------- */

function folded(el: Element): boolean {
  if (el.closest('[hidden], [inert]')) return true;
  return typeof el.checkVisibility === 'function' && el.isConnected && !el.checkVisibility();
}

function standIn(el: Element): Element | null {
  let at: Element | null = el.parentElement?.closest('[data-target], [data-target-holds]') ?? null;
  while (at && folded(at)) at = at.parentElement?.closest('[data-target], [data-target-holds]') ?? null;
  return at;
}

function scrollerOf(el: Element): Element | null {
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const y = getComputedStyle(p).overflowY;
    if (y === 'auto' || y === 'scroll') return p;
  }
  return null;
}

/** Null when at least half its height shows in its scroller and the window; else the side it is on. */
function offSide(el: Element): 'above' | 'below' | null {
  const r = el.getBoundingClientRect();
  if (!r.height) return null;
  let top = 0;
  let bottom = window.innerHeight;
  const sc = scrollerOf(el);
  if (sc) {
    const s = sc.getBoundingClientRect();
    top = Math.max(top, s.top);
    bottom = Math.min(bottom, s.bottom);
  }
  if (Math.min(r.bottom, bottom) - Math.max(r.top, top) >= r.height / 2) return null;
  return r.top + r.height / 2 < top ? 'above' : 'below';
}

/* ---------- the edge chip (B18) ---------- */

const chips = new Map<string, HTMLElement>();
const ARROW = { below: 'M6 9l6 6 6-6', above: 'M6 15l6-6 6 6' };

/**
 * The chip at the edge of the scroller nearest the change. It is laid out at once but shows only when a droplet lands
 * on it (or a wash reaches it at Off). Tapping it jumps to the row and washes it. It goes when tapped, when the row
 * comes into view, or, if nothing landed on it, after three seconds.
 */
function edgeChip(
  key: string,
  scroller: Element | null,
  side: 'above' | 'below',
  label: string | undefined,
  target: Element | null,
  reveal: (() => void) | null,
): HTMLElement | null {
  if (!FLIGHT.edgeChip) return null;
  const had = chips.get(key);
  if (had?.isConnected) return had;
  const box = scroller ? scroller.getBoundingClientRect() : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
  const holder = document.createElement('span');
  holder.className = 'fx-edge';
  holder.style.cssText = `left:${box.left}px;top:${box.top}px;width:${box.width}px;height:${box.height}px`;
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = side === 'above' ? 'edge up' : 'edge';
  chip.dataset.edgeFor = key;
  chip.innerHTML = `<svg class="ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ARROW[side]}"/></svg>`;
  if (label) chip.append(label);
  holder.appendChild(chip);
  layer().appendChild(holder);
  chips.set(key, chip);

  let io: IntersectionObserver | null = null;
  const off = () => {
    io?.disconnect();
    scroller?.removeEventListener('scroll', onScroll);
    holder.remove();
    if (chips.get(key) === chip) chips.delete(key);
  };
  const onScroll = () => {
    if (!target) off();
  };
  chip.addEventListener('click', () => {
    off();
    if (target?.isConnected) {
      target.scrollIntoView({ block: 'center', behavior: 'instant' });
      wash(target);
    } else reveal?.();
  });
  if (target && typeof IntersectionObserver !== 'undefined') {
    io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && off(), { threshold: 0.5 });
    io.observe(target);
  }
  scroller?.addEventListener('scroll', onScroll, { passive: true });
  // Nothing landed in three seconds, or the row or its list went away: the chip goes too.
  let age = 0;
  const check = () => {
    age += 1000;
    if ((age >= 3000 && !chip.classList.contains('in')) || target?.isConnected === false || scroller?.isConnected === false) off();
    else if (holder.isConnected) after(1000, check);
  };
  after(1000, check);
  return chip;
}
