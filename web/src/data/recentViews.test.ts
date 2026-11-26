import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  RECENT_VIEWS,
  forgetRecentViews,
  forgetTask,
  forgetView,
  faultedPage,
  nameOpenTask,
  noteView,
  pageFaulted,
  pageKey,
  readRecentViews,
  setRecentHere,
  useOpenTask,
  useRecentViews,
  viewKey,
} from './recentViews';

const KEY = 'tico-next:recent-views.v1';
const ME = 'Tico Team\u0000human:chris';
const view = (key: string, ...trail: string[]) => ({ key, to: key.startsWith('task:') ? `/tasks/product?task=${key.slice(5)}` : key.slice(5), trail });
const keys = () => readRecentViews(ME).map((r) => r.key);

beforeEach(() => {
  localStorage.clear();
  setRecentHere(ME, '');
});
afterEach(() => forgetRecentViews());

describe('recent views', () => {
  it('lists the views before the open one, newest first, and a view opened again moves to the top once', () => {
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    noteView(view('task:12', 'Tasks', 'Product', 'Fix the login bug'));
    noteView(view('page:/library/docs', 'Library', 'Docs'));
    setRecentHere(ME, 'page:/library/docs');
    const { result } = renderHook(() => useRecentViews());
    expect(result.current.map((r) => r.trail.join(' › '))).toEqual(['Tasks › Product › Fix the login bug', 'Tasks › Product']);

    act(() => {
      noteView(view('page:/tasks/product', 'Tasks', 'Product'));
      noteView(view('page:/company/strategy', 'Company', 'Strategy'));
      setRecentHere(ME, 'page:/company/strategy');
    });
    expect(result.current.map((r) => r.key)).toEqual(['page:/tasks/product', 'page:/library/docs', 'task:12']);
  });

  it('keeps the last view of each as it was last seen: its address and its trail', () => {
    noteView({ key: 'page:/tasks/product', to: '/tasks/product?who=me', trail: ['Tasks', 'Product'] });
    noteView({ key: 'page:/tasks/product', to: '/tasks/product?who=me&stage=build', trail: ['Tasks', 'Product'] });
    expect(readRecentViews(ME)).toEqual([{ key: 'page:/tasks/product', to: '/tasks/product?who=me&stage=build', trail: ['Tasks', 'Product'] }]);
  });

  it(`shows exactly the ${RECENT_VIEWS} most recent before the open view after 20`, () => {
    for (let i = 1; i <= 20; i++) noteView(view(`page:/library/docs/d${i}`, 'Library', 'Docs', `Doc ${i}`));
    setRecentHere(ME, 'page:/library/docs/d20');
    const { result } = renderHook(() => useRecentViews());
    expect(result.current.map((r) => r.trail.at(-1))).toEqual(Array.from({ length: 12 }, (_, i) => `Doc ${19 - i}`));
  });

  it('is kept across a reload, and a tab sees what another tab opened', () => {
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    const stored = localStorage.getItem(KEY);
    expect(JSON.parse(stored ?? 'null')).toMatchObject({ v: 1, rows: [{ key: 'page:/tasks/product' }] });
    // another tab writes it; this one hears the storage event
    const { result } = renderHook(() => useRecentViews());
    act(() => {
      localStorage.setItem(KEY, JSON.stringify({ v: 1, who: ME, rows: [view('page:/library/docs/a', 'Library', 'Docs', 'A'), view('page:/tasks/product', 'Tasks', 'Product')] }));
      window.dispatchEvent(new StorageEvent('storage', { key: KEY }));
    });
    expect(result.current.map((r) => r.key)).toEqual(['page:/library/docs/a', 'page:/tasks/product']);
  });

  it('shows another person, or another company, none of its rows; sign-out clears it', () => {
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    expect(readRecentViews('Tico Team\u0000human:sam')).toEqual([]);
    expect(readRecentViews('Other Co\u0000human:chris')).toEqual([]);
    setRecentHere('Tico Team\u0000human:sam', '');
    const { result } = renderHook(() => useRecentViews());
    expect(result.current).toEqual([]);
    forgetRecentViews();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('reads bad or newer data as empty, and never writes over newer data', () => {
    for (const bad of ['not json', '{"v":1,"who":"x"}', JSON.stringify({ v: 1, who: ME, rows: [{ key: 1 }] })]) {
      localStorage.setItem(KEY, bad);
      expect(readRecentViews(ME)).toEqual([]);
    }
    const newer = JSON.stringify({ v: 2, who: ME, rows: [view('page:/tasks/product', 'Tasks', 'Product')] });
    localStorage.setItem(KEY, newer);
    expect(readRecentViews(ME)).toEqual([]);
    noteView(view('page:/library/docs', 'Library', 'Docs'));
    expect(localStorage.getItem(KEY)).toBe(newer);
  });

  it('drops a view once it is gone, and does not list it again in this load', () => {
    noteView(view('page:/library/docs/gone', 'Library', 'Docs', 'Gone'));
    noteView(view('task:12', 'Tasks', 'Product', 'Fix the login bug'));
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    forgetView('page:/library/docs/gone');
    forgetTask('12');
    expect(keys()).toEqual(['page:/tasks/product']);
    noteView(view('page:/library/docs/gone', 'Library', 'Docs', 'Gone'));
    expect(keys()).toEqual(['page:/tasks/product']);
  });

  it('drops a task gone by the address its row was saved under, though its row is keyed by its number', () => {
    noteView({ key: 'task:12', to: '/tasks/product?task=t-abc&tab=files', trail: ['Tasks', 'Product', 'Fix the login bug'] });
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    forgetTask('t-abc');
    expect(keys()).toEqual(['page:/tasks/product']);
  });

  it('drops a view found gone before it knew whose list it was, once it knows', () => {
    noteView(view('page:/library/docs/gone', 'Library', 'Docs', 'Gone'));
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    setRecentHere('', '');
    noteView({ key: 'task:12', to: '/tasks/product?task=t-abc', trail: ['Tasks', 'Product', 'Fix the login bug'] }, ME);
    forgetView('page:/library/docs/gone');
    forgetTask('t-abc');
    setRecentHere(ME, '');
    expect(keys()).toEqual(['page:/tasks/product']);
  });

  it('counts no address that only passes on, and no view without a trail', () => {
    noteView(view('page:/', 'Tico Team'));
    noteView(view('page:/library', 'Library'));
    noteView({ key: 'page:/updates', to: '/updates', trail: [] });
    expect(keys()).toEqual([]);
  });

  it('is one view per page, whatever its filters, and one per task, whatever page it is open over', () => {
    expect(viewKey('/tasks/product', { who: 'me' })).toBe(viewKey('/next/tasks/product/', { stage: 'build' }));
    expect(viewKey('/tasks/product', { task: 12, tab: 'files' })).toBe(viewKey('/needs-you', { task: '12' }));
    expect(viewKey('/tasks/product', { task: 12 })).toBe('task:12');
    expect(pageKey('/next/library/docs/abc/')).toBe('page:/library/docs/abc');
  });

  it("carries the open task's name and number from the task to the trail, whichever way its address names it", () => {
    const { result } = renderHook(() => useOpenTask('t-abc'));
    expect(result.current).toEqual({ name: '', as: '' });
    act(() => nameOpenTask('t-abc', 'Fix the login bug', '12'));
    expect(result.current).toEqual(expect.objectContaining({ name: 'Fix the login bug', as: '12' }));
    act(() => nameOpenTask('13', 'Another'));
    expect(result.current).toEqual({ name: '', as: '' });
  });

  it('takes a page that shows its fault out of the list, without barring it once it draws again', () => {
    noteView(view('page:/company/risk', 'Company', 'Risk'));
    noteView(view('page:/tasks/product', 'Tasks', 'Product'));
    pageFaulted('page:/company/risk', true);
    expect(keys()).toEqual(['page:/tasks/product']);
    expect(faultedPage()).toBe('page:/company/risk');
    pageFaulted('page:/company/risk', false);
    expect(faultedPage()).toBe('');
    noteView(view('page:/company/risk', 'Company', 'Risk'));
    expect(keys()).toEqual(['page:/company/risk', 'page:/tasks/product']);
  });
});
