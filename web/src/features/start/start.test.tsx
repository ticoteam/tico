import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { ApiError } from '@/api/client';
import { directoryQuery, queryClient } from '@/data';
// Keep the lazy Settings import's transform outside the sign-in test.
import '@/features/settings/exports';
import { subscriptionsQuery } from '@/features/settings/routing';
import { FirstRun } from './FirstRun';
import { useStartGate } from './gate';
import { GithubConnection } from './GithubConnection';
import { landing, type SetupRecord } from './queries';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Made-up data only. The server is a table of answers (anything else gets `otherwise`); every call is kept to check what was (and was not) sent. */

type Answer = { status?: number; body: unknown };
let answers: Record<string, Answer | Answer[]>;
let otherwise: (method: string, url: string) => Answer;
let calls: { method: string; url: string; body?: string }[];

const json = (a: Answer) => new Response(JSON.stringify(a.body), { status: a.status ?? 200, headers: { 'Content-Type': 'application/json' } });
const refusal = (status: number, code: string, detail: string): Answer => ({ status, body: { error: { code, detail } } });
/** Every call not in the table answers 200 with what `reply` gives. */
const serve = (reply: (method: string, url: string) => unknown) => (otherwise = (method, url) => ({ body: reply(method, url) ?? {} }));

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const RECORD: SetupRecord = {
  names: { company_name: '', app_name: '', assistant_name: '', owner_name: '', team_domain: '', website: '' },
  answers: {},
  selected: {},
  completed: null,
  needed: true,
  default_chart: [
    { team: 'Engineering', on: true },
    { team: 'Support', on: false },
    { team: 'Sales', on: false },
    { team: 'Founders', on: false, locked: true },
  ],
  first_jobs: {
    choices: [
      { key: 'build', label: 'Build the app', seat: 'eng', team: 'Engineering' },
      { key: 'support', label: 'Answer customers', seat: 'sup', team: 'Support' },
    ],
    picked: 'build',
  },
};

const SETUP = {
  action: 'https://github.com/organizations/acme-co/settings/apps/new?state=made-up-state',
  manifest: { name: 'Acme Tico' },
  state: 'made-up-state',
};
const NOT_CONNECTED: Answer = { body: { connected: false, app_name: 'Acme Tico' } };

let submit = vi.fn<(form: HTMLFormElement) => void>();

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  sessionStorage.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  calls = [];
  answers = { 'GET /api/v2/github/app': NOT_CONNECTED };
  otherwise = () => ({ status: 404, body: {} });
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: typeof init?.body === 'string' ? init.body : undefined });
    const key = `${method} ${url.split('?')[0]}`;
    const a = answers[key];
    if (!a) return json(otherwise(method, url));
    return json(Array.isArray(a) ? (a.length > 1 ? a.shift()! : a[0]!) : a);
  });
  submit = vi.fn<(form: HTMLFormElement) => void>();
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    submit(this);
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.querySelectorAll('form[hidden]').forEach((f) => f.remove());
});

const posted = () => submit.mock.calls.map(([f]) => f);
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split('?')[0] === path);
const putBody = () => JSON.parse(sent('PUT', '/api/v2/setup')[0]!.body!) as unknown;

