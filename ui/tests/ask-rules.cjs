// No browser: Ask's rules (ui/app/ask-rules.js). The "Loop in Tico Support" switch: support is in the chat while an open
// request is shown there; flipping it takes that request out, brings one taken out back, or starts a new one; with
// support off on the server there is no switch. Peeks beside the dot: from known items only, one at a time, none while
// the panel is open, while typing or in the first 3 s, a dismissed one never again, any one at most once a day.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/ask-rules.js'), 'utf8')
  + '\n;globalThis.api = {askSupportState, askSupportFlip, askPeekPick, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS};', context);
const {askSupportState, askSupportFlip, askPeekPick, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS} = context.api;
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
