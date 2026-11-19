import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import type * as conversations from '@/conversation';
import { sendMessage } from '@/conversation';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { decisionName, effectOf, sendTitle } from './model';
import type { TaskData } from './queries';
import { TaskChat, TaskPage } from './TaskPage';

/* The decision (plan/approvals.html): the thing asked about is the first tab, one box decides it, Approve is bound to
   what was shown, Request changes is a message to the agent. Made-up rows only; every answer is stubbed. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
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
type Reply = { status?: number; body: unknown };
const sent: { url: string; body: Record<string, unknown> }[] = [];
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
  shown = task();
  reply = () => null;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const actors = [{ id: 'bot:lo', kind: 'agent', name: 'Legal Owner' }];
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
    const r = await (reply(String(url), method) ?? base(String(url)));
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

  it('keeps the decision being sent in view while a second approval waits', async () => {
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
    expect(await screen.findByText('Check the brief?')).toBeTruthy();
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
    await waitFor(() => expect(vi.mocked(sendMessage)).toHaveBeenCalledWith({ id: 'c7', variant: 'task', text: 'Ask for a replacement', me: 'human:me' }));
    expect(await within(chat).findByText(/^Changes asked/)).toBeTruthy();
    expect(within(chat).queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(within(chat).getByRole('textbox', { name: 'Message Legal Owner' })).toBeTruthy();
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

  it('when its preview does not load, says so in place of what Approve does, with Try again @approvals', async () => {
    shown = task({ title: titled });
    let busy = true;
    reply = (url) => (busy && url.endsWith('/api/v2/email/sends/s1') ? { status: 503, body: { error: { code: 'busy', detail: 'Tico is busy; retry shortly', retryable: true } } } : null);
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
    show(<TaskPage task="12" pipeline="p1" tab="chat" />, '/tasks/p1/12/chat');
    const chat = await screen.findByRole('region', { name: 'Chat with Legal Owner' });
    expect(within(chat).getByRole('region', { name: 'The decision' })).toBeTruthy();
    // one box: the page's own dock steps aside on the Chat tab
    expect(screen.getAllByRole('region', { name: 'The decision' })).toHaveLength(1);
  });
});
