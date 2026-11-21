import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { approvalCard, useApprovalCards, type ApprovalScope } from './ThreadCards';
import type { TaskData } from './queries';

/* An approval an agent asked for, as a card in its thread: it names the case and who the email goes to, shows the
   letter, opens the email, and never decides. Made-up rows only; every answer is stubbed. */

type Reply = { status?: number; body: unknown };
const asked: string[] = [];
/** Every address read. */
const read: string[] = [];

const ask = (task: string, by: string, send: string, since = '2026-11-19T09:00:00Z') => ({
  kind: 'ask',
  id: `${task}:r1`,
  task_id: task,
  line: 'Send this email?',
  since,
  task: { id: task, number: 40, title: 'Send: Re: Notice of claim → dana@example.test', pipeline_id: 'general', stage_id: 'todo', owner_id: by },
  ask: { requirement_id: 'r1', kind: 'ok', line: 'Send this email?', met: false, asked_by: by, shows: [`email:${send}`], may: ['ok', 'no'] },
});

let needs: unknown[] = [];
let state = 'waiting_approval';

const okTask = (id: string, by = 'bot:lo') =>
  ({
    id,
    number: 40,
    title: 'Send: Re: Notice of claim → dana@example.test',
    status: 'open',
    pipeline_id: 'general',
    owner_id: by,
    children: [],
    relations: { depends_on: [{ id: 'm1', number: 7, title: 'Ruiz: reply letter', status: 'open', direction: 'in' }] },
    to_leave: [{ requirement_id: 'r1', kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: ['email:s1'], asked_by: by }],
    email_send: { id: 's1', state },
  }) as unknown as TaskData;

const matter = { id: 'm1', number: 7, title: 'Ruiz: reply letter', status: 'open', pipeline_id: 'legal', children: [], relations: { depends_on: [{ id: 't1', title: 'Send: Re: Notice of claim → dana@example.test', status: 'open', direction: 'out' }] }, to_leave: [] };

let theMatter: unknown = matter;

/* Other OKs: a page the task's stage needs approved (asked by no one, held by the task's owner) and a finance item the
   agent asked for by its slug. */
const otherAsk = (task: string, kind: 'page' | 'ok', by: string | null, owner: string, shows: string[]) => ({
  kind: 'ask',
  id: `${task}:r2`,
  task_id: task,
  line: 'OK this?',
  since: '2026-11-19T10:00:00Z',
  task: { id: task, number: 41, title: kind === 'page' ? 'Release report' : 'Pay the rent', pipeline_id: 'ops', stage_id: 'review', owner_id: owner },
  ask: { requirement_id: 'r2', kind, line: 'OK this?', met: false, asked_by: by, shows, may: ['ok', 'no'] },
});
const otherTask = (id: string, title: string, shows: string[]) => ({
  id,
  number: 41,
  title,
  status: 'open',
  pipeline_id: 'ops',
  owner_id: 'bot:lo',
  children: [],
  to_leave: [{ requirement_id: 'r2', kind: 'page', line: 'OK this?', met: false, may: ['ok', 'no'], shows, asked_by: null }],
});

