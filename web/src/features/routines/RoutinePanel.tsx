import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { fmtDate, fmtMoney } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { Face, HeadSwitch, press, RemoveItem, useConfirmRemove, useName, Verb, Word } from './parts';
import { routineQuery, routinesQuery, runsQuery } from './queries';
import type { Routine, Run } from './types';
import { browserZone, EVERY, eventWords, parseSchedule, scheduleCron, timeChoices, triggerWords, WEEKDAYS, runWords, type Every } from './words';

/* One routine, as a panel (rules.js routinePanel): its facts, the instruction, the last runs and Run now. Edit turns
   the panel into the editor; `routine:new` opens the editor empty. */

export function RoutinePanel({ id, onClose }: { id: string; onClose: () => void }) {
  if (id === 'new') return <RoutineEditor onDone={onClose} onClose={onClose} />;
  return <RoutineView id={id} onClose={onClose} />;
}

const stepOf = (r: Routine | undefined) => r?.steps?.find((s) => s.type === 'bot_turn');

function RoutineView({ id, onClose }: { id: string; onClose: () => void }) {
  const listed = useQuery(routinesQuery).data?.find((r) => r.id === id);
  const one = useQuery({ ...routineQuery(id), enabled: !listed && !id.startsWith('tmp:') });
  const r = listed ?? one.data;
  const [editing, setEditing] = useState(false);
  const remove = useConfirmRemove();
  if (!r) {
    return (
      <SidePanel title="Routine" onClose={onClose}>
        {one.isError && <Empty icon="alert" title="This routine did not load" />}
      </SidePanel>
    );
  }
  if (editing) return <RoutineEditor routine={r} onDone={() => setEditing(false)} onClose={onClose} />;
  const on = r.enabled !== false && !r.paused;
  const step = stepOf(r);
  const tmp = r.id.startsWith('tmp:');
  return (
    <SidePanel
      title={r.name || 'Routine'}
      target={`routine:${r.id}`}
      onClose={onClose}
      actions={
        <>
          <HeadSwitch
            label={on ? 'On' : 'Paused'}
            checked={on}
            disabled={r.can_pause === false || tmp}
            onChange={(next) => void press(next ? 'routines.resume' : 'routines.pause', { routine: r.id })}
          />
          <Menu trigger={<IconButton icon="more" label="More" quiet disabled={tmp} />}>
            <MenuItem label="Edit" icon="edit" disabled={!r.can_edit} onSelect={() => setEditing(true)} />
            <RemoveItem disabled={!r.can_edit} onSelect={() => remove.confirm(r.name || 'this routine', () => void press('routines.remove', { routine: r.id }))} />
          </Menu>
        </>
      }
    >
      <section className="rail-sec">
        <Facts label="About">
          <LabelValue label="Trigger">{triggerWords(r.trigger)}</LabelValue>
          <LabelValue label="Does">
            <Face id={step?.bot ?? r.owner_id} />
            <Runner id={step?.bot ?? r.owner_id} />
          </LabelValue>
          {r.spend_cap?.per_30m ? <LabelValue label="Limit">{`${fmtMoney(r.spend_cap.per_30m, { cents: true })} each 30 minutes`}</LabelValue> : null}
          {r.updated_at && <LabelValue label="Changed">{fmtDate(r.updated_at)}</LabelValue>}
        </Facts>
      </section>
      {step?.prompt && (
        <section className="rail-sec">
          <h3>Instruction</h3>
          <p className="inst t-read">{step.prompt}</p>
        </section>
      )}
      {!tmp && <Runs id={r.id} />}
      {remove.node}
    </SidePanel>
  );
}

function Runner({ id }: { id: string | null | undefined }) {
  const name = useName(id);
  return <span>{name || 'An agent'}</span>;
}

/** The task or thread a run made: the first one a step names. */
export function runLink(run: Pick<Run, 'steps'>): string | undefined {
  for (const s of run.steps ?? []) {
    const o = s.outputs;
    const task = o?.ref ?? o?.turn?.task;
    if (typeof task === 'string' && task) return `/task/${encodeURIComponent(task)}`;
    if (typeof o?.thread === 'string' && o.thread) return `/threads/${encodeURIComponent(o.thread)}`;
  }
  return undefined;
}

function Runs({ id }: { id: string }) {
  const runs = useQuery(runsQuery(id));
  const list = runs.data ?? [];
  return (
    <section className="rail-sec">
      <h3>
        Last runs
        <Verb onClick={() => void press('routines.runNow', { routine: id })} target={`routine-runs:${id}`}>
          Run now
        </Verb>
      </h3>
      {runs.isError && <Word>Runs did not load</Word>}
      {list.map((run) => {
        const at = run.started_at ?? run.finished_at;
        const what = run.error || run.summary || runWords(run.status);
        const href = runLink(run);
        return (
          <Row key={run.id} className="prun" title={what} href={href} target={`run:${run.id}`}>
            <span className="run-at">{at ? fmtDate(at) : ''}</span>
            <span className="row-sub">{what}</span>
            {run.status === 'failed' ? <Word>Failed</Word> : <span className="row-age">{run.cost ? fmtMoney(run.cost, { cents: true }) : ''}</span>}
          </Row>
        );
      })}
    </section>
  );
}

