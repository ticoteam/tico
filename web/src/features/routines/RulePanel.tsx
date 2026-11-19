import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { pipelinesQuery } from '@/features/tasks/exports';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { fmtDate } from '@/lib';
import { SidePanel } from '@/pagekinds';
import type { RuleTab } from './actions';
import { partiesOf, useNamer } from './CompanyRules';
import { Face, press, RemoveItem, useConfirmRemove, useName } from './parts';
import { holdsQuery, orgRulesQuery, stageRulesQuery, teamsQuery } from './queries';
import type { OrgRule, RuleHold, StageRule } from './types';
import { agentWords, BROKEN, cap, STAGE_EVENTS, STAGE_THENS, STAGE_TIMERS, stageParts } from './words';

/* One rule, as a panel (rules.js rulePanel): When, Check and Then as facts, then the tasks it holds. Edit turns the
   panel into the rule's editor; `org:new` and `stage:new` open the editor empty. */

export function RulePanel({ panel, tab, onClose }: { panel: string; tab: RuleTab; onClose: () => void }) {
  const [kind, id = ''] = panel.split(/:(.*)/s) as ['org' | 'stage', string?];
  if (kind === 'stage') return id === 'new' ? <StageEditor onDone={onClose} onClose={onClose} /> : <StageView id={id} onClose={onClose} />;
  if (id === 'new') return <OrgEditor outside={tab === 'outside'} onDone={onClose} onClose={onClose} />;
  return <OrgView id={id} onClose={onClose} />;
}

function Gone({ title, loading, onClose }: { title: string; loading: boolean; onClose: () => void }) {
  return (
    <SidePanel className="rr" title={title} onClose={onClose}>
      {!loading && <Empty icon="alert" title="This rule is gone" />}
    </SidePanel>
  );
}

function Head({ onEdit, onRemove, can, tmp }: { onEdit: () => void; onRemove: () => void; can: boolean; tmp: boolean }) {
  if (!can) return null;
  return (
    <>
      <IconButton icon="edit" label="Edit" quiet disabled={tmp} onClick={onEdit} />
      <Menu trigger={<IconButton icon="more" label="More" quiet disabled={tmp} />}>
        <RemoveItem onSelect={onRemove} />
      </Menu>
    </>
  );
}

// ---------------------------------------------------------------- a company rule

const brokenWords = (r: Pick<OrgRule, 'if_broken'>) => BROKEN.find((b) => b.value === (r.if_broken ?? 'block'))?.label ?? 'Stop';

function OrgView({ id, onClose }: { id: string; onClose: () => void }) {
  const active = useQuery(orgRulesQuery('active'));
  const suggested = useQuery(orgRulesQuery('suggested'));
  const r = active.data?.find((x) => x.id === id) ?? suggested.data?.find((x) => x.id === id);
  const [editing, setEditing] = useState(false);
  const remove = useConfirmRemove();
  const name = useNamer();
  const reviewer = useName(r?.reviewer ? `human:${r.reviewer.id}` : null);
  if (!r) return <Gone title="Rule" loading={active.isPending} onClose={onClose} />;
  if (editing) return <OrgEditor rule={r} outside={r.kind === 'external'} onDone={() => setEditing(false)} onClose={onClose} />;
  const tmp = r.id.startsWith('tmp:');
  return (
    <SidePanel
      className="rr"
      title={r.kind === 'external' ? 'Outside rule' : 'Rule'}
      target={`rule:${r.id}`}
      onClose={onClose}
      actions={<Head can={r.can_edit} tmp={tmp} onEdit={() => setEditing(true)} onRemove={() => remove.confirm('this rule', () => void press('rules.remove', { rule: r.id, kind: 'org' }))} />}
    >
      <section className="rail-sec">
        <p className="inst t-read">{agentWords(r.text)}</p>
        <Facts label="About">
          <LabelValue label="If broken">{r.if_broken === 'escalate' && r.reviewer ? `Ask ${reviewer || r.reviewer.name || 'a person'}` : brokenWords(r)}</LabelValue>
          <LabelValue label="Who">{partiesOf(r.who).map(name).join(', ')}</LabelValue>
          {r.source?.name && <LabelValue label="From">{r.source.name}</LabelValue>}
          {r.updated_at && <LabelValue label="Changed">{fmtDate(r.updated_at)}</LabelValue>}
        </Facts>
      </section>
      {!tmp && <Holds id={r.id} />}
      {remove.node}
    </SidePanel>
  );
}

