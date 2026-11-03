// No browser: Ask's rules (ui/app/ask-rules.js). Peeks beside the dot: from known items only, one at a time, none while
// the panel is open, while typing or in the first 3 s, a dismissed one never again, any one at most once a day; each
// says what waits in six words at most. A turn at work with no words yet says how long it has run and its newest step.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/ask-rules.js'), 'utf8')
  + '\n;globalThis.api = {askPeekPick, askPeekLine, askPeekTidy, askToolStep, askWorkingLine, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS};', context);
const {askPeekPick, askPeekLine, askPeekTidy, askToolStep, askWorkingLine, ASK_PEEK_WAIT_MS, ASK_PEEK_DAY_MS} = context.api;
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

// ---- A turn at work
// Its newest tool step, as a verb and what it acts on; tool names only ever reach it (backend/turns.py tool_label).
assert.equal(askToolStep('Ran hub task bulk close'), 'Closing tasks');
assert.equal(askToolStep('Ran hub hub task list'), 'Reading tasks', 'an MCP tool with its server prefix');
assert.equal(askToolStep('Ran tico goal show'), 'Reading goals');
assert.equal(askToolStep('Ran hub kpi update'), 'Updating KPIs');
assert.equal(askToolStep('Ran Bash'), 'Running a command');
assert.equal(askToolStep('Ran hub task check back'), 'Task check back', 'a name it does not know stays as it is');
assert.equal(askToolStep('Ran github open thing'), 'Github open thing');
assert.equal(askToolStep('Ran a tool'), '');
const started = new Date(now - 4 * 60000 - 20000).toISOString();
const run = over => ({state: 'running', label: 'Working', text: '', started_at: started, parts: [
  {kind: 'tool', text: 'Ran hub task list'}, {kind: 'progress', text: 'Clearing them out.'}, {kind: 'tool', text: 'Ran hub task bulk close'}], ...over});
assert.equal(askWorkingLine(run(), now), 'Working · 4m · Closing tasks', 'its label, its time and its newest step');
assert.equal(askWorkingLine(run({parts: []}), now), 'Working · 4m');
assert.equal(askWorkingLine(run({started_at: new Date(now - 125 * 60000).toISOString()}), now), 'Working · 2h 5m · Closing tasks');
assert.equal(askWorkingLine(run({state: 'leased', label: 'Starting', started_at: undefined, parts: []}), now), 'Starting');
for (const state of ['completed', 'queued', 'canceled', 'uncertain']) assert.equal(askWorkingLine(run({state}), now), '', state);
assert.equal(askWorkingLine(null, now), '');
