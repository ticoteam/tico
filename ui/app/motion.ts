/* ui/app/motion.ts — How the app moves, the parts a stylesheet cannot do alone: lists that patch in place and slide
   (motionSwap), the page change (motionPage), overlays opening and closing (motionOpen, motionClose), the sidebar's
   sliding current-row bar, counts that tick when their number changes, where a menu grows from, the check a task draws
   when it is marked done
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Every effect here is short (styles/motion.css), moves only transform and opacity, waits on nothing, never plays on a
   page's first paint, and is skipped under reduced motion. No other file tests for reduced motion, names a duration
   or an easing, or starts an animation of its own: it calls these. */
'use strict';

// The one reduced-motion test: the system's setting, or data-motion="reduce" on <html> (a setting, a check).
const motionStill = (): boolean => matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion === 'reduce';
// The motion spec's numbers for what a script animates; styles/motion.css holds the same ones for the classes.
const MOTION_FAST = 120, MOTION_DUR = 180, MOTION_SLOW = 280, MOTION_EASE = 'cubic-bezier(.2,0,0,1)';

// ---- lists. motionSwap(root, html) is `root.innerHTML = html` for a list that is drawn again and again (a live
// event, a poll, a filter): the elements that carry a key (data-key, or the attribute opts.key names) are matched by
// it, wherever under root they are (core.ts keyedPatch). One whose markup is unchanged is left alone, the same node,
// with its hover, focus, selection, scroll and running animations. One whose markup changed is patched in place. One
// that moved slides from where it was (FLIP on translate, 180ms; 280ms into another group). A new one comes in with
// .m-enter; one that is gone fades for 120ms where it was, inert, and then the rows below close up (180ms).
// Groups: give the groups keys and their rows keys and call once on the box around them; it recurses, and the keys
// of rows and groups share one set, so prefix them ("g:", "t:").
// The DOM is what `html` says when the call returns, except for a second call for the same root before the next
// paint: that one waits (the newest wins) and is built on a following frame, so a burst builds a list at most once a
// frame. A leaving row stays in the page until its fade ends: wait for it to be gone before reading the list back.
// Nothing moves on a first fill (an empty root, a skeleton, a box someone else drew), past opts.max changed rows (40),
// in a hidden window or under reduced motion: the list is patched and that is all.
const MOTION_MAX = 40;
interface MotionNext { html: string; attr: string; max: number }
/** What motionSwap knows of a box it draws: the markup and the children it last drew, the paint it drew them in, the
 * markup waiting for the next frame, and the leaving rows still fading (with the key each had). */
