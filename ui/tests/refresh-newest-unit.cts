// No browser: refreshes whose answers arrive out of order. Each value (status, roster, seats) shows the newest
// refresh that read it, so an older poll that answers last cannot put back the roster from before a change.
// Run: node ui/tests/refresh-newest-unit.cts (after npm run build:ui)
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

// One read refresh() started: its path, whether it asked fresh, and how to answer it.
interface Read {path: string; fresh: boolean; resolve(value: unknown): void; reject(error: unknown): void}
interface Shown {status?: unknown; emps?: unknown; people?: unknown; seats?: unknown; statusPending?: boolean; overviewRosterFresh?: boolean}

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/refresh.js'), 'utf8');
// Only the refresh loop: the rest of the file wires live events and window listeners as it loads.
const start = source.indexOf('let SEATS_POLLS'), end = source.indexOf('const fieldsOf');
assert.ok(start >= 0 && end > start, 'the refresh loop is where this test reads it');

function app() {
  const reads: Read[] = [];
  const S: Shown = {};
  const nothing = () => {};
  const context = vm.createContext({
    S, BOT: null, TASKS_ST: null, PERSON_TASKS: null, Promise, Date,
    document: {querySelectorAll: () => []}, $: () => null,
    get: (p: string, options: {fresh?: boolean}) => new Promise((resolve, reject) => reads.push({path: p, fresh: !!options?.fresh, resolve, reject})),
    v2Refresh: async () => {}, overviewLoadComputers: async () => {},
    namedRoster: (emps: unknown) => emps, setPeople: (v: {people: unknown; seats?: unknown}) => { S.people = v.people; if (Array.isArray(v.seats)) S.seats = v.seats; },   // as sidebar.ts does
    cloudAccessFromStatus: nothing, overviewRefresh: nothing, renderTree: nothing, renderHeartbeat: nothing, pausedRender: nothing,
    botAvatarsSync: nothing, goalsFollowRoster: nothing, tipRefresh: nothing, botAlertDraw: nothing, isTasksRoute: () => false,
  });
  vm.runInContext(`${source.slice(start, end)};globalThis.refresh = refresh;`, context);
  const refresh = (context as unknown as {refresh(force?: boolean): Promise<void>}).refresh;
  // Start a refresh; answer(roster) gives every read it made that roster's answer, then waits for it to finish.
  const begin = (force: boolean) => {
    const from = reads.length, done = refresh(force), mine = reads.slice(from);
    return {
      reads: mine.map(r => r.path),
      async answer(name: string, fail: string[] = []) {
        for (const r of mine) {
          if (fail.includes(r.path)) r.reject(new Error('offline'));
          else r.resolve(({'/status': {name}, '/employees': [{slug: name}], '/humans': {people: [{id: name}], seats: [{id: `${name} (people)`}]}, '/v2/seats': {seats: [{id: name}]}} as Record<string, unknown>)[r.path]);
        }
        await done;
      },
    };
  };
  return {S, begin, reads};
}
const roster = (S: Shown) => ({status: (S.status as {name: string} | null)?.name, emps: (S.emps as {slug: string}[])[0]!.slug,
  people: (S.people as {id: string}[])[0]!.id, seats: (S.seats as {id: string}[])[0]!.id});

(async () => {
  {
    // AC-1 and AC-4: a 2-minute poll waits on its answers; a change's forced refresh answers first, the poll last.
    const {S, begin} = app();
    await (begin(false)).answer('boot');
    const poll = begin(false), forced = begin(true);
    await forced.answer('new');
    assert.deepEqual(roster(S), {status: 'new', emps: 'new', people: 'new', seats: 'new'});
    await poll.answer('old');
    assert.deepEqual(roster(S), {status: 'new', emps: 'new', people: 'new', seats: 'new'}, 'the older poll does not put back the old roster or status');
    assert.equal(S.overviewRosterFresh, true);
  }
  {
    // AC-2: answers in start order: the later refresh is shown, as before.
    const {S, begin} = app();
    const first = begin(true), second = begin(true);
    await first.answer('first');
    assert.deepEqual(roster(S), {status: 'first', emps: 'first', people: 'first', seats: 'first'});
    await second.answer('second');
    assert.deepEqual(roster(S), {status: 'second', emps: 'second', people: 'second', seats: 'second'});
  }
  {
    // AC-3: an older forced refresh read seats, a newer poll did not; the poll finishes first. The forced refresh's
    // seats are still shown, and its roster is not (the poll's is newer).
    const {S, begin} = app();
    await (begin(false)).answer('boot');
    const forced = begin(true), poll = begin(false);
    assert.ok(forced.reads.includes('/v2/seats') && !poll.reads.includes('/v2/seats'), 'only the forced refresh reads seats');
    await poll.answer('poll');
    assert.equal(roster(S).seats, 'poll (people)', 'the people read carries seats too');
    await forced.answer('forced');
    assert.deepEqual(roster(S), {status: 'poll', emps: 'poll', people: 'poll', seats: 'forced'});
  }
  {
    // A newer refresh whose roster read failed does not hide an older one's good answer, nor mark the newer roster stale.
    const {S, begin} = app();
    await (begin(false)).answer('boot');
    const older = begin(true), newer = begin(true);
    await newer.answer('newer', ['/employees']);
    assert.equal(S.overviewRosterFresh, false);
    await older.answer('older');
    assert.deepEqual(roster(S), {status: 'newer', emps: 'older', people: 'older', seats: 'newer'});
    assert.equal(S.overviewRosterFresh, true);
    // And an older roster that fails after a newer one was shown leaves it shown and fresh.
    const late = begin(true), latest = begin(true);
    await latest.answer('latest');
    await late.answer('late', ['/humans']);
    assert.equal(roster(S).emps, 'latest');
    assert.equal(S.overviewRosterFresh, true);
  }
  {
    // AC-5: one refresh alone shows everything it read; a forced one asks fresh.
    const {S, begin, reads} = app();
    await (begin(true)).answer('only');
    assert.deepEqual(roster(S), {status: 'only', emps: 'only', people: 'only', seats: 'only'});
    assert.equal(S.statusPending, false);
    assert.ok(reads.every(r => r.fresh));
  }
  console.log('refresh-newest-unit: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
