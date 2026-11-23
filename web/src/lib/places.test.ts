import { afterEach, describe, expect, it, vi } from 'vitest';
import { forgetPlaces, leaveTask, nextInList, noteList, notePlace } from './places';

/* Closing a task goes back to where it was opened from, never to the task itself. */

const at = (n: number) => ({ __TSR_index: n });
const router = (step: number) => {
  const heard = new Set<() => void>();
  const history = { location: { state: at(step) }, go: vi.fn(), subscribe: (on: () => void) => (heard.add(on), () => heard.delete(on)) };
  return { history, navigate: vi.fn(), move: () => heard.forEach((on) => on()) };
};
const board = (p: string | null) => () => p;

afterEach(() => forgetPlaces());

describe('leaveTask', () => {
  it('a cold-loaded old Flow link is the task, not where it was opened from: its board, never the task again', async () => {
    notePlace('/flow/12', '?tab=details', { tab: 'details' }, at(0));
    notePlace('/tasks/p1/12/details', '', {}, at(0));
    const r = router(0);
    leaveTask(r, board('p1'));
    await vi.waitFor(() => expect(r.navigate).toHaveBeenCalledWith({ href: '/tasks/p1' }));
    expect(r.history.go).not.toHaveBeenCalled();
  });

  it('steps back to the list it was opened from', () => {
    notePlace('/needs-you', '?view=all', { view: 'all' }, at(0));
    notePlace('/tasks/p1/12/details', '', {}, at(1));
    const r = router(1);
    const read = vi.fn(() => 'p1');
    leaveTask(r, read);
    expect(r.history.go).toHaveBeenCalledWith(-1);
    // the board is read only when nothing is behind
    expect(read).not.toHaveBeenCalled();
  });

  it('with no board to go to, goes to Tasks, once the board is known', async () => {
    const r = router(0);
    let known!: (p: string | null) => void;
    leaveTask(r, () => new Promise((done) => (known = done)));
    await Promise.resolve();
    expect(r.navigate).not.toHaveBeenCalled();
    known(null);
    await vi.waitFor(() => expect(r.navigate).toHaveBeenCalledWith({ href: '/tasks' }));
  });

  it('a page opened while the board is read stands, even after Back to the task', async () => {
    const r = router(0);
    let known!: (p: string | null) => void;
    leaveTask(r, () => new Promise((done) => (known = done)));
    r.move(); // the person opens Needs you meanwhile
    r.move(); // and steps back to the task
    known('p1');
    await new Promise((done) => setTimeout(done, 0));
    expect(r.navigate).not.toHaveBeenCalled();
  });
});

describe('nextInList', () => {
  const rows = [
    { id: 't7', n: '7' },
    { id: 't5', n: '5' },
  ];

  it('goes on to the row below only for a task opened from its board, not from a thread’s card over the board', () => {
    // each entry in the history as the router keys it
    const entry = (n: number, key: string) => ({ ...at(n), __TSR_key: key });
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    notePlace('/tasks/p1/7', '', {}, entry(1, 'b'));
    // the address names its tab in place: the same task
    notePlace('/tasks/p1/7/details', '', {}, entry(1, 'c'));
    noteList({ pipeline: 'p1', open: '7', rows });
    expect(nextInList('t7')).toEqual({ pipeline: 'p1', task: '5' });
    // the same task opened again from a thread's card in it: from the thread
    notePlace('/tasks/p1/7', '?chat=c1&via=thread', { chat: 'c1', via: 'thread' }, entry(2, 'e'));
    expect(nextInList('t7')).toBeNull();
    // Back to the board, and the task again from a thread's card over it
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    notePlace('/tasks/p1/7', '?chat=c1&via=thread', { chat: 'c1', via: 'thread' }, entry(1, 'd'));
    expect(nextInList('t7')).toBeNull();
    // Back and Forward to the entry opened from the board: from the board again
    notePlace('/tasks/p1/7/details', '', {}, entry(1, 'c'));
    expect(nextInList('t7')).toEqual({ pipeline: 'p1', task: '5' });
  });
});