describe('Connect GitHub @s1158', () => {
  it('a return with forged or unknown address values does nothing: the server decides', async () => {
    show(<GithubConnection />, '/library/tools?tool=github&github=connected&code=c0de-xyz&state=forged-state&installation_id=99&setup_action=install&error=boom');
    expect(await screen.findByText('Not connected')).toBeTruthy();
    // Only the state was read; nothing was finished, saved or taken from the address.
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['GET /api/v2/github/app']);
    expect(screen.queryByRole('button', { name: 'Finish' })).toBeNull();
    expect(sessionStorage.length).toBe(0);
    expect(document.body.textContent).not.toMatch(/c0de-xyz|forged-state|boom/);
    for (const a of document.querySelectorAll('a')) expect(a.getAttribute('href') ?? '').not.toMatch(/c0de|forged/);
  });

  it('hands off to GitHub with the setup, and the setup never enters the cache', async () => {
    answers['GET /api/v2/github/app/manifest'] = { body: SETUP };
    show(<FirstRun />, '/tasks?setup=github');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Connect GitHub' })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('radio', { name: /An organization/ }));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Organization' }), { target: { value: 'acme-co' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    const form = posted()[0]!;
    expect(form.method).toBe('post');
    expect(form.action).toBe(SETUP.action);
    expect(JSON.parse((form.elements.namedItem('manifest') as HTMLInputElement).value)).toEqual(SETUP.manifest);
    const asked = new URL(sent('GET', '/api/v2/github/app/manifest')[0]!.url, 'http://x');
    expect(asked.searchParams.get('org')).toBe('acme-co');
    expect(asked.searchParams.get('administration')).toBe('true');
    const cached = JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]));
    expect(cached).not.toContain('made-up-state');
  });

  it('a setup page that is not GitHub is never posted to', async () => {
    answers['GET /api/v2/github/app/manifest'] = { body: { ...SETUP, action: 'https://elsewhere.example/apps/new' } };
    show(<FirstRun />, '/tasks?setup=github');
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(within(dialog).getByRole('alert').textContent).toBe('GitHub did not give a setup page');
    expect(posted()).toHaveLength(0);
  });

  it('a lost key is replaced only after the owner says so', async () => {
    answers['GET /api/v2/github/app/manifest'] = [refusal(409, 'github_key_missing', 'made-up server words'), { body: SETUP }];
    answers['POST /api/v2/github/app/replace-lost-key'] = { body: { replaced: true } };
    show(<FirstRun />, '/tasks?setup=github');
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await within(dialog).findByText(/Replacing it removes the GitHub app and directory sync keys/)).toBeTruthy();
    expect(sent('POST', '/api/v2/github/app/replace-lost-key')).toHaveLength(0);
    expect(posted()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace key' }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(sent('POST', '/api/v2/github/app/replace-lost-key')).toHaveLength(1);
  });

  it('a refused finish shows the server’s reason and stays not connected', async () => {
    sessionStorage.setItem('tico.githubSetup', 'made-up-state');
    answers['POST /api/v2/github/app/finish'] = refusal(409, 'github_not_returned', 'GitHub has not sent this setup back yet');
    show(<GithubConnection />, '/library/tools?tool=github');
    fireEvent.click(await screen.findByRole('button', { name: 'Finish' }));
    expect((await screen.findByRole('alert')).textContent).toBe('GitHub has not sent this setup back yet');
    expect(JSON.parse(sent('POST', '/api/v2/github/app/finish')[0]!.body!)).toEqual({ state: 'made-up-state' });
    expect(screen.getByText('Not connected')).toBeTruthy();
    // Not returned yet: the setup is kept to try again.
    expect(sessionStorage.getItem('tico.githubSetup')).toBe('made-up-state');
  });

  it('connected: the account, and Check again asks GitHub for the missing permission', async () => {
    const app = {
      connected: true,
      org: 'acme-co',
      slug: 'acme-co-tico',
      account_type: 'Organization',
      installed: true,
      administration: false,
      permissions_url: 'https://github.com/organizations/acme-co/settings/apps/acme-co-tico/permissions',
    };
    answers['GET /api/v2/github/app'] = { body: app };
    answers['POST /api/v2/github/app/check-permissions'] = { body: { ...app, administration: true } };
    show(<GithubConnection />, '/library/tools?tool=github');
    expect(await screen.findByText('acme-co')).toBeTruthy();
    expect(screen.getByText('Needs Administration')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Permissions' }).getAttribute('href')).toBe(app.permissions_url);
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('Agents create them')).toBeTruthy();
  });
});

function Probe() {
  const gate = useStartGate();
  return <span>{gate.firstRun ? 'First run' : 'The app'}</span>;
}

describe('Setup, first run @s1154', () => {
  it('an owner the server says still owes the first run lands with it open; others land plain', async () => {
    serve(() => ({}));
    queryClient.setQueryData(['config'], { onboarding_needed: true });
    expect(await landing(queryClient)).toEqual({ setup: true });
    queryClient.setQueryData(['config'], { onboarding_needed: false });
    expect(await landing(queryClient)).toEqual({ setup: false });
  });

  it('not signed in, it leaves the answer to the sign-in gate', async () => {
    queryClient.removeQueries({ queryKey: ['me'] });
    otherwise = () => refusal(401, 'signin', 'Sign in');
    expect(await landing(queryClient)).toBeNull();
    expect(queryClient.getQueryState(['me'])?.error).toBeInstanceOf(ApiError);
  });

  it('the address opens it over the app for the owner', async () => {
    serve(() => ({}));
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [], byId: {} });
    const router = show(<Probe />, '/?setup=path');
    expect(await screen.findByText('First run')).toBeTruthy();
    expect(router.state.location.search).toEqual({ setup: 'path' });
  });

  it('sends anyone else to Tasks, with no first run', async () => {
    serve(() => ({}));
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [], byId: {} });
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
    const router = show(<Probe />, '/?setup=path');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(screen.getByText('The app')).toBeTruthy();
    expect(router.state.location.search).toEqual({});
  });
});

describe('First run @s1154 @s1171', () => {
  it('opens from the address over the app, with "Have my agent set it up" chosen first', async () => {
    serve(() => ({}));
    show(<FirstRun />, '/tasks?setup=path');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Set up your company' })).toBeTruthy();
    expect(within(dialog).getByRole('radio', { name: /Have my agent set it up/ }).getAttribute('aria-checked')).toBe('true');
    expect(within(dialog).getByRole('radio', { name: /Set it up myself/ }).getAttribute('aria-checked')).toBe('false');
    expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeTruthy();
  });
});

