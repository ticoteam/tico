/* ui/app/motion.js — The state changes a stylesheet cannot see: the sidebar's sliding current-row bar, counts that
   tick when their number changes, where a menu grows from, the check a task draws when it is marked done
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Every effect here is short (styles/motion.css), moves only transform and opacity, waits on nothing, never plays on a
   page's first paint, and is skipped under reduced motion. */
'use strict';

const motionStill = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---- counts. A badge's number set through countSet ticks when it changes from one number to another: the old one
// slides up and out (drawn over the badge by ::before), the new one, the badge's own text, comes up from below. The
// first number drawn, a badge appearing or going away, and the same number again never tick. The badge's text is the
// new number throughout, so whoever reads it reads the count.
function countTick(el, was) {
  if (motionStill() || !el.isConnected) return;
  const text = el.textContent;
  clearTimeout(el._countTick);
  if (getComputedStyle(el).position === 'static') el.classList.add('m-tick-rel');   // a box for the old number
  el.dataset.tickWas = was;
  const span = document.createElement('span');
  span.className = 'm-tick-in';
  span.textContent = text;
  el.replaceChildren(span);
  el.classList.add('m-ticking');
  el._countTick = setTimeout(() => {
    if (el.firstChild === span && el.childNodes.length === 1) el.textContent = span.textContent;
    el.classList.remove('m-ticking', 'm-tick-rel');
    delete el.dataset.tickWas;
  }, 200);
}
function countSet(el, text) {
  if (!el) return;
  text = String(text ?? '');
  const was = el.dataset.count;
  el.dataset.count = text;
  if (el.textContent !== text || el.firstElementChild) el.textContent = text;
  if (was && text && was !== text && !el.hidden) countTick(el, was);
}
// Counts drawn by a list's markup (the team list's, the Tasks tabs' and board columns'): each count is remembered by
// what it counts, and one that comes back drawn with another number ticks. `keyOf(badge)` names what it counts ('' to
// leave it alone); `when()` false skips the redraws a watch does not care about. A page's own counts are forgotten when
// the page changes, so returning to a page is a first paint.
function countWatch(root, sel, keyOf, {when = () => true, page = false} = {}) {
  if (!root) return;
  const seen = new Map();
  const remember = el => { const k = keyOf(el); if (k) seen.set(k, el.textContent.trim()); };
  root.querySelectorAll(sel).forEach(remember);
  const badges = node => node.nodeType !== 1 ? [] : node.matches(sel) ? [node] : [...node.querySelectorAll(sel)];
  new MutationObserver(records => {
    if (!when()) return;
    // A redraw takes a row out and puts its new copy in: a count taken out now and not put back is gone ('').
    const gone = new Map();
    for (const r of records) for (const n of r.removedNodes) for (const b of badges(n)) {
      const k = keyOf(b); if (k && !gone.has(k)) gone.set(k, seen.get(k));
    }
    for (const k of gone.keys()) seen.set(k, '');
    for (const r of records) {
      const found = r.type === 'characterData' ? [r.target.parentElement?.closest(sel)].filter(Boolean) : [...r.addedNodes].flatMap(badges);
      for (const b of found) {
        if (b.classList.contains('m-ticking')) continue;
        const k = keyOf(b); if (!k) continue;
        const now = b.textContent.trim(), was = gone.has(k) ? gone.get(k) : seen.get(k);
        seen.set(k, now);
        if (was && now && was !== now && b.isConnected) countTick(b, was);
      }
    }
  }).observe(root, {childList: true, subtree: true, characterData: true});
  if (page) addEventListener('hashchange', () => seen.clear());
}
countWatch($('#tree'), '.cnt', b => b.closest('[data-org]')?.dataset.org || '');
const motionTasksPage = () => /^#\/(tasks|task\/)/.test(location.hash);
countWatch($('#main'), '.tl-tabs .cnt, .bcol > header .cnt', b => {
  const tab = b.closest('[data-view]'), col = b.closest('.bcol');
  return tab ? 'view:' + tab.dataset.view : col ? 'col:' + col.dataset.col : '';
}, {when: motionTasksPage, page: true});

// ---- the sidebar's current row: one accent bar (styles/sidebar.css .nav-ind) on whichever row is current, sliding
// to the new one when the page changes. sidebar.js marks the row (navCurSync, and the page links' own pass); this
// only watches the marks, so the list's markup is untouched. It snaps instead of sliding when it first appears, when
// rows above it fold or are added (the same row, moved), and when the sidebar is resized.
(function navIndicator() {
  const scroller = $('#side .side-scroll');
  if (!scroller || !window.MutationObserver) return;
  const bar = document.createElement('span');
  bar.className = 'nav-ind';
  bar.setAttribute('aria-hidden', 'true');
  bar.hidden = true;
  scroller.prepend(bar);
  const ROWS = ':scope > .nav-link.cur, #tree a.node.cur, #tree a.dept-label.cur, .inbox-list a.cur';
  let at = '', queued = 0;
  const place = () => {
    queued = 0;
    const row = [...scroller.querySelectorAll(ROWS)].find(el => el.checkVisibility ? el.checkVisibility() : el.offsetParent);
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
  const soon = () => { if (!queued) queued = requestAnimationFrame(place); };
  new MutationObserver(records => { if (records.some(r => r.target !== bar)) soon(); })
    .observe(scroller, {subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden', 'aria-expanded']});
  // A resize moves the same row: no slide.
  const snap = () => { at = ''; bar.hidden = true; soon(); };
  if (window.ResizeObserver) new ResizeObserver(snap).observe(scroller);
  document.fonts?.ready.then(snap);
  soon();
})();

// ---- where a menu grows from. A menu placed by script (a popover, the task list's pickers, the team's + menu)
// scales up from the side of it nearest the button that opened it; the others keep their stylesheet's origin.
let MOTION_PRESS = null;
document.addEventListener('pointerdown', ev => { MOTION_PRESS = {el: ev.target.closest?.('button, a, summary, [role=button]'), at: performance.now()}; }, true);
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
function taskDoneMotion(key) {
  for (const el of document.querySelectorAll(`#main [data-task-key="${CSS.escape(key)}"]`)) {
    if (el.classList.contains('m-task-done')) continue;
    el.classList.add('m-task-done');
    if (motionStill()) continue;
    const chip = el.querySelector(':scope > .tl-status');
    if (chip) chip.innerHTML = MOTION_CHECK + esc(typeof STATUS_WORD === 'object' && STATUS_WORD.done || 'Done');
    else if (el.matches('.bcard')) el.insertAdjacentHTML('beforeend', MOTION_CHECK);
  }
}
document.addEventListener('click', ev => {
  const done = ev.target.closest?.('[data-task-finish="done"]');
  if (done && !motionStill()) done.classList.add('m-checking');
}, true);
