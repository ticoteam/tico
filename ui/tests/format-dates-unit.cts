// No browser: the date words in ui/app/format.ts to the style guide's rule ("Dates and times"): month first, 12-hour
// with no leading zero, Today and Tomorrow for the day, the year only when it is not this year, and under a day old how
// long ago ("35m", "2h"), after that the date ("Nov 6"). Days are the viewer's own: checked on a clock fixed just after
// midnight in Los Angeles (yesterday evening is not Today) and in Kiritimati (UTC+14, where a UTC morning is already
// tomorrow's evening), with bare days ("2026-11-15") read as that day in every zone. Also cronWords in 12-hour words
// and the date field's typed days (dfParse).
// Run: node ui/tests/format-dates-unit.cts (after npm run build:ui)
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/format.js'), 'utf8');
type Words = {
  ago: (v: unknown) => string; until: (v: unknown) => string; fmt: (v: unknown) => string;
  dayWords: (v: unknown, o?: {weekday?: boolean}) => string; timeWords: (v: unknown) => string; clockWords: (v: unknown) => string;
  cronWords: (c: string) => string; dfParse: (t: string) => string | null;
};

// The file's helpers on a clock stopped at `now`, in time zone `tz`. The page parts (timers, observers, the DOM) are stubs.
function wordsAt(now: string, tz: string): Words {
  process.env['TZ'] = tz;
  const fixed = new Date(now).getTime();
  class FixedDate extends Date {
    constructor(...args: unknown[]) { if (args.length) super(...(args as [number])); else super(fixed); }
    static override now() { return fixed; }
  }
  const noop = () => {};
  const context = vm.createContext({
    Date: FixedDate, Intl, Math, Number, String, Array, Object, JSON, RegExp, console,
    setInterval: noop, requestAnimationFrame: noop, addEventListener: noop, matchMedia: () => ({matches: false}),
    document: {addEventListener: noop, hidden: true, documentElement: {}},
    MutationObserver: class { observe() {} }, HTMLInputElement: class {},
    esc: (x: unknown) => String(x),
  });
  Object.defineProperty((context as {HTMLInputElement: {prototype: object}}).HTMLInputElement.prototype, 'value', {get: () => '', set: noop});
  vm.runInContext(`${source};globalThis.W = {ago, until, fmt, dayWords, timeWords, clockWords, cronWords, dfParse};`, context);
  return (context as unknown as {W: Words}).W;
}

