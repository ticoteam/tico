import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { changeBoardSearch, forgetPlaces, NEEDS_YOU, nextInList } from '@/lib';
import { TaskList, type TaskListProps } from './TaskLists';

/* The list beside a task open over a page (the task modal's left column): the list it was opened from, the task in it. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

const ENG = { id: 'eng', name: 'Engineering', stages: [{ id: 'review', name: 'Review' }, { id: 'qa', name: 'QA' }], done: null };
const row = (n: number, title: string, stage: string, more: Record<string, unknown> = {}) => ({ id: `t${n}`, number: n, title, status: 'open', pipeline_id: 'eng', stage_id: stage, owner_id: 'bot:qa', labels: [], late: false, turn: null, updated_at: '2026-11-18T09:00:00Z', created_at: `2026-11-0${n % 9 || 1}T00:00:00Z`, version: 1, ...more });

/** The open task as the modal read it. */
const cur = (r: object) => r as TaskListProps['current'];

let tasks: (params: URLSearchParams) => unknown;
const calls: string[] = [];
beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});
beforeEach(() => {
  calls.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['pipelines'], [ENG]);
  queryClient.setQueryData(['directory'], { actors: [{ id: 'bot:qa', kind: 'agent', name: 'QA Agent' }], byId: { 'bot:qa': { id: 'bot:qa', kind: 'agent', name: 'QA Agent' } } });
  queryClient.setQueryData(['flow'], { count: 0, items: [] });
  tasks = () => ({ tasks: [] });
  vi.stubGlobal('fetch', async (url: string) => {
    calls.push(String(url));
    const u = new URL(String(url), 'http://x');
    const body = u.pathname === '/api/v2/tasks' ? tasks(u.searchParams) : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  forgetPlaces();
});

function show(props: TaskListProps) {
  const root = createRootRoute({ component: () => <TaskList {...props} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [`/tasks?task=${props.open}`] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const titles = () => [...document.querySelectorAll('.li-title')].map((t) => t.textContent);
const head = () => document.querySelector('.li-head') as HTMLElement;

describe("a pipeline's list", () => {
  const rows = [row(7, 'Bulk edit stock', 'review'), row(8, 'Postal codes', 'qa'), row(5, 'Rate fallback', 'review')];
  beforeEach(() => {
    // the pipeline's read, and a stage's read by priority (Tico's order)
    tasks = (p) => (p.get('sort') === 'priority' ? { tasks: rows.filter((t) => t.stage_id === p.get('stage')).reverse() } : { tasks: rows });
  });

  it("shows the open task's stage in Tico's order, the task chosen; another stage from its head", async () => {
    show({ list: 'eng', open: '7', current: cur(row(7, 'Bulk edit stock', 'review')) });
    await waitFor(() => expect(titles()).toEqual(['Rate fallback', 'Bulk edit stock']));
    expect(head().textContent).toBe('EngineeringReview2');
    expect(screen.getByRole('link', { name: /Bulk edit stock/ }).getAttribute('aria-current')).toBe('true');
    expect(calls.some((c) => c.includes('stage=review') && c.includes('sort=priority'))).toBe(true);
    fireEvent.click(within(head()).getByRole('button', { name: /^Engineering/ }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /^QA/ }));
    await waitFor(() => expect(titles()).toEqual(['Postal codes']));
  });

  it('goes on past a stage’s last row to the next stage’s first; a sort changes the order it goes on in', async () => {
    show({ list: 'eng', open: '7', current: cur(row(7, 'Bulk edit stock', 'review')) });
    await waitFor(() => expect(titles()).toEqual(['Rate fallback', 'Bulk edit stock']));
    expect(nextInList('t5', 'eng')).toBe('7');
    expect(nextInList('t7', 'eng')).toBe('8');
    expect(nextInList('t8', 'eng')).toBeNull();
    act(() => changeBoardSearch('eng', { sort: 'oldest' }));
    await waitFor(() => expect(calls.some((c) => c.includes('sort=oldest'))).toBe(true));
    await waitFor(() => expect(titles()).toEqual(['Rate fallback', 'Bulk edit stock']));
    expect(nextInList('t7', 'eng')).toBe('8');
  });
});

describe('Needs you', () => {
  it('lists what waits on the person in its order, the task chosen; an answer goes on down it', async () => {
    const item = (n: number, title: string) => ({ id: `t${n}`, number: n, title, pipeline_id: 'eng', stage_id: 'qa', owner_id: 'bot:qa', late: false, stuck: false, since: '2026-11-18T09:00:00Z' });
    queryClient.setQueryData(['flow'], { count: 2, items: [item(3, 'Approve the note'), item(4, 'Pick the date')] });
    show({ list: undefined, open: '3' });
    await waitFor(() => expect(titles()).toEqual(['Approve the note', 'Pick the date']));
    expect(head().textContent).toBe('Needs you');
    expect(screen.getByRole('link', { name: /Approve the note/ }).getAttribute('aria-current')).toBe('true');
    expect(nextInList('t3', NEEDS_YOU)).toBe('4');
  });
});

describe("a teammate's list", () => {
  it('reads one page of their open tasks, their id exact, and has the task opened from it though it is done', async () => {
    tasks = () => ({ tasks: [row(7, 'Bulk edit stock', 'qa')] });
    show({ list: 'bot:qa', open: '12', current: cur(row(12, 'Shipped the fix', 'qa', { status: 'done' })) });
    await waitFor(() => expect(titles()).toEqual(['Shipped the fix', 'Bulk edit stock']));
    expect(head().textContent).toBe('QA Agent');
    const read = new URL(calls.find((c) => c.includes('owner='))!, 'http://x').searchParams;
    expect(Object.fromEntries(read)).toEqual({ owner: 'bot:qa', status: 'open', brief: 'true', exact: 'true', limit: '200' });
    expect(calls.filter((c) => c.includes('owner='))).toHaveLength(1);
    expect(nextInList('t12', 'bot:qa')).toBe('7');
  });

  it('one the roster does not know is Needs you, never every task', async () => {
    show({ list: 'bot:gone', open: '12' });
    await waitFor(() => expect(head().textContent).toBe('Needs you'));
  });
});

describe('search hits', () => {
  it("are Cmd+K's: the same few, the id a board shows found by its number first, the task picked among them", async () => {
    tasks = (p) => (p.get('search') === '412' ? { tasks: [row(412, 'Rate fallback', 'qa')] } : { tasks: [row(7, 'G412 notes', 'qa')] });
    show({ list: 'search:G412', open: '412', current: cur(row(412, 'Rate fallback', 'qa')) });
    await waitFor(() => expect(titles()).toEqual(['Rate fallback', 'G412 notes']));
    expect(head().textContent).toBe('“G412”');
    expect(calls.filter((c) => c.includes('search=')).map((c) => new URL(c, 'http://x').searchParams.get('limit'))).toEqual(['8', '8']);
    expect(screen.getByRole('link', { name: /Rate fallback/ }).getAttribute('aria-current')).toBe('true');
  });
});
