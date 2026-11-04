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

// The webhook board: one rule, no questions, Sort. A draft or reply turn off mail is no board (it runs mail actions).
const w = shape(fx.webhook);
assert.ok(w, 'the webhook flow is a board');
assert.deepEqual(plain(w.llm), {mode: 'sort', prompt: 'Sort the order event.', to: ['ops', 'log'], default: 'log', min: 0.5, as: ''});
assert.deepEqual(plain(flow(w)), {steps: fx.webhook.steps, destinations: fx.webhook.destinations});
const turn = {...clone(fx.webhook), steps: [clone(fx.webhook.steps[0]), {id: 'l1', type: 'llm', out: 'l1', as: 'assistant:dana', prompt: 'x',
  choose: {to: ['ops', 'log'], default: 'log', mode: 'draft'}}]};
assert.equal(shape(turn), null, 'no draft turn off mail');
assert.ok(shape({...turn, trigger: {type: 'message', inbox: 'ops@acme.example'}}), 'a draft turn on mail is a board');

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

// Minimums as stored: a question or a Sort with none keeps none (the engine's own default), and saves without one.
const nomin = clone(fx.inbox);
delete nomin.steps[2].rules[0].when[0].min;
for (const r of nomin.steps[4].rules) if (r.when.length) delete r.when[0].min;
const nb = shape(nomin);
assert.ok(nb, 'no minimum is still a board');
assert.equal('min' in nb.questions[0], false);
assert.equal(nb.questions[1].min, 0.8);
assert.equal('min' in nb.llm, false);
assert.deepEqual(plain(flow(nb)), {steps: nomin.steps, destinations: nomin.destinations});

// Sort's unsure destination outside its answers: as a draft or reply it joins choose.to (a default must be one of them).
const outside = clone(fx.inbox);
delete outside.steps[3].questions.to.criteria.me;
outside.steps[4].rules.splice(0, 1);
const os = shape(outside);
assert.deepEqual(plain([os.llm.to, os.llm.default]), [['legal', 'eng', 'archive'], 'me']);
assert.deepEqual(plain(flow({...os, llm: {...os.llm, mode: 'reply', as: 'assistant:dana'}}).steps.at(-1).choose),
  {to: ['legal', 'eng', 'archive', 'me'], default: 'me', mode: 'reply'});
assert.deepEqual(plain(flow(os)), {steps: outside.steps, destinations: outside.destinations}, 'as Sort it is unchanged');

// Levels as stored: a decide with none keeps none (the engine runs it as low), and a written prompt is kept.
const nolevel = clone(fx.inbox);
delete nolevel.steps[1].level; delete nolevel.steps[3].level;
assert.deepEqual(plain(flow(shape(nolevel))), {steps: nolevel.steps, destinations: nolevel.destinations});
const noWebLevel = clone(fx.webhook);
delete noWebLevel.steps[1].level;
assert.deepEqual(plain(flow(shape(noWebLevel))), {steps: noWebLevel.steps, destinations: noWebLevel.destinations});

// Sort with one destination: a last filter with one rule and no conditions, no decide (a one-option choice is refused).
const one = shape(fx.inbox);
Object.assign(one.llm, {to: ['me'], default: 'me'});
const oneOut = flow(one);
assert.deepEqual(plain(oneOut.steps.map(s => s.type)), ['filter', 'decide', 'filter', 'filter']);
assert.deepEqual(plain(oneOut.steps[3]), {...fx.inbox.steps[4], rules: [{when: [], match: 'all', then: [], to: 'me', next: 'end', stop: true}]});
const oneBack = shape({...fx.inbox, ...plain(oneOut)});
assert.deepEqual(plain(oneBack.llm), {mode: 'sort', prompt: '', to: ['me'], default: 'me', as: ''});
assert.deepEqual(plain(flow(oneBack)), plain(oneOut));
const lone = {...clone(fx.webhook), steps: [{id: 's1', type: 'filter', enabled: true, rules: [{when: [], match: 'all', then: [], to: 'ops', next: 'end', stop: true}]}]};
assert.deepEqual(plain(shape(lone).rules), [], 'a lone routing filter is Sort, not a rule');
assert.equal(shape(lone).llm.default, 'ops');
// Back to two: a new decide (the board's level, a starting instruction) and the same filter, at the board's minimum.
Object.assign(oneBack.llm, {to: ['me', 'eng']});
const two = flow(oneBack);
const newSort = two.steps.find(s => s.type === 'decide' && s.questions.to);
assert.equal(newSort.level, 'high');
assert.equal(newSort.questions.to.instructions, 'Pick where this message goes.');
assert.equal(flow({...shape(lone), llm: {...shape(lone).llm, to: ['ops', 'log']}}).steps[0].questions.to.instructions, 'Pick where this goes.');
// One destination to a draft: the llm step gets the starting instruction too (a prompt is required).
const drafted = flow({...oneBack, llm: {...oneBack.llm, to: ['me'], mode: 'draft', as: 'assistant:dana'}});
assert.equal(drafted.steps.at(-1).type, 'llm');
assert.equal(drafted.steps.at(-1).prompt, 'Pick where this message goes.');
assert.deepEqual(plain(two.steps.slice(-1)[0].rules.map(r => [r.when[0]?.min ?? null, r.to])), [[0.5, 'me'], [0.5, 'eng'], [null, 'me']]);
assert.equal(two.steps.slice(-1)[0].id, 'sf');
assert.ok(shape({...fx.inbox, ...plain(two)}));