const partyOf = (r: OrgRule | undefined) => (r ? (partiesOf(r.who)[0] ?? '*') : '*');

function OrgEditor({ rule, outside, onDone, onClose }: { rule?: OrgRule; outside: boolean; onDone: () => void; onClose: () => void }) {
  const actors = useActors();
  const teams = useQuery(teamsQuery).data ?? [];
  const [text, setText] = useState(rule?.text ?? '');
  const [broken, setBroken] = useState<'block' | 'escalate' | 'warn'>(rule?.if_broken ?? 'block');
  const [reviewer, setReviewer] = useState<string | null>(rule?.reviewer ? `human:${rule.reviewer.id}` : null);
  const [who, setWho] = useState(partyOf(rule));
  const [busy, setBusy] = useState(false);
  const people = actors.filter((a) => a.kind === 'person').map((a) => ({ value: a.id, label: a.name }));
  const whos = [
    { value: '*', label: 'Everyone' },
    ...teams.map((g) => ({ value: `group:${g.id}`, label: g.name })),
    ...actors.map((a) => ({ value: a.id, label: a.name })),
  ];
  if (!whos.some((w) => w.value === who)) whos.push({ value: who, label: who });
  if (reviewer && !people.some((p) => p.value === reviewer)) people.unshift({ value: reviewer, label: rule?.reviewer?.name ?? 'A person' });
  const ready = !!text.trim() && (broken !== 'escalate' || !!reviewer);

  const save = async () => {
    if (!ready) return;
    const org = { text: text.trim(), ifBroken: broken, ...(broken === 'escalate' && reviewer ? { reviewer } : {}), who, ...(outside ? { outside: true } : {}) };
    setBusy(true);
    const ok = await press(rule ? 'rules.update' : 'rules.add', rule ? { rule: rule.id, kind: 'org', org } : { kind: 'org', org });
    setBusy(false);
    if (ok && rule) onDone();
  };

  return (
    <SidePanel
      className="rr"
      title={rule ? 'Rule' : outside ? 'New outside rule' : 'New rule'}
      target={rule ? `rule:${rule.id}` : 'panel:rule:new'}
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {rule ? 'Save' : 'Add rule'}
          </Button>
          <Button variant="quiet" onClick={onDone}>
            Cancel
          </Button>
        </>
      }
    >
      <section className="rail-sec rr-form">
        <Field label="Rule" area value={text} onValueChange={setText} placeholder="An agent emails a customer for the first time" autoFocus={!rule} />
        <Select label="Who" options={whos} value={who} onValueChange={setWho} />
        <div className="rr-field">
          <span className="set-name">If broken</span>
          <Segmented label="If broken" options={BROKEN} value={broken} onValueChange={setBroken} />
        </div>
        {broken === 'escalate' && <Select label="Ask" options={people} value={reviewer} onValueChange={setReviewer} placeholder="Pick a person" />}
      </section>
    </SidePanel>
  );
}

// ---------------------------------------------------------------- a stage rule

function useStageRule(id: string) {
  const active = useQuery(stageRulesQuery('active'));
  const suggested = useQuery(stageRulesQuery('suggested'));
  return { r: active.data?.items.find((x) => x.id === id) ?? suggested.data?.items.find((x) => x.id === id), loading: active.isPending };
}

