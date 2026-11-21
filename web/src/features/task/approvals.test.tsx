import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import type * as conversations from '@/conversation';
import { sendMessage } from '@/conversation';
import { keys, queryClient } from '@/data';
import { matterWhen } from '@/features/company/exports';
import { Toasts } from '@/kit/Toast';
import { forgetPlaces, notePlace } from '@/lib';
import { useFollow } from '@/live/follow';
import { fakePdf } from '@/test/fakePdf';
import { decisionName, effectOf, sendTitle } from './model';
import { taskFilesQuery, type TaskData } from './queries';
import { TaskChat, TaskPage } from './TaskPage';

/* The decision (plan/approvals.html): the thing asked about is the first tab, one box decides it, Approve is bound to
   what was shown, Request changes is a message to the agent. Made-up rows only; every answer is stubbed. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
// PDF.js has no canvas or worker here: a PDF reads as one page (test/fakePdf.ts).
// Each PDF read, by its address: which version a tab shows.
const pdfRead = vi.hoisted(() => [] as string[]);
vi.mock('@/features/library/pdf', () => ({ openPdf: vi.fn((src: string) => (pdfRead.push(src), Promise.resolve(fakePdf(1).open))) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
// The conversation is tested in its own folder: here a stand-in with its foot and, while it may post, its message box.
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<Conversations>()),
  Conversation: ({ foot, canPost = true, title }: { foot?: ReactNode; canPost?: boolean; title?: string }) => (
    <section aria-label={`Chat with ${title}`}>
      {foot}
      {canPost && <textarea aria-label={`Message ${title}`} />}
    </section>
  ),
  sendMessage: vi.fn(async () => null),
}));

type Conversations = typeof conversations;
type Reply = { status?: number; body: unknown; text?: string };
const sent: { url: string; body: Record<string, unknown> }[] = [];
/** The Idempotency-Key of each write, in order. */
const keysSent: (string | null)[] = [];
let reply: (url: string, method: string) => Reply | Promise<Reply> | null = () => null;

const req = (more: Partial<TaskRequirement> = {}) =>
  ({ requirement_id: 'r1', kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: ['email:s1'], asked_by: 'bot:lo', answer: null, standing: 'waiting', who: 'human:me', ...more }) as TaskRequirement;

const task = (more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    version: 3,
    title: 'Send: Reply to the late-delivery claim',
    status: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Legal' },
    stage_id: 's2',
    stage: { id: 's2', name: 'Review' },
    next: { id: 's3', name: 'Done' },
    owner_id: 'bot:lo',
    conversation_id: 'c7',
    labels: [],
    created_at: '2026-11-01T10:00:00Z',
    documents: [],
    to_leave: [req()],
    links: [],
    ...more,
  }) as unknown as TaskData;

let shown = task();
const mail = {
  send: { id: 's1', subject: 'Re: Notice of claim', mailbox: 'legal@example.test', recipients: ['Dana Ruiz <dana@example.test>'], attachments: [{ part: 2, name: 'letter.pdf', size: 4096, type: 'application/pdf' }] },
  body: 'Dear Ms. Ruiz, our response is attached.',
  bodies: [],
  preview_complete: true,
  shown_hash: 'h-mail',
};

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { body: { task: shown, runs: [], children: [], can_comment: true, mover: true } };
  if (url.endsWith('/files?all=1')) return { body: { files: [] } };
  if (url.endsWith('/api/v2/email/sends/s1')) return { body: mail };
  if (url.endsWith('/api/v2/oks/r1')) return { body: { requirement_id: 'r1', subject: 'email_send:s1', content: {}, shown_hash: 'h-grant', standing: 'waiting', task_id: 't1' } };
  if (url.includes('/needs-you')) return { body: { items: [{ task: { id: 't1', number: 12, pipeline_id: 'p1' } }, { task: { id: 't9', number: 13, pipeline_id: 'p1' } }] } };
  if (url.includes('/api/v2/pipelines')) return { body: { pipelines: [] } };
  return { body: {} };
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  sent.length = 0;
  keysSent.length = 0;
  shown = task();
  reply = () => null;
  queryClient.clear();
  forgetPlaces();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:lo', kind: 'agent', name: 'Legal Owner' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
    if (method !== 'GET') keysSent.push(new Headers(init?.headers).get('Idempotency-Key'));
    const r = await (reply(String(url), method) ?? base(String(url)));
    if (r.text !== undefined) return new Response(r.text, { status: r.status ?? 200 });
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(sendMessage).mockClear();
});

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        <Toasts />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  // as the shell does: each address shown is noted, so closing a task goes back to where it was opened from
  const note = (l: typeof router.state.location) => notePlace(l.pathname, l.searchStr, l.search as Record<string, unknown>, l.state);
  note(router.state.location);
  router.subscribe('onResolved', (e) => note(e.toLocation));
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const decision = () => screen.findByRole('region', { name: 'The decision' });
const ready = async (box: HTMLElement) => {
  const approve = within(box).getByRole('button', { name: 'Approve' });
  await waitFor(() => expect(approve).toHaveProperty('disabled', false));
  return approve;
};

