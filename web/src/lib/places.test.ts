import { afterEach, describe, expect, it, vi } from 'vitest';
import { forgetPlaces, hearPlaces, leaveTask, NEEDS_YOU, nextInList, noteList, notePlace, openedFrom, pressListRow } from './places';

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

  it('goes on to the row below only for a task a press opened from its board or list, never from the address before it', () => {
    // each entry in the history as the router keys it
    const entry = (n: number, key: string) => ({ ...at(n), __TSR_key: key });
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    // opened over the board with no press of its own (Tico's tracker, a link): not from the board
    notePlace('/tasks/p1/7', '', {}, entry(1, 'x'));
    noteList({ pipeline: 'p1', open: '7', rows });
    expect(nextInList('t7')).toBeNull();
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    // a press on its card that the page took for itself (picking) opens nothing
    pressListRow('p1', '7', { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: true });
    notePlace('/tasks/p1/7', '', {}, entry(1, 'y'));
    expect(nextInList('t7')).toBeNull();
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    pressListRow('p1', '7', { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false });
    notePlace('/tasks/p1/7', '', {}, entry(1, 'b'));
    // the address names its tab in place: the same task
    notePlace('/tasks/p1/7/details', '', {}, entry(1, 'c'));
    noteList({ pipeline: 'p1', open: '7', rows });
    expect(nextInList('t7')).toEqual({ pipeline: 'p1', task: '5' });
    // the same task opened again from a thread's card in it: from the thread
    notePlace('/tasks/p1/7', '?chat=c1&via=thread', { chat: 'c1', via: 'thread' }, entry(2, 'e'));
    expect(nextInList('t7')).toBeNull();
    // Back and Forward to the entry its card opened: from the board again
    notePlace('/tasks/p1', '', {}, entry(0, 'a'));
    notePlace('/tasks/p1/7/details', '', {}, entry(1, 'c'));
    expect(nextInList('t7')).toEqual({ pipeline: 'p1', task: '5' });
    // after a reload nothing is marked: Back to it goes back, as from a link
    forgetPlaces();
    notePlace('/tasks/p1/7/details', '', {}, entry(1, 'c'));
    noteList({ pipeline: 'p1', open: '7', rows });
    expect(nextInList('t7')).toBeNull();
  });

  it('a Needs you row: opened from Needs you, whatever its pipeline; the row below opens in its own pipeline, from Needs you again', () => {
    const entry = (n: number, key: string) => ({ ...at(n), __TSR_key: key });
    const heard = vi.fn();
    const stop = hearPlaces(heard);
    notePlace('/tasks', '', {}, entry(0, 'a'));
    const plain = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
    pressListRow('eng', '7', plain, NEEDS_YOU);
    // while the address is on its way, the press says where it is from; once noted, the address does
    expect(openedFrom('7')).toBe(NEEDS_YOU);
    notePlace('/tasks/eng/7/details', '', {}, entry(1, 'b'));
    expect(heard).toHaveBeenCalled();
    expect(openedFrom('7')).toBe(NEEDS_YOU);
    // Needs you beside it: an Ops row below, then an Engineering one
    noteList({ pipeline: NEEDS_YOU, open: '7', rows: [{ id: 't7', n: '7', pipeline: 'eng' }, { id: 't12', n: '12', pipeline: 'ops' }, { id: 't5', n: '5', pipeline: 'eng' }] });
    expect(nextInList('t7')).toEqual({ pipeline: 'ops', task: '12', list: NEEDS_YOU });
    // the pipeline's own list beside it is not the list it was opened from
    noteList({ pipeline: 'eng', open: '7', rows: [{ id: 't7', n: '7' }, { id: 't5', n: '5' }] });
    expect(nextInList('t7')).toBeNull();
    // a board's card marks its pipeline's list, not Needs you
    notePlace('/tasks/eng', '', {}, entry(2, 'c'));
    pressListRow('eng', '5', plain);
    notePlace('/tasks/eng/5', '', {}, entry(3, 'd'));
    expect(openedFrom('5')).toBe('eng');
    stop();
  });

  it('a task that moved pipelines since its row was pressed keeps where it was opened from', () => {
    const entry = (n: number, key: string) => ({ ...at(n), __TSR_key: key });
    notePlace('/tasks', '', {}, entry(0, 'a'));
    // its row still said Engineering; the task's own address is under Ops now
    pressListRow('eng', '7', { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false }, NEEDS_YOU);
    notePlace('/tasks/ops/7/details', '', {}, entry(1, 'b'));
    expect(openedFrom('7')).toBe(NEEDS_YOU);
    noteList({ pipeline: NEEDS_YOU, open: '7', rows: [{ id: 't7', n: '7', pipeline: 'ops' }, { id: 't5', n: '5', pipeline: 'eng' }] });
    expect(nextInList('t7')).toEqual({ pipeline: 'eng', task: '5', list: NEEDS_YOU });
  });

  it('a pipeline whose id reads "needs-you" is its own list, never Needs you', () => {
    const entry = (n: number, key: string) => ({ ...at(n), __TSR_key: key });
    notePlace('/tasks/needs-you', '', {}, entry(0, 'a'));
    pressListRow('needs-you', '7', { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false });
    notePlace('/tasks/needs-you/7', '', {}, entry(1, 'b'));
    expect(openedFrom('7')).toBe('needs-you');
    expect(openedFrom('7')).not.toBe(NEEDS_YOU);
    // Needs you's own list beside it is not the list it was opened from
    noteList({ pipeline: NEEDS_YOU, open: '7', rows: [{ id: 't7', n: '7', pipeline: 'needs-you' }, { id: 't5', n: '5', pipeline: 'eng' }] });
    expect(nextInList('t7')).toBeNull();
  });
});
