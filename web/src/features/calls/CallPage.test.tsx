import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Call } from '@/api/schema';
import { ActionsRoot, run } from '@/actions';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { openCallWindow, watchCallWindow, type CallWindowState, type TicoDesktop } from '@/desktop';
import { Toasts } from '@/kit/Toast';
import { dot } from '@/tico/dot';
import './actions';
import { CallBar } from './CallBar';
import { CallPage } from './CallPage';
import { CallSide } from './CallSide';
import { callQuery, ringingQuery, ticoBotQuery } from './queries';
import { RingCard } from './RingCard';
import { callState, resetCall } from './session';

// The stage's file preview is the library's, and the chat beside is the conversation's: not under test here.
vi.mock('@/features/library/exports', () => ({ FilePreview: () => null }));
vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => <span className="lvl" />,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));
vi.mock('@/conversation', async (real) => ({ ...(await real<Record<string, unknown>>()), Conversation: () => <div /> }));

// LiveKit, faked: a room that records what it is asked and lets a test raise its events. Nothing connects.
type Handler = (...a: unknown[]) => void;
const lk = vi.hoisted(() => ({
  rooms: [] as {
    remoteParticipants: Map<string, unknown>;
    emit: (e: string, ...a: unknown[]) => void;
    connect: { mock: { calls: unknown[][] } };
    disconnect: { mock: { calls: unknown[][] } };
    localParticipant: { setMicrophoneEnabled: { mock: { calls: unknown[][] } }; setScreenShareEnabled: { mock: { calls: unknown[][] } } };
  }[],
  refuseConnect: false,
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
    DisconnectReason: { ROOM_DELETED: 'room_deleted' },
    Track: { Source: { Microphone: 'microphone', ScreenShare: 'screen_share' }, Kind: { Audio: 'audio', Video: 'video' } },
  };
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const ROOM = '0f8fad5b-d9cb-469f-a165-70867728950e';
// What every call the server sends carries in this test, on top of the defaults.
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

const posted: string[] = [];
const asked: { url: string; method: string; body?: unknown }[] = [];
const sent = () => asked.filter((a) => a.method !== 'GET');
const getUserMedia = vi.fn();
const fakeStream = () => ({ getAudioTracks: () => [{ kind: 'audio' }], getTracks: () => [{ stop: vi.fn() }] });

beforeEach(() => {
  resetCall();
  queryClient.clear();
  lk.rooms.length = 0;
  lk.refuseConnect = false;
  base = {};
  posted.length = 0;
  asked.length = 0;
  getUserMedia.mockReset();
  getUserMedia.mockImplementation(async () => fakeStream());
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia, getDisplayMedia: vi.fn() } });
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const gm = { id: 'bot:gm', name: 'Growth Manager', kind: 'bot' };
  const fin = { id: 'bot:fin', name: 'Finance', kind: 'bot' };
  queryClient.setQueryData(['directory'], { actors: [gm, fin], byId: { 'bot:gm': gm, 'bot:fin': fin } });
  queryClient.setQueryData(ticoRoomQuery.queryKey, 'th-tico');
  queryClient.setQueryData(ticoBotQuery.queryKey, 'bot:b1');
  queryClient.setQueryData(callQuery(ROOM).queryKey, call());
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method ?? 'GET';
    if (method === 'POST') posted.push(u);
    asked.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const body = u.endsWith('/join')
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

// The root mounts the bar, the ring card and the toasts on every page, as __root.tsx does.
function show(at = `/call/${ROOM}`) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <CallBar />
        <RingCard />
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

async function joinNow() {
  fireEvent.click(await screen.findByRole('button', { name: 'Join' }));
}
async function joined() {
  await joinNow();
  await screen.findByRole('button', { name: 'Mute' });
}
async function joinThenGo(router: ReturnType<typeof show>, to = '/tasks') {
  await joined();
  await act(() => router.navigate({ to }));
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

  it('says there is no such call when the server has none, with nothing to try again', async () => {
    queryClient.removeQueries({ queryKey: callQuery(ROOM).queryKey });
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ error: { code: 'not_found', message: 'No such call' } }), { status: 404, headers: { 'Content-Type': 'application/json' } }));
    show();
    expect(await screen.findByText('No such call', {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('joins on the press: microphone, token, room; then Mute and Leave @s974', async () => {
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

  it('with Tico, the Dot is the stage and speaks while the call voice talks @n:cl:tico', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico', bots: ['b1'] }));
    show();
    await joinNow();
    await screen.findByRole('button', { name: 'Mute' });
    expect(document.querySelector('.tc-orb .dot')).toBeTruthy();
    const voice = { identity: `tico-recorder-${ROOM}`, isMicrophoneEnabled: true };
    act(() => {
      room().remoteParticipants.set(voice.identity, voice);
      room().emit('ParticipantConnected', voice);
      room().emit('ActiveSpeakersChanged', [voice]);
    });
    await waitFor(() => expect(dot.set).toHaveBeenCalledWith('speak', expect.any(Function)));
    // The voice is not a face of its own.
    expect(callState().others).toEqual([]);
    expect(callState().voice).toBe(voice.identity);
  });

  it('Tico taken out of its call: the stage is who is there, and the Dot is back when Tico is', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico', bots: ['b1'] }));
    show();
    await joined();
    expect(document.querySelector('.tc-orb .dot')).toBeTruthy();
    act(() => void queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico', bots: [] })));
    await waitFor(() => expect(document.querySelector('.tc-orb')).toBeNull());
    expect(screen.getAllByText('You').length).toBeGreaterThan(0);
    // nobody else reads Tico's room: there is no one to ring in
    expect(screen.queryByRole('button', { name: 'Add people or agents' })).toBeNull();
    act(() => void queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico', bots: ['b1'] })));
    await waitFor(() => expect(document.querySelector('.tc-orb .dot')).toBeTruthy());
  });

  it('Tico on a call is Tico: its name in the caption, its face the Dot', async () => {
    const tico = { id: 'bot:b1', name: 'Assistant', kind: 'bot' };
    queryClient.setQueryData(['directory'], { actors: [tico], byId: { 'bot:b1': tico } });
    queryClient.setQueryData(
      callQuery(ROOM).queryKey,
      call({ thread_id: 'th-tico', bots: ['b1'], voice: { bots: [{ slug: 'b1', state: 'speaking', caption: 'The ads start Monday.' }], note: null } }),
    );
    show();
    await joined();
    const said = await screen.findByText('The ads start Monday.');
    expect(said.querySelector('b')?.textContent).toBe('Tico');
    expect(screen.queryByText(/Assistant/)).toBeNull();
  });

  it('my own shared screen shows on the stage, in Tico’s call too, once it is out', async () => {
    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ thread_id: 'th-tico', bots: ['b1'] }));
    show();
    await joined();
    let out: (v?: unknown) => void = () => {};
    (room().localParticipant.setScreenShareEnabled as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => new Promise((r) => (out = r)));
    void run('calls.share', { id: ROOM, on: true }, { via: 'click' });
    await waitFor(() => expect(room().localParticipant.setScreenShareEnabled.mock.calls.length).toBe(1));
    expect(callState().sharing).toBe(false); // the picker is open: nothing is out yet
    act(() => out());
    expect(await screen.findByLabelText('Shared screen')).toBeTruthy();
    expect(screen.getAllByText('You').length).toBeGreaterThan(0);
    expect(callState().sharing).toBe(true);
  });

  it('the server closing the room ends the call rather than losing it', async () => {
    show();
    await joinNow();
    await screen.findByRole('button', { name: 'Mute' });
    act(() => room().emit('Disconnected', 'room_deleted'));
    expect(await screen.findByText('Call ended')).toBeTruthy();
  });

  it('another page keeps the call: the bar shows it, mutes it, opens it, and Leave hangs up @s1049', async () => {
    const router = show();
    await joinNow();
    await screen.findByRole('button', { name: 'Mute' });
    await act(() => router.navigate({ to: '/tasks' }));
    await screen.findByRole('heading', { name: 'Tasks' });
    const bar = await screen.findByRole('banner', { name: 'Call' });
    expect(callState().phase).toBe('live');
    expect(posted.some((u) => u.endsWith('/leave'))).toBe(false);
    expect(room().disconnect).not.toHaveBeenCalled();
    fireEvent.click(within(bar).getByRole('button', { name: 'Mute' }));
    await waitFor(() => expect(within(bar).getByRole('button', { name: 'Unmute' })).toBeTruthy());
    expect(within(bar).getAllByRole('link', { name: 'Open call' })[0]?.getAttribute('href')).toBe(`/call/${ROOM}`);
    fireEvent.click(within(bar).getByRole('button', { name: 'Leave' }));
    await waitFor(() => expect(screen.queryByRole('banner', { name: 'Call' })).toBeNull());
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/leave`))).toBe(true));
  });

  it('a call ringing shows its card; Decline hides it, Answer joins @s1041', async () => {
    const other = '7c9e6679-7425-40de-944b-e07fc1f90ae7';
    queryClient.setQueryData(ringingQuery.queryKey, { calls: [call({ id: other, ring: true, connector: 'human:ana', title: 'Seed box pricing' })] });
    show('/tasks');
    const card = await screen.findByRole('alertdialog', { name: 'Incoming call' });
    expect(within(card).getByText('Seed box pricing')).toBeTruthy();
    fireEvent.click(within(card).getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${other}/join`))).toBe(true));
    await waitFor(() => expect(screen.queryByRole('alertdialog', { name: 'Incoming call' })).toBeNull());
  });

  it('Decline hides a ring for good on this device', async () => {
    const other = '9b2f1c55-3d1e-4a8e-9f6d-2b7a4c1e8d03';
    queryClient.setQueryData(ringingQuery.queryKey, { calls: [call({ id: other, ring: true, connector: 'human:ana' })] });
    show('/tasks');
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Decline' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(posted).toEqual([]);
  });

  it('calls an agent: their direct thread, a Tico call with them in it, then the call itself @s488', async () => {
    const router = show('/tasks');
    await screen.findByRole('heading', { name: 'Tasks' });
    asked.length = 0;
    expect(await run('calls.start', { with: ['bot:gm'] }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(asked).toEqual([]);
    expect(await run('calls.start', { with: ['bot:gm'] }, { via: 'click' })).toMatchObject({ ok: true });
    expect(asked[0]?.url).toContain('/api/v2/threads/dm?with=bot%3Agm&create=true');
    expect(asked.find((a) => a.url.endsWith(`/threads/${ROOM}/calls`))?.body).toEqual({ provider: 'tico', bots: ['gm'] });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/call/${ROOM}`));
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/join`))).toBe(true));
  });
});

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
    await waitFor(() => expect(sent().some((x) => x.method === 'PUT' && x.url.endsWith(`/calls/${ROOM}/stage`))).toBe(true));
    expect(room().localParticipant.setScreenShareEnabled.mock.calls[0]).toEqual([true, { audio: false }]);
    expect(sent().find((x) => x.method === 'PUT')?.body).toEqual({ kind: 'screen' });
    expect(await within(bar).findByRole('button', { name: 'Stop sharing' })).toBeTruthy();
    expect(within(bar).queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('the call page’s More holds End call for the one who started it @s1049', async () => {
    base = { connector: 'human:me' };
    queryClient.setQueryData(callQuery(ROOM).queryKey, call());
    show();
    await joined();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'End call' }));
    const ask = await screen.findByRole('alertdialog', { name: 'End the call for everyone?' });
    fireEvent.click(within(ask).getByRole('button', { name: 'End call' }));
    await waitFor(() => expect(sent().some((x) => x.method === 'POST' && x.url.endsWith(`/calls/${ROOM}/end`))).toBe(true));
  });

  it('More’s Add agent brings the agent in; End is not there for someone else’s call, and Tico may not end it either @s1049', async () => {
    show();
    await joined();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const add = await screen.findByRole('menuitem', { name: 'Add agent' });
    expect(screen.queryByRole('menuitem', { name: 'End call' })).toBeNull();
    fireEvent.click(add);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Finance' }));
    await waitFor(() => expect(sent().find((x) => x.url.endsWith(`/calls/${ROOM}/bots`))?.body).toEqual({ bots: ['fin'] }));

    expect(await run('calls.end', { id: ROOM }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(sent().some((x) => x.url.endsWith('/end'))).toBe(false);
    queryClient.setQueryData(callQuery(ROOM).queryKey, call({ connector: 'human:me' }));
    expect(await run('calls.end', { id: ROOM }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(sent().some((x) => x.url.endsWith(`/calls/${ROOM}/end`))).toBe(true);
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
    await waitFor(() => expect(sent().some((x) => x.method === 'DELETE' && x.url.endsWith(`/calls/${ROOM}/bots/gm`))).toBe(true));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(sent().find((x) => x.method === 'POST' && x.url.endsWith(`/calls/${ROOM}/bots`))?.body).toEqual({ bots: ['gm'] }));
  });

  it('sharing my screen stays my own press: Tico may not start it @s1046', async () => {
    const router = show();
    await joinThenGo(router);
    expect(await run('calls.share', { id: ROOM, on: true }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(room().localParticipant.setScreenShareEnabled.mock.calls).toEqual([]);
  });
});

describe('After the call @s1047', () => {
  it('Leave hangs up and tells the server; then the summary, the transcript, and Create task makes a task tied to the meeting @s1047', async () => {
    show();
    await joinNow();
    fireEvent.click(await screen.findByRole('button', { name: 'Leave' }));
    expect(await screen.findByText('Call ended')).toBeTruthy();
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/leave`))).toBe(true));
    expect(await screen.findByText('Prices stay as they are')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Transcript' })).toBeTruthy();
    expect(await screen.findByText('We keep the prices')).toBeTruthy();

    fireEvent.click(await screen.findByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(sent().find((x) => x.url.endsWith('/api/v2/tasks'))?.body).toEqual({ title: 'Send the deck', owner: 'human:ana', live_meeting_id: 'm1' }));
    expect(await screen.findByRole('link', { name: 'Open task' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Create task' })).toBeNull();
  });

  it('Hide puts an item away @s1047', async () => {
    show();
    await joinNow();
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
    await joinThenGo(show());
    expect(callState().chat).toBe('thread:th1');
    await run('calls.hideChat', {}, { via: 'click' });
    expect(callState().chat).toBeNull();
  });

  it('no chat beside for a call with no agents @s488', async () => {
    await joinThenGo(show());
    expect(callState().chat).toBeNull();
  });
});

/* The desktop app, faked as root.test.tsx does: `window.tico` with the small call window's bridge. */
function fakeDesktop() {
  const said: { state?: (s: CallWindowState) => void } = {};
  const t = {
    version: '1.0.0',
    platform: 'mac',
    companies: { list: vi.fn(async () => []), switchTo: vi.fn(), add: vi.fn(), remove: vi.fn(), reportSelf: vi.fn(), onChange: vi.fn(() => () => {}) },
    setBadge: vi.fn(),
    callWindow: {
      open: vi.fn(async () => {}),
      close: vi.fn(async () => {}),
      setOnTop: vi.fn(async () => {}),
      onState: vi.fn((cb: (s: CallWindowState) => void) => {
        said.state = cb;
        return () => {};
      }),
    },
    app: { updateState: vi.fn(), restartToUpdate: vi.fn(), checkForUpdates: vi.fn(), showLog: vi.fn(), logTail: vi.fn(), openAtLogin: vi.fn() },
  } satisfies TicoDesktop;
  (window as { tico?: TicoDesktop }).tico = t;
  return { t, said };
}

describe('The desktop app’s small call window', () => {
  let stop: () => void = () => {};
  afterEach(() => {
    stop();
    delete (window as { tico?: TicoDesktop }).tico;
  });

  it('the small window’s address in a browser tab waits for Join and offers one way back', async () => {
    show(`/call/${ROOM}?window=1`);
    const back = await screen.findByRole('link', { name: 'Back to the app' });
    expect(back.getAttribute('href')).toBe(`/call/${ROOM}`);
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(await screen.findByRole('button', { name: 'Join' })).toBeTruthy();
    expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/join`))).toBe(false);
  });

  it('in a browser, nothing offers the small window', async () => {
    show();
    await joinNow();
    await screen.findByRole('button', { name: 'Leave' });
    expect(screen.queryByRole('button', { name: 'Open in a small window' })).toBeNull();
  });

  it('the main window moves the call out through the shell’s action', async () => {
    const { t } = fakeDesktop();
    show();
    await joinNow();
    fireEvent.click(await screen.findByRole('button', { name: 'Open in a small window' }));
    await waitFor(() => expect(t.callWindow.open).toHaveBeenCalledWith(ROOM));
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/leave`))).toBe(true));
  });

  it('while the call is out, the bar and the call page say so and draw no controls', async () => {
    const { said } = fakeDesktop();
    stop = watchCallWindow(() => {});
    await openCallWindow(ROOM);
    act(() => said.state?.('open'));
    const router = show('/tasks');
    const bar = await screen.findByRole('banner', { name: 'Call' });
    expect(within(bar).getByText('In the small window')).toBeTruthy();
    expect(within(bar).queryByRole('button')).toBeNull();
    await act(() => router.navigate({ to: '/call/$room', params: { room: ROOM } }));
    expect(await screen.findByText('In the small window')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Join' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Leave' })).toBeNull();
    act(() => said.state?.('closed'));
    expect(await screen.findByRole('button', { name: 'Join' })).toBeTruthy();
  });

  it('the small window joins at once, keeps on top, and goes back to the app @s975', async () => {
    const { t } = fakeDesktop();
    show(`/call/${ROOM}?window=1`);
    await waitFor(() => expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/join`))).toBe(true));
    expect(await screen.findByRole('button', { name: 'Leave' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Back to bar' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open in a small window' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Keep on top' }));
    await waitFor(() => expect(t.callWindow.setOnTop).toHaveBeenCalledWith(false));
    fireEvent.click(screen.getByRole('button', { name: 'Back to the app' }));
    await waitFor(() => expect(t.callWindow.close).toHaveBeenCalledWith('return'));
    expect(posted.some((u) => u.endsWith(`/calls/${ROOM}/leave`))).toBe(true);
  });
});
