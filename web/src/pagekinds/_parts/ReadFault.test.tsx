import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data/query-client';
import { FakeEventSource, opening } from '@/live/fake-stream';
import { startLive, stopLive } from '@/live/stream';
import { Button } from '@/kit/Button';
import { List } from '@/pagekinds/List';
import { ReadFault } from './ReadFault';
import { showPage } from './test-page';

/* A read that failed with nothing to show, on any page (the page kinds' fault state) or in a part of one: one line that
   says why, read again by itself once the stream is back when that could help, and Try again only when it could not. */

const starting = () => new ApiError(503, 'starting', 'Tico is restarting', true);

/** A page whose one read failed with `error`. */
const page = (error: unknown, refetch: () => unknown) => (
  <List
    title="Tasks"
    path="/tasks"
    actions={
      <Button variant="primary" onClick={() => {}}>
        Add task
      </Button>
    }
    state="fault"
    reads={[{ error, refetch }]}
    sections={null}
  />
);

beforeEach(() => {
  FakeEventSource.all = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  // the stream's "who is this" on a resumed stream: the same person
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ actor: 'human:a', email: '', role: 'human' })));
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  stopLive();
  queryClient.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('a page whose read failed', () => {
  it('says why in its one line, and a restart has no Try again: it is read again by itself', async () => {
    const main = await showPage(page(starting(), () => undefined), '/tasks');
    expect(within(main).getByRole('heading', { name: "Tasks didn't load · server restarting" })).toBeTruthy();
    expect(within(main).queryByRole('button', { name: 'Try again' })).toBeNull();
    // no Try again in the body, so the head keeps its filled button
    expect(within(main.querySelector('header')!).getByRole('button', { name: 'Add task' }).className).toContain('btn-fill');
  });

  it('is read again once the stream that dropped is back', async () => {
    const refetch = vi.fn();
    startLive();
    FakeEventSource.last.feed(opening(900));
    await showPage(page(starting(), refetch), '/tasks');
    expect(refetch).not.toHaveBeenCalled();
    FakeEventSource.last.fail();
    await vi.waitFor(() => expect(FakeEventSource.all.length).toBeGreaterThan(1));
    FakeEventSource.last.feed(opening(905, 900));
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    // joining a read already on its way, never cancelling it for another
    expect(refetch).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  it.each([
    [403, 'forbidden', 'no access'],
    [404, 'not_found', 'not found'],
  ])('keeps Try again for a %i, which reading again by itself would not fix', async (status, code, reason) => {
    const refetch = vi.fn();
    startLive();
    const main = await showPage(page(new ApiError(status, code, 'No.'), refetch), '/tasks');
    expect(within(main).getByRole('heading', { name: `Tasks didn't load · ${reason}` })).toBeTruthy();
    // the stream coming up reads nothing again
    FakeEventSource.last.feed(opening(900));
    expect(refetch).not.toHaveBeenCalled();
    // Try again is the page's one filled button, so the head's draws plain
    const again = within(main).getByRole('button', { name: 'Try again' });
    expect(again.className).toContain('btn-fill');
    expect(within(main.querySelector('header')!).getByRole('button', { name: 'Add task' }).className).not.toContain('btn-fill');
    fireEvent.click(again);
    expect(refetch).toHaveBeenCalledWith({ cancelRefetch: true });
  });
});

describe('a part of a page whose read failed', () => {
  it('says why in a line, with Try again only for what reading again by itself would not fix', () => {
    const { rerender } = render(<ReadFault what="Requesters" reads={[{ error: new ApiError(500, 'internal', 'Down', true), refetch: () => undefined }]} inline />);
    expect(screen.getByText("Requesters didn't load · server error")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    rerender(<ReadFault what="Requesters" reads={[{ error: new TypeError('x is undefined'), refetch: () => undefined }]} inline />);
    expect(screen.getByText("Requesters didn't load · app error")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('names the first read that failed, and a read paused offline (no error) as one that got no answer', () => {
    const ok = { error: null, refetch: () => undefined };
    const { rerender } = render(<ReadFault what="Sidebars" reads={[ok, { error: new ApiError(403, 'forbidden', 'No.'), refetch: () => undefined }]} />);
    expect(screen.getByRole('heading', { name: "Sidebars didn't load · no access" })).toBeTruthy();
    rerender(<ReadFault what="Sidebars" reads={[ok]} />);
    expect(screen.getByRole('heading', { name: "Sidebars didn't load · no connection" })).toBeTruthy();
  });

  it('a restart beside a refusal: the refusal is the reason, and Try again reads both', () => {
    const restart = vi.fn();
    const refused = vi.fn();
    render(
      <ReadFault
        what="Mail"
        reads={[
          { error: starting(), refetch: restart },
          { error: new ApiError(403, 'forbidden', 'No.'), refetch: refused },
        ]}
      />,
    );
    expect(screen.getByRole('heading', { name: "Mail didn't load · no access" })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(restart).toHaveBeenCalledWith({ cancelRefetch: true });
    expect(refused).toHaveBeenCalledWith({ cancelRefetch: true });
  });
});
