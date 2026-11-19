import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Call, CallList } from '@/api/schema';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { callCount, callLength, callWhen } from './model';
import { callQuery, threadCallsQuery } from './queries';
import { resetCall } from './session';
import { callCardOf, CallCard, ThreadCallHead } from './ThreadCall';

vi.mock('@/tico/dot', () => ({
  Dot: () => <span className="dot" />,
  CallBars: () => <span className="lvl" />,
  dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null },
}));
// LiveKit, faked: a room that connects to nothing.
vi.mock('livekit-client', () => {
  class Room {
    localParticipant = { identity: 'human:me', audioLevel: 0, publishTrack: vi.fn(async () => undefined), setMicrophoneEnabled: vi.fn(async () => undefined) };
    remoteParticipants = new Map();
    canPlaybackAudio = true;
    startAudio = vi.fn(async () => undefined);
    disconnect = vi.fn(async () => undefined);
    connect = vi.fn(async () => undefined);
    static getLocalDevices = vi.fn(async () => []);
    on() {
      return this;
    }
  }
  return { Room, RoomEvent: new Proxy({}, { get: (_t, k) => k }), DisconnectReason: {}, Track: { Source: { Microphone: 'microphone' }, Kind: { Audio: 'audio' } } };
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false, staleTime: Infinity } });
});

const ID = '0f8fad5b-d9cb-469f-a165-70867728950e';
const call = (p: Partial<Call> = {}): Call =>
  ({
    id: ID,
    bots: [],
    connector: 'human:me',
    effort: null,
    end_reason: null,
    ended_at: null,
    holds: [],
    meeting_id: 'm1',
    model: null,
    participants: [{ actor: 'human:ana', in_call: true, joined_at: '2026-11-18T10:00:00Z', left_at: null, removed_at: null }],
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
    card_message_id: 'msg1',
    duration_s: null,
    join_url: null,
    live_count: 0,
    meet_uri: null,
    provider: 'tico',
    ring: false,
    seen_at: null,
    status_source: null,
    transcript_message_id: null,
    ...p,
  }) as Call;
const list = (p: Partial<CallList> = {}): CallList => ({ available: true, calls: [], next_before: null, next_scheduled: null, providers: ['tico'], ...p });

const sent: { method: string; url: string; body: unknown }[] = [];
let answer: (url: string) => unknown = () => call();
const getUserMedia = vi.fn(async () => ({ getAudioTracks: () => [{ kind: 'audio' }], getTracks: () => [{ stop: vi.fn() }] }));

beforeEach(() => {
  resetCall();
  queryClient.clear();
  sent.length = 0;
  answer = () => call();
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], {
    actors: [],
    byId: { 'human:ana': { id: 'human:ana', kind: 'person', name: 'Ana Reyes' }, 'bot:b1': { id: 'bot:b1', kind: 'agent', name: 'Growth Manager' } },
  });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(answer(String(url))), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  cleanup();
  resetCall();
  vi.unstubAllGlobals();
});

