import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { keys, queryClient } from '@/data';
import { flowQuery } from '@/features/tasks/routing';
import { useFollow } from '@/live/follow';
import { pageVersionQuery, taskFilesQuery, type TaskData } from './queries';
import { loadTask } from './routing';
import { TaskPage } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
function holdReply() {
  let resolve!: (reply: Reply) => void;
  const promise = new Promise<Reply>((done) => { resolve = done; });
  return { promise, resolve };
}

const calls: string[] = [];
const sent: { method: string; url: string; body: Record<string, unknown> }[] = [];
/** Anything `reply` leaves (undefined) gets `base`'s answer. */
function serve(reply: (url: string, method: string, body: Record<string, unknown>) => Reply | Promise<Reply> | undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push(`${method} ${String(url)}`);
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    if (method !== 'GET') sent.push({ method, url: String(url), body });
    const r = (await reply(String(url), method, body)) ?? base(String(url));
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at = '/tasks/p1/12') {
  // The shell's ActionsRoot lends the router to the action layer: a control's press runs through it.
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
    </QueryClientProvider>,
  );
  return router;
}

/* Made-up rows only. */
const task = (more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    title: 'Fix the sign-in page',
    status: 'open',
    lane: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Product' },
    stage_id: 's2',
    stage: { id: 's2', name: 'Review' },
    owner_id: 'bot:a1',
    requester_id: 'human:me',
    labels: [],
    created_at: '2026-11-01T10:00:00Z',
    due_at: null,
    late: null,
    documents: [
      { file_id: 'f1', name: 'Spec', version: 2, approved_version: 2 },
      { file_id: 'f2', name: 'QA report', version: 1 },
    ],
    to_leave: [
      { requirement_id: 'r1', line: 'QA report approved', met: false, may: ['ok', 'no'], approval: { document: 'QA report', file_id: 'f2' } },
      { requirement_id: 'r0', line: 'Spec approved', met: true, may: [] },
    ],
    links: [],
    runs: [],
    children: [],
    parent: null,
    can_comment: true,
    mover: true,
    ...more,
  }) as unknown as TaskData;

const detail = (t: TaskData) => ({ task: t, runs: [], children: [], comments: [], events: [], messages: [], has_more: false, can_comment: true, mover: true });
/** The task read answers with `t`. */
const reads = (t: TaskData) => (url: string): Reply | undefined =>
  url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(t) } : undefined;

const ok: Reply = { status: 200, body: {} };
function base(url: string): Reply {
  if (url.includes('/meetings')) return { status: 200, body: { meetings: [], next_cursor: null } };
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { status: 200, body: detail(task()) };
  if (url.includes('/files')) return { status: 200, body: { files: [] } };
  if (url.includes('/api/v2/pages/')) return { status: 200, body: { page: { src: 'about:blank' } } };
  if (url.includes('/api/v2/pipelines'))
    return {
      status: 200,
      body: {
        pipelines: [
          { id: 'p1', name: 'Product', stages: [{ id: 's1', name: 'Build', side: false }, { id: 's2', name: 'Review', side: false }, { id: 's3', name: 'Ship', side: false }] },
          { id: 'p2', name: 'Ops', stages: [{ id: 'o1', name: 'Triage', side: false }] },
        ],
      },
    };
  if (url.includes('/needs-you')) return { status: 200, body: { items: [] } };
  // A stage's own OK has no grant: Approve answers with the page version.
  if (url.includes('/api/v2/oks/')) return { status: 404, body: { error: { code: 'not_found', detail: 'No such OK' } } };
  return ok;
}

const people = (actors: { id: string; kind: string; name: string }[]) =>
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
const details = () => show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
const more = async () => fireEvent.click((await screen.findAllByRole('button', { name: 'More' }))[0] as HTMLElement);
const lastMore = async () => fireEvent.click((await screen.findAllByRole('button', { name: 'More' })).at(-1) as HTMLElement);
const addDialog = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'File or page' }));
  return screen.findByRole('dialog', { name: 'New page' });
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  calls.length = 0;
  sent.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  people([]);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(useFollow).mockClear();
});

