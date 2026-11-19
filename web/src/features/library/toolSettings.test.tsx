import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { sendsMore } from './actions';
import {
  agentSendingQuery,
  googleQuery,
  lettersSettingsQuery,
  numberRequestsQuery,
  sendingQuery,
  type GoogleState,
  type SendPolicy,
} from './queries';
import { toolsSearch } from './search';
import { GoogleSection, NumberRequests, SharedInboxDialog } from './ToolSettings';
import { ToolsPage } from './ToolsPage';

/* The company's settings for Email, Texts and Letters (the old Tools › Settings): what each sends, what Tico may not
   run, and that a secret shown or typed here never lands in the cache. Made-up companies, domains and numbers only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
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
    return new Response(JSON.stringify(reply(String(url), method)), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => (method === 'DELETE' ? [method, url] : [method, url, body]));

function at(path: string, node: ReactNode = null) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: path.split('?')[0] ?? '/', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const may = { organize_mail: false, send_mail: true, change_calendar: false };
const google = (o: Partial<GoogleState> = {}): GoogleState => ({ can_admin: true, workspace: null, pending: null, me: null, permissions: may, ...o });
const policy: SendPolicy = {
  mode: 'ask_first',
  what: 'replies_only',
  to: 'known_addresses',
  daily_cap: 20,
  mailbox_daily_cap: 50,
  company_daily_cap: 200,
  undo_seconds: 30,
  max_outside_recipients: 1,
  blocked_recipients: ['@rival.test'],
  ask_new_address: true,
  ask_legal_money: true,
  ask_attachments: false,
  ask_many_recipients: true,
  ask_outside_cc: true,
};
const letters = { mode: 'ask_first', monthly_cap_micros: 50_000_000, return_address: null, availability: 'ready' };

describe('Google', () => {
  it('turns on what agents may do, and from Tico only turns things off', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    queryClient.setQueryData(googleQuery.queryKey, google());
    expect(await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setMailMay', { ...may, send_mail: false }, { via: 'tico' })).ok).toBe(true);
    expect((await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/google/permissions', { ...may, send_mail: false }],
      ['PUT', '/api/v2/google/permissions', { ...may, organize_mail: true }],
    ]);
  });

  it('shows a setup command in its dialog only, and never makes one, takes a key or signs in for Tico', async () => {
    const CODE = 'tico google setup --code fake-0000-code';
    const sent = serve((url) => (url.endsWith('/setup-code') ? { command: CODE, expires_at: '2026-11-19T12:30:00Z' } : google()));
    queryClient.setQueryData(googleQuery.queryKey, google());
    at('/library/tools', <GoogleSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Google settings' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Setup command' }));
    fireEvent.change(await screen.findByLabelText('Domain'), { target: { value: 'example.test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make' }));
    expect(await screen.findByText(CODE)).toBeTruthy();
    expect(writes(sent)).toContainEqual(['POST', '/api/v2/google/workspace/setup-code', { domain: 'example.test' }]);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('fake-0000');

    const before = sent.length;
    expect(await run('tools.googleSetup', { domain: 'example.test' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.uploadGoogleKey', { domain: 'example.test', key: '{"type":"service_account"}' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.connectMyGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.disconnectGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.disconnectMyGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    // letters: Tico narrows, never widens
    expect(await run('tools.setIntake', { inbox: 'm1', senders: ['scans@example.test'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLetterAccess', { type: 'bot', who: 'fern', read: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLegalMail', { bot: 'fern' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent.length).toBe(before);
  });

  it('uploads a key as JSON, and refuses one that is not JSON', async () => {
    const sent = serve(() => ({ delegation_url: 'https://admin.example.test' }));
    at('/library/tools');
    expect((await run('tools.uploadGoogleKey', { domain: 'Example.test', key: '{"type":"service_account"}' }, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([['POST', '/api/v2/google/workspace/key', { domain: 'example.test', key: { type: 'service_account' } }]]);
    expect((await run('tools.uploadGoogleKey', { domain: 'example.test', key: 'not json' }, { via: 'click' })).ok).toBe(false);
  });

  it('checks and disconnects the Workspace', async () => {
    const sent = serve((url) => (url.endsWith('/check') ? { ok: false, message: 'nope' } : {}));
    at('/library/tools');
    expect(await run('tools.checkGoogle', {}, { via: 'tico' })).toMatchObject({ ok: true, did: 'Google did not accept it' });
    expect((await run('tools.disconnectGoogle', {}, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([
      ['POST', '/api/v2/google/workspace/check', {}],
      ['DELETE', '/api/v2/google/workspace'],
    ]);
  });
});

describe('what agents may send', () => {
  it('knows a change that lets more out', () => {
    expect(sendsMore(undefined, { mode: 'never' })).toBe(true);
    expect(sendsMore(policy, { mode: 'never', daily_cap: 5, undo_seconds: 120 })).toBe(false);
    expect(sendsMore(policy, { mode: 'on_their_own' })).toBe(true);
    expect(sendsMore(policy, { to: 'anyone' })).toBe(true);
    expect(sendsMore(policy, { daily_cap: 21 })).toBe(true);
    expect(sendsMore(policy, { undo_seconds: 0 })).toBe(true);
    expect(sendsMore(policy, { ask_new_address: false })).toBe(true);
    expect(sendsMore(policy, { blocked_recipients: [] })).toBe(true);
    expect(sendsMore(policy, { blocked_recipients: ['@rival.test', '@other.test'] })).toBe(false);
  });

  it('sends only the change, and from Tico only a tighter one', async () => {
    const sent = serve();
    queryClient.setQueryData(sendingQuery.queryKey, { effective: policy, company: policy });
    at('/library/tools');
    expect(await run('tools.setSending', { mode: 'on_their_own' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setSending', { daily_cap: 10 }, { via: 'tico' })).ok).toBe(true);
    expect((await run('tools.setSending', { mode: 'on_their_own', undo_seconds: 120 }, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/email/sending-policy', { daily_cap: 10 }],
      ['PUT', '/api/v2/email/sending-policy', { mode: 'on_their_own', undo_seconds: 120 }],
    ]);
    expect((await run('tools.setSending', { undo_seconds: 45 }, { via: 'click' })).ok).toBe(false);
  });

  it('sets one agent’s own mode or hands it back, and from Tico never loosens it', async () => {
    const sent = serve();
    queryClient.setQueryData(sendingQuery.queryKey, { effective: policy, company: policy });
    queryClient.setQueryData(agentSendingQuery('fern').queryKey, { mode: 'never' });
    at('/library/tools');
    expect(await run('tools.setAgentSending', { bot: 'fern', mode: null }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setAgentSending', { bot: 'fern', mode: 'ask_first' }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setAgentSending', { bot: 'fern', mode: null }, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/bots/fern/email-sending-policy', { mode: 'ask_first' }],
      ['PUT', '/api/v2/bots/fern/email-sending-policy', { inherit: true }],
    ]);
  });
});

describe('the shared Inbox', () => {
  const CODE = 'x'.repeat(43);

  it('names the company first, joins once agreed, and keeps the code out of the cache', async () => {
    const sent = serve(() => ({ connected: false, company: { company_id: 'c1', name: 'Sample Co' } }));
    at('/library/tools', <SharedInboxDialog onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText('Connection code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Sample Co')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Join' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Share Inbox both ways with this company' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        ['POST', '/api/v2/inbound/company-connection', { action: 'preview', code: CODE }],
        ['POST', '/api/v2/inbound/company-connection', { action: 'connect', code: CODE }],
      ]),
    );
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(CODE);
  });

  it('is never Tico’s to join', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.joinSharedInbox', { code: CODE }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('number requests', () => {
  const later = Math.floor(Date.now() / 1000) + 3600;

  it('shows a fresh quote to accept, an old one as expired, and cancels', async () => {
    const sent = serve();
    queryClient.setQueryData(numberRequestsQuery.queryKey, [
      { request_id: 'r1', name: 'Front desk', state: 'quoted', price_micros: 2_500_000, quoted_until: later },
      { request_id: 'r2', name: 'Night line', state: 'quoted', price_micros: 2_500_000, quoted_until: 1 },
      { request_id: 'r3', name: 'Spare line', state: 'requested' },
      { request_id: 'r4', name: 'Old line', state: 'canceled' },
    ]);
    at('/library/tools', <NumberRequests />);
    expect(await screen.findByText('$2.50 for 30 days')).toBeTruthy();
    expect(screen.getByText('Quote expired')).toBeTruthy();
    expect(screen.getByText('Awaiting quote')).toBeTruthy();
    expect(screen.queryByText('Old line')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Accept' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(writes(sent)).toEqual([['POST', '/api/v2/inbound/phone-requests/r1/accept', { price_micros: 2_500_000 }]]));
    expect((await run('tools.cancelNumberRequest', { request: 'r3' }, { via: 'tico' })).ok).toBe(true);
    expect(writes(sent)[1]).toEqual(['POST', '/api/v2/inbound/phone-requests/r3/cancel', {}]);
  });

  it('never accepts a quote for Tico: it is paid', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.acceptQuote', { request: 'r1', price_micros: 2_500_000 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('letters', () => {
  it('chooses where emailed letters come in, and refuses an inbox with no senders', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.setIntake', { inbox: 'ib1', senders: [] }, { via: 'click' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await run('tools.setIntake', { inbox: 'ib1', senders: [' Scans@Provider.test', '@provider.test'] }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setIntake', { inbox: null, senders: ['@provider.test'] }, { via: 'click' })).ok).toBe(true);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/postal/intake', { inbox_id: 'ib1', senders: ['scans@provider.test', '@provider.test'] }],
      ['PUT', '/api/v2/postal/intake', { inbox_id: null, senders: [] }],
    ]);
  });

  it('lets someone read every letter and takes it back; nobody but a person or an agent', async () => {
    const sent = serve();
    at('/library/tools');
    expect((await run('tools.setLetterAccess', { type: 'bot', who: 'fern', read: true }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLetterAccess', { type: 'person', who: 'p1', read: false }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLetterAccess', { type: 'admins', who: 'x', read: true }, { via: 'click' })).ok).toBe(false);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/postal/addresses/all/grants/bot/fern', { level: 'read' }],
      ['DELETE', '/api/v2/postal/addresses/all/grants/person/p1'],
    ]);
  });

  it('sends legal letters to an agent or back to the owner', async () => {
    const sent = serve();
    at('/library/tools');
    expect((await run('tools.setLegalMail', { bot: 'fern' }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLegalMail', { bot: null }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLegalMail', { bot: '' }, { via: 'click' })).ok).toBe(false);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/postal/settings', { legal_bot_id: 'fern' }],
      ['PUT', '/api/v2/postal/settings', { legal_bot_id: null }],
    ]);
  });

  it('saves how letters go out, and from Tico never raises the cap or lets more go', async () => {
    const sent = serve(() => letters);
    queryClient.setQueryData(lettersSettingsQuery.queryKey, letters as never);
    at('/library/tools');
    expect(await run('tools.setLettersOut', { monthly_cap_micros: 80_000_000 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLettersOut', { monthly_cap_micros: null }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLettersOut', { mode: 'on_their_own' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setLettersOut', { monthly_cap_micros: 20_000_000 }, { via: 'tico' })).ok).toBe(true);
    const address = { name: 'Sample Co', line1: '1 Main St', city: 'Springfield', state: 'IL', postal_code: '62701' };
    expect((await run('tools.setLettersOut', { mode: 'on_their_own', return_address: address }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLettersOut', { return_address: { ...address, state: 'Illinois' } }, { via: 'click' })).ok).toBe(false);
    expect(writes(sent)).toEqual([
      ['PUT', '/api/v2/letters/settings', { monthly_cap_micros: 20_000_000 }],
      ['PUT', '/api/v2/letters/settings', { mode: 'on_their_own', return_address: address }],
    ]);
    expect((await run('tools.checkLetters', {}, { via: 'tico' })).ok).toBe(true);
    expect(sent.at(-1)).toMatchObject({ method: 'GET', url: '/api/v2/letters/settings?check=true' });
  });
});

describe('the settings pages', () => {
  function show(path: string) {
    const Tools = () => <ToolsPage search={toolsSearch(useSearch({ strict: false }))} />;
    const router = createRouter({ routeTree: createRootRoute({ component: Tools }), history: createMemoryHistory({ initialEntries: [path] }) });
    bind({ router });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }
  const reply = (url: string) =>
    url.includes('/tool-index')
      ? { rows: [] }
      : url === '/api/v2/google'
        ? google({ workspace: { status: 'connected', domain: 'example.test', client_id: '1', delegation_url: '', checked_at: null, last_error: null } })
        : url.startsWith('/api/v2/email/sending-policy')
          ? { effective: policy, company: policy, bot: {}, inbox: {} }
          : url.startsWith('/api/v2/inbound/phone-requests')
            ? { phone_requests: [], next: null }
            : url.startsWith('/api/v2/postal/settings')
              ? { settings: { legal_bot_id: null } }
              : url.startsWith('/api/v2/letters?') || url.startsWith('/api/v2/postal/items')
                ? { items: [] }
                : {};

  it('gives Email its Google connection, what agents may do and what they may send', async () => {
    serve(reply);
    show('/?tool=mailbox');
    expect(await screen.findByRole('heading', { name: 'Email' })).toBeTruthy();
    expect(await screen.findByText('example.test, connected')).toBeTruthy();
    expect(await screen.findByRole('switch', { name: 'Send mail' })).toBeTruthy();
    expect(await screen.findByText('Ask first')).toBeTruthy();
    expect(screen.getByText('20 an agent, 50 a mailbox, 200 in all')).toBeTruthy();
  });

  it('gives phone numbers their requests, and letters their rules', async () => {
    serve(reply);
    const router = show('/?tool=number');
    expect(await screen.findByText('No requests')).toBeTruthy();
    await act(async () => void (await run('tools.open', { tool: 'address' }, { via: 'click' })));
    expect(router.state.location.search).toMatchObject({ tool: 'address' });
    expect(await screen.findByText('Legal letters')).toBeTruthy();
  });
});
