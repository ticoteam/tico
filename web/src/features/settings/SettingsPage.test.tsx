import { CancelledError, notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { Agents } from './Agents';
import { run } from '@/actions/run';
import { Account, General, People, Security } from './Basics';
import { loadSection } from './main';
import { AddPersonButton } from './People';
import {
  accessQuery,
  askQuery,
  computersQuery,
  contactTypeQuery,
  contactTypesQuery,
  historyQuery,
  humansQuery,
  labelsQuery,
  modelsQuery,
  roadmapQuery,
  screeningQuery,
  settingsCatalogQuery,
  sleepQuery,
  subscriptionsQuery,
  type AccessFull,
  type AccessView,
} from './queries';
import { SettingsPage } from './SettingsPage';

// jsdom has no layout: the list draws every row, and End stands for reaching its end.
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: <T,>({ items, getKey, renderItem, label, onReachEnd }: { items: readonly T[]; getKey: (t: T) => string; renderItem: (t: T, i: number) => ReactNode; label?: string; onReachEnd?: () => void }) => (
    <div role="list" aria-label={label}>
      {items.map((t, i) => (
        <div key={getKey(t)}>{renderItem(t, i)}</div>
      ))}
      <button type="button" onClick={onReachEnd}>
        End
      </button>
    </div>
  ),
}));
vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let read: string[] = [];
/** Anything `reply` leaves (undefined) answers 200 with {}. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply> | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') read.push(String(url));
    else sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = (await reply(String(url), method)) ?? ok({});
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

const ok = (body: unknown): Reply => ({ status: 200, body });

/** `load`: the route's loader; what it throws goes to the router's error component (the app's is the shell's PageFault). */
function show(node: ReactNode, at: string, load?: () => Promise<unknown>) {
  const root = createRootRoute({
    loader: load,
    errorComponent: () => <p>The router’s fault page</p>,
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

const ACCESS: AccessView = {
  owner: { email: 'owner@example.test', person: 'p1' },
  people: [
    { id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true },
    { id: 'p2', name: 'Ben Sample', email: 'ben@example.test', left: false, owner: false, role: 'member', sign_in: false },
  ],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: { bot_reports_direct: true },
  you: 'owner',
  invites: {},
  invite_email: true,
};

/** People with someone who left, joining by domain, and an agent limit: what the owner's People and General read. */
const FULL: AccessFull = {
  ...ACCESS,
  owner: { email: 'ana@example.test', person: 'p1' },
  people: [...ACCESS.people, { id: 'p3', name: 'Cal Former', email: 'cal@example.test', left: true, owner: false, role: 'member', sign_in: false, removable: true }],
  allowed: ['ben@example.test'],
  allowed_domains: ['other.test'],
  revision: 4,
  home_domain: 'example.test',
  join: 'admin',
  member_bot_limit: 3,
};
/** The server with `FULL` as its access list. */
const serveFull = (reply: (url: string, method: string) => Reply | undefined = () => undefined) =>
  serve((url, method) => reply(url, method) ?? (url === '/api/v2/access' ? ok(FULL) : undefined));

/** The page as its route draws it: the opened row comes from the address. */
function Opened({ section }: { section: 'contacts' }) {
  const { open } = useSearch({ strict: false }) as { open?: string };
  return <SettingsPage section={section} open={open} />;
}

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const unfold = async (name: string) => {
  const region = await screen.findByRole('region', { name });
  fireEvent.click(within(region).getAllByRole('button', { expanded: false })[0] as HTMLElement);
  return region;
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  // An observer's queued notification must run before jsdom tears down the React window.
  notifyManager.setScheduler(queueMicrotask);
});
beforeEach(() => {
  sent = [];
  read = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings', () => {
  it('links from a phone section back to the settings list', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => ({ status: 200, body: ACCESS }));
      queryClient.setQueryData(accessQuery.queryKey, ACCESS);
      const router = show(<SettingsPage section="people" />, '/settings/people');
      fireEvent.click(await screen.findByRole('link', { name: 'Back to Settings' }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/settings'));
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });

  it('ignores results for the previous search while new words settle', async () => {
    serve(() => ({ status: 200, body: ACCESS }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(settingsCatalogQuery('billing').queryKey, [
      { key: 'plan', name: 'Billing plan', card: '#/settings/billing' },
    ]);
    const router = show(<SettingsPage section="people" />, '/settings/people');
    const field = await screen.findByRole('textbox', { name: 'Search settings' });
    fireEvent.change(field, { target: { value: 'billing' } });
    expect(await screen.findByRole('link', { name: 'Billing plan' })).toBeTruthy();
    fireEvent.change(field, { target: { value: 'computers' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.queryByRole('link', { name: 'Billing plan' })).toBeNull();
    expect(router.state.location.pathname).toBe('/settings/people');
  });

  it('lists the sections as links and marks the open one @s1065', async () => {
    serve();
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const nav = await screen.findByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'People' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Billing' }).getAttribute('href')).toBe('/settings/billing');
  });

  it('a person row opens that person in the address, with their role to change @s1073', async () => {
    serve();
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const row = await screen.findByTitle('Ben Sample');
    expect(row.getAttribute('href')).toBe('/settings/people?open=p2');
  });

  it('an opened person shows their role and what can be done to them', async () => {
    serve();
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" open="p2" />, '/settings/people?open=p2');
    expect(await screen.findByRole('button', { name: 'Send invite again' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy();
    expect(screen.getByTitle('Ben Sample').getAttribute('href')).toBe('/settings/people');
  });

  it('a switch shows at once and is taken back when the server refuses @s1072', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'PUT' && url.endsWith('/api/v2/access/rules'))
        return new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners only' } } })));
      if (url.startsWith('/api/v2/access')) return { status: 200, body: ACCESS };
      return { status: 200, body: { pages: {} } };
    });
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="general" />, '/settings/general');
    const sw = await screen.findByRole('switch', { name: 'Reports to Tico support' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Reports to Tico support' }).getAttribute('aria-checked')).toBe('false'));
    expect(sent[0]).toMatchObject({ url: '/api/v2/access/rules', method: 'PUT', body: { bot_reports_direct: false } });
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => refuse());
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Reports to Tico support' }).getAttribute('aria-checked')).toBe('true'));
    // The short words first, the server's reason second.
    const reason = await screen.findByText('Owners only');
    expect(reason.parentElement?.textContent).toBe("Couldn't save: Owners only");
  });

  it('says when a section is for owners and admins only', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners and admins' } } }));
    show(<SettingsPage section="billing" />, '/settings/billing', () => loadSection(queryClient, 'billing'));
    expect(await screen.findByText('Owners and admins only.')).toBeTruthy();
    // The section list stays beside it.
    expect(screen.getByRole('link', { name: 'General' })).toBeTruthy();
  });

  it('a section that does not load goes to the router’s fault page', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'down', detail: 'down' } } }));
    show(<SettingsPage section="history" />, '/settings/history', () => loadSection(queryClient, 'history'));
    expect(await screen.findByText('The router’s fault page')).toBeTruthy();
  });

  it('completes the section load when a shell observer cancels a shared read', async () => {
    const read = vi.spyOn(queryClient, 'ensureQueryData').mockRejectedValueOnce(new CancelledError({ revert: true }));
    try {
      await expect(loadSection(queryClient, 'general')).resolves.toBeUndefined();
    } finally {
      read.mockRestore();
    }
  });

  it('says so when there are no API tokens', async () => {
    serve((url) => (url.endsWith('/api/v2/me/tokens') ? { status: 200, body: { tokens: [], can_create: true } } : { status: 200, body: { people: [] } }));
    show(<SettingsPage section="account" />, '/settings/account');
    expect(await screen.findByRole('heading', { name: 'No tokens' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Make token' })).toBeTruthy();
  });
});

describe('A model sign-in @s746', () => {
  const CODE = 'WDJB-MJHT';
  const PAGE = `https://auth.example/device?user_code=${CODE}`;

  it('keeps the provider’s code and page in the dialog only, never in the query cache', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/model-subscriptions/connect')
        ? { status: 200, body: { id: 'l1', runner_id: 'r1', kind: 'codex_login', state: 'starting' } }
        : url.endsWith('/api/v2/computers/r1/logins/l1')
          ? { status: 200, body: { id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE, lines: [`Enter ${CODE}`] } }
          : { status: 404, body: { detail: 'not_found' } },
    );
    const kind = { connect: false, help: '', kind: 'codex_login' as const, label: 'Codex', owned: false, runtime: 'codex' as const, secret_label: '' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [kind], subscriptions: [] });
    queryClient.setQueryData(computersQuery.queryKey, []);
    show(<Agents />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Codex/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Codex' });
    expect((await within(dialog).findByRole('link', { name: 'Open page' })).getAttribute('href')).toBe(PAGE);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });
});

