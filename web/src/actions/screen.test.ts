import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { onTopic } from '@/live';
import { run, show } from './run';
import { actOn, screenHandler, startScreen } from './screen';
import type { ScreenEvent } from './wire';

vi.mock('./run', () => ({ run: vi.fn(), show: vi.fn() }));
vi.mock('@/live', () => ({ onTopic: vi.fn(() => () => {}) }));
vi.mock('@/api/client', () => ({ api: { post: vi.fn() } }));

/* A same-page stand-in for BroadcastChannel: delivers to every other member at once, like two tabs would. */
function bus() {
  const members = new Set<{ onmessage: ((e: MessageEvent) => void) | null }>();
  return () => {
    const me = {
      onmessage: null as ((e: MessageEvent) => void) | null,
      postMessage(data: unknown) {
        for (const m of members) if (m !== me) m.onmessage?.({ data } as MessageEvent);
      },
      close() {
        members.delete(me);
      },
    };
    members.add(me);
    return me;
  };
}

const event = (o: Partial<ScreenEvent> = {}): ScreenEvent => ({
  seq: 1,
  actor: 'assistant',
  at: '2026-11-19T10:00:00Z',
  id: 'e1',
  action: 'task.open',
  input: { task: 412 },
  done: false,
  ...o,
});

function tab(id: string, join: ReturnType<typeof bus>, front: boolean) {
  const act = vi.fn(async () => ({ ok: true, did: 'Opened #412' }));
  const post = vi.fn(async () => {});
  const h = screenHandler({ tab: id, channel: join(), inFront: () => front, visible: () => true, act, post });
  return { h, act, post };
}

beforeEach(() => vi.clearAllMocks());

describe('screen', () => {
  it('a background turn acts once, in the most recently focused tab', async () => {
    const join = bus();
    const a = tab('A', join, true);
    const b = tab('B', join, true); // B opened (and claimed) after A
    await Promise.all([a.h.handle(event()), b.h.handle(event())]);
    expect(a.act).not.toHaveBeenCalled();
    expect(b.act).toHaveBeenCalledTimes(1);

    a.h.claim(); // the person goes back to A
    await Promise.all([a.h.handle(event({ id: 'e2' })), b.h.handle(event({ id: 'e2' }))]);
    expect(a.act).toHaveBeenCalledTimes(1);
    expect(b.act).toHaveBeenCalledTimes(1);
  });

  it('posts the result once, even when the event arrives twice', async () => {
    const a = tab('A', bus(), true);
    await a.h.handle(event());
    await a.h.handle(event());
    expect(a.act).toHaveBeenCalledTimes(1);
    expect(a.post).toHaveBeenCalledTimes(1);
    expect(a.post).toHaveBeenCalledWith('e1', { ok: true, did: 'Opened #412' });
  });

  it('posts a failure as a result too', async () => {
    const a = tab('A', bus(), true);
    a.act.mockResolvedValueOnce({ ok: false, error: 'not_allowed' } as never);
    await a.h.handle(event());
    expect(a.post).toHaveBeenCalledWith('e1', { ok: false, error: 'not_allowed' });
  });

  it('a hidden tab never acts on a background turn', async () => {
    const act = vi.fn();
    const h = screenHandler({ tab: 'A', channel: null, inFront: () => true, visible: () => false, act, post: vi.fn() });
    await h.handle(event());
    expect(act).not.toHaveBeenCalled();
  });

  it('a duplicated tab (same sessionStorage) is still its own tab: one request acts once', async () => {
    const join = bus();
    vi.stubGlobal('BroadcastChannel', function BroadcastChannel() {
      return join();
    });
    vi.mocked(run).mockResolvedValue({ ok: true, did: 'Opened #412' });
    vi.mocked(api.post).mockResolvedValue({});
    const focus = vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    const stops = [startScreen(), startScreen()]; // the original, then its duplicate (which claims last)
    for (const [, handler] of vi.mocked(onTopic).mock.calls) handler([event()]);
    await vi.waitFor(() => expect(run).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(run).toHaveBeenCalledTimes(1);
    stops.forEach((stop) => stop());
    focus.mockRestore();
    vi.unstubAllGlobals();
  });

  it('done shows only; otherwise it runs as Tico', async () => {
    await actOn(event({ done: true }));
    expect(show).toHaveBeenCalledWith('task.open', { task: 412 });
    expect(run).not.toHaveBeenCalled();
    await actOn(event());
    expect(run).toHaveBeenCalledWith('task.open', { task: 412 }, { via: 'tico' });
    expect(show).toHaveBeenCalledTimes(1);
  });
});
