// No browser: a stage's held count on the pipeline (ui/app/task-pipelines.ts pipelineStageHeld) is the unfinished cards the
// task list holds in it now, plus the cards there the server counts for the pipeline's rule editor but the viewer cannot
// read (step.unseen, D68). The board then never shows room the cap refuses a bot.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../build/ui/app/task-pipelines.js'), 'utf8'), context);
const held = context.pipelineStageHeld as (tasks: {id: string; step_id: string; status: string}[], step: {id: string; unseen?: number}) => number;
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
// The list's bulk actions (tasksBulkApply) live in task-list-bulk.ts, beside it.
const listSource = ['task-list.js', 'task-list-bulk.js']
  .map(name => fs.readFileSync(path.join(__dirname, '../../build/ui/app', name), 'utf8')).join('\n');
// One function's source from the built list, read up to its closing brace.
function listFunction(name: string) {
  const start = listSource.indexOf(`async function ${name}(`);
  assert.ok(start >= 0, `${name} is defined`);
  let depth = 0, end = listSource.indexOf('{', start);
  for (; end < listSource.length; end++) {
    if (listSource[end] === '{') depth++;
    else if (listSource[end] === '}' && --depth === 0) break;
  }
  return listSource.slice(start, end + 1);
}
const warned: [string, unknown, unknown][] = [];
const card = {id: 'T1', version: 3, type_id: 'line'};
const rowContext = vm.createContext({
  tasksById: () => new Map([['T1', card]]),
  post: async () => ({over_cap: true, task: card}),
  taskOverCapWarn: (task: {id: string}, body: {step: string}, data?: {over_cap?: boolean}) => warned.push([task.id, body.step, data?.over_cap]),
  taskDoneMotion: () => {}, taskRefusedSet: () => {}, v2Get: async () => null, tasksLoad: async () => {},
  TASKS_ST: null,
});
vm.runInContext(listFunction('tasksRowStatusSave'), rowContext);
rowContext.tasksRowStatusSave({}, 'T1', () => ({step: 'doing'})).then(() => {
  assert.deepEqual(warned, [['T1', 'doing', true]], 'the row picker says the stage is over its cap');
  console.log('stage-caps-unit: row picker ok');
});

// A hand-picked bulk move (tasksBulkApply) says it too: one line per stage over its cap, however many cards went.
const bulkWarned: [string, unknown, unknown][] = [];
const picked = [{id: 'T1', version: 1, type_id: 'line'}, {id: 'T2', version: 1, type_id: 'line'}];
const bulkContext = vm.createContext({
  tasksSelectedTasks: () => picked, tasksCanSelect: () => true, tasksMenuClose: () => {}, tasksBulkBar: () => {},
  post: async () => ({over_cap: true}), v2Get: async () => null, pipelineTypeId: () => 'line', actorSlug: () => '',
  taskRequester: () => '', TASKS_ST: null,
  taskOverCapWarn: (task: {id: string}, body: {step: string}, data?: {over_cap?: boolean}) => bulkWarned.push([task.id, body.step, data?.over_cap]),
});
vm.runInContext(listFunction('tasksBulkApply'), bulkContext);
bulkContext.tasksBulkApply({}, {done: 'moved to Doing'}, () => ({step: 'doing'})).then(() => {
  assert.deepEqual(bulkWarned, [['T2', 'doing', true]], 'a bulk move says the stage is over its cap once');
  console.log('stage-caps-unit: bulk move ok');
});

// A Status move names no stage: each card's landing stage (the returned task's step_id) gets its own line.
const statusWarned: [string, unknown, unknown][] = [];
const twoLines = [{id: 'A1', version: 1, type_id: 'one'}, {id: 'B1', version: 1, type_id: 'two'}];
const statusContext = vm.createContext({
  tasksSelectedTasks: () => twoLines, tasksCanSelect: () => true, tasksMenuClose: () => {}, tasksBulkBar: () => {},
  post: async (url: string) => ({over_cap: true, task: {step_id: url.includes('A1') ? 'doing-one' : 'doing-two'}}),
  v2Get: async () => null, pipelineTypeId: () => 'line', actorSlug: () => '', taskRequester: () => '', TASKS_ST: null,
  taskOverCapWarn: (task: {id: string}, body: {step: string}, data?: {over_cap?: boolean}) => statusWarned.push([task.id, body.step, data?.over_cap]),
});
vm.runInContext(listFunction('tasksBulkApply'), statusContext);
statusContext.tasksBulkApply({}, {done: 'moved'}, () => ({status: 'doing'})).then(() => {
  assert.deepEqual(statusWarned, [['A1', 'doing-one', true], ['B1', 'doing-two', true]], 'one line per landing stage');
  console.log('stage-caps-unit: status move ok');
});