interface MotionList {
  html: string | null; attr: string; keyed: boolean; nodes: ChildNode[];
  paint: number; next: MotionNext | null; ghosts: Map<Element, string>;
}
/** Where a keyed element was before a build: its corner and bottom on the screen, and its parent. */
interface MotionRect { x: number; y: number; bottom: number; parent: Element | null }
const MOTION_LISTS = new WeakMap<Element, MotionList>();
const MOTION_WAITING = new Set<Element>();
const MOTION_FLIPS = new WeakMap<Element, Animation>();
// Builds are counted by paint: MOTION_PAINT goes up once each frame has been drawn, and a root is built once per count.
let MOTION_PAINT = 0, MOTION_TICKING = false;
/** Every list build since the page loaded: the browser scripts hold "one build a frame" with it. */
let MOTION_SWAP_BUILDS = 0;
function motionSwap(root: Element, html: string, opts: {key?: string; max?: number} = {}): void {
  let st = MOTION_LISTS.get(root);
  if (!st) { st = {html: null, attr: 'data-key', keyed: false, nodes: [], paint: -1, next: null, ghosts: new Map()}; MOTION_LISTS.set(root, st); }
  st.next = {html, attr: opts.key || 'data-key', max: opts.max ?? MOTION_MAX};
  if (st.paint === MOTION_PAINT && !document.hidden) { MOTION_WAITING.add(root); motionFrame(); return; }
  motionBuild(root, st);
}
// Each frame builds the lists that waited for it, then counts the paint in a task of its own, which runs once the
// frame is drawn.
function motionFrame(): void {
  if (MOTION_TICKING) return;
  MOTION_TICKING = true;
  requestAnimationFrame(() => {
    for (const root of [...MOTION_WAITING]) { const st = MOTION_LISTS.get(root); if (st && st.paint !== MOTION_PAINT) motionBuild(root, st); }
    setTimeout(() => { MOTION_PAINT++; MOTION_TICKING = false; if (MOTION_WAITING.size) motionFrame(); });
  });
}
// A hidden window draws no frames: what waited for one is built now, with no motion.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) for (const root of [...MOTION_WAITING]) { const st = MOTION_LISTS.get(root); if (st) motionBuild(root, st); }
});
const motionKids = (root: Element): ChildNode[] => [...root.childNodes].filter(n => !PATCH_GHOSTS.has(n));
function motionBuild(root: Element, st: MotionList): void {
  const next = st.next;
  st.next = null; MOTION_WAITING.delete(root);
  if (!next) return;
  const kids = motionKids(root);
  const owned = st.html !== null && kids.length === st.nodes.length && kids.every((n, i) => n === st.nodes[i]);
  if (owned && st.html === next.html && st.attr === next.attr) return;
  MOTION_SWAP_BUILDS++;
  st.paint = MOTION_PAINT;
  if (!document.hidden) motionFrame();
  const {html, attr, max} = next, has = `[${attr}]`;
  const moving = !motionStill() && !document.hidden && root.isConnected;
  if (!owned || !st.keyed || st.attr !== attr) {
    // A first fill: nothing to match, so nothing moves. Rows that replace a skeleton fade in, in the skeleton's place.
    const loaded = moving && owned && !!root.querySelector(':scope > .skel, :scope > [aria-busy="true"]');
    for (const ghost of st.ghosts.keys()) ghost.remove();
    st.ghosts.clear();
    keyedFill(root, html, attr);
    Object.assign(st, {html, attr, nodes: [...root.childNodes], keyed: !!root.querySelector(has)});
    if (loaded && st.keyed && root.animate) root.animate({opacity: [0, 1]}, {duration: MOTION_FAST, easing: MOTION_EASE});
    return;
  }
  const plan = keyedPlan(root, html, attr);
  // A row that is back while its old self still fades takes its place at once.
  for (const [ghost, key] of st.ghosts) if (plan.keys.has(key)) { ghost.remove(); st.ghosts.delete(ghost); }
  // Every place is read before anything is written; the new places are read once, after the caller has finished.
  const first = moving && plan.entered + plan.changed + plan.gone.length <= max ? motionRects(root, has) : null;
  const done = keyedApply(plan);
  if (first && done.moved <= max) {
    root.classList.add('m-list');
    for (const el of done.entered) motionEnter(el);
    motionLeave(root, st, done.left, attr);
    queueMicrotask(() => motionFlip(root, has, first));
  } else {
    for (const el of done.left) el.remove();
    for (const el of root.querySelectorAll(has)) MOTION_FLIPS.get(el)?.cancel();
  }
  Object.assign(st, {html, nodes: motionKids(root), keyed: !!root.querySelector(has)});
}
function motionRects(root: Element, has: string): Map<Element, MotionRect> {
  const rects = new Map<Element, MotionRect>();
  for (const el of root.querySelectorAll(has)) {
    const r = el.getBoundingClientRect();
    if (r.width || r.height) rects.set(el, {x: r.left, y: r.top, bottom: r.bottom, parent: el.parentElement});
  }
  return rects;
}
// Each kept element starts from where it was and settles where it is now. A slide still running is replaced by one
// from where the element is on the screen, so a second change never jumps. An element inside a keyed one that slides
// is carried by it and only makes up the difference; one off the screen before and after is left alone.
function motionFlip(root: Element, has: string, first: Map<Element, MotionRect>): void {
  if (!root.isConnected || document.hidden) return;
  const els = [...root.querySelectorAll(has)];
  for (const el of els) MOTION_FLIPS.get(el)?.cancel();
  const last = els.map(el => el.getBoundingClientRect()), height = innerHeight;
  const carried = new Map<Element, {x: number; y: number}>();
  els.forEach((el, i) => {
    const a = first.get(el), b = last[i], up = el.parentElement?.closest(has);
    const base = (up && carried.get(up)) || {x: 0, y: 0};
    carried.set(el, base);
    if (!a || !b || !(b.width || b.height) || !el.animate) return;
    const x = a.x - b.left - base.x, y = a.y - b.top - base.y;
    if (Math.abs(x) < 0.5 && Math.abs(y) < 0.5) return;
    if ((a.bottom < 0 || a.y > height) && (b.bottom < 0 || b.top > height)) return;
    carried.set(el, {x: a.x - b.left, y: a.y - b.top});
    MOTION_FLIPS.set(el, el.animate({translate: [`${x}px ${y}px`, '0px 0px']},
      {duration: a.parent === el.parentElement ? MOTION_DUR : MOTION_SLOW, easing: MOTION_EASE, composite: 'add'}));
  });
}
function motionEnter(el: Element): void {
  const end = (ev: Event): void => {
    if (ev.target !== el) return;
    el.classList.remove('m-enter');
    el.removeEventListener('animationend', end); el.removeEventListener('animationcancel', end);
  };
  el.classList.add('m-enter');
  el.addEventListener('animationend', end); el.addEventListener('animationcancel', end);
}
// A leaving element stays where it was while it fades: it takes no press and no focus, is hidden from a screen reader
// and has given up its key, so the next build does not see it. Its other attributes stay until it is gone.
function motionLeave(root: Element, st: MotionList, left: Element[], attr: string): void {
  if (!left.length) return;
  for (const el of left) {
    st.ghosts.set(el, el.getAttribute(attr) ?? '');
    PATCH_GHOSTS.add(el);
    for (const keyed of [el, ...el.querySelectorAll(`[${attr}]`)]) keyed.removeAttribute(attr);
    el.setAttribute('inert', ''); el.setAttribute('aria-hidden', 'true');
    el.classList.remove('m-enter'); el.classList.add('m-leave');
  }
  setTimeout(() => {
    const here = left.filter(el => st.ghosts.delete(el) && el.isConnected);
    if (!here.length) return;
    const first = !motionStill() && !document.hidden && root.isConnected ? motionRects(root, `[${attr}]`) : null;
    for (const el of here) el.remove();
    if (first) queueMicrotask(() => motionFlip(root, `[${attr}]`, first));
  }, MOTION_FAST);
}

