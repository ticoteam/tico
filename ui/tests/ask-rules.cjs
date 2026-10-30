// No browser: Ask's rules (ui/app/ask-rules.js). The "Loop in Tico Support" switch: support is in the chat while an open
// request is shown there; flipping it takes that request out, brings one taken out back, or starts a new one; with
// support off on the server there is no switch. Peeks beside the dot: from known items only, one at a time, none while
// the panel is open, while typing or in the first 3 s, a dismissed one never again, any one at most once a day.
// Turning support on shares the chat: its last 20 turns, scrubbed of keys, go to the request (a new one when none is
// open); turning it off stops what this chat sends going there.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/ask-rules.js'), 'utf8')
  + '\n;globalThis.api = {askSupportState, askSupportFlip, askPeekPick, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS,'
  + ' askSupportSwitch, askSendTo, askScrub, askShareText, askShared};', context);
const {askSupportState, askSupportFlip, askPeekPick, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS,
  askSupportSwitch, askSendTo, askScrub, askShareText, askShared} = context.api;
const plain = v => JSON.parse(JSON.stringify(v));

// ---- The switch
const a = {id: 'sup_a'}, b = {id: 'sup_b'};
// Off on this server: no switch, nothing to flip.
assert.deepEqual(plain(askSupportState({enabled: false, open: [a]})), {available: false, on: false, ticket: null, held: null});
assert.deepEqual(plain(askSupportFlip(askSupportState({enabled: false, open: [a]}))), {do: 'none'});
assert.deepEqual(plain(askSupportFlip(null)), {do: 'none'});
// An open request shown here: on, and flipping takes it out.
let s = askSupportState({open: [a, b]});
assert.equal(s.on, true);
assert.equal(s.ticket.id, 'sup_a', 'the newest open request');
assert.deepEqual(plain(askSupportFlip(s)), {do: 'drop', id: 'sup_a'});
// Taken out: the next open request is shown instead; with every one taken out it is off, and flipping brings one back.
assert.equal(askSupportState({open: [a, b], hidden: ['sup_a']}).ticket.id, 'sup_b');
s = askSupportState({open: [a, b], hidden: ['sup_a', 'sup_b']});
assert.deepEqual([s.on, s.ticket, s.held.id], [false, null, 'sup_a']);
assert.deepEqual(plain(askSupportFlip(s)), {do: 'show', id: 'sup_a'});
// No open request: flipping starts one in Support.
s = askSupportState({open: []});
assert.deepEqual([s.available, s.on], [true, false]);
assert.deepEqual(plain(askSupportFlip(s)), {do: 'file'});
// A closed request taken out long ago stays out of the way: hidden ids with no open request are ignored.
assert.deepEqual(plain(askSupportFlip(askSupportState({open: [], hidden: ['sup_old']}))), {do: 'file'});
assert.equal(askSupportState({open: [null, a]}).ticket.id, 'sup_a', 'a missing entry is skipped');

// ---- Peeks
const now = 1_800_000_000_000, loadedAt = now - 10_000;
const items = [{id: 'q1'}, {id: 'q2'}, {id: 'q3'}];
const pick = over => askPeekPick(items, {now, loadedAt, ...over})?.id || null;
assert.equal(pick({}), 'q1', 'the first waiting item');
assert.equal(askPeekPick([], {now, loadedAt}), null, 'nothing known, nothing said');
assert.equal(askPeekPick(null, {now, loadedAt}), null);
assert.equal(askPeekPick([{title: 'no id'}], {now, loadedAt}), null, 'only real items');
assert.equal(pick({open: true}), null, 'none while the panel is open');
assert.equal(pick({typing: true}), null, 'none while typing');
assert.equal(pick({loadedAt: now - ASK_PEEK_WAIT_MS + 1}), null, 'none in the first 3 s');
assert.equal(pick({loadedAt: now - ASK_PEEK_WAIT_MS}), 'q1');
assert.equal(ASK_PEEK_WAIT_MS, 3000);
assert.equal(pick({dismissed: ['q1']}), 'q2', 'a dismissed item never peeks again');
assert.equal(pick({dismissed: ['q1', 'q2', 'q3']}), null);
assert.equal(pick({shown: {q1: now - 60_000}}), 'q2', 'shown today: not again');
assert.equal(pick({shown: {q1: now - ASK_PEEK_DAY_MS}}), 'q1', 'a day later it may');
assert.equal(pick({shown: {q1: 'junk'}}), 'q1', 'a broken store does not block');
// The store keeps the last 50 dismissed and a day of shown times.
const tidy = plain(askPeekTidy({dismissed: [...Array(60).keys()].map(String).concat('5'), shown: {old: now - ASK_PEEK_DAY_MS - 1, fresh: now - 1}}, now));
assert.equal(tidy.dismissed.length, 50);
assert.equal(tidy.dismissed.at(-1), '59');
assert.deepEqual(tidy.shown, {fresh: now - 1});
assert.deepEqual(plain(askPeekTidy({}, now)), {dismissed: [], shown: {}});

// ---- The scrub: backend/redaction.py's key and token rules, giving the same text.
assert.equal(askScrub('key sk-ant-abcdefghijklmnop123 and ghp_abcdefghij1234567890 password: hunter2, token="abc def" Bearer abcdefghijkl '
  + 'uuid 3f9a2b7c-1234-4abc-8def-0123456789ab long aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3 AKIAABCDEFGHIJKLMNOP xoxb-1234567890'),
  'key [key] and [token] password: [redacted], token=[redacted] Bearer [token] uuid 3f9a2b7c-1234-4abc-8def-0123456789ab long [secret] [key] [token]');
