import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Diagnostics } from './Diagnostics';
import { noteFailure } from './failures';

/* The tier 2 gap of Tico support's diagnostics (Settings row s1125), closed. Plain sample data only. */

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let read: string[] = [];
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') read.push(String(url));
    else sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method) ?? { status: 200, body: {} };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown): Reply => ({ status: 200, body });

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  read = [];
  queryClient.clear();
});
afterEach(() => vi.unstubAllGlobals());

// ---------------------------------------------------------------- Diagnostics

describe('Tico support › Diagnostics @s1125', () => {
  const bundle = { id: 'd1', bytes: 100, text: JSON.stringify({ format: 1, computers: { a: 1 }, logs: ['x'] }, null, 2) };
  const open = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <Diagnostics open onOpenChange={() => {}} attached={null} onAttach={() => {}} />
      </QueryClientProvider>,
    );

  it('an edit stays in the text; Refresh captures again @s1125', async () => {
    serve((url) => (url.startsWith('/api/v2/support/diagnostics') ? ok(bundle) : undefined));
    open();
    const text = (await screen.findByRole('textbox', { name: 'Diagnostics' })) as HTMLTextAreaElement;
    await waitFor(() => expect(JSON.parse(text.value)).toHaveProperty('logs'));
    fireEvent.change(text, { target: { value: JSON.stringify({ format: 1, computers: { a: 1 } }) } });
    expect(JSON.parse(text.value)).not.toHaveProperty('logs');
    read = [];
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(read.some((u) => u.startsWith('/api/v2/support/diagnostics'))).toBe(true));
    await waitFor(() => expect(JSON.parse(text.value)).toHaveProperty('logs'));
  });

  it("what failed in this browser goes with the capture, with no message or address @s1125", async () => {
    serve((url) => (url.startsWith('/api/v2/support/diagnostics') ? ok(bundle) : undefined));
    act(() => noteFailure('error', { filename: 'https://app.example.test/assets/main-abc.js', lineno: 12, colno: 4 }));
    open();
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/support/diagnostics/capture', method: 'POST' }));
    const body = sent.at(-1)?.body as { browser: { file: string; line: number; kind: string }[]; performance_hours: number };
    expect(body.performance_hours).toBe(24);
    expect(body.browser[0]).toMatchObject({ kind: 'error', file: 'main-abc.js', line: 12 });
    expect(JSON.stringify(body.browser)).not.toContain('example.test');
  });
});
