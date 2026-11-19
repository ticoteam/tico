import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Call } from '@/api/schema';
import { ActionsRoot, run } from '@/actions';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import './actions';
import { CallBar } from './CallBar';
import { CallPage } from './CallPage';
import { CallSide } from './CallSide';
import { callQuery } from './queries';
import { callState, resetCall } from './session';

/* The live call's gaps: the bar's Share and Captions, the call page's Add agent and End (s1049); show a screen or a file, take an agent
   out with Undo (s1046); after the call, the transcript and Create task (s1047); the agents' chat beside (s488). */

// The stage's file preview is the library's: not under test here.
vi.mock('@/features/library/exports', () => ({ FilePreview: () => null }));
vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => <span className="lvl" />,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));
vi.mock('@/conversation', async (real) => ({ ...(await real<Record<string, unknown>>()), Conversation: () => <div /> }));

// LiveKit, faked as in CallPage.test.tsx, with a screen to share. Nothing connects.
type Handler = (...a: unknown[]) => void;
const lk = vi.hoisted(() => ({
  rooms: [] as {
    emit: (e: string, ...a: unknown[]) => void;
    localParticipant: { setScreenShareEnabled: { mock: { calls: unknown[][] } } };
  }[],
}));
vi.mock('livekit-client', () => {
  class Room {
    handlers = new Map<string, Handler>();
    localParticipant = {
      identity: 'human:me',
      audioLevel: 0,
      publishTrack: vi.fn(async () => undefined),
      setMicrophoneEnabled: vi.fn(async () => undefined),
      setScreenShareEnabled: vi.fn(async () => undefined),
      getTrackPublication: vi.fn(() => undefined),
    };
    remoteParticipants = new Map();
    canPlaybackAudio = true;
    startAudio = vi.fn(async () => undefined);
    disconnect = vi.fn(async () => undefined);
    switchActiveDevice = vi.fn(async () => true);
    connect = vi.fn(async () => undefined);
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
    DisconnectReason: { ROOM_DELETED: 'room_deleted' },
    Track: { Source: { Microphone: 'microphone', ScreenShare: 'screen_share' }, Kind: { Audio: 'audio', Video: 'video' } },
  };
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const ROOM = '0f8fad5b-d9cb-469f-a165-70867728950e';
let base: Partial<Call> = {};
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
    ...base,
    ...p,
  }) as Call;

type Sent = { url: string; method: string; body: unknown };
let sent: Sent[] = [];
const getUserMedia = vi.fn();
const fakeStream = () => ({ getAudioTracks: () => [{ kind: 'audio' }], getTracks: () => [{ stop: vi.fn() }] });

beforeEach(() => {
  resetCall();
  queryClient.clear();
  lk.rooms.length = 0;
  base = {};
  sent = [];
  getUserMedia.mockReset();
  getUserMedia.mockImplementation(async () => fakeStream());
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia, getDisplayMedia: vi.fn() } });
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], {
    actors: [
      { id: 'bot:gm', name: 'Growth Manager', kind: 'bot' },
      { id: 'bot:fin', name: 'Finance', kind: 'bot' },
    ],
    byId: { 'bot:gm': { id: 'bot:gm', name: 'Growth Manager', kind: 'bot' }, 'bot:fin': { id: 'bot:fin', name: 'Finance', kind: 'bot' } },
  });
  queryClient.setQueryData(ticoRoomQuery.queryKey, 'th-tico');
  queryClient.setQueryData(callQuery(ROOM).queryKey, call());
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const body =
      u.endsWith('/join')
        ? { call: call(), token: 'test-token', url: 'wss://example.invalid' }
        : u.endsWith('/notes')
          ? { items: [{ owner: 'human:ana', title: 'Send the deck' }], summary: ['Prices stay as they are'], pending: false, message_id: null }
          : u.includes('/live-meetings/')
            ? { chunks: [{ seq: 1, speaker: 'human:ana', text: 'We keep the prices' }], event_id: 4 }
            : u.endsWith('/api/v2/tasks')
              ? { task: { id: 't9', title: 'Send the deck' } }
              : u.endsWith('/stage') && method === 'PUT'
                ? call({ stage: { ...(init?.body ? JSON.parse(String(init.body)) : {}), by: 'human:me' } as Call['stage'] })
                : u.includes('/bots/') && method === 'DELETE'
                  ? call({ bots: [] })
                  : u.endsWith('/bots')
                    ? call({ bots: ['gm'] })
                    : call({ state: u.endsWith('/leave') || u.endsWith('/end') ? 'ended' : 'live' });
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  cleanup();
  resetCall();
  vi.unstubAllGlobals();
});

