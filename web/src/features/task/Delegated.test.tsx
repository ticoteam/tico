import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import type { AssistantOkCited } from '@/api/schema';
import { onJump, ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { DelegatedOks, type HistoryEvent } from './Delegated';

vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* The task history's line for an OK the person's Assistant gave on their word (backend/assistant_oks.py): "Chris OK'd
   · via assistant", the person's words quoted, and a link to the message they said them in for a reader who may open
   that chat (`cited` is null for anyone else). Made-up rows. */

const CHRIS = { id: 'human:chris', kind: 'person', name: 'Chris' };

const okVia = (cited: AssistantOkCited | null, undone = false): HistoryEvent => ({
  id: 'e1',
  field: 'answer',
  new: 'ok',
  actor: CHRIS.id,
  via: 'assistant',
  created_at: '2026-11-26T14:02:00Z',
  source: { kind: 'person', name: 'Chris', link: null },
  delegation: { words: 'OK the spec for the suggestion cards task', cited, answer_id: 'a1', undone },
});

beforeEach(() => queryClient.setQueryData(['directory'], { actors: [CHRIS], byId: { [CHRIS.id]: CHRIS } }));
afterEach(() => queryClient.clear());

/** The line on a page of its own, beside the thread page a link may open; Tico's sheet is the shell's. */
function show(events: HistoryEvent[], at = '/') {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        <Outlet />
      </>
    ),
  });
  const task = createRoute({ getParentRoute: () => root, path: '/', component: () => <DelegatedOks events={events} /> });
  const thread = createRoute({ getParentRoute: () => root, path: '/threads/$thread', component: () => <p>The thread</p> });
  const router = createRouter({ routeTree: root.addChildren([task, thread]), history: createMemoryHistory({ initialEntries: [at] }) });
  const r = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, ...r };
}

const jumps: string[] = [];
const stops: (() => void)[] = [];
const listen = (chat: string) => stops.push(onJump(chat, (m) => jumps.push(`${chat} ${m}`)));
afterEach(() => {
  stops.splice(0).forEach((s) => s());
  jumps.length = 0;
});

describe("the history line of an OK the person's Assistant gave", () => {
  it("reads Chris OK'd · via assistant, quotes his words, and opens his message in a thread", async () => {
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-tico');
    const { router } = show([okVia({ message_id: 'm9', conversation_id: 'room-chris' })]);
    expect(await screen.findByText("Chris OK'd · via assistant")).toBeTruthy();
    expect(screen.getByText(/“OK the spec for the suggestion cards task”/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open message' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/threads/room-chris'));
    listen('room-chris');
    expect(jumps).toEqual(['room-chris m9']);
  });

  it("opens his own Assistant room in its sheet, at his message", async () => {
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-chris');
    const { router } = show([okVia({ message_id: 'm9', conversation_id: 'room-chris' })]);
    listen('room-chris');
    fireEvent.click(await screen.findByRole('button', { name: 'Open message' }));
    await waitFor(() => expect(jumps).toEqual(['room-chris m9']));
    expect(router.state.location.pathname).toBe('/');
  });

  it('says via Agent Ops when Agent Ops gave the OK on his word', async () => {
    show([{ ...okVia(null), via: 'botops' }]);
    expect(await screen.findByText("Chris OK'd · via Agent Ops")).toBeTruthy();
  });

  it('is laid out as a Details line: its mark first, then the words', async () => {
    const { container } = show([okVia(null)]);
    await screen.findByText(/“OK the spec for the suggestion cards task”/);
    const line = container.querySelector('.dt-line');
    expect(line?.firstElementChild?.classList.contains('dt-mark')).toBe(true);
    expect(line?.children[1]?.classList.contains('dt-text')).toBe(true);
  });

  it('closes the task open over the page, so his Assistant room in its sheet is seen', async () => {
    queryClient.setQueryData(ticoRoomQuery.queryKey, 'room-chris');
    const { router } = show([okVia({ message_id: 'm9', conversation_id: 'room-chris' })], '/?task=t1');
    listen('room-chris');
    fireEvent.click(await screen.findByRole('button', { name: 'Open message' }));
    await waitFor(() => expect(jumps).toEqual(['room-chris m9']));
    await waitFor(() => expect((router.state.location.search as { task?: string }).task).toBeUndefined());
    expect(router.state.location.pathname).toBe('/');
  });

  it('marks an OK he took back', async () => {
    show([okVia({ message_id: 'm9', conversation_id: 'room-chris' }, true)]);
    expect(await screen.findByText('Taken back')).toBeTruthy();
  });

  it('shows the words but no way to the message to a reader who may not open that chat', async () => {
    show([okVia(null)]);
    expect(await screen.findByText(/“OK the spec for the suggestion cards task”/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Open message' })).toBeNull();
    expect(screen.queryByText('Taken back')).toBeNull();
  });

  it('draws nothing for answers the person gave themselves', async () => {
    const own: HistoryEvent = { id: 'e2', field: 'answer', new: 'ok', actor: CHRIS.id, created_at: '2026-11-26T14:00:00Z', source: { kind: 'person', name: 'Chris', link: null } };
    const { router, container } = show([own]);
    await waitFor(() => expect(router.state.status).toBe('idle'));
    expect(container.textContent).toBe('');
  });
});
