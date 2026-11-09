// No browser: Ask's rules (ui/app/ask-rules.ts). A turn at work with no words yet says how long it has run and its
// newest step.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../build/ui/app/ask-rules.js'), 'utf8')
  + '\n;globalThis.api = {askToolStep, askWorkingLine};', context);
const {askToolStep, askWorkingLine} = context.api;
const now = 1_800_000_000_000;

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
assert.equal(askToolStep('Ran curl'), 'Running curl', 'a shell program the server named');
assert.equal(askToolStep('Filing a task'), 'Filing a task', 'a label the server already says in words');
const started = new Date(now - 4 * 60000 - 20000).toISOString();
interface Run {
  state: string;
  label: string;
  text: string;
  started_at?: string | undefined;
  parts: {kind: string; text: string}[];
}
const run = (over?: Partial<Run>) => ({state: 'running', label: 'Working', text: '', started_at: started, parts: [
  {kind: 'tool', text: 'Ran hub task list'}, {kind: 'progress', text: 'Clearing them out.'}, {kind: 'tool', text: 'Ran hub task bulk close'}], ...over});
assert.equal(askWorkingLine(run(), now), 'Working · 4m · Closing tasks', 'its label, its time and its newest step');
assert.equal(askWorkingLine(run({parts: []}), now), 'Working · 4m');
assert.equal(askWorkingLine(run({started_at: new Date(now - 125 * 60000).toISOString()}), now), 'Working · 2h 5m · Closing tasks');
assert.equal(askWorkingLine(run({state: 'leased', label: 'Starting', started_at: undefined, parts: []}), now), 'Starting');
for (const state of ['completed', 'queued', 'canceled', 'uncertain']) assert.equal(askWorkingLine(run({state}), now), '', state);
assert.equal(askWorkingLine(null, now), '');
