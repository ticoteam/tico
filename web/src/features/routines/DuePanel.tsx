import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { meQuery, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { SidePanel } from '@/pagekinds';
import { automationHref } from './actions';
import { useNamer } from './CompanyRules';
import { Face, PanelRead, press, RemoveItem, useConfirmRemove, Verb, Word } from './parts';
import { dueOneQuery, orgRulesQuery } from './queries';
import type { DueDetail, DueMark, Repeats } from './types';
import { agentWords, dayWords } from './words';

/* One due date, as a panel (org-rules.ts ruDuePanel): its rule, when it is due, how often, who owns it and the task
   opened before it; Mark satisfied, and the history of marked days. Edit turns it into the form; `due:new` opens the
   form empty, with a rule to pick. A new date or repeat starts a new row on the server, and the panel follows it. */

const REPEATS: { value: Repeats; label: string }[] = [
  { value: 'once', label: 'Once' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'yearly', label: 'Yearly' },
];
const STATUS: Record<string, string> = { overdue: 'Late', due_soon: 'Due soon', upcoming: 'Upcoming', satisfied: 'Satisfied' };

const pad = (n: number) => String(n).padStart(2, '0');
/** A picked day as the server's YYYY-MM-DD, in the viewer's own calendar. */
const toDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fromDay = (s: string | undefined): Date | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s ?? '');
  return m ? new Date(+m[1]!, +m[2]! - 1, +m[3]!) : null;
};
const longDay = (s: string) => fromDay(s)?.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) ?? s;

export function DuePanel({ id, onClose }: { id: string; onClose: () => void }) {
  // `new:<rule>`: opened from a rule's panel, that rule already picked
  if (id === 'new' || id.startsWith('new:')) return <DueEditor rule={id.slice(4) || undefined} onDone={onClose} onClose={onClose} />;
  return <DueView id={id} onClose={onClose} />;
}

