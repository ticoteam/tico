import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { TeammateChat } from './TeammateChat';

vi.mock('@/conversation', () => ({ Conversation: ({ id }: { id: string }) => <div data-testid="conversation">{id}</div> }));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
let asked: string[] = [];
let answerMe: () => void = () => {};

beforeEach(() => {
  queryClient.clear();
  asked = [];
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [pat, ana], byId: { 'human:p1': pat, p1: pat, 'bot:0190-ana': ana, ana } });
  // /me answers only when the test says so, as on a slow first load.
  const me = new Promise<void>((r) => (answerMe = r));
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    asked.push(u);
    if (u.includes('/api/v2/me')) {
      await me;
      return Response.json({ actor: 'human:p1', email: '', role: 'member', slug: null });
    }
    if (u.includes('/threads/dm')) return Response.json({ id: 'th-1' });
    return Response.json({});
  });
});
afterEach(() => vi.unstubAllGlobals());

const show = (who: string) =>
  render(
    <QueryClientProvider client={queryClient}>
      <TeammateChat who={who} />
    </QueryClientProvider>,
  );

describe('TeammateChat', () => {
  it('never asks for a chat with myself, even before it knows who I am', async () => {
    show('p1');
    await act(async () => {
      await Promise.resolve();
    });
    expect(asked.some((u) => u.includes('/threads/dm'))).toBe(false);
    await act(async () => {
      answerMe();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(asked.some((u) => u.includes('/api/v2/me'))).toBe(true);
    expect(asked.some((u) => u.includes('/threads/dm'))).toBe(false);
    expect(screen.queryByTestId('conversation')).toBeNull();
    expect(screen.queryByText('The chat did not load')).toBeNull();
  });

  it('opens the direct thread with a teammate once it knows who I am @s604', async () => {
    show('ana');
    expect(asked.some((u) => u.includes('/threads/dm'))).toBe(false);
    await act(async () => {
      answerMe();
    });
    expect((await screen.findByTestId('conversation')).textContent).toBe('th-1');
    expect(asked.find((u) => u.includes('/threads/dm'))).toContain('with=bot%3A0190-ana');
  });
});
