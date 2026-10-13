/* ui/app/stick-scroll.js — A thread that stays at its newest line while you are there, and stays put when you are not
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   const stick = stickScroll(el, {pill, onBottom})   one per scrolling element; calling it again returns the same one
     el        the element that scrolls (overflow:auto), newest content last
     pill      optional: the button to use as the "New reply ↓" pill; by default one is made and placed after `el`
     onBottom  optional: called when the reader gets back to the bottom (scrolling down, or the pill)
   stick.update(fn, {fresh, prepended})   run fn (it changes el's content), then keep the reader where they were:
     at the bottom, el stays pinned to it; scrolled up, nothing moves. fresh: new content worth telling a reader who
     is scrolled up about (true, or the pill's words, e.g. 'Latest'); prepended: fn added older content at the top.
   stick.atBottom                         whether the reader is following the newest line
   stick.toBottom()                       go to the bottom and follow it (the first paint, a message just sent)
   stick.release()                        stop following without moving (a jump to an older message)
   stick.restore({atBottom, top}, opts)   for an element rebuilt wholesale: take on the old one's place
   stick.stop()                           disconnect it

   "At the bottom" changes only when the reader scrolls (wheel, touch, keys, scrollbar), never because content arrived,
   an image loaded or the box changed size: those re-pin a reader who is at the bottom and leave anyone else alone. */
'use strict';

const STICK_SLACK = 40;                     // px from the end that still counts as at the bottom, everywhere
const STICKS = new WeakMap();
let STICK_PRESSED = false;                  // a pointer held down: dragging a scrollbar keeps counting as the reader
addEventListener('pointerup', () => { STICK_PRESSED = false; }, true);
addEventListener('pointercancel', () => { STICK_PRESSED = false; }, true);
function stickScroll(el, opts = {}) {
  let s = STICKS.get(el);
  if (s) { if (opts.onBottom) s.onBottom = opts.onBottom; return s; }
  let height = 0, client = 0, ours = null, input = -1e9, pill = opts.pill || null;
  const measure = () => { height = el.scrollHeight; client = el.clientHeight; };
  const set = top => {
    el.scrollTop = top; ours = el.scrollTop; measure();
    requestAnimationFrame(() => { ours = null; });
  };
  const pin = () => set(el.scrollHeight);
  const place = () => {
    if (!pill || pill.hidden) return;
    pill.style.left = `${el.offsetLeft + el.offsetWidth / 2}px`;
    pill.style.top = `${el.offsetTop + el.offsetHeight - 10}px`;
  };
  const hide = () => { if (pill) pill.hidden = true; };
  const show = label => {
    if (!pill) {
      pill = Object.assign(document.createElement('button'), {type: 'button'});
      el.after(pill);
    }
    const host = pill.parentElement;
    if (host && getComputedStyle(host).position === 'static') host.style.position = 'relative';
    pill.classList.add('stick-pill');
    pill.onclick = () => s.toBottom();
    pill.innerHTML = `${esc(typeof label === 'string' ? label : 'New reply')} <span aria-hidden="true">↓</span>`;
    pill.hidden = false;
    place();
  };
  const reached = () => { hide(); s.onBottom?.(); };
  const resize = new ResizeObserver(() => {
    if (!el.isConnected) return;
    if (s.atBottom) pin(); else measure();
    place();
  });
  const watch = new MutationObserver(records => {
    for (const r of records) {
      for (const n of r.removedNodes) if (n.nodeType === 1) resize.unobserve(n);
      for (const n of r.addedNodes) if (n.nodeType === 1) resize.observe(n);
    }
  });
  const touched = ev => { input = performance.now(); if (ev.type === 'pointerdown') STICK_PRESSED = true; };
  const onScroll = () => {
    if (!STICK_PRESSED && performance.now() - input > 600) {
      if (ours != null && Math.abs(el.scrollTop - ours) <= 1) return;      // our own scroll
      if (el.scrollHeight !== height || el.clientHeight !== client) {     // the content or the box changed size
        measure();
        if (s.atBottom) pin();
        return;
      }
    }
    ours = null; measure();
    const was = s.atBottom;
    s.atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLACK;
    if (s.atBottom && (!was || pill && !pill.hidden)) reached();
  };
  const events = ['wheel', 'touchstart', 'touchmove', 'pointerdown', 'keydown'];
  s = {
    atBottom: true,
    onBottom: opts.onBottom || null,
    update(fn, {fresh = false, prepended = false} = {}) {
      const top = el.scrollTop, was = el.scrollHeight;
      fn();
      if (s.atBottom) pin();
      else {
        set(prepended ? top + el.scrollHeight - was : top);
        if (fresh) show(fresh);
      }
    },
    toBottom() { s.atBottom = true; pin(); reached(); },
    release() { s.atBottom = false; },
    restore(was, {fresh = false} = {}) {
      if (!was || was.atBottom) { s.toBottom(); return; }
      s.atBottom = false; set(was.top);
      if (fresh) show(fresh);
    },
    stop() {
      resize.disconnect(); watch.disconnect();
      el.removeEventListener('scroll', onScroll);
      for (const e of events) el.removeEventListener(e, touched);
      STICKS.delete(el);
    },
  };
  STICKS.set(el, s);
  el.style.overflowAnchor = 'none';         // the browser's own anchoring would fight the pin
  el.addEventListener('scroll', onScroll, {passive: true});
  for (const e of events) el.addEventListener(e, touched, {passive: true});
  resize.observe(el);
  for (const child of el.children) resize.observe(child);
  watch.observe(el, {childList: true});
  if (pill) { pill.classList.add('stick-pill'); pill.onclick = () => s.toBottom(); }
  measure();
  return s;
}
