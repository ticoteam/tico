import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { meQuery, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { SidePanel } from '@/pagekinds';
import { TRIGGER_FIELDS, type Cond, type FlowRule } from './board';
import {
  ACTIONS,
  EVENT_SHORT,
  eventShort,
  findStep,
  FIND_SOURCES,
  KIND_WORDS,
  MAX_PATHS,
  newStep,
  newTrigger,
  QUESTION_TYPES,
  REPEATS,
  STEP_WORDS,
  WHEN,
  type FlowStep,
  type Path,
  type Question,
} from './flow';
import { useNamer } from './FlowView';
import { press, Verb } from './parts';
import type { Routine, Trigger } from './types';
import { ScheduleFields } from './RoutinePanel';
import { browserZone, parseSchedule } from './words';

/* One step's editor, and the trigger's, in the panel (automations-editors.ts): every field the step's type carries,
   then Save, which writes the flow as a new version. A new step opens here first and joins the flow when saved. */

const SHORT_OPS: Record<string, string> = { is: '=', is_not: '≠', '>=': '≥', '<=': '≤', contains: 'has', matches: 'matches' };
const opChoices = Object.entries(SHORT_OPS).map(([value, label]) => ({ value, label }));
const CUSTOM = '__custom';

/** What `?edit=` holds, read back: the trigger, a new step at a place, or a step by its id. */
export function readEdit(edit: string): { kind: 'trigger' } | { kind: 'new'; key: string; at: number; type: string } | { kind: 'step'; id: string } {
  if (edit === 'trigger') return { kind: 'trigger' };
  const p = edit.split('|');
  if (p[0] === 'new' && p.length === 4) return { kind: 'new', key: p[1]!, at: Number(p[2]), type: p[3]! };
  return { kind: 'step', id: edit };
}

export function StepPanel({ routine, edit, onClose }: { routine: Routine; edit: string; onClose: () => void }) {
  const me = useQuery(meQuery).data;
  const agents = useActors().filter((a) => a.kind === 'agent' && a.slug !== 'botops');
  const e = readEdit(edit);
  if (e.kind === 'trigger') return <TriggerForm routine={routine} onClose={onClose} />;
  if (e.kind === 'new') {
    const meId = (me?.id ?? '').replace(/^\w+:/, '');
    return <StepForm key={edit} routine={routine} start={newStep(e.type, routine.steps ?? [], meId, agents[0]?.id ?? '')} insert={{ key: e.key, at: e.at }} onClose={onClose} />;
  }
  const hit = findStep(routine.steps, e.id);
  if (!hit)
    return (
      <SidePanel className="rr au-ed" title="Step" onClose={onClose}>
        <section className="rail-sec">
          <p className="au-note">This step is no longer in the flow</p>
        </section>
      </SidePanel>
    );
  return <StepForm key={edit} routine={routine} start={hit.step} onClose={onClose} />;
}

// ---------------------------------------------------------------- conditions

function CondRows({ conds, onChange, fields, min, label }: { conds: Cond[]; onChange: (next: Cond[]) => void; fields: string[]; min: boolean; label: string }) {
  const set = (i: number, c: Partial<Cond>) => onChange(conds.map((x, j) => (j === i ? { ...x, ...c } : x)));
  return (
    <div className="au-conds" role="group" aria-label={label}>
      {conds.map((c, i) => (
        <div key={i} className={min ? 'au-cond' : 'au-cond is-nomin'}>
          <Field label="Field" labelHidden value={String(c.field ?? '')} onValueChange={(v) => set(i, { field: v })} placeholder={fields[0] ?? 'Field'} />
          <Select label="Test" labelHidden options={opChoices} value={c.op || 'is'} onValueChange={(op) => set(i, { op })} />
          <Field label="Value" labelHidden value={c.value === true ? 'yes' : c.value === false ? 'no' : String(c.value ?? '')} onValueChange={(v) => set(i, { value: v })} placeholder="Value" />
          {min && (
            <Field
              label="Minimum, percent"
              labelHidden
              type="number"
              value={c.min != null && c.min !== '' ? String(Math.round(Number(c.min) * 100)) : ''}
              onValueChange={(v) => set(i, { min: v === '' ? undefined : Math.max(0, Math.min(100, Number(v))) / 100 })}
              placeholder="Min"
            />
          )}
          <IconButton icon="close" label="Remove condition" size="sm" quiet onClick={() => onChange(conds.filter((_, j) => j !== i))} />
        </div>
      ))}
      <Verb onClick={() => onChange([...conds, { field: fields[0] ?? '', op: 'is', value: '' }])}>Add condition</Verb>
    </div>
  );
}

/** The fields a condition can test: the trigger's, and every Decide or LLM output in the flow. */
function fieldsOf(r: Routine): string[] {
  const out = [...(TRIGGER_FIELDS[r.trigger.type] ?? [])];
  const walk = (steps: FlowStep[] | undefined) =>
    (steps ?? []).forEach((s) => {
      if (s.type === 'decide' && s.out) Object.keys(s.questions ?? {}).forEach((q) => out.push(`${s.out}.${q}`));
      if (s.type === 'llm' && s.out) out.push(s.out);
      if (s.type === 'paths') [...(s.paths ?? []), s.otherwise ?? {}].forEach((p) => walk(p.steps as FlowStep[] | undefined));
    });
  walk(r.steps as FlowStep[] | undefined);
  return [...new Set(out)];
}

// ---------------------------------------------------------------- one step

interface QRow {
  id: string;
  type: string;
  ask: string;
  opts: string;
}
const qRows = (s: FlowStep): QRow[] =>
  Object.entries(s.questions ?? {}).map(([id, q]) => ({
    id,
    type: q.type ?? 'noul',
    ask: typeof q.instructions === 'string' ? q.instructions : q.instructions == null ? '' : JSON.stringify(q.instructions),
    opts: Array.isArray(q.criteria) ? q.criteria.join(', ') : Object.keys(q.criteria ?? {}).join(', '),
  }));

function StepForm({ routine, start, insert, onClose }: { routine: Routine; start: FlowStep; insert?: { key: string; at: number }; onClose: () => void }) {
  const [s, setS] = useState<FlowStep>(() => structuredClone(start));
  const [qs, setQs] = useState<QRow[]>(() => qRows(start));
  const [busy, setBusy] = useState(false);
  const name = useNamer();
  const me = useQuery(meQuery).data;
  const agents = useActors().filter((a) => a.kind === 'agent' && a.slug !== 'botops');
  const people = useActors().filter((a) => a.kind === 'person');
  const can = !!routine.can_edit && !routine.readonly;
  const fields = fieldsOf(routine);
  const put = (patch: Partial<FlowStep>) => setS((x) => ({ ...x, ...patch }));
  const arg = (k: string) => String(s.args?.[k] ?? '');
  const setArg = (k: string, v: string) => put({ args: { ...(s.args ?? {}), [k]: v } });
  const withCur = (opts: { value: string; label: string }[], v: string | undefined) => (v && !opts.some((o) => o.value === v) ? [{ value: v, label: name(v) || 'Agent' }, ...opts] : opts);
  const agentOpts = agents.map((a) => ({ value: a.id, label: a.name }));
  const whoOpts = [...people.map((p) => ({ value: p.id.replace(/^human:/, 'person:'), label: p.name })), ...agentOpts];

  const save = async () => {
    const step = structuredClone(s);
    if (step.type === 'decide' && !step.set) {
      if (qs.some((q) => q.id.trim() && !q.ask.trim())) return void toast({ text: 'Each question needs what to ask', kind: 'failed' });
      const old = step.questions ?? {};
      step.questions = Object.fromEntries(
        qs
          .filter((q) => q.id.trim())
          .map((q): [string, Question] => {
            const def: Question = { ...(old[q.id] ?? {}), type: q.type, instructions: q.ask.trim() };
            if (q.type === 'choice') def.criteria = Object.fromEntries(q.opts.split(',').map((o) => o.trim()).filter(Boolean).map((k) => [k, '']));
            else if (q.type === 'score') {
              if (!Array.isArray(def.criteria) || def.criteria.length < 2) def.criteria = ['Low', 'Medium', 'High'];
            } else delete def.criteria;
            return [q.id.trim(), def];
          }),
      );
    }
    setBusy(true);
    const ok = await press('automations.saveStep', { routine: routine.id, step, ...(insert ? { insert } : {}) });
    setBusy(false);
    if (ok) onClose();
  };
  const remove = async () => {
    if (await press('automations.removeStep', { routine: routine.id, step: s.id })) onClose();
  };

  let body = null;
  if (s.type === 'bot_turn') {
    const bot = s.bot ? (s.bot.includes(':') ? s.bot : `bot:${s.bot}`) : '';
    body = (
      <>
        <Select label="Agent" options={withCur(agentOpts, bot)} value={bot || null} onValueChange={(v) => put({ bot: v })} disabled={!can} />
        <Field label="Prompt" area value={s.prompt ?? ''} onValueChange={(v) => put({ prompt: v })} disabled={!can} />
      </>
    );
  } else if (s.type === 'llm') {
    const mine = `assistant:${(me?.id ?? '').replace(/^\w+:/, '')}`;
    body = (
      <>
        <Select label="Runs as" options={withCur([{ value: mine, label: name(mine) || 'My Assistant' }, ...agentOpts], s.as)} value={s.as ?? null} onValueChange={(v) => put({ as: v })} disabled={!can} />
        <Field label="Prompt" area value={s.prompt ?? ''} onValueChange={(v) => put({ prompt: v })} disabled={!can} />
      </>
    );
  } else if (s.type === 'find') {
    body = (
      <>
        <Select label="Pulls" options={Object.entries(FIND_SOURCES).map(([value, label]) => ({ value, label }))} value={s.source ?? 'mail'} onValueChange={(v) => put({ source: v })} disabled={!can} />
        <Field label="Query" value={s.query ?? ''} onValueChange={(v) => put({ query: v })} placeholder="Optional" disabled={!can} />
        <div className="au-two">
          <Select
            label="Since"
            options={[
              { value: 'last_run', label: 'Last run' },
              { value: 'any', label: 'Any time' },
            ]}
            value={s.since === 'last_run' ? 'last_run' : 'any'}
            onValueChange={(v) => put({ since: v === 'any' ? '' : v })}
            disabled={!can}
          />
          <Field label="Limit" type="number" value={s.limit != null ? String(s.limit) : ''} onValueChange={(v) => put({ limit: v === '' ? undefined : Math.max(1, Math.min(200, Number(v))) })} disabled={!can} />
        </div>
      </>
    );
  } else if (s.type === 'action') {
    const act = s.do ?? 'archive';
    body = (
      <>
        <Select label="Action" options={Object.entries(ACTIONS).map(([value, label]) => ({ value, label }))} value={act} onValueChange={(v) => put({ do: v, args: {} })} disabled={!can} />
        {act === 'create_task' && <Field label="Title" value={arg('title')} onValueChange={(v) => setArg('title', v)} disabled={!can} />}
        {(act === 'create_task' || act === 'assign') && <Select label="Assign to" options={withCur(whoOpts, arg('owner'))} value={arg('owner') || null} onValueChange={(v) => setArg('owner', v)} disabled={!can} />}
        {act === 'wake_bot' && <Select label="Agent" options={withCur(agentOpts, arg('bot'))} value={arg('bot') || null} onValueChange={(v) => setArg('bot', v)} disabled={!can} />}
        {act === 'forward' && <Field label="To" value={arg('to')} onValueChange={(v) => setArg('to', v)} placeholder="name@fernhill.example" disabled={!can} />}
        {act === 'label' && <Field label="Label" value={arg('name') || arg('label')} onValueChange={(v) => setArg('name', v)} disabled={!can} />}
        {(act === 'note' || act === 'post_update') && <Field label="Text" area value={arg('text')} onValueChange={(v) => setArg('text', v)} disabled={!can} />}
        {(act === 'draft_reply' || act === 'send_reply') && <Field label="Reply" area value={arg('body') || arg('text')} onValueChange={(v) => setArg('body', v)} disabled={!can} />}
        {act === 'call_webhook' && <Field label="Webhook" value={arg('webhook')} onValueChange={(v) => setArg('webhook', v)} disabled={!can} />}
      </>
    );
  } else if (s.type === 'filter') {
    const rules = s.rules ?? [];
    const setRule = (i: number, r: Partial<FlowRule>) => put({ rules: rules.map((x, j) => (j === i ? { ...x, ...r } : x)) });
    body = (
      <>
        {rules.map((r, i) => {
          const then = (r.then ?? []).map((x) => (typeof x === 'string' ? { do: x, args: {} } : x));
          const stops = r.next ? r.next === 'end' : r.to || r.stop === true;
          return (
            <section key={i} className="au-rb" aria-label={`Rule ${i + 1}`}>
              <div className="au-rb-h">
                <span>{`Rule ${i + 1}`}</span>
                <Select
                  label="Match"
                  labelHidden
                  look="quiet"
                  options={[
                    { value: 'all', label: 'All of' },
                    { value: 'any', label: 'Any of' },
                  ]}
                  value={r.match === 'any' ? 'any' : 'all'}
                  onValueChange={(v) => setRule(i, { match: v })}
                />
                <IconButton icon="close" label="Remove rule" size="sm" quiet onClick={() => put({ rules: rules.filter((_, j) => j !== i) })} />
              </div>
              <CondRows label="Conditions" conds={r.when ?? []} onChange={(when) => setRule(i, { when })} fields={fields} min />
              <div className="au-then" role="group" aria-label="Then">
                {then.map((x, j) => (
                  <span key={j} className="au-then-a">
                    <Select
                      label="Then"
                      labelHidden
                      options={Object.entries(ACTIONS).map(([value, label]) => ({ value, label }))}
                      value={x.do ?? 'archive'}
                      onValueChange={(v) => setRule(i, { then: then.map((y, k) => (k === j ? { do: v, args: {} } : y)) })}
                    />
                    {then.length > 1 && <IconButton icon="close" label="Remove action" size="sm" quiet onClick={() => setRule(i, { then: then.filter((_, k) => k !== j) })} />}
                  </span>
                ))}
                <Verb onClick={() => setRule(i, { then: [...then, { do: 'archive', args: {} }] })}>Add action</Verb>
              </div>
              <Checkbox label="Stop here" checked={!!stops} onCheckedChange={(on) => setRule(i, 'next' in r ? { next: on ? 'end' : 'step', stop: on } : { stop: on })} />
            </section>
          );
        })}
        <Verb onClick={() => put({ rules: [...rules, { when: [{ field: fields[0] ?? '', op: 'contains', value: '' }], match: 'all', then: [{ do: 'archive', args: {} }], stop: true }] })}>Add rule</Verb>
      </>
    );
  } else if (s.type === 'decide') {
    body = s.set ? (
      <p className="au-note">{`Question set ${String(s.set).replace(/@(\d+)$/, ' v$1')}`}</p>
    ) : (
      <>
        {qs.map((q, i) => {
          const setQ = (patch: Partial<QRow>) => setQs(qs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
          return (
            <section key={i} className="au-rb" aria-label={`Question ${i + 1}`}>
              <div className="au-rb-h">
                <Field label="Name" labelHidden value={q.id} onValueChange={(v) => setQ({ id: v.replace(/\s+/g, '_') })} placeholder="Name" />
                <Select label="Answer" labelHidden options={Object.entries(QUESTION_TYPES).map(([value, label]) => ({ value, label }))} value={q.type} onValueChange={(v) => setQ({ type: v })} />
                <IconButton icon="close" label="Remove question" size="sm" quiet onClick={() => setQs(qs.filter((_, j) => j !== i))} />
              </div>
              <Field label="What to ask" labelHidden area value={q.ask} onValueChange={(v) => setQ({ ask: v })} placeholder="What to ask" />
              {q.type === 'choice' && <Field label="Options" labelHidden value={q.opts} onValueChange={(v) => setQ({ opts: v })} placeholder="Options, comma separated" />}
            </section>
          );
        })}
        <Verb onClick={() => setQs([...qs, { id: `q${qs.length + 1}`, type: 'noul', ask: '', opts: '' }])}>Add question</Verb>
      </>
    );
  } else if (s.type === 'paths') {
    const paths = s.paths ?? [];
    const setPath = (i: number, p: Partial<Path>) => put({ paths: paths.map((x, j) => (j === i ? { ...x, ...p } : x)) });
    body = (
      <>
        {paths.map((p, i) => (
          <section key={i} className="au-rb" aria-label={p.name || `Path ${i + 1}`}>
            <div className="au-rb-h">
              <Field label="Path name" labelHidden value={p.name ?? ''} onValueChange={(v) => setPath(i, { name: v })} placeholder="Name" />
              {paths.length > 1 && <IconButton icon="close" label="Remove path" size="sm" quiet onClick={() => put({ paths: paths.filter((_, j) => j !== i) })} />}
            </div>
            <CondRows label="Conditions" conds={p.when ?? []} onChange={(when) => setPath(i, { when })} fields={fields} min />
          </section>
        ))}
        <section className="au-rb is-other" aria-label="Otherwise">
          <div className="au-rb-h">
            <span>Otherwise</span>
          </div>
          <p className="au-quiet">Nothing else</p>
        </section>
        {paths.length < MAX_PATHS && <Verb onClick={() => put({ paths: [...paths, { name: `Path ${paths.length + 1}`, when: [], steps: [] }] })}>Add path</Verb>}
      </>
    );
  }

  return (
    <SidePanel
      className="rr au-ed"
      title={STEP_WORDS[s.type] ?? 'Step'}
      target={`step:${s.id}`}
      onClose={onClose}
      foot={
        can && (
          <>
            {!insert && (
              <Button variant="quiet" onClick={remove}>
                Delete step
              </Button>
            )}
            <Button variant="primary" busy={busy} onClick={save}>
              Save
            </Button>
          </>
        )
      }
    >
      <section className="rail-sec au-form">{body}</section>
    </SidePanel>
  );
}

// ---------------------------------------------------------------- the trigger

function TriggerForm({ routine, onClose }: { routine: Routine; onClose: () => void }) {
  const [t, setT] = useState<Trigger>(() => structuredClone(routine.trigger));
  const [custom, setCustom] = useState(() => t.type === 'event' && !!t.name && !EVENT_SHORT[t.name] && !t.name.startsWith('tico.builtin.'));
  const [busy, setBusy] = useState(false);
  const people = useActors().filter((a) => a.kind === 'person');
  const agents = useActors().filter((a) => a.kind === 'agent');
  const can = !!routine.can_edit && !routine.readonly;
  const put = (patch: Partial<Trigger>) => setT((x) => ({ ...x, ...patch }));
  // a schedule, an event or a due date; a kind made elsewhere (a message, a webhook) shows as itself
  const fixed = !WHEN.some(([k]) => k === routine.trigger.type);
  const kinds = [...(fixed ? [{ value: routine.trigger.type, label: KIND_WORDS[routine.trigger.type] ?? routine.trigger.type }] : []), ...WHEN.map(([value, label]) => ({ value, label }))];
  const events = [
    ...Object.keys(EVENT_SHORT)
      .filter((k) => !k.startsWith('tico.builtin.'))
      .map((value) => ({ value, label: eventShort(value) })),
    { value: CUSTOM, label: 'Custom' },
  ];
  const save = async () => {
    const out: Trigger = { ...t, ...(t.type === 'event' ? { name: String(t.name ?? '').trim() } : {}), ...(t.type === 'due' || t.type === 'schedule' ? { tz: String(t.tz ?? '').trim() || browserZone() } : {}) };
    setBusy(true);
    const ok = await press('automations.setTrigger', { routine: routine.id, trigger: out });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <SidePanel
      className="rr au-ed"
      title="Trigger"
      target={`trigger:${routine.id}`}
      onClose={onClose}
      foot={
        can && (
          <Button variant="primary" busy={busy} onClick={save}>
            Save
          </Button>
        )
      }
    >
      <section className="rail-sec au-form">
        <Select
          label="When"
          options={kinds}
          value={t.type}
          disabled={!can || fixed}
          onValueChange={(type) => {
            // another kind starts fresh: its filters test another kind's fields
            setT(type === routine.trigger.type ? structuredClone(routine.trigger) : newTrigger(type));
            setCustom(false);
          }}
        />
        {t.type === 'message' && <Field label="Inbox" value={t.inbox ?? ''} onValueChange={(v) => put({ inbox: v })} disabled={!can} />}
        {t.type === 'task_received' && (
          <Select
            label="To"
            options={[...people.map((p) => ({ value: p.id.replace(/^human:/, 'person:'), label: p.name })), ...agents.map((a) => ({ value: a.id, label: a.name }))]}
            value={t.to ?? null}
            onValueChange={(v) => put({ to: v })}
            disabled={!can}
          />
        )}
        {t.type === 'schedule' && (
          <>
            <ScheduleFields value={parseSchedule(t.cron || '0 9 * * 1-5')} onChange={(v) => put({ cron: v.cron })} />
            <Field label="Time zone" value={t.tz ?? ''} onValueChange={(v) => put({ tz: v })} placeholder={browserZone()} disabled={!can} />
          </>
        )}
        {t.type === 'webhook' && <Field label="Webhook" value={t.name || t.id || ''} disabled />}
        {t.type === 'event' && (
          <>
            <Select
              label="Event"
              options={custom || !t.name || EVENT_SHORT[t.name] ? events : [{ value: t.name, label: eventShort(t.name) }, ...events]}
              value={custom ? CUSTOM : (t.name ?? 'task.created')}
              onValueChange={(v) => (v === CUSTOM ? (setCustom(true), put({ name: '' })) : (setCustom(false), put({ name: v })))}
              disabled={!can}
            />
            {custom && <Field label="Event name" value={t.name ?? ''} onValueChange={(v) => put({ name: v })} placeholder="custom.name" disabled={!can} />}
          </>
        )}
        {t.type === 'due' && (
          <>
            <Field label="First due" value={t.first_due ?? ''} onValueChange={(v) => put({ first_due: v })} placeholder="2027-01-31" disabled={!can} />
            <Select label="Repeats" options={REPEATS.map(([value, label]) => ({ value, label }))} value={t.repeats ?? 'yearly'} onValueChange={(v) => put({ repeats: v })} disabled={!can} />
            <Field label="Days before" type="number" value={String(t.open_days ?? 14)} onValueChange={(v) => put({ open_days: Math.max(0, Math.min(366, Number(v) || 0)) })} disabled={!can} />
            <Field label="Time zone" value={t.tz ?? ''} onValueChange={(v) => put({ tz: v })} placeholder={browserZone()} disabled={!can} />
          </>
        )}
        <div className="au-sub">
          <h3>Filters</h3>
          <CondRows label="Filters" conds={(t.filters ?? []) as Cond[]} onChange={(filters) => put({ filters })} fields={TRIGGER_FIELDS[t.type] ?? []} min={false} />
        </div>
      </section>
    </SidePanel>
  );
}

