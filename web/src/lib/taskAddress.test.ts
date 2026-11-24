import { describe, expect, it } from 'vitest';
import { coldOf, coldPage, openOf, pageSearch, withOpen } from './taskAddress';

/* A task opens over the page: its number, its list and its own words ride in the page's address. */

describe('the open task in an address', () => {
  it('opens over the page, keeping the page’s own words apart from the task’s', () => {
    const page = { tab: 'work', v: 3 };
    const open = withOpen(page, { task: '12', list: 'bot:eng', words: { tab: 'details', v: 2, ask: 'a1' } });
    expect(open).toEqual({ tab: 'work', v: 3, task: 12, list: 'bot:eng', 'task.tab': 'details', 'task.v': 2, 'task.ask': 'a1' });
    expect(openOf(open)).toEqual({ task: '12', list: 'bot:eng', words: { tab: 'details', ask: 'a1', v: 2 } });
    expect(pageSearch(open)).toEqual(page);
  });

  it('another task replaces the one open, words and all', () => {
    const one = withOpen({}, { task: '12', words: { tab: 'details', session: 'bot:eng' } });
    expect(withOpen(one, { task: '13' })).toEqual({ task: 13 });
  });

  it('no task: nothing open; junk words are dropped', () => {
    expect(openOf({ tab: 'work' })).toBeNull();
    expect(openOf({ task: 'T-9', 'task.v': 'x', 'task.part': 0, 'task.session': 'nope' })).toEqual({ task: 'T-9', words: { part: 0 } });
  });
});

describe('old task addresses', () => {
  it.each([
    ['/task/12', {}, { task: '12', words: {} }, { to: '/tasks', search: { task: 12 } }],
    ['/task/12', { tab: 'details' }, { task: '12', words: { tab: 'details' } }, { to: '/tasks', search: { task: 12, 'task.tab': 'details' } }],
    ['/flow/12', {}, { task: '12', words: {} }, { to: '/tasks', search: { task: 12 } }],
    ['/tasks/p1/12', { ask: 'a1' }, { task: '12', pipeline: 'p1', words: { ask: 'a1' } }, { to: '/tasks/p1', search: { task: 12, list: 'p1', 'task.ask': 'a1' } }],
    ['/tasks/p1/12/details', { file: 'a.pdf', v: 2 }, { task: '12', pipeline: 'p1', words: { tab: 'details', file: 'a.pdf', v: 2 } }, { to: '/tasks/p1', search: { task: 12, list: 'p1', 'task.tab': 'details', 'task.file': 'a.pdf', 'task.v': 2 } }],
    ['/tasks/all/12/chat', { session: 'bot:eng' }, { task: '12', pipeline: 'all', words: { tab: 'chat', session: 'bot:eng' } }, { to: '/tasks', search: { task: 12, 'task.tab': 'chat', 'task.session': 'bot:eng' } }],
    ['/next/tasks/p1/12/', {}, { task: '12', pipeline: 'p1', words: {} }, { to: '/tasks/p1', search: { task: 12, list: 'p1' } }],
  ])('%s %o opens over its page', (path, search, cold, page) => {
    const c = coldOf(path, search);
    expect(c).toEqual(cold);
    expect(coldPage(c!)).toEqual(page);
  });

  it.each(['/tasks', '/tasks/p1', '/tasks/p1/edit', '/tasks/p1/edit/stages', '/team/bot:eng', '/taskx/1'])('%s is not a task', (path) => {
    expect(coldOf(path)).toBeNull();
  });
});