describe('the thing is the first tab', () => {
  it('a file it shows comes first, marked as waiting on the person @approvals', async () => {
    const brief = {
      id: 'f9',
      name: 'brief.pdf',
      title: 'Brief',
      mime: 'application/pdf',
      archived: false,
      current_version: 1,
      links: [],
      writers: [],
      versions: [{ n: 1, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: '/api/v2/files/f9?v=1', answers: [], ask: null }],
    };
    shown = task({ documents: [{ file_id: 'd1', name: 'Notes', kind: 'markdown', version: 1 }] as never, to_leave: [req({ shows: ['file:f9@1'], line: 'OK the brief?' })] });
    reply = (url) => (url.endsWith('/files?all=1') ? { body: { files: [brief] } } : url.endsWith('/oks/r1') ? { body: { subject: 'file:f9@1', shown_hash: 'h1', content: {} } } : null);
    const router = show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/brief'));
    const tabs = screen.getByRole('navigation', { name: 'Reply to the late-delivery claim pages' });
    const first = tabs.querySelector('.doc-tab') as HTMLElement;
    expect(first.textContent).toBe('Brief');
    expect(first.querySelector('.dot')).toBeTruthy();
  });

  it('a PDF to sign opens over the review page it also shows; the chat’s card carries the review pill @approvals', async () => {
    const pdf = {
      id: 'f9',
      name: 'declaration.pdf',
      title: 'declaration.pdf',
      mime: 'application/pdf',
      archived: false,
      current_version: 1,
      links: [],
      writers: [],
      versions: [{ n: 1, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: '/api/v2/files/f9?v=1', answers: [], ask: null }],
    };
    const review = { id: 'k1', title: 'Review: second opinion', status: 'open', owner_id: 'bot:lo', role: 'review' };
    shown = task({
      title: 'Search warrant',
      documents: [{ file_id: 'd1', name: 'Review', kind: 'page', version: 2 }] as never,
      to_leave: [req({ shows: ['file:f9@1', 'page:d1@2'], line: 'Sign the declaration' })],
    });
    reply = (url) =>
      url.endsWith('/files?all=1')
        ? { body: { files: [pdf] } }
        : url.endsWith('/api/v2/tasks/t1') || url.includes('/api/v2/tasks/%2312')
          ? { body: { task: shown, runs: [], children: [review], can_comment: true, mover: true } }
          : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12',
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/declaration-pdf'));
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    expect(within(box).getByText('Review running')).toBeTruthy();
    // the stream follows the review, so its end or reopening reaches the pill (live/patch.test.ts lands it)
    expect(vi.mocked(useFollow)).toHaveBeenCalledWith({ task: 'k1' });
  });

  it('shows the version the ask pinned, not a newer one, and Approve binds to what was shown @approvals', async () => {
    const at = (n: number) => ({ n, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: `/api/v2/files/f9?v=${n}`, answers: [], ask: null });
    const pdf = { id: 'f9', name: 'declaration.pdf', title: 'declaration.pdf', mime: 'application/pdf', archived: false, current_version: 2, links: [], writers: [], versions: [at(1), at(2)] };
    shown = task({ title: 'Search warrant', to_leave: [req({ shows: ['file:f9@1'], line: 'Sign the declaration' })] });
    reply = (url) =>
      url.endsWith('/files?all=1') ? { body: { files: [pdf] } } : url.endsWith('/oks/r1') ? { body: { subject: 'file:f9@1', shown_hash: 'h-v1', content: { file: 'f9', version: 1 } } } : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12',
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/declaration-pdf'));
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    expect(pdfRead.at(-1)).toBe('/api/v2/files/f9?v=1');
    // an older version is read, not signed
    expect(screen.queryByRole('button', { name: 'Sign' })).toBeNull();
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r1/ok', body: { shown_hash: 'h-v1' } }));
  });

  it('with an email OK first, a file OK keeps its tab at the version it pinned, its dot and Approve @approvals', async () => {
    const at = (n: number) => ({ n, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: `/api/v2/files/f9?v=${n}`, answers: [], ask: null });
    const pdf = { id: 'f9', name: 'declaration.pdf', title: 'declaration.pdf', mime: 'application/pdf', archived: false, current_version: 2, links: [], writers: [], versions: [at(1), at(2)] };
    shown = task({ title: 'Search warrant', to_leave: [req(), req({ requirement_id: 'r2', shows: ['file:f9@1'], line: 'Sign the declaration' })] });
    reply = (url) =>
      url.endsWith('/files?all=1') ? { body: { files: [pdf] } } : url.endsWith('/oks/r2') ? { body: { subject: 'file:f9@1', shown_hash: 'h-v1', content: { file: 'f9', version: 1 } } } : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12',
    );
    // the email's OK came first: its tab opens first, and the file's tab waits on the person too
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/email'));
    const tabs = screen.getByRole('navigation', { name: 'Search warrant pages' });
    expect((await within(tabs).findByRole('link', { name: /declaration/ })).querySelector('.dot')).toBeTruthy();
    cleanup();
    show(
      <>
        <TaskPage task="12" pipeline="p1" tab="declaration-pdf" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12/declaration-pdf',
    );
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    expect(pdfRead.at(-1)).toBe('/api/v2/files/f9?v=1'); // the version the ask pinned
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r2/ok', body: { shown_hash: 'h-v1' } }));
  });

  const twoPdfs = () => {
    const at = (id: string, n: number) => ({ n, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: `/api/v2/files/${id}?v=${n}`, answers: [], ask: null });
    const pdf = (id: string, name: string) => ({ id, name, title: name, mime: 'application/pdf', archived: false, current_version: 3, links: [], writers: [], versions: [at(id, 1), at(id, 2), at(id, 3)] });
    const asks = (met: boolean) => [req({ shows: ['file:f9@1'], line: 'Sign A' }), req({ requirement_id: 'r3', shows: ['file:f8@2'], line: 'Sign B', ...(met && { met: true, may: [] }) })];
    shown = task({ title: 'Two declarations', to_leave: asks(false) });
    return { files: [pdf('f9', 'a.pdf'), pdf('f8', 'b.pdf')], after: task({ title: 'Two declarations', to_leave: asks(true) }) };
  };

  it('the ask the address names (a card opened it) is on screen at its pinned version, its tab keeps it, Approve answers it, and the next ask opens after @approvals', async () => {
    const { files, after } = twoPdfs();
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r3/ok')
        ? { body: { task: after } }
        : url.endsWith('/files?all=1')
          ? { body: { files } }
          : url.endsWith('/oks/r3')
            ? { body: { subject: 'file:f8@2', shown_hash: 'h-b2', content: { file: 'f8', version: 2 } } }
            : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" tab="b-pdf" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12/b-pdf?ask=r3&chat=agent%3Alo&via=thread&vs=1',
    );
    expect(await screen.findByRole('region', { name: 'b.pdf' })).toBeTruthy();
    expect(pdfRead.at(-1)).toBe('/api/v2/files/f8?v=2'); // the version r3 pinned
    // its tab keeps the ask: chosen again, it is still r3
    const tabs = screen.getByRole('navigation', { name: 'Two declarations pages' });
    const own = tabs.querySelector('[data-target="task-tab:b-pdf"]') as HTMLElement;
    expect(new URL(own.getAttribute('href') ?? '', 'http://x').searchParams.get('ask')).toBe('r3');
    fireEvent.click(own);
    await waitFor(() => expect((router.state.location.search as { ask?: string }).ask).toBe('r3'));
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r3/ok', body: { shown_hash: 'h-b2' } }));
    // then the next ask waiting (r1) opens for itself, its tab, the thread beside it kept
    await waitFor(() => expect((router.state.location.search as { ask?: string }).ask).toBe('r1'));
    expect(router.state.location.pathname).toBe('/tasks/p1/12/a-pdf');
    expect(router.state.location.searchStr).toContain('chat=agent');
    expect(router.state.location.searchStr).not.toContain('vs='); // the next page at its own pinned version
  });

  it('when the next ask after a file’s is an email’s, its Email tab opens, the thread kept and the answered ask gone @approvals', async () => {
    const { files } = twoPdfs();
    const asks = (met: boolean) => [req(), req({ requirement_id: 'r3', shows: ['file:f8@2'], line: 'Sign B', ...(met && { met: true, may: [] }) })];
    shown = task({ title: 'Two declarations', to_leave: asks(false) });
    const after = task({ title: 'Two declarations', to_leave: asks(true) });
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r3/ok')
        ? { body: { task: after } }
        : url.endsWith('/files?all=1')
          ? { body: { files } }
          : url.endsWith('/oks/r3')
            ? { body: { subject: 'file:f8@2', shown_hash: 'h-b2', content: { file: 'f8', version: 2 } } }
            : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" tab="b-pdf" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12/b-pdf?ask=r3&chat=agent%3Alo&via=thread&vs=1',
    );
    expect(await screen.findByRole('region', { name: 'b.pdf' })).toBeTruthy();
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    fireEvent.click(await ready(box));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/email'));
    const params = new URLSearchParams(router.state.location.searchStr);
    expect([params.get('chat'), params.get('via'), params.get('ask'), params.get('vs')]).toEqual(['agent:lo', 'thread', null, null]);
  });

  it('a refused answer to the ask the address names leaves the person on its file, at its version @approvals', async () => {
    const { files } = twoPdfs();
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r3/ok')
        ? { status: 422, body: { error: { code: 'ok_refused', detail: 'This OK is no longer asked.' } } }
        : url.endsWith('/files?all=1')
          ? { body: { files } }
          : url.endsWith('/oks/r3')
            ? { body: { subject: 'file:f8@2', shown_hash: 'h-b2', content: { file: 'f8', version: 2 } } }
            : null;
    const router = show(
      <>
        <TaskPage task="12" pipeline="p1" tab="b-pdf" />
        <TaskChat task="12" />
      </>,
      '/tasks/p1/12/b-pdf?ask=r3',
    );
    expect(await screen.findByRole('region', { name: 'b.pdf' })).toBeTruthy();
    const box = within(screen.getByRole('region', { name: 'Chat with Legal Owner' })).getByRole('region', { name: 'The decision' });
    fireEvent.click(await ready(box));
    expect(await within(box).findByText('This OK is no longer asked.')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'b.pdf' })).toBeTruthy();
    expect(pdfRead.at(-1)).toBe('/api/v2/files/f8?v=2');
    expect(router.state.location.pathname).toBe('/tasks/p1/12/b-pdf');
  });

  it('on a computer, opened beside the thread it came from (`?chat=`), the page holds its own Approve @approvals', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email?chat=agent%3Alo&via=thread');
    const box = await decision();
    // in the page, not in the phone's dock (which a computer does not draw)
    expect(box.closest('[data-dock]')).toBeNull();
    expect(await ready(box)).toBeTruthy();
  });

  it('opens the version the address names over the one the ask pinned @approvals', async () => {
    const at = (n: number) => ({ n, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 10, url: `/api/v2/files/f9?v=${n}`, answers: [], ask: null });
    const pdf = { id: 'f9', name: 'declaration.pdf', title: 'declaration.pdf', mime: 'application/pdf', archived: false, current_version: 2, links: [], writers: [], versions: [at(1), at(2)] };
    shown = task({ title: 'Search warrant', to_leave: [req({ shows: ['file:f9@1'], line: 'Sign the declaration' })] });
    reply = (url) => (url.endsWith('/files?all=1') ? { body: { files: [pdf] } } : null);
    show(<TaskPage task="12" pipeline="p1" tab="declaration-pdf" />, '/tasks/p1/12/declaration-pdf?v=2');
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    await waitFor(() => expect(pdfRead.at(-1)).toBe('/api/v2/files/f9?v=2'));
  });

  it('reads a pinned version the file’s listing no longer reaches by the file’s id @approvals', async () => {
    // its newest version is another type: the pinned one is drawn by its own type, read from its meta
    const v2 = { n: 2, by: 'bot:lo', created_at: '2026-11-02T10:00:00Z', mime: 'text/plain', size: 10, url: '/api/v2/files/f9?v=2', answers: [], ask: null };
    const pdf = { id: 'f9', name: 'declaration.txt', title: 'declaration.pdf', mime: 'text/plain', archived: false, current_version: 2, links: [], writers: [], versions: [v2] };
    shown = task({ title: 'Search warrant', to_leave: [req({ shows: ['file:f9@1'], line: 'Sign the declaration' })] });
    reply = (url) =>
      url.endsWith('/files?all=1')
        ? { body: { files: [pdf] } }
        : url.endsWith('/api/v2/files/f9/meta?v=1')
          ? { body: { name: 'declaration.pdf', content_type: 'application/pdf', size: 10 } }
          : null;
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    expect(pdfRead.at(-1)).toBe('/api/v2/files/f9?v=1');
    expect(screen.queryByRole('button', { name: 'Sign' })).toBeNull();
  });

  it('a file the ask is about on another task opens as its own tab, read by its id, never the review beside it @approvals', async () => {
    shown = task({
      title: 'Search warrant',
      documents: [{ file_id: 'd1', name: 'Review', kind: 'markdown', version: 1 }] as never,
      to_leave: [req({ shows: ['file:x9@1', 'page:d1@1'], line: 'Sign the declaration' })],
    });
    reply = (url) =>
      url.endsWith('/api/v2/files/x9/info')
        ? { body: { file: { id: 'x9', name: 'declaration.pdf', title: 'Declaration', mime: 'application/pdf', version: 2, size: 10 } } }
        : url.endsWith('/api/v2/files/x9/versions')
          ? { body: { versions: [{ version: 1, mime: 'application/pdf', name: 'declaration.pdf', size: 10 }, { version: 2, current: true, mime: 'application/pdf', name: 'declaration.pdf', size: 10 }] } }
          : null;
    const router = show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/file'));
    const tabs = screen.getByRole('navigation', { name: 'Search warrant pages' });
    await waitFor(() => expect((tabs.querySelector('.doc-tab') as HTMLElement).textContent).toBe('Declaration'));
    await waitFor(() => expect(pdfRead.at(-1)).toBe('/api/v2/files/x9/versions/1'));
  });

  it('names a page by its version and says what Approve does @approvals', () => {
    const t = task({
      documents: [{ file_id: 'f2', name: 'QA report', version: 2 }] as never,
      next: { id: 's4', name: 'Release' },
      to_leave: [req({ kind: 'page', shows: [], approval: { document: 'QA report', file_id: 'f2', version: 2, visit_id: 'v1', unchecked: false } })],
    });
    const r = t.to_leave?.[0] as TaskRequirement;
    expect(decisionName(t, r)).toBe('QA report v2');
    expect(effectOf(t, r)).toBe('Moves to Release');
    // another thing still missing: Approve does not move it
    expect(effectOf({ ...t, to_leave: [r, req({ requirement_id: 'r2', shows: [] })] }, r)).toBe('');
  });
});

