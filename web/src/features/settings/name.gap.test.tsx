import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { General } from './Basics';
import { accessQuery, type AccessFull } from './queries';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Settings › General › Name (s1072): the owner renames the company in place. The server's only route is the whole
   setup record (PUT /api/v2/setup), so the record is read and sent back with only the name changed. */

const SETUP = {
  names: { company_name: '', app_name: 'Tico', assistant_name: 'Helper', owner_name: 'Ana Setup', team_domain: 'example.test', website: 'https://example.test' },
  answers: { building: 'Garden kits', what_we_do: 'We sell kits', customers: 'consumers', regions: ['Oregon'], teams_off: ['Growth'], first_job: 'grow' },
  selected: { helper: { template: 'assistant', display_name: 'Helper', instructions: 'Be brief', reports_to: '' } },
  completed: '2026-01-01T00:00:00Z',
  bots: [],
  needed: false,
};

type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
let putStatus = 200;
function serve() {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const u = String(url);
    if (method !== 'GET') sent.push({ url: u, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    let status = 200;
    let body: unknown = {};
    if (u === '/api/v2/setup' && method === 'GET') body = SETUP;
    else if (u === '/api/v2/setup' && method === 'PUT') {
      status = putStatus;
      body = putStatus === 200 ? SETUP : { error: { code: 'forbidden', detail: 'Only the owner may set this company up' } };
    } else if (u.startsWith('/api/v2/repositories')) body = { github_connected: false, github_installed: false, new_bot_default: 'own', repositories: [] };
    else if (u === '/api/v2/system/storage') body = { active: null, server: { mode: 'disk' }, tico: null, copy: { done: 0, total: 0 }, earlier: [] };
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show() {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <General />
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/settings/general'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
}

const ACCESS = {
  owner: { email: 'ana@example.test', person: 'p1' },
  people: [{ id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true }],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: {},
  you: 'owner',
  invites: {},
  invite_email: true,
  home_domain: 'example.test',
  join: 'admin',
  member_bot_limit: 3,
} as AccessFull;

const companyName = () => (queryClient.getQueryData(['config']) as { company_name: string }).company_name;

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  putStatus = 200;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'ana@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
  queryClient.setQueryData(accessQuery.queryKey, ACCESS);
  serve();
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings › General › Name', () => {
  it('the owner renames the company in place; only the name changes in the setup sent back @s1072', async () => {
    show();
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

  it('leaving the field saves; Escape and an unchanged name send nothing @s1072', async () => {
    show();
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

  it('a refusal takes the name back and says so @s1072', async () => {
    putStatus = 403;
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Company name, Sample Co' }));
    const box = screen.getByRole('textbox', { name: 'Company name' });
    fireEvent.change(box, { target: { value: 'Nope Co' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(await screen.findByText(/Couldn't save/)).toBeTruthy();
    expect(companyName()).toBe('Sample Co');
  });

  it('an admin reads the name; Tico may rename (ordinary work) @s1072', async () => {
    queryClient.setQueryData(accessQuery.queryKey, { ...ACCESS, you: 'admin' });
    show();
    expect(await screen.findByText('Sample Co')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Company name/ })).toBeNull();
    expect(await run('settings.renameCompany', { name: 'Tico Co' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.map((x) => x.method + ' ' + x.url)).toEqual(['PUT /api/v2/setup']);
  });
});
