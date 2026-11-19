import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Switch } from '@/kit/Switch';
import { newId } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { pipelineSettingsQuery, type Pipeline, type StageDraft } from './queries';

type Row = StageDraft & { k: string };

const HAS_TASKS = 'stage_has_tasks';

/**
 * Edit pipeline, the smallest set (the old app's stage list): each stage's name, side or not, its place, remove; then
 * Add stage and Save. A side stage cannot be first. Removing a stage that holds tasks asks where they go.
 */
export function PipelinePanel({ p, onClose }: { p: Pipeline; onClose: () => void }) {
  const rec = useQuery(pipelineSettingsQuery(p.id));
  const save = useAction('tasks.savePipeline');
  const [draft, setDraft] = useState<Row[] | null>(null);
  /** Why the last save was refused; `move` when moving the removed stages' tasks would let it through. */
  const [held, setHeld] = useState<{ text: string; move: boolean } | null>(null);
  const rows: Row[] | null = draft ?? rec.data?.stages.map((s) => ({ id: s.id, name: s.name, side: s.side, k: s.id })) ?? null;

  const change = (next: Row[]) => {
    setHeld(null);
    setDraft(next);
  };
  const put = (i: number, patch: Partial<Row>) => rows && change(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const swap = (i: number, j: number) => {
    if (!rows) return;
    const next = [...rows];
    [next[i], next[j]] = [next[j]!, next[i]!];
    if (next[0]?.side) next[0] = { ...next[0], side: false };
    change(next);
  };
  const ready = !!rows && !!draft && rows.length > 0 && rows.every((r) => r.name.trim());

  // A removed stage's tasks go to the first saved stage that stays.
  const keep = rows?.find((r) => r.id && !r.side);
  const keepId = keep?.id;

  const send = async (moveTo?: { pipeline: string; stage: string }) => {
    if (!rows) return;
    const stages = rows.map(({ id, name, side }) => ({ ...(id ? { id } : {}), name: name.trim(), side }));
    const out = await save.run({ pipeline: p.id, stages, ...(moveTo ? { move_to: moveTo } : {}) });
    if (out.ok) {
      setDraft(null);
      onClose();
    } else if (out.error === HAS_TASKS && keep) setHeld({ text: `A removed stage holds tasks. Move them to ${keep.name}?`, move: true });
    else setHeld({ text: out.error === 'not_allowed' ? 'Only the pipeline’s owner can change it' : "Couldn't save the pipeline", move: false });
  };

  return (
    <SidePanel
      title="Edit pipeline"
      target="panel:pipeline"
      onClose={onClose}
      foot={
        <>
          {held?.move && keepId && (
            <Button onClick={() => void send({ pipeline: p.id, stage: keepId })} disabled={save.pending}>
              Move tasks
            </Button>
          )}
          <Button variant="primary" onClick={() => void send()} disabled={!ready || save.pending} busy={save.pending}>
            Save
          </Button>
        </>
      }
    >
      <div className="rail-sec">
        <h3>{p.name}</h3>
        {held && (
          <p className="pe-held" role="alert">
            {held.text}
          </p>
        )}
        {rows && (
          <div className="pe-stages">
            {rows.map((r, i) => (
              <div key={r.k} className={r.side ? 'ps is-side' : 'ps'}>
                <i className="ps-node" />
                <div className="ps-in">
                  <div className="ps-row">
                    <Field label="Name" labelHidden value={r.name} onValueChange={(name) => put(i, { name })} />
                    <span className="grow" />
                    <Switch label="Side" checked={r.side} disabled={i === 0} onCheckedChange={(side) => put(i, { side })} />
                    <Menu trigger={<IconButton icon="more" label={`More, ${r.name || 'new stage'}`} size="sm" />}>
                      <MenuItem label="Move up" disabled={i === 0} onSelect={() => swap(i - 1, i)} />
                      <MenuItem label="Move down" disabled={i === rows.length - 1} onSelect={() => swap(i, i + 1)} />
                      <MenuRule />
                      <MenuItem label="Remove stage" onSelect={() => change(rows.filter((_, j) => j !== i).map((x, j) => (j === 0 ? { ...x, side: false } : x)))} />
                    </Menu>
                  </div>
                </div>
              </div>
            ))}
            <div className="ps is-add">
              <i className="ps-node">
                <Icon name="plus" />
              </i>
              <div className="ps-in">
                <button className="ps-row" type="button" onClick={() => change([...rows, { name: '', side: false, k: newId() }])}>
                  <span className="ps-name">Add stage</span>
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </SidePanel>
  );
}
