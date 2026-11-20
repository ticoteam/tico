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
  }) as unknown as TaskData;

const matter = { id: 'm1', number: 7, title: 'Ruiz: reply letter', status: 'open', pipeline_id: 'legal', children: [], relations: { depends_on: [{ id: 't1', title: 'Send: Re: Notice of claim → dana@example.test', status: 'open', direction: 'out' }] }, to_leave: [] };

let theMatter: unknown = matter;

function answer(url: string): Reply {
  if (url.includes('/needs-you')) return { body: { actor: 'human:me', items: needs } };
  if (url.endsWith('/api/v2/tasks/t1')) return { body: { task: okTask('t1') } };
  if (url.endsWith('/api/v2/tasks/t2')) return { body: { task: okTask('t2', 'bot:other') } };
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
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
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
  it('names the case and who it goes to, shows the letter, and opens the case on the email', async () => {
    show({ agent: 'bot:legal-owner' });
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.textContent).toContain('Ruiz: reply letter, to Dana Ruiz');
    expect(card.textContent).toContain('ruiz-letter.pdf');
    expect(card.textContent).toContain('PDF');
    // The case's own address: it opens on the email waiting on the person.
    expect(card.getAttribute('href')).toBe('/task/m1');
    // At the time it was asked, from the agent.
    expect(card.parentElement?.dataset.at).toBe('2026-11-19T09:00:00Z');
    expect(card.parentElement?.dataset.from).toBe('bot:lo');
  });

  it('opens the case beside the thread it sits in, with a way back (plan/layers.html)', async () => {
    show({ agent: 'bot:lo' }, 'agent:lo');
    const card = await screen.findByRole('link', { name: /Approve/ });
    expect(card.getAttribute('href')).toBe('/task/m1?chat=agent%3Alo&via=thread');
    expect(card.dataset.target).toBe('approval:/task/m1');
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
    await waitFor(() => expect(card.getAttribute('href')).toBe('/tasks/general/40'));
  });

  it('an email already sent or canceled shows no card', async () => {
    state = 'canceled';
    show({ agent: 'bot:lo' });
    await waitFor(() => expect(queryClient.getQueryData(['tasks', 'detail', 't1', 'email', 's1'])).toBeTruthy());
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
