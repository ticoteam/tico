import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { useActors } from '@/data';
import { StageRules } from '@/features/routines/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Select, type SelectOption } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { newId } from '@/lib';
import { Record as RecordPage, Section, type PageState } from '@/pagekinds';
import { Face, OwnerButton } from './parts';
import {
  factoryChoicesQuery,
  pipelineChanged,
  pipelinesQuery,
  pipelineSettingsNow,
  pipelineSettingsQuery,
  type PipelineSettingsRecord,
  type StageDraft,
} from './queries';

type Row = StageDraft & { k: string };

/** The pipeline's own facts as the fields hold them: '' for none. */
interface Facts {
  name: string;
  goal: string;
  manager: string;
  factory: string;
  prefix: string;
}

interface Draft {
  facts: Facts;
  rows: Row[];
}

const HAS_TASKS = 'stage_has_tasks';
const CHANGED = 'task_changed';
const NEW: Draft = { facts: { name: '', goal: '', manager: '', factory: '', prefix: '' }, rows: [] };

function draftOf(rec: PipelineSettingsRecord): Draft {
  const text = (v: unknown) => (typeof v === 'string' ? v : '');
  return {
    facts: { name: rec.name, goal: text(rec.goal), manager: text(rec.manager_id), factory: text(rec.factory_id), prefix: text(rec.prefix) },
    rows: rec.stages.map((s) => ({ id: s.id, name: s.name, side: s.side, purpose: s.purpose ?? '', owner: s.owner ?? null, task_limit: s.task_limit ?? null, k: s.id })),
  };
}

/**
 * Edit pipeline, a page (`/tasks/<pipeline>/edit`; more.js pipeline, page-types: a Record that is mostly an editor):
 * the pipeline's Goal, Manager, Factory and Prefix in a row under the head, then its stages in order. A stage opens under its row: its Goal, Owner and Limit, then (once saved) its rules,
 * On entering, While here and To leave. Each stage can be moved, made a side stage or removed; Add stage, then Save.
 * Removing a stage that holds tasks asks where they go. Someone else's save while it is open is said, with what they
 * changed: load theirs, or save these edits over it. `fresh` is New pipeline (`?new=1`): Name first, the same fields;
 * `pipeline` is then only where Back goes.
 */
