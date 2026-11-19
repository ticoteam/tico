import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import './actions';
import {
  apiToolQuery,
  inboxesQuery,
  inboxMessagesQuery,
  intakeQuery,
  legalMailQuery,
  lettersQuery,
  lettersSettingsQuery,
  mailQuery,
  postalAddressesQuery,
  toolsQuery,
} from './queries';
import { addressGrantsQuery, automationsQuery, lettersInQuery, letterSendersQuery } from './toolMail';
import { ToolsPage } from './ToolsPage';

/* The tier-2 Tools gaps: an outgoing webhook's Delete, New secret and catch-up sends (s515); one mailing address's
   page and its letters (s868); letters sent, who may send them and each agent's rules (s869); Messages across every
   address (s870) and a message's code (s932); Show key for whoever is given the key (s916); the admin-only Add items
   and an address's Edit and Stop using (s934 s935 s936 s938 s940). Made-up tools, addresses and letters only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

const ME = { id: 'human:me', email: '', slug: null };
const as = (role: 'owner' | 'person') => queryClient.setQueryData(['me'], { ...ME, role });
const ACTORS = [
  { id: 'bot:b1', kind: 'agent', name: 'Clerk' },
  { id: 'human:p2', kind: 'person', name: 'Pat Sample' },
];

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  as('person');
  queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [] } as never);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

interface Sent {
  url: string;
  method: string;
  body: unknown;
}
function serve(reply: (url: string, method: string) => unknown = () => ({})) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    return new Response(JSON.stringify(reply(String(url), method) ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));
const reads = (sent: Sent[]) => sent.filter((x) => x.method === 'GET').map((x) => x.url);
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data));
const tico = (id: string, input: unknown) => run(id, input, { via: 'tico' });

function at(tool: string) {
  const node = <ToolsPage search={{ tool }} />;
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: '/library/tools', component: () => node as ReactNode });
  const any = createRoute({ getParentRoute: () => root, path: '$', component: () => null });
  const router = createRouter({ routeTree: root.addChildren([page, any]), history: createMemoryHistory({ initialEntries: [`/library/tools?tool=${encodeURIComponent(tool)}`] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who: [], who_total: 0, needs_you: false, detail: '', way: '', status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
/** The open tool's own ⋯ (the list's head has one too). */
const toolMore = async (name: string) => {
  const tool = await screen.findByRole('region', { name });
  fireEvent.click(await within(tool).findByRole('button', { name: 'More' }));
};
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/* ---------------------------------------------------------------- an outgoing webhook */

const HOOK = {
  id: 'wh1',
  name: 'Order feed',
  url: 'https://hooks.example.test/orders',
  host: 'hooks.example.test',
  events: ['task.created'],
  enabled: true,
  paused_reason: '',
  disabled_reason: null,
  can_change: true,
  last_7_days: { delivered: 9, failed: 1, pending: 0 },
};
const hookServer = (url: string) =>
  url === '/api/v2/webhooks/wh1'
    ? { webhook: HOOK }
    : url.startsWith('/api/v2/webhooks/wh1/sends')
      ? { items: [{ id: 'x1', type: 'task.created', subject: 'New task', state: 'failed', last_status: 500, last_error: 'Server error', created_at: '2026-11-18T00:00:00Z', delivered_at: null }] }
      : url === '/api/v2/webhooks/wh1/rotate'
        ? { webhook: { ...HOOK, secret: 'whsec_fake_0000', replayed: false } }
        : url === '/api/v2/webhooks/wh1/recover'
          ? { webhook: 'wh1', since: 'x', recovered: 2, more: false }
          : url === '/api/v2/webhooks/wh1/replay'
            ? { webhook: 'wh1', since: 'x', queued: 3, checked: 3, more: false }
            : undefined;
const openHook = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'webhook:wh1', kind: 'webhook', name: 'Order feed' })] } as never);
  return at('webhook:wh1');
};

