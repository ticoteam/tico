// No browser: Catch up (ui/app/catch-up.ts cuCardHTML) shows Later only when there is something to put the card behind.
// - one card left and no next page: no Later (cuLater would do nothing), Mute and Mark read stay;
// - one card left but a next page to come: Later shows;
// - two cards left (one in the queue, one put off): Later shows.
// Run: node ui/tests/catch-up-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const ctx = vm.createContext({
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  threadOnRail: () => true,
  threadTitle: () => 'general',
  threadHashHTML: () => '',
  threadMarkHTML: () => '',
  threadHref: (t: {id: string}) => '#/threads/' + t.id,
  actorAvatar: () => '',
  actorName: (who: string) => who,
  ago: () => '',
  fmt: () => '',
  safeMd: (s: string) => s,
  messageMd: (cls: string, s: string, _who: string, _o: unknown, tail = '') => `<div class="${cls}">${s}${tail}</div>`,
  document: {addEventListener() {}, querySelector: () => null},
  window: {addEventListener() {}},
});
const file = path.join(__dirname, '..', 'app', 'catch-up.ts');
vm.runInContext(transformSync(fs.readFileSync(file, 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'catch-up.ts'});

const card = (id: string) => ({thread: {id, audience: 'company', me: {can_post: true}}, new_count: 1, more: 0, mention: false,
  messages: [{id: id + '-m', sender_id: 'human:ana', body: 'hello', created_at: '2026-11-12T08:00:00Z', depth: 0}]});
// Catch up's state as cuPage leaves it: `queue` and `later` cards, and the next page's cursor.
function set(queue: unknown[], later: unknown[], next: string | null): void {
  ctx.__s = {queue, later, next};
  vm.runInContext('CU.queue = __s.queue; CU.later = __s.later; CU.next = __s.next;', ctx);
}
const html = (): string => vm.runInContext('cuCardHTML(cuCurrent())', ctx) as string;

// 1. The last card, no next page: Later is left out; Mute and Mark read stay.
set([card('t1')], [], null);
let h = html();
assert.ok(!h.includes('data-cu-later'), 'last card: no Later');
assert.ok(h.includes('data-cu-mute'), 'last card: Mute stays');
assert.ok(h.includes('data-cu-read'), 'last card: Mark read stays');

// 2. One card, but a next page to come: Later shows.
set([card('t1')], [], 'cursor-2');
assert.ok(html().includes('data-cu-later'), 'one card with a next page: Later');

// 3. Two cards: Later shows, whether the second is queued or put off.
set([card('t1'), card('t2')], [], null);
assert.ok(html().includes('data-cu-later'), 'two queued cards: Later');
set([card('t1')], [card('t2')], null);
assert.ok(html().includes('data-cu-later'), 'one queued, one later: Later');

console.log('catch-up-unit: ok');
