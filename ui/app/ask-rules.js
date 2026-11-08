/* ui/app/ask-rules.js — Ask's rules with no page in them: the peeks beside the dot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   ui/tests/ask-rules.cjs runs them without a browser. */
'use strict';

// Peeks: one line beside the dot about something already waiting on the person (a bot's question, an ask), so Tico
// speaks up without nagging. One at a time; none while the panel is open, while typing or in the first few seconds;
// one dismissed stays dismissed; an item peeks at most once a day. `askPeekFresh`: the items that may peek now.
const ASK_PEEK_WAIT_MS = 3000;
const ASK_PEEK_DAY_MS = 24 * 3600 * 1000;
function askPeekFresh(items, {now = Date.now(), loadedAt = 0, typing = false, open = false, dismissed = [], shown = {}} = {}) {
  if (open || typing || now - loadedAt < ASK_PEEK_WAIT_MS) return [];
  return (items || []).filter(it => it?.id && !dismissed.includes(it.id) && !(now - (Number(shown[it.id]) || 0) < ASK_PEEK_DAY_MS));
}
const askPeekPick = (items, opts) => askPeekFresh(items, opts)[0] || null;
// What a peek says, and the items it speaks for: Tico talking, six words at most, about the fresh items of the first
// one's kind (bots' questions, or Confirm cards: `confirm` true). Never who asked, never the question's own words.
function askPeekLine(fresh) {
  const first = (fresh || [])[0];
  if (!first) return {text: '', ids: []};
  const same = fresh.filter(it => !!it.confirm === !!first.confirm), n = same.length;
  const text = first.confirm ? (n > 1 ? `${n} things need your OK` : 'Need your OK on something')
    : n > 1 ? `${n} questions are waiting on you` : 'Got a quick question for you';
  return {text, ids: same.map(it => it.id)};
}
// What the peek store keeps: the last 50 dismissed ids, and when each item last peeked (a day's worth is enough).
function askPeekTidy({dismissed = [], shown = {}} = {}, now = Date.now()) {
  return {dismissed: [...new Set(dismissed)].slice(-50),
    shown: Object.fromEntries(Object.entries(shown).filter(([, at]) => now - Number(at) < ASK_PEEK_DAY_MS).slice(-50))};
}
