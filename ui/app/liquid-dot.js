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
// The dot hardly ever just vanishes: when it moves (a button into a panel and back, an older turn to the newest), a
// copy flies from where it was (`from`, a rect) to `el`, following `el` if that moves meanwhile, while `el` itself
// waits unseen. Transform and opacity only, over every clip (a copy on top of the page); none with reduced motion.
const LDOT_FLY_MS = 420;
const ldotCalm = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
function ldotFly(el, from, {ms = LDOT_FLY_MS, lift = 14} = {}) {
  el?._ldotLand?.();
  if (!el?.isConnected || !from?.width || ldotCalm() || typeof requestAnimationFrame !== 'function') return false;
  const to = el.getBoundingClientRect();
  if (!to.width || Math.hypot(from.left - to.left, from.top - to.top) < 2) return false;
  const ghost = el.cloneNode(true);
  ghost.classList.add('ldot-fly');
  ghost.style.width = `${to.width}px`;
  document.body.append(ghost);
  el.classList.add('ldot-away');
  const start = performance.now(), ease = t => 1 - (1 - t) ** 3;
  let frame = 0;
  const land = () => { cancelAnimationFrame(frame); ghost.remove(); el.classList.remove('ldot-away'); delete el._ldotLand; };
  const step = now => {
    const r = el.getBoundingClientRect();
    if (!el.isConnected || !r.width) return land();
    const t = Math.min(1, (now - start) / ms), k = ease(t);
    const x = from.left + (r.left - from.left) * k, y = from.top + (r.top - from.top) * k - Math.sin(Math.PI * t) * lift;
    ghost.style.transform = `translate(${x}px,${y}px) scale(${(from.width + (r.width - from.width) * k) / to.width})`;
    if (t < 1) frame = requestAnimationFrame(step); else land();
  };
  el._ldotLand = land;
  step(start);
  return true;
}
