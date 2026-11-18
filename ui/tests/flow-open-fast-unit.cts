// No browser: Flow on a stage or a pipeline (ui/app/decide.ts decideStageOpen) opens at once, without waiting for every
// page of Needs you (needsReadAll, held here as a promise that settles only when a case says so):
// - with some of the walk held, the screen opens on it and the rest joins when the read ends (decideRefresh);
// - with none of it held but the server's count says some wait, the screen opens waiting, and the first item comes in;
// - the server's count of 0 opens nothing and reads nothing;
// - a count the whole list does not bear out closes the waiting screen and answers false, as nothing opening did;
// - with no counts and nothing held, every page is read first, as before;
// - with the whole list held, nothing is read.
// Run: node ui/tests/flow-open-fast-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'decide.ts'), 'utf8');
/** One top-level declaration of decide.ts, from its first line to the next line that starts a declaration. */
function piece(start: string): string {
  const at = source.indexOf(start);
  assert.ok(at >= 0, `decide.ts has ${start}`);
  const end = source.slice(at + start.length).search(/\n(?:async function |function |const |let |\/\/|\/\*\*)/);
  return source.slice(at, end < 0 ? undefined : at + start.length + end);
}

interface Item { id: string; stage: string }
interface St { stage: string; pipeline: string; at: string | null; order: string[]; items: Map<string, Item>; gone: Set<string>; filling: boolean }
let reads = 0, release: (() => void) | null = null;
const log: string[] = [];
const ctx = vm.createContext({
  S: {v2: {needs: [] as Item[], needsNext: null as string | null, needsStages: null as Record<string, number> | null}},
  DEC: null as St | null,
  document: {activeElement: null},
  decideNoFlow: () => false,
  needsAll: () => !ctx.S.v2.needsNext,
  needsReadAll: () => { reads++; return new Promise<void>(r => { release = r; }); },
  decideSource: () => ctx.S.v2.needs,
  decidePipelines: () => [{id: 'p1', stages: [{id: 's1'}, {id: 's2'}]}],
  // The screen as decide.ts opens it, cut to what decideStageOpen reads: the walk's items in the list's order, the one on
  // screen, and whether it showed.
  decideOpen(id: string, items: Item[], stage: string, pipeline: string, end = false) {
    const st: St = ctx.DEC || {stage, pipeline, at: null, order: [], items: new Map(), gone: new Set(), filling: false};
    const walk = items.filter(it => stage ? it.stage === stage : ['s1', 's2'].includes(it.stage));
    if (!walk.length && !end) { log.push(`refused ${stage || pipeline}`); return false; }
    Object.assign(st, {stage, pipeline, order: walk.map(it => it.id), items: new Map(walk.map(it => [it.id, it])), at: walk[0]?.id || null});
    ctx.DEC = st;
    log.push(`open ${stage || pipeline} at ${st.at || '-'}${st.filling ? ' filling' : ''}`);
    return true;
  },
  decideRefresh(st: St, items: Item[]) {
    const walk = items.filter(it => st.stage ? it.stage === st.stage : ['s1', 's2'].includes(it.stage));
    Object.assign(st, {order: walk.map(it => it.id), items: new Map(walk.map(it => [it.id, it]))});
    if (!st.at || !st.items.has(st.at)) st.at = walk[0]?.id || null;
    log.push(`refresh ${st.order.join(',') || '-'} at ${st.at || '-'}${st.filling ? ' filling' : ''}`);
  },
  decideRender: () => { log.push('render'); },
  decideStop: () => { ctx.DEC = null; log.push('stop'); },
});
const code = [piece('const decideFocused = '), piece('const decideBlank = '), piece('const decideLive = '), piece('const decideCurrent = '),
  piece('function decideWaiting('), piece('async function decideStageOpen(')].join('\n');