function StageView({ id, onClose }: { id: string; onClose: () => void }) {
  const { r, loading } = useStageRule(id);
  const teams = useQuery(teamsQuery).data ?? [];
  const [editing, setEditing] = useState(false);
  const remove = useConfirmRemove();
  if (!r) return <Gone title="Rule" loading={loading} onClose={onClose} />;
  if (editing) return <StageEditor rule={r} onDone={() => setEditing(false)} onClose={onClose} />;
  const parts = stageParts(r, (tid) => teams.find((g) => g.id === tid)?.name);
  const tmp = r.id.startsWith('tmp:');
  return (
    <SidePanel
      className="rr"
      title={r.name || parts[0] || 'Rule'}
      target={`rule:${r.id}`}
      onClose={onClose}
      actions={
        <Head
          can={r.can_edit !== false}
          tmp={tmp}
          onEdit={() => setEditing(true)}
          onRemove={() => remove.confirm(r.name || 'this rule', () => void press('rules.remove', { rule: r.id, kind: 'stage' }))}
        />
      }
    >
      <section className="rail-sec">
        <Facts label="About">
          <LabelValue label="When">{parts[0]}</LabelValue>
          {parts.length > 2 && <LabelValue label="Check">{parts[1]}</LabelValue>}
          <LabelValue label="Then">{parts[parts.length - 1]}</LabelValue>
          <LabelValue label="Pipeline">{r.scope === 'company' ? 'Every pipeline' : (r.pipeline_name ?? '')}</LabelValue>
          {r.not_acting && <LabelValue label="Paused">{agentWords(r.not_acting)}</LabelValue>}
          {r.updated_at && <LabelValue label="Changed">{fmtDate(r.updated_at)}</LabelValue>}
        </Facts>
      </section>
      {r.jev && (
        <section className="rail-sec">
          <h3>Decision model</h3>
          <p className="inst t-read">{r.jev}</p>
        </section>
      )}
      {r.reason && (
        <section className="rail-sec">
          <h3>Why</h3>
          <p className="inst t-read">{r.reason}</p>
        </section>
      )}
      {remove.node}
    </SidePanel>
  );
}

type StageEvent = (typeof STAGE_EVENTS)[number]['value'];
type TimerKind = (typeof STAGE_TIMERS)[number]['value'];
type Then = (typeof STAGE_THENS)[number]['value'];

