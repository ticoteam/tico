import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AssistantOkCited } from '@/api/schema';
import { queryClient } from '@/data';
import { DelegatedOks, type HistoryEvent } from './Delegated';

/* The task history's line for an OK the person's Assistant gave on their word (backend/assistant_oks.py): "Chris OK'd
   · via assistant", the person's words quoted, and a link to the message they said them in for a reader who may open
   that chat (`cited` is null for anyone else). Made-up rows. */

const CHRIS = { id: 'human:chris', kind: 'person', name: 'Chris' };

const okVia = (cited: AssistantOkCited | null): HistoryEvent => ({
  id: 'e1',
  field: 'answer',
  new: 'ok',
  actor: CHRIS.id,
  via: 'assistant',
  created_at: '2026-11-26T14:02:00Z',
  source: { kind: 'person', name: 'Chris', link: null },
  delegation: { words: 'OK the spec for the suggestion cards task', cited },
});

beforeEach(() => queryClient.setQueryData(['directory'], { actors: [CHRIS], byId: { [CHRIS.id]: CHRIS } }));
afterEach(() => queryClient.clear());

const show = (events: HistoryEvent[]) =>
  render(
    <QueryClientProvider client={queryClient}>
      <DelegatedOks events={events} />
    </QueryClientProvider>,
  );

describe("the history line of an OK the person's Assistant gave", () => {
  it("reads Chris OK'd · via assistant, quotes his words, and links to his message", () => {
    show([okVia({ message_id: 'm9', conversation_id: 'room-chris' })]);
    expect(screen.getByText("Chris OK'd · via assistant")).toBeTruthy();
    expect(screen.getByText(/“OK the spec for the suggestion cards task”/)).toBeTruthy();
    expect(screen.getByRole('link', { name: /Assistant room/ }).getAttribute('href')).toBe('/threads/room-chris');
  });

  it('shows the words but no link to a reader who may not open that chat', () => {
    show([okVia(null)]);
    expect(screen.getByText(/“OK the spec for the suggestion cards task”/)).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('draws nothing for answers the person gave themselves', () => {
    const own: HistoryEvent = { id: 'e2', field: 'answer', new: 'ok', actor: CHRIS.id, created_at: '2026-11-26T14:00:00Z', source: { kind: 'person', name: 'Chris', link: null } };
    const { container } = show([own]);
    expect(container.textContent).toBe('');
  });
});
