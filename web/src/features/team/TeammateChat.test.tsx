import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { directoryQuery, queryClient, type Actor } from '@/data';
import { NO_SIDEBAR_SKILL, TeammateChat } from './TeammateChat';

vi.mock('@/conversation', async () => ({
  timelineQuery: (await import('@/conversation/queries')).timelineQuery,
  Conversation: ({ id, mentionOnly, starters, foot }: { id: string; mentionOnly?: readonly string[]; starters?: readonly string[]; foot?: ReactNode }) => (
    <>
      <div data-testid="conversation" data-mention-only={mentionOnly?.join(' ')} data-starters={starters?.join('|')}>
        {id}
      </div>
      {foot}
    </>
  ),
}));

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});

const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
let asked: string[] = [];
let timeline: unknown[] = [];
let answerMe: () => void = () => {};

beforeEach(() => {
  queryClient.clear();
  asked = [];
  timeline = [];
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
    if (u.includes('/threads/th-1/timeline')) return Response.json({ messages: timeline, next_cursor: null });
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
    const chat = await screen.findByTestId('conversation');
    expect(chat.textContent).toBe('th-1');
    expect(asked.find((u) => u.includes('/threads/dm'))).toContain('with=bot%3A0190-ana');
    // its @ list offers the teammate alone: the server refuses an @ of anyone outside a direct chat
    expect(chat.getAttribute('data-mention-only')).toBe('bot:0190-ana');
  });

  it("offers an agent's empty chat two starters", async () => {
    show('ana');
    await act(async () => {
      answerMe();
    });
    const chat = await screen.findByTestId('conversation');
    expect(chat.getAttribute('data-starters')).toBe('Add a widget showing…|What did you make this week?');
  });

  it('offers no widget starter to an agent whose card leaves out the sidebar skill (Agent Ops)', async () => {
    const ops: Actor = { id: 'bot:0190-ops', kind: 'agent', name: 'Agent Ops', slug: 'agent-ops', template: 'agent-ops' };
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [pat, ops], byId: { 'human:p1': pat, p1: pat, 'bot:0190-ops': ops, 'agent-ops': ops } });
    show('agent-ops');
    await act(async () => {
      answerMe();
    });
    const chat = await screen.findByTestId('conversation');
    expect(chat.getAttribute('data-starters')).toBe('What did you make this week?');
  });

  it('knows the very templates whose card leaves out the sidebar skill', async () => {
    const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as {
      readdirSync(dir: URL): string[];
      readFileSync(file: URL, encoding: 'utf8'): string;
    };
    const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
    const catalog = new URL('../../../../templates/catalog/', here);
    const card = (t: string) => {
      try {
        return fs.readFileSync(new URL(`${t}/card.yaml`, catalog), 'utf8');
      } catch {
        return '';
      }
    };
    // `base_skip:` and its list, inline or one item a line
    const skips = (t: string) => /^base_skip:(.*(?:\n[ \t]+-.*)*)/m.exec(card(t))?.[1]?.includes('arrange-your-sidebar') ?? false;
    const skipping = fs.readdirSync(catalog).filter(skips);
    expect(skipping.length).toBeGreaterThan(0);
    expect(new Set(skipping)).toEqual(NO_SIDEBAR_SKILL);
  });

  it("pins the agent's question to me over the box, with the answers it offers", async () => {
    timeline = [
      { id: 'm1', seq: 1, thread_id: 'th-1', sender_id: 'bot:0190-ana', body: 'Ship the launch note Friday?', refs: { question: { id: 'q1', to: 'human:p1', choices: ['Yes', 'Next week'] } } },
    ];
    show('ana');
    await act(async () => {
      answerMe();
    });
    const card = await screen.findByRole('region', { name: 'Question' });
    expect(within(card).getByText('Ship the launch note Friday?')).toBeTruthy();
    expect(within(card).getAllByRole('button').map((b) => b.textContent)).toEqual(['Next week', 'Yes']);
  });
});
