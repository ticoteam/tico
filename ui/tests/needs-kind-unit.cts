// No browser: Flow sorts an ask by its kind (ui/app/tasks.ts decideKind, tasks-page.ts needsGroup, dashboard.ts dashAct),
// as backend/cards.py needs_act does. Only an OK or a page's OK is approved; an ask of a kind Flow does not know (or one
// whose kind it may not read) is 'other', which never offers Approve or Send back, and an OK on the card as the server
// words every ask. A bot's ❓ is replied to, as a mention is.
// Run: node ui/tests/needs-kind-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const APP = path.join(__dirname, '..', 'app');
/** One top-level declaration of a UI file, from its first line to the next line that starts a declaration. */
function piece(file: string, start: string): string {
  const source = fs.readFileSync(path.join(APP, file), 'utf8'), at = source.indexOf(start);
  assert.ok(at >= 0, `${file} has ${start}`);
  const end = source.slice(at + start.length).search(/\n(?:function |const |let |\/\/|\/\*\*)/);
  return source.slice(at, end < 0 ? undefined : at + start.length + end);
}
const code = [piece('tasks.ts', 'function decideKind('), piece('tasks-page.ts', 'function needsGroup('),
  piece('dashboard.ts', 'const dashAct = ')].join('\n');
const ctx = vm.createContext({});
vm.runInContext(transformSync(code + '\nObject.assign(globalThis, {decideKind, needsGroup, dashAct});', {loader: 'ts', target: 'es2022'}).code, ctx);
const {decideKind, needsGroup, dashAct} = ctx as unknown as Record<'decideKind' | 'needsGroup' | 'dashAct', (it: unknown) => string>;

const ask = (kind: string | null) => ({kind: 'ask', id: 'v:r', line: 'x', since: '', ask: kind ? {kind} : {removed: true}});
for (const kind of ['ok', 'page']) {
  assert.equal(decideKind(ask(kind)), 'ok', `${kind}: an OK`);
  assert.equal(needsGroup(ask(kind)), 'review', `${kind}: under Review`);
  assert.equal(dashAct(ask(kind)), 'ok', `${kind}: ok on the card`);
}
for (const kind of ['mark', null]) {
  assert.equal(decideKind(ask(kind)), 'other', `${kind}: never Approve or Send back`);
  assert.equal(needsGroup(ask(kind)), 'answer', `${kind}: under Answer`);
  assert.equal(dashAct(ask(kind)), 'ok', `${kind}: ok on the card, as the server says`);
}
const mark = {kind: 'question', id: 'qmark:q1', line: 'x', since: ''};
assert.equal(decideKind(mark), 'mention', 'a ❓: replied to in its chat');
assert.equal(needsGroup(mark), 'answer', 'a ❓: under Answer');
assert.equal(dashAct(mark), 'reply', 'a ❓: reply on the card');
console.log('needs-kind-unit: ok');