describe('task route loading', () => {
  it('reuses a task read from an older id link when redirecting to its numbered address', async () => {
    serve(base);
    expect(await loadTask(queryClient, 't1')).toMatchObject({ task: { id: 't1', number: 12 } });
    expect(await loadTask(queryClient, '12', 'qa-report')).toMatchObject({ task: { id: 't1' } });
    expect(calls.filter((call) => /^GET \/api\/v2\/tasks\/(t1|%2312)$/.test(call))).toEqual(['GET /api/v2/tasks/t1']);
  });

  it('warms an open task’s chat before the file manifest comes back', async () => {
    const manifest = holdReply();
    serve((url) => {
      if (url.endsWith('/tasks/%2312')) return { status: 200, body: detail(task({ conversation_id: 'room', documents: [] })) };
      if (url.endsWith('/tasks/t1/files?all=1')) return manifest.promise;
      return base(url);
    });
    const loaded = loadTask(queryClient, '12', 'details', true);
    try {
      await waitFor(() => expect(calls).toContain('GET /api/v2/threads/room/timeline?flat=1&limit=50'));
      expect(calls).toContain('GET /api/v2/threads/room');
      expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.fetchStatus).toBe('fetching');
    } finally {
      manifest.resolve({ status: 200, body: { files: [] } });
      await loaded;
    }
  });

  it.each([['spec', 'f1'], [undefined, 'f2']] as const)('starts the selected page read while files are pending for tab %s', async (tab, file) => {
    const manifest = holdReply();
    serve((url) => (url.endsWith('/tasks/t1/files?all=1') ? manifest.promise : base(url)));
    const loaded = loadTask(queryClient, '12', tab);
    const finished = vi.fn();
    void loaded.then(finished);
    try {
      await waitFor(() => expect(finished).toHaveBeenCalledWith({ task: expect.objectContaining({ id: 't1' }), tab: tab ?? 'qa-report' }));
      expect(calls).toContain('GET /api/v2/tasks/t1/files?all=1');
      expect(calls).toContain(`GET /api/v2/pages/${file}`);
      expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.fetchStatus).toBe('fetching');
    } finally {
      manifest.resolve({ status: 200, body: { files: [] } });
      await loaded;
      await queryClient.prefetchQuery(taskFilesQuery('t1'));
    }
  });

  it.each(['attachment-pdf', 'details', undefined])('waits for the files when the route needs them for tab %s', async (tab) => {
    const manifest = holdReply();
    const t = task();
    t.to_leave = [{ ...t.to_leave![0]!, approval: undefined, shows: ['file:f6'] }];
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312')) return { status: 200, body: detail(t) };
      if (url.endsWith('/tasks/t1/files?all=1')) return manifest.promise;
      return base(url);
    });
    const loaded = loadTask(queryClient, '12', tab);
    const finished = vi.fn();
    void loaded.then(finished);
    try {
      await waitFor(() => expect(calls).toContain('GET /api/v2/tasks/t1/files?all=1'));
      expect(finished).not.toHaveBeenCalled();
      expect(calls.some((url) => url.startsWith('GET /api/v2/pages/'))).toBe(false);
    } finally {
      manifest.resolve({ status: 200, body: { files: [{ id: 'f6', name: 'attachment.pdf', versions: [] }] } });
    }
    await expect(loaded).resolves.toMatchObject({ tab: tab ?? 'attachment-pdf' });
  });

  it('keeps what the ask is about when the files do not load: its own tab, read by its id, not the page beside it @approvals', async () => {
    const t = task();
    t.to_leave = [{ ...t.to_leave![0]!, approval: undefined, shows: ['file:f6@1', 'page:f2@1'] }];
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312')) return { status: 200, body: detail(t) };
      if (url.endsWith('/tasks/t1/files?all=1')) return { status: 500, body: { error: { code: 'oops', detail: 'down' } } };
      return base(url);
    });
    await expect(loadTask(queryClient, '12')).resolves.toMatchObject({ tab: 'file' });
  });

  it('waits for the files when the ask is about a file and also shows a page @approvals', async () => {
    const t = task();
    t.to_leave = [{ ...t.to_leave![0]!, approval: undefined, shows: ['file:f6@1', 'page:f2@1'] }];
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312')) return { status: 200, body: detail(t) };
      if (url.endsWith('/tasks/t1/files?all=1')) return { status: 200, body: { files: [{ id: 'f6', name: 'declaration.pdf', versions: [] }] } };
      return base(url);
    });
    await expect(loadTask(queryClient, '12')).resolves.toMatchObject({ tab: 'declaration-pdf' });
  });
});

