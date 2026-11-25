import { afterEach, describe, expect, it, vi } from 'vitest';
import { boardSearch, closeTask, forgetPlaces, holdList, NEEDS_YOU, nextInList, noteList, notePlace, OVER_PAGE } from './places';

/* Closing the task open over a page leaves the page as it was; an answer goes on to the row below in the list it was
   opened from. */

const router = (state: Record<string, unknown>, pathname: string, search: Record<string, unknown>) => ({
  history: { location: { state }, back: vi.fn() },
  state: { location: { pathname, search } },
  navigate: vi.fn(),
});

afterEach(() => forgetPlaces());

describe('closeTask', () => {
  it('opened over the page in the app: steps back to it (Forward opens it again)', () => {
    const r = router({ [OVER_PAGE]: true }, '/team/bot:eng', { tab: 'work', task: 12 });
    closeTask(r);
    expect(r.history.back).toHaveBeenCalled();
    expect(r.navigate).not.toHaveBeenCalled();
  });

  it('opened from a link or a reload: the same page, only the task taken out of its address', () => {
    const r = router({}, '/tasks', { view: 'all', task: 12, list: 'p1', 'task.tab': 'details', 'task.v': 2 });
    closeTask(r);
    expect(r.history.back).not.toHaveBeenCalled();
    expect(r.navigate).toHaveBeenCalledWith({ to: '/tasks', search: { view: 'all' }, hash: true, replace: true, resetScroll: false, hashScrollIntoView: false });
  });
});

describe('boardSearch', () => {
  it('keeps the board’s own words, never a task open over it', () => {
    notePlace('/tasks/p1', '', { view: 'list', task: 12, list: 'p1', 'task.tab': 'details' });
    expect(boardSearch('p1')).toEqual({ view: 'list' });
  });
});

describe('nextInList', () => {
  const rows = [
    { id: 't7', n: '7' },
    { id: 't12', n: '12' },
    { id: 't5', n: '5' },
  ];

  it('the row below in the list the task was opened from, past rows that left since the answer', () => {
    noteList({ pipeline: NEEDS_YOU, open: '7', rows });
    holdList();
    // the answer takes 7 and 12 out of the list
    noteList({ pipeline: NEEDS_YOU, open: '7', rows: [{ id: 't5', n: '5' }] });
    expect(nextInList('t7', NEEDS_YOU)).toBe('5');
    expect(nextInList('t5', NEEDS_YOU)).toBeNull();
  });

  it('none while another list is drawn', () => {
    noteList({ pipeline: 'p1', open: '7', rows });
    expect(nextInList('t7', 'p1')).toBe('12');
    expect(nextInList('t7', NEEDS_YOU)).toBeNull();
    expect(nextInList('t7', 'needs-you')).toBeNull();
  });
});
