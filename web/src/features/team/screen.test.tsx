// The app's boot order: every action registered before a page imports its feature's actions (a cycle otherwise).
import '@/actions';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { BrowserDialog } from './BrowserView';
import { agentsQuery, computersQuery, modelsQuery, type AgentRow, type Computer } from './queries';
import { SignIns } from './SignIns';
import { TeamWho } from './TeamWho';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));

// The viewer and its socket are stand-ins: nothing connects anywhere.
const fake = vi.hoisted(() => ({ viewers: [] as FakeViewer[], sockets: [] as FakeSocket[] }));
interface FakeViewer extends EventTarget {
  channel: FakeSocket;
  viewOnly: boolean;
  disconnect: () => void;
  closed: boolean;
}
interface FakeSocket extends EventTarget {
  url: string;
}
vi.mock('@novnc/novnc', () => ({
  default: class extends EventTarget {
    viewOnly = false;
    scaleViewport = false;
    resizeSession = true;
    background = '';
    focusOnClick = true;
    closed = false;
    constructor(
      _target: Element,
      public channel: FakeSocket,
    ) {
      super();
      fake.viewers.push(this as unknown as FakeViewer);
    }
    disconnect() {
      this.closed = true;
    }
    focus() {}
  },
}));
class Socket extends EventTarget {
  binaryType = 'blob';
  constructor(public url: string) {
    super();
    fake.sockets.push(this);
  }
  close() {}
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
const TICKET = 'tk-made-up-1';
function serve(reply: (url: string, method: string, body: unknown) => Response | undefined) {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (method !== 'GET') sent.push({ url: u, method, body });
    return reply(u, method, body) ?? json({});
  });
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const screenServer = (url: string, method: string, body: unknown) => {
  if (method === 'POST' && url.endsWith('/api/v2/computers/r1/screen/sessions'))
    return json({ session_id: 's1', ticket: TICKET, control: (body as { control: boolean }).control, ws_path: `/api/v2/runners/r1/screen/ws?ticket=${TICKET}`, width: 1440, height: 900 });
  if (url.includes('/api/employees')) return json(roster);
  return undefined;
};

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

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const roster: AgentRow[] = [{ id: '0190-ana', slug: 'ana', display_name: 'Ana', revision: 3, machine: { runner_id: 'r1', label: 'Studio', generation: 2 } } as AgentRow];
const studio = { id: 'r1', label: 'Studio', online: true, bots: ['0190-ana'], screen: 'control', screen_available: true, may_manage: true } as Computer;
const viewer = (i: number) => fake.viewers[i]!;

beforeEach(() => {
  fake.viewers.length = 0;
  fake.sockets.length = 0;
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: '', role: 'person', slug: null });
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [ana], byId: { [ana.id]: ana, ana } });
  queryClient.setQueryData(agentsQuery.queryKey, roster);
  queryClient.setQueryData(computersQuery.queryKey, [studio]);
  queryClient.setQueryData(modelsQuery.queryKey, { models: [], harnesses: [] });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("A computer's screen", () => {
  it('watches first, takes control in a new session, and keeps the ticket out of the cache and the address', async () => {
    serve(screenServer);
    const router = show(<TeamWho who="ana" tab="screen" />, '/team/ana?tab=screen');
    await waitFor(() => expect(fake.viewers).toHaveLength(1));
    expect(sent[0]?.body).toEqual({ control: false });
    expect(viewer(0).viewOnly).toBe(true);
    expect(viewer(0).channel.url).toMatch(/^ws:\/\/[^/]+\/api\/v2\/runners\/r1\/screen\/ws\?ticket=/);
    act(() => void viewer(0).dispatchEvent(new Event('connect')));
    expect(await screen.findByText('View only')).toBeTruthy();
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(TICKET);
    expect(router.state.location.href).not.toContain(TICKET);

    fireEvent.click(screen.getByRole('button', { name: 'Control' }));
    await waitFor(() => expect(fake.viewers).toHaveLength(2));
    expect(viewer(0).closed).toBe(true);
    expect(sent[1]?.body).toEqual({ control: true });
    expect(viewer(1).viewOnly).toBe(false);
    act(() => void viewer(1).dispatchEvent(new Event('connect')));
    expect(await screen.findByText('You have control')).toBeTruthy();

    cleanup();
    expect(viewer(1).closed).toBe(true);
  });

  it('says why when the computer ends the session, and reconnects with a new one', async () => {
    serve(screenServer);
    show(<TeamWho who="ana" tab="screen" />, '/team/ana?tab=screen');
    await waitFor(() => expect(fake.viewers).toHaveLength(1));
    act(() => {
      fake.sockets[0]!.dispatchEvent(Object.assign(new Event('close'), { code: 4404 }));
      viewer(0).dispatchEvent(new Event('disconnect'));
    });
    expect(await screen.findByText('The computer did not answer')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    await waitFor(() => expect(fake.viewers).toHaveLength(2));
    expect(sent).toHaveLength(2);
  });

  it('says plainly when it is refused, and connects nothing', async () => {
    serve((url, method, body) =>
      url.includes('/screen/sessions') ? json({ error: { code: 'forbidden', detail: 'No' } }, 403) : screenServer(url, method, body),
    );
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, screen: 'view' }]);
    show(<TeamWho who="ana" tab="screen" />, '/team/ana?tab=screen');
    expect(await screen.findByText('Only its owner or an admin can open it')).toBeTruthy();
    expect(fake.viewers).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Control' })).toBeNull();
  });

  it('offers Turn on to its owner when the screen is off', async () => {
    serve(screenServer);
    queryClient.setQueryData(computersQuery.queryKey, [{ ...studio, screen: 'off' }]);
    show(<TeamWho who="ana" tab="screen" />, '/team/ana?tab=screen');
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on' }));
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/computers/r1/screen'))?.body).toEqual({ mode: 'control' }));
  });
});