describe('an outgoing webhook’s ⋯', () => {
  it('New secret asks first and shows the secret once, in its dialog only; Delete asks first and goes back to Tools; neither is Tico’s @s515', async () => {
    const sent = serve(hookServer);
    const router = openHook();
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New secret' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Make a new signing secret?' });
    expect(writes(sent)).toEqual([]);
    fireEvent.click(within(ask).getByRole('button', { name: 'Make' }));
    const shown = await screen.findByRole('dialog', { name: 'Signing secret' });
    expect(within(shown).getByText('whsec_fake_0000')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/rotate', method: 'POST', body: {} }]);
    expect(cached()).not.toContain('whsec_fake_0000');
    fireEvent.click(within(shown).getByRole('button', { name: 'Done' }));
    expect(screen.queryByText('whsec_fake_0000')).toBeNull();

    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const sure = await screen.findByRole('alertdialog', { name: 'Delete Order feed?' });
    fireEvent.click(within(sure).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/delete', method: 'POST', body: {} }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await tico('tools.newWebhookSecret', { webhook: 'wh1' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await tico('tools.deleteWebhook', { webhook: 'wh1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Retry failed and Send missed go since a day (a week back to start); a failed send retries from its row; Tico may pause, never restart @s515', async () => {
    const sent = serve(hookServer);
    openHook();
    const week = iso(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000));
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Retry failed' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Retry failed' })).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/recover', method: 'POST', body: { since: week } }]));
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Send missed' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Send missed' })).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/replay', method: 'POST', body: { since: week } }));
    const failed = (await screen.findByText('Failed (500)')).closest('.row') as HTMLElement;
    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/sends/x1/retry', method: 'POST', body: {} }));
    expect(await tico('tools.pauseWebhook', { webhook: 'wh1', on: true })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.pauseWebhook', { webhook: 'wh1', on: false })).ok).toBe(true);
    expect((await tico('tools.retryWebhook', { webhook: 'wh1', since: week })).ok).toBe(true);
  });
});

/* ---------------------------------------------------------------- a mailing address */

const ACTIVE = { assignment_id: 'as1', address: '1 Fern Way, Dover DE', status: 'active', state: 'DE', kind: 'shared', agent: true };
const PENDING = { assignment_id: 'as2', address: null, status: 'pending_1583', state: 'TX', kind: 'private' };
const LETTER_IN = {
  id: 'm1',
  provider: 'gateway',
  address_id: 'as1',
  sender: 'County office',
  summary: 'Tax notice',
  kind: 'government_tax',
  status: 'failed',
  received_at: '2026-11-17T00:00:00Z',
  file_url: '/api/v2/files/f1',
  task_id: 't9',
};
const addresses = () => {
  queryClient.setQueryData(postalAddressesQuery.queryKey, { addresses: [ACTIVE, PENDING], orders: [] } as never);
  queryClient.setQueryData(lettersInQuery.queryKey, [LETTER_IN] as never);
  queryClient.setQueryData(automationsQuery.queryKey, [
    { id: 'r1', name: 'File the mail', enabled: true, trigger: { type: 'message', inbox: ACTIVE.address } },
    { id: 'r2', name: 'Elsewhere', enabled: true, trigger: { type: 'message', inbox: 'other@example.test' } },
  ]);
  queryClient.setQueryData(addressGrantsQuery('as1').queryKey, [{ id: 'g1', address_id: 'as1', grantee_type: 'bot', grantee_id: 'b1', level: 'read' }] as never);
};

describe('one mailing address', () => {
  it('opens on its own page: who can read it (give, remove), the routines its mail starts, and Check connection @s868', async () => {
    as('owner');
    const sent = serve((url) => (url === '/api/v2/postal/addresses' ? { addresses: [ACTIVE, PENDING], orders: [] } : undefined));
    addresses();
    at('address:as1');
    const page = await screen.findByRole('region', { name: '1 Fern Way, Dover DE' });
    expect(within(page).getByText('Yes')).toBeTruthy(); // registered agent
    const readers = within(page).getByRole('region', { name: 'Who can read' });
    fireEvent.click(within(readers).getByRole('button', { name: 'Remove Clerk' }));
    await waitFor(() => expect(writes(sent)).toMatchObject([{ url: '/api/v2/postal/addresses/as1/grants/bot/b1', method: 'DELETE' }]));
    fireEvent.click(within(readers).getByRole('button', { name: 'Add someone' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Pat Sample/ }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/postal/addresses/as1/grants/person/p2', method: 'PUT', body: { level: 'read' } }));
    const autos = within(page).getByRole('region', { name: 'Automations' });
    expect(within(autos).getByRole('link', { name: /File the mail/ }).getAttribute('href')).toBe('/company/routines?panel=routine%3Ar1');
    expect(within(autos).queryByText('Elsewhere')).toBeNull();
    const before = reads(sent).filter((u) => u === '/api/v2/postal/addresses').length;
    fireEvent.click(within(page).getByRole('button', { name: 'Check connection' }));
    await waitFor(() => expect(reads(sent).filter((u) => u === '/api/v2/postal/addresses').length).toBe(before + 1));
    expect(await tico('tools.setAddressAccess', { address: 'as1', type: 'person', who: 'p2', read: true })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.setAddressAccess', { address: 'as1', type: 'bot', who: 'b1', read: false })).ok).toBe(true);
  });

  it('its letters open their scan and their task, and an admin retries a failed one; an address waiting on Form 1583 shows its steps @s868', async () => {
    as('owner');
    const opened = vi.fn();
    vi.stubGlobal('open', opened);
    const sent = serve((url) =>
      url === '/api/v2/postal/addresses' ? { addresses: [ACTIVE, PENDING], orders: [] } : url === '/api/v2/postal/items?limit=100' ? { items: [LETTER_IN] } : undefined,
    );
    addresses();
    const router = at('address:as1');
    const page = await screen.findByRole('region', { name: '1 Fern Way, Dover DE' });
    expect(within(page).getByText('Government or tax, Failed')).toBeTruthy();
    const menu = () => fireEvent.click(within(page).getByRole('button', { name: 'County office options' }));
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open scan' }));
    expect(opened).toHaveBeenCalledWith('/api/v2/files/f1', '_blank', 'noopener');
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Retry' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/postal/items/m1/retry', method: 'POST', body: {} }]));
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open task' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/task/t9'));
    cleanup();
    at('address:as2');
    const pending = await screen.findByRole('region', { name: 'TX, Private' });
    const steps = within(pending).getByRole('region', { name: 'Setup' });
    expect(within(steps).getByText('Form 1583').closest('.row')?.getAttribute('aria-current')).toBe('step');
    expect(within(steps).getByText(/emails the Form 1583 to sign/)).toBeTruthy();
  });

  it('the mailing address lists each address as a link to its page @s868', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })] } as never);
    addresses();
    queryClient.setQueryData(mailQuery.queryKey, []);
    queryClient.setQueryData(lettersQuery.queryKey, []);
    at('address');
    const list = await screen.findByRole('region', { name: 'Addresses' });
    expect(within(list).getByRole('link', { name: /1 Fern Way/ }).getAttribute('href')).toBe('/library/tools?tool=address%3Aas1');
  });
});

