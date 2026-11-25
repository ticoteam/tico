import { createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router';
import { describe, expect, it, vi } from 'vitest';
import { hearListClicks, openTasksOver } from './openOver';
import { closeTask, OVER_PAGE, type Steps } from './places';
import { changeWords, withOpen } from './taskAddress';

/** A router with a page, a board and a task's old addresses, opening tasks over the page as the app's does. */
async function at(href: string) {
  const root = createRootRoute();
  const page = (path: string) => createRoute({ getParentRoute: () => root, path });
  const router = createRouter({
    routeTree: root.addChildren([page('/team/$who'), page('/tasks/$pipeline'), page('/task/$task'), page('/tasks/$pipeline/$task'), page('/tasks/$pipeline/$task/$tab')]),
    history: createMemoryHistory({ initialEntries: [href] }),
  });
  openTasksOver(router);
  await router.load();
  return router;
}
const steps = (r: Awaited<ReturnType<typeof at>>) => r as unknown as Steps;

describe('a task opened from a page', () => {
  it("opens over the page it was opened on, the page's own words kept, and one Back closes it", async () => {
    const r = await at('/team/qa?tab=files');
    await r.navigate({ to: '/tasks/$pipeline/$task', params: { pipeline: 'eng', task: '7' } } as never);
    expect(r.state.location.pathname).toBe('/team/qa');
    expect(r.state.location.search).toEqual({ tab: 'files', task: 7 });
    expect((r.state.location.state as unknown as Record<string, unknown>)[OVER_PAGE]).toBe(true);
    // another task while one is open takes its place: still one step over the page
    await r.navigate({ href: '/tasks/eng/8/details?v=2' });
    expect(r.state.location.search).toEqual({ tab: 'files', task: 8, 'task.tab': 'details', 'task.v': 2 });
    closeTask(steps(r));
    await r.load(); // as the app's router does when the history moves
    expect(r.state.location.pathname).toBe('/team/qa');
    expect(r.state.location.search).toEqual({ tab: 'files' });
  });

  it('opened by its address alone (a cold link), closes to the page under it, with nothing to step back to', async () => {
    const r = await at('/tasks/eng?task=7&list=eng&view=list');
    closeTask(steps(r));
    await r.load(); // as the app's router does when the history moves
    expect(r.state.location.search).toEqual({ view: 'list' });
  });

  it('opened by a link to an anchored page, closes to that page at its anchor, never scrolled to it', async () => {
    const r = await at('/team/qa?task=7#notes');
    closeTask(steps(r));
    await r.load();
    expect(r.state.location.href).toBe('/team/qa#notes');
    expect((r.state.location.state as unknown as Record<string, unknown>).__hashScrollIntoViewOptions).toBe(false);
  });

  it('opened from a search hit over an anchored page: the page is not scrolled back to its anchor', async () => {
    const r = await at('/team/qa#notes');
    await r.navigate({ to: '.', search: ((s: Record<string, unknown>) => withOpen(s, { task: '7', list: 'search:frost' })) as never } as never);
    expect(r.state.location.href).toBe('/team/qa?task=7&list=search%3Afrost#notes');
    expect((r.state.location.state as unknown as Record<string, unknown>).__hashScrollIntoViewOptions).toBe(false);
  });

  it('takes its list from the list the press was in, and keeps it moving between tasks', async () => {
    hearListClicks(document);
    const r = await at('/tasks/eng');
    const list = document.createElement('div');
    list.dataset.list = 'eng';
    const row = document.createElement('a');
    let went: Promise<void> | undefined;
    row.addEventListener('click', () => (went = r.navigate({ to: '/tasks/$pipeline/$task', params: { pipeline: 'eng', task: '7' } } as never)));
    list.append(row);
    document.body.append(list);
    row.click();
    await went;
    expect(r.state.location.search).toEqual({ task: 7, list: 'eng' });
    await r.navigate({ to: '/task/$task', params: { task: '9' } } as never);
    expect(r.state.location.search).toEqual({ task: 9, list: 'eng' });
    list.remove();
  });

  it("hands the task's own words to a search update written for its old address, its tab in its path", async () => {
    const r = await at('/team/qa?task=12&task.tab=spec&task.ask=r1');
    await r.navigate({ to: '/tasks/$pipeline/$task/$tab', params: { pipeline: 'eng', task: '12', tab: 'spec' }, search: (prev: Record<string, unknown>) => ({ ...prev, v: 3 }) } as never);
    expect(r.state.location.search).toEqual({ task: 12, 'task.tab': 'spec', 'task.ask': 'r1', 'task.v': 3 });
  });

  it("keeps the step's mark and the page's hash as the open task's words change, so Close still steps back", async () => {
    const r = await at('/team/qa#notes');
    await r.navigate({ to: '/tasks/$pipeline/$task/$tab', params: { pipeline: 'eng', task: '7', tab: 'details' }, search: { file: 'f1' } } as never);
    const opened = r.history.length;
    // a file closed in Details, the list switched: updates in place
    await r.navigate({ to: '.', search: changeWords(({ file: _, ...rest }) => rest) as never, replace: true } as never);
    await r.navigate({ to: '.', search: ((s: Record<string, unknown>) => ({ ...s, list: 'eng' })) as never, replace: true } as never);
    expect(r.state.location.search).toEqual({ task: 7, list: 'eng', 'task.tab': 'details' });
    expect(r.state.location.hash).toBe('notes');
    expect((r.state.location.state as unknown as Record<string, unknown>)[OVER_PAGE]).toBe(true);
    // the page under it is never scrolled to its anchor as the task opens or changes
    expect((r.state.location.state as unknown as Record<string, unknown>).__hashScrollIntoViewOptions).toBe(false);
    expect(r.history.length).toBe(opened);
    closeTask(steps(r));
    await r.load();
    expect(r.state.location.href).toBe('/team/qa#notes');
    r.history.forward();
    await r.load();
    expect(r.state.location.search).toEqual({ task: 7, list: 'eng', 'task.tab': 'details' });
  });

  it("a search hit opens over the page in one step, and another hit picked while it is open takes its place", async () => {
    const r = await at('/team/qa');
    const hit = (task: string) => r.navigate({ to: '.', search: ((s: Record<string, unknown>) => withOpen(s, { task, list: 'search:frost' })) as never } as never);
    await hit('7');
    const opened = r.history.length;
    await hit('8');
    expect(r.history.length).toBe(opened);
    expect(r.state.location.search).toEqual({ task: 8, list: 'search:frost' });
    closeTask(steps(r));
    await r.load();
    expect(r.state.location.href).toBe('/team/qa');
    expect(r.history.length).toBe(opened);
  });

  it('forgets the list a press was in once that click is done, even a click stopped on its way', async () => {
    hearListClicks(document);
    const r = await at('/tasks/eng');
    const list = document.createElement('div');
    list.dataset.list = 'eng';
    const stop = document.createElement('button');
    stop.addEventListener('click', (e) => e.stopPropagation());
    list.append(stop);
    document.body.append(list);
    stop.click();
    await new Promise((done) => setTimeout(done));
    await r.navigate({ to: '/task/$task', params: { task: '9' } } as never);
    expect(r.state.location.search).toEqual({ task: 9 });
    list.remove();
  });

  it('each click it hears forgets the last one’s list as it starts, never a later one’s', async () => {
    hearListClicks(document);
    const set = vi.spyOn(globalThis, 'setTimeout');
    const clear = vi.spyOn(globalThis, 'clearTimeout');
    const list = document.createElement('div');
    list.dataset.list = 'eng';
    document.body.append(list);
    list.click();
    const first = set.mock.results.at(-1)?.value;
    list.click();
    expect(clear).toHaveBeenCalledWith(first);
    set.mockRestore();
    clear.mockRestore();
    list.remove();
  });

  it('leaves every other address alone', async () => {
    const r = await at('/team/qa');
    await r.navigate({ to: '/tasks/$pipeline', params: { pipeline: 'eng' } } as never);
    expect(r.state.location.pathname).toBe('/tasks/eng');
  });
});