describe('the decision', () => {
  it('clears the request-changes draft when a different approval starts waiting', async () => {
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Legal Owner' });
    fireEvent.click(within(await decision()).getByRole('button', { name: 'Request changes' }));
    fireEvent.change(within(chat).getByRole('textbox', { name: 'What should change?' }), { target: { value: 'Update the email subject' } });

    const next = req({ requirement_id: 'r2', shows: [], line: 'Check the brief?' });
    act(() => {
      queryClient.setQueryData<TaskData>(keys.tasks.detail('t1'), (t) => t && { ...t, version: t.version + 1, to_leave: [next] });
    });

    expect(await within(chat).findByText('Check the brief?')).toBeTruthy();
    const box = within(chat).getByRole('region', { name: 'The decision' });
    expect(within(box).queryByRole('textbox', { name: 'What should change?' })).toBeNull();
    expect(within(chat).getByRole('textbox', { name: 'Message Legal Owner' })).toBeTruthy();
    fireEvent.click(within(box).getByRole('button', { name: 'Request changes' }));
    expect(within(box).getByRole('textbox', { name: 'What should change?' })).toHaveProperty('value', '');
  });

  it('keeps the decision being sent in view, and never turns into another ask on the email’s tab', async () => {
    const next = req({ requirement_id: 'r2', shows: [], line: 'Check the brief?' });
    shown = task({ to_leave: [req(), next] });
    let respond!: (r: Reply) => void;
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r1/ok')
        ? new Promise((resolve) => { respond = resolve; })
        : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/asks/r1/ok'));
    expect(within(box).getByText('Email to Dana Ruiz')).toBeTruthy();
    expect(within(box).queryByText('Check the brief?')).toBeNull();
    await act(async () => respond({ body: { task: task({ to_leave: [req({ met: true, may: [] }), next] }) } }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull());
    expect(screen.queryByText('Check the brief?')).toBeNull();
  });

  it('an email OK on a case itself shows the case’s own brief, and names no other matter @approvals', async () => {
    shown = task({ title: 'Ruiz: late delivery', note: 'Dana Ruiz wants a refund.' } as Partial<TaskData>);
    const parent = { id: 'p0', title: 'Legal 2026', label: null, step: null };
    reply = (url) => (url.endsWith('/api/v2/tasks/t1') || url.includes('/api/v2/tasks/%2312') ? { body: { task: shown, runs: [], children: [], parent, can_comment: true, mover: true } } : null);
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    const brief = await screen.findByRole('region', { name: 'Brief' });
    expect(within(brief).getByText('Dana Ruiz wants a refund.')).toBeTruthy();
    expect(screen.queryByText(/Legal 2026/)).toBeNull();
    expect(screen.queryByRole('link', { name: /^From / })).toBeNull();
  });

  it('names the email, who asks and what Approve does; the head is quiet and Details sits under ⋯ @approvals', async () => {
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect(await screen.findByRole('heading', { level: 1, name: 'Reply to the late-delivery claim' })).toBeTruthy();
    expect(screen.getByText('Legal · Review')).toBeTruthy();
    const box = await decision();
    expect(await within(box).findByText('Email to Dana Ruiz')).toBeTruthy();
    expect(within(box).getByText('Sends now')).toBeTruthy();
    expect(within(box).getByRole('img', { name: 'Legal Owner' })).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Reply to the late-delivery claim pages' });
    expect(within(tabs).queryByRole('link', { name: 'Details' })).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'More' })[0] as HTMLElement);
    expect(await screen.findByRole('menuitem', { name: 'Details' })).toBeTruthy();
  });

  it('reads the day a held email goes live, and drops it once the hold is cleared @approvals', async () => {
    const held = { ...mail, not_before: '2099-11-25T00:00:00-08:00' };
    reply = (url) => (url.endsWith('/api/v2/email/sends/s1') ? { body: held } : null);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Sends Nov 25')).toBeTruthy();
    // the case's row on the stream says the hold is gone (live/patch.ts): an explicit null
    await act(async () => queryClient.setQueryData(['email-send-state', 's1'], (s: object | undefined) => ({ ...s, not_before: null })));
    expect(await within(box).findByText('Sends now')).toBeTruthy();
  });

  it('says the day a held email goes and flags its archived file @approvals', async () => {
    const held = { ...mail, not_before: '2099-11-25T16:00:00.000000Z', send: { ...mail.send, attachments: [{ part: 2, name: 'letter.pdf', size: 4096, archived: true }] } };
    reply = (url) => (url.endsWith('/api/v2/email/sends/s1') ? { body: held } : null);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Sends Nov 25')).toBeTruthy();
    expect(within(box).getByText('letter.pdf')).toBeTruthy();
    expect(within(box).getByText('Archived')).toBeTruthy();
  });

  it('two sends on one task: each email tab approves its own send with its own hash @approvals', async () => {
    const second = { ...mail, send: { ...mail.send, id: 's2', subject: 'Records enclosed', recipients: ['Lee Park <lee@example.test>'] }, shown_hash: 'h-mail-2' };
    shown = task({ to_leave: [req(), req({ requirement_id: 'r2', shows: ['email:s2'] })] });
    reply = (url) =>
      url.endsWith('/api/v2/email/sends/s2')
        ? { body: second }
        : url.endsWith('/api/v2/oks/r2')
          ? { body: { requirement_id: 'r2', subject: 'email_send:s2', content: {}, shown_hash: 'h-grant-2', standing: 'waiting', task_id: 't1' } }
          : null;
    show(<TaskPage task="12" pipeline="p1" tab="email-2" />, '/tasks/p1/12/email-2');
    const box = await decision();
    expect(await within(box).findByText('Email to Lee Park')).toBeTruthy();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r2/ok', body: { shown_hash: 'h-mail-2' } }));
  });

  const secondSend = () => {
    const second = { ...mail, send: { ...mail.send, id: 's2', subject: 'Records enclosed', recipients: ['Lee Park <lee@example.test>'] }, shown_hash: 'h-mail-2' };
    const okTwo = { requirement_id: 'r2', subject: 'email_send:s2', content: {}, shown_hash: 'h-grant-2', standing: 'waiting', task_id: 't1' };
    reply = (url) => (url.endsWith('/api/v2/email/sends/s2') ? { body: second } : url.endsWith('/api/v2/oks/r2') ? { body: okTwo } : null);
  };

  it('off its Email tab an email’s OK is a pointer, its preview never read @approvals', async () => {
    const got: string[] = [];
    reply = (url) => {
      got.push(url);
      return null;
    };
    shown = task({ documents: [{ file_id: 'd1', name: 'Notes', kind: 'markdown', version: 1 }] as never });
    show(<TaskPage task="12" pipeline="p1" tab="notes" />, '/tasks/p1/12/notes');
    const pointer = await screen.findByRole('region', { name: 'Waits on you' });
    expect(within(pointer).getByRole('button', { name: 'Open' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
    expect(got.filter((u) => u.includes('/api/v2/email/sends/'))).toEqual([]);
  });

  it('beside an older email OK filed apart, the case’s own card steps aside @approvals', async () => {
    const sub = task({ id: 't7', number: 77, title: 'Send: Old notice → dana@example.test', pipeline_id: 'general', to_leave: [req({ requirement_id: 'r7', shows: ['email:s7'] })] });
    const children = [{ id: 't7', number: 77, title: sub.title, status: 'open' }];
    const old = { ...mail, send: { ...mail.send, id: 's7', state: 'waiting_approval' } };
    reply = (url) =>
      url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')
        ? { body: { task: shown, runs: [], children, can_comment: true, mover: true } }
        : url.endsWith('/api/v2/tasks/t7')
          ? { body: { task: sub, runs: [], children: [], can_comment: true, mover: true } }
          : url.endsWith('/api/v2/email/sends/s7')
            ? { body: old }
            : null;
    show(<TaskChat task="12" />, '/tasks/p1/12/email-2');
    await waitFor(() => expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.status).toBe('success'));
    await waitFor(() => expect(queryClient.getQueryData(['tasks', 'detail', 't7'])).toBeTruthy());
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
  });

  it('beside an address naming no tab of the task, the chat card answers the tab the page opens @approvals', async () => {
    shown = task({ to_leave: [req(), req({ requirement_id: 'r2', shows: [], line: 'Check the brief?' })] });
    for (const at of ['/tasks/p1/12/bogus', '/tasks/p1/12/%E0%A4%A']) {
      show(<TaskChat task="12" />, at);
      const box = await decision();
      expect(await within(box).findByText('Email to Dana Ruiz')).toBeTruthy(); // the Email tab opens: its own OK
      expect(within(box).queryByText('Check the brief?')).toBeNull();
      cleanup();
    }
  });

  it('the chat card beside an email tab approves that send @approvals', async () => {
    secondSend();
    shown = task({ to_leave: [req(), req({ requirement_id: 'r2', shows: ['email:s2'] })] });
    show(<TaskChat task="12" />, '/tasks/p1/12/email-2');
    const box = await decision();
    expect(await within(box).findByText('Email to Lee Park')).toBeTruthy();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r2/ok', body: { shown_hash: 'h-mail-2' } }));
  });

  it('an email tab whose send has no OK to give shows no decision, in the chat or the dock @approvals', async () => {
    secondSend();
    shown = task({ to_leave: [req(), req({ requirement_id: 'r2', shows: ['email:s2'], met: true, may: [] })] });
    show(<TaskChat task="12" />, '/tasks/p1/12/email-2');
    await waitFor(() => expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.status).toBe('success'));
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
    cleanup();
    show(<TaskPage task="12" pipeline="p1" tab="email-2" />, '/tasks/p1/12/email-2');
    expect(await screen.findByRole('heading', { name: 'Records enclosed' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
  });

  it('Approve sends the hash of the email as shown, then offers Undo @approvals', async () => {
    reply = (url, method) => {
      if (method === 'POST' && url.endsWith('/asks/r1/ok')) return { body: { task: task({ to_leave: [req({ met: true, may: ['undo'], answer: { id: 'a1', value: 'ok', by: 'human:me', at: '2026-11-19T10:00:00Z', note: null } })] }) } };
      if (method === 'POST' && url.includes('/undo')) return { body: { task: task() } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r1/ok', body: { shown_hash: 'h-mail' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/answers/a1/undo'));
  });

  it('holds the box with Approve busy while the server restarts, then sends the same answer again @approvals', async () => {
    let restarting = true;
    reply = (url, method) => {
      if (method !== 'POST' || !url.endsWith('/asks/r1/ok')) return null;
      // The proxy's answer while the server restarts (backend/caddy_assets.py HOLD_BODY), without its 5 s Retry-After.
      if (restarting) return { status: 503, body: null, text: 'Tico is starting. Try again in a few seconds.' };
      return { body: { task: task({ to_leave: [req({ met: true, may: [] })] }), answer_id: 'a9' } };
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const box = await decision();
    const approve = await ready(box);
    fireEvent.click(approve);
    await waitFor(() => expect(keysSent).toHaveLength(1));
    expect(approve.getAttribute('aria-busy')).toBe('true');
    expect(approve).toHaveProperty('disabled', true);
    fireEvent.click(approve);
    restarting = false;
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeTruthy();
    expect(keysSent).toEqual([keysSent[0], keysSent[0]]);
  });

  it('Undo takes back the answer the OK names, when the server gives its id @approvals', async () => {
    reply = (url, method) => {
      if (method === 'POST' && url.endsWith('/asks/r1/ok')) return { body: { task: task({ to_leave: [req({ met: true, may: [] })] }), answer_id: 'a9' } };
      if (method === 'POST' && url.includes('/undo')) return { body: { task: task() } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    fireEvent.click(await ready(await decision()));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/answers/a9/undo'));
  });

  it('says Changed when what it shows moved, and takes a new press for the new one @approvals', async () => {
    let hash = 'h1';
    reply = (url, method) => {
      if (url.endsWith('/oks/r1')) return { body: { subject: 'file:f9@1', shown_hash: hash, content: {} } };
      if (method === 'POST' && url.endsWith('/asks/r1/ok')) {
        if ((sent.at(-1)?.body.shown_hash as string) === 'h1') {
          hash = 'h2';
          return { status: 409, body: { error: { code: 'task_changed', detail: 'What you saw is not what is asked now. Open it again.' } } };
        }
        return { body: { task: task({ to_leave: [req({ met: true, may: [] })] }) } };
      }
      return null;
    };
    shown = task({ to_leave: [req({ shows: ['file:f9@1'], line: 'OK the brief?' })] });
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const box = await decision();
    fireEvent.click(await ready(box));
    expect(await within(box).findByText('Changed')).toBeTruthy();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ shown_hash: 'h2' }));
    expect(sent.filter((x) => x.url.endsWith('/ok'))).toHaveLength(2);
  });

  it('a page’s Request changes sends the version the card showed when it opened @approvals', async () => {
    reply = (url, method) => {
      if (url.endsWith('/oks/r1')) return { status: 404, body: { error: { code: 'not_found' } } };
      if (method === 'POST' && url.endsWith('/asks/r1/answer')) return { body: { task: task({ to_leave: [req({ kind: 'page', met: true, may: [] })] }) } };
      return null;
    };
    const page = { file_id: 'f9', version: 2, ref: 'page:f9@2' };
    shown = task({ visit_id: 'v4', to_leave: [req({ kind: 'page', shows: [], line: 'Go live page', page } as Partial<TaskRequirement>)] } as Partial<TaskData>);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const box = await decision();
    fireEvent.click(within(box).getByRole('button', { name: 'Request changes' }));
    fireEvent.change(within(box).getByRole('textbox', { name: 'What should change?' }), { target: { value: 'Shorter' } });
    fireEvent.click(within(box).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent.at(-1)?.body).toEqual({ value: 'no', note: 'Shorter', page_version: 'f9@2', visit_id: 'v4' }));
  });

  it('a page’s Approve sends the version the card shows @approvals', async () => {
    reply = (url, method) => {
      if (url.endsWith('/oks/r1')) return { status: 404, body: { error: { code: 'not_found' } } };
      if (method === 'POST' && url.endsWith('/asks/r1/answer')) return { body: { task: task({ to_leave: [req({ kind: 'page', met: true, may: [] })] }) } };
      return null;
    };
    const page = { file_id: 'f9', version: 2, ref: 'page:f9@2' };
    shown = task({ visit_id: 'v4', to_leave: [req({ kind: 'page', shows: [], line: 'Go live page', page } as Partial<TaskRequirement>)] } as Partial<TaskData>);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r1/answer', body: { value: 'ok', page_version: 'f9@2', visit_id: 'v4' } }));
  });

  it('in Flow, opens the next item once approved @approvals', async () => {
    reply = (url, method) => (method === 'POST' && url.endsWith('/asks/r1/ok') ? { body: { task: task({ to_leave: [req({ met: true, may: [] })] }) } } : null);
    const router = show(<TaskPage task="12" flow />, '/flow/12');
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow/13'));
  });

  it('Request changes is a message to the agent in its chat, and marks the request Changes asked @approvals', async () => {
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r1/answer')
        ? { body: { task: task({ to_leave: [req({ may: ['undo'], answer: { id: 'a2', value: 'no', by: 'human:me', at: '2026-11-19T10:14:00Z', note: 'Ask for a replacement' } })] }) } }
        : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const chat = await screen.findByRole('region', { name: 'Chat with Legal Owner' });
    expect(within(chat).getByRole('textbox', { name: 'Message Legal Owner' })).toBeTruthy();
    const box = await decision();
    fireEvent.click(within(box).getByRole('button', { name: 'Request changes' }));
    const line = within(box).getByRole('textbox', { name: 'What should change?' });
    // one place to type: the chat's own box steps aside
    expect(within(chat).queryByRole('textbox', { name: 'Message Legal Owner' })).toBeNull();
    const send = within(box).getByRole('button', { name: 'Send' });
    expect(send).toHaveProperty('disabled', true);
    fireEvent.change(line, { target: { value: 'Ask for a replacement' } });
    fireEvent.click(send);
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r1/answer', body: { value: 'no', note: 'Ask for a replacement' } }));
    expect(await within(chat).findByText(/^Changes asked/)).toBeTruthy();
    // the server posts the note in the chat itself: the app adds no second copy
    expect(vi.mocked(sendMessage)).not.toHaveBeenCalled();
    expect(within(chat).queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(within(chat).getByRole('textbox', { name: 'Message Legal Owner' })).toBeTruthy();
  });
});

describe('a rule to add', () => {
  const bound = {
    rule: 'o7',
    version: 2,
    text: 'Refunds over $100 go to Ben first.',
    who: { everyone: false, all_bots: false, groups: [], bots: [{ id: 'ops', name: 'Ops' }], people: [] },
    with: { groups: [], bots: [], people: [{ id: 'ben', name: 'Ben' }] },
    where: [],
    if_broken: 'escalate',
    reviewer: { id: 'ben', name: 'Ben' },
    exact_check: { kind: 'max_usd', action: 'refund', field: 'amount_usd', max_usd: 100 },
  };
  const ruleTask = () => task({ title: 'Make this a rule: refunds', to_leave: [req({ line: 'Approve this rule', shows: ['rule:o7'] })] });

  it('shows the bound rule and every field that enforces it, and Approve sends what was shown @approvals', async () => {
    shown = ruleTask();
    reply = (url) => (url.endsWith('/api/v2/oks/r1') ? { body: { requirement_id: 'r1', subject: 'rule:o7', content: bound, shown_hash: 'h-rule', standing: 'waiting' } } : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Refunds over $100 go to Ben first.')).toBeTruthy();
    const facts = [...within(box).getByRole('group', { name: 'The rule' }).querySelectorAll('.fact')].map((f) => [f.firstElementChild?.textContent, f.lastElementChild?.textContent]);
    expect(facts).toEqual([
      ['Who', 'Ops'],
      ['With', 'Ben'],
      ['Where', 'Everywhere'],
      ['If broken', 'Ask first'],
      ['Ask', 'Ben'],
      ['Checked exactly', 'refund: amount_usd at most $100'],
    ]);
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/asks/r1/ok'));
    expect(sent.at(-1)?.body).toMatchObject({ shown_hash: 'h-rule' });
  });

  it('keeps Approve off while a field that enforces the rule is missing from what is shown @approvals', async () => {
    shown = ruleTask();
    const { with: _w, ...partial } = bound;
    reply = (url) => (url.endsWith('/api/v2/oks/r1') ? { body: { requirement_id: 'r1', subject: 'rule:o7', content: partial, shown_hash: 'h-rule', standing: 'waiting' } } : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Part of it did not load')).toBeTruthy();
    expect(within(box).getByRole('button', { name: 'Approve' })).toHaveProperty('disabled', true);
  });
});

describe('an email waiting to send', () => {
  const titled = 'Send: Re: Notice of claim → Dana Ruiz <dana@example.test> +1';

  it('reads its subject and recipient from the title the server gives it @approvals', () => {
    expect(sendTitle(titled)).toEqual({ subject: 'Notice of claim', to: 'Dana Ruiz <dana@example.test>' });
    expect(sendTitle('Send: Fwd: RE: Lease → a@example.test')).toEqual({ subject: 'Lease', to: 'a@example.test' });
    expect(sendTitle('Reply to the claim')).toBeNull();
  });

  it('is titled by its subject alone, and the title is not a field to rename @approvals', async () => {
    shown = task({ title: titled });
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const title = await screen.findByRole('heading', { level: 1, name: 'Notice of claim' });
    fireEvent.click(title);
    expect(screen.queryByRole('textbox', { name: 'Title' })).toBeNull();
    expect(document.activeElement?.tagName).not.toBe('INPUT');
  });

  it.each([
    { problem: 'fails', response: { status: 500, body: { error: { code: 'internal', detail: 'Try again' } } } },
    { problem: 'is incomplete', response: { body: { ...mail, body: '', preview_complete: false, shown_hash: null } } },
  ])('offers a retry when the email preview $problem @approvals', async ({ response }) => {
    shown = task({ title: titled });
    let busy = true;
    reply = (url) => (busy && url.endsWith('/api/v2/email/sends/s1') ? response : null);
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Part of it did not load')).toBeTruthy();
    expect(within(box).getByText('Email to Dana Ruiz')).toBeTruthy();
    expect(within(box).queryByText('Sends now')).toBeNull();
    expect(within(box).getByRole('button', { name: 'Approve' })).toHaveProperty('disabled', true);
    busy = false;
    fireEvent.click(within(box).getByRole('button', { name: 'Try again' }));
    await ready(box);
    expect(within(box).getByText('Sends now')).toBeTruthy();
    expect(within(box).queryByText('Part of it did not load')).toBeNull();
  });

  it('a refused decision stays with its email, never shown on another tab @approvals', async () => {
    const notes = { file_id: 'd1', name: 'Notes', kind: 'markdown', version: 1 };
    shown = task({ documents: [notes] as never });
    let refuse!: () => void;
    reply = (url, method) =>
      method === 'POST' && url.endsWith('/asks/r1/ok')
        ? new Promise<Reply>((done) => (refuse = () => done({ status: 422, body: { error: { code: 'ok_refused', detail: 'This OK is no longer asked.' } } })))
        : null;
    const router = show(<TaskChat task="12" />, '/tasks/p1/12/email');
    const box = await decision();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)?.url).toBe('/api/v2/tasks/t1/asks/r1/ok'));
    // while it is sent, the task says the OK is no longer asked; then the server refuses it
    shown = task({ documents: [notes] as never, to_leave: [req({ met: true, may: [] })] });
    await act(() => queryClient.invalidateQueries({ queryKey: keys.tasks.detail('t1') }));
    await act(async () => refuse());
    expect(await within(box).findByText('This OK is no longer asked.')).toBeTruthy(); // held on its email, to say why
    await act(() => router.navigate({ to: '/tasks/p1/12/notes' as never }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/notes'));
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
  });

  it('keeps Approve off for a stage’s own OK whose email did not load @approvals', async () => {
    shown = task({ title: titled });
    reply = (url) =>
      url.endsWith('/api/v2/oks/r1')
        ? { status: 404, body: { error: { code: 'not_found', detail: 'No such OK' } } }
        : url.endsWith('/api/v2/email/sends/s1')
          ? { body: { ...mail, body: '', preview_complete: false, shown_hash: null } }
          : null;
    show(<TaskChat task="12" />, '/tasks/p1/12');
    const box = await decision();
    expect(await within(box).findByText('Part of it did not load')).toBeTruthy();
    expect(within(box).getByRole('button', { name: 'Approve' })).toHaveProperty('disabled', true);
  });
});

describe('what the person needs beside the email', () => {
  const header = () => document.querySelector('.ap-hdr') as HTMLElement;
  const pairs = (dl: HTMLElement) => [...dl.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
  const withMail = (more: Record<string, unknown>, send: Record<string, unknown> = {}) => {
    const body = { ...mail, ...more, send: { ...mail.send, ...send } };
    return (url: string) => (url.endsWith('/api/v2/email/sends/s1') ? { body } : null);
  };

  it('names To, Cc and Bcc apart once the preview gives them, and names the email by its To @approvals', async () => {
    reply = withMail(
      { to: ['Dana Ruiz <dana@example.test>'], cc: ['counsel@example.test'], bcc: ['files@example.test'] },
      { recipients: ['counsel@example.test', 'Dana Ruiz <dana@example.test>', 'files@example.test'] },
    );
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    expect(pairs(header())).toEqual([
      ['From', 'legal@example.test'],
      ['To', 'Dana Ruiz <dana@example.test>'],
      ['Cc', 'counsel@example.test'],
      ['Bcc', 'files@example.test'],
    ]);
    expect(await within(await decision()).findByText('Email to Dana Ruiz')).toBeTruthy();
  });

  it('before the preview splits them, every address is under To @approvals', async () => {
    reply = withMail({}, { recipients: ['Dana Ruiz <dana@example.test>', 'counsel@example.test'] });
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    expect(pairs(header())).toEqual([
      ['From', 'legal@example.test'],
      ['To', 'Dana Ruiz <dana@example.test>, counsel@example.test'],
    ]);
  });

  it('shows the letter it carries under the message, without a press; the other files stay cards @approvals', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:letter') }));
    const files = [
      { part: 1, name: 'photo.jpg', size: 2048, type: 'image/jpeg' },
      { part: 2, name: 'letter.pdf', size: 4096, type: 'application/pdf' },
      { part: 3, name: 'exhibit.pdf', size: 8192, type: 'application/pdf' },
    ];
    reply = withMail({}, { attachments: files });
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    const letter = await screen.findByRole('group', { name: 'letter.pdf' });
    // PDF.js draws the letter (library/PdfView): its place is under the message, before any press
    await waitFor(() => expect(letter.querySelector('.fv-pdf, .fv-none')).toBeTruthy());
    expect(screen.queryByRole('group', { name: 'exhibit.pdf' })).toBeNull();
    for (const name of ['photo.jpg', 'letter.pdf', 'exhibit.pdf']) expect(screen.getByRole('link', { name: new RegExp(name.replace('.', '\\.')) })).toBeTruthy();
  });

  it('a Word letter is drawn as its sheet under the message @approvals', async () => {
    reply = withMail({}, { attachments: [{ part: 2, name: 'letter.docx', size: 4096, type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }] });
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    // the stub answers no .docx bytes: the sheet's own place says so, under the message
    expect(await screen.findByText('letter.docx did not load')).toBeTruthy();
  });

  it('shows the email it answers in the envelope, its first line until pressed @approvals', async () => {
    const original = { msg_id: 'm1', thread_id: 'th1', from_header: 'Dana Ruiz <dana@example.test>', from_addr: 'dana@example.test', epoch: 1763400000, body: 'Dear Legal,\nYour delivery was late.\nI want a refund.\nPlease reply by Friday.' };
    const mailRead = withMail({}, { reply_to_id: 'm1', thread_id: 'th1' });
    reply = (url) => (url.includes('/api/v2/mail/messages/m1?mailbox=legal%40example.test') ? { body: original } : mailRead(url));
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    const answered = await screen.findByRole('region', { name: 'In reply to' });
    // in the envelope, under From and To: above the message and its letter, not under them
    expect(answered.closest('.ap-env')).toBeTruthy();
    const head = within(answered).getByRole('button', { expanded: false });
    expect(head.textContent).toContain('Dana Ruiz, ');
    expect(within(answered).getByText(/Your delivery was late/)).toBeTruthy();
    fireEvent.click(head);
    expect(within(answered).getByRole('button', { expanded: true })).toBeTruthy();
    expect(answered.classList.contains('is-open')).toBe(true);
  });

  it('when the email it answers cannot be read here, shows nothing of it @approvals', async () => {
    const mailRead = withMail({}, { reply_to_id: 'm1' });
    reply = (url) => (url.includes('/api/v2/mail/messages/') ? { status: 403, body: { error: { code: 'forbidden', detail: 'No access to this mailbox' } } } : mailRead(url));
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    await waitFor(() => expect(sent).toEqual([]));
    expect(screen.queryByRole('region', { name: 'In reply to' })).toBeNull();
    expect(screen.queryByText(/No access/)).toBeNull();
  });

  it('links the task the email came from, once, in the quiet line @approvals', async () => {
    const matter = task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [] });
    const mailRead = withMail({}, { task_id: 't5' });
    reply = (url) => (url.endsWith('/api/v2/tasks/t5') ? { body: { task: matter, runs: [], children: [] } } : mailRead(url));
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const from = await screen.findByRole('link', { name: 'From Late-delivery claim: Dana Ruiz' });
    expect(from.getAttribute('href')).toBe('/tasks/p9/40');
    expect(screen.getAllByRole('link', { name: /^From / })).toHaveLength(1);
  });

  it('names the matter, not an earlier OK, when the email was queued on that OK @approvals', async () => {
    const earlier = task({ id: 't4', number: 39, title: 'Send: Re: Notice of claim → dana@example.test', status: 'closed', pipeline_id: 'general', to_leave: [] });
    const mailRead = withMail({}, { task_id: 't4' });
    reply = (url) =>
      url.endsWith('/api/v2/tasks/t4')
        ? { body: { task: earlier, runs: [], children: [], parent: { id: 't5', title: 'Late-delivery claim: Dana Ruiz', label: null, step: null } } }
        : mailRead(url);
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const from = await screen.findByRole('link', { name: 'From Late-delivery claim: Dana Ruiz' });
    expect(from.getAttribute('href')).toBe('/task/t5');
    expect(screen.queryByRole('link', { name: /^From Send/ })).toBeNull();
  });

  it('names the matter that waits on the OK when the send names none @approvals', async () => {
    shown = task({ relations: { depends_on: [{ id: 't5', title: 'Late-delivery claim: Dana Ruiz', status: 'open', owner_id: 'bot:lo', direction: 'in' }] } } as Partial<TaskData>);
    const earlier = task({ id: 't4', title: 'Send an email', status: 'closed', to_leave: [] });
    const mailRead = withMail({}, { task_id: 't4' });
    reply = (url) => (url.endsWith('/api/v2/tasks/t4') ? { body: { task: earlier, runs: [], children: [] } } : mailRead(url));
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect((await screen.findByRole('link', { name: 'From Late-delivery claim: Dana Ruiz' })).getAttribute('href')).toBe('/task/t5');
  });

  it('names no source when the only one is another OK @approvals', async () => {
    const earlier = task({ id: 't4', title: 'Send: Re: Notice of claim → dana@example.test', status: 'closed', to_leave: [] });
    const mailRead = withMail({}, { task_id: 't4' });
    reply = (url) => (url.endsWith('/api/v2/tasks/t4') ? { body: { task: earlier, runs: [], children: [] } } : mailRead(url));
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await within(await decision()).findByText('Email to Dana Ruiz');
    await waitFor(() => expect(screen.queryByRole('link', { name: /^From / })).toBeNull());
  });

  it('names no source when the email was queued on the task that is open @approvals', async () => {
    reply = withMail({}, { task_id: 't1' });
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await within(await decision()).findByText('Email to Dana Ruiz');
    expect(screen.queryByRole('link', { name: /^From / })).toBeNull();
  });
});

describe('on a phone, the decision holds the bottom', () => {
  const docked = () => document.querySelector('[data-dock]');

  it('of the thing’s tab and of an attachment open over it @approvals', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    expect(within(docked() as HTMLElement).getByRole('region', { name: 'The decision' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: /letter\.pdf/ }));
    expect(await screen.findByRole('link', { name: 'Back to the email' })).toBeTruthy();
    expect(within(docked() as HTMLElement).getByRole('region', { name: 'The decision' })).toBeTruthy();
  });

  it('of the Chat tab, over its message box @approvals', async () => {
    shown = task({ to_leave: [req(), req({ requirement_id: 'r2', shows: [], line: 'Check the brief?' })] });
    show(<TaskPage task="12" pipeline="p1" tab="chat" />, '/tasks/p1/12/chat');
    const chat = await screen.findByRole('region', { name: 'Chat with Legal Owner' });
    // the task's own ask: an email's OK is approved only on its Email tab
    expect(within(within(chat).getByRole('region', { name: 'The decision' })).getByText('Check the brief?')).toBeTruthy();
    // one box: the page's own dock steps aside on the Chat tab
    expect(screen.getAllByRole('region', { name: 'The decision' })).toHaveLength(1);
  });
});

