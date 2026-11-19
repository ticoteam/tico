import { describe, expect, it } from 'vitest';
import { loadAtStart, viewsToUnload } from './memory';

const view = (id: string, shownAt: number, more: { loaded?: boolean; inCall?: boolean } = {}) => ({
  id,
  shownAt,
  loaded: more.loaded ?? true,
  inCall: more.inCall ?? false,
});

describe('memory with many companies', () => {
  it('keeps every page loaded up to the limit', () => {
    expect(viewsToUnload([view('a', 1), view('b', 2), view('c', 3)], 'a', 3)).toEqual([]);
    expect(viewsToUnload([view('a', 1), view('b', 2), view('c', 3), view('d', 4, { loaded: false })], 'a', 3)).toEqual([]);
  });

  it('unloads the one shown longest ago, never the one on screen or one in a call', () => {
    const views = [view('a', 1), view('b', 2, { inCall: true }), view('c', 3), view('d', 4)];
    expect(viewsToUnload(views, 'a', 3)).toEqual(['c']);
    expect(viewsToUnload(views, 'a', 2)).toEqual(['c', 'd']);
    // nothing left that may go: more stay loaded than the limit rather than drop a call
    expect(viewsToUnload([view('a', 1), view('b', 2, { inCall: true })], 'a', 1)).toEqual([]);
  });

  it('loads the active company first at start, then the others in order', () => {
    expect([...loadAtStart(['a', 'b', 'c', 'd'], 'c', 2)]).toEqual(['c', 'a']);
    expect([...loadAtStart(['a', 'b'], null, 6)]).toEqual(['a', 'b']);
    expect([...loadAtStart(['a', 'b'], 'gone', 1)]).toEqual(['a']);
  });
});
