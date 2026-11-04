// No browser: the board's shape (ui/app/automations-flow.js auBoardShape) reads an inbox's rules, questions and LLM out
// of a flow, auBoardFlow writes them back unchanged, and any other flow is no board (it keeps the step list).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({AU: null});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/automations-flow.js'), 'utf8'), context);
const {auBoardShape: shape, auBoardFlow: flow} = context;
const fx = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/automations-board.json'), 'utf8'));
const clone = x => JSON.parse(JSON.stringify(x));
const plain = x => JSON.parse(JSON.stringify(x));   // out of the vm's realm, for deepEqual

// The inbox board: three rules, two questions, Sort over four destinations, unsure → Me.
const b = shape(fx.inbox);
assert.ok(b, 'the inbox is a board');
assert.deepEqual(plain(b.rules.map(r => [r.to || '', r.next])), [['legal', 'end'], ['', 'rule'], ['', 'end']]);
assert.deepEqual(plain(b.questions), [
  {id: 'q1', text: 'Is someone asking to meet?', min: 0.75, then: [{do: 'draft_reply', args: {body: 'Here is my booking link.'}}], to: 'me'},
  {id: 'q2', text: 'Is it a bug report?', min: 0.8, then: [], to: 'eng'}]);
assert.deepEqual(plain(b.llm), {mode: 'sort', prompt: 'Pick where this goes. Unsure: Me.', to: ['me', 'legal', 'eng', 'archive'], default: 'me', min: 0.5, as: ''});
assert.deepEqual(plain(b.dests), fx.inbox.destinations);

// Written back untouched: the same steps (ids, outs, levels) and the same destinations.
assert.deepEqual(plain(flow(b)), {steps: fx.inbox.steps, destinations: fx.inbox.destinations});

// The webhook board: one rule, no questions, a draft turn.
const w = shape(fx.webhook);
assert.ok(w, 'the webhook flow is a board');
assert.deepEqual(plain(w.llm), {mode: 'draft', prompt: 'Sort the order event.', to: ['ops', 'log'], default: 'log', as: 'assistant:dana'});
assert.deepEqual(plain(flow(w)), {steps: fx.webhook.steps, destinations: fx.webhook.destinations});

// A rule moved on, a question added and Sort turned to a reply: still a board, read back as written.
const c = shape(fx.inbox);
Object.assign(c.rules[0], {to: '', next: 'rule'});
c.questions.push({id: 'q3', text: 'Is it a press ask?', min: 0.9, then: [{do: 'forward', args: {to: 'press@acme.example'}}], to: ''});
Object.assign(c.llm, {mode: 'reply', as: 'assistant:dana'});
const out = flow(c), back = shape({...fx.inbox, ...plain(out)});
assert.ok(back, 'the changed flow is still a board');
assert.deepEqual(plain(out.steps[0].rules[0]), {when: fx.inbox.steps[0].rules[0].when, match: 'all', then: [{do: 'label', args: {name: 'STARRED'}}], next: 'rule', stop: false});
assert.equal(back.questions[2].text, 'Is it a press ask?');
assert.equal(out.steps[2].rules[2].when[0].field, 'd1.q3');
assert.deepEqual(plain(out.steps.map(s => s.type)), ['filter', 'decide', 'filter', 'llm']);
assert.deepEqual(plain(out.steps[3].choose), {to: ['me', 'legal', 'eng', 'archive'], default: 'me', mode: 'reply'});
assert.equal(back.llm.mode, 'reply');
// Back to Sort: a decide over the destinations and a filter routing each, the last rule the default.
Object.assign(back.llm, {mode: 'sort', to: ['me', 'eng'], default: 'eng'});
const sorted = flow(back);
assert.deepEqual(plain(sorted.steps.slice(-1)[0].rules.map(r => [r.when.length ? r.when[0].value : '', r.to])), [['me', 'me'], ['eng', 'eng'], ['', 'eng']]);
assert.ok(shape({...fx.inbox, ...plain(sorted)}));

// No board: no destinations, a rule going on to the next step, a saved question set, an extra step, a missing
// destination, or no LLM at the end.
const without = mut => { const f = clone(fx.inbox); mut(f); return shape(f); };
assert.equal(without(f => { delete f.destinations; }), null);
assert.equal(without(f => { f.destinations = []; }), null);
assert.equal(without(f => { f.steps[0].rules[1].next = 'step'; }), null);
assert.equal(without(f => { f.steps[0].rules[1].stop = false; delete f.steps[0].rules[1].next; }), null);
assert.equal(without(f => { f.steps[1].set = 'mail-triage@1'; }), null);
assert.equal(without(f => { f.steps.push({id: 'a1', type: 'action', do: 'archive'}); }), null);
assert.equal(without(f => { f.steps[2].rules[0].to = 'nowhere'; }), null);
assert.equal(without(f => { f.steps.splice(3, 2); }), null);
assert.equal(without(f => { f.steps[4].rules.pop(); }), null);
assert.equal(shape({trigger: {type: 'message'}, steps: [{id: 'f1', type: 'filter', rules: []}]}), null, 'a plain flow keeps its steps');
const legacy = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/redesign-automations.json'), 'utf8')).automations;
for (const a of legacy) assert.equal(shape(a), null, `${a.id} keeps its step list`);
console.log('automations-board-unit: ok');
