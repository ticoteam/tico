import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
// eslint-disable-next-line no-restricted-imports -- the test plays the server's stream, as the shell's startLive opens it
import { startLive, stopLive } from '@/live';
// eslint-disable-next-line no-restricted-imports -- the test plays the server's stream, as the shell's startLive opens it
import { FakeEventSource, opening, task, taskFrame } from '@/live/fake-stream';
// eslint-disable-next-line no-restricted-imports -- the test plays the server's stream, as the shell's startLive opens it
import { flushFrame } from '@/live/frame';
import { resetFollows } from '@/live/follow';
import { followSend, markLine, rowWhere, SendMark, type SendWhere } from './sending';

/* After Approve on an email: the toast counts down the undo window, then says Sent or Not sent; the email's quiet line
   says Sending, then Sent. The send is read once; each move after that comes on the live stream as its tasks' rows
   (`email_send`), never by reading the send again. The clock is held still and moved by hand. */

const NOW = Date.parse('2026-11-19T13:20:00Z');
let send: Record<string, unknown> = {};
let reads = 0;
let seq = 900;
const base = { id: 's1', mailbox: 'legal@acme.example', recipients: ['dexter@acme.example'], attachments: [], detail: '', finished_at: null };

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  reads = 0;
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  vi.stubGlobal('fetch', async (url: string) => {
    const mine = String(url).endsWith('/api/v2/email/sends/s1');
    if (mine) reads++;
    const body = mine ? { send: { ...base, ...send }, body: '', bodies: [], preview_complete: true } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  startLive();
  FakeEventSource.last.feed(opening(seq));
});
afterEach(() => {
  stopLive();
  resetFollows();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** The send moved: the server sends the task its OK sits on again, with the send's newest state. */
const moved = (state: Record<string, unknown>) =>
  act(() => {
    seq += 1;
    FakeEventSource.last.feed(taskFrame(seq, task('t1', { email_send: { ...base, ...state } })));
    flushFrame();
  });

/** The stream has reconnected to follow the task (`t1`) the toast follows the send by. */
async function following() {
  await waitFor(() => expect(FakeEventSource.last.params.get('task')).toBe('t1'));
  FakeEventSource.last.feed(opening(seq, seq, ['tasks']));
}

function shown(node = <Toasts />) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('the toast after Approve on an email', () => {
  it('counts down the undo window with Undo, then says Sent when the stream says it went', async () => {
    send = { state: 'pending', send_after: '2026-11-19T13:21:58Z' };
    shown();
    let stop = () => {};
    act(() => {
      stop = followSend({ send: 's1', to: ['Dexter Moss <dexter@acme.example>'], task: 't1', answer: 'a1' });
    });
    expect(await screen.findByText('Sending to dexter@acme.example in 1:58')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    // The stream follows the task its OK sits on while the toast follows the send.
    await following();
    vi.setSystemTime(NOW + 1000);
    expect(await screen.findByText('Sending to dexter@acme.example in 1:57')).toBeTruthy();
    await moved({ state: 'sent', send_after: '2026-11-19T13:21:58Z', finished_at: '2026-11-19T13:22:01Z' });
    expect(await screen.findByText('Sent to dexter@acme.example')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(reads).toBe(1);
    stop();
  });

  it('says Not sent with why, and stays until dismissed', async () => {
    send = { state: 'pending', send_after: '2026-11-19T13:21:58Z' };
    shown();
    let stop = () => {};
    act(() => {
      stop = followSend({ send: 's1', to: ['dexter@acme.example', 'ana@acme.example'], task: 't1', answer: 'a1' });
    });
    expect(await screen.findByText('Sending to dexter@acme.example +1 in 1:58')).toBeTruthy();
    await following();
    await moved({ state: 'failed', send_after: '2026-11-19T13:21:58Z', detail: 'Gmail refused the message' });
    const line = await screen.findByText('Not sent: Gmail refused the message');
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    // Base UI keeps the close mark out of the reading order until the toast is focused or hovered.
    fireEvent.click(document.querySelector('.toasts button[aria-label="Dismiss"]') as HTMLElement);
    await waitFor(() => expect(line.isConnected).toBe(false));
    expect(reads).toBe(1);
    stop();
  });
});

describe('the quiet line where the email shows', () => {
  it('says Sending with the time left, then Sent at the time the stream says it went', async () => {
    send = { state: 'pending', send_after: '2026-11-19T13:21:40Z' };
    shown(<SendMark send="s1" />);
    expect(await screen.findByText('Sending · 1:40')).toBeTruthy();
    const at = '2026-11-19T13:21:41Z';
    await moved({ state: 'sent', send_after: '2026-11-19T13:21:40Z', finished_at: at });
    const time = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date(at));
    expect(await screen.findByText(`Sent ${time}`)).toBeTruthy();
    expect(reads).toBe(1);
  });

  it('shows nothing while the email still waits on its OK', async () => {
    send = { state: 'waiting_approval', send_after: '2026-11-19T13:00:00Z' };
    const { container } = shown(<SendMark send="s1" />);
    await waitFor(() => expect(queryClient.getQueryData(['email-send-state', 's1'])).toBeTruthy());
    expect(container.textContent).toBe('');
  });

  it('shows nothing for a canceled send: an agent cancels one to rewrite it, nothing failed', async () => {
    send = { state: 'canceled', send_after: '2026-11-19T13:00:00Z', finished_at: '2026-11-19T13:10:00Z' };
    const { container } = shown(<SendMark send="s1" />);
    await waitFor(() => expect(queryClient.getQueryData(['email-send-state', 's1'])).toBeTruthy());
    expect(container.textContent).toBe('');
  });
});

describe("a task row's send", () => {
  const row = { id: 's1', send_after: '2026-11-19T13:00:00Z', detail: '' };

  it('is shown on its way and for a day after it ended, never while it waits on an OK', () => {
    expect(rowWhere({ ...row, state: 'waiting_approval', finished_at: null }, NOW)).toBeNull();
    expect(rowWhere({ ...row, state: 'pending', finished_at: null }, NOW)?.kind).toBe('sending');
    expect(rowWhere({ ...row, state: 'sent', finished_at: '2026-11-19T05:23:00Z' }, NOW)?.kind).toBe('sent');
    expect(rowWhere({ ...row, state: 'sent', finished_at: '2026-11-17T05:23:00Z' }, NOW)).toBeNull();
  });

  it('held past its undo window for its matter’s day, it says that day, never Sending', () => {
    const held = { ...row, state: 'pending' as const, finished_at: null, not_before: '2026-12-08T00:00:00-08:00' };
    const w = rowWhere(held, NOW);
    expect(w).toEqual({ kind: 'held', on: '2026-12-08T00:00:00-08:00' });
    expect(markLine(w as SendWhere, NOW)).toBe('Sends Dec 8');
  });

  it('says nothing once canceled, and Not sent only when it failed, was blocked, or is unsure', () => {
    const ended = '2026-11-19T13:10:00Z';
    expect(rowWhere({ ...row, state: 'canceled', finished_at: ended }, NOW)).toBeNull();
    expect(rowWhere({ ...row, state: 'failed', finished_at: ended, detail: 'Gmail refused the message' }, NOW)).toEqual({ kind: 'not_sent', detail: 'Gmail refused the message' });
    expect(rowWhere({ ...row, state: 'blocked', finished_at: ended }, NOW)?.kind).toBe('not_sent');
    expect(rowWhere({ ...row, state: 'unknown', finished_at: ended }, NOW)?.kind).toBe('unsure');
  });
});
