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

// The Tasks list's row picker (ui/app/task-list.ts tasksRowStatusSave) says a move over a stage's cap, as the board and
// the task do: the server's over_cap reaches taskOverCapWarn.
const listSource = fs.readFileSync(path.join(__dirname, '../../build/ui/app/task-list.js'), 'utf8');
const start = listSource.indexOf('async function tasksRowStatusSave(');
assert.ok(start >= 0, 'tasksRowStatusSave is defined');
let depth = 0, end = listSource.indexOf('{', start);
for (; end < listSource.length; end++) {
  if (listSource[end] === '{') depth++;
  else if (listSource[end] === '}' && --depth === 0) break;
}
const warned = [];
const card = {id: 'T1', version: 3, type_id: 'line'};
const rowContext = vm.createContext({
  tasksById: () => new Map([['T1', card]]),
  post: async () => ({over_cap: true, task: card}),
  taskOverCapWarn: (task, body, data) => warned.push([task.id, body.step, data?.over_cap]),
  taskDoneMotion: () => {}, taskRefusedSet: () => {}, v2Get: async () => null, tasksLoad: async () => {},
  TASKS_ST: null,
});
vm.runInContext(listSource.slice(start, end + 1), rowContext);
rowContext.tasksRowStatusSave({}, 'T1', () => ({step: 'doing'})).then(() => {
  assert.deepEqual(warned, [['T1', 'doing', true]], 'the row picker says the stage is over its cap');
  console.log('stage-caps-unit: row picker ok');
});
