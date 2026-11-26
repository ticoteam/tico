import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { queryClient } from '@/data';
import { PipelineNotes, pipelineNotesQuery, type PipelineNote } from './PipelineNotes';

/* A pipeline's and a stage's notes in the pipeline editor. Sample rows only. */

const note = (over: Partial<PipelineNote>): PipelineNote => ({
  id: 'n_1',
  stage_id: 'eng-qa',
  note: 'A pending approval is the ask.',
  why: 'Approvers got two cards for one decision.',
  since: '2026-11-18',
  evidence: '#4237',
  guard: '',
  state: 'active',
  revision: 1,
  created_by: 'human:ann',
  changes: [],
  ...over,
});

const list = (stageNotes: PipelineNote[]) =>
  queryClient.setQueryData(pipelineNotesQuery('eng').queryKey, {
    notes: [note({ id: 'n_0', stage_id: null, note: 'Heavy checks run one at a time.' })],
    stages: [{ id: 'eng-qa', name: 'QA', notes: stageNotes }],
  });

const shown = (stage: string | null, mayChange = true) =>
  render(
    <QueryClientProvider client={queryClient}>
      <PipelineNotes pipeline="eng" stage={stage} mayChange={mayChange} />
    </QueryClientProvider>,
  );

beforeEach(() => queryClient.clear());
afterEach(() => vi.restoreAllMocks());

describe('PipelineNotes', () => {
  it("lists a stage's notes with why, since and evidence, and only that stage's", () => {
    list([note({}), note({ id: 'n_2', state: 'retired', note: 'Gone.' })]);
    shown('eng-qa');
    expect(screen.getByText('A pending approval is the ask.')).toBeTruthy();
    expect(screen.getByText(/Why: Approvers got two cards for one decision\. · Since 2026-11-18 · #4237/)).toBeTruthy();
    expect(screen.queryByText('Heavy checks run one at a time.')).toBeNull();
    expect(screen.queryByText('Gone.')).toBeNull();
  });

  it('asks Note and Why before a note is added', async () => {
    list([]);
    const post = vi.spyOn(api, 'post').mockResolvedValue({});
    shown('eng-qa');
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Big runs go to RunsOn.' } });
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Why'), { target: { value: 'The shared machine went offline.' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]?.[0]).toBe('/api/v2/pipelines/eng/notes');
    expect(post.mock.calls[0]?.[1]).toMatchObject({ stage: 'eng-qa', note: 'Big runs go to RunsOn.', why: 'The shared machine went offline.' });
  });

  it('shows a proposal with Accept and Decline, and the reason goes to the server', async () => {
    list([note({ id: 'n_9', state: 'proposed', created_by: 'bot:qa' })]);
    const post = vi.spyOn(api, 'post').mockResolvedValue({});
    shown('eng-qa');
    expect(screen.getByText('Proposed:')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    fireEvent.change(screen.getByLabelText('Reason (the proposer receives it)'), { target: { value: 'Two refusals back it.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v2/pipeline-notes/n_9/accept', { reason: 'Two refusals back it.' }));
  });

  it('offers no change to someone who may not change the pipeline', () => {
    list([note({}), note({ id: 'n_9', state: 'proposed' })]);
    shown('eng-qa', false);
    for (const name of ['Add note', 'Edit', 'Retire', 'Accept', 'Decline']) expect(screen.queryByRole('button', { name })).toBeNull();
  });
});