/* ---------------------------------------------------------------- letters out */

const SENT_LETTER = {
  id: 'L1',
  redacted: false,
  status: 'waiting_for_ok',
  can_cancel: true,
  created_at: '2026-11-18T00:00:00Z',
  recipient: { name: 'Sam Sample', line1: '2 Elm St', city: 'Dover', state: 'DE', postal_code: '19901' },
  return_address: { name: 'Sample Co', line1: '1 Fern Way', city: 'Dover', state: 'DE', postal_code: '19901' },
  page_count: 2,
  mail_class: 'first_class',
  color: false,
  duplex: true,
  total_micros: 1_500_000,
  billing: 'none',
  tracking: [{ barcode: '9400111', status: 'In transit' }],
  requested_by: 'human:me',
  acting_bot_id: null,
  proof: '/api/v2/files/p1',
  error: '',
  task_id: null,
};
const openLetters = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })] } as never);
  queryClient.setQueryData(postalAddressesQuery.queryKey, { addresses: [], orders: [] } as never);
  queryClient.setQueryData(mailQuery.queryKey, []);
  const rest = Array.from({ length: 19 }, (_, n) => ({ id: `R${n}`, redacted: true, status: 'mailed', can_cancel: false, created_at: '2026-11-01T00:00:00Z', page_count: 1 }));
  queryClient.setQueryData(lettersQuery.queryKey, [SENT_LETTER, ...rest] as never);
  queryClient.setQueryData(lettersSettingsQuery.queryKey, { mode: 'ask_first', monthly_cap_micros: 50_000_000, return_address: null, availability: 'ready' } as never);
  queryClient.setQueryData(intakeQuery.queryKey, {});
  queryClient.setQueryData(legalMailQuery.queryKey, null as never);
  return at('address');
};

