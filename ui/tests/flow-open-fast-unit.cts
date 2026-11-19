// No browser: Flow opens at once, without waiting for every page of Needs you (needsReadAll, held here as a promise that
// settles only when a case says so; each page it reads in between is `page`). On a stage or a pipeline (ui/app/decide.ts
// decideStageOpen):
// - with some of the walk held, the screen opens on it and the rest joins when the read ends (decideRefresh);
// - with none of it held but the server's count says some wait, the screen opens waiting, and the first item comes in;
// - the server's count of 0 opens nothing and reads nothing;
// - a count the whole list does not bear out closes the waiting screen and answers false, as nothing opening did;
// - with no counts and nothing held, every page is read first, as before;
// - with the whole list held, nothing is read.
// On a bot or a team (decideBotsFlow: a bot's page, a team's page, a setup mark):
// - the screen opens on the bot's held items, its mark moving, and each page read adds its items;
// - with none held but the server's per-bot count says some wait, it waits there (not All done), and the first comes in;
// - the count says none: All done at once, nothing read; all of them held already: nothing read either;
// - All done shows only once the read ends.
// The crumb's menu inside the screen (decideWalkTo) opens its walk at once and fills it the same way.
// A task opened while it waits on the person's ask (decideTaskOpen): held, the ask opens; not held, the task's window
// opens at once (false) and the one ask, read by its id (GET /v2/needs-you?ids=<visit>:<requirement>), comes over it.
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

interface Item { id: string; stage: string; bot?: string; kind?: string; task_id?: string; requirement_id?: string }
interface St {
  stage: string; pipeline: string; at: string | null; order: string[]; items: Map<string, Item>; gone: Set<string>; filling: boolean;
  bots: string[]; ping: {scope: string}; asked: Item | null;
}
let reads = 0, release: (() => void) | null = null, got: (() => void) | null = null;
const log: string[] = [], gets: string[] = [];
let answer: ((r: {items: Item[]} | null) => void) | null = null;
const modal = {open: false, dataset: {task: ''}, contains: () => false};
const ctx = vm.createContext({
  S: {v2: {needs: [] as Item[], needsNext: null as string | null, needsStages: null as Record<string, number> | null,
    needsBots: null as Record<string, number> | null}},
  DEC: null as St | null,
  DEC_OTHER: '-',
  document: {activeElement: null, querySelector: (q: string) => q === '#task-modal' ? modal : null},
  decideNoFlow: () => false,
  needsAll: () => !ctx.S.v2.needsNext,
  needsReadAll: (each?: () => void) => { reads++; got = each || null; return new Promise<void>(r => { release = r; }); },
  needsBotsWhole: () => ctx.S.v2.needsNext && ctx.S.v2.needsBots ? ctx.S.v2.needsBots : null,
  needsHeld: () => ctx.S.v2.needs,
  needsAsEntries: (items: Item[] | undefined) => items || [],
  decideSource: () => ctx.S.v2.needs,
  decidePipelines: () => [{id: 'p1', stages: [{id: 's1'}, {id: 's2'}]}],
  decideMenu: () => false,
  decideInWalk: (st: St, it: Item) => walkOf(st, [it]).length > 0,
  decidePing: () => { log.push('ping'); },
  myActor: () => 'human:ana',
  decideRid: (it: Item) => it.requirement_id || it.id,
  decideTaskId: (it: Item) => it.task_id || '',
  v2Refresh: () => { log.push('refresh list'); return Promise.resolve(); },
  v2Get: (path: string) => { gets.push(path); return new Promise(r => { answer = r; }); },
  // The screen as decide.ts opens it, cut to what these read: the walk's items in the list's order (a bot's or a team's
  // only theirs), the one on screen, and whether it showed.
  decideOpen(id: string, items: Item[], stage = '', pipeline = '', end = false) {
    const st: St = ctx.DEC || {stage, pipeline, at: null, order: [], items: new Map(), gone: new Set(), filling: false,
      bots: [], ping: {scope: 'reports'}, asked: null};
    const walk = walkOf({...st, stage, pipeline}, items);
    const to = id ? walk.find(it => it.id === id)?.id || null : walk[0]?.id || null;
    if (!to && !end) { log.push(`refused ${stage || pipeline}`); return false; }
    Object.assign(st, {stage, pipeline, order: walk.map(it => it.id), items: new Map(walk.map(it => [it.id, it])), at: to});
    ctx.DEC = st;
    log.push(`open ${stage || pipeline} at ${st.at || '-'}${st.filling ? ' filling' : ''}`);
    return true;
  },
  decideRefresh(st: St, items: Item[]) {
    const walk = walkOf(st, items);
    Object.assign(st, {order: walk.map(it => it.id), items: new Map(walk.map(it => [it.id, it]))});
    if (!st.at || !st.items.has(st.at)) st.at = walk[0]?.id || null;
    log.push(`refresh ${st.order.join(',') || '-'} at ${st.at || '-'}${st.filling ? ' filling' : ''}`);
  },
  decideRender: (st: St) => { log.push(st.filling ? 'render filling' : st.at ? 'render' : 'render All done'); },
  decideStop: () => { ctx.DEC = null; log.push('stop'); },
});
/** The items a walk takes (decide.ts decideInWalk, cut down): a bot's or a team's only theirs, a stage's that stage's. */
function walkOf(st: {stage: string; pipeline?: string; bots?: string[]; ping?: {scope: string}}, items: Item[]): Item[] {
  return items.filter(it => (!st.ping || st.ping.scope === 'reports' || (st.bots || []).includes(it.bot || ''))
    && (st.stage ? it.stage === st.stage : !st.pipeline || ['s1', 's2'].includes(it.stage)));
}
const code = [piece('const decideFocused = '), piece('const decideBlank = '), piece('const decideLive = '), piece('const decideCurrent = '),
  piece('function decideWaiting('), piece('async function decideStageOpen('), piece('async function decideFill('), piece('const decideMore = '),
  piece('async function decideBotsFlow('), piece('async function decideWalkTo('),
  piece('const decideAskOf = '), piece('function decideAskHeld('), piece('async function decideAskRead('), piece('function decideAskShow('),
  piece('async function decideTaskOpen('), piece('async function decideAskAttach('), piece('function decideHeldAsk(')].join('\n');