describe('Setup dialog, three steps @s1171', () => {
  it('step 1 saves the company name and moves to step 2', async () => {
    serve((method, url) => {
      if (url.startsWith('/api/v2/model-subscriptions')) return { subscriptions: [] };
      if (method === 'PUT') return { ...RECORD, names: { ...RECORD.names, company_name: 'Fernhill' } };
      return RECORD;
    });
    queryClient.setQueryData(['setup'], RECORD);
    const router = show(<FirstRun />, '/tasks?setup=name');
    const dialog = await screen.findByRole('dialog', { name: 'Name your company' });
    expect(within(dialog).getByRole('img', { name: 'Step 1 of 3' })).toBeTruthy();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Company name' }), { target: { value: ' Fernhill ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect((router.state.location.search as { setup?: string }).setup).toBe('model'));
    expect(putBody()).toMatchObject({ names: { company_name: 'Fernhill' } });
    const next = await screen.findByRole('dialog', { name: 'Connect your AI plan' });
    expect(within(next).getByRole('img', { name: 'Step 2 of 3' })).toBeTruthy();
  });

  it('connects inline, retries, and continues only after a provider is ready', async () => {
    let allowed = false;
    let saved = false;
    const usage = { used_pct: 100, resets_at: '2099-07-01T00:00:00Z' };
    const existing = { id: 'old', name: 'Held plan', runtime: 'claude', status: 'working', available: false, usage: { ...usage, quota_hold_until: usage.resets_at } };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [], subscriptions: [existing] } as never);
    const codePage = 'https://auth.example/claude';
    otherwise = (method, url) => {
      if (url.endsWith('/connect')) {
        return allowed
          ? { body: { id: 'login1', runner_id: 'r1', state: 'waiting', url: codePage, code: 'TEMP-CODE' } }
          : refusal(409, 'offline', 'No computer can sign in');
      }
      if (url.endsWith('/logins/login1/cancel')) return { body: { state: 'canceled' } };
      if (url.endsWith('/logins/login1')) return { body: { id: 'login1', runner_id: 'r1', state: 'waiting', url: codePage, code: 'TEMP-CODE', not_yours: true } };
      if (url === '/api/v2/model-subscriptions') {
        if (method === 'POST') {
          saved = true;
          return { body: { id: 'new', name: 'New plan' } };
        }
        return { body: { kinds: [], subscriptions: saved ? [existing, { id: 'new', name: 'New plan', runtime: 'claude', status: 'working', available: true, usage }] : [existing] } };
      }
      return { body: RECORD };
    };
    const router = show(<><ActionsRoot dot={() => null} fold={() => {}} /><FirstRun /></>, '/tasks?setup=model');
    const dialog = await screen.findByRole('dialog', { name: 'Connect your AI plan' });
    expect(within(dialog).getByRole('button', { name: 'Use an API key' })).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Continue' })).toBeNull();
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Connect' })[0]!);
    await within(dialog).findByRole('region', { name: 'Connect Claude plan' });
    const connect = () => within(within(dialog).getByRole('region', { name: 'Connect Claude plan' }));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(await connect().findByRole('button', { name: 'Paste instead' })).toBeTruthy();
    allowed = true;
    fireEvent.click(connect().getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(connect().getByRole('link', { name: 'Open Claude' })).toBeTruthy());
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain('TEMP-CODE');
    fireEvent.click(await connect().findByRole('button', { name: 'Paste instead' }));
    fireEvent.change(await connect().findByLabelText('Setup token'), { target: { value: 'made-up-plan-token' } });
    fireEvent.click(connect().getByRole('button', { name: 'Connect' }));
    expect(sent('POST', '/api/v2/model-subscriptions/connect')).toHaveLength(2);
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ setup: 'team' }));
  });

  it('step 3 makes the team with the first job picked, then lands on Tasks with the dialog shut', async () => {
    serve((method) => (method === 'PUT' ? RECORD : {}));
    queryClient.setQueryData(['setup'], RECORD);
    const router = show(<FirstRun />, '/tasks?setup=team');
    const dialog = await screen.findByRole('dialog', { name: 'Pick a team' });
    expect(within(dialog).getByRole('img', { name: 'Step 3 of 3' })).toBeTruthy();
    expect(within(dialog).getByRole('radio', { name: /Build the app/ }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(within(dialog).getByRole('radio', { name: /Answer customers/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    await waitFor(() => expect((router.state.location.search as { setup?: string }).setup).toBeUndefined());
    // the chart as the server laid it out, less the teams off; the picked job's team and a locked one are never off
    expect(putBody()).toMatchObject({ answers: { first_job: 'support', teams_off: ['Sales'] }, selected: {} });
    expect(sent('POST', '/api/v2/setup/complete')).toHaveLength(1);
  });

  it('a pasted key is a password field held in the step only, never in the cache', async () => {
    serve((_method, url) => (url.startsWith('/api/v2/model-subscriptions') ? { subscriptions: [] } : {}));
    show(<FirstRun />, '/tasks?setup=paste');
    const dialog = await screen.findByRole('dialog', { name: 'Paste a key' });
    const key = within(dialog).getByLabelText('Key') as HTMLInputElement;
    expect(key.type).toBe('password');
    fireEvent.change(key, { target: { value: 'made-up-secret-123' } });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('made-up-secret-123');
  });
});