// No board: no destinations, a rule going on to the next step, a saved question set, an extra step, a missing
// destination, no LLM at the end, Sort minimums that differ, or a step switched off.
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
// Sort routes with different minimums (the board shows one), or any step switched off (the board can't show it).
assert.equal(without(f => { f.steps[4].rules[1].when[0].min = 0.7; }), null);
assert.equal(without(f => { delete f.steps[4].rules[2].when[0].min; }), null);
assert.equal(without(f => { f.steps[3].enabled = false; f.steps[4].enabled = false; }), null);
assert.equal(without(f => { f.steps[0].enabled = false; }), null);
// A rule with a destination that still moves on (its own next wins) isn't one the board can draw.
assert.equal(without(f => { f.steps[0].rules[0].next = 'rule'; }), null);
assert.equal(shape({trigger: {type: 'message'}, steps: [{id: 'f1', type: 'filter', rules: []}]}), null, 'a plain flow keeps its steps');
const legacy = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/redesign-automations.json'), 'utf8')).automations;
for (const a of legacy) assert.equal(shape(a), null, `${a.id} keeps its step list`);

// The inbox templates (backend/automation_templates) are boards as a person's message flow, so New inbox opens one.
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/automations-new.js'), 'utf8'), context);
const tdir = path.join(__dirname, '../../backend/automation_templates');
const templates = Object.fromEntries(fs.readdirSync(tdir).filter(f => f.endsWith('.json'))
  .map(f => [f.replace(/\.json$/, ''), JSON.parse(fs.readFileSync(path.join(tdir, f), 'utf8'))]));
assert.deepEqual(Object.keys(templates).sort(), ['blank', 'founder', 'sales', 'support']);
const mine = t => ({owner: 'person:dana', trigger: {type: 'message', inbox: 'dana@acme.example'}, steps: clone(t.steps),
  destinations: clone(t.destinations)});
for (const [id, t] of Object.entries(templates)) assert.ok(shape(mine(t)), `${id} is a board`);

// Apply template: the October inbox's one llm step becomes the LLM, its own fields kept and its prompt the instructions.
const october = {id: 'au_old', owner_id: 'person:dana', trigger: {type: 'message', inbox: 'dana@acme.example'},
  steps: [{id: 'read', type: 'llm', as: 'assistant:dana', prompt: 'My rules.', out: 'triage', model: 'm1'}]};
const founder = context.auApplyFlow(october, mine(templates.founder));
const fs1 = plain(founder.steps);
assert.deepEqual(fs1.map(s => s.id), ['rules', 'ask', 'route', 'read']);
assert.deepEqual(fs1[3], {id: 'read', type: 'llm', as: 'assistant:dana', prompt: 'My rules.', out: 'triage', model: 'm1',
  choose: {...templates.founder.steps[3].choose, mode: 'draft'}});
assert.deepEqual(plain(founder.destinations), templates.founder.destinations);
assert.ok(shape({...october, ...plain(founder)}), 'the result is a board');
// Blank sorts with no LLM turn of its own; a person's still gets the draft turn, over the same destinations.
const blank = plain(context.auApplyFlow(october, mine(templates.blank)));
assert.deepEqual(blank.steps, [{...october.steps[0], choose: {to: ['me', 'archive'], default: 'me', mode: 'draft'}}]);
// A bot's turn runs as the bot; an empty old prompt keeps the template's; a clashing out gets a fresh one.
const botOld = {...clone(october), owner_id: 'bot:support'};
botOld.steps[0] = {id: 'read', type: 'llm', as: 'bot:support', prompt: ' ', out: 'q'};
const bot = plain(context.auApplyFlow(botOld, mine(templates.founder)));
const bl = bot.steps.at(-1);
assert.equal(bl.as, 'bot:support');
assert.equal(bl.prompt, templates.founder.steps[3].prompt);
assert.notEqual(bl.out, 'q', 'the questions step keeps q');
assert.equal(bot.steps[1].out, 'q');
// A group sorts: the old prompt instructs the template's Sort, and no llm step is left.
const groupOld = {...clone(october), owner_id: 'group:support', trigger: {type: 'message', inbox: 'slack:#support'}};
const groupFill = mine(templates.blank);
const group = plain(context.auApplyFlow(groupOld, groupFill));
assert.deepEqual(group.steps.map(s => s.type), ['decide', 'filter']);
assert.equal(group.steps[0].questions.route.instructions, 'My rules.');
assert.equal(context.auApplyFlow(october, {steps: [], destinations: []}), null, 'a fill that is no board applies nothing');
console.log('automations-board-unit: ok');
