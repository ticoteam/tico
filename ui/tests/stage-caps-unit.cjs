// No browser: a stage's held count on the pipeline (ui/app/task-pipelines.ts pipelineStageHeld) is the unfinished cards the
// task list holds in it now, plus the cards there the server counts for the pipeline's rule editor but the viewer cannot
// read (step.unseen, D68). The board then never shows room the cap refuses a bot.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../build/ui/app/task-pipelines.js'), 'utf8'), context);
const held = context.pipelineStageHeld;
assert.equal(typeof held, 'function', 'pipelineStageHeld is defined');

const tasks = [
  {id: 'T1', step_id: 'spec', status: 'doing'},
  {id: 'T2', step_id: 'spec', status: 'done'},      // finished: it holds no place
  {id: 'T3', step_id: 'dev', status: 'open'},
];
assert.equal(held(tasks, {id: 'spec', unseen: 0}), 1, 'the readable unfinished card');
assert.equal(held(tasks, {id: 'spec', unseen: 1}), 2, 'one private card the editor cannot read counts too');
assert.equal(held(tasks, {id: 'spec'}), 1, 'no unseen: none added');
assert.equal(held([], {id: 'ice', unseen: 2}), 2, 'only unseen cards');
console.log('stage-caps-unit: ok');
