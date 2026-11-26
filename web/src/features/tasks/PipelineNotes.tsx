import { queryOptions, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '@/api/client';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';

/** A note as GET /api/v2/pipelines/{id}/notes gives it (backend/pipeline_notes.py). */
export interface PipelineNote {
  id: string;
  stage_id: string | null;
  note: string;
  why: string;
  since: string;
  evidence: string;
  guard: string;
  state: 'active' | 'proposed' | 'declined' | 'retired';
  revision: number;
  created_by: string;
  changes: { by_id: string; kind: string; line: string; created_at: string }[];
}

interface NotesList {
  notes: PipelineNote[];
  stages: { id: string; name: string; notes: PipelineNote[] }[];
}

export const pipelineNotesQuery = (pipeline: string) =>
  queryOptions({
    queryKey: ['pipeline-notes', pipeline],
    queryFn: ({ signal }) => api.get<NotesList>(`/api/v2/pipelines/${encodeURIComponent(pipeline)}/notes?state=any`, { signal }),
  });

type Draft = { note: string; why: string; since: string; evidence: string; line: string };
const BLANK: Draft = { note: '', why: '', since: '', evidence: '', line: '' };

/**
 * A pipeline's or one stage's notes in the pipeline editor, next to its rules: what the team learned there, each with
 * why, since and its evidence, served to a bot's first turn there. People who may change the pipeline add, edit and
 * retire them, saying what changed and why; a bot's proposal shows as Proposed with Accept and Decline.
 */
export function PipelineNotes({ pipeline, stage, mayChange }: { pipeline: string; stage: string | null; mayChange: boolean }) {
  const q = useQuery(pipelineNotesQuery(pipeline));
  const client = useQueryClient();
  const [open, setOpen] = useState<{ id: string | null; act: 'add' | 'edit' | 'retire' | 'accept' | 'decline' } | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState('');
  const all = stage === null ? (q.data?.notes ?? []) : (q.data?.stages.find((s) => s.id === stage)?.notes ?? []);
  const shown = all.filter((n) => n.state === 'active' || n.state === 'proposed');
  const set = (k: keyof Draft) => (v: string) => setDraft((d) => ({ ...d, [k]: v }));

  const start = (act: NonNullable<typeof open>['act'], n?: PipelineNote) => {
    setError('');
    setDraft(n && act === 'edit' ? { note: n.note, why: n.why, since: n.since, evidence: n.evidence, line: '' } : BLANK);
    setOpen({ id: n?.id ?? null, act });
  };

  const save = async () => {
    if (!open) return;
    const id = encodeURIComponent(open.id ?? '');
    const p = encodeURIComponent(pipeline);
    try {
      if (open.act === 'add') {
        await api.post(`/api/v2/pipelines/${p}/notes`, { stage: stage ?? undefined, note: draft.note, why: draft.why, since: draft.since || undefined, evidence: draft.evidence });
      } else if (open.act === 'edit') {
        await api.post(`/api/v2/pipeline-notes/${id}`, { note: draft.note, why: draft.why, since: draft.since || undefined, evidence: draft.evidence, line: draft.line });
      } else {
        await api.post(`/api/v2/pipeline-notes/${id}/${open.act}`, { reason: draft.line });
      }
      setOpen(null);
      await client.invalidateQueries({ queryKey: ['pipeline-notes', pipeline] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const form = () => {
    if (!open) return null;
    const words = open.act === 'add' || open.act === 'edit';
    const line =
      open.act === 'edit' ? 'What changed and why' : open.act === 'retire' ? 'Why retire it' : 'Reason (the proposer receives it)';
    return (
      <div className="rule-new rr-form" aria-label={open.act === 'add' ? 'Add note' : 'Change note'}>
        {words && (
          <>
            <Field label="Note" value={draft.note} onValueChange={set('note')} />
            <Field label="Why" value={draft.why} onValueChange={set('why')} />
            <Field label="Since" placeholder="2026-11-18" value={draft.since} onValueChange={set('since')} />
            <Field label="Evidence" placeholder="A task, a pull request or a run" value={draft.evidence} onValueChange={set('evidence')} />
          </>
        )}
        {open.act !== 'add' && <Field label={line} value={draft.line} onValueChange={set('line')} />}
        {error && <p role="alert">{error}</p>}
        <div className="rule-new-foot">
          <Button variant="primary" onClick={save} disabled={(words && (!draft.note.trim() || !draft.why.trim())) || (open.act !== 'add' && !draft.line.trim())}>
            Save
          </Button>
          <Button onClick={() => setOpen(null)}>Cancel</Button>
        </div>
      </div>
    );
  };

  return (
    <section className="ph ps-notes" aria-label={stage === null ? 'Pipeline notes' : 'Notes'}>
      <header className="ph-head">
        <h3>
          {stage === null ? 'Pipeline notes' : 'Notes'} <span className="count">{shown.length}</span>
        </h3>
        {mayChange && (
          <Button onClick={() => start('add')} disabled={open?.act === 'add'}>
            Add note
          </Button>
        )}
      </header>
      <ul>
        {shown.map((n) => (
          <li key={n.id} className={n.state === 'proposed' ? 'note is-proposed' : 'note'}>
            <p>
              {n.state === 'proposed' && <strong>Proposed: </strong>}
              {n.note}
            </p>
            <p className="note-why">
              Why: {n.why} · Since {n.since}
              {n.evidence && ` · ${n.evidence}`}
              {n.guard && ` · guard ${n.guard}`}
            </p>
            {n.changes
              .filter((c) => c.kind === 'edited')
              .map((c, i) => (
                <p key={i} className="note-change">
                  {c.created_at.slice(0, 10)}: {c.line}
                </p>
              ))}
            {mayChange && n.state === 'active' && (
              <>
                <Button onClick={() => start('edit', n)}>Edit</Button>
                <Button onClick={() => start('retire', n)}>Retire</Button>
              </>
            )}
            {mayChange && n.state === 'proposed' && (
              <>
                <Button onClick={() => start('accept', n)}>Accept</Button>
                <Button onClick={() => start('decline', n)}>Decline</Button>
              </>
            )}
            {open?.id === n.id && form()}
          </li>
        ))}
      </ul>
      {open?.act === 'add' && form()}
    </section>
  );
}
