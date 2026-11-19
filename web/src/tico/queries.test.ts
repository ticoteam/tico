import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { FakeEventSource, frame, opening } from '@/live/fake-stream';
import { flushFrame } from '@/live/frame';
import { startLive, stopLive } from '@/live';
import { ticoPowerQuery, type TicoPower } from './queries';

const off: TicoPower = { available: false, bot: 'assistant', can_turn_on: true, state: 'archived' };
const stale = () => queryClient.getQueryState(ticoPowerQuery.queryKey)?.isInvalidated;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  startLive();
  FakeEventSource.last.feed(opening(900));
  queryClient.setQueryData(ticoPowerQuery.queryKey, off);
});

afterEach(() => {
  stopLive();
  queryClient.clear();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Tico's power follows the server", () => {
  it('reads the power again when the stream says the Assistant changed, whatever status it carries', () => {
    // a status line that looks "on" is not trusted: the power is read again, not set from it
    FakeEventSource.last.feed(frame(901, 'bots', { bot: 'assistant', status: { state: 'active' } }));
    flushFrame();
    expect(stale()).toBe(true);
    expect(queryClient.getQueryData(ticoPowerQuery.queryKey)).toEqual(off);
  });

  it('leaves it alone when another agent changed', () => {
    FakeEventSource.last.feed(frame(901, 'bots', { bot: 'sales', status: null }));
    flushFrame();
    expect(stale()).toBe(false);
  });

  it("follows the person's own Assistant when it is not named assistant", () => {
    queryClient.setQueryData(ticoPowerQuery.queryKey, { ...off, bot: 'ana' });
    FakeEventSource.last.feed(frame(901, 'bots', { bot: 'assistant', status: null }));
    flushFrame();
    expect(stale()).toBe(false);
    FakeEventSource.last.feed(frame(902, 'bots', { bot: 'ana', status: null }));
    flushFrame();
    expect(stale()).toBe(true);
  });
});