vm.runInContext(transformSync(code + '\nObject.assign(globalThis, {decideStageOpen, decideBotsFlow, decideWalkTo, decideTaskOpen});',
  {loader: 'ts', target: 'es2022'}).code, ctx);
const open = ctx['decideStageOpen'] as (stage: string, pipeline?: string) => Promise<boolean>;
const botsFlow = ctx['decideBotsFlow'] as (from: unknown, bots: string[], name: string, ping: {scope: string; id?: string}, ask?: boolean) => Promise<void>;
const walkTo = ctx['decideWalkTo'] as (stage: string, pipeline: string) => Promise<void>;
const taskOpen = ctx['decideTaskOpen'] as (task: {id: string; turn?: unknown; waits?: unknown[]; status?: string; visit_id?: string | null}) => Promise<boolean>;

const flush = () => new Promise(r => setImmediate(r));
/** Settles a promise, or says it has not settled after the queue drained. */
const settled = async <T,>(p: Promise<T>): Promise<T | 'waiting'> => Promise.race([p, flush().then(() => 'waiting' as const)]);
function start(held: Item[], next: string | null, stages: Record<string, number> | null, bots: Record<string, number> | null = null) {
  Object.assign(ctx.S.v2, {needs: held, needsNext: next, needsStages: stages, needsBots: bots});
  ctx.DEC = null; reads = 0; release = null; got = null; answer = null; log.length = 0; gets.length = 0;
  Object.assign(modal, {open: false, dataset: {task: ''}});
}
/** One more page of the read lands, the list now `held` with more still to come. */
function page(held: Item[]) {
  ctx.S.v2.needs = held;
  got!();
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

  // ---- a bot's Flow mark (its page, a team's, a setup mark: decideBotsFlow)
  const p1 = {id: 'p1', stage: 's1', bot: 'bot:legal'}, p2 = {id: 'p2', stage: '', bot: 'bot:legal'}, p3 = {id: 'p3', stage: 's2', bot: 'bot:legal'};
  const q1 = {id: 'q1', stage: 's1', bot: 'bot:pm'};
  const legal = (ask = false) => botsFlow(null, ['bot:legal'], 'Legal Owner', {scope: 'bot', id: 'legal'}, ask);

  // Some of the bot's held, more waiting past the first page: open now on what is held, filling; pages add as they come.
  start([p1, q1], 'v.50', null, {'bot:legal': 3, 'bot:pm': 1});
  const flowed = legal();
  assert.equal(await settled(flowed), 'waiting', 'the read goes on behind the open screen');
  assert.deepEqual(log, ['open  at p1 filling'], 'the screen opened at once, before any page, on the held item');
  assert.equal(reads, 1);
  page([p1, q1, p2]);
  assert.deepEqual(log.slice(1), ['refresh p1,p2 at p1 filling'], 'a page that came added its item, the one on screen kept');
  await finish([p1, q1, p2, p3]);
  assert.equal(await flowed, undefined);
  assert.deepEqual(log.slice(2), ['refresh p1,p2,p3 at p1'], 'the last of it joined once all was read');
  assert.equal(ctx.DEC!.filling, false);

  // None of the bot's held, but the count says some wait: the screen waits on the read (not All done), then the first comes.
  start([q1], 'v.50', null, {'bot:legal': 1, 'bot:pm': 1});
  const waited = legal();
  await flush();
  assert.deepEqual(log, ['open  at - filling'], 'open, waiting with the mark moving, never All done');
  page([q1, p1]);
  assert.deepEqual(log.slice(1), ['refresh p1 at p1 filling'], 'the first item came on screen with its page');
  await finish([q1, p1]);
  await waited;
  assert.equal(ctx.DEC!.at, 'p1');

  // The count says none of the bot's wait: All done at once, and nothing read.
  start([q1], 'v.50', null, {'bot:pm': 1});
  await legal();
  assert.equal(reads, 0, 'no page read');
  assert.deepEqual(log, ['open  at -'], 'All done at once (not filling)');
  assert.equal(ctx.DEC!.filling, false);

  // Every one of the bot's held already (its count says so): opened on them, nothing more read.
  start([p1, q1, p2], 'v.50', null, {'bot:legal': 2, 'bot:pm': 1});
  await legal();
  assert.equal(reads, 0, 'no page read');
  assert.deepEqual(log, ['open  at p1']);

  // A team with no bots: All done at once too.
  start([q1], 'v.50', null, {'bot:pm': 1});
  await botsFlow(null, [], 'Empty', {scope: 'group', id: 'empty'});
  assert.equal(reads, 0);
  assert.deepEqual(log, ['open  at -']);

  // The count over-says (an item answered meanwhile): All done only once the read ends.
  start([q1], 'v.50', null, {'bot:legal': 1, 'bot:pm': 1});
  const over = legal();
  await flush();
  assert.deepEqual(log, ['open  at - filling']);
  await finish([q1]);
  await over;
  assert.deepEqual(log.slice(1), ['refresh - at -', 'render All done'], 'All done after the read, not before');

  // A setup mark pings the bot as the screen opens, the read still behind it.
  start([], 'v.50', null, {'bot:legal': 1});
  const setup = legal(true);
  await flush();
  assert.deepEqual(log, ['open  at - filling', 'ping']);
  await finish([p1]);
  await setup;

  // Closed while it filled: the late read does nothing to the screen.
  start([p1], 'v.50', null, {'bot:legal': 2});
  const closed = legal();
  ctx.DEC = null;
  await finish([p1, p2]);
  await closed;
  assert.deepEqual(log, ['open  at p1 filling']);

  // The whole list held: no read, no filling.
  start([p1, q1], null, null, null);
  await legal();
  assert.equal(reads, 0);
  assert.deepEqual(log, ['open  at p1']);

  // ---- the crumb's menu inside the screen (decideWalkTo)
  start([x], 'v.50', {s1: 2});
  ctx.DEC = {stage: '', pipeline: '', at: 'x', order: ['x'], items: new Map([['x', x]]), gone: new Set(), filling: false,
    bots: ['bot:legal'], ping: {scope: 'bot'}, asked: null};
  const walked = walkTo('s1', '');
  await flush();
  assert.deepEqual(log, ['open s1 at - filling'], 'the stage opened at once, waiting on its items');
  assert.equal(ctx.DEC!.ping.scope, 'reports', 'the menu walks all of the person\'s, not the bot\'s');
  page([x, a]);
  assert.deepEqual(log.slice(1), ['refresh a at a filling']);
  await finish([x, a, b]);
  await walked;
  assert.deepEqual(log.slice(2), ['refresh a,b at a']);

  // The menu's stage with a count of 0 (a stale menu): no read, All mine as before.
  start([x], 'v.50', {s1: 1});
  ctx.DEC = {stage: 's1', pipeline: '', at: null, order: [], items: new Map(), gone: new Set(), filling: false,
    bots: [], ping: {scope: 'reports'}, asked: null};
  await walkTo('s2', '');
  assert.equal(reads, 0);
  assert.deepEqual(log, ['refused s2', 'open  at x']);

  // ---- a task opened while it waits on the person's own ask (decideTaskOpen)
  const ask = {id: 'v9:r1', stage: 's1', kind: 'ask', task_id: 't1', requirement_id: 'r1'};
  const task = {id: 't1', status: 'open', visit_id: 'v9', turn: {ask: true, requirement_id: 'r1', who: 'human:ana'}};

  // Held: its ask opens, nothing read.
  start([x, ask], 'v.50', null);
  assert.equal(await taskOpen(task), true);
  assert.deepEqual(log, ['open  at v9:r1']);
  assert.deepEqual(gets, []);

  // Not held: the task's window is not held up (false at once, even with the read never answering); only that one ask is
  // read, by its id, and it comes over the task's window once it lands.
  start([x], 'v.50', null);
  assert.equal(await settled(taskOpen(task)), false, 'the task window opens at once');
  assert.deepEqual(gets, ['/v2/needs-you?ids=v9%3Ar1'], 'one ask read by its id, no page of the list');
  assert.equal(reads, 0);
  Object.assign(modal, {open: true, dataset: {task: 't1'}});
  answer!({items: [ask]});
  await flush();
  assert.deepEqual(log, ['open  at v9:r1'], 'the ask opened over the task window');
  assert.equal(ctx.DEC!.asked?.id, 'v9:r1', 'kept in the walk while the list held does not reach it');

  // The window was closed (or moved to another task) before the ask came: nothing opens over the page.
  start([x], 'v.50', null);
  assert.equal(await taskOpen(task), false);
  answer!({items: [ask]});
  await flush();
  assert.deepEqual(log, []);
  assert.equal(ctx.DEC, null);

  // Not waiting on this person: no read at all.
  start([x], 'v.50', null);
  assert.equal(await taskOpen({...task, turn: {ask: true, requirement_id: 'r1', who: 'human:bo'}}), false);
  assert.deepEqual(gets, []);
  console.log('flow-open-fast-unit: ok');
})().catch(e => { console.error(e); process.exit(1); });
