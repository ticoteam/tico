import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { queryClient } from '@/data';
import type { TicoDesktop } from '@/desktop';
import { installNotices, noticesSettled, resetNotices } from './notices';
import { dispatch } from './patch';

// System notifications (platforms.md 4.7): a new ask and a ring, only while the page is out of sight, once each.
// No real Notification: a stand-in records what would show.

const { go } = vi.hoisted(() => ({ go: vi.fn(async (_id: string, _input: unknown) => ({ ok: true })) }));
vi.mock('@/actions', () => ({ run: go }));

class FakeNote {
  static permission: NotificationPermission = 'granted';
  static shown: FakeNote[] = [];
  onclick: (() => void) | null = null;
  close = vi.fn();
  constructor(
    public title: string,
    public opts: NotificationOptions,
  ) {
    FakeNote.shown.push(this);
  }
}

let hidden = true;
const ask = (id: string, more: Record<string, unknown> = {}) => ({ id, kind: 'ask', line: `Approve the plan ${id}`, since: null, task: { id: `t-${id}`, number: 12 }, ...more });
let list: ReturnType<typeof ask>[] = [];
const titles = () => FakeNote.shown.map((n) => n.title);

async function needs(...changed: string[]) {
  dispatch('needs', [{ changed }]);
  await noticesSettled();
}

beforeAll(() => installNotices());

beforeEach(() => {
  FakeNote.shown = [];
  FakeNote.permission = 'granted';
  hidden = true;
  list = [];
  vi.stubGlobal('Notification', FakeNote);
  vi.spyOn(window, 'focus').mockImplementation(() => undefined);
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  vi.spyOn(document, 'hasFocus').mockImplementation(() => !hidden);
  vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
    const ids = new URL(path, 'http://x').searchParams.get('ids');
    return { actor: 'human:p1', items: ids ? list.filter((it) => ids.split(',').includes(it.id)) : list } as never;
  });
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'p1@example.com' });
  queryClient.setQueryData(['settings', 'humans'], [{ id: 'p1', notify_browser: true }]);
  queryClient.setQueryData(['directory'], { actors: [], byId: { 'human:ann': { id: 'human:ann', name: 'Ann' } } });
});

afterEach(() => {
  resetNotices();
  queryClient.clear();
  localStorage.clear();
  delete (window as { tico?: TicoDesktop }).tico;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  go.mockReset();
});

describe('asks', () => {
  it('notifies a new ask once, never what already waited', async () => {
    list = [ask('old'), ask('a1')];
    await needs('a1');
    expect(titles()).toEqual(['Needs you']);
    expect(FakeNote.shown[0]!.opts).toMatchObject({ body: 'Approve the plan a1', tag: 'need-a1' });
    // both change again later: neither is new
    await needs('old', 'a1');
    expect(FakeNote.shown).toHaveLength(1);
  });

  it('stays quiet while the page is in front, and that item never notifies later', async () => {
    hidden = false;
    list = [ask('a1')];
    await needs('a1');
    hidden = true;
    await needs('a1');
    expect(FakeNote.shown).toHaveLength(0);
  });

  it('not for the person’s own ask, an @, or with the switch off', async () => {
    list = [ask('mine', { ask: { asked_by: 'human:p1' } }), ask('at', { kind: 'mention' })];
    await needs('mine', 'at');
    expect(FakeNote.shown).toHaveLength(0);

    queryClient.setQueryData(['settings', 'humans'], [{ id: 'p1', notify_browser: false }]);
    list = [ask('a2')];
    await needs('a2');
    expect(FakeNote.shown).toHaveLength(0);
  });

  it('a click opens the task in Flow', async () => {
    list = [ask('a1')];
    await needs('a1');
    FakeNote.shown[0]!.onclick!();
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith('shell.goTo', { to: '/flow/12' }, { via: 'click' }));
  });
});

describe('rings', () => {
  const call = (id: string, connector = 'human:ann') => ({ id, connector, ring: true, state: 'live', title: 'Weekly plan' });

  it('notifies a call ringing the person, and takes it away when the ring stops', async () => {
    queryClient.setQueryData(['calls', 'ringing'], { calls: [call('c1'), call('c2', 'human:p1')] });
    await vi.waitFor(() => expect(titles()).toEqual(['Ann']));
    expect(FakeNote.shown[0]!.opts).toMatchObject({ body: 'Weekly plan', tag: 'call-c1' });
    queryClient.setQueryData(['calls', 'ringing'], { calls: [call('c1')] });
    expect(FakeNote.shown).toHaveLength(1);
    queryClient.setQueryData(['calls', 'ringing'], { calls: [] });
    expect(FakeNote.shown[0]!.close).toHaveBeenCalled();
  });

  it('nothing without permission', async () => {
    FakeNote.permission = 'default';
    queryClient.setQueryData(['calls', 'ringing'], { calls: [call('c1')] });
    await Promise.resolve();
    expect(FakeNote.shown).toHaveLength(0);
  });
});

describe('in the desktop app', () => {
  it('puts the company first with two companies, and a click brings that company forward, then opens the address', async () => {
    const order: string[] = [];
    (window as { tico?: TicoDesktop }).tico = {
      companies: {
        list: async () => [
          { id: 'co1', name: 'Fernhill', self: true },
          { id: 'co2', name: 'Other', self: false },
        ],
        switchTo: async (id: string) => void order.push(`switch ${id}`),
      },
    } as unknown as TicoDesktop;
    go.mockImplementation(async (_id: string, input: unknown) => {
      order.push(`go ${(input as { to: string }).to}`);
      return { ok: true };
    });
    list = [ask('a1')];
    await needs('a1');
    expect(titles()).toEqual(['Fernhill · Needs you']);
    FakeNote.shown[0]!.onclick!();
    await vi.waitFor(() => expect(order).toEqual(['switch co1', 'go /flow/12']));
  });

  it('one company: the title alone', async () => {
    (window as { tico?: TicoDesktop }).tico = {
      companies: { list: async () => [{ id: 'co1', name: 'Fernhill', self: true }], switchTo: async () => undefined },
    } as unknown as TicoDesktop;
    list = [ask('a1')];
    await needs('a1');
    expect(titles()).toEqual(['Needs you']);
  });
});
