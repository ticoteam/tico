import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createMemoryHistory, createRootRouteWithContext, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { TasksPage } from '@/features/tasks/exports';
import { flowCountQuery, pipelineTasksQuery, type TaskItem } from '@/features/tasks/routing';
import { Menu } from '@/shell';
import { Tracker } from './Tracker';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* One dot, one meaning: the menu's count, a board's Dots and Tico's tracker say the same tasks wait on the person,
   the ones Needs you names. Made-up rows: four tasks Maya asked for on one board, two of them in Needs you. */

const ME = 'human:maya';
const turn = (who: string, state: 'person' | 'bot') => ({ state, who, line: 'Ship it?', requirement_id: null, since: null, until: null, ask: false }) as TaskItem['turn'];
const task = (id: string, number: number, title: string, stage: string, more: Partial<TaskItem>): TaskItem => ({
  id,
  number,
  title,
  status: 'open',
  pipeline_id: 'eng',
  stage_id: stage,
  owner_id: 'bot:qa',
  labels: [],
  late: null,
  turn: null,
  updated_at: '2026-11-20T09:00:00Z',
  stage_since: '2026-11-20T09:00:00Z',
  version: 1,
  ...more,
});
const TASKS = [
  // an OK waits on Maya, and the turn is hers
  task('a1', 1, 'Approve the export', 'review', { turn: turn(ME, 'person'), status_line: 'needs_you' }),
  // her turn, nothing asked of her (she holds it): no Dot
  task('a2', 2, 'Write the brief', 'review', { owner_id: ME, turn: turn(ME, 'person'), status_line: 'needs_you' }),
  // the agent works on, while an OK and a ❓ further down its waits are hers: one Needs you row
  task('a3', 3, 'Price the bundle', 'qa', { turn: turn('bot:qa', 'bot'), status_line: 'working' }),
  // nothing for her
  task('a4', 4, 'Tag the photos', 'qa', { turn: turn('bot:qa', 'bot'), status_line: 'up_next' }),
];
const brief = (t: (typeof TASKS)[number]) => ({ id: t.id, number: t.number, title: t.title, pipeline_id: 'eng', stage_id: t.stage_id, owner_id: t.owner_id, late: false, stuck: false });
// GET /needs-you: oldest first, as the server lists it; an access request has no task
const NEEDS = [
  { id: 'v1:r1', kind: 'ask', task_id: 'a1', requirement_id: 'r1', line: 'Ship it?', since: '2026-11-20T09:00:00Z', task: brief(TASKS[0]!) },
  { id: 'v1:r3', kind: 'ask', task_id: 'a3', requirement_id: 'r3', line: 'Price OK?', since: '2026-11-20T09:01:00Z', task: brief(TASKS[2]!) },
  { id: 'q1', kind: 'question', task_id: 'a3', from_id: 'bot:qa', line: 'Which bundle?', since: '2026-11-20T09:02:00Z', task: brief(TASKS[2]!) },
  { id: 'access:x1', kind: 'access', from_id: 'bot:qa', line: 'Read TIDYAPP/web', since: '2026-11-20T09:03:00Z' },
];
// GET /needs-you?count=true: backend/views.py flow_count over that list, one per distinct task plus each request
const FLOW_COUNT = 3;
const ENG = {
  id: 'eng',
  name: 'Engineering',
  stages: [
    { id: 'review', name: 'Review' },
    { id: 'qa', name: 'QA' },
  ],
  owner: null,
  done: null,
  closed: null,
  mayChange: false,
};

function Sidebar() {
  const flow = useQuery(flowCountQuery).data;
  const maya = { id: ME, name: 'Maya', short: 'MA', hue: 0, kind: 'person' as const, to: '/team/maya' };
  return <Menu company={{ mark: 'F', name: 'Fernhill' }} me={maya} org={[]} counts={{ flow }} folded={false} />;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['tags'], []);
  queryClient.setQueryData(['pipelines'], [ENG]);
  queryClient.setQueryData(pipelineTasksQuery('eng', {}).queryKey, { tasks: TASKS, cursor: 'c1' });
  vi.stubGlobal('fetch', async (url: string) => {
    const u = new URL(String(url), 'http://x');
    const body =
      u.pathname === '/api/v2/needs-you'
        ? u.searchParams.get('count')
          ? { actor: ME, count: NEEDS.length, flow_count: FLOW_COUNT }
          : { actor: ME, items: NEEDS, next: null }
        : u.pathname === '/api/v2/tasks'
          ? // what Maya asked for: open, or finished (none)
            { tasks: u.searchParams.get('status') === 'open' ? TASKS.map(({ turn: _t, status_line: _s, ...t }) => t) : [], next_after: null }
          : { key: 'ui.tracker_seen_at', value: null };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

it('the menu’s count, the board’s Dots and the tracker agree on what waits on the person: what Needs you names', async () => {
  const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        <Sidebar />
        <TasksPage pipeline="eng" search={{ view: 'board' }} />
        <Tracker />
      </>
    ),
  });
  const router = createRouter({ routeTree: root, context: { queryClient }, history: createMemoryHistory({ initialEntries: ['/tasks/eng'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );

  // the board: a Dot on each card Needs you names, none on a turn that is only hers
  const dotted = () =>
    TASKS.filter((t) => screen.getByTitle(t.title).closest('.card')?.querySelector('.status-line.is-needs_you')).map((t) => t.id);
  await waitFor(() => expect(dotted()).toEqual(['a1', 'a3']), { timeout: 5_000 });
  const columns = [...document.querySelectorAll('.col-flow b')].map((b) => Number(b.textContent));
  expect(columns.reduce((a, b) => a + b, 0)).toBe(2);

  // the menu: the same tasks, plus the request, which is no task
  const tasks = within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: /^Tasks/ });
  await waitFor(() => expect(tasks.querySelector('.nav-flow')?.textContent).toBe(String(dotted().length + 1)));

  // the tracker: of the tasks she asked for that someone else holds, the same two need her
  const row = await screen.findByRole('button', { name: /Tracking 3/ });
  expect(row.textContent).toContain('2 needs you');
  fireEvent.click(row);
  const needs = (await within(document.querySelector('.tracker') as HTMLElement).findByRole('button', { name: /^Needs you/ })).closest('section') as HTMLElement;
  expect(within(needs).getAllByRole('link').map((a) => a.getAttribute('title'))).toEqual(['Approve the export', 'Price the bundle']);
}, 20_000);
