import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { listParams, pipelineIdFor, pipelinesQuery, type Pipeline } from './queries';
import { PipelinePage } from './PipelinePage';
import { TasksPage } from './TasksPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let posts: { url: string; body: Record<string, unknown> }[] = [];
function serve(reply: (url: string, method: string) => Reply) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST') posts.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : {} });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const PIPE: Pipeline = {
  id: 'ops',
  name: 'Orders',
  stages: [
    { id: 'o1', name: 'Inbox' },
    { id: 'o2', name: 'Packing' },
  ],
  owner: null,
  done: null,
  closed: null,
  mayChange: true,
};
const REC = {
  id: 'ops',
  name: 'Orders',
  goal: 'Every order out the door',
  prefix: 'O',
  manager_id: null,
  factory_id: null,
  settings_version: 5,
  done: null,
  closed: null,
  stages: [
    { id: 'o1', name: 'Inbox', side: false, purpose: '', owner: null, task_limit: null, requirements: [] },
    { id: 'o2', name: 'Packing', side: false, purpose: '', owner: null, task_limit: null, requirements: [{ kind: 'owner_done' }] },
  ],
};
const ACTORS = [
  { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
  { id: 'bot:packer', kind: 'agent', name: 'Packer' },
];

const pick = async (combo: HTMLElement, name: string) => {
  fireEvent.click(combo);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  posts = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  queryClient.setQueryData(['tags'], []);
  queryClient.setQueryData(['flow'], { count: 0, items: [] });
  queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
  queryClient.setQueryData(keys.tasks.list(listParams('ops', {})), { tasks: [], cursor: null });
  queryClient.setQueryData(['pipelines', 'factories'], [{ id: 'f1', name: 'Fulfillment' }]);
});
afterEach(() => vi.unstubAllGlobals());