function StageEditor({ rule, onDone, onClose }: { rule?: StageRule; onDone: () => void; onClose: () => void }) {
  const pipelines = useQuery(pipelinesQuery).data ?? [];
  const [name, setName] = useState(rule?.name ?? '');
  const [pipeline, setPipeline] = useState(rule && rule.scope !== 'company' ? rule.pipeline_id : '');
  const [stage, setStage] = useState(rule?.stages[0] ?? '');
  const firstEvent = (rule?.event.split(',')[0] ?? 'enters') as StageEvent;
  const [event, setEvent] = useState<StageEvent>(STAGE_EVENTS.some((e) => e.value === firstEvent) ? firstEvent : 'enters');
  const [timerKind, setTimerKind] = useState<TimerKind>((rule?.timer?.kind as TimerKind | undefined) ?? 'in_stage');
  const [hours, setHours] = useState(String(rule?.timer?.hours ?? 24));
  const [model, setModel] = useState(rule?.jev ?? '');
  const [then, setThen] = useState<Then>((rule?.then as Then | undefined) ?? 'tell_owner');
  const [reason, setReason] = useState(rule?.reason ?? '');
  const [busy, setBusy] = useState(false);
  const pipe = pipelines.find((p) => p.id === pipeline);
  const timer = event === 'timer';
  const h = Number(hours);
  const ready = !timer || (Number.isFinite(h) && h >= 0.25);

  const save = async () => {
    if (!ready) return;
    const fields = {
      ...(name.trim() ? { name: name.trim() } : {}),
      pipeline,
      stages: stage ? [stage] : [],
      event: rule && rule.event.includes(',') && !timer && rule.event.split(',')[0] === event ? rule.event : event,
      ...(timer ? { timerKind, timerHours: h } : {}),
      decisionModel: model.trim(),
      then: timer ? then : 'block',
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    };
    setBusy(true);
    const ok = await press(rule ? 'rules.update' : 'rules.add', rule ? { rule: rule.id, kind: 'stage', stage: fields } : { kind: 'stage', stage: fields });
    setBusy(false);
    if (ok && rule) onDone();
  };

  return (
    <SidePanel
      className="rr"
      title={rule ? rule.name || 'Rule' : 'New stage rule'}
      target={rule ? `rule:${rule.id}` : 'panel:rule:new'}
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {rule ? 'Save' : 'Add rule'}
          </Button>
          <Button variant="quiet" onClick={onDone}>
            Cancel
          </Button>
        </>
      }
    >
      <section className="rail-sec rr-form">
        <Field label="Name" value={name} onValueChange={setName} autoFocus={!rule} />
        <Select
          label="Pipeline"
          options={[{ value: '', label: 'Every pipeline' }, ...pipelines.map((p) => ({ value: p.id, label: p.name }))]}
          value={pipeline}
          onValueChange={(v) => {
            setPipeline(v);
            setStage('');
          }}
        />
        {pipe && (
          <Select
            label="Stage"
            options={[{ value: '', label: 'Every stage' }, ...pipe.stages.filter((st) => !st.side).map((st) => ({ value: st.id, label: st.name }))]}
            value={stage}
            onValueChange={setStage}
          />
        )}
        <Select label="When" options={STAGE_EVENTS} value={event} onValueChange={setEvent} />
        {timer && (
          <>
            <Field label="Hours" type="number" value={hours} onValueChange={setHours} error={ready ? undefined : 'A quarter hour or more'} />
            <Select label="Timer" options={STAGE_TIMERS.map((x) => ({ value: x.value, label: cap(x.label) }))} value={timerKind} onValueChange={setTimerKind} />
          </>
        )}
        <Field label="Decision model" area value={model} onValueChange={setModel} placeholder="What the decision model checks, if anything" />
        {timer ? (
          <Select label="Then" options={STAGE_THENS} value={then} onValueChange={setThen} />
        ) : (
          <Facts>
            <LabelValue label="Then">Block</LabelValue>
          </Facts>
        )}
        <Field label="Why" area value={reason} onValueChange={setReason} placeholder="Said when the rule acts" />
      </section>
    </SidePanel>
  );
}

// ---------------------------------------------------------------- what a rule holds

const HOLD_MARK: Record<string, string> = { held: 'Waits on you', approved: 'OK given', allowed: 'OK given', ok: 'OK given' };

function Holds({ id }: { id: string }) {
  const q = useQuery(holdsQuery);
  const list = (q.data ?? []).filter((h) => h.rules.some((r) => r.id === id));
  if (!list.length) return null;
  return (
    <section className="rail-sec">
      <h3>Holds</h3>
      {list.map((h) => (
        <HoldRow key={h.id} h={h} />
      ))}
    </section>
  );
}

function HoldRow({ h }: { h: RuleHold }) {
  const ok = HOLD_MARK[h.outcome ?? ''] === 'OK given';
  const mark = h.state === 'held' ? 'Waits on you' : ok ? 'OK given' : 'Sent back';
  const title = h.to?.length ? h.to.join(', ') : h.operation ? agentWords(h.operation.replace(/[._]+/g, ' ')) : 'A held step';
  return (
    <Row className="hold" title={title} href={h.task_id ? `/task/${encodeURIComponent(h.task_id)}` : undefined} target={`hold:${h.id}`}>
      <span className="hold-mark" title={mark}>
        {h.state === 'held' ? <FlowMark /> : <Icon name={ok ? 'check' : 'reply'} />}
      </span>
      <span className="row-title">{title}</span>
      <Face id={h.author ?? null} />
      <Ago at={h.created_at} className="row-age" />
    </Row>
  );
}
