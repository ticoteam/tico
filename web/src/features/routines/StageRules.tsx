import { useQuery } from '@tanstack/react-query';
import { useId, useState, type ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { FoldButton } from '@/kit/SectionHead';
import { press, RemoveItem, RuleRow, useConfirmRemove } from './parts';
import { stageRulesQuery, teamsQuery } from './queries';
import { useStageRuleForm, type StageEvent } from './RulePanel';
import type { StageRule } from './types';
import { stageParts } from './words';
import './routines.css';

/** A stage's own record from the pipeline (GET /api/v2/pipelines/{id}): what it takes to leave it. */
export interface StageLeave {
  requirements?: readonly Record<string, unknown>[];
  by_hand?: boolean;
}

type Moment = 'enter' | 'here' | 'leave';

// the old pipeline editor's requirement kinds (PE_KINDS)
const KINDS: Record<string, string> = {
  owner_done: 'Owner says done',
  ok: 'OK from someone',
  linked_done: 'Linked work done',
  page: 'A page',
  related_input: 'Related input',
  pr_open: 'PR open',
  checks_pass: 'Checks pass',
  pr_merged: 'PR merged',
  in_release: 'In a release',
};
/** Never pushed past: a person cannot wave these through. */
const HARD = new Set(['pr_merged', 'in_release']);

const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');

/** A requirement in words: "Approve <page>", its own line, or its kind's name. */
export function leaveWords(q: Record<string, unknown>): string {
  const kind = str(q.kind);
  const settings = (q.settings ?? {}) as Record<string, unknown>;
  if (kind === 'page' && str(settings.document)) return `Approve ${str(settings.document)}`;
  return str(q.line) || (kind === 'item' ? str(q.condition) : '') || KINDS[kind] || kind;
}

/** Which moment a rule acts at (stage-rules.ts srPhaseOf): entering, or while here. Nothing on the server fires on leaving. */
const momentOf = (r: StageRule): Moment => (r.event.split(',').includes('enters') ? 'enter' : 'here');
const EVENT: Record<Exclude<Moment, 'leave'>, StageEvent> = {
  enter: 'enters',
  here: 'timer',
};

/** Does this rule act on the stage: its own stage, every stage of its pipeline, or every pipeline. */
const holds = (r: StageRule, pipeline: string, stage: string) =>
  r.scope === 'company' || (r.pipeline_id === pipeline && (r.scope === 'pipeline' || !r.stages.length || r.stages.includes(stage)));

const X = ({ d }: { d: string }) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);
// more.js ICO: the marks the shared icon set does not have
const ENTER = <X d="M3.5 12h10.5M10.5 8l4 4-4 4M19.5 4.5v15" />;
const LEAVE = <X d="M4.5 4.5v15M9.5 12h11M16.5 8l4 4-4 4" />;

/**
 * A stage's rules in the pipeline editor (more.js stageBody): On entering, While here and To leave, each a section
 * that folds, with dense one-line rows. Add rule and Edit open the one stage rule editor under the section, with the
 * pipeline and stage preset. To leave lists what the stage needs before a task moves on.
 */
export function StageRules({ pipeline, stage, leave }: { pipeline: string; stage: string; leave?: StageLeave }) {
  const q = useQuery(stageRulesQuery('active'));
  const teams = useQuery(teamsQuery).data ?? [];
  const team = (id: string) => teams.find((g) => g.id === id)?.name;
  const remove = useConfirmRemove();
  /** The editor that is open: a new rule in a moment, or a rule. */
  const [open, setOpen] = useState<{ at: Moment; rule?: StageRule } | null>(null);
  const rules = (q.data?.items ?? []).filter((r) => holds(r, pipeline, stage));
  const by = (m: Moment) => rules.filter((r) => momentOf(r) === m);

  const row = (r: StageRule) => (
    <RuleRow
      key={r.id}
      how={r.jev ? 'model' : 'fixed'}
      parts={stageParts(r, team)}
      cost={!!r.jev}
      off={!!r.not_acting}
      target={`rule:${r.id}`}
      more={
        r.can_edit !== false && !r.id.startsWith('tmp:') ? (
          <>
            <MenuItem label="Edit" onSelect={() => setOpen({ at: momentOf(r), rule: r })} />
            <RemoveItem onSelect={() => remove.confirm(r.name || 'this rule', () => void press('rules.remove', { rule: r.id, kind: 'stage', stay: true }))} />
          </>
        ) : undefined
      }
    />
  );

  const editor = (at: Exclude<Moment, 'leave'>) =>
    open?.at === at && (
      <RuleEditor key={open.rule?.id ?? 'new'} rule={open.rule} pipeline={pipeline} stage={stage} event={EVENT[at]} onDone={() => setOpen(null)} />
    );

  const leaveRows = [
    ...(leave?.requirements ?? []).map((x, i) => (
      <RuleRow key={i} how="fixed" parts={[leaveWords(x)]} lock={x.can_push_past === false || HARD.has(str(x.kind))} target={`requirement:${stage}:${i}`} />
    )),
    ...(leave?.by_hand ? [<RuleRow key="hand" how="person" parts={['OK from anyone']} target={`requirement:${stage}:hand`} />] : []),
  ];

  return (
    <div className="rr ps-moments">
      <Phase icon={ENTER} name="On entering" count={by('enter').length} onAdd={() => setOpen({ at: 'enter' })}>
        {by('enter').map(row)}
        {editor('enter')}
      </Phase>
      <Phase icon={<Icon name="clock" />} name="While here" count={by('here').length} onAdd={() => setOpen({ at: 'here' })}>
        {by('here').map(row)}
        {editor('here')}
      </Phase>
      <Phase icon={LEAVE} name="To leave" count={leaveRows.length}>
        {leaveRows}
      </Phase>
      {remove.node}
    </div>
  );
}

/** A moment: its name folds it and says how many rules it holds; "+" adds one. */
function Phase({ icon, name, count, onAdd, children }: { icon: ReactNode; name: string; count: number; onAdd?: () => void; children: ReactNode }) {
  const [shut, setShut] = useState(false);
  const id = useId();
  const add = () => {
    setShut(false);
    onAdd?.();
  };
  return (
    <section className={shut ? 'ph is-folded' : 'ph'} aria-label={name}>
      <header className="ph-head">
        {icon}
        <h3>
          <FoldButton name={name} count={count} open={!shut} onToggle={() => setShut(!shut)} controls={id} />
        </h3>
        {onAdd && <IconButton icon="plus" label="Add rule" size="sm" quiet onClick={add} />}
      </header>
      <div id={id} hidden={shut}>
        {!shut && children}
      </div>
    </section>
  );
}

/** The one stage rule editor, under its moment, with this pipeline and stage preset; it stays on the page when it saves. */
function RuleEditor({ rule, pipeline, stage, event, onDone }: { rule?: StageRule; pipeline: string; stage: string; event: StageEvent; onDone: () => void }) {
  const form = useStageRuleForm({
    rule,
    preset: { pipeline, stage, event },
    onSaved: onDone,
    onCancel: onDone,
  });
  return (
    <div className="rule-new rr-form">
      {form.fields}
      <div className="rule-new-foot">{form.foot}</div>
    </div>
  );
}