// Los Angeles, 12:30 AM on Sat, Nov 14, 2026 (08:30 UTC).
{
  const w = wordsAt('2026-11-14T08:30:00Z', 'America/Los_Angeles');
  assert.equal(w.ago('2026-11-14T08:29:40Z'), 'now', 'under a minute');
  assert.equal(w.ago('2026-11-14T07:55:00Z'), '35m', 'minutes, no "ago"');
  assert.equal(w.ago('2026-11-14T06:30:00Z'), '2h', 'hours');
  assert.equal(w.ago('2026-11-13T09:00:00Z'), '23h', 'still under a day');
  assert.equal(w.ago('2026-11-12T08:00:00Z'), 'Nov 12', 'after a day, the date');
  assert.equal(w.ago('2025-10-10T12:00:00Z'), 'Oct 10, 2025', 'the year when it is not this year');
  assert.equal(w.ago(''), '');
  assert.equal(w.ago('not a time'), '', 'an unreadable time says nothing');
  // Ahead: a minute or less is a fast clock ("now"); more says when it is.
  assert.equal(w.ago('2026-11-14T08:30:40Z'), 'now');
  assert.equal(w.ago('2026-11-14T11:00:00Z'), 'Today, 3:00 AM', 'hours ahead: its time, not "now"');
  assert.equal(w.ago('2026-11-16T20:00:00Z'), 'Mon, Nov 16, 12:00 PM');
  // A bare day is its own day in the viewer's zone, never hours since a UTC midnight.
  assert.equal(w.ago('2026-11-14'), 'Today');
  assert.equal(w.ago('2026-11-12'), 'Nov 12');
  // 11:50 PM yesterday is 40 minutes ago, and its day is not Today.
  assert.equal(w.ago('2026-11-14T07:50:00Z'), '40m');
  assert.equal(w.dayWords('2026-11-14T07:50:00Z'), 'Nov 13');
  assert.equal(w.timeWords('2026-11-14T07:50:00Z'), 'Fri, Nov 13, 11:50 PM');
  assert.equal(w.timeWords('2026-11-14T08:15:00Z'), 'Today, 12:15 AM');
  assert.equal(w.timeWords('2026-11-15T17:05:00Z'), 'Tomorrow, 9:05 AM', '12-hour, no leading zero');
  assert.equal(w.fmt('2026-11-11T18:15:00Z'), 'Wed, Nov 11, 10:15 AM');
  assert.equal(w.clockWords('2026-11-14T20:00:00Z'), '12:00 PM');
  // Bare days are that day wherever the viewer is (not UTC midnight, the evening before here).
  assert.equal(w.dayWords('2026-11-14'), 'Today');
  assert.equal(w.dayWords('2026-11-15'), 'Tomorrow');
  assert.equal(w.dayWords('2026-11-16'), 'Nov 16');
  assert.equal(w.dayWords('2026-11-16', {weekday: true}), 'Mon, Nov 16');
  assert.equal(w.dayWords('2027-01-02'), 'Jan 2, 2027');
  assert.equal(w.until('2026-11-14T09:00:00Z'), 'in 30m');
  assert.equal(w.until('2026-11-14T11:30:00Z'), 'in 3h');
  assert.equal(w.until('2026-11-16T20:00:00Z'), 'Nov 16', 'a day or more ahead: the date');
  assert.equal(w.until('2026-11-14T08:00:00Z'), 'due');
  // Schedules: 12-hour words, no leading zero, :00 left out.
  assert.equal(w.cronWords('0 9 * * *'), 'every day at 9 AM');
  assert.equal(w.cronWords('30 15 * * 5'), 'Fri at 3:30 PM');
  assert.equal(w.cronWords('0 0 * * 1'), 'Mon at 12 AM');
  assert.equal(w.cronWords('0 12 * * 1-5'), 'weekdays at 12 PM');
  assert.equal(w.cronWords('5 9,17 * * *'), 'every day at 9:05 AM and 5:05 PM');
  assert.equal(w.cronWords('*/15 * * * *'), 'every 15 minutes');
  assert.equal(w.cronWords('5 * * * *'), 'every hour at :05');
  // Typed days in the date field.
  for (const [typed, day] of [['', ''], ['2026-11-20', '2026-11-20'], ['11/20/2026', '2026-11-20'], ['11/20/26', '2026-11-20'],
    ['11/20', '2026-11-20'], ['Nov 20, 2026', '2026-11-20'], ['nov 20', '2026-11-20'], ['Tue, Nov 20, 2026', '2026-11-20'],
    ['20 Nov 2026', '2026-11-20'], ['today', '2026-11-14'], ['tomorrow', '2026-11-15'], ['2/30/2026', null], ['13/1/2026', null],
    ['soon', null]] as const) assert.equal(w.dfParse(typed), day, `typed "${typed}"`);
}

// Kiritimati (UTC+14), 10:30 PM on Sat, Nov 14 (08:30 UTC): 11:00 UTC is already Sunday morning there.
{
  const w = wordsAt('2026-11-14T08:30:00Z', 'Pacific/Kiritimati');
  assert.equal(w.timeWords('2026-11-14T11:00:00Z'), 'Tomorrow, 1:00 AM');
  assert.equal(w.timeWords('2026-11-14T08:00:00Z'), 'Today, 10:00 PM');
  assert.equal(w.dayWords('2026-11-14'), 'Today', 'a bare day is the viewer\'s own day here too');
  assert.equal(w.ago('2026-11-13T12:00:00Z'), '21h');
}

// New Year's Eve: a date in the new year shows its year, last year's too, this year's not.
{
  const w = wordsAt('2026-12-31T20:00:00Z', 'America/Los_Angeles');
  assert.equal(w.dayWords('2027-01-01'), 'Tomorrow');
  assert.equal(w.dayWords('2027-01-02'), 'Jan 2, 2027');
  assert.equal(w.dayWords('2026-01-02'), 'Jan 2');
}
console.log('format-dates-unit: month first, 12-hour, Today/Tomorrow, ages under a day, the year only when it differs, in three zones');
