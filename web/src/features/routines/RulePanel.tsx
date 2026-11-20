import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { pipelinesQuery } from '@/features/tasks/routing';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { fmtDate } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { RULES, type RuleTab } from './actions';
import { PartyPick, useNamer } from './CompanyRules';
import { DuePanel } from './DuePanel';
import { Face, press, RemoveItem, useConfirmRemove, useName, Verb, Word } from './parts';
import { holdsQuery, orgRulesQuery, partiesOf, ruleSourcesQuery, stageRulesQuery, teamsQuery } from './queries';
import type { OrgRule, RuleHold, StageRule } from './types';
import { agentWords, BROKEN, cap, dayWords, STAGE_EVENTS, STAGE_THENS, STAGE_TIMERS, stageParts } from './words';

/* One rule, as a panel (rules.js rulePanel): When, Check and Then as facts, then the tasks it holds. Edit turns the
   panel into the rule's editor; `org:new` and `stage:new` open the editor empty. */

export function RulePanel({ panel, tab, onClose }: { panel: string; tab: RuleTab; onClose: () => void }) {
  const [kind, id = ''] = panel.split(/:(.*)/s) as ['org' | 'stage' | 'due', string?];
  if (kind === 'due') return <DuePanel id={id} onClose={onClose} />;
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
  const quote = r.cite && !r.cite.hidden && r.cite.quote ? r.cite : null;
  const dues = r.due ?? [];
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
          <LabelValue label="If broken">{r.if_broken === 'escalate' ? `Ask ${reviewer || r.reviewer?.name || 'the manager'}` : brokenWords(r)}</LabelValue>
          <LabelValue label="Who">{partiesOf(r.who).map(name).join(', ')}</LabelValue>
          {r.source?.name && <LabelValue label="From">{r.source.name}</LabelValue>}
          {r.updated_at && <LabelValue label="Changed">{fmtDate(r.updated_at)}</LabelValue>}
        </Facts>
        {quote && (
          <blockquote className="rr-quote t-read">
            {quote.file_id ? (
              <Link className="verb" href={`/library/files?file=${encodeURIComponent(quote.file_id)}`}>
                {quote.section || 'Document'}
              </Link>
            ) : (
              <b>{quote.section || 'Document'}</b>
            )}{' '}
            “{quote.quote}”
          </blockquote>
        )}
      </section>
      {(dues.length > 0 || (r.can_edit && !tmp)) && (
        <section className="rail-sec">
          <h3>
            Due
            {r.can_edit && !tmp && (
              <Verb onClick={() => void press('rules.newDue', { rule: r.id })} target={`due-new:${r.id}`}>
                Add date
              </Verb>
            )}
          </h3>
          {dues.map((d) => (
            <Row key={d.id} className="prun" title={dueWords(d)} href={`${RULES}?tab=due&panel=${encodeURIComponent(`due:${d.id}`)}`} target={`due:${d.id}`}>
              <span className="run-at">{dayWords(d.due_on)}</span>
              <span className="row-sub">{dueWords(d)}</span>
              {d.status === 'overdue' ? <Word>Late</Word> : <span className="row-age" />}
            </Row>
          ))}
          {!!r.due_more && <p className="row-sub">{`${r.due_more} more`}</p>}
        </section>
      )}
      {!tmp && <Holds id={r.id} />}
      {remove.node}
    </SidePanel>
  );
}

const DUE_REPEATS: Record<string, string> = { weekly: 'Every week', monthly: 'Every month', quarterly: 'Every quarter', yearly: 'Every year' };
const dueWords = (d: { repeats?: string; task_days?: number | null }) =>
  [DUE_REPEATS[d.repeats ?? ''] ?? 'Once', d.task_days != null ? `task ${d.task_days} days before` : ''].filter(Boolean).join(', ');

const MANAGER = 'manager';
const NEW_FROM = '+new';
const FROM_KINDS = [
  { value: 'vendor', label: 'Vendor' },
  { value: 'customer', label: 'Customer' },
  { value: 'government', label: 'Government' },
  { value: 'other', label: 'Other' },
] as const;
type FromKind = (typeof FROM_KINDS)[number]['value'];

