import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data';
import { SupportChat } from '@/features/support/exports';
import { agentsQuery, computersQuery as teamComputers } from '@/features/team/exports';
import { Toasts } from '@/kit/Toast';
import { Computers } from './Agents';
import { useDestination } from './Destination';
import {
  botAccessQuery,
  botMailQuery,
  botsQuery,
  changelogQuery,
  computersQuery,
  contactTypeQuery,
  contactTypesQuery,
  healthQuery,
  hostedQuery,
  labelsQuery,
  limitsQuery,
  modelsQuery,
  roadmapQuery,
  stoppedRunsQuery,
  subscriptionsQuery,
  withDestination,
  type DestinationChoice,
} from './queries';
import { SettingsPage } from './SettingsPage';
import { UsagePage } from './Usage';

/* Tier 2 parity rows of Settings, checked against the old app: one test per row, tagged with its id. */

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method) ?? { status: 200, body: {} };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown): Reply => ({ status: 200, body });

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
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

/** The page as its route draws it: the opened row comes from the address. */
function Opened({ section }: { section: 'contacts' | 'changelog' }) {
  const { open } = useSearch({ strict: false }) as { open?: string };
  return <SettingsPage section={section} open={open} />;
}

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const ACTORS = [
  { id: 'bot:a1', kind: 'agent', name: 'Alpha Agent', slug: 'alpha', team: 'Product' },
  { id: 'bot:b2', kind: 'agent', name: 'Beta Agent', slug: 'beta', team: 'Growth' },
  { id: 'human:p1', kind: 'person', name: 'Ana Test' },
];
const ROWS = [
  { id: 'a1', slug: 'alpha', harness: 'claude', model: 'model-one', resolved_model_label: 'Model One', revision: 3, can_manage: true },
  { id: 'b2', slug: 'beta', harness: 'claude', model: 'model-one', resolved_model_label: 'Model One', revision: 5, can_manage: true },
];
const NONE = { daily_usd: null, hourly_usd: null, monthly_usd: null, per_30m_usd: null, weekly_usd: null };
const LIST = { can_add: false, can_review: false, current_version: '0.0.2', drafts: [], next_offset: null, unread_count: 0 };
const ENTRY = { area: 'tasks', bullets: [], shipped_at: '2026-01-02T00:00:00Z' };
const EVERY = { everyone: true, people: [], teams: [], bots: [] };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
  queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [] });
  queryClient.setQueryData(agentsQuery.queryKey, ROWS as never);
  queryClient.setQueryData(modelsQuery.queryKey, { models: [], enabled_providers: [], harnesses: [], default: {} });
  queryClient.setQueryData(limitsQuery.queryKey, { bots: {}, default: { ...NONE, count_subscription: false }, may_edit_default: true, pools: null });
  queryClient.setQueryData(botsQuery.queryKey, []);
});
afterEach(() => vi.unstubAllGlobals());

// ---------------------------------------------------------------- Agents and computers

describe('Settings › Agents, archived agents @s693', () => {
  it('lists a retired agent its reader may bring back, and Restore sends it back', async () => {
    serve();
    queryClient.setQueryData(botsQuery.queryKey, [{ id: 'c3', slug: 'gamma', display_name: 'Gamma Agent', state: 'archived', owner_id: 'p1' }]);
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: /Retired/ }));
    const row = await screen.findByTitle('Gamma Agent');
    fireEvent.click(within(row).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/c3/restore', method: 'POST' }));
  });
});