describe('letters out', () => {
  it('a letter’s ⋯ holds its details, its proof and Review; Load more reads the older ones @s869', async () => {
    const opened = vi.fn();
    vi.stubGlobal('open', opened);
    const sent = serve((url) =>
      url === '/api/v2/letters?limit=20&before=R18'
        ? { items: [{ id: 'O1', redacted: false, status: 'delivered', can_cancel: false, created_at: '2026-10-01T00:00:00Z', recipient: { name: 'Old Friend' }, page_count: 1, proof: null, tracking: [] }], next_before: null }
        : url === '/api/v2/letters/access'
          ? { send: true, outbox_id: 'company' }
          : undefined,
    );
    const router = openLetters();
    const options = async () => fireEvent.click(await screen.findByRole('button', { name: 'Sam Sample, Dover options' }));
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Details' }));
    const details = await screen.findByRole('dialog', { name: 'Sam Sample, Dover' });
    expect(within(details).getByText('Sam Sample, 2 Elm St, Dover DE 19901')).toBeTruthy();
    expect(within(details).getByText('Black and white, both sides')).toBeTruthy();
    expect(within(details).getByText('$1.50, not charged')).toBeTruthy();
    expect(within(details).getByText('9400111, In transit')).toBeTruthy();
    fireEvent.click(within(details).getByRole('button', { name: 'Close' }));
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Proof PDF' }));
    expect(opened).toHaveBeenCalledWith('/api/v2/files/p1', '_blank', 'noopener');
    // a private letter has no ⋯: its recipient and proof are not the reader's
    expect(screen.getAllByRole('button', { name: /options$/ }).filter((b) => b.getAttribute('aria-label')?.startsWith('A letter'))).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Old Friend')).toBeTruthy();
    expect(reads(sent)).toContain('/api/v2/letters?limit=20&before=R18');
    expect(await screen.findByText('Can send')).toBeTruthy();
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Review' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow'));
  });

  it('an admin gives and takes send access and sets an agent’s rule and cap; Tico may only take away and tighten @s869', async () => {
    as('owner');
    const sent = serve();
    queryClient.setQueryData(letterSendersQuery.queryKey, [
      { id: 'lg1', outbox_id: 'company', grantee_type: 'bot', grantee_id: 'b1', level: 'send', created_at: '', granted_by_id: 'human:me', mode: 'ask_first', monthly_cap_micros: 50_000_000 },
    ]);
    openLetters();
    const senders = await screen.findByRole('region', { name: 'Who can send letters' });
    expect(within(senders).getByText('Ask first, $50.00 a month')).toBeTruthy();
    fireEvent.click(within(senders).getByRole('button', { name: 'Clerk options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Letter rules' }));
    const rules = await screen.findByRole('dialog', { name: 'Clerk' });
    fireEvent.change(within(rules).getByLabelText('A month ($)'), { target: { value: '20' } });
    fireEvent.click(within(rules).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/letters/bots/b1/settings', method: 'PUT', body: { mode: 'ask_first', monthly_cap_micros: 20_000_000 } }]));
    fireEvent.click(within(senders).getByRole('button', { name: 'Let someone send letters' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Pat Sample/ }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/letters/outboxes/company/grants/person/p2', method: 'PUT', body: { level: 'send' } }));
    expect(await tico('tools.setLetterSender', { type: 'person', who: 'p2', send: true })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.setLetterSender', { type: 'person', who: 'p2', send: false })).ok).toBe(true);
    queryClient.setQueryData(letterSendersQuery.queryKey, [
      { id: 'lg1', outbox_id: 'company', grantee_type: 'bot', grantee_id: 'b1', level: 'send', created_at: '', granted_by_id: 'human:me', mode: 'ask_first', monthly_cap_micros: 50_000_000 },
    ]);
    expect(await tico('tools.setAgentLetters', { bot: 'b1', mode: 'on_their_own', monthly_cap_micros: 10_000_000 })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await tico('tools.setAgentLetters', { bot: 'b1', mode: 'ask_first', monthly_cap_micros: 90_000_000 })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await tico('tools.setAgentLetters', { bot: 'b1', mode: null, monthly_cap_micros: 10_000_000 })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.setAgentLetters', { bot: 'b1', mode: 'never', monthly_cap_micros: 10_000_000 })).ok).toBe(true);
  });
});

/* ---------------------------------------------------------------- Messages, and a message's code */

const INBOXES = [
  { inbox_id: 'i1', kind: 'email', name: 'Logins', address: 'logins@in.example.test', state: 'active', audience: 'company' },
  { inbox_id: 'i2', kind: 'sms', name: 'Mobile', address: '+15550100', state: 'active', audience: 'company' },
];
const MESSAGES = [
  { message_id: 'm1', inbox_id: 'i1', kind: 'email', from: 'bank@example.test', subject: 'Your code', received_at: 1763500000, has_code: true },
  { message_id: 'm2', inbox_id: 'i2', kind: 'sms', from: '+15550199', preview: 'See you at noon', received_at: 1763400000 },
];

describe('Messages', () => {
  it('lists what came in to every address, narrowed by code, channel and words, a page at a time @s870', async () => {
    const sent = serve((url) => (url.startsWith('/api/v2/inbound/messages?') ? { messages: url.includes('before=') ? [] : MESSAGES, next: url.includes('before=') ? null : 77 } : undefined));
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    at('messages');
    const page = await screen.findByRole('region', { name: 'Messages' });
    expect(await within(page).findByText('Your code')).toBeTruthy();
    expect(within(page).getByText('See you at noon')).toBeTruthy();
    expect(reads(sent)).toContain('/api/v2/inbound/messages?limit=50');
    fireEvent.click(within(page).getByRole('button', { name: 'Filter messages' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Has a code' }));
    await waitFor(() => expect(reads(sent)).toContain('/api/v2/inbound/messages?limit=50&has_code=true'));
    fireEvent.click(within(page).getByRole('button', { name: 'Filter messages' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Channel' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Texts' }));
    await waitFor(() => expect(reads(sent)).toContain('/api/v2/inbound/messages?limit=50&kind=sms&has_code=true'));
    fireEvent.change(within(page).getByRole('textbox', { name: 'Find messages' }), { target: { value: 'bank' } });
    await waitFor(() => expect(reads(sent)).toContain('/api/v2/inbound/messages?limit=50&kind=sms&q=bank&has_code=true'));
    expect(within(page).getByRole('group', { name: 'Filters' }).textContent).toContain('Texts');
    fireEvent.click(await within(page).findByRole('button', { name: 'Older messages' }));
    await waitFor(() => expect(reads(sent).some((u) => u.includes('before=77'))).toBe(true));
  });

  it('a message with a code reveals it in the reader only, on its own press; never Tico’s @s932', async () => {
    const CODE = '482913';
    const sent = serve((url) =>
      url === '/api/v2/inbound/messages/m1/code' ? { code: CODE } : url === '/api/v2/inbound/messages/m1' ? { text: 'Your sign-in code is inside.', next_offset: null } : undefined,
    );
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins' })] } as never);
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, [MESSAGES[0]] as never);
    at('tico:i1');
    fireEvent.click(await screen.findByRole('button', { name: /Your code/ }));
    const reader = await screen.findByRole('dialog', { name: 'Your code' });
    expect(await within(reader).findByText('Your sign-in code is inside.')).toBeTruthy();
    expect(reads(sent)).not.toContain('/api/v2/inbound/messages/m1/code');
    fireEvent.click(within(reader).getByRole('button', { name: 'Reveal code' }));
    expect(await within(reader).findByText(CODE)).toBeTruthy();
    expect(cached()).not.toContain(CODE);
    expect(await tico('tools.revealCode', { message: 'm1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

/* ---------------------------------------------------------------- Show key, and what only an admin sees */

const KEY = { id: 'k1', name: 'Main key', preview: 'sk-…abcd', access_level: 'read', expires_on: null, expires_source: null };

describe('who sees what', () => {
  it('Show key is offered to someone the key is given to, without the manager’s changes @s916', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    queryClient.setQueryData(apiToolQuery('t1').queryKey, {
      tool: { id: 't1', name: 'Sample API', service: 'sample', base_url: 'https://api.example.com', auth_scheme: 'bearer', delivery: 'proxy', check_path: '' },
      keys: [KEY],
      access: [],
      checks: [],
      recent: [],
      settings: [],
      status: { tone: 'ok', text: 'Working' },
      can_manage: false,
      can_set_audience: false,
      mcp: null,
    } as never);
    at('api:t1');
    expect(screen.queryByRole('button', { name: 'Replace key' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show key' }));
    expect(await screen.findByRole('dialog', { name: 'Main key' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Set end date' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Remove key' })).toBeNull();
  });

  it('Add tool offers Email, Phone number and Mailing address only to an admin @s938 @s934 @s940', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    at('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    expect(await screen.findByRole('menuitem', { name: 'Tico address' })).toBeTruthy();
    for (const name of ['Email', 'Phone number', 'Mailing address']) expect(screen.queryByRole('menuitem', { name })).toBeNull();
    cleanup();
    as('owner');
    at('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    for (const name of ['Email', 'Phone number', 'Mailing address']) expect(await screen.findByRole('menuitem', { name })).toBeTruthy();
    expect((await tico('tools.requestNumber', { name: 'Mobile number' })).ok).toBe(true);
  });

  it('an address’s Stop using, and a number’s Edit, are an admin’s; anyone renames an address @s935 @s936', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, {
      rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins' }), indexRow({ id: 'number:i2', kind: 'number', name: 'Mobile' })],
    } as never);
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, []);
    queryClient.setQueryData(inboxMessagesQuery('i2').queryKey, []);
    at('tico:i1');
    await toolMore('Logins');
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Stop using' })).toBeNull();
    cleanup();
    at('number:i2');
    const number = await screen.findByRole('region', { name: 'Mobile' });
    expect(within(number).queryByRole('button', { name: 'More' })).toBeNull();
    cleanup();
    as('owner');
    at('number:i2');
    await toolMore('Mobile');
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Stop using' })).toBeTruthy();
  });
});