function show(at = `/call/${ROOM}`) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <CallBar />
        <Toasts />
        <Outlet />
      </>
    ),
  });
  const page = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <CallPage room={page.useParams().room} /> });
  const side = createRoute({ getParentRoute: () => root, path: '/side', component: () => <CallSide room={ROOM} /> });
  const tasks = createRoute({ getParentRoute: () => root, path: '/tasks', component: () => <h1>Tasks</h1> });
  const task = createRoute({ getParentRoute: () => root, path: '/task/$task', component: () => <h1>Task</h1> });
  const router = createRouter({ routeTree: root.addChildren([page, side, tasks, task]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const room = () => lk.rooms.at(-1)!;
async function joined() {
  fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
  await screen.findByRole('button', { name: 'Mute' });
}

async function joinThenGo(router: ReturnType<typeof show>, to = '/tasks') {
  fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
  await screen.findByRole('button', { name: 'Mute' });
  await act(() => router.navigate({ to }));
}

describe('The call bar @s1049', () => {
  it('Captions hide what is said; Share screen sends the screen and puts it on the stage @s1049', async () => {
    const router = show();
    await joinThenGo(router);
    const bar = await screen.findByRole('banner', { name: 'Call' });
    act(() => room().emit('TranscriptionReceived', [{ text: 'Prices stay' }], { identity: 'human:ana' }));
    expect(await within(bar).findByText('Prices stay')).toBeTruthy();
    fireEvent.click(within(bar).getByRole('button', { name: 'Captions' }));
    await waitFor(() => expect(within(bar).queryByText('Prices stay')).toBeNull());
    expect(callState().captions).toBe(false);

    fireEvent.click(within(bar).getByRole('button', { name: 'Share screen' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'PUT' && x.url.endsWith(`/calls/${ROOM}/stage`))).toBe(true));
    expect(room().localParticipant.setScreenShareEnabled.mock.calls[0]).toEqual([true, { audio: false }]);
    expect(sent.find((x) => x.method === 'PUT')?.body).toEqual({ kind: 'screen' });
    expect(await within(bar).findByRole('button', { name: 'Stop sharing' })).toBeTruthy();
    expect(within(bar).queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('the call page’s More holds Add agent, and End call for the one who started it @s1049', async () => {
    base = { connector: 'human:me' };
    queryClient.setQueryData(callQuery(ROOM).queryKey, call());
    show();
    await joined();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Add agent' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'End call' }));
    const ask = await screen.findByRole('alertdialog', { name: 'End the call for everyone?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'End call' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'POST' && x.url.endsWith(`/calls/${ROOM}/end`))).toBe(true));
  });

  it('End is not in More for someone else’s call, and Tico may not end it either @s1049', async () => {
    show();
    await joined();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('menuitem', { name: 'Add agent' });
    expect(screen.queryByRole('menuitem', { name: 'End call' })).toBeNull();
    expect(await run('calls.end', { id: ROOM }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(sent.some((x) => x.url.endsWith('/end'))).toBe(false);

    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ connector: 'human:me' }));
    expect(await run('calls.end', { id: ROOM }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent.some((x) => x.url.endsWith(`/calls/${ROOM}/end`))).toBe(true);
  });

  it('Add agent brings the agent in @s1049', async () => {
    show();
    await joined();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add agent' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Finance' }));
    await waitFor(() => expect(sent.find((x) => x.url.endsWith(`/calls/${ROOM}/bots`))?.body).toEqual({ bots: ['fin'] }));
  });
});

describe('Show and take out @s1046', () => {
  it('People takes an agent out, and Undo brings it back @s1046', async () => {
    base = { bots: ['gm'] };
    queryClient.setQueryData(callQuery(ROOM).queryKey, call());
    const router = show();
    await joinThenGo(router, '/side');
    fireEvent.click(await screen.findByRole('tab', { name: /People/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Take Growth Manager out' }));
    await waitFor(() => expect(sent.some((x) => x.method === 'DELETE' && x.url.endsWith(`/calls/${ROOM}/bots/gm`))).toBe(true));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(sent.find((x) => x.method === 'POST' && x.url.endsWith(`/calls/${ROOM}/bots`))?.body).toEqual({ bots: ['gm'] }));
  });

  it('sharing my screen stays my own press: Tico may not start it @s1046', async () => {
    const router = show();
    await joinThenGo(router);
    expect(await run('calls.share', { id: ROOM, on: true }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(room().localParticipant.setScreenShareEnabled.mock.calls).toEqual([]);
  });
});

describe('After the call @s1047', () => {
  it('shows the transcript, and Create task makes a task tied to the meeting @s1047', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Leave' }));
    expect(await screen.findByText('Call ended')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Transcript' })).toBeTruthy();
    expect(await screen.findByText('We keep the prices')).toBeTruthy();

    fireEvent.click(await screen.findByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(sent.find((x) => x.url.endsWith('/api/v2/tasks'))?.body).toEqual({ title: 'Send the deck', owner: 'human:ana', live_meeting_id: 'm1' }));
    expect(await screen.findByRole('link', { name: 'Open task' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Create task' })).toBeNull();
  });

  it('Hide puts an item away @s1047', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Leave' }));
    expect(await screen.findByText('Send the deck')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    await waitFor(() => expect(screen.queryByText('Send the deck')).toBeNull());
  });
});

describe('The agents’ chat beside the page @s488', () => {
  it('a call with agents keeps their chat open beside the page until it is closed @s488', async () => {
    base = { bots: ['gm'] };
    queryClient.setQueryData(callQuery(ROOM).queryKey, call());
    const router = show();
    await joinThenGo(router);
    expect(callState().chat).toBe('thread:th1');
    await run('calls.hideChat', {}, { via: 'click' });
    expect(callState().chat).toBeNull();
  });

  it('no chat beside for a call with no agents, or Tico’s own @s488', async () => {
    const router = show();
    await joinThenGo(router);
    expect(callState().chat).toBeNull();
  });
});
