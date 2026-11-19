// Run with node. The thread's real row rendering runs without a browser.
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const app = (file: string) => fs.readFileSync(path.join(__dirname, '..', 'app', file), 'utf8');
const thread = app('thread-view.ts');
function declaration(start: string) {
  const offset = thread.indexOf(start);
  assert.ok(offset >= 0, start);
  const next = thread.slice(offset + start.length).search(/\n(?:async function |function |const |let |\/\/)/);
  return thread.slice(offset, offset + start.length + next);
}
const escape: Record<string, string> = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'};
const context = vm.createContext({
  document: {addEventListener() {}},
  esc: (word: unknown) => String(word ?? '').replace(/[&<>"]/g, c => escape[c]!),
  ago: () => '', fmt: () => '', chatDayKey: () => '',
  chatGoalControl: () => false, chatQuietMerge: (html: string) => html,
  thvUpdateGroups: () => new Map(), thvIsNew: () => false, thvJoins: () => false,
  thvMsgHTML: (_view: unknown, m: {body: string}) => `<p>${m.body}</p>`,
});
const source = [app('hub-v2.ts'), ...[
  'function thvRows(', 'const thvTaskUpdate = ', 'function thvLineGroups(', 'const thvLineHTML = ', 'function thvRowsHTML(',
].map(declaration)].join('\n');
vm.runInContext(transformSync(source, {loader: 'ts', target: 'es2022'}).code, context);

const openLines = new Set<string>();
const at = '2026-11-18T10:00:00.000000Z';
context.view = {
  state: 'ready', opts: {}, pending: [], openLines,
  messages: [
    {id: 'one', seq: 1, body: 'Chat before'},
    {id: 'notice', seq: 2, body: 'Started task: Release', sender_id: 'keeper', refs: {task: 'release'}},
    {id: 'two', seq: 4, body: 'Chat between'},
  ],
  taskActivity: [
    {id: 'a', seq: 3, created_at: at, task_id: 'release', title: 'Release <review>', excerpt: 'Checked the tests'},
    {id: 'b', seq: 5, created_at: at, task_id: 'docs', title: 'Docs', excerpt: 'Updated the guide'},
  ],
};
const render = () => vm.runInContext('thvRowsHTML(view)', context) as string;
const html = render();
const groups = [...html.matchAll(/<details\b([^>]*)>([\s\S]*?)<\/details>/g)];
assert.equal(groups.length, 2, 'the chat message separates the update groups');
assert.ok(groups.every(group => !/\bopen\b/.test(group[1]!)), 'every task activity group starts shut');
assert.match(groups[0]![2]!, /2 task updates[\s\S]*Started task: Release[\s\S]*href="#\/task\/release">Release &lt;review&gt;<\/a>[\s\S]*Checked the tests/);
assert.match(groups[1]![2]!, /1 task update[\s\S]*href="#\/task\/docs">Docs<\/a>[\s\S]*Updated the guide/);
assert.match(html, /Chat before[\s\S]*2 task updates[\s\S]*Chat between[\s\S]*1 task update/);

openLines.add('task-activity:a');
assert.match(render(), /<details class="src-group" data-src-group="notice" open>/);
console.log('chat-task-activity-unit: ok');
