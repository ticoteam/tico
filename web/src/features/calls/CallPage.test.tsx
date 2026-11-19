import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Call } from '@/api/schema';
import { ActionsRoot } from '@/actions';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { dot } from '@/tico/dot';
import { CallPage } from './CallPage';
import { callQuery } from './queries';
import { resetCall } from './session';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => <span className="lvl" />,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));

// LiveKit, faked: a room that records what it is asked and lets a test raise its events. Nothing connects.
type Handler = (...a: unknown[]) => void;
const lk = vi.hoisted(() => ({
  rooms: [] as {
    remoteParticipants: Map<string, unknown>;
    emit: (e: string, ...a: unknown[]) => void;
    connect: { mock: { calls: unknown[][] } };
    localParticipant: { setMicrophoneEnabled: { mock: { calls: unknown[][] } } };
  }[],
  refuseConnect: false,
}));
vi.mock('livekit-client', () => {
  class Room {
    handlers = new Map<string, Handler>();
    localParticipant = { identity: 'human:me', audioLevel: 0, publishTrack: vi.fn(async () => undefined), setMicrophoneEnabled: vi.fn(async () => undefined) };
    remoteParticipants = new Map();
    canPlaybackAudio = true;
    startAudio = vi.fn(async () => undefined);
    disconnect = vi.fn(async () => undefined);
    switchActiveDevice = vi.fn(async () => true);
    connect = vi.fn(async () => {
      if (lk.refuseConnect) throw new Error('could not connect');
    });
    static getLocalDevices = vi.fn(async () => []);
    constructor() {
      lk.rooms.push(this as never);
    }
    on(e: string, f: Handler) {
      this.handlers.set(e, f);
      return this;
    }
    emit(e: string, ...a: unknown[]) {
      this.handlers.get(e)?.(...a);
    }
    getParticipantByIdentity() {
      return undefined;
    }
  }
  return {
    Room,
    RoomEvent: new Proxy({}, { get: (_t, k) => k }),
    Track: { Source: { Microphone: 'microphone' }, Kind: { Audio: 'audio' } },
  };
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const ROOM = '0f8fad5b-d9cb-469f-a165-70867728950e';
const call = (p: Partial<Call> = {}): Call =>
  ({
    id: ROOM,
    bots: [],
    connector: 'tico',
    effort: null,
    end_reason: null,
    ended_at: null,
    holds: [],
    meeting_id: 'm1',
    model: null,
    participants: [{ actor: 'human:ana', in_call: true, joined_at: '2026-11-18T10:00:00Z', left_at: null }],
    scheduled_end: null,
    scheduled_start: null,
    speak_replies: false,
    stage: null,
    stage_n: 0,
    started_at: new Date().toISOString(),
    state: 'live',
    thread_id: 'th1',
    title: 'Pricing review',
    voice: null,
    ...p,
  }) as Call;

const posted: string[] = [];
const getUserMedia = vi.fn();
const fakeStream = () => ({ getAudioTracks: () => [{ kind: 'audio' }], getTracks: () => [{ stop: vi.fn() }] });

beforeEach(() => {
  resetCall();
  queryClient.clear();
  lk.rooms.length = 0;
  lk.refuseConnect = false;
  posted.length = 0;
  getUserMedia.mockReset();
  getUserMedia.mockImplementation(async () => fakeStream());
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(ticoRoomQuery.queryKey, 'th-tico');
  queryClient.setQueryData(callQuery(ROOM).queryKey, call());
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method === 'POST') posted.push(u);
    const body = u.endsWith('/join')
      ? { call: call(), token: 'test-token', url: 'wss://example.invalid' }
      : u.endsWith('/notes')
        ? { items: [], summary: ['Prices stay as they are'], pending: false, message_id: null }
        : call({ state: u.endsWith('/leave') ? 'ended' : 'live' });
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  cleanup();
  resetCall();
  vi.unstubAllGlobals();
});

function show() {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const page = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <CallPage room={page.useParams().room} /> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [`/call/${ROOM}`] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

async function joinNow() {
  fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
}
const room = () => lk.rooms.at(-1) as (typeof lk.rooms)[number];

describe('Call window', () => {
  it('waits for a press: no microphone, no room, no join before Join', async () => {
    show();
    expect(await screen.findByRole('heading', { name: 'Pricing review' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Join' })).toBeTruthy();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(lk.rooms).toHaveLength(0);
    expect(posted).toEqual([]);
  });

  it('joins on the press: microphone, token, room; then Mute and Leave', async () => {
    show();
    await joinNow();
    expect(await screen.findByRole('button', { name: 'Leave' })).toBeTruthy();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/join`))).toBe(true);
    expect(room().connect.mock.calls[0]).toEqual(['wss://example.invalid', 'test-token']);
    const mute = screen.getByRole('button', { name: 'Mute' });
    fireEvent.click(mute);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unmute' }).getAttribute('aria-pressed')).toBe('true'));
    expect(room().localParticipant.setMicrophoneEnabled.mock.calls[0]).toEqual([false]);
  });

  it('a blocked microphone says so, and Try again asks once more', async () => {
    getUserMedia.mockImplementationOnce(async () => {
      throw new DOMException('denied', 'NotAllowedError');
    });
    show();
    await joinNow();
    expect(await screen.findByText('Microphone blocked')).toBeTruthy();
    expect(posted).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Leave' })).toBeTruthy();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  it('no microphone at all says so, with Try again', async () => {
    getUserMedia.mockImplementationOnce(async () => {
      throw new DOMException('none', 'NotFoundError');
    });
    show();
    await joinNow();
    expect(await screen.findByText('No microphone found')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a room that will not connect is a lost call, with Join again', async () => {
    lk.refuseConnect = true;
    show();
    await joinNow();
    expect(await screen.findByText('Call disconnected')).toBeTruthy();
    lk.refuseConnect = false;
    fireEvent.click(screen.getByRole('button', { name: 'Join again' }));
    expect(await screen.findByRole('button', { name: 'Leave' })).toBeTruthy();
  });

  it('reconnecting offers Leave, and comes back to the call', async () => {
    show();
    await joinNow();
    await screen.findByRole('button', { name: 'Mute' });
    act(() => room().emit('Reconnecting'));
    expect(await screen.findByText('Reconnecting')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Leave' }).length).toBeGreaterThan(0);
    act(() => room().emit('Reconnected'));
    await waitFor(() => expect(screen.queryByText('Reconnecting')).toBeNull());
    act(() => room().emit('Disconnected'));
    expect(await screen.findByText('Call disconnected')).toBeTruthy();
  });

  it('Leave hangs up and tells the server', async () => {
    show();
    await joinNow();
    fireEvent.click(await screen.findByRole('button', { name: 'Leave' }));
    expect(await screen.findByText('Call ended')).toBeTruthy();
    expect(await screen.findByText('Prices stay as they are')).toBeTruthy();
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/leave`))).toBe(true));
  });

  it('with Tico, the Dot is the stage and speaks while Tico talks', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico' }));
    show();
    await joinNow();
    await screen.findByRole('button', { name: 'Mute' });
    expect(document.querySelector('.tc-orb .dot')).toBeTruthy();
    const tico = { identity: 'bot:tico', isMicrophoneEnabled: true };
    act(() => {
      room().remoteParticipants.set('bot:tico', tico);
      room().emit('ParticipantConnected', tico);
      room().emit('ActiveSpeakersChanged', [tico]);
    });
    await waitFor(() => expect(dot.set).toHaveBeenCalledWith('speak', expect.any(Function)));
  });
});
