import { QueryClient } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router';
import { afterEach, describe, expect, it } from 'vitest';
import type { Ctx } from '@/actions';
import { boardSearch, forgetPlaces, NEEDS_YOU, noteList, notePlace } from '@/lib';
import { filterTasks, sortTasks } from './actions';

/* The list tools (Search, Filter, Sort) of the list beside a task open over a board act on that list, never on the
   board under it: the board's address, and so its own filters, stay as they were. */

async function at(href: string) {
  const root = createRootRoute();
  const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline' })]), history: createMemoryHistory({ initialEntries: [href] }) });
  await router.load();
  const note = () => notePlace(router.state.location.pathname, '', router.state.location.search as Record<string, unknown>);
  note();
  return { router, note, ctx: { router, queryClient: new QueryClient() } as unknown as Ctx };
}

afterEach(() => forgetPlaces());

describe('the list beside a task open over a board', () => {
  it('sorts, searches and filters that list, the board under it unchanged, and keeps them as tasks open and close', async () => {
    const { router, note, ctx } = await at('/tasks/eng?view=list&owner=bot:a&task=7&list=eng');
    noteList({ pipeline: 'eng', open: '7', rows: [] });
    expect(sortTasks.can!(ctx, { sort: 'newest' })).toBe(true);
    expect(filterTasks.can!(ctx, {})).toBe(true);
    sortTasks.run(ctx, { sort: 'newest' });
    await filterTasks.run(ctx, { q: 'frost', late: true });
    expect(boardSearch('eng')).toEqual({ view: 'list', owner: 'bot:a', sort: 'newest', q: 'frost', late: true });
    expect(router.state.location.search).toEqual({ view: 'list', owner: 'bot:a', task: 7, list: 'eng' });
    // another task, then closed: the list keeps what it was given, the board its own words
    await router.navigate({ to: '.', search: { view: 'list', owner: 'bot:a', task: 8, list: 'eng' } as never });
    note();
    expect(boardSearch('eng')).toMatchObject({ sort: 'newest', q: 'frost' });
    await router.navigate({ to: '.', search: { view: 'list', owner: 'bot:a' } as never });
    note();
    expect(router.state.location.search).toEqual({ view: 'list', owner: 'bot:a' });
    expect(boardSearch('eng')).toMatchObject({ sort: 'newest', q: 'frost' });
    // the board changes its own words: the list over it takes them next time
    await router.navigate({ to: '.', search: { view: 'board' } as never });
    note();
    expect(boardSearch('eng')).toEqual({ view: 'board' });
  });

  it('with Needs you, a teammate or search hits beside the task, the tools have no list to act on, and never touch the board', async () => {
    const { router, ctx } = await at('/tasks/eng?view=list&task=7');
    for (const list of [NEEDS_YOU, 'bot:a', 'search:frost']) {
      noteList({ pipeline: list, open: '7', rows: [] });
      expect(sortTasks.can!(ctx, { sort: 'newest' })).toBe(false);
      expect(filterTasks.can!(ctx, {})).toBe(false);
      await filterTasks.run(ctx, { q: 'frost' });
    }
    expect(router.state.location.search).toEqual({ view: 'list', task: 7 });
    expect(boardSearch('eng')).toEqual({ view: 'list' });
  });

  it('with no task open, filter the board on screen', async () => {
    const { router, ctx } = await at('/tasks/eng?view=list');
    expect(filterTasks.can!(ctx, {})).toBe(true);
    await filterTasks.run(ctx, { q: 'frost' });
    expect(router.state.location.search).toEqual({ view: 'list', q: 'frost' });
  });
});