describe('Settings, Agents and computers, a trial @n:se:settings-fleet', () => {
  it('groups the agents by team, and by computer when asked', async () => {
    serve();
    queryClient.setQueryData(teamComputers.queryKey, [{ id: 'r1', label: 'Shared One', online: true }] as never);
    queryClient.setQueryData(agentsQuery.queryKey, [{ ...ROWS[0], machine: { runner_id: 'r1', label: 'Shared One', generation: 1 } }, ROWS[1]] as never);
    show(<SettingsPage section="agents" />, '/settings/agents');
    expect(await screen.findByRole('region', { name: /Product/ })).toBeTruthy();
    expect(screen.getByRole('region', { name: /Growth/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Computer' }));
    expect(await screen.findByRole('region', { name: /Shared One/ })).toBeTruthy();
    expect(screen.getByRole('region', { name: /No computer/ })).toBeTruthy();
  });
});

describe('Settings › Computers, external agents @s703', () => {
  it('is one dimmed Coming soon line with nothing to press', async () => {
    serve();
    queryClient.setQueryData(computersQuery.queryKey, [{ id: 'r1', label: 'Studio', online: true }] as never);
    show(<Computers />, '/settings/computers');
    const section = await screen.findByRole('region', { name: 'External agents' });
    expect(within(section).getByText('Coming soon')).toBeTruthy();
    expect(within(section).getByText('Agents made and run outside Tico').closest('.row')?.getAttribute('aria-disabled')).toBe('true');
    expect(within(section).queryByRole('button', { name: /pair|add|connect/i })).toBeNull();
    expect(within(section).queryByRole('link')).toBeNull();
  });
});

describe('Settings › Computers, hosted servers @s704', () => {
  it('a stopped server starts for an owner, says why when held, and Tico cannot start it', async () => {
    serve((url) => (url === '/api/v2/hosted-servers/h1/start' ? ok({ state: 'paused', message: 'Out of credit' }) : undefined));
    queryClient.setQueryData(computersQuery.queryKey, [] as never);
    queryClient.setQueryData(hostedQuery.queryKey, { state: 'ok', servers: [{ server_id: 'h1', kind: 'tico', power: 'stopped' }] });
    show(<Computers />, '/settings/computers');
    const row = await screen.findByText('Tico server');
    const line = row.closest('.row') as HTMLElement;
    expect(within(line).getByText('Stopped')).toBeTruthy();
    fireEvent.click(within(line).getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/hosted-servers/h1/start', method: 'POST' }));
    expect(await within(line).findByText('Out of credit')).toBeTruthy();
    sent = [];
    expect(await run('settings.startServer', { server: 'h1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });
});

describe('Settings › Agents › an opened agent', () => {
  const open = (stopped: unknown[] = []) => {
    queryClient.setQueryData(botsQuery.queryKey, [{ id: 'a1', slug: 'alpha', display_name: 'Alpha Agent', state: 'active', owner_id: 'human:p1', bot_owners: [] }]);
    queryClient.setQueryData(botAccessQuery('a1').queryKey, { bot: 'a1', see: EVERY, read: EVERY, write: EVERY, revision: 2 });
    queryClient.setQueryData(botMailQuery('a1').queryKey, { outbound_send: false, company_send: true } as never);
    queryClient.setQueryData(stoppedRunsQuery('a1').queryKey, stopped as never);
    show(<SettingsPage section="agents" open="alpha" />, '/settings/agents?open=alpha');
  };

  it('mail: the switch lets it send without asking; Tico cannot @s1111', async () => {
    serve();
    open();
    fireEvent.click(await screen.findByRole('switch', { name: 'Send mail without asking' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/bots/a1/mail-settings', method: 'POST', body: { outbound_send: true } }));
    sent = [];
    expect(await run('settings.agentMail', { agent: 'a1', on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });

  it('stopped runs: Review opens them, Try again reconciles with the note; Tico cannot @s1110', async () => {
    serve();
    open([{ id: 'j1', run_id: 'r9', stopped: 'changed', reason: 'The computer restarted', task: { title: 'Send the weekly note' } }]);
    const line = (await screen.findByText('Stopped runs')).closest('.fact') as HTMLElement;
    fireEvent.click(within(line).getByRole('button', { name: 'Review' }));
    const dlg = await screen.findByRole('dialog', { name: 'Stopped runs' });
    expect(within(dlg).getByText('Send the weekly note')).toBeTruthy();
    expect(within(dlg).getByText('Trying again could repeat work that already finished.')).toBeTruthy();
    fireEvent.change(within(dlg).getByLabelText('Note for the agent'), { target: { value: 'Nothing was sent' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/jobs/j1/reconcile', method: 'POST', body: { run_id: 'r9', decision: 'resume' } }));
    expect((sent.at(-1)?.body as { note: string }).note).toContain('Details: Nothing was sent');
    sent = [];
    expect(await run('settings.stoppedRun', { agent: 'a1', job: 'j1', run: 'r9', decision: 'dismiss' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(sent).toHaveLength(0);
  });
});

// ---------------------------------------------------------------- Tasks and Contacts

describe('Settings › Tasks @s1077', () => {
  it('lists the public roadmap labels with their address, adds one, and Tico cannot change what the public sees', async () => {
    serve((url) => (url === '/api/v2/roadmaps/settings' ? ok({ labels: ['feature', 'idea'], bot_public_approval: true }) : undefined));
    queryClient.setQueryData(roadmapQuery.queryKey, { labels: ['feature'], bot_public_approval: true });
    queryClient.setQueryData(labelsQuery.queryKey, [
      { id: '1', key: 'feature', label: 'Feature' },
      { id: '2', key: 'idea', label: 'Idea' },
    ] as never);
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const road = await screen.findByRole('region', { name: /Public roadmap/ });
    expect(within(road).getByText(/\/public\/roadmaps\/feature$/)).toBeTruthy();
    expect(within(road).getByRole('switch', { name: "Ask before an agent's words go public" })).toBeTruthy();
    await pick(within(road).getByRole('combobox', { name: 'Add a label' }), /^Idea$/);
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/roadmaps/settings', method: 'POST', body: { labels: ['feature', 'idea'] } }));
    expect(await run('settings.roadmapLabels', { labels: ['feature', 'idea', 'secret'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Settings › Contacts @s1078', () => {
  it('lists the types with their counts; an opened type shows its fields and Add field sends one', async () => {
    serve();
    queryClient.setQueryData(contactTypesQuery.queryKey, [
      { key: 'organization', name: 'Organization', built_in: true, count: 1200, approximate: true },
      { key: 'person', name: 'Person', built_in: true, count: 3, approximate: false },
    ]);
    queryClient.setQueryData(contactTypeQuery('organization').queryKey, {
      attributes: [{ key: 'domain', name: 'Domain', value_type: 'domain', owner: 'tico', required: false }],
    });
    show(<Opened section="contacts" />, '/settings/contacts?open=organization');
    const org = await screen.findByTitle('Person');
    expect(within(org).getByText('3')).toBeTruthy();
    expect(org.getAttribute('href')).toBe('/settings/contacts?open=person');
    expect(within(screen.getByTitle('Organization')).getByText('1,200+')).toBeTruthy();
    const fields = await screen.findByRole('region', { name: /Organization fields/ });
    expect(within(fields).getByTitle('Domain')).toBeTruthy();
    fireEvent.click(within(fields).getByRole('button', { name: 'Add field' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add a field to Organization' });
    fireEvent.change(within(dlg).getByLabelText('Name'), { target: { value: 'Ships to' } });
    fireEvent.change(within(dlg).getByLabelText('What it holds'), { target: { value: 'Where orders go' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/v2/entities/types/organization/attributes',
        method: 'POST',
        body: { key: 'ships_to', name: 'Ships to', value_type: 'text', description: 'Where orders go' },
      }),
    );
  });
});

// ---------------------------------------------------------------- Health and Changelog

describe('Settings › Health @s1082', () => {
  it('lists each check with its state, marks the broken one and links its fix', async () => {
    serve();
    queryClient.setQueryData(healthQuery.queryKey, {
      checked: '2026-01-02T00:00:00Z',
      attention: 1,
      checks: [
        { id: 'db', label: 'Database', status: 'ok', summary: 'Answers in 12 ms' },
        { id: 'mail', label: 'Mail', status: 'bad', summary: 'Nothing received today', fixes: [{ label: 'Read how', href: 'https://example.test/mail' }] },
      ],
    } as never);
    show(<SettingsPage section="health" />, '/settings/health');
    const mail = await screen.findByTitle('Mail');
    expect(mail.className).toContain('is-bad');
    expect(within(mail).getByText('Broken')).toBeTruthy();
    expect(within(mail).getByRole('link', { name: 'Read how' }).getAttribute('href')).toBe('https://example.test/mail');
    expect(within(screen.getByTitle('Database')).getByText('Fine')).toBeTruthy();
  });
});

describe('Settings › Changelog @s1068', () => {
  it('groups the product updates by version, newest first', async () => {
    serve();
    queryClient.setQueryData(changelogQuery.queryKey, {
      ...LIST,
      entries: [
        { ...ENTRY, kind: 'product', id: 'e1', title: 'Flow asks one at a time', version: '0.0.2' },
        { ...ENTRY, kind: 'product', id: 'e2', title: 'Fix a late badge', version: '0.0.2', area: 'fix' },
        { ...ENTRY, kind: 'product', id: 'e3', title: 'Calls ring on the phone', version: '0.0.1' },
      ],
    } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    const newest = await screen.findByRole('region', { name: /0\.0\.2/ });
    expect(within(newest).getByText('Flow asks one at a time')).toBeTruthy();
    expect(within(newest).getByText('Fixed')).toBeTruthy();
    const older = screen.getByRole('region', { name: /0\.0\.1/ });
    expect(within(older).getByText('Calls ring on the phone')).toBeTruthy();
    expect(newest.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('Add update publishes a title and its bullets @s1120', async () => {
    serve();
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, can_add: true, entries: [] } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    fireEvent.click(await screen.findByRole('button', { name: 'Add update' }));
    const dlg = await screen.findByRole('dialog', { name: 'Product update' });
    const publish = within(dlg).getByRole('button', { name: 'Publish' });
    expect(publish.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dlg).getByLabelText('Title'), { target: { value: 'Calls on the phone' } });
    fireEvent.change(within(dlg).getByLabelText('Bullets, one a line'), { target: { value: '- Ring on the phone\n- Answer from the lock screen' } });
    fireEvent.click(publish);
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/changelog',
        method: 'POST',
        body: { title: 'Calls on the phone', bullets: ['Ring on the phone', 'Answer from the lock screen'] },
      }),
    );
  });
});

// ---------------------------------------------------------------- Usage, a destination, search

describe('Usage › Limits, clear the automatic queue @s1115', () => {
  it('asks first, then clears; Tico cannot', async () => {
    serve((url) => (url === '/api/v2/usage/queues/purge' ? ok({ cancelled: 3, kept_people: 1, still_running: 0 }) : undefined));
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    fireEvent.click(await screen.findByRole('button', { name: 'Clear queue' }));
    const dlg = await screen.findByRole('alertdialog', { name: 'Clear the automatic queue?' });
    expect(sent).toHaveLength(0);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Clear queue' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/usage/queues/purge', method: 'POST', body: { scope: 'company' } }));
    sent = [];
    expect(await run('usage.clearQueue', { scope: 'company' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });
});

describe('A held change of address @s1106', () => {
  function Editor({ answers }: { answers: (DestinationChoice | undefined)[] }) {
    const { ask, dialog } = useDestination();
    const [said, setSaid] = useState('');
    const save = () =>
      withDestination(async (credentials) => {
        answers.push(credentials);
        if (!credentials)
          throw new ApiError(409, 'destination_change', 'Held', false, null, {
            name: 'Sample hook',
            old: { address: 'https://old.example.test' },
            new: { address: 'https://new.example.test' },
            credentials: ['Signing secret', 'Token'],
          });
        return credentials;
      }, ask).then(
        (c) => setSaid(`saved ${c}`),
        (e: Error) => setSaid(e.message),
      );
    return (
      <>
        <button type="button" onClick={() => void save()}>
          Save
        </button>
        <span>{said}</span>
        {dialog}
      </>
    );
  }

  it('names both addresses and every credential, and Clear sends again with clear', async () => {
    const answers: (DestinationChoice | undefined)[] = [];
    render(<Editor answers={answers} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Change where Sample hook sends?')).toBeTruthy();
    expect(screen.getByText(/Signing secret, Token were saved for https:\/\/old\.example\.test\. This change sends to https:\/\/new\.example\.test/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Clear' })));
    expect(await screen.findByText('saved clear')).toBeTruthy();
    expect(answers).toEqual([undefined, 'clear']);
  });
});

describe('Search settings @s1130', () => {
  it('finds a setting by name and opens its section; a setting the reader may not change is not offered', async () => {
    queryClient.setQueryData(['me'], { id: 'human:p2', email: 'ben@example.test', role: 'person', slug: null });
    serve((url) => {
      if (url.startsWith('/api/v2/settings/catalog'))
        return ok({
          settings: [
            { key: 'a', name: 'Default model', who: 'owner', card: '#/settings/models/x', where: 'Models' },
            { key: 'b', name: 'Theme', who: 'person', card: '#/settings/general/y', where: 'General' },
          ],
        });
      if (url.startsWith('/api/v2/access')) return { status: 403, body: { error: { code: 'forbidden', detail: 'Admins' } } };
      return undefined;
    });
    show(<SettingsPage section="agents" />, '/settings/agents');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Search settings' }), { target: { value: 'the' } });
    const hit = await screen.findByRole('link', { name: 'Theme' });
    expect(hit.getAttribute('href')).toBe('/settings/general');
    expect(screen.queryByRole('link', { name: 'Default model' })).toBeNull();
  });
});

// ---------------------------------------------------------------- Diagnostics (Tico support's chat)

describe('Diagnostics @s1125', () => {
  const layout: Record<string, PropertyDescriptor> = { clientHeight: { get: () => 300 }, clientWidth: { get: () => 400 }, offsetHeight: { get: () => 50 } };
  const kept = Object.keys(layout).map((k) => [k, Object.getOwnPropertyDescriptor(HTMLElement.prototype, k)] as const);
  beforeAll(() => {
    notifyManager.setScheduler((cb) => cb());
    for (const [k, d] of Object.entries(layout)) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, ...d });
  });
  afterAll(() => {
    for (const [k, d] of kept) if (d) Object.defineProperty(HTMLElement.prototype, k, d);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
  });

  it('shows exactly what goes before it is attached, and the next message carries it', async () => {
    const list = {
      enabled: true,
      unread: 0,
      tickets: [{ id: 't1', status: 'answered', message: 'Labels fail', created_at: '2026-01-02T00:00:00Z', unread: 0, messages: [] }],
    };
    serve((url) =>
      url === '/api/v2/support/tickets' || url.endsWith('/refresh')
        ? ok(list)
        : url.startsWith('/api/v2/support/diagnostics?performance_hours=24')
          ? ok({ id: 'd1', text: '{"version":"0.0.1"}', bytes: 20 })
          : url === '/api/v2/support/compose'
            ? ok({ email: 'ana@example.test', version: '0.0.1', install_id: 'i1' })
            : url.endsWith('/messages')
              ? ok({ ...list.tickets[0], messages: [] })
              : undefined,
    );
    const root = createRootRoute({
      component: () => (
        <>
          <ActionsRoot dot={() => null} fold={() => {}} />
          <SupportChat />
        </>
      ),
    });
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks?chat=support'] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await screen.findByText('Labels fail');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Attach diagnostics' }));
    const dlg = await screen.findByRole('dialog', { name: 'Diagnostics' });
    await waitFor(() => expect((within(dlg).getByRole('textbox', { name: 'Diagnostics' }) as HTMLTextAreaElement).value).toBe('{"version":"0.0.1"}'));
    expect(sent.filter((c) => c.url.includes('/messages') || c.url.includes('/diagnostics'))).toHaveLength(0);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Attach' }));
    const box = await screen.findByRole('textbox', { name: 'Message Tico support' });
    fireEvent.change(box, { target: { value: 'Here is what it says' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(sent.find((c) => c.url.endsWith('/messages'))?.body).toEqual({ message: 'Here is what it says', diagnostics: 'd1' }));
  });
});
