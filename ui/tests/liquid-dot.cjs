// No browser: the Liquid Dot's state rules (ui/app/liquid-dot.js). Ask's chat maps to idle, working or talking; a dot
// under 20px only idles (or holds still); an unknown state idles; and setting a drawn dot follows the same rules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/liquid-dot.js'), 'utf8') + '\n;globalThis.api = {ldotState, ldotWord, ldotChatState, ldotHTML, ldotSet};', context);
const {ldotState, ldotWord, ldotChatState, ldotHTML, ldotSet} = context.api;

// Ask: a streaming reply talks, a turn with no words yet works, anything else idles.
assert.equal(ldotChatState({replying: true, streaming: true}), 'talking');
assert.equal(ldotChatState({replying: true, streaming: false}), 'thinking');
assert.equal(ldotChatState({replying: false, streaming: false}), 'idle');
assert.equal(ldotChatState(), 'idle');

// Size: 20px and up shows every state; under 20px only idle and still.
for (const s of ['idle', 'listening', 'thinking', 'talking', 'done', 'still']) assert.equal(ldotState(s, 22), s);
for (const s of ['listening', 'thinking', 'talking', 'done']) assert.equal(ldotState(s, 16), 'idle', `${s} at 16px idles`);
assert.equal(ldotState('still', 16), 'still');
assert.equal(ldotState('sparkle', 40), 'idle', 'an unknown state idles');

// Reduced motion shows the working states as a word; idle and still need none.
assert.ok(ldotWord('thinking') && ldotWord('talking') && ldotWord('thinking') !== ldotWord('talking'));
assert.equal(ldotWord('idle'), '');
assert.equal(ldotWord('still'), '');

// The markup carries the mapped state, the small class and no glow or highlight.
assert.match(ldotHTML({state: 'talking', size: 16}), /class="ldot sm" data-state="idle" data-size="16"/);
assert.match(ldotHTML({state: 'talking', size: 22}), /class="ldot" data-state="talking"/);
assert.doesNotMatch(ldotHTML(), /glow/);

// A drawn dot moves by the same rules.
const el = {dataset: {size: '22', state: 'idle'}};
ldotSet(el, 'thinking');
assert.equal(el.dataset.state, 'thinking');
const small = {dataset: {size: '18', state: 'idle'}};
ldotSet(small, 'talking');
assert.equal(small.dataset.state, 'idle');
ldotSet(null, 'talking');   // nothing drawn: no throw
console.log('PASS: liquid dot states');
