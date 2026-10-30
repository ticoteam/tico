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
  return {available: true, shown: shown.map(t => t.id), on: !!shown.length, ticket: shown[0] || null, held: shown.length ? null : open.find(t => t && hidden.includes(t.id)) || null};
}
// What flipping the switch does: take the request out (drop), bring a request taken out back (show), or, with no
// open request, start one in Support, where the person sees what goes with it before it is sent (file).
function askSupportFlip(state) {
  if (!state?.available) return {do: 'none'};
  if (state.on) return {do: 'drop', id: state.ticket.id};
  if (state.held) return {do: 'show', id: state.held.id};
  return {do: 'file'};
}
// The switch is the consent: its label says support will see the chat, so turning it on shares it. The chat's recent
// turns, scrubbed, go to the request it brings back (show) or to a new request sent at once (file); turning it off
// takes every shown request out of the chat (drop), and nothing more from this chat goes to support. `io` does the work:
// {share() -> the text to send, file(text) -> the new request, reply(id, text), hide(id), show(id)}. The request is
// shown only once what it shares has gone, so a failure leaves the switch off.
async function askSupportSwitch(state, io) {
  const act = askSupportFlip(state);
  if (act.do === 'drop') for (const id of state.shown || [act.id]) io.hide(id);
  else if (act.do === 'show') { await io.reply(act.id, io.share()); io.show(act.id); }
  else if (act.do === 'file') act.id = (await io.file(io.share()))?.id || '';
  return act;
}
// Where a message typed in Ask goes: to the request while support is in the chat, otherwise to Tico.
const askSendTo = state => state?.on && state.ticket ? {to: 'support', id: state.ticket.id} : {to: 'tico'};

// Key-shaped strings, cut from anything Ask shares with support: the key and token rules of backend/redaction.py,
// which every support report and diagnostics bundle goes through, as they are there: known key and token shapes,
// `password: ...`-style values and long runs that mix letters and digits. Emails, hosts and names stay; the person is
// sharing their own chat.
const ASK_CONTROL = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g;   // HQ refuses these
const ASK_SECRET_FIELD = /(["']?\b[A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|passwd)[A-Za-z0-9_.-]*["']?\s*[=:]\s*)(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^,;\r\n}\]]+)/gi;
const ASK_SECRETS = [
  [/\bsk-[A-Za-z0-9_-]{8,}/g, '[key]'],
  [/\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{8,}/g, '[token]'],
  [/\bgithub_pat_[A-Za-z0-9_]{10,}/g, '[token]'],
  [/\bxox[abprs]-[A-Za-z0-9-]{6,}/g, '[token]'],
  [/\bxapp-[A-Za-z0-9-]{6,}/g, '[token]'],
  [/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g, '[key]'],
  [/\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g, '[jwt]'],
  [/\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}/gi, '$1 [token]'],
];
const ASK_LONG = /(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{32,}/g;
const ASK_UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
function askScrub(text) {
  let s = String(text ?? '').replace(ASK_CONTROL, '').replace(ASK_SECRET_FIELD, '$1[redacted]');
  for (const [pattern, mark] of ASK_SECRETS) s = s.replace(pattern, mark);
  return s.replace(ASK_LONG, w => /\d/.test(w) && /[A-Za-z]/.test(w) && !ASK_UUID.test(w) ? '[secret]' : w);
}
// What turning support on sends: the chat's last 20 turns, oldest first, each scrubbed and on one line, under a head
// the thread knows (it doesn't draw this message again: the chat is already on screen). Older turns drop off until it
// fits a support message (4000 characters). `turns`: [{me, text}] oldest first; `name`: the chat bot's name.
const ASK_SHARE_HEAD = 'Shared from my chat with ';
const ASK_SHARE_TURNS = 20, ASK_SHARE_MAX = 4000, ASK_SHARE_LINE = 600;
function askShareText(turns, name = 'the Assistant', max = ASK_SHARE_MAX) {
  const head = `${ASK_SHARE_HEAD}${name}:`;
  const lines = (turns || []).filter(t => t && String(t.text || '').trim()).slice(-ASK_SHARE_TURNS).map(t => {
    const line = askScrub(t.text).replace(/\s+/g, ' ').trim();
    return `${t.me ? 'Me' : name}: ${line.length > ASK_SHARE_LINE ? line.slice(0, ASK_SHARE_LINE - 1) + '…' : line}`;
  });
  while (lines.length > 1 && head.length + 2 + lines.join('\n').length > max) lines.shift();
  return lines.length ? `${head}\n\n${lines.join('\n')}`.slice(0, max) : `${head} nothing in it yet.`;
}
const askShared = body => String(body || '').startsWith(ASK_SHARE_HEAD);

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
