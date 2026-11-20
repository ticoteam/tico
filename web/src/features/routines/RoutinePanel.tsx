import { useQuery } from '@tanstack/react-query';
import { Fragment, useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { SidePanel } from '@/pagekinds';
import { press, pressOut, useName, Verb, Word } from './parts';
import { versionsQuery } from './queries';
import type { Routine, Trigger, Version } from './types';
import { EVERY, eventWords, flowLines, lineDiff, parseSchedule, scheduleCron, timeChoices, triggerWords, WEEKDAYS, type Every } from './words';

/* A new agent routine in the panel (`?panel=routine:new`): a name, the agent, when it runs and its instruction. And the
   pieces an automation's page opens: a test run with sample input, its versions and proposals, the run guard's hold,
   its spend limit and the schedule picker. */

const stepOf = (r: Routine | undefined) => r?.steps?.find((s) => s.type === 'bot_turn');

export function RoutinePanel({ id, onClose }: { id: string; onClose: () => void }) {
  return <RoutineEditor key={id} onDone={onClose} onClose={onClose} />;
}

/** What its trigger hands a run, as JSON to edit (automations-editors.ts auSample); a schedule hands nothing. A due
   routine's is what its scheduler starts a run with for its next due day (`nextDue`), naming the rule's due date it was
   made for. */
export function sampleFor(t: Trigger, nextDue?: string | null): string {
  const due = {
    due_on: nextDue || t.first_due || '',
    first_due: t.first_due ?? '',
    repeats: t.repeats ?? 'once',
    open_days: t.open_days ?? 14,
    ...(t.tz ? { tz: t.tz } : {}),
    ...(t.rule_due_id ? { rule_due_id: t.rule_due_id } : {}),
  };
  const samples: Record<string, unknown> = {
    message: { message: { from: '', subject: '', body: '' } },
    task_received: { task: { title: '', body: '' } },
    event: { event: { name: t.name ?? '' } },
    webhook: { body: {} },
    due: { due },
  };
  const one = samples[t.type];
  return one ? JSON.stringify(one, null, 2) : '';
}

/** Run it once with sample input standing in for its trigger. A sample its filters drop makes no run, and says so. */
export function TestRun({ routine, onClose }: { routine: Routine; onClose: () => void }) {
  const [text, setText] = useState(() => sampleFor(routine.trigger, routine.next_due_at));
  const [dropped, setDropped] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    const r = await pressOut<{ dropped: boolean }>('automations.testRun', { routine: routine.id, ...(text.trim() ? { sample: text } : {}) });
    setBusy(false);
    if (r?.out?.dropped) setDropped(true);
    else if (r) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Test run"
      description={dropped ? "Dropped by the trigger's filters" : undefined}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={() => go()}>
            Run
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Field
          label="Sample input"
          area
          value={text}
          onValueChange={(v) => {
            setText(v);
            setDropped(false);
          }}
          placeholder="None"
          autoFocus
        />
      </div>
    </Dialog>
  );
}

/** Its versions, newest first, with an agent's proposals above them: any but the current one restores as a new
   version; a proposal is turned on or discarded. */
export function Versions({ routine, onDone, onClose }: { routine: Routine; onDone: () => void; onClose: () => void }) {
  const q = useQuery(versionsQuery(routine.id));
  const all = q.data ?? [];
  const proposed = all.filter((v) => v.status === 'pending');
  const saved = all.filter((v) => v.status !== 'pending');
  const can = !!routine.can_edit;
  const [diff, setDiff] = useState<number | null>(null);
  return (
    <SidePanel
      className="rr"
      title={routine.name || 'Routine'}
      target={`routine-versions:${routine.id}`}
      onClose={onClose}
      foot={
        <Button variant="quiet" onClick={onDone}>
          Back
        </Button>
      }
    >
      {!!proposed.length && (
        <section className="rail-sec">
          <h3>Proposed</h3>
          {proposed.map((v) => (
            <Fragment key={v.n}>
              <VersionRow v={v}>
                <Verb onClick={() => setDiff(diff === v.n ? null : v.n)}>{diff === v.n ? 'Hide diff' : 'View diff'}</Verb>
                {can && (
                  <>
                    <Verb onClick={() => void press('automations.restoreVersion', { routine: routine.id, n: v.n })}>Turn on</Verb>
                    <Verb onClick={() => void press('automations.discardVersion', { routine: routine.id, n: v.n })}>Discard</Verb>
                  </>
                )}
              </VersionRow>
              {diff === v.n && <FlowDiff from={routine} to={v.flow} />}
            </Fragment>
          ))}
        </section>
      )}
      <section className="rail-sec">
        <h3>Versions</h3>
        {q.isError && <Word>Versions did not load</Word>}
        {saved.map((v) => (
          <VersionRow key={v.n} v={v}>
            {v.n === routine.version ? (
              <span className="row-age">Current</span>
            ) : (
              can && <Verb onClick={() => void press('automations.restoreVersion', { routine: routine.id, n: v.n })}>Restore</Verb>
            )}
          </VersionRow>
        ))}
      </section>
    </SidePanel>
  );
}

/** What a proposal changes, against the routine as it is now: lines added and taken out. */
function FlowDiff({ from, to }: { from: Routine; to: Version['flow'] }) {
  const lines = lineDiff(flowLines(from), flowLines(to ? { trigger: to.trigger ?? from.trigger, steps: to.steps ?? from.steps } : null));
  return (
    <pre className="rr-diff" aria-label="Diff">
      {lines.length
        ? lines.map((l, i) => (
            <Fragment key={i}>
              {l.startsWith('+') ? <ins>{l}</ins> : <del>{l}</del>}
              {'\n'}
            </Fragment>
          ))
        : 'No changes'}
    </pre>
  );
}