// ---- the page change. Only router.ts calls motionPage(draw): the new page is drawn at once and its content, #main
// alone, fades in for 120ms. The sidebar and the chat column are not part of it and no layer sits over the page, so a
// press lands where it is aimed from the first frame. A second change restarts the fade; a draw that left the page as
// it was (an address that sends on to another) does not fade.
let MOTION_PAGE: Animation | null = null;
function motionPage(draw: () => void): void {
  const main: HTMLElement | null = $('#main'), was = main?.firstChild ?? null;
  draw();
  MOTION_PAGE?.cancel(); MOTION_PAGE = null;
  if (!main || !main.animate || main.firstChild === was || motionStill() || document.hidden) return;
  MOTION_PAGE = main.animate({opacity: [0, 1]}, {duration: MOTION_FAST, easing: MOTION_EASE});
}

// ---- overlays, sheets and the chat column. motionOpen(el) shows el (a dialog with showModal unless it is open, a
// popover with showPopover, anything else by taking `hidden` off) and plays its entrance; motionClose(el) plays its
// exit, hides it the same way and resolves. What plays is the stylesheet's (styles/motion.css): a menu, a listbox
// or a popover pops from its anchor (.m-pop), a dialog rises, an element marked .m-sheet comes up from the bottom
// edge, anything else fades and rises (.m-enter); .m-leave is the way out. An open during a close, or a close during
// an open, plays the running animation backwards from where it is. Under reduced motion both are instant, and
// motionClose resolves at once. An open during a close resolves that close with the element still shown.
const MOTION_POPS = '[popover], [role="menu"], [role="listbox"], .m-pop';
const MOTION_CLOSING = new WeakMap<HTMLElement, {stop: () => void; done: Promise<void>}>();
const motionShown = (el: HTMLElement): boolean => el instanceof HTMLDialogElement ? el.open : el.hasAttribute('popover') ? el.matches(':popover-open') : !el.hidden;
function motionShow(el: HTMLElement, on: boolean): void {
  if (motionShown(el) === on) return;
  if (el instanceof HTMLDialogElement) { if (on) el.showModal(); else el.close(); }
  else if (el.hasAttribute('popover')) { if (on) el.showPopover(); else el.hidePopover(); }
  else el.hidden = !on;
}
// The entrance or exit running on `el` itself (not one inside it).
const motionRunning = (el: HTMLElement): Animation | undefined => el.getAnimations()
  .find(a => a instanceof CSSAnimation && a.playState === 'running' && a.effect instanceof KeyframeEffect && a.effect.target === el);
