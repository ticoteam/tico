import { afterEach, describe, expect, it, vi } from 'vitest';
import { forgetPlaces, leaveTask, notePlace } from './places';

/* Closing a task goes back to where it was opened from, never to the task itself. */

const at = (n: number) => ({ __TSR_index: n });
const router = (step: number) => ({ history: { location: { state: at(step) }, go: vi.fn() }, navigate: vi.fn() });

afterEach(() => forgetPlaces());

describe('leaveTask', () => {
  it('a cold-loaded old Flow link is the task, not where it was opened from: its board, never the task again', () => {
    notePlace('/flow/12', '?tab=details', { tab: 'details' }, at(0));
    notePlace('/tasks/p1/12/details', '', {}, at(0));
    const r = router(0);
    leaveTask(r, 'p1');
    expect(r.navigate).toHaveBeenCalledWith({ href: '/tasks/p1' });
    expect(r.history.go).not.toHaveBeenCalled();
  });

  it('steps back to the list it was opened from', () => {
    notePlace('/needs-you', '?view=all', { view: 'all' }, at(0));
    notePlace('/tasks/p1/12/details', '', {}, at(1));
    const r = router(1);
    leaveTask(r, 'p1');
    expect(r.history.go).toHaveBeenCalledWith(-1);
  });

  it('with no board to go to, goes to Tasks', () => {
    const r = router(0);
    leaveTask(r, null);
    expect(r.navigate).toHaveBeenCalledWith({ href: '/tasks' });
  });
});
