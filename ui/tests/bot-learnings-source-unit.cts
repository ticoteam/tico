// No browser: the Learnings panel (ui/app/bot-learnings.ts learnSource) links each update to where it was learned.
// A chat source from GET /api/v2/bots/{bot}/memory (backend/memory_history.py source(), its keys pinned in
// backend/tests/test_memory_history.py) names the bot as `bot_id`: the link opens that bot's chat and its
// data-learn-bot/data-learn-jump are what the panel's click hands to jumpToMessage. A task source links to the task.
// Run: node ui/tests/bot-learnings-source-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const ctx = vm.createContext({
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  ago: () => '',
  document: {addEventListener() {}, querySelector: () => null},
  window: {addEventListener() {}},
});
const file = path.join(__dirname, '..', 'app', 'bot-learnings.ts');
vm.runInContext(transformSync(fs.readFileSync(file, 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'bot-learnings.ts'});
ctx.__u = null;
const source = (s: unknown): string => { ctx.__u = {subject: 's', author: 'ops', when: '', files: [], source: s}; return vm.runInContext('learnSource(__u)', ctx) as string; };
const attr = (h: string, name: string): string | undefined => new RegExp(`${name}="([^"]*)"`).exec(h)?.[1];

// 1. A chat source, exactly as the API returns it: the bot's chat, and a jump to that message.
const chat = source({bot_id: 'ops', conversation: 'conv-1', message: 'msg-1', excerpt: 'Remember to check the live PR'});
assert.equal(attr(chat, 'href'), '#/bot/ops', 'chat source links to the bot\'s chat');
assert.equal(attr(chat, 'data-learn-bot'), 'ops', 'the jump goes to that bot');
assert.equal(attr(chat, 'data-learn-jump'), 'msg-1', 'the jump goes to that message');
assert.ok(!chat.includes('undefined'), 'nothing undefined in the link');
assert.ok(chat.includes('Remember to check the live PR'), 'the excerpt is the link text');

// 2. A task source is unchanged: a link to the task, no jump.
const task = source({bot_id: 'ops', task: 'task-9', title: 'Ship it'});
assert.equal(attr(task, 'href'), '#/task/task-9');
assert.equal(attr(task, 'data-learn-jump'), undefined);
assert.ok(task.includes('Ship it'));

// 3. No source (a reader who may not see it): nothing.
assert.equal(source(null), '');

console.log('bot-learnings-source-unit: ok');
