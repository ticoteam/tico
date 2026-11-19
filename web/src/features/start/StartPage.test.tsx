import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CalendarOccurrence } from '@/api/schema';
import { queryClient, type Actor } from '@/data';
import { FirstRun } from './FirstRun';
import { needsQuery, todayQuery, type Need } from './queries';
import { StartPage } from './StartPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

function show(node: ReactNode, at = '/start') {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const ADA: Actor = { id: 'bot:ada', kind: 'agent', name: 'Ada', slug: 'ada', status: 'Writing the release notes', working: true };
const IDLE: Actor = { id: 'bot:bo', kind: 'agent', name: 'Bo', slug: 'bo' };
const seed = (items: Need[], actors: Actor[] = [], events: Partial<CalendarOccurrence>[] = []) => {
  queryClient.setQueryData(needsQuery.queryKey, { count: items.length, items });
  queryClient.setQueryData(todayQuery().queryKey, events as CalendarOccurrence[]);
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['config'], {});
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => vi.unstubAllGlobals());

describe('Start page', () => {
  it('each row opens its page: a need its task, an agent its page, today the calendar', async () => {
    seed(
      [{ id: 'n1', kind: 'ask', title: 'Approve the launch plan', href: '/task/t1', at: '2026-01-02T10:00:00Z', from: 'bot:ada' }],
      [ADA, IDLE],
      [{ occurrence_id: 'o1', title: 'Weekly review', start: '2026-01-02T15:00:00Z' }],
    );
    show(<StartPage />);
    const main = await screen.findByRole('main');
    expect(within(main).getByRole('heading', { level: 1, name: 'Start' })).toBeTruthy();
    expect(within(main).getByTitle('Approve the launch plan').closest('a')?.getAttribute('href')).toBe('/task/t1');
    expect(within(main).getByTitle('Ada: Writing the release notes').closest('a')?.getAttribute('href')).toBe('/team/ada');
    expect(within(main).queryByTitle('Bo')).toBeNull();
    expect(within(main).getByTitle('Weekly review').closest('a')?.getAttribute('href')).toBe('/calendar');
    expect(within(main).getByRole('button', { name: 'Ask Tico' })).toBeTruthy();
  });

  it('says so when nothing needs the person', async () => {
    seed([]);
    show(<StartPage />);
    expect(await screen.findByRole('heading', { name: 'Nothing needs you' })).toBeTruthy();
  });
});

describe('First run @s1154 @s1171', () => {
  it('opens from the address over the app, with "Have my agents set it up" chosen first', async () => {
    seed([]);
    show(
      <>
        <StartPage />
        <FirstRun />
      </>,
      '/start?setup=path',
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Set up your company' })).toBeTruthy();
    expect(within(dialog).getByRole('radio', { name: /Have my agents set it up/ }).getAttribute('aria-checked')).toBe('true');
    expect(within(dialog).getByRole('radio', { name: /Set it up myself/ }).getAttribute('aria-checked')).toBe('false');
    expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeTruthy();
  });

  it('counts the steps by hand', async () => {
    queryClient.setQueryData(['setup'], { names: { company_name: '' }, answers: {}, selected: {}, completed: null, needed: true });
    show(<FirstRun />, '/start?setup=name');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('img', { name: 'Step 1 of 3' })).toBeTruthy();
    expect(within(dialog).getByRole('textbox', { name: 'Company name' })).toBeTruthy();
  });
});