describe('one open task', () => {
  it('has no name while it loads, so the tab title and a thread’s page chip never read a placeholder', async () => {
    serve(() => new Promise<Reply>(() => {}));
    show(<TaskPage task="12" pipeline="p1" />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByRole('heading', { level: 1, name: 'Task' })).toBeNull();
    // what Tico's page name reads (tico/pageName: the page's h1)
    expect(document.querySelector('.pane.page h1')?.textContent ?? '').toBe('');
  });

  it('opens on the page that waits on the person, with every tab a link, and follows the task @s51 @s54 @s74 @s780 @s789', async () => {
    serve(base);
    show(<TaskPage task="12" pipeline="p1" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' })).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Fix the sign-in page pages' });
    // the tab that opens is picked once the task's files are read (a file is a tab too)
    await waitFor(() => expect(within(tabs).getByRole('link', { name: /QA report/ }).getAttribute('aria-current')).toBe('page'));
    expect(within(tabs).getByRole('link', { name: /Spec/ }).getAttribute('href')).toBe('/tasks/p1/12/spec');
    // An approval's view keeps Details under ⋯, not in the tab row.
    expect(within(tabs).queryByRole('link', { name: 'Details' })).toBeNull();
    expect(await screen.findByTitle('QA report')).toBeTruthy();
    // where it sits is the frame's trail (shell/Trail.tsx): the where line names no pipeline
    expect(screen.queryByRole('link', { name: 'Product' })).toBeNull();
    expect(vi.mocked(useFollow)).toHaveBeenCalledWith({ task: 't1' });
  });

  it('says its one status line in the head; Paused links Fix to where it is fixed', async () => {
    const turn = { state: 'paused' as const, who: 'bot:a1', line: 'Birch has no computer', href: '#/settings/computers', requirement_id: null, since: null, until: null, ask: false };
    serve(reads(task({ to_leave: [], status_line: 'paused', turn })));
    details();
    const said = await screen.findByTitle('Birch has no computer');
    expect(said.closest('.page-sub, header')).toBeTruthy();
    expect(within(said).getByRole('link', { name: 'Fix' }).getAttribute('href')).toBe('/settings/computers');
  });

  it('draws Details: what it needs to leave its stage, with what is met folded @s58', async () => {
    serve(base);
    details();
    expect(await screen.findByRole('heading', { name: /To leave Review/ })).toBeTruthy();
    expect(screen.getByText('QA report approved')).toBeTruthy();
    expect(screen.queryByText('Spec approved')).toBeNull();
  });

  it('ends the tab row with Expand; the due date is a Details fact even before one is set', async () => {
    serve(base);
    details();
    expect(await screen.findByRole('button', { name: 'Expand' })).toBeTruthy();
    const due = document.querySelector('[data-target="task-due:t1"]') as HTMLElement;
    expect(within(due).getByRole('button', { name: 'None' })).toBeTruthy();
  });

  it('uses one pipeline list and skips the Flow ordering read on a regular task', async () => {
    serve(base);
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Stage: Review' }));
    expect(await screen.findByRole('menuitem', { name: 'Build' })).toBeTruthy();
    expect(calls.filter((url) => url.startsWith('GET /api/v2/pipelines'))).toEqual(['GET /api/v2/pipelines']);
    expect(calls).not.toContain('GET /api/v2/needs-you?limit=200');
  });

  it('loads the stages of an ended task’s retired pipeline by id', async () => {
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312')) return { status: 200, body: detail(task({ status: 'done' })) };
      if (url.endsWith('/api/v2/pipelines')) return { status: 200, body: { pipelines: [] } };
      if (url.endsWith('/api/v2/pipelines/p1')) return { status: 200, body: { pipeline: { stages: [{ id: 's1', name: 'Build', side: false }] } } };
      return base(url);
    });
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Stage: Done' }));
    expect(await screen.findByRole('menuitem', { name: 'Build' })).toBeTruthy();
    expect(calls).toContain('GET /api/v2/pipelines/p1');
  });

  it('inside Flow, keeps the tab in the search @s126', async () => {
    serve(base);
    await queryClient.prefetchQuery(flowQuery);
    show(<TaskPage task="12" tab="details" flow />, '/flow/12?tab=details');
    const tabs = await screen.findByRole('navigation', { name: 'Fix the sign-in page pages' });
    expect(within(tabs).getByRole('link', { name: /Spec/ }).getAttribute('href')).toBe('/flow/12?tab=spec');
    expect(screen.queryByRole('link', { name: 'Flow' })).toBeNull();
    expect(calls.filter((url) => url === 'GET /api/v2/needs-you?limit=200')).toHaveLength(1);
  });

  it('shows an approval at once and takes it back with the server’s reason when refused @s109', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'POST' && url.includes('/asks/r1/answer'))
        return new Promise<Reply>(
          (r) => (refuse = () => r({ status: 409, body: { error: { code: 'task_stage_rule', detail: 'The QA report has no checks yet', missing: [] } } })),
        );
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" />);
    const card = await screen.findByRole('region', { name: 'The decision' });
    const approve = within(card).getByRole('button', { name: 'Approve' });
    await waitFor(() => expect(approve).toHaveProperty('disabled', false));
    fireEvent.click(approve);
    await waitFor(() => {
      const held = queryClient.getQueryData<TaskData>(keys.tasks.detail('t1'));
      expect(held?.to_leave?.find((r) => r.requirement_id === 'r1')?.met).toBe(true);
    });
    await waitFor(() => expect(calls.some((c) => c.includes('/asks/r1/answer'))).toBe(true));
    const tabs = screen.getByRole('navigation', { name: 'Fix the sign-in page pages' });
    expect(within(tabs).getByRole('link', { name: /QA report/ }).getAttribute('aria-current')).toBe('page');
    await act(async () => refuse());
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'The QA report has no checks yet');
    await waitFor(() => {
      const back = queryClient.getQueryData<TaskData>(keys.tasks.detail('t1'));
      expect(back?.to_leave?.find((r) => r.requirement_id === 'r1')?.met).toBe(false);
    });
    expect(within(screen.getByRole('region', { name: 'The decision' })).getByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('moves past what is missing only with a reason the person gives', async () => {
    serve((url, method, body) => {
      if (method === 'POST' && url.endsWith('/move') && !body.why)
        return { status: 409, body: { error: { code: 'task_not_met', detail: 'Not met: QA report approved.', missing: ['QA report approved: not yet'] } } };
      if (method === 'POST' && url.endsWith('/move')) return { status: 200, body: { task: task({ stage_id: 's3', stage: { id: 's3', name: 'Ship' } } as Partial<TaskData>) } };
      return base(url);
    });
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Stage: Review' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Ship' }));
    const dialog = await screen.findByRole('dialog', { name: 'Move to Ship?' });
    expect(within(dialog).getByText('QA report approved: not yet')).toBeTruthy();
    const anyway = within(dialog).getByRole('button', { name: 'Move anyway' });
    expect(anyway).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'The owner checked it by hand' } });
    fireEvent.click(anyway);
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ to: 's3', why: 'The owner checked it by hand' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('edits a fact in place and adds a link from Add, each saved at once', async () => {
    serve((url, method, body) => {
      if (method === 'POST' && url.endsWith('/links'))
        return { status: 200, body: { links: [{ id: 'l1', kind: 'url', url: body.url, title: null, task_id: 't1', created_at: '2026-11-02T10:00:00Z' }] } };
      if (method === 'POST' && url.endsWith('/api/v2/tasks/t1')) return { status: 200, body: { task: task({ labels: body.labels as string[] }) } };
      return base(url);
    });
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Facts' }));
    // Tags is not set yet.
    await screen.findAllByRole('button', { name: 'None' });
    fireEvent.click(within(document.querySelector('[data-target="task-tags:t1"]') as HTMLElement).getByRole('button', { name: 'None' }));
    const tags = await screen.findByRole('textbox', { name: 'Tags' });
    fireEvent.change(tags, { target: { value: 'bug, checkout, bug' } });
    fireEvent.keyDown(tags, { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ labels: ['bug', 'checkout'] }));
    expect(await screen.findByText('checkout')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Link' }));
    const link = await screen.findByRole('textbox', { name: 'Link' });
    fireEvent.change(link, { target: { value: 'https://status.example.com/today' } });
    fireEvent.keyDown(link, { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/links', body: { url: 'https://status.example.com/today' } }));
    expect(await screen.findByText('status.example.com')).toBeTruthy();
  });

  it('a link shows on the task at once and the server’s list replaces it; removing takes it off at once', async () => {
    let answer!: (r: Reply) => void;
    const at = '2026-11-02T10:00:00Z';
    serve((url, method, body) => {
      if (method === 'POST' && url.endsWith('/links') && body.url) return new Promise<Reply>((r) => (answer = r));
      if (method === 'POST' && url.endsWith('/links')) return { status: 200, body: { links: [] } };
      return base(url);
    });
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Link' }));
    const link = await screen.findByRole('textbox', { name: 'Link' });
    fireEvent.change(link, { target: { value: 'https://status.example.com/today' } });
    fireEvent.keyDown(link, { key: 'Enter' });
    const links = () => queryClient.getQueryData<TaskData>(keys.tasks.detail('t1'))?.links;
    await waitFor(() => expect(links()).toEqual([expect.objectContaining({ id: 'pending:https://status.example.com/today', kind: 'url', task_id: 't1' })]));
    expect(sent.filter((x) => x.url.endsWith('/links'))).toHaveLength(1);
    await act(async () => answer({ status: 200, body: { links: [{ id: 'l1', kind: 'url', url: 'https://status.example.com/today', task_id: 't1', created_at: at }] } }));
    await waitFor(() => expect(links()?.map((l) => l.id)).toEqual(['l1']));
    expect(await screen.findByText('status.example.com')).toBeTruthy();
  });

  it('draws the waiting question without a leading question mark emoji', async () => {
    serve((url) =>
      url.endsWith('/api/v2/tasks/t1') || url.includes('/api/v2/tasks/%2312')
        ? { status: 200, body: detail(task({ owner_id: 'bot:a1', turn: { who: 'human:me', line: '\u2753 Which headline goes live?' } } as Partial<TaskData>)) }
        : base(url),
    );
    details();
    expect(await screen.findByText('Which headline goes live?')).toBeTruthy();
    expect(screen.queryByText(/\u2753/u)).toBeNull();
  });

  it('marks paid shots reconciled from Details: their names, busy until the server answers, the keys it showed', async () => {
    const shots = { state: 'person', who: 'human:me', line: 'Reconcile 1 shot', requirement_id: 'paid_render:t1', since: null, until: null, ask: true, shots: [{ key: 'shot_a', name: 'Kettle wide' }] };
    const answer = holdReply();
    let waits: unknown[] = [shots];
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/tasks/t1/shots/reconcile')) return answer.promise;
      return reads(task({ waits } as Partial<TaskData>))(url);
    });
    details();
    const row = await waitFor(() => {
      const found = document.querySelector<HTMLElement>('[data-target="task-shots:t1"]');
      if (!found) throw new Error('no shots row yet');
      return found;
    });
    expect(within(row).getByText('Reconcile 1 shot')).toBeTruthy();
    expect(screen.getByText('Kettle wide')).toBeTruthy();
    const mark = screen.getByRole('button', { name: 'Mark reconciled' });
    fireEvent.click(mark);
    await waitFor(() => expect(sent).toContainEqual({ method: 'POST', url: '/api/v2/tasks/t1/shots/reconcile', body: { shots: ['shot_a'] } }));
    expect((mark as HTMLButtonElement).disabled).toBe(true); // busy while the request is in flight
    waits = []; // the task read after the answer has no shots left: the row and its button go
    await act(async () => answer.resolve({ status: 200, body: { reconciled: [{ key: 'shot_a', name: 'Kettle wide' }] } }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Mark reconciled' })).toBeNull());
    expect(document.querySelector('[data-target="task-shots:t1"]')).toBeNull();
  });

  it('draws a file that is not a page or a picture with the library preview @s56', async () => {
    const notes = {
      id: 'f6',
      name: 'notes.txt',
      title: 'Launch notes',
      mime: 'text/plain',
      archived: false,
      current_version: 2,
      links: [],
      writers: [],
      versions: [{ n: 2, by: 'bot:a1', created_at: '2026-11-02T10:00:00Z', mime: 'text/plain', url: '/api/v2/files/f6?v=2', answers: [], ask: null }],
    };
    serve((url) => {
      if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [notes] } };
      if (url.endsWith('/api/v2/files/f6/info')) return { status: 200, body: { file: { id: 'f6', name: 'notes.txt', title: 'Launch notes', mime: 'text/plain', library_kind: 'document', version: 2 } } };
      if (url.endsWith('/api/v2/files/f6/versions')) return { status: 200, body: { versions: [{ version: 2, current: true, mime: 'text/plain', name: 'notes.txt' }] } };
      if (url.endsWith('/api/v2/files/f6/versions/2')) return { status: 200, body: 'Ship on Friday after the review.' };
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" tab="details" file="f6" />, '/tasks/p1/12/details?file=f6');
    expect(await screen.findByText('Ship on Friday after the review.')).toBeTruthy();
    expect(document.querySelector('.frame.fv iframe')).toBeNull();
  });

  it('says when there is no such task', async () => {
    serve(() => ({ status: 404, body: { error: { code: 'not_found', detail: 'No such task' } } }));
    show(<TaskPage task="99" pipeline="p1" />, '/tasks/p1/99');
    expect(await screen.findByRole('heading', { name: 'No such task' })).toBeTruthy();
  });

  it('shows nothing of a task the person may not see', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden', detail: 'no' } } }));
    show(<TaskPage task="12" pipeline="p1" />);
    expect(await screen.findByRole('heading', { name: "You don't have access to this page" })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
  });

  it('offers Try again when the read fails', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'oops', detail: 'down' } } }));
    show(<TaskPage task="12" pipeline="p1" />);
    // The read is tried once more (after a second) before the fault shows.
    expect(await screen.findByRole('heading', { name: 'Task did not load' }, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('a page’s versions, a new page and a picture', () => {
  const versions = [
    { n: 1, by: 'bot:a1', at: '2026-11-01T10:00:00Z', note: 'Sent back' },
    { n: 2, by: 'bot:a1', at: '2026-11-02T10:00:00Z', note: null },
  ];
  const withVersions = () =>
    task({
      documents: [
        { file_id: 'f1', name: 'Spec', version: 2, approved_version: 2, versions },
        { file_id: 'f2', name: 'QA report', version: 1 },
      ] as never,
    });
  const html = (url: string): Reply | null => {
    if (url.endsWith('/api/v2/files/f1?v=1')) return { status: 200, body: '<p>Sign in with your email.</p>' };
    if (url.endsWith('/api/v2/files/f1?v=2')) return { status: 200, body: '<p>Sign in with your email or phone.</p>' };
    return null;
  };
  const frame = (title: string) => screen.findByTitle(title) as Promise<HTMLIFrameElement>;

  it.each([
    ['its list of versions', versions],
    ['a list that no longer reaches it', versions.slice(1)],
  ])('opens a page an ask pinned at v1 with v2 present, from %s @approvals', async (_, listed) => {
    const t = withVersions();
    (t.documents![0] as { versions: unknown }).versions = listed;
    t.to_leave = [{ requirement_id: 'r1', line: 'Spec approved', met: false, may: ['ok', 'no'], approval: { document: 'Spec', file_id: 'f1', version: 1 } } as never];
    serve((url) => (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(t) } : (html(url) ?? base(url))));
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect((await frame('Spec, v1')).getAttribute('srcdoc')).toContain('Sign in with your email.');
  });

  const pinnedAt = (n: number, live: Record<string, unknown>) => {
    const t = withVersions();
    t.to_leave = [{ requirement_id: 'r1', line: 'Spec approved', met: false, may: ['ok', 'no'], approval: { document: 'Spec', file_id: 'f1', version: n } } as never];
    serve((url) =>
      url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')
        ? { status: 200, body: detail(t) }
        : url.endsWith('/api/v2/pages/f1')
          ? { status: 200, body: { page: live } }
          : (html(url) ?? base(url)),
    );
  };

  it('shows the live page only while it is the version pinned; else that version’s own text @approvals', async () => {
    pinnedAt(2, { src: 'about:blank', version: 3 });
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect((await frame('Spec')).getAttribute('srcdoc')).toContain('Sign in with your email or phone.');
  });

  it('opens the version the address names even when the list does not reach it @approvals', async () => {
    const t = withVersions();
    (t.documents![0] as { versions: unknown }).versions = versions.slice(1);
    serve((url) => (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(t) } : (html(url) ?? base(url))));
    show(<TaskPage task="12" pipeline="p1" tab="spec" />, '/tasks/p1/12/spec?v=1');
    expect((await frame('Spec, v1')).getAttribute('srcdoc')).toContain('Sign in with your email.');
  });

  it('a related page an ask pinned shows live only while it is that version @approvals', async () => {
    const at = (n: number) => ({ n, by: 'bot:a1', created_at: '2026-11-01T10:00:00Z', mime: 'text/html', size: 10, url: `/api/v2/files/f7?v=${n}`, answers: [], ask: null });
    const page = { id: 'f7', name: 'plan.html', title: 'Plan', mime: 'text/html', archived: false, current_version: 2, links: [], writers: [], versions: [at(1), at(2)] };
    const t = task({ documents: [], to_leave: [{ requirement_id: 'r1', line: 'Plan approved', met: false, may: ['ok', 'no'], shows: ['file:f7@1'] } as never] });
    serve((url) =>
      url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')
        ? { status: 200, body: detail(t) }
        : url.endsWith('/tasks/t1/files?all=1')
          ? { status: 200, body: { files: [page] } }
          : url.endsWith('/api/v2/pages/f7')
            ? { status: 200, body: { page: { src: 'about:blank', version: 2 } } }
            : url.endsWith('/api/v2/files/f7?v=1')
              ? { status: 200, body: '<p>The plan as it was.</p>' }
              : base(url),
    );
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect((await frame('Plan')).getAttribute('srcdoc')).toContain('The plan as it was.');
  });

  it('with a version pinned, picking the newest names it, so it does not snap back to the pin @approvals', async () => {
    pinnedAt(1, { src: 'about:blank', version: 2 });
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    fireEvent.click(await screen.findByRole('button', { name: 'Version 1' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v2/ }));
    expect(await screen.findByRole('button', { name: 'Version 2' })).toBeTruthy();
    expect((await frame('Spec')).getAttribute('src')).toBe('about:blank');
  });

  it('opens an older version and marks what changed since the one before @s55', async () => {
    serve((url) => (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(withVersions()) } : (html(url) ?? base(url))));
    show(<TaskPage task="12" pipeline="p1" tab="spec" />, '/tasks/p1/12/spec');
    fireEvent.click(await screen.findByRole('button', { name: 'Version 2' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v1/ }));
    expect((await frame('Spec, v1')).getAttribute('srcdoc')).toContain('Sign in with your email.');

    fireEvent.click(await screen.findByRole('button', { name: 'Version 1' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v2/ }));
    const changes = await screen.findByRole('button', { name: '1 change' });
    fireEvent.click(changes);
    const marked = await frame('Spec, v2');
    expect(marked.getAttribute('srcdoc')).toMatch(/<del[^>]*>email\.<\/del> <ins[^>]*>email or phone\.<\/ins>/);
    expect(screen.getByRole('button', { name: '1 change' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Next change' })).toBeTruthy();
  });

  it('shows the selected older version even if the read for its change count fails', async () => {
    const t = withVersions();
    const doc = t.documents![0]!;
    doc.version = 3;
    doc.versions = [...versions, { ...versions[1]!, n: 3 }];
    const selected = holdReply();
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312')) return { status: 200, body: detail(t) };
      if (url.endsWith('/api/v2/files/f1?v=1')) return { status: 500, body: {} };
      if (url.endsWith('/api/v2/files/f1?v=2')) return selected.promise;
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" tab="spec" />, '/tasks/p1/12/spec?v=2');
    try {
      await waitFor(() => expect(queryClient.getQueryState(pageVersionQuery('f1', 1).queryKey)?.status).toBe('error'));
    } finally {
      selected.resolve(html('/api/v2/files/f1?v=2')!);
    }
    expect((await frame('Spec, v2')).getAttribute('srcdoc')).toContain('Sign in with your email or phone.');
  });

  it('lets the reader leave a linked comparison with no text changes', async () => {
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) {
        return { status: 200, body: detail(withVersions()) };
      }
      if (/\/api\/v2\/files\/f1\?v=[12]$/.test(url)) return { status: 200, body: '<p>Review the draft.</p>' };
      return base(url);
    });
    const router = show(<TaskPage task="12" pipeline="p1" tab="spec" />, '/tasks/p1/12/spec?vs=1');
    const changes = await screen.findByRole('button', { name: 'No changes' });
    expect(changes.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(changes);
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('vs'));
  });

  it('writes a new page from Add and opens it @s79', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/pages'))
        return { status: 200, body: { page: { id: 'pg9', file_id: 'f9', title: 'Release notes' }, unembedded: [] } };
      return base(url);
    });
    details();
    const dialog = await addDialog();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Title' }), { target: { value: 'Release notes' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Text' }), { target: { value: '<p>What shipped</p>' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent.at(-1)).toEqual({
        method: 'POST',
        url: '/api/v2/pages',
        body: { title: 'Release notes', text: '<p>What shipped</p>', task: 't1', as: 'related', name: 'release-notes.html' },
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens a picture full size from its file @s78', async () => {
    const pic = {
      id: 'f5',
      name: 'shot.png',
      title: 'Sign-in screen',
      mime: 'image/png',
      archived: false,
      current_version: 1,
      links: [],
      writers: [],
      versions: [{ n: 1, by: 'bot:a1', created_at: '2026-11-02T10:00:00Z', mime: 'image/png', url: '/api/v2/files/f5?v=1', answers: [], ask: null }],
    };
    serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [pic] } } : base(url)));
    show(<TaskPage task="12" pipeline="p1" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
    fireEvent.click(await screen.findByRole('button', { name: 'Sign-in screen, full size' }));
    expect(await screen.findByRole('dialog', { name: 'Sign-in screen' })).toBeTruthy();
  });
});

