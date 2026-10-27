/* ui/app/pull-refresh.js — Pull to refresh on a touch screen
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// An installed phone app has no reload button, so a list that drifted stays drifted. Pulling down at the very top
// of `host` and letting go past the mark runs `refresh` (a promise) under a spinner. Only a touch that starts with
// everything above it scrolled to the top and first moves down is a pull; any other touch scrolls as usual. While
// pulling, only the spinner moves: the page is held still (no scroll, no bounce, no browser reload).
const PULL_ARM = 64;          // the spinner's travel, in px, past which letting go refreshes
const PULL_MAX = 96;
const PULL_DRAG = 0.55;       // the spinner follows the finger at this rate, so a pull is a deliberate one
function pullAtTop(el) {
  for (let n = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) if (n.scrollTop > 0) return false;
  return (document.scrollingElement?.scrollTop || 0) <= 0;
}
function pullToRefresh(host, refresh, {signal, skip} = {}) {
  if (!host) return;
  let pull = null, busy = false, mark = null;
  const draw = travel => {
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
  };
  const clear = () => { mark?.remove(); mark = null; };
  signal?.addEventListener('abort', clear);
  host.addEventListener('touchstart', ev => {
    pull = null;
    if (busy || ev.touches.length !== 1 || skip?.() || !pullAtTop(ev.target)) return;
    pull = {x: ev.touches[0].clientX, y: ev.touches[0].clientY, on: false, travel: 0};
  }, {passive: true, signal});
  host.addEventListener('touchmove', ev => {
    if (!pull) return;
    if (ev.touches.length !== 1 || skip?.()) { pull = null; clear(); return; }
    const dy = ev.touches[0].clientY - pull.y, dx = ev.touches[0].clientX - pull.x;
    // Decided on the first move: a browser lets a touch's scroll be held only before it starts.
    if (!pull.on) {
      if (dy <= 0 || Math.abs(dx) >= dy || !ev.cancelable) { pull = null; return; }   // up, or sideways (a board's columns)
      pull.on = true;
    }
    ev.preventDefault();
    pull.travel = Math.max(0, Math.min(PULL_MAX, dy * PULL_DRAG));
    draw(pull.travel);
  }, {passive: false, signal});
  host.addEventListener('touchend', async () => {
    const done = pull; pull = null;
    if (!done?.on) return;
    if (done.travel < PULL_ARM) { clear(); return; }
    busy = true;
    draw(PULL_ARM);
    mark.classList.add('spinning');
    mark.setAttribute('role', 'status');
    mark.removeAttribute('aria-hidden');
    mark.setAttribute('aria-label', 'Refreshing');
    try { await refresh(); } catch (e) { console.error(e); } finally { busy = false; clear(); }
  }, {signal});
  host.addEventListener('touchcancel', () => { pull = null; if (!busy) clear(); }, {signal});
}
