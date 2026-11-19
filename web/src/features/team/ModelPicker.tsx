import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { effortLabel } from './model';
import { WordPick } from './parts';
import { modelsQuery, type AgentRow } from './queries';
import './team.css';

/**
 * The model picker (mates.js modelPanel): Harness, Model, Effort, then Apply. Every choice and every default comes from
 * GET /api/v2/models; nothing here names a model.
 */
export function ModelPicker({ agent, open, onOpenChange }: { agent: AgentRow; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { data } = useQuery({ ...modelsQuery, enabled: open });
  const apply = useAction('agents.setModel');
  const [harness, setHarness] = useState(agent.harness ?? '');
  const [model, setModel] = useState(agent.model || agent.resolved_model || '');
  const [effort, setEffort] = useState(agent.reasoning_effort ?? '');

  const enabled = data?.enabled_providers;
  const usable = (data?.models ?? []).filter((m) => !m.deprecated && (!enabled || !m.provider || enabled.includes(m.provider)));
  const harnesses = (data?.harnesses ?? []).filter((h) => usable.some((m) => !m.harnesses || m.harnesses.includes(h.id)));
  const models = usable.filter((m) => !harness || !m.harnesses || m.harnesses.includes(harness));
  const chosen = models.find((m) => m.id === model);
  const efforts = chosen?.efforts ?? [];

  const reset = () => {
    setHarness(agent.harness ?? '');
    setModel(agent.model || agent.resolved_model || '');
    setEffort(agent.reasoning_effort ?? '');
  };
  const pickHarness = (h: string) => {
    setHarness(h);
    const fits = usable.filter((m) => !m.harnesses || m.harnesses.includes(h));
    if (!fits.some((m) => m.id === model)) {
      setModel(fits[0]?.id ?? '');
      setEffort('');
    }
  };
  const pickModel = (id: string) => {
    setModel(id);
    if (!usable.find((m) => m.id === id)?.efforts?.includes(effort)) setEffort('');
  };
  const changed = model !== (agent.model || agent.resolved_model || '') || effort !== (agent.reasoning_effort ?? '') || harness !== (agent.harness ?? '');

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) reset();
        onOpenChange(o);
      }}
      title="Model"
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!model || !changed}
            onClick={() => {
              onOpenChange(false);
              void apply.run({ slug: agent.slug, model, ...(effort ? { effort } : {}), ...(harness ? { harness } : {}) });
            }}
          >
            Apply
          </Button>
        </>
      }
    >
      {harnesses.length > 1 && (
        <div className="f">
          <span className="f-label">Harness</span>
          <WordPick label="Harness" options={harnesses.map((h) => ({ value: h.id, label: h.name }))} value={harness} onValueChange={pickHarness} />
        </div>
      )}
      <div className="f">
        <span className="f-label">Model</span>
        <div className="mp-list" role="radiogroup" aria-label="Model">
          {models.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={m.id === model}
              className={m.id === model ? 'radio is-on' : 'radio'}
              onClick={() => pickModel(m.id)}
            >
              <span className="radio-box" />
              <span>{m.label}</span>
              {m.id === data?.default?.model && <em>Default</em>}
            </button>
          ))}
        </div>
      </div>
      {efforts.length > 0 && (
        <div className="f">
          <span className="f-label">Effort</span>
          <WordPick
            label="Effort"
            options={['', ...efforts].map((e) => ({
              value: e,
              label: effortLabel(e),
            }))}
            value={effort}
            onValueChange={setEffort}
          />
        </div>
      )}
    </Dialog>
  );
}
