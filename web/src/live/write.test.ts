import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, write } from '@/data';
import { clearHeld, heldCount } from '@/data/held';
import { FakeEventSource, message, messageFrame, opening } from './fake-stream';
import { flushFrame } from './frame';
import { removeMessage, upsertMessage, type MessageRow, type Timeline, type TimelineData } from './patch';
import { liveStatus, startLive, stopLive } from './stream';

// A chat send end to end: the pending row, the stream's echo, the POST's answer; and the stream's return sending
// what was held.

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  startLive();
  FakeEventSource.last.feed(opening(900));
});

afterEach(() => {
  clearHeld();
  stopLive();
  queryClient.clear();
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const timeline = ['thread', 'th1', 'timeline'];
const ids = () => queryClient.getQueryData<Timeline>(timeline)?.messages.map((m) => m.id);

function sendMessage(send: (key: string) => Promise<MessageRow>, offline?: 'hold') {
  const pending = { id: 'tmp:1', thread_id: 'th1', body: 'Hello' };
  return write<MessageRow>({
    key: timeline,
    optimistic: (d: TimelineData) => upsertMessage(d, 'th1', pending),
    send,
    settle: (d: TimelineData, real) => upsertMessage(removeMessage(d, pending.id), 'th1', real),
    revert: (d: TimelineData) => removeMessage(d, pending.id),
    ...(offline && { offline }),
  });
}

describe('a chat send', () => {
  it('shows at once and is swapped for the real message, never twice, when the echo comes first', async () => {
    queryClient.setQueryData<Timeline>(timeline, { messages: [message('m1', 'th1', 4400)] });
    let answer!: (m: MessageRow) => void;
    const send = vi.fn((_key: string) => new Promise<MessageRow>((res) => (answer = res)));
    const sent = sendMessage(send);
    expect(ids()).toEqual(['m1', 'tmp:1']);
    expect(send.mock.calls[0]?.[0]).toMatch(/^[0-9a-f-]{36}$/);

    const real = message('m2', 'th1', 4411, { body: 'Hello' });
    FakeEventSource.last.feed(messageFrame(901, real));
    flushFrame();
    expect(ids()).toEqual(['m1', 'm2', 'tmp:1']);

    answer(real);
    await sent;
    expect(ids()).toEqual(['m1', 'm2']);
  });

  it('is held while the stream is offline and sent when it is live again', async () => {
    queryClient.setQueryData<Timeline>(timeline, { messages: [] });
    window.dispatchEvent(new Event('offline'));
    expect(liveStatus()).toBe('offline');
    const real = message('m2', 'th1', 4411, { body: 'Hello' });
    const send = vi.fn((_key: string) => Promise.resolve<MessageRow>(real));
    const sent = sendMessage(send, 'hold');
    expect(ids()).toEqual(['tmp:1']);
    expect(heldCount()).toBe(1);

    window.dispatchEvent(new Event('online'));
    expect(send).not.toHaveBeenCalled(); // reconnecting is not live yet
    FakeEventSource.last.feed(opening(905, 900));
    await sent;
    expect(send).toHaveBeenCalledTimes(1);
    expect(heldCount()).toBe(0);
    expect(ids()).toEqual(['m2']);
  });
});