function DueView({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery(dueOneQuery(id));
  const d = q.data;
  const [editing, setEditing] = useState(false);
  const remove = useConfirmRemove();
  const moved = d?.ended_on && d.replaced_by ? d.replaced_by : null;
  // an edit ended this row and started another: open the live one
  useEffect(() => {
    if (moved) void press('rules.openDue', { due: moved });
  }, [moved]);
  if (!d || d.retired_at || d.ended_on) {
    return (
      <SidePanel className="rr" title="Due date" onClose={onClose}>
        {q.isSuccess ? !moved && <Empty icon="alert" title="This due date is gone" /> : <PanelRead q={q} what="This due date" />}
      </SidePanel>
    );
  }
  if (editing) return <DueEditor due={d} onDone={() => setEditing(false)} onClose={onClose} />;
  return (
    <SidePanel
      className="rr"
      title="Due date"
      target={`due:${d.id}`}
      onClose={onClose}
      actions={
        d.can_edit && (
          <>
            <IconButton icon="edit" label="Edit" quiet onClick={() => setEditing(true)} />
            <Menu trigger={<IconButton icon="more" label="More" quiet />}>
              <RemoveItem onSelect={() => remove.confirm('this due date', () => void press('rules.removeDue', { due: d.id }))} />
            </Menu>
          </>
        )
      }
    >
      <section className="rail-sec">
        <p className="inst t-read">{agentWords(d.rule.text)}</p>
        <Facts label="About">
          <LabelValue label="Due">{longDay(d.due_on)}</LabelValue>
          <LabelValue label="Repeats">{REPEATS.find((r) => r.value === d.repeats)?.label ?? d.repeats}</LabelValue>
          <LabelValue label="Owner">
            <Face id={d.owner} />
            <Owner id={d.owner} />
          </LabelValue>
          <LabelValue label="Routine">
            <span>{d.task_days == null ? 'None' : `A task ${d.task_days} days before`}</span>
            {d.task_days != null && d.routine_id && (
              <Link className="verb" to={automationHref(d.routine_id)}>
                Open
              </Link>
            )}
          </LabelValue>
          <LabelValue label="Status">
            <span>{STATUS[d.status] ?? d.status}</span>
            {d.can_satisfy && d.status !== 'satisfied' && <Satisfy d={d} />}
          </LabelValue>
        </Facts>
      </section>
      <History d={d} />
      {remove.node}
    </SidePanel>
  );
}

function Owner({ id }: { id: string | null | undefined }) {
  const name = useNamer();
  return <span>{id ? name(id) : 'No one'}</span>;
}

/** Mark it satisfied; with more than one day open, say which first. */
function Satisfy({ d }: { d: DueDetail }) {
  const open = d.open_days ?? [];
  const [picking, setPicking] = useState(false);
  const [day, setDay] = useState(open.includes(d.due_on) ? d.due_on : (open[0] ?? null));
  if (picking && open.length > 1) {
    return (
      <span className="rr-field">
        <Select label="Which day" labelHidden look="quiet" options={open.map((o) => ({ value: o, label: longDay(o) }))} value={day} onValueChange={setDay} />
        <Verb onClick={() => void press('rules.satisfyDue', { due: d.id, ...(day ? { on: day } : {}) }).then((ok) => ok && setPicking(false))}>Mark</Verb>
      </span>
    );
  }
  return <Verb onClick={() => (open.length > 1 ? setPicking(true) : void press('rules.satisfyDue', { due: d.id }))}>Mark satisfied</Verb>;
}

function History({ d }: { d: DueDetail }) {
  const name = useNamer();
  const marks: DueMark[] = (d.chain ?? []).flatMap((link) => link.satisfied ?? []).sort((a, b) => b.due_on.localeCompare(a.due_on));
  if (!marks.length) return null;
  return (
    <section className="rail-sec">
      <h3>History</h3>
      {marks.map((m) => {
        const how = m.via === 'routine' ? 'Task closed' : m.satisfied_by ? `Marked by ${name(m.satisfied_by)}` : 'Marked';
        const outcome = m.outcome && m.outcome !== 'met' ? (m.outcome === 'waived' ? 'Waived' : 'Missed') : 'Satisfied';
        return (
          <Row key={`${m.due_on}-${m.satisfied_at ?? ''}`} className="prun" title={`${dayWords(m.due_on)}, ${how}`}>
            <span className="run-at">{dayWords(m.due_on)}</span>
            <span className="row-sub">{how}</span>
            {outcome === 'Missed' ? <Word>Missed</Word> : <span className="row-age">{outcome}</span>}
          </Row>
        );
      })}
    </section>
  );
}

// ---------------------------------------------------------------- the form

function DueEditor({ due, rule: preset, onDone, onClose }: { due?: DueDetail; rule?: string; onDone: () => void; onClose: () => void }) {
  const me = useQuery(meQuery).data?.id ?? null;
  const actors = useActors();
  const rules = useQuery({ ...orgRulesQuery('active'), enabled: !due }).data ?? [];
  const [rule, setRule] = useState<string | null>(preset ?? null);
  const [day, setDay] = useState<Date | null>(fromDay(due?.due_on));
  const [repeats, setRepeats] = useState<Repeats>(due?.repeats ?? 'yearly');
  const [picked, setOwner] = useState<string | null>(due?.owner ?? null);
  const owner = picked ?? me;
  const name = useNamer();
  const [task, setTask] = useState<'none' | 'task'>(due && due.task_days != null ? 'task' : 'none');
  const [days, setDays] = useState(String(due?.task_days ?? 14));
  const [busy, setBusy] = useState(false);
  const owners = actors.map((a) => ({ value: a.id, label: a.name }));
  if (owner && !owners.some((o) => o.value === owner)) owners.unshift({ value: owner, label: name(owner) });
  const lead = task === 'task' ? Number(days) : null;
  const badDays = lead !== null && !(Number.isInteger(lead) && lead >= 0 && lead <= 366);
  const ready = !!day && !!owner && !badDays && (!!due || !!rule);

  const save = async () => {
    if (!ready || !day || !owner) return;
    setBusy(true);
    let ok: boolean;
    if (!due) {
      ok = await press('rules.addDue', { rule, due: toDay(day), repeats, owner, ...(lead !== null ? { taskDays: lead } : {}) });
    } else {
      // a new date or repeat starts again from the date shown, so days already marked stay behind; an owner alone keeps it
      const first = toDay(day);
      const moved = first !== due.due_on || repeats !== due.repeats;
      const change = {
        ...(moved ? { first, repeats } : {}),
        ...(owner !== due.owner ? { owner } : {}),
        ...(lead !== (due.task_days ?? null) ? { taskDays: lead } : {}),
      };
      ok = Object.keys(change).length ? await press('rules.updateDue', { due: due.id, ...change }) : true;
    }
    setBusy(false);
    if (ok && due) onDone();
  };

  return (
    <SidePanel
      className="rr"
      title={due ? 'Due date' : 'New due date'}
      target={due ? `due:${due.id}` : 'panel:due:new'}
      onClose={onClose}
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => save()}>
            {due ? 'Save' : 'Add'}
          </Button>
          <Button variant="quiet" onClick={onDone}>
            Cancel
          </Button>
        </>
      }
    >
      <section className="rail-sec rr-form">
        {due ? (
          <p className="inst t-read">{agentWords(due.rule.text)}</p>
        ) : (
          <Select
            label="Rule"
            options={rules.filter((r) => r.can_edit && !r.id.startsWith('tmp:')).map((r) => ({ value: r.id, label: agentWords(r.text) }))}
            value={rule}
            onValueChange={setRule}
            placeholder="Pick a rule"
          />
        )}
        <DatePick label="Due" withTime={false} value={day} onValueChange={setDay} />
        <Select label="Repeats" options={REPEATS} value={repeats} onValueChange={setRepeats} />
        <Select label="Owner" options={owners} value={owner} onValueChange={setOwner} placeholder="Pick someone" />
        <Select
          label="Routine"
          options={[
            { value: 'none', label: 'None' },
            { value: 'task', label: 'A task before it' },
          ]}
          value={task}
          onValueChange={setTask}
        />
        {task === 'task' && <Field label="Days before" type="number" value={days} onValueChange={setDays} error={badDays ? 'A whole number up to 366' : undefined} />}
      </section>
    </SidePanel>
  );
}