describe('the matter shows the email that waits on the person', () => {
  const matter = (more: Partial<TaskData> = {}) =>
    task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [], ...more });
  const sub = task({ id: 't1', number: 12, title: 'Send: Re: Notice of claim → dana@example.test', pipeline_id: 'general' });
  const serve = (m: TaskData, children: unknown[], state = 'waiting_approval') => {
    reply = (url) => {
      if (url.includes('/api/v2/tasks/%2340') || url.endsWith('/api/v2/tasks/t5')) return { body: { task: m, runs: [], children, can_comment: true, mover: true } };
      if (url.endsWith('/api/v2/tasks/t1')) return { body: { task: { ...sub, email_send: { id: 's1', state } }, runs: [], children: [], parent: { id: 't5', title: m.title, label: null, step: null } } };
      if (url.endsWith('/api/v2/email/sends/s1')) return { body: { ...mail, send: { ...mail.send, state } } };
      return null;
    };
  };

  it('opens on the email: its brief, then the decision of its "Send:" sub-task, then the email, Approve bound to what was shown @approvals', async () => {
    serve(matter({ note: 'Dana Ruiz wants a refund for a late delivery.' } as Partial<TaskData>), [{ id: 't1', title: sub.title, status: 'open', owner_id: 'bot:lo' }]);
    const router = show(<TaskPage task="40" pipeline="p9" />, '/tasks/p9/40');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p9/40/email'));
    const brief = await screen.findByRole('region', { name: 'Brief' });
    const box = await decision();
    const env = await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    // the brief reads first, the decision right under it, the email after
    expect(brief.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(box.compareDocumentPosition(env) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(brief).getByText('Dana Ruiz wants a refund for a late delivery.')).toBeTruthy();
    expect(within(box).getByText('Email to Dana Ruiz')).toBeTruthy();
    expect(within(box).getByText('Sends now')).toBeTruthy();
    fireEvent.click(await ready(box));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/tasks/t1/asks/r1/ok', body: { shown_hash: 'h-mail' } }));
  });

  it('on its other pages, where the email is not on screen, only points to it: no Approve, Open goes to the email @approvals', async () => {
    const deadlines = { file_id: 'd1', name: 'Deadlines', kind: 'markdown', version: 1 };
    serve(matter({ documents: [deadlines], relations: { depends_on: [{ id: 't1', title: sub.title, status: 'open', owner_id: 'bot:lo', direction: 'out' }] } } as unknown as Partial<TaskData>), []);
    const router = show(<TaskPage task="40" pipeline="p9" tab="deadlines" />, '/tasks/p9/40/deadlines');
    const pointer = await screen.findByRole('region', { name: 'Waits on you' });
    // named from the OK task's title: its preview is never read off the Email tab
    expect(await within(pointer).findByText('Email to dana@example.test')).toBeTruthy();
    // one label on the bar: the dot says it waits on you to a screen reader, no second line drawn over the name
    expect(within(pointer).queryByText(/waits on you/i)).toBeNull();
    expect(within(pointer).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Request changes' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'The decision' })).toBeNull();
    fireEvent.click(within(pointer).getByRole('button', { name: 'Open' }));
    // the Email tab, where the decision sits over the email (the test above)
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p9/40/email'));
    expect(sent).toEqual([]);
  });

  it('on a PDF tab while it waits on the person, the PDF still offers Sign under the pointer @approvals', async () => {
    const at = { n: 1, by: 'bot:lo', created_at: '2026-11-01T10:00:00Z', mime: 'application/pdf', size: 2048, url: '/api/v2/files/pdf1?v=1', answers: [], ask: null };
    const pdf = { id: 'pdf1', name: 'declaration.pdf', title: 'declaration.pdf', mime: 'application/pdf', archived: false, current_version: 1, links: [], writers: [], versions: [at] };
    serve(matter({ relations: { depends_on: [{ id: 't1', title: sub.title, status: 'open', owner_id: 'bot:lo', direction: 'out' }] } } as unknown as Partial<TaskData>), []);
    const inner = reply;
    reply = (url, method) => (url.endsWith('/api/v2/tasks/t5/files?all=1') ? { body: { files: [pdf] } } : inner(url, method));
    show(<TaskPage task="40" pipeline="p9" tab="declaration-pdf" />, '/tasks/p9/40/declaration-pdf');
    const pointer = await screen.findByRole('region', { name: 'Waits on you' });
    expect(await within(pointer).findByText('Email to dana@example.test')).toBeTruthy();
    expect(await screen.findByRole('region', { name: 'declaration.pdf' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign' })).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it('shows nothing once the email is no longer to go out, or with no OK hung on it @approvals', async () => {
    serve(matter(), [{ id: 't1', title: sub.title, status: 'open', owner_id: 'bot:lo' }], 'canceled');
    show(<TaskPage task="40" pipeline="p9" />, '/tasks/p9/40');
    await waitFor(() => expect(queryClient.getQueryData(keys.tasks.detail('t1'))).toBeTruthy());
    await act(async () => {});
    expect(document.querySelector('.tv-waits')).toBeNull();
    expect(queryClient.getQueryData([...keys.tasks.detail('t1'), 'email', 's1'])).toBeUndefined();
    expect(sent).toEqual([]);
  });
});

describe('a matter that waits on its "Send:" sub-task’s ask answers the sub-task', () => {
  // The shape of a real case: the matter asks nothing itself (a page, its links done, its owner done); its turn is the
  // Send sub-task, whose OK of the email is the ask. A second, plain sub-task sits beside it.
  const rid = '47dfc023';
  const ask = req({ requirement_id: rid, may: ['ok', 'no', 'give', 'withdraw'] });
  const ownerDone = req({ requirement_id: 'rdone', kind: 'owner_done', line: 'Done', may: ['done', 'give'], shows: [], asked_by: null });
  const send = (asked: TaskRequirement = ask) =>
    task({ id: 'b78be168', number: 36462, title: 'Send: Re: Notice of claim → dana@example.test', pipeline_id: 'legal-responses', to_leave: [ownerDone, asked] });
  const matter = task({
    id: '19ca7418',
    number: 36461,
    title: 'Flores: reply email',
    pipeline_id: 'legal-responses',
    documents: [{ file_id: 'd1', name: 'Review page', kind: 'markdown', version: 1 }],
    to_leave: [
      req({ requirement_id: 'rpage', kind: 'page', line: 'Review page', may: [], shows: [], asked_by: null }),
      req({ requirement_id: 'rlinked', kind: 'linked_done', line: 'Linked tasks done', met: true, may: [], shows: [], asked_by: null }),
      req({ requirement_id: 'rowner', kind: 'owner_done', line: 'Done', may: ['done', 'give'], shows: [], asked_by: null }),
    ],
    turn: { state: 'tasks', who: 'b78be168', line: 'Send: Re: Notice of claim → dana@example.test', requirement_id: 'rpage', ask: false },
  } as unknown as Partial<TaskData>);
  const children = [
    { id: 'b78be168', title: 'Send: Re: Notice of claim → dana@example.test', status: 'open', owner_id: 'bot:lo' },
    { id: 'c0ffee00', title: 'Review the reply', status: 'open', owner_id: 'bot:lo' },
  ];
  const serve = (post: (url: string) => Reply | Promise<Reply> | null) => {
    reply = (url, method) => {
      if (method === 'POST') return post(url);
      if (url.includes('/api/v2/tasks/%2336461') || url.endsWith('/api/v2/tasks/19ca7418')) return { body: { task: matter, runs: [], children, can_comment: true, mover: true } };
      if (url.endsWith('/api/v2/tasks/b78be168')) return { body: { task: send(), runs: [], children: [], parent: { id: '19ca7418', title: matter.title, label: null, step: null } } };
      if (url.endsWith(`/api/v2/oks/${rid}`)) return { body: { requirement_id: rid, subject: 'email_send:s1', content: {}, shown_hash: 'h-grant', standing: 'waiting', task_id: 'b78be168' } };
      return null;
    };
  };
  const open = () => show(<TaskPage task="36461" pipeline="legal-responses" tab="email" />, '/tasks/legal-responses/36461/email');

  it('Approve answers the sub-task’s ask with the hash of the email as shown, and says so @approvals', async () => {
    const answered = send(req({ requirement_id: rid, met: true, may: ['undo'], answer: { id: 'a7', value: 'ok', by: 'human:me', at: '2026-11-19T10:00:00Z', note: null } } as Partial<TaskRequirement>));
    serve((url) => (url.endsWith(`/asks/${rid}/ok`) ? { body: { task: answered } } : null));
    const router = open();
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(sent).toEqual([{ url: `/api/v2/tasks/b78be168/asks/${rid}/ok`, body: { shown_hash: 'h-mail' } }]));
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Sending to dana@example.test'));
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    // nothing else on it waits on the person: the task closes (opened from nowhere here, so to Tasks)
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
  });

  it('opened from a board, Approve closes the task once nothing on it waits: back to the board as it was, Undo offered @approvals', async () => {
    const answered = send(req({ requirement_id: rid, met: true, may: ['undo'], answer: { id: 'a7', value: 'ok', by: 'human:me', at: '2026-11-19T10:00:00Z', note: null } } as Partial<TaskRequirement>));
    serve((url) => (url.endsWith(`/asks/${rid}/ok`) ? { body: { task: answered } } : null));
    const router = show(<TaskPage task="36461" pipeline="legal-responses" tab="email" />, '/tasks/legal-responses?owner=bot%3Alo');
    await act(() => router.navigate({ href: '/tasks/legal-responses/36461/email' }));
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/legal-responses'));
    expect(router.state.location.search).toEqual({ owner: 'bot:lo' });
    // stepped back, not pushed: the board is where it was in the history
    expect(router.history.canGoBack()).toBe(false);
    // the email's own toast follows it through its undo window
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Sending to dana@example.test'));
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
  });

  it('when something else on the task still waits, Approve stays on it and opens that ask @approvals', async () => {
    const page = req({ requirement_id: 'rpage', kind: 'page', line: 'Review page', shows: [], approval: { document: 'Review page', file_id: 'd1', version: 1, visit_id: 'v1', unchecked: false } } as Partial<TaskRequirement>);
    const asking = { ...matter, to_leave: [page, ...(matter.to_leave ?? []).slice(1)] } as TaskData;
    const answered = send(req({ requirement_id: rid, met: true, may: ['undo'], answer: { id: 'a7', value: 'ok', by: 'human:me', at: '2026-11-19T10:00:00Z', note: null } } as Partial<TaskRequirement>));
    serve((url) => (url.endsWith(`/asks/${rid}/ok`) ? { body: { task: answered } } : null));
    const own = reply;
    reply = (url, method) => (method === 'GET' && (url.includes('/api/v2/tasks/%2336461') || url.endsWith('/api/v2/tasks/19ca7418')) ? { body: { task: asking, runs: [], children, can_comment: true, mover: true } } : own(url, method));
    const router = show(<TaskPage task="36461" pipeline="legal-responses" tab="email" />, '/tasks/legal-responses');
    await act(() => router.navigate({ href: '/tasks/legal-responses/36461/email' }));
    // two asks: the email's (over the email) and the matter's own page
    const box = await waitFor(() => {
      const found = screen.getAllByRole('region', { name: 'The decision' }).find((b) => within(b).queryByText('Email to Dana Ruiz'));
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    fireEvent.click(await ready(box));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/legal-responses/36461/review-page'));
    // the email's own toast follows it through its undo window
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Sending to dana@example.test'));
  });

  it('a refused Approve says why, over the email, with Approve there to press again @approvals', async () => {
    let refuse = () => {};
    serve(() => new Promise<Reply>((done) => (refuse = () => done({ status: 422, body: { error: { code: 'ok_refused', detail: 'This OK is no longer asked.' } } }))));
    open();
    fireEvent.click(await ready(await decision()));
    await waitFor(() => expect(sent.map((x) => x.url)).toEqual([`/api/v2/tasks/b78be168/asks/${rid}/ok`]));
    // while it is sent, the email and its decision stay on screen
    await act(async () => {});
    expect(screen.getByText('Dear Ms. Ruiz, our response is attached.')).toBeTruthy();
    await act(async () => refuse());
    const box = await decision();
    expect((await within(box).findByRole('alert')).textContent).toContain('This OK is no longer asked.');
    expect(within(box).getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByText('Dear Ms. Ruiz, our response is attached.')).toBeTruthy();
  });

  it('Request changes answers the sub-task’s ask @approvals', async () => {
    serve((url) => (url.endsWith(`/asks/${rid}/answer`) ? { body: { task: send(req({ requirement_id: rid, may: ['undo'], answer: { id: 'a8', value: 'no', by: 'human:me', at: '2026-11-19T10:14:00Z', note: 'Shorter' } } as Partial<TaskRequirement>)) } } : null));
    const router = open();
    const box = await decision();
    fireEvent.click(within(box).getByRole('button', { name: 'Request changes' }));
    fireEvent.change(within(box).getByRole('textbox', { name: 'What should change?' }), { target: { value: 'Shorter' } });
    fireEvent.click(within(box).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent).toEqual([{ url: `/api/v2/tasks/b78be168/asks/${rid}/answer`, body: { value: 'no', note: 'Shorter' } }]));
    expect(await screen.findByText(/^Changes asked/)).toBeTruthy();
    // nothing else on it waits on the person: the task closes too
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
  });
});

describe('an email’s OK reads with the matter it answers', () => {
  it('names the matter and its brief above the email @approvals', async () => {
    shown = task({ title: 'Send: Re: Notice of claim → dana@example.test' });
    const matter = task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [], note: 'Dana Ruiz wants a refund.' } as Partial<TaskData>);
    reply = (url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
        return { body: { task: shown, runs: [], children: [], parent: { id: 't5', title: matter.title, label: null, step: null } } };
      if (url.endsWith('/api/v2/tasks/t5')) return { body: { task: matter, runs: [], children: [] } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const brief = await screen.findByRole('region', { name: 'Brief' });
    expect(within(brief).getByRole('link', { name: 'Late-delivery claim: Dana Ruiz' }).getAttribute('href')).toBe('/tasks/p9/40');
    expect(within(brief).getByText('Dana Ruiz wants a refund.')).toBeTruthy();
    const body = await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    expect(brief.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the matter’s open key dates in one line above the email @approvals', async () => {
    shown = task({ title: 'Send: Re: Notice of claim → dana@example.test' });
    // In the API's order: a timed deadline (03:00 UTC Dec 8, the evening of Dec 7 in the company's zone) before an all-day
    // Dec 8. A string sort would put the all-day date first, so the test fails if the strip sorts.
    const dates = [
      { id: 'd1', label: 'Hearing', starts_at: null, due_at: '2026-12-08T03:00:00Z', done_at: null },
      { id: 'd2', label: 'Reply due', starts_at: null, due_at: '2026-12-08', done_at: null },
      { id: 'd3', label: 'Old date', starts_at: null, due_at: '2026-10-01', done_at: '2026-10-02' },
    ];
    const matter = task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [], matter: { dates } as unknown as TaskData['matter'] });
    reply = (url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
        return { body: { task: shown, runs: [], children: [], parent: { id: 't5', title: matter.title, label: null, step: null } } };
      if (url.endsWith('/api/v2/tasks/t5')) return { body: { task: matter, runs: [], children: [] } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    // Date then label, one line; the expected dates come from the same formatter, so no clock is pinned.
    const line = `${matterWhen('2026-12-08T03:00:00Z')} Hearing · ${matterWhen('2026-12-08')} Reply due`;
    const strip = await screen.findByText(line);
    expect(screen.queryByText(/Old date/)).toBeNull();
    const body = await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    expect(strip.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the case’s open key dates above the email of a send task with no OK to give @approvals', async () => {
    // Its email is filed on the send task, which holds no matter: the strip reads the case the send answers.
    shown = task({ title: 'Send: Re: Notice of claim → dana@example.test', to_leave: [req({ standing: 'done', met: true, may: ['undo'], answer: { id: 'a9', value: 'ok', by: 'human:ana', at: '2026-11-19T10:00:00Z', note: null } })] });
    const dates = [{ id: 'd1', label: 'Reply due', starts_at: null, due_at: '2026-12-15', done_at: null }];
    const matter = task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [], matter: { dates } as unknown as TaskData['matter'] });
    reply = (url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
        return { body: { task: shown, runs: [], children: [], parent: { id: 't5', title: matter.title, label: null, step: null } } };
      if (url.endsWith('/api/v2/tasks/t5')) return { body: { task: matter, runs: [], children: [] } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect(await screen.findByText(`${matterWhen('2026-12-15')} Reply due`)).toBeTruthy();
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
  });

  it('shows nothing more when the matter has no note @approvals', async () => {
    const matter = task({ id: 't5', number: 40, title: 'Late-delivery claim: Dana Ruiz', pipeline_id: 'p9', to_leave: [] });
    reply = (url) => {
      if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1'))
        return { body: { task: shown, runs: [], children: [], parent: { id: 't5', title: matter.title, label: null, step: null } } };
      if (url.endsWith('/api/v2/tasks/t5')) return { body: { task: matter, runs: [], children: [] } };
      return null;
    };
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    await screen.findByText('Dear Ms. Ruiz, our response is attached.');
    await waitFor(() => expect(queryClient.getQueryData(keys.tasks.detail('t5'))).toBeTruthy());
    expect(screen.queryByRole('region', { name: 'Brief' })).toBeNull();
  });
});
