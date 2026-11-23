import { Radio } from '@base-ui/react/radio';
import { RadioGroup } from '@base-ui/react/radio-group';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { FoldButton } from '@/kit/SectionHead';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { effortLabel, modelName, pickable } from './model';
import { agentQuery, COMPACT_AT, modelsQuery, type AgentRow } from './queries';
import './team.css';

const COMPACT = [
  { value: '', label: 'Default' },
  ...COMPACT_AT.map((n) => ({ value: String(n), label: n >= 1000000 ? `${n / 1000000}M` : `${n / 1000}K` })),
];

/**
 * The model picker (mates.js modelPanel): Harness, Model, Effort, then More (where it compacts), then Apply. Every
 * choice and every default comes from GET /api/v2/models; nothing here names a model.
 */
export function ModelPicker({
  agent,
  also = [],
  open,
  onOpenChange,
}: {
  agent: AgentRow;
  /** The computer's "All agents" row: the same pick for these too. */
  also?: readonly AgentRow[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data } = useQuery({ ...modelsQuery, enabled: open });
  const detail = useQuery({ ...agentQuery(agent.slug), enabled: open && !also.length }).data;
  const apply = useAction('agents.setModel');
  const compactAt = useAction('agents.setCompact');
  const [harness, setHarness] = useState(agent.harness ?? '');
  const [model, setModel] = useState(agent.model || agent.resolved_model || '');
  const [effort, setEffort] = useState(agent.reasoning_effort ?? '');
  const [more, setMore] = useState(false);
  const [compact, setCompact] = useState<string | null>(null);
  const savedCompact = detail?.compact_at ? String(detail.compact_at) : '';
  const compactNow = compact ?? savedCompact;

  // Older rows without a harness run their runtime's. A harness no longer offered stays only when every target is on it.
  const on = (a: AgentRow) => a.harness || a.resolved_runtime || '';
  const current = also.every((a) => on(a) === on(agent)) ? on(agent) : undefined;
  const { models: usable, harnesses } = pickable(data, current);
  const models = usable.filter((m) => !harness || !m.harnesses || m.harnesses.includes(harness));
  const chosen = models.find((m) => m.id === model);
  const efforts = chosen?.efforts ?? [];
  // Where the pick runs: the harness chosen, else the agent's own when the model runs there, else the model's first.
  const runs = chosen?.harnesses;
  const dest = harness || (!runs?.length || runs.includes(on(agent)) ? on(agent) : runs[0]);
  // Compact shows only where that harness compacts, or while a saved value (a fallback may use it) can still be cleared.
  const compacts = !also.length && (!!data?.harnesses.find((h) => h.id === dest)?.capabilities?.compaction || !!savedCompact);

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
  const differs = (a: AgentRow) => model !== (a.model || a.resolved_model || '') || effort !== (a.reasoning_effort ?? '') || harness !== (a.harness ?? '');
  const moving = [agent, ...also].filter(differs);
  const modelChanged = moving.length > 0;
  const compactChanged = compacts && compactNow !== savedCompact;
  const save = async () => {
    onOpenChange(false);
    // Both edits advance the same configuration revision. The model change must use the revision from compact-at.
    if (compactChanged) {
      const result = await compactAt.run({ slug: agent.slug, at: compactNow ? Number(compactNow) : null });
      if (!result.ok) return;
    }
    for (const a of moving) void apply.run({ slug: a.slug, model, ...(effort ? { effort } : {}), ...(harness ? { harness } : {}) });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Model"
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!model || (!modelChanged && !compactChanged)}
            onClick={() => save()}
          >
            Apply
          </Button>
        </>
      }
    >
      {harnesses.length > 1 && (
        <div className="f">
          <span className="f-label">Harness</span>
          <Segmented className="is-fill" label="Harness" options={harnesses.map((h) => ({ value: h.id, label: h.name }))} value={harness} onValueChange={pickHarness} />
        </div>
      )}
      <div className="f">
        <span className="f-label">Model</span>
        <RadioGroup className="mp-list" value={model} onValueChange={(id) => pickModel(id as string)} aria-label="Model">
          {models.map((m) => (
            <label key={m.id} className={m.id === model ? 'radio is-on' : 'radio'}>
              <Radio.Root value={m.id} className="radio-box" />
              <span className="mp-name">{modelName(m.label)}</span>
              {m.id === data?.default?.model && <em>Default</em>}
            </label>
          ))}
        </RadioGroup>
      </div>
      {efforts.length > 0 && (
        <div className="f">
          <span className="f-label">Effort</span>
          <Segmented className="is-fill" label="Effort" options={['', ...efforts].map((e) => ({ value: e, label: effortLabel(e) }))} value={effort} onValueChange={setEffort} />
        </div>
      )}
      {compacts && <FoldButton name="More" open={more} onToggle={() => setMore(!more)} />}
      {more && compacts && <Select label="Compact" options={COMPACT} value={compactNow} onValueChange={setCompact} />}
    </Dialog>
  );
}
