import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { keys, queryClient } from '@/data';
import { useFollow } from '@/live/follow';
import type { TaskData } from './queries';
import { TaskPage } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
const calls: string[] = [];
const sent: { url: string; body: Record<string, unknown> }[] = [];
function serve(reply: (url: string, method: string, body: Record<string, unknown>) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push(`${method} ${String(url)}`);
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    if (method !== 'GET') sent.push({ url: String(url), body });
    const r = await reply(String(url), method, body);
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

const ok: Reply = { status: 200, body: {} };
function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { status: 200, body: detail(task()) };
  if (url.includes('/files')) return { status: 200, body: { files: [] } };
  if (url.includes('/api/v2/pages/')) return { status: 200, body: { page: { src: 'about:blank' } } };
  if (url.includes('/api/v2/pipelines'))
    return {
      status: 200,
      body: { pipelines: [{ id: 'p1', name: 'Product', stages: [{ id: 's1', name: 'Build', side: false }, { id: 's2', name: 'Review', side: false }, { id: 's3', name: 'Ship', side: false }] }] },
    };
  if (url.includes('/needs-you')) return { status: 200, body: { items: [] } };
  return ok;
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  calls.length = 0;
  sent.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(useFollow).mockClear();
});

describe('one open task', () => {
  it('opens on the page that waits on the person, with every tab a link, and follows the task @s51 @s54 @s74 @s780 @s789', async () => {
    serve(base);
    show(<TaskPage task="12" pipeline="p1" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' })).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Fix the sign-in page pages' });
    expect(within(tabs).getByRole('link', { name: /QA report/ }).getAttribute('aria-current')).toBe('page');
    expect(within(tabs).getByRole('link', { name: /Spec/ }).getAttribute('href')).toBe('/tasks/p1/12/spec');
    expect(within(tabs).getByRole('link', { name: 'Details' }).getAttribute('href')).toBe('/tasks/p1/12/details');
    expect(await screen.findByTitle('QA report')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Product' }).getAttribute('href')).toBe('/tasks/p1');
    expect(vi.mocked(useFollow)).toHaveBeenCalledWith({ task: 't1' });
  });

  it('draws Details: what it needs to leave its stage, with what is met folded @s58', async () => {
    serve(base);
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    expect(await screen.findByRole('heading', { name: /To leave Review/ })).toBeTruthy();
    expect(screen.getByText('QA report approved')).toBeTruthy();
    expect(screen.queryByText('Spec approved')).toBeNull();
  });

  it('inside Flow, keeps the tab in the search @s126', async () => {
    serve(base);
    show(<TaskPage task="12" tab="spec" flow />, '/flow/12?tab=spec');
    const tabs = await screen.findByRole('navigation', { name: 'Fix the sign-in page pages' });
    expect(within(tabs).getByRole('link', { name: 'Details' }).getAttribute('href')).toBe('/flow/12?tab=details');
    expect(screen.getByRole('link', { name: 'Flow' }).getAttribute('href')).toBe('/flow');
  });

  it('shows an approval at once and takes it back with the server’s reason when refused @s109', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (url.includes('/api/v2/oks/')) return { status: 404, body: { error: { code: 'not_found', detail: 'none' } } };
      if (method === 'POST' && url.includes('/asks/r1/answer'))
        return new Promise<Reply>(
          (r) => (refuse = () => r({ status: 409, body: { error: { code: 'task_stage_rule', detail: 'The QA report has no checks yet', missing: [] } } })),
        );
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" />);
    const card = await screen.findByRole('region', { name: 'Answer' });
    fireEvent.click(within(card).getByRole('button', { name: 'Approve' }));
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
    expect(within(screen.getByRole('region', { name: 'Answer' })).getByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('moves past what is missing only with a reason the person gives', async () => {
    serve((url, method, body) => {
      if (method === 'POST' && url.endsWith('/move') && !body.why)
        return { status: 409, body: { error: { code: 'task_not_met', detail: 'Not met: QA report approved.', missing: ['QA report approved: not yet'] } } };
      if (method === 'POST' && url.endsWith('/move')) return { status: 200, body: { task: task({ stage_id: 's3', stage: { id: 's3', name: 'Ship' } } as Partial<TaskData>) } };
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
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
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    fireEvent.click(await screen.findByRole('button', { name: 'Facts' }));
    // Part of, then Tags: neither is set yet.
    fireEvent.click((await screen.findAllByRole('button', { name: 'None' }))[1] as HTMLElement);
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
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/links', body: { url: 'https://status.example.com/today' } }));
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
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
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
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    expect(await screen.findByText('Which headline goes live?')).toBeTruthy();
    expect(screen.queryByText(/\u2753/u)).toBeNull();
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

  it('writes a new page from Add and opens it @s79', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/pages'))
        return { status: 200, body: { page: { id: 'pg9', file_id: 'f9', title: 'Release notes' }, unembedded: [] } };
      return base(url);
    });
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'File or page' }));
    const dialog = await screen.findByRole('dialog', { name: 'New page' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Title' }), { target: { value: 'Release notes' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Text' }), { target: { value: '<p>What shipped</p>' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent.at(-1)).toEqual({
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
  const more = async () => fireEvent.click((await screen.findAllByRole('button', { name: 'More' }))[0] as HTMLElement);

  it('snoozes the task until a day the person picks @s51', async () => {
    serve((url, method) => (method === 'POST' ? { status: 200, body: { task: task() } } : base(url)));
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Snooze' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Tomorrow' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/snooze'));
    const until = new Date(String(sent.at(-1)?.body.until));
    expect(until.getTime()).toBeGreaterThan(Date.now());
  });

  it('in Flow, Give to hands the task over and goes on to the next one @s113', async () => {
    const actors = [{ id: 'bot:b2', kind: 'agent', name: 'Reviewer' }];
    queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
    flowServe();
    const router = show(<TaskPage task="12" tab="details" flow />, '/flow/12?tab=details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Give to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reviewer' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/owner', body: { owner: 'bot:b2' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });

  it('in Flow, Move back sends it to an earlier stage and goes on to the next one @s114', async () => {
    flowServe();
    const router = show(<TaskPage task="12" tab="details" flow />, '/flow/12?tab=details');
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move back' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Build' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/move', body: { to: 's1' } }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });
});
