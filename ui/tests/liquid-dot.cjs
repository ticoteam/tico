// No browser: the Liquid Dot's state rules (ui/app/liquid-dot.js). Ask's chat maps to idle, working or talking; a dot
// under 20px only idles (or holds still); an unknown state idles; and setting a drawn dot follows the same rules. The
// idle scheduler swaps variants every 6-14 s and leaves no timer in a hidden tab; every keyframe in liquid-dot.css
// moves transform or opacity only, and the blob's shape never animates.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// A page, as much of one as the scheduler needs: a tab that can be hidden, and timers we can see.
const timers = new Map();
let nextTimer = 1, onVisibility = null;
const page = {hidden: false, addEventListener: (type, fn) => { if (type === 'visibilitychange') onVisibility = fn; }, querySelectorAll: () => []};
const context = vm.createContext({document: page, setTimeout: (fn, ms) => { timers.set(nextTimer, ms); return nextTimer++; },
  clearTimeout: id => timers.delete(id), matchMedia: () => ({matches: false})});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/liquid-dot.js'), 'utf8')
  + '\n;globalThis.api = {ldotState, ldotWord, ldotChatState, ldotHTML, ldotSet, ldotNextIdle, ldotSchedule, ldotScheduled, LDOT_IDLES, LDOT_IDLE_MS};', context);
const {ldotState, ldotWord, ldotChatState, ldotHTML, ldotSet, ldotNextIdle, ldotSchedule, ldotScheduled, LDOT_IDLES, LDOT_IDLE_MS} = context.api;

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

// Idle variants: never the same one twice running, every one reachable, a short variant often rests on breathing.
for (const now of LDOT_IDLES) for (const roll of [0, .2, .39, .4, .6, .99]) {
  const next = ldotNextIdle(now, roll);
  assert.ok(LDOT_IDLES.includes(next) && next !== now, `${now} @${roll} -> ${next}`);
}
assert.deepEqual(new Set(LDOT_IDLES.flatMap(now => [0, .25, .5, .75, .99].map(roll => ldotNextIdle(now, roll)))), new Set(LDOT_IDLES));
assert.deepEqual([...LDOT_IDLE_MS], [6000, 14000]);
// The scheduler: one timer, 6-14 s out; none while the tab is hidden, and one again when it shows.
ldotSchedule();
assert.equal(timers.size, 1);
const [ms] = timers.values();
assert.ok(ms >= 6000 && ms <= 14000, `${ms}ms`);
page.hidden = true; onVisibility();
assert.equal(timers.size, 0, 'no timer in a hidden tab');
assert.equal(ldotScheduled(), false);
ldotSchedule();
assert.equal(timers.size, 0, 'nor after a reschedule while hidden');
page.hidden = false; onVisibility();
assert.equal(timers.size, 1);
assert.equal(ldotScheduled(), true);

// The stylesheet: transform and opacity only in every keyframe; the shape (border-radius) is set once, never animated.
const css = fs.readFileSync(path.join(__dirname, '../styles/liquid-dot.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const frames = [...css.matchAll(/@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}/g)];
assert.ok(frames.length >= 9, `found ${frames.length} keyframes`);
for (const [, name, body] of frames) for (const [, decls] of body.matchAll(/\{([^{}]*)\}/g))
  for (const decl of decls.split(';').map(d => d.trim()).filter(Boolean))
    assert.ok(/^(transform|opacity)\s*:/.test(decl), `${name} animates ${decl}`);
assert.doesNotMatch(css, /transition\s*:[^;}]*(border-radius|width|height|left|top|filter|box-shadow)/);
assert.doesNotMatch(css, /filter\s*:|glow/i, 'flat: no filters or glow');
console.log('PASS: liquid dot states');