describe('Open browser', () => {
  const base = '/api/v2/website-tools/w1/accounts/a1/browser';
  const frame = () =>
    new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'X-Page-Url': 'https://example.com/login?next=1', 'X-Page-Width': '1280', 'X-Page-Height': '800' },
    });
  const browserServer = (url: string, method: string, body: unknown) => {
    if (url === base && method === 'POST') return json({ ok: true, where: 'central' });
    if (url === `${base}/frame`) return frame();
    if (url === `${base}/control`) return json({ ok: true, control: (body as { take: boolean }).take });
    if (url === `${base}/input`) return json({ ok: true });
    return undefined;
  };
  beforeEach(() => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:made-up'), revokeObjectURL: vi.fn() }));
  });

  it('opens the browser, takes control, types, and gives it back on Done', async () => {
    serve(browserServer);
    const onClose = vi.fn();
    show(<BrowserDialog tool="w1" account="a1" title="Example" onClose={onClose} />, '/library/tools');
    expect(await screen.findByRole('img', { name: 'The page at example.com/login' })).toBeTruthy();
    expect(sent[0]).toMatchObject({ url: base, method: 'POST' });

    fireEvent.click(screen.getByRole('button', { name: 'Take control' }));
    expect(await screen.findByText('You have control')).toBeTruthy();
    expect(sent.find((s) => s.url.endsWith('/control'))?.body).toEqual({ take: true });

    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'hello' } });
    fireEvent.submit(screen.getByLabelText('Type').closest('form')!);
    await waitFor(() => expect(sent.find((s) => s.url.endsWith('/input'))?.body).toEqual({ kind: 'type', text: 'hello' }));

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent.filter((s) => s.url.endsWith('/control')).map((s) => s.body)).toEqual([{ take: true }, { take: false }]);
  });

  it('gives control back when it closes', async () => {
    serve(browserServer);
    show(<BrowserDialog tool="w1" account="a1" title="Example" onClose={() => {}} />, '/library/tools');
    fireEvent.click(await screen.findByRole('button', { name: 'Take control' }));
    expect(await screen.findByText('You have control')).toBeTruthy();
    cleanup();
    await waitFor(() => expect(sent.filter((s) => s.url.endsWith('/control')).map((s) => s.body)).toEqual([{ take: true }, { take: false }]));
  });
});

describe('A sign-in on a computer', () => {
  const CODE = 'WDJB-MJHT';
  const PAGE = `https://auth.example/device?user_code=${CODE}`;

  it('shows the provider’s code and page in its dialog only, never in the query cache', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/api/v2/computers/r1/logins')) return json({ id: 'l1', runner_id: 'r1', state: 'starting' });
      if (url.endsWith('/api/v2/computers/r1/logins/l1')) return json({ id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE, lines: [`Enter ${CODE}`] });
      return undefined;
    });
    queryClient.setQueryData(modelsQuery.queryKey, { models: [], harnesses: [{ id: 'codex', name: 'Codex', capabilities: { browser_sign_in: true } }] });
    const c = { ...studio, readiness: { runtimes: { codex: { installed: true, authenticated: 'missing' } } } } as Computer;
    show(<SignIns c={c} />, '/team/computers/r1');
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in to Codex' }));
    const dialog = await screen.findByRole('dialog', { name: 'Sign in to Codex' });
    expect(await within(dialog).findByText(CODE)).toBeTruthy();
    expect(within(dialog).getByRole('link').getAttribute('href')).toBe(PAGE);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });
});
