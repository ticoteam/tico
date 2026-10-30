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