function VersionRow({ v, children }: { v: Version; children: ReactNode }) {
  const who = useName(v.actor);
  const what = v.summary || who || 'Saved';
  return (
    <Row className="prun" title={`v${v.n} ${what}`} target={`version:${v.n}`}>
      <span className="run-at">
        v{v.n} <Ago at={v.created_at} />
      </span>
      <span className="row-sub">{what}</span>
      <span className="rr-field">{children}</span>
    </Row>
  );
}

/** The run guard held its runs: how many, and Release or Drop for whoever may change it (Drop asks first). */
export function HoldLine({ routine, held, dropped }: { routine: Routine; held: number; dropped: number }) {
  const [asking, setAsking] = useState(false);
  const end = (action: 'release' | 'drop') => press('automations.endHold', { routine: routine.id, action });
  return (
    <div className="rr-field" data-target={`hold:${routine.id}`}>
      <Word icon="pause">On hold</Word>
      <span className="row-sub">{dropped ? `${held} held, ${dropped} dropped` : `${held} held`}</span>
      {routine.can_edit && (
        <>
          <Verb onClick={() => end('release')}>Release</Verb>
          <Verb onClick={() => setAsking(true)}>Drop</Verb>
        </>
      )}
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        title={`Drop ${held} held ${held === 1 ? 'run' : 'runs'}?`}
        alert
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setAsking(false);
                end('drop');
              }}
            >
              Drop
            </Button>
          </>
        }
      />
    </div>
  );
}

/** Its spend limit, as the old app's editor: dollars each 30 minutes, blank for the agent's own limit. */
export function LimitEditor({ routine, onDone }: { routine: Routine; onDone: () => void }) {
  const [value, setValue] = useState(routine.spend_cap?.per_30m != null ? String(routine.spend_cap.per_30m) : '');
  const typed = value.trim() === '' ? null : Number(value);
  const bad = typed !== null && !(typed >= 0);
  const save = async (per30m: number | null) => {
    if (await press('automations.setLimit', { routine: routine.id, per30m })) onDone();
  };
  return (
    <div className="rr-form">
      <Field
        label="Dollars each 30 minutes"
        type="number"
        value={value}
        onValueChange={setValue}
        placeholder="Agent's limit"
        error={bad ? 'Enter an amount in dollars' : undefined}
        autoFocus
      />
      <div className="rr-field">
        <Button variant="primary" disabled={bad} onClick={() => save(typed)}>
          Save
        </Button>
        {/* blank is the agent's own limit (the placeholder says so) */}
        <Button onClick={() => save(null)}>Clear</Button>
        <Button variant="quiet" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
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
  const [picked, setAgent] = useState<string | null>(step?.bot ?? (routine?.owner_id?.startsWith('bot:') ? routine.owner_id : null));
  const [start, setStart] = useState<Start>(t?.type === 'event' ? 'event' : 'schedule');
  const [sched, setSched] = useState(parseSchedule(t?.type === 'schedule' && t.cron ? t.cron : '0 9 * * 1-5'));
  const [event, setEvent] = useState(t?.type === 'event' && t.name ? t.name : 'task.created');
  const [instruction, setInstruction] = useState(step?.prompt ?? '');
  const [busy, setBusy] = useState(false);
  // an event the catalog does not list (one saved before) stays a choice
  const events = [...(EVENT_CHOICES.some((e) => e.value === event) ? [] : [{ value: event, label: eventWords(event) }]), ...EVENT_CHOICES];
  const options = agents.map((a) => ({ value: a.id, label: a.name }));
  // An older step may name its agent by slug; the server takes the agent's id.
  const agent = (picked && agents.find((a) => a.id === picked || `bot:${a.slug}` === picked)?.id) ?? picked;
  if (agent && !options.some((o) => o.value === agent)) options.unshift({ value: agent, label: agent.slice(4) });
  // a routine whose work is other steps (a filter, an action) may have no instruction
  const others = !!routine?.steps?.some((x) => x.type !== 'bot_turn');
  const ready = !!name.trim() && !!agent && (!!instruction.trim() || (others && !step));

  const save = async () => {
    if (!ready || !agent) return;
    const when = fixed ? {} : start === 'schedule' ? { cron: scheduleCron(sched) } : { event };
    const input = { name: name.trim(), agent, ...(instruction.trim() ? { instruction: instruction.trim() } : {}), ...when };
    setBusy(true);
    const ok = await press(routine ? 'automations.update' : 'automations.create', routine ? { routine: routine.id, ...input } : input);
    setBusy(false);
    if (ok && routine) onDone();
  };

  return (
    <SidePanel
      className="rr"
      title={routine ? routine.name || 'Routine' : 'New routine'}
      target={routine ? `routine:${routine.id}` : 'panel:routine:new'}
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => save()}>
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
            {start === 'schedule' ? <ScheduleFields value={sched} onChange={setSched} /> : <Select label="When" options={events} value={event} onValueChange={setEvent} />}
          </>
        )}
        <Field label="Instruction" area value={instruction} onValueChange={setInstruction} placeholder="What the agent does each time" />
      </section>
    </SidePanel>
  );
}

export function ScheduleFields({ value, onChange }: { value: ReturnType<typeof parseSchedule>; onChange: (s: ReturnType<typeof parseSchedule>) => void }) {
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