describe('Settings › Account › API tokens', () => {
  const LIST = { tokens: [{ id: 't1', label: 'Script', created_at: '2026-11-01T00:00:00Z', last_used_at: null, revoked_at: null }], can_create: true };

  it('a new token shows once with its shell line, and is kept nowhere else @s726', async () => {
    const SECRET = 'tico_sample_secret_1234';
    serve((url, method) =>
      url.endsWith('/api/v2/me/tokens')
        ? method === 'POST'
          ? { status: 200, body: { id: 't2', token: SECRET, label: 'CI', expires_at: null } }
          : { status: 200, body: LIST }
        : { status: 200, body: { people: [] } },
    );
    show(<SettingsPage section="account" />, '/settings/account');
    fireEvent.click(await screen.findByRole('button', { name: 'Make token' }));
    const make = await screen.findByRole('dialog', { name: 'Make a token' });
    fireEvent.change(within(make).getByLabelText('Name'), { target: { value: 'CI' } });
    fireEvent.click(within(make).getByRole('button', { name: 'Make' }));
    const shown = await screen.findByRole('dialog', { name: 'Your new token' });
    expect(within(shown).getByLabelText('Token').textContent).toBe(SECRET);
    expect(within(shown).getByLabelText('Shell line').textContent).toBe(`export HUB_API_URL=${window.location.origin} HUB_TOKEN=${SECRET}`);
    expect(sent[0]).toMatchObject({ url: '/api/v2/me/tokens', method: 'POST', body: { label: 'CI' } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(SECRET);
  });

  it('Revoke asks first, and only Revoke in the question sends it @s706', async () => {
    serve((url) => (url.endsWith('/api/v2/me/tokens') ? { status: 200, body: LIST } : { status: 200, body: { people: [] } }));
    show(<SettingsPage section="account" />, '/settings/account');
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Revoke Script?' });
    expect(sent).toHaveLength(0);
    fireEvent.click(within(ask).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/me/tokens/t1/revoke', method: 'POST' }));
  });

  it("an owner sees another person's token and revokes it after a confirm; Tico cannot @s706", async () => {
    const mine = { id: 'k1', label: 'Laptop', created_at: '2026-11-01T00:00:00Z', last_used_at: null, expires_at: null, revoked_at: null };
    serveFull((url) => {
      if (url === '/api/v2/me/tokens') return ok({ tokens: [mine], can_create: true });
      if (url === '/api/v2/access/tokens')
        return ok({ tokens: [mine, { ...mine, id: 'k2', label: 'Script', human: 'p2', name: 'Ben Sample', email: 'ben@example.test' }] });
      return undefined;
    });
    queryClient.setQueryData(accessQuery.queryKey, FULL);
    queryClient.setQueryData(humansQuery.queryKey, []);
    show(<Account />, '/settings/account');
    const all = await unfold("Everyone's tokens");
    expect(within(all).queryByText('Laptop')).toBeNull();
    expect(within(all).getByText('Ben Sample')).toBeTruthy();
    fireEvent.click(within(all).getByRole('button', { name: 'Revoke' }));
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Revoke Script?' })).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/me/tokens/k2/revoke', method: 'POST', body: {} }));
    expect(await run('settings.revokeAnyToken', { token: 'k2' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('a new token takes an expiry in days @s706', async () => {
    serve((url, method) => (url === '/api/v2/me/tokens' && method === 'POST' ? ok({ id: 'k3', token: 'secret-value', label: 'CI', expires_at: null }) : undefined));
    const r = await run('settings.createToken', { label: 'CI', days: 30 }, { via: 'click' });
    expect(r).toMatchObject({ ok: true });
    expect(sent).toContainEqual({ url: '/api/v2/me/tokens', method: 'POST', body: { label: 'CI', expires_in_days: 30 } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('secret-value');
  });
});

describe('Settings › People › the owner', () => {
  const MINE: AccessView = { ...ACCESS, people: ACCESS.people.map((p) => (p.id === 'p2' ? { ...p, sign_in: true } : p)) };
  const held = (url: string, method: string): Reply =>
    method === 'POST' ? { status: 409, body: { error: { code: 'version_conflict', detail: 'Changed' } } } : url.startsWith('/api/v2/access') ? { status: 200, body: MINE } : { status: 200, body: {} };

  it('Make owner needs the email typed, keeps no admin unless chosen, and sends the hand-over @s753', async () => {
    serve(held);
    queryClient.setQueryData(accessQuery.queryKey, MINE);
    show(<SettingsPage section="people" open="p2" />, '/settings/people?open=p2');
    fireEvent.click(await screen.findByRole('button', { name: 'Make owner' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Make Ben Sample the owner?' });
    const go = within(ask).getByRole('button', { name: 'Make owner' });
    expect(go.hasAttribute('disabled')).toBe(true);
    expect(within(ask).getByRole('checkbox', { name: 'Keep Ana Test as an admin' }).getAttribute('aria-checked')).toBe('false');
    fireEvent.change(within(ask).getByLabelText('Type ben@example.test to confirm'), { target: { value: 'ben@example.test' } });
    fireEvent.click(go);
    await waitFor(() =>
      expect(sent[0]).toMatchObject({ url: '/api/v2/access/owner', method: 'POST', body: { person: 'p2', previous_owner_bot_admin: false, confirm: true } }),
    );
    expect(await run('settings.makeOwner', { person: 'p2', keepAdmin: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Change email needs the new address twice and sends both @s752', async () => {
    serve(held);
    queryClient.setQueryData(accessQuery.queryKey, MINE);
    show(<SettingsPage section="people" open="p1" />, '/settings/people?open=p1');
    fireEvent.click(await screen.findByRole('button', { name: 'Change email' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Change your sign-in email?' });
    const go = within(ask).getByRole('button', { name: 'Change email' });
    fireEvent.change(within(ask).getByLabelText('New sign-in email'), { target: { value: 'new@example.test' } });
    expect(go.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(ask).getByLabelText('Type it again'), { target: { value: 'new@example.test' } });
    fireEvent.click(go);
    await waitFor(() =>
      expect(sent[0]).toMatchObject({
        url: '/api/v2/access/owner/email',
        method: 'POST',
        body: { email: 'new@example.test', confirm_email: 'new@example.test', confirm: true },
      }),
    );
  });
});

describe('Settings › Computers › when idle', () => {
  it('the computers-sleep switch sends at once @s705', async () => {
    serve(() => ({ status: 404, body: { detail: 'not_found' } }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(computersQuery.queryKey, []);
    queryClient.setQueryData(sleepQuery.queryKey, { hosted: true, managed: false, hours: 2, runners: false });
    show(<SettingsPage section="computers" />, '/settings/computers', () => loadSection(queryClient, 'computers'));
    const sw = await screen.findByRole('switch', { name: "Agents' computers sleep too" });
    fireEvent.click(sw);
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/sleep', method: 'POST', body: { runners: true } }));
  });
});

describe('Settings › Agents › a subscription', () => {
  it('Edit renames it and sends only the name when the holder is unchanged @s747', async () => {
    serve((url, method) => (method === 'POST' ? { status: 200, body: {} } : url.startsWith('/api/v2/access') ? { status: 200, body: ACCESS } : { status: 404, body: { detail: 'not_found' } }));
    const kind = { connect: true, help: '', kind: 'claude_login' as const, label: 'Claude', owned: true, runtime: 'claude' as const, secret_label: '' };
    const plan = { id: 's1', kind: 'claude_login', name: 'Plan One', label: 'Claude', status: 'working', in_use_on: [], owner_id: 'human:p1', owner_name: 'Ana Test' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [kind], subscriptions: [plan] } as never);
    queryClient.setQueryData(computersQuery.queryKey, []);
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<Agents open="s1" />, '/settings/agents?open=s1');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const edit = await screen.findByRole('dialog', { name: 'Edit Plan One' });
    fireEvent.change(within(edit).getByLabelText('Name'), { target: { value: 'Plan Two' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/model-subscriptions/s1', method: 'POST', body: { name: 'Plan Two' } }));
    expect((sent[0]?.body as Record<string, unknown>).owner_id).toBeUndefined();
  });
});

describe('Settings › Security', () => {
  it('a detector switch sends at once; Tico may turn one on but not off, nor open Tico support @s1079', async () => {
    serve();
    queryClient.setQueryData(screeningQuery.queryKey, { modes: { webhook: 'hold', mail: 'warn' }, layers: { patterns: true, model: true }, sources: [] });
    show(<SettingsPage section="security" />, '/settings/security');
    fireEvent.click(await screen.findByRole('switch', { name: 'Patterns' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/screening', method: 'PUT', body: { patterns: false } }));
    expect(await run('settings.setDetector', { detector: 'model', on: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.setScreening', { door: 'webhook', mode: 'off' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.setSupport', { on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(1);
  });

  it('When caught sets every door but mail, which has its own row @s1079', async () => {
    const SCREEN = { modes: { webhook: 'warn', idea: 'warn', mail: 'warn' }, layers: { patterns: true, model: true }, sources: [] };
    serve(() => ({ status: 200, body: SCREEN }));
    queryClient.setQueryData(screeningQuery.queryKey, SCREEN as never);
    show(<SettingsPage section="security" />, '/settings/security');
    fireEvent.click(within(await screen.findByRole('group', { name: 'When caught' })).getByRole('button', { name: 'Hold' }));
    await waitFor(() => expect(sent.map((x) => x.body)).toEqual([{ webhook: 'hold' }, { idea: 'hold' }]));
    expect(within(screen.getByRole('group', { name: 'In mail' })).queryByRole('button', { name: 'Hold' })).toBeNull();
  });

  const security = () => {
    queryClient.setQueryData(accessQuery.queryKey, FULL);
    queryClient.setQueryData(screeningQuery.queryKey, {
      modes: { webhook: 'warn' },
      layers: { patterns: true, model: true },
      sources: [{ id: 'webhook', name: 'Webhooks' }],
      recent: [{ at: '2026-11-18T10:00:00Z', action: 'held', source: 'webhook', score: 0.91, task: 't9' }],
    } as never);
    serveFull((url, method) => {
      if (url === '/api/v2/support/access' && method === 'GET')
        return ok({ available: true, active: null, last: null, sessions: [], can_start: true, can_full: true });
      if (url === '/api/v2/support/tickets') return ok({ tickets: [{ id: 't1', status: 'open', created_at: '2026-11-18T09:00:00Z' }] });
      if (url.startsWith('/api/v2/support')) return ok({ available: false });
      return undefined;
    });
    show(<Security />, '/settings/security');
  };

  it('lists the holds and warnings @s1079', async () => {
    security();
    const screening = await screen.findByRole('region', { name: 'Screening' });
    fireEvent.click(within(screening).getByRole('button', { name: 'Show' }));
    const caught = await screen.findByRole('dialog', { name: 'Held and warned' });
    expect(within(caught).getByText('Held · webhook · 91%')).toBeTruthy();
    expect(within(caught).getByRole('link').getAttribute('href')).toBe('/task/t9');
  });

  it('lets Tico support in against the open request; letting in is closed to Tico, Stop is open @s1124', async () => {
    security();
    const support = await screen.findByRole('region', { name: 'Tico support' });
    fireEvent.click(await within(support).findByRole('switch', { name: 'Tico support may sign in' }));
    const dlg = await screen.findByRole('alertdialog', { name: 'Let Tico support sign in?' });
    fireEvent.click(await within(dlg).findByRole('button', { name: 'Let in' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/support/access', method: 'POST', body: { ticket_id: 't1', tier: 'app', hours: 72 } }));
    expect(await run('settings.letSupportIn', { ticket: 't1', tier: 'full', hours: 1 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.stopSupport', { session: 's1' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent).toContainEqual({ url: '/api/v2/support/access/s1/stop', method: 'POST', body: {} });
  });
});

describe('Settings › Models @s1076 @s710', () => {
  it('When a plan runs out has each computer’s Gateway switch, which sends at once', async () => {
    const studio = { id: 'r1', label: 'Studio', online: true, gateway_fallback: false };
    serve((url) => {
      if (url === '/api/v2/providers') return { status: 200, body: { enabled: [], default: {}, revision: 0 } };
      if (url === '/api/v2/operations') return { status: 200, body: { machines: [{ ...studio, gateway_fallback: true }] } };
      return { status: 200, body: {} };
    });
    queryClient.setQueryData(modelsQuery.queryKey, { models: [{ id: 'm1', label: 'Sample Model', provider: 'anthropic' }] } as never);
    queryClient.setQueryData(computersQuery.queryKey, [studio]);
    show(<SettingsPage section="models" />, '/settings/models', () => loadSection(queryClient, 'models'));
    const out = await screen.findByRole('region', { name: 'When a plan runs out' });
    fireEvent.click(within(out).getByRole('switch', { name: 'Studio: use Tico Gateway' }));
    await waitFor(() => expect(sent[0]).toMatchObject({ url: '/api/v2/computers/r1/gateway-fallback', body: { on: true } }));
    expect(screen.queryByRole('region', { name: 'Anthropic' })).toBeNull();
  });
});

describe('Settings › History @s1080', () => {
  it('History is one list; People and Agents narrow it in the address', async () => {
    serve();
    queryClient.setQueryData(historyQuery.queryKey, {
      items: [
        { id: 'h1', at: '2026-11-18T10:00:00Z', actor: 'human:p2', kind: 'task', subject: 'Plan the launch', result: 'Done' },
        { id: 'h2', at: '2026-11-18T09:00:00Z', actor: 'bot:builder', kind: 'run', subject: 'Built it', result: '' },
      ],
      names: { 'human:p2': 'Ben Sample', 'bot:builder': 'Builder' },
    });
    const router = show(<SettingsPage section="history" who="people" />, '/settings/history?who=people');
    expect(await screen.findByTitle('Ben Sample, Plan the launch: Done')).toBeTruthy();
    expect(screen.queryByTitle('Builder, Built it')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Agents' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ who: 'agents' }));
  });

  it('reads the next page by its cursor when the list reaches its end', async () => {
    serve((url) => (url.includes('before=c1') ? ok({ items: [{ id: 'h0', at: '2026-11-01T09:00:00Z', actor: 'human:p1', kind: 'task', subject: 'Older work' }], names: { 'human:p1': 'Ana Sample' }, next: null }) : undefined));
    queryClient.setQueryData(historyQuery.queryKey, { items: [{ id: 'h1', at: '2026-11-18T09:00:00Z', actor: 'human:p1', kind: 'task', subject: 'New work', result: '' }], names: { 'human:p1': 'Ana Sample' }, next: 'c1' } as never);
    show(<SettingsPage section="history" />, '/settings/history');
    fireEvent.click(await screen.findByRole('button', { name: 'End' }));
    expect(await screen.findByTitle('Ana Sample, Older work')).toBeTruthy();
    expect(screen.getByTitle('Ana Sample, New work')).toBeTruthy();
    expect(read.filter((u) => u.includes('before=')).length).toBe(1);
  });

  it('keeps older matches reachable after a filtered page is empty and the next read fails', async () => {
    let attempts = 0;
    serve((url) => {
      if (!url.includes('before=c1')) return undefined;
      if (++attempts === 1) return { status: 422, body: { error: { code: 'timeout', detail: 'History stopped' } } };
      return ok({ items: [{ id: 'h0', at: '2026-11-01T09:00:00Z', actor: 'human:p2', kind: 'task', subject: 'Older work' }], names: { 'human:p2': 'Ben Sample' } });
    });
    queryClient.setQueryData(historyQuery.queryKey, {
      items: [{ id: 'h1', at: '2026-11-18T09:00:00Z', actor: 'bot:builder', kind: 'run', result: 'Done' }],
      names: { 'bot:builder': 'Builder' },
      next: 'c1',
    });
    show(<SettingsPage section="history" who="people" />, '/settings/history?who=people');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByTitle('Ben Sample, Older work')).toBeTruthy();
    expect(attempts).toBe(2);
  });
});

describe('Settings › General @s1072', () => {
  const SETUP = {
    names: { company_name: '', app_name: 'Tico', assistant_name: 'Helper', owner_name: 'Ana Setup', team_domain: 'example.test', website: 'https://example.test' },
    answers: { building: 'Garden kits', what_we_do: 'We sell kits', customers: 'consumers', regions: ['Oregon'], teams_off: ['Growth'], first_job: 'grow' },
    selected: { helper: { template: 'assistant', display_name: 'Helper', instructions: 'Be brief', reports_to: '' } },
    completed: '2026-01-01T00:00:00Z',
    bots: [],
    needed: false,
  };
  const repo = (full_name: string, enabled: boolean) => ({ full_name, enabled, reachable: true, default_branch: 'main', bot_repo: false, last_seen_at: null, setup_command: null, setup_source: null });
  const REPOS = { github_connected: true, github_installed: true, new_bot_default: 'own', repositories: [repo('acme/web', true), repo('acme/api', false)] };
  /** The name's only route is the whole setup record (PUT /api/v2/setup): it is read and sent back with only the name changed. */
  const general = (putStatus = 200, repos: unknown = { github_connected: false, github_installed: false, new_bot_default: 'own', repositories: [] }) => {
    serve((url, method) => {
      if (url === '/api/v2/setup' && method === 'GET') return ok(SETUP);
      if (url === '/api/v2/setup' && method === 'PUT')
        return putStatus === 200 ? ok(SETUP) : { status: putStatus, body: { error: { code: 'forbidden', detail: 'Only the owner may set this company up' } } };
      if (url.startsWith('/api/v2/repositories')) return ok(repos);
      if (url === '/api/v2/system/storage') return ok({ active: null, server: { mode: 'disk' }, tico: null, copy: { done: 0, total: 0 }, earlier: [] });
      return undefined;
    });
    show(<General />, '/settings/general');
  };
  const companyName = () => (queryClient.getQueryData(['config']) as { company_name: string }).company_name;
  beforeEach(() => queryClient.setQueryData(accessQuery.queryKey, ACCESS));

  it('the owner renames the company in place; only the name changes in the setup sent back', async () => {
    general();
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    const box = screen.getByRole('textbox', { name: 'Company name' });
    fireEvent.change(box, { target: { value: '  Renamed Co ' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    // Shown at once, before the server answers.
    expect(await screen.findByRole('button', { name: 'Company name, Renamed Co' })).toBeTruthy();
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      url: '/api/v2/setup',
      method: 'PUT',
      body: {
        // owner_name blank: a name there would rename the owner on the roster; blank leaves the roster as it is.
        names: { ...SETUP.names, company_name: 'Renamed Co', owner_name: '' },
        answers: SETUP.answers,
        selected: SETUP.selected,
      },
    });
    expect(companyName()).toBe('Renamed Co');
  });

  it('leaving the field saves; Escape and an unchanged name send nothing', async () => {
    general();
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Company name' }), { key: 'Escape' });
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    fireEvent.blur(screen.getByRole('textbox', { name: 'Company name' }));
    expect(sent).toEqual([]);
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    const box = screen.getByRole('textbox', { name: 'Company name' });
    fireEvent.change(box, { target: { value: 'Blur Co' } });
    fireEvent.blur(box);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0]?.body as { names: { company_name: string } }).names.company_name).toBe('Blur Co');
  });

  it('a refusal takes the name back and says so', async () => {
    general(403);
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    const box = screen.getByRole('textbox', { name: 'Company name' });
    fireEvent.change(box, { target: { value: 'Nope Co' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(await screen.findByText(/Couldn't save/)).toBeTruthy();
    expect(companyName()).toBe('Sample Co');
  });

  it('an admin reads the name; Tico may rename (ordinary work)', async () => {
    queryClient.setQueryData(accessQuery.queryKey, { ...ACCESS, you: 'admin' });
    general();
    expect(await screen.findByText('Sample Co')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Company name/ })).toBeNull();
    expect(await run('settings.renameCompany', { name: 'Tico Co' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.map((x) => x.method + ' ' + x.url)).toEqual(['PUT /api/v2/setup']);
  });

  it('shows the icon and the Repositories card; Add goes through an action', async () => {
    general(200, REPOS);
    expect(await screen.findByRole('img', { name: 'Team icon' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change' })).toBeTruthy();
    expect(within(await screen.findByRole('region', { name: 'Repositories' })).getByText('acme/web')).toBeTruthy();
    const off = await unfold('Not in use');
    fireEvent.click(within(off).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/repositories/acme/api', method: 'PUT', body: { enabled: true } }));
  });

  it('Tico may not change the icon or make a repository', async () => {
    serve();
    const file = new File(['x'], 'icon.png', { type: 'image/png' });
    expect(await run('settings.teamIcon', { file }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.createRepo', { name: 'site' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('Settings › People: joining, left and adds @s1073', () => {
  beforeEach(() => queryClient.setQueryData(accessQuery.queryKey, FULL));

  it('adds a person, and restores someone who left', async () => {
    serveFull();
    show(
      <>
        <AddPersonButton />
        <People />
      </>,
      '/settings/people',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Add person' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add a person' });
    fireEvent.change(within(dlg).getByRole('textbox', { name: 'Email' }), { target: { value: 'dee@example.test' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Invite' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/access/humans', method: 'POST', body: { email: 'dee@example.test', name: '' } }));
    const left = await unfold('Left');
    fireEvent.click(within(left).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(sent).toContainEqual({ url: '/api/v2/access/humans/p3', method: 'POST', body: { left: false } }));
  });

  it('widening who can join, raising the limit and restoring are closed to Tico; tightening is open', async () => {
    serve();
    expect(await run('settings.setJoining', { allowed: FULL.allowed, domains: ['other.test', 'example.test'] }, { via: 'tico' })).toMatchObject({
      ok: false,
      error: 'not_allowed',
    });
    expect(await run('settings.agentLimit', { limit: 5 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.restorePerson', { person: 'p3' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect(await run('settings.setJoining', { allowed: [], domains: [] }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent[0]).toMatchObject({ url: '/api/v2/access/allow', method: 'PUT', body: { allowed: [], allowed_domains: [] } });
  });
});

describe('Settings › Tasks @s1077', () => {
  const ROAD = { labels: ['feature'], bot_public_approval: true };
  const RULES = { mode: 'on', budget: 10, criteria: ['Spending', 'Hiring'], own: false };

  it('lists the public roadmap labels with their address, adds one; Tico may only take one off', async () => {
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
    // Tico may take a label off, but not put one on.
    expect(await run('settings.roadmapLabels', { labels: ['feature', 'idea', 'secret'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('settings.roadmapLabels', { labels: [] }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.at(-1)).toMatchObject({ url: '/api/v2/roadmaps/settings', body: { labels: [] } });
  });

  it('what agents may ask: Screen asks saves your own rules, Everyone saves the team default', async () => {
    const ASK = { settings: RULES, team: { ...RULES, own: true }, may_edit_team: true };
    serve((url, method) => (method === 'GET' && url === '/api/v2/ask-ingest/settings' ? ok(ASK) : url === '/api/v2/roadmaps/settings' ? ok(ROAD) : undefined));
    queryClient.setQueryData(roadmapQuery.queryKey, ROAD);
    queryClient.setQueryData(labelsQuery.queryKey, [{ id: '1', key: 'feature', label: 'Feature' }] as never);
    queryClient.setQueryData(askQuery.queryKey, ASK);
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const ask = await screen.findByRole('region', { name: 'What agents may ask' });
    fireEvent.click(within(ask).getByRole('switch', { name: 'Screen asks' }));
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({ url: '/api/v2/ask-ingest/settings', method: 'PUT', body: { mode: 'off', budget: 10, criteria: ['Spending', 'Hiring'], team: false } }),
    );
    fireEvent.click(within(ask).getByRole('button', { name: 'Everyone' }));
    fireEvent.change(within(ask).getByRole('spinbutton', { name: 'Tighten after' }), { target: { value: '25' } });
    fireEvent.keyDown(within(ask).getByRole('spinbutton', { name: 'Tighten after' }), { key: 'Enter' });
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: 'PUT', body: { budget: 25, team: true } }));
    const lines = screen.getByRole('region', { name: /Ask me about/ });
    expect(within(lines).getByTitle('Spending')).toBeTruthy();
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

describe('Search settings @s1130', () => {
  const HITS = {
    settings: [
      { key: 'a', name: 'Public roadmap labels', who: 'admin', card: '#/settings/tasks/settings-roadmaps', where: 'Tasks' },
      { key: 'b', name: 'Theme', who: 'person', card: '#/settings/general', where: 'General' },
    ],
  };

  it('arrow keys move through the results', async () => {
    serve((url) => (url.startsWith('/api/v2/settings/catalog') ? ok(HITS) : undefined));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="general" />, '/settings/general');
    const box = await screen.findByRole('textbox', { name: 'Search settings' });
    fireEvent.change(box, { target: { value: 'the' } });
    const first = await screen.findByRole('link', { name: 'Public roadmap labels' });
    expect(first.getAttribute('href')).toBe('/settings/tasks');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(first.getAttribute('aria-current')).toBe('true');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(screen.getByRole('link', { name: 'Theme' }).getAttribute('aria-current')).toBe('true');
    expect(first.getAttribute('aria-current')).toBeNull();
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(first.getAttribute('aria-current')).toBe('true');
  });

  it('a result opens the setting itself: its part of the section takes the focus', async () => {
    serve((url) => (url.startsWith('/api/v2/settings/catalog') ? ok(HITS) : undefined));
    queryClient.setQueryData(roadmapQuery.queryKey, { labels: ['feature'], bot_public_approval: true });
    queryClient.setQueryData(labelsQuery.queryKey, [{ id: '1', key: 'feature', label: 'Feature' }] as never);
    show(<SettingsPage section="tasks" />, '/settings/tasks');
    const box = await screen.findByRole('textbox', { name: 'Search settings' });
    fireEvent.change(box, { target: { value: 'road' } });
    await screen.findByRole('link', { name: 'Public roadmap labels' });
    fireEvent.keyDown(box, { key: 'Enter' });
    const road = await screen.findByRole('region', { name: /Public roadmap/ });
    await waitFor(() => expect(road.contains(document.activeElement)).toBe(true));
  });
});