function motionOpen(el: HTMLElement): void {
  const closing = MOTION_CLOSING.get(el);
  if (closing) { closing.stop(); return; }
  motionShow(el, true);
  if (motionStill() || document.hidden) return;
  el.classList.remove('m-leave');
  if (el.matches(MOTION_POPS)) { el.classList.add('m-pop'); return; }
  el.classList.add('m-enter');
  const anim = motionRunning(el), end = (): void => el.classList.remove('m-enter');
  if (anim) anim.onfinish = anim.oncancel = end; else end();
}
function motionClose(el: HTMLElement): Promise<void> {
  const under = MOTION_CLOSING.get(el);
  if (under) return under.done;
  if (!motionShown(el)) return Promise.resolve();
  if (motionStill() || document.hidden || !el.isConnected) { motionShow(el, false); return Promise.resolve(); }
  let resolve = (): void => {}, over = false;
  const done = new Promise<void>(r => { resolve = r; });
  const opening = motionRunning(el);
  if (opening) opening.reverse(); else el.classList.add('m-leave');
  const anim = opening ?? motionRunning(el);
  const settle = (): void => { over = true; clearTimeout(timer); MOTION_CLOSING.delete(el); resolve(); };
  const end = (): void => {
    if (over) return;
    settle();
    motionShow(el, false);
    el.classList.remove('m-leave', 'm-enter');
  };
  // An animation a hidden window paused, or one the browser dropped, never ends: the close does not wait on it.
  const timer = setTimeout(end, 600);
  MOTION_CLOSING.set(el, {done, stop: () => {
    if (over) return;
    settle();
    const back = (): void => el.classList.remove('m-leave', 'm-enter');
    if (anim && anim.playState === 'running') { anim.onfinish = anim.oncancel = back; anim.reverse(); } else back();
  }});
  if (anim) anim.onfinish = anim.oncancel = end; else end();
  return done;
}

