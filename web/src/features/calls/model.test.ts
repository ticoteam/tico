import { describe, expect, it } from 'vitest';
import type { CalendarOccurrence } from '@/api/schema';
import { calendarSearch, dayKey, hoursFor, joinOf, onDay, place, rangeOf, rangeWords, step, type Placed } from './model';

const d = (s: string) => {
  const [y, m, day] = s.split('-').map(Number);
  return new Date(y as number, (m as number) - 1, day);
};
const keys = (ds: Date[]) => ds.map(dayKey);

const occ = (p: Partial<CalendarOccurrence>): CalendarOccurrence => ({
  all_day: false,
  attendees: [],
  busy_only: false,
  calendar: { id: 'c1', name: 'Work', ref: 'cal:1', type: 'tico' },
  can: { edit: true, join: false },
  end: '',
  event_id: null,
  home: 'tico',
  kind: 'event',
  links: { attempt: null, automation: null, meeting: null, task: null, thread: null },
  meeting_url: null,
  occurrence_id: 'o1',
  open_url: null,
  recurring: false,
  response: null,
  start: '',
  status: 'confirmed',
  time_zone: null,
  title: 'Planning',
  ...p,
});
const at = (day: string, h: number, m = 0) => new Date(d(day).getTime() + (h * 60 + m) * 60_000).toISOString();

describe('ranges', () => {
  it('a week runs Sunday to Saturday around the day', () => {
    const r = rangeOf('week', d('2026-11-18'));
    expect(dayKey(r.from)).toBe('2026-11-15');
    expect(dayKey(r.to)).toBe('2026-11-22');
    expect(r.days).toHaveLength(7);
  });

  it('a month draws whole weeks from the Sunday before the first', () => {
    const r = rangeOf('month', d('2026-11-18'));
    expect(dayKey(r.from)).toBe('2026-11-01');
    expect(r.weeks).toBe(5);
    expect(dayKey(r.days.at(-1) as Date)).toBe('2026-12-05');
    const feb = rangeOf('month', d('2026-02-10'));
    expect(dayKey(feb.from)).toBe('2026-02-01');
    expect(feb.weeks).toBe(4);
  });

  it('a day is one day, and List is the week ahead', () => {
    expect(keys(rangeOf('day', d('2026-11-18')).days)).toEqual(['2026-11-18']);
    const list = rangeOf('list', d('2026-11-18'));
    expect(dayKey(list.from)).toBe('2026-11-18');
    expect(dayKey(list.to)).toBe('2026-11-25');
  });

  it('steps by the view, a month landing on its first', () => {
    expect(dayKey(step('day', d('2026-11-30'), 1))).toBe('2026-12-01');
    expect(dayKey(step('week', d('2026-11-18'), -1))).toBe('2026-11-11');
    expect(dayKey(step('month', d('2026-01-31'), 1))).toBe('2026-02-01');
    expect(dayKey(step('month', d('2026-01-15'), -1))).toBe('2025-12-01');
  });

  it('names the range in the head', () => {
    expect(rangeWords('week', d('2026-11-18'))).toBe('Nov 15 to Nov 21');
    expect(rangeWords('month', d('2026-11-18'))).toBe('November 2026');
    expect(rangeWords('day', d('2026-11-18'))).toBe('Wed, Nov 18');
  });
});

describe('the address', () => {
  it('keeps a known view, a real day and a panel; drops the rest', () => {
    expect(calendarSearch({ view: 'month', date: '2026-11-18', panel: 'event:o1', x: 1 })).toEqual({
      view: 'month',
      date: '2026-11-18',
      panel: 'event:o1',
    });
    expect(calendarSearch({ view: 'year', date: '2026-02-30', panel: 'task:1' })).toEqual({});
  });
});

describe('placing things in the hours', () => {
  const day = '2026-11-18';
  const p = (id: string, h: number, len: number): Placed => {
    const s = h * 60;
    return { o: occ({ occurrence_id: id, start: at(day, h), end: at(day, h, len) }), s, e: s + len };
  };

  it('clips a thing to the day and keeps all-day things apart', () => {
    const list = [
      occ({ occurrence_id: 'late', start: at(day, 23), end: at('2026-11-19', 1) }),
      occ({ occurrence_id: 'all', all_day: true, start: '2026-11-18', end: '2026-11-19' }),
    ];
    const today = onDay(list, d(day));
    expect(today.map((x) => [x.o.occurrence_id, x.s, x.e])).toEqual([
      ['all', null, null],
      ['late', 1380, 1440],
    ]);
    expect(onDay(list, d('2026-11-19')).map((x) => [x.o.occurrence_id, x.s, x.e])).toEqual([['late', 0, 60]]);
  });

  it('stands overlapping things side by side, and folds past the cap into one "more"', () => {
    const three = [p('a', 9, 60), p('b', 9, 30), p('c', 9, 45)];
    const two = place(three.slice(0, 2), 5, false);
    expect(two.map((b) => [b.p?.o.occurrence_id, b.col, b.cols])).toEqual([
      ['a', 0, 2],
      ['b', 1, 2],
    ]);
    const capped = place(three, 2, false);
    expect(capped.filter((b) => b.p)).toHaveLength(1);
    expect(capped.find((b) => b.more)?.more).toBe(2);
  });

  it('in a week lays the later one over the earlier', () => {
    const blocks = place([p('a', 9, 60), p('b', 9.5, 60)], 2, true);
    expect(blocks.map((b) => [b.p?.o.occurrence_id, b.over])).toEqual([
      ['a', false],
      ['b', true],
    ]);
  });

  it('widens the hours to what is on', () => {
    expect(hoursFor([], 'week')).toEqual([7, 20]);
    expect(hoursFor([p('early', 6, 30), p('late', 21, 60)], 'week')).toEqual([6, 22]);
  });
});

describe('joining', () => {
  it('a Tico call by its id, another app by its link, nothing without either', () => {
    const id = '0f8fad5b-d9cb-469f-a165-70867728950e';
    expect(joinOf(occ({ meeting_url: `https://app.example/call/${id}` }))).toEqual({ call: id });
    expect(joinOf(occ({ meeting_url: 'https://meet.google.com/abc-defg-hij' }))).toEqual({ url: 'https://meet.google.com/abc-defg-hij' });
    expect(joinOf(occ({ can: { edit: true, join: true }, event_id: id.replace(/-/g, '') }))).toEqual({ call: id });
    expect(joinOf(occ({}))).toBeNull();
  });
});
