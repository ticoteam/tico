import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { FileCard } from './FileCard';

/* A chat's file opened full screen whose read failed: the one line that says why, as every read that failed does.
   Made-up files only; every read is refused. */

const refuse = (status: number) => vi.fn(async () => new Response(JSON.stringify({ error: { code: 'forbidden', detail: 'No.' } }), { status }));

beforeEach(() => {
  queryClient.clear();
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a chat file opened full screen whose read failed', () => {
  it.each([
    ['a sheet', 'csv', 'ledger.csv', 'text/csv'],
    ['a zip', 'zip', 'photos.zip', 'application/zip'],
  ] as const)('%s says why, with Try again for a refusal', async (_, kind, name, mime) => {
    vi.stubGlobal('fetch', refuse(403));
    render(
      <QueryClientProvider client={queryClient}>
        <FileCard kind={kind} url={`/api/v2/files/x/${name}`} name={name} mime={mime} size={4096} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name }));
    expect(await screen.findByRole('heading', { name: "This file didn't load · no access" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});
