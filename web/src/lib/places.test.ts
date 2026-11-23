import { afterEach, describe, expect, it, vi } from 'vitest';
import { forgetPlaces, leaveTask, notePlace } from './places';

/* Closing a task goes back to where it was opened from, never to the task itself. */

const at = (n: number) => ({ __TSR_index: n });
const router = (step: number) => ({ history: { location: { state: at(step) }, go: vi.fn() }, navigate: vi.fn() });
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
});
