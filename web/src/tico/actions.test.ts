// The app's boot order: every action registered before a feature's actions import them.
import '@/actions';
import { createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import './actions';
import { ticoPowerQuery, type TicoPower } from './queries';

const on: TicoPower = { available: true, bot: 'assistant', can_turn_on: true, state: 'active' };
let sent: { url: string; body: unknown }[] = [];

beforeEach(() => {
  sent = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') sent.push({ url: String(url), body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify({ id: 'm1', body: 'x' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  queryClient.setQueryData(ticoPowerQuery.queryKey, on);
  queryClient.setQueryData(ticoRoomQuery.queryKey, 'room1');
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Ask Tico', () => {
  it('says which page it was asked from as from_page, with the file open over it', async () => {
    const root = createRootRoute();
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/library/files?file=f1'] }) });
    await router.load();
    bind({ router });
    await run('tico.ask', { text: 'Who made this?' }, { via: 'click' });
    const ask = sent.find((s) => s.url === '/api/v2/assistant/messages');
    expect(ask?.body).toEqual({ text: 'Who made this?', from_page: { page: 'docs', label: 'Files', item: { kind: 'file', id: 'f1' } } });
  });
});
