import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { configQuery, meQuery, type ReleaseOffer } from '@/data';
import { UpdateNotice } from '@/features/notices/exports';
import { Version } from './Version';
import { accessQuery } from './queries';

const offer: ReleaseOffer = { current: '1.2.3', latest: '1.2.4', available: true, held: '', managed: false };
const UPDATE = '/api/v2/system/update';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

let client: QueryClient;

function config(update: ReleaseOffer) {
  return {
    company_name: 'Acme',
    app_name: 'Tico',
    assistant_name: 'Tico',
    assistant_bot: 'tico',
    public_url: '',
    owner_email: 'owner@acme.example',
    version: update.current,
    update,
  };
}

function role(you: 'owner' | 'admin' | 'member') {
  client.setQueryData(meQuery.queryKey, { id: 'human:1', email: 'person@example.test', role: you === 'owner' ? 'owner' : 'person', slug: null });
  client.setQueryData(accessQuery.queryKey, {
    owner: { email: 'owner@acme.example', person: '1' },
    people: [],
    allowed: [],
    allowed_domains: [],
    revision: 1,
    rules: {},
    you,
    invites: {},
    invite_email: false,
  });
}

function SettingsRow({ owner }: { owner: boolean }) {
  const { data } = useQuery(configQuery);
  return data?.update ? <Version update={data.update} owner={owner} /> : null;
}

function show({ update = offer, you = 'owner' }: { update?: ReleaseOffer; you?: 'owner' | 'admin' | 'member' } = {}) {
  client.setQueryData(configQuery.queryKey, config(update));
  role(you);
  return render(
    <QueryClientProvider client={client}>
      <SettingsRow owner={you === 'owner'} />
      <UpdateNotice />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('Company updates', () => {
  it('shows an admin the available release and persists dismissal for that release', async () => {
    const request = vi.fn();
    vi.stubGlobal('fetch', request);
    const page = show({ you: 'admin' });
    expect(screen.getByText('Tico v1.2.3')).toBeTruthy();
    expect(screen.getByText('v1.2.4 is out')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Update now|Check for updates/ })).toBeNull();
    expect(screen.getByRole('link', { name: 'View update' }).getAttribute('href')).toBe('/settings/general?open=tico-version');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    page.unmount();
    show({ you: 'admin' });
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    act(() => client.setQueryData(configQuery.queryKey, config({ ...offer, latest: '1.2.5' })));
    expect(await screen.findByText('Tico v1.2.5 is out')).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });

  it('keeps the notice away from members', () => {
    show({ you: 'member' });
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
  });

  it('gives the backend’s reason for a held release and lets the owner check again', async () => {
    const held = { ...offer, available: false, held: 'This company’s tasks would not carry over' };
    const request = vi.fn(async (path: string) => json(path === UPDATE ? { state: 'idle', configured: true } : config(offer)));
    vi.stubGlobal('fetch', request);
    show({ update: held });
    expect(screen.getByText(held.held)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByRole('button', { name: 'Update now' })).toBeTruthy();
    expect(request).toHaveBeenCalledWith(`${UPDATE}/check`, expect.objectContaining({ method: 'POST' }));
  });

  it('shows a managed release without offering updater actions', () => {
    const request = vi.fn();
    vi.stubGlobal('fetch', request);
    show({ update: { ...offer, managed: true } });
    expect(screen.getByText('v1.2.4 is out')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Update now|Check for updates/ })).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it.each([
    { state: 'healthy', running: offer.latest, outcome: 'Updated to v1.2.4.' },
    { state: 'rolled_back', running: offer.current, outcome: 'Tico went back to the previous version.' },
    { state: 'failed', running: offer.current, outcome: 'The update failed.' },
  ])('holds the button across a restart and reports $state', async ({ state, running, outcome }) => {
    vi.useFakeTimers();
    const tick = (ms = 10) =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    let posts = 0;
    let reads = 0;
    let key: string | null = null;
    let version: string | undefined;
    vi.stubGlobal('fetch', async (path: string, init: RequestInit) => {
      if (path === '/api/v2/config') return json(config({ ...offer, current: running, available: running !== offer.latest }));
      if (path !== UPDATE) throw new Error(`Unexpected request to ${path}`);
      if (init.method === 'POST') {
        posts++;
        key = new Headers(init.headers).get('Idempotency-Key');
        version = JSON.parse(String(init.body)).version;
        return json({ state: 'pulling', to: 'v1.2.4', configured: true });
      }
      const status = !posts ? 'idle' : ++reads === 1 ? 'restarting' : state;
      if (reads === 2) throw new TypeError('Failed to fetch');
      return json({ state: status, to: 'v1.2.4', configured: true, running: status === state ? running : offer.current });
    });

    show();
    await tick();
    const button = screen.getByRole('button', { name: 'Update now' });
    fireEvent.click(button);
    await tick();
    expect(key).toBeTruthy();
    expect(version).toBe(offer.latest);
    expect(screen.getByText('Downloading the update…')).toBeTruthy();
    expect(button.getAttribute('aria-busy')).toBe('true');
    await tick(3_050);
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    await tick(3_050);
    expect(reads).toBe(2);
    expect(button.getAttribute('aria-busy')).toBe('true');
    fireEvent.click(button);
    await tick(3_050);
    expect(posts).toBe(1);
    expect(screen.getByText(outcome)).toBeTruthy();
    expect(screen.getByText(`Tico v${running}`)).toBeTruthy();
    if (state === 'healthy') {
      expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
      expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    } else {
      expect(button.hasAttribute('disabled')).toBe(false);
    }
    await tick(6_000);
    expect(reads).toBe(3);
  });
});
