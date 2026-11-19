import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { FirstRun } from './FirstRun';
import { GithubConnection } from './GithubConnection';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Made-up data only. The server is a table of answers; every call is kept to check what was (and was not) sent. */

type Answer = { status?: number; body: unknown };
let answers: Record<string, Answer | Answer[]>;
let calls: { method: string; url: string; body?: string }[];

const json = (a: Answer) => new Response(JSON.stringify(a.body), { status: a.status ?? 200, headers: { 'Content-Type': 'application/json' } });
const refusal = (status: number, code: string, detail: string): Answer => ({ status, body: { error: { code, detail } } });

function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

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
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: typeof init?.body === 'string' ? init.body : undefined });
    const key = `${method} ${url.split('?')[0]}`;
    const a = answers[key];
    if (!a) return json({ status: 404, body: {} });
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
    show(<FirstRun />, '/start?setup=github');
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
    show(<FirstRun />, '/start?setup=github');
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(within(dialog).getByRole('alert').textContent).toBe('GitHub did not give a setup page');
    expect(posted()).toHaveLength(0);
  });

  it('a lost key is replaced only after the owner says so', async () => {
    answers['GET /api/v2/github/app/manifest'] = [refusal(409, 'github_key_missing', 'made-up server words'), { body: SETUP }];
    answers['POST /api/v2/github/app/replace-lost-key'] = { body: { replaced: true } };
    show(<FirstRun />, '/start?setup=github');
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
