import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { flowQuery, type FlowItem, type FlowRequest } from '@/features/tasks/routing';
import { Toasts } from '@/kit/Toast';
import { answerAsk } from './queries';
import { RequestCard } from './Requests';

/* Needs you's requests without a task (master plan P1): an access request (Grant, Read only, Decline), a key card (the
   key, then Save) and a key-drop proposal (Grant selected). Each press goes to the kind's own route, and once the
   server takes it the card leaves Flow. Made-up rows only; every answer is stubbed. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));

const ACCESS: FlowRequest = {
  kind: 'access',
  id: 'access:r1',
  line: 'Growth Manager asks for Meta Ads · Read and write',
  since: '2026-11-20T09:00:00Z',
  from_id: 'bot:growth',
  access: { id: 'r1', state: 'open', subject: 'bot:growth', subject_name: 'Growth Manager', resource_type: 'tool', resource_name: 'Meta Ads', level: 'read_write', why: 'Pause 2 ad sets' },
};
const KEY: FlowRequest = {
  kind: 'key',
  id: 'key:k1',
  line: 'Tools Owner needs a new Stripe key',
  since: '2026-11-20T09:01:00Z',
  from_id: 'bot:tools',
  key: { id: 'k1', title: 'Tools Owner needs a new Stripe key', label: 'a new Stripe key', env: 'STRIPE_KEY', format: '', help_url: '', kind: 'api_key' },
};
const DROP: FlowRequest = {
  kind: 'key_drop',
  id: 'key_drop:m1',
  line: 'Grant JIRA_LOGIN to Jira; CLOSE_KEY to Close CRM',
  since: '2026-11-20T09:02:00Z',
  from_id: 'bot:tools',
  key_drop: {
    message_id: 'm1',
    state: 'proposed',
    losses: 'l0ss',
    rows: [
      { at: 0, key: 'JIRA_LOGIN', tool_name: 'Jira', level: 'read', bots: [{ id: 'bot:builder', name: 'Builder' }], replaces: null },
      {
        at: 1,
        key: 'CLOSE_KEY',
        tool_name: 'Close CRM',
        level: 'read_write',
        bots: [{ id: 'bot:sales', name: 'Sales Owner' }],
        replaces: { key: 'Close old', keeps: ['Builder'], loses: ['Tools Owner'] },
      },
    ],
  },
};

type Reply = { status?: number; body: unknown };
const sent: { url: string; body: Record<string, unknown> }[] = [];
let waiting: FlowRequest[] = [];
let reply: (url: string) => Reply | Promise<Reply> | null = () => null;

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  sent.length = 0;
  reply = () => null;
  waiting = [ACCESS, KEY, DROP];
  queryClient.clear();
  queryClient.setQueryData(flowQuery.queryKey, { count: 3, items: [], requests: waiting, task_ids: [] });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    let r: Reply = { body: {} };
    if (method !== 'GET') {
      sent.push({ url: String(url), body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
      r = (await reply(String(url))) ?? { body: {} };
      if ((r.status ?? 200) === 200 && (r.body as { state?: string }).state !== 'open') waiting = waiting.filter((it) => !String(url).includes(`/${it.id.split(':')[1]}/`));
    } else if (String(url).includes('/needs-you')) r = { body: { actor: 'human:me', items: waiting, next: null } };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

function Requests() {
  const { data } = useQuery(flowQuery);
  return (data?.requests ?? []).map((it) => <RequestCard key={it.id} it={it} />);
}

function show() {
  render(
    <QueryClientProvider client={queryClient}>
      <Toasts />
      <Requests />
    </QueryClientProvider>,
  );
}

const card = (name: string) => screen.findByRole('region', { name });
const gone = (name: string) => waitFor(() => expect(screen.queryByRole('region', { name })).toBeNull());

describe('an access request in Needs you', () => {
  it('says who asks for what and why; Grant gives what was asked and the card leaves', async () => {
    reply = () => ({ body: { state: 'granted', level: 'read_write' } });
    queryClient.setQueryData(['tools', 'index', 'vendor', 'meta'], { vendor: 'meta', name: 'Meta', rows: [] });
    show();
    const box = await card('Access request');
    expect(within(box).getByText('Growth Manager asks for Meta Ads')).toBeTruthy();
    expect(within(box).getByText('Read and write')).toBeTruthy();
    expect(within(box).getByText('Pause 2 ad sets')).toBeTruthy();
    expect(within(box).getAllByRole('button').map((b) => b.textContent)).toEqual(['Decline', 'Read only', 'Grant']);
    fireEvent.click(within(box).getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/access-requests/r1/grant', body: {} }]));
    await gone('Access request');
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Granted: Meta Ads'));
    // Tools reads what the grant changed: its list, the vendor's access lists, the tool's page.
    expect(queryClient.getQueryState(['tools', 'index', 'vendor', 'meta'])?.isInvalidated).toBe(true);
  });

  it('says what the server settled: Read only, or a decline someone else made first', async () => {
    reply = () => ({ body: { state: 'granted', level: 'read' } });
    show();
    fireEvent.click(within(await card('Access request')).getByRole('button', { name: 'Read only' }));
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Granted read only: Meta Ads'));
    reply = () => ({ body: { state: 'declined', level: 'read_write' } });
    waiting = [ACCESS];
    queryClient.setQueryData(flowQuery.queryKey, { count: 1, items: [], requests: waiting, task_ids: [] });
    fireEvent.click(within(await card('Access request')).getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Declined by someone else: Meta Ads'));
  });

  it('Read only gives read alone; Decline declines', async () => {
    show();
    fireEvent.click(within(await card('Access request')).getByRole('button', { name: 'Read only' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/access-requests/r1/grant', body: { level: 'read' } }]));
    await gone('Access request');
    waiting = [ACCESS];
    queryClient.setQueryData(flowQuery.queryKey, { count: 1, items: [], requests: waiting, task_ids: [] });
    fireEvent.click(within(await card('Access request')).getByRole('button', { name: 'Decline' }));
    await waitFor(() => expect(sent.at(-1)).toEqual({ url: '/api/v2/access-requests/r1/decline', body: {} }));
    await gone('Access request');
  });

  it('holds every button while one answer is with the server', async () => {
    let answer!: (r: Reply) => void;
    const pending = new Promise<Reply>((done) => (answer = done));
    reply = () => pending;
    show();
    const box = await card('Access request');
    fireEvent.click(within(box).getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(within(box).getAllByRole('button').every((b) => (b as HTMLButtonElement).disabled)).toBe(true));
    fireEvent.click(within(box).getByRole('button', { name: 'Decline' }));
    expect(sent.map((s) => s.url)).toEqual(['/api/v2/access-requests/r1/grant']);
    answer({ body: { state: 'granted', level: 'read_write' } });
  });

  it('a refusal is said in the card, which stays', async () => {
    reply = () => ({ status: 403, body: { error: { code: 'forbidden', detail: 'You can’t grant this', retryable: false } } });
    show();
    const box = await card('Access request');
    fireEvent.click(within(box).getByRole('button', { name: 'Grant' }));
    expect((await within(box).findByRole('alert')).textContent).toBe('You can’t grant this');
    expect(screen.getByRole('region', { name: 'Access request' })).toBeTruthy();
  });
  it('a grant that did not take effect yet keeps the card and says so', async () => {
    reply = () => ({ body: { state: 'open', level: 'read_write' } });
    show();
    const box = await card('Access request');
    fireEvent.click(within(box).getByRole('button', { name: 'Grant' }));
    expect((await within(box).findByRole('alert')).textContent).toBe('The grant didn’t take effect yet');
    expect(screen.getByRole('region', { name: 'Access request' })).toBeTruthy();
  });
});

describe('Flow’s count', () => {
  it('keeps the requests when a task’s ask is answered', async () => {
    queryClient.setQueryData(flowQuery.queryKey, { count: 2, items: [{ id: 't1' } as FlowItem], requests: [ACCESS], task_ids: ['t1'] });
    const answered = answerAsk('t1', 'q1', 'ok');
    expect(queryClient.getQueryData<{ count: number }>(flowQuery.queryKey)?.count).toBe(1);
    await answered.catch(() => undefined);
  });
});

describe('a key card in Needs you', () => {
  it('Save waits for the key, sends it to its card alone, and the card leaves', async () => {
    show();
    const box = await card('Key');
    expect(within(box).getByText('Tools Owner needs a new Stripe key')).toBeTruthy();
    const save = within(box).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(box).getByLabelText('Key'), { target: { value: ' sk-synthetic-0011 ' } });
    fireEvent.click(save);
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/credential-requests/k1/save', body: { value: 'sk-synthetic-0011' } }]));
    await gone('Key');
  });
});

describe('a key-drop proposal in Needs you', () => {
  it('lists each key, its tool, level and agents, ticked; Grant selected grants the rows left ticked', async () => {
    reply = () => ({
      body: { state: 'granted', rows: [{ key: 'JIRA_LOGIN', tool_name: 'Jira' }], results: [{ key: 'JIRA_LOGIN', ok: false, detail: 'Didn’t work: 401' }] },
    });
    show();
    const box = await card('Key proposal');
    expect(within(box).getByText('2 keys')).toBeTruthy();
    const rows = within(box).getAllByRole('checkbox');
    expect(rows.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'true']);
    expect(box.textContent).toContain('Close CRM · Read and write · Sales OwnerReplaces Close old · Tools Owner lose access');
    fireEvent.click(within(box).getByText('Close CRM', { exact: false }));
    fireEvent.click(within(box).getByRole('button', { name: 'Grant selected' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/tools-chat/proposals/m1/grant', body: { keep: [0], seen: 'l0ss', positions: 'original' } }]));
    await gone('Key proposal');
    await waitFor(() => expect(document.querySelector('.toasts')?.textContent).toContain('Granted, 1 not connected: JIRA_LOGIN for Jira'));
  });

  it('sends only the rows still on the card when a tool is deleted after it was drawn', async () => {
    show();
    const box = await card('Key proposal');
    const [, close] = DROP.key_drop?.rows ?? [];
    waiting = [{ ...DROP, key_drop: { ...DROP.key_drop!, rows: [{ ...close!, at: 1 }] } }];
    queryClient.setQueryData(flowQuery.queryKey, { count: 1, items: [], requests: waiting, task_ids: [] });
    await waitFor(() => expect(within(box).getAllByRole('checkbox')).toHaveLength(1));
    fireEvent.click(within(box).getByRole('button', { name: 'Grant selected' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/tools-chat/proposals/m1/grant', body: { keep: [1], seen: 'l0ss', positions: 'original' } }]));
  });
});
