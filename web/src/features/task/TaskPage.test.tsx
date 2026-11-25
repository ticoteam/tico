import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { keys, queryClient } from '@/data';
import { coldOf, coldPage, forgetPlaces, NEEDS_YOU, noteList, openOf, openTasksOver } from '@/lib';
import { useFollow } from '@/live/follow';
import { answered, answering } from './answered';
import { pageVersionQuery, taskFilesQuery, type TaskData } from './queries';
import { loadTask } from './routing';
import { TaskChat, TaskPage } from './TaskPage';

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

/** The task over its board as a cold link opens it (lib taskAddress), with the app's router opening tasks over it. */
function over(at: string): string {
  const [path = '', query = ''] = at.split('?');
  const words = Object.fromEntries(new URLSearchParams(query));
  const cold = coldOf(path, words);
  if (!cold) return at;
  const to = coldPage(cold, words);
  return `${to.to}?${new URLSearchParams(Object.entries(to.search).map(([k, v]) => [k, String(v)]))}`;
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
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [over(at)] }) });
  openTasksOver(router);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

// Read with node's fs (tests load no CSS, not even ?raw), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import

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
const details = () => show(<TaskPage task="12" tab="details" />, '/tasks/p1/12/details');
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

describe('warming an opening task', () => {
  it('reuses a task read from an older id link for its number', async () => {
    serve(base);
    await loadTask(queryClient, 't1');
    await loadTask(queryClient, '12', 'qa-report');
    expect(calls.filter((call) => /^GET \/api\/v2\/tasks\/(t1|%2312)$/.test(call))).toEqual(['GET /api/v2/tasks/t1']);
  });

  it('warms an open task’s chat before the file manifest comes back', async () => {
    const manifest = holdReply();
    serve((url) => {
      if (url.endsWith('/tasks/%2312')) return { status: 200, body: detail(task({ conversation_id: 'room', documents: [] })) };
      if (url.endsWith('/tasks/t1/files?all=1')) return manifest.promise;
      return base(url);
    });
    const loaded = loadTask(queryClient, '12', 'details');
    try {
      await waitFor(() => expect(calls).toContain('GET /api/v2/threads/room/timeline?flat=1&limit=50'));
      expect(calls).toContain('GET /api/v2/threads/room');
      expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.fetchStatus).toBe('fetching');
    } finally {
      manifest.resolve({ status: 200, body: { files: [] } });
      await loaded;
    }
  });

  it('warms only the comments of a task’s own thread, the view its chat opens on', async () => {
    serve((url) => {
      if (url.endsWith('/tasks/%2312')) return { status: 200, body: detail(task({ conversation_id: 'room', documents: [] })) };
      if (url.endsWith('/api/v2/threads/room')) return { status: 200, body: { id: 'room', task_id: 't1' } };
      return base(url);
    });
    await loadTask(queryClient, '12', 'details');
    // the warm read is not waited for: it goes once the thread is read
    await waitFor(() => expect(calls.filter((c) => c.startsWith('GET /api/v2/threads/room/timeline'))).toHaveLength(1));
    expect(calls.find((c) => c.startsWith('GET /api/v2/threads/room/timeline'))).toContain('comments=true');
  });

  it.each([['spec', 'f1'], [undefined, 'f2'], ['attachment-pdf', null], ['details', null]] as const)(
    'starts the page tab %s shows and never waits for the files',
    async (tab, file) => {
      const manifest = holdReply();
      serve((url) => (url.endsWith('/tasks/t1/files?all=1') ? manifest.promise : base(url)));
      try {
        // done while the manifest is still on its way: nothing reads what the warm-up finds
        await loadTask(queryClient, '12', tab);
        expect(calls).toContain('GET /api/v2/tasks/t1/files?all=1');
        if (file) expect(calls).toContain(`GET /api/v2/pages/${file}`);
        else expect(calls.some((url) => url.startsWith('GET /api/v2/pages/'))).toBe(false);
        expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.fetchStatus).toBe('fetching');
      } finally {
        manifest.resolve({ status: 200, body: { files: [] } });
        await queryClient.prefetchQuery(taskFilesQuery('t1'));
      }
    },
  );
});

