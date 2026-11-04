// The performance gate distinguishes a descheduled renderer from expensive work, without accepting missing data.
const assert = require('node:assert/strict');
const {FRAME_SAMPLES, frameWork, assertFrameWork} = require('./support/frame-work.cjs');

function trace({cpu = 1000, stall = 0, task = 1000} = {}) {
  const mark = (name, i) => ({name: 'TimeStamp', args: {data: {message: 'dot-perf:' + name}},
    pid: 7, tid: 9, ts: 1000 + i * 16667 + (i ? stall : 0), tts: 100 + i * cpu});
  return [mark('start', 0), ...Array.from({length: FRAME_SAMPLES}, (_, i) => mark('frame', i + 1)), mark('end', FRAME_SAMPLES + 1),
    {name: 'ThreadControllerImpl::RunTask', ph: 'X', pid: 7, tid: 9, ts: 1000, dur: task + stall, tdur: task}];
}

const plain = frameWork(trace());
assertFrameWork('plain', plain);
assert.deepEqual(frameWork(trace({stall: 200000})), plain, '200 ms off CPU does not become app work');
assert.throws(() => assertFrameWork('busy frames', frameWork(trace({cpu: 6000}))), /frame CPU p95/, 'repeated 24 ms frames fail');
assert.throws(() => assertFrameWork('busy task', frameWork(trace({task: 13000}))), /task CPU max/, 'a 52 ms task fails even across frame boundaries');

const spike = trace();
for (const e of spike) if (e.tts !== undefined && e.ts >= spike[15].ts) e.tts += 13000;
assert.throws(() => assertFrameWork('one busy frame', frameWork(spike)), /frame CPU max/, 'a single expensive frame cannot hide in the percentile');

const foreign = {...trace().at(-1), pid: 99, tdur: 1000000};
assert.deepEqual(frameWork([...trace(), foreign]), plain, 'only the page renderer is charged');
assert.throws(() => frameWork([]), /starts before/);
assert.throws(() => frameWork(trace().filter(e => e.args?.data.message !== 'dot-perf:frame')), /samples/);
assert.throws(() => frameWork(trace().map(({tts, ...e}) => e)), /thread clocks/);
assert.throws(() => frameWork(trace({cpu: 0})), /clock advances/);
assert.throws(() => frameWork(trace().map(({tdur, ...e}) => e)), /task CPU durations/);
