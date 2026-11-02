// No browser: Ask's rules (ui/app/ask-rules.js). Peeks beside the dot: from known items only, one at a time, none while
// the panel is open, while typing or in the first 3 s, a dismissed one never again, any one at most once a day; each
// says what waits in six words at most.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/ask-rules.js'), 'utf8')
  + '\n;globalThis.api = {askPeekPick, askPeekLine, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS};', context);
const {askPeekPick, askPeekLine, askPeekTidy, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS} = context.api;
const plain = v => JSON.parse(JSON.stringify(v));

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
// What it says: Tico talking, six words at most, never who asked or the question; it speaks for the fresh items of the
// first one's kind.
const line = list => plain(askPeekLine(list));
assert.deepEqual(line([{id: 'q1'}]), {text: 'Got a quick question for you', ids: ['q1']});
assert.deepEqual(line(items), {text: '3 questions are waiting on you', ids: ['q1', 'q2', 'q3']});
assert.deepEqual(line([{id: 'c1', confirm: true}, {id: 'q1'}]), {text: 'Need your OK on something', ids: ['c1']});
assert.deepEqual(line([{id: 'q1'}, {id: 'c1', confirm: true}, {id: 'c2', confirm: true}]).ids, ['q1']);
assert.equal(line([{id: 'c1', confirm: true}, {id: 'c2', confirm: true}]).text, '2 things need your OK');
assert.deepEqual(line([]), {text: '', ids: []});
for (const n of [1, 2, 12, 140]) for (const confirm of [false, true]) {
  const text = askPeekLine([...Array(n).keys()].map(i => ({id: 'x' + i, confirm}))).text;
  assert.ok(text.split(/\s+/).length <= 6 && !/asks/i.test(text), text);
}
// The store keeps the last 50 dismissed and a day of shown times.
const tidy = plain(askPeekTidy({dismissed: [...Array(60).keys()].map(String).concat('5'), shown: {old: now - ASK_PEEK_DAY_MS - 1, fresh: now - 1}}, now));
assert.equal(tidy.dismissed.length, 50);
assert.equal(tidy.dismissed.at(-1), '59');
assert.deepEqual(tidy.shown, {fresh: now - 1});
assert.deepEqual(plain(askPeekTidy({}, now)), {dismissed: [], shown: {}});
