import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { HELD_EVERY_MS, QuestionCard } from './QuestionCard';

/* A ❓ answered in place (backend/question_marks.py): the agent's question, the answers it offers as buttons (its pick
   the filled one), and a reply box. A tap posts that answer on the task's chat, naming the ❓ (`ask`), and the card
   goes; a ❓ answered already says so; a reply a company rule held keeps the card, marked Held. An old free-text ❓
   is the same card with the reply box alone. Made-up rows only. */

// Read with node's fs (tests load no CSS), as requests.test.tsx does.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url;

type Reply = { status?: number; body: unknown };
const sent: { url: string; body: Record<string, unknown> }[] = [];
let reply: () => Reply = () => ({ body: { comment: { id: 'm9' } } });
let hold: Record<string, unknown> = {};

beforeEach(() => {
  sent.length = 0;
  reply = () => ({ body: { comment: { id: 'm9' } } });
  hold = { id: 'h1', state: 'held' };
  queryClient.clear();
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if ((init?.method ?? 'GET') !== 'GET') sent.push({ url: String(url), body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown> });
    if (String(url).includes('/rule-holds/')) return new Response(JSON.stringify(hold), { status: 200, headers: { 'Content-Type': 'application/json' } });
    const r = reply();
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

const show = (choices?: string[]) =>
  render(
    <QueryClientProvider client={queryClient}>
      <QuestionCard id="qmark:q1" task="t1" line="❓ Switch the HeyGen check to the new address?" choices={choices} who="bot:gm" />
    </QueryClientProvider>,
  );

const card = () => screen.getByRole('region', { name: 'Question' });
// The card's first read of its hold has come back. A refetch while that read is in flight joins it rather than reading
// again, so a decision made before it lands is never seen.
const heldRead = () => waitFor(() => expect(queryClient.getQueryData(['rules', 'holds', 'one', 'h1'])).toEqual({ id: 'h1', state: 'held' }));

describe('QuestionCard', () => {
  it('shows the offered answers and a tap answers the question on its task', async () => {
    show(['Yes', 'Not now']);
    const box = card();
    expect(within(box).getByText('Switch the HeyGen check to the new address?')).toBeTruthy();
    const buttons = within(box).getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Not now', 'Yes']); // the agent's pick last, filled
    fireEvent.click(within(box).getByRole('button', { name: 'Yes' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question' })).toBeNull());
    expect(sent).toEqual([{ url: '/api/v2/tasks/t1/comments', body: { text: 'Yes', ask: 'qmark:q1' } }]);
  });

  it('shows an old free-text question with the reply box alone, which answers it', async () => {
    show();
    const box = card();
    expect(within(box).getByText('Switch the HeyGen check to the new address?')).toBeTruthy();
    expect(within(box).queryAllByRole('button')).toEqual([]);
    fireEvent.change(within(box).getByRole('textbox', { name: 'Reply' }), { target: { value: 'Yes, switch it.' } });
    fireEvent.click(within(box).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sent.map((s) => s.body)).toEqual([{ text: 'Yes, switch it.', ask: 'qmark:q1' }]));
  });

  it('says so when the question was answered already', async () => {
    reply = () => ({ status: 409, body: { error: { code: 'answered', detail: 'Already answered' } } });
    show(['Yes']);
    fireEvent.click(within(card()).getByRole('button', { name: 'Yes' }));
    expect(await within(card()).findByRole('alert')).toBeTruthy();
    expect(card().textContent).toContain('Already answered');
  });

  it('keeps the card, marked Held, when a company rule held the reply, drawn again too', async () => {
    reply = () => ({ body: { held: { id: 'h1', reason: 'Waiting on a reviewer' } } });
    const first = show(['Yes']);
    fireEvent.click(within(card()).getByRole('button', { name: 'Yes' }));
    expect(await within(card()).findByText('Held')).toBeTruthy();
    expect(within(card()).getByText('Waiting on a reviewer')).toBeTruthy();
    expect(within(card()).queryByRole('button')).toBeNull(); // nothing to tap again while it waits
    first.unmount(); // the Chat tab left and come back to
    show(['Yes']);
    expect(within(card()).getByText('Held')).toBeTruthy();
    expect(within(card()).queryByRole('button')).toBeNull();
    await heldRead(); // else the refetch below joins that first read, which says held
    hold = { id: 'h1', state: 'sent' };
    await queryClient.refetchQueries({ queryKey: ['rules', 'holds', 'one', 'h1'] });
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Question' })).toBeNull());
  });

  it('sends a typed reply on Enter, never on the Enter that picks a word while composing', async () => {
    show(['Yes']);
    const box = within(card()).getByRole('textbox', { name: 'Reply' });
    fireEvent.change(box, { target: { value: 'はい' } });
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true });
    expect(sent).toEqual([]);
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(sent.map((s) => s.body)).toEqual([{ text: 'はい', ask: 'qmark:q1' }]));
  });

  it('brings its answers back when the reviewer keeps the held reply blocked', async () => {
    reply = () => ({ body: { held: { id: 'h1', reason: 'Waiting on a reviewer' } } });
    show(['Yes']);
    fireEvent.click(within(card()).getByRole('button', { name: 'Yes' }));
    expect(await within(card()).findByText('Held')).toBeTruthy();
    // read again only at the five-minute backstop while it waits (no push carries a hold's decision). The card's read
    // subscribes in an effect after the Held mark is drawn, so wait for it.
    const interval = () => queryClient.getQueryCache().find({ queryKey: ['rules', 'holds', 'one', 'h1'] })?.observers[0]?.options.refetchInterval;
    await waitFor(() => expect(interval()).toBe(5 * 60_000));
    expect(HELD_EVERY_MS).toBe(5 * 60_000);
    await heldRead();
    hold = { id: 'h1', state: 'kept' };
    await queryClient.refetchQueries({ queryKey: ['rules', 'holds', 'one', 'h1'] });
    expect(await within(card()).findByRole('button', { name: 'Yes' })).toBeTruthy();
    expect(within(card()).getByRole('alert').textContent).toContain('Kept blocked');
  });

  it('wraps a long question and answer inside the card rather than clipping them, and shows focus', () => {
    const style = document.createElement('style');
    style.textContent = ['../../styles/parts.css', './ask.css'].map((f) => fs.readFileSync(new URL(f, here), 'utf8')).join('\n');
    document.head.append(style);
    try {
      const long = 'Yes, switch it now and tell the Growth Manager once it passes';
      show([long.slice(0, 60)]);
      const css = getComputedStyle(within(card()).getByRole('button', { name: long.slice(0, 60) }));
      expect([css.whiteSpace, css.height, css.maxWidth]).toEqual(['normal', 'auto', '100%']);
      expect(getComputedStyle(within(card()).getByText('Switch the HeyGen check to the new address?')).whiteSpace).toBe('normal');
      // the app's focus ring (parts.css :focus-visible) is never reset on the reply box
      expect(/\.askcard-reply\{[^}]*\}/.exec(style.textContent)?.[0]).not.toContain('outline');
    } finally {
      style.remove();
    }
  });
});