function answer(url: string): Reply {
  if (url.includes('/needs-you')) return { body: { actor: 'human:me', items: needs } };
  if (url.endsWith('/api/v2/tasks/t1')) return { body: { task: okTask('t1') } };
  if (url.endsWith('/api/v2/tasks/t2')) return { body: { task: okTask('t2', 'bot:other') } };
  if (url.endsWith('/api/v2/tasks/t4')) {
    const ok = (rid: string, send: string) => ({ requirement_id: rid, kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: [`email:${send}`], asked_by: 'bot:lo' });
    return { body: { task: { ...okTask('t4'), to_leave: [ok('r1', 's1'), ok('r5', 's5')] } } };
  }
  // a case whose OK is on itself: its newest send (s2) was canceled, the one its OK shows (s1) still waits
  if (url.endsWith('/api/v2/tasks/c1'))
    return { body: { task: { ...okTask('c1'), title: 'Ruiz: reply letter', relations: {}, email_send: { id: 's2', state: 'canceled' } }, parent: { id: 'p0', title: 'Legal 2026' } } };
  // a case with two emails waiting on OKs of its own, and an older question
  if (url.endsWith('/api/v2/tasks/c2/files?all=1'))
    return { body: { files: [{ id: 'f-email', name: 'email.md', title: 'Email', mime: 'text/markdown', archived: false, current_version: 1, links: [], versions: [], writers: [] }] } };
  if (url.endsWith('/api/v2/tasks/c2')) {
    const two = (rid: string, send: string) => ({ requirement_id: rid, kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: [`email:${send}`], asked_by: 'bot:lo' });
    return { body: { task: { ...okTask('c2'), title: 'Ruiz: two letters', relations: {}, to_leave: [two('r1', 's1'), two('r3', 's3')] } } };
  }
  if (url.endsWith('/api/v2/tasks/p1')) return { body: { task: otherTask('p1', 'Release report', ['page:file-a@2']) } };
  if (url.endsWith('/api/v2/tasks/p1/files?all=1'))
    return { body: { files: [{ id: 'file-a', name: 'release-report.md', title: 'Release report', mime: 'text/markdown', archived: false, current_version: 2, links: [], versions: [], writers: [] }, { id: 'file-b', name: 'notes.md', title: 'Notes', mime: 'text/markdown', archived: false, current_version: 1, links: [], versions: [], writers: [] }] } };
  if (url.endsWith('/api/v2/tasks/f1')) return { body: { task: otherTask('f1', 'Pay the rent', ['finance:x1']) } };
  if (url.endsWith('/api/v2/tasks/m1')) return { body: { task: theMatter, children: (theMatter as { children: unknown[] }).children } };
  if (url.endsWith('/api/v2/email/sends/s1'))
    return {
      body: {
        send: { id: 's1', subject: 'Re: Notice of claim', task_id: 't1', state, recipients: ['Dana Ruiz <dana@example.test>'], attachments: [{ part: 1, name: 'ruiz-letter.pdf', size: 4096, type: 'application/pdf' }] },
        body: '',
        bodies: [],
        preview_complete: true,
      },
    };
  return { status: 404, body: {} };
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  asked.length = 0;
  needs = [ask('t1', 'bot:lo', 's1')];
  state = 'waiting_approval';
  theMatter = matter;
  queryClient.clear();
  const actors = [{ id: 'bot:lo', kind: 'agent', name: 'Legal Owner', slug: 'legal-owner' }];
  queryClient.setQueryData(['directory'], { actors, byId: { 'bot:lo': actors[0], 'bot:legal-owner': actors[0] } });
  read.length = 0;
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    read.push(String(url));
    if ((init?.method ?? 'GET') !== 'GET') asked.push(String(url));
    const r = answer(String(url));
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

/** The rows a thread gets, drawn as the conversation draws them: each through `approvalCard`. */
function Thread({ scope, chat }: { scope: ApprovalScope; chat?: string }) {
  const rows = useApprovalCards(scope, chat);
  return (
    <div role="log">
      {rows.map((m) => (
        <div key={m.id} data-from={m.sender_id} data-at={m.created_at}>
          {approvalCard(m)}
        </div>
      ))}
    </div>
  );
}

const show = (scope: ApprovalScope, chat?: string) =>
  render(
    <QueryClientProvider client={queryClient}>
      <Thread scope={scope} chat={chat} />
    </QueryClientProvider>,
  );

describe('an approval waiting, as a card in the agent’s thread @approvals', () => {
  it('names the case and who it goes to from the OK task, never reading the email, and opens the case on it', async () => {
    show({ agent: 'bot:legal-owner' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.textContent).toContain('Ruiz: reply letter, to dana@example.test');
    // its preview is read only on the Email tab: that read counts as showing the email
    expect(read.filter((u) => u.includes('/email/sends/'))).toEqual([]);
    // The case's own address: it opens on the email waiting on the person.
    // the case's tab for this email, as the case's page names it
    expect(card.getAttribute('href')).toBe('/tasks/legal/7/email');
    // At the time it was asked, from the agent.
    expect(card.parentElement?.dataset.at).toBe('2026-11-19T09:00:00Z');
    expect(card.parentElement?.dataset.from).toBe('bot:lo');
  });

  it('opens the case beside the thread it sits in, with a way back (plan/layers.html)', async () => {
    show({ agent: 'bot:lo' }, 'agent:lo');
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.getAttribute('href')).toBe('/tasks/legal/7/email?chat=agent%3Alo&via=thread');
    expect(card.dataset.target).toBe('approval:/tasks/legal/7/email');
  });

  it('a page its task needs approved is a card in the owner’s thread, the page as a chip', async () => {
    needs = [otherAsk('p1', 'page', null, 'bot:lo', ['page:file-a@2'])];
    show({ agent: 'bot:lo' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.textContent).toContain('Release report');
    expect(card.querySelectorAll('.file')).toHaveLength(1);
    expect(card.querySelector('.file em')?.textContent).toBe('MD');
    expect(card.getAttribute('href')).toBe('/tasks/ops/41');
    expect(card.parentElement?.dataset.from).toBe('bot:lo');
  });

  it('an OK the agent asked for by its slug, on a finance item, is a card with no chips', async () => {
    needs = [otherAsk('f1', 'ok', 'bot:legal-owner', 'bot:lo', ['finance:x1'])];
    show({ agent: 'bot:lo' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.textContent).toContain('Pay the rent');
    expect(card.querySelector('.file')).toBeNull();
  });

  it('has no Approve of its own: nothing decides from the thread', async () => {
    show({ agent: 'bot:lo' });
    await screen.findByRole('link', { name: /Approve/ });
    expect(screen.queryByRole('button')).toBeNull();
    expect(asked).toEqual([]);
  });

  it('a case with two emails waiting opens the email’s own page, not a guess at the tab', async () => {
    theMatter = { ...matter, children: [{ id: 't1', title: 'Send: A → a@example.test', status: 'open' }, { id: 't3', title: 'Send: B → b@example.test', status: 'open' }] };
    show({ agent: 'bot:lo' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    await waitFor(() => expect(card.getAttribute('href')).toBe('/tasks/general/40/email'));
  });

  it('two OKs on one "Send:" task open two different emails on its own page', async () => {
    theMatter = { ...matter, children: [{ id: 't1', title: 'Send: A → a@example.test', status: 'open' }, { id: 't3', title: 'Send: B → b@example.test', status: 'open' }] };
    needs = [ask('t4', 'bot:lo', 's1'), { ...ask('t4', 'bot:lo', 's5'), id: 't4:r5' }];
    show({ agent: 'bot:lo' });
    await waitFor(() => expect(screen.getAllByRole('link', { name: /Approve/ })).toHaveLength(2));
    const hrefs = screen.getAllByRole('link', { name: /Approve/ }).map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(['/tasks/general/40/email', '/tasks/general/40/email-2']);
  });

  it('a case whose newer send was canceled still shows the card of the email its OK waits on, named as the case', async () => {
    needs = [ask('c1', 'bot:lo', 's1')];
    show({ agent: 'bot:lo' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.textContent).toContain('Ruiz: reply letter'); // the case itself, never its parent
    expect(card.textContent).not.toContain('Legal 2026');
    // its own email's tab on the case, never a guess at another send's
    expect(card.getAttribute('href')).toBe('/tasks/general/40/email');
  });

  it('two email OKs on one case are two cards, each opening its own email, and an older question hides neither', async () => {
    const question = { ...ask('c2', 'bot:lo', 's1', '2026-11-19T08:00:00Z'), ask: { requirement_id: 'q1', kind: 'question', line: 'Which date?', met: false, asked_by: 'bot:lo', shows: [], may: [] } };
    needs = [question, ask('c2', 'bot:lo', 's1'), { ...ask('c2', 'bot:lo', 's3'), id: 'c2:r3' }];
    show({ agent: 'bot:lo' });
    await waitFor(() => expect(screen.getAllByRole('link', { name: /Approve/ })).toHaveLength(2));
    const hrefs = screen.getAllByRole('link', { name: /Approve/ }).map((a) => a.getAttribute('href'));
    // as the task's page names its tabs, files included: its file "Email" took the first slug
    expect(hrefs).toEqual(['/tasks/general/40/email-2', '/tasks/general/40/email-2-2']);
  });

  it('a case with no "Send:" task shows the card of the email OK on itself in its own chat', async () => {
    needs = [ask('c1', 'bot:lo', 's1')];
    const own = { id: 'c1', title: 'Ruiz: reply letter', children: [], relations: {}, to_leave: [{ requirement_id: 'r1', kind: 'ok', met: false, may: ['ok', 'no'], shows: ['email:s1'] }] };
    show({ task: own as unknown as TaskData });
    expect((await screen.findByRole('link', { name: /Approve/ })).textContent).toContain('Ruiz: reply letter');
  });

  it('a new email OK on a case shows its card in the case’s chat, the case read again when its cached read lacks it', async () => {
    needs = [ask('c1', 'bot:lo', 's1')];
    const cached = { ...okTask('c1'), title: 'Ruiz: reply letter', relations: {}, to_leave: [] };
    queryClient.setQueryData(['tasks', 'detail', 'c1'], cached);
    show({ task: cached as unknown as TaskData });
    expect(await screen.findByRole('link', { name: /Approve/ })).toBeTruthy();
  });

  it('a case’s chat shows the card of an email OK a person asked for, too', async () => {
    needs = [ask('c1', 'human:omar', 's1')];
    const own = { id: 'c1', title: 'Ruiz: reply letter', children: [], relations: {}, to_leave: [{ requirement_id: 'r1', kind: 'ok', met: false, may: ['ok', 'no'], shows: ['email:s1'] }] };
    show({ task: own as unknown as TaskData });
    expect(await screen.findByRole('link', { name: /Approve/ })).toBeTruthy();
  });

  it('an email already sent or canceled shows no card', async () => {
    state = 'canceled';
    show({ agent: 'bot:lo' });
    await waitFor(() => expect(queryClient.getQueryData(['tasks', 'detail', 't1'])).toBeTruthy());
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('another agent’s ask stays out of this agent’s thread', async () => {
    needs = [ask('t2', 'bot:other', 's1')];
    show({ agent: 'bot:lo' });
    await waitFor(() => expect(queryClient.getQueryData(['flow'])).toBeTruthy());
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('a case’s chat shows the emails hung on it', async () => {
    show({ task: matter as unknown as TaskData });
    expect(await screen.findByRole('link', { name: /Approve/ })).toBeTruthy();
  });

  it('drops off once answered', async () => {
    show({ agent: 'bot:lo' });
    await screen.findByRole('link', { name: /Approve/ });
    needs = [];
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] }));
    await waitFor(() => expect(screen.queryByRole('link')).toBeNull());
  });
});
