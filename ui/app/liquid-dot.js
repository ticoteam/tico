/* ui/app/liquid-dot.js — The Liquid Dot: Tico, the assistant, drawn as one jade blob (styles/liquid-dot.css)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The dot only ever means Tico; bots keep their own avatars. One live dot per screen: every other dot on it is still.
// It stays jade in every state, so a status colour lives in text and chips. Under 20px it has no shadow and only
// idles; with reduced motion it holds still (the stylesheet) and a state shows as a word (ldotWord).
const LDOT_STATES = ['idle', 'listening', 'thinking', 'talking', 'done', 'still'];
const LDOT_SMALL = 20;
const LDOT_WORDS = {listening: 'Listening…', thinking: 'Working…', talking: 'Replying…'};
// The state a dot of this size can show: anything unknown idles, and a small dot idles unless it is still.
function ldotState(state, size = 24) {
  const s = LDOT_STATES.includes(state) ? state : 'idle';
  return size < LDOT_SMALL && s !== 'still' ? 'idle' : s;
}
const ldotWord = state => LDOT_WORDS[state] || '';
// Ask's state from its chat: a reply streaming in talks, a turn with no words yet is working, else idle.
function ldotChatState({replying = false, streaming = false} = {}) {
  return streaming ? 'talking' : replying ? 'thinking' : 'idle';
}
function ldotHTML({state = 'idle', size = 24, cls = ''} = {}) {
  return `<span class="ldot${size < LDOT_SMALL ? ' sm' : ''}${cls ? ' ' + cls : ''}" data-state="${ldotState(state, size)}" data-size="${size}"`
    + ` style="width:${size}px" aria-hidden="true"><i class="shadow"></i><i class="body"></i></span>`;
}
// Moves a drawn dot to a state; the same state again leaves its motion where it is.
function ldotSet(el, state) {
  if (!el) return;
  const next = ldotState(state, Number(el.dataset.size) || 24);
  if (el.dataset.state !== next) el.dataset.state = next;
}
// The dot hardly ever just vanishes: when it moves (a pill into a panel and back, an older turn to the newest), a copy
// flies from where it was (`from`, a rect) to `el` along an arc and squashes as it lands, while `el` waits unseen. Both
// rects are measured once, before the flight (`to` when the caller knows better, e.g. a panel still sliding in); the
// copy moves by a Web Animation of transform alone, on top of every clip, and is the only thing with will-change.
// None with reduced motion. Returns the Animation, or null when nothing flew.
const ldotCalm = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
function ldotFly(el, from, {to = null} = {}) {
  if (!el?.isConnected || !from?.width || ldotCalm() || typeof el.animate !== 'function') return null;
  to ||= el.getBoundingClientRect();
  const dx = from.left - to.left, dy = from.top - to.top, dist = Math.hypot(dx, dy);
  if (!to.width || dist < 2) return null;
  el._ldotFly?.cancel();
  const ghost = el.cloneNode(true);
  ghost.removeAttribute('data-ask-dot'); ghost.removeAttribute('id');
  ghost.classList.remove('ldot-away');
  ghost.classList.add('ldot-fly');
  ghost.dataset.state = 'still';
  Object.assign(ghost.style, {left: `${to.left}px`, top: `${to.top}px`, width: `${to.width}px`});
  document.body.append(ghost);
  el.classList.add('ldot-away');
  const arc = Math.min(110, dist * .3) + 24, s0 = from.width / to.width;
  const run = ghost.animate([
    {transform: `translate(${dx}px,${dy}px) scale(${s0})`},
    {transform: `translate(${dx * .5}px,${dy * .5 - arc}px) scale(1.05,.96)`, offset: .5},
    {transform: 'translate(0,0) scale(1.22,.8)', offset: .84},
    {transform: 'translate(0,-3px) scale(.95,1.06)', offset: .93},
    {transform: 'none'},
  ], {duration: Math.min(900, 480 + dist * .45), easing: 'cubic-bezier(.45,0,.4,1)'});
  const land = () => { ghost.remove(); el.classList.remove('ldot-away'); if (el._ldotFly === run) delete el._ldotFly; };
  run.onfinish = land;
  run.oncancel = land;
  el._ldotFly = run;
  return run;
}
// One hop (a peek arrived, support left) or a squash (support popped out of it): a Web Animation on the body, on top
// of whatever the dot was doing, transform only; none with reduced motion or on a still dot.
const LDOT_MOVES = {
  hop: [{transform: 'none'}, {transform: 'translateY(5%) scale(1.12,.88)', offset: .15}, {transform: 'translateY(-34%) scale(.94,1.07)', offset: .45},
        {transform: 'translateY(0) scale(1.1,.9)', offset: .75}, {transform: 'none'}],
  split: [{transform: 'none'}, {transform: 'scale(1.3,.75)', offset: .3}, {transform: 'scale(.85,1.15)', offset: .55},
          {transform: 'scale(1.06,.95)', offset: .8}, {transform: 'none'}],
};
function ldotMove(el, move = 'hop') {
  const body = el?.querySelector?.('.body');
  if (!body || typeof body.animate !== 'function' || ldotCalm() || el.dataset.state === 'still') return null;
  return body.animate(LDOT_MOVES[move] || LDOT_MOVES.hop, {duration: move === 'split' ? 500 : 750, easing: 'cubic-bezier(.3,0,.2,1)'});
}

// Idle alternates, cheaply: a setTimeout (never a frame loop) gives the live idle dot a short variant every 6-14 s; in
// between it breathes. Dots handed to ldotWatch pause while off screen (an IntersectionObserver); every dot pauses
// while the tab is hidden, and then no timer is left running.
const LDOT_IDLES = ['breathe', 'sway', 'look', 'squish', 'hop'];
const LDOT_IDLE_MS = [6000, 14000];
const LDOT_WATCHED = new Set();
const LDOT_OFF = new WeakSet();              // watched dots now off screen
let LDOT_TIMER = 0, LDOT_SEEN = null;
function ldotNextIdle(now = 'breathe', roll = Math.random()) {
  const rest = LDOT_IDLES.filter(v => v !== now);
  return now !== 'breathe' && roll < .4 ? 'breathe' : rest[Math.floor(roll * rest.length) % rest.length];
}
function ldotIdle() {
  LDOT_TIMER = 0;
  if (globalThis.document?.hidden || ldotCalm()) return;
  const live = [...LDOT_WATCHED].find(el => el.isConnected && el.dataset.state === 'idle' && !el.hasAttribute('data-paused') && !el.classList.contains('sm'));
  if (live) live.dataset.idle = ldotNextIdle(live.dataset.idle);
  ldotSchedule();
}
function ldotSchedule() {
  clearTimeout(LDOT_TIMER);
  LDOT_TIMER = globalThis.document?.hidden || ldotCalm() ? 0 : setTimeout(ldotIdle, LDOT_IDLE_MS[0] + Math.random() * (LDOT_IDLE_MS[1] - LDOT_IDLE_MS[0]));
}
const ldotScheduled = () => !!LDOT_TIMER;
function ldotWatch(el) {
  if (!el || LDOT_WATCHED.has(el)) return;
  LDOT_WATCHED.add(el);
  if (typeof IntersectionObserver === 'function') {
    LDOT_SEEN ||= new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) LDOT_OFF.delete(e.target); else LDOT_OFF.add(e.target);
      e.target.toggleAttribute('data-paused', !e.isIntersecting || document.hidden);
    }));
    LDOT_SEEN.observe(el);
  }
  if (!LDOT_TIMER) ldotSchedule();
}
globalThis.document?.addEventListener('visibilitychange', () => {
  document.querySelectorAll('.ldot').forEach(el => el.toggleAttribute('data-paused', document.hidden || LDOT_OFF.has(el)));
  if (document.hidden) { clearTimeout(LDOT_TIMER); LDOT_TIMER = 0; } else ldotSchedule();
});
