import { useQuery } from '@tanstack/react-query';
import { Fragment, useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { fmtDate, fmtMoney } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { BoardItemEditor, BoardView, type ItemRef } from './BoardEditor';
import { routineBoard, takesTemplate } from './board';
import { Face, HeadSwitch, PanelRead, press, pressOut, RemoveItem, useConfirmRemove, useName, Verb, Word } from './parts';
import { routineQuery, routinesQuery, runsQuery, versionsQuery } from './queries';
import { ApplyTemplate } from './Templates';
import type { Routine, Trigger, Version } from './types';
import { EVERY, eventWords, flowLines, lineDiff, parseSchedule, runLink, runWords, scheduleCron, timeChoices, triggerWords, WEEKDAYS, type Every } from './words';

/* One routine, as a panel (rules.js routinePanel): its facts, the instruction, the last runs and Run now. Edit turns
   the panel into the editor; `routine:new` opens the editor empty. "⋯" also holds a test run with sample input and its
   versions (the old app's Test run and Versions). */

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
  const [limiting, setLimiting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [versions, setVersions] = useState(false);
  const [item, setItem] = useState<ItemRef | null>(null);
  const [applying, setApplying] = useState(false);
  const remove = useConfirmRemove();
  if (!r) {
    return (
      <SidePanel className="rr" title="Routine" onClose={onClose}>
        <PanelRead q={one} what="This routine" />
      </SidePanel>
    );
  }
  if (editing) return <RoutineEditor routine={r} onDone={() => setEditing(false)} onClose={onClose} />;
  if (versions) return <Versions routine={r} onDone={() => setVersions(false)} onClose={onClose} />;
  const board = routineBoard(r);
  if (board && item) return <BoardItemEditor key={`${item.part}:${item.at ?? ''}`} routine={r} board={board} it={item} onDone={() => setItem(null)} onClose={onClose} />;
  const on = r.enabled !== false && !r.paused;
  const step = stepOf(r);
  const together = r.steps?.find((s) => s.type === 'find' && s.together === true);
  const tmp = r.id.startsWith('tmp:');
  return (
    <SidePanel
      className="rr"
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
            {/* an inbox board is edited a part at a time, in the board below */}
            {!board && <MenuItem label="Edit" icon="edit" disabled={!r.can_edit} onSelect={() => setEditing(true)} />}
            <MenuItem label="Test run" icon="play" disabled={r.readonly || !r.can_edit} onSelect={() => setTesting(true)} />
            <MenuItem label="Versions" icon="clock" onSelect={() => setVersions(true)} />
            {takesTemplate(r) && <MenuItem label="Apply template" icon="mail" onSelect={() => setApplying(true)} />}
            <RemoveItem disabled={!r.can_edit} onSelect={() => remove.confirm(r.name || 'this routine', () => void press('routines.remove', { routine: r.id }))} />
          </Menu>
        </>
      }
    >
      <section className="rail-sec">
        <Facts label="About">
          <LabelValue label="Trigger">{triggerWords(r.trigger)}</LabelValue>
          {together && <LabelValue label="Batch">{`Once per run, for new ${String(together.source)} since the last run`}</LabelValue>}
          <LabelValue label="Does">
            <Face id={step?.bot ?? r.owner_id} />
            <Runner id={step?.bot ?? r.owner_id} />
          </LabelValue>
          <LabelValue label="Limit">
            <span>{r.spend_cap?.per_30m != null ? `${fmtMoney(r.spend_cap.per_30m, { cents: true })} each 30 minutes` : "Agent's limit"}</span>
            {r.can_edit && !tmp && !limiting && <Verb onClick={() => setLimiting(true)}>Change</Verb>}
          </LabelValue>
          {r.updated_at && <LabelValue label="Changed">{fmtDate(r.updated_at)}</LabelValue>}
        </Facts>
        {limiting && <LimitEditor routine={r} onDone={() => setLimiting(false)} />}
        {r.hold && <HoldLine routine={r} held={r.hold.held} dropped={r.hold.dropped} />}
      </section>
      {step?.prompt && (
        <section className="rail-sec">
          <h3>Instruction</h3>
          <p className="inst t-read">{step.prompt}</p>
        </section>
      )}
      {board && <BoardView routine={r} board={board} onOpen={setItem} />}
      {!tmp && <Runs id={r.id} />}
      {remove.node}
      {testing && <TestRun routine={r} onClose={() => setTesting(false)} />}
      {applying && <ApplyTemplate routine={r} onClose={() => setApplying(false)} />}
    </SidePanel>
  );
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
function TestRun({ routine, onClose }: { routine: Routine; onClose: () => void }) {
  const [text, setText] = useState(() => sampleFor(routine.trigger, routine.next_due_at));
  const [dropped, setDropped] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    const r = await pressOut<{ dropped: boolean }>('routines.testRun', { routine: routine.id, ...(text.trim() ? { sample: text } : {}) });
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
function Versions({ routine, onDone, onClose }: { routine: Routine; onDone: () => void; onClose: () => void }) {
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
                    <Verb onClick={() => void press('routines.restoreVersion', { routine: routine.id, n: v.n })}>Turn on</Verb>
                    <Verb onClick={() => void press('routines.discardVersion', { routine: routine.id, n: v.n })}>Discard</Verb>
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
              can && <Verb onClick={() => void press('routines.restoreVersion', { routine: routine.id, n: v.n })}>Restore</Verb>
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
function HoldLine({ routine, held, dropped }: { routine: Routine; held: number; dropped: number }) {
  const [asking, setAsking] = useState(false);
  const end = (action: 'release' | 'drop') => press('routines.endHold', { routine: routine.id, action });
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
function LimitEditor({ routine, onDone }: { routine: Routine; onDone: () => void }) {
  const [value, setValue] = useState(routine.spend_cap?.per_30m != null ? String(routine.spend_cap.per_30m) : '');
  const typed = value.trim() === '' ? null : Number(value);
  const bad = typed !== null && !(typed >= 0);
  const save = async (per30m: number | null) => {
    if (await press('routines.setLimit', { routine: routine.id, per30m })) onDone();
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

function Runner({ id }: { id: string | null | undefined }) {
  const name = useName(id);
  return <span>{name || 'An agent'}</span>;
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
    const ok = await press(routine ? 'routines.update' : 'routines.create', routine ? { routine: routine.id, ...input } : input);
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
