import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { TicoSection } from './Desktop';
import { GatewayLines } from './Gateway';
import { gatewayQuery } from './queries';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

function show(node: ReactNode) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        {node}
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

const bridge = (restart: () => Promise<void>) => ({
  version: '9.9.9',
  platform: 'mac',
  app: {
    updateState: async () => ({ state: 'ready', version: '9.9.10' }),
    restartToUpdate: vi.fn(restart),
    checkForUpdates: async () => {},
    showLog: vi.fn(async () => {}),
    logTail: async () => ({ version: '9.9.9', lines: [] }),
    openAtLogin: vi.fn(async (on?: boolean) => on ?? false),
  },
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => queryClient.clear());
afterEach(() => {
  delete (window as { tico?: unknown }).tico;
  vi.unstubAllGlobals();
});

describe('Settings › the desktop app', () => {
  it("a browser shows the company's version only, and the app's actions are refused", async () => {
    show(<TicoSection version="1.2.3" />);
    expect(await screen.findByText('1.2.3')).toBeTruthy();
    expect(screen.queryByText('Updates')).toBeNull();
    expect(await run('settings.restartToUpdate', {}, { via: 'click' })).toMatchObject({ ok: false });
  });

  it('shows the version and a ready update; a restart during a call is refused with a plain line', async () => {
    const t = bridge(async () => {
      throw new Error("Error invoking remote method 'app:restart': Error: A call is live. Restart after it ends.");
    });
    (window as { tico?: unknown }).tico = t;
    show(<TicoSection version="9.9.9" />);
    expect(await screen.findByText('9.9.9')).toBeTruthy();
    expect(await screen.findByText('Ready')).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Restart to update' })));
    expect(t.app.restartToUpdate).toHaveBeenCalledOnce();
    expect(await screen.findByText('Restart after the call ends')).toBeTruthy();
  });

  it('open at login reads the computer and sets it; Tico cannot press any of these', async () => {
    const t = bridge(async () => {});
    (window as { tico?: unknown }).tico = t;
    show(<TicoSection version="9.9.9" />);
    const sw = await screen.findByRole('switch', { name: 'Open at login' });
    await act(async () => fireEvent.click(sw));
    expect(t.app.openAtLogin).toHaveBeenLastCalledWith(true);
    expect(await run('settings.restartToUpdate', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(t.app.restartToUpdate).not.toHaveBeenCalled();
  });
});

describe('Settings › Tico Gateway @s710', () => {
  it('a new address while a key is saved asks first, and Keep sends it again with the answer', async () => {
    const sent: unknown[] = [];
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? '{}')) as { credentials?: string };
      sent.push(body);
      const held = {
        error: { code: 'destination_change', detail: 'Held', name: 'Tico Gateway', old: { address: 'https://old.example.test' }, new: { address: 'https://new.example.test' }, credentials: ['Gateway key'] },
      };
      const ok = { connected: true, url: 'https://new.example.test', default_url: 'https://default.example.test', editable: true, preview: 'abc…xyz', revision: 3 };
      return new Response(JSON.stringify(body.credentials ? ok : held), { status: body.credentials ? 200 : 409, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(['me'], { id: 'human:p1', email: 'owner@example.test', role: 'owner', slug: null });
    queryClient.setQueryData(gatewayQuery.queryKey, { connected: true, url: '', default_url: 'https://default.example.test', editable: true, preview: 'abc…xyz', revision: 2 });
    show(<GatewayLines />);
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
    fireEvent.change(screen.getByLabelText('Address'), { target: { value: 'https://new.example.test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Change where Tico Gateway sends?')).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Keep' })));
    expect(sent).toEqual([
      { url: 'https://new.example.test', expected_revision: 2 },
      { url: 'https://new.example.test', credentials: 'keep', expected_revision: 2 },
    ]);
  });
});
