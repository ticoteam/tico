const assert = require('node:assert/strict');

const CPU_RATE = 4;
const FRAME_SAMPLES = 30;
const BUDGET = {p95: 20, max: 50, task: 50};
const MARK = 'dot-perf:';
const percentile = (values, p) => [...values].sort((a, b) => a - b)[Math.floor(values.length * p)];

// Chromium's ts/dur are elapsed microseconds; tts/tdur are thread CPU microseconds. A busy neighbouring browser
// can delay this renderer without doing any work in it. Charge the renderer's work, including style/layout/paint,
// to the frame, not that scheduling delay. CPU throttling suspends the thread too, so restore its 4x cost explicitly.
// Missing thread clocks must fail, never silently turn a broken recording into zero work.
function frameWork(events) {
  const marks = events.filter(e => e.name === 'TimeStamp' && e.args?.data?.message?.startsWith(MARK)).sort((a, b) => a.ts - b.ts);
  const start = marks[0], end = marks.at(-1);
  assert.equal(start?.args.data.message, MARK + 'start', 'frame trace starts before the action');
  assert.equal(end?.args.data.message, MARK + 'end', 'frame trace ends after the action');
  const frames = marks.slice(1, -1);
  assert.ok(frames.length >= FRAME_SAMPLES, `frame trace: ${frames.length} samples`);
  assert.ok(frames.every(e => e.args.data.message === MARK + 'frame'), 'frame trace has only frame samples');
  assert.ok(marks.every(e => e.pid === start.pid && e.tid === start.tid && Number.isFinite(e.tts)), 'frame trace needs renderer thread clocks');
  const work = marks.slice(1).map((e, i) => (e.tts - marks[i].tts) * CPU_RATE / 1000);
  assert.ok(work.every(ms => ms >= 0), 'frame thread clock is monotonic');
  assert.ok(work.some(ms => ms > 0), 'frame thread clock advances');
  const tasks = events.filter(e => e.ph === 'X' && e.name === 'ThreadControllerImpl::RunTask'
    && e.pid === start.pid && e.tid === start.tid && e.ts < end.ts && e.ts + e.dur > start.ts);
  assert.ok(tasks.length && tasks.every(e => Number.isFinite(e.tdur) && e.tdur >= 0), 'frame trace needs task CPU durations');
  return {frames: frames.length, p95: percentile(work, .95), max: Math.max(...work),
    task: Math.max(...tasks.map(e => e.tdur)) * CPU_RATE / 1000};
}

function assertFrameWork(name, work) {
  assert.ok(work.p95 <= BUDGET.p95, `${name}: frame CPU p95 ${work.p95.toFixed(1)} ms (budget ${BUDGET.p95})`);
  assert.ok(work.max <= BUDGET.max, `${name}: frame CPU max ${work.max.toFixed(1)} ms (budget ${BUDGET.max})`);
  assert.ok(work.task <= BUDGET.task, `${name}: task CPU max ${work.task.toFixed(1)} ms (budget ${BUDGET.task})`);
}

module.exports = {CPU_RATE, FRAME_SAMPLES, frameWork, assertFrameWork};