describe('one open task', () => {
  it('closed before its files are read, never opens itself again as they arrive', async () => {
    const manifest = holdReply();
    serve((url) => (url.endsWith('/tasks/t1/files?all=1') ? manifest.promise : undefined));
    // the modal draws the task while its layer fades out: here it stays drawn after the address closes it
    const router = show(<TaskPage task="12" />, '/tasks/p1/12');
    await waitFor(() => expect(calls).toContain('GET /api/v2/tasks/t1/files?all=1'));
    await act(() => router.navigate({ to: '/tasks/p1', search: {}, replace: true } as never));
    manifest.resolve({ status: 200, body: { files: [] } });
    await waitFor(() => expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.status).toBe('success'));
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(router.state.location.search).toEqual({});
  });

  it('answered as it closes (the answer landing once Back took it away): opens no next task over the page', async () => {
    serve(reads(task({ to_leave: [] })));
    const router = show(<TaskPage task="12" />, '/task/12');
    await screen.findByRole('heading', { name: 'Fix the sign-in page' });
    // Needs you, the list it was opened from, has a task below it
    noteList({ pipeline: NEEDS_YOU, open: '12', rows: [{ id: 't1', n: '12' }, { id: 't2', n: '13' }] });
    answering('t1', null);
    await act(() => router.navigate({ to: '/tasks', search: {}, replace: true } as never));
    act(() => answered());
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(router.state.location.search).toEqual({});
    forgetPlaces();
  });

  it('an answer to another task (its card still drawn under this one): this one stays open', async () => {
    serve(reads(task({ to_leave: [] })));
    const router = show(<TaskPage task="12" />, '/task/12');
    await screen.findByRole('heading', { name: 'Fix the sign-in page' });
    noteList({ pipeline: NEEDS_YOU, open: '12', rows: [{ id: 't1', n: '12' }, { id: 't2', n: '13' }] });
    answering('t9', null);
    act(() => answered());
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(String(openOf(router.state.location.search as Record<string, unknown>)?.task)).toBe('12');
    forgetPlaces();
  });

  it('has no name while it loads, so the tab title and a thread’s page chip never read a placeholder', async () => {
    serve(() => new Promise<Reply>(() => {}));
    show(<TaskPage task="12" />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByRole('heading', { level: 1, name: 'Task' })).toBeNull();
    // what Tico's page name reads (tico/pageName: the page's h1)
    expect(document.querySelector('.pane.page h1')?.textContent ?? '').toBe('');
  });

  it('opens on the page that waits on the person, with every tab a link, and follows the task @s51 @s54 @s74 @s780 @s789', async () => {
    serve(base);
    show(<TaskPage task="12" />);
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

  it('names who has it beside their face in the head, on a page tab too', async () => {
    people([{ id: 'bot:a1', kind: 'agent', name: 'Builder' }]);
    serve(reads(task({ to_leave: [] })));
    show(<TaskPage task="12" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' })).toBeTruthy();
    const who = await waitFor(() => document.querySelector('[data-target="task-owner:t1"]') as HTMLElement);
    expect(await screen.findByRole('button', { name: 'Who has it: Builder' })).toBe(who);
    expect(within(who).getByText('Builder')).toBeTruthy();
  });

  it('says the task another task blocks it on in the head, on a page tab too, linked and never by its id', async () => {
    const id = '3f9c1d2e-7b4a-4c1e-9d2f-0a1b2c3d4e5f';
    const blocker = { id, number: 130, title: 'Matches cause check', status: 'open', direction: 'out', owner_id: 'bot:a1' };
    const turn = { state: 'tasks', who: id, line: 'Blocked by Matches cause check, in To do', requirement_id: 'dep:x', since: null, until: null, ask: false };
    serve(reads(task({ to_leave: [], relations: { depends_on: [blocker] }, turn } as Partial<TaskData>)));
    show(<TaskPage task="12" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' })).toBeTruthy();
    const link = within(document.querySelector('.pk-where') as HTMLElement).getByRole('link', { name: 'Blocked by Matches cause check, in To do' });
    const head = link.closest('header') as HTMLElement;
    expect(head).toBeTruthy();
    expect(link.getAttribute('href')).toBe(`/task/${id}`);
    expect(head.textContent).not.toContain(id);
    // a page tab, not Details
    expect(screen.queryByText('Waiting on')).toBeNull();
  });

  it('says nothing in the head of a task blocking it that the reader may not open', async () => {
    // the server names a private blocker to its readers only (task_calls.task_view); this is what any other line reads
    const turn = { state: 'tasks', who: 'private-1', line: 'Blocked by Salary numbers, in Build', requirement_id: 'dep:x', since: null, until: null, ask: false };
    serve(reads(task({ to_leave: [], turn } as Partial<TaskData>)));
    show(<TaskPage task="12" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' })).toBeTruthy();
    const head = document.querySelector('header') as HTMLElement;
    expect(head.textContent).not.toContain('Salary');
    expect(head.querySelector('[title*="Salary"]')).toBeNull();
  });

  it('says the task blocking it in the head of a sent-back approval’s tab too', async () => {
    const id = '3f9c1d2e-7b4a-4c1e-9d2f-0a1b2c3d4e5f';
    const blocker = { id, number: 130, title: 'Matches cause check', status: 'open', direction: 'out', owner_id: 'bot:a1' };
    const turn = { state: 'tasks', who: id, line: 'Blocked by Matches cause check, in To do', requirement_id: 'dep:x', since: null, until: null, ask: false };
    serve(reads(task({ relations: { depends_on: [blocker] }, turn } as Partial<TaskData>)));
    show(<TaskPage task="12" />);
    await waitFor(() => expect(document.querySelector('.tv.is-quiet')).toBeTruthy());
    const where = document.querySelector('.pk-where') as HTMLElement;
    expect(within(where).getByRole('link', { name: 'Blocked by Matches cause check, in To do' }).getAttribute('href')).toBe(`/task/${id}`);
  });

  it.each([
    ['a phone', 'cards', false],
    ['a computer', 'sheets', false],
    ['a computer, a sent-back approval', 'sheets', true],
  ])('on %s, the wait is whole on its own line under the where line, whose picks never break', async (_, layout, quiet) => {
    const style = document.createElement('style');
    style.textContent = ['../../styles/parts.css', '../../pagekinds/pagekinds.css', './task.css'].map((f) => fs.readFileSync(new URL(f, here), 'utf8')).join('\n');
    document.head.append(style);
    document.documentElement.dataset.layout = layout;
    try {
      const id = '3f9c1d2e-7b4a-4c1e-9d2f-0a1b2c3d4e5f';
      const blocker = { id, number: 130, title: 'Matches cause check', status: 'open', direction: 'out', owner_id: 'bot:a1' };
      const turn = { state: 'tasks', who: id, line: 'Blocked by Matches cause check, in To do', requirement_id: 'dep:x', since: null, until: null, ask: false };
      people([{ id: 'bot:a1', kind: 'agent', name: 'Engineering Manager' }]);
      serve(reads(task({ ...(!quiet && { to_leave: [] }), relations: { depends_on: [blocker] }, turn } as Partial<TaskData>)));
      show(<TaskPage task="12" />);
      await waitFor(() => expect(document.querySelector(quiet ? '.tv.is-quiet .pk-where' : '.tv:not(.is-quiet) .pk-where')).toBeTruthy());
      const where = document.querySelector('.tv .pk-where') as HTMLElement;
      expect([getComputedStyle(where).flexWrap, getComputedStyle(where).height]).toEqual(['wrap', 'auto']);
      const link = await within(where).findByRole('link', { name: 'Blocked by Matches cause check, in To do' });
      const wait = link.closest('.tv-wait') as HTMLElement;
      expect(wait.parentElement).toBe(where);
      expect([getComputedStyle(wait).flexBasis, getComputedStyle(wait).whiteSpace, getComputedStyle(wait).textOverflow]).toEqual(['100%', 'normal', 'clip']);
      expect(getComputedStyle(link).textDecoration).toContain('underline');
      if (quiet) return;
      const owner = where.querySelector('[data-target="task-owner:t1"]') as HTMLElement;
      expect([getComputedStyle(owner).flexShrink, getComputedStyle(owner).whiteSpace]).toEqual(['0', 'nowrap']);
      // a long stage's name is cut short within the head; its button names it whole
      const stage = screen.getByRole('button', { name: 'Stage: Review' });
      expect(getComputedStyle(stage).maxWidth).toBe('100%');
      expect(getComputedStyle(within(stage).getByText('Review')).textOverflow).toBe('ellipsis');
      expect(getComputedStyle(within(owner).getByText('Engineering Manager')).textOverflow).toBe('ellipsis');
      if (layout !== 'cards') return;
      // a press, var(--ctl) high and centered on its 22 high row, reaches (--ctl - 22) / 2 past it each way: rows that far
      // apart never share one (read from the sheet: jsdom computes no var() in a gap); the wait's link has none
      const css = fs.readFileSync(new URL('./task.css', here), 'utf8');
      expect(css).toContain(':where(html[data-layout="cards"]) .tv .pk-where :is(a,.tv-pick)::after{content:"";position:absolute;top:50%;right:0;left:0;height:var(--ctl);');
      expect(css).toContain(':where(html[data-layout="cards"]) .tv .pk-where{row-gap:calc(var(--ctl) - 22px)}');
      expect(css.indexOf('.tv .pk-where{row-gap:calc(var(--ctl) - 22px)}')).toBeGreaterThan(css.indexOf('.tv .pk-where{flex-wrap:wrap;height:auto;row-gap:var(--s-1)}'));
      expect(getComputedStyle(link, '::after').content).toBe('none');
    } finally {
      style.remove();
      delete document.documentElement.dataset.layout;
    }
  });

  it('draws Details: what it needs to leave its stage, with what is met folded @s58', async () => {
    serve(base);
    details();
    expect(await screen.findByRole('heading', { name: /To leave Review/ })).toBeTruthy();
    expect(screen.getByText('QA report approved')).toBeTruthy();
    expect(screen.queryByText('Spec approved')).toBeNull();
  });

  it('the due date is a Details fact even before one is set', async () => {
    serve(base);
    details();
    await waitFor(() => expect(document.querySelector('[data-target="task-due:t1"]')).toBeTruthy());
    const due = document.querySelector('[data-target="task-due:t1"]') as HTMLElement;
    expect(within(due).getByRole('button', { name: 'None' })).toBeTruthy();
  });

  it('uses one pipeline list and reads no Needs you list', async () => {
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

  it('shows an approval at once and takes it back with the server’s reason when refused @s109', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'POST' && url.includes('/asks/r1/answer'))
        return new Promise<Reply>(
          (r) => (refuse = () => r({ status: 409, body: { error: { code: 'task_stage_rule', detail: 'The QA report has no checks yet', missing: [] } } })),
        );
      return base(url);
    });
    show(<TaskPage task="12" />);
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

  it('warms the agent’s session a link opens on, not the comments', async () => {
    serve((url) => {
      if (url.endsWith('/tasks/%2312')) return { status: 200, body: detail(task({ conversation_id: 'room', documents: [] })) };
      if (url.endsWith('/api/v2/threads/room')) return { status: 200, body: { id: 'room', task_id: 't1' } };
      return base(url);
    });
    await loadTask(queryClient, '12', 'details', 'bot:r2');
    await waitFor(() => expect(calls.filter((c) => c.startsWith('GET /api/v2/threads/room/timeline'))).toHaveLength(1));
    const read = calls.find((c) => c.startsWith('GET /api/v2/threads/room/timeline'))!;
    expect(read).toContain('session=bot%3Ar2');
    expect(read).not.toContain('comments=true');
  });

  it('an old link to an agent’s session as the page: the one chat beside the task heads it, a way back, the decision over its box; the page a tab of its own', async () => {
    people([{ id: 'bot:r2', kind: 'agent', name: 'Rowan Reviewer' }]);
    serve((url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
        return { status: 200, body: { ...detail(task({ conversation_id: 'room' } as Partial<TaskData>)), can_direct: true, sessions: [{ agent: 'bot:r2', last_seq: 3 }] } };
      if (url.endsWith('/api/v2/threads/room')) return { status: 200, body: { id: 'room', task_id: 't1' } };
      if (url.includes('/threads/room/timeline')) return { status: 200, body: { messages: [], next_cursor: null, read_marks: {} } };
      return base(url);
    });
    const router = show(
      <>
        <TaskChat task="12" />
        <TaskPage task="12" tab="chat" />
      </>,
      '/tasks/p1/12/chat?session=bot%3Ar2',
    );
    expect(await screen.findByRole('link', { name: 'Rowan Reviewer' })).toBeTruthy();
    expect(await screen.findByRole('textbox', { name: 'Message Rowan Reviewer' })).toBeTruthy();
    // never two conversations: the page names a tab of its own, the session kept for the chat beside it
    await waitFor(() => expect(router.state.location.search).toMatchObject({ task: 12, 'task.tab': 'qa-report', 'task.session': 'bot:r2' }));
    expect(screen.getAllByRole('textbox', { name: 'Message Rowan Reviewer' })).toHaveLength(1);
    // the decision over the chat's box, once (the page's dock is a phone's)
    await screen.findAllByRole('region', { name: 'The decision' });
    const boxes = screen.getAllByRole('region', { name: 'The decision' }).filter((b) => !b.closest('.dock'));
    expect(boxes).toHaveLength(1);
    expect(within(boxes[0]!).getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(calls.some((c) => c.includes('/threads/room/timeline') && c.includes('session=bot%3Ar2'))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Back to comments' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ task: 12, list: 'p1', 'task.tab': 'qa-report' }));
    expect(await screen.findByRole('textbox', { name: 'Add a comment' })).toBeTruthy();
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
        ? { status: 200, body: detail(task({ owner_id: 'bot:a1', turn: { state: 'person', who: 'human:me', line: '\u2753 Which headline goes live?', ask: true } } as Partial<TaskData>)) }
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
    show(<TaskPage task="12" tab="details" file="f6" />, '/tasks/p1/12/details?file=f6');
    expect(await screen.findByText('Ship on Friday after the review.')).toBeTruthy();
    expect(document.querySelector('.frame.fv iframe')).toBeNull();
  });

  it('says when the task is gone', async () => {
    serve(() => ({ status: 404, body: { error: { code: 'not_found', detail: 'No such task' } } }));
    show(<TaskPage task="99" />, '/tasks/p1/99');
    expect(await screen.findByRole('heading', { name: 'This task is gone' })).toBeTruthy();
  });

  it('shows nothing of a task the person may not see', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden', detail: 'no' } } }));
    show(<TaskPage task="12" />);
    expect(await screen.findByRole('heading', { name: "You don't have access to this page" })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
  });

  it('says why when the read fails; a server error is read again by itself, so no Try again', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'oops', detail: 'down' } } }));
    show(<TaskPage task="12" />);
    // The read is tried once more (after a second) before the fault shows.
    expect(await screen.findByRole('heading', { name: "Task didn't load · server error" }, { timeout: 4000 })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it.each([
    [400, 'bad', 'request refused'],
    [503, 'stopped', 'company stopped'],
  ])('a %i (%s) that will only fail again is not read again by itself: Try again is the way', async (status, code, reason) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serve(() => ({ status, body: { error: { code, detail: 'no', retryable: false } } }));
      show(<TaskPage task="12" />);
      expect(await screen.findByRole('heading', { name: `Task didn't load · ${reason}` }, { timeout: 4000 })).toBeTruthy();
      const sentBefore = calls.filter((c) => c.startsWith('GET /api/v2/tasks/')).length;
      await act(() => vi.advanceTimersByTimeAsync(15_000));
      expect(calls.filter((c) => c.startsWith('GET /api/v2/tasks/'))).toHaveLength(sentBefore);
      expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('a task on screen whose read turns refused hides, says so, and is not read again every few minutes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let refuse = false;
      serve((url) => (refuse && url.endsWith('/api/v2/tasks/t1') ? { status: 403, body: { error: { code: 'forbidden', detail: 'no' } } } : undefined));
      show(<TaskPage task="12" />);
      await screen.findByRole('heading', { name: 'Fix the sign-in page' });
      refuse = true;
      const reads = () => calls.filter((c) => c.startsWith('GET ') && c.endsWith('/api/v2/tasks/t1')).length;
      const before = reads();
      // the backstop reads it once, and is refused
      await act(() => vi.advanceTimersByTimeAsync(5 * 60_000 + 1_000));
      await waitFor(() => expect(reads()).toBe(before + 1));
      // the refusal takes the task's place
      expect(await screen.findByRole('heading', { name: "You don't have access to this page" })).toBeTruthy();
      expect(screen.queryByRole('heading', { name: 'Fix the sign-in page' })).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(15 * 60_000));
      expect(reads()).toBe(before + 1);
    } finally {
      vi.useRealTimers();
    }
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
    show(<TaskPage task="12" />, '/tasks/p1/12');
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
    show(<TaskPage task="12" />, '/tasks/p1/12');
    expect((await frame('Spec')).getAttribute('srcdoc')).toContain('Sign in with your email or phone.');
  });

  it('opens the version the address names even when the list does not reach it @approvals', async () => {
    const t = withVersions();
    (t.documents![0] as { versions: unknown }).versions = versions.slice(1);
    serve((url) => (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(t) } : (html(url) ?? base(url))));
    show(<TaskPage task="12" tab="spec" />, '/tasks/p1/12/spec?v=1');
    expect((await frame('Spec, v1')).getAttribute('srcdoc')).toContain('Sign in with your email.');
  });

  it('a version whose read was refused says why, with Try again', async () => {
    serve((url) =>
      url.endsWith('/api/v2/files/f1?v=1') ? { status: 403, body: { error: { code: 'forbidden', detail: 'No.' } } } : url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(withVersions()) } : (html(url) ?? base(url)),
    );
    show(<TaskPage task="12" tab="spec" />, '/tasks/p1/12/spec?v=1');
    expect(await screen.findByRole('heading', { name: "Spec didn't load · no access" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
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
    show(<TaskPage task="12" />, '/tasks/p1/12');
    expect((await frame('Plan')).getAttribute('srcdoc')).toContain('The plan as it was.');
  });

  it('with a version pinned, picking the newest names it, so it does not snap back to the pin @approvals', async () => {
    pinnedAt(1, { src: 'about:blank', version: 2 });
    show(<TaskPage task="12" />, '/tasks/p1/12');
    fireEvent.click(await screen.findByRole('button', { name: 'Version 1' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v2/ }));
    expect(await screen.findByRole('button', { name: 'Version 2' })).toBeTruthy();
    expect((await frame('Spec')).getAttribute('src')).toBe('about:blank');
  });

  it('opens an older version and marks what changed since the one before @s55', async () => {
    serve((url) => (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1') ? { status: 200, body: detail(withVersions()) } : (html(url) ?? base(url))));
    show(<TaskPage task="12" tab="spec" />, '/tasks/p1/12/spec');
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
    show(<TaskPage task="12" tab="spec" />, '/tasks/p1/12/spec?v=2');
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
    const router = show(<TaskPage task="12" tab="spec" />, '/tasks/p1/12/spec?vs=1');
    const changes = await screen.findByRole('button', { name: 'No changes' });
    expect(changes.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(changes);
    await waitFor(() => expect(Object.keys(router.state.location.search)).not.toContain('task.vs'));
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
    show(<TaskPage task="12" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
    fireEvent.click(await screen.findByRole('button', { name: 'Sign-in screen, full size' }));
    expect(await screen.findByRole('dialog', { name: 'Sign-in screen' })).toBeTruthy();
  });
});

describe('the task’s Snooze and Close', () => {
  it('snoozes from the head in one press, busy until the server answers', async () => {
    const reply = holdReply();
    serve((url) => (url.endsWith('/snooze') ? reply.promise : undefined));
    details();
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    fireEvent.click(screen.getByRole('button', { name: 'Snooze' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Tomorrow' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/snooze'));
    // snoozed at once, and Unsnooze waits on the server's answer
    const unsnooze = await screen.findByRole('button', { name: 'Unsnooze' });
    expect(unsnooze.getAttribute('aria-busy')).toBe('true');
    await act(async () => reply.resolve({ status: 200, body: { task: task({ snoozed: true } as Partial<TaskData>) } }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unsnooze' }).getAttribute('aria-busy')).toBeNull());
  });

  it('closes from the head, busy until the server answers', async () => {
    const reply = holdReply();
    serve((url) => (url.endsWith('/move') ? reply.promise : undefined));
    details();
    const close = await screen.findByRole('button', { name: 'Close' });
    fireEvent.click(close);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to: 'closed' } }));
    expect(close.getAttribute('aria-busy')).toBe('true');
    await act(async () => reply.resolve({ status: 200, body: { task: task() } }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' }).getAttribute('aria-busy')).toBeNull());
  });

  it('closes from ⋯, beside Mark done', async () => {
    serve((url) => (url.endsWith('/move') ? { status: 200, body: { task: task() } } : undefined));
    details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Close' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/tasks/t1/move', body: { to: 'closed' } }));
  });

  it('a finished task has neither', async () => {
    serve(reads(task({ status: 'done' } as Partial<TaskData>)));
    details();
    await screen.findByRole('heading', { level: 1, name: 'Fix the sign-in page' });
    expect(screen.queryByRole('button', { name: 'Snooze' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
  });
});

describe('the task’s More menu', () => {
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

  it('a refused delete says the server’s reason, never a bare Not saved', async () => {
    const why = 'Only a person, signed in as themselves, deletes a task; close it instead';
    serve((url) =>
      url.endsWith('/delete')
        ? { status: 403, body: { error: { code: 'forbidden', detail: why } } }
        : undefined,
    );
    details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const ask = await screen.findByRole('alertdialog');
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    expect(await within(ask).findByText(why)).toBeTruthy();
    expect(within(ask).queryByText('Not saved')).toBeNull();
  });

  it('a delete the server failed on says so, not Not saved', async () => {
    serve((url) => (url.endsWith('/delete') ? { status: 500, body: 'Internal Server Error' } : undefined));
    details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const ask = await screen.findByRole('alertdialog');
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }));
    expect(await within(ask).findByText('Something went wrong. Try again.', {}, { timeout: 5000 })).toBeTruthy();
  });

  it('deleted once another task is open in its place: that one stays open', async () => {
    const reply = holdReply();
    serve((url) => (url.endsWith('/delete') ? reply.promise : undefined));
    const router = details();
    await more();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/delete'));
    // closed, and another opened, while the delete is sent
    await act(() => router.navigate({ to: '/tasks/p1', search: { task: 13, list: 'p1' } } as never));
    reply.resolve({ status: 200, body: { deleted: true } });
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(router.state.location.search).toMatchObject({ task: 13 });
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

  it('moves the task to another pipeline and copies its link, staying on it @s58', async () => {
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
    // the task stays open where it is: its new pipeline is its own business
    expect(router.state.location.search).toMatchObject({ task: 12 });
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

  /** Now's Waiting on row, once the task is drawn. */
  const waitingOn = async () => (await screen.findByText('Waiting on')).parentElement as HTMLElement;

  it('names a task it waits on by its line, linked to it, never by its id', async () => {
    const id = '3f9c1d2e-7b4a-4c1e-9d2f-0a1b2c3d4e5f';
    const blocker = { id, number: 130, title: 'Matches cause check', status: 'open', direction: 'out', owner_id: 'bot:a1' };
    serve(reads(task({ relations: { depends_on: [blocker] }, turn: { state: 'tasks', who: id, line: 'Blocked by Matches cause check, in To do', requirement_id: 'dep:x', since: null, until: null, ask: false } } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(row.textContent).not.toContain(id);
    expect(within(row).getByRole('link', { name: 'Blocked by Matches cause check, in To do' }).getAttribute('href')).toBe(`/task/${id}`);
    await facts();
    expect(((await screen.findByText('Waiting')).parentElement as HTMLElement).textContent).not.toContain(id);
  });

  it('says a task it waits on that the reader may not open only by its line, without a link', async () => {
    serve(reads(task({ turn: { state: 'tasks', who: null, line: 'A sub-task', requirement_id: 'sub:x', since: null, until: null, ask: false } } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(row.textContent).toBe('Waiting onA sub-task');
    expect(within(row).queryByRole('link')).toBeNull();
  });

  it('does not link a task it waits on that the reader may not open, and keeps its line whole', async () => {
    serve(reads(task({ turn: { state: 'tasks', who: 'private-1', line: '\u2753 Which copy wins?', requirement_id: 'dep:x', since: null, until: null, ask: false } } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(row.textContent).toBe('Waiting on\u2753 Which copy wins?');
    expect(within(row).queryByRole('link')).toBeNull();
  });

  it('keeps a snooze reason whole and says until when', async () => {
    serve(reads(task({ turn: { state: 'snoozed', who: 'human:bo', line: '\u2753 After the launch', requirement_id: null, since: null, until: '2026-12-01T12:00:00Z', ask: false } } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(row.textContent).toMatch(/^Waiting on\u2753 After the launch · until /);
    expect(row.querySelector('time')?.getAttribute('datetime')).toBe('2026-12-01T12:00:00.000Z');
  });

  it('keeps the row and its Fix for an agent with the task whose cloud computer failed to start', async () => {
    const turn = { state: 'bot', who: 'bot:a1', line: 'Its cloud computer did not start', requirement_id: null, since: null, until: null, ask: false, href: '#/settings/computers' };
    serve(reads(task({ owner_id: 'bot:a1', status_line: 'paused', turn } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(within(row).getByText('Its cloud computer did not start')).toBeTruthy();
    expect(within(row).getByRole('link', { name: 'Fix' }).getAttribute('href')).toBe('/settings/computers');
  });

  it('keeps the row for the person who has the task while they cannot see it', async () => {
    const turn = { state: 'person', who: 'human:bo', line: "Bo Lee can't see this task", requirement_id: null, since: null, until: null, ask: false, share: ['human:bo'] };
    serve(reads(task({ owner_id: 'human:bo', turn } as Partial<TaskData>)));
    details();
    expect(within(await waitingOn()).getByText("Bo Lee can't see this task")).toBeTruthy();
  });

  it('says On ice for a snooze with no reason and no date, not who snoozed it', async () => {
    serve(reads(task({ turn: { state: 'snoozed', who: 'human:bo', line: '', requirement_id: null, since: null, until: null, ask: false } } as Partial<TaskData>)));
    details();
    expect((await waitingOn()).textContent).toBe('Waiting onOn ice');
  });

  it('keeps the row for a Paused agent that has the task, with its Fix', async () => {
    const turn = { state: 'paused', who: 'bot:a1', line: 'Builder has no computer', requirement_id: null, since: null, until: null, ask: false, href: '#/settings/computers' };
    serve(reads(task({ owner_id: 'bot:a1', turn } as Partial<TaskData>)));
    details();
    const row = await waitingOn();
    expect(within(row).getByText('Builder')).toBeTruthy();
    expect(within(row).getByText('Builder has no computer')).toBeTruthy();
    expect(within(row).getByRole('link', { name: 'Fix' }).getAttribute('href')).toBe('/settings/computers');
  });

  it('leaves out the row when it only repeats who has the task, and draws no face for a requirement on GitHub', async () => {
    const to_leave = [{ requirement_id: 'ci', line: 'Checks pass', met: false, may: [], who: 'github' }];
    serve(reads(task({ owner_id: 'bot:a1', to_leave, turn: { state: 'bot', who: 'bot:a1', line: 'Draft the post', requirement_id: null, since: null, until: null, ask: false } } as unknown as Partial<TaskData>)));
    details();
    const line = (await screen.findByText('Checks pass')).parentElement as HTMLElement;
    expect(within(line).queryByRole('img')).toBeNull();
    expect(screen.queryByText('Waiting on')).toBeNull();
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

  it('has no Follow, and puts someone in a role', async () => {
    // an older server still answers with followers: the facts show none of them
    serve(reads(task({ followers: [{ actor: 'human:bo', notify: 'live' }] } as Partial<TaskData>)));
    details();
    await facts();
    expect(await screen.findByText('Assignee')).toBeTruthy();
    expect(screen.queryByText('Followers')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Follow' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Add someone' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add someone' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Role' }), { target: { value: 'QA Lead' } });
    expect((within(dialog).getByRole('button', { name: 'Add' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('always shows the Assignee, and a person assignee among who sees a private task', async () => {
    serve(reads(task({ owner_id: 'bot:a1', assignee_id: 'human:bo', private: true, readers: ['human:bo'] } as Partial<TaskData>)));
    details();
    await facts();
    const assignee = (await screen.findByText('Assignee')).parentElement as HTMLElement;
    expect(assignee.textContent).toContain('Bo Lee');
    // Bo reads it as its assignee: a fixed face, not a reader to take off
    const visible = (screen.getByText('Visible to').closest('.dt-kv') ?? document.body) as HTMLElement;
    expect(within(visible).queryByRole('button', { name: 'Bo Lee' })).toBeNull();
    expect(within(visible).getAllByText('BL').length).toBeGreaterThan(0);
  });

  it('shows Assignee as None when nobody is assigned', async () => {
    serve(reads(task({ assignee_id: null } as Partial<TaskData>)));
    details();
    await facts();
    expect(((await screen.findByText('Assignee')).parentElement as HTMLElement).textContent).toBe('AssigneeNone');
  });

  it('lets someone see a private task, keeping both names of its reader list', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/readers')
        ? { status: 200, body: { private: true, readers: ['human:bo'], shared_with: ['human:bo'] } }
        : reads(task({ private: true, readers: [], shared_with: [] } as Partial<TaskData>))(url),
    );
    details();
    await facts();
    expect(await run('task.setReaders', { task: 't1', add: ['human:bo'], remove: [] }, { via: 'click' })).toMatchObject({ ok: true });
    const t = queryClient.getQueryData<TaskData>(keys.tasks.detail('t1'));
    expect(t?.readers).toEqual(['human:bo']);
    expect(t?.shared_with).toEqual(['human:bo']);
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
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.file']).toBe('f9'));
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
    const router = show(<TaskPage task="12" tab="details" file="f9" />, '/tasks/p1/12/details?file=f9');
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
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.file']).toBeUndefined());
  });

  it('puts its pictures and videos first, newest first, and opens one over the tab it is on', async () => {
    const at = (id: string, mime: string, created_at: string, more: Record<string, unknown> = {}) => ({
      n: 1, by: 'bot:a1', created_at, mime, url: `/api/v2/files/${id}?v=1`, answers: [], ask: null, poster_url: null, thumb_url: null, ...more,
    });
    const cut = { ...shot, id: 'v1', name: 'cut.mp4', title: 'Beach cut', mime: 'video/mp4', current_version: 1, versions: [at('v1', 'video/mp4', '2026-11-03T10:00:00Z', { poster_url: '/api/v2/files/v1/poster?v=1' })] };
    const notes = { ...shot, id: 'n1', name: 'notes.txt', title: 'Shot list', mime: 'text/plain', current_version: 1, versions: [at('n1', 'text/plain', '2026-11-04T10:00:00Z')] };
    const old = { ...shot, id: 'p0', title: 'Old frame', archived: true };
    serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [notes, shot, old, cut] } } : files(url)));
    const router = details();
    const strip = await screen.findByRole('list', { name: 'Pictures and videos' });
    // newest first, pictures and videos only: the archived picture and the text file are not in it
    expect(within(strip).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Beach cut', 'Sign-in screen']);
    expect(strip.querySelector('img')?.getAttribute('src')).toBe('/api/v2/files/v1/poster?v=1');
    // a task may have many: each is read only as it nears the screen
    expect([...strip.querySelectorAll('img')].map((img) => img.getAttribute('loading'))).toEqual(['lazy', 'lazy']);
    // first: before the tab's own body; Details' Files keeps only what is not a picture or a video
    await waitFor(() => expect(document.querySelector('.dt-file[title="Shot list"]')).toBeTruthy());
    const rows = [...document.querySelectorAll('.dt-file')].map((row) => row.getAttribute('title'));
    expect(rows).toEqual(['Shot list']);
    expect(strip.compareDocumentPosition(screen.getByText('Has it')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // a video is not a tab of its own, as a picture is not: the strip opens it
    const tabs = screen.getByRole('navigation', { name: / pages$/ });
    expect(within(tabs).getByRole('link', { name: /Shot list/ })).toBeTruthy();
    expect(within(tabs).queryByRole('link', { name: /Beach cut/ })).toBeNull();
    fireEvent.click(within(strip).getByRole('button', { name: 'Beach cut' }));
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.file']).toBe('v1'));
  });

  it('opens a picture from the top over a page tab and leaves the page’s version and comparison as they were', async () => {
    serve(files);
    const router = show(<TaskPage task="12" tab="qa-report" />, '/tasks/p1/12/qa-report?v=1&vs=1');
    const strip = await screen.findByRole('list', { name: 'Pictures and videos' });
    fireEvent.click(within(strip).getByRole('button', { name: 'Sign-in screen' }));
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.file']).toBe('f9'));
    expect(router.state.location.search).toMatchObject({ 'task.tab': 'qa-report', 'task.v': 1, 'task.vs': 1 });
  });

  it('a file over a page tab: its versions are its own, Next steps in place, and Back returns to the page as it was', async () => {
    const notes = {
      ...shot,
      id: 'n1',
      name: 'notes.txt',
      title: 'Shot list',
      mime: 'text/plain',
      current_version: 1,
      versions: [{ n: 1, by: 'bot:a1', created_at: '2026-11-04T10:00:00Z', mime: 'text/plain', url: '/api/v2/files/n1?v=1', answers: [], ask: null }],
    };
    serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [shot, notes] } } : files(url)));
    const router = show(<TaskPage task="12" tab="qa-report" file="f9" />, '/tasks/p1?task=12&list=p1&task.tab=qa-report&task.file=f9&task.v=1');
    const words = () => router.state.location.search as Record<string, unknown>;
    // named for the tab under it, never Details
    const back = await screen.findByRole('button', { name: 'Back to QA report' });
    expect(screen.queryByRole('button', { name: 'Back to Details' })).toBeNull();
    // the file's version and comparison are its own words: the page's v stays, and it gets no vs
    await lastMore();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Versions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'v1' }));
    await waitFor(() => expect(words()['task.fv']).toBe(1));
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Compare' }));
    await waitFor(() => expect(words()['task.fvs']).toBe(2));
    expect(words()).toMatchObject({ 'task.tab': 'qa-report', 'task.v': 1 });
    expect(words()['task.vs']).toBeUndefined();
    // Next: the next file in its place, over the same tab, no step in the history
    const steps = router.history.length;
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(words()['task.file']).toBe('n1'));
    expect(words()).toMatchObject({ 'task.tab': 'qa-report', 'task.v': 1 });
    expect(words()['task.fv']).toBeUndefined();
    expect(router.history.length).toBe(steps);
    // Back: the page as it was read
    fireEvent.click(back);
    await waitFor(() => expect(words()['task.file']).toBeUndefined());
    expect(words()).toMatchObject({ 'task.tab': 'qa-report', 'task.v': 1 });
    expect(router.history.length).toBe(steps);
  });

  it('Remove from a file over a page tab goes back to that tab', async () => {
    serve(files);
    const router = show(<TaskPage task="12" tab="qa-report" file="f9" />, '/tasks/p1/12/qa-report?file=f9');
    await screen.findByRole('button', { name: 'Back to QA report' });
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove file' }));
    await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.file']).toBeUndefined());
    expect(router.state.location.search).toMatchObject({ 'task.tab': 'qa-report' });
  });

  it('an old link to a video’s own tab opens that video over the page, at the version it named', async () => {
    const at = (n: number) => ({ n, by: 'bot:a1', created_at: `2026-11-0${n}T10:00:00Z`, mime: 'video/mp4', url: `/api/v2/files/v1?v=${n}`, answers: [], ask: null, poster_url: null });
    const cut = { ...shot, id: 'v1', name: 'beach-cut.mp4', title: '', mime: 'video/mp4', current_version: 2, versions: [at(1), at(2)] };
    serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [cut] } } : files(url)));
    const router = show(<TaskPage task="12" tab="beach-cut-mp4" />, '/tasks/p1/12/beach-cut-mp4?v=1');
    await waitFor(() => expect(router.state.location.search).toMatchObject({ 'task.tab': 'qa-report', 'task.file': 'v1', 'task.fv': 1 }));
    expect((router.state.location.search as Record<string, unknown>)['task.v']).toBeUndefined();
  });

  it('names the tab of a file opened with no tab, leaving the page’s version the page’s and the file at its newest', async () => {
    serve(files);
    const router = show(<TaskPage task="12" file="f9" />, '/tasks/p1?task=12&list=p1&task.file=f9&task.v=1');
    await waitFor(() => expect(router.state.location.search).toMatchObject({ 'task.tab': 'qa-report', 'task.file': 'f9', 'task.v': 1 }));
    expect((router.state.location.search as Record<string, unknown>)['task.fv']).toBeUndefined();
    expect(await screen.findByRole('button', { name: 'Sign-in screen, full size' })).toBeTruthy();
    expect(screen.queryByAltText('Sign-in screen, v1')).toBeNull();
  });

  describe('an open file at a version past its listing (it keeps only the newest)', () => {
    const newest = { ...shot, versions: shot.versions.slice(1) };
    const at = '/tasks/p1?task=12&list=p1&task.tab=qa-report&task.file=f9&task.fv=1';
    const serveMeta = (meta: Reply) =>
      serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [newest] } } : url.endsWith('/api/v2/files/f9/meta?v=1') ? meta : files(url)));

    it('is read by the file’s id, never shown at its current version', async () => {
      serveMeta({ status: 200, body: { name: 'shot-old.png', content_type: 'image/png', size: 10 } });
      const router = show(<TaskPage task="12" tab="qa-report" file="f9" />, at);
      expect((await screen.findByAltText('Sign-in screen, v1')).getAttribute('src')).toBe('/api/v2/files/f9?v=1');
      expect(screen.queryByRole('button', { name: 'Sign-in screen, full size' })).toBeNull();
      expect((router.state.location.search as Record<string, unknown>)['task.fv']).toBe(1);
    });

    it('says it did not load when that read fails, never showing the current one', async () => {
      serveMeta({ status: 404, body: { error: 'not_found' } });
      show(<TaskPage task="12" tab="qa-report" file="f9" />, at);
      expect(await screen.findByText("This file didn't load · not found")).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Sign-in screen, full size' })).toBeNull();
    });
  });

  describe('an old link to a tab a video had', () => {
    /** A file of the task as the server lists it, made at `day`. */
    const made = (id: string, title: string, mime: string, day: number) => ({
      ...shot,
      id,
      name: `${id}.${mime.split('/')[1]}`,
      title,
      mime,
      current_version: 1,
      versions: [{ n: 1, by: 'bot:a1', created_at: `2026-11-0${day}T10:00:00Z`, mime, url: `/api/v2/files/${id}?v=1`, answers: [], ask: null, poster_url: null }],
    });
    /** The task, its files in the server's order, opened at the old address `at`: the words it lands on. */
    const land = async (list: unknown[], at: string) => {
      serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: list } } : files(url)));
      const slug = at.split('?')[0]!.split('/').at(-1)!;
      const router = show(<TaskPage task="12" tab={slug} />, `/tasks/p1/12/${at}`);
      await waitFor(() => expect((router.state.location.search as Record<string, unknown>)['task.tab']).not.toBe(slug));
      return router.state.location.search as Record<string, unknown>;
    };
    // Two videos named alike had beach-cut and beach-cut-2, in the server's order: the newer one is second.
    const twins = [made('v1', 'Beach cut', 'video/mp4', 1), made('v2', 'Beach cut', 'video/mp4', 3)];

    it('opens the video a -2 slug named', async () => {
      expect(await land(twins, 'beach-cut-2?v=1')).toMatchObject({ 'task.tab': 'qa-report', 'task.file': 'v2', 'task.fv': 1 });
    });

    it('opens the first of two videos named alike, never the newer one', async () => {
      expect(await land(twins, 'beach-cut')).toMatchObject({ 'task.tab': 'qa-report', 'task.file': 'v1' });
    });

    it('opens the video, never the PDF of its name that took the slug after it', async () => {
      const list = [made('v1', 'Promo', 'video/mp4', 1), made('d1', 'Promo', 'application/pdf', 2)];
      expect(await land(list, 'promo')).toMatchObject({ 'task.tab': 'qa-report', 'task.file': 'v1' });
      // the PDF keeps the slug it had beside the video
      expect(within(screen.getByRole('navigation', { name: / pages$/ })).getByRole('link', { name: /Promo/ }).getAttribute('href')).toContain('promo-2');
    });

    it('opens nothing for a picture’s name: a picture never had a tab', async () => {
      const words = await land([shot], 'sign-in-screen');
      expect(words['task.tab']).toBe('qa-report');
      expect(words['task.file']).toBeUndefined();
    });
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
      const router = show(<TaskPage task="12" />);
      expect(await screen.findByRole('link', { name: 'Letter' })).toBeTruthy();
      expect(screen.queryByRole('link', { name: 'Letter draft' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Archive (1)' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Letter draft' }));
      await waitFor(() => expect(router.state.location.search).toMatchObject({ task: 12, 'task.tab': 'details', 'task.file': 'f7' }));
    });

    it('open and restore from their own ⋯', async () => {
      serve(withDraft);
      show(<TaskPage task="12" tab="details" file="f7" />, '/tasks/p1/12/details?file=f7');
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
      show(<TaskPage task="12" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
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
      show(<TaskPage task="12" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
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
      show(<TaskPage task="12" tab="details" file="f5" />, '/tasks/p1/12/details?file=f5');
      await waitFor(() => expect(calls).toContain('GET /api/v2/files/f5/versions/1'));
      await lastMore();
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Restore' }));
      await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/files/f5', method: 'PATCH', body: { archived: false } }));
      expect(sent.at(-2)).toEqual({ url: '/api/v2/pages/f5', method: 'PATCH', body: { archived: false } });
    });

    it('open an archived picture without a full size view, and offer Restore only to whoever may', async () => {
      serve((url) => (url.endsWith('/api/v2/tasks/t1/files?all=1') ? { status: 200, body: { files: [{ ...shot, archived: true, can_restore: false }] } } : files(url)));
      show(<TaskPage task="12" tab="details" file="f9" />, '/tasks/p1/12/details?file=f9');
      expect(await screen.findByAltText('Sign-in screen, v2')).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Sign-in screen, full size' })).toBeNull();
      await lastMore();
      expect(await screen.findByRole('menuitem', { name: 'Download' })).toBeTruthy();
      expect(screen.queryByRole('menuitem', { name: 'Restore' })).toBeNull();
    });
  });

  it('a page’s ⋯ archives the page and goes back to Details', async () => {
    serve((url, method) => (method === 'PATCH' && url.endsWith('/api/v2/pages/f2') ? { status: 200, body: { page: { ...page, archived: true } } } : files(url)));
    const router = show(<TaskPage task="12" />);
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/pages/f2', method: 'PATCH', body: { archived: true } }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ task: 12, 'task.tab': 'details' }));
  });

  it('a page’s ⋯ archive answered once another task is open in its place: that one keeps its tab', async () => {
    const reply = holdReply();
    serve((url, method) => (method === 'PATCH' && url.endsWith('/api/v2/pages/f2') ? reply.promise : files(url)));
    const router = show(<TaskPage task="12" />);
    await screen.findByTitle('QA report');
    await lastMore();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/pages/f2'));
    await act(() => router.navigate({ to: '/tasks/p1', search: { task: 13, 'task.tab': 'chat' } } as never));
    reply.resolve({ status: 200, body: { page: { ...page, archived: true } } });
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(router.state.location.search).toMatchObject({ task: 13, 'task.tab': 'chat' });
  });

  it('a page’s ⋯ deletes the page for good only after asking', async () => {
    serve(files);
    show(<TaskPage task="12" />);
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
    show(<TaskPage task="12" />);
    await screen.findByTitle('QA report');
    const tico = { via: 'tico' } as const;
    expect((await run('task.upload', { task: 't1', file: new File(['hi'], 'a.txt') }, tico)).ok).toBe(false);
    expect((await run('task.deletePage', { task: 't1', page: 'f2' }, tico)).ok).toBe(false);
    // Showing a task to the whole team is the person's own press too.
    expect((await run('task.setVisible', { task: 't1', to: 'team' }, tico)).ok).toBe(false);
    expect(sent).toEqual([]);
  });
});
