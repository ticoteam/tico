import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Outline } from './Outline';

/* A chat's Outline whose read failed says why, as every read that failed does. Made-up chat only. */

beforeEach(() => {
  queryClient.clear();
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the Outline', () => {
  it('says why its read failed, not "No prompts yet", and Try again reads it again', async () => {
    let asked = 0;
    vi.stubGlobal('fetch', async () => {
      asked++;
      return new Response(JSON.stringify({ error: { code: 'forbidden', detail: 'No.' } }), { status: 403, headers: { 'Content-Type': 'application/json' } });
    });
    render(
      <QueryClientProvider client={queryClient}>
        <Outline id="c1" open onOpenChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('heading', { name: "The outline didn't load · no access" })).toBeTruthy();
    expect(screen.queryByText('No prompts yet')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(asked).toBe(2));
  });
});