// ---------------------------------------------------------------- the editor

const EVENT_CHOICES = ['task.created', 'task.assigned', 'task.step_changed', 'task.done', 'task.closed', 'task.commented', 'thread.created', 'message.created', 'inbound.received', 'bot.run_failed'].map(
  (value) => ({ value, label: eventWords(value) }),
);

type Start = 'schedule' | 'event';

function RoutineEditor({ routine, onDone, onClose }: { routine?: Routine; onDone: () => void; onClose: () => void }) {
  const agents = useActors().filter((a) => a.kind === 'agent');
  const step = stepOf(routine);
  const t = routine?.trigger;
  // A trigger the editor cannot draw (a webhook, a due date, a message) is kept as it is.
  const fixed = !!t && t.type !== 'schedule' && t.type !== 'event';
  const [name, setName] = useState(routine?.name ?? '');
  const [agent, setAgent] = useState<string | null>(step?.bot ?? (routine?.owner_id?.startsWith('bot:') ? routine.owner_id : null));
  const [start, setStart] = useState<Start>(t?.type === 'event' ? 'event' : 'schedule');
  const [sched, setSched] = useState(parseSchedule(t?.type === 'schedule' && t.cron ? t.cron : '0 9 * * 1-5'));
  const [event, setEvent] = useState(t?.type === 'event' && t.name ? t.name : 'task.created');
  const [instruction, setInstruction] = useState(step?.prompt ?? '');
  const [busy, setBusy] = useState(false);
  const events = EVENT_CHOICES.some((e) => e.value === event) ? EVENT_CHOICES : [{ value: event, label: eventWords(event) }, ...EVENT_CHOICES];
  const options = agents.map((a) => ({ value: a.id, label: a.name }));
  if (agent && !options.some((o) => o.value === agent)) options.unshift({ value: agent, label: agent.slice(4) });
  const ready = !!name.trim() && !!agent && !!instruction.trim();

  const save = async () => {
    if (!ready || !agent) return;
    const when = fixed ? {} : start === 'schedule' ? { cron: scheduleCron(sched), tz: t?.tz ?? browserZone() } : { event };
    const input = { name: name.trim(), agent, instruction: instruction.trim(), ...when };
    setBusy(true);
    const ok = await press(routine ? 'routines.update' : 'routines.create', routine ? { routine: routine.id, ...input } : input);
    setBusy(false);
    if (ok && routine) onDone();
  };

  return (
    <SidePanel
      title={routine ? routine.name || 'Routine' : 'New routine'}
      target={routine ? `routine:${routine.id}` : 'panel:routine:new'}
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {routine ? 'Save' : 'Add routine'}
          </Button>
          <Button variant="quiet" onClick={onDone}>
            Cancel
          </Button>
        </>
      }
    >
      <section className="rail-sec rr-form">
        <Field label="Name" value={name} onValueChange={setName} autoFocus={!routine} />
        <Select label="Agent" options={options} value={agent} onValueChange={setAgent} placeholder="Pick an agent" />
        {fixed && t ? (
          <Facts>
            <LabelValue label="Trigger">{triggerWords(t)}</LabelValue>
          </Facts>
        ) : (
          <>
            <Select
              label="Runs"
              options={[
                { value: 'schedule', label: 'On a schedule' },
                { value: 'event', label: 'When something happens' },
              ]}
              value={start}
              onValueChange={(v: Start) => setStart(v)}
            />
            {start === 'schedule' ? (
              <ScheduleFields value={sched} onChange={setSched} />
            ) : (
              <Select label="When" options={events} value={event} onValueChange={setEvent} />
            )}
          </>
        )}
        <Field label="Instruction" area value={instruction} onValueChange={setInstruction} placeholder="What the agent does each time" />
      </section>
    </SidePanel>
  );
}

function ScheduleFields({ value, onChange }: { value: ReturnType<typeof parseSchedule>; onChange: (s: ReturnType<typeof parseSchedule>) => void }) {
  const set = (patch: Partial<typeof value>) => {
    const next = { ...value, ...patch };
    onChange({ ...next, cron: scheduleCron(next) });
  };
  return (
    <>
      <Select label="Every" options={EVERY} value={value.every} onValueChange={(every: Every) => set({ every })} />
      {value.every === 'weekly' && <Select label="Day" options={WEEKDAYS} value={String(value.day)} onValueChange={(d) => set({ day: Number(d) })} />}
      {value.every !== 'hourly' && value.every !== 'custom' && <Select label="At" options={timeChoices(value.at)} value={value.at} onValueChange={(at) => set({ at })} />}
      {value.every === 'custom' && <Field label="Cron" value={value.cron} onValueChange={(cron) => onChange({ ...value, cron })} placeholder="0 9 * * 1-5" />}
    </>
  );
}
