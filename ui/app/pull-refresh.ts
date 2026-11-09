/* ui/app/pull-refresh.ts — Pull to refresh on a touch screen
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// An installed phone app has no reload button, so a list that drifted stays drifted. Pulling down at the very top
// of `host` and letting go past the mark runs `refresh` (a promise) under a spinner. Only a touch that starts with
// everything above it scrolled to the top and first moves down is a pull; any other touch scrolls as usual. While
// pulling, only the spinner moves: the page is held still (no scroll, no bounce, no browser reload).
const PULL_ARM = 64;          // the spinner's travel, in px, past which letting go refreshes
const PULL_MAX = 96;
const PULL_DRAG = 0.55;       // the spinner follows the finger at this rate, so a pull is a deliberate one
function pullAtTop(el: Element | null): boolean {
  for (let n: Element | null = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) if (n.scrollTop > 0) return false;
  return (document.scrollingElement?.scrollTop || 0) <= 0;
}
function pullToRefresh(host: Element | null, refresh: () => unknown, {signal, skip}: {signal?: AbortSignal; skip?: () => boolean} = {}): void {
  // Touch listeners are typed for an HTML element. A page's pull area is one (the tasks pane is a div).
  if (!(host instanceof HTMLElement)) return;
  type Pull = {x: number; y: number; on: boolean; travel: number};
  let pull: Pull | null = null, busy = false, mark: HTMLDivElement | null = null;
  // Each listener ends with the signal, when there is one: an absent signal is the same as no option at all.
  const listen = (options: AddEventListenerOptions): AddEventListenerOptions => signal ? {...options, signal} : options;
  const draw = (travel: number): HTMLDivElement => {
    if (!mark?.isConnected) {
      mark = document.createElement('div');
      mark.className = 'pull-spin';
      mark.setAttribute('aria-hidden', 'true');
      mark.innerHTML = '<span></span>';
      document.body.appendChild(mark);
    }
    mark.style.setProperty('--pull', travel + 'px');
    mark.style.setProperty('--turn', travel * 4 + 'deg');
    mark.classList.toggle('armed', travel >= PULL_ARM);
    return mark;
  };
  const clear = (): void => { mark?.remove(); mark = null; };
  signal?.addEventListener('abort', clear);
  host.addEventListener('touchstart', ev => {
    pull = null;
    // One finger: the touch's own point is there when the list has exactly one.
    const touch = ev.touches[0];
    if (busy || ev.touches.length !== 1 || !touch || skip?.() || !pullAtTop(eventElement(ev))) return;
    pull = {x: touch.clientX, y: touch.clientY, on: false, travel: 0};
  }, listen({passive: true}));
  host.addEventListener('touchmove', ev => {
    if (!pull) return;
    const touch = ev.touches[0];
    if (ev.touches.length !== 1 || !touch || skip?.()) { pull = null; clear(); return; }
    const dy = touch.clientY - pull.y, dx = touch.clientX - pull.x;
    // Decided on the first move: a browser lets a touch's scroll be held only before it starts.
    if (!pull.on) {
      if (dy <= 0 || Math.abs(dx) >= dy || !ev.cancelable) { pull = null; return; }   // up, or sideways (a board's columns)
      pull.on = true;
    }
    ev.preventDefault();
    pull.travel = Math.max(0, Math.min(PULL_MAX, dy * PULL_DRAG));
    draw(pull.travel);
  }, listen({passive: false}));
  host.addEventListener('touchend', async () => {
    const done = pull; pull = null;
    if (!done?.on) return;
    if (done.travel < PULL_ARM) { clear(); return; }
    busy = true;
    const spin = draw(PULL_ARM);
    spin.classList.add('spinning');
    spin.setAttribute('role', 'status');
    spin.removeAttribute('aria-hidden');
    spin.setAttribute('aria-label', 'Refreshing');
    try { await refresh(); } catch (e) { console.error(e); } finally { busy = false; clear(); }
  }, listen({}));
  host.addEventListener('touchcancel', () => { pull = null; if (!busy) clear(); }, listen({}));
}