function show(node: ReactNode) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const page = createRoute({ getParentRoute: () => root, path: '/threads/th1', component: () => node });
  const callPage = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <h1>Call page</h1> });
  const router = createRouter({ routeTree: root.addChildren([page, callPage]), history: createMemoryHistory({ initialEntries: ['/threads/th1'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('A thread’s call control', () => {
  it('starts a voice call with the agents left picked, then opens it @s1006', async () => {
    queryClient.setQueryData(threadCallsQuery('th1').queryKey, list());
    const router = show(<ThreadCallHead thread="th1" members={['human:ana', 'bot:b1', 'bot:b2']} canPost />);
    fireEvent.click(await screen.findByRole('button', { name: 'Call' }));
    // the people are rung by the call itself: set, not pickable
    expect(screen.getByRole('checkbox', { name: /Ana Reyes/ }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('checkbox', { name: /Ana Reyes/ }).getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(screen.getByRole('checkbox', { name: /b2/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Start call' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/call/${ID}`));
    expect(sent[0]).toEqual({ method: 'POST', url: '/api/v2/threads/th1/calls', body: { provider: 'tico', bots: ['b1'] } });
    expect(getUserMedia).toHaveBeenCalled();
  });

  it('posts a Google Meet link in the tab the press opened, with no agents', async () => {
    queryClient.setQueryData(threadCallsQuery('th1').queryKey, list({ providers: ['google_meet', 'tico'] }));
    const tab = { closed: false, opener: {}, location: { href: '' }, close: vi.fn() };
    const open = vi.fn(() => tab);
    vi.stubGlobal('open', open);
    answer = () => call({ provider: 'google_meet', join_url: 'https://meet.example.invalid/abc' });
    show(<ThreadCallHead thread="th1" members={['human:ana', 'bot:b1']} canPost />);
    fireEvent.click(await screen.findByRole('button', { name: 'Call' }));
    fireEvent.click(screen.getByRole('button', { name: 'Google Meet' }));
    expect(screen.getByRole('checkbox', { name: /Growth Manager/ }).getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Post link' }));
    await waitFor(() => expect(tab.location.href).toBe('https://meet.example.invalid/abc'));
    expect(open).toHaveBeenCalledWith('', '_blank');
    expect(sent[0]?.body).toEqual({ provider: 'google_meet' });
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it('offers Join, with how many are in it, while a call is on', async () => {
    queryClient.setQueryData(threadCallsQuery('th1').queryKey, list({ calls: [{ ...call(), connector: 'human:ana' }] }));
    queryClient.setQueryData(callQuery(ID).queryKey, call({ bots: ['b1'] }));
    show(<ThreadCallHead thread="th1" members={['human:ana']} canPost />);
    const join = await screen.findByRole('button', { name: 'Join call' });
    expect(join.textContent).toContain('2');
    expect(screen.queryByRole('button', { name: 'Call' })).toBeNull();
  });

  it('a reply without its lists reads as no calls, not a crash', async () => {
    answer = () => ({ available: true, providers: ['tico'] });
    show(<ThreadCallHead thread="th1" members={['human:ana']} canPost />);
    expect(await screen.findByRole('button', { name: 'Call' })).toBeTruthy();
  });

  it('shows nothing to start where the person cannot post', async () => {
    queryClient.setQueryData(threadCallsQuery('th1').queryKey, list());
    show(
      <>
        <p>Thread</p>
        <ThreadCallHead thread="th1" members={['human:ana']} canPost={false} />
      </>,
    );
    await screen.findByText('Thread');
    expect(screen.queryByRole('button', { name: 'Call' })).toBeNull();
  });
});

describe('A call’s card', () => {
  it('is read from a message that carries it', () => {
    expect(callCardOf({ refs: { call: { id: ID, card: true } } })).toBe(ID);
    expect(callCardOf({ refs: { call: { id: ID } } })).toBeNull();
    expect(callCardOf({ refs: {} })).toBeNull();
  });

  it('ended: how long it was', async () => {
    queryClient.setQueryData(callQuery(ID).queryKey, call({ state: 'ended', duration_s: 23 * 60, transcript_message_id: 'msg9' }));
    show(<CallCard id={ID} />);
    expect(await screen.findByText('23m')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Transcript' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Join' })).toBeNull();
  });

  it('a Google Meet its starter can end for everyone', async () => {
    queryClient.setQueryData(callQuery(ID).queryKey, call({ provider: 'google_meet', status_source: 'google', live_count: 1 }));
    answer = () => call({ provider: 'google_meet', state: 'ended' });
    show(<CallCard id={ID} />);
    fireEvent.click(await screen.findByRole('button', { name: 'End' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'End the call for everyone' }));
    await waitFor(() => expect(sent.map((x) => x.url)).toEqual([`/api/v2/calls/${ID}/end`]));
  });
});

describe('call words', () => {
  it('counts who joined and the agents of a voice call, the source’s count for a Meet', () => {
    expect(callCount(call({ bots: ['b1'] }))).toBe(2);
    expect(callCount(call({ provider: 'google_meet', status_source: 'google', live_count: 5 }))).toBe(5);
    expect(callCount(call({ provider: 'google_meet', status_source: null, live_count: 5 }))).toBe(0);
  });
  it('says when a booked call is, and how long one was', () => {
    const now = new Date(2026, 10, 18, 9, 0);
    expect(callWhen(new Date(2026, 10, 18, 14, 30).toISOString(), now)).toBe('2:30 PM');
    expect(callWhen(new Date(2026, 10, 19, 14, 30).toISOString(), now)).toBe('Thu, Nov 19, 2:30 PM');
    expect(callLength(45)).toBe('45s');
    expect(callLength(65 * 60)).toBe('1h 5m');
  });
});