assert.equal(askScrub('plain words, a path /usr/local/bin and ben@acme.test stay'), 'plain words, a path /usr/local/bin and ben@acme.test stay');
assert.equal(askScrub('bell\u0007 and ‮flip'), 'bell and flip', 'control characters HQ refuses are dropped');

// ---- What is shared: the last 20 turns, scrubbed, one line each, under the head the thread skips; fits 4000 characters.
const turns = [...Array(25).keys()].map(i => ({me: i % 2 === 0, text: `turn ${i}` + (i === 24 ? ' my key is sk-abcdefgh12345678\n  ok' : '')}));
const shared = askShareText(turns, 'Assistant');
const lines = shared.split('\n');
assert.equal(lines[0], 'Shared from my chat with Assistant:');
assert.equal(lines.length, 2 + 20, 'the last 20 turns');
assert.equal(lines[2], 'Assistant: turn 5');
assert.equal(lines.at(-1), 'Me: turn 24 my key is [key] ok', 'scrubbed, on one line');
assert.ok(askShared(shared));
const long = askShareText([...Array(20).keys()].map(i => ({me: true, text: 'x'.repeat(i) + ' ' + 'word '.repeat(200)})), 'Assistant');
assert.ok(long.length <= 4000, 'fits a support message');
assert.match(long.split('\n').at(-1), /^Me: x{19} /, 'the newest turn stays; older ones drop off');
assert.equal(askShareText([], 'Assistant'), 'Shared from my chat with Assistant: nothing in it yet.');

// ---- The switch. A small support: open requests, the ids hidden from this chat, and what was sent where.
function desk(open = []) {
  const d = {open: open.map(id => ({id})), hidden: [], sent: [], filed: [], toTico: []};
  d.state = () => askSupportState({open: d.open, hidden: d.hidden});
  d.io = {share: () => askShareText([{me: true, text: 'The board will not load, token=abc123'}, {me: false, text: 'Try a reload.'}], 'Assistant'),
    file: async text => { const t = {id: 'sup_new'}; d.filed.push(text); d.open.unshift(t); return t; },
    reply: async (id, text) => { d.sent.push({id, text}); },
    hide: id => { d.hidden.push(id); }, show: id => { d.hidden = d.hidden.filter(x => x !== id); }};
  // A message typed in Ask: where askSendTo sends it.
  d.type = text => { const to = askSendTo(d.state()); if (to.to === 'support') d.sent.push({id: to.id, text}); else d.toTico.push(text); };
  return d;
}
const SHARED = 'Shared from my chat with Assistant:\n\nMe: The board will not load, token=[redacted]\nAssistant: Try a reload.';
(async () => {
  // No open request: turning it on sends a new request holding the scrubbed chat, and support is in.
  const none = desk();
  assert.equal(none.state().on, false);
  none.type('before');
  assert.deepEqual(plain(await askSupportSwitch(none.state(), none.io)), {do: 'file', id: 'sup_new'});
  assert.deepEqual(none.filed, [SHARED], 'a new request with the scrubbed chat');
  assert.equal(none.state().on, true);
  none.type('while on');
  assert.deepEqual(none.sent, [{id: 'sup_new', text: 'while on'}], 'while on, messages go to the request');
  // Off: the request stays open but is out of the chat, and nothing more goes to it.
  assert.deepEqual(plain(await askSupportSwitch(none.state(), none.io)), {do: 'drop', id: 'sup_new'});
  assert.equal(none.state().on, false);
  assert.equal(none.open.length, 1, 'the request stays open');
  none.type('after');
  assert.deepEqual(none.sent, [{id: 'sup_new', text: 'while on'}], 'off: nothing more from this chat goes');
  assert.deepEqual(none.toTico, ['before', 'after']);
  // On again: the same request comes back and gets the chat shared again; no second request.
  assert.deepEqual(plain(await askSupportSwitch(none.state(), none.io)), {do: 'show', id: 'sup_new'});
  assert.deepEqual(none.sent.at(-1), {id: 'sup_new', text: SHARED});
  assert.equal(none.filed.length, 1);
  assert.equal(none.state().on, true);
  // The switch applies to the chat across every open request, including after an older request returns.
  const many = desk(['sup_new', 'sup_old']);
  await askSupportSwitch(many.state(), many.io);
  assert.equal(many.state().on, false, 'one switch turns the chat off across all open requests');
  many.type('after off');
  assert.deepEqual(many.sent, []);
  assert.deepEqual(many.toTico, ['after off']);
  assert.equal(many.open.length, 2, 'requests stay open in Support');
  await askSupportSwitch(many.state(), many.io);
  assert.equal(many.state().ticket.id, 'sup_new', 'turning on restores the newest request');
  assert.deepEqual(many.sent, [{id: 'sup_new', text: SHARED}]);
  many.type('while on');
  assert.deepEqual(many.sent.at(-1), {id: 'sup_new', text: 'while on'});
  await askSupportSwitch(many.state(), many.io);
  assert.equal(many.state().on, false, 'turning off again leaves the older request hidden');
  many.type('after second off');
  assert.deepEqual(many.toTico, ['after off', 'after second off']);
  // An open request taken out earlier: the chat goes to it first; if that fails the switch stays off.
  const held = desk(['sup_1']);
  held.hidden = ['sup_1'];
  held.io.reply = async () => { throw new Error('Could not reach Tico support.'); };
  await assert.rejects(askSupportSwitch(held.state(), held.io), /Could not reach/);
  assert.equal(held.state().on, false, 'nothing shared: still off');
  // Support off on the server: the switch does nothing.
  const off = desk();
  assert.deepEqual(plain(await askSupportSwitch(askSupportState({enabled: false}), off.io)), {do: 'none'});
  assert.deepEqual([off.filed, off.sent], [[], []]);
})().catch(error => { console.error(error); process.exitCode = 1; });
