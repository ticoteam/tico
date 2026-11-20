import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { followSend, SendMark } from './sending';

/* After Approve on an email: the toast counts down the undo window, then says Sent or Not sent; the email's quiet line
   says Sending, then Sent. The send's state is stubbed; the clock is held still and moved by hand. */

const NOW = Date.parse('2026-11-19T13:20:00Z');
let send: Record<string, unknown> = {};
const base = { id: 's1', mailbox: 'legal@acme.example', recipients: ['dexter@acme.example'], attachments: [], detail: '', finished_at: null };

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  vi.stubGlobal('fetch', async (url: string) => {
    const body = String(url).endsWith('/api/v2/email/sends/s1') ? { send: { ...base, ...send }, body: '', bodies: [], preview_complete: true } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const settle = (state: Record<string, unknown>) => {
  send = state;
  return act(() => queryClient.invalidateQueries({ queryKey: ['email-send-state', 's1'] }));
};

function shown(node = <Toasts />) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('the toast after Approve on an email', () => {
  it('counts down the undo window with Undo, then says Sent', async () => {
    send = { state: 'pending', send_after: '2026-11-19T13:21:58Z' };
    shown();
    let stop = () => {};
    act(() => {
      stop = followSend({ send: 's1', to: ['Dexter Moss <dexter@acme.example>'], task: 't1', answer: 'a1' });
    });
    expect(await screen.findByText('Sending to dexter@acme.example in 1:58')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    vi.setSystemTime(NOW + 1000);
    expect(await screen.findByText('Sending to dexter@acme.example in 1:57')).toBeTruthy();
    await settle({ state: 'sent', send_after: '2026-11-19T13:21:58Z', finished_at: '2026-11-19T13:22:01Z' });
    expect(await screen.findByText('Sent to dexter@acme.example')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
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
    await settle({ state: 'failed', send_after: '2026-11-19T13:21:58Z', detail: 'Gmail refused the message' });
    const line = await screen.findByText('Not sent: Gmail refused the message');
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    // Base UI keeps the close mark out of the reading order until the toast is focused or hovered.
    fireEvent.click(document.querySelector('.toasts button[aria-label="Dismiss"]') as HTMLElement);
    await waitFor(() => expect(line.isConnected).toBe(false));
    stop();
  });
});

describe('the quiet line where the email shows', () => {
  it('says Sending with the time left, then Sent at the time it went', async () => {
    send = { state: 'pending', send_after: '2026-11-19T13:21:40Z' };
    shown(<SendMark send="s1" approved />);
    expect(await screen.findByText('Sending · 1:40')).toBeTruthy();
    const at = '2026-11-19T13:21:41Z';
    await settle({ state: 'sent', send_after: '2026-11-19T13:21:40Z', finished_at: at });
    const time = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date(at));
    expect(await screen.findByText(`Sent ${time}`)).toBeTruthy();
  });

  it('shows nothing while the email still waits on its OK', async () => {
    send = { state: 'waiting_approval', send_after: '2026-11-19T13:00:00Z' };
    const { container } = shown(<SendMark send="s1" />);
    await waitFor(() => expect(queryClient.getQueryData(['email-send-state', 's1'])).toBeTruthy());
    expect(container.textContent).toBe('');
  });
});