// ---- counts. A badge's number set through countSet ticks when it changes from one number to another: the old one
// slides up and out (drawn over the badge by ::before), the new one, the badge's own text, comes up from below. The
// first number drawn, a badge appearing or going away, and the same number again never tick. The badge's text is the
// new number throughout, so whoever reads it reads the count.
// The timer that ends a badge's tick, by badge (it was a property on the element).
const countTimers = new WeakMap<HTMLElement, number>();
function countTick(el: HTMLElement, was: string): void {
  if (motionStill() || !el.isConnected) return;
  const text = el.textContent;
  clearTimeout(countTimers.get(el));
  if (getComputedStyle(el).position === 'static') el.classList.add('m-tick-rel');   // a box for the old number
  el.dataset.tickWas = was;
  const span = document.createElement('span');
  span.className = 'm-tick-in';
  span.textContent = text;
  el.replaceChildren(span);
  el.classList.add('m-ticking');
  countTimers.set(el, setTimeout(() => {
    if (el.firstChild === span && el.childNodes.length === 1) el.textContent = span.textContent;
    el.classList.remove('m-ticking', 'm-tick-rel');
    delete el.dataset.tickWas;
  }, 200));
}
// Callers pass any Element (a badge drawn by a list, or a document query); only an HTML badge has a count to set.
function countSet(el: Element | null, value: unknown): void {
  if (!(el instanceof HTMLElement)) return;
  const text = String(value ?? '');
  const was = el.dataset.count;
  el.dataset.count = text;
  if (el.textContent !== text || el.firstElementChild) el.textContent = text;
  if (was && text && was !== text && !el.hidden) countTick(el, was);
}
// Counts drawn by a list's markup (the team list's, the Tasks tabs' and board columns'): each count is remembered by
// what it counts, and one that comes back drawn with another number ticks. `keyOf(badge)` names what it counts ('' to
// leave it alone); `when()` false skips the redraws a watch does not care about. A page's own counts are forgotten when
// the page changes, so returning to a page is a first paint.
function countWatch(root: Element | null, sel: string, keyOf: (el: HTMLElement) => string,
  {when = () => true, page = false}: {when?: () => boolean; page?: boolean} = {}): void {
  if (!root) return;
  const seen = new Map<string, string>();
  const remember = (el: HTMLElement): void => { const k = keyOf(el); if (k) seen.set(k, (el.textContent ?? '').trim()); };
  root.querySelectorAll<HTMLElement>(sel).forEach(remember);
  const badges = (node: Node): HTMLElement[] => {
    if (!(node instanceof HTMLElement)) return [];
    return node.matches(sel) ? [node] : [...node.querySelectorAll<HTMLElement>(sel)];
  };
  new MutationObserver(records => {
    if (!when()) return;
    // A redraw takes a row out and puts its new copy in: a count taken out now and not put back is gone ('').
    const gone = new Map<string, string | undefined>();
    for (const r of records) for (const n of r.removedNodes) for (const b of badges(n)) {
      const k = keyOf(b); if (k && !gone.has(k)) gone.set(k, seen.get(k));
    }
    for (const k of gone.keys()) seen.set(k, '');
    for (const r of records) {
      const found: HTMLElement[] = r.type === 'characterData'
        ? [r.target.parentElement?.closest<HTMLElement>(sel)].filter((el): el is HTMLElement => !!el)
        : [...r.addedNodes].flatMap(badges);
      for (const b of found) {
        if (b.classList.contains('m-ticking')) continue;
        const k = keyOf(b); if (!k) continue;
        const now = (b.textContent ?? '').trim(), was = gone.has(k) ? gone.get(k) : seen.get(k);
        seen.set(k, now);
        if (was && now && was !== now && b.isConnected) countTick(b, was);
      }
    }
  }).observe(root, {childList: true, subtree: true, characterData: true});
  if (page) addEventListener('hashchange', () => seen.clear());
}
countWatch($('#tree'), '.cnt', b => b.closest<HTMLElement>('[data-org]')?.dataset.org || '');
const motionTasksPage = (): boolean => /^#\/(tasks|task\/)/.test(location.hash);
countWatch($('#main'), '.tl-scope .cnt, .bcol > header .cnt', b => {
  const tab = b.closest<HTMLElement>('[data-view]'), col = b.closest<HTMLElement>('.bcol');
  return tab ? 'view:' + tab.dataset.view : col ? 'col:' + col.dataset.col : '';
}, {when: motionTasksPage, page: true});