describe('the task’s More menu', () => {
  const flowServe = () =>
    serve((url, method) => {
      if (url.includes('/needs-you'))
        return { status: 200, body: { items: [{ task: { id: 't1', number: 12, pipeline_id: 'p1' } }, { task: { id: 't9', number: 13, pipeline_id: 'p1' } }] } };
      if (method === 'POST') return { status: 200, body: { task: task() } };
      return base(url);
    });
  it('snoozes the task until a day the person picks @s51', async () => {
    serve((url, method) => (method === 'POST' ? { status: 200, body: { task: task() } } : base(url)));
    details();
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Snooze' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Tomorrow' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/snooze'));
    const until = new Date(String(sent.at(-1)?.body.until));
    expect(until.getTime()).toBeGreaterThan(Date.now());
  });

  it('in Flow, Give to hands the task over and goes on to the next one @s113', async () => {
    people([{ id: 'bot:b2', kind: 'agent', name: 'Reviewer' }]);
    flowServe();
    const router = show(<TaskPage task="12" tab="details" flow />, '/flow/12?tab=details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Give to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reviewer' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/owner', body: { owner: 'bot:b2' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });

  it('in Flow, Move back sends it to an earlier stage and goes on to the next one @s114', async () => {
    flowServe();
    const router = show(<TaskPage task="12" tab="details" flow />, '/flow/12?tab=details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move back' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Build' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/move', body: { to: 's1' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });

  it('asks before deleting, then deletes and goes to the pipeline @s58', async () => {
    serve((url) => (url.endsWith('/delete') ? { status: 200, body: { deleted: true } } : undefined));
    const router = details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Delete “Fix the sign-in page”?' });
    expect(sent.some((s) => s.url.endsWith('/delete'))).toBe(false);
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/delete', body: {} }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1'));
  });

  it('reopens a finished task only with a reason @s58', async () => {
    serve(reads(task({ status: 'done' } as Partial<TaskData>)));
    details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reopen' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reopen it?' });
    const go = within(dialog).getByRole('button', { name: 'Reopen' }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Why reopen it?' }), { target: { value: 'The fix broke on Safari' } });
    fireEvent.click(go);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to: 'reopen', why: 'The fix broke on Safari' } }));
  });

  it('asks why when the stage picker reopens a finished task', async () => {
    serve(reads(task({ status: 'done' } as Partial<TaskData>)));
    details();
    fireEvent.click(await screen.findByRole('button', { name: 'Stage: Done' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reopen' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reopen it?' });
    expect(sent).toEqual([]);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Why reopen it?' }), { target: { value: 'Still broken' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reopen' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to: 'reopen', why: 'Still broken' } }));
  });

  it('moves the task to another pipeline and copies its link @s58', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    serve((url) => (url.endsWith('/move') ? { status: 200, body: { task: task({ pipeline_id: 'p2', stage_id: 'o1' }) } } : undefined));
    const router = details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${location.origin}/task/12`));

    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Ops' }));
    const dialog = await screen.findByRole('dialog', { name: 'Move to Ops' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to_pipeline: 'p2' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p2/12'));
  });
});

describe('Details: the facts @s58', () => {
  beforeEach(() =>
    people([
      { id: 'human:me', kind: 'person', name: 'Ana Diaz' },
      { id: 'human:bo', kind: 'person', name: 'Bo Lee' },
      { id: 'bot:a1', kind: 'agent', name: 'Builder' },
    ]),
  );
  const facts = async () => fireEvent.click(await screen.findByRole('button', { name: 'Facts' }));

  it('shows a related task once when it is both a parent and a blocker', async () => {
    const rollout = { id: 't2', number: 13, title: 'Plan the rollout', status: 'open', direction: 'out', owner_id: 'bot:a1' };
    serve(reads(task({ relations: { parent: [rollout], depends_on: [rollout] } })));
    details();
    const related = within(await screen.findByRole('region', { name: 'Related' }));
    const rows = related.getAllByRole('link', { name: 'Plan the rollout' });
    expect(rows).toHaveLength(1);
    expect(rows[0]?.getAttribute('href')).toBe('/task/13');
  });

  it('says how long the task has waited and on whom', async () => {
    const since = new Date(Date.now() - 2 * 3600_000).toISOString();
    serve(reads(task({ turn: { state: 'person', who: 'human:bo', line: 'OK the draft?', since, ask: true } } as Partial<TaskData>)));
    details();
    await facts();
    const label = await screen.findByText('Waiting');
    const line = label.parentElement as HTMLElement;
    expect(line.textContent).toBe('Waiting2h on Bo Lee');
  });

  it('says only whom the task waits on after a reassignment in the same stage', async () => {
    // The wait's time is still the stage's: it says nothing of how long the new owner has had it.
    const entered = '2026-11-01T10:00:00Z';
    serve(reads(task({ stage_since: entered, owner_id: 'bot:a1', turn: { state: 'bot', who: 'bot:a1', line: 'Draft the post', since: entered, ask: false } } as Partial<TaskData>)));
    details();
    await facts();
    const label = await screen.findByText('Waiting');
    expect((label.parentElement as HTMLElement).textContent).toBe('Waitingon Builder');
  });

  it('sets the Human in place', async () => {
    serve(base);
    details();
    await facts();
    fireEvent.click(await screen.findByRole('button', { name: 'Human' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Bo Lee' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1', body: { human: 'human:bo' } }));
  });

  it('validates and saves the size in place', async () => {
    serve(base);
    details();
    await facts();
    fireEvent.click(screen.getByRole('button', { name: 'Size' }));
    const size = await screen.findByRole('textbox', { name: 'Size' });
    fireEvent.change(size, { target: { value: 'big' } });
    fireEvent.keyDown(size, { key: 'Enter' });
    expect(await screen.findByText('Use a number')).toBeTruthy();
    fireEvent.change(size, { target: { value: '3' } });
    fireEvent.keyDown(size, { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1', body: { size: 3 } }));
  });

  it('puts the task under another from Part of, and reads both again', async () => {
    serve((url, method) => (method === 'POST' && url.endsWith('/parent') ? { status: 200, body: { task: { id: 't1', number: 12 } } } : undefined));
    details();
    await facts();
    fireEvent.click(await screen.findByRole('button', { name: 'Part of' }));
    const parent = await screen.findByRole('textbox', { name: 'Part of' });
    fireEvent.change(parent, { target: { value: '40' } });
    const before = calls.length;
    fireEvent.keyDown(parent, { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks/t1/parent', body: { parent: '#40' } }));
    await waitFor(() => expect(calls.slice(before)).toContain('GET /api/v2/tasks/t1'));
  });

  it('task.subtask takes an existing task or a new title, one of the two', async () => {
    serve(base);
    const bad = (input: object) => run('task.subtask', input, { via: 'click' });
    expect(await bad({ task: 't1' })).toMatchObject({ ok: false, error: expect.stringMatching(/^bad_input: input: give subtask/) });
    expect(await bad({ task: 't1', subtask: '40', title: 'Write it' })).toMatchObject({ ok: false, error: expect.stringMatching(/^bad_input: input: give subtask/) });
    expect(await bad({ task: 't1', subtask: '40', owner: 'bot:a1' })).toMatchObject({ ok: false, error: 'bad_input: owner: is only for a new subtask (title)' });
    expect(sent).toEqual([]);
    expect(await run('task.subtask', { task: 't1', title: ' Write it ' }, { via: 'click' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toEqual({ method: 'POST', url: '/api/v2/tasks', body: { title: 'Write it', parent: 't1' } });
  });

  it('task.move to reopen needs a why', async () => {
    serve(base);
    expect(await run('task.move', { task: 't1', to: 'reopen' }, { via: 'click' })).toMatchObject({ ok: false, error: 'bad_input: why: is needed to reopen a task' });
    expect(sent).toEqual([]);
  });

  it('follows the task, and puts someone in a role', async () => {
    serve((url, method) => {
      if (method === 'PUT' && url.endsWith('/followers')) return { status: 200, body: { task_id: 't1', following: true, notify: 'live', followers: [{ actor: 'human:me', notify: 'live' }] } };
      return reads(task({ followers: [] } as Partial<TaskData>))(url);
    });
    details();
    await facts();
    fireEvent.click(await screen.findByRole('button', { name: 'Follow' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ method: 'PUT', url: '/api/v2/tasks/t1/followers', body: { following: true } }));
    expect(await screen.findByRole('button', { name: 'Unfollow' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Add someone' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add someone' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Role' }), { target: { value: 'QA Lead' } });
    expect((within(dialog).getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('lists the meetings the task was in, each opening in the calendar', async () => {
    serve((url) =>
      url.includes('/meetings')
        ? { status: 200, body: { meetings: [{ kind: 'linked', meeting_id: 'm1', started_at: '2026-11-10T10:00:00Z', title: 'Sign-in review', url: '' }], next_cursor: null } }
        : undefined,
    );
    details();
    fireEvent.click(await screen.findByRole('button', { name: /Meetings/ }));
    const row = await screen.findByRole('link', { name: /Sign-in review/ });
    expect(row.getAttribute('href')).toMatch(/^\/calendar\?.*panel=meeting%3Am1/);
  });
});

describe('a task’s files and pages @s56 @s76 @s780', () => {
  const page = { file_id: 'f2', title: 'QA report', src: 'about:blank', archived: false, may_write: true, version: 1 };
  const shot = {
    id: 'f9',
    name: 'shot.png',
    title: 'Sign-in screen',
    mime: 'image/png',
    archived: false,
    current_version: 2,
    links: [],
    writers: [],
    versions: [
      { n: 1, by: 'bot:a1', created_at: '2026-11-01T10:00:00Z', mime: 'image/png', url: '/api/v2/files/f9?v=1', answers: [], ask: null },
      { n: 2, by: 'bot:a1', created_at: '2026-11-02T10:00:00Z', mime: 'image/png', url: '/api/v2/files/f9?v=2', answers: [], ask: null },
    ],
  };
  /** The task with one page (QA report) the person manages, and one picture. */
  const files = (url: string): Reply | undefined => {
    if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [shot] } };
    if (url.endsWith('/api/v2/pages/f2')) return { status: 200, body: { page, can_manage: true, can_delete: true } };
    return reads(task({ documents: [{ file_id: 'f2', name: 'QA report', version: 1 }], to_leave: [] } as unknown as Partial<TaskData>))(url);
  };

  it('uploads a file from Add and opens it', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/tasks/t1/files') ? { status: 200, body: { file_id: 'f9', version: 1, link: null, file: shot } } : files(url),
    );
    const router = details();
    const dialog = await addDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Upload' }));
    await screen.findByRole('dialog', { name: 'Upload a file' });
    fireEvent.change(screen.getByLabelText('Files to add'), { target: { files: [new File(['hi'], 'notes.txt', { type: 'text/plain' })] } });
    await waitFor(() => expect(calls).toContain('POST /api/v2/tasks/t1/files'));
    await waitFor(() => expect((router.state.location.search as { file?: string }).file).toBe('f9'));
  });

  it('links a page from elsewhere to the task', async () => {
    serve((url, method) => {
      if (url.includes('/api/v2/files?kind=page')) return { status: 200, body: { files: [{ id: 'pg9', title: 'Pricing notes', may_write: true }] } };
      if (method === 'POST' && url.endsWith('/api/v2/pages/pg9/links')) return { status: 200, body: { page: { file_id: 'pg9' } } };
      return files(url);
    });
    details();
    const dialog = await addDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Link a page' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Pricing notes' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/pg9/links', method: 'POST', body: { target: 'task:t1', role: 'related' } }));
  });

  it('shows an older version of an open file, compares two, and removes the file', async () => {
    serve(files);
    const router = show(<TaskPage task="12" pipeline="p1" tab="details" file="f9" />, '/tasks/p1/12/details?file=f9');
    expect(await screen.findByRole('button', { name: 'Sign-in screen, full size' })).toBeTruthy();
    await lastMore();
    expect(screen.getByRole('menuitem', { name: 'Download' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Versions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'v1' }));
    expect(await screen.findByAltText('Sign-in screen, v1')).toBeTruthy();

    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Compare' }));
    expect(await screen.findByRole('region', { name: 'v1' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'v2' })).toBeTruthy();

    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove file' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f9', method: 'PATCH', body: { archived: true } }));
    await waitFor(() => expect((router.state.location.search as { file?: string }).file).toBeUndefined());
  });

  describe('archived files', () => {
    const live = {
      id: 'f8',
      name: 'letter.md',
      title: 'Letter',
      mime: 'text/markdown',
      archived: false,
      current_version: 1,
      versions: [{ n: 1, by: 'bot:a1', created_at: '2026-11-02T10:00:00Z', mime: 'text/markdown', url: '/api/v2/files/f8?v=1', answers: [], ask: null }],
    };
    const draft = { ...live, id: 'f7', name: 'letter-v1.md', title: 'Letter draft', archived: true, can_restore: true, versions: [{ ...live.versions[0], url: '/api/v2/files/f7?v=1' }] };
    const withDraft = (url: string, method: string): Reply | undefined => {
      if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [live, draft] } };
      if (method === 'PATCH' && url.endsWith('/api/v2/files/f7')) return ok;
      if (url.endsWith('/api/v2/files/f7/info')) return { status: 200, body: { file: { id: 'f7', name: 'letter-v1.md', title: 'Letter draft', mime: 'text/markdown', library_kind: 'document', version: 1 } } };
      if (url.endsWith('/api/v2/files/f7/versions')) return { status: 200, body: { versions: [{ version: 1, current: true, mime: 'text/markdown', name: 'letter-v1.md' }] } };
      if (url.endsWith('/api/v2/files/f7/versions/1')) return { status: 200, body: 'Dear Dana,' };
      return files(url);
    };

    it('leave the tabs for the Archive menu at the row’s end, where each opens', async () => {
      serve(withDraft);
      const router = show(<TaskPage task="12" pipeline="p1" />);
      expect(await screen.findByRole('link', { name: 'Letter' })).toBeTruthy();
      expect(screen.queryByRole('link', { name: 'Letter draft' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Archive (1)' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Letter draft' }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
      expect((router.state.location.search as { file?: string }).file).toBe('f7');
    });

    it('open and restore from their own ⋯', async () => {
      serve(withDraft);
      show(<TaskPage task="12" pipeline="p1" tab="details" file="f7" />, '/tasks/p1/12/details?file=f7');
      expect(await screen.findByText('Dear Dana,')).toBeTruthy();
      await lastMore();
      expect(screen.queryByRole('menuitem', { name: 'Remove file' })).toBeNull();
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
      await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f7', method: 'PATCH', body: { archived: false } }));
    });

    it('restore a page as a page for a writer of it, who need not move the task', async () => {
      const brief = { ...draft, id: 'f5', name: 'brief.html', title: 'Brief', mime: 'text/html', page_archived: true, links: [], writers: [] };
      serve((url, method) => {
        if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [brief] } };
        if (method === 'PATCH' && url.endsWith('/api/v2/pages/f5')) return { status: 200, body: { page: { file_id: 'f5', archived: false } } };
        return reads(task({ mover: false, owner_id: 'bot:a1', requester_id: 'human:other', documents: [], to_leave: [] } as unknown as Partial<TaskData>))(url);
      });
      show(<TaskPage task="12" pipeline="p1" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
      await waitFor(() => expect(calls).toContain('GET /api/v2/files/f5/versions/1'));
      await lastMore();
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
      await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/f5', method: 'PATCH', body: { archived: false } }));
    });

    it('restore a page whose file was archived by clearing the file’s flag', async () => {
      const brief = { ...draft, id: 'f5', name: 'brief.html', title: 'Brief', mime: 'text/html', page_archived: false, links: [], writers: [] };
      serve((url, method) => {
        if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [brief] } };
        if (method === 'PATCH' && url.endsWith('/api/v2/files/f5')) return ok;
        return files(url);
      });
      show(<TaskPage task="12" pipeline="p1" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
      await waitFor(() => expect(calls).toContain('GET /api/v2/files/f5/versions/1'));
      await lastMore();
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
      await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f5', method: 'PATCH', body: { archived: false } }));
    });

    it('restore a page archived both as a page and as a file by clearing both', async () => {
      const brief = { ...draft, id: 'f5', name: 'brief.html', title: 'Brief', mime: 'text/html', page_archived: true, file_archived: true, links: [], writers: [] };
      serve((url, method) => {
        if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { status: 200, body: { files: [brief] } };
        if (method === 'PATCH' && url.endsWith('/api/v2/pages/f5')) return { status: 200, body: { page: { file_id: 'f5', archived: true } } };
        if (method === 'PATCH' && url.endsWith('/api/v2/files/f5')) return ok;
        return files(url);
      });
      show(<TaskPage task="12" pipeline="p1" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
      await waitFor(() => expect(calls).toContain('GET /api/v2/files/f5/versions/1'));
      await lastMore();
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
      await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f5', method: 'PATCH', body: { archived: false } }));
      expect(sent.at(-2)).toEqual({ url: '/api/v2/pages/f5', method: 'PATCH', body: { archived: false } });
    });

    it('open an archived picture without a full size view, and offer Restore only to whoever may', async () => {
      serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [{ ...shot, archived: true, can_restore: false }] } } : files(url)));
      show(<TaskPage task="12" pipeline="p1" tab="details" file="f9" />, '/tasks/p1/12/details?file=f9');
      expect(await screen.findByAltText('Sign-in screen, v2')).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Sign-in screen, full size' })).toBeNull();
      await lastMore();
      expect(await screen.findByRole('menuitem', { name: 'Download' })).toBeTruthy();
      expect(screen.queryByRole('menuitem', { name: 'Restore' })).toBeNull();
    });
  });

  it('a page’s ⋯ archives the page and goes back to Details', async () => {
    serve((url, method) => (method === 'PATCH' && url.endsWith('/api/v2/pages/f2') ? { status: 200, body: { page: { ...page, archived: true } } } : files(url)));
    const router = show(<TaskPage task="12" pipeline="p1" />);
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/f2', method: 'PATCH', body: { archived: true } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
  });

  it('a page’s ⋯ deletes the page for good only after asking', async () => {
    serve(files);
    show(<TaskPage task="12" pipeline="p1" />);
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete page' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Delete QA report?' });
    expect(calls.some((c) => c.startsWith('DELETE'))).toBe(false);
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls).toContain('DELETE /api/v2/files/f2'));
  });

  it('adding a file from disk, deleting a page and showing the task to the team are closed to Tico', async () => {
    serve(files);
    show(<TaskPage task="12" pipeline="p1" />);
    await screen.findByTitle('QA report');
    const tico = { via: 'tico' } as const;
    expect((await run('task.upload', { task: 't1', file: new File(['hi'], 'a.txt') }, tico)).ok).toBe(false);
    expect((await run('task.deletePage', { task: 't1', page: 'f2' }, tico)).ok).toBe(false);
    // Showing a task to the whole team is the person's own press too.
    expect((await run('task.setVisible', { task: 't1', to: 'team' }, tico)).ok).toBe(false);
    expect(sent).toEqual([]);
  });
});
