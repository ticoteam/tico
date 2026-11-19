import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { Conversation } from './Conversation';

vi.mock('@/live', async (actual) => ({ ...(await actual<Record<string, unknown>>()), useFollow: () => {} }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

const T = 't1';
const scout: Actor = { id: 'bot:0190-sc', kind: 'agent', name: 'Scout', slug: 'scout' };

beforeEach(() => {
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['thread', T, 'timeline'], { pages: [{ messages: [], next_cursor: null, read_marks: {} }], pageParams: [null] });
  queryClient.setQueryData(directoryQuery.queryKey, { actors: [scout], byId: { [scout.id]: scout } });
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

const show = (actor: string | undefined, title: string) =>
  render(
    <QueryClientProvider client={queryClient}>
      <Conversation id={T} variant="side" title={title} actor={actor} page={null} />
    </QueryClientProvider>,
  );

describe('a chat head', () => {
  it("links an agent's name to its page, by slug", () => {
    show(scout.id, 'Scout');
    expect(screen.getByRole('link', { name: 'Scout' }).getAttribute('href')).toBe('/team/scout');
  });

  it("links a person's name to their page, by id", () => {
    show('human:p7', 'Kim Roe');
    expect(screen.getByRole('link', { name: 'Kim Roe' }).getAttribute('href')).toBe('/team/p7');
  });

  it('draws a plain name when the chat names no teammate', () => {
    show(undefined, 'Launch');
    expect(screen.getByText('Launch')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Launch' })).toBeNull();
  });
});