// ---- the sidebar's current row: one accent bar (styles/sidebar.css .nav-ind) on whichever row is current, sliding
// to the new one when the page changes. sidebar.js marks the row (navCurSync, and the page links' own pass); this
// only watches the marks, so the list's markup is untouched. It snaps instead of sliding when it first appears, when
// rows above it fold or are added (the same row, moved), and when the sidebar is resized.
(function navIndicator(): void {
  const scroller: HTMLElement | null = $('#side .side-scroll');
  if (!scroller || !window.MutationObserver) return;
  const bar = document.createElement('span');
  bar.className = 'nav-ind';
  bar.setAttribute('aria-hidden', 'true');
  bar.hidden = true;
  scroller.prepend(bar);
  const ROWS = ':scope > .nav-link.cur, :scope > .nav-row > .nav-link.cur, :scope > .rd-lib > .nav-link.cur, #tree a.node.cur, #tree a.dept-label.cur, .inbox-list a.cur';
  let at = '', queued = 0;
  const place = (): void => {
    queued = 0;
    const row = [...scroller.querySelectorAll<HTMLElement>(ROWS)].find(el => el.checkVisibility ? el.checkVisibility() : el.offsetParent);
    if (!row) { bar.classList.remove('slide'); bar.hidden = true; at = ''; return; }
    const s = scroller.getBoundingClientRect(), r = row.getBoundingClientRect();
    if (!r.height) return;
    const inset = Math.max(4, Math.round(r.height * 0.22)), h = Math.max(4, r.height - 2 * inset);
    const x = r.left - s.left + scroller.scrollLeft, y = r.top - s.top + scroller.scrollTop + inset;
    const key = (row.getAttribute('href') || '') + '|' + (row.dataset.org || '');
    const slide = !bar.hidden && !!at && at !== key && !motionStill();
    bar.classList.toggle('slide', slide);
    bar.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px) scaleY(${(h / 16).toFixed(3)})`;
    bar.hidden = false;
    at = key;
  };
  const soon = (): void => { if (!queued) queued = requestAnimationFrame(place); };
  new MutationObserver(records => { if (records.some(r => r.target !== bar)) soon(); })
    .observe(scroller, {subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden', 'aria-expanded']});
  // A resize moves the same row: no slide.
  const snap = (): void => { at = ''; bar.hidden = true; soon(); };
  if (window.ResizeObserver) new ResizeObserver(snap).observe(scroller);
  document.fonts?.ready.then(snap);
  soon();
})();

// ---- where a menu grows from. A menu placed by script (a popover, the task list's pickers, the team's + menu)
// scales up from the side of it nearest the button that opened it; the others keep their stylesheet's origin.
let MOTION_PRESS: {el: Element | null; at: number} | null = null;
document.addEventListener('pointerdown', ev => { MOTION_PRESS = {el: eventElement(ev)?.closest('button, a, summary, [role=button]') ?? null, at: performance.now()}; }, true);
document.addEventListener('animationstart', ev => {
  const pop = ev.target;
  if (ev.animationName !== 'm-pop' || !(pop instanceof HTMLElement) || pop.closest('.ask-panel')) return;
  const recent = MOTION_PRESS && performance.now() - MOTION_PRESS.at < 1500 ? MOTION_PRESS.el : null;
  const controls = pop.id ? document.querySelector(`[aria-controls="${CSS.escape(pop.id)}"][aria-expanded="true"]`) : null;
  const anchor = [pop.anchor, controls, recent, document.activeElement].find(el => el instanceof Element && el !== document.body && !pop.contains(el));
  if (!anchor) return;
  const p = pop.getBoundingClientRect(), a = anchor.getBoundingClientRect();
  if (!p.width || !a.width) return;
  const x = Math.max(0, Math.min(p.width, a.left + a.width / 2 - p.left));
  const below = p.top + p.height / 2 >= a.top + a.height / 2;
  pop.style.transformOrigin = `${Math.round(x)}px ${below ? 0 : Math.round(p.height)}px`;
});

// ---- a task marked done (the list's status menu, a board card dropped on a done step): its check draws, and the row
// or card settles to muted while the list reads the server again. A list row then leaves its open view; a board's
// finished cards stay muted (styles/motion.css .bcard[data-done]). The task page's Done button draws its own check.
const MOTION_CHECK = '<svg class="m-check" viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 7.3l2.6 2.6L11 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function taskDoneMotion(key: string): void {
  for (const el of document.querySelectorAll<HTMLElement>(`#main [data-task-key="${CSS.escape(key)}"]`)) {
    if (el.classList.contains('m-task-done')) continue;
    el.classList.add('m-task-done');
    if (motionStill()) continue;
    const chip = el.querySelector(':scope > .tl-status');
    if (chip) chip.innerHTML = MOTION_CHECK + 'Done';
    else if (el.matches('.bcard')) el.insertAdjacentHTML('beforeend', MOTION_CHECK);
  }
}
document.addEventListener('click', ev => {
  const done = eventElement(ev)?.closest('[data-task-finish="done"]');
  if (done && !motionStill()) done.classList.add('m-checking');
}, true);
