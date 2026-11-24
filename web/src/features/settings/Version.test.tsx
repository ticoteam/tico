import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { adaptMe } from '@/api/adapt';
import { configQuery, meQuery, useInitialReleaseRefresh, type ReleaseOffer } from '@/data';
import { UpdateNotice } from '@/features/notices/exports';
import { fixHref } from './fixes';
import { Version } from './Version';

const offer: ReleaseOffer = { current: '1.2.3', latest: '1.2.4', available: true, held: '', managed: false };
const UPDATE = '/api/v2/system/update';
const CONFIG = '/api/v2/config';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const tick = (ms = 10) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

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
  client.setQueryData(
    meQuery.queryKey,
    adaptMe({ actor: 'human:1', email: 'person@example.test', role: you === 'owner' ? 'owner' : 'human', kind: you }),
  );
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
  localStorage.clear();
});

describe('Company updates', () => {
  it('refreshes an empty release offer at 30 seconds and two minutes', async () => {
    vi.useFakeTimers();
    let update = { ...offer, latest: '', available: false };
    const request = vi.fn(async () => json(config(update)));
    vi.stubGlobal('fetch', request);
    function Shell() {
      useInitialReleaseRefresh(true);
      return <UpdateNotice />;
    }
    role('owner');
    render(
      <QueryClientProvider client={client}>
        <Shell />
      </QueryClientProvider>,
    );
    await tick();
    await tick(29_900);
    expect(request).toHaveBeenCalledTimes(1);
    await tick(100);
    expect(request).toHaveBeenCalledTimes(2);
    update = offer;
    await tick(89_900);
    expect(request).toHaveBeenCalledTimes(2);
    await tick(100);
    expect(request).toHaveBeenCalledTimes(3);
    expect(screen.getByText('Tico v1.2.4 is out')).toBeTruthy();
    await tick(120_000);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it('shows an admin the available release and persists dismissal for that release', async () => {
    let update = offer;
    const request = vi.fn(async () => json(config(update)));
    vi.stubGlobal('fetch', request);
    const page = show({ you: 'admin' });
    expect(screen.getByText('Tico v1.2.3')).toBeTruthy();
    expect(screen.getByText('v1.2.4 is out')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Update now|Check now/ })).toBeNull();
    expect(screen.getByText('The owner can update Tico.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View update' }).getAttribute('href')).toBe(fixHref({ label: 'Update', click: '#new-version' }));
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    page.unmount();
    update = { ...offer, latest: '1.2.5' };
    show({ you: 'admin' });
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    expect(await screen.findByText('Tico v1.2.5 is out')).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenLastCalledWith(CONFIG, expect.objectContaining({ method: 'GET' }));
  });

  it('keeps the notice away from members', () => {
    vi.stubGlobal('fetch', async () => json(config(offer)));
    show({ you: 'member' });
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
  });

  it('gives the backend’s reason for a held release and lets the owner check again', async () => {
    const held = { ...offer, available: false, held: 'This company’s tasks would not carry over' };
    let checked = false;
    const request = vi.fn(async (path: string) => {
      if (path === UPDATE) return json({ state: 'idle', configured: true });
      if (path === CONFIG) return json(config(checked ? offer : held));
      checked = true;
      return json({ available: true, checking: false });
    });
    vi.stubGlobal('fetch', request);
    show({ update: held });
    expect(screen.getByText(held.held)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    expect(await screen.findByRole('button', { name: 'Update now' })).toBeTruthy();
    expect(request).toHaveBeenCalledWith(`${UPDATE}/check`, expect.objectContaining({ method: 'POST' }));
  });

  it('shows a managed release without offering updater actions', () => {
    const managed = { ...offer, managed: true };
    const request = vi.fn(async () => json(config(managed)));
    vi.stubGlobal('fetch', request);
    show({ update: managed });
    expect(screen.getByText('v1.2.4 is out')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Update now|Check now/ })).toBeNull();
    expect(screen.queryByRole('link', { name: 'View update' })).toBeNull();
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(CONFIG, expect.objectContaining({ method: 'GET' }));
  });

  it('holds Update now until a fresh status read confirms the updater', async () => {
    let resolve!: (response: Response) => void;
    const promise = new Promise<Response>((reply) => {
      resolve = reply;
    });
    const request = vi.fn(async (path: string) => (path === CONFIG ? json(config(offer)) : promise));
    vi.stubGlobal('fetch', request);
    client.setQueryData(['settings', 'update'], { configured: true, state: 'idle' });
    show();
    const button = screen.getByRole('button', { name: 'Update now' });
    expect(button.hasAttribute('disabled')).toBe(true);
    fireEvent.click(button);
    resolve(json({ configured: true, state: 'idle' }));
    await waitFor(() => expect(button.hasAttribute('disabled')).toBe(false));
    expect(request.mock.calls.filter(([path]) => path === UPDATE)).toHaveLength(1);
  });

  it('shows the manual command when there is no updater', async () => {
    const command = 'docker compose pull && docker compose up -d';
    vi.stubGlobal('fetch', async (path: string) => json(path === CONFIG ? config(offer) : { configured: false, state: 'unavailable', command }));
    show();
    expect(await screen.findByText(command)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
  });

  it('shows a scheduled release alongside the owner’s update action', () => {
    const update = { ...offer, scheduled: `v${offer.latest}` };
    vi.stubGlobal('fetch', async (path: string) => json(path === CONFIG ? config(update) : { configured: true, state: 'idle' }));
    show({ update });
    expect(screen.getByText('Tico will update to v1.2.4 when idle.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Update now' })).toBeTruthy();
  });

  it.each([1, 3])('clears a pending check after %i scheduled config reads', async (followUps) => {
    vi.useFakeTimers();
    const upToDate = { ...offer, latest: offer.current, available: false };
    let checked = false;
    let reads = 0;
    vi.stubGlobal('fetch', async (path: string) => {
      if (path === CONFIG) {
        if (!checked) return json(config(upToDate));
        reads++;
        return json(config(followUps === 1 ? { ...offer, checking: false } : { ...upToDate, checking: true }));
      }
      if (path === UPDATE) return json({ state: 'idle', configured: true });
      checked = true;
      return json({ checking: true, available: false });
    });
    show({ update: upToDate });
    await tick();
    const button = screen.getByRole('button', { name: 'Check now' });
    fireEvent.click(button);
    await tick();
    expect(screen.getByText('Still checking…')).toBeTruthy();
    for (let i = 0; i < followUps; i++) {
      expect(screen.getByText('Still checking…')).toBeTruthy();
      expect(reads).toBe(i);
      await tick(5_050);
    }
    expect(screen.queryByText('Still checking…')).toBeNull();
    if (followUps === 1) expect(screen.getByText('v1.2.4 is out')).toBeTruthy();
    expect(button.hasAttribute('disabled')).toBe(false);
    await tick(20_000);
    expect(reads).toBe(followUps);
  });

  it.each([
    { state: 'healthy', running: offer.latest },
    { state: 'failed', running: offer.current },
  ])('keeps a stale $state outcome silent when a newer release is out', async ({ state, running }) => {
    const update = { ...offer, current: running, latest: '1.2.5' };
    vi.stubGlobal('fetch', async (path: string) =>
      json(path === CONFIG ? config(update) : { state, configured: true, to: `v${offer.latest}`, running, message: 'Earlier attempt.' }),
    );
    show({ update });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Update now' }).hasAttribute('disabled')).toBe(false));
    expect(screen.queryByText(/Updated to|The update failed|Earlier attempt/)).toBeNull();
  });

  it('recovers a lost start without repeating the POST', async () => {
    vi.useFakeTimers();
    let posts = 0;
    let reads = 0;
    vi.stubGlobal('fetch', async (path: string, init: RequestInit) => {
      if (path === CONFIG) return json(config(offer));
      if (init.method === 'POST') {
        posts++;
        throw new TypeError('Failed to fetch');
      }
      reads++;
      if (reads === 2) return json({}, 503);
      return json({ configured: true, state: reads < 4 ? 'idle' : 'pulling', to: `v${offer.latest}` });
    });
    show();
    await tick();
    const button = screen.getByRole('button', { name: 'Update now' });
    fireEvent.click(button);
    await tick();
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    await tick(6_050);
    expect(reads).toBe(3);
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    expect(button.getAttribute('aria-busy')).toBe('true');
    fireEvent.click(button);
    await tick(3_050);
    expect(screen.getByText('Downloading the update…')).toBeTruthy();
    expect(posts).toBe(1);
  });

  it('recovers when the first status read fails and config catches up', async () => {
    vi.useFakeTimers();
    const request = vi.fn(async (path: string) => (path === CONFIG ? json(config(offer)) : json({}, 503)));
    vi.stubGlobal('fetch', request);
    show();
    await tick();
    await tick(3_050);
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    expect(request.mock.calls.filter(([path]) => path === UPDATE)).toHaveLength(2);
    const caughtUp = { ...offer, current: offer.latest, available: false };
    act(() => client.setQueryData(configQuery.queryKey, config(caughtUp)));
    await tick();
    await tick(3_050);
    expect(screen.queryByText('Restarting Tico…')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Update now' })).toBeNull();
    expect(request.mock.calls.filter(([path]) => path === UPDATE)).toHaveLength(2);
  });

  it('ends mount recovery on the first idle status', async () => {
    vi.useFakeTimers();
    let reads = 0;
    vi.stubGlobal('fetch', async (path: string) => {
      if (path === CONFIG) return json(config(offer));
      return ++reads === 1 ? json({}, 503) : json({ configured: true, state: 'idle' });
    });
    show();
    await tick();
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    await tick(3_050);
    expect(screen.queryByText('Restarting Tico…')).toBeNull();
    expect(screen.getByRole('button', { name: 'Update now' }).hasAttribute('disabled')).toBe(false);
    await tick(6_000);
    expect(reads).toBe(2);
  });

  it('ends lost-start recovery on three consecutive idle statuses', async () => {
    vi.useFakeTimers();
    let reads = 0;
    const request = vi.fn(async (path: string, init: RequestInit) => {
      if (path === CONFIG) return json(config(offer));
      if (init.method === 'POST') throw new TypeError('Failed to fetch');
      reads++;
      return json({ configured: true, state: 'idle' });
    });
    vi.stubGlobal('fetch', request);
    show();
    await tick();
    const button = screen.getByRole('button', { name: 'Update now' });
    fireEvent.click(button);
    await tick();
    await tick(6_050);
    expect(reads).toBe(3);
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    await tick(3_050);
    expect(reads).toBe(4);
    expect(screen.getByText("The update didn't start. Try again.")).toBeTruthy();
    expect(button.hasAttribute('disabled')).toBe(false);
    await tick(6_000);
    expect(reads).toBe(4);
    expect(request.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
  });

  it('reports success when an aborted start recovers directly to healthy', async () => {
    vi.useFakeTimers();
    let started = false;
    vi.stubGlobal('fetch', async (path: string, init: RequestInit) => {
      if (path === CONFIG) return json(config(offer));
      if (init.method === 'POST') {
        started = true;
        throw new DOMException('Aborted', 'AbortError');
      }
      return json(started ? { configured: true, state: 'healthy', to: 'v1.2.4', running: '1.2.4' } : { configured: true, state: 'idle' });
    });
    show();
    await tick();
    fireEvent.click(screen.getByRole('button', { name: 'Update now' }));
    await tick();
    expect(screen.getByText('Restarting Tico…')).toBeTruthy();
    await tick(3_050);
    expect(screen.getByText('Updated to v1.2.4.')).toBeTruthy();
    expect(screen.queryByText('Restarting Tico…')).toBeNull();
  });

  it('stops recovery reads after five minutes without a reachable status', async () => {
    vi.useFakeTimers();
    let healthy = false;
    const request = vi.fn(async (path: string) => {
      if (path === CONFIG) return json(config(offer));
      return healthy ? json({ configured: true, state: 'healthy', to: 'v1.2.4', running: '1.2.4' }) : json({}, 503);
    });
    vi.stubGlobal('fetch', request);
    show();
    await tick();
    await tick(300_000);
    expect(screen.getByText('Could not confirm the update.')).toBeTruthy();
    const count = request.mock.calls.length;
    await tick(6_000);
    expect(request.mock.calls).toHaveLength(count);
    expect(request.mock.calls.filter(([path]) => path === CONFIG)).toHaveLength(1);
    healthy = true;
    await act(() => client.refetchQueries({ queryKey: ['settings', 'update'] }));
    await tick();
    expect(screen.queryByText('Could not confirm the update.')).toBeNull();
    expect(screen.getByText('Updated to v1.2.4.')).toBeTruthy();
  });

  it('shows a 502 start refusal immediately and reads status once', async () => {
    vi.useFakeTimers();
    const error = 'The updater did not answer. Try again, or update by hand.';
    const request = vi.fn(async (path: string, init: RequestInit) => {
      if (path === CONFIG) return json(config(offer));
      if (init.method === 'POST') return json({ error: { code: 'updater_unreachable', detail: error } }, 502);
      return json({ configured: true, state: 'idle' });
    });
    vi.stubGlobal('fetch', request);
    show();
    await tick();
    const button = screen.getByRole('button', { name: 'Update now' });
    fireEvent.click(button);
    await tick();
    expect(screen.getByText(error)).toBeTruthy();
    expect(screen.queryByText('Restarting Tico…')).toBeNull();
    expect(button.hasAttribute('disabled')).toBe(false);
    await tick(6_000);
    expect(request.mock.calls.filter(([path, init]) => path === UPDATE && init.method === 'GET')).toHaveLength(2);
  });

  it.each([
    { state: 'healthy', running: offer.latest, outcome: 'Updated to v1.2.4.' },
    { state: 'rolled_back', running: offer.current, outcome: 'Tico went back to the previous version.' },
    { state: 'failed', running: offer.current, outcome: 'The update failed.' },
  ])('holds the button across a restart and reports $state', async ({ state, running, outcome }) => {
    vi.useFakeTimers();
    let posts = 0;
    let reads = 0;
    let configReads = 0;
    let key: string | null = null;
    let version: string | undefined;
    vi.stubGlobal('fetch', async (path: string, init: RequestInit) => {
      if (path === CONFIG) {
        configReads++;
        return json(config(reads === 3 ? { ...offer, current: running, available: running !== offer.latest } : offer));
      }
      if (path !== UPDATE) throw new Error(`Unexpected request to ${path}`);
      if (init.method === 'POST') {
        posts++;
        key = new Headers(init.headers).get('Idempotency-Key');
        version = JSON.parse(String(init.body)).version;
        return json({ state: 'pulling', to: 'v1.2.4', configured: true });
      }
      const status = !posts ? 'idle' : ++reads === 1 ? 'restarting' : state;
      if (reads === 2) throw new TypeError('Failed to fetch');
      return json({
        state: status,
        to: 'v1.2.4',
        configured: true,
        running: status === state ? running : offer.current,
        message: 'The download timed out.',
      });
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
    expect(screen.getByText(state === 'healthy' ? outcome : `${outcome} The download timed out.`)).toBeTruthy();
    expect(screen.getByText(`Tico v${running}`)).toBeTruthy();
    expect(configReads).toBe(2);
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