export function PipelinePage({ pipeline, fresh }: { pipeline: string; fresh: boolean }) {
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const p = fresh ? undefined : pipes.find((x) => x.id === pipeline);
  const rec = useQuery({ ...pipelineSettingsQuery(p?.id ?? ''), enabled: !!p });
  const edit = useAction('tasks.editPipeline');
  const onClose = () => void edit.run({ pipeline, open: false });
  const factories = useQuery(factoryChoicesQuery).data;
  const actors = useActors();
  const save = useAction('tasks.savePipeline');
  const create = useAction('tasks.createPipeline');
  const [draft, setDraft] = useState<Draft | null>(null);
  /** The record the edits started from: its settings_version is what the save says it read. */
  const [base, setBase] = useState<PipelineSettingsRecord | null>(null);
  /** Why the last save was refused; `move` when moving the removed stages' tasks would let it through. */
  const [held, setHeld] = useState<{ text: string; move: boolean } | null>(null);
  /** Someone saved first: their record, and what they changed. */
  const [clash, setClash] = useState<{ theirs: PipelineSettingsRecord; changed: string[] } | null>(null);
  /** The stage that is open, by its row key. */
  const [open, setOpen] = useState<string | null>(null);
  const shown: Draft | null = draft ?? (p ? (rec.data ? draftOf(rec.data) : null) : NEW);
  const busy = save.pending || create.pending;

  const change = (next: Draft) => {
    setHeld(null);
    if (!draft && rec.data) setBase(rec.data);
    setDraft(next);
  };
  const fact = (patch: Partial<Facts>) => shown && change({ ...shown, facts: { ...shown.facts, ...patch } });
  const rowsTo = (rows: Row[]) => shown && change({ ...shown, rows });
  const put = (i: number, patch: Partial<Row>) => shown && rowsTo(shown.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const firstNotSide = (rows: Row[]) => rows.map((x, j) => (j === 0 ? { ...x, side: false } : x));
  const swap = (i: number, j: number) => {
    if (!shown) return;
    const next = [...shown.rows];
    [next[i], next[j]] = [next[j]!, next[i]!];
    rowsTo(firstNotSide(next));
  };
  const rows = shown?.rows ?? [];
  const named = !!shown && (p ? true : !!shown.facts.name.trim());
  const ready = !!draft && named && rows.length > 0 && rows.every((r) => r.name.trim()) && !clash;

  // A removed stage's tasks go to the first saved stage that stays.
  const keep = rows.find((r) => r.id && !r.side);
  const keepId = keep?.id;

  const send = async (moveTo?: { pipeline: string; stage: string }, version?: number) => {
    if (!shown) return;
    const { facts } = shown;
    const stages = shown.rows.map(({ id, name, side, purpose, owner, task_limit }, i) => ({
      ...(id ? { id } : {}),
      name: name.trim(),
      side,
      purpose: (purpose ?? '').trim(),
      owner: owner ?? null,
      task_limit: side || i === 0 ? null : (task_limit ?? null),
    }));
    const fields = {
      goal: facts.goal.trim(),
      manager_id: facts.manager || null,
      factory_id: facts.factory || null,
      prefix: facts.prefix.trim() ? facts.prefix.trim().toUpperCase() : null,
    };
    if (!p) {
      const made = await create.run({ name: facts.name.trim(), ...fields, stages });
      if (made.ok) setDraft(null);
      else setHeld({ text: made.why ?? (made.error === 'not_allowed' ? 'Only Admins can make a pipeline' : "Couldn't make the pipeline"), move: false });
      return;
    }
    const seen = version ?? base?.settings_version ?? rec.data?.settings_version;
    const out = await save.run({ pipeline: p.id, ...fields, stages, ...(seen != null ? { version: seen } : {}), ...(moveTo ? { move_to: moveTo } : {}) });
    if (out.ok) {
      setDraft(null);
      setBase(null);
      onClose();
    } else if (out.error === CHANGED && base) {
      try {
        const theirs = await pipelineSettingsNow(p.id);
        setClash({ theirs, changed: pipelineChanged(base, theirs) });
      } catch {
        setHeld({ text: "Couldn't read the pipeline again", move: false });
      }
    } else if (out.error === HAS_TASKS && keep) setHeld({ text: `A removed stage holds tasks. Move them to ${keep.name}?`, move: true });
    else setHeld({ text: out.error === 'not_allowed' ? 'Only the pipeline’s owner can change it' : (out.why ?? "Couldn't save the pipeline"), move: false });
  };

  /** Their record in place of these edits. */
  const loadTheirs = () => {
    setClash(null);
    setDraft(null);
    setBase(null);
  };
  /** These edits over theirs, at their version. */
  const saveMine = () => {
    if (!clash) return;
    const { theirs } = clash;
    setBase(theirs);
    setClash(null);
    void send(undefined, theirs.settings_version);
  };

  const agents = actors.filter((a) => a.kind === 'agent');
  const people = actors.filter((a) => a.kind === 'person');
  const choice = (list: { id: string; name: string }[], none: string, on: string): SelectOption[] => {
    const out = [{ value: '', label: none }, ...list.map((a) => ({ value: a.id, label: a.name }))];
    // A saved value the list lacks stays chosen under its own id.
    return on && !out.some((o) => o.value === on) ? [...out, { value: on, label: on }] : out;
  };
  const ownerChoices = (r: Row): SelectOption[] => {
    const on = r.owner ?? '';
    const lead = [{ value: '', label: 'Not set' }, { value: 'assignee', label: "The task's assignee" }];
    // One of several (a pool of agents) is set in the old editor's place for it; here it stays as it is, or goes.
    if (on === 'pick') lead.push({ value: 'pick', label: 'One of several' });
    const rest = [...agents, ...people].map((a) => ({ value: a.id, label: a.name }));
    const all = [...lead, ...rest];
    return on && !all.some((o) => o.value === on) ? [...all, { value: on, label: on }] : all;
  };

  const addStage = () => rowsTo([...rows, { name: '', side: false, k: newId() }]);
  let state: PageState = 'ready';
  // A read again that fails later keeps the edits on screen: only a first read that fails is the page's fault.
  if (!fresh) {
    if (!p) state = 'empty';
    else if (!p.mayChange) state = 'no-access';
    else if (!rec.data) state = rec.isError ? 'fault' : 'loading';
  }

  const facts = shown?.facts;
  return (
    <RecordPage
      title={p?.name ?? 'New pipeline'}
      path={`/tasks/${encodeURIComponent(pipeline)}/edit`}
      crumb={[{ label: 'Tasks', to: `/tasks/${encodeURIComponent(pipeline)}` }]}
      className="pe-page"
      state={state}
      empty={{ title: 'No such pipeline' }}
      noAccess={{ text: 'Only the pipeline’s owner can change it.' }}
      onRetry={() => void rec.refetch()}
      actions={
        <>
          {p?.owner && <OwnerButton actor={p.owner} />}
          {held?.move && keepId && p && (
            <Button onClick={() => send({ pipeline: p.id, stage: keepId })} disabled={busy}>
              Move tasks
            </Button>
          )}
          {clash ? (
            <>
              <Button onClick={loadTheirs}>Load theirs</Button>
              <Button variant="primary" onClick={saveMine} disabled={busy}>
                Save mine
              </Button>
            </>
          ) : (
            <Button variant="primary" onClick={() => send()} disabled={!ready || busy} busy={busy} data-target="pipeline:save">
              Save
            </Button>
          )}
        </>
      }
      facts={
        <>
          {clash && (
            <p className="pe-held" role="alert">
              Someone saved this pipeline while you had it open. They changed: {clash.changed.join(', ') || 'nothing shown here'}.
            </p>
          )}
          {held && (
            <p className="pe-held" role="alert">
              {held.text}
            </p>
          )}
          {facts && (
            <div className="pe-facts">
              {!p && (
                <div className="w-pname">
                  <Field label="Name" value={facts.name} onValueChange={(name) => fact({ name: name.slice(0, 80) })} autoFocus />
                </div>
              )}
              <div className="w-pgoal">
                <Field label="Goal" value={facts.goal} onValueChange={(goal) => fact({ goal: goal.slice(0, 300) })} />
              </div>
              <div className="w-manager">
                <Select label="Manager" options={choice(agents, 'Not set', facts.manager)} value={facts.manager} onValueChange={(manager) => fact({ manager })} />
              </div>
              {factories && (
                <Select label="Factory" options={choice(factories, 'None', facts.factory)} value={facts.factory} onValueChange={(factory) => fact({ factory })} />
              )}
              <Field
                label="Prefix"
                value={facts.prefix}
                placeholder={(facts.name || p?.name || '').trim().charAt(0).toUpperCase()}
                onValueChange={(v) => fact({ prefix: v.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase() })}
              />
            </div>
          )}
        </>
      }
      sections={
        shown && (
          <Section name="Stages" count={rows.length || undefined}>
            {rows.length === 0 ? (
              <Empty title="No stages yet">
                <Button icon="plus" onClick={addStage}>
                  Add stage
                </Button>
              </Empty>
            ) : (
              <div className="pe-stages">
                {rows.map((r, i) => {
                  const isOpen = open === r.k;
                  // Rules belong to a saved work stage.
                  const rulesOf = r.id && !r.side ? r.id : null;
                  return (
                    <div key={r.k} className={['ps', r.side && 'is-side', isOpen && 'is-open'].filter(Boolean).join(' ')}>
                      <i className="ps-node" />
                      <div className="ps-in">
                        <div className="ps-row">
                          <Field label="Name" labelHidden value={r.name} onValueChange={(name) => put(i, { name })} />
                          <button
                            type="button"
                            className="ps-chev"
                            aria-expanded={isOpen}
                            aria-label={`${rulesOf ? 'Rules' : 'Open'}, ${r.name || 'new stage'}`}
                            onClick={() => setOpen(isOpen ? null : r.k)}
                          >
                            <Icon name="chevron" />
                          </button>
                          <span className="grow" />
                          {r.owner && /^(human|bot):/.test(r.owner) && <Face actor={r.owner} />}
                          <Menu trigger={<IconButton icon="more" label={`More, ${r.name || 'new stage'}`} size="sm" />}>
                            <MenuItem label="Move up" disabled={i === 0} onSelect={() => swap(i - 1, i)} />
                            <MenuItem label="Move down" disabled={i === rows.length - 1} onSelect={() => swap(i, i + 1)} />
                            <MenuRule />
                            <MenuItem label="Remove stage" onSelect={() => rowsTo(firstNotSide(rows.filter((_, j) => j !== i)))} />
                          </Menu>
                        </div>
                        {isOpen && (
                          <div className="ps-body">
                            <div className="ps-facts">
                              <div className="w-goal">
                                <Field label="Goal" value={r.purpose ?? ''} onValueChange={(purpose) => put(i, { purpose: purpose.slice(0, 300) })} />
                              </div>
                              <div className="w-owner">
                                <Select
                                  label="Owner"
                                  options={ownerChoices(r).filter((o) => !r.side || o.value !== 'pick')}
                                  value={r.owner ?? ''}
                                  onValueChange={(owner) => put(i, { owner: owner || null })}
                                />
                              </div>
                              {i > 0 && !r.side && (
                                <Field
                                  label="Limit"
                                  type="number"
                                  placeholder="None"
                                  value={r.task_limit ? String(r.task_limit) : ''}
                                  onValueChange={(v) => put(i, { task_limit: Number(v) > 0 ? Math.round(Number(v)) : null })}
                                />
                              )}
                              {/* A side stage hangs off the line (dashed node, indented); the first stage is never one. */}
                              {i > 0 && (
                                <span className="ps-side">
                                  <Switch
                                    label="Side"
                                    checked={r.side}
                                    onCheckedChange={(side) => put(i, side ? { side, task_limit: null, owner: r.owner === 'pick' ? null : r.owner } : { side })}
                                  />
                                </span>
                              )}
                            </div>
                            {rulesOf && p && <StageRules pipeline={p.id} stage={rulesOf} leave={rec.data?.stages.find((st) => st.id === rulesOf)} />}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div className="ps is-add">
                  <i className="ps-node">
                    <Icon name="plus" />
                  </i>
                  <div className="ps-in">
                    <button className="ps-row" type="button" onClick={addStage}>
                      <span className="ps-name">Add stage</span>
                    </button>
                  </div>
                </div>
              </div>
            )}
          </Section>
        )
      }
    />
  );
}
