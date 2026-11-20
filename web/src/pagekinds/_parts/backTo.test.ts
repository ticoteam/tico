import { describe, expect, it } from 'vitest';
import { stepBackIs, watchSteps } from './backTo';

/** A router as far as backTo reads it: the current location, and its resolved navigations. */
function fakeRouter(start: string) {
  let fn: ((e: { toLocation: { pathname: string; state: unknown } }) => void) | null = null;
  const router = {
    state: { location: { pathname: start, state: { __TSR_index: 0 } } },
    subscribe: (_event: 'onResolved', f: NonNullable<typeof fn>) => {
      fn = f;
      return () => {};
    },
    go(pathname: string, index: number) {
      router.state.location = { pathname, state: { __TSR_index: index } };
      fn?.({ toLocation: router.state.location });
    },
  };
  return router;
}

describe('a phone Back up the trail', () => {
  it('goes back when the step before is the trail page (a list left with its view), and up otherwise', () => {
    const router = fakeRouter('/tasks/launch');
    watchSteps(router);
    router.go('/tasks/launch/2', 1);
    expect(stepBackIs(router, '/tasks/launch')).toBe(true);
    expect(stepBackIs(router, '/tasks/general')).toBe(false);
    // a replace (the task's first tab) keeps the step's index
    router.go('/tasks/launch/2/details', 1);
    expect(stepBackIs(router, '/tasks/launch')).toBe(true);
  });

  it('goes up when the task was opened first (nothing before it), or from elsewhere', () => {
    const router = fakeRouter('/tasks/launch/2');
    watchSteps(router);
    expect(stepBackIs(router, '/tasks/launch')).toBe(false);
    router.go('/calendar', 1);
    router.go('/tasks/launch/3', 2);
    expect(stepBackIs(router, '/tasks/launch')).toBe(false);
  });
});