describe('Pipeline editor @s138', () => {
  it('saves the pipeline’s Goal, Manager and Prefix and a stage’s Goal, Owner and Limit @s138', async () => {
    serve((url, method) => {
      if (url === '/api/v2/pipelines/ops' && method === 'GET') return { status: 200, body: { pipeline: REC } };
      if (url === '/api/v2/pipelines/ops') return { status: 200, body: { pipeline: { ...REC, settings_version: 6 } } };
      return { status: 200, body: {} };
    });
    show(<PipelinePage pipeline="ops" fresh={false} />, '/tasks/ops/edit');
    const goal = await screen.findByRole('textbox', { name: 'Goal' });
    expect((goal as HTMLInputElement).value).toBe('Every order out the door');
    fireEvent.change(goal, { target: { value: 'Every order out the same day' } });
    await pick(screen.getByRole('combobox', { name: /Manager/ }), 'Packer');
    fireEvent.change(screen.getByRole('textbox', { name: 'Prefix' }), { target: { value: 'or' } });
    fireEvent.click(screen.getByRole('button', { name: 'Rules, Packing' }));
    const stage = screen.getByRole('button', { name: 'Rules, Packing' }).closest('.ps') as HTMLElement;
    fireEvent.change(within(stage).getByRole('textbox', { name: 'Goal' }), { target: { value: 'Packed and labeled' } });
    await pick(within(stage).getByRole('combobox', { name: /Owner/ }), 'Ana Ruiz');
    fireEvent.change(within(stage).getByRole('spinbutton', { name: 'Limit' }), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body).toMatchObject({
      settings_version: 5,
      goal: 'Every order out the same day',
      manager_id: 'bot:packer',
      prefix: 'OR',
      stages: [
        { id: 'o1', name: 'Inbox', task_limit: null },
        { id: 'o2', name: 'Packing', purpose: 'Packed and labeled', owner: 'human:ana', task_limit: 4, requirements: [{ kind: 'owner_done' }] },
      ],
    });
  });

  it('when someone saved first, says what they changed and keeps the edits: Save mine goes at their version @s138', async () => {
    const theirs = { ...REC, settings_version: 6, goal: 'Same-day orders', stages: [REC.stages[0], { ...REC.stages[1], name: 'Pack' }] };
    let read = 0;
    serve((url, method) => {
      if (url === '/api/v2/pipelines/ops' && method === 'GET') return { status: 200, body: { pipeline: read++ === 0 ? REC : theirs } };
      if (url === '/api/v2/pipelines/ops' && posts.length === 1) return { status: 409, body: { error: { code: 'task_changed', detail: 'The pipeline changed since you looked.' } } };
      if (url === '/api/v2/pipelines/ops') return { status: 200, body: { pipeline: { ...theirs, settings_version: 7 } } };
      return { status: 200, body: {} };
    });
    show(<PipelinePage pipeline="ops" fresh={false} />, '/tasks/ops/edit');
    const prefix = await screen.findByRole('textbox', { name: 'Prefix' });
    fireEvent.change(prefix, { target: { value: 'D' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Someone saved this pipeline');
    expect(alert.textContent).toContain('Goal, Pack');
    // The edits stay on screen, not overwritten by theirs.
    expect((screen.getByRole('textbox', { name: 'Prefix' }) as HTMLInputElement).value).toBe('D');
    fireEvent.click(screen.getByRole('button', { name: 'Save mine' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[0]!.body).toMatchObject({ settings_version: 5, prefix: 'D' });
    expect(posts[1]!.body).toMatchObject({ settings_version: 6, prefix: 'D' });
  });

  it('Load theirs puts their record in place of the edits @s138', async () => {
    const theirs = { ...REC, settings_version: 6, goal: 'Same-day orders' };
    let read = 0;
    serve((url, method) => {
      if (url === '/api/v2/pipelines/ops' && method === 'GET') return { status: 200, body: { pipeline: read++ === 0 ? REC : theirs } };
      if (url === '/api/v2/pipelines/ops') return { status: 409, body: { error: { code: 'task_changed', detail: 'The pipeline changed since you looked.' } } };
      return { status: 200, body: {} };
    });
    show(<PipelinePage pipeline="ops" fresh={false} />, '/tasks/ops/edit');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Prefix' }), { target: { value: 'D' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load theirs' }));
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Goal' }) as HTMLInputElement).value).toBe('Same-day orders'));
    expect((screen.getByRole('textbox', { name: 'Prefix' }) as HTMLInputElement).value).toBe('O');
    expect(posts).toHaveLength(1);
  });

  it('New pipeline sits in the page’s ⋯ and opens the editor’s page @s138', async () => {
    serve(() => ({ status: 200, body: {} }));
    const router = show(<TasksPage pipeline="ops" search={{}} />, '/tasks/ops');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New pipeline' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/ops/edit'));
    expect(router.state.location.search).toMatchObject({ new: true });
  });

  it('the New pipeline page sends the name, facts and stages @s138', async () => {
    serve((url, method) => {
      if (url === '/api/v2/pipelines' && method === 'GET') return { status: 200, body: { pipelines: [REC] } };
      if (url === '/api/v2/pipelines/returns' && method === 'POST')
        return { status: 200, body: { pipeline: { ...REC, id: 'returns', name: 'Returns', settings_version: 1, stages: [{ ...REC.stages[0], id: 'r1', name: 'Received' }] } } };
      return { status: 200, body: {} };
    });
    const router = show(<PipelinePage pipeline="ops" fresh />, '/tasks/ops/edit?new=1');
    const save = await screen.findByRole('button', { name: 'Save' });
    expect(save.hasAttribute('disabled') || save.getAttribute('aria-disabled') === 'true').toBe(true);
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Returns' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Goal' }), { target: { value: 'Every return refunded' } });
    await pick(screen.getByRole('combobox', { name: /Factory/ }), 'Fulfillment');
    fireEvent.click(screen.getByRole('button', { name: 'Add stage' }));
    const stages = document.querySelectorAll('.pe-stages .ps:not(.is-add)');
    fireEvent.change(within(stages[0] as HTMLElement).getByRole('textbox', { name: 'Name' }), { target: { value: 'Received' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      url: '/api/v2/pipelines/returns',
      body: { name: 'Returns', goal: 'Every return refunded', factory_id: 'f1', stages: [{ name: 'Received', side: false, requirements: [] }] },
    });
    expect(posts[0]!.body).not.toHaveProperty('settings_version');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/returns'));
  });

  it('a new id never lands on a pipeline in use or a reserved path @s138', () => {
    expect(pipelineIdFor('Returns & Refunds', new Set())).toBe('returns-refunds');
    expect(pipelineIdFor('Returns', new Set(['returns', 'returns-2']))).toBe('returns-3');
    expect(pipelineIdFor('New', new Set())).toBe('new-2');
  });

  it('removing a stage that holds tasks is closed to Tico; the rest of a save is open @s138', async () => {
    serve((url, method) => {
      if (url === '/api/v2/pipelines/ops' && method === 'GET') return { status: 200, body: { pipeline: REC } };
      if (url === '/api/v2/pipelines/ops') return { status: 200, body: { pipeline: { ...REC, settings_version: 6 } } };
      return { status: 200, body: {} };
    });
    show(<TasksPage pipeline="ops" search={{}} />, '/tasks/ops');
    await screen.findByRole('heading', { level: 1, name: 'Tasks' });
    const stages = [{ id: 'o1', name: 'Inbox', side: false }];
    expect(await run('tasks.savePipeline', { pipeline: 'ops', stages, move_to: { pipeline: 'ops', stage: 'o1' } }, { via: 'tico' })).toMatchObject({
      ok: false,
      error: 'not_allowed',
    });
    expect(posts).toHaveLength(0);
    expect(await run('tasks.savePipeline', { pipeline: 'ops', goal: 'Faster', stages: REC.stages.map(({ id, name, side }) => ({ id, name, side })) }, { via: 'tico' })).toMatchObject({
      ok: true,
    });
    expect(posts[0]!.body).toMatchObject({ goal: 'Faster', settings_version: 5 });
  });

  it('Edit pipeline saves the stages whole; a removed stage that holds tasks asks where they go @s138', async () => {
    let saves = 0;
    serve((url, method) => {
      if (url === '/api/v2/pipelines/ops' && method === 'GET') return { status: 200, body: { pipeline: REC } };
      if (url === '/api/v2/pipelines') return { status: 200, body: { pipelines: [REC] } };
      if (url === '/api/v2/pipelines/ops' && method === 'POST') {
        saves += 1;
        if (saves === 1) return { status: 409, body: { error: { code: 'stage_has_tasks', detail: 'Packing holds tasks' } } };
        return { status: 200, body: { pipeline: { ...REC, settings_version: 6, stages: [REC.stages[0]] } } };
      }
      return { status: 200, body: {} };
    });
    show(<PipelinePage pipeline="ops" fresh={false} />, '/tasks/ops/edit');
    fireEvent.click(await screen.findByRole('button', { name: 'More, Packing' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove stage' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(posts[0]?.body).toMatchObject({ settings_version: 5, stages: [{ id: 'o1', name: 'Inbox', side: false }] });
    fireEvent.click(screen.getByRole('button', { name: 'Move tasks' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toMatchObject({ move_tasks_to: { pipeline: 'ops', stage: 'o1' } });
  });

  it('Edit pipeline opens a stage to show its rules @s115 @s142', async () => {
    const rec = { ...REC, stages: [REC.stages[0], { ...REC.stages[1], by_hand: false, requirements: [{ kind: 'checks_pass' }] }] };
    const rule = { id: 'sr1', name: '', pipeline_id: 'ops', pipeline_name: 'Orders', stages: ['o2'], stage_names: ['Packing'], scope: 'stage', event: 'enters' };
    const full = { ...rule, timer: null, cond: {}, jev: '', then: 'block', status: 'active', revision: 1, can_edit: true };
    serve((url) => {
      if (url === '/api/v2/pipelines/ops') return { status: 200, body: { pipeline: rec } };
      if (url.startsWith('/api/v2/stage-rules')) return { status: 200, body: { items: [full], built_in: [], may_add: true } };
      return { status: 200, body: {} };
    });
    show(<PipelinePage pipeline="ops" fresh={false} />, '/tasks/ops/edit');
    const open = await screen.findByRole('button', { name: 'Rules, Packing' });
    expect(open.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(open);
    expect(open.getAttribute('aria-expanded')).toBe('true');
    const enter = await screen.findByRole('region', { name: 'On entering' });
    expect(await within(enter).findByTitle('Enters Packing, Block')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'To leave' })).getByTitle('Checks pass')).toBeTruthy();
    fireEvent.click(within(enter).getByRole('button', { name: 'Add rule' }));
    expect(within(enter).getByRole('textbox', { name: 'Name' })).toBeTruthy();
  });
});