function OrgEditor({ rule, outside, onDone, onClose }: { rule?: OrgRule; outside: boolean; onDone: () => void; onClose: () => void }) {
  const actors = useActors();
  const sources = useQuery({ ...ruleSourcesQuery, enabled: outside }).data ?? [];
  const [text, setText] = useState(rule?.text ?? '');
  const [broken, setBroken] = useState<'block' | 'escalate' | 'warn'>(rule?.if_broken ?? 'block');
  // under Ask first, nobody picked is the manager above whoever broke it
  const [reviewer, setReviewer] = useState<string>(rule?.reviewer ? `human:${rule.reviewer.id}` : MANAGER);
  // every team, agent and person it names, not only the first
  const [who, setWho] = useState<string[]>(rule ? partiesOf(rule.who) : ['*']);
  const [from, setFrom] = useState<string | null>(rule?.source?.id ?? null);
  const [fromName, setFromName] = useState('');
  const [fromKind, setFromKind] = useState<FromKind>('vendor');
  const [busy, setBusy] = useState(false);
  const people = [{ value: MANAGER, label: 'Manager' }, ...actors.filter((a) => a.kind === 'person').map((a) => ({ value: a.id, label: a.name }))];
  if (!people.some((p) => p.value === reviewer)) people.push({ value: reviewer, label: rule?.reviewer?.name ?? 'A person' });
  const fromOptions = [...sources.map((x) => ({ value: x.id, label: x.name })), { value: NEW_FROM, label: 'New…' }];
  if (from && from !== NEW_FROM && !fromOptions.some((o) => o.value === from)) fromOptions.unshift({ value: from, label: rule?.source?.name ?? 'This From' });
  const fromReady = !outside || (from === NEW_FROM ? !!fromName.trim() : !!from);
  const ready = !!text.trim() && who.length > 0 && fromReady;

  const save = async () => {
    if (!ready) return;
    const org = {
      text: text.trim(),
      ifBroken: broken,
      ...(broken === 'escalate' && reviewer !== MANAGER ? { reviewer } : {}),
      who,
      ...(outside ? { outside: true, ...(from === NEW_FROM ? { newSource: { name: fromName.trim(), kind: fromKind } } : from ? { source: from } : {}) } : {}),
    };
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
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => save()}>
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
        <PartyPick label="Who" value={who} onChange={setWho} />
        {outside && <Select label="From" options={fromOptions} value={from} onValueChange={setFrom} placeholder="Pick one" />}
        {outside && from === NEW_FROM && (
          <>
            <Field label="Name" value={fromName} onValueChange={setFromName} />
            <Segmented label="Kind" options={FROM_KINDS} value={fromKind} onValueChange={setFromKind} />
          </>
        )}
        <div className="rr-field">
          <span className="set-name">If broken</span>
          <Segmented label="If broken" options={BROKEN} value={broken} onValueChange={setBroken} />
        </div>
        {broken === 'escalate' && <Select label="Ask" options={people} value={reviewer} onValueChange={setReviewer} />}
      </section>
    </SidePanel>
  );
}

// ---------------------------------------------------------------- a stage rule

function useStageRule(id: string) {
  const active = useQuery(stageRulesQuery('active'));
  const suggested = useQuery(stageRulesQuery('suggested'));
  const r = active.data?.items.find((x) => x.id === id) ?? suggested.data?.items.find((x) => x.id === id);
  // a suggested change or retire names the rule in force it is about
  const was = r?.suggests ? active.data?.items.find((x) => x.id === r.suggests) : undefined;
  return { r, was, loading: active.isPending };
}

/** What a suggested change alters in the rule it replaces, part by part: [part, now, after]. */
export function stageDiff(was: StageRule, r: StageRule, team: (id: string) => string | undefined): [string, string, string][] {
  const a = stageParts(was, team);
  const b = stageParts(r, team);
  const ifs = (p: string[]) => (p.length > 2 ? (p[1] ?? '') : 'Always');
  const where = (x: StageRule) => (x.scope === 'company' ? 'Every pipeline' : [x.pipeline_name, ...(x.stage_names ?? [])].filter(Boolean).join(', '));
  const rows: [string, string, string][] = [
    ['Name', was.name, r.name],
    ['On', where(was), where(r)],
    ['When', a[0] ?? '', b[0] ?? ''],
    ['Check', ifs(a), ifs(b)],
    ['Then', a[a.length - 1] ?? '', b[b.length - 1] ?? ''],
    ['Why', was.reason ?? '', r.reason ?? ''],
  ];
  return rows.filter(([, x, y]) => x !== y);
}

function StageView({ id, onClose }: { id: string; onClose: () => void }) {
  const { r, was, loading } = useStageRule(id);
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
          can={r.can_edit !== false && r.status !== 'suggested'}
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
      {r.suggests && (
        <section className="rail-sec" aria-label={r.then_params?.retire ? 'Suggested retire' : 'Suggested change'}>
          <h3>{r.then_params?.retire ? 'Suggested retire' : 'Suggested change'}</h3>
          {!was ? (
            <Word>Gone</Word>
          ) : r.then_params?.retire ? (
            <p className="row-sub">{typeof r.then_params.retire === 'string' ? r.then_params.retire : `Takes ${was.name || 'this rule'} out of force`}</p>
          ) : (
            <Facts label="Changes">
              {stageDiff(was, r, (tid) => teams.find((g) => g.id === tid)?.name).map(([k, x, y]) => (
                <LabelValue key={k} label={k}>
                  <span className="rr-diff">
                    <del>{x || 'None'}</del> → <ins>{y || 'None'}</ins>
                  </span>
                </LabelValue>
              ))}
            </Facts>
          )}
        </section>
      )}
      {remove.node}
    </SidePanel>
  );
}

