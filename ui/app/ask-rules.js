/* ui/app/ask-rules.js — Ask's rules with no page in them: the Tico Support switch and the peeks beside the dot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   ui/tests/ask-rules.cjs runs them without a browser. */
'use strict';

// Tico Support in the chat (ui/app/ask-panel.js). It rides on the existing ticket transport (ui/support.js): support is
// in the chat while one of the person's open requests is shown here; taking it out hides that request from this chat
// (on this browser), and the request itself stays open in Support. `open` is the person's open requests, newest first;
// `hidden` the ids taken out of the chat; `enabled` false when support is off on this server.
function askSupportState({enabled = true, open = [], hidden = []} = {}) {
  if (enabled === false) return {available: false, on: false, ticket: null, held: null};
  const shown = open.filter(t => t && !hidden.includes(t.id));
  return {available: true, on: !!shown.length, ticket: shown[0] || null, held: shown.length ? null : open.find(t => t && hidden.includes(t.id)) || null};
}
// What flipping the switch does: take the request out (drop), bring a request taken out back (show), or, with no
// open request, start one in Support, where the person sees what goes with it before it is sent (file).
function askSupportFlip(state) {
  if (!state?.available) return {do: 'none'};
  if (state.on) return {do: 'drop', id: state.ticket.id};
  if (state.held) return {do: 'show', id: state.held.id};
  return {do: 'file'};
}

// Peeks: one line beside the dot about something already waiting on the person (a bot's question, an ask), so Tico
// speaks up without nagging. One at a time; none while the panel is open, while typing or in the first few seconds;
// one dismissed stays dismissed; an item peeks at most once a day.
const ASK_PEEK_WAIT_MS = 3000;
const ASK_PEEK_DAY_MS = 24 * 3600 * 1000;
function askPeekPick(items, {now = Date.now(), loadedAt = 0, typing = false, open = false, dismissed = [], shown = {}} = {}) {
  if (open || typing || now - loadedAt < ASK_PEEK_WAIT_MS) return null;
  return (items || []).find(it => it?.id && !dismissed.includes(it.id) && !(now - (Number(shown[it.id]) || 0) < ASK_PEEK_DAY_MS)) || null;
}
// What the peek store keeps: the last 50 dismissed ids, and when each item last peeked (a day's worth is enough).
function askPeekTidy({dismissed = [], shown = {}} = {}, now = Date.now()) {
  return {dismissed: [...new Set(dismissed)].slice(-50),
    shown: Object.fromEntries(Object.entries(shown).filter(([, at]) => now - Number(at) < ASK_PEEK_DAY_MS).slice(-50))};
}
