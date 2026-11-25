import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data';
import { GithubConnection } from './GithubConnection';
import { landing } from './queries';

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

  it('a read that was refused says why, and Try again reads it again', async () => {
    answers['GET /api/v2/github/app'] = [refusal(400, 'bad', 'No.'), NOT_CONNECTED];
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    show(<GithubConnection />, '/library/tools?tool=github');
    expect(await screen.findByRole('heading', { name: "GitHub didn't load · request refused" })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Not connected')).toBeTruthy();
  });

  it('hands off to GitHub with the setup, and the setup never enters the cache', async () => {
    answers['GET /api/v2/github/app/manifest'] = { body: SETUP };
    show(<GithubConnection />, '/library/tools?tool=github');
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
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
    show(<GithubConnection />, '/library/tools?tool=github');
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(within(dialog).getByRole('alert').textContent).toBe('GitHub did not give a setup page');
    expect(posted()).toHaveLength(0);
  });

  it('a lost key is replaced only after the owner says so', async () => {
    answers['GET /api/v2/github/app/manifest'] = [refusal(409, 'github_key_missing', 'made-up server words'), { body: SETUP }];
    answers['POST /api/v2/github/app/replace-lost-key'] = { body: { replaced: true } };
    show(<GithubConnection />, '/library/tools?tool=github');
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
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

  it('a reconnect that made a new app shows both bots, and Disconnect says a new connect needs the new one', async () => {
    const notice =
      "This is a new GitHub app. GitHub now sees Tico's bots as made-up-2[bot] (App ID 22), not made-up[bot] (App ID 11). " +
      "Update CI workflows, rulesets and branch rules that name the old bot, or they will skip or block your agents' pull requests.";
    answers['GET /api/v2/github/app'] = {
      body: {
        connected: true,
        org: 'acme-co',
        slug: 'made-up-2',
        account_type: 'Organization',
        installed: true,
        administration: true,
        bot: { login: 'made-up-2[bot]', app_id: 22, user_id: 333 },
        app_changed: { notice, since: '2026-11-22T00:00:00Z', previous: {} },
      },
    };
    show(<GithubConnection />, '/library/tools?tool=github');
    expect(await screen.findByText('made-up-2[bot] · App ID 22 · user id 333')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe(`⚠ ${notice}`);
    fireEvent.click(screen.getAllByRole('button', { name: 'GitHub app' }).at(-1)!);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Disconnect' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/Connecting again makes a new app: CI or rules that name this bot will need the new one\./)).toBeTruthy();
  });
});

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
});
