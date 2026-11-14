// No browser: Company > Replay (ui/app/replay.ts) names every bot in its chip row, a bot no longer on the roster too.
// - a bot on the roster: its display name, and its own face;
// - a deleted bot: the server's name for it ("Deleted bot"), or "Deleted bot" when the answer has none, never its id;
// - its face is drawn from that name, so its initials never come from the id either.
// Run: node ui/tests/replay-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const GONE = '01a1cdda-6f96-7593-be09-719b45a9df76';
const emps = [{id: 'writer-id', slug: 'writer', display_name: 'Writer'}];
type Face = string | {id: string; display_name: string};
const ctx = vm.createContext({
  window: {addEventListener() {}, matchMedia: () => ({matches: false, addEventListener() {}})},
  document: {addEventListener() {}},
  S: {emps, people: []},
  botRow: (id: string) => emps.find(e => e.id === id),
  botDisplayName: (id: string) => emps.find(e => e.id === id)?.display_name || id,   // the app's: the id when unknown
  actorSlug: (a: unknown) => String(a || '').startsWith('bot:') ? String(a).slice(4) : '',
  actorPerson: (a: unknown) => String(a || '').startsWith('human:') ? String(a).slice(6) : '',
  personDisplay: (id: string) => id,
  AUTOMATIC_LABEL: 'Automatic',
  // The face as the name it would draw initials from.
  botAvatar: (bot: Face) => `<face ${typeof bot === 'string' ? emps.find(e => e.id === bot)?.display_name || bot : bot.display_name}>`,
  personAvatar: () => '', personCircle: (name: string) => `<face ${name}>`,
});
// The names the shell keeps for every page (ui/app/pages-shell.ts, the page parts' ui/parts.json) load first, as in the app.
vm.runInContext(transformSync(fs.readFileSync(path.join(__dirname, '..', 'app', 'pages-shell.ts'), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'pages-shell.ts'});
const file = path.join(__dirname, '..', 'app', 'replay.ts');
vm.runInContext(transformSync(fs.readFileSync(file, 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'replay.ts'});
const name = (names: Record<string, string> | undefined, actor: string): string =>
  (vm.runInContext('replayName', ctx) as (d: unknown) => (a: string) => string)({names})(actor);
const face = (actor: string): string => (vm.runInContext('replayFace', ctx) as (a: string, s: number) => string)(actor, 28);

// 1. A bot on the roster: its display name and face.
assert.equal(name({}, 'bot:writer-id'), 'Writer');
assert.equal(face('bot:writer-id'), '<face Writer>');

// 2. A deleted bot: the server's name for it, else "Deleted bot"; never its id.
assert.equal(name({['bot:' + GONE]: 'Deleted bot'}, 'bot:' + GONE), 'Deleted bot');
assert.equal(name({}, 'bot:' + GONE), 'Deleted bot');
assert.equal(name(undefined, 'bot:' + GONE), 'Deleted bot');

// 3. Its face draws from that name, not the id.
assert.ok(!face('bot:' + GONE).includes(GONE), face('bot:' + GONE));
assert.equal(face('bot:' + GONE), '<face Deleted bot>');

console.log('replay-unit: ok');