export type StageEvent = (typeof STAGE_EVENTS)[number]['value'];
type TimerKind = (typeof STAGE_TIMERS)[number]['value'];
type Then = (typeof STAGE_THENS)[number]['value'];

/** Where a new rule goes when its place is already known (a stage in the pipeline editor): no Pipeline or Stage to pick. */
export interface StagePreset {
  pipeline: string;
  stage: string;
  event: StageEvent;
}

/**
 * The one stage rule editor: its fields, and Add rule (or Save) and Cancel. The Rules page draws it in a panel; the
 * pipeline editor draws it under a stage, with the pipeline and stage preset, and stays where it is when it saves.
 */
export function useStageRuleForm({ rule, preset, onSaved, onCancel }: { rule?: StageRule; preset?: StagePreset; onSaved?: () => void; onCancel: () => void }) {
  const pipelines = useQuery(pipelinesQuery).data ?? [];
  const [name, setName] = useState(rule?.name ?? '');
  const [pipeline, setPipeline] = useState(rule ? (rule.scope !== 'company' ? rule.pipeline_id : '') : (preset?.pipeline ?? ''));
  const [stage, setStage] = useState(rule ? (rule.stages[0] ?? '') : (preset?.stage ?? ''));
  const firstEvent = (rule?.event.split(',')[0] ?? preset?.event ?? 'enters') as StageEvent;
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
  const stay = preset ? { stay: true } : {};

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
    const ok = await press(rule ? 'rules.update' : 'rules.add', rule ? { rule: rule.id, kind: 'stage', stage: fields } : { kind: 'stage', stage: fields, ...stay });
    setBusy(false);
    if (ok) onSaved?.();
  };

  const foot = (
    <>
      <Button variant="primary" busy={busy} disabled={!ready} onClick={() => save()}>
        {rule ? 'Save' : 'Add rule'}
      </Button>
      <Button variant="quiet" onClick={onCancel}>
        Cancel
      </Button>
    </>
  );

  const fields = (
    <>
      <Field label="Name" value={name} onValueChange={setName} autoFocus={!rule} />
      {!preset && (
        <Select
          label="Pipeline"
          options={[{ value: '', label: 'Every pipeline' }, ...pipelines.map((p) => ({ value: p.id, label: p.name }))]}
          value={pipeline}
          onValueChange={(v) => {
            setPipeline(v);
            setStage('');
          }}
        />
      )}
      {!preset && pipe && (
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
    </>
  );

  return { fields, foot };
}

function StageEditor({ rule, onDone, onClose }: { rule?: StageRule; onDone: () => void; onClose: () => void }) {
  const form = useStageRuleForm({ rule, onSaved: rule ? onDone : undefined, onCancel: onDone });
  return (
    <SidePanel
      className="rr"
      title={rule ? rule.name || 'Rule' : 'New stage rule'}
      target={rule ? `rule:${rule.id}` : 'panel:rule:new'}
      onClose={onClose}
      foot={form.foot}
    >
      <section className="rail-sec rr-form">{form.fields}</section>
    </SidePanel>
  );
}

// ---------------------------------------------------------------- what a rule holds

/** A hold's state, as its row's mark says it: held until its reviewer sends it anyway or keeps it blocked. */
const HOLD_MARK: Record<string, string> = { held: 'Waits on you', sent: 'Sent anyway', kept: 'Kept blocked' };

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
  const ok = h.state === 'sent';
  const mark = HOLD_MARK[h.state] ?? 'Kept blocked';
  const title = h.to?.length ? h.to.join(', ') : h.operation ? agentWords(h.operation.replace(/[._]+/g, ' ')) : 'A held step';
  return (
    <Row className="hold" title={title} href={h.task_id ? `/task/${encodeURIComponent(h.task_id)}` : undefined} target={`hold:${h.id}`}>
      <span className="hold-mark" title={mark}>
        {h.state === 'held' ? <FlowMark /> : <Icon name={ok ? 'send' : 'lock'} />}
      </span>
      <span className="row-title">{title}</span>
      <Face id={h.author ?? null} />
      <Ago at={h.created_at} className="row-age" />
    </Row>
  );
}