vm.runInContext(transformSync(code + '\nObject.assign(globalThis, {decideStageOpen});', {loader: 'ts', target: 'es2022'}).code, ctx);
const open = ctx['decideStageOpen'] as (stage: string, pipeline?: string) => Promise<boolean>;

const flush = () => new Promise(r => setImmediate(r));
/** Settles a promise, or says it has not settled after the queue drained. */
const settled = async <T,>(p: Promise<T>): Promise<T | 'waiting'> => Promise.race([p, flush().then(() => 'waiting' as const)]);
function start(held: Item[], next: string | null, stages: Record<string, number> | null) {
  Object.assign(ctx.S.v2, {needs: held, needsNext: next, needsStages: stages});
  ctx.DEC = null; reads = 0; release = null; log.length = 0;
}
/** The read of every page ends with `whole` held. */
async function finish(whole: Item[]) {
  Object.assign(ctx.S.v2, {needs: whole, needsNext: null});
  release!();
  await flush();
}
const a = {id: 'a', stage: 's1'}, b = {id: 'b', stage: 's1'}, c = {id: 'c', stage: 's2'}, x = {id: 'x', stage: 'other'};

(async () => {
  // Some of the stage held: open now, before the read ends.
  start([a, x], 'v.50', {s1: 2, s2: 1});
  assert.equal(await settled(open('s1')), true, 'Flow on a stage answers at once');
  assert.deepEqual(log, ['open s1 at a filling'], 'it opened on the held item, filling');
  assert.equal(reads, 1);
  assert.equal(ctx.DEC!.filling, true, 'the mark moves while the rest is read');
  await finish([a, x, b]);
  assert.deepEqual(log.slice(1), ['refresh a,b at a'], 'the rest joined in the list order, the item on screen kept');
  assert.equal(ctx.DEC!.filling, false);

  // A pipeline's walk with none of it held yet: open waiting, then the first item.
  start([x], 'v.50', {s2: 1});
  const piped = open('', 'p1');
  assert.equal(await settled(piped), 'waiting', 'nothing to show yet: the answer waits on the read');
  assert.deepEqual(log, ['open p1 at - filling'], 'but the screen is already open, waiting');
  await finish([x, c]);
  assert.equal(await piped, true);
  assert.deepEqual(log.slice(1), ['refresh c at c'], 'the first item came on screen');

  // The server's count says nothing waits there: nothing read, nothing opened.
  start([a], 'v.50', {s1: 1});
  assert.equal(await open('s2'), false);
  assert.equal(reads, 0, 'no page read');
  assert.deepEqual(log, [], 'nothing opened');

  // A count the whole list does not bear out: the waiting screen goes, and the caller's empty state follows.
  start([x], 'v.50', {s2: 1});
  const wrong = open('s2');
  await flush();
  await finish([x]);
  assert.equal(await wrong, false);
  assert.deepEqual(log, ['open s2 at - filling', 'refresh - at -', 'stop']);
  assert.equal(ctx.DEC, null);

  // Closed while it filled: the late read does nothing to the screen.
  start([a], 'v.50', {s1: 2});
  assert.equal(await open('s1'), true);
  ctx.DEC = null;
  await finish([a, b]);
  assert.deepEqual(log, ['open s1 at a filling']);

  // No counts and none held: every page is read first, as before.
  start([x], 'v.50', null);
  const old = open('s1');
  assert.equal(await settled(old), 'waiting');
  assert.deepEqual(log, ['refused s1'], 'nothing opens before the read');
  assert.equal(ctx.DEC, null, 'no screen is left half made');
  await finish([x, a]);
  assert.equal(await old, true);
  assert.deepEqual(log.slice(1), ['open s1 at a']);

  // The whole list held: open with no read.
  start([a, b], null, null);
  assert.equal(await open('s1'), true);
  assert.equal(reads, 0);
  assert.deepEqual(log, ['open s1 at a']);
  console.log('flow-open-fast-unit: ok');
})().catch(e => { console.error(e); process.exit(1); });
